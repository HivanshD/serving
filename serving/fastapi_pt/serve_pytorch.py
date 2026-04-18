"""
serve_pytorch.py

FastAPI + PyTorch inference endpoint for ingredient substitution.
Based directly on the Online Evaluation lab's FastAPI pattern
(eval-online-chi/fastapi_pt/app.py):
  - prometheus-fastapi-instrumentator auto-instruments HTTP latency/status
  - prometheus-client Histogram/Counter for custom application metrics
  - /predict endpoint returns model prediction + confidence

Extended beyond the lab for this project:
  - PyTorch → ONNX path is covered by serve_onnx.py (production default)
  - Request logging to object storage for data pipeline feedback loop
  - Pydantic schema matches sample_data/input_sample.json
  - /health endpoint for K8S readiness/liveness probes

Endpoints:
  POST /predict    — main inference endpoint
  GET  /health     — readiness/liveness probe
  GET  /metrics    — auto-instrumented + custom Prometheus metrics

Environment variables:
  OS_ENDPOINT, OS_ACCESS_KEY, OS_SECRET_KEY  — object storage
  MODEL_PATH        (default /app/model.pth)
  LOG_REQUESTS      (default "true")
  REQUEST_LOG_BUCKET (default "logs-proj01")
  SERVING_VERSION   (default "pytorch-baseline")
"""

import os
import json
import time
import uuid
import threading
from typing import Optional, List

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Lab pattern: auto-instrumentation wraps the whole app
from prometheus_fastapi_instrumentator import Instrumentator
# Lab pattern: custom application metrics use prometheus-client directly
from prometheus_client import Histogram, Counter, Gauge

from model_stub import (
    SubstitutionModel, build_stub_vocab_and_model, tokenize_ingredients,
    CONTEXT_LEN, PAD_ID, UNK_ID,
)

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
MODEL_PATH = os.getenv("MODEL_PATH", "/app/model.pth")
LOG_REQUESTS = os.getenv("LOG_REQUESTS", "true").lower() == "true"
REQUEST_LOG_BUCKET = os.getenv("REQUEST_LOG_BUCKET", "logs-proj01")
SERVING_VERSION = os.getenv("SERVING_VERSION", "pytorch-baseline")

# ------------------------------------------------------------------
# Custom Prometheus metrics (application-level)
# The lab adds similar per-class and per-confidence metrics in
# eval-online-chi/fastapi_pt/app.py; we adapt for substitution ranking.
# ------------------------------------------------------------------
TOP1_SCORE = Histogram(
    "subst_top1_embedding_score",
    "Embedding score of the top-1 suggestion (for drift detection)",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

OOV_MISSING = Counter(
    "subst_oov_missing_total",
    "Requests where the missing ingredient was OOV (not in vocab)",
)

MODEL_LOADED = Gauge(
    "subst_model_loaded",
    "1 if a real trained model is loaded, 0 if running on stub weights",
)

INFLIGHT = Gauge(
    "subst_inflight_requests",
    "Number of requests currently being processed by this pod",
)

# ------------------------------------------------------------------
# Model loading
# ------------------------------------------------------------------
_model: Optional[SubstitutionModel] = None
_vocab: dict = {}
_id_to_ingredient: dict = {}


def load_model():
    """Load trained checkpoint; fall back to stub if unavailable.
    Never raises — the pod always starts, even during incidents."""
    global _model, _vocab, _id_to_ingredient

    if os.path.exists(MODEL_PATH):
        try:
            # weights_only=False because the checkpoint dict contains
            # vocab + config in addition to state_dict (see INTEGRATION.md)
            checkpoint = torch.load(MODEL_PATH, map_location="cpu",
                                     weights_only=False)
            vocab = checkpoint["vocab"]
            config = checkpoint.get("config", {})
            embed_dim = config.get("embed_dim", 128)

            model = SubstitutionModel(
                vocab_size=len(vocab), embed_dim=embed_dim)
            model.load_state_dict(checkpoint["model_state_dict"])
            model.eval()

            _model = model
            _vocab = vocab
            _id_to_ingredient = {v: k for k, v in vocab.items()}
            MODEL_LOADED.set(1)
            print(f"[startup] Loaded trained model from {MODEL_PATH}")
            print(f"[startup] vocab_size={len(vocab)}, embed_dim={embed_dim}")
            return

        except Exception as e:
            print(f"[startup] Failed to load {MODEL_PATH}: {e}")
            print(f"[startup] Falling back to stub model")

    model, vocab, id_to_ingredient = build_stub_vocab_and_model()
    model.eval()
    _model = model
    _vocab = vocab
    _id_to_ingredient = id_to_ingredient
    MODEL_LOADED.set(0)
    print(f"[startup] Running with STUB model (random weights), "
          f"vocab_size={len(vocab)}")


# ------------------------------------------------------------------
# Request logging to object storage (background thread)
# Privacy safeguarding: stores only request_id, recipe_id, ingredient
# names, timestamp. No user identity.
# ------------------------------------------------------------------
_s3_client = None
_s3_lock = threading.Lock()


def _get_s3():
    global _s3_client
    with _s3_lock:
        if _s3_client is None:
            import boto3
            _s3_client = boto3.client(
                "s3",
                endpoint_url=os.getenv("OS_ENDPOINT"),
                aws_access_key_id=os.getenv("OS_ACCESS_KEY"),
                aws_secret_access_key=os.getenv("OS_SECRET_KEY"),
            )
        return _s3_client


def log_request(request_id: str, payload: dict, result: dict):
    if not LOG_REQUESTS or not os.getenv("OS_ENDPOINT"):
        return

    def _upload():
        try:
            entry = {
                "request_id": request_id,
                "recipe_id": payload.get("recipe_id", ""),
                "missing_ingredient":
                    payload.get("missing_ingredient", {}).get("normalized", ""),
                "top_substitutions": result.get("substitutions", [])[:3],
                "serving_version": SERVING_VERSION,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime()),
            }
            key = f"requests/request_{int(time.time())}_{request_id}.json"
            _get_s3().put_object(
                Bucket=REQUEST_LOG_BUCKET, Key=key,
                Body=json.dumps(entry))
        except Exception as e:
            print(f"[log_request] Background upload failed: {e}")

    threading.Thread(target=_upload, daemon=True).start()


# ------------------------------------------------------------------
# Request / Response schemas
# (Must match sample_data/input_sample.json and output_sample.json —
# this is the cross-team contract documented in INTEGRATION.md)
# ------------------------------------------------------------------
class IngredientEntry(BaseModel):
    raw: str
    normalized: str


class MissingIngredient(BaseModel):
    raw: str
    normalized: str


class PredictRequest(BaseModel):
    recipe_id: str
    recipe_title: Optional[str] = ""
    ingredients: List[IngredientEntry] = []
    instructions: List[str] = []
    missing_ingredient: MissingIngredient
    top_k: Optional[int] = 3


class SubstitutionResponse(BaseModel):
    ingredient: str
    rank: int
    embedding_score: float


class PredictResponse(BaseModel):
    request_id: str
    recipe_id: str
    missing_ingredient: str
    substitutions: List[SubstitutionResponse]
    serving_version: str


# ------------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------------
app = FastAPI(
    title="Ingredient Substitution API (PyTorch baseline)",
    description=("PyTorch baseline serving endpoint. See serve_onnx.py "
                 "for production-default ONNX backend."),
    version=SERVING_VERSION,
)


@app.on_event("startup")
def _startup():
    load_model()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": bool(MODEL_LOADED._value.get() == 1),
        "vocab_size": len(_vocab),
        "serving_version": SERVING_VERSION,
    }


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    request_id = f"req_{uuid.uuid4().hex[:8]}"
    INFLIGHT.inc()

    try:
        top_k = max(1, min(req.top_k or 3, 10))

        ingredient_strings = [e.normalized for e in req.ingredients]
        context_ids = tokenize_ingredients(ingredient_strings, _vocab,
                                             context_len=CONTEXT_LEN)

        missing_key = req.missing_ingredient.normalized.lower().strip()
        missing_id = _vocab.get(missing_key, UNK_ID)
        if missing_id == UNK_ID:
            OOV_MISSING.inc()

        substitutions = _model.get_substitutions(
            context_ids=context_ids,
            missing_id=missing_id,
            id_to_ingredient=_id_to_ingredient,
            top_k=top_k,
        )

        result = {
            "request_id": request_id,
            "recipe_id": req.recipe_id,
            "missing_ingredient": req.missing_ingredient.normalized,
            "substitutions": substitutions,
            "serving_version": SERVING_VERSION,
        }

        if substitutions:
            TOP1_SCORE.observe(substitutions[0]["embedding_score"])

        log_request(request_id, req.model_dump(), result)
        return result

    except Exception as e:
        print(f"[predict] ERROR request_id={request_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        INFLIGHT.dec()


# ------------------------------------------------------------------
# Auto-instrumentation — MUST be at the end of the file, after all
# routes are registered (this is the lab pattern)
# ------------------------------------------------------------------
Instrumentator().instrument(app).expose(app)

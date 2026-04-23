"""
serve_pytorch.py — PyTorch baseline (for serving options table)

Changes from previous version:
  - REQUEST_LOG_BUCKET: logs-proj01 → data-proj01
  - Log key prefix: requests/ → logs/requests/
  - Response: added model_version + latency_ms, kept serving_version
  - Accepts optional request_id + timestamp from client
"""

import os, json, time, uuid, threading
from typing import Optional, List

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Histogram, Counter, Gauge

from model_stub import (
    SubstitutionModel, build_stub_vocab_and_model, tokenize_ingredients,
    CONTEXT_LEN, PAD_ID, UNK_ID,
)

MODEL_PATH = os.getenv("MODEL_PATH", "/app/model.pth")
MODEL_METADATA_PATH = os.getenv("MODEL_METADATA_PATH", "/app/model_metadata.json")
LOG_REQUESTS = os.getenv("LOG_REQUESTS", "true").lower() == "true"
REQUEST_LOG_BUCKET = os.getenv("REQUEST_LOG_BUCKET", "data-proj01")
SERVING_VERSION = os.getenv("SERVING_VERSION", "pytorch-baseline")

TOP1_SCORE = Histogram("subst_top1_embedding_score",
    "Top-1 embedding score", buckets=[0.0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0])
OOV_MISSING = Counter("subst_oov_missing_total", "Missing ingredient was OOV")
MODEL_LOADED = Gauge("subst_model_loaded", "1=real model, 0=stub")
INFLIGHT = Gauge("subst_inflight_requests", "In-flight requests")
REQUEST_LATENCY = Histogram(
    "subst_request_latency_seconds",
    "End-to-end request latency in seconds",
    ["status"],
    buckets=[0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 2.0, 5.0],
)
REQUESTS_TOTAL = Counter(
    "subst_requests_total",
    "Total inference requests",
    ["status"],
)

_model = None
_vocab = {}
_id_to_ingredient = {}
_model_version = "unknown"


def _load_model_version_from_metadata():
    global _model_version
    if not os.path.exists(MODEL_METADATA_PATH):
        return
    try:
        with open(MODEL_METADATA_PATH) as f:
            metadata = json.load(f)
        _model_version = (
            metadata.get("model_version")
            or metadata.get("run_name")
            or metadata.get("run_id")
            or _model_version)
    except Exception as e:
        print(f"[startup] Model metadata load failed: {e}")


def load_model():
    global _model, _vocab, _id_to_ingredient, _model_version
    _load_model_version_from_metadata()
    if os.path.exists(MODEL_PATH):
        try:
            ckpt = torch.load(MODEL_PATH, map_location="cpu", weights_only=False)
            vocab = ckpt["vocab"]
            config = ckpt.get("config", {})
            embed_dim = config.get("embed_dim", 128)
            model = SubstitutionModel(vocab_size=len(vocab), embed_dim=embed_dim)
            model.load_state_dict(ckpt["model_state_dict"])
            model.eval()
            _model = model
            _vocab = vocab
            _id_to_ingredient = {v: k for k, v in vocab.items()}
            _model_version = config.get("model_version") or config.get("run_name") or _model_version
            MODEL_LOADED.set(1)
            print(f"[startup] Loaded model. vocab={len(vocab)} embed_dim={embed_dim}")
            return
        except Exception as e:
            print(f"[startup] Load failed: {e}, using stub")
    model, vocab, id_to_ing = build_stub_vocab_and_model()
    model.eval()
    _model = model
    _vocab = vocab
    _id_to_ingredient = id_to_ing
    MODEL_LOADED.set(0)
    print(f"[startup] Running with STUB model")


_s3_client = None
_s3_lock = threading.Lock()

def _get_s3():
    global _s3_client
    with _s3_lock:
        if _s3_client is None:
            import boto3
            _s3_client = boto3.client("s3",
                endpoint_url=os.getenv("OS_ENDPOINT"),
                aws_access_key_id=os.getenv("OS_ACCESS_KEY"),
                aws_secret_access_key=os.getenv("OS_SECRET_KEY"))
        return _s3_client

def log_request(request_id, payload, result):
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
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            key = f"logs/requests/request_{int(time.time())}_{request_id}.json"
            _get_s3().put_object(Bucket=REQUEST_LOG_BUCKET, Key=key,
                                Body=json.dumps(entry))
        except Exception as e:
            print(f"[log_request] Failed: {e}")
    threading.Thread(target=_upload, daemon=True).start()


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
    request_id: Optional[str] = None
    timestamp: Optional[str] = None
    top_k: Optional[int] = 3

class SubstitutionItem(BaseModel):
    ingredient: str
    rank: int
    embedding_score: float

class PredictResponse(BaseModel):
    recipe_id: str
    missing_ingredient: str
    request_id: str
    substitutions: List[SubstitutionItem]
    model_version: str
    serving_version: str
    latency_ms: int


app = FastAPI(title="Ingredient Substitution API (PyTorch baseline)",
              version=SERVING_VERSION)

@app.on_event("startup")
def _startup():
    load_model()

@app.get("/health")
def health():
    return {"status": "ok",
            "model_loaded": bool(MODEL_LOADED._value.get() == 1),
            "vocab_size": len(_vocab),
            "model_version": _model_version,
            "serving_version": SERVING_VERSION}

@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    start = time.perf_counter()
    request_id = req.request_id or f"req_{uuid.uuid4().hex[:8]}"
    status = "success"
    INFLIGHT.inc()
    try:
        top_k = max(1, min(req.top_k or 3, 10))
        context_ids = tokenize_ingredients(
            [e.normalized for e in req.ingredients], _vocab)
        missing_key = req.missing_ingredient.normalized.lower().strip()
        missing_id = _vocab.get(missing_key, UNK_ID)
        if missing_id == UNK_ID:
            OOV_MISSING.inc()

        ctx_t = torch.tensor([context_ids], dtype=torch.long)
        miss_t = torch.tensor([missing_id], dtype=torch.long)
        with torch.no_grad():
            scores = _model(ctx_t, miss_t).squeeze(0)
        scores[PAD_ID] = -float("inf")
        scores[UNK_ID] = -float("inf")
        if 0 <= missing_id < len(scores):
            scores[missing_id] = -float("inf")
        top_vals, top_ids = torch.topk(scores, k=top_k)
        substitutions = [
            {"ingredient": _id_to_ingredient.get(idx.item(), f"<id_{idx.item()}>"),
             "rank": r,
             "embedding_score": round(max(0.0, min(1.0, val.item())), 4)}
            for r, (idx, val) in enumerate(zip(top_ids, top_vals), 1)
        ]

        latency_ms = int((time.perf_counter() - start) * 1000)
        result = {
            "recipe_id": req.recipe_id,
            "missing_ingredient": req.missing_ingredient.normalized,
            "request_id": request_id,
            "substitutions": substitutions,
            "model_version": _model_version,
            "serving_version": SERVING_VERSION,
            "latency_ms": latency_ms,
        }
        if substitutions:
            TOP1_SCORE.observe(substitutions[0]["embedding_score"])
        log_request(request_id, req.model_dump(), result)
        return result
    except Exception as e:
        status = "error"
        print(f"[predict] ERROR {request_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        duration = time.perf_counter() - start
        REQUESTS_TOTAL.labels(status=status).inc()
        REQUEST_LATENCY.labels(status=status).observe(duration)
        INFLIGHT.dec()

Instrumentator().instrument(app).expose(app)

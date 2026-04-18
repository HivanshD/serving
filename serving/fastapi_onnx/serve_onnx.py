"""
serve_onnx.py

FastAPI + ONNX Runtime inference endpoint for ingredient substitution.
PRODUCTION DEFAULT for April 20.

Pattern based on:
  - Online Evaluation lab (eval-online-chi/fastapi_pt/app.py) for the
    prometheus-fastapi-instrumentator + prometheus-client pattern
  - System Optimizations lab (serve-system-chi/fastapi_onnx) for the
    ONNX Runtime inference path

Why this as production default (with 4 uvicorn workers + HPA 1-4):
  - Single-request p50/p95 = 0.6ms/1.2ms (from initial implementation)
  - 4 workers * up to 4 HPA replicas = 16 concurrent request slots
  - CPU-based HPA scales before p95 degrades under bursty load
  - No GPU lease needed = reliable 2-week operation window
  - Model is tiny so GPU overhead is wasted
  - For the "best throughput under bursty load" option, Triton ONNX GPU
    stays in the repo as subst_model_onnx — swap via K8S image change

Endpoints:
  POST /predict    — main inference endpoint
  GET  /health     — readiness/liveness probe
  GET  /metrics    — auto-instrumented + custom Prometheus metrics

Environment variables:
  OS_ENDPOINT, OS_ACCESS_KEY, OS_SECRET_KEY  — object storage
  ONNX_MODEL_PATH  (default /app/model.onnx)
  VOCAB_PATH       (default /app/vocab.json)
  LOG_REQUESTS     (default "true")
  REQUEST_LOG_BUCKET (default "logs-proj01")
  ORT_THREADS      (default 2)   — intra-op threads per worker
  SERVING_VERSION  (default "onnx-quantized")
"""

import os
import json
import time
import uuid
import threading
from typing import Optional, List

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Histogram, Counter, Gauge

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
ONNX_MODEL_PATH = os.getenv("ONNX_MODEL_PATH", "/app/model.onnx")
VOCAB_PATH = os.getenv("VOCAB_PATH", "/app/vocab.json")
LOG_REQUESTS = os.getenv("LOG_REQUESTS", "true").lower() == "true"
REQUEST_LOG_BUCKET = os.getenv("REQUEST_LOG_BUCKET", "logs-proj01")
SERVING_VERSION = os.getenv("SERVING_VERSION", "onnx-quantized")

CONTEXT_LEN = 20
PAD_ID = 0
UNK_ID = 1

# ------------------------------------------------------------------
# Custom application metrics (lab pattern: Histogram/Counter/Gauge
# from prometheus_client on top of auto-instrumentation)
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
    "1 if a real trained ONNX model is loaded, 0 if running on stub",
)

INFLIGHT = Gauge(
    "subst_inflight_requests",
    "Number of requests currently being processed by this pod",
)

# ------------------------------------------------------------------
# Model / vocab state
# ------------------------------------------------------------------
_session: Optional[ort.InferenceSession] = None
_vocab: dict = {}
_id_to_ingredient: dict = {}
_stub_embeddings: Optional[np.ndarray] = None


def _build_stub_state():
    """Stub vocab + embeddings for smoke testing when no model is available."""
    global _vocab, _id_to_ingredient, _stub_embeddings

    common = [
        "sour cream", "greek yogurt", "plain yogurt", "buttermilk",
        "butter", "olive oil", "cream cheese", "heavy cream",
        "milk", "water", "sugar", "brown sugar", "honey", "maple syrup",
        "flour", "all purpose flour", "whole wheat flour", "almond flour",
        "salt", "pepper", "garlic", "onion", "tomato", "basil",
        "cheddar", "mozzarella", "parmesan", "feta",
        "chicken", "beef", "pork", "tofu", "tempeh",
        "egg", "egg white", "flax egg", "chia egg",
    ]
    vocab = {"<PAD>": PAD_ID, "<UNK>": UNK_ID}
    for c in common:
        vocab[c] = len(vocab)
    while len(vocab) < 10000:
        vocab[f"ingredient_{len(vocab)}"] = len(vocab)

    np.random.seed(42)
    embeds = np.random.randn(len(vocab), 128).astype(np.float32) * 0.1

    _vocab = vocab
    _id_to_ingredient = {v: k for k, v in vocab.items()}
    _stub_embeddings = embeds


def load_model():
    """Load ONNX session + vocab. Fall back to stub on any failure."""
    global _session, _vocab, _id_to_ingredient

    # Vocabulary
    if os.path.exists(VOCAB_PATH):
        try:
            with open(VOCAB_PATH) as f:
                raw = json.load(f)
            _vocab = {str(k): int(v) for k, v in raw.items()}
            _id_to_ingredient = {v: k for k, v in _vocab.items()}
            print(f"[startup] Loaded vocabulary ({len(_vocab)} entries) "
                  f"from {VOCAB_PATH}")
        except Exception as e:
            print(f"[startup] Failed to load vocabulary: {e}")
            _build_stub_state()
            MODEL_LOADED.set(0)
            return

    # ONNX session
    if not os.path.exists(ONNX_MODEL_PATH):
        print(f"[startup] ONNX model not found at {ONNX_MODEL_PATH}")
        _build_stub_state()
        MODEL_LOADED.set(0)
        return

    try:
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL)
        sess_options.intra_op_num_threads = int(os.getenv("ORT_THREADS", "2"))
        sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

        # Lab pattern from System Optimizations: CPUExecutionProvider by default
        _session = ort.InferenceSession(
            ONNX_MODEL_PATH,
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )

        inputs = {i.name: i.shape for i in _session.get_inputs()}
        outputs = {o.name: o.shape for o in _session.get_outputs()}
        print(f"[startup] ONNX model loaded from {ONNX_MODEL_PATH}")
        print(f"[startup]   Inputs:  {inputs}")
        print(f"[startup]   Outputs: {outputs}")

        _warmup()
        MODEL_LOADED.set(1)

    except Exception as e:
        print(f"[startup] Failed to load ONNX model: {e}")
        _session = None
        if not _vocab:
            _build_stub_state()
        MODEL_LOADED.set(0)


def _warmup():
    if _session is None:
        return
    dummy_ctx = np.zeros((1, CONTEXT_LEN), dtype=np.int64)
    dummy_miss = np.zeros((1,), dtype=np.int64)
    try:
        for _ in range(3):
            _session.run(None, {
                "context_ids": dummy_ctx,
                "missing_id": dummy_miss,
            })
        print("[startup] Warmup complete")
    except Exception as e:
        print(f"[startup] Warmup failed (non-fatal): {e}")


# ------------------------------------------------------------------
# Inference
# ------------------------------------------------------------------
def infer(context_ids: List[int], missing_id: int,
           top_k: int = 3) -> list:
    if _session is not None:
        scores = _infer_onnx(context_ids, missing_id)
    else:
        scores = _infer_stub(context_ids, missing_id)

    scores = scores.copy()
    scores[PAD_ID] = -np.inf
    scores[UNK_ID] = -np.inf
    if 0 <= missing_id < len(scores):
        scores[missing_id] = -np.inf

    top_k = min(top_k, len(scores))
    top_unsorted = np.argpartition(-scores, top_k - 1)[:top_k]
    top_idx = top_unsorted[np.argsort(-scores[top_unsorted])]

    out = []
    for rank, idx in enumerate(top_idx.tolist(), start=1):
        display_score = float(max(0.0, min(1.0, scores[idx])))
        out.append({
            "ingredient": _id_to_ingredient.get(int(idx), f"<id_{idx}>"),
            "rank": rank,
            "embedding_score": round(display_score, 4),
        })
    return out


def _infer_onnx(context_ids: List[int], missing_id: int) -> np.ndarray:
    ctx = np.array([context_ids], dtype=np.int64)
    miss = np.array([missing_id], dtype=np.int64)
    out = _session.run(None, {"context_ids": ctx, "missing_id": miss})
    return out[0][0]


def _infer_stub(context_ids: List[int], missing_id: int) -> np.ndarray:
    ctx = np.array(context_ids, dtype=np.int64)
    ctx_mask = (ctx != PAD_ID).astype(np.float32)
    ctx_embeds = _stub_embeddings[ctx]
    ctx_sum = (ctx_embeds * ctx_mask[:, None]).sum(0)
    ctx_count = max(float(ctx_mask.sum()), 1.0)
    ctx_vec = ctx_sum / ctx_count

    miss_vec = _stub_embeddings[missing_id]
    query = ctx_vec + miss_vec
    query = query / (np.linalg.norm(query) + 1e-9)

    candidates = _stub_embeddings / (
        np.linalg.norm(_stub_embeddings, axis=-1, keepdims=True) + 1e-9)
    return candidates @ query


def tokenize_ingredients(ingredient_strings: List[str]) -> List[int]:
    ids = []
    for ing in ingredient_strings:
        if isinstance(ing, str):
            ids.append(_vocab.get(ing.lower().strip(), UNK_ID))
    ids = ids[:CONTEXT_LEN]
    ids += [PAD_ID] * (CONTEXT_LEN - len(ids))
    return ids


# ------------------------------------------------------------------
# Privacy-safe request logging (background thread)
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
    title="Ingredient Substitution API (ONNX — production default)",
    description="ONNX-backed ingredient substitution serving endpoint.",
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
        context_ids = tokenize_ingredients(ingredient_strings)

        missing_key = req.missing_ingredient.normalized.lower().strip()
        missing_id = _vocab.get(missing_key, UNK_ID)
        if missing_id == UNK_ID:
            OOV_MISSING.inc()

        substitutions = infer(context_ids, missing_id, top_k=top_k)

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
# Auto-instrumentation — must come AFTER all routes are registered
# (lab pattern from Online Evaluation lab)
# ------------------------------------------------------------------
Instrumentator().instrument(app).expose(app)

"""
serve_onnx.py - Production FastAPI + ONNX Runtime endpoint

INTEGRATION CONTRACTS (verified against actual teammate code):
  - Primary data bucket: data-proj01 (request logs, default model keys)
  - Request logs → data-proj01/logs/requests/  (drift_monitor.py reads here)
  - Model artifacts: MODEL_BUCKET + MODEL_MANIFEST_KEY (reload_model.py), or
    data-proj01/models/production/ when using direct keys
  - Stub fallback matches training's architecture: .mean(dim=1), no padding mask
  - Response includes serving_version, model_version, and latency_ms
"""

import json
import os
import threading
import time
import uuid
from typing import List, Optional

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------
ONNX_MODEL_PATH = os.getenv("ONNX_MODEL_PATH", "/app/model.onnx")
VOCAB_PATH = os.getenv("VOCAB_PATH", "/app/vocab.json")
MODEL_METADATA_PATH = os.getenv("MODEL_METADATA_PATH", "/app/model_metadata.json")
MODEL_BUCKET = os.getenv("MODEL_BUCKET", "data-proj01")
ONNX_MODEL_KEY = os.getenv("ONNX_MODEL_KEY", "models/production/subst_model_current.onnx")
VOCAB_KEY = os.getenv("VOCAB_KEY", "models/production/vocab.json")
MODEL_METADATA_KEY = os.getenv("MODEL_METADATA_KEY", "models/production/model_metadata.json")
MODEL_REFRESH_INTERVAL_SEC = int(os.getenv("MODEL_REFRESH_INTERVAL_SEC", "60"))
LOG_REQUESTS = os.getenv("LOG_REQUESTS", "true").lower() == "true"
REQUEST_LOG_BUCKET = os.getenv("REQUEST_LOG_BUCKET", "data-proj01")
SERVING_VERSION = os.getenv("SERVING_VERSION", "onnx-quantized")
CONTEXT_LEN = 20
PAD_ID = 0
UNK_ID = 1

# ------------------------------------------------------------------
# Prometheus metrics
# ------------------------------------------------------------------
TOP1_SCORE = Histogram(
    "subst_top1_embedding_score",
    "Top-1 embedding score",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)
REQUEST_LATENCY = Histogram(
    "subst_request_latency_seconds",
    "Request latency in seconds",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)
REQUESTS = Counter("subst_requests_total", "Requests by status", ["status"])
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

# ------------------------------------------------------------------
# State
# ------------------------------------------------------------------
_session: Optional[ort.InferenceSession] = None
_vocab: dict = {}
_id_to_ingredient: dict = {}
_stub_embeddings: Optional[np.ndarray] = None
_model_version: str = "unknown"
_model_object_version: Optional[str] = None
_last_refresh_check = 0.0
_refresh_lock = threading.Lock()


def _build_stub_state():
    global _vocab, _id_to_ingredient, _stub_embeddings, _model_version
    common = [
        "flour",
        "egg",
        "sugar",
        "butter",
        "milk",
        "salt",
        "pepper",
        "oil",
        "garlic",
        "onion",
        "tomato",
        "chicken",
        "beef",
        "rice",
        "pasta",
        "cheese",
        "cream",
        "lemon",
        "herbs",
        "vanilla",
        "baking_powder",
        "yeast",
        "water",
        "vinegar",
        "honey",
        "soy_sauce",
        "ginger",
        "cinnamon",
        "nutmeg",
        "paprika",
        "cumin",
        "oregano",
        "basil",
        "thyme",
        "rosemary",
        "potato",
        "carrot",
        "celery",
        "mushroom",
        "spinach",
        "sour cream",
        "greek yogurt",
        "cream cheese",
        "buttermilk",
        "heavy cream",
        "all-purpose flour",
        "beef sirloin",
        "beef broth",
        "egg noodles",
    ]
    vocab = {"<PAD>": PAD_ID, "<UNK>": UNK_ID}
    for c in common:
        if c not in vocab:
            vocab[c] = len(vocab)
    np.random.seed(42)
    embeds = np.random.randn(len(vocab), 128).astype(np.float32) * 0.1
    _vocab = vocab
    _id_to_ingredient = {v: k for k, v in vocab.items()}
    _stub_embeddings = embeds
    _model_version = "stub"


def _ensure_stub_embeddings():
    global _stub_embeddings, _model_version
    if _stub_embeddings is not None:
        return
    if not _vocab:
        _build_stub_state()
        return
    np.random.seed(42)
    _stub_embeddings = np.random.randn(len(_vocab), 128).astype(np.float32) * 0.1
    if _model_version == "unknown":
        _model_version = "stub"


def _load_metadata():
    global _model_version
    if not os.path.exists(MODEL_METADATA_PATH):
        return
    try:
        with open(MODEL_METADATA_PATH) as f:
            metadata = json.load(f)
        _model_version = metadata.get("run_name") or metadata.get("run_id") or _model_version
    except Exception as e:
        print(f"[startup] Metadata load failed: {e}")


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
    global _session, _vocab, _id_to_ingredient

    _load_model_version_from_metadata()

    if os.path.exists(VOCAB_PATH):
        try:
            with open(VOCAB_PATH) as f:
                raw = json.load(f)
            _vocab = {str(k): int(v) for k, v in raw.items()}
            _id_to_ingredient = {v: k for k, v in _vocab.items()}
            print(f"[startup] Vocab loaded ({len(_vocab)} entries)")
        except Exception as e:
            print(f"[startup] Vocab load failed: {e}")
            _build_stub_state()
            MODEL_LOADED.set(0)
            return
    else:
        print(f"[startup] No vocab at {VOCAB_PATH}")

    if not os.path.exists(ONNX_MODEL_PATH):
        print(f"[startup] No ONNX model at {ONNX_MODEL_PATH}")
        _ensure_stub_embeddings()
        MODEL_LOADED.set(0)
        return

    try:
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = int(os.getenv("ORT_THREADS", "2"))
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        _session = ort.InferenceSession(
            ONNX_MODEL_PATH,
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        _load_metadata()
        print(f"[startup] ONNX loaded. Inputs: {[i.name for i in _session.get_inputs()]}")
        _warmup()
        MODEL_LOADED.set(1)
    except Exception as e:
        print(f"[startup] ONNX load failed: {e}")
        _session = None
        _ensure_stub_embeddings()
        MODEL_LOADED.set(0)


def _warmup():
    if not _session:
        return
    ctx = np.zeros((1, CONTEXT_LEN), dtype=np.int64)
    miss = np.zeros((1,), dtype=np.int64)
    try:
        for _ in range(3):
            _session.run(None, {"context_ids": ctx, "missing_id": miss})
        print("[startup] Warmup done")
    except Exception as e:
        print(f"[startup] Warmup failed (non-fatal): {e}")


# ------------------------------------------------------------------
# Inference
# ------------------------------------------------------------------
def infer(context_ids, missing_id, top_k=3):
    if _session is not None:
        scores = _infer_onnx(context_ids, missing_id)
    else:
        scores = _infer_stub(context_ids, missing_id)
    scores = scores.copy()
    scores[PAD_ID] = -np.inf
    if UNK_ID < len(scores):
        scores[UNK_ID] = -np.inf
    if 0 <= missing_id < len(scores):
        scores[missing_id] = -np.inf
    top_k = min(top_k, len(scores))
    top_idx = np.argsort(scores)[::-1][:top_k]
    return [
        {
            "ingredient": _id_to_ingredient.get(int(i), f"<id_{i}>"),
            "rank": r,
            "embedding_score": round(float(max(0, min(1, scores[i]))), 4),
        }
        for r, i in enumerate(top_idx.tolist(), 1)
    ]


def _infer_onnx(context_ids, missing_id):
    ctx = np.array([context_ids], dtype=np.int64)
    miss = np.array([missing_id], dtype=np.int64)
    return _session.run(None, {"context_ids": ctx, "missing_id": miss})[0][0]


def _infer_stub(context_ids, missing_id):
    ctx_embeds = _stub_embeddings[np.array(context_ids, dtype=np.int64)]
    ctx_vec = ctx_embeds.mean(axis=0)
    miss_vec = _stub_embeddings[missing_id]
    query = ctx_vec + miss_vec
    q_norm = query / (np.linalg.norm(query) + 1e-9)
    all_norm = _stub_embeddings / (
        np.linalg.norm(_stub_embeddings, axis=-1, keepdims=True) + 1e-9
    )
    return all_norm @ q_norm


def tokenize_ingredients(ingredient_strings):
    ids = [_vocab.get(s.lower().strip(), UNK_ID) for s in ingredient_strings if isinstance(s, str)]
    ids = ids[:CONTEXT_LEN]
    ids += [PAD_ID] * (CONTEXT_LEN - len(ids))
    return ids


# ------------------------------------------------------------------
# Object storage access
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


def _download_optional(bucket, key, destination):
    try:
        _get_s3().download_file(Bucket=bucket, Key=key, Filename=destination)
        return True
    except Exception as e:
        print(f"[model_refresh] Optional download skipped for {bucket}/{key}: {e}")
        return False


def _maybe_refresh_model(force=False):
    global _last_refresh_check, _model_object_version
    if not os.getenv("OS_ENDPOINT"):
        return
    now = time.time()
    if not force and (now - _last_refresh_check) < MODEL_REFRESH_INTERVAL_SEC:
        return

    with _refresh_lock:
        now = time.time()
        if not force and (now - _last_refresh_check) < MODEL_REFRESH_INTERVAL_SEC:
            return
        _last_refresh_check = now
        try:
            model_head = _get_s3().head_object(Bucket=MODEL_BUCKET, Key=ONNX_MODEL_KEY)
            vocab_head = _get_s3().head_object(Bucket=MODEL_BUCKET, Key=VOCAB_KEY)
            current_version = f'{model_head.get("ETag", "")}|{vocab_head.get("ETag", "")}'
            if not force and current_version == _model_object_version:
                return

            tmp_model = f"{ONNX_MODEL_PATH}.download"
            tmp_vocab = f"{VOCAB_PATH}.download"
            _get_s3().download_file(Bucket=MODEL_BUCKET, Key=ONNX_MODEL_KEY, Filename=tmp_model)
            _get_s3().download_file(Bucket=MODEL_BUCKET, Key=VOCAB_KEY, Filename=tmp_vocab)
            os.replace(tmp_model, ONNX_MODEL_PATH)
            os.replace(tmp_vocab, VOCAB_PATH)
            _download_optional(MODEL_BUCKET, MODEL_METADATA_KEY, MODEL_METADATA_PATH)
            _model_object_version = current_version
            print(f"[model_refresh] Loaded latest model artifacts from {MODEL_BUCKET}")
            load_model()
        except Exception as e:
            print(f"[model_refresh] Refresh skipped: {e}")


# ------------------------------------------------------------------
# Request logging -> data-proj01/logs/requests/
# ------------------------------------------------------------------
def log_request(request_id, payload, result, status="ok", error_detail=None):
    if not LOG_REQUESTS or not os.getenv("OS_ENDPOINT"):
        return

    def _upload():
        try:
            entry = {
                "request_id": request_id,
                "recipe_id": payload.get("recipe_id", ""),
                "missing_ingredient": payload.get("missing_ingredient", {}).get("normalized", ""),
                "top_substitutions": result.get("substitutions", [])[:3],
                "serving_version": SERVING_VERSION,
                "model_version": _model_version,
                "status": status,
                "latency_ms": result.get("latency_ms", 0),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if error_detail:
                entry["error_detail"] = error_detail[:200]
            key = f"logs/requests/request_{int(time.time())}_{request_id}.json"
            _get_s3().put_object(Bucket=REQUEST_LOG_BUCKET, Key=key, Body=json.dumps(entry))
        except Exception as e:
            print(f"[log_request] Failed: {e}")

    threading.Thread(target=_upload, daemon=True).start()


# ------------------------------------------------------------------
# Schemas
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


# ------------------------------------------------------------------
# App
# ------------------------------------------------------------------
app = FastAPI(title="Ingredient Substitution API", version=SERVING_VERSION)


@app.on_event("startup")
def _startup():
    _maybe_refresh_model(force=True)
    if _session is None:
        load_model()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": bool(MODEL_LOADED._value.get() == 1),
        "vocab_size": len(_vocab),
        "model_version": _model_version,
        "serving_version": SERVING_VERSION,
    }


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    start = time.perf_counter()
    request_id = req.request_id or f"req_{uuid.uuid4().hex[:8]}"
    status = "success"
    INFLIGHT.inc()
    try:
        _maybe_refresh_model()
        top_k = max(1, min(req.top_k or 3, 10))
        context_ids = tokenize_ingredients([e.normalized for e in req.ingredients])
        missing_key = req.missing_ingredient.normalized.lower().strip()
        missing_id = _vocab.get(missing_key, UNK_ID)
        if missing_id == UNK_ID:
            OOV_MISSING.inc()
        substitutions = infer(context_ids, missing_id, top_k=top_k)
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
        REQUEST_LATENCY.observe(time.perf_counter() - start)
        REQUESTS.labels(status="ok").inc()
        log_request(request_id, req.model_dump(), result, status="ok")
        return result
    except Exception as e:
        status = "error"
        print(f"[predict] ERROR {request_id}: {e}")
        elapsed = time.perf_counter() - start
        latency_ms = int(elapsed * 1000)
        REQUEST_LATENCY.observe(elapsed)
        REQUESTS.labels(status="error").inc()
        log_request(
            request_id,
            req.model_dump(),
            {
                "recipe_id": req.recipe_id,
                "missing_ingredient": req.missing_ingredient.normalized,
                "request_id": request_id,
                "substitutions": [],
                "model_version": _model_version,
                "serving_version": SERVING_VERSION,
                "latency_ms": latency_ms,
            },
            status="error",
            error_detail=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        duration = time.perf_counter() - start
        REQUESTS_TOTAL.labels(status=status).inc()
        REQUEST_LATENCY.labels(status=status).observe(duration)
        INFLIGHT.dec()


Instrumentator().instrument(app).expose(app)

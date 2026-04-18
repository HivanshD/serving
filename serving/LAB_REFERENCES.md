# LAB_REFERENCES.md

Every non-trivial code pattern in this serving component, and which lab
it's based on. The course policy is explicit:

> "Wherever possible, you should use the lab assignments as a starting
>  point for code or configs (like a human would!), and build on that
>  rather than starting from scratch."

This document is the evidence that we did.

---

## Lab 7: System Optimizations for Serving
(`teaching-on-testbeds.github.io/serve-system-chi/`)

| Pattern used here | Lab section |
|-------------------|-------------|
| FastAPI wrapper for a PyTorch model with `/predict` endpoint | "Preparing an endpoint in FastAPI" |
| Docker Compose bringing up `fastapi_server` with a model | "Bring up containers" |
| FastAPI + ONNX Runtime using `CPUExecutionProvider` | "ONNX version" |
| Triton Python backend with `config.pbtxt` (`backend: "python"`) | "Anatomy of a Triton model with Python backend" |
| Triton ONNX backend with `backend: "onnxruntime"` | "Serving an ONNX model" |
| `max_batch_size: 16` and `instance_group { kind: KIND_GPU }` | "Triton ONNX backend config" |
| `dynamic_batching { preferred_batch_size: [...] }` | "Dynamic batching with ONNX model" |
| `perf_analyzer -u triton_server:8000 -m <model> -b 1 --concurrency-range ...` | "Benchmark the service" |
| `nvcr.io/nvidia/tritonserver:24.01-py3` base image with opset 14 | "Preparing the Triton container" |

**Files in this repo that follow these patterns:**
- `fastapi_pt/serve_pytorch.py`
- `fastapi_onnx/serve_onnx.py`
- `models/subst_model/config.pbtxt` (Python backend config)
- `models/subst_model_onnx/config.pbtxt` (ONNX backend config)
- `models/subst_model/1/model.py` (Python backend handler)
- `docker/Dockerfile.fastapi_pt`
- `docker/Dockerfile.fastapi_onnx`
- `docker/Dockerfile.triton`
- `docker/docker-compose-fastapi.yaml`
- `docker/docker-compose-triton.yaml`
- `sample_data/input_triton.json` (perf_analyzer input format)

---

## Lab 8: Online Evaluation of ML Systems
(`teaching-on-testbeds.github.io/eval-online-chi/`)

| Pattern used here | Lab section |
|-------------------|-------------|
| `prometheus-fastapi-instrumentator` in requirements.txt | "Monitor operational metrics" |
| `Instrumentator().instrument(app).expose(app)` at end of app.py | "Monitor operational metrics" |
| Custom `Histogram` and `Counter` from `prometheus_client` for application-level metrics | "Monitor predictions" |
| Prometheus scrape config with `scrape_interval: 15s` and `targets: ['fastapi_server:8000']` | "Monitor operational metrics" |
| Grafana dashboard panels for p50/p95/p99 latency using `histogram_quantile(...)` | "Build a dashboard" |
| Alert rule on error rate exceeding a threshold | "Alert on operational metrics" |

**Files in this repo that follow these patterns:**
- `fastapi_pt/serve_pytorch.py` — custom `TOP1_SCORE`, `OOV_MISSING`, `INFLIGHT` metrics + `Instrumentator().instrument(app).expose(app)` at end
- `fastapi_onnx/serve_onnx.py` — same pattern
- `fastapi_pt/requirements.txt` — `prometheus-client`, `prometheus-fastapi-instrumentator`
- `fastapi_onnx/requirements.txt` — same
- `scripts/check_rollback.py` — uses `histogram_quantile(0.95, rate(...)_bucket[10m])` for alerting, same pattern as Lab 8's error-rate alert
- `scripts/check_promote.py` — same

---

## Lab 6: Model Optimizations for Serving
(`teaching-on-testbeds.github.io/serve-model-chi/`)

| Pattern used here | Lab section |
|-------------------|-------------|
| `torch.onnx.export` with `opset_version=14` | "Convert PyTorch to ONNX" |
| `ort.InferenceSession(onnx_path, providers=['CPUExecutionProvider'])` | "Try a different execution provider" |
| ONNX dynamic quantization (we use `onnxruntime.quantization` instead of `neural_compressor` — see note below) | "Quantize the ONNX model" |
| Measure model size via `os.path.getsize(onnx_path)` | "Benchmark the quantized model" |

**Files in this repo that follow these patterns:**
- `scripts/export_onnx.py` — uses opset 14 explicitly
- `scripts/quantize_onnx.py` — uses `onnxruntime.quantization.quantize_dynamic`

**Deviation from the lab (documented):**
The lab uses Intel Neural Compressor (`neural_compressor.quantization.fit`)
for quantization. We hit breaking API changes in INC during initial
implementation. Our `quantize_onnx.py` uses ORT's built-in
`quantize_dynamic` as a stable alternative. Trade-off: we don't do
accuracy-driven post-training quantization (INC's killer feature), but
for an embedding-only model the accuracy delta is negligible and we get
comparable size/speed gains. Documented in `DEPLOYMENT_DECISIONS.md`.

---

## Lab 5: ML Experiment Tracking with MLFlow
(`teaching-on-testbeds.github.io/mlflow-chi/`)

This is primarily the Training team's lab reference, but serving interacts
with MLflow through:

| Pattern used here | Lab section |
|-------------------|-------------|
| MLflow tracking URI pattern `http://mlflow.<host>:5000` | "Run Pytorch code with MLFlow logging" |
| Model registration (`mlflow.register_model(...)`) so serving can pull by name | "Register this model in the MLFlow model registry" |
| Model URI format `runs:/{run_id}/model` | "Register this model" |

**Files in this repo that interact with MLflow:**
- `INTEGRATION.md` — documents that Training registers with `mlflow.register_model` and DevOps automation watches for candidates

---

## Lab 2: Persistent Storage on Chameleon
(Data platforms lab — `teaching-on-testbeds.github.io/data-platform-chi/`)

The Data Platforms lab uses MinIO on port 9000 for object storage with
an S3-compatible API. We follow the same pattern.

| Pattern used here | Lab section |
|-------------------|-------------|
| MinIO container at port 9000 (API) and 9001 (console) | "MinIO + MinIO init" |
| `boto3.client("s3", endpoint_url=..., aws_access_key_id=..., aws_secret_access_key=...)` | "MinIO init: set up buckets" |
| Bucket-per-purpose separation | "prepare some database tables" (pattern analog) |

**Files in this repo that follow these patterns:**
- `fastapi_pt/serve_pytorch.py` `_get_s3()` — boto3 client with endpoint_url
- `fastapi_onnx/serve_onnx.py` — same
- `scripts/reload_model.py` — same boto3 pattern
- `scripts/export_onnx.py` `export_from_object_storage()` — same
- `scripts/quantize_onnx.py` `quantize_from_object_storage()` — same

---

## Versions and packages — lab-aligned

The labs do NOT pin exact package versions in their `requirements.txt`.
Example from `eval-online-chi/fastapi_pt/requirements.txt` (paraphrased):

```
fastapi
uvicorn
torch
pillow
numpy
prometheus-client
prometheus-fastapi-instrumentator
alibi-detect
```

Our `requirements.txt` files follow the same convention — unpinned,
letting `pip` resolve compatible versions at build time. This matches
the labs, and it also avoids the specific version conflicts we hit in
initial implementation (INC vs ONNX, opset 20 vs 14, etc.).

**The ONE version we DO pin is the Triton base image:**
`nvcr.io/nvidia/tritonserver:24.01-py3` — because opset 14 is required
for Triton 24.01 and changing the Triton version without re-verifying
opset is a known footgun.

---

## What is NOT from the labs (our additions)

To be transparent about what we wrote beyond the labs:

| Addition | Why |
|----------|-----|
| Background-thread object storage logging | Lab doesn't use object storage for request logs; we need this for the data team's feedback loop |
| Stub-mode fallback on model download failure | Lab's Dockerfile assumes the model is present; we need to survive incidents |
| `subst_inflight_requests` gauge | Useful for future HPA on queue depth; lab only uses CPU-based metrics |
| Triton `dynamic_batching { max_queue_delay_microseconds: 100 }` | Lab shows the pattern but uses default delay; we tuned to 100 based on our load profile |
| 4-worker uvicorn | Lab uses 1 worker (demo scale); we scale up for bursty traffic |
| `check_rollback.py` and `check_promote.py` | Course project requirement (automated rollback/promote), built on top of Lab 8's alert patterns |

All additions are justified in `DEPLOYMENT_DECISIONS.md`.

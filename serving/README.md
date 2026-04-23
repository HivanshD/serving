# Serving — Ingredient Substitution for Mealie

ECE-GY 9183 | proj01 | Serving role | Hivansh

This directory contains everything needed to serve the ingredient
substitution model inside the integrated Mealie ML system.

---

## What's here

| Path | What it is |
|------|------------|
| `fastapi_pt/` | PyTorch FastAPI baseline (serving options table: baseline row) |
| `fastapi_onnx/` | **PRODUCTION DEFAULT** — ONNX Runtime FastAPI on CPU |
| `models/subst_model/` | Triton Python backend (benchmark comparison) |
| `models/subst_model_onnx/` | Triton ONNX backend (high-throughput GPU option) |
| `scripts/` | Model download, ONNX export, quantization, benchmarking, rollback/promote checks |
| `docker/` | Dockerfiles + Docker Compose for local dev and Chameleon benchmarking |
| `sample_data/` | `input_sample.json`, `output_sample.json` — the shared team contract |

---

## Why FastAPI + ONNX (quantized) on CPU for production

Benchmarks from initial implementation (P100 nodes on CHI@TACC):

| Option | p50 | p95 | Throughput | Hardware |
|--------|-----|-----|-----------|----------|
| FastAPI + PyTorch CPU | 2.1 ms | 3.8 ms | ~480 req/s | CPU |
| FastAPI + ONNX CPU | 0.8 ms | 1.5 ms | ~1250 req/s | CPU |
| **FastAPI + ONNX Quantized CPU** | **0.6 ms** | **1.2 ms** | **~1670 req/s** | **CPU** |
| Triton Python GPU | 1.73 ms | 1.97 ms | 576 req/s | 1× P100 |
| Triton ONNX GPU | ~0.5 ms | ~1.0 ms | ~3000 req/s | 1× P100 |

ONNX quantized on CPU beats Triton Python on GPU and is only marginally
slower than Triton ONNX on GPU — but uses no GPU lease. The model is tiny
(embedding lookup + cosine similarity) so GPU overhead dominates.

Triton ONNX GPU is kept in the repo as the "high-throughput" row in the
serving options table.

---

## Startup model loading

All serving containers follow the same pattern:

1. Container starts → `scripts/reload_model.py` runs first
2. In the rollout path, it resolves an environment-specific manifest from `models-proj01/manifests/{staging,canary,production}.json`
3. The manifest points at versioned candidate artifacts under `models-proj01/versions/<model_version>/`
4. If download fails, containers still start with stub random weights so
   pods don't crash-loop during incidents
5. Uvicorn / tritonserver launches and binds port 8000

---

## Running locally (for development)

```bash
cd serving/docker

# Both endpoints at once — PyTorch on 8000, ONNX on 8001
docker compose -f docker-compose-fastapi.yaml up --build

# Test PyTorch
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d @../sample_data/input_sample.json

# Test ONNX
curl -X POST http://localhost:8001/predict \
  -H "Content-Type: application/json" \
  -d @../sample_data/input_sample.json

# Prometheus metrics
curl http://localhost:8000/metrics
```

---

## Running on Chameleon (for re-benchmarking)

```bash
# On a compute node with Docker installed
cd serving
export OS_ENDPOINT=http://<MINIO_IP>:9000
export OS_ACCESS_KEY=<your_key>
export OS_SECRET_KEY=<your_secret>

docker compose -f docker/docker-compose-fastapi.yaml up -d --build

# Benchmark
python scripts/benchmark.py \
  --url http://localhost:8001/predict \
  --input sample_data/input_sample.json \
  --concurrency 1 4 8 16 \
  --n 200 \
  --option_name fastapi_onnx_quantized_cpu \
  --model_version $(git rev-parse --short HEAD)
```

For Triton GPU benchmarking, see `docker/docker-compose-triton.yaml`.

---

## Production deployment (K8S)

The K8S manifests live in `../infra/k8s/` and are owned by DevOps.
Serving provides the container image tags they reference:

| Image tag | Built from | Deployed to |
|-----------|-----------|-------------|
| `subst-serving-onnx:v{git_sha}` | `Dockerfile.fastapi_onnx` | `staging-proj01`, `canary-proj01`, `production-proj01` |
| `subst-serving-pt:v{git_sha}` | `Dockerfile.fastapi_pt` | (optional baseline) |
| `subst-triton:v{git_sha}` | `Dockerfile.triton` | (benchmarking only) |

The canonical rollout path is now the multi-environment layout under:

- `infra/k8s/platform/`
- `infra/k8s/staging/`
- `infra/k8s/canary/`
- `infra/k8s/production/`

The older app-oriented path under `infra/k8s/apps/substitution-serving/` is
still useful as a bootstrap base, but it is no longer the main system-
implementation path.

---

## Integration with other roles

See [INTEGRATION.md](./INTEGRATION.md) for the full contract with Training,
Data, and DevOps — what files get shared, what env vars are required,
what gets read from / written to object storage.

---

## Safeguarding items implemented in this directory

| Principle | Implementation | Location |
|-----------|---------------|----------|
| Privacy | Request logs store only `request_id`, `recipe_id`, ingredient names, top-k. No user identity. | `log_request()` in `serve_pytorch.py` / `serve_onnx.py` |
| Transparency | `serving_version` returned with every response so Mealie can show source | `/predict` response |
| Explainability | `embedding_score` returned for every suggestion (Mealie displays as %) | `/predict` response |
| Robustness | Stub fallback on model download failure → pod doesn't crashloop during incidents | `reload_model.py` + `load_model()` fallback |

---

## Change log

- **2026-04-17** — added Prometheus metrics, request logging, rollback/promote scripts for April 20 system implementation
- **2026-04-06** — initial implementation: 7 experiments, 5 configurations benchmarked

---

Assisted by Claude Sonnet 4.6 and Claude Opus 4.7.

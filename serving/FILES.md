# FILES.md

Every file in this directory, why it exists, and what depends on it.
If you ever think "can I delete this?" — check this document first.

Files are grouped by the lifecycle stage they participate in:
build, startup, request handling, operations, documentation.

---

## Top-level

### `README.md`
**Purpose:** First thing anyone opening the repo reads.
**Why it exists:** The rubric explicitly says "Graders must be able to
reproduce the project from scratch using only the repositories." The
README has the serving options table, local-run commands, and points
to the other docs.
**Depends on:** nothing
**Things that depend on it:** nothing directly, but it's the entry point

### `INTEGRATION.md`
**Purpose:** Cross-team contract document. Answers "how do I plug into
the serving component?" for training, data, devops.
**Why it exists:** Without this, team members guess at the shared
schema or env var names and everything breaks at integration time.
This document codifies the contract so integration is deterministic.
**Depends on:** `sample_data/*.json` (for the schema), the decisions in
`DEPLOYMENT_DECISIONS.md`
**Things that depend on it:** training team's checkpoint format, data
team's `online_features.py`, devops team's K8S manifests, Mealie
integration backend code

### `DEPLOYMENT_DECISIONS.md`
**Purpose:** Explains WHY I chose what I chose. Answers "why FastAPI
over Triton?" "why 4 workers?" "why these thresholds?"
**Why it exists:** The rubric grades on "justifying those choices and
trade-offs using course concepts." Without this doc, nobody (including
future me) can remember why any particular number was picked.
**Depends on:** benchmark results from initial implementation
**Things that depend on it:** future changes — if you want to modify a
threshold or swap a backend, read this first to understand what you're
giving up

### `FILES.md`
**Purpose:** This document. The "what does every file do" reference.
**Why it exists:** With 30+ files, it's easy to get lost. New teammates
joining the project (or me coming back in a week) need a map.
**Depends on:** the actual files
**Things that depend on it:** nothing

### `RUNBOOK.md`
**Purpose:** Operations guide. What to do when specific things break
in production.
**Why it exists:** At 3am during the demo week, when a pod is in
CrashLoopBackOff, this is what you read instead of scrolling logs for
30 minutes. Also mandatory for the rubric: the course emphasizes
"operating ML systems" as a core learning objective.
**Depends on:** the Grafana alert rules, the automation webhook,
Prometheus metric names
**Things that depend on it:** nobody programmatically, but on-call uses it

### `.gitignore`
**Purpose:** Tells git what NOT to commit.
**Why it exists:** Prevents accidentally committing model weights
(which go in object storage, not git), secrets, cached Python files.
A single `.pth` accidentally committed would bloat the repo and leak
model weights.
**Depends on:** nothing
**Things that depend on it:** git

### `.dockerignore`
**Purpose:** Tells Docker what NOT to copy into the build context.
**Why it exists:** Without this, `docker build` sends the entire git
history, virtual envs, and cached model files to the Docker daemon on
every build. This can turn a 30-second build into a 5-minute build.
**Depends on:** nothing
**Things that depend on it:** `docker build` operations

### `Makefile`
**Purpose:** One-command operations: `make build-onnx`, `make smoke`,
`make burst RATE=20`.
**Why it exists:** Nobody remembers the exact docker compose incantation
or the right flags for benchmark.py. The Makefile is living documentation
of "the commands I actually use to operate this component."
**Depends on:** docker, docker-compose, python scripts
**Things that depend on it:** human memory

---

## Model code (shared architecture)

### `fastapi_pt/model_stub.py`
**Purpose:** The canonical `SubstitutionModel` class definition.
Architecture, vocab constants, tokenization helper, stub model factory.
**Why it exists:** Three pieces of code need to agree on the model
architecture exactly: the training script, the PyTorch serving path,
and the Triton Python backend. This file is the source of truth. If
training and serving disagree on architecture, `load_state_dict` fails
with cryptic errors.
**Depends on:** torch
**Things that depend on it:**
 - `fastapi_pt/serve_pytorch.py` imports SubstitutionModel to run inference
 - `scripts/export_onnx.py` imports SubstitutionModel to load checkpoint
   before exporting
 - `training/train.py` (Training team's file) — imports SubstitutionModel
   so the architecture it trains matches what we serve
 - `models/subst_model/1/model.py` — Triton Python backend imports it
 - `docker/Dockerfile.triton` copies it into `/models/subst_model/1/`

---

## FastAPI PyTorch serving (baseline)

### `fastapi_pt/serve_pytorch.py`
**Purpose:** FastAPI app that serves inference using PyTorch directly.
Includes /health, /predict, /metrics endpoints, Prometheus metrics, and
privacy-preserving request logging.
**Why it exists:** The serving options table requires a baseline.
Running PyTorch directly (no ONNX conversion, no quantization) is the
reference point against which all optimizations are compared.
**Depends on:** `model_stub.py`, pytorch, fastapi, prometheus-client, boto3
**Things that depend on it:** Dockerfile.fastapi_pt starts this; the
baseline row in the serving options table reports its p50/p95

### `fastapi_pt/requirements.txt`
**Purpose:** Pins Python dependencies for the PyTorch serving container.
**Why it exists:** Reproducible builds. Without pinned versions, a
`pip install torch` today and a `pip install torch` next week could
pull different binaries with different performance characteristics.
**Depends on:** nothing
**Things that depend on it:** Dockerfile.fastapi_pt

---

## FastAPI ONNX serving (production default)

### `fastapi_onnx/serve_onnx.py`
**Purpose:** FastAPI app that serves inference using ONNX Runtime.
This is the PRODUCTION backend. Same endpoint contract as
serve_pytorch.py — they're hot-swappable in K8S.
**Why it exists:** ONNX quantized on CPU is 3-4x faster than raw PyTorch
(0.6ms vs 2.1ms p50) and uses no GPU. It's the pragmatic production
choice for this model size.
**Depends on:** onnxruntime, fastapi, prometheus-client, boto3. Needs
the ONNX model file + vocab JSON at container startup (downloaded by
reload_model.py).
**Things that depend on it:** Dockerfile.fastapi_onnx runs this; all
three K8S environments (staging, canary, production) deploy it

### `fastapi_onnx/requirements.txt`
Same reason as `fastapi_pt/requirements.txt` but for the ONNX container.

---

## Triton configs (high-throughput alternative)

### `models/subst_model/config.pbtxt`
**Purpose:** Triton Inference Server config for the Python backend
version of the model. Specifies input/output tensor names and shapes,
max batch size, dynamic batching settings.
**Why it exists:** The serving options table needs a row for "GPU +
Python backend" to demonstrate why Python backend is slower than ONNX
backend. This is that configuration.
**Depends on:** `models/subst_model/1/model.py` exists
**Things that depend on it:** Triton at startup scans `/models/` and
reads this file. `docker-compose-triton.yaml` references the model name.

### `models/subst_model/1/model.py`
**Purpose:** Triton Python backend handler. Triton calls its
`initialize()` at model load and `execute()` on each batched request.
**Why it exists:** Needed for the "Triton Python backend GPU" row in
the serving options table.
**Depends on:** torch, numpy, triton_python_backend_utils (from Triton
container), `model_stub.py` (copied next to it at build time)
**Things that depend on it:** Triton loads this when
`config.pbtxt` points at it

### `models/subst_model_onnx/config.pbtxt`
**Purpose:** Triton config for the ONNX Runtime backend.
**Why it exists:** The "best throughput" / "best bursty load" row in
the serving options table. Dynamic batching on GPU via Triton ONNX
was the best throughput option in initial implementation benchmarks.
**Depends on:** `models/subst_model_onnx/1/model.onnx` downloaded at startup
**Things that depend on it:** Triton loads this

### `models/subst_model_onnx/1/README.txt`
**Purpose:** Placeholder in the directory structure. Notes that the
actual ONNX model is downloaded at container startup, not committed.
**Why it exists:** Git doesn't track empty directories. Without some
file here, the directory wouldn't exist in the repo and Triton would
fail when trying to load the model.
**Depends on:** nothing
**Things that depend on it:** git's ability to preserve the directory

---

## Scripts (build-time and runtime operations)

### `scripts/reload_model.py`
**Purpose:** Downloads the current production model from object
storage at container startup.
**Why it exists:** We can't bake the model into the container image
because (a) images would be huge, (b) we'd need a new container build
on every retrain, (c) K8S pods wouldn't auto-pick-up new models.
Instead, models live in object storage and each pod downloads on start.
**Depends on:** boto3, object storage accessible
**Things that depend on it:** Dockerfile.fastapi_pt, Dockerfile.fastapi_onnx,
and Dockerfile.triton all invoke this at startup via CMD

### `scripts/export_onnx.py`
**Purpose:** Converts a trained PyTorch checkpoint into ONNX format
at opset 14 (required for Triton 24.01).
**Why it exists:** The production serving container uses ONNX, but
training produces PyTorch checkpoints. Something has to bridge them.
This runs after a training run passes the quality gate.
**Depends on:** torch, onnx, onnxruntime, model_stub.py
**Things that depend on it:** Training team's pipeline invokes this
after save_and_register. Also runnable standalone for manual exports.

### `scripts/quantize_onnx.py`
**Purpose:** Applies dynamic INT8 quantization to the ONNX model.
Smaller file, faster inference on CPU.
**Why it exists:** Quantization was one of our best optimizations
(p95 went from 1.5ms unquantized to 1.2ms quantized). Needs a dedicated
script because it runs AFTER export_onnx.py and BEFORE serving reads
the model.
**Depends on:** onnxruntime.quantization
**Things that depend on it:** The production deployment (optional but
recommended). Training team's pipeline can invoke this as a post-processing
step after export.

### `scripts/benchmark.py`
**Purpose:** Measures p50/p95/p99 latency and throughput at multiple
concurrency levels. Used to populate the serving options table.
**Why it exists:** The rubric requires "a variety of serving options
... appropriate to validate the expected benefit of each optimization."
This script produces the numbers that go in the table.
**Depends on:** requests, a running serving endpoint
**Things that depend on it:** Me, when filling in the serving options
table. Also DevOps for right-sizing evidence.

### `scripts/load_test_burst.py`
**Purpose:** Simulates Poisson-distributed bursty traffic at a target
rate. Tests whether serving survives realistic traffic patterns.
**Why it exists:** `benchmark.py` tests ideal-case performance at
fixed concurrency. Real traffic is bursty, and bursts are what break
things. This script catches problems that `benchmark.py` misses.
Run this before the demo.
**Depends on:** requests, a running serving endpoint
**Things that depend on it:** Me, before recording the demo, to verify
production readiness

### `scripts/smoke_test.py`
**Purpose:** Quick end-to-end sanity check. Verifies /health, /metrics,
and /predict all work correctly.
**Why it exists:** After any deployment, you want a 5-second check
that the new pod is actually functional. Smoke tests catch dumb mistakes
(wrong image, missing env var, misconfigured service) before a load
test would.
**Depends on:** requests, the running serving endpoint
**Things that depend on it:** DevOps automation can invoke this as
part of the staging -> canary promotion gate

### `scripts/check_rollback.py`
**Purpose:** K8S CronJob script. Queries Prometheus for production
health metrics. Calls the DevOps automation webhook to rollback if
thresholds are breached.
**Why it exists:** The rubric requires "automated model rollback
process that kicks in if the production system is not doing well."
This implements that for serving.
**Depends on:** Prometheus reachable, automation webhook reachable
**Things that depend on it:** `k8s-cronjob-manifests.yaml` schedules this;
DevOps automation webhook receives its rollback calls

### `scripts/check_promote.py`
**Purpose:** K8S CronJob script. Checks if canary has been healthy
long enough to be promoted to production. Calls automation webhook
to promote.
**Why it exists:** The rubric requires "automated promotion of new
model versions with well-justified rules." This implements it.
**Depends on:** Prometheus, automation webhook, kubectl (for deployment age)
**Things that depend on it:** `k8s-cronjob-manifests.yaml` schedules this

### `scripts/triton_startup.sh`
**Purpose:** Entrypoint for the Triton container. Downloads model
files from object storage before tritonserver starts.
**Why it exists:** Same reason as reload_model.py — models live in
object storage, not baked in. Triton has its own startup sequence so
it needs a bash wrapper instead of the Python-only reload pattern.
**Depends on:** reload_model.py, tritonserver binary
**Things that depend on it:** Dockerfile.triton uses this as CMD

---

## Docker (build configuration)

### `docker/Dockerfile.fastapi_pt`
**Purpose:** Builds the PyTorch baseline serving container.
**Why it exists:** Needed for the baseline row in the serving options
table. Also useful as a sanity check: if this works but Dockerfile.fastapi_onnx
doesn't, the problem is in the ONNX pipeline, not the model itself.
**Depends on:** `fastapi_pt/` code, `scripts/reload_model.py`, python:3.11-slim base image
**Things that depend on it:** `make build-pt`, `docker-compose-fastapi.yaml`

### `docker/Dockerfile.fastapi_onnx`
**Purpose:** Builds the production ONNX serving container. Runs 4
uvicorn workers per pod.
**Why it exists:** This is THE production image. `subst-serving-onnx:*`
tags are built from this.
**Depends on:** `fastapi_onnx/` code, `scripts/reload_model.py`,
`scripts/check_rollback.py`, `scripts/check_promote.py`
**Things that depend on it:** Every K8S deployment that runs serving;
both CronJobs (they reuse this image); `make build-onnx`

### `docker/Dockerfile.triton`
**Purpose:** Builds the Triton container with both Python and ONNX
backends configured.
**Why it exists:** Needed for the "best throughput" row in the serving
options table. Also available as a swap-in production option if
FastAPI proves insufficient.
**Depends on:** `models/` directory, `scripts/triton_startup.sh`,
`scripts/reload_model.py`, `model_stub.py`, Triton 24.01 base image
**Things that depend on it:** `make build-triton`, `docker-compose-triton.yaml`

### `docker/docker-compose-fastapi.yaml`
**Purpose:** Brings up both PyTorch and ONNX FastAPI servers side-by-side
on ports 8000 and 8001 for local development.
**Why it exists:** Manual `docker run` with all the env vars is
error-prone. Compose makes local dev one command.
**Depends on:** both Dockerfiles
**Things that depend on it:** `make up`, manual local development

### `docker/docker-compose-triton.yaml`
**Purpose:** Brings up Triton server + SDK client for GPU benchmarking
on Chameleon.
**Why it exists:** Triton needs the NVIDIA Docker runtime and specific
shared-memory settings. This file encodes all of that so you don't
have to remember `--gpus all --shm-size=1g ...` every time.
**Depends on:** Dockerfile.triton, NVIDIA Docker runtime on the host
**Things that depend on it:** Benchmarking Triton on Chameleon GPU nodes

---

## Sample data (cross-team contract)

### `sample_data/input_sample.json`
**Purpose:** Canonical example of the /predict request payload.
**Why it exists:** Three teams need to agree on this schema: data
(`online_features.py` produces it), serving (we consume it), Mealie
backend (constructs it). The file IS the contract. If the schema
changes, this file changes, and everyone notices because their tests
break.
**Depends on:** nothing
**Things that depend on it:** benchmark.py, smoke_test.py,
load_test_burst.py, Data team's online_features, Mealie integration

### `sample_data/output_sample.json`
**Purpose:** Canonical example of the /predict response.
**Why it exists:** Data team's `data_generator.py` needs to know what
shape to expect. Mealie frontend needs to know what fields to display.
**Depends on:** nothing
**Things that depend on it:** Data's data_generator, Mealie's Vue
component

### `sample_data/input_triton.json`
**Purpose:** perf_analyzer input data for Triton benchmarking. Triton
uses a different input format (tensors by name with explicit shapes).
**Why it exists:** Can't reuse input_sample.json for Triton because
Triton's /v2/inference API is different from FastAPI's JSON.
**Depends on:** nothing
**Things that depend on it:** `perf_analyzer` commands when benchmarking
Triton

---

## Kubernetes (DevOps-adjacent)

### `k8s-cronjob-manifests.yaml`
**Purpose:** K8S manifests for the two serving-owned CronJobs
(check_rollback and check_promote) plus a ServiceAccount with the
necessary RBAC for check_promote.py to read deployment ages.
**Why it exists:** DevOps owns most K8S manifests, but these specific
CronJobs run serving scripts with serving-specific logic and thresholds.
It's clearer for serving to own the specs and hand them to DevOps to apply.
**Depends on:** `subst-serving-onnx` image being built and pushed;
Prometheus + automation webhook being reachable
**Things that depend on it:** DevOps's `kubectl apply` workflow

---

## What if I want to add a new file?

Ask yourself:
 1. What's the purpose (one sentence)?
 2. Why does it exist — what breaks if I delete it?
 3. What does it depend on?
 4. What depends on it?

If you can't answer those, you don't need the file. If you can,
add it to this document when you commit it.

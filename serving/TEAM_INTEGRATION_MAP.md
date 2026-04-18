# TEAM_INTEGRATION_MAP.md

The plain-English guide to what every role's work must connect to.
If you're on this team, read this top to bottom before writing any code.

---

# The 4 shared plumbing points

Everything on the team connects through ONE of these four places:

1. **`data-proj01`** object storage bucket — training data and retraining triggers
2. **`models-proj01`** object storage bucket — model files
3. **`logs-proj01`** object storage bucket — production logs and user feedback
4. **K8S internal DNS** (HTTP calls inside the cluster) — real-time request flow

That's it. If your piece of code doesn't read from or write to one of these
four places, something is wrong.

---

# Role 1: DATA (owner of `online_features.py`, `ingest.py`, feedback endpoint, generator)

## What Data WRITES:

| File/Service | Where it writes | Who reads it |
|--------------|-----------------|--------------|
| `ingest.py` | `data-proj01/raw/recipe1msubs/train.json` (+ val, test) | **Training** reads this to train the initial model |
| `batch_pipeline.py` | `data-proj01/processed/train_v{timestamp}.json` | **Training** reads this during retraining |
| `batch_pipeline.py` | `data-proj01/triggers/retrain_{timestamp}.json` | **Training's `watch_trigger.py`** picks this up every 30 min |
| `feedback_endpoint.py` | `logs-proj01/feedback/feedback_{timestamp}_{id}.json` | **Data's own `batch_pipeline.py`** reads these for retraining |

## What Data READS:

| File/Service | Where it reads from | Who wrote it |
|--------------|---------------------|--------------|
| `batch_pipeline.py` | `logs-proj01/feedback/` | Feedback endpoint (i.e., Mealie frontend calls feedback endpoint, which writes here) |
| `drift_monitor.py` | `logs-proj01/requests/` | **Serving** writes every inference request here |
| `drift_monitor.py` | `data-proj01/raw/recipe1msubs/train.json` | Data's own `ingest.py` wrote this earlier |

## What Data provides TO OTHER ROLES as code:

| Deliverable | Who needs it | Why |
|-------------|--------------|-----|
| `online_features.py` | **Mealie backend** (team integrates together) | Normalizes raw Mealie ingredient strings like "1 cup sour cream" → "sour cream" before calling serving |
| `sample_data/input_sample.json` schema alignment | **Everyone** | Data's `online_features.py` must produce exactly this format; Serving consumes it |

## What Data runs as K8S services:

- `feedback_endpoint.py` → Deployment + Service at `subst-feedback.production-proj01:8001/feedback`
- `data_generator.py` → Deployment that runs continuously during demo, sending traffic to Serving
- `ingest.py` → K8S Job (runs once, pre-deadline)

## What Data runs as CronJobs (DevOps writes the YAMLs):

- `batch_pipeline.py` — daily, in `monitoring-proj01` namespace
- `drift_monitor.py` — every 6 hours, in `monitoring-proj01`

---

# Role 2: TRAINING (owner of `train.py`, `evaluate.py`, `watch_trigger.py`)

## What Training WRITES:

| File/Service | Where it writes | Who reads it |
|--------------|-----------------|--------------|
| `train.py` `save_and_register()` | `models-proj01/checkpoints/subst_model_v{run_id}.pth` | (Archive for rollback history) |
| `train.py` `save_and_register()` | `models-proj01/production/subst_model_current.pth` | **Serving PyTorch** pod reads this at startup |
| `train.py` (then `export_onnx.py`) | `models-proj01/production/subst_model_current.onnx` | **Serving ONNX** pod reads this at startup |
| `train.py` (then `export_onnx.py`) | `models-proj01/production/vocab.json` | **Serving ONNX** pod reads this at startup |
| `train.py` via `mlflow.log_*` | MLflow at `mlflow.monitoring-proj01:5000` | **DevOps automation** watches MLflow for new candidate models |

## What Training READS:

| File/Service | Where it reads from | Who wrote it |
|--------------|---------------------|--------------|
| `train.py` (initial run) | `data-proj01/raw/recipe1msubs/{train,val}.json` | **Data's `ingest.py`** |
| `train.py` (retraining) | `data-proj01/processed/train_v{timestamp}.json` | **Data's `batch_pipeline.py`** |
| `watch_trigger.py` | `data-proj01/triggers/retrain_*.json` | **Data's `batch_pipeline.py`** |

## What Training MUST MATCH with Serving:

**The single most important cross-team contract:**
Training's `SubstitutionModel` class must be **byte-identical** to Serving's
`serving/fastapi_pt/model_stub.py`. Either:
 - Training imports from `serving/fastapi_pt/model_stub.py` (recommended)
 - OR Training copies the file into its own directory

If they drift, `load_state_dict` fails at serving startup with cryptic errors.

**The checkpoint dict format is also a contract:**
```python
torch.save({
  "model_state_dict": model.state_dict(),
  "vocab": {"<PAD>": 0, "<UNK>": 1, "sour cream": 2, ...},
  "config": {"embed_dim": 128, "lr": 0.001, ...}
}, "subst_model_v{run_id}.pth")
```

## What Training runs as K8S services:

- `watch_trigger.py` → CronJob every 30 min in `monitoring-proj01` (DevOps writes the YAML)
- `train.py` → started by `watch_trigger.py` as a K8S Job (one-shot per training run)

---

# Role 3: SERVING (me — owner of FastAPI + ONNX endpoint, rollback/promote scripts)

## What Serving WRITES:

| File/Service | Where it writes | Who reads it |
|--------------|-----------------|--------------|
| `serve_onnx.py` `log_request()` | `logs-proj01/requests/request_{timestamp}_{id}.json` | **Data's `drift_monitor.py`** |
| `/metrics` endpoint | (exposed HTTP, not a file) | **DevOps's Prometheus** scrapes this every 15s |
| `check_rollback.py` | Posts to `http://automation.monitoring-proj01:8080/rollback` | **DevOps's `automation.py`** |
| `check_promote.py` | Posts to `http://automation.monitoring-proj01:8080/promote` | **DevOps's `automation.py`** |

## What Serving READS:

| File/Service | Where it reads from | Who wrote it |
|--------------|---------------------|--------------|
| `reload_model.py` (startup) | `models-proj01/production/subst_model_current.pth` OR `subst_model_current.onnx` + `vocab.json` | **Training's `train.py`** |
| `check_rollback.py` | Prometheus at `http://prometheus.monitoring-proj01:9090/api/v1/query` | **DevOps's Prometheus** (scrapes serving's `/metrics`) |
| `check_promote.py` | Same Prometheus + `kubectl get deployment` (for canary age) | **DevOps** |

## What Serving CONSUMES as HTTP requests:

| Endpoint | Who calls it | What they send |
|----------|--------------|----------------|
| `POST /predict` | **Mealie backend** + **Data's `data_generator.py`** | JSON matching `sample_data/input_sample.json` |
| `GET /health` | K8S readiness/liveness probes (DevOps configures) | — |
| `GET /metrics` | **DevOps's Prometheus** (every 15s) | — |

## What Serving runs as K8S services:

- Serving → Deployment + Service at:
  - `subst-serving.production-proj01:8000`
  - `subst-serving.canary-proj01:8000`
  - `subst-serving.staging-proj01:8000`
- `check_rollback.py` → CronJob every 5 min in `production-proj01` (reuses serving image)
- `check_promote.py` → CronJob every 5 min in `canary-proj01` (reuses serving image)

## What Serving MUST MATCH with Training:

- `model_stub.py` exact class definition (`SubstitutionModel`)
- Checkpoint dict keys: `model_state_dict`, `vocab`, `config`
- Vocab must contain `<PAD>` at index 0, `<UNK>` at index 1

---

# Role 4: DEVOPS (owner of K8S cluster, namespaces, automation, monitoring stack)

## What DevOps MUST BUILD:

| Component | Where it runs | What it does |
|-----------|---------------|--------------|
| 4 K8S namespaces | cluster-wide | `staging-proj01`, `canary-proj01`, `production-proj01`, `monitoring-proj01` |
| MinIO (or equivalent) | `monitoring-proj01` | Hosts the 3 object storage buckets (`data-proj01`, `models-proj01`, `logs-proj01`) |
| MLflow | `monitoring-proj01` | Training logs runs here; DevOps automation watches for new candidates |
| Prometheus | `monitoring-proj01` | Scrapes `/metrics` from every serving pod (via pod annotations) |
| Grafana | `monitoring-proj01` | Dashboards + alert rules |
| Mealie | `production-proj01` | The actual open-source service we're extending |
| `automation.py` webhook | `monitoring-proj01` | Exposes `/rollback` and `/promote` endpoints |
| `os-credentials` Secret | EVERY namespace | Holds MinIO access keys — EVERYTHING needs this to access buckets |
| NGINX Ingress | cluster-wide | Canary traffic splitting (90% prod / 10% canary) |
| HPA on serving | `production-proj01` | Scales 1-4 pods on CPU 70% |

## What DevOps RECEIVES from other roles:

| From | What | What DevOps does with it |
|------|------|--------------------------|
| **Serving** | `k8s-cronjob-manifests.yaml` | `kubectl apply -f` to deploy check_rollback and check_promote CronJobs |
| **Serving** | Container images `subst-serving-onnx:v{sha}` | Reference in K8S Deployment for all 3 environments |
| **Training** | CronJob spec for `watch_trigger.py` | Apply to `monitoring-proj01` |
| **Training** | Training container image | Reference in the CronJob spec |
| **Data** | CronJob specs for `batch_pipeline.py`, `drift_monitor.py` | Apply to `monitoring-proj01` |
| **Data** | Data container images | Reference in the CronJob specs |

## What DevOps's `automation.py` RECEIVES:

| Caller | Endpoint | What happens |
|--------|----------|--------------|
| **Serving's `check_rollback.py`** | `POST /rollback` | `kubectl rollout undo deployment/subst-serving -n production-proj01` |
| **Serving's `check_promote.py`** | `POST /promote` | `kubectl set image deployment/subst-serving ...` from canary → production |

## What DevOps MUST configure:

- Every K8S Deployment that uses object storage reads env vars from the `os-credentials` Secret
- Every serving pod has these Prometheus scrape annotations (so Prometheus finds them automatically):
  ```yaml
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "8000"
    prometheus.io/path: "/metrics"
  ```

---

# Full data flow in 8 steps (the demo video narrative)

```
Step 1: User opens a recipe in Mealie
   → Mealie UI calls Mealie backend

Step 2: Mealie backend → Data's online_features.py
   → Converts "1 cup sour cream" → "sour cream"
   → Builds JSON matching sample_data/input_sample.json

Step 3: Mealie backend → HTTP POST /predict
   → Target: subst-serving.production-proj01:8000/predict
   → Serving runs inference, returns ranked substitutions

Step 4: Serving → logs-proj01/requests/ (background)
   → Privacy-safe log entry written asynchronously

Step 5: Mealie UI shows the suggestions with "Suggested by AI" label
   → User clicks "Use this" or "Skip"

Step 6: Mealie UI → HTTP POST /feedback
   → Target: subst-feedback.production-proj01:8001/feedback
   → Data's feedback_endpoint writes to logs-proj01/feedback/

Step 7 (daily): Data's batch_pipeline.py
   → Reads logs-proj01/feedback/
   → Writes data-proj01/processed/train_v{date}.json
   → Writes data-proj01/triggers/retrain_{date}.json

Step 8 (every 30 min): Training's watch_trigger.py
   → Sees new trigger, runs train.py
   → If quality gate passes: writes to models-proj01/production/
   → Registers candidate in MLflow
   → DevOps automation sees candidate, deploys to staging
   → After 30 min canary validation: Serving's check_promote.py
     → POSTs to automation /promote
     → automation.py does kubectl set image in production-proj01
   → If production degrades: Serving's check_rollback.py
     → POSTs to automation /rollback
     → automation.py does kubectl rollout undo
```

---

# The 3 things that WILL BREAK integration (guard against these)

## 1. Model architecture drift

**Symptom:** Serving pod crashes at startup with "Error(s) in loading state_dict"

**Cause:** Training added a layer, changed `embed_dim`, or renamed a parameter.

**Fix:** Training and Serving must import from the SAME `model_stub.py`.
Either Training copies `serving/fastapi_pt/model_stub.py` into its repo, or
(better) the monorepo structure lets both import from the same file.

## 2. Ingredient normalization mismatch

**Symptom:** Predictions return `<UNK>` or obviously wrong suggestions.

**Cause:** Data's `online_features.py` produces "Sour Cream" but training's
vocab contains "sour cream". OR Training lowercased but Data didn't strip
parentheticals.

**Fix:** BOTH sides agree on: `text.lower().strip()` + strip units + strip
parentheticals. Data's `online_features.py` and Training's `build_vocab()`
both call the same normalization function. Send a sample output from
`build_serving_payload()` to Training BEFORE training starts so they can
confirm vocab keys will match.

## 3. Object storage credentials not accessible

**Symptom:** Every pod's logs say "Could not download models-proj01/..." or
"NoCredentialsError" at startup.

**Cause:** DevOps forgot to create the `os-credentials` Secret in one of
the namespaces, or the Secret has different keys than the code expects.

**Fix:** DevOps creates the Secret with EXACTLY these keys in EVERY
namespace that runs a pod:
```
OS_ENDPOINT
OS_ACCESS_KEY
OS_SECRET_KEY
```
Every Deployment references them via `valueFrom.secretKeyRef`.

---

# Summary chart: who reads/writes what

```
                         ┌─────────────────┐
                         │  data-proj01    │
                         │                 │
               writes    │ raw/            │    reads
    Data     ──────────▶ │ processed/      │ ◀────────── Training
                         │ triggers/       │
                         └─────────────────┘

                         ┌─────────────────┐
                         │  models-proj01  │
                         │                 │
               writes    │ checkpoints/    │    reads
    Training ──────────▶ │ production/     │ ◀────────── Serving
                         │ onnx/           │
                         └─────────────────┘

                         ┌─────────────────┐
                         │  logs-proj01    │
                         │                 │
               writes    │ requests/       │    reads
    Serving  ──────────▶ │ feedback/       │ ◀────────── Data
             (Mealie-UI)                   │
                         └─────────────────┘


 HTTP calls inside the cluster:

    Mealie-backend ──POST /predict──▶ Serving (production-proj01:8000)
    Mealie-UI ──POST /feedback──▶ Data's feedback endpoint (:8001)
    Serving ──exposes /metrics──▶ DevOps Prometheus
    Serving (check_rollback) ──POST /rollback──▶ DevOps automation (:8080)
    Serving (check_promote) ──POST /promote──▶ DevOps automation (:8080)
```

If you remember nothing else from this document, remember: **3 buckets + 4
HTTP endpoints**. Everything in the system is either writing to one of those
buckets, reading from one, calling one of those endpoints, or being called
by one.

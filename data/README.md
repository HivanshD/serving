# Data Team — ForkWise Ingredient Substitution

## What this folder does

Implements the **data pipeline** for the ForkWise ingredient substitution system.
Covers all three rubric-required quality checkpoints plus supporting services.

## Rubric mapping


| Rubric requirement                                       | File                | Quality checkpoint |
| -------------------------------------------------------- | ------------------- | ------------------ |
| Evaluate data quality at ingestion from external sources | `ingest.py`         | QC1                |
| Evaluate data quality when compiling training sets       | `batch_pipeline.py` | QC2                |
| Monitor live inference data quality and drift            | `drift_monitor.py`  | QC3                |


## Bucket layout

Single bucket `data-proj01` with three top-level prefixes:

```
data-proj01/
├── data/
│   ├── raw/recipe1msubs/        ← ingest.py uploads validated splits here
│   │   ├── train.json
│   │   ├── val.json
│   │   └── test.json
│   ├── processed/               ← batch_pipeline.py writes versioned datasets
│   │   └── train_v<timestamp>.json
│   ├── triggers/                ← batch_pipeline.py writes retrain triggers
│   │   └── retrain_<timestamp>.json
│   ├── production_holdout/      ← holdout set (NEVER used for training)
│   │   └── holdout.json
│   └── quality_reports/         ← QC1/QC2/QC3 JSON reports
│       ├── ingest_<ts>.json
│       ├── batch_<ts>.json
│       └── drift_<ts>.json
├── logs/
│   ├── requests/                ← serving writes prediction logs here
│   │   └── request_<ts>_<id>.json
│   └── feedback/                ← feedback_endpoint.py writes here
│       └── feedback_<ts>_<id>.json
└── models/                      ← training writes checkpoints, serving reads
    └── <model files>
```

## Files


| File                   | Purpose                                                    | Runs as                    |
| ---------------------- | ---------------------------------------------------------- | -------------------------- |
| `ingest.py`            | QC1: validate + upload Recipe1MSubs, create bucket/folders | K8s Job (one-shot)         |
| `batch_pipeline.py`    | QC2: compile versioned training data from feedback         | K8s CronJob (daily)        |
| `drift_monitor.py`     | QC3: OOV rate + confidence + volume checks                 | K8s CronJob (every 6h)     |
| `feedback_endpoint.py` | FastAPI service capturing user accept/reject               | K8s Deployment + Service   |
| `online_features.py`   | Converts Mealie recipe → serving input schema              | Imported by Mealie backend |
| `data_generator.py`    | Replays holdout records against serving endpoint           | K8s Deployment             |


## Canonical deployment assets

The canonical cloud deployment path now lives in:

- `infra/k8s/apps/forkwise-data/`
- `infra/docs/FORKWISE_CLOUD_SETUP.md`

The published GHCR images used by those manifests are:

```text
ghcr.io/itsnotaka/forkwise-ingest:demo
ghcr.io/itsnotaka/forkwise-feedback:demo
ghcr.io/itsnotaka/forkwise-batch:demo
ghcr.io/itsnotaka/forkwise-generator:demo
```

## Environment variables (all scripts)

```
OS_ENDPOINT=http://<MINIO_IP>:9000
OS_ACCESS_KEY=<key>
OS_SECRET_KEY=<secret>
BUCKET=data-proj01                  # default, override if needed
```

Script-specific:

```
# data_generator.py
SERVING_URL=http://subst-serving.production-proj01.svc.cluster.local:8000/predict
CANARY_SERVING_URL=http://subst-serving.canary-proj01.svc.cluster.local:8000/predict
CANARY_TRAFFIC_PERCENT=0.10
REQUESTS_PER_SEC=1

# batch_pipeline.py
MIN_NEW_SAMPLES=50

# drift_monitor.py
OOV_THRESHOLD=0.15
LOW_CONFIDENCE_THRESHOLD=0.5
MIN_REQUESTS_EXPECTED=10
METRICS_PORT=8002

# feedback_endpoint.py
# runs on port 8001 via uvicorn
```

## Execution order

```
1. ingest.py            ← run FIRST (creates bucket + uploads data)
2. feedback_endpoint.py ← deploy as service (always running)
3. data_generator.py    ← start sending traffic to serving
4. batch_pipeline.py    ← run after feedback accumulates
5. drift_monitor.py     ← run after serving has logged requests
```

## Cross-team integration

### Training team must change

`watch_trigger.py` line 10:

```python
# OLD:  Prefix='triggers/'
# NEW:  Prefix='data/triggers/'
```

`train.py` model upload:

```python
# OLD:  Bucket='models-proj01'
# NEW:  Bucket='data-proj01', Key=f'models/{key}'
```

### Serving team must change

`serve_pytorch.py` / `serve_onnx.py`:

```python
# OLD:  REQUEST_LOG_BUCKET default "logs-proj01"
# NEW:  REQUEST_LOG_BUCKET default "data-proj01"

# OLD:  key = f"requests/request_..."
# NEW:  key = f"logs/requests/request_..."
```

Serving must also call feedback endpoint from Mealie frontend:

```
POST http://subst-feedback.forkwise-data.svc.cluster.local:8001/feedback
```

## Docker builds

```bash
docker build -f Dockerfile.ingest   -t forkwise-ingest .
docker build -f Dockerfile.feedback -t forkwise-feedback .
docker build -f Dockerfile.batch    -t forkwise-batch .
docker build -f Dockerfile.generator -t forkwise-generator .
```

## Docker pull and run

Teammates can pull the canonical remote images directly:

```bash
docker pull ghcr.io/itsnotaka/forkwise-feedback:demo
docker pull ghcr.io/itsnotaka/forkwise-batch:demo
docker pull ghcr.io/itsnotaka/forkwise-generator:demo
docker pull ghcr.io/itsnotaka/forkwise-ingest:demo
```

If the packages are private, run `docker login ghcr.io` first.

## Testing locally

```bash
export OS_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480
export OS_ACCESS_KEY=<key>
export OS_SECRET_KEY=<secret>

# Test 1: Ingest (creates bucket + QC1)
python ingest.py

# Test 2: Feedback endpoint
uvicorn feedback_endpoint:app --port 8001
curl -X POST http://localhost:8001/feedback \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"t1","recipe_id":"123","missing_ingredient":"sour cream","suggested_substitution":"greek yogurt","user_accepted":true}'

# Test 3: Batch pipeline (QC2) — needs feedback in MinIO
MIN_NEW_SAMPLES=1 python batch_pipeline.py

# Test 4: Drift monitor (QC3) — needs serving request logs in MinIO
MIN_REQUESTS_EXPECTED=1 python drift_monitor.py

# Test 5: Data generator — needs serving endpoint up
SERVING_URL=http://localhost:8000/predict python data_generator.py
```

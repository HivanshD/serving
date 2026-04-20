# AGENTS.md — Data Team

Instructions for AI coding agents (Claude Code, Cursor, Copilot, etc.)
working on files in the `data/` directory.

## Project context

ForkWise is an ingredient substitution feature for Mealie (self-hosted recipe manager).
This is a 4-person ML Systems course project at NYU (ECE-GY 9183, Spring 2026).
The system runs on Chameleon Cloud with Kubernetes (K3s).

The data team owns the data pipeline: ingestion, quality checks, feedback capture,
batch compilation, and drift monitoring.

## Architecture

Single MinIO bucket: `data-proj01`
Three top-level prefixes: `data/`, `logs/`, `models/`

All scripts use the same S3 helper pattern:
```python
def get_s3():
    import boto3
    return boto3.client('s3',
        endpoint_url=os.getenv('OS_ENDPOINT'),
        aws_access_key_id=os.getenv('OS_ACCESS_KEY'),
        aws_secret_access_key=os.getenv('OS_SECRET_KEY'))

BUCKET = os.getenv('BUCKET', 'data-proj01')
```

## File ownership and purposes

| File | QC checkpoint | What it does |
|---|---|---|
| `ingest.py` | QC1 | Validates external Recipe1MSubs data, creates bucket, uploads |
| `batch_pipeline.py` | QC2 | Reads feedback, validates, compiles versioned training set, writes retrain trigger |
| `drift_monitor.py` | QC3 | Checks OOV rate, confidence drift, volume anomaly on live requests |
| `feedback_endpoint.py` | — | FastAPI service, captures user accept/reject to `logs/feedback/` |
| `online_features.py` | — | Pure function: Mealie recipe dict → serving input JSON |
| `data_generator.py` | — | Replays holdout records against serving, does NOT call feedback |

## Key S3 paths (never change these without coordinating with team)

```
data/raw/recipe1msubs/{train,val,test}.json  — ingest writes, training reads
data/processed/train_v<ts>.json              — batch writes, training reads
data/triggers/retrain_<ts>.json              — batch writes, watch_trigger.py reads+deletes
data/production_holdout/holdout.json         — ingest writes, data_generator reads
data/quality_reports/{ingest,batch,drift}_<ts>.json — all QC scripts write
logs/requests/request_<ts>_<id>.json         — serving writes, drift_monitor reads
logs/feedback/feedback_<ts>_<id>.json        — feedback_endpoint writes, batch reads
models/                                      — training writes, serving reads
```

## Cross-team contracts

### Serving input schema (online_features.py must produce this):
```json
{
  "recipe_id": "string",
  "recipe_title": "string",
  "ingredients": [{"raw": "string", "normalized": "string"}],
  "instructions": ["string"],
  "missing_ingredient": {"raw": "string", "normalized": "string"}
}
```

### Serving output schema (data_generator.py and drift_monitor.py parse this):
```json
{
  "request_id": "string",
  "recipe_id": "string",
  "missing_ingredient": "string",
  "substitutions": [{"ingredient": "string", "rank": 1, "embedding_score": 0.91}],
  "serving_version": "string"
}
```

### Feedback schema (feedback_endpoint.py accepts this):
```json
{
  "request_id": "string",
  "recipe_id": "string",
  "missing_ingredient": "string",
  "suggested_substitution": "string",
  "user_accepted": true,
  "model_version": "string (optional)"
}
```

### Trigger schema (batch_pipeline.py writes, watch_trigger.py reads):
```json
{
  "trigger_version": "v<timestamp>",
  "new_samples": 42,
  "total_samples": 5042,
  "dataset_path": "data-proj01/data/processed/train_v<ts>.json",
  "quality_report": "data/quality_reports/batch_<ts>.json",
  "created_at": "ISO8601"
}
```

## Training data format (train.py expects this):
Each record in train/val/test JSON arrays:
```json
{
  "recipe_id": "string",
  "original": "ingredient name",
  "replacement": "substitute ingredient name",
  "ingredients": ["context", "ingredient", "list"],
  "source": "recipe1msubs | user_feedback"
}
```

## Rules for modifications

1. Never change S3 key prefixes without updating ALL scripts that read/write them
2. Never change the feedback or trigger JSON schemas without coordinating with serving/training
3. `online_features.py` output must always match `serving/sample_data/input_sample.json`
4. `data_generator.py` must NEVER call the feedback endpoint (prevents synthetic contamination)
5. Quality reports always go to `data/quality_reports/` with pipeline name prefix
6. All Prometheus metrics use `subst_` prefix to avoid collisions with serving metrics
7. Bucket creation is idempotent — safe to call `ensure_bucket()` multiple times

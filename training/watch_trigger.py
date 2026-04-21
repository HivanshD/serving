"""
watch_trigger.py - watches retraining trigger objects in data-proj01 and
launches one training run per new trigger.

Run as K8S CronJob every 15 minutes:
  python watch_trigger.py
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TRAIN_SCRIPT = "/workspace/training/train.py"
CONFIG = "/workspace/training/config.yaml"
BUCKET = os.getenv("BUCKET", "data-proj01")
TRIGGER_PREFIX = os.getenv("TRIGGER_PREFIX", "data/triggers/")
PROCESSED_PREFIX = os.getenv("PROCESSED_TRIGGER_PREFIX", "data/triggers/processed/")
MODEL_PREFIX = os.getenv("MODEL_PREFIX", "models")
VAL_KEY = os.getenv("VAL_DATASET_KEY", "data/raw/recipe1msubs/val.json")
LOCAL_WORKDIR = Path(os.getenv("LOCAL_TRAINING_DIR", "/tmp/forkwise_training"))
MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "")
PYTHON_BIN = os.getenv("PYTHON_BIN", sys.executable)


def get_s3():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.getenv("OS_ENDPOINT"),
        aws_access_key_id=os.getenv("OS_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("OS_SECRET_KEY"),
    )


def parse_storage_path(path):
    if path.startswith("s3://"):
        bucket_and_key = path[5:]
        bucket, key = bucket_and_key.split("/", 1)
        return bucket, key
    if "/" not in path:
        raise ValueError(f"Unsupported dataset_path: {path}")
    bucket, key = path.split("/", 1)
    return bucket, key


def list_pending_triggers(s3):
    paginator = s3.get_paginator("list_objects_v2")
    pending = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=TRIGGER_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".keep") or "/processed/" in key:
                continue
            if not Path(key).name.startswith("retrain_"):
                continue
            marker_key = f"{PROCESSED_PREFIX}{Path(key).name}.done"
            try:
                s3.head_object(Bucket=BUCKET, Key=marker_key)
                continue
            except Exception:
                pending.append((key, obj["LastModified"]))
    pending.sort(key=lambda item: item[1])
    return [key for key, _ in pending]


def download_file(s3, bucket, key, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(Bucket=bucket, Key=key, Filename=str(destination))
    print(f"Downloaded {bucket}/{key} -> {destination}")


def load_trigger(s3, key):
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    return json.loads(obj["Body"].read())


def run_training(train_path, val_path):
    cmd = [
        PYTHON_BIN,
        TRAIN_SCRIPT,
        "--config",
        CONFIG,
        "--dataset",
        str(train_path),
        "--val_dataset",
        str(val_path),
        "--run_name",
        f"auto-retrain-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
        "--storage_bucket",
        BUCKET,
        "--model_prefix",
        MODEL_PREFIX,
    ]
    if MLFLOW_URI:
        cmd.extend(["--mlflow_tracking_uri", MLFLOW_URI])

    print(f"[{datetime.utcnow()}] Starting retraining: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    print(f"[{datetime.utcnow()}] Training finished with exit code {result.returncode}")
    return result.returncode


def mark_processed(s3, trigger_key, trigger_payload, status):
    marker_key = f"{PROCESSED_PREFIX}{Path(trigger_key).name}.done"
    marker = {
        "trigger_key": trigger_key,
        "status": status,
        "processed_at": datetime.utcnow().isoformat() + "Z",
        "dataset_path": trigger_payload.get("dataset_path", ""),
    }
    s3.put_object(Bucket=BUCKET, Key=marker_key, Body=json.dumps(marker))
    print(f"Wrote processed marker {marker_key}")


def main():
    print(f"[{datetime.utcnow()}] ForkWise watch_trigger started")
    s3 = get_s3()
    pending = list_pending_triggers(s3)
    if not pending:
        print(f"[{datetime.utcnow()}] No new retraining triggers. Skipping.")
        return

    trigger_key = pending[0]
    trigger = load_trigger(s3, trigger_key)
    dataset_bucket, dataset_key = parse_storage_path(trigger["dataset_path"])

    LOCAL_WORKDIR.mkdir(parents=True, exist_ok=True)
    train_path = LOCAL_WORKDIR / Path(dataset_key).name
    val_path = LOCAL_WORKDIR / "val.json"
    download_file(s3, dataset_bucket, dataset_key, train_path)
    download_file(s3, BUCKET, VAL_KEY, val_path)

    rc = run_training(train_path, val_path)
    if rc == 0:
        mark_processed(s3, trigger_key, trigger, "completed")
    else:
        print(f"[{datetime.utcnow()}] Retraining failed with exit code {rc}")
        raise SystemExit(rc)


if __name__ == "__main__":
    main()

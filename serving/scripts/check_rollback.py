"""
check_rollback.py

K8S CronJob for the canonical single-environment deployment.
Reads recent request logs from object storage and rolls back the serving model
artifact to the previous published version when production degrades.

Rollback triggers:
  1. p95 latency above threshold over recent successful requests
  2. error rate above threshold over recent requests
  3. serving /health reports model_loaded=false
"""

import json
import math
import os
from datetime import datetime, timedelta

import requests

MODEL_BUCKET = os.getenv("MODEL_BUCKET", "data-proj01")
REQUEST_LOG_BUCKET = os.getenv("REQUEST_LOG_BUCKET", MODEL_BUCKET)
REQUEST_LOG_PREFIX = os.getenv("REQUEST_LOG_PREFIX", "logs/requests/")
MODEL_PREFIX = os.getenv("MODEL_PREFIX", "models")
SERVING_HEALTH_URL = os.getenv(
    "SERVING_HEALTH_URL",
    "http://substitution-serving.forkwise-serving.svc.cluster.local:8000/health",
)
P95_LIMIT = float(os.getenv("LATENCY_P95_THRESHOLD_S", "0.5"))
ERR_LIMIT = float(os.getenv("ERROR_RATE_THRESHOLD", "0.05"))
LATENCY_WINDOW_MIN = int(os.getenv("LATENCY_WINDOW_MIN", "10"))
ERROR_WINDOW_MIN = int(os.getenv("ERROR_WINDOW_MIN", "5"))
MIN_REQUESTS = int(os.getenv("MIN_REQUESTS", "10"))


def get_s3():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.getenv("OS_ENDPOINT"),
        aws_access_key_id=os.getenv("OS_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("OS_SECRET_KEY"),
    )


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[index]


def load_recent_request_logs(s3, since_minutes):
    cutoff = datetime.utcnow() - timedelta(minutes=since_minutes)
    reqs = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=REQUEST_LOG_BUCKET, Prefix=REQUEST_LOG_PREFIX):
        for obj in page.get("Contents", []):
            if obj["LastModified"].replace(tzinfo=None) <= cutoff:
                continue
            try:
                body = s3.get_object(Bucket=REQUEST_LOG_BUCKET, Key=obj["Key"])
                reqs.append(json.loads(body["Body"].read()))
            except Exception:
                continue
    return reqs


def check_serving_health():
    try:
        r = requests.get(SERVING_HEALTH_URL, timeout=10)
        r.raise_for_status()
        payload = r.json()
        return bool(payload.get("model_loaded", False)), payload
    except Exception as e:
        print(f"[check_rollback] Health probe failed: {e}")
        return False, {"status": "error", "detail": str(e)}


def rollback_to_previous(s3, reason):
    pairs = [
        (
            f"{MODEL_PREFIX}/production/subst_model_previous.onnx",
            f"{MODEL_PREFIX}/production/subst_model_current.onnx",
        ),
        (
            f"{MODEL_PREFIX}/production/vocab_previous.json",
            f"{MODEL_PREFIX}/production/vocab.json",
        ),
        (
            f"{MODEL_PREFIX}/production/model_metadata_previous.json",
            f"{MODEL_PREFIX}/production/model_metadata.json",
        ),
        (
            f"{MODEL_PREFIX}/production/subst_model_previous.pth",
            f"{MODEL_PREFIX}/production/subst_model_current.pth",
        ),
    ]
    restored = []
    for source_key, dest_key in pairs:
        try:
            s3.head_object(Bucket=MODEL_BUCKET, Key=source_key)
            s3.copy_object(
                Bucket=MODEL_BUCKET,
                CopySource={"Bucket": MODEL_BUCKET, "Key": source_key},
                Key=dest_key,
            )
            restored.append(dest_key)
        except Exception:
            continue

    if not restored:
        raise RuntimeError("No previous model artifacts found to roll back to")

    event = {
        "rolled_back_at": datetime.utcnow().isoformat() + "Z",
        "reason": reason,
        "restored_keys": restored,
    }
    event_key = (
        f"{MODEL_PREFIX}/rollbacks/rollback_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    )
    s3.put_object(Bucket=MODEL_BUCKET, Key=event_key, Body=json.dumps(event, indent=2))
    print(f"[check_rollback] Rollback restored {restored}")
    print(f"[check_rollback] Rollback event logged to {event_key}")


def main():
    s3 = get_s3()

    success_logs = load_recent_request_logs(s3, LATENCY_WINDOW_MIN)
    error_logs = load_recent_request_logs(s3, ERROR_WINDOW_MIN)

    ok_latencies = [
        float(entry.get("latency_ms", 0)) / 1000.0
        for entry in success_logs
        if entry.get("status", "ok") == "ok" and entry.get("latency_ms") is not None
    ]
    p95 = percentile(ok_latencies, 95)

    total_recent = len(error_logs)
    error_count = sum(1 for entry in error_logs if entry.get("status") == "error")
    err = (error_count / total_recent) if total_recent else 0.0

    model_loaded, health_payload = check_serving_health()

    print(
        f"[check_rollback] p95={p95}s err={err:.1%} "
        f"requests={total_recent} model_loaded={model_loaded}"
    )

    breaches = []
    if p95 is not None and len(ok_latencies) >= MIN_REQUESTS and p95 > P95_LIMIT:
        breaches.append(f"p95={p95:.3f}s > {P95_LIMIT}s")
    if total_recent >= MIN_REQUESTS and err > ERR_LIMIT:
        breaches.append(f"err={err:.1%} > {ERR_LIMIT:.0%}")
    if not model_loaded:
        breaches.append(f"health={health_payload}")

    if not breaches:
        print("[check_rollback] Health check PASSED")
        return

    reason = " AND ".join(breaches)
    print(f"[check_rollback] ROLLBACK TRIGGERED: {reason}")
    rollback_to_previous(s3, reason)


if __name__ == "__main__":
    main()

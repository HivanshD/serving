"""
feedback_endpoint.py — Captures user accept/reject from Mealie frontend

Writes to data-proj01/logs/feedback/. No user identity stored (privacy).

Endpoints:
  POST /feedback   — store feedback
  GET  /health     — probe
  GET  /metrics    — Prometheus
"""

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import json, os, time

from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter

app = FastAPI(title='ForkWise Feedback')

FEEDBACK_COUNT = Counter('subst_feedback_total', 'Feedback entries', ['accepted'])
FEEDBACK_ERRORS = Counter('subst_feedback_errors_total', 'Failed saves')

BUCKET = os.getenv('BUCKET', 'data-proj01')
_s3 = None

def get_s3():
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client('s3',
            endpoint_url=os.getenv('OS_ENDPOINT'),
            aws_access_key_id=os.getenv('OS_ACCESS_KEY'),
            aws_secret_access_key=os.getenv('OS_SECRET_KEY'))
    return _s3


class FeedbackPayload(BaseModel):
    request_id: str
    recipe_id: str
    missing_ingredient: str
    suggested_substitution: str
    user_accepted: bool
    model_version: Optional[str] = None


@app.post('/feedback')
def receive_feedback(payload: FeedbackPayload):
    ts = time.strftime('%Y%m%d_%H%M%S')
    key = f'logs/feedback/feedback_{ts}_{payload.request_id}.json'
    entry = payload.model_dump()
    entry['server_timestamp'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    try:
        get_s3().put_object(Bucket=BUCKET, Key=key, Body=json.dumps(entry))
        FEEDBACK_COUNT.labels(accepted=str(payload.user_accepted).lower()).inc()
        return {'status': 'logged', 'key': key}
    except Exception as e:
        FEEDBACK_ERRORS.inc()
        return {'status': 'error', 'detail': str(e)}


@app.get('/health')
def health():
    return {'status': 'ok'}


Instrumentator().instrument(app).expose(app)

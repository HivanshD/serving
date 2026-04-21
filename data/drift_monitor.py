"""
drift_monitor.py — Quality Check Point 3: live inference data quality + drift

Runs as K8s CronJob (every 6h). Checks:
  1. OOV rate — fraction of missing ingredients not in training vocab
  2. Confidence drift — fraction of predictions with low top-1 score
  3. Volume anomaly — request count below expected minimum

Approach from Online Evaluation lab:
  - Lab uses MMDDrift for image embeddings (continuous features)
  - For categorical ingredients, OOV rate is the analogous signal

Prometheus metrics exposed for Grafana:
  subst_oov_rate, subst_low_confidence_rate, subst_drift_alert
"""

import json, os
from datetime import datetime, timedelta
from collections import Counter

from prometheus_client import (
    Gauge, Counter as PCounter, start_http_server
)

def get_s3():
    import boto3
    return boto3.client('s3',
        endpoint_url=os.getenv('OS_ENDPOINT'),
        aws_access_key_id=os.getenv('OS_ACCESS_KEY'),
        aws_secret_access_key=os.getenv('OS_SECRET_KEY'))

BUCKET = os.getenv('BUCKET', 'data-proj01')
OOV_THRESHOLD = float(os.getenv('OOV_THRESHOLD', '0.15'))
LOW_CONF_THRESHOLD = float(os.getenv('LOW_CONFIDENCE_THRESHOLD', '0.5'))
MIN_REQUESTS = int(os.getenv('MIN_REQUESTS_EXPECTED', '10'))

OOV_RATE_GAUGE = Gauge('subst_oov_rate', 'OOV rate (0-1)')
OOV_TOTAL = PCounter('subst_oov_ingredients_total', 'Cumulative OOV count')
LOW_CONF_GAUGE = Gauge('subst_low_confidence_rate', 'Low confidence fraction')
DRIFT_CHECK = PCounter('subst_drift_check_total', 'Drift check runs')
DRIFT_ALERT = Gauge('subst_drift_alert', '1=drift, 0=ok')


def load_training_vocab(s3):
    try:
        obj = s3.get_object(Bucket=BUCKET,
                            Key='models/production/vocab.json')
        vocab_json = json.loads(obj['Body'].read())
        vocab = {str(k).lower().strip() for k in vocab_json.keys()}
        vocab.discard('')
        print("[drift] Loaded production vocab from models/production/vocab.json")
        return vocab
    except Exception:
        pass

    obj = s3.get_object(Bucket=BUCKET,
                        Key='data/raw/recipe1msubs/train.json')
    data = json.loads(obj['Body'].read())
    vocab = set()
    for rec in data:
        for f in ('original', 'replacement'):
            v = rec.get(f, '')
            if isinstance(v, str): vocab.add(v.lower().strip())
        for ing in rec.get('ingredients', []):
            if isinstance(ing, str): vocab.add(ing.lower().strip())
    vocab.discard('')
    print("[drift] Loaded fallback vocab from data/raw/recipe1msubs/train.json")
    return vocab


def load_recent_requests(s3, since_hours=24):
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)
    reqs = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=BUCKET, Prefix='logs/requests/'):
        for obj in page.get('Contents', []):
            if obj['LastModified'].replace(tzinfo=None) > cutoff:
                try:
                    body = s3.get_object(Bucket=BUCKET, Key=obj['Key'])
                    reqs.append(json.loads(body['Body'].read()))
                except Exception:
                    continue
    return reqs


def check_oov(reqs, vocab):
    ings = [r.get('missing_ingredient', '').lower().strip()
            for r in reqs if r.get('missing_ingredient')]
    if not ings:
        return {'passed': True, 'detail': 'No ingredients'}

    oov = [i for i in ings if i not in vocab]
    rate = len(oov) / len(ings)
    OOV_RATE_GAUGE.set(rate)
    OOV_TOTAL.inc(len(oov))

    passed = rate <= OOV_THRESHOLD
    result = {'passed': passed, 'oov_rate': round(rate, 4),
              'threshold': OOV_THRESHOLD, 'oov_count': len(oov),
              'total': len(ings)}
    if not passed:
        top = Counter(oov).most_common(10)
        result['top_oov'] = [{'ing': i, 'n': c} for i, c in top]
        print(f"[drift] ALERT: OOV {rate:.1%} > {OOV_THRESHOLD:.0%}")
        for i, c in top: print(f"  '{i}': {c}")
    else:
        print(f"[drift] OOV PASSED ({rate:.1%})")
    return result


def check_confidence(reqs):
    scores = []
    for r in reqs:
        subs = r.get('top_substitutions', [])
        if subs and len(subs) > 0:
            s = subs[0].get('embedding_score')
            if s is not None: scores.append(float(s))
    if not scores:
        return {'passed': True, 'detail': 'No scores'}

    low = sum(1 for s in scores if s < LOW_CONF_THRESHOLD)
    low_rate = low / len(scores)
    avg = sum(scores) / len(scores)
    LOW_CONF_GAUGE.set(low_rate)

    threshold = 0.30
    passed = low_rate <= threshold
    result = {'passed': passed, 'low_rate': round(low_rate, 4),
              'threshold': threshold, 'avg_score': round(avg, 4),
              'total': len(scores)}
    if not passed:
        print(f"[drift] ALERT: {low_rate:.1%} low confidence")
    else:
        print(f"[drift] Confidence PASSED (avg={avg:.3f})")
    return result


def check_volume(reqs):
    n = len(reqs)
    passed = n >= MIN_REQUESTS
    result = {'passed': passed, 'count': n, 'min': MIN_REQUESTS}
    if not passed:
        print(f"[drift] ALERT: {n} requests (need >= {MIN_REQUESTS})")
    else:
        print(f"[drift] Volume PASSED ({n})")
    return result


def main():
    print(f"[drift] Running at {datetime.utcnow().isoformat()}")
    DRIFT_CHECK.inc()
    s3 = get_s3()

    vocab = load_training_vocab(s3)
    print(f"[drift] Vocab: {len(vocab)} ingredients")

    reqs = load_recent_requests(s3, since_hours=24)
    print(f"[drift] Requests (24h): {len(reqs)}")

    if not reqs:
        print("[drift] No requests."); DRIFT_ALERT.set(0); return

    oov = check_oov(reqs, vocab)
    conf = check_confidence(reqs)
    vol = check_volume(reqs)

    drifted = not (oov['passed'] and conf['passed'] and vol['passed'])
    DRIFT_ALERT.set(1 if drifted else 0)

    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    report = {
        'pipeline': 'drift_monitor', 'checkpoint': 'QC3',
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'drift_detected': drifted,
        'checks': {'oov': oov, 'confidence': conf, 'volume': vol},
    }
    s3.put_object(Bucket=BUCKET,
                  Key=f'data/quality_reports/drift_{ts}.json',
                  Body=json.dumps(report, indent=2))

    status = "DRIFT DETECTED" if drifted else "ALL CLEAR"
    print(f"\n[drift] {status}")


if __name__ == '__main__':
    port = int(os.getenv('METRICS_PORT', '8002'))
    start_http_server(port)
    print(f"[drift] Prometheus on :{port}")
    main()

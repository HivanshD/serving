"""
data_generator.py — Replays holdout records against serving endpoint

Uses records NEVER seen in training. Does NOT call feedback endpoint
(prevents synthetic contamination of the feedback loop).
"""

import json, os, time, random
import requests as http_requests

def get_s3():
    import boto3
    return boto3.client('s3',
        endpoint_url=os.getenv('OS_ENDPOINT'),
        aws_access_key_id=os.getenv('OS_ACCESS_KEY'),
        aws_secret_access_key=os.getenv('OS_SECRET_KEY'))

BUCKET = os.getenv('BUCKET', 'data-proj01')
SERVING_URL = os.getenv(
    'SERVING_URL',
    'http://subst-serving.production-proj01.svc.cluster.local:8000/predict',
)
CANARY_SERVING_URL = os.getenv(
    'CANARY_SERVING_URL',
    'http://subst-serving.canary-proj01.svc.cluster.local:8000/predict',
)
CANARY_TRAFFIC_PERCENT = float(os.getenv('CANARY_TRAFFIC_PERCENT', '0.1'))
RATE = float(os.getenv('REQUESTS_PER_SEC', '1'))


def choose_serving_url():
    if not CANARY_SERVING_URL or CANARY_TRAFFIC_PERCENT <= 0:
        return SERVING_URL, 'production'
    if random.random() < CANARY_TRAFFIC_PERCENT:
        return CANARY_SERVING_URL, 'canary'
    return SERVING_URL, 'production'


def load_holdout(s3):
    obj = s3.get_object(Bucket=BUCKET,
                        Key='data/production_holdout/holdout.json')
    return json.loads(obj['Body'].read())


def build_request(rec):
    ings = rec.get('ingredients', [])
    if isinstance(ings, list) and ings:
        if isinstance(ings[0], str):
            ing_list = [{'raw': i, 'normalized': i.lower().strip()} for i in ings]
        else:
            ing_list = ings
    else:
        ing_list = []

    return {
        'recipe_id': str(rec.get('recipe_id', '')),
        'recipe_title': rec.get('title', ''),
        'ingredients': ing_list,
        'instructions': rec.get('instructions', []),
        'missing_ingredient': {
            'raw': rec['original'],
            'normalized': rec['original'].lower().strip(),
        },
    }


def main():
    s3 = get_s3()
    records = load_holdout(s3)
    print(f"[datagen] {len(records)} holdout records")
    print(f"[datagen] Production target: {SERVING_URL}")
    print(f"[datagen] Canary target: {CANARY_SERVING_URL}")
    print(f"[datagen] Canary traffic: {CANARY_TRAFFIC_PERCENT:.0%}")
    print(f"[datagen] Rate: {RATE} req/s\n")

    sent = errors = 0
    start = time.time()

    while True:
        rec = random.choice(records)
        payload = build_request(rec)
        target_url, target_name = choose_serving_url()
        try:
            r = http_requests.post(target_url, json=payload, timeout=5)
            if r.status_code == 200:
                res = r.json()
                top = (res['substitutions'][0]['ingredient']
                       if res.get('substitutions') else 'none')
                sent += 1
                rps = sent / (time.time() - start) if time.time() > start else 0
                print(f"[{sent}] [{target_name}] {rec['original']} -> {top} | {rps:.1f} req/s")
            else:
                errors += 1
                print(f"[err] [{target_name}] {r.status_code}: {r.text[:80]}")
        except Exception as e:
            errors += 1
            print(f"[err] [{target_name}] {e}")
        time.sleep(1.0 / RATE)


if __name__ == '__main__':
    main()

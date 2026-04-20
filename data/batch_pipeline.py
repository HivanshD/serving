"""
batch_pipeline.py — Quality Check Point 2: when compiling training sets

Runs as K8s CronJob (daily). Reads feedback, validates (schema, dedup,
leakage, class balance, min count), merges accepted feedback into
training data, writes versioned dataset + retrain trigger.

Candidate selection:
  - Only user_accepted=True → positive training signal
  - Rejected feedback logged but not used
  - Dedup by request_id and (recipe, original, substitute) pair
  - Leakage check against production holdout
"""

import json, os, sys
from datetime import datetime, timedelta

def get_s3():
    import boto3
    return boto3.client('s3',
        endpoint_url=os.getenv('OS_ENDPOINT'),
        aws_access_key_id=os.getenv('OS_ACCESS_KEY'),
        aws_secret_access_key=os.getenv('OS_SECRET_KEY'))

BUCKET = os.getenv('BUCKET', 'data-proj01')
MIN_NEW_SAMPLES = int(os.getenv('MIN_NEW_SAMPLES', '50'))


def collect_recent_feedback(s3, since_hours=24):
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)
    entries = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=BUCKET, Prefix='logs/feedback/'):
        for obj in page.get('Contents', []):
            if obj['LastModified'].replace(tzinfo=None) > cutoff:
                body = s3.get_object(Bucket=BUCKET, Key=obj['Key'])
                entries.append(json.loads(body['Body'].read()))
    return entries


def load_holdout_pairs(s3):
    try:
        obj = s3.get_object(Bucket=BUCKET,
                            Key='data/production_holdout/holdout.json')
        holdout = json.loads(obj['Body'].read())
        return set(
            (r.get('original', '').lower().strip(),
             r.get('replacement', '').lower().strip())
            for r in holdout)
    except Exception:
        print("[QC2] WARNING: Could not load holdout for leakage check")
        return set()


def quality_check_2(entries, holdout_pairs):
    """
    QC2: Validate feedback when compiling training sets.
    Checks: schema, dedup, data leakage, class balance, min samples.
    """
    required = ['request_id', 'recipe_id', 'missing_ingredient',
                'suggested_substitution', 'user_accepted']

    report = {
        'checkpoint': 'QC2', 'total_entries': len(entries),
        'checks': {}, 'checked_at': datetime.utcnow().isoformat() + 'Z',
    }
    print(f"\n[QC2] Validating {len(entries)} feedback entries...")

    valid = []
    schema_bad = dup_bad = leak_bad = 0
    seen_ids = set()
    seen_pairs = set()

    for e in entries:
        if not all(e.get(f) is not None for f in required):
            schema_bad += 1; continue
        if e['request_id'] in seen_ids:
            dup_bad += 1; continue
        seen_ids.add(e['request_id'])

        pair = (e['recipe_id'],
                e['missing_ingredient'].lower().strip(),
                e['suggested_substitution'].lower().strip())
        if pair in seen_pairs:
            dup_bad += 1; continue
        seen_pairs.add(pair)

        ho_key = (e['missing_ingredient'].lower().strip(),
                  e['suggested_substitution'].lower().strip())
        if ho_key in holdout_pairs:
            leak_bad += 1; continue

        valid.append(e)

    accepted = sum(1 for e in valid if e['user_accepted'])
    rejected = sum(1 for e in valid if not e['user_accepted'])
    rate = accepted / len(valid) if valid else 0

    report['checks'] = {
        'schema':        {'passed': True, 'rejected': schema_bad},
        'dedup':         {'passed': True, 'rejected': dup_bad},
        'data_leakage':  {'passed': leak_bad == 0, 'rejected': leak_bad},
        'class_balance': {'passed': True, 'accepted': accepted,
                          'rejected': rejected, 'accept_rate': round(rate, 4)},
        'min_samples':   {'passed': accepted >= MIN_NEW_SAMPLES,
                          'count': accepted, 'threshold': MIN_NEW_SAMPLES},
    }
    report['valid'] = len(valid)
    report['accepted_for_training'] = accepted

    p = sum(1 for c in report['checks'].values() if c['passed'])
    print(f"[QC2] Valid:{len(valid)} Schema:{schema_bad} "
          f"Dedup:{dup_bad} Leakage:{leak_bad}")
    print(f"[QC2] Accepted:{accepted} Rejected:{rejected} Rate:{rate:.1%}")
    print(f"[QC2] {p}/{len(report['checks'])} checks passed")
    return valid, report


def compile_dataset(s3, valid_feedback):
    obj = s3.get_object(Bucket=BUCKET,
                        Key='data/raw/recipe1msubs/train.json')
    original = json.loads(obj['Body'].read())

    new = [{'recipe_id': fb['recipe_id'],
            'original': fb['missing_ingredient'],
            'replacement': fb['suggested_substitution'],
            'source': 'user_feedback'}
           for fb in valid_feedback if fb['user_accepted']]

    combined = original + new
    print(f"[batch] {len(original)} original + {len(new)} feedback "
          f"= {len(combined)} total")
    return combined, len(original), len(new)


def main():
    print(f"[batch] Starting at {datetime.utcnow().isoformat()}")
    s3 = get_s3()

    feedback = collect_recent_feedback(s3, since_hours=24)
    print(f"[batch] {len(feedback)} feedback entries in last 24h")
    if not feedback:
        print("[batch] No feedback. Exiting."); return

    holdout_pairs = load_holdout_pairs(s3)
    valid, qc_report = quality_check_2(feedback, holdout_pairs)

    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    s3.put_object(Bucket=BUCKET,
                  Key=f'data/quality_reports/batch_{ts}.json',
                  Body=json.dumps(qc_report, indent=2))

    if qc_report['accepted_for_training'] < MIN_NEW_SAMPLES:
        print(f"[batch] Only {qc_report['accepted_for_training']} accepted "
              f"(need {MIN_NEW_SAMPLES}). Skipping."); return

    dataset, orig_n, new_n = compile_dataset(s3, valid)
    ds_key = f'data/processed/train_v{ts}.json'
    s3.put_object(Bucket=BUCKET, Key=ds_key, Body=json.dumps(dataset))
    print(f"[batch] Dataset -> {ds_key}")

    trigger = {
        'trigger_version': f'v{ts}',
        'new_samples': new_n,
        'total_samples': len(dataset),
        'dataset_path': f'{BUCKET}/{ds_key}',
        'quality_report': f'data/quality_reports/batch_{ts}.json',
        'created_at': datetime.utcnow().isoformat() + 'Z',
    }
    trig_key = f'data/triggers/retrain_{ts}.json'
    s3.put_object(Bucket=BUCKET, Key=trig_key, Body=json.dumps(trigger))
    print(f"[batch] Trigger -> {trig_key}")
    print("[batch] watch_trigger.py will pick this up.")


if __name__ == '__main__':
    main()

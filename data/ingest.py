"""
ingest.py — Quality Check Point 1: at ingestion from external data sources

Validates Recipe1MSubs data (schema, nulls, counts, duplicates, vocab size),
creates the bucket + folder structure, uploads validated splits.

Also creates a production holdout set (never used for training).

Usage:
  python ingest.py --data-dir ./recipe1msubs
"""

import json, argparse, sys, hashlib, os
from pathlib import Path
from datetime import datetime

def get_s3():
    import boto3
    return boto3.client('s3',
        endpoint_url=os.getenv('OS_ENDPOINT'),
        aws_access_key_id=os.getenv('OS_ACCESS_KEY'),
        aws_secret_access_key=os.getenv('OS_SECRET_KEY'))

BUCKET = os.getenv('BUCKET', 'data-proj01')


def ensure_bucket(s3):
    """Create bucket and folder structure if they don't exist. Idempotent."""
    try:
        s3.head_bucket(Bucket=BUCKET)
    except Exception:
        s3.create_bucket(Bucket=BUCKET)
        print(f"[setup] Created bucket: {BUCKET}")

    folders = [
        'data/raw/recipe1msubs/',
        'data/raw/recipe1m/',
        'data/processed/',
        'data/triggers/',
        'data/production_holdout/',
        'data/quality_reports/',
        'logs/requests/',
        'logs/feedback/',
        'models/',
    ]
    for folder in folders:
        s3.put_object(Bucket=BUCKET, Key=folder + '.keep', Body=b'')
    print(f"[setup] Bucket {BUCKET} ready")


REQUIRED_FIELDS = ['recipe_id', 'original', 'replacement']

QUALITY_GATES = {
    'min_train_records': 1000,
    'min_val_records': 100,
    'min_test_records': 100,
    'max_null_rate': 0.0,
    'max_duplicate_rate': 0.05,
    'min_unique_ingredients': 50,
}


def quality_check_1(data, split_name):
    """
    QC1: Validate external data at ingestion.
    Checks: non-empty, schema, nulls, min records, duplicates, vocab size.
    """
    report = {
        'split': split_name,
        'total_records': len(data),
        'checks': {},
        'passed': True,
        'checked_at': datetime.utcnow().isoformat() + 'Z',
    }
    print(f"\n[QC1] Checking {split_name} ({len(data)} records)...")

    if len(data) == 0:
        report['checks']['non_empty'] = {'passed': False}
        report['passed'] = False
        return report
    report['checks']['non_empty'] = {'passed': True}

    # Schema
    missing = []
    for i, rec in enumerate(data):
        for f in REQUIRED_FIELDS:
            if f not in rec:
                missing.append((i, f))
    ok = len(missing) == 0
    report['checks']['schema'] = {'passed': ok, 'missing_count': len(missing)}
    if not ok:
        report['passed'] = False
        print(f"  FAIL: {len(missing)} records missing required fields")

    # Nulls
    nulls = []
    for i, rec in enumerate(data):
        for f in REQUIRED_FIELDS:
            val = rec.get(f)
            if val is None or (isinstance(val, str) and val.strip() == ''):
                nulls.append((i, f))
    null_rate = len(nulls) / len(data)
    ok = null_rate <= QUALITY_GATES['max_null_rate']
    report['checks']['null_values'] = {
        'passed': ok, 'null_count': len(nulls),
        'null_rate': round(null_rate, 4),
    }
    if not ok:
        report['passed'] = False
        print(f"  FAIL: {len(nulls)} null/empty values")

    # Min records
    min_key = f'min_{split_name}_records'
    min_req = QUALITY_GATES.get(min_key, 0)
    ok = len(data) >= min_req
    report['checks']['min_records'] = {
        'passed': ok, 'actual': len(data), 'required': min_req,
    }
    if not ok:
        report['passed'] = False
        print(f"  FAIL: {len(data)} records (need >= {min_req})")

    # Duplicates
    seen = set()
    dupes = 0
    for rec in data:
        k = (rec.get('recipe_id', ''), rec.get('original', ''),
             rec.get('replacement', ''))
        if k in seen: dupes += 1
        seen.add(k)
    dupe_rate = dupes / len(data)
    ok = dupe_rate <= QUALITY_GATES['max_duplicate_rate']
    report['checks']['duplicates'] = {
        'passed': ok, 'count': dupes, 'rate': round(dupe_rate, 4),
    }
    if not ok:
        report['passed'] = False
        print(f"  FAIL: {dupes} duplicates ({dupe_rate:.2%})")

    # Vocab size
    vocab = set()
    for rec in data:
        for f in ('original', 'replacement'):
            v = rec.get(f, '')
            if isinstance(v, str): vocab.add(v.lower().strip())
    vocab.discard('')
    ok = len(vocab) >= QUALITY_GATES['min_unique_ingredients']
    report['checks']['vocab_size'] = {
        'passed': ok, 'unique': len(vocab),
        'threshold': QUALITY_GATES['min_unique_ingredients'],
    }
    if not ok:
        report['passed'] = False
        print(f"  FAIL: only {len(vocab)} unique ingredients")

    passed = sum(1 for c in report['checks'].values() if c['passed'])
    total = len(report['checks'])
    status = "PASSED" if report['passed'] else "FAILED"
    print(f"[QC1] {split_name}: {status} ({passed}/{total} checks)")
    return report


def upload_split(s3, data, split_name):
    key = f'data/raw/recipe1msubs/{split_name}.json'
    body = json.dumps(data)
    md5 = hashlib.md5(body.encode()).hexdigest()[:8]
    s3.put_object(Bucket=BUCKET, Key=key, Body=body,
                  Metadata={'split': split_name,
                            'record_count': str(len(data)),
                            'checksum': md5,
                            'ingested_at': datetime.utcnow().isoformat()})
    print(f"[ingest] Uploaded {split_name}: {len(data)} records -> {key}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default='./recipe1msubs',
                        help='Dir with Recipe1MSubs train/val/test.json')
    parser.add_argument('--recipe1m-dir', default='./recipe1m',
                        help='Dir with Recipe1M layer1.json')
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    r1m_dir = Path(args.recipe1m_dir)

    if not data_dir.exists():
        print(f"ERROR: {data_dir} not found. Download Recipe1MSubs first.")
        sys.exit(1)

    s3 = get_s3()
    ensure_bucket(s3)

    # ── Upload Recipe1M context (layer1.json) ───────────────────
    layer1_path = r1m_dir / 'layer1.json'
    if layer1_path.exists():
        print(f"\n[ingest] Uploading Recipe1M layer1.json...")
        with open(layer1_path, 'rb') as f:
            s3.put_object(Bucket=BUCKET,
                          Key='data/raw/recipe1m/layer1.json',
                          Body=f.read())
        print(f"[ingest] Uploaded layer1.json -> data/raw/recipe1m/layer1.json")
    else:
        print(f"[ingest] WARNING: {layer1_path} not found. "
              f"Training will need Recipe1M context separately.")

    # ── QC1: Validate and upload Recipe1MSubs splits ────────────
    all_reports = []
    all_passed = True

    for split in ['train', 'val', 'test']:
        fp = data_dir / f'{split}.json'
        if not fp.exists():
            print(f"ERROR: {fp} not found")
            sys.exit(1)
        data = json.loads(fp.read_text())
        report = quality_check_1(data, split)
        all_reports.append(report)
        if report['passed']:
            upload_split(s3, data, split)
        else:
            all_passed = False

    # ── Production holdout (NEVER used for training) ────────────
    if all_passed:
        test_data = json.loads((data_dir / 'test.json').read_text())
        holdout = test_data[:len(test_data) // 2]
        s3.put_object(Bucket=BUCKET,
                      Key='data/production_holdout/holdout.json',
                      Body=json.dumps(holdout))
        print(f"\n[ingest] Holdout: {len(holdout)} records (never train on this)")

    # ── Save quality report ─────────────────────────────────────
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    report = {
        'pipeline': 'ingest', 'checkpoint': 'QC1',
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'all_passed': all_passed,
        'gates': QUALITY_GATES,
        'splits': all_reports,
    }
    s3.put_object(Bucket=BUCKET,
                  Key=f'data/quality_reports/ingest_{ts}.json',
                  Body=json.dumps(report, indent=2))

    if all_passed:
        print("\n[ingest] ALL PASSED. Training team can start.")
    else:
        print("\n[ingest] FAILED. Fix data before training.")
        sys.exit(1)


if __name__ == '__main__':
    main()

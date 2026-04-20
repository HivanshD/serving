"""
ingest.py — Quality Check Point 1: at ingestion from external data sources

Downloads Recipe1MSubs + Recipe1M from original sources (same URLs as
notebook 4), validates data quality, uploads to data-proj01 bucket.

Downloads directly — no need to manually download datasets first.
Caches downloads in /tmp/forkwise_ingest so re-runs are fast.

Run from anywhere with Python + internet:
  export OS_ACCESS_KEY=xxx OS_SECRET_KEY=xxx
  python ingest.py
"""

import os, json, csv, pickle, hashlib, tarfile, sys, time, types
from datetime import datetime
from io import BytesIO
import urllib.request

# ── S3 helper ───────────────────────────────────────────────────
def get_s3():
    import boto3
    return boto3.client('s3',
        endpoint_url=os.getenv('OS_ENDPOINT',
                               'https://chi.tacc.chameleoncloud.org:7480'),
        aws_access_key_id=os.getenv('OS_ACCESS_KEY'),
        aws_secret_access_key=os.getenv('OS_SECRET_KEY'))

BUCKET = os.getenv('BUCKET', 'data-proj01')
WORK_DIR = os.getenv('WORK_DIR', '/tmp/forkwise_ingest')


# ── Bucket setup (idempotent) ──────────────────────────────────
def ensure_bucket(s3):
    try:
        s3.head_bucket(Bucket=BUCKET)
    except Exception:
        s3.create_bucket(Bucket=BUCKET)
        print(f"[setup] Created bucket: {BUCKET}")

    for f in ['data/raw/recipe1msubs/', 'data/raw/recipe1m/',
              'data/raw/flavorgraph/', 'data/processed/',
              'data/triggers/', 'data/production_holdout/',
              'data/quality_reports/', 'logs/requests/',
              'logs/feedback/', 'models/']:
        s3.put_object(Bucket=BUCKET, Key=f + '.keep', Body=b'')
    print(f"[setup] Bucket {BUCKET} ready")


# ── Source URLs (same as notebook 4) ───────────────────────────
DOWNLOADS = {
    "recipe1M_layers.tar.gz": "http://wednesday.csail.mit.edu/temporal/release/recipe1M_layers.tar.gz",
    "det_ingrs.json": "http://wednesday.csail.mit.edu/temporal/release/det_ingrs.json",
    "train_comments_subs.pkl": "https://dl.fbaipublicfiles.com/gismo/train_comments_subs.pkl",
    "val_comments_subs.pkl": "https://dl.fbaipublicfiles.com/gismo/val_comments_subs.pkl",
    "test_comments_subs.pkl": "https://dl.fbaipublicfiles.com/gismo/test_comments_subs.pkl",
    "vocab_ingrs.pkl": "https://dl.fbaipublicfiles.com/gismo/vocab_ingrs.pkl",
    "merge_dict.pkl": "https://dl.fbaipublicfiles.com/gismo/merge_dict.pkl",
}

FLAVORGRAPH_URLS = {
    "nodes_191120.csv": "https://raw.githubusercontent.com/lamypark/FlavorGraph/master/input/nodes_191120.csv",
    "edges_191120.csv": "https://raw.githubusercontent.com/lamypark/FlavorGraph/master/input/edges_191120.csv",
}


# ── Vocab stub (same as notebook 4 — needed to unpickle) ──────
_inv = types.ModuleType("inv_cooking")
_inv.config = types.ModuleType("inv_cooking.config")
class _Vocab:
    def __init__(self):
        self.word2idx = {}
        self.idx2word = {}
    def __len__(self):
        return len(self.word2idx)
_inv.config.Vocabulary = _Vocab
sys.modules["inv_cooking"] = _inv
sys.modules["inv_cooking.config"] = _inv.config


# ── Step 1: Download ───────────────────────────────────────────
def step1_download():
    print("\n" + "=" * 60)
    print("STEP 1: Downloading data sources")
    print("=" * 60)
    os.makedirs(WORK_DIR, exist_ok=True)

    for fname, url in {**DOWNLOADS, **FLAVORGRAPH_URLS}.items():
        dest = os.path.join(WORK_DIR, fname)
        if os.path.exists(dest):
            print(f"  Cached: {fname}")
        else:
            print(f"  Downloading: {fname}...")
            urllib.request.urlretrieve(url, dest)
            print(f"  Saved ({os.path.getsize(dest)/1e6:.1f} MB)")

    # Extract layer1.json from tar
    layer1 = os.path.join(WORK_DIR, "layer1.json")
    if not os.path.exists(layer1):
        print("  Extracting layer1.json from tar...")
        with tarfile.open(os.path.join(WORK_DIR, "recipe1M_layers.tar.gz"), "r:gz") as tar:
            for m in tar.getmembers():
                if m.name.endswith("layer1.json"):
                    m.name = "layer1.json"
                    tar.extract(m, WORK_DIR)
                    break


# ── Step 2: Upload raw to bucket ───────────────────────────────
def step2_upload_raw(s3):
    print("\n" + "=" * 60)
    print("STEP 2: Uploading raw files to bucket")
    print("=" * 60)
    uploads = [
        ("data/raw/recipe1m/layer1.json", "layer1.json"),
        ("data/raw/recipe1m/det_ingrs.json", "det_ingrs.json"),
        ("data/raw/recipe1msubs/train_comments_subs.pkl", "train_comments_subs.pkl"),
        ("data/raw/recipe1msubs/val_comments_subs.pkl", "val_comments_subs.pkl"),
        ("data/raw/recipe1msubs/test_comments_subs.pkl", "test_comments_subs.pkl"),
        ("data/raw/recipe1msubs/vocab_ingrs.pkl", "vocab_ingrs.pkl"),
        ("data/raw/recipe1msubs/merge_dict.pkl", "merge_dict.pkl"),
        ("data/raw/flavorgraph/nodes_191120.csv", "nodes_191120.csv"),
        ("data/raw/flavorgraph/edges_191120.csv", "edges_191120.csv"),
    ]
    for s3_key, local in uploads:
        path = os.path.join(WORK_DIR, local)
        if os.path.exists(path):
            with open(path, 'rb') as f:
                s3.put_object(Bucket=BUCKET, Key=s3_key, Body=f.read())
            print(f"  {s3_key} ({os.path.getsize(path)/1e6:.1f} MB)")


# ── Step 3: QC1 — validate substitution data ──────────────────
QUALITY_GATES = {
    'min_train_records': 1000,
    'min_val_records': 100,
    'min_test_records': 100,
    'max_null_rate': 0.0,
    'max_duplicate_rate': 0.05,
    'min_unique_ingredients': 50,
}


def load_subs_pkl(split):
    """Load GISMo pkl, resolve vocab indices to ingredient names."""
    with open(os.path.join(WORK_DIR, f"{split}_comments_subs.pkl"), "rb") as f:
        raw = pickle.load(f, encoding='latin1')
    with open(os.path.join(WORK_DIR, "vocab_ingrs.pkl"), "rb") as f:
        vocab = pickle.load(f)

    records = []
    for entry in raw:
        rid = entry.get("id", "")
        subs = entry.get("subs", [])
        if len(subs) < 2:
            continue
        try:
            if hasattr(vocab, 'idx2word') and hasattr(vocab, 'word2idx'):
                orig = vocab.idx2word[vocab.word2idx[subs[0]]][0]
                repl = vocab.idx2word[vocab.word2idx[subs[1]]][0]
            else:
                orig, repl = str(subs[0]), str(subs[1])
        except (KeyError, IndexError):
            orig, repl = str(subs[0]), str(subs[1])
        records.append({'recipe_id': rid, 'original': orig, 'replacement': repl})
    return records


def quality_check_1(records, split):
    """QC1: schema, nulls, counts, duplicates, vocab size."""
    report = {'split': split, 'total': len(records), 'checks': {},
              'passed': True, 'checked_at': datetime.utcnow().isoformat() + 'Z'}
    print(f"\n[QC1] {split} ({len(records)} records)...")

    if not records:
        report['checks']['non_empty'] = {'passed': False}
        report['passed'] = False
        return report
    report['checks']['non_empty'] = {'passed': True}

    # Schema
    req = ['recipe_id', 'original', 'replacement']
    bad = sum(1 for r in records for f in req if f not in r)
    ok = bad == 0
    report['checks']['schema'] = {'passed': ok, 'missing': bad}
    if not ok: report['passed'] = False; print(f"  FAIL schema: {bad}")

    # Nulls
    nulls = sum(1 for r in records for f in req
                if not r.get(f) or (isinstance(r.get(f), str) and not r[f].strip()))
    rate = nulls / len(records)
    ok = rate <= QUALITY_GATES['max_null_rate']
    report['checks']['nulls'] = {'passed': ok, 'count': nulls, 'rate': round(rate, 4)}
    if not ok: report['passed'] = False; print(f"  FAIL nulls: {nulls}")

    # Min records
    min_r = QUALITY_GATES.get(f'min_{split}_records', 0)
    ok = len(records) >= min_r
    report['checks']['min_records'] = {'passed': ok, 'actual': len(records), 'required': min_r}
    if not ok: report['passed'] = False; print(f"  FAIL count: {len(records)} < {min_r}")

    # Duplicates
    seen = set()
    dupes = 0
    for r in records:
        k = (r.get('recipe_id'), r.get('original'), r.get('replacement'))
        if k in seen: dupes += 1
        seen.add(k)
    dr = dupes / len(records)
    ok = dr <= QUALITY_GATES['max_duplicate_rate']
    report['checks']['duplicates'] = {'passed': ok, 'count': dupes, 'rate': round(dr, 4)}
    if not ok: report['passed'] = False; print(f"  FAIL dupes: {dupes}")

    # Vocab
    v = set()
    for r in records:
        for f in ('original', 'replacement'):
            x = r.get(f, '')
            if isinstance(x, str): v.add(x.lower().strip())
    v.discard('')
    ok = len(v) >= QUALITY_GATES['min_unique_ingredients']
    report['checks']['vocab'] = {'passed': ok, 'unique': len(v)}
    if not ok: report['passed'] = False; print(f"  FAIL vocab: {len(v)}")

    p = sum(1 for c in report['checks'].values() if c['passed'])
    status = "PASSED" if report['passed'] else "FAILED"
    print(f"[QC1] {split}: {status} ({p}/{len(report['checks'])} checks)")
    return report


def step3_validate_upload(s3):
    print("\n" + "=" * 60)
    print("STEP 3: QC1 — Validate + upload as JSON")
    print("=" * 60)
    all_reports, all_records = [], {}
    all_passed = True

    for split in ['train', 'val', 'test']:
        recs = load_subs_pkl(split)
        all_records[split] = recs
        rpt = quality_check_1(recs, split)
        all_reports.append(rpt)
        if rpt['passed']:
            s3.put_object(Bucket=BUCKET,
                          Key=f'data/raw/recipe1msubs/{split}.json',
                          Body=json.dumps(recs))
            print(f"  Uploaded {split}.json ({len(recs)} records)")
        else:
            all_passed = False

    return all_records, all_reports, all_passed


# ── Step 4: Holdout ────────────────────────────────────────────
def step4_holdout(s3, all_records):
    print("\n" + "=" * 60)
    print("STEP 4: Production holdout")
    print("=" * 60)
    test = all_records.get('test', [])
    holdout = test[:len(test) // 2]
    s3.put_object(Bucket=BUCKET,
                  Key='data/production_holdout/holdout.json',
                  Body=json.dumps(holdout))
    print(f"  Holdout: {len(holdout)} records (NEVER train on this)")
    print(f"  Remaining test: {len(test) - len(holdout)} for offline eval")


# ── Step 5: Recipe context map ─────────────────────────────────
def step5_context_map(s3):
    print("\n" + "=" * 60)
    print("STEP 5: Recipe context from layer1.json")
    print("=" * 60)
    path = os.path.join(WORK_DIR, "layer1.json")
    if not os.path.exists(path):
        print("  WARNING: layer1.json missing, skipping"); return

    with open(path) as f:
        layer1 = json.load(f)
    ctx = {}
    for r in layer1:
        ctx[r['id']] = [i['text'].lower().strip() for i in r.get('ingredients', [])]
    s3.put_object(Bucket=BUCKET,
                  Key='data/raw/recipe1m/context_map.json',
                  Body=json.dumps(ctx))
    print(f"  Context map: {len(ctx):,} recipes")


# ── Step 6: Save report ───────────────────────────────────────
def step6_report(s3, all_reports, all_passed):
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    report = {
        'pipeline': 'ingest', 'checkpoint': 'QC1',
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'all_passed': all_passed,
        'gates': QUALITY_GATES, 'splits': all_reports,
    }
    s3.put_object(Bucket=BUCKET,
                  Key=f'data/quality_reports/ingest_{ts}.json',
                  Body=json.dumps(report, indent=2))
    print(f"\n  Report -> data/quality_reports/ingest_{ts}.json")


# ── Main ───────────────────────────────────────────────────────
def main():
    start = time.time()
    s3 = get_s3()
    ensure_bucket(s3)

    step1_download()
    step2_upload_raw(s3)
    recs, rpts, ok = step3_validate_upload(s3)

    if ok:
        step4_holdout(s3, recs)
        step5_context_map(s3)

    step6_report(s3, rpts, ok)

    elapsed = time.time() - start
    if ok:
        print(f"\n[ingest] ALL PASSED ({elapsed/60:.1f} min). Training can start.")
    else:
        print(f"\n[ingest] FAILED ({elapsed/60:.1f} min). Fix data first.")
        sys.exit(1)


if __name__ == '__main__':
    main()

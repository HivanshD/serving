"""
reload_model.py — Downloads model artifacts at startup.
Exits 0 on failure so server starts with stub.
"""

import json
import os
import sys


def download(s3, bucket, key, local_path, description):
    try:
        s3.download_file(Bucket=bucket, Key=key, Filename=local_path)
        size = os.path.getsize(local_path)
        print(f"[reload_model] Downloaded {description}: "
              f"{bucket}/{key} -> {local_path} ({size} bytes)")
        return True
    except Exception as e:
        print(f"[reload_model] Could not download {description} "
              f"({bucket}/{key}): {e}")
        return False


def write_metadata(path, payload):
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(payload, f)
    except Exception as e:
        print(f'[reload_model] Could not write metadata file {path}: {e}')


def load_manifest(s3, bucket, key):
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        manifest = json.loads(obj['Body'].read())
        print(f'[reload_model] Loaded manifest: {bucket}/{key}')
        return manifest
    except Exception as e:
        print(f'[reload_model] Could not load manifest ({bucket}/{key}): {e}')
        return None


def download_from_manifest(s3, bucket, backend, manifest, metadata_path, manifest_key):
    artifacts = manifest.get('artifacts', {})
    metadata = {
        'model_version': manifest.get('model_version', 'unknown'),
        'run_name': manifest.get('run_name', ''),
        'run_id': manifest.get('run_id', ''),
        'manifest_key': manifest_key,
        'bucket': bucket,
    }

    if backend == 'pytorch':
        key = artifacts.get('pytorch_key')
        if not key:
            print('[reload_model] Manifest missing artifacts.pytorch_key')
            return False
        ok = download(
            s3, bucket, key,
            os.getenv('MODEL_PATH', '/app/model.pth'),
            'PyTorch checkpoint')
        if ok:
            write_metadata(metadata_path, metadata)
        return ok

    if backend == 'onnx':
        model_key = artifacts.get('onnx_key')
        vocab_key = artifacts.get('vocab_key')
        if not model_key or not vocab_key:
            print('[reload_model] Manifest missing artifacts.onnx_key or artifacts.vocab_key')
            return False
        ok_model = download(
            s3, bucket, model_key,
            os.getenv('ONNX_MODEL_PATH', '/app/model.onnx'),
            'ONNX model')
        ok_vocab = download(
            s3, bucket, vocab_key,
            os.getenv('VOCAB_PATH', '/app/vocab.json'),
            'Vocabulary')
        if ok_model and ok_vocab:
            write_metadata(metadata_path, metadata)
            return True
        return False

    print(f'[reload_model] Unsupported backend: {backend}')
    return False


def download_direct(s3, bucket, backend, metadata_path):
    metadata = {
        'model_version': os.getenv('MODEL_VERSION', 'unknown'),
        'manifest_key': '',
        'bucket': bucket,
    }
    if backend == 'pytorch':
        ok = download(
            s3, bucket,
            os.getenv('MODEL_KEY', 'models/production/subst_model_current.pth'),
            os.getenv('MODEL_PATH', '/app/model.pth'),
            'PyTorch checkpoint')
        if ok:
            write_metadata(metadata_path, metadata)
        return ok

    if backend == 'onnx':
        ok_model = download(
            s3, bucket,
            os.getenv('ONNX_MODEL_KEY', 'models/production/subst_model_current.onnx'),
            os.getenv('ONNX_MODEL_PATH', '/app/model.onnx'),
            'ONNX model')
        ok_vocab = download(
            s3, bucket,
            os.getenv('VOCAB_KEY', 'models/production/vocab.json'),
            os.getenv('VOCAB_PATH', '/app/vocab.json'),
            'Vocabulary')
        download(
            s3, bucket,
            os.getenv('MODEL_METADATA_KEY', 'models/production/model_metadata.json'),
            os.getenv('MODEL_METADATA_PATH', '/app/model_metadata.json'),
            'Model metadata')
        if ok_model and ok_vocab:
            write_metadata(metadata_path, metadata)
            return True
        return False

    print(f'[reload_model] Unsupported backend: {backend}')
    return False


def main():
    bucket = os.getenv('MODEL_BUCKET', 'models-proj01')
    backend = os.getenv('BACKEND', 'pytorch').lower()
    manifest_key = os.getenv('MODEL_MANIFEST_KEY', '')
    metadata_path = os.getenv('MODEL_METADATA_PATH', '/app/model_metadata.json')
    allow_direct_fallback = os.getenv('ALLOW_DIRECT_FALLBACK', 'false').lower() == 'true'
    endpoint = os.getenv('OS_ENDPOINT')
    if not endpoint:
        print('[reload_model] OS_ENDPOINT not set. Server will use stub.')
        sys.exit(0)
    try:
        import boto3
        s3 = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=os.getenv('OS_ACCESS_KEY'),
            aws_secret_access_key=os.getenv('OS_SECRET_KEY'))
    except Exception as e:
        print(f'[reload_model] S3 client init failed: {e}')
        sys.exit(0)

    if manifest_key:
        manifest = load_manifest(s3, bucket, manifest_key)
        if manifest is not None and download_from_manifest(
                s3, bucket, backend, manifest, metadata_path, manifest_key):
            sys.exit(0)
        if not allow_direct_fallback:
            print('[reload_model] Manifest load failed and direct fallback is disabled. Server will use stub.')
            sys.exit(0)
        print('[reload_model] Falling back to direct model keys.')

    download_direct(s3, bucket, backend, metadata_path)
    sys.exit(0)


if __name__ == '__main__':
    main()

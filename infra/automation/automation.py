import json
import os
import subprocess
from typing import Any

from fastapi import FastAPI, HTTPException


app = FastAPI(title='ForkWise Automation', version='0.2.0')
KUBECTL = os.getenv('KUBECTL_BIN', 'kubectl')
ROLLOUT_TIMEOUT = os.getenv('ROLLOUT_TIMEOUT', '180s')
MODEL_BUCKET = os.getenv('MODEL_BUCKET', 'models-proj01')
STAGING_MANIFEST_KEY = os.getenv('STAGING_MANIFEST_KEY', 'manifests/staging.json')
CANARY_MANIFEST_KEY = os.getenv('CANARY_MANIFEST_KEY', 'manifests/canary.json')
PRODUCTION_MANIFEST_KEY = os.getenv('PRODUCTION_MANIFEST_KEY', 'manifests/production.json')
PRODUCTION_PREVIOUS_MANIFEST_KEY = os.getenv(
    'PRODUCTION_PREVIOUS_MANIFEST_KEY', 'manifests/production_previous.json')

TARGETS = {
    'staging': ('staging-proj01', STAGING_MANIFEST_KEY),
    'canary': ('canary-proj01', CANARY_MANIFEST_KEY),
    'production': ('production-proj01', PRODUCTION_MANIFEST_KEY),
}

LEGACY_PRODUCTION_ARTIFACTS = {
    'pytorch_key': 'production/subst_model_current.pth',
    'onnx_key': 'production/subst_model_current.onnx',
    'vocab_key': 'production/vocab.json',
}


def get_s3_client():
    try:
        import boto3
        return boto3.client(
            's3',
            endpoint_url=os.getenv('OS_ENDPOINT'),
            aws_access_key_id=os.getenv('OS_ACCESS_KEY'),
            aws_secret_access_key=os.getenv('OS_SECRET_KEY'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'could not initialize s3 client: {e}')


def run_kubectl(*args: str, timeout: int = 30) -> str:
    cmd = [KUBECTL, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or 'kubectl command failed'
        raise HTTPException(status_code=500, detail=detail)
    return proc.stdout.strip()



def rollout_restart(namespace: str, deployment: str) -> str:
    restart_output = run_kubectl(
        'rollout', 'restart', f'deployment/{deployment}', '-n', namespace)
    status_output = run_kubectl(
        'rollout', 'status', f'deployment/{deployment}', '-n', namespace,
        f'--timeout={ROLLOUT_TIMEOUT}', timeout=240)
    return '\n'.join(part for part in [restart_output, status_output] if part)



def read_manifest(s3, key: str) -> dict[str, Any]:
    try:
        obj = s3.get_object(Bucket=MODEL_BUCKET, Key=key)
        return json.loads(obj['Body'].read())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'could not read manifest {key}: {e}')



def write_manifest(s3, key: str, manifest: dict[str, Any]) -> None:
    try:
        s3.put_object(Bucket=MODEL_BUCKET, Key=key, Body=json.dumps(manifest, indent=2))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'could not write manifest {key}: {e}')


def object_exists(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket=MODEL_BUCKET, Key=key)
        return True
    except Exception:
        return False


def build_legacy_production_manifest() -> dict[str, Any]:
    return {
        'model_version': 'legacy-production',
        'run_name': 'legacy-production',
        'run_id': '',
        'created_at': '',
        'metrics': {},
        'artifacts': dict(LEGACY_PRODUCTION_ARTIFACTS),
    }


def resolve_bootstrap_candidate(s3) -> dict[str, Any] | None:
    if object_exists(s3, 'candidates/latest.json'):
        return read_manifest(s3, 'candidates/latest.json')
    return None


def ensure_manifest(s3, key: str, manifest: dict[str, Any], overwrite: bool = False) -> None:
    if overwrite or not object_exists(s3, key):
        write_manifest(s3, key, manifest)



def deploy_manifest_to_target(s3, manifest: dict[str, Any], target: str, deployment: str) -> dict[str, str]:
    if target not in TARGETS:
        raise HTTPException(status_code=400, detail=f'unknown target: {target}')
    namespace, manifest_key = TARGETS[target]
    write_manifest(s3, manifest_key, manifest)
    rollout_output = rollout_restart(namespace, deployment)
    return {
        'target': target,
        'namespace': namespace,
        'manifest_key': manifest_key,
        'model_version': str(manifest.get('model_version', 'unknown')),
        'rollout': rollout_output,
    }


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/deploy-candidate')
def deploy_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    manifest_key = payload.get('manifest_key')
    deployment = payload.get('deployment', 'subst-serving')
    targets = payload.get('targets') or ['staging', 'canary']
    if not manifest_key:
        raise HTTPException(status_code=400, detail='manifest_key is required')

    s3 = get_s3_client()
    manifest = read_manifest(s3, str(manifest_key))
    results = [deploy_manifest_to_target(s3, manifest, str(target), deployment) for target in targets]
    return {
        'status': 'candidate_deployed',
        'source_manifest_key': str(manifest_key),
        'model_version': str(manifest.get('model_version', 'unknown')),
        'results': results,
    }


@app.post('/bootstrap-rollout')
def bootstrap_rollout(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    overwrite = bool(payload.get('overwrite', False))
    deployment = str(payload.get('deployment', 'subst-serving'))
    restart_targets = payload.get('restart_targets') or []

    s3 = get_s3_client()

    if object_exists(s3, PRODUCTION_MANIFEST_KEY):
        production_manifest = read_manifest(s3, PRODUCTION_MANIFEST_KEY)
    elif all(object_exists(s3, key) for key in LEGACY_PRODUCTION_ARTIFACTS.values()):
        production_manifest = build_legacy_production_manifest()
        write_manifest(s3, PRODUCTION_MANIFEST_KEY, production_manifest)
    else:
        candidate_manifest = resolve_bootstrap_candidate(s3)
        if candidate_manifest is None:
            raise HTTPException(
                status_code=500,
                detail='no production manifest, no legacy production artifacts, and no candidates/latest.json found')
        production_manifest = candidate_manifest
        write_manifest(s3, PRODUCTION_MANIFEST_KEY, production_manifest)

    ensure_manifest(s3, PRODUCTION_PREVIOUS_MANIFEST_KEY, production_manifest, overwrite=overwrite)

    candidate_manifest = resolve_bootstrap_candidate(s3) or production_manifest
    ensure_manifest(s3, STAGING_MANIFEST_KEY, candidate_manifest, overwrite=overwrite)
    ensure_manifest(s3, CANARY_MANIFEST_KEY, candidate_manifest, overwrite=overwrite)

    restarted = []
    for target in restart_targets:
        if target not in TARGETS:
            continue
        namespace, manifest_key = TARGETS[target]
        rollout_output = rollout_restart(namespace, deployment)
        restarted.append({
            'target': target,
            'namespace': namespace,
            'manifest_key': manifest_key,
            'rollout': rollout_output,
        })

    return {
        'status': 'bootstrapped',
        'production_manifest_key': PRODUCTION_MANIFEST_KEY,
        'production_previous_manifest_key': PRODUCTION_PREVIOUS_MANIFEST_KEY,
        'staging_manifest_key': STAGING_MANIFEST_KEY,
        'canary_manifest_key': CANARY_MANIFEST_KEY,
        'production_model_version': str(production_manifest.get('model_version', 'unknown')),
        'candidate_model_version': str(candidate_manifest.get('model_version', 'unknown')),
        'restarted': restarted,
    }


@app.post('/rollback')
def rollback(payload: dict[str, Any]) -> dict[str, str]:
    deployment = payload.get('deployment', 'subst-serving')
    namespace = payload.get('namespace', 'production-proj01')
    s3 = get_s3_client()
    previous_manifest = read_manifest(s3, PRODUCTION_PREVIOUS_MANIFEST_KEY)
    write_manifest(s3, PRODUCTION_MANIFEST_KEY, previous_manifest)
    rollout_output = rollout_restart(namespace, deployment)
    return {
        'status': 'rolled_back',
        'namespace': namespace,
        'deployment': deployment,
        'manifest_key': PRODUCTION_MANIFEST_KEY,
        'model_version': str(previous_manifest.get('model_version', 'unknown')),
        'rollout_status': rollout_output,
        'reason': str(payload.get('reason', '')),
    }


@app.post('/promote')
def promote(payload: dict[str, Any]) -> dict[str, str]:
    deployment = payload.get('deployment', 'subst-serving')
    namespace = payload.get('to', 'production-proj01')
    s3 = get_s3_client()
    canary_manifest = read_manifest(s3, CANARY_MANIFEST_KEY)
    try:
        current_manifest = read_manifest(s3, PRODUCTION_MANIFEST_KEY)
        write_manifest(s3, PRODUCTION_PREVIOUS_MANIFEST_KEY, current_manifest)
    except HTTPException:
        pass
    write_manifest(s3, PRODUCTION_MANIFEST_KEY, canary_manifest)
    rollout_output = rollout_restart(namespace, deployment)
    return {
        'status': 'promoted',
        'from_manifest_key': CANARY_MANIFEST_KEY,
        'to_manifest_key': PRODUCTION_MANIFEST_KEY,
        'namespace': namespace,
        'deployment': deployment,
        'model_version': str(canary_manifest.get('model_version', 'unknown')),
        'rollout_status': rollout_output,
    }

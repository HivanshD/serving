# ForkWise Data Kubernetes App

This directory is the canonical home for the data-side workloads that support
the integrated ForkWise deployment.

## What Is Here

This app deploys the remote GHCR images for:

1. `subst-feedback` - FastAPI feedback capture service
2. `data-generator` - synthetic traffic generator for the serving endpoint
3. `batch-pipeline` - daily QC2 dataset build CronJob
4. `drift-monitor` - QC3 drift-monitoring CronJob
5. `training-trigger` - retraining watcher CronJob for new trigger objects
6. `forkwise-ingest` - one-time bootstrap Job used to seed `data-proj01`

## Canonical Images

These manifests point at the GHCR images under `ghcr.io/itsnotaka/` and use the
shared `demo` tag for the data-side services. The training watcher is different:
its manifest defaults to `forkwise-train:local`, and you should patch it to the
training image you build from this repo before running the live loop.

1. `ghcr.io/itsnotaka/forkwise-ingest:demo`
2. `ghcr.io/itsnotaka/forkwise-feedback:demo`
3. `ghcr.io/itsnotaka/forkwise-batch:demo`
4. `ghcr.io/itsnotaka/forkwise-generator:demo`
5. `forkwise-train:local` default placeholder for `training-trigger`

## Safe Defaults

The always-on feedback service is enabled immediately.
The retraining watcher is also enabled immediately because it stays idle until
`batch_pipeline.py` writes a trigger under `data/triggers/`.

The noisier workloads are intentionally gated so a fresh cluster does not fail
before object storage is seeded:

1. `data-generator` starts at `replicas: 0`
2. `batch-pipeline` starts with `suspend: true`
3. `drift-monitor` starts with `suspend: true`

## Required Secret

Create `s3-credentials` in the `forkwise-data` namespace before applying the
manifests:

```bash
kubectl create secret generic s3-credentials \
  -n forkwise-data \
  --from-literal=access-key=<YOUR_OS_ACCESS_KEY> \
  --from-literal=secret-key=<YOUR_OS_SECRET_KEY>
```

If your GHCR packages are private, also create an image pull secret in this
namespace before deployment.

## Apply Order

```bash
# Create namespace + config + safe workloads
kubectl apply -k infra/k8s/apps/forkwise-data

# Point the training-trigger CronJob at the training image built from this repo.
kubectl set image cronjob/training-trigger \
  training=ghcr.io/<your-org-or-user>/forkwise-train:<tag> \
  -n forkwise-data

# Run the one-time bootstrap job
kubectl apply -f infra/k8s/apps/forkwise-data/job-ingest.yaml
kubectl logs -n forkwise-data job/forkwise-ingest -f

# Turn on the remaining workloads once ingest succeeds
kubectl scale deployment/data-generator -n forkwise-data --replicas=1
kubectl patch cronjob batch-pipeline -n forkwise-data -p '{"spec":{"suspend":false}}'
kubectl patch cronjob drift-monitor -n forkwise-data -p '{"spec":{"suspend":false}}'
kubectl get cronjob training-trigger -n forkwise-data
```

## In-Cluster Contracts

1. Feedback endpoint: `http://subst-feedback.forkwise-data.svc.cluster.local:8001/feedback`
2. Production serving endpoint consumed by generator: `http://subst-serving.production-proj01.svc.cluster.local:8000/predict`
3. Canary serving endpoint for rollout validation: `http://subst-serving.canary-proj01.svc.cluster.local:8000/predict`
4. Single-namespace fallback (bootstrap path): `http://substitution-serving.forkwise-serving.svc.cluster.local:8000/predict`
5. Retraining trigger prefix watched by training: `data/triggers/`

# ForkWise Cloud Setup

This is the canonical step-by-step runbook for bringing up the current
ForkWise stack on a cloud Kubernetes environment, with the data-plane images
pulled from GHCR instead of built ad hoc on the cluster.

## What this deploys

1. Chameleon VMs via Terraform
2. k3s via Ansible
3. `Mealie` in `forkwise-app`
4. `substitution-serving` in `forkwise-serving`
5. `forkwise-data` workloads in `forkwise-data`
   - `subst-feedback`
   - `data-generator`
   - `batch-pipeline`
   - `drift-monitor`
   - `training-trigger`
   - one-time `forkwise-ingest` bootstrap job

## Canonical GHCR images

```text
ghcr.io/itsnotaka/forkwise-ingest:demo
ghcr.io/itsnotaka/forkwise-feedback:demo
ghcr.io/itsnotaka/forkwise-batch:demo
ghcr.io/itsnotaka/forkwise-generator:demo
```

The Kubernetes manifests under `infra/k8s/apps/forkwise-data/` already point to
those images.

## Before you start

You need:

1. Chameleon credentials outside Git
2. `terraform`, `ansible`, `kubectl`, and `docker` installed locally
3. OpenStack object-store credentials for `data-proj01`
4. A registry image for `substitution-serving`, or a local build/push plan for it
5. A registry image for `forkwise-train`, or a local build/push plan for it

If the GHCR packages are private, log in before you do anything else:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-username> --password-stdin
```

`GHCR_TOKEN` needs `read:packages` to pull and `write:packages` to push.

## 1. Provision the cloud VMs

```bash
cd infra/tf/kvm
cp terraform.tfvars.example terraform.tfvars

# Fill in terraform.tfvars and keep it out of Git.
terraform init
terraform validate
terraform plan
terraform apply -auto-approve
```

Record the floating IP for `node1`.

## 2. Bootstrap k3s

```bash
cd ../../ansible
cp ansible.cfg.example ansible.cfg
```

Edit `ansible.cfg` so the SSH host points at the `node1` floating IP, then run:

```bash
ansible-playbook -i inventory.yml general/hello_host.yml
ansible-playbook -i inventory.yml pre_k8s/pre_k8s_configure.yml
ansible-playbook -i inventory.yml k8s/install_k3s.yml
ansible-playbook -i inventory.yml post_k8s/post_k8s_configure.yml
```

## 3. Build and deploy substitution-serving and training

Build and push the serving and training images from this repo, then deploy the base apps:

```bash
cd ../serving
make build-onnx REGISTRY=ghcr.io/<your-org-or-user>
make push-onnx REGISTRY=ghcr.io/<your-org-or-user>

cd ../training
docker build -t ghcr.io/<your-org-or-user>/forkwise-train:$(git -C .. rev-parse --short HEAD) \
  -f docker_nvidia/Dockerfile ..
docker push ghcr.io/<your-org-or-user>/forkwise-train:$(git -C .. rev-parse --short HEAD)

cd ../infra/ansible
ansible-playbook -i inventory.yml deploy/deploy_apps.yml \
  -e serving_image=ghcr.io/<your-org-or-user>/subst-serving-onnx:$(git -C ../.. rev-parse --short HEAD)
```

Verify the base services:

```bash
kubectl get pods -n forkwise-app
kubectl get pods -n forkwise-serving
kubectl get svc -n forkwise-app
kubectl get svc -n forkwise-serving
kubectl get cronjob -n forkwise-serving
```

You should see:

1. `mealie` running in `forkwise-app`
2. `substitution-serving` running in `forkwise-serving`
3. `check-rollback` present in `forkwise-serving`
4. NodePort `30090` for Mealie and `30080` for serving

## 4. Create the object-store secrets

The data workloads expect the `s3-credentials` secret in `forkwise-data`.
The serving deployment also expects the same secret in `forkwise-serving`
because it loads model artifacts and writes request logs directly to object
storage.

```bash
kubectl create namespace forkwise-data --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace forkwise-serving --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic s3-credentials \
  -n forkwise-data \
  --from-literal=access-key=<YOUR_OS_ACCESS_KEY> \
  --from-literal=secret-key=<YOUR_OS_SECRET_KEY>

kubectl create secret generic s3-credentials \
  -n forkwise-serving \
  --from-literal=access-key=<YOUR_OS_ACCESS_KEY> \
  --from-literal=secret-key=<YOUR_OS_SECRET_KEY>
```

If the GHCR packages are private, also create an image pull secret in
`forkwise-data`:

```bash
kubectl create secret docker-registry ghcr-pull \
  -n forkwise-data \
  --docker-server=ghcr.io \
  --docker-username=<github-username> \
  --docker-password=<GHCR_TOKEN>
```

Then patch the default service account or add `imagePullSecrets` to the
manifests before applying them.

## 5. Apply the canonical ForkWise data manifests

```bash
kubectl apply -k infra/k8s/apps/forkwise-data

kubectl set image cronjob/training-trigger \
  training=ghcr.io/<your-org-or-user>/forkwise-train:$(git rev-parse --short HEAD) \
  -n forkwise-data
```

This creates:

1. the `forkwise-data` namespace
2. the shared config map
3. the `subst-feedback` deployment and service
4. the `data-generator` deployment at `replicas=0`
5. the `batch-pipeline` and `drift-monitor` cronjobs in a suspended state
6. the `training-trigger` cronjob in an active state

Verify:

```bash
kubectl get all -n forkwise-data
kubectl get cronjobs -n forkwise-data
kubectl get cronjob training-trigger -n forkwise-data
```

## 6. Seed object storage once with the ingest job

The data stack is not ready until `data-proj01` has the validated raw splits and
holdout set. Run the one-time ingest job:

```bash
kubectl apply -f infra/k8s/apps/forkwise-data/job-ingest.yaml
kubectl logs -n forkwise-data job/forkwise-ingest -f
kubectl wait --for=condition=complete job/forkwise-ingest -n forkwise-data --timeout=30m
```

Success means:

1. bucket prefixes were created
2. `data/raw/recipe1msubs/{train,val,test}.json` were uploaded
3. `data/production_holdout/holdout.json` was written
4. a QC1 report was written under `data/quality_reports/`

## 7. Turn on the live workloads

Once ingest is complete and `substitution-serving` is healthy, enable the rest:

```bash
kubectl scale deployment/data-generator -n forkwise-data --replicas=1
kubectl patch cronjob batch-pipeline -n forkwise-data -p '{"spec":{"suspend":false}}'
kubectl patch cronjob drift-monitor -n forkwise-data -p '{"spec":{"suspend":false}}'
```

Verify:

```bash
kubectl rollout status deployment/subst-feedback -n forkwise-data
kubectl rollout status deployment/data-generator -n forkwise-data
kubectl logs deployment/data-generator -n forkwise-data --tail=20
kubectl get cronjobs -n forkwise-data
kubectl get cronjob training-trigger -n forkwise-data
```

## 8. Smoke-test the stack

Tunnel to the NodePorts from your laptop:

```bash
ssh -N -L 8000:127.0.0.1:30080 -L 9000:127.0.0.1:30090 cc@<FLOATING_IP>
```

Then verify:

```bash
curl http://localhost:8000/health
curl http://localhost:9000

kubectl port-forward svc/subst-feedback -n forkwise-data 8001:8001
curl -X POST http://localhost:8001/feedback \
  -H "Content-Type: application/json" \
  -d '{"request_id":"demo-1","recipe_id":"123","missing_ingredient":"sour cream","suggested_substitution":"greek yogurt","user_accepted":true}'
```

## 9. Teammate self-demo with Docker

If a teammate only wants to verify the published images, they can run them
outside Kubernetes.

### Feedback service

```bash
docker pull ghcr.io/itsnotaka/forkwise-feedback:demo

docker run --rm -p 8001:8001 \
  -e OS_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480 \
  -e OS_ACCESS_KEY=<YOUR_OS_ACCESS_KEY> \
  -e OS_SECRET_KEY=<YOUR_OS_SECRET_KEY> \
  -e BUCKET=data-proj01 \
  ghcr.io/itsnotaka/forkwise-feedback:demo
```

Test it:

```bash
curl http://localhost:8001/health
```

### One-shot ingest

```bash
docker pull ghcr.io/itsnotaka/forkwise-ingest:demo

docker run --rm \
  -e OS_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480 \
  -e OS_ACCESS_KEY=<YOUR_OS_ACCESS_KEY> \
  -e OS_SECRET_KEY=<YOUR_OS_SECRET_KEY> \
  -e BUCKET=data-proj01 \
  ghcr.io/itsnotaka/forkwise-ingest:demo
```

### Batch pipeline

```bash
docker pull ghcr.io/itsnotaka/forkwise-batch:demo

docker run --rm \
  -e OS_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480 \
  -e OS_ACCESS_KEY=<YOUR_OS_ACCESS_KEY> \
  -e OS_SECRET_KEY=<YOUR_OS_SECRET_KEY> \
  -e BUCKET=data-proj01 \
  -e MIN_NEW_SAMPLES=1 \
  ghcr.io/itsnotaka/forkwise-batch:demo \
  python batch_pipeline.py
```

### Drift monitor

```bash
docker run --rm \
  -e OS_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480 \
  -e OS_ACCESS_KEY=<YOUR_OS_ACCESS_KEY> \
  -e OS_SECRET_KEY=<YOUR_OS_SECRET_KEY> \
  -e BUCKET=data-proj01 \
  -e MIN_REQUESTS_EXPECTED=1 \
  ghcr.io/itsnotaka/forkwise-batch:demo \
  python drift_monitor.py
```

### Generator

The generator only makes sense once serving is reachable:

```bash
docker pull ghcr.io/itsnotaka/forkwise-generator:demo

docker run --rm \
  -e OS_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480 \
  -e OS_ACCESS_KEY=<YOUR_OS_ACCESS_KEY> \
  -e OS_SECRET_KEY=<YOUR_OS_SECRET_KEY> \
  -e BUCKET=data-proj01 \
  -e SERVING_URL=http://<reachable-serving-host>:8000/predict \
  -e REQUESTS_PER_SEC=1 \
  ghcr.io/itsnotaka/forkwise-generator:demo
```

## 10. What to do if something fails

1. `forkwise-ingest` fails:
   check the job logs first; object-store credentials or outbound internet access
   are usually the issue.
2. `subst-feedback` fails:
   confirm `s3-credentials` exists in `forkwise-data`.
3. `substitution-serving` falls back to the stub model:
   confirm `s3-credentials` exists in `forkwise-serving` and that the
   `models/production/` artifacts are present in object storage.
4. `check-rollback` fails:
   confirm the CronJob image was patched to the same serving image and that
   previous model artifacts exist under `models/production/`.
5. `data-generator` fails:
   confirm ingest already wrote the holdout file and the serving URL is reachable.
6. CronJobs stay suspended:
   this is intentional for `batch-pipeline` and `drift-monitor` until you
   explicitly unsuspend them.

This file is the canonical cloud bring-up doc for the unreleased GHCR-based
ForkWise deployment.

For a concise end-to-end verification of the internal data -> training ->
serving loop, use [INTERNAL_LOOP_SMOKE.md](./INTERNAL_LOOP_SMOKE.md).

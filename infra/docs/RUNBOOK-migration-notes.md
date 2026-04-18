# Runbook

This runbook defines the intended bring-up flow for the integrated Mealie plus substitution-serving system.

The deployment target is Chameleon. A local Kubernetes path exists for fast iteration and contract testing.

## Scope Of This Runbook

This runbook is intentionally narrower than the course lab.

It covers:

1. the canonical repo boundary after migration
2. the primary serving path, `FastAPI + ONNX`
3. the open-source application target, `Mealie`
4. the intended split between local iteration and Chameleon deployment

It does not yet claim:

1. full lifecycle automation with Argo Workflows
2. `staging/canary/production` rollout environments
3. a complete feedback and retraining loop inside this repo

## System Shape

The minimum integrated system for this repo is:

1. `Mealie` running in Kubernetes
2. `substitution-serving` running in Kubernetes
3. a documented contract so the application can call the serving API
4. optional shared platform services added only when needed by the next milestone

## Repo Boundary

This repository is now the deployment source of truth.

That means:

1. serving implementation stays here
2. Kubernetes manifests live here
3. Chameleon IaC and deployment orchestration live here
4. other agents should not assume there is a separate canonical infra repo

## Local Iteration Path

Use local container workflows for fast serving-only work:

```bash
docker compose -f docker-compose-fastapi.yaml up --build
```

Primary local serving health check:

```bash
curl http://127.0.0.1:8000/health
```

For local Kubernetes iteration, build an image and apply the app manifests directly:

```bash
docker build -f fastapi_onnx/Dockerfile -t substitution-serving:local .
kubectl apply -k k8s/apps/substitution-serving
kubectl -n forkwise-serving rollout status deployment/substitution-serving
kubectl apply -f k8s/apps/mealie/namespace.yaml
kubectl -n forkwise-app create secret generic mealie-credentials \
  --from-literal=postgres-user=mealie \
  --from-literal=postgres-password=change-me \
  --from-literal=postgres-db=mealie \
  --from-literal=base-url=http://localhost:9000
kubectl apply -k k8s/apps/mealie
```

## Chameleon Target Path

The Chameleon deployment flow implemented in this repo is:

### 1. Provision infrastructure

```bash
cd tf/kvm
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform validate
terraform plan
terraform apply -auto-approve
```

### 2. Configure Ansible

```bash
cd ansible
cp ansible.cfg.example ansible.cfg
```

Replace `REPLACE_WITH_FLOATING_IP` with the Terraform floating IP for `node1`.

### 3. Verify connectivity

```bash
ansible-playbook -i inventory.yml general/hello_host.yml
```

### 4. Prepare nodes and bootstrap k3s

```bash
ansible-playbook -i inventory.yml pre_k8s/pre_k8s_configure.yml
ansible-playbook -i inventory.yml k8s/install_k3s.yml
ansible-playbook -i inventory.yml post_k8s/post_k8s_configure.yml
```

### 5. Build and push the serving image

```bash
docker build -f fastapi_onnx/Dockerfile -t <registry>/substitution-serving:<tag> .
docker push <registry>/substitution-serving:<tag>
```

### 6. Deploy Mealie and substitution-serving

```bash
cd ansible
ansible-playbook -i inventory.yml deploy/deploy_apps.yml \
  -e serving_image=<registry>/substitution-serving:<tag>
```

The deploy playbook will:

1. copy `k8s/` to `node1`
2. create the `mealie-credentials` secret if it does not already exist
3. apply the `Mealie` manifests
4. apply the `substitution-serving` manifests
5. wait for the deployments to roll out

## Primary Serving Path

The primary deployed serving stack is:

1. image build from `fastapi_onnx/Dockerfile`
2. runtime contract from `fastapi_onnx/app.py`
3. health endpoint at `/health`
4. inference endpoint at `/predict`

Triton remains available for benchmarking and comparison, but it is not the default operational path for this migration phase.

## Access Pattern

The app services are intentionally exposed as NodePorts on `node1`:

1. `substitution-serving` -> `30080`
2. `Mealie` -> `30090`

The recommended access path is SSH tunneling through the floating IP of `node1`:

```bash
ssh -N -L 8000:127.0.0.1:30080 -L 9000:127.0.0.1:30090 cc@<FLOATING_IP>
```

Then open:

1. `http://localhost:8000/health`
2. `http://localhost:9000`

## Application Integration Contract

The in-cluster serving URL is intended to be:

`http://substitution-serving.forkwise-serving.svc.cluster.local:8000/predict`

That URL is the contract future Mealie integration work should target, either directly or through a thin adapter layer.

A concrete copy of that contract now also lives in Kubernetes config as:

`ConfigMap/forkwise-integration` in the `forkwise-app` namespace.

The expected serving request shape is:

```json
{
  "recipe_context": [12, 45, 3],
  "missing_ingredient": 77
}
```

The expected response shape is:

```json
{
  "substitutions": [
    {"candidate_id": 42, "score": 0.91}
  ]
}
```

This contract should remain stable as the integration layer is added.

## Deployment Principles

All future infra work added to this repo should follow these rules:

1. Chameleon is the real target environment
2. local Kubernetes exists only to speed up iteration
3. no secrets are committed to Git
4. the repo should reflect actual deployment truth, not lab ceremony copied without use
5. avoid introducing `staging/canary/production` until they are really needed

## Verification Checklist

The minimum branch-level verification target is:

1. this repo clearly reads as the canonical deployment repo
2. the migration boundary is documented in `docs/`
3. the root README explains the new role of the repo
4. the infra directories exist in the canonical places
5. future agents can tell where Mealie deployment and substitution-serving deployment belong

## Related Docs

1. `README.md`
2. `docs/adr/0001-repo-migration-and-deployment-boundary.md`
3. `docs/MIGRATION_MAP.md`

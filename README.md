# ml-sys-ops-project

**ECE-GY 9183 | proj01 | Ingredient Substitution for Mealie**

End-to-end ML system that adds AI-powered ingredient substitution
suggestions to Mealie, a self-hosted recipe manager. When a user is missing an ingredient, the
system suggests ranked substitutions via an embedding-based model trained
on Recipe1MSubs.

## Components

| Directory | Owner | Purpose |
|-----------|-------|---------|
| serving/ | Serving | FastAPI + ONNX inference, monitoring, rollback/promote |
| training/ | Training | Train pipeline, MLflow integration, quality gates |
| data/ | Data | Recipe1MSubs ingestion, feedback capture, drift monitoring |
| infra/ | DevOps | K8S manifests, Ansible, Terraform, automation |
| mealie-integration/ | Team | Mealie backend patch + Vue component |
| archive/ | - | Initial implementation files, preserved for reference |

## Where to start

- New team member: read serving/TEAM_INTEGRATION_MAP.md
- Cloud deployment: `infra/docs/FORKWISE_CLOUD_SETUP.md`
- Serving runbook: `serving/RUNBOOK.md`
- Infra migration notes: `infra/docs/`
- Historical serving integration notes: `serving/INTEGRATION.md`

## Deployment Status

The repo currently includes a working bootstrap path on Chameleon using the
app-oriented namespaces below:

- `forkwise-app` for Mealie
- `forkwise-serving` for the primary serving API
- `forkwise-data` for feedback, ingest, generator, batch, and drift workloads

That bootstrap path is useful for cluster bring-up, and the repo now also
includes an initial rollout implementation under `infra/k8s/platform/`,
`infra/k8s/staging/`, `infra/k8s/canary/`, and `infra/k8s/production/`.

The remaining work for full rubric credit is narrower now:

- validate the rollout loop live on Chameleon under traffic
- verify the custom Mealie flow against the production rollout path
- decide whether the current synthetic canary split is sufficient or whether to add ingress-based traffic splitting
- keep one unified set of buckets, registries, and monitoring services rather than duplicated role-owned stacks

Use these docs as the canonical entry points:

- `infra/docs/FORKWISE_CLOUD_SETUP.md` for cloud bootstrap and target-state notes
- `infra/docs/DEVOPS_RUBRIC_MAP.md` for the DevOps rubric-to-repo mapping
- `infra/docs/EXTERNAL_ACCESS.md` for reviewer/professor SSH tunnel access to Mealie, serving, Grafana, and Prometheus

`infra/docs/FORKWISE_CLOUD_SETUP.md` also includes the practical Jupyter-host
details that came up during real deployment: explicit Terraform/OpenStack env
setup, lease-backed `flavor_id` usage, SSH-agent loading for Ansible, and a
Mealie-only fallback path when the serving image is not ready yet.

## Published ForkWise data images

```text
ghcr.io/itsnotaka/forkwise-ingest:demo
ghcr.io/itsnotaka/forkwise-feedback:demo
ghcr.io/itsnotaka/forkwise-batch:demo
ghcr.io/itsnotaka/forkwise-generator:demo
```

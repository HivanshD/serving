# ADR 0001: Repo Migration And Deployment Boundary

## Status

Accepted.

## Context

This Git repository originally contained only serving-focused implementation and benchmarking assets:

1. `fastapi_onnx/`
2. `fastapi_pt/`
3. `triton_models/`
4. `Dockerfile.triton`
5. `docker-compose-fastapi.yaml`
6. `docker-compose-triton.yaml`
7. `benchmark.py`

That layout was sufficient when the repo was only answering serving questions like latency, throughput, and backend comparison.

It is no longer sufficient once the project needs a single canonical deployment source of truth for the integrated system.

The project now needs a repo that can explain and eventually own:

1. how infrastructure is provisioned on Chameleon
2. how Kubernetes is bootstrapped and configured
3. how Mealie is deployed
4. how the substitution-serving service is deployed
5. how future agents should reason about the difference between the serving implementation and the integrated deployed system

The course lab provides a useful DevOps shape with `tf/`, `ansible/`, `k8s/`, and operations documentation, but the lab structure is a reference pattern rather than something to copy unchanged.

## Decision

This repository is now the canonical deployment repository for the integrated system.

The repo boundary decision is:

1. keep the existing `serving/` Git repository as canonical
2. keep the current serving implementation files at repo root
3. add `tf/`, `ansible/`, `k8s/`, and `docs/` beside the existing serving code
4. treat `FastAPI + ONNX` as the primary deployed serving path
5. keep `Triton` as a secondary benchmark and experiment path
6. target local Kubernetes for iteration and Chameleon for documented real deployment
7. avoid adopting `staging/canary/production` until those environments are actually required
8. use a simplified k3s-based Kubernetes bootstrap path before introducing heavier cluster automation
9. use `node1` as the initial app entrypoint via SSH tunneling and NodePorts

## Rationale

This decision keeps the current repository identity intact while making the deployment boundary explicit.

The main reasons are:

1. the current Git history already belongs to the serving code that the integrated system depends on
2. adding infra beside the existing code is less disruptive than creating a second canonical repo
3. future agents need one place to understand both the serving implementation and the deployment intent
4. `FastAPI + ONNX` is operationally simpler than Triton for the first Kubernetes deployment path
5. the lab's multi-environment rollout model introduces complexity we are not yet using

## Consequences

After this migration:

1. this repo must be readable as more than a benchmark repo
2. documentation must explain the old and new meanings of the repo
3. infra secrets and local auth material must be ignored in Git
4. new deployment work should land in this repo unless there is a deliberate exception
5. other agents should assume this repo is the source of truth for deployment shape

## Explicit Non-Goals For This Phase

This ADR does not claim that the repo already contains:

1. a full retraining pipeline
2. full feedback-loop persistence
3. Argo-based lifecycle automation
4. production rollout environments beyond the minimum needed integrated system

Those can be added later without changing the repo-boundary decision.

## Implementation Notes

The first documentation and structure changes required by this ADR are:

1. root `README.md` rewritten around the new repo role
2. `RUNBOOK.md` added as the operational entrypoint
3. `docs/MIGRATION_MAP.md` added for old-to-new path mapping
4. `tf/`, `ansible/`, and `k8s/` created as canonical homes for deployment assets

## References

1. `README.md`
2. `RUNBOOK.md`
3. `docs/MIGRATION_MAP.md`

# Migration Map

This document explains how to interpret the repository after the infra migration.

Its audience is another engineer or agent landing on this branch and asking:

1. what this repo used to be
2. what it is now
3. what moved conceptually even if files were not physically relocated
4. which lab concepts were intentionally adopted or rejected

## Old Repo Meaning

Before the migration, this repo was effectively a serving experiment repo.

The main areas were:

1. `fastapi_onnx/` for ONNX-based serving
2. `fastapi_pt/` for PyTorch-based serving
3. `triton_models/` and `Dockerfile.triton` for Triton-based serving
4. `docker-compose-fastapi.yaml` and `docker-compose-triton.yaml` for local container execution
5. `benchmark.py` for serving-side evaluation

In that phase, the repo answered questions like:

1. which serving backend is faster
2. how to expose `/predict`
3. how to benchmark latency and throughput

## New Repo Meaning

After the migration, this repo is the canonical deployment source of truth for the integrated Mealie plus substitution-serving system.

It now needs to answer additional questions:

1. where Chameleon infrastructure definitions belong
2. where Kubernetes deployment assets belong
3. where app-level deployment assets belong
4. how another agent should distinguish primary deployment paths from experimental ones

## Path Mapping

### Existing Paths That Still Mean The Same Thing

1. `fastapi_onnx/` remains the primary implementation of the serving API
2. `fastapi_pt/` remains a comparison implementation
3. `triton_models/` remains the Triton model repository
4. `Dockerfile.triton` remains the Triton experiment path
5. `benchmark.py` remains the serving benchmark script

### New Canonical Infra Paths

1. `tf/` is the canonical home for Chameleon infrastructure definitions
2. `ansible/` is the canonical home for configuration and deployment orchestration
3. `k8s/` is the canonical home for Kubernetes application manifests
4. `docs/` is the canonical home for architecture and migration documentation
5. `RUNBOOK.md` is the canonical operational entrypoint

## Primary Vs Secondary Paths

### Primary Operational Path

The first-class deployment path is:

1. `FastAPI + ONNX`
2. local Kubernetes for iteration
3. Chameleon as the documented real environment
4. Mealie as the open-source application target
5. k3s as the current Kubernetes bootstrap choice
6. `node1` as the initial browser and SSH-tunnel entrypoint

### Secondary Experimental Path

The following assets remain important but are not the default deployment path:

1. Triton
2. PyTorch FastAPI
3. raw Docker Compose experiments

## Intentional Deviations From The Course Lab Shape

The course lab is a reference for DevOps structure, not a requirement to copy every concept.

This branch intentionally adopts:

1. `tf/`
2. `ansible/`
3. `k8s/`
4. explicit runbook and migration docs

This branch intentionally does not adopt, at least yet:

1. `staging/canary/production` rollout folders
2. Argo Workflows and full lifecycle automation
3. platform services that are not yet needed for the minimum integrated system

## What Another Agent Should Assume

Another agent reading this branch should assume:

1. this repo is now the canonical deployment repo
2. new deployment assets should be added here by default
3. the repo root still contains the serving implementation because that implementation is central, not accidental
4. Mealie deployment should live under `k8s/`, not in a separate hidden repo
5. future retraining and feedback-loop pieces may be added later, but should respect this repo boundary

## What Another Agent Should Not Assume

Another agent should not assume:

1. there is still a separate canonical infra repo elsewhere
2. `Triton` is the default production path
3. `staging/canary/production` exists and is required right now
4. the absence of full automation means the repo boundary is temporary

## Practical Reading Order

If you are new to this branch, read in this order:

1. `README.md`
2. `docs/adr/0001-repo-migration-and-deployment-boundary.md`
3. `RUNBOOK.md`
4. the relevant app or infra subdirectory

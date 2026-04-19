# AGENTS.md

## Repo Overview

This repository does not have a single application entry point.
It is a multi-surface course project with separate code and docs for serving,
infra, training, data, and Mealie integration.

Use this file as the top-level navigation map before diving into a subdirectory.

## Directory Map

| Path                  | Status                        | What To Expect                                                                         |
| --------------------- | ----------------------------- | -------------------------------------------------------------------------------------- |
| `serving/`            | Most complete, runnable       | FastAPI + ONNX/PyTorch serving code, Dockerfiles, benchmarks, runbooks, local Makefile |
| `infra/tf/kvm/`       | Runnable                      | Terraform for Chameleon KVM provisioning                                               |
| `infra/ansible/`      | Runnable                      | Ansible playbooks for k3s bootstrap and app deploy                                     |
| `infra/k8s/`          | Source of truth for manifests | App-oriented Kubernetes layout using Kustomize                                         |
| `training/`           | Mostly contract/docs          | Expected training outputs and integration notes                                        |
| `data/`               | Mostly contract/docs          | Data-team ownership notes                                                              |
| `mealie-integration/` | Mostly contract/docs          | Mealie integration notes                                                               |
| `archive/`            | Reference only                | Older implementation snapshots; do not treat as current source                         |

## Best Starting Points

Choose the docs based on the task.

1. For local serving work, read `serving/README.md` and `serving/Makefile`.
2. For cross-team contracts, read `serving/INTEGRATION.md` and `serving/TEAM_INTEGRATION_MAP.md`.
3. For serving operations, read `serving/RUNBOOK.md`.
4. For Terraform changes, read `infra/tf/kvm/README.md`.
5. For Ansible changes, read `infra/ansible/README.md`.
6. For Kubernetes manifest changes, read `infra/k8s/README.md` and the app README under `infra/k8s/apps/`.

## Working Rules

1. Prefer the root `Makefile` for common repo-level commands. Drop into component-specific commands only when you need deeper control.
2. Treat `archive/` as read-only unless a task explicitly asks for historical reference or migration work.
3. Do not assume `training/`, `data/`, or `mealie-integration/` are complete standalone apps; today they are mostly documentation and contracts.
4. When editing serving code, keep the request/response contract in sync with `serving/sample_data/input_sample.json` and `serving/sample_data/output_sample.json`.
5. When editing infra, preserve the current app-oriented layout in `infra/k8s/apps/` instead of reintroducing the older environment-oriented structure unless the task explicitly requires it.

## Notes For Contributors

1. The easiest local demo path is `serving/`; it is the only area with a mature local dev workflow in this repo.
2. The current top-level docs are partly historical. If you change a workflow, update `README.md` and the closest subdirectory README in the same pass.
3. If you add a new stable entry point, also add it to the root `Makefile` so people can discover it quickly.

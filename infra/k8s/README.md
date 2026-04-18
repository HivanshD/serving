# Kubernetes Layout

This directory is the canonical home for Kubernetes application manifests.

## Layout

```text
k8s/
├── apps/
│   ├── mealie/
│   └── substitution-serving/
└── platform/
```

## Direction

The repository intentionally uses app-oriented names instead of the course lab's `staging/canary/production` layout.

That is because this migration phase is focused on a minimal integrated system rather than a multi-environment rollout model.

The current first-pass deployment choices are:

1. app workloads are pinned to `node1` for simpler persistent-volume behavior
2. browser-facing access uses NodePorts on `node1`
3. the recommended user path is SSH tunneling through the single floating IP

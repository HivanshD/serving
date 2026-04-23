# Kubernetes Layout

This directory is the canonical home for Kubernetes manifests for the integrated
ForkWise system.

## Current State

```text
k8s/
├── apps/
│   ├── forkwise-data/
│   ├── mealie/
│   └── substitution-serving/
├── platform/
├── staging/
├── canary/
└── production/
```

The repo now contains both:

1. bootstrap app manifests under `apps/`
2. the rollout and platform directories needed for the four-person Kubernetes target

The `apps/` manifests are still useful for first cloud bring-up and as reusable
bases for Mealie, serving, and data workloads.

## Apr 20 / Final Target

For a four-person team, the course rubric requires the integrated Kubernetes
system to support:

1. shared platform services deployed once for the whole system
2. separate `staging`, `canary`, and `production` environments
3. automated promotion and rollback rules
4. monitoring, alerting, and autoscaling

The current rollout structure is:

```text
k8s/
├── apps/        # bootstrap and reusable app manifests
├── platform/    # shared services: monitoring, automation, dashboards
├── staging/     # staging serving deployment
├── canary/      # canary serving deployment + promote checks
└── production/  # production serving deployment + HPA + rollback checks
```

## Working Rules

1. Do not duplicate shared platform services per role or environment unless there is a real technical reason.
2. Treat `apps/` as reusable building blocks or bootstrap manifests, not the final system boundary.
3. Keep shared service names, buckets, secrets, and promotion logic consistent across environments.
4. Keep NodePort and entrypoint pinning only where it is needed for Chameleon access and local-path storage behavior.

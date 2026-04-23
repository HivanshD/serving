# Mealie Kubernetes App

This directory is the canonical home for Mealie deployment assets.

## Why It Exists

The course lab often represents application environments as `staging`, `canary`, and `production`.

This repo intentionally does not do that yet.

For this migration phase, we only need a clear and truthful home for the open-source application deployment.

## What Is Here Now

This directory now contains a first raw-manifest pass for:

1. the Mealie deployment
2. the Postgres dependency used by Mealie
3. persistent volume claims for app and database data
4. a NodePort service for Mealie on `30090`
5. an integration `ConfigMap` declaring the in-cluster substitution-serving and feedback URLs
6. stateful workloads pinned to `node1` for simpler local-path storage behavior

Apply with:

```bash
kubectl apply -k k8s/apps/mealie
```

## Deploy-Time Secret Requirement

These manifests expect a `mealie-credentials` secret in the `forkwise-app` namespace.

Example shape:

```bash
kubectl -n forkwise-app create secret generic mealie-credentials \
  --from-literal=postgres-user=mealie \
  --from-literal=postgres-password=change-me \
  --from-literal=postgres-db=mealie \
  --from-literal=base-url=http://localhost:9000
```

Do not commit live credentials to Git.

## Future Integration Targets

The intended in-cluster service contracts for Mealie-side integration are:

1. `http://substitution-serving.forkwise-serving.svc.cluster.local:8000/predict`
2. `http://subst-feedback.forkwise-data.svc.cluster.local:8001/feedback`

This repo does not yet implement the application-side hook, but these are the
service contract locations that future work should target.

## Mealie-Only Fallback

The Mealie deployment can boot without a live `substitution-serving` backend.
The substitution UI will still render because it is part of the custom Mealie
image, but feature calls that depend on serving or feedback will fail until
those services are reachable.

If you only need to bring up Mealie on a fresh cluster, create the secret and
apply this app directly:

```bash
kubectl create namespace forkwise-app --dry-run=client -o yaml | kubectl apply -f -

kubectl -n forkwise-app create secret generic mealie-credentials \
  --from-literal=postgres-user=mealie \
  --from-literal=postgres-password=change-me \
  --from-literal=postgres-db=mealie \
  --from-literal=base-url=http://localhost:9000 \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k k8s/apps/mealie
kubectl rollout status deployment/mealie-postgres -n forkwise-app --timeout=180s
kubectl rollout status deployment/mealie -n forkwise-app --timeout=240s
kubectl get pods -n forkwise-app
kubectl get svc -n forkwise-app
```

The service is exposed as NodePort `30090`, so the recommended access path is
still an SSH tunnel through the cluster entrypoint node.

## Expected Responsibility

Assets added here should define:

1. the Mealie app deployment
2. any required database deployment or dependency wiring
3. the service exposure used by the integrated system
4. future configuration needed for Mealie to call the serving and feedback APIs

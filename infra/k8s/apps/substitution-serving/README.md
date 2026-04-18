# Substitution Serving Kubernetes App

This directory is the canonical home for the primary serving deployment.

## Primary Path

The first-class deployed serving path is:

1. image built from `fastapi_onnx/Dockerfile`
2. API contract defined in `fastapi_onnx/app.py`
3. deployment assets stored here

## What Is Here Now

This directory now contains a first raw-manifest pass for:

1. namespace creation
2. deployment
3. a NodePort service on `30080`
4. readiness and liveness probes on `/health`
5. pod pinning to `node1` for predictable tunneling and simpler first deployment behavior

Apply with:

```bash
kubectl apply -k k8s/apps/substitution-serving
```

The deployment currently defaults to the local-development image reference:

`substitution-serving:local`

For Chameleon deployment, the Ansible deploy playbook overrides that image with the `serving_image` variable.

## Secondary Paths

The following are still useful, but are not the default operational path:

1. `fastapi_pt/`
2. Triton via `Dockerfile.triton` and `triton_models/`

## Expected Responsibility

Assets added here should define:

1. deployment and service manifests
2. environment variables and image contract
3. readiness and liveness expectations
4. resource requests and limits once measured

# INTEGRATION.md — Serving Cross-Team Contract

This document is the single source of truth for how the serving component
integrates with Training, Data, DevOps, and the Mealie frontend.

If anything in this document is unclear or changes, ping me (serving role,
Hivansh) before making assumptions.

---

## Part 1 — What serving EXPECTS FROM other teams

### 1.1 From Training team

Training must produce a PyTorch checkpoint that serving can load. The
checkpoint format is a dict with these EXACT keys:

```python
{
  "model_state_dict": <OrderedDict from model.state_dict()>,
  "vocab": {
    "<PAD>": 0,
    "<UNK>": 1,
    "sour cream": 2,
    "greek yogurt": 3,
    # ... every ingredient string → int ID
  },
  "config": {
    "embed_dim": 128,
    "lr": 0.001,
    "batch_size": 64,
    "epochs": 20,
    # ... other hyperparameters
  }
}
```

**Hard requirements:**

1. `model_state_dict` must be loadable into a `SubstitutionModel` whose
   architecture matches `fastapi_pt/model_stub.py` EXACTLY. Any architecture
   change in training means updating `model_stub.py` in serving's repo too.

2. `vocab` must contain `<PAD>` at index 0 and `<UNK>` at index 1. These are
   the reserved tokens that serving expects.

3. Ingredient keys in `vocab` must be **lowercased, stripped, normalized**
   strings (e.g. `"sour cream"`, NOT `"Sour Cream"` or `"1 cup sour cream"`).
   Normalization happens in `data/online_features.py` (Data team owns).

4. `config["embed_dim"]` must match the embedding layer's actual dimension.
   Serving reads this to construct the model shell before `load_state_dict`.

**Training must upload to object storage:**

- `models-proj01/checkpoints/subst_model_v{run_id}.pth` — archive of each
  candidate (used for rollback history)
- `models-proj01/production/subst_model_current.pth` — the CURRENT production
  model. Serving downloads this on every pod startup.

**Training must ALSO run `scripts/export_onnx.py` after a successful run:**

```bash
# Inside the training container, after save_and_register():
python /app/scripts/export_onnx.py --from-object-storage
```

This downloads the latest checkpoint, exports to ONNX opset 14, and uploads:

- `models-proj01/production/subst_model_current.onnx`
- `models-proj01/production/vocab.json`

The ONNX serving container reads these two files on startup.

**Integration question to ask Training:**

> "Can you confirm the checkpoint dict has keys `model_state_dict`, `vocab`,
> `config`, and that your model architecture matches the `SubstitutionModel`
> class in `serving/fastapi_pt/model_stub.py` exactly? After save_and_register,
> can you call `export_onnx.py --from-object-storage` so the ONNX artifacts
> are also in object storage?"

---

### 1.2 From Data team

Data owns the request/response input format. Serving uses this schema for
its `/predict` endpoint. The agreed schema lives in
`serving/sample_data/input_sample.json` and `output_sample.json`.

**Data must provide `online_features.py`** — a Python module that, given a
Mealie recipe object + a raw missing ingredient string, returns a payload
matching `input_sample.json` EXACTLY.

Specifically, Data must normalize:
- `"1 cup sour cream, room temperature"` → `"sour cream"`
- `"1/2 tsp salt"` → `"salt"`
- `"3 large eggs"` → `"egg"`
- `"1 (15 oz) can diced tomatoes"` → `"diced tomatoes"`

Serving will lowercase and strip whitespace one more time as defense-in-depth,
but the heavy lifting (unit stripping, quantity removal, parenthetical removal)
is Data's responsibility.

**Data writes feedback to `logs-proj01/feedback/`.** Serving DOES NOT handle
`/feedback` — the Mealie frontend calls Data's feedback endpoint directly:

```
Mealie frontend → POST http://subst-feedback.production-proj01:8001/feedback
```

Not:

```
Mealie frontend → POST /feedback on serving  ❌ WRONG
```

This separation means Data owns feedback writes end-to-end.

**Integration question to ask Data:**

> "Can you send me a sample output from `online_features.build_serving_payload()`
> for a real Mealie recipe? I want to confirm the `normalized` strings match
> what Training's vocab will contain. Also, where is `feedback_endpoint.py`
> deployed — what's the in-cluster URL for the Mealie frontend to call?"

---

### 1.3 From DevOps team

DevOps owns the K8S manifests, namespaces, and automation webhook.
Serving provides the container images they reference.

**Container images serving produces:**

```
subst-serving-onnx:v{git_sha}   ← production default
subst-serving-pt:v{git_sha}     ← baseline
subst-triton:v{git_sha}         ← GPU benchmarking
```

**DevOps must:**

1. Create 4 K8S namespaces: `staging-proj01`, `canary-proj01`,
   `production-proj01`, `monitoring-proj01`.

2. Create a K8S Secret called `os-credentials` in every namespace that runs
   serving pods. Required keys:
   ```
   OS_ENDPOINT       # e.g. http://minio.default:9000
   OS_ACCESS_KEY
   OS_SECRET_KEY
   ```

3. Write a Deployment for serving with these exact env vars + annotations:
   ```yaml
   metadata:
     annotations:
       prometheus.io/scrape: "true"
       prometheus.io/port: "8000"
       prometheus.io/path: "/metrics"
   spec:
     containers:
       - name: subst-serving
         image: subst-serving-onnx:v<sha>
         ports:
           - containerPort: 8000
         env:
           - name: OS_ENDPOINT
             valueFrom: { secretKeyRef: { name: os-credentials, key: OS_ENDPOINT } }
           - name: OS_ACCESS_KEY
             valueFrom: { secretKeyRef: { name: os-credentials, key: OS_ACCESS_KEY } }
           - name: OS_SECRET_KEY
             valueFrom: { secretKeyRef: { name: os-credentials, key: OS_SECRET_KEY } }
           - name: LOG_REQUESTS
             value: "true"
           - name: REQUEST_LOG_BUCKET
             value: "logs-proj01"
           - name: SERVING_VERSION
             value: "onnx-quantized-v<sha>"
         readinessProbe:
           httpGet: { path: /health, port: 8000 }
           initialDelaySeconds: 15
           periodSeconds: 5
         livenessProbe:
           httpGet: { path: /health, port: 8000 }
           initialDelaySeconds: 30
           periodSeconds: 10
         resources:
           requests: { cpu: "500m", memory: "512Mi" }
           limits:   { cpu: "1000m", memory: "1Gi" }
   ```

4. Write a Service so Mealie can reach serving at:
   `http://subst-serving.production-proj01.svc.cluster.local:8000/predict`

5. Write an NGINX Ingress with canary annotation (10% canary, 90% prod)
   so both namespaces get traffic during canary evaluation.

6. Write CronJob manifests that RUN SERVING'S SCRIPTS:
   - `scripts/check_rollback.py` → every 5 min in `production-proj01`
   - `scripts/check_promote.py` → every 5 min in `canary-proj01`

   Both CronJobs reuse the `subst-serving-onnx` image (the scripts are
   already baked in at `/app/scripts/`). They need env vars:
   ```
   PROMETHEUS_URL=http://prometheus.monitoring-proj01:9090
   DEVOPS_HOOK=http://automation.monitoring-proj01:8080
   NAMESPACE=production-proj01          # for check_rollback
   CANARY_NS=canary-proj01              # for check_promote
   PROD_NS=production-proj01            # for check_promote
   ```

   `check_promote.py` shells out to `kubectl`, so the CronJob's ServiceAccount
   needs `get` permission on `deployments` in `canary-proj01`:
   ```yaml
   apiVersion: rbac.authorization.k8s.io/v1
   kind: Role
   metadata:
     name: deployment-reader
     namespace: canary-proj01
   rules:
     - apiGroups: ["apps"]
       resources: ["deployments"]
       verbs: ["get", "list"]
   ```

7. Write the `automation.py` webhook service. It exposes:
   - `POST /rollback` → `kubectl rollout undo deployment/subst-serving -n <ns>`
   - `POST /promote` → `kubectl set image deployment/subst-serving ...`

   Its ServiceAccount needs write permissions on deployments in staging,
   canary, and production namespaces.

8. Write an HPA so serving scales 1→4 pods based on CPU ≥ 70%.

**Integration question to ask DevOps:**

> "Can you confirm the cluster has 4 namespaces created, an `os-credentials`
> secret in each, and Prometheus pod-scraping enabled? What's the service name
> I should expect for the automation webhook — `automation.monitoring-proj01`?
> And can you confirm the canary ingress is set to 10% canary / 90% prod?"

---

### 1.4 From Mealie integration

The Mealie backend (patched by the whole team together) must:

1. Import or copy `data/online_features.py` into its codebase.
2. Add a new route:
   ```python
   @router.post("/recipes/{slug}/substitutions")
   def get_substitutions(slug: str, body: MissingIngredientRequest):
       recipe = recipe_service.get_by_slug(slug)
       payload = online_features.build_serving_payload(
           recipe, body.missing_ingredient)
       try:
           r = httpx.post(
               "http://subst-serving.production-proj01:8000/predict",
               json=payload, timeout=2.0)
           return r.json()
       except Exception:
           # Robustness safeguarding: never crash Mealie on serving failure
           return {"substitutions": [], "fallback": True}
   ```

**Integration question to ask the team:**

> "When we wire up the Mealie integration, let's agree that the backend route
> calls my serving endpoint at `http://subst-serving.production-proj01:8000/predict`
> and the frontend calls the feedback endpoint at
> `http://subst-feedback.production-proj01:8001/feedback` — NOT through serving.
> Also, the Vue component needs to display `serving_version` somewhere (even
> just in a tooltip) for the transparency safeguarding item."

---

## Part 2 — What serving PROVIDES TO other teams

### 2.1 For Training

- `fastapi_pt/model_stub.py` — the model architecture file. Training imports
  this to ensure train/serve architecture parity.
- `scripts/export_onnx.py` — training runs this after a passing run to push
  ONNX to object storage.
- `scripts/quantize_onnx.py` — training can optionally run this to produce
  the quantized ONNX. If skipped, serving serves the FP32 ONNX (slightly
  slower but still fine).

### 2.2 For Data

- `sample_data/input_sample.json` — the exact schema the `/predict` endpoint
  accepts. Data's `online_features.py` must produce this schema.
- `sample_data/output_sample.json` — what `/predict` returns. Data's
  `data_generator.py` consumes this shape.

### 2.3 For DevOps

- Three container image build targets (see table above).
- Two CronJob scripts (`check_rollback.py`, `check_promote.py`) that run
  inside the `subst-serving-onnx` image.
- Prometheus metrics exposed at `/metrics` on port 8000 — already has scrape
  annotations in my deployment snippet for you.

### 2.4 For Mealie integration

- A stable HTTP contract:
  - `POST /predict` → takes `input_sample.json` shape, returns `output_sample.json` shape
  - `GET /health` → returns 200 when ready
  - `GET /metrics` → Prometheus format

---

## Part 3 — End-to-end integration test (run this before recording demo)

Execute on a pod inside the cluster (or port-forward from local):

```bash
# 1. Serving is up and healthy
curl http://subst-serving.production-proj01:8000/health
# expect: {"status":"ok","model_loaded":true,...}

# 2. Metrics endpoint works
curl http://subst-serving.production-proj01:8000/metrics | grep subst_request
# expect: subst_request_latency_seconds_bucket ... etc.

# 3. Predict returns real (non-stub) results
curl -X POST http://subst-serving.production-proj01:8000/predict \
  -H "Content-Type: application/json" \
  -d @sample_data/input_sample.json
# expect: substitutions array with 3 items, real ingredient names

# 4. Request got logged to object storage
mc ls logs-proj01/requests/ | tail -5
# expect: recent files, request_<timestamp>_<id>.json

# 5. Mealie can reach serving
kubectl exec -n production-proj01 deploy/mealie -- \
  curl -X POST http://subst-serving.production-proj01:8000/predict \
  -H "Content-Type: application/json" \
  -d @sample_data/input_sample.json

# 6. Rollback works (simulate by breaking serving on purpose)
kubectl set image deploy/subst-serving -n production-proj01 \
  subst-serving=nginx:latest   # deliberately wrong image
# Within ~5 min, check_rollback.py should detect the failure and roll back
kubectl rollout history deploy/subst-serving -n production-proj01

# 7. Promote works
# After deploying a new version to canary, within 30-35 min check_promote.py
# should promote it. Check the automation webhook logs.
kubectl logs -n monitoring-proj01 deploy/automation --tail=20
```

---

## Part 4 — Questions I need answered (check these off as I get answers)

- [ ] **Training**: confirmed checkpoint dict format matches Part 1.1
- [ ] **Training**: confirmed `export_onnx.py --from-object-storage` runs after save_and_register
- [ ] **Data**: sample output from `online_features.build_serving_payload()` matches my input schema
- [ ] **Data**: feedback endpoint URL is `http://subst-feedback.production-proj01:8001/feedback`
- [ ] **DevOps**: 4 namespaces exist
- [ ] **DevOps**: `os-credentials` secret exists in every namespace
- [ ] **DevOps**: Prometheus is auto-scraping pods with annotations
- [ ] **DevOps**: automation webhook URL is `http://automation.monitoring-proj01:8080`
- [ ] **DevOps**: canary ingress weight is 10%
- [ ] **DevOps**: CronJob ServiceAccount has kubectl permissions for check_promote.py
- [ ] **Team**: Mealie backend calls serving at `subst-serving.production-proj01:8000/predict`
- [ ] **Team**: Mealie frontend calls feedback at `subst-feedback.production-proj01:8001/feedback`
- [ ] **Team**: Vue component displays `serving_version` (transparency)

---

## Part 5 — Things that can break integration (check these are right)

| Potential break | How to prevent |
|----------------|----------------|
| Training uses different embed_dim than serving expects | Serving reads `embed_dim` from checkpoint dict → no mismatch possible |
| ONNX opset mismatch with Triton | `export_onnx.py` hardcodes opset 14 |
| Data's normalized strings don't match Training's vocab | Both sides agree on: lowercase, stripped, unit-removed, parenthetical-removed |
| Mealie crashes when serving is down | Mealie backend has try/except fallback returning `{substitutions: [], fallback: true}` |
| Serving pod crash-loops when model isn't ready | `reload_model.py` always exits 0; `load_model()` falls back to stub |
| Prometheus can't scrape serving | Deployment must have the 3 `prometheus.io/*` annotations |
| check_rollback triggers during startup when metrics are empty | Script returns early if `p95 is None` |
| Canary promoted too fast | `check_promote.py` requires age ≥ 30 min AND all health conditions |

---

Last updated: 2026-04-17

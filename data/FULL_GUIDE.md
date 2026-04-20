# FULL GUIDE — Data Team, Start to Finish

---

## Your files (what you push to Git)

```
data/
├── ingest.py              ← QC1 (downloads data, validates, uploads)
├── batch_pipeline.py      ← QC2 (compiles training set from feedback)
├── drift_monitor.py       ← QC3 (checks OOV, confidence, volume)
├── feedback_endpoint.py   ← FastAPI capturing user accept/reject
├── online_features.py     ← Mealie recipe → serving input format
├── data_generator.py      ← replays holdout against serving
├── requirements.txt
├── Dockerfile.ingest
├── Dockerfile.feedback
├── Dockerfile.batch
├── Dockerfile.generator
├── README.md
├── AGENTS.md
└── STRUCTURE.md
```

---

## PHASE A: Setup (do once)

---

### A1. Generate EC2 credentials

📍 **WHERE:** Chameleon Jupyter notebook (https://jupyter.chameleoncloud.org)

Open a new notebook. Run this single cell:

```python
import chi
from chi import context

context.version = "1.0"
context.choose_project()
context.choose_site(default="CHI@TACC")

conn_tacc = chi.clients.connection()
project_id = conn_tacc.current_project_id
identity_ep = conn_tacc.session.get_endpoint(
    service_type="identity", interface="public")
url = f"{identity_ep}/v3/users/{conn_tacc.current_user_id}/credentials/OS-EC2"

resp = conn_tacc.session.post(url, json={"tenant_id": project_id})
resp.raise_for_status()
ec2 = resp.json()["credential"]

print("=" * 50)
print("SAVE THESE — you need them for everything below")
print("=" * 50)
print(f'export OS_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480')
print(f'export OS_ACCESS_KEY={ec2["access"]}')
print(f'export OS_SECRET_KEY={ec2["secret"]}')
```

Copy the 3 `export` lines. You will paste them into your terminal before every step below.

**Done with Chameleon Jupyter.** You don't need it again.

---

### A2. Push files to Git

📍 **WHERE:** Your laptop terminal

```bash
# Clone the team repo (or cd into it if you already have it)
git clone https://github.com/<your-org>/ml-sys-ops-project.git
cd ml-sys-ops-project

# Copy your data/ folder into the repo
# (assuming you downloaded the files from Claude)
cp -r ~/Downloads/forkwise/data ./data/

# Push
git add data/
git commit -m "data: add QC1/QC2/QC3 pipelines, feedback endpoint, data generator"
git push
```

Your folder in the repo should now be `ml-sys-ops-project/data/data/` or `ml-sys-ops-project/data/` depending on your repo structure. Match whatever the team uses.

---

## PHASE B: Run ingestion (do once)

---

### B1. Run ingest.py

📍 **WHERE:** Your laptop terminal (or any machine with Python + internet)

This downloads ~750MB of data, validates it, and uploads to your CHI@TACC bucket.
Takes ~10-15 minutes on a decent connection.

```bash
# Install dependency
pip install boto3

# Set credentials (paste the 3 lines from step A1)
export OS_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480
export OS_ACCESS_KEY=<paste yours>
export OS_SECRET_KEY=<paste yours>

# Run
python data/ingest.py
```

**Expected output:**
```
[setup] Bucket data-proj01 ready
STEP 1: Downloading data sources
  Downloading: recipe1M_layers.tar.gz...
  Saved (620.3 MB)
  ...
STEP 3: QC1 — Validate + upload as JSON
[QC1] train (XXXXX records)...
[QC1] train: PASSED (6/6 checks)
[QC1] val: PASSED (6/6 checks)
[QC1] test: PASSED (6/6 checks)
STEP 4: Production holdout
  Holdout: XXXX records (NEVER train on this)
STEP 5: Recipe context from layer1.json
  Context map: 1,029,720 recipes
[ingest] ALL PASSED (12.3 min). Training can start.
```

### B2. Verify in Chameleon Horizon

📍 **WHERE:** Browser — https://chi.tacc.chameleoncloud.org

Go to **Project → Object Store → Containers → data-proj01**

You should see:
```
data/raw/recipe1msubs/train.json     ✓
data/raw/recipe1msubs/val.json       ✓
data/raw/recipe1msubs/test.json      ✓
data/raw/recipe1m/layer1.json        ✓
data/raw/recipe1m/context_map.json   ✓
data/production_holdout/holdout.json ✓
data/quality_reports/ingest_*.json   ✓
```

**Tell team chat:** "Data uploaded to `data-proj01`. Training can start."

---

## PHASE C: Build Docker images

---

### C1. Build and push all 4 images

📍 **WHERE:** Your laptop terminal, from the `data/` folder

```bash
cd data/

# Build all 4 images
docker build -f Dockerfile.ingest    -t forkwise-ingest .
docker build -f Dockerfile.feedback  -t forkwise-feedback .
docker build -f Dockerfile.batch     -t forkwise-batch .
docker build -f Dockerfile.generator -t forkwise-generator .

# Tag for your container registry
# Ask DevOps what registry to use. Examples:
#   Docker Hub:  docker.io/<username>/forkwise-feedback:latest
#   GitHub CR:   ghcr.io/<org>/forkwise-feedback:latest
#   If team uses a private registry, ask for the URL.

docker tag forkwise-feedback <REGISTRY>/forkwise-feedback:latest
docker tag forkwise-batch    <REGISTRY>/forkwise-batch:latest
docker tag forkwise-generator <REGISTRY>/forkwise-generator:latest

# Push
docker push <REGISTRY>/forkwise-feedback:latest
docker push <REGISTRY>/forkwise-batch:latest
docker push <REGISTRY>/forkwise-generator:latest
```

(You don't need to push `forkwise-ingest` — it already ran locally in step B1.)

---

## PHASE D: Deploy to Kubernetes

---

### D0. Create the S3 credentials secret

📍 **WHERE:** Your laptop terminal (with kubectl configured to your cluster)

Ask DevOps: "Can I run kubectl? What's the namespace?"

```bash
kubectl create secret generic s3-credentials \
  --from-literal=access-key=<YOUR_OS_ACCESS_KEY> \
  --from-literal=secret-key=<YOUR_OS_SECRET_KEY> \
  -n production-proj01
```

---

### D1. Deploy feedback endpoint (always running)

📍 **WHERE:** Your laptop terminal

Create file `data/k8s/feedback.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: subst-feedback
  namespace: production-proj01
spec:
  replicas: 1
  selector:
    matchLabels:
      app: subst-feedback
  template:
    metadata:
      labels:
        app: subst-feedback
    spec:
      containers:
      - name: feedback
        image: <REGISTRY>/forkwise-feedback:latest
        ports:
        - containerPort: 8001
        env:
        - name: OS_ENDPOINT
          value: "https://chi.tacc.chameleoncloud.org:7480"
        - name: OS_ACCESS_KEY
          valueFrom:
            secretKeyRef:
              name: s3-credentials
              key: access-key
        - name: OS_SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: s3-credentials
              key: secret-key
        livenessProbe:
          httpGet:
            path: /health
            port: 8001
          initialDelaySeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: subst-feedback
  namespace: production-proj01
spec:
  selector:
    app: subst-feedback
  ports:
  - port: 8001
    targetPort: 8001
```

Apply:
```bash
kubectl apply -f data/k8s/feedback.yaml
```

Verify:
```bash
kubectl get pods -n production-proj01 -l app=subst-feedback
# Should show: Running

# Test it
kubectl port-forward svc/subst-feedback 8001:8001 -n production-proj01 &
curl -X POST http://localhost:8001/feedback \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"test1","recipe_id":"123","missing_ingredient":"sour cream","suggested_substitution":"greek yogurt","user_accepted":true}'
# Should return: {"status":"logged","key":"logs/feedback/..."}
```

---

### D2. Deploy data generator (always running)

📍 **WHERE:** Your laptop terminal

Create file `data/k8s/generator.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: data-generator
  namespace: production-proj01
spec:
  replicas: 1
  selector:
    matchLabels:
      app: data-generator
  template:
    metadata:
      labels:
        app: data-generator
    spec:
      containers:
      - name: generator
        image: <REGISTRY>/forkwise-generator:latest
        env:
        - name: OS_ENDPOINT
          value: "https://chi.tacc.chameleoncloud.org:7480"
        - name: OS_ACCESS_KEY
          valueFrom:
            secretKeyRef:
              name: s3-credentials
              key: access-key
        - name: OS_SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: s3-credentials
              key: secret-key
        - name: SERVING_URL
          value: "http://subst-serving:8000/predict"
        - name: REQUESTS_PER_SEC
          value: "1"
```

Apply:
```bash
kubectl apply -f data/k8s/generator.yaml
```

Verify:
```bash
kubectl logs -f deployment/data-generator -n production-proj01
# Should print: [1] sour cream -> greek yogurt | 1.0 req/s
```

⚠️ This only works after serving team has their endpoint running.

---

### D3. Deploy batch pipeline CronJob (runs daily at 2am)

📍 **WHERE:** Your laptop terminal

Create file `data/k8s/batch-cronjob.yaml`:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: batch-pipeline
  namespace: production-proj01
spec:
  schedule: "0 2 * * *"
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
          - name: batch
            image: <REGISTRY>/forkwise-batch:latest
            command: ["python", "batch_pipeline.py"]
            env:
            - name: OS_ENDPOINT
              value: "https://chi.tacc.chameleoncloud.org:7480"
            - name: OS_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: s3-credentials
                  key: access-key
            - name: OS_SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: s3-credentials
                  key: secret-key
            - name: MIN_NEW_SAMPLES
              value: "50"
```

Apply:
```bash
kubectl apply -f data/k8s/batch-cronjob.yaml
```

Test manually (don't wait until 2am):
```bash
kubectl create job batch-test --from=cronjob/batch-pipeline -n production-proj01
kubectl logs job/batch-test -n production-proj01 -f
```

---

### D4. Deploy drift monitor CronJob (runs every 6h)

📍 **WHERE:** Your laptop terminal

Create file `data/k8s/drift-cronjob.yaml`:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: drift-monitor
  namespace: production-proj01
spec:
  schedule: "0 */6 * * *"
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
          - name: drift
            image: <REGISTRY>/forkwise-batch:latest
            command: ["python", "drift_monitor.py"]
            env:
            - name: OS_ENDPOINT
              value: "https://chi.tacc.chameleoncloud.org:7480"
            - name: OS_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: s3-credentials
                  key: access-key
            - name: OS_SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: s3-credentials
                  key: secret-key
            - name: OOV_THRESHOLD
              value: "0.15"
            - name: MIN_REQUESTS_EXPECTED
              value: "10"
```

Apply:
```bash
kubectl apply -f data/k8s/drift-cronjob.yaml
```

Test manually:
```bash
kubectl create job drift-test --from=cronjob/drift-monitor -n production-proj01
kubectl logs job/drift-test -n production-proj01 -f
```

---

## PHASE E: Integration with teammates

---

### E1. What to tell Training team

Send this message:

> **Data is in `data-proj01` bucket on CHI@TACC.**
>
> Your changes needed:
>
> `watch_trigger.py` line 10:
> ```python
> result = s3.list_objects_v2(Bucket='data-proj01', Prefix='data/triggers/')
> ```
>
> `train.py` model upload — change bucket:
> ```python
> s3.put_object(Bucket='data-proj01', Key=f'models/{key}', Body=f)
> ```
>
> Training data is at: `data-proj01/data/raw/recipe1msubs/train.json`
> Recipe context is at: `data-proj01/data/raw/recipe1m/context_map.json`
> Retrain triggers will appear at: `data-proj01/data/triggers/retrain_*.json`

---

### E2. What to tell Serving team

Send this message:

> **Two changes needed in serve_pytorch.py / serve_onnx.py:**
>
> 1. Change default bucket:
> ```python
> REQUEST_LOG_BUCKET = os.getenv("REQUEST_LOG_BUCKET", "data-proj01")
> ```
>
> 2. Change request log key prefix:
> ```python
> key = f"logs/requests/request_{int(time.time())}_{request_id}.json"
> ```
>
> Also: Mealie frontend needs to call my feedback endpoint when user
> clicks accept/reject:
> ```
> POST http://subst-feedback:8001/feedback
> Body: {"request_id":"...", "recipe_id":"...", "missing_ingredient":"...",
>        "suggested_substitution":"...", "user_accepted": true/false}
> ```

---

### E3. What to tell DevOps

Send this message:

> **I need:**
> 1. Container registry URL to push my images
> 2. K8s namespace name (I'm assuming `production-proj01`)
> 3. kubectl access configured
> 4. Confirm: can I create secrets in the namespace?
>
> **I will deploy:**
> - 1 Deployment + Service (feedback endpoint, port 8001)
> - 1 Deployment (data generator)
> - 2 CronJobs (batch pipeline daily, drift monitor every 6h)

---

## PHASE F: Verify everything works end-to-end

---

📍 **WHERE:** Your laptop terminal

### F1. Check all pods are running
```bash
kubectl get pods -n production-proj01
# Should see: subst-feedback Running, data-generator Running
kubectl get cronjobs -n production-proj01
# Should see: batch-pipeline, drift-monitor with schedules
```

### F2. Check data generator is producing traffic
```bash
kubectl logs deployment/data-generator -n production-proj01 --tail=5
```

### F3. Check serving is logging requests
📍 **WHERE:** Browser — Chameleon Horizon → Object Store → data-proj01
Look in `logs/requests/` — should see `request_*.json` files appearing.

### F4. Send test feedback and verify batch pipeline
```bash
# Send 3 test feedback entries
for i in 1 2 3; do
  kubectl exec deployment/subst-feedback -n production-proj01 -- \
    curl -s -X POST http://localhost:8001/feedback \
    -H 'Content-Type: application/json' \
    -d "{\"request_id\":\"test_$i\",\"recipe_id\":\"123\",\"missing_ingredient\":\"butter\",\"suggested_substitution\":\"margarine\",\"user_accepted\":true}"
  echo ""
done

# Trigger batch pipeline manually
kubectl create job batch-verify --from=cronjob/batch-pipeline -n production-proj01
kubectl logs job/batch-verify -n production-proj01 -f

# Should see QC2 checks and either:
# "Only X accepted (need 50). Skipping." (expected with only 3 test entries)
# or if you lower MIN_NEW_SAMPLES, a trigger gets written
```

### F5. Trigger drift monitor manually
```bash
kubectl create job drift-verify --from=cronjob/drift-monitor -n production-proj01
kubectl logs job/drift-verify -n production-proj01 -f

# Should see:
# [drift] Vocab: XXXX ingredients
# [drift] Requests (24h): XX
# [drift] OOV PASSED (X.X%)
# [drift] ALL CLEAR
```

### F6. Check quality reports in bucket
📍 **WHERE:** Browser — Chameleon Horizon → Object Store → data-proj01
Look in `data/quality_reports/` — should see:
```
ingest_<ts>.json    ← from step B1
batch_<ts>.json     ← from step F4
drift_<ts>.json     ← from step F5
```

---

## PHASE G: Demo video (your ~5 min section)

---

📍 **WHERE:** Screen recording on your laptop

Show these in order:

1. **Terminal:** `kubectl logs deployment/data-generator` — show traffic flowing
2. **Horizon:** Open `logs/requests/` — show files appearing, open one, show
   it has ingredients but NO user identity → **"privacy safeguard"**
3. **Terminal:** Trigger batch pipeline, show QC2 output:
   ```
   [QC2] Valid: X, Schema: 0, Dedup: 0, Leakage: 0
   [QC2] PASSED
   ```
4. **Horizon:** Show `data/triggers/retrain_*.json` appeared → explain
   "training's watch_trigger.py picks this up automatically"
5. **Terminal:** Trigger drift monitor, show QC3 output:
   ```
   [drift] OOV PASSED (2.3%)
   [drift] Confidence PASSED (avg=0.847)
   [drift] ALL CLEAR
   ```
6. **Horizon:** Show `data/quality_reports/` with all 3 report types →
   "I evaluate data quality at all three required checkpoints"

---

## Quick reference: what runs where

| Action | Where | When |
|---|---|---|
| Generate EC2 credentials | Chameleon Jupyter notebook | Once |
| Run `ingest.py` | Your laptop terminal | Once |
| Verify bucket contents | Browser (Chameleon Horizon) | After ingest |
| `docker build` + `docker push` | Your laptop terminal | Once (or when code changes) |
| `kubectl apply` manifests | Your laptop terminal | Once |
| `kubectl create job --from=cronjob` | Your laptop terminal | To test CronJobs |
| `kubectl logs` | Your laptop terminal | To verify |
| batch_pipeline.py | K8s CronJob (automatic) | Daily 2am |
| drift_monitor.py | K8s CronJob (automatic) | Every 6h |
| feedback_endpoint.py | K8s Deployment (automatic) | Always running |
| data_generator.py | K8s Deployment (automatic) | Always running |
| Demo video | Screen recording | Once, at end |

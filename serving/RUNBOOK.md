# RUNBOOK — Serving Operations

Things that might happen in production and how to respond.

---

## 1. Serving pod is crash-looping

**Symptom:** `kubectl get pods -n production-proj01` shows `CrashLoopBackOff`

**First check:**
```bash
kubectl logs -n production-proj01 deploy/subst-serving --tail=100
```

**Most common causes:**

| Log message | Cause | Fix |
|-------------|-------|-----|
| `Could not download {bucket}/{key}` | Object storage unreachable | Check `os-credentials` secret; check MinIO/Swift is up. Pod will run with stub anyway — not actually crashing. |
| `KeyError: 'model_state_dict'` | Checkpoint dict wrong format | Ping Training. Roll back: `kubectl rollout undo deploy/subst-serving -n production-proj01` |
| `RuntimeError: Error(s) in loading state_dict` | Architecture mismatch between training and `model_stub.py` | Ping Training. Roll back. |
| `ImportError: No module named 'model_stub'` | Dockerfile didn't copy `model_stub.py` correctly | Rebuild image; check `COPY` statements |
| `OOMKilled` | Hit memory limit | Increase `resources.limits.memory` in K8S deployment |

---

## 2. `/predict` returns weird/random suggestions

**Symptom:** Greek yogurt for salt, flour for chicken, etc.

**Check:**
```bash
curl http://<serving_ip>:8000/health
# Look at: model_loaded
```

- If `model_loaded: false` → serving is running on stub weights. The model
  download failed. Check:
  ```bash
  mc stat models-proj01/production/subst_model_current.pth
  kubectl logs deploy/subst-serving -n production-proj01 | grep reload_model
  ```

- If `model_loaded: true` but suggestions are bad → the model itself has
  quality issues. Training's quality gate should have caught this. Check
  MLflow for the registered model's MRR@3. Roll back if necessary.

---

## 3. Latency spike (p95 > 500ms)

**Symptom:** Grafana alert fires, `check_rollback.py` triggers rollback

**This is expected automated behavior.** The rollback will restore the
previous model. Watch:

```bash
kubectl rollout history deploy/subst-serving -n production-proj01
kubectl logs deploy/subst-serving -n production-proj01 --tail=50
```

**If rollback didn't work:**

```bash
# Manual rollback
kubectl rollout undo deploy/subst-serving -n production-proj01

# If that also fails, scale down and up
kubectl scale deploy/subst-serving --replicas=0 -n production-proj01
kubectl scale deploy/subst-serving --replicas=1 -n production-proj01
```

**Investigating why it happened:**

```bash
# Latency by quantile
curl -s http://<prometheus>:9090/api/v1/query \
  --data-urlencode 'query=histogram_quantile(0.95, sum by (le) (rate(subst_request_latency_seconds_bucket{namespace="production-proj01"}[10m])))'

# CPU / memory
kubectl top pods -n production-proj01
```

Common triggers: HPA didn't scale fast enough, model file got much bigger,
vocabulary exploded, cold-start after a deploy.

---

## 4. Error rate spike (>5%)

Same story as latency — `check_rollback.py` will auto-rollback.

**To find the cause:**
```bash
# What kind of errors are happening?
kubectl logs deploy/subst-serving -n production-proj01 | grep ERROR

# Are the requests malformed?
curl http://<serving_ip>:8000/predict -X POST \
  -H "Content-Type: application/json" \
  -d @sample_data/input_sample.json

# If that works, the bad requests are from Mealie. Check Mealie's logs.
kubectl logs deploy/mealie -n production-proj01 --tail=100
```

---

## 5. Canary won't promote

**Symptom:** Canary has been up >30 min but `check_promote.py` never promotes.

**Check the CronJob logs:**
```bash
kubectl get cronjob check-promote -n canary-proj01
kubectl get jobs -n canary-proj01 | grep check-promote | tail -3
kubectl logs -n canary-proj01 job/<most-recent-job>
```

Most likely causes from the logs:
- `canary_p95 too high vs prod` → canary is actually slower; investigate new model perf
- `canary_err >= 0.02` → canary has errors; check canary pod logs
- `canary_age < 30min` → not enough time; wait
- `Missing metrics — canary hasn't served enough traffic yet` → canary isn't
  getting any traffic. Check ingress annotations are set correctly.

---

## 6. Disk filling up from request logs

**Symptom:** MinIO fills up; writes start failing

This shouldn't happen in 2 weeks but if it does:

```bash
# Check bucket size
mc du logs-proj01/

# Delete logs older than 7 days
mc find logs-proj01/requests/ --older-than 7d --exec "mc rm {}"
```

Long-term: add a lifecycle policy to `logs-proj01` to expire objects >30 days.

---

## 7. Prometheus isn't scraping

**Symptom:** Grafana dashboard shows no data

```bash
# Is the /metrics endpoint reachable?
kubectl exec -n monitoring-proj01 deploy/prometheus -- \
  wget -O- http://subst-serving.production-proj01:8000/metrics

# Does Prometheus know about the target?
kubectl port-forward -n monitoring-proj01 svc/prometheus 9090:9090
# Open http://localhost:9090/targets
# Look for subst-serving pods — should be UP
```

If targets are missing, the pod annotations are wrong. Check:
```bash
kubectl get pod -n production-proj01 -l app=subst-serving \
  -o jsonpath='{.items[0].metadata.annotations}'
# Must include prometheus.io/scrape=true, port=8000, path=/metrics
```

---

## 8. Full system rebuild (nuclear option)

If something is very wrong and you want to reset serving completely:

```bash
# 1. Delete deployments in all namespaces (serving only — don't touch Mealie)
for ns in staging-proj01 canary-proj01 production-proj01; do
  kubectl delete deploy/subst-serving -n $ns
  kubectl delete svc/subst-serving -n $ns
done

# 2. Delete CronJobs
kubectl delete cronjob/check-rollback -n production-proj01
kubectl delete cronjob/check-promote -n canary-proj01

# 3. Rebuild image from scratch
cd serving
docker build -t subst-serving-onnx:latest -f docker/Dockerfile.fastapi_onnx .

# 4. Re-apply manifests (DevOps owns these)
kubectl apply -f ../infra/k8s/production/
kubectl apply -f ../infra/k8s/canary/
kubectl apply -f ../infra/k8s/staging/
kubectl apply -f ../infra/k8s/cronjobs/

# 5. Verify
kubectl get pods -A | grep subst-serving
```

---

## 9. Emergency contacts

- Training team member: <fill in>
- Data team member: <fill in>
- DevOps team member: <fill in>
- Course staff (only in true emergency): ffund@nyu.edu

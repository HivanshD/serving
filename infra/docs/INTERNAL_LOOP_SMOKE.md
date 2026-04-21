# Internal Loop Smoke Test

This procedure verifies the internal `data -> training -> serving` loop on
Chameleon without involving Mealie.

## Preconditions

1. `substitution-serving` is deployed in `forkwise-serving`
2. `forkwise-data` manifests are applied in `forkwise-data`
3. `forkwise-ingest` completed successfully
4. `training-trigger` points at a training image built from the current repo
5. `s3-credentials` exists in both `forkwise-data` and `forkwise-serving`
6. `check-rollback` exists in `forkwise-serving`

## 1. Confirm serving is live

From your laptop:

```bash
kubectl get cronjob check-rollback -n forkwise-serving
ssh -N -L 8000:127.0.0.1:30080 cc@<FLOATING_IP>
curl http://localhost:8000/health
```

Expect:

1. HTTP 200
2. `model_loaded` is either `true` or `false`, but the service is healthy

## 2. Generate one request log

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d @serving/sample_data/input_sample.json
```

Expect:

1. HTTP 200
2. a substitutions response with `model_version` and `serving_version`

## 3. Generate one feedback log

In another terminal:

```bash
kubectl port-forward svc/subst-feedback -n forkwise-data 8001:8001
```

Then:

```bash
curl -X POST http://localhost:8001/feedback \
  -H "Content-Type: application/json" \
  -d '{"request_id":"smoke-1","recipe_id":"123","missing_ingredient":"sour cream","suggested_substitution":"greek yogurt","user_accepted":true}'
```

Expect:

1. `{"status":"logged",...}`

## 4. Lower the retraining threshold for the smoke run

```bash
kubectl patch configmap forkwise-data-config -n forkwise-data \
  --type merge \
  -p '{"data":{"MIN_NEW_SAMPLES":"1"}}'
```

## 5. Run the batch pipeline once

```bash
kubectl create job batch-smoke --from=cronjob/batch-pipeline -n forkwise-data
kubectl logs -n forkwise-data job/batch-smoke -f
```

Expect log lines showing:

1. QC2 report written
2. `Dataset -> data/processed/train_v...json`
3. `Trigger -> data/triggers/retrain_...json`

## 6. Run training once from the trigger

```bash
kubectl create job training-smoke --from=cronjob/training-trigger -n forkwise-data
kubectl logs -n forkwise-data job/training-smoke -f
```

Expect log lines showing:

1. trigger downloaded
2. training completed
3. evaluation ran
4. ONNX exported
5. artifacts uploaded under `models/production/`

## 7. Verify serving picks up the new model

Wait about 60 seconds for serving's refresh interval, then call:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d @serving/sample_data/input_sample.json
```

Expect:

1. service remains healthy
2. `model_version` reflects the newly published training run metadata

## 8. Restore the normal feedback threshold

```bash
kubectl patch configmap forkwise-data-config -n forkwise-data \
  --type merge \
  -p '{"data":{"MIN_NEW_SAMPLES":"50"}}'
```

If all eight steps succeed, the internal loop is operational:

`request/feedback logs -> batch pipeline -> retraining trigger -> train -> evaluate -> publish model artifact -> serving loads updated model`

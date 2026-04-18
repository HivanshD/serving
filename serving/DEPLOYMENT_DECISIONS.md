# DEPLOYMENT_DECISIONS.md

Why the serving component looks the way it does. This is the document
to read if you want to understand "why did Hivansh pick X over Y?"
It also covers what to change if the initial assumptions turn out wrong.

---

## Decision 1: FastAPI + ONNX quantized CPU with 4 uvicorn workers + HPA 1-4

### What this actually means

Each serving pod runs 4 Uvicorn worker processes. Each worker loads its
own ONNX Runtime session with the quantized model. K8S HPA scales the
pod count between 1 and 4 based on CPU utilization (70% target). So at
peak, the system has:

    4 workers/pod * 4 pods = 16 concurrent request slots
    with 0.6-1.2 ms per request = ~13,000 req/s theoretical ceiling

### Alternatives considered

**A. Triton ONNX GPU with dynamic batching**
 - Stable p95 under bursts (batching absorbs spikes)
 - Benchmarks showed 0.5ms at concurrency 1, sub-ms with batching
 - Needs GPU lease — hard to get for the full 2-week operation phase
 - More complex K8S setup (shm_size, nvidia runtime, resource types)
 - Kept in repo as the "best throughput / best bursty load" row in the
   serving options table

**B. FastAPI + PyTorch CPU (single worker)**
 - Simplest option, p50 = 2.1 ms
 - Python GIL limits concurrency
 - Would struggle under any realistic bursty load
 - Kept as the "baseline" row in the serving options table

**C. FastAPI + ONNX (unquantized) CPU**
 - p95 1.5 ms vs 1.2 ms for quantized — small difference
 - Chose quantized for the extra headroom and ~2x smaller model file
   (faster pod startup time in K8S)

### Why this is the right production default

1. **Model is tiny.** Embedding table ~10K vocab x 128 dims x 4 bytes
   = 5 MB. Inference is effectively one matrix-vector multiply. GPU
   overhead is wasted on a workload this small.

2. **Sub-millisecond is already fast enough.** The rollback threshold
   is p95 > 500 ms. Our baseline is 450x under the threshold, so we
   have enormous headroom for bursts.

3. **CPU scaling is reliable.** HPA on CPU utilization is well-tested
   in K8S. GPU-aware HPA requires additional metrics pipeline and
   the nodes to advertise GPU capacity correctly.

4. **No GPU lease dependency.** Chameleon GPU leases often fail to
   renew for extended periods. A 2-week production phase on GPU is a
   real operational risk.

5. **Team can reason about it.** All four team members understand
   "FastAPI, workers, HPA". Only one team member (me) would be able to
   debug Triton issues at 3am during the demo week.

### When to switch

Run `python scripts/load_test_burst.py --rate <expected_rps> --duration-sec 300`
before the demo. If that shows:
 - p95 > 400 ms (getting close to rollback threshold), OR
 - error rate > 2%, OR
 - HPA is hitting max replicas (4) at moderate traffic

Then the FastAPI option is under-provisioned. Options, in order of
increasing effort:

1. Increase `UVICORN_WORKERS` in the Dockerfile from 4 to 8. Free.
2. Increase HPA `maxReplicas` from 4 to 8. Free (assumes cluster has
   capacity).
3. Lower HPA CPU target from 70% to 50% (scales up sooner). Free.
4. Swap image in K8S Deployment from `subst-serving-onnx` to
   `subst-triton`. Requires GPU node in the cluster. Same /predict
   contract, so no code changes elsewhere.

---

## Decision 2: Inference via ONNX Runtime directly, not via embedding extraction

Earlier iteration of `serve_onnx.py` extracted the embedding table from
the ONNX graph's initializers and ran cosine similarity in numpy. This
was clever but fragile — it assumed the model only did embedding +
cosine sim, which breaks if training team adds any learned transformation.

Current version runs the ONNX graph directly via `_session.run(...)`.
Benefits:
 - Uses ORT's SIMD-optimized quantized matrix ops
 - Won't silently break if training team extends the model architecture
 - Less code to maintain
 - Still falls back to numpy stub mode when no ONNX file is loaded,
   so the pod never crash-loops during incidents

---

## Decision 3: Request logging in a background thread

Every `/predict` call uploads a request log to `logs-proj01/requests/`.
This data feeds Data team's drift monitor and batch pipeline.

The upload runs in a daemon thread, not in the request path. Reasoning:
 - Object storage latency (typically 10-50ms) would dominate the response
   time if inline
 - We never want request logging to affect the user-facing SLO
 - If MinIO/Swift is down, inference keeps working; only logging stops
 - Worst case: we lose a few log entries during an incident. That's OK
   because the feedback loop data (Data team's feedback endpoint) is
   separate and more important for retraining

---

## Decision 4: Stub fallback mode when the real model isn't available

If `reload_model.py` can't download the production model, or if the
ONNX session fails to load, the server starts with stub/random weights
anyway. Reasoning:
 - Crash-looping pods are worse than pods returning low-quality results
 - During an incident, we want /health to return 200 so dependent
   services don't cascade-fail
 - `MODEL_LOADED` Prometheus gauge goes to 0, which fires an alert
   so the on-call person knows to investigate
 - Mealie's substitution route has its own try/except fallback that
   returns an empty list, so stub responses are actually hidden from
   users during an incident

---

## Decision 5: Checkpoint format is a dict with explicit keys

Training team's checkpoint must contain:
```
{
  "model_state_dict": ...,
  "vocab": {<ingredient>: <int_id>},
  "config": {"embed_dim": 128, ...}
}
```

Alternative was to store only `model_state_dict` and hard-code vocab
size + embed dim in serving's env vars. Rejected because:
 - Retraining can legitimately change vocab size (new ingredients from
   user feedback) without a code change on the serving side
 - Hard-coding embed dim means any training config change breaks
   serving silently (bad state_dict load)
 - Keeping vocab alongside weights means a single atomic artifact for
   a given model version

---

## Decision 6: check_rollback thresholds

| Condition | Threshold | Window | Reasoning |
|-----------|-----------|--------|-----------|
| p95 latency | 500 ms | 10 min | UI becomes perceptibly laggy above ~500ms. 10 min window avoids reacting to transient spikes from HPA scaling. |
| Error rate | 5% | 5 min | 1 in 20 failures is unacceptable. 5 min window gives HPA time to scale up before we assume the problem is the model itself. |

Either condition triggers rollback. We do NOT require both, because
different failure modes manifest differently (bad model might not raise
errors but has high latency; OOM will raise errors but latency is fine
until it kills the pod).

---

## Decision 7: check_promote requires THREE conditions

Canary is only promoted if ALL of:
 1. canary p95 <= 1.1 * production p95 (relative, not absolute)
 2. canary error rate < 2% (stricter than production's 5%)
 3. canary has been running >= 30 minutes

Reasoning:
 - Relative p95 catches "new model is slower" regressions
 - Stricter error rate catches "new model throws on edge cases" regressions
 - 30-minute age catches "new model uses more memory, OOMs after
   running a while" regressions

The 30-minute window is important because some regressions only surface
after sustained traffic (gradual memory leaks, slow cache growth).

---

## Decision 8: Prometheus metric names

All metrics named `subst_*` so they're easy to filter in PromQL and
don't collide with other teams' metrics. Labels:
 - `status` on REQUEST_COUNT: "success" or "error"
 - (implicit) `namespace` from the K8S pod label → filters by environment

The `subst_inflight_requests` gauge is particularly useful for HPA:
a custom-metrics-based HPA could scale on this instead of CPU
(left as a future improvement; CPU-based HPA is sufficient for April 20).

---

## Decision 9: The image used by CronJobs is the serving image itself

The `check_rollback` and `check_promote` CronJobs run inside the
`subst-serving-onnx` image. Benefits:
 - One less image to build, version, and push to the registry
 - Scripts in `/app/scripts/` are version-matched to the serving code
   they monitor — no "rollback thresholds don't match the latency
   buckets" drift problem
 - Simpler RBAC (one ServiceAccount pattern for all serving-owned pods)

Downside: the CronJob pulls a larger image than it needs. Marginal cost,
accepted.

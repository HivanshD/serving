"""
check_rollback.py

K8S CronJob (runs every 5 min in production-proj01).
Queries Prometheus for production service health metrics.
If thresholds are breached, calls the DevOps automation webhook to roll back
to the previous model version.

Thresholds and justifications:
  p95 latency > 500ms for 10 min:
    The substitution feature blocks the Mealie recipe UI until a response
    arrives. 500ms is the boundary above which users perceive a UI as
    laggy. The 10-min window avoids reacting to short bursts.

  error rate > 5% for 5 min:
    1 in 20 requests failing is unacceptable for a user-facing feature.
    5 min window gives the HPA time to scale up before we assume the
    problem is model-level rather than capacity-level.

If EITHER condition is breached, we call /rollback.

Environment variables:
  PROMETHEUS_URL   (default http://prometheus.monitoring-proj01:9090)
  DEVOPS_HOOK      (default http://automation.monitoring-proj01:8080)
  NAMESPACE        (default production-proj01)
  LATENCY_P95_THRESHOLD_S  (default 0.5)
  ERROR_RATE_THRESHOLD     (default 0.05)
"""

import os
import sys
import requests


PROM = os.getenv("PROMETHEUS_URL", "http://prometheus.monitoring-proj01:9090")
DEVOPS = os.getenv("DEVOPS_HOOK", "http://automation.monitoring-proj01:8080")
NAMESPACE = os.getenv("NAMESPACE", "production-proj01")
P95_LIMIT = float(os.getenv("LATENCY_P95_THRESHOLD_S", "0.5"))
ERR_LIMIT = float(os.getenv("ERROR_RATE_THRESHOLD", "0.05"))


def query_prom(promql: str):
    """Return the single scalar value of a PromQL query, or None."""
    try:
        r = requests.get(f"{PROM}/api/v1/query",
                         params={"query": promql}, timeout=10)
        r.raise_for_status()
        res = r.json()["data"]["result"]
        if not res:
            return None
        return float(res[0]["value"][1])
    except Exception as e:
        print(f"[check_rollback] Prometheus query failed: {e}")
        return None


def main():
    # Only look at metrics from the target namespace — this is important
    # when staging/canary/production are all scraped by the same Prometheus
    latency_q = (
        'histogram_quantile(0.95, '
        'rate(subst_request_latency_seconds_bucket'
        '{namespace="' + NAMESPACE + '"}[10m]))'
    )
    error_q = (
        'rate(subst_requests_total'
        '{namespace="' + NAMESPACE + '",status="error"}[5m]) / '
        'rate(subst_requests_total'
        '{namespace="' + NAMESPACE + '"}[5m])'
    )

    p95 = query_prom(latency_q)
    err = query_prom(error_q) or 0.0

    print(f"[check_rollback] namespace={NAMESPACE} "
          f"p95={p95}s err={err:.1%}")

    if p95 is None:
        # Not enough data yet — don't trigger rollback on missing metrics
        print("[check_rollback] No latency data yet. Skipping.")
        return

    breaches = []
    if p95 > P95_LIMIT:
        breaches.append(f"p95={p95:.3f}s > {P95_LIMIT}s")
    if err > ERR_LIMIT:
        breaches.append(f"err={err:.1%} > {ERR_LIMIT:.0%}")

    if not breaches:
        print("[check_rollback] Health check PASSED")
        return

    reason = " AND ".join(breaches)
    print(f"[check_rollback] ROLLBACK TRIGGERED: {reason}")

    try:
        r = requests.post(f"{DEVOPS}/rollback",
                          json={"namespace": NAMESPACE, "reason": reason},
                          timeout=10)
        print(f"[check_rollback] Automation webhook response: "
              f"{r.status_code} {r.text}")
    except Exception as e:
        print(f"[check_rollback] Could not call automation webhook: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

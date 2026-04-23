"""
check_promote.py

K8S CronJob (runs every 5 min in canary-proj01).
Queries Prometheus for canary vs production metrics.
If the canary has been running cleanly for long enough, calls the DevOps
automation webhook to promote the canary image to production.

Promotion conditions (ALL must hold):
  1. canary p95 latency <= 1.1 * production p95 latency
     (canary must not degrade UX)
  2. canary error rate < 2%
     (stricter than production's 5% — canary should be pristine)
  3. canary has been running >= 30 minutes
     (catches regressions that only appear under sustained traffic)

Environment variables:
  PROMETHEUS_URL
  DEVOPS_HOOK
  CANARY_NS        (default canary-proj01)
  PROD_NS          (default production-proj01)
  LATENCY_RATIO_LIMIT (default 1.1)
  CANARY_ERROR_LIMIT  (default 0.02)
  CANARY_MIN_AGE_MIN  (default 30)
  DEPLOYMENT_NAME   (default subst-serving)
"""

import os
import sys
import subprocess
from datetime import datetime, timezone

import requests


PROM = os.getenv("PROMETHEUS_URL", "http://prometheus.monitoring-proj01:9090")
DEVOPS = os.getenv("DEVOPS_HOOK", "http://automation.monitoring-proj01:8080")
CANARY_NS = os.getenv("CANARY_NS", "canary-proj01")
PROD_NS = os.getenv("PROD_NS", "production-proj01")
RATIO_LIMIT = float(os.getenv("LATENCY_RATIO_LIMIT", "1.1"))
CANARY_ERR_LIMIT = float(os.getenv("CANARY_ERROR_LIMIT", "0.02"))
MIN_AGE_MIN = float(os.getenv("CANARY_MIN_AGE_MIN", "30"))
DEPLOYMENT = os.getenv("DEPLOYMENT_NAME", "subst-serving")


def query_prom_namespace(namespace: str, promql_template: str):
    """
    Run a PromQL query with namespace filter injected.
    The template must use {ns} as a placeholder for the namespace label.
    """
    q = promql_template.format(ns=namespace)
    try:
        r = requests.get(f"{PROM}/api/v1/query",
                         params={"query": q}, timeout=10)
        r.raise_for_status()
        res = r.json()["data"]["result"]
        if not res:
            return None
        return float(res[0]["value"][1])
    except Exception as e:
        print(f"[check_promote] PromQL query failed: {e}")
        return None


def get_deployment_age_minutes(namespace: str, name: str):
    """Use kubectl to read deployment creation timestamp."""
    try:
        out = subprocess.run(
            ["kubectl", "get", "deployment", name,
             "-n", namespace,
             "-o", "jsonpath={.metadata.creationTimestamp}"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        ts = out.stdout.strip()
        if not ts:
            return 0.0
        created = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - created
        return age.total_seconds() / 60.0
    except Exception as e:
        print(f"[check_promote] Could not read deployment age: {e}")
        return 0.0


def main():
    latency_template = (
        'histogram_quantile(0.95, '
        'sum by (le) (rate(subst_request_latency_seconds_bucket'
        '{{namespace="{ns}"}}[10m])))'
    )
    error_template = (
        'sum(rate(subst_requests_total'
        '{{namespace="{ns}",status="error"}}[5m])) / '
        'sum(rate(subst_requests_total{{namespace="{ns}"}}[5m]))'
    )

    canary_p95 = query_prom_namespace(CANARY_NS, latency_template)
    prod_p95 = query_prom_namespace(PROD_NS, latency_template)
    canary_err = query_prom_namespace(CANARY_NS, error_template) or 0.0
    canary_age = get_deployment_age_minutes(CANARY_NS, DEPLOYMENT)

    print(f"[check_promote] canary_p95={canary_p95} "
          f"prod_p95={prod_p95} "
          f"canary_err={canary_err:.1%} "
          f"canary_age={canary_age:.1f}min")

    # Missing metrics = not ready
    if canary_p95 is None or prod_p95 is None:
        print("[check_promote] Missing metrics — canary hasn't served "
              "enough traffic yet. Skipping.")
        return

    checks = []

    if canary_p95 > prod_p95 * RATIO_LIMIT:
        checks.append(f"canary_p95={canary_p95:.3f}s too high vs "
                       f"prod={prod_p95:.3f}s (ratio limit {RATIO_LIMIT})")

    if canary_err >= CANARY_ERR_LIMIT:
        checks.append(f"canary_err={canary_err:.1%} >= "
                       f"{CANARY_ERR_LIMIT:.0%}")

    if canary_age < MIN_AGE_MIN:
        checks.append(f"canary_age={canary_age:.0f}min < {MIN_AGE_MIN}min")

    if checks:
        print("[check_promote] NOT READY:")
        for c in checks:
            print(f"  - {c}")
        return

    print(f"[check_promote] Canary READY for promotion. Calling /promote...")
    try:
        r = requests.post(
            f"{DEVOPS}/promote",
            json={"from": CANARY_NS, "to": PROD_NS,
                  "deployment": DEPLOYMENT},
            timeout=10,
        )
        print(f"[check_promote] Automation response: "
              f"{r.status_code} {r.text}")
    except Exception as e:
        print(f"[check_promote] Could not call /promote: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

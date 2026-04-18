"""
smoke_test.py

Quick end-to-end sanity check. Run this after deploying serving to verify:
  1. /health returns 200
  2. /metrics returns Prometheus format
  3. /predict accepts the input_sample.json and returns a valid response

Exit codes:
  0 = all checks pass
  1 = at least one check failed

Designed to be cheap — takes <5 seconds. Suitable for:
  - Running from CI after a deployment
  - Running locally after docker compose up
  - Running inside the cluster as a post-deploy verification

Example:
  python smoke_test.py --url http://subst-serving.production-proj01:8000
"""

import argparse
import json
import sys

import requests


def check(label: str, condition: bool, detail: str = ""):
    marker = "PASS" if condition else "FAIL"
    print(f"  [{marker}] {label}" + (f" — {detail}" if detail else ""))
    return condition


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True,
                        help="Base URL (e.g. http://localhost:8000)")
    parser.add_argument("--input",
                        default="../sample_data/input_sample.json",
                        help="Path to input_sample.json")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    print(f"Smoke testing {args.url}...")
    all_ok = True

    # 1. /health
    try:
        r = requests.get(f"{args.url}/health", timeout=args.timeout)
        ok = r.status_code == 200
        all_ok &= check("GET /health returns 200", ok,
                         f"got {r.status_code}")
        if ok:
            body = r.json()
            all_ok &= check("  health body has 'status' field",
                             "status" in body, f"body={body}")
    except Exception as e:
        all_ok &= check("GET /health returns 200", False, str(e))

    # 2. /metrics
    try:
        r = requests.get(f"{args.url}/metrics", timeout=args.timeout)
        ok = r.status_code == 200
        all_ok &= check("GET /metrics returns 200", ok,
                         f"got {r.status_code}")
        if ok:
            has_latency = "subst_request_latency_seconds" in r.text
            has_count = "subst_requests_total" in r.text
            all_ok &= check("  metrics include subst_request_latency_seconds",
                             has_latency)
            all_ok &= check("  metrics include subst_requests_total",
                             has_count)
    except Exception as e:
        all_ok &= check("GET /metrics returns 200", False, str(e))

    # 3. /predict
    try:
        with open(args.input) as f:
            payload = json.load(f)
        r = requests.post(f"{args.url}/predict",
                          json=payload, timeout=args.timeout)
        ok = r.status_code == 200
        all_ok &= check("POST /predict returns 200", ok,
                         f"got {r.status_code}")
        if ok:
            body = r.json()
            all_ok &= check("  response has 'request_id'",
                             "request_id" in body)
            all_ok &= check("  response has 'substitutions' array",
                             isinstance(body.get("substitutions"), list))
            subs = body.get("substitutions", [])
            all_ok &= check(f"  returned {len(subs)} substitutions (>=1)",
                             len(subs) >= 1)
            if subs:
                first = subs[0]
                all_ok &= check("  first substitution has 'ingredient'",
                                 "ingredient" in first)
                all_ok &= check("  first substitution has 'embedding_score'",
                                 "embedding_score" in first)
                all_ok &= check("  first substitution has 'rank' == 1",
                                 first.get("rank") == 1)
                print(f"         -> {first['ingredient']} "
                      f"(score={first.get('embedding_score')})")
    except Exception as e:
        all_ok &= check("POST /predict returns 200", False, str(e))

    print()
    if all_ok:
        print("ALL CHECKS PASSED")
        sys.exit(0)
    else:
        print("ONE OR MORE CHECKS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()

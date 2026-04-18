"""
load_test_burst.py

Bursty load test with Poisson-distributed inter-arrival times.
This simulates the kind of traffic that Data team's data_generator.py
will send during the ongoing-operation phase.

Unlike benchmark.py (which sends N requests at fixed concurrency to
measure ideal performance), this script tests whether the service
stays healthy under realistic traffic patterns:

  - Inter-arrival times drawn from Exponential(lambda=target_rate)
    → arrivals form a Poisson process, which is bursty
  - Duration controlled by --duration-sec
  - Prints rolling p95 so you can watch it stay stable (or spike)

RUN THIS BEFORE RECORDING THE DEMO. If p95 stays under 500ms at your
target traffic rate, you're good. If it spikes, either add more workers,
scale HPA more aggressively, or swap to Triton.

Example:
  # Simulate 10 req/s for 5 minutes
  python load_test_burst.py \\
    --url http://localhost:8000/predict \\
    --input ../sample_data/input_sample.json \\
    --rate 10 \\
    --duration-sec 300
"""

import argparse
import json
import random
import threading
import time
from collections import deque
from statistics import median

import requests


class Stats:
    def __init__(self, window_size=500):
        self.latencies = deque(maxlen=window_size)
        self.errors = 0
        self.total = 0
        self.lock = threading.Lock()

    def record(self, latency_ms, is_error):
        with self.lock:
            self.latencies.append(latency_ms)
            self.total += 1
            if is_error:
                self.errors += 1

    def snapshot(self):
        with self.lock:
            if not self.latencies:
                return None
            sorted_lat = sorted(self.latencies)
            n = len(sorted_lat)
            return {
                "n": n,
                "total": self.total,
                "errors": self.errors,
                "p50_ms": sorted_lat[n // 2],
                "p95_ms": sorted_lat[int(n * 0.95)],
                "p99_ms": sorted_lat[int(n * 0.99)],
                "max_ms": sorted_lat[-1],
                "error_rate": self.errors / max(self.total, 1),
            }


def send_request(url: str, payload: dict, stats: Stats):
    t0 = time.perf_counter()
    try:
        r = requests.post(url, json=payload, timeout=10)
        latency_ms = (time.perf_counter() - t0) * 1000
        stats.record(latency_ms, r.status_code != 200)
    except Exception:
        latency_ms = (time.perf_counter() - t0) * 1000
        stats.record(latency_ms, True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--rate", type=float, required=True,
                        help="Target requests per second (Poisson lambda)")
    parser.add_argument("--duration-sec", type=float, default=60)
    parser.add_argument("--print-every-sec", type=float, default=5)
    args = parser.parse_args()

    with open(args.input) as f:
        payload = json.load(f)

    stats = Stats()
    threads = []

    end_time = time.time() + args.duration_sec
    last_print = time.time()

    print(f"\n{'='*70}")
    print(f"  Bursty load test")
    print(f"  URL: {args.url}")
    print(f"  Target rate: {args.rate} req/s (Poisson arrivals)")
    print(f"  Duration: {args.duration_sec}s")
    print(f"{'='*70}\n")

    print(f"{'elapsed':>8} {'sent':>6} {'errs':>5} {'err%':>6} "
          f"{'p50':>6} {'p95':>6} {'p99':>6} {'max':>7}")

    start = time.time()
    while time.time() < end_time:
        # Poisson: wait for Exponential(lambda=rate) between arrivals
        wait = random.expovariate(args.rate)
        time.sleep(wait)

        t = threading.Thread(
            target=send_request, args=(args.url, payload, stats), daemon=True)
        t.start()
        threads.append(t)

        # Periodic rolling stats
        now = time.time()
        if now - last_print >= args.print_every_sec:
            snap = stats.snapshot()
            if snap:
                print(f"{now-start:>7.1f}s {snap['total']:>6} "
                      f"{snap['errors']:>5} {snap['error_rate']*100:>5.1f}% "
                      f"{snap['p50_ms']:>5.1f} {snap['p95_ms']:>5.1f} "
                      f"{snap['p99_ms']:>5.1f} {snap['max_ms']:>6.1f}")
            last_print = now

    # Wait for in-flight to finish
    print("\n[load_test] Waiting for in-flight requests to complete...")
    for t in threads:
        t.join(timeout=30)

    final = stats.snapshot()
    print(f"\n{'='*70}")
    print("FINAL RESULTS:")
    print(f"{'='*70}")
    if final:
        print(f"  Total sent:  {final['total']}")
        print(f"  Errors:      {final['errors']} "
              f"({final['error_rate']*100:.2f}%)")
        print(f"  p50 latency: {final['p50_ms']:.1f} ms")
        print(f"  p95 latency: {final['p95_ms']:.1f} ms")
        print(f"  p99 latency: {final['p99_ms']:.1f} ms")
        print(f"  max latency: {final['max_ms']:.1f} ms")

        # Production readiness check
        print(f"\n{'='*70}")
        print("PRODUCTION READINESS:")
        print(f"{'='*70}")
        ok_p95 = final['p95_ms'] < 500
        ok_err = final['error_rate'] < 0.05
        print(f"  p95 < 500ms (rollback threshold): "
              f"{'PASS' if ok_p95 else 'FAIL'} "
              f"({final['p95_ms']:.1f}ms)")
        print(f"  error rate < 5% (rollback threshold): "
              f"{'PASS' if ok_err else 'FAIL'} "
              f"({final['error_rate']*100:.2f}%)")
        if ok_p95 and ok_err:
            print("\n  Service is production-ready at this traffic level.")
        else:
            print("\n  Service FAILED production readiness check.")
            print("  Options: (a) scale HPA more aggressively, "
                  "(b) increase UVICORN_WORKERS, "
                  "(c) swap to Triton ONNX GPU.")


if __name__ == "__main__":
    main()

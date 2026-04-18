"""
benchmark.py

Load-tests a FastAPI serving endpoint and prints a row suitable for the
serving options table in the initial implementation / system implementation
submission.

Usage:
  python benchmark.py \\
    --url http://localhost:8000/predict \\
    --input ../sample_data/input_sample.json \\
    --concurrency 1 4 8 16 \\
    --n 200 \\
    --option_name fastapi_onnx_quantized_cpu \\
    --model_version v20260420_001
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import median

import requests


def warmup(url: str, payload: dict, n: int = 10):
    print(f"[benchmark] Warming up with {n} requests...")
    for _ in range(n):
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception:
            pass


def check_health(url: str):
    health_url = url.replace("/predict", "/health")
    try:
        r = requests.get(health_url, timeout=10)
        if r.status_code == 200:
            print(f"[benchmark] Health OK: {r.json()}")
            return True
    except Exception as e:
        print(f"[benchmark] Health check failed: {e}")
    return False


def run_benchmark(url: str, payload: dict, n: int, concurrency: int):
    latencies = []
    errors = 0

    def _one_request():
        t0 = time.perf_counter()
        try:
            r = requests.post(url, json=payload, timeout=30)
            latency = (time.perf_counter() - t0) * 1000  # ms
            if r.status_code != 200:
                return latency, True
            return latency, False
        except Exception:
            return (time.perf_counter() - t0) * 1000, True

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_one_request) for _ in range(n)]
        for fut in as_completed(futures):
            latency, is_err = fut.result()
            latencies.append(latency)
            if is_err:
                errors += 1

    wall_time = time.perf_counter() - start

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]

    return {
        "concurrency": concurrency,
        "n": n,
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
        "throughput": n / wall_time,
        "error_rate": errors / n,
        "wall_time_sec": wall_time,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--input", required=True,
                        help="Path to input_sample.json")
    parser.add_argument("--concurrency", nargs="+", type=int,
                        default=[1, 4, 8, 16])
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--option_name", default="")
    parser.add_argument("--model_version", default="")
    args = parser.parse_args()

    with open(args.input) as f:
        payload = json.load(f)

    print(f"\n{'='*70}")
    print(f"  Benchmarking: {args.url}")
    print(f"  Option: {args.option_name or '(unnamed)'}")
    print(f"  n={args.n} per concurrency level, levels={args.concurrency}")
    print(f"{'='*70}\n")

    if not check_health(args.url):
        print("[benchmark] WARNING: health check failed, continuing anyway...")

    warmup(args.url, payload)

    results = []
    for c in args.concurrency:
        print(f"[benchmark] Running concurrency={c} ...")
        r = run_benchmark(args.url, payload, args.n, c)
        results.append(r)
        print(
            f"  p50={r['p50_ms']:.1f}ms  p95={r['p95_ms']:.1f}ms  "
            f"p99={r['p99_ms']:.1f}ms  tps={r['throughput']:.1f}  "
            f"err={r['error_rate']*100:.1f}%"
        )

    print(f"\n{'='*70}")
    print("RUBRIC TABLE ROW:")
    print(f"{'='*70}")
    r_single = next((r for r in results if r["concurrency"] == 1), results[0])
    r_conc = results[-1]
    print(
        f"Option:       {args.option_name}\n"
        f"Model ver:    {args.model_version}\n"
        f"p50/p95 (c=1):       {r_single['p50_ms']:.1f}ms / "
        f"{r_single['p95_ms']:.1f}ms\n"
        f"p50/p95 (c={r_conc['concurrency']}):      {r_conc['p50_ms']:.1f}ms / "
        f"{r_conc['p95_ms']:.1f}ms\n"
        f"Throughput (c={r_conc['concurrency']}):   "
        f"{r_conc['throughput']:.1f} req/s\n"
        f"Error rate:   {max(r['error_rate'] for r in results)*100:.1f}%"
    )


if __name__ == "__main__":
    main()

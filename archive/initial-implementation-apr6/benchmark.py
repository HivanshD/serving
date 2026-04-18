"""
Benchmark inference latency, throughput, and model size for the
ingredient-substitution model (PyTorch and ONNX).

Usage:
    python benchmark.py --mode pytorch --model subst_model.pth
    python benchmark.py --mode onnx    --model subst_model.onnx
    python benchmark.py --mode onnx    --model subst_model.onnx --graph-opt extended
    python benchmark.py --mode onnx    --model subst_model_quantized_dynamic.onnx
"""

import argparse
import os
import time
import numpy as np

# ── constants ────────────────────────────────────────────────────────────
MAX_INGREDIENTS = 20
NUM_TRIALS = 100
NUM_BATCHES = 50
BATCH_SIZE = 32


def random_inputs_np(batch_size: int = 1):
    ctx = np.random.randint(0, 100, (batch_size, MAX_INGREDIENTS)).astype(np.int64)
    miss = np.random.randint(1, 100, (batch_size, 1)).astype(np.int64)
    return ctx, miss


# ── PyTorch benchmark ────────────────────────────────────────────────────
def benchmark_pytorch(model_path: str, device_str: str = "cpu"):
    import torch
    from model_stub import SubstitutionModel  # noqa: F401 — needed for unpickling

    device = torch.device(device_str)
    model = torch.load(model_path, map_location=device, weights_only=False)
    model.to(device)
    model.eval()

    model_size = os.path.getsize(model_path)
    print(f"Model Size on Disk: {model_size / 1e6:.2f} MB")

    # --- single-sample latency ---
    ctx_np, miss_np = random_inputs_np(1)
    ctx_t = torch.tensor(ctx_np, device=device)
    miss_t = torch.tensor(miss_np, device=device)

    with torch.no_grad():
        model(ctx_t, miss_t)  # warm-up

    latencies = []
    with torch.no_grad():
        for _ in range(NUM_TRIALS):
            t0 = time.time()
            model(ctx_t, miss_t)
            latencies.append(time.time() - t0)

    latencies = np.array(latencies)
    print(f"Inference Latency (single, median):  {np.percentile(latencies, 50)*1000:.2f} ms")
    print(f"Inference Latency (single, p95):     {np.percentile(latencies, 95)*1000:.2f} ms")
    print(f"Inference Latency (single, p99):     {np.percentile(latencies, 99)*1000:.2f} ms")
    print(f"Inference Throughput (single):        {NUM_TRIALS / latencies.sum():.2f} FPS")

    # --- batch throughput ---
    ctx_np_b, miss_np_b = random_inputs_np(BATCH_SIZE)
    ctx_b = torch.tensor(ctx_np_b, device=device)
    miss_b = torch.tensor(miss_np_b, device=device)

    with torch.no_grad():
        model(ctx_b, miss_b)  # warm-up

    batch_times = []
    with torch.no_grad():
        for _ in range(NUM_BATCHES):
            t0 = time.time()
            model(ctx_b, miss_b)
            batch_times.append(time.time() - t0)

    batch_fps = (BATCH_SIZE * NUM_BATCHES) / sum(batch_times)
    print(f"Batch Throughput:                     {batch_fps:.2f} FPS")


# ── ONNX benchmark ──────────────────────────────────────────────────────
def benchmark_onnx(model_path: str, graph_opt: str = "none", ep: str = "cpu"):
    import onnxruntime as ort

    opt_map = {
        "none": ort.GraphOptimizationLevel.ORT_DISABLE_ALL,
        "basic": ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
        "extended": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
    }

    ep_map = {
        "cpu": ["CPUExecutionProvider"],
        "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
        "rocm": ["ROCMExecutionProvider", "CPUExecutionProvider"],
        "openvino": ["OpenVINOExecutionProvider", "CPUExecutionProvider"],
    }

    sess_opts = ort.SessionOptions()
    sess_opts.graph_optimization_level = opt_map.get(
        graph_opt, ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
    )

    providers = ep_map.get(ep, ["CPUExecutionProvider"])
    session = ort.InferenceSession(model_path, sess_options=sess_opts, providers=providers)
    print(f"Execution providers: {session.get_providers()}")

    model_size = os.path.getsize(model_path)
    print(f"Model Size on Disk: {model_size / 1e6:.2f} MB")

    input_names = [inp.name for inp in session.get_inputs()]

    def run(ctx, miss):
        return session.run(None, {input_names[0]: ctx, input_names[1]: miss})

    # --- single-sample latency ---
    ctx_np, miss_np = random_inputs_np(1)
    run(ctx_np, miss_np)  # warm-up

    latencies = []
    for _ in range(NUM_TRIALS):
        t0 = time.time()
        run(ctx_np, miss_np)
        latencies.append(time.time() - t0)

    latencies = np.array(latencies)
    print(f"Inference Latency (single, median):  {np.percentile(latencies, 50)*1000:.2f} ms")
    print(f"Inference Latency (single, p95):     {np.percentile(latencies, 95)*1000:.2f} ms")
    print(f"Inference Latency (single, p99):     {np.percentile(latencies, 99)*1000:.2f} ms")
    print(f"Inference Throughput (single):        {NUM_TRIALS / latencies.sum():.2f} FPS")

    # --- batch throughput ---
    ctx_np_b, miss_np_b = random_inputs_np(BATCH_SIZE)
    run(ctx_np_b, miss_np_b)  # warm-up

    batch_times = []
    for _ in range(NUM_BATCHES):
        t0 = time.time()
        run(ctx_np_b, miss_np_b)
        batch_times.append(time.time() - t0)

    batch_fps = (BATCH_SIZE * NUM_BATCHES) / sum(batch_times)
    print(f"Batch Throughput:                     {batch_fps:.2f} FPS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["pytorch", "onnx"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", default="cpu", help="cpu | cuda | rocm")
    parser.add_argument(
        "--graph-opt",
        default="extended",
        choices=["none", "basic", "extended"],
        help="ONNX graph optimization level",
    )
    parser.add_argument(
        "--ep",
        default="cpu",
        choices=["cpu", "cuda", "rocm", "openvino"],
        help="ONNX execution provider",
    )
    args = parser.parse_args()

    if args.mode == "pytorch":
        benchmark_pytorch(args.model, args.device)
    else:
        benchmark_onnx(args.model, args.graph_opt, args.ep)

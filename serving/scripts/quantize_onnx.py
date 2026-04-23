"""
quantize_onnx.py — Dynamic INT8 quantization. Uses onnxruntime.quantization (not INC).
Bucket: models-proj01, keys under production/
"""

import argparse, os, sys, tempfile
from onnxruntime.quantization import quantize_dynamic, QuantType

def quantize(input_path, output_path):
    print(f"[quantize] {input_path} -> {output_path}")
    quantize_dynamic(model_input=input_path, model_output=output_path,
                     weight_type=QuantType.QInt8)
    in_sz = os.path.getsize(input_path)
    out_sz = os.path.getsize(output_path)
    print(f"[quantize] {in_sz} -> {out_sz} bytes ({100*(1-out_sz/in_sz):.1f}% reduction)")
    import onnxruntime as ort, numpy as np
    sess = ort.InferenceSession(output_path, providers=["CPUExecutionProvider"])
    out = sess.run(None, {"context_ids": np.zeros((1,20), dtype=np.int64),
                          "missing_id": np.zeros((1,), dtype=np.int64)})
    print(f"[quantize] Sanity check passed. Shape: {out[0].shape}")

def quantize_from_object_storage():
    import boto3
    bucket = os.getenv("MODEL_BUCKET", "models-proj01")
    in_key = os.getenv("INPUT_ONNX_KEY",
                        "production/subst_model_current.onnx")
    out_key = os.getenv("OUTPUT_ONNX_KEY",
                         "production/subst_model_quantized.onnx")
    s3 = boto3.client("s3", endpoint_url=os.getenv("OS_ENDPOINT"),
        aws_access_key_id=os.getenv("OS_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("OS_SECRET_KEY"))
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "model.onnx")
        outp = os.path.join(td, "model_q.onnx")
        s3.download_file(Bucket=bucket, Key=in_key, Filename=inp)
        quantize(inp, outp)
        s3.upload_file(Filename=outp, Bucket=bucket, Key=out_key)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input")
    p.add_argument("--output", default="model_quantized.onnx")
    p.add_argument("--from-object-storage", action="store_true")
    args = p.parse_args()
    if args.from_object_storage:
        quantize_from_object_storage()
    else:
        if not args.input: p.error("--input required")
        quantize(args.input, args.output)

if __name__ == "__main__":
    main()

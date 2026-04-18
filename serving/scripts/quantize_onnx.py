"""
quantize_onnx.py

Applies dynamic quantization to an ONNX model to shrink it and speed up
CPU inference. Produces the quantized model that serve_onnx.py uses in
production.

IMPORTANT: We use onnxruntime.quantization (NOT Intel Neural Compressor).
The initial implementation hit breaking API changes with INC. ORT's
built-in dynamic quantization is a stable, well-supported alternative
that produces comparable results for embedding-based models.

Usage:
  python quantize_onnx.py \\
    --input /path/to/model.onnx \\
    --output /path/to/model_quantized.onnx

Or from object storage:
  python quantize_onnx.py --from-object-storage
"""

import argparse
import os
import sys
import tempfile

from onnxruntime.quantization import quantize_dynamic, QuantType


def quantize(input_path: str, output_path: str):
    print(f"[quantize_onnx] Quantizing {input_path} -> {output_path}")
    quantize_dynamic(
        model_input=input_path,
        model_output=output_path,
        weight_type=QuantType.QInt8,
    )

    in_size = os.path.getsize(input_path)
    out_size = os.path.getsize(output_path)
    reduction = 100 * (1 - out_size / in_size)
    print(f"[quantize_onnx] Size: {in_size} -> {out_size} bytes "
          f"({reduction:.1f}% reduction)")

    # Sanity check
    import onnxruntime as ort
    import numpy as np
    sess = ort.InferenceSession(output_path,
                                  providers=["CPUExecutionProvider"])
    input_shapes = {i.name: i.shape for i in sess.get_inputs()}
    print(f"[quantize_onnx] Quantized model inputs: {input_shapes}")

    # Dummy inference — shape [1, 20] for context, [1] for missing
    out = sess.run(None, {
        "context_ids": np.zeros((1, 20), dtype=np.int64),
        "missing_id": np.zeros((1,), dtype=np.int64),
    })
    print(f"[quantize_onnx] Sanity-check passed. Output shape: {out[0].shape}")


def quantize_from_object_storage():
    import boto3
    bucket = os.getenv("MODEL_BUCKET", "models-proj01")
    in_key = os.getenv("INPUT_ONNX_KEY",
                        "production/subst_model_current.onnx")
    out_key = os.getenv("OUTPUT_ONNX_KEY",
                         "production/subst_model_quantized.onnx")

    s3 = boto3.client(
        "s3",
        endpoint_url=os.getenv("OS_ENDPOINT"),
        aws_access_key_id=os.getenv("OS_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("OS_SECRET_KEY"),
    )

    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "model.onnx")
        out_path = os.path.join(td, "model_quantized.onnx")

        s3.download_file(Bucket=bucket, Key=in_key, Filename=in_path)
        quantize(in_path, out_path)
        s3.upload_file(Filename=out_path, Bucket=bucket, Key=out_key)
        print(f"[quantize_onnx] Uploaded quantized model to "
              f"{bucket}/{out_key}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--output", default="model_quantized.onnx")
    parser.add_argument("--from-object-storage", action="store_true")
    args = parser.parse_args()

    if args.from_object_storage:
        quantize_from_object_storage()
    else:
        if not args.input:
            parser.error("--input required when not using --from-object-storage")
        quantize(args.input, args.output)


if __name__ == "__main__":
    main()

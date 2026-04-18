"""
Quantize the ONNX ingredient-substitution model using Intel Neural Compressor.

Usage:
    python quantize_onnx.py --input subst_model.onnx --output subst_model_quantized_dynamic.onnx
"""

import argparse
import os
from onnxruntime.quantization import quantize_dynamic, QuantType

def quantize(input_path, output_path):
    quantize_dynamic(input_path, output_path, weight_type=QuantType.QUInt8)
    model_size = os.path.getsize(output_path)
    print(f"Quantized model saved to {output_path}  ({model_size / 1e6:.2f} MB)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="subst_model.onnx")
    parser.add_argument("--output", default="subst_model_quantized_dynamic.onnx")
    args = parser.parse_args()
    quantize(args.input, args.output)
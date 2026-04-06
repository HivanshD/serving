"""
Quantize the ONNX ingredient-substitution model using Intel Neural Compressor.

Usage:
    python quantize_onnx.py --input subst_model.onnx --output subst_model_quantized_dynamic.onnx
"""

import argparse
import os

import neural_compressor
from neural_compressor import quantization
from neural_compressor.config import PostTrainingQuantConfig


def quantize_dynamic(input_path: str, output_path: str):
    fp32_model = neural_compressor.model.onnx_model.ONNXModel(input_path)

    config_ptq = PostTrainingQuantConfig(approach="dynamic")

    q_model = quantization.fit(model=fp32_model, conf=config_ptq)
    q_model.save_model_to_file(output_path)

    model_size = os.path.getsize(output_path)
    print(f"Quantized model saved to {output_path}  ({model_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="subst_model.onnx")
    parser.add_argument("--output", default="subst_model_quantized_dynamic.onnx")
    args = parser.parse_args()
    quantize_dynamic(args.input, args.output)

"""
Export the ingredient-substitution PyTorch model to ONNX format.

Usage:
    python export_onnx.py [--input subst_model.pth] [--output subst_model.onnx]
"""

import argparse
import torch
import onnx
from model_stub import SubstitutionModel, MAX_INGREDIENTS, MODEL_FILENAME


def export(input_path: str, output_path: str):
    device = torch.device("cpu")
    model = torch.load(input_path, map_location=device, weights_only=False)
    model.eval()

    # Dummy inputs matching the model signature
    dummy_context = torch.randint(0, 100, (1, MAX_INGREDIENTS), dtype=torch.int64)
    dummy_missing = torch.randint(1, 100, (1, 1), dtype=torch.int64)

    torch.onnx.export(
        model,
        (dummy_context, dummy_missing),
        output_path,
        export_params=True,
        opset_version=14,
        do_constant_folding=True,
        input_names=["recipe_context", "missing_ingredient"],
        output_names=["scores"],
        dynamic_axes={
            "recipe_context": {0: "batch_size"},
            "missing_ingredient": {0: "batch_size"},
            "scores": {0: "batch_size"},
        },
    )
    print(f"Exported ONNX model to {output_path}")

    # Validate
    onnx_model = onnx.load(output_path)
    onnx.checker.check_model(onnx_model)
    print("ONNX model passed validation check.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=MODEL_FILENAME)
    parser.add_argument("--output", default="subst_model.onnx")
    args = parser.parse_args()
    export(args.input, args.output)

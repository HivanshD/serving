"""
export_onnx.py

Converts a trained PyTorch checkpoint into an ONNX graph.

CRITICAL: opset_version must be 14 to match Triton 24.01 container.
  - opset 20 caused mysterious failures in the initial implementation
  - opset 14 is tested and works with both Triton and onnxruntime 1.18

The script also writes out the vocabulary as a separate JSON file so
the ONNX serving container can tokenize ingredient strings.

Usage:
  python export_onnx.py \\
    --checkpoint /path/to/subst_model.pth \\
    --output-onnx /path/to/model.onnx \\
    --output-vocab /path/to/vocab.json

Or from object storage (inside a container):
  python export_onnx.py --from-object-storage

Environment variables:
  OS_ENDPOINT, OS_ACCESS_KEY, OS_SECRET_KEY
  MODEL_BUCKET       (default "models-proj01")
  INPUT_CHECKPOINT_KEY (default "production/subst_model_current.pth")
  OUTPUT_ONNX_KEY    (default "production/subst_model_current.onnx")
  OUTPUT_VOCAB_KEY   (default "production/vocab.json")
"""

import argparse
import json
import os
import sys
import tempfile

import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fastapi_pt"))
from model_stub import SubstitutionModel, CONTEXT_LEN


ONNX_OPSET = 14   # DO NOT CHANGE — must match Triton 24.01


def export(checkpoint_path: str, onnx_path: str, vocab_path: str):
    print(f"[export_onnx] Loading checkpoint: {checkpoint_path}")
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)

    vocab = ckpt["vocab"]
    config = ckpt.get("config", {})
    embed_dim = config.get("embed_dim", 128)
    vocab_size = len(vocab)

    print(f"[export_onnx] vocab_size={vocab_size}, embed_dim={embed_dim}")

    model = SubstitutionModel(vocab_size=vocab_size, embed_dim=embed_dim)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()

    # Dummy inputs that match the model signature
    dummy_context = torch.zeros(1, CONTEXT_LEN, dtype=torch.long)
    dummy_missing = torch.zeros(1, dtype=torch.long)

    print(f"[export_onnx] Exporting to {onnx_path} (opset={ONNX_OPSET})")
    torch.onnx.export(
        model,
        (dummy_context, dummy_missing),
        onnx_path,
        input_names=["context_ids", "missing_id"],
        output_names=["scores"],
        dynamic_axes={
            "context_ids": {0: "batch"},
            "missing_id": {0: "batch"},
            "scores": {0: "batch"},
        },
        opset_version=ONNX_OPSET,
        do_constant_folding=True,
    )

    # Write vocabulary
    with open(vocab_path, "w") as f:
        json.dump(vocab, f)
    print(f"[export_onnx] Wrote vocab: {vocab_path}")

    # Sanity check: load the ONNX model and run a forward pass
    import onnxruntime as ort
    import numpy as np
    sess = ort.InferenceSession(onnx_path,
                                  providers=["CPUExecutionProvider"])
    out = sess.run(None, {
        "context_ids": dummy_context.numpy().astype(np.int64),
        "missing_id": dummy_missing.numpy().astype(np.int64),
    })
    print(f"[export_onnx] ONNX sanity-check passed. Output shape: "
          f"{out[0].shape}")


def export_from_object_storage():
    import boto3
    bucket = os.getenv("MODEL_BUCKET", "models-proj01")
    in_key = os.getenv("INPUT_CHECKPOINT_KEY",
                        "production/subst_model_current.pth")
    out_onnx_key = os.getenv("OUTPUT_ONNX_KEY",
                               "production/subst_model_current.onnx")
    out_vocab_key = os.getenv("OUTPUT_VOCAB_KEY", "production/vocab.json")

    s3 = boto3.client(
        "s3",
        endpoint_url=os.getenv("OS_ENDPOINT"),
        aws_access_key_id=os.getenv("OS_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("OS_SECRET_KEY"),
    )

    with tempfile.TemporaryDirectory() as td:
        ckpt_path = os.path.join(td, "ckpt.pth")
        onnx_path = os.path.join(td, "model.onnx")
        vocab_path = os.path.join(td, "vocab.json")

        print(f"[export_onnx] Downloading {bucket}/{in_key}")
        s3.download_file(Bucket=bucket, Key=in_key, Filename=ckpt_path)

        export(ckpt_path, onnx_path, vocab_path)

        print(f"[export_onnx] Uploading ONNX to {bucket}/{out_onnx_key}")
        s3.upload_file(Filename=onnx_path, Bucket=bucket, Key=out_onnx_key)

        print(f"[export_onnx] Uploading vocab to {bucket}/{out_vocab_key}")
        s3.upload_file(Filename=vocab_path, Bucket=bucket, Key=out_vocab_key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint",
                        help="Path to input PyTorch checkpoint .pth")
    parser.add_argument("--output-onnx", default="model.onnx")
    parser.add_argument("--output-vocab", default="vocab.json")
    parser.add_argument("--from-object-storage", action="store_true",
                        help="Download checkpoint and upload ONNX to "
                             "object storage")
    args = parser.parse_args()

    if args.from_object_storage:
        export_from_object_storage()
    else:
        if not args.checkpoint:
            parser.error("--checkpoint is required when not using "
                          "--from-object-storage")
        export(args.checkpoint, args.output_onnx, args.output_vocab)


if __name__ == "__main__":
    main()

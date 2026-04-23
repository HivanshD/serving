"""
export_onnx.py — Converts PyTorch checkpoint to ONNX opset 14.

NOTE: Training's train.py already does ONNX export inline after quality gate.
This script exists for manual re-exports or if training's inline export fails.

Bucket: models-proj01
Keys:   production/subst_model_current.{pth,onnx}, production/vocab.json
"""

import argparse, json, os, sys, tempfile
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fastapi_pt"))
from model_stub import SubstitutionModel, CONTEXT_LEN

ONNX_OPSET = 14

def export(checkpoint_path, onnx_path, vocab_path):
    print(f"[export_onnx] Loading: {checkpoint_path}")
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    vocab = ckpt["vocab"]
    config = ckpt.get("config", {})
    embed_dim = config.get("embed_dim", 128)
    print(f"[export_onnx] vocab={len(vocab)} embed_dim={embed_dim}")

    model = SubstitutionModel(vocab_size=len(vocab), embed_dim=embed_dim)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()

    dummy_ctx = torch.zeros(1, CONTEXT_LEN, dtype=torch.long)
    dummy_miss = torch.zeros(1, dtype=torch.long)

    torch.onnx.export(model, (dummy_ctx, dummy_miss), onnx_path,
        input_names=["context_ids", "missing_id"],
        output_names=["scores"],
        dynamic_axes={"context_ids": {0: "batch"}, "missing_id": {0: "batch"},
                      "scores": {0: "batch"}},
        opset_version=ONNX_OPSET, do_constant_folding=True)

    with open(vocab_path, "w") as f:
        json.dump(vocab, f)
    print(f"[export_onnx] Wrote {onnx_path} + {vocab_path}")

    import onnxruntime as ort
    import numpy as np
    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    out = sess.run(None, {"context_ids": dummy_ctx.numpy().astype(np.int64),
                          "missing_id": dummy_miss.numpy().astype(np.int64)})
    print(f"[export_onnx] Sanity check passed. Shape: {out[0].shape}")


def export_from_object_storage():
    import boto3
    bucket = os.getenv("MODEL_BUCKET", "models-proj01")
    in_key = os.getenv("INPUT_CHECKPOINT_KEY",
                        "production/subst_model_current.pth")
    out_onnx_key = os.getenv("OUTPUT_ONNX_KEY",
                               "production/subst_model_current.onnx")
    out_vocab_key = os.getenv("OUTPUT_VOCAB_KEY",
                                "production/vocab.json")
    s3 = boto3.client("s3", endpoint_url=os.getenv("OS_ENDPOINT"),
        aws_access_key_id=os.getenv("OS_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("OS_SECRET_KEY"))

    with tempfile.TemporaryDirectory() as td:
        ckpt = os.path.join(td, "ckpt.pth")
        onnx = os.path.join(td, "model.onnx")
        vocab = os.path.join(td, "vocab.json")
        print(f"[export_onnx] Downloading {bucket}/{in_key}")
        s3.download_file(Bucket=bucket, Key=in_key, Filename=ckpt)
        export(ckpt, onnx, vocab)
        s3.upload_file(Filename=onnx, Bucket=bucket, Key=out_onnx_key)
        s3.upload_file(Filename=vocab, Bucket=bucket, Key=out_vocab_key)
        print(f"[export_onnx] Uploaded to {bucket}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint")
    p.add_argument("--output-onnx", default="model.onnx")
    p.add_argument("--output-vocab", default="vocab.json")
    p.add_argument("--from-object-storage", action="store_true")
    args = p.parse_args()
    if args.from_object_storage:
        export_from_object_storage()
    else:
        if not args.checkpoint:
            p.error("--checkpoint required")
        export(args.checkpoint, args.output_onnx, args.output_vocab)

if __name__ == "__main__":
    main()

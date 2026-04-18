"""
model.py — Triton Inference Server Python backend for the substitution model.

Triton calls TritonPythonModel.initialize() once when loading the model,
and execute() for each (potentially batched) request.

Loads the PyTorch checkpoint from /models/subst_model/1/model.pth at init,
then runs ranking in a loop over the batch.

For production, prefer the ONNX backend (subst_model_onnx/) — it has better
performance and no Python overhead. This Python backend is kept as a
benchmark comparison point and as a fallback if ONNX export breaks.
"""

import json
import os
import sys

import numpy as np
import torch
import triton_python_backend_utils as pb_utils

# model_stub.py is copied next to this file at container build time
sys.path.insert(0, os.path.dirname(__file__))
from model_stub import SubstitutionModel


class TritonPythonModel:

    def initialize(self, args):
        model_dir = args["model_repository"] + "/" + args["model_version"]
        ckpt_path = os.path.join(model_dir, "model.pth")

        print(f"[triton-python] Loading checkpoint: {ckpt_path}")
        ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)

        vocab = ckpt["vocab"]
        config = ckpt.get("config", {})
        embed_dim = config.get("embed_dim", 128)

        self.model = SubstitutionModel(
            vocab_size=len(vocab), embed_dim=embed_dim)
        self.model.load_state_dict(ckpt["model_state_dict"])
        self.model.eval()

        self.vocab_size = len(vocab)

        if torch.cuda.is_available():
            self.model = self.model.cuda()
            self.device = "cuda"
        else:
            self.device = "cpu"

        print(f"[triton-python] Loaded. vocab_size={self.vocab_size}, "
              f"device={self.device}")

    def execute(self, requests):
        responses = []

        for request in requests:
            ctx_tensor = pb_utils.get_input_tensor_by_name(
                request, "context_ids")
            miss_tensor = pb_utils.get_input_tensor_by_name(
                request, "missing_id")

            ctx_np = ctx_tensor.as_numpy()        # (batch, 20)
            miss_np = miss_tensor.as_numpy()      # (batch, 1)

            ctx_t = torch.from_numpy(ctx_np).long().to(self.device)
            miss_t = torch.from_numpy(miss_np.squeeze(-1)).long().to(
                self.device)

            with torch.no_grad():
                scores = self.model(ctx_t, miss_t)   # (batch, vocab)

            scores_np = scores.cpu().numpy().astype(np.float32)

            out_tensor = pb_utils.Tensor("scores", scores_np)
            responses.append(pb_utils.InferenceResponse([out_tensor]))

        return responses

    def finalize(self):
        print("[triton-python] Shutting down")

"""
Triton Python-backend model for ingredient substitution.

Accepts JSON-encoded requests with recipe_context and missing_ingredient,
returns top-K substitution candidates with scores.
"""

import json
import os
import sys

# Ensure model_stub.py is importable for torch.load unpickling
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import torch
from model_stub import SubstitutionModel  # noqa: F401
import triton_python_backend_utils as pb_utils


class TritonPythonModel:
    def initialize(self, args):
        model_dir = os.path.dirname(__file__)
        model_path = os.path.join(model_dir, "subst_model.pth")

        # Determine device from Triton instance config
        instance_kind = args.get("model_instance_kind", "cpu").lower()
        if instance_kind == "gpu":
            device_id = int(args.get("model_instance_device_id", 0))
            torch.cuda.set_device(device_id)
            self.device = torch.device(
                f"cuda:{device_id}" if torch.cuda.is_available() else "cpu"
            )
        else:
            self.device = torch.device("cpu")

        self.model = torch.load(model_path, map_location=self.device, weights_only=False)
        self.model.to(self.device)
        self.model.eval()

        self.max_ingredients = 20
        self.top_k = 10

    def _parse_request(self, raw_bytes):
        """Parse a single JSON request into padded numpy arrays."""
        if isinstance(raw_bytes, bytes):
            raw_bytes = raw_bytes.decode("utf-8")
        data = json.loads(raw_bytes)

        ctx = data["recipe_context"][: self.max_ingredients]
        ctx = ctx + [0] * (self.max_ingredients - len(ctx))
        missing = data["missing_ingredient"]
        return np.array(ctx, dtype=np.int64), np.array([missing], dtype=np.int64)

    def execute(self, requests):
        batched_ctx = []
        batched_miss = []

        for request in requests:
            in_tensor = pb_utils.get_input_tensor_by_name(request, "INPUT_JSON")
            raw = in_tensor.as_numpy()[0, 0]  # shape [1,1]
            ctx, miss = self._parse_request(raw)
            batched_ctx.append(ctx)
            batched_miss.append(miss)

        ctx_t = torch.tensor(np.stack(batched_ctx), device=self.device)
        miss_t = torch.tensor(np.stack(batched_miss), device=self.device)

        with torch.no_grad():
            scores = self.model(ctx_t, miss_t)  # (B, NUM_CANDIDATES)

        scores_np = scores.cpu().numpy()

        responses = []
        for i, _request in enumerate(requests):
            row = scores_np[i]
            top_ids = np.argsort(row)[::-1][: self.top_k]
            result = [
                {"candidate_id": int(idx), "score": float(row[idx])} for idx in top_ids
            ]
            result_str = json.dumps(result)

            out_np = np.array([[result_str]], dtype=object)
            out_tensor = pb_utils.Tensor("OUTPUT_JSON", out_np)
            responses.append(pb_utils.InferenceResponse(output_tensors=[out_tensor]))

        return responses

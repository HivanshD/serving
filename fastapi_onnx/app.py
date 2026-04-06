"""
FastAPI endpoint for the ingredient-substitution model (ONNX backend).

POST /predict
Body: {"recipe_context": [12, 45, 3, ...], "missing_ingredient": 77}
Response: {"substitutions": [{"candidate_id": 42, "score": 0.91}, ...]}
"""

import os
import numpy as np
import onnxruntime as ort
from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import List

# ── app ──────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Ingredient Substitution API (ONNX)",
    description="Serves ranked substitution suggestions via ONNX Runtime.",
    version="1.0.0",
)

# ── request / response schemas ───────────────────────────────────────────
class SubstitutionRequest(BaseModel):
    recipe_context: List[int] = Field(
        ..., description="List of ingredient IDs in the recipe (max 20, 0-padded)"
    )
    missing_ingredient: int = Field(
        ..., description="Ingredient ID that needs a substitute"
    )

class CandidateScore(BaseModel):
    candidate_id: int
    score: float

class SubstitutionResponse(BaseModel):
    substitutions: List[CandidateScore]

# ── model loading ────────────────────────────────────────────────────────
MAX_INGREDIENTS = 20
TOP_K = 10
ONNX_MODEL_PATH = os.getenv("ONNX_MODEL_PATH", "subst_model.onnx")

sess_options = ort.SessionOptions()
sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED

providers = ["CPUExecutionProvider"]
if "CUDAExecutionProvider" in ort.get_available_providers():
    providers.insert(0, "CUDAExecutionProvider")

session = ort.InferenceSession(ONNX_MODEL_PATH, sess_options=sess_options, providers=providers)
input_names = [inp.name for inp in session.get_inputs()]


# ── inference endpoint ───────────────────────────────────────────────────
@app.post("/predict", response_model=SubstitutionResponse)
def predict(request: SubstitutionRequest):
    ctx = request.recipe_context[:MAX_INGREDIENTS]
    ctx = ctx + [0] * (MAX_INGREDIENTS - len(ctx))

    ctx_np = np.array([ctx], dtype=np.int64)
    miss_np = np.array([[request.missing_ingredient]], dtype=np.int64)

    outputs = session.run(None, {input_names[0]: ctx_np, input_names[1]: miss_np})
    scores = outputs[0][0]  # (NUM_CANDIDATES,)

    top_indices = np.argsort(scores)[::-1][:TOP_K]
    substitutions = [
        CandidateScore(candidate_id=int(idx), score=float(scores[idx]))
        for idx in top_indices
    ]
    return SubstitutionResponse(substitutions=substitutions)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "backend": "onnx",
        "providers": session.get_providers(),
    }

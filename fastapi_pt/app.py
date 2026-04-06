"""
FastAPI endpoint for the ingredient-substitution model (PyTorch backend).

POST /predict
Body: {"recipe_context": [12, 45, 3, ...], "missing_ingredient": 77}
Response: {"substitutions": [{"candidate_id": 42, "score": 0.91}, ...]}
"""

import os
import numpy as np
import torch
from model_stub import SubstitutionModel  # noqa: F401 — needed for unpickling
from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import List

# ── app ──────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Ingredient Substitution API (PyTorch)",
    description="Serves ranked substitution suggestions for a missing ingredient.",
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
MODEL_PATH = os.getenv("MODEL_PATH", "subst_model.pth")

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = torch.load(MODEL_PATH, map_location=device, weights_only=False)
model.to(device)
model.eval()


# ── inference endpoint ───────────────────────────────────────────────────
@app.post("/predict", response_model=SubstitutionResponse)
def predict(request: SubstitutionRequest):
    # Pad / truncate recipe context to MAX_INGREDIENTS
    ctx = request.recipe_context[:MAX_INGREDIENTS]
    ctx = ctx + [0] * (MAX_INGREDIENTS - len(ctx))

    ctx_t = torch.tensor([ctx], dtype=torch.int64, device=device)
    miss_t = torch.tensor([[request.missing_ingredient]], dtype=torch.int64, device=device)

    with torch.no_grad():
        scores = model(ctx_t, miss_t)  # (1, NUM_CANDIDATES)

    scores_np = scores.cpu().numpy()[0]
    top_indices = np.argsort(scores_np)[::-1][:TOP_K]

    substitutions = [
        CandidateScore(candidate_id=int(idx), score=float(scores_np[idx]))
        for idx in top_indices
    ]
    return SubstitutionResponse(substitutions=substitutions)


@app.get("/health")
def health():
    return {"status": "ok", "backend": "pytorch", "device": str(device)}

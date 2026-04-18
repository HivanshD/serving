"""
Ingredient Substitution Model Stub

Embedding-based ranking model: given recipe context (ingredient IDs) and a
missing ingredient ID, produces similarity scores over a fixed candidate
vocabulary.  Weights are random — sufficient for serving-pipeline validation
per rubric requirements.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

# ── constants (shared across all serving artifacts) ──────────────────────
VOCAB_SIZE = 1000          # ingredient vocabulary size
EMBEDDING_DIM = 128        # embedding dimensionality
MAX_INGREDIENTS = 20       # max ingredients per recipe context
NUM_CANDIDATES = 100       # number of substitution candidates to score
MODEL_FILENAME = "subst_model.pth"


class SubstitutionModel(nn.Module):
    """
    Forward pass
    ------------
    recipe_context   : (batch, MAX_INGREDIENTS)  int64 — padded ingredient IDs
    missing_ingredient: (batch, 1)               int64 — the ingredient to replace

    Returns
    -------
    scores           : (batch, NUM_CANDIDATES)   float32 — cosine-similarity
                        scores for each candidate substitution
    """

    def __init__(
        self,
        vocab_size: int = VOCAB_SIZE,
        embedding_dim: int = EMBEDDING_DIM,
        num_candidates: int = NUM_CANDIDATES,
    ):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embedding_dim, padding_idx=0)
        self.context_proj = nn.Linear(embedding_dim, embedding_dim)
        self.missing_proj = nn.Linear(embedding_dim, embedding_dim)
        # Fixed candidate embedding matrix (learned in real training)
        self.candidate_embeddings = nn.Parameter(
            torch.randn(num_candidates, embedding_dim)
        )

    def forward(self, recipe_context: torch.Tensor, missing_ingredient: torch.Tensor):
        # Embed and mean-pool recipe context (ignore pad=0)
        ctx_emb = self.embedding(recipe_context)          # (B, 20, D)
        mask = (recipe_context != 0).unsqueeze(-1).float() # (B, 20, 1)
        ctx_vec = (ctx_emb * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)  # (B, D)
        ctx_vec = self.context_proj(ctx_vec)               # (B, D)

        # Embed missing ingredient
        miss_emb = self.embedding(missing_ingredient.squeeze(-1))  # (B, D)
        miss_vec = self.missing_proj(miss_emb)                      # (B, D)

        # Combined query
        query = F.normalize(ctx_vec + miss_vec, dim=-1)             # (B, D)

        # Score against candidates via cosine similarity
        cand = F.normalize(self.candidate_embeddings, dim=-1)       # (C, D)
        scores = torch.matmul(query, cand.T)                        # (B, C)
        return scores


def create_and_save(path: str = MODEL_FILENAME):
    """Instantiate with random weights and save."""
    model = SubstitutionModel()
    model.eval()
    torch.save(model, path)
    print(f"Saved model stub to {path}")
    return model


if __name__ == "__main__":
    # Import via module name so torch.save pickles the class as
    # 'model_stub.SubstitutionModel' rather than '__main__.SubstitutionModel'
    import model_stub
    model_stub.create_and_save()

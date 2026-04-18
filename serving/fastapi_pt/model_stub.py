"""
model_stub.py

Shared model architecture for the ingredient substitution model.
This file is used by:
  - fastapi_pt/serve_pytorch.py  (PyTorch serving)
  - training/train.py             (training script — training team imports this)
  - models/subst_model/1/model.py (Triton Python backend)

IMPORTANT: Any change to SubstitutionModel must be coordinated with the
training team. The saved checkpoint's state_dict must match this architecture
exactly, or load_state_dict will fail.

Architecture (contract):
  Input:  recipe_context (list of ingredient IDs, padded to length 20)
          missing_ingredient (single ingredient ID)
  Output: ranked list of top-k (ingredient_string, embedding_score)

Method:
  1. Embed recipe_context and average-pool → context_vector
  2. Embed missing_ingredient → missing_vector
  3. query_vector = context_vector + missing_vector
  4. Embed ALL ingredients in vocab → candidate_matrix
  5. Cosine similarity between query and every candidate
  6. Mask out PAD, UNK, and missing_ingredient itself
  7. Return top-k
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

# Default hyperparameters. Training can override these via config.yaml,
# but the saved checkpoint will carry the actual values.
DEFAULT_VOCAB_SIZE = 10000
DEFAULT_EMBED_DIM = 128
CONTEXT_LEN = 20

# Reserved vocabulary indices. These MUST be the same in training and serving.
PAD_ID = 0
UNK_ID = 1


class SubstitutionModel(nn.Module):
    """
    Embedding-based ranking model for ingredient substitution.

    The model is deliberately simple:
      - Only an nn.Embedding layer
      - All ranking logic is cosine similarity in embedding space
    This keeps the serving path fast (sub-millisecond on CPU for small vocabs)
    and lets us export cleanly to ONNX.
    """

    def __init__(self, vocab_size=DEFAULT_VOCAB_SIZE, embed_dim=DEFAULT_EMBED_DIM):
        super().__init__()
        self.vocab_size = vocab_size
        self.embed_dim = embed_dim
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=PAD_ID)

    def forward(self, context_ids, missing_id):
        """
        Forward pass that returns scores for every ingredient in the vocabulary.

        Args:
          context_ids:  LongTensor of shape (batch, CONTEXT_LEN)
          missing_id:   LongTensor of shape (batch,)

        Returns:
          scores: FloatTensor of shape (batch, vocab_size)
                  Higher score = more likely substitution.
        """
        # (batch, CONTEXT_LEN, embed_dim)
        ctx_embed = self.embedding(context_ids)

        # Mask out PAD tokens when averaging
        mask = (context_ids != PAD_ID).float().unsqueeze(-1)
        ctx_sum = (ctx_embed * mask).sum(dim=1)
        ctx_count = mask.sum(dim=1).clamp(min=1.0)
        ctx_vec = ctx_sum / ctx_count            # (batch, embed_dim)

        miss_vec = self.embedding(missing_id)    # (batch, embed_dim)

        query = ctx_vec + miss_vec               # (batch, embed_dim)
        query = F.normalize(query, dim=-1)

        # Every ingredient in the vocab is a candidate
        all_embeds = self.embedding.weight       # (vocab_size, embed_dim)
        all_embeds_normed = F.normalize(all_embeds, dim=-1)

        # Cosine similarity via dot product of normalized vectors
        scores = query @ all_embeds_normed.T     # (batch, vocab_size)
        return scores

    @torch.no_grad()
    def get_substitutions(self, context_ids, missing_id, id_to_ingredient,
                           top_k=3):
        """
        Inference helper: returns top-k substitution strings with scores.

        Args:
          context_ids:       list[int] of length CONTEXT_LEN
          missing_id:        int
          id_to_ingredient:  dict mapping int -> str (inverted vocabulary)
          top_k:             how many suggestions to return

        Returns:
          list of dicts like:
          [
            {"ingredient": "greek yogurt", "rank": 1, "embedding_score": 0.91},
            {"ingredient": "plain yogurt", "rank": 2, "embedding_score": 0.85},
            ...
          ]
        """
        self.eval()
        ctx = torch.tensor([context_ids], dtype=torch.long)
        miss = torch.tensor([missing_id], dtype=torch.long)

        scores = self.forward(ctx, miss).squeeze(0)   # (vocab_size,)

        # Mask out ingredients we never want to suggest
        scores[PAD_ID] = -float("inf")
        scores[UNK_ID] = -float("inf")
        if 0 <= missing_id < len(scores):
            scores[missing_id] = -float("inf")

        top_values, top_indices = torch.topk(scores, k=top_k)

        out = []
        for rank, (idx, val) in enumerate(zip(top_indices.tolist(),
                                               top_values.tolist()), start=1):
            ingredient = id_to_ingredient.get(idx, f"<id_{idx}>")
            # Clip score to [0,1] range (cosine sim is in [-1,1], clip for UI)
            display_score = max(0.0, min(1.0, val))
            out.append({
                "ingredient": ingredient,
                "rank": rank,
                "embedding_score": round(display_score, 4),
            })
        return out


def tokenize_ingredients(ingredient_strings, vocab, context_len=CONTEXT_LEN):
    """
    Convert a list of ingredient strings into a padded list of IDs.

    Args:
      ingredient_strings: list[str], each string already normalized
                          (e.g. "sour cream", not "1 cup sour cream").
                          Normalization is the Data team's responsibility.
      vocab:              dict mapping ingredient_string -> int
      context_len:        length to pad / truncate to

    Returns:
      list[int] of length context_len
    """
    ids = []
    for ing in ingredient_strings:
        if not isinstance(ing, str):
            continue
        key = ing.lower().strip()
        ids.append(vocab.get(key, UNK_ID))

    # Pad or truncate
    ids = ids[:context_len]
    ids += [PAD_ID] * (context_len - len(ids))
    return ids


def build_stub_vocab_and_model(vocab_size=DEFAULT_VOCAB_SIZE,
                                 embed_dim=DEFAULT_EMBED_DIM):
    """
    Build a STUB model with random weights for testing/benchmarking
    when no trained checkpoint is available.

    Returns (model, vocab, id_to_ingredient).
    The stub vocab uses "ingredient_0", "ingredient_1", etc. as placeholders.
    Real vocab comes from the training checkpoint.
    """
    # Build placeholder vocab
    vocab = {"<PAD>": PAD_ID, "<UNK>": UNK_ID}
    # Add common real ingredients so stub responses look plausible
    common = [
        "sour cream", "greek yogurt", "plain yogurt", "buttermilk",
        "butter", "olive oil", "cream cheese", "heavy cream",
        "milk", "water", "sugar", "brown sugar", "honey", "maple syrup",
        "flour", "all purpose flour", "whole wheat flour", "almond flour",
        "salt", "pepper", "garlic", "onion", "tomato", "basil",
        "cheddar", "mozzarella", "parmesan", "feta",
        "chicken", "beef", "pork", "tofu", "tempeh",
        "egg", "egg white", "flax egg", "chia egg",
    ]
    for c in common:
        if c not in vocab:
            vocab[c] = len(vocab)

    # Fill out to the requested vocab_size with placeholders
    while len(vocab) < vocab_size:
        vocab[f"ingredient_{len(vocab)}"] = len(vocab)

    id_to_ingredient = {v: k for k, v in vocab.items()}

    model = SubstitutionModel(vocab_size=vocab_size, embed_dim=embed_dim)
    # Seed so stub responses are deterministic
    torch.manual_seed(42)
    model.embedding.weight.data = torch.randn(vocab_size, embed_dim) * 0.1

    return model, vocab, id_to_ingredient

import torch, torch.nn as nn, torch.nn.functional as F
import boto3, json, pandas as pd, io

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

s3 = boto3.client("s3",
    endpoint_url="https://chi.tacc.chameleoncloud.org:7480",
    aws_access_key_id="8921c48faf83433db2b1439a9b2889fd",
    aws_secret_access_key="7d1ce78efc5a48019888c9f3fa8ba2dd",
    region_name="us-east-1")

def load_s3_csv(key):
    return pd.read_csv(io.BytesIO(s3.get_object(Bucket="data-proj01", Key=key)["Body"].read()))

fg_nodes = load_s3_csv("data/raw/flavorgraph/nodes_191120.csv")
fg_edges = load_s3_csv("data/raw/flavorgraph/edges_191120.csv")
fg_node_names = {str(n).lower().strip(): i for i, n in enumerate(fg_nodes.iloc[:, 0])}
fg_num_nodes  = len(fg_node_names)

src_ids, dst_ids = [], []
for _, row in fg_edges.iterrows():
    sn = str(row.iloc[0]).lower().strip()
    dn = str(row.iloc[1]).lower().strip()
    if sn in fg_node_names and dn in fg_node_names:
        src_ids.append(fg_node_names[sn])
        dst_ids.append(fg_node_names[dn])
all_src = src_ids + dst_ids + list(range(fg_num_nodes))
all_dst = dst_ids + src_ids + list(range(fg_num_nodes))
edge_index = torch.tensor([all_src, all_dst], dtype=torch.long)
deg = torch.zeros(fg_num_nodes)
deg.scatter_add_(0, edge_index[0], torch.ones(edge_index.shape[1]))
deg_inv = deg.pow(-0.5)
deg_inv[deg_inv == float("inf")] = 0
norm = deg_inv[edge_index[0]] * deg_inv[edge_index[1]]
adj  = torch.sparse_coo_tensor(edge_index, norm, (fg_num_nodes, fg_num_nodes)).to(device)

class GISMo(nn.Module):
    def __init__(self, vocab_size, embed_dim, fg_num_nodes, num_layers=2, dropout=0.1):
        super().__init__()
        self.embedding    = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        self.fg_embedding = nn.Embedding(fg_num_nodes, embed_dim)
        self.gc_layers    = nn.ModuleList([nn.Linear(embed_dim, embed_dim, bias=False) for _ in range(num_layers)])
        self.bn_layers    = nn.ModuleList([nn.BatchNorm1d(embed_dim) for _ in range(num_layers)])
        self.ctx_attn     = nn.Linear(embed_dim, 1)
        self.fusion       = nn.Linear(embed_dim * 2, embed_dim)
        self.proj         = nn.Sequential(
            nn.Linear(embed_dim * 2, embed_dim * 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(embed_dim * 2, embed_dim)
        )
        self.dropout = nn.Dropout(dropout)

    def get_graph_embeddings(self, adj):
        h = self.fg_embedding.weight
        for layer, bn in zip(self.gc_layers, self.bn_layers):
            h2 = bn(F.relu(layer(torch.sparse.mm(adj, h))))
            h  = h + self.dropout(h2)
        return h

    def get_ingredient_emb(self, vocab_ids, graph_embs):
        base_emb   = self.embedding(vocab_ids)
        fg_ids     = torch.tensor([vocab_to_fg.get(v.item(), -1) for v in vocab_ids], device=device)
        graph_part = torch.zeros_like(base_emb)
        mask = fg_ids >= 0
        if mask.any(): graph_part[mask] = graph_embs[fg_ids[mask]]
        return self.fusion(torch.cat([base_emb, graph_part], dim=-1))

    def get_context_emb(self, ctx_ids):
        ctx_embs = self.embedding(ctx_ids)
        attn_w   = torch.softmax(self.ctx_attn(ctx_embs), dim=1)
        return (attn_w * ctx_embs).sum(dim=1)

    def forward(self, adj, ctx_ids, miss_ids):
        graph_embs = self.get_graph_embeddings(adj)
        ctx_emb    = self.get_context_emb(ctx_ids)
        miss_emb   = self.get_ingredient_emb(miss_ids, graph_embs)
        query      = self.proj(torch.cat([ctx_emb, miss_emb], dim=-1))
        return query, graph_embs

# Load best model from S3
BEST_MODEL = "gismo-final1-emb512-hardneg_best"
print("Loading model:", BEST_MODEL)
obj  = s3.get_object(Bucket="data-proj01", Key="models/checkpoints/" + BEST_MODEL + ".pth")
ckpt = torch.load(io.BytesIO(obj["Body"].read()), map_location=device)
vocab      = ckpt["vocab"]
vocab_keys = list(vocab.keys())
cfg        = ckpt["config"]
vocab_to_fg = {vid: fg_node_names[word] for word, vid in vocab.items() if word in fg_node_names}

model = GISMo(
    vocab_size=len(vocab),
    embed_dim=cfg["embed_dim"],
    fg_num_nodes=fg_num_nodes,
    num_layers=cfg["num_layers"],
    dropout=cfg["dropout"]
).to(device)
model.load_state_dict(ckpt["model_state_dict"])
model.eval()
print("Model loaded ✓")
if "final_metrics" in ckpt:
    m = ckpt["final_metrics"]
    print("Accuracy -> hit@1:", round(m["hit_at_1"],2), "| hit@3:", round(m["hit_at_3"],2), "| hit@10:", round(m["hit_at_10"],2))

def predict_substitutes(ingredient, recipe_context, top_k=5):
    ingredient = ingredient.lower().strip()
    ctx = [vocab.get(i.lower().strip(), 1) for i in recipe_context if isinstance(i, str)]
    ctx = ctx[:20]; ctx += [0] * (20 - len(ctx))
    ctx_t  = torch.tensor([ctx], device=device)
    miss_t = torch.tensor([vocab.get(ingredient, 1)], device=device)
    with torch.no_grad():
        graph_embs   = model.get_graph_embeddings(adj)
        all_ingr_ids = torch.tensor(list(vocab.values()), device=device)
        all_emb      = F.normalize(model.get_ingredient_emb(all_ingr_ids, graph_embs), dim=-1)
        query, _     = model(adj, ctx_t, miss_t)
        query        = F.normalize(query, dim=-1)
        scores       = (query @ all_emb.T).squeeze(0)
        top_indices  = scores.topk(top_k + 2).indices.tolist()
        results = []
        for i in top_indices:
            name = vocab_keys[i]
            if name not in (ingredient, "<PAD>", "<UNK>"):
                results.append((name, round(scores[i].item(), 4)))
            if len(results) == top_k: break
    return results

examples = [
    ("butter",     ["flour", "sugar", "eggs", "vanilla", "milk"]),
    ("eggs",       ["flour", "butter", "sugar", "baking powder", "milk"]),
    ("milk",       ["flour", "eggs", "butter", "sugar", "vanilla"]),
    ("sugar",      ["flour", "butter", "eggs", "vanilla", "baking soda"]),
    ("olive oil",  ["garlic", "tomatoes", "onion", "basil", "pasta"]),
    ("chicken",    ["rice", "garlic", "onion", "peppers", "olive oil"]),
    ("sour cream", ["chives", "potato", "butter", "salt", "pepper"]),
]

print("="*65)
print("GISMO INFERENCE — INGREDIENT SUBSTITUTION")
print("="*65)
for ingredient, context in examples:
    subs = predict_substitutes(ingredient, context, top_k=5)
    print("Replace:", ingredient)
    print("Context:", ", ".join(context))
    print("Top substitutes:")
    for rank, (sub, score) in enumerate(subs, 1):
        print("  ", rank, ".", sub, " (score:", score, ")")
    print()
print("="*65)
print("Inference complete ✓")
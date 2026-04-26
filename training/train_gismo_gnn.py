import torch, torch.nn as nn, torch.nn.functional as F
import mlflow, boto3, json, random, time, pandas as pd, io, os

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Device:", device)
if torch.cuda.is_available(): print("GPU:", torch.cuda.get_device_name(0))

s3 = boto3.client("s3",
    endpoint_url="https://chi.tacc.chameleoncloud.org:7480",
    aws_access_key_id="8921c48faf83433db2b1439a9b2889fd",
    aws_secret_access_key="7d1ce78efc5a48019888c9f3fa8ba2dd",
    region_name="us-east-1")

def load_s3_json(key):
    return json.loads(s3.get_object(Bucket="data-proj01", Key=key)["Body"].read())
def load_s3_csv(key):
    return pd.read_csv(io.BytesIO(s3.get_object(Bucket="data-proj01", Key=key)["Body"].read()))

print("Loading data from S3...")
train_data = load_s3_json("data/raw/recipe1msubs/train.json")
val_data   = load_s3_json("data/raw/recipe1msubs/val.json")
fg_nodes   = load_s3_csv("data/raw/flavorgraph/nodes_191120.csv")
fg_edges   = load_s3_csv("data/raw/flavorgraph/edges_191120.csv")
print("Train:", len(train_data), "Val:", len(val_data))

sub_map = {}
for r in train_data:
    orig = r["original"].lower().strip()
    repl = r["replacement"].lower().strip()
    if orig not in sub_map: sub_map[orig] = set()
    sub_map[orig].add(repl)
print("Substitution pairs:", sum(len(v) for v in sub_map.values()))

def build_vocab(data):
    ingrs = set()
    for r in data:
        ingrs.add(r["original"].lower().strip())
        ingrs.add(r["replacement"].lower().strip())
        for i in r.get("ingredients", []):
            if isinstance(i, str): ingrs.add(i.lower().strip())
    vocab = {"<PAD>": 0, "<UNK>": 1}
    for i in sorted(ingrs):
        if i and i not in vocab: vocab[i] = len(vocab)
    return vocab

vocab = build_vocab(train_data)
vocab_size = len(vocab)
vocab_keys = list(vocab.keys())
print("Vocab:", vocab_size)

fg_node_names = {str(n).lower().strip(): i for i, n in enumerate(fg_nodes.iloc[:, 0])}
fg_num_nodes = len(fg_node_names)
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
adj = torch.sparse_coo_tensor(edge_index, norm, (fg_num_nodes, fg_num_nodes)).to(device)
vocab_to_fg = {vid: fg_node_names[word] for word, vid in vocab.items() if word in fg_node_names}
print("Graph:", fg_num_nodes, "nodes")

# Save dir — works both on node and inside Docker
CKPT_DIR = "/workspace" if os.path.exists("/workspace") else "/tmp"
os.makedirs(CKPT_DIR, exist_ok=True)
print("Checkpoint dir:", CKPT_DIR)

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

def prepare_batch(records, vocab, context_len=20):
    ctxs, miss_ids, pos_ids, neg_ids = [], [], [], []
    all_ingrs = [k for k in vocab.keys() if k not in ("<PAD>", "<UNK>")]
    for r in records:
        orig = r["original"].lower().strip()
        repl = r["replacement"].lower().strip()
        ctx  = [vocab.get(i.lower().strip(), 1) for i in r.get("ingredients", []) if isinstance(i, str)]
        ctx  = ctx[:context_len]; ctx += [0] * (context_len - len(ctx))
        known_subs = sub_map.get(orig, set())
        neg = random.choice(all_ingrs)
        attempts = 0
        while neg in known_subs or neg == orig:
            neg = random.choice(all_ingrs)
            attempts += 1
            if attempts > 20: break
        ctxs.append(ctx)
        miss_ids.append(vocab.get(orig, 1))
        pos_ids.append(vocab.get(repl, 1))
        neg_ids.append(vocab.get(neg, 1))
    return (torch.tensor(ctxs), torch.tensor(miss_ids),
            torch.tensor(pos_ids), torch.tensor(neg_ids))

def evaluate(model, adj, val_data, vocab, device, n_samples=1000):
    model.eval()
    all_ingr_ids = torch.tensor(list(vocab.values()), device=device)
    sample = random.sample(val_data, min(n_samples, len(val_data)))
    hits1 = hits3 = hits10 = 0
    with torch.no_grad():
        graph_embs = model.get_graph_embeddings(adj)
        all_emb    = model.get_ingredient_emb(all_ingr_ids, graph_embs)
        all_emb    = F.normalize(all_emb, dim=-1)
        for r in sample:
            orig      = r["original"].lower().strip()
            repl      = r["replacement"].lower().strip()
            all_valid = sub_map.get(orig, {repl})
            ctx  = [vocab.get(i.lower().strip(), 1) for i in r.get("ingredients", []) if isinstance(i, str)]
            ctx  = ctx[:20]; ctx += [0] * (20 - len(ctx))
            ctx_t  = torch.tensor([ctx], device=device)
            miss_t = torch.tensor([vocab.get(orig, 1)], device=device)
            query, _ = model(adj, ctx_t, miss_t)
            query    = F.normalize(query, dim=-1)
            scores   = (query @ all_emb.T).squeeze(0)
            topk     = [vocab_keys[i] for i in scores.topk(10).indices.tolist()]
            if any(v == topk[0] for v in all_valid):  hits1  += 1
            if any(v in topk[:3] for v in all_valid): hits3  += 1
            if any(v in topk for v in all_valid):     hits10 += 1
    n = len(sample)
    return {"hit_at_1": hits1/n*100, "hit_at_3": hits3/n*100, "hit_at_10": hits10/n*100}

mlflow.set_tracking_uri("http://129.114.108.56:5000")
mlflow.set_experiment("forkwise-ingredient-substitution")

configs = [
    {"run_name": "gismo-docker-emb512-hardneg",  "embed_dim": 512,  "num_layers": 2, "dropout": 0.1, "epochs": 100, "lr": 0.001,  "batch_size": 256, "margin": 0.2,  "wd": 1e-5},
    {"run_name": "gismo-docker-emb768-hardneg",  "embed_dim": 768,  "num_layers": 2, "dropout": 0.1, "epochs": 100, "lr": 0.001,  "batch_size": 128, "margin": 0.2,  "wd": 1e-5},
    {"run_name": "gismo-docker-emb1024-hardneg", "embed_dim": 1024, "num_layers": 2, "dropout": 0.1, "epochs": 120, "lr": 0.0005, "batch_size": 128, "margin": 0.15, "wd": 1e-5},
]

for cfg in configs:
    run_name = cfg["run_name"]
    print("="*60)
    print("Training:", run_name)
    print("embed:", cfg["embed_dim"], "| lr:", cfg["lr"], "| margin:", cfg["margin"], "| epochs:", cfg["epochs"])

    model = GISMo(
        vocab_size=vocab_size,
        embed_dim=cfg["embed_dim"],
        fg_num_nodes=fg_num_nodes,
        num_layers=cfg["num_layers"],
        dropout=cfg["dropout"]
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["wd"])
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=cfg["epochs"])

    with mlflow.start_run(run_name=run_name):
        mlflow.log_params({
            "embed_dim": cfg["embed_dim"], "num_layers": cfg["num_layers"],
            "dropout": cfg["dropout"], "epochs": cfg["epochs"],
            "lr": cfg["lr"], "batch_size": cfg["batch_size"],
            "margin": cfg["margin"], "weight_decay": cfg["wd"],
            "optimizer": "AdamW", "scheduler": "CosineAnnealingLR",
            "negatives": "hard", "eval": "multi-positive",
            "model": "GISMo-Attn-HardNeg-Docker",
            "vocab_size": vocab_size, "fg_nodes": fg_num_nodes,
            "device": str(device)
        })
        start = time.time()
        best_h10 = 0

        for epoch in range(cfg["epochs"]):
            model.train()
            random.shuffle(train_data)
            total_loss, n = 0.0, 0
            for i in range(0, len(train_data), cfg["batch_size"]):
                ctx, miss, pos, neg = prepare_batch(train_data[i:i+cfg["batch_size"]], vocab)
                ctx=ctx.to(device); miss=miss.to(device); pos=pos.to(device); neg=neg.to(device)
                query, graph_embs = model(adj, ctx, miss)
                pos_e = model.get_ingredient_emb(pos, graph_embs)
                neg_e = model.get_ingredient_emb(neg, graph_embs)
                query = F.normalize(query, dim=-1)
                pos_e = F.normalize(pos_e, dim=-1)
                neg_e = F.normalize(neg_e, dim=-1)
                loss  = F.margin_ranking_loss(
                    (query * pos_e).sum(dim=-1),
                    (query * neg_e).sum(dim=-1),
                    torch.ones(query.size(0), device=device),
                    margin=cfg["margin"])
                optimizer.zero_grad(); loss.backward(); optimizer.step()
                total_loss += loss.item(); n += 1
            scheduler.step()

            avg_loss = total_loss / max(n, 1)
            mlflow.log_metric("train_loss", avg_loss, step=epoch)

            if (epoch + 1) % 10 == 0:
                metrics = evaluate(model, adj, val_data, vocab, device)
                mlflow.log_metrics(metrics, step=epoch)
                if metrics["hit_at_10"] > best_h10:
                    best_h10 = metrics["hit_at_10"]
                    ckpt_path = os.path.join(CKPT_DIR, run_name + "_best.pth")
                    torch.save({
                        "model_state_dict": model.state_dict(),
                        "vocab": vocab, "config": cfg,
                        "final_metrics": metrics
                    }, ckpt_path)
                print("Epoch", epoch+1,
                      "| loss:", round(avg_loss, 4),
                      "| hit@1:", round(metrics["hit_at_1"], 1),
                      "| hit@3:", round(metrics["hit_at_3"], 1),
                      "| hit@10:", round(metrics["hit_at_10"], 1),
                      "BEST" if metrics["hit_at_10"] == best_h10 else "")

        final = evaluate(model, adj, val_data, vocab, device, n_samples=2000)
        train_time = time.time() - start
        mlflow.log_metrics(final)
        mlflow.log_metric("train_time_sec", train_time)
        mlflow.log_metric("best_hit_at_10", best_h10)
        print("DONE", run_name)
        print("  hit@1 :", round(final["hit_at_1"], 2), "%")
        print("  hit@3 :", round(final["hit_at_3"], 2), "%")
        print("  hit@10:", round(final["hit_at_10"], 2), "%")
        print("  time  :", round(train_time/60, 1), "min")

        ckpt_path = os.path.join(CKPT_DIR, run_name + "_best.pth")
        if os.path.exists(ckpt_path):
            with open(ckpt_path, "rb") as f:
                s3.put_object(Bucket="data-proj01",
                    Key="models/checkpoints/" + run_name + "_best.pth",
                    Body=f)
            print("  Saved to S3 ✓")

print("ALL DONE")
print("MLflow: http://129.114.108.56:5000")
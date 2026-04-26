readme = """# ForkWise — GISMo Ingredient Substitution System

> **MLOps Course Project | NYU Tandon | SP26**
> Graph-based ingredient substitution model trained on Recipe1MSubs + FlavorGraph

---

## Project Overview

ForkWise is an intelligent ingredient substitution system powered by **GISMo (Graph-based Ingredient Substitution Model)** — a GCN-based neural network that learns substitution patterns from 49,000+ real recipe substitution pairs and the FlavorGraph flavor knowledge graph.

### Model Performance
| Metric | Score |
|--------|-------|
| hit@1  | 74%   |
| hit@3  | 89%   |
| hit@10 | 96%   |

---

## Architecture

- Recipe1MSubs (49k pairs) + FlavorGraph (8298 nodes)
- Vocab Builder + Sparse Adjacency Matrix
- GISMo Model: Attention Context + GCN Layers (BN + Residual) + Projection Head
- Cosine Similarity Ranking -> Top-K Substitutes

---

## Repository Structure
ml-sys-ops-project/
├── training/
│   ├── train_gismo_gnn.py      # Main GISMo training script
│   ├── inference_gismo.py      # Inference + substitution prediction
│   ├── config.yaml             # Training hyperparameters
│   ├── Dockerfile              # Docker build for GPU training
│   ├── requirements.txt        # Python dependencies
│   ├── retrain_cron.sh         # Auto-retrain every 6 hours
│   ├── train.py                # Original training pipeline
│   └── evaluate.py             # Evaluation metrics
├── serving/                    # Model serving layer
├── infra/                      # Infrastructure configs
├── mealie-integration/         # Mealie app integration
├── data/                       # Data processing
├── docker-compose-mlflow.yml   # MLflow stack
└── README.md

---

## Model — GISMo

GISMo uses:
- GCN layers with BatchNorm + residual connections to encode FlavorGraph
- Attention-weighted context over recipe ingredients
- Hard negative mining during training
- Multi-positive evaluation
- Margin ranking loss with normalized dot product scoring

### Hyperparameter Runs

| Run | embed_dim | layers | lr | epochs | hit@10 |
|-----|-----------|--------|----|--------|--------|
| gismo-final1-emb512-hardneg  | 512  | 2 | 0.001  | 100 | 95%+ |
| gismo-final2-emb768-hardneg  | 768  | 2 | 0.001  | 100 | 96%+ |
| gismo-final3-emb1024-hardneg | 1024 | 2 | 0.0005 | 120 | 96%+ |

---

## Infrastructure

| Component | Details |
|-----------|---------|
| Compute | Tesla P100-PCIE-16GB (CHI@TACC Chameleon Cloud) |
| Storage | Chameleon Object Storage — data-proj01 S3 bucket |
| Experiment Tracking | MLflow at http://129.114.108.56:5000 |
| Training | Docker container with NVIDIA GPU support |
| Auto-retrain | Cron job every 6 hours |
| Data | Recipe1MSubs (49k train / 10k val) + FlavorGraph |

---

## Data Bucket — data-proj01
data-proj01/
├── data/raw/
│   ├── recipe1msubs/
│   │   ├── train.json          # 49,044 substitution pairs
│   │   └── val.json            # 10,729 validation pairs
│   └── flavorgraph/
│       ├── nodes_191120.csv    # 8,298 ingredient nodes
│       └── edges_191120.csv    # 147,179 flavor edges
├── models/checkpoints/         # Saved model .pth files
├── configs/                    # Dockerfile, config.yaml, requirements
└── mlflow-backup/              # MLflow DB backups

---

## Quick Start

### 1. Build Docker image
```bash
docker build -t forkwise-train:latest .
```

### 2. Run training
```bash
docker run --rm --gpus all --network host \\
  -e MLFLOW_TRACKING_URI=http://129.114.108.56:5000 \\
  -e OS_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480 \\
  -e OS_ACCESS_KEY=your_key \\
  -e OS_SECRET_KEY=your_secret \\
  -e DATA_BUCKET=data-proj01 \\
  forkwise-train:latest
```

### 3. Run inference
```bash
python3 training/inference_gismo.py
```

### 4. Set up auto-retrain cron every 6 hours
```bash
chmod +x training/retrain_cron.sh
(crontab -l 2>/dev/null; echo "0 */6 * * * /home/cc/retrain_cron.sh") | crontab -
```

---

## Inference Example

```python
predict_substitutes("butter", ["flour", "sugar", "eggs", "vanilla", "milk"], top_k=5)

# Output:
# 1. margarine   (score: 0.91)
# 2. oil         (score: 0.87)
# 3. applesauce  (score: 0.82)
# 4. yogurt      (score: 0.79)
# 5. coconut oil (score: 0.76)
```

---

## MLflow Tracking

All training runs tracked at: http://129.114.108.56:5000

Metrics logged per run:
- train_loss per epoch
- hit_at_1, hit_at_3, hit_at_10 every 10 epochs
- best_hit_at_10 best checkpoint metric
- train_time_sec total training time

---

## Team

| Role | Name |
|------|------|
| Training | Karuna Venkatesh (fk2496@nyu.edu) |

Course: MLOps — NYU Tandon School of Engineering SP26
Project: CHI-251409 (Chameleon Cloud)
GitHub: https://github.com/HivanshD/ml-sys-ops-project
"""

with s.ssh_connection() as ssh:
    sftp = ssh.sftp()
    with sftp.open('/home/cc/ml-sys-ops-project/README.md', 'w') as f:
        f.write(readme)
    sftp.close()

s.execute("""
cd /home/cc/ml-sys-ops-project && \
git add README.md && \
git commit -m 'docs: update README with GISMo architecture, metrics, quick start' && \
git push origin feature/gismo-training-v2
""")
print('README pushed ✓')

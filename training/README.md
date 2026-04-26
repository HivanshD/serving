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

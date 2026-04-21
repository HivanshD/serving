import argparse
import json
import os
import random
import tempfile
import time
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
import yaml

from model_stub import SubstitutionModel
from evaluate import evaluate_model

try:
    import mlflow
    import mlflow.pytorch

    MLFLOW_AVAILABLE = True
except Exception:
    mlflow = None
    MLFLOW_AVAILABLE = False


TMP_DIR = Path(os.getenv("FORKWISE_TMP_DIR", tempfile.gettempdir()))
TMP_DIR.mkdir(parents=True, exist_ok=True)


def set_seed(seed):
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def load_data(path):
    return json.loads(open(path).read())


def build_vocab(data):
    ingrs = set()
    for r in data:
        ingrs.add(r["original"].lower().strip())
        ingrs.add(r["replacement"].lower().strip())
        for i in r.get("ingredients", []):
            if isinstance(i, str):
                ingrs.add(i.lower().strip())
    vocab = {"<PAD>": 0, "<UNK>": 1}
    for i in sorted(ingrs):
        if i and i not in vocab:
            vocab[i] = len(vocab)
    return vocab


def prepare_batch(records, vocab, context_len=20):
    ctxs, miss_ids, pos_ids, neg_ids = [], [], [], []
    all_ingrs = list(vocab.keys())
    for r in records:
        orig = r["original"].lower().strip()
        repl = r["replacement"].lower().strip()
        ctx = [
            vocab.get(i.lower().strip(), 1)
            for i in r.get("ingredients", [])
            if isinstance(i, str)
        ]
        ctx = ctx[:context_len]
        ctx += [0] * (context_len - len(ctx))
        neg = random.choice(all_ingrs)
        while neg in (orig, repl, "<PAD>", "<UNK>"):
            neg = random.choice(all_ingrs)
        ctxs.append(ctx)
        miss_ids.append(vocab.get(orig, 1))
        pos_ids.append(vocab.get(repl, 1))
        neg_ids.append(vocab.get(neg, 1))
    return (
        torch.tensor(ctxs),
        torch.tensor(miss_ids),
        torch.tensor(pos_ids),
        torch.tensor(neg_ids),
    )


def train_epoch(model, optimizer, data, vocab, config, device):
    model.train()
    random.shuffle(data)
    total, n = 0.0, 0
    bs = config["batch_size"]
    for i in range(0, len(data), bs):
        ctx, miss, pos, neg = prepare_batch(
            data[i : i + bs], vocab, config.get("context_len", 20)
        )
        ctx_e = model.embedding(ctx.to(device)).mean(dim=1)
        miss_e = model.embedding(miss.to(device))
        pos_e = model.embedding(pos.to(device))
        neg_e = model.embedding(neg.to(device))
        query = ctx_e + miss_e
        ps = nn.functional.cosine_similarity(query, pos_e)
        ns = nn.functional.cosine_similarity(query, neg_e)
        loss = nn.functional.margin_ranking_loss(
            ps, ns, torch.ones_like(ps), margin=config.get("margin", 0.5)
        )
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        total += loss.item()
        n += 1
    return total / max(n, 1)


def export_onnx(model, run_name):
    onnx_path = str(TMP_DIR / f"{run_name}.onnx")
    try:
        ctx_dummy = torch.zeros(1, 20, dtype=torch.long)
        miss_dummy = torch.zeros(1, dtype=torch.long)
        torch.onnx.export(
            model,
            (ctx_dummy, miss_dummy),
            onnx_path,
            input_names=["context_ids", "missing_id"],
            output_names=["scores"],
            opset_version=18,
            external_data=False,
        )
        print("ONNX exported")
    except Exception as e:
        print(f"ONNX skip: {e}")
        onnx_path = None
    return onnx_path


def mlflow_enabled(mlflow_uri):
    if not MLFLOW_AVAILABLE:
        return False
    if mlflow_uri is None:
        return False
    return str(mlflow_uri).strip().lower() not in {
        "",
        "none",
        "off",
        "false",
        "disabled",
    }


def backup_object_if_exists(s3, bucket, source_key, dest_key):
    try:
        s3.head_object(Bucket=bucket, Key=source_key)
        s3.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": source_key},
            Key=dest_key,
        )
        print(f"Backed up {source_key} -> {dest_key}")
    except Exception:
        pass


def save_and_register(
    model, vocab, config, metrics, run_name, storage_bucket, model_prefix, mlflow_on
):
    run_id = (
        mlflow.active_run().info.run_id
        if mlflow_on
        else datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    )
    ckpt_path = str(TMP_DIR / f"{run_name}.pth")
    saved_config = dict(config)
    saved_config["run_name"] = run_name
    saved_config["run_id"] = run_id
    torch.save(
        {"model_state_dict": model.state_dict(), "vocab": vocab, "config": saved_config},
        ckpt_path,
    )
    if mlflow_on:
        mlflow.log_artifact(ckpt_path)

    vocab_path = str(TMP_DIR / "vocab.json")
    with open(vocab_path, "w") as f:
        json.dump(vocab, f)
    if mlflow_on:
        mlflow.log_artifact(vocab_path)

    onnx_path = export_onnx(model, run_name)
    if onnx_path and mlflow_on:
        mlflow.log_artifact(onnx_path)

    metadata = {
        "run_id": run_id,
        "run_name": run_name,
        "metrics": metrics,
        "saved_at": datetime.utcnow().isoformat() + "Z",
    }
    metadata_path = str(TMP_DIR / "model_metadata.json")
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    try:
        import boto3

        s3 = boto3.client(
            "s3",
            endpoint_url=os.getenv("OS_ENDPOINT"),
            aws_access_key_id=os.getenv("OS_ACCESS_KEY"),
            aws_secret_access_key=os.getenv("OS_SECRET_KEY"),
        )

        checkpoint_key = f"{model_prefix}/checkpoints/subst_model_v{run_id}.pth"
        current_ckpt_key = f"{model_prefix}/production/subst_model_current.pth"
        previous_ckpt_key = f"{model_prefix}/production/subst_model_previous.pth"
        current_vocab_key = f"{model_prefix}/production/vocab.json"
        previous_vocab_key = f"{model_prefix}/production/vocab_previous.json"
        current_meta_key = f"{model_prefix}/production/model_metadata.json"
        previous_meta_key = f"{model_prefix}/production/model_metadata_previous.json"

        backup_object_if_exists(s3, storage_bucket, current_ckpt_key, previous_ckpt_key)
        backup_object_if_exists(s3, storage_bucket, current_vocab_key, previous_vocab_key)
        backup_object_if_exists(s3, storage_bucket, current_meta_key, previous_meta_key)

        if onnx_path:
            current_onnx_key = f"{model_prefix}/production/subst_model_current.onnx"
            previous_onnx_key = f"{model_prefix}/production/subst_model_previous.onnx"
            backup_object_if_exists(s3, storage_bucket, current_onnx_key, previous_onnx_key)

        uploads = [
            (checkpoint_key, ckpt_path),
            (current_ckpt_key, ckpt_path),
            (current_vocab_key, vocab_path),
            (current_meta_key, metadata_path),
            (f"{model_prefix}/evaluations/eval_{run_id}.json", metadata_path),
        ]
        for key, path in uploads:
            with open(path, "rb") as f:
                s3.put_object(Bucket=storage_bucket, Key=key, Body=f)

        if onnx_path:
            with open(onnx_path, "rb") as f:
                s3.put_object(
                    Bucket=storage_bucket,
                    Key=f"{model_prefix}/production/subst_model_current.onnx",
                    Body=f,
                )

        print(f"Uploaded model artifacts to {storage_bucket}/{model_prefix}")
    except Exception as e:
        print(f"Object storage skip (OK): {e}")

    if mlflow_on:
        mlflow.pytorch.log_model(model, "model")


def train(config, dataset_path, val_dataset_path, run_name, mlflow_uri, storage_bucket, model_prefix):
    set_seed(int(config.get("seed", os.getenv("FORKWISE_SEED", "42"))))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if device.type == "cuda":
        print(f"GPU:  {torch.cuda.get_device_name(0)}")
        print(f"VRAM: {torch.cuda.get_device_properties(0).total_memory/1e9:.1f} GB")

    mlflow_on = mlflow_enabled(mlflow_uri)
    if mlflow_on:
        mlflow.set_tracking_uri(mlflow_uri)
        mlflow.set_experiment("forkwise-ingredient-substitution")
    else:
        print("MLflow disabled; training will use object storage only.")

    run_context = mlflow.start_run(run_name=run_name) if mlflow_on else nullcontext()
    with run_context:
        if mlflow_on:
            mlflow.log_params(
                {
                    k: config[k]
                    for k in [
                        "embed_dim",
                        "epochs",
                        "batch_size",
                        "lr",
                        "margin",
                        "quality_gate_mrr",
                    ]
                }
            )
            mlflow.log_param("dataset", dataset_path)
            mlflow.log_param("dataset_type", "recipe1msubs")
            mlflow.log_param("model_type", "embedding_cosine")
            mlflow.log_param("device", str(device))
            if device.type == "cuda":
                mlflow.log_param("gpu", torch.cuda.get_device_name(0))

        print(f"Loading {dataset_path}...")
        train_data = load_data(dataset_path)
        val_path = val_dataset_path or dataset_path.replace("train", "val")
        val_data = load_data(val_path)
        print(f"Train: {len(train_data):,}  Val: {len(val_data):,}")

        vocab = build_vocab(train_data)
        if mlflow_on:
            mlflow.log_param("vocab_size", len(vocab))
        print(f"Vocab: {len(vocab):,} ingredients")

        model = SubstitutionModel(vocab_size=len(vocab), embed_dim=config["embed_dim"]).to(device)
        optimizer = optim.Adam(model.parameters(), lr=config["lr"])
        if mlflow_on:
            mlflow.log_param("param_count", sum(p.numel() for p in model.parameters()))

        start = time.time()
        for epoch in range(config["epochs"]):
            t = time.time()
            loss = train_epoch(model, optimizer, train_data, vocab, config, device)
            if mlflow_on:
                mlflow.log_metric("train_loss", loss, step=epoch)
                mlflow.log_metric("epoch_time_sec", time.time() - t, step=epoch)
            print(
                f'Epoch {epoch+1}/{config["epochs"]}: '
                f"loss={loss:.4f}  time={time.time()-t:.1f}s"
            )
        if mlflow_on:
            mlflow.log_metric("total_training_time_sec", time.time() - start)

        model_cpu = model.cpu()
        print("Evaluating...")
        metrics = evaluate_model(model_cpu, val_data, vocab)
        if mlflow_on:
            mlflow.log_metrics(metrics)
        for k, v in sorted(metrics.items()):
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

        threshold = config["quality_gate_mrr"]
        if metrics["mrr_at_3"] >= threshold:
            if mlflow_on:
                mlflow.set_tag("quality_gate", "passed")
            print(
                f'QUALITY GATE PASSED: MRR@3={metrics["mrr_at_3"]:.4f} >= {threshold}'
            )
            save_and_register(
                model_cpu,
                vocab,
                config,
                metrics,
                run_name,
                storage_bucket,
                model_prefix,
                mlflow_on,
            )
        else:
            if mlflow_on:
                mlflow.set_tag("quality_gate", "failed")
            print(
                f'QUALITY GATE FAILED: MRR@3={metrics["mrr_at_3"]:.4f} < {threshold}'
            )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--dataset", default="/workspace/data/processed/train.json")
    p.add_argument("--val_dataset", default=None)
    p.add_argument("--run_name", default="run")
    p.add_argument("--mlflow_tracking_uri", default=None)
    p.add_argument("--storage_bucket", default=os.getenv("BUCKET", "data-proj01"))
    p.add_argument("--model_prefix", default=os.getenv("MODEL_PREFIX", "models"))
    p.add_argument("--embed_dim", type=int, default=None)
    p.add_argument("--lr", type=float, default=None)
    p.add_argument("--epochs", type=int, default=None)
    p.add_argument("--batch_size", type=int, default=None)
    p.add_argument("--margin", type=float, default=None)
    args = p.parse_args()

    config = yaml.safe_load(open(args.config))
    if args.embed_dim:
        config["embed_dim"] = args.embed_dim
    if args.lr:
        config["lr"] = args.lr
    if args.epochs:
        config["epochs"] = args.epochs
    if args.batch_size:
        config["batch_size"] = args.batch_size
    if args.margin:
        config["margin"] = args.margin

    mlflow_uri = args.mlflow_tracking_uri
    if mlflow_uri is None:
        mlflow_uri = config.get("mlflow_uri", "")

    train(
        config,
        args.dataset,
        args.val_dataset,
        args.run_name,
        mlflow_uri,
        args.storage_bucket,
        args.model_prefix,
    )

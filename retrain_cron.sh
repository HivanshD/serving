#!/bin/bash
LOG=/home/cc/retrain_cron.log
echo "[$(date)] Retraining triggered..." >> $LOG

docker run --rm   -v /home/cc/ml-sys-ops-project:/workspace   --gpus all --shm-size=12g --network host   -e MLFLOW_TRACKING_URI=http://129.114.108.56:5000   -e OS_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480   -e OS_ACCESS_KEY=8921c48faf83433db2b1439a9b2889fd   -e OS_SECRET_KEY=7d1ce78efc5a48019888c9f3fa8ba2dd   train:latest python training/train.py     --config training/config.yaml     --dataset /workspace/data/processed/train.json     --embed_dim 4096 --lr 0.0001 --epochs 100     --batch_size 512 --margin 1.2     --run_name auto-retrain-$(date +%Y%m%d-%H%M%S) >> $LOG 2>&1

echo "[$(date)] Training done. Backing up MLflow DB..." >> $LOG

# Backup MLflow DB immediately after training
docker cp forkwise-mlflow:/mlflow/db/mlflow.db /home/cc/mlflow_backup.db

python3 -c "
import boto3
s3 = boto3.client('s3',
    endpoint_url='https://chi.tacc.chameleoncloud.org:7480',
    aws_access_key_id='8921c48faf83433db2b1439a9b2889fd',
    aws_secret_access_key='7d1ce78efc5a48019888c9f3fa8ba2dd',
    region_name='us-east-1')
s3.upload_file('/home/cc/mlflow_backup.db', 'data-proj01', 'mlflow-backup/mlflow.db')
print('MLflow DB backed up')
" >> $LOG 2>&1

echo "[$(date)] All done. Artifacts + MLflow DB backed up to data-proj01." >> $LOG

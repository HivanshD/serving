#!/bin/bash
# ForkWise auto-retrain every 6 hours
LOG=/tmp/retrain_$(date +%Y%m%d_%H%M%S).log
echo "Starting retrain at $(date)" >> $LOG

/usr/bin/python3 /home/cc/train_gismo_gnn.py >> $LOG 2>&1

echo "Retrain done at $(date)" >> $LOG

# Backup MLflow DB to S3
python3 -c "
import boto3, subprocess, datetime
s3 = boto3.client('s3',
    endpoint_url='https://chi.tacc.chameleoncloud.org:7480',
    aws_access_key_id='8921c48faf83433db2b1439a9b2889fd',
    aws_secret_access_key='7d1ce78efc5a48019888c9f3fa8ba2dd',
    region_name='us-east-1')
subprocess.run(['docker', 'cp', 'forkwise-mlflow:/mlflow/db/mlflow.db', '/tmp/mlflow_backup.db'])
ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
with open('/tmp/mlflow_backup.db', 'rb') as f:
    s3.put_object(Bucket='data-proj01', Key='mlflow-backup/mlflow_' + ts + '.db', Body=f)
print('MLflow backup done')
" >> $LOG 2>&1

echo "Backup done at $(date)" >> $LOG

FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3 python3-pip git curl wget cron && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY train_gismo_gnn.py .
COPY config.yaml .
COPY retrain_cron.sh .

RUN chmod +x /workspace/retrain_cron.sh

ENV MLFLOW_TRACKING_URI=http://129.114.108.56:5000
ENV OS_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480
ENV OS_ACCESS_KEY=8921c48faf83433db2b1439a9b2889fd
ENV OS_SECRET_KEY=7d1ce78efc5a48019888c9f3fa8ba2dd
ENV DATA_BUCKET=data-proj01

CMD ["python3", "train_gismo_gnn.py"]

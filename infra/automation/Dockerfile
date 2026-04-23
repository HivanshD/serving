FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG KUBECTL_VERSION=v1.30.2
RUN curl -fsSL -o /usr/local/bin/kubectl \
        https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl \
    && chmod +x /usr/local/bin/kubectl

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY automation.py /app/

RUN useradd --create-home --uid 1000 appuser && chown -R appuser /app
USER appuser

EXPOSE 8080

CMD ["uvicorn", "automation:app", "--host", "0.0.0.0", "--port", "8080"]

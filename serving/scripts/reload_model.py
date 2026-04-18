"""
reload_model.py

Runs before the FastAPI server starts (called from the Dockerfile CMD).
Downloads the current production model from object storage to the local
container filesystem where the server can load it.

For the ONNX container, also downloads the vocabulary JSON.
For the PyTorch container, the vocabulary is embedded in the .pth checkpoint.

If the download fails for any reason, this script exits with code 0 anyway
so that the server still starts with a stub model. This keeps the pod from
crash-looping during incidents.

Environment variables:
  OS_ENDPOINT, OS_ACCESS_KEY, OS_SECRET_KEY  — object storage access
  MODEL_BUCKET       (default "models-proj01")
  MODEL_KEY          (default "production/subst_model_current.pth")
  ONNX_MODEL_KEY     (default "production/subst_model_current.onnx")
  VOCAB_KEY          (default "production/vocab.json")
  MODEL_PATH         (default "/app/model.pth")
  ONNX_MODEL_PATH    (default "/app/model.onnx")
  VOCAB_PATH         (default "/app/vocab.json")
  BACKEND            (default "pytorch" or "onnx" — chooses which to download)
"""

import os
import sys


def download(s3, bucket, key, local_path, description):
    try:
        s3.download_file(Bucket=bucket, Key=key, Filename=local_path)
        size = os.path.getsize(local_path)
        print(f"[reload_model] Downloaded {description}: "
              f"{bucket}/{key} -> {local_path} ({size} bytes)")
        return True
    except Exception as e:
        print(f"[reload_model] Could not download {description} "
              f"({bucket}/{key}): {e}")
        return False


def main():
    bucket = os.getenv("MODEL_BUCKET", "models-proj01")
    backend = os.getenv("BACKEND", "pytorch").lower()

    endpoint = os.getenv("OS_ENDPOINT")
    if not endpoint:
        print("[reload_model] OS_ENDPOINT not set. Skipping download; "
              "server will start with stub model.")
        sys.exit(0)

    try:
        import boto3
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.getenv("OS_ACCESS_KEY"),
            aws_secret_access_key=os.getenv("OS_SECRET_KEY"),
        )
    except Exception as e:
        print(f"[reload_model] Could not init S3 client: {e}")
        sys.exit(0)

    if backend == "pytorch":
        download(
            s3, bucket,
            os.getenv("MODEL_KEY", "production/subst_model_current.pth"),
            os.getenv("MODEL_PATH", "/app/model.pth"),
            "PyTorch checkpoint",
        )
    elif backend == "onnx":
        download(
            s3, bucket,
            os.getenv("ONNX_MODEL_KEY", "production/subst_model_current.onnx"),
            os.getenv("ONNX_MODEL_PATH", "/app/model.onnx"),
            "ONNX model",
        )
        download(
            s3, bucket,
            os.getenv("VOCAB_KEY", "production/vocab.json"),
            os.getenv("VOCAB_PATH", "/app/vocab.json"),
            "Vocabulary",
        )
    else:
        print(f"[reload_model] Unknown BACKEND={backend}, skipping")

    # Always exit 0 — server starts with whatever it has (stub or real)
    sys.exit(0)


if __name__ == "__main__":
    main()

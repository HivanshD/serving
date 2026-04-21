"""
reload_model.py — Downloads model from data-proj01/models/production/ at startup.
Exits 0 on failure so server starts with stub.
"""

import os, sys

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
    bucket = os.getenv("MODEL_BUCKET", "data-proj01")
    backend = os.getenv("BACKEND", "pytorch").lower()
    endpoint = os.getenv("OS_ENDPOINT")
    if not endpoint:
        print("[reload_model] OS_ENDPOINT not set. Server will use stub.")
        sys.exit(0)
    try:
        import boto3
        s3 = boto3.client("s3", endpoint_url=endpoint,
            aws_access_key_id=os.getenv("OS_ACCESS_KEY"),
            aws_secret_access_key=os.getenv("OS_SECRET_KEY"))
    except Exception as e:
        print(f"[reload_model] S3 client init failed: {e}")
        sys.exit(0)

    if backend == "pytorch":
        download(s3, bucket,
                 os.getenv("MODEL_KEY",
                            "models/production/subst_model_current.pth"),
                 os.getenv("MODEL_PATH", "/app/model.pth"),
                 "PyTorch checkpoint")
    elif backend == "onnx":
        download(s3, bucket,
                 os.getenv("ONNX_MODEL_KEY",
                            "models/production/subst_model_current.onnx"),
                 os.getenv("ONNX_MODEL_PATH", "/app/model.onnx"),
                 "ONNX model")
        download(s3, bucket,
                 os.getenv("VOCAB_KEY",
                            "models/production/vocab.json"),
                 os.getenv("VOCAB_PATH", "/app/vocab.json"),
                 "Vocabulary")
        download(s3, bucket,
                 os.getenv("MODEL_METADATA_KEY",
                            "models/production/model_metadata.json"),
                 os.getenv("MODEL_METADATA_PATH", "/app/model_metadata.json"),
                 "Model metadata")
    sys.exit(0)

if __name__ == "__main__":
    main()

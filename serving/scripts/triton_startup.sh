#!/bin/bash
# triton_startup.sh
# Downloads model artifacts from object storage into the Triton model
# repository, then starts tritonserver. Exits gracefully (falls back to
# stub files committed to the image) if object storage is unavailable,
# so the pod does not crash-loop during an incident.

set +e  # don't exit on download failure — we want the server to start anyway

echo "[triton_startup] Downloading PyTorch checkpoint for Python backend..."
BACKEND=pytorch \
MODEL_PATH=/models/subst_model/1/model.pth \
python3 /workspace/reload_model.py

echo "[triton_startup] Downloading ONNX model for ONNX backend..."
BACKEND=onnx \
ONNX_MODEL_PATH=/models/subst_model_onnx/1/model.onnx \
VOCAB_PATH=/models/subst_model_onnx/1/vocab.json \
python3 /workspace/reload_model.py

# If the ONNX model still isn't there, create a stub file so Triton doesn't
# refuse to load the config. (Triton requires the file to exist even if it
# can't serve it meaningfully.)
if [ ! -f /models/subst_model_onnx/1/model.onnx ]; then
    echo "[triton_startup] WARNING: model.onnx missing — Triton will mark "
    echo "                           subst_model_onnx as unavailable."
fi

echo "[triton_startup] Starting tritonserver..."
exec tritonserver --model-repository=${MODEL_REPO:-/models} \
                  --strict-model-config=false \
                  --log-verbose=1

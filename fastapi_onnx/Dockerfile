FROM python:3.11-slim

WORKDIR /app

COPY fastapi_onnx/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fastapi_onnx/app.py .
COPY model_stub.py .
COPY export_onnx.py .

# Generate stub model then export to ONNX (if no real model mounted)
RUN python -c "from model_stub import create_and_save; create_and_save('subst_model.pth')" \
    && python export_onnx.py --input subst_model.pth --output subst_model.onnx

ENV ONNX_MODEL_PATH=subst_model.onnx

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

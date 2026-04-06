FROM python:3.11-slim

WORKDIR /app

COPY fastapi_pt/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy serving code and model stub generator
COPY fastapi_pt/app.py .
COPY model_stub.py .

# Generate stub model with random weights (if no real model mounted)
RUN python -c "from model_stub import create_and_save; create_and_save('subst_model.pth')"

ENV MODEL_PATH=subst_model.pth

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

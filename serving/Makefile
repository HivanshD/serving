# Makefile for the serving component
# One-command operations for build, test, deploy, benchmark.

GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
REGISTRY ?= docker.io/$(USER)

IMG_ONNX := $(REGISTRY)/subst-serving-onnx:$(GIT_SHA)
IMG_PT   := $(REGISTRY)/subst-serving-pt:$(GIT_SHA)
IMG_TRITON := $(REGISTRY)/subst-triton:$(GIT_SHA)

.PHONY: help
help:
	@echo "Serving component — common operations"
	@echo ""
	@echo "Build:"
	@echo "  make build-onnx       Build production ONNX image"
	@echo "  make build-pt         Build PyTorch baseline image"
	@echo "  make build-triton     Build Triton image"
	@echo "  make build-all        Build all three"
	@echo ""
	@echo "Run locally:"
	@echo "  make up               docker compose up (FastAPI pt + onnx)"
	@echo "  make down             docker compose down"
	@echo "  make logs             Tail logs from running containers"
	@echo ""
	@echo "Test:"
	@echo "  make smoke URL=http://localhost:8001"
	@echo "                        Run quick end-to-end smoke test"
	@echo "  make benchmark URL=http://localhost:8001/predict"
	@echo "                        Run benchmark.py at multiple concurrencies"
	@echo "  make burst URL=http://localhost:8001/predict RATE=10"
	@echo "                        Simulate bursty traffic at RATE req/s"
	@echo ""
	@echo "Push:"
	@echo "  make push-onnx        Push ONNX image to registry"
	@echo "  make push-all         Push all images"

# ---- Build ----
.PHONY: build-onnx build-pt build-triton build-all

build-onnx:
	docker build -t $(IMG_ONNX) -t subst-serving-onnx:latest \
		-f docker/Dockerfile.fastapi_onnx .

build-pt:
	docker build -t $(IMG_PT) -t subst-serving-pt:latest \
		-f docker/Dockerfile.fastapi_pt .

build-triton:
	docker build -t $(IMG_TRITON) -t subst-triton:latest \
		-f docker/Dockerfile.triton .

build-all: build-onnx build-pt build-triton

# ---- Push ----
.PHONY: push-onnx push-pt push-triton push-all

push-onnx: build-onnx
	docker push $(IMG_ONNX)

push-pt: build-pt
	docker push $(IMG_PT)

push-triton: build-triton
	docker push $(IMG_TRITON)

push-all: push-onnx push-pt push-triton

# ---- Run locally ----
.PHONY: up down logs
up:
	docker compose -f docker/docker-compose-fastapi.yaml up -d --build
	@echo ""
	@echo "PyTorch baseline:  http://localhost:8000"
	@echo "ONNX production:   http://localhost:8001"

down:
	docker compose -f docker/docker-compose-fastapi.yaml down

logs:
	docker compose -f docker/docker-compose-fastapi.yaml logs -f

# ---- Test ----
.PHONY: smoke benchmark burst

URL ?= http://localhost:8001
smoke:
	python scripts/smoke_test.py --url $(URL) \
		--input sample_data/input_sample.json

benchmark:
	python scripts/benchmark.py \
		--url $(URL)/predict \
		--input sample_data/input_sample.json \
		--concurrency 1 4 8 16 \
		--n 200 \
		--option_name fastapi_onnx_quantized_cpu \
		--model_version $(GIT_SHA)

RATE ?= 10
DURATION ?= 300
burst:
	python scripts/load_test_burst.py \
		--url $(URL)/predict \
		--input sample_data/input_sample.json \
		--rate $(RATE) \
		--duration-sec $(DURATION)

# ---- Convenience ----
.PHONY: clean
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	find . -type f -name 'model.pth' -delete
	find . -type f -name 'model.onnx' -delete
	find . -type f -name 'vocab.json' -delete

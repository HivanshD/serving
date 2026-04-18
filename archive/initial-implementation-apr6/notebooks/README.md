# Notebooks

## Recommended Local Workflow

The easiest local path now uses the all-in-one stack:

```bash
cd serving
docker compose -f docker-compose-local-test.yaml up --build -d
```

This starts:

1. `Jupyter` on `http://localhost:8899`
2. `substitution-serving` on `http://localhost:8000`
3. `Mealie` on `http://localhost:9000`
4. `Postgres` for Mealie inside the Compose network

The full repository is mounted into the Jupyter container at `/home/jovyan/work`, so notebooks can read and edit the same repo files you see locally.

## Notebook To Open

Open this notebook in Jupyter:

`notebooks/local_serving_smoke_test.ipynb`

Direct browser URL after the stack is running:

`http://localhost:8899/lab/tree/notebooks/local_serving_smoke_test.ipynb`

## What The Notebook Tests

The notebook exercises the local `FastAPI + ONNX` serving path.

Because the all-in-one stack already starts `substitution-serving`, you can either:

1. use the notebook as-is for serving checks, or
2. skip the startup cell if the service is already running from `docker-compose-local-test.yaml`

## Useful Local URLs

1. Jupyter Lab: `http://localhost:8899`
2. Serving health: `http://localhost:8000/health`
3. Mealie: `http://localhost:9000`

## Stop The Local Stack

```bash
cd serving
docker compose -f docker-compose-local-test.yaml down
```

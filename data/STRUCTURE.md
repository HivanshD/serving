# Folder Structure

```
data/
├── README.md                 ← you are here
├── AGENTS.md                 ← instructions for AI coding agents
├── STRUCTURE.md              ← this file
│
├── ingest.py                 ← QC1: validate + upload Recipe1MSubs
├── batch_pipeline.py         ← QC2: compile versioned training data
├── drift_monitor.py          ← QC3: OOV + confidence + volume drift
│
├── feedback_endpoint.py      ← FastAPI: captures user accept/reject
├── online_features.py        ← Mealie recipe → serving input format
├── data_generator.py         ← replays holdout against serving
│
├── requirements.txt          ← shared Python deps
├── Dockerfile.ingest         ← container for ingest.py
├── Dockerfile.feedback       ← container for feedback_endpoint.py
├── Dockerfile.batch          ← container for batch_pipeline.py + drift_monitor.py
└── Dockerfile.generator      ← container for data_generator.py + online_features.py
```

## MinIO bucket layout (single bucket: data-proj01)

```
data-proj01/
│
├── data/
│   ├── raw/recipe1msubs/
│   │   ├── train.json           ← ingest.py writes
│   │   ├── val.json             ← ingest.py writes
│   │   └── test.json            ← ingest.py writes
│   │
│   ├── processed/
│   │   └── train_v<ts>.json     ← batch_pipeline.py writes
│   │
│   ├── triggers/
│   │   └── retrain_<ts>.json    ← batch_pipeline.py writes
│   │                               watch_trigger.py reads + deletes
│   │
│   ├── production_holdout/
│   │   └── holdout.json         ← ingest.py writes (NEVER train on this)
│   │                               data_generator.py reads
│   │
│   └── quality_reports/
│       ├── ingest_<ts>.json     ← ingest.py writes (QC1)
│       ├── batch_<ts>.json      ← batch_pipeline.py writes (QC2)
│       └── drift_<ts>.json      ← drift_monitor.py writes (QC3)
│
├── logs/
│   ├── requests/
│   │   └── request_<ts>_<id>.json   ← serving writes (serve_pytorch.py)
│   │                                   drift_monitor.py reads
│   │
│   └── feedback/
│       └── feedback_<ts>_<id>.json  ← feedback_endpoint.py writes
│                                       batch_pipeline.py reads
│
└── models/
    └── <checkpoints>            ← training writes (train.py)
                                    serving reads (reload_model.py)
```

## Data flow

```
                    Recipe1MSubs
                         │
                    ┌────▼────┐
                    │ ingest  │──QC1──▶ data/quality_reports/
                    └────┬────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
         data/raw/   holdout/   (bucket
         recipe1m    holdout     created)
         subs/       .json
              │          │
              │     ┌────▼──────┐
              │     │  data     │
              │     │ generator │──────▶ serving /predict
              │     └───────────┘
              │
              │                    serving ──▶ logs/requests/
              │
              │     Mealie user clicks ──▶ feedback_endpoint
              │                               │
              │                          logs/feedback/
              │                               │
              ├───────────────────┐            │
              │                   ▼            ▼
              │             ┌─────────────────────┐
              └────────────▶│   batch_pipeline    │──QC2──▶ quality_reports/
                            └─────────┬───────────┘
                                      │
                              ┌───────┼───────┐
                              ▼               ▼
                       data/processed/   data/triggers/
                       train_v<ts>       retrain_<ts>
                              │               │
                              └───────┬───────┘
                                      ▼
                              watch_trigger.py
                              (training team)


              logs/requests/ ──▶ drift_monitor ──QC3──▶ quality_reports/
```

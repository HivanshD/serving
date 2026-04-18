# GITHUB_SETUP.md

How to get this serving component into GitHub in the right structure
for April 20 system implementation submission.

Two approaches, pick one as a team.

---

## Approach A (RECOMMENDED): Monorepo at the top level

The rubric says "The repo structure must reflect architectural boundaries,
not role separation." A single monorepo with one subdirectory per
component (serving, training, data, infra, mealie-integration) is the
cleanest expression of this.

### Target structure

```
github.com/<org-or-user>/ml-sys-ops-project
├── README.md                    ← top-level, points to each subcomponent
├── .gitignore
├── serving/                     ← THIS DIRECTORY (what I built for you)
│   ├── README.md
│   ├── INTEGRATION.md
│   ├── DEPLOYMENT_DECISIONS.md
│   ├── FILES.md
│   ├── RUNBOOK.md
│   ├── Makefile
│   ├── fastapi_pt/
│   ├── fastapi_onnx/
│   ├── models/
│   ├── scripts/
│   ├── docker/
│   ├── sample_data/
│   └── k8s-cronjob-manifests.yaml
├── training/                    ← Training team's subdirectory
│   ├── README.md
│   ├── train.py
│   ├── evaluate.py
│   ├── watch_trigger.py
│   ├── config.yaml
│   ├── Dockerfile
│   └── requirements.txt
├── data/                        ← Data team's subdirectory
│   ├── README.md
│   ├── ingest.py
│   ├── batch_pipeline.py
│   ├── feedback_endpoint.py
│   ├── drift_monitor.py
│   ├── data_generator.py
│   ├── online_features.py
│   └── Dockerfile.*
├── infra/                       ← DevOps subdirectory
│   ├── README.md
│   ├── k8s/
│   │   ├── namespaces.yaml
│   │   ├── staging/
│   │   ├── canary/
│   │   ├── production/
│   │   ├── monitoring/
│   │   └── cronjobs/
│   └── automation/
│       ├── automation.py
│       └── Dockerfile
└── mealie-integration/          ← team effort via OpenCode
    ├── README.md
    ├── substitution_route.py
    ├── SubstitutionDialog.vue
    └── Dockerfile
```

### Setup steps (run these once)

```bash
# 1. ONE team member creates the monorepo on GitHub UI as public
#    (or private + add ffund as collaborator before the deadline)
# 2. Clone it
git clone https://github.com/<your-org>/ml-sys-ops-project.git
cd ml-sys-ops-project

# 3. Copy the serving directory (from this package) into place
#    If you downloaded serving_package.zip:
unzip ~/Downloads/serving_package.zip -d .
# You should now have ml-sys-ops-project/serving/

# 4. Create placeholder directories for the other components
mkdir -p training data infra/{k8s,automation} mealie-integration
touch training/.gitkeep data/.gitkeep infra/.gitkeep mealie-integration/.gitkeep

# 5. Add a top-level README
cat > README.md << 'EOF'
# ml-sys-ops-project

ECE-GY 9183 | proj01 | Ingredient Substitution for Mealie

End-to-end ML system that adds AI-powered ingredient substitution
suggestions to Mealie, a self-hosted recipe manager.

## Components

| Directory | Owner | Purpose |
|-----------|-------|---------|
| [`serving/`](./serving) | Hivansh | FastAPI + ONNX inference, monitoring, rollback/promote scripts |
| [`training/`](./training) | <name> | Training pipeline, MLflow integration, quality gates |
| [`data/`](./data) | <name> | Recipe1MSubs ingestion, feedback capture, drift monitoring |
| [`infra/`](./infra) | <name> | K8S manifests, CI/CD, automation webhook |
| [`mealie-integration/`](./mealie-integration) | Team | Mealie backend patch + Vue component |

## One-command setup (on Chameleon)

```bash
# Apply all K8S manifests
kubectl apply -f infra/k8s/namespaces.yaml
kubectl apply -f infra/k8s/ --recursive
```

## Where to start

- New team member: read [`serving/README.md`](./serving/README.md) and
  [`serving/INTEGRATION.md`](./serving/INTEGRATION.md)
- Grader: read each subdirectory's README; run through the demo video;
  use the one-command setup above to reproduce
- Operations: [`serving/RUNBOOK.md`](./serving/RUNBOOK.md)

Assisted by Claude (Opus 4.5, Sonnet 4.6, Opus 4.7).
EOF

# 6. Create a top-level .gitignore
cat > .gitignore << 'EOF'
# Local Python
__pycache__/
*.py[cod]
.venv/
venv/

# Secrets — NEVER commit
.env
.env.*
*.secret
secrets/
k8s/secrets/

# Model weights — object storage only
*.pth
*.onnx
*.safetensors
vocab.json

# OS / editor
.DS_Store
.vscode/
.idea/
EOF

# 7. Commit and push
git add .
git commit -m "Import serving component for April 20 system implementation

- FastAPI + ONNX quantized CPU production default (4 workers + HPA)
- Triton ONNX GPU retained as high-throughput option
- Prometheus metrics, privacy-preserving request logging
- check_rollback and check_promote CronJob scripts
- Complete cross-team contract in serving/INTEGRATION.md

Assisted by Claude Opus 4.7"
git push
```

### What each teammate does next

Each teammate adds their subdirectory. Example for Training:

```bash
cd ml-sys-ops-project
# Training team writes their code into ./training/
# ...
git add training/
git commit -m "Add training pipeline

- train.py with MRR@3 quality gate
- evaluate.py with per-cuisine fairness metrics
- watch_trigger.py for automated retraining

Assisted by Claude Sonnet 4.6"
git push
```

DevOps does the same with their K8S manifests under `infra/`.

---

## Approach B (BACKWARDS COMPATIBLE): Keep existing repos, link from monorepo

Your DevOps member already has work on
`github.com/HivanshD/serving`. If the team would rather not migrate
that, keep separate repos and have a meta-repo that points to each.

### Target structure

```
github.com/<org>/ml-sys-ops-project          ← meta-repo
├── README.md                    ← explains where each component lives
├── infra/                       ← K8S manifests (lives here, small)
├── submodules/
│   ├── serving     @  github.com/HivanshD/serving          (git submodule)
│   ├── training    @  github.com/<teammate>/training       (git submodule)
│   └── data        @  github.com/<teammate>/data           (git submodule)
└── mealie-integration/          ← team effort, small enough to live in meta-repo
```

### Setup

```bash
# Meta repo
git clone https://github.com/<org>/ml-sys-ops-project.git
cd ml-sys-ops-project

git submodule add https://github.com/HivanshD/serving submodules/serving
git submodule add https://github.com/<teammate>/training submodules/training
git submodule add https://github.com/<teammate>/data submodules/data

git commit -m "Add component submodules"
git push
```

### Pros/cons of Approach B

Pros:
- Doesn't disturb existing repos
- Each team member can keep their GitHub commit history

Cons:
- Graders have to clone with `--recurse-submodules`
- Submodule pins drift from main branches — easy to forget to update
- `kubectl apply` has to find files across multiple repos

**I recommend Approach A.** A monorepo with one commit history is cleaner
for grading, and your DevOps member's `migrate-repo-boundary-and-local-test`
branch was already setting up for repo migration. Now is the time.

---

## Migration path for the existing serving repo

You already have code in `github.com/HivanshD/serving`. Here's how to
migrate it cleanly:

```bash
# 1. Create the new monorepo (see Approach A above)
# 2. In the new monorepo:
cd ml-sys-ops-project

# 3. Import the old serving repo's history as a subtree
git remote add serving-old https://github.com/HivanshD/serving.git
git fetch serving-old
git subtree add --prefix=serving/ serving-old main --squash
git remote remove serving-old

# 4. Now replace serving/ with the updated content from this package
rm -rf serving/
unzip ~/Downloads/serving_package.zip -d .

git add serving/
git commit -m "Replace serving/ with April 20 system implementation version"
git push
```

Your initial-implementation code is preserved in git history, and the
current `serving/` reflects the new April 20 content.

---

## One-command verification after push

Anyone (including the grader) can verify the repo works with:

```bash
git clone https://github.com/<your-org>/ml-sys-ops-project.git
cd ml-sys-ops-project/serving

# Build and run locally
make build-onnx
make up
# Wait ~30 seconds for the container to start

# Smoke test
make smoke URL=http://localhost:8001
# Expect: ALL CHECKS PASSED

# Benchmark
make benchmark URL=http://localhost:8001

# Shut down
make down
```

If any of this fails, the repo is broken. Fix before the April 20 deadline.

---

## Final checklist before April 20

- [ ] Monorepo created at `github.com/<org>/ml-sys-ops-project`
- [ ] Repo is public OR `ffund` is added as collaborator
- [ ] Top-level `README.md` exists with component table
- [ ] `serving/` subdirectory contains all files from this package
- [ ] `training/`, `data/`, `infra/`, `mealie-integration/` populated by teammates
- [ ] `make smoke` passes locally from a fresh clone
- [ ] No `.pth` or `.onnx` files committed (check with `find . -name "*.pth" -o -name "*.onnx"`)
- [ ] No secrets committed (check with `grep -r "OS_ACCESS_KEY" --include="*.yaml" --include="*.md"` — should only match docs, not configs)
- [ ] Your `serving/INTEGRATION.md` checklist (Part 4) has all items checked off
- [ ] The demo video links to this repo in its description

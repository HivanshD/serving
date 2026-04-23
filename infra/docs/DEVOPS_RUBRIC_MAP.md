# DevOps Rubric Map — Apr 20 System Implementation

This document links the Apr 20 system-implementation expectations to the
current repo so the DevOps story is explicit, reviewable, and honest.

## Direct Answer: Prometheus Or Grafana?

1. Prometheus: yes. In this repo's current design it is effectively required because `serving/scripts/check_promote.py` and `serving/scripts/check_rollback.py` query the Prometheus HTTP API directly, and the serving and data code already expose Prometheus metrics.
2. Grafana: treat it as required for a strong DevOps submission. The rubric asks for monitoring, alerting, and visible system-health reporting. If another dashboard and alerting stack is used, document that replacement explicitly, but Grafana is the simplest fit to the current code.
3. Loki: optional. Useful for centralized logs and debugging, but it does not replace Prometheus-based metrics, alerting, or promotion and rollback logic.

## Repo Evidence That DevOps Can Already Cite

1. Kubernetes on Chameleon: `infra/tf/kvm/`, `infra/ansible/`, and `infra/docs/FORKWISE_CLOUD_SETUP.md`.
2. Serving metrics endpoint and model-serving telemetry: `serving/fastapi_onnx/serve_onnx.py`.
3. Feedback-service metrics endpoint: `data/feedback_endpoint.py`.
4. Live data-quality and drift metrics: `data/drift_monitor.py`.
5. Promotion and rollback decision logic: `serving/scripts/check_promote.py`, `serving/scripts/check_rollback.py`, and `serving/k8s-cronjob-manifests.yaml`.
6. Mealie deployment wired to the custom image and in-cluster integration URLs: `infra/k8s/apps/mealie/mealie-deployment.yaml` and `infra/k8s/apps/mealie/integration-configmap.yaml`.

## Rubric Map

| DevOps / joint requirement | Repo evidence now | Gap to close for full credit |
| --- | --- | --- |
| Integrated system runs on Chameleon Kubernetes | `infra/tf/kvm/`, `infra/ansible/`, `infra/docs/FORKWISE_CLOUD_SETUP.md` | Verify the final integrated stack end-to-end, not just bootstrap apps |
| Unified shared infrastructure | Root repo layout, `serving/TEAM_INTEGRATION_MAP.md`, and `infra/k8s/platform/` | Keep one shared monitoring stack, one shared MLflow path, and one shared object-store contract; avoid duplicate per-role deployments |
| `staging`, `canary`, `production` environments for a four-person team | `infra/k8s/staging/`, `infra/k8s/canary/`, `infra/k8s/production/`, `models-proj01/manifests/*.json`, `training/watch_trigger.py` | Validate the full rollout path live on Chameleon and decide whether the current synthetic canary split is enough or whether to add ingress-based splitting |
| Automated promotion and rollback | `infra/automation/automation.py`, `infra/k8s/platform/automation-*.yaml`, `serving/scripts/check_promote.py`, `serving/scripts/check_rollback.py`, `models-proj01/manifests/*.json` | Verify the deployed webhook against live metrics under traffic |
| Monitoring of model behavior, ops metrics, and user feedback | `serving/fastapi_onnx/serve_onnx.py`, `data/feedback_endpoint.py`, `infra/k8s/platform/prometheus-configmap.yaml`, `infra/k8s/platform/grafana-dashboard-configmap.yaml` | Expand data-quality and infra-health coverage as needed and verify dashboards under live traffic |
| Infrastructure monitoring and automated scaling | `infra/k8s/production/hpa.yaml`, `infra/k8s/platform/prometheus-configmap.yaml`, `infra/k8s/platform/grafana-*.yaml` | Add any remaining cluster-health views or alerts you want for the final demo |
| Clean repo organization for integrated system | Top-level structure is already architectural rather than purely by person | Keep docs aligned with the final deploy path and avoid role-by-role duplicate stacks |
| Open-source app uses the ML feature in the normal user flow | `infra/k8s/apps/mealie/mealie-deployment.yaml` points at the custom Mealie image and wires service URLs | Verify the custom image behavior end-to-end and document the demo path clearly |

## Minimum DevOps Stack To Claim Cleanly

1. Shared monitoring namespace or equivalent platform area.
2. Prometheus scraping serving and data metrics.
3. Grafana dashboards and alert rules for latency, error rate, drift, and service health.
4. Automation service exposing `/promote` and `/rollback`.
5. `staging`, `canary`, and `production` serving deployments.
6. HPA or another documented autoscaling mechanism with justified thresholds.
7. One shared object-store and one shared monitoring stack for the whole system.

## Nice To Have But Not The Main Rubric Lever

1. Loki and Promtail for centralized logs.
2. Extra Grafana dashboards for deeper debugging.
3. Argo or another GitOps layer, if it does not slow down delivery.

## Current Priority Order

1. Build and push the automation image, then deploy the full stack on Chameleon and validate the live dashboards, CronJobs, and bootstrap-manifest flow.
2. Verify the custom Mealie image in the normal user flow against the production rollout path.
3. Decide whether to keep the synthetic canary traffic split or add ingress-based traffic splitting.
4. Close any remaining data-contract edge cases discovered during live retraining.

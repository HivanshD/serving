# Platform Services

This directory is the home for cluster-wide services shared by the entire
ForkWise system.

It now contains the first actual platform implementation for the integrated
rollout path.

## Shared Services That Belong Here

1. Prometheus scraping the serving and data metrics endpoints
2. Grafana dashboards and alert rules for model, service, and infrastructure health
3. the automation webhook or service that implements `/promote` and `/rollback`
4. MLflow or the model-registry service used by retraining and promotion
5. ingress, traffic-splitting, and autoscaling primitives shared across environments
6. object storage only if it is self-hosted in cluster; otherwise document the external shared object store as the single source of truth

## De-duplication Rule

Run one shared instance of each platform service for the integrated system
unless a clear technical reason requires otherwise. Do not create separate
monitoring stacks, MLflow instances, or duplicate buckets just because work
started role-by-role.

## Monitoring Guidance

1. Prometheus is required in this repo's current design because the serving promote and rollback scripts query the Prometheus HTTP API directly.
2. Grafana should be treated as part of the expected platform because the rubric asks for visible monitoring and alerting; if you replace it, document the equivalent dashboard and alerting stack explicitly.
3. Loki or Promtail are optional centralized logging components. They are useful for debugging and demos, but they do not replace Prometheus-based metrics and alerts.

## Manifests Added Here

1. `prometheus-*` manifests for scraping serving and feedback metrics and evaluating alert rules
2. `grafana-*` manifests for dashboards and the Prometheus datasource
3. `automation-*` manifests for the internal promotion and rollback webhook

## Automation Behavior

The automation service now manages rollout manifests in `models-proj01`:

1. `POST /deploy-candidate` copies a candidate manifest into the rollout targets such as `staging` and `canary`
2. `POST /promote` promotes the current canary manifest into production and saves the previous production manifest for rollback
3. `POST /rollback` restores `manifests/production_previous.json` into production and restarts the production deployment
4. `POST /bootstrap-rollout` seeds `manifests/staging.json`, `manifests/canary.json`, and `manifests/production.json` for a fresh cluster

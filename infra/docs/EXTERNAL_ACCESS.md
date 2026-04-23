# External Access

This doc explains how an external reviewer or professor can access the live
ForkWise deployment on Chameleon without Jupyter access.

The reviewer only needs:

1. SSH access to the `cc` user on the floating-IP node
2. the matching private key on their own laptop
3. the current floating IP

They do not need the Chameleon Jupyter host, `ProxyJump`, or direct access to
the private node IPs.

They also should not need a manual security-group change for Mealie, serving,
Grafana, or Prometheus if SSH to `cc@<FLOATING_IP>` already works. The browser
access in this doc is done through SSH local port forwarding over port `22`.

## 1. Add the reviewer's SSH public key

From a machine that already has access to the Chameleon node, run the exact
pattern below:

```bash
ssh cc@<FLOATING_IP> 'echo "<REVIEWER_PUBLIC_KEY>" >> ~/.ssh/authorized_keys'
```

Example:

```bash
ssh cc@129.114.27.72 'echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJcVq9eWU0orFqyTQrjjq6hTMOAP3XdndodI1Vufv5xZ ffund@ffund-thinkpad" >> ~/.ssh/authorized_keys'
```

If that SSH command works, the reviewer can use the tunnel commands below
without any extra security-group edits.

If you want to verify the key was added:

```bash
ssh cc@<FLOATING_IP> 'tail -n 5 ~/.ssh/authorized_keys'
```

## 2. Mealie UI access

The current Mealie service is a NodePort defined in
`infra/k8s/apps/mealie/mealie-service.yaml`:

1. service port `9000`
2. NodePort `30090`

The safest access method is an SSH tunnel from the reviewer's laptop:

```bash
ssh -N -L 9000:127.0.0.1:30090 cc@<FLOATING_IP>
```

Then open:

```text
http://localhost:9000
```

## 3. Base serving API access

The bootstrap serving service is a NodePort defined in
`infra/k8s/apps/substitution-serving/service.yaml`:

1. service port `8000`
2. NodePort `30080`

Tunnel it from the reviewer's laptop:

```bash
ssh -N -L 8000:127.0.0.1:30080 cc@<FLOATING_IP>
```

Then test:

```bash
curl http://localhost:8000/health
```

## 4. Rollout-stack Grafana and Prometheus access

These ports exist only after the rollout stack is deployed with
`infra/ansible/deploy/deploy_rollout_stack.yml`.

### Grafana

Defined in `infra/k8s/platform/grafana-service.yaml`:

1. service port `3000`
2. NodePort `31300`

Tunnel it:

```bash
ssh -N -L 3000:127.0.0.1:31300 cc@<FLOATING_IP>
```

Open:

```text
http://localhost:3000
```

Grafana admin credentials come from `infra/k8s/platform/grafana-secret.yaml`:

1. username `admin`
2. password `admin`

### Prometheus

Defined in `infra/k8s/platform/prometheus-service.yaml`:

1. service port `9090`
2. NodePort `31090`

Tunnel it:

```bash
ssh -N -L 9090:127.0.0.1:31090 cc@<FLOATING_IP>
```

Open:

```text
http://localhost:9090
```

## 5. Rollout-environment serving endpoints

These ports exist only after the rollout stack is deployed.

1. production `infra/k8s/production/service.yaml` -> NodePort `31080`
2. canary `infra/k8s/canary/service.yaml` -> NodePort `31081`
3. staging `infra/k8s/staging/service.yaml` -> NodePort `31082`

Example tunnel set:

```bash
ssh -N \
  -L 8080:127.0.0.1:31080 \
  -L 8081:127.0.0.1:31081 \
  -L 8082:127.0.0.1:31082 \
  cc@<FLOATING_IP>
```

Then test:

```bash
curl http://localhost:8080/health
curl http://localhost:8081/health
curl http://localhost:8082/health
```

## 6. One tunnel for the common demo surfaces

If Mealie, base serving, Grafana, and Prometheus are all deployed, use one SSH
command:

```bash
ssh -N \
  -L 9000:127.0.0.1:30090 \
  -L 8000:127.0.0.1:30080 \
  -L 3000:127.0.0.1:31300 \
  -L 9090:127.0.0.1:31090 \
  cc@<FLOATING_IP>
```

Then open:

1. Mealie: `http://localhost:9000`
2. Serving: `http://localhost:8000/health`
3. Grafana: `http://localhost:3000`
4. Prometheus: `http://localhost:9090`

## 7. Useful checks before sending access to someone else

From a machine that already has cluster access:

```bash
ssh cc@<FLOATING_IP> 'kubectl get svc -A'
ssh cc@<FLOATING_IP> 'kubectl get pods -A'
```

For just the Mealie bootstrap path, you should at least see:

1. namespace `forkwise-app`
2. service `mealie` on NodePort `30090`
3. `mealie` and `mealie-postgres` pods in `Running`

For the rollout stack, you should also see:

1. namespace `monitoring-proj01`
2. service `grafana` on NodePort `31300`
3. service `prometheus` on NodePort `31090`
4. namespaces `staging-proj01`, `canary-proj01`, and `production-proj01`

## 8. Host-key troubleshooting

If the floating IP was reused and SSH reports a changed host key, the reviewer
should remove the stale entry and retry:

```bash
ssh-keygen -R <FLOATING_IP>
```

Then rerun the tunnel command.

# Ansible Deployment Flow

This directory contains the repo's actual configuration and deployment orchestration path for Chameleon.

## Layout

```text
ansible/
├── ansible.cfg.example
├── inventory.yml
├── general/
├── pre_k8s/
├── k8s/
├── post_k8s/
└── deploy/
```

## Playbooks

1. `general/hello_host.yml` verifies SSH connectivity.
2. `pre_k8s/pre_k8s_configure.yml` prepares the nodes for Kubernetes.
3. `k8s/install_k3s.yml` installs k3s with `node1` as the server and `node2`/`node3` as agents.
4. `post_k8s/post_k8s_configure.yml` prepares kubectl access and installs Helm and metrics-server.
5. `deploy/deploy_apps.yml` copies this repo's `k8s/` directory to `node1`, creates the required Mealie secret, and applies the base app manifests for Mealie and substitution-serving.

## Minimal Sequence

```bash
cd ansible
cp ansible.cfg.example ansible.cfg
# replace REPLACE_WITH_FLOATING_IP in ansible.cfg
eval "$(ssh-agent -s)"
ssh-add /work/.ssh/id_rsa    # or the private key that matches your Chameleon keypair
ansible-playbook -i inventory.yml general/hello_host.yml
ansible-playbook -i inventory.yml pre_k8s/pre_k8s_configure.yml
ansible-playbook -i inventory.yml k8s/install_k3s.yml
ansible-playbook -i inventory.yml post_k8s/post_k8s_configure.yml
ansible-playbook -i inventory.yml deploy/deploy_apps.yml -e serving_image=<registry>/substitution-serving:<tag>
```

For the full ForkWise cloud bring-up, continue with
`infra/docs/FORKWISE_CLOUD_SETUP.md` after this base deploy. That runbook covers
the GHCR-backed `forkwise-data` workloads and the one-time ingest bootstrap job.

## Notes

1. `ansible.cfg` is ignored by Git because it contains live connection details.
2. `deploy/deploy_apps.yml` deploys the bootstrap app manifests only. The rollout and monitoring layers are applied separately by `deploy/deploy_rollout_stack.yml`.
3. `deploy/deploy_rollout_stack.yml` expects `os-credentials` to exist in `monitoring-proj01` before it runs, because the automation service uses object storage to seed and manage rollout manifests.
4. Services are exposed as NodePorts on `node1` so you can reach them with SSH tunneling without opening additional public ports.
5. `kubectl` is prepared on `node1` by `post_k8s/post_k8s_configure.yml`; use `ssh cc@<FLOATING_IP> 'kubectl ...'` for remote checks unless you copy kubeconfig locally.
6. If `general/hello_host.yml` fails with `Connection closed by UNKNOWN port 65535`, verify the jump-host path manually before retrying Ansible:

```bash
ssh cc@<FLOATING_IP> hostname
ssh -J cc@<FLOATING_IP> -o ControlMaster=no cc@192.168.1.11 hostname
ssh -J cc@<FLOATING_IP> -o ControlMaster=no cc@192.168.1.12 hostname
ssh -J cc@<FLOATING_IP> -o ControlMaster=no cc@192.168.1.13 hostname
```

7. `serving_image` in `deploy/deploy_apps.yml` is only for the `substitution-serving` deployment and `check-rollback` CronJob image. Do not pass the custom Mealie image via `-e serving_image=...`.

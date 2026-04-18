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
5. `deploy/deploy_apps.yml` copies this repo's `k8s/` directory to `node1`, creates the required Mealie secret, and applies the app manifests.

## Minimal Sequence

```bash
cd ansible
cp ansible.cfg.example ansible.cfg
# replace REPLACE_WITH_FLOATING_IP in ansible.cfg
ansible-playbook -i inventory.yml general/hello_host.yml
ansible-playbook -i inventory.yml pre_k8s/pre_k8s_configure.yml
ansible-playbook -i inventory.yml k8s/install_k3s.yml
ansible-playbook -i inventory.yml post_k8s/post_k8s_configure.yml
ansible-playbook -i inventory.yml deploy/deploy_apps.yml -e serving_image=<registry>/substitution-serving:<tag>
```

## Notes

1. `ansible.cfg` is ignored by Git because it contains live connection details.
2. This path intentionally avoids Argo and multi-environment rollout complexity.
3. Services are exposed as NodePorts on `node1` so you can reach them with SSH tunneling without opening additional public ports.

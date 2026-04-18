# Terraform For Chameleon KVM

This directory contains the Chameleon infrastructure definition for the repo's minimal Kubernetes deployment.

## Topology

The default topology provisions:

1. three Ubuntu instances
2. one private cluster network with fixed IPs
3. one `sharednet1` interface on each node for outbound access
4. one floating IP attached only to `node1`

`node1` is intended to be:

1. the SSH jump host
2. the k3s server node
3. the browser and tunnel entrypoint for Mealie and substitution-serving

## Files

1. `versions.tf`
2. `provider.tf`
3. `variables.tf`
4. `data.tf`
5. `main.tf`
6. `outputs.tf`
7. `terraform.tfvars.example`

## Usage

```bash
cd tf/kvm
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform validate
terraform plan
terraform apply -auto-approve
```

## Secrets Hygiene

Do not commit:

1. `clouds.yaml`
2. `terraform.tfvars`
3. `.tfstate` files

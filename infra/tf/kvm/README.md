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
export PATH=/work/.local/bin:$PATH
unset $(set | grep -o "^OS_[A-Za-z0-9_]*")
export OS_CLIENT_CONFIG_FILE=$PWD/clouds.yaml
export OS_CLOUD=openstack

cp terraform.tfvars.example terraform.tfvars
terraform init
terraform validate
terraform plan
terraform apply -auto-approve
```

If you are provisioning from the Chameleon Jupyter control host, keep
`clouds.yaml` in this directory and prefer the explicit `OS_CLIENT_CONFIG_FILE`
and `OS_CLOUD` exports above so Terraform and `openstack` CLI use the same
credential source.

If normal scheduling is full, create a short Chameleon lease for `3 x
m1.large`, copy the reservation-backed `flavor_id` into `terraform.tfvars`,
then retry with:

```bash
terraform apply -auto-approve -parallelism=1
```

If you create multiple leases with the same name while iterating, use the
lease UUID instead of the human-readable name when checking lease status with
the `openstack` CLI.

## Secrets Hygiene

Do not commit:

1. `clouds.yaml`
2. `terraform.tfvars`
3. `.tfstate` files

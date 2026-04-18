variable "openstack_cloud" {
  description = "Cloud name from clouds.yaml"
  type        = string
  default     = "openstack"
}

variable "suffix" {
  description = "Course-required project suffix, e.g. proj99"
  type        = string
}

variable "key" {
  description = "Existing Chameleon keypair name"
  type        = string
}

variable "image_name" {
  description = "Chameleon image/appliance"
  type        = string
  default     = "CC-Ubuntu24.04"
}

variable "flavor_id" {
  description = "Reserved flavor UUID to use when capacity is obtained through a Chameleon lease"
  type        = string
  default     = ""
}

variable "flavor_name" {
  description = "Flavor name for each VM when not using a reserved flavor UUID"
  type        = string
  default     = "m1.medium"
}

variable "sharednet_name" {
  description = "Default external network used by the simple lab-style topology"
  type        = string
  default     = "sharednet1"
}

variable "floating_ip_pool" {
  description = "Floating IP pool"
  type        = string
  default     = "public"
}

variable "security_group_names" {
  description = "Security groups attached to the public/sharednet interface"
  type        = list(string)
  default     = ["default"]
}

variable "nodes" {
  description = "Three-node k3s topology with node1 as control plane and entrypoint"
  type        = map(string)
  default = {
    node1 = "192.168.1.11"
    node2 = "192.168.1.12"
    node3 = "192.168.1.13"
  }
}

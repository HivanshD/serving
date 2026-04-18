data "openstack_networking_network_v2" "sharednet1" {
  name = var.sharednet_name
}

data "openstack_networking_secgroup_v2" "sgs" {
  for_each = toset(var.security_group_names)
  name     = each.value
}

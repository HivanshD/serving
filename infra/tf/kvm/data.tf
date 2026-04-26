data "openstack_networking_network_v2" "sharednet1" {
  name = "sharednet1"
}

data "openstack_networking_secgroup_v2" "default" {
  name = "default"
}

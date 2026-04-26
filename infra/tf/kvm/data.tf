data "openstack_networking_network_v2" "public_net" {
  name = var.floating_ip_pool
}

output "floating_ip" {
  value = openstack_networking_floatingip_v2.floating_ip.address
}

output "node_names" {
  value = [for node in openstack_compute_instance_v2.nodes : node.name]
}

output "private_ips" {
  value = var.nodes
}

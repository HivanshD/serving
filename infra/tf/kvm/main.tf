resource "openstack_networking_network_v2" "private_net" {
  name                  = "private-net-serving-${var.suffix}"
  port_security_enabled = false
}

resource "openstack_networking_subnet_v2" "private_subnet" {
  name       = "private-subnet-serving-${var.suffix}"
  network_id = openstack_networking_network_v2.private_net.id
  cidr       = "192.168.1.0/24"
  no_gateway = true
}

resource "openstack_networking_port_v2" "private_net_ports" {
  for_each              = var.nodes
  name                  = "port-${each.key}-serving-${var.suffix}"
  network_id            = openstack_networking_network_v2.private_net.id
  port_security_enabled = false

  fixed_ip {
    subnet_id  = openstack_networking_subnet_v2.private_subnet.id
    ip_address = each.value
  }
}

resource "openstack_networking_port_v2" "sharednet_ports" {
  for_each           = var.nodes
  name               = "sharednet1-${each.key}-serving-${var.suffix}"
  network_id         = data.openstack_networking_network_v2.sharednet1.id
  security_group_ids = [for sg in data.openstack_networking_secgroup_v2.sgs : sg.id]
}

resource "openstack_compute_instance_v2" "nodes" {
  for_each    = var.nodes
  name        = "${each.key}-serving-${var.suffix}"
  image_name  = var.image_name
  flavor_id   = var.flavor_id != "" ? var.flavor_id : null
  flavor_name = var.flavor_id == "" ? var.flavor_name : null
  key_pair    = var.key

  network {
    port = openstack_networking_port_v2.sharednet_ports[each.key].id
  }

  network {
    port = openstack_networking_port_v2.private_net_ports[each.key].id
  }

  user_data = <<-EOF
    #!/bin/bash
    echo "127.0.1.1 ${each.key}-serving-${var.suffix}" >> /etc/hosts
    su cc -c /usr/local/bin/cc-load-public-keys
  EOF
}

resource "openstack_networking_floatingip_v2" "floating_ip" {
  pool        = var.floating_ip_pool
  description = "Serving repo floating IP for ${var.suffix}"
  port_id     = openstack_networking_port_v2.sharednet_ports["node1"].id
}

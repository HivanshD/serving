# ── Project network + subnet (with gateway for internet via router) ──
resource "openstack_networking_network_v2" "project_net" {
  name                  = "net-forkwise-${var.suffix}"
  port_security_enabled = false
}

resource "openstack_networking_subnet_v2" "project_subnet" {
  name            = "subnet-forkwise-${var.suffix}"
  network_id      = openstack_networking_network_v2.project_net.id
  cidr            = "192.168.1.0/24"
  ip_version      = 4
  dns_nameservers = ["8.8.8.8", "8.8.4.4"]
}

# ── Router: connects project subnet to the public network ──
resource "openstack_networking_router_v2" "router" {
  name                = "router-forkwise-${var.suffix}"
  external_network_id = data.openstack_networking_network_v2.public_net.id
}

resource "openstack_networking_router_interface_v2" "router_iface" {
  router_id = openstack_networking_router_v2.router.id
  subnet_id = openstack_networking_subnet_v2.project_subnet.id
}

# ── Ports on the project network ──
resource "openstack_networking_port_v2" "project_ports" {
  for_each              = var.nodes
  name                  = "port-${each.key}-forkwise-${var.suffix}"
  network_id            = openstack_networking_network_v2.project_net.id
  port_security_enabled = false

  fixed_ip {
    subnet_id  = openstack_networking_subnet_v2.project_subnet.id
    ip_address = each.value
  }
}

# ── Compute instances ──
resource "openstack_compute_instance_v2" "nodes" {
  for_each    = var.nodes
  name        = "${each.key}-serving-${var.suffix}"
  image_name  = var.image_name
  flavor_id   = var.flavor_id != "" ? var.flavor_id : null
  flavor_name = var.flavor_id == "" ? var.flavor_name : null
  key_pair    = var.key

  network {
    port = openstack_networking_port_v2.project_ports[each.key].id
  }

  user_data = <<-EOF
    #!/bin/bash
    echo "127.0.1.1 ${each.key}-serving-${var.suffix}" >> /etc/hosts
    su cc -c /usr/local/bin/cc-load-public-keys
  EOF

  depends_on = [openstack_networking_router_interface_v2.router_iface]
}

# ── Floating IP on node1 ──
resource "openstack_networking_floatingip_v2" "floating_ip" {
  pool        = var.floating_ip_pool
  description = "ForkWise floating IP for ${var.suffix}"
}

resource "openstack_compute_floatingip_associate_v2" "fip_assoc" {
  floating_ip = openstack_networking_floatingip_v2.floating_ip.address
  instance_id = openstack_compute_instance_v2.nodes["node1"].id
}

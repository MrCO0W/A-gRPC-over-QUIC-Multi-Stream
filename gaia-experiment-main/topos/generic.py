import logging
from core.topo import Topo, TopoConfig, LinkCharacteristics, iface_name, NetemAt
from core.netmodel import NetModel


class GenericTopo(Topo):
    NAME = "Generic"

    def __init__(self, topo_builder, parameter_file):
        logging.info("Initializing GenericTopo...")
        super(GenericTopo, self).__init__(topo_builder, parameter_file)

        self.net_model = NetModel.from_parameter(self.topo_parameter)
        self.net_model.allocate()

        # Add routers
        self.router_nodes = {}
        for r_name in self.net_model.routers:
            r_node = self.topo_builder.add_host(r_name)
            self.router_nodes[r_name] = r_node
            if r_node not in self.routers:
                self.routers.append(r_node)

        # Add clients, servers, and idle hosts
        self.host_nodes = {}
        self.idle_nodes = []
        for h_name, host_spec in self.net_model.hosts.items():
            if host_spec.role == "client":
                h_node = self.add_client(h_name)
            elif host_spec.role == "server":
                h_node = self.add_server(h_name)
            elif host_spec.role == "idle":
                h_node = self.topo_builder.add_host(h_name)
                self.idle_nodes.append(h_node)
            else:
                raise ValueError(f"Invalid host role '{host_spec.role}' for host '{h_name}'")
            self.host_nodes[h_name] = h_node

        # Add links with LinkCharacteristics & deterministic interface mapping (F6-1 & F6-2)
        self.c2r_links = []
        self.link_chars = []
        self.iface_of = {}  # (node_name, link_idx) -> "node-ethN"
        counter = {}

        for idx, link_spec in enumerate(self.net_model.links):
            node_a = self.host_nodes.get(link_spec.a) or self.router_nodes.get(link_spec.a)
            node_b = self.host_nodes.get(link_spec.b) or self.router_nodes.get(link_spec.b)

            if node_a and node_b:
                lc = LinkCharacteristics(
                    id=idx,
                    link_type="l",
                    delay=link_spec.delay,
                    queue_size=link_spec.queue_size,
                    bandwidth=link_spec.bandwidth,
                    loss=link_spec.loss,
                    backup=link_spec.backup
                )
                self.link_chars.append(lc)
                b_link = self.add_bottleneck_link(node_a, node_b, link_characteristics=lc)
                self.c2r_links.append(b_link)

                # Record interface names based on add_link order (BE-10)
                for node_name in (link_spec.a, link_spec.b):
                    n = counter.get(node_name, 0)
                    self.iface_of[(node_name, idx)] = iface_name(node_name, n)
                    counter[node_name] = n + 1

        # Bind netemAt schedules to link_chars (T-12c)
        for link_idx, at_sec, cmd in getattr(self.net_model, 'netem_at_schedules', []):
            if 0 <= link_idx < len(self.link_chars):
                self.link_chars[link_idx].add_netem_at(NetemAt(at_sec, cmd))

    def add_client(self, name):
        client = self.topo_builder.add_host(name)
        self.clients.append(client)
        return client

    def add_server(self, name):
        server = self.topo_builder.add_host(name)
        self.servers.append(server)
        return server

    def client_count(self):
        return len(self.clients)

    def router_count(self):
        return len(self.routers)

    def server_count(self):
        return len(self.servers)

    def get_client(self, i):
        return self.clients[i] if i < len(self.clients) else None

    def get_router(self, i=0):
        return self.routers[i] if i < len(self.routers) else None

    def get_server(self, i=0):
        return self.servers[i] if i < len(self.servers) else None

    def get_client_name(self, index):
        client_names = self.net_model.hosts_by_role("client")
        return client_names[index] if index < len(client_names) else None

    def get_server_name(self, index):
        server_names = self.net_model.hosts_by_role("server")
        return server_names[index] if index < len(server_names) else None

    def get_idle_name(self, index):
        idle_names = self.net_model.hosts_by_role("idle")
        return idle_names[index] if index < len(idle_names) else None

    def get_router_name(self, index):
        router_names = list(self.net_model.routers.keys())
        return router_names[index] if index < len(router_names) else None

    def reinit_variables(self):
        client_names = self.net_model.hosts_by_role("client")
        server_names = self.net_model.hosts_by_role("server")
        idle_names = self.net_model.hosts_by_role("idle")
        router_names = list(self.net_model.routers.keys())

        self.clients = [self.get_host(name) for name in client_names]
        self.servers = [self.get_host(name) for name in server_names]
        self.idle_nodes = [self.get_host(name) for name in idle_names]
        self.routers = [self.get_host(name) for name in router_names]
        self.host_nodes = {name: self.get_host(name) for name in (client_names + server_names + idle_names)}
        self.router_nodes = {name: self.get_host(name) for name in router_names}

        for b in self.bottleneck_links:
            b.reinit_variables()

    def __str__(self):
        return f"Generic Multi-Router Topology (Routers: {len(self.routers)}, Hosts: {len(self.clients) + len(self.servers) + len(getattr(self, 'idle_nodes', []))})"


class GenericConfig(TopoConfig):
    NAME = "Generic"

    def __init__(self, topo, param):
        super(GenericConfig, self).__init__(topo, param)
        self.net_model = getattr(topo, 'net_model', None)
        if self.net_model is None:
            self.net_model = NetModel.from_parameter(param)
            self.net_model.allocate()

    @property
    def client(self):
        return self.topo.clients[0] if self.topo.clients else None

    @property
    def server(self):
        return self.topo.servers[0] if self.topo.servers else None

    @property
    def router(self):
        return self.topo.routers[0] if self.topo.routers else None

    @property
    def clients(self):
        return self.topo.clients

    @property
    def servers(self):
        return self.topo.servers

    @property
    def routers(self):
        return self.topo.routers

    def configure_interfaces(self):
        logging.info("Configuring interfaces for GenericConfig...")
        super(GenericConfig, self).configure_interfaces()
        self._configure_node_rp_filter()
        self._configure_router_forwarding_and_proxy_arp()
        self._configure_host_interfaces()
        self._configure_router_host_interfaces()
        self._configure_transit_links()

    def _configure_node_rp_filter(self):
        all_nodes = self.all_hosts + self.routers
        for node in all_nodes:
            self.topo.command_to(node, "sysctl -w net.ipv4.conf.all.rp_filter=0")
            self.topo.command_to(node, "sysctl -w net.ipv4.conf.default.rp_filter=0")

    def _configure_router_forwarding_and_proxy_arp(self):
        for router in self.topo.routers:
            self.topo.command_to(router, "sysctl -w net.ipv4.ip_forward=1")
            self.topo.command_to(router, "sysctl -w net.ipv4.conf.all.proxy_arp=1")
            self.topo.command_to(router, "sysctl -w net.ipv4.conf.default.proxy_arp=1")

    def _configure_host_interfaces(self):
        for h_name, host_spec in self.net_model.hosts.items():
            host_node = self.topo.get_host(h_name)
            router_spec = self.net_model.routers.get(host_spec.router)

            if host_node and router_spec and host_spec.ip:
                link_idx = None
                for idx, l in enumerate(self.net_model.links):
                    if (l.a == h_name and l.b == host_spec.router) or (l.b == h_name and l.a == host_spec.router):
                        link_idx = idx
                        break

                main_intf = getattr(self.topo, 'iface_of', {}).get((h_name, link_idx))
                if not main_intf:
                    intf_names = self.topo.get_interface_names(host_node)
                    main_intf = intf_names[0] if intf_names else None

                if main_intf:
                    netmask = str(router_spec.subnet.netmask)
                    cmd = self.interface_up_command(
                        interface_name=main_intf,
                        ip=str(host_spec.ip),
                        subnet=netmask
                    )
                    self.topo.command_to(host_node, cmd)

                    # Configure static ARP to router
                    router_node = self.topo.get_host(host_spec.router)
                    if router_node and router_spec.ip and link_idx is not None:
                        r_intf_name = getattr(self.topo, 'iface_of', {}).get((host_spec.router, link_idx))
                        if r_intf_name and hasattr(router_node, 'intf') and router_node.intf(r_intf_name):
                            r_mac = router_node.intf(r_intf_name).MAC()
                            self.topo.command_to(host_node, f"arp -s {router_spec.ip} {r_mac}")

    def _configure_router_host_interfaces(self):
        for idx, link_spec in enumerate(self.net_model.links):
            router_name = None
            host_name = None
            if link_spec.a in self.net_model.routers and link_spec.b in self.net_model.hosts:
                router_name = link_spec.a
                host_name = link_spec.b
            elif link_spec.b in self.net_model.routers and link_spec.a in self.net_model.hosts:
                router_name = link_spec.b
                host_name = link_spec.a

            if router_name:
                router_spec = self.net_model.routers.get(router_name)
                router_node = self.topo.get_host(router_name)
                r_intf_name = getattr(self.topo, 'iface_of', {}).get((router_name, idx))
                if router_node and router_spec and router_spec.ip and r_intf_name:
                    netmask = str(router_spec.subnet.netmask)
                    cmd = self.interface_up_command(
                        interface_name=r_intf_name,
                        ip=str(router_spec.ip),
                        subnet=netmask
                    )
                    self.topo.command_to(router_node, cmd)
                    self.topo.command_to(router_node, f"sysctl -w net.ipv4.conf.{r_intf_name}.proxy_arp=1")

                    if host_name:
                        host_spec = self.net_model.hosts.get(host_name)
                        if host_spec and host_spec.ip:
                            self.topo.command_to(router_node, f"ip route replace {host_spec.ip}/32 dev {r_intf_name}")

    def _configure_transit_links(self):
        for t_link in getattr(self.net_model, 'transit_links', []):
            try:
                li = self.net_model.links.index(t_link.link_spec)
            except ValueError:
                continue

            netmask = str(t_link.subnet.netmask)
            for r_name, ip in ((t_link.router_a, t_link.ip_a), (t_link.router_b, t_link.ip_b)):
                r_node = self.topo.get_host(r_name)
                r_intf = getattr(self.topo, 'iface_of', {}).get((r_name, li))
                if r_node and r_intf:
                    cmd = self.interface_up_command(
                        interface_name=r_intf,
                        ip=str(ip),
                        subnet=netmask
                    )
                    self.topo.command_to(r_node, cmd)

    def configure_routing(self):
        logging.info("Configuring routing for GenericConfig...")
        # 1. Default routes for hosts to their primary router
        for h_name, host_spec in self.net_model.hosts.items():
            host_node = self.topo.get_host(h_name)
            router_spec = self.net_model.routers.get(host_spec.router)

            if host_node and router_spec and router_spec.ip:
                cmd = self.add_simple_default_route_command(via=str(router_spec.ip))
                self.topo.command_to(host_node, cmd)

        # 2. Static routes between routers
        computed_routes = self.net_model.compute_routes()
        for r_name, routes_list in computed_routes.items():
            router_node = self.topo.get_host(r_name)
            if router_node:
                for route in routes_list:
                    cmd = f"ip route add {route['destination']} via {route['via']}"
                    self.topo.command_to(router_node, cmd)

    # Backward compatibility shims
    def get_client_ip(self, interface_index=0, client_index=0):
        if client_index < len(self.topo.clients):
            c_name = self.topo.clients[client_index].name
            h_spec = self.net_model.hosts.get(c_name)
            if h_spec and h_spec.ip:
                return str(h_spec.ip)
        return f"10.0.1.{client_index + 2}"

    def get_server_ip(self, interface_index=0, server_index=0):
        if server_index < len(self.topo.servers):
            s_name = self.topo.servers[server_index].name
            h_spec = self.net_model.hosts.get(s_name)
            if h_spec and h_spec.ip:
                return str(h_spec.ip)
        return f"10.0.2.{server_index + 2}"

    def client_interface_count(self):
        return 1

    def server_interface_count(self):
        return 1

    def get_client_interface(self, client_index=0, interface_index=0):
        if client_index < len(self.topo.clients):
            c_name = self.topo.clients[client_index].name
            return iface_name(c_name, interface_index)
        return iface_name(f"c{client_index}", interface_index)

    def get_server_interface(self, server_index=0, interface_index=0):
        if server_index < len(self.topo.servers):
            s_name = self.topo.servers[server_index].name
            return iface_name(s_name, interface_index)
        return iface_name(f"s{server_index}", interface_index)

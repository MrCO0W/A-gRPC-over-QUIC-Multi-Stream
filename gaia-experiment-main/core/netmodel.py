import ipaddress
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from core.topo import parse_bool


@dataclass
class RouterSpec:
    name: str
    subnet: ipaddress.IPv4Network
    ip: Optional[ipaddress.IPv4Address] = None


VALID_HOST_ROLES = {"client", "server", "idle"}


@dataclass
class HostSpec:
    name: str
    role: str  # 'client', 'server', or 'idle'
    router: str  # Primary attached router name
    ip: Optional[ipaddress.IPv4Address] = None

    def __post_init__(self):
        if self.role not in VALID_HOST_ROLES:
            raise ValueError(f"Invalid host role '{self.role}'. Allowed roles: {sorted(VALID_HOST_ROLES)}")


@dataclass
class LinkSpec:
    a: str
    b: str
    delay: float = 15.0
    queue_size: int = 1000
    bandwidth: float = 100.0
    loss: float = 0.0
    backup: int = 0


@dataclass
class TransitLink:
    router_a: str
    router_b: str
    subnet: ipaddress.IPv4Network
    ip_a: ipaddress.IPv4Address
    ip_b: ipaddress.IPv4Address
    link_spec: LinkSpec


class NetModel:
    def __init__(self):
        self.routers: Dict[str, RouterSpec] = {}
        self.hosts: Dict[str, HostSpec] = {}
        self.links: List[LinkSpec] = []
        self.transit_subnet: ipaddress.IPv4Network = ipaddress.IPv4Network("172.31.0.0/16")
        self.change_netem: bool = False
        self.netem_at_schedules: List[Tuple[int, float, str]] = []
        self.transit_links: List[TransitLink] = []
        self.validation_errors: List[str] = []
        self._raw_router_names: List[str] = []
        self._raw_host_names: List[str] = []

    def add_router(self, name: str, subnet_str: str, ip_str: Optional[str] = None):
        self._raw_router_names.append(name)
        subnet = ipaddress.IPv4Network(subnet_str, strict=False)
        ip = ipaddress.IPv4Address(ip_str) if ip_str else None
        self.routers[name] = RouterSpec(name=name, subnet=subnet, ip=ip)

    def add_host(self, name: str, role: str, router: str, ip_str: Optional[str] = None):
        self._raw_host_names.append(name)
        ip = ipaddress.IPv4Address(ip_str) if ip_str else None
        self.hosts[name] = HostSpec(name=name, role=role, router=router, ip=ip)

    def add_link(self, a: str, b: str, delay: float = 15.0, queue_size: int = 1000,
                 bandwidth: float = 100.0, loss: float = 0.0, backup: int = 0):
        self.links.append(LinkSpec(a=a, b=b, delay=delay, queue_size=queue_size,
                                   bandwidth=bandwidth, loss=loss, backup=backup))

    def find_link_index(self, node1: str, node2: str) -> Optional[int]:
        """Find link index between node1 and node2 bidirectionally."""
        for i, link in enumerate(self.links):
            if (link.a == node1 and link.b == node2) or (link.a == node2 and link.b == node1):
                return i
        return None

    def hosts_by_role(self, role: str) -> List[str]:
        """Return list of host names matching the specified role."""
        return [h.name for h in self.hosts.values() if h.role == role]

    def validate(self) -> List[str]:
        errors = list(self.validation_errors)

        # 0. Check valid host roles (WP-2)
        for h_name, host in self.hosts.items():
            if host.role not in VALID_HOST_ROLES:
                errors.append(f"Invalid host role '{host.role}' for host '{h_name}': must be one of client, server, idle.")

        # 1. Check duplicate node names
        seen_names = set()
        for name in self._raw_router_names + self._raw_host_names:
            if name in seen_names:
                err = f"Duplicate node name '{name}'."
                if err not in errors:
                    errors.append(err)
            seen_names.add(name)

        # Also check cross-collision between current routers and hosts
        router_set = set(self.routers.keys())
        host_set = set(self.hosts.keys())
        for cross in router_set & host_set:
            err = f"Duplicate node name '{cross}'."
            if err not in errors:
                errors.append(err)

        # 2. Check node name length (IFNAMSIZ constraint: <= 8 chars)
        for r_name in self.routers:
            if len(r_name) > 8:
                errors.append(f"Router name '{r_name}' exceeds 8 character limit for Linux interface safety.")
        for h_name in self.hosts:
            if len(h_name) > 8:
                errors.append(f"Host name '{h_name}' exceeds 8 character limit for Linux interface safety.")

        # 3. Check router subnet overlaps
        subnets = [(r.name, r.subnet) for r in self.routers.values()]
        for i in range(len(subnets)):
            for j in range(i + 1, len(subnets)):
                r1, s1 = subnets[i]
                r2, s2 = subnets[j]
                if s1.overlaps(s2):
                    errors.append(f"Subnet overlap detected between router '{r1}' ({s1}) and '{r2}' ({s2}).")

        # 4. Check link node references
        valid_nodes = router_set.union(host_set)
        for idx, link in enumerate(self.links):
            if link.a not in valid_nodes:
                errors.append(f"Link #{idx} references unknown node '{link.a}'.")
            if link.b not in valid_nodes:
                errors.append(f"Link #{idx} references unknown node '{link.b}'.")

        # 5. Check direct host-to-host links (A-5a)
        for link in self.links:
            if link.a in self.hosts and link.b in self.hosts:
                errors.append(f"Direct host-to-host link between '{link.a}' and '{link.b}' is not allowed.")

        # 6. Check host router references and link connectivity (A-5a)
        for h_name, host in self.hosts.items():
            if host.router and host.router not in self.routers:
                errors.append(f"Host '{h_name}' references non-existent router '{host.router}'.")

            attached_routers = []
            for link in self.links:
                if link.a == h_name and link.b in self.routers:
                    attached_routers.append(link.b)
                elif link.b == h_name and link.a in self.routers:
                    attached_routers.append(link.a)

            if not attached_routers:
                errors.append(f"Host '{h_name}' is not connected to any router.")
            elif len(attached_routers) > 1:
                errors.append(f"Host '{h_name}' is connected to multiple routers ({', '.join(sorted(attached_routers))}). Multi-homing is currently not supported.")
            elif host.router and host.router not in attached_routers:
                errors.append(f"Host '{h_name}' specifies router '{host.router}' but is linked to {', '.join(repr(r) for r in attached_routers)}.")

        # 7. Router graph connectivity validation (A-5b)
        router_names = list(self.routers.keys())
        if len(router_names) >= 2:
            adj: Dict[str, Set[str]] = {r: set() for r in router_names}
            for link in self.links:
                if link.a in self.routers and link.b in self.routers:
                    adj[link.a].add(link.b)
                    adj[link.b].add(link.a)

            visited = set()
            components = []
            for r in router_names:
                if r not in visited:
                    comp = set()
                    q = [r]
                    visited.add(r)
                    while q:
                        curr = q.pop(0)
                        comp.add(curr)
                        for neighbor in adj[curr]:
                            if neighbor not in visited:
                                visited.add(neighbor)
                                q.append(neighbor)
                    components.append(comp)

            if len(components) > 1:
                comps_with_hosts = []
                for comp in components:
                    comp_hosts = [h_name for h_name, h in self.hosts.items() if h.router in comp]
                    if comp_hosts:
                        comps_with_hosts.append((comp, comp_hosts))

                if len(comps_with_hosts) > 1:
                    for i in range(len(comps_with_hosts)):
                        c_i_routers = sorted(list(comps_with_hosts[i][0]))
                        for j in range(i + 1, len(comps_with_hosts)):
                            c_j_routers = sorted(list(comps_with_hosts[j][0]))
                            errors.append(f"Disconnected router topology: hosts on {c_j_routers} cannot reach hosts on {c_i_routers} (no transit path).")

                # Also enumerate any isolated routers without hosts
                for comp in components:
                    comp_hosts = [h_name for h_name, h in self.hosts.items() if h.router in comp]
                    if not comp_hosts:
                        for r in sorted(list(comp)):
                            errors.append(f"Router '{r}' is isolated (no transit link) and has no attached hosts.")

        return errors

    def get_unreachable_router_pairs(self) -> List[Tuple[str, str]]:
        router_names = sorted(list(self.routers.keys()))
        if len(router_names) < 2:
            return []
        adj: Dict[str, Set[str]] = {r: set() for r in router_names}
        for link in self.links:
            if link.a in self.routers and link.b in self.routers:
                adj[link.a].add(link.b)
                adj[link.b].add(link.a)
        unreachable = []
        for i in range(len(router_names)):
            r1 = router_names[i]
            visited = {r1}
            q = [r1]
            while q:
                curr = q.pop(0)
                for neighbor in adj[curr]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        q.append(neighbor)
            for j in range(i + 1, len(router_names)):
                r2 = router_names[j]
                if r2 not in visited:
                    unreachable.append((r1, r2))
        return unreachable

    def allocate(self):
        """Allocate IP addresses for routers, hosts, and transit links."""
        # 1. Router IPs
        for r_name, router in self.routers.items():
            if router.ip is None or router.ip not in router.subnet:
                usable = list(router.subnet.hosts())
                if usable:
                    router.ip = usable[0]
                else:
                    router.ip = router.subnet.network_address + 1

        # 2. Host IPs
        for h_name, host in self.hosts.items():
            if host.router in self.routers:
                router = self.routers[host.router]
                if host.ip is None or host.ip not in router.subnet or host.ip == router.ip:
                    used_ips = set()
                    if router.ip and router.ip in router.subnet:
                        used_ips.add(router.ip)
                    for other_h in self.hosts.values():
                        if other_h.name != host.name and other_h.router == host.router and other_h.ip and other_h.ip in router.subnet:
                            used_ips.add(other_h.ip)

                    allocated = False
                    for candidate in router.subnet.hosts():
                        if candidate not in used_ips:
                            host.ip = candidate
                            allocated = True
                            break
                    if not allocated:
                        host.ip = None
                        err = f"Subnet exhausted for router '{router.name}'."
                        if err not in self.validation_errors:
                            self.validation_errors.append(err)

        # 3. Transit Router-to-Router links
        self.transit_links = []
        try:
            transit_subnets_iter = self.transit_subnet.subnets(new_prefix=30)
        except ValueError:
            return

        # Sort router links deterministically
        router_links = [l for l in self.links if l.a in self.routers and l.b in self.routers]
        router_links.sort(key=lambda l: (sorted([l.a, l.b]), l.a, l.b))

        for link in router_links:
            try:
                sub = next(transit_subnets_iter)
            except StopIteration:
                err = "Transit subnet pool exhausted (/30 subnets)."
                if err not in self.validation_errors:
                    self.validation_errors.append(err)
                break
            usable = list(sub.hosts())
            t_link = TransitLink(
                router_a=link.a,
                router_b=link.b,
                subnet=sub,
                ip_a=usable[0],
                ip_b=usable[1],
                link_spec=link
            )
            self.transit_links.append(t_link)

    def compute_routes(self) -> Dict[str, List[Dict[str, str]]]:
        """
        Compute static routes between routers using BFS on transit links.
        Returns a dict: router_name -> list of route dicts {"destination": CIDR, "via": nexthop_ip}
        """
        routes: Dict[str, List[Dict[str, str]]] = {r: [] for r in self.routers}

        # Build adjacency graph for routers
        adj: Dict[str, List[Tuple[str, ipaddress.IPv4Address]]] = {r: [] for r in self.routers}
        for t_link in self.transit_links:
            adj[t_link.router_a].append((t_link.router_b, t_link.ip_b))
            adj[t_link.router_b].append((t_link.router_a, t_link.ip_a))

        for src_router in self.routers:
            # BFS from src_router
            queue = deque([(src_router, [])])
            visited = {src_router}
            parent_nexthop = {}

            while queue:
                curr, path = queue.popleft()
                for neighbor, next_ip in adj[curr]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        first_nexthop = path[0][1] if path else next_ip
                        parent_nexthop[neighbor] = first_nexthop
                        queue.append((neighbor, path + [(neighbor, next_ip)]))

            # For each target router not src_router, add static route for target's subnet
            for dst_router, target_r in self.routers.items():
                if dst_router != src_router and dst_router in parent_nexthop:
                    via_ip = str(parent_nexthop[dst_router])
                    routes[src_router].append({
                        "destination": str(target_r.subnet),
                        "via": via_ip
                    })

        return routes

    def to_config_text(self) -> str:
        lines = ["topoType:Generic\n"]
        for r in self.routers.values():
            if r.ip:
                lines.append(f"router:{r.name},{r.subnet},{r.ip}\n")
            else:
                lines.append(f"router:{r.name},{r.subnet}\n")
        for h in self.hosts.values():
            prefix = h.role
            if h.ip:
                lines.append(f"{prefix}:{h.name},{h.router},{h.ip}\n")
            else:
                lines.append(f"{prefix}:{h.name},{h.router}\n")
        for l in self.links:
            lines.append(f"link:{l.a},{l.b},{l.delay},{l.queue_size},{l.bandwidth},{l.loss},{l.backup}\n")
        lines.append(f"transitSubnet:{self.transit_subnet}\n")
        lines.append(f"changeNetem:{'true' if self.change_netem else 'false'}\n")
        for link_idx, at_sec, cmd in self.netem_at_schedules:
            lines.append(f"netemAt:{link_idx},{at_sec},{cmd}\n")
        return "".join(lines)

    @classmethod
    def from_parameter(cls, param) -> "NetModel":
        topo_type = param.parameters.get("topoType")
        if not topo_type or topo_type != "Generic":
            raise ValueError(f"Unsupported topoType '{topo_type}': this editor supports 'Generic' only.")

        model = cls()
        model.change_netem = parse_bool(param.parameters.get("changeNetem", False))
        if "transitSubnet" in param.parameters:
            model.transit_subnet = ipaddress.IPv4Network(param.get("transitSubnet"))

        # Routers
        for line in param.get_list("router"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                name, subnet = parts[0], parts[1]
                ip = parts[2] if len(parts) >= 3 and parts[2] and parts[2].lower() != "none" else None
                model.add_router(name, subnet, ip)

        # Clients, Servers, and Idle hosts
        for role, key in [("client", "client"), ("server", "server"), ("idle", "idle")]:
            for line in param.get_list(key):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    name, router = parts[0], parts[1]
                    ip = parts[2] if len(parts) >= 3 and parts[2] and parts[2].lower() != "none" else None
                    model.add_host(name, role, router, ip)

        # Links
        for line in param.get_list("link"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                a, b = parts[0], parts[1]
                delay = float(parts[2]) if len(parts) > 2 else 15.0
                qs = int(parts[3]) if len(parts) > 3 else 1000
                bw = float(parts[4]) if len(parts) > 4 else 100.0
                loss = float(parts[5]) if len(parts) > 5 else 0.0
                backup = int(parts[6]) if len(parts) > 6 else 0
                model.add_link(a, b, delay, qs, bw, loss, backup)

        # Parse netemAt: <link_idx>,<at_sec>,<netem_cmd> (T-12c)
        for line in param.get_list("netemAt"):
            parts = [p.strip() for p in line.split(",", 2)]
            if len(parts) >= 3:
                try:
                    link_idx = int(parts[0])
                    at_sec = float(parts[1])
                    cmd = parts[2]
                    if 0 <= link_idx < len(model.links):
                        model.netem_at_schedules.append((link_idx, at_sec, cmd))
                    else:
                        model.validation_errors.append(
                            f"netemAt specifies invalid link index {link_idx} (valid: 0..{len(model.links)-1})"
                        )
                except ValueError as e:
                    model.validation_errors.append(f"Invalid netemAt format '{line}': {e}")

        model.allocate()
        return model

    def to_dict(self) -> dict:
        return {
            "routers": [
                {"name": r.name, "subnet": str(r.subnet), "ip": str(r.ip) if r.ip else None}
                for r in self.routers.values()
            ],
            "hosts": [
                {"name": h.name, "role": h.role, "router": h.router, "ip": str(h.ip) if h.ip else None}
                for h in self.hosts.values()
            ],
            "links": [
                {"a": l.a, "b": l.b, "delay": l.delay, "queue_size": l.queue_size,
                 "bandwidth": l.bandwidth, "loss": l.loss, "backup": l.backup}
                for l in self.links
            ],
            "transit_subnet": str(self.transit_subnet),
            "change_netem": self.change_netem
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NetModel":
        model = cls()
        model.change_netem = d.get("change_netem", False)
        if "transit_subnet" in d:
            model.transit_subnet = ipaddress.IPv4Network(d["transit_subnet"])

        for r in d.get("routers", []):
            model.add_router(r["name"], r["subnet"], r.get("ip"))

        for h in d.get("hosts", []):
            model.add_host(h["name"], h.get("role", "client"), h["router"], h.get("ip"))

        for l in d.get("links", []):
            model.add_link(
                l["a"], l["b"],
                delay=float(l.get("delay", 15.0)),
                queue_size=int(l.get("queue_size", 1000)),
                bandwidth=float(l.get("bandwidth", 100.0)),
                loss=float(l.get("loss", 0.0)),
                backup=int(l.get("backup", 0))
            )

        model.allocate()
        return model


"""Structured JSON specification schema dataclasses for GAIA skills (T-1, T-6).
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Any, Union
import json


@dataclass
class PathSpec:
    delay_ms: float = 15.0
    bandwidth_mbps: float = 100.0
    queue_size: int = 1000
    loss_pct: float = 0.0
    backup: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PathSpec":
        return cls(
            delay_ms=float(data.get("delay_ms", 15.0)),
            bandwidth_mbps=float(data.get("bandwidth_mbps", 100.0)),
            queue_size=int(data.get("queue_size", 1000)),
            loss_pct=float(data.get("loss_pct", 0.0)),
            backup=bool(data.get("backup", False)),
        )


@dataclass
class NetemScheduleSpec:
    at_sec: float
    cmd: str
    # MultiIf identifiers
    path_type: str = "c2r"  # 'c2r' or 'r2s'
    path_id: int = 0
    # Generic identifiers (either link_nodes or link_idx)
    link_idx: Optional[int] = None
    link_nodes: Optional[Tuple[str, str]] = None  # e.g. ('c0', 'r0')

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetemScheduleSpec":
        nodes = data.get("link_nodes")
        if nodes is not None:
            if isinstance(nodes, (list, tuple)) and len(nodes) == 2:
                nodes = (str(nodes[0]), str(nodes[1]))
            else:
                nodes = None

        return cls(
            at_sec=float(data["at_sec"]),
            cmd=str(data["cmd"]),
            path_type=str(data.get("path_type", "c2r")),
            path_id=int(data.get("path_id", 0)),
            link_idx=int(data["link_idx"]) if "link_idx" in data and data["link_idx"] is not None else None,
            link_nodes=nodes,
        )


@dataclass
class TopoSpec:
    name: str
    intent: str = "single_path"  # 'single_path' | 'mptcp_multipath' | 'multi_router'
    paths: List[PathSpec] = field(default_factory=lambda: [PathSpec()])
    server_paths: List[PathSpec] = field(default_factory=lambda: [PathSpec()])
    clients: int = 1
    servers: int = 1
    routers_count: int = 1  # For multi_router intent (must be >= 2)
    transit_path: Optional[PathSpec] = None
    netem_schedule: List[NetemScheduleSpec] = field(default_factory=list)
    base_topo: Optional[str] = None
    override: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TopoSpec":
        if "name" not in data or not str(data["name"]).strip():
            raise ValueError("TopoSpec requires non-empty 'name'.")

        name = str(data["name"]).strip()
        base_topo = data.get("base_topo")
        override = data.get("override")
        intent = str(data.get("intent", "single_path")).strip().lower()
        if intent not in ("single_path", "mptcp_multipath", "multi_router"):
            raise ValueError(f"Invalid intent '{intent}': must be single_path, mptcp_multipath, or multi_router.")

        paths_raw = data.get("paths", [{"delay_ms": 15.0}])
        paths = [PathSpec.from_dict(p) for p in paths_raw] if paths_raw else [PathSpec()]

        server_paths_raw = data.get("server_paths", [{"delay_ms": 15.0}])
        server_paths = [PathSpec.from_dict(p) for p in server_paths_raw] if server_paths_raw else [PathSpec()]

        clients = int(data.get("clients", 1))
        servers = int(data.get("servers", 1))
        routers_count = int(data.get("routers_count", 1))

        transit_raw = data.get("transit_path")
        transit_path = PathSpec.from_dict(transit_raw) if transit_raw else None

        netem_raw = data.get("netem_schedule", [])
        netem_schedule = [NetemScheduleSpec.from_dict(n) for n in netem_raw]

        return cls(
            name=name,
            intent=intent,
            paths=paths,
            server_paths=server_paths,
            clients=clients,
            servers=servers,
            routers_count=routers_count,
            transit_path=transit_path,
            netem_schedule=netem_schedule,
            base_topo=base_topo,
            override=override,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkloadSpec:
    node: str
    kind: str = "none"  # 'none' | 'iperf3_client' | 'iperf3_server' | 'mesh_ping' | 'custom_client' | 'custom_server'
    command: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkloadSpec":
        return cls(
            node=str(data["node"]).strip(),
            kind=str(data.get("kind", "none")).strip(),
            command=str(data["command"]).strip() if data.get("command") else None,
        )


@dataclass
class ScenarioSpec:
    name: str
    topo_file: Optional[str] = None
    xp_type: str = "iperf3_exp"  # 'iperf3_exp' | 'mesh_ping' | 'performance-analysis' | 'workload_exp'
    mptcp: str = "auto"  # 'auto' | 'yes' | 'no'
    pcap: str = "no"
    pcap_nodes: List[str] = field(default_factory=list)
    # iperf3_exp options
    duration: int = 10
    parallel: int = 1
    reverse: bool = False
    congctrl: str = "cubic"
    # mesh_ping options
    ping_count: int = 3
    ping_timeout: int = 1
    max_probes: int = 200
    strict_mesh: bool = False
    # performance-analysis options
    pa_protocol: str = "udp"  # 'udp' | 'tcp' | 'quic'
    pa_packet_size: int = 100
    pa_count: int = 10
    pa_exp: str = "simple"
    # Per-node workloads (WP-3, Phase 5)
    workloads: List[WorkloadSpec] = field(default_factory=list)
    # Base scenario override options
    base_xp: Optional[str] = None
    override: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScenarioSpec":
        if "name" not in data or not str(data["name"]).strip():
            raise ValueError("ScenarioSpec requires non-empty 'name'.")

        name = str(data["name"]).strip()
        base_xp = data.get("base_xp")
        override = data.get("override")
        topo_file = str(data["topo_file"]).strip() if "topo_file" in data and data["topo_file"] is not None else None

        if not topo_file and not base_xp:
            raise ValueError("ScenarioSpec requires non-empty 'topo_file' or 'base_xp'.")

        xp_type = str(data.get("xp_type", "iperf3_exp")).strip()
        mptcp = str(data.get("mptcp", "auto")).strip().lower()
        pcap = str(data.get("pcap", "no")).strip().lower()

        pcap_nodes_raw = data.get("pcap_nodes", [])
        if isinstance(pcap_nodes_raw, str):
            pcap_nodes = [n.strip() for n in pcap_nodes_raw.split(",") if n.strip()]
        else:
            pcap_nodes = [str(n).strip() for n in pcap_nodes_raw if str(n).strip()]

        workloads_raw = data.get("workloads", [])
        workloads = [WorkloadSpec.from_dict(w) for w in workloads_raw] if workloads_raw else []

        return cls(
            name=name,
            topo_file=topo_file,
            xp_type=xp_type,
            mptcp=mptcp,
            pcap=pcap,
            pcap_nodes=pcap_nodes,
            duration=int(data.get("duration", 10)),
            parallel=int(data.get("parallel", 1)),
            reverse=bool(data.get("reverse", False)),
            congctrl=str(data.get("congctrl", "cubic")),
            ping_count=int(data.get("ping_count", 3)),
            ping_timeout=int(data.get("ping_timeout", 1)),
            max_probes=int(data.get("max_probes", 200)),
            strict_mesh=bool(data.get("strict_mesh", False)),
            pa_protocol=str(data.get("pa_protocol", "udp")),
            pa_packet_size=int(data.get("pa_packet_size", 100)),
            pa_count=int(data.get("pa_count", 10)),
            pa_exp=str(data.get("pa_exp", "simple")),
            workloads=workloads,
            base_xp=base_xp,
            override=override,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

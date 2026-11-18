"""Configuration validation pipeline (S2, rootless) — WF-4, WF-11, WF-16, T-4.

Provides static validation of topology and experiment files before execution.
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

from core.topo import TopoParameter, Topo, parse_bool, iface_name
from core.experiment import ExperimentParameter
from core.netmodel import NetModel


def validate_config(topo_file: str, xp_file: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
    """
    Validate topology file and optional experiment file.
    Returns (is_valid, report_dict).
    """
    from runner import TOPOS, TOPO_CONFIGS, EXPERIMENTS

    report = {
        "valid": True,
        "topo_file": topo_file,
        "xp_file": xp_file,
        "errors": [],
        "warnings": [],
        "details": {}
    }

    if not os.path.exists(topo_file):
        report["valid"] = False
        report["errors"].append(f"Topology file '{topo_file}' not found.")
        return False, report

    try:
        topo_param = TopoParameter(topo_file)
    except Exception as e:
        report["valid"] = False
        report["errors"].append(f"Failed to parse topology parameter file: {e}")
        return False, report

    topo_type = topo_param.get("topoType")
    report["details"]["topo_type"] = topo_type

    if topo_type not in TOPOS:
        report["valid"] = False
        report["errors"].append(f"Unknown or unsupported topoType: '{topo_type}'")
        return False, report

    # 1. Generic Topo Validation with NetModel
    if topo_type == "Generic":
        try:
            model = NetModel.from_parameter(topo_param)
            model_errors = model.validate()
            if model_errors:
                report["valid"] = False
                report["errors"].extend(model_errors)

            unreachable = model.get_unreachable_router_pairs()
            if unreachable:
                report["valid"] = False
                report["errors"].append(f"Unreachable router pairs in topology: {unreachable}")

            report["details"]["routers"] = len(model.routers)
            report["details"]["hosts"] = len(model.hosts)
            report["details"]["links"] = len(model.links)
            report["details"]["clients"] = model.hosts_by_role("client")
            report["details"]["servers"] = model.hosts_by_role("server")
            report["details"]["idle_hosts"] = model.hosts_by_role("idle")
            report["details"]["client_max_eps"] = 1
            report["details"]["server_max_eps"] = 1
            report["details"]["mptcp_capable"] = False

            # Check IFNAMSIZ for generated interfaces (max 15 chars)
            for node_name in list(model.routers.keys()) + list(model.hosts.keys()):
                for i in range(10):
                    iface = f"{node_name}-eth{i}"
                    if len(iface) > 15:
                        report["valid"] = False
                        report["errors"].append(f"Interface name '{iface}' exceeds IFNAMSIZ limit of 15 characters.")

        except Exception as e:
            report["valid"] = False
            report["errors"].append(f"NetModel validation failed: {e}")
    else:
        # MultiInterface / MultiInterfaceMultiClients validation
        clients = int(topo_param.get("clients", 1))
        servers = int(topo_param.get("servers", 1))
        report["details"]["clients"] = clients
        report["details"]["servers"] = servers

        client_prefix = topo_param.get("client_name_prefix", Topo.CLIENT_NAME_PREFIX)
        server_prefix = topo_param.get("server_name_prefix", Topo.SERVER_NAME_PREFIX)
        router_prefix = topo_param.get("router_name_prefix", Topo.ROUTER_NAME_PREFIX)

        c2r_paths = len([k for k in topo_param.parameters if k.startswith("path_c2r_")])
        r2s_paths = len([k for k in topo_param.parameters if k.startswith("path_r2s_")])
        client_max_eps = max(1, c2r_paths)
        server_max_eps = max(1, r2s_paths)
        mptcp_capable = (client_max_eps >= 2 or server_max_eps >= 2)
        report["details"]["client_max_eps"] = client_max_eps
        report["details"]["server_max_eps"] = server_max_eps
        report["details"]["mptcp_capable"] = mptcp_capable

        # Check IFNAMSIZ for clients, servers, routers using iface_name() (P2-2)
        for c in range(clients):
            c_name = f"{client_prefix}_{c}"
            for i in range(client_max_eps):
                iface = iface_name(c_name, i)
                if len(iface) > 15:
                    report["valid"] = False
                    report["errors"].append(f"Client interface name '{iface}' exceeds IFNAMSIZ limit of 15 characters.")

        for s in range(servers):
            s_name = f"{server_prefix}_{s}"
            for j in range(server_max_eps):
                iface = iface_name(s_name, j)
                if len(iface) > 15:
                    report["valid"] = False
                    report["errors"].append(f"Server interface name '{iface}' exceeds IFNAMSIZ limit of 15 characters.")

        # Check netemAt targets exist (F-2)
        for k in topo_param.parameters:
            if k.startswith("netemAt_"):
                parts = k.split("_")
                if len(parts) >= 3:
                    p_type = parts[1]
                    try:
                        p_id = int(parts[2])
                    except ValueError:
                        p_id = -1
                    target_path_key = f"path_{p_type}_{p_id}"
                    if target_path_key not in topo_param.parameters:
                        report["valid"] = False
                        report["errors"].append(f"netemAt target '{target_path_key}' is not defined in topology.")
                else:
                    report["valid"] = False
                    report["errors"].append(f"Invalid netemAt parameter format '{k}'.")

    # 2. Experiment Parameter Validation (if provided)
    if xp_file:
        if not os.path.exists(xp_file):
            report["valid"] = False
            report["errors"].append(f"Experiment file '{xp_file}' not found.")
            return False, report

        try:
            xp_param = ExperimentParameter(xp_file)
            xp_type = xp_param.get("xpType")
            report["details"]["xp_type"] = xp_type

            if xp_type not in EXPERIMENTS and xp_type != "none":
                report["valid"] = False
                report["errors"].append(f"Unknown xpType '{xp_type}'. Known: {list(EXPERIMENTS.keys())}")

            # Workload schema validation and D-10 Generic restriction
            workload_lines = xp_param.get_list("workload")
            if xp_type == "workload_exp" or workload_lines:
                if topo_type != "Generic":
                    report["valid"] = False
                    report["errors"].append(f"Per-node workload requires topoType:Generic (got '{topo_type}').")

            custom_commands = []
            if workload_lines:
                valid_nodes = set()
                if topo_type == "Generic":
                    try:
                        m_topo = NetModel.from_parameter(topo_param)
                        valid_nodes = set(m_topo.hosts.keys()).union(set(m_topo.routers.keys()))
                    except Exception:
                        pass

                from core.workload import parse_workload_line, ALLOWED_PLACEHOLDER_NAMES, ALL_BRACES_REGEX, PLACEHOLDER_REGEX
                for wline in workload_lines:
                    try:
                        witem = parse_workload_line(wline)
                        if valid_nodes and witem.node not in valid_nodes:
                            report["valid"] = False
                            report["errors"].append(f"Workload references unknown node '{witem.node}' not present in topology.")
                        if witem.command:
                            for brace_token in ALL_BRACES_REGEX.findall(witem.command):
                                m = PLACEHOLDER_REGEX.fullmatch(brace_token)
                                if not m or m.group(1) not in ALLOWED_PLACEHOLDER_NAMES:
                                    report["valid"] = False
                                    report["errors"].append(f"Unknown or invalid placeholder '{brace_token}' in command for node '{witem.node}': {witem.command}")
                            custom_commands.append({
                                "node": witem.node,
                                "kind": witem.kind,
                                "command": witem.command
                            })
                    except Exception as we:
                        report["valid"] = False
                        report["errors"].append(str(we))

            report["details"]["custom_commands"] = custom_commands

            # Selective PCAP nodes validation & collision warning
            pcap_nodes_raw = xp_param.get(ExperimentParameter.PCAP_NODES, "")
            pcap_node_list = [n.strip() for n in pcap_nodes_raw.split(",") if n.strip()]
            if pcap_node_list:
                valid_nodes = set()
                if topo_type == "Generic":
                    try:
                        m_topo = NetModel.from_parameter(topo_param)
                        valid_nodes = set(m_topo.hosts.keys()).union(set(m_topo.routers.keys()))
                    except Exception:
                        pass
                else:
                    try:
                        valid_nodes = set()
                        c_count = int(topo_param.get(TopoParameter.CLIENTS, 1))
                        s_count = int(topo_param.get(TopoParameter.SERVERS, 1))
                        c_pfx = topo_param.get("client_name_prefix", Topo.CLIENT_NAME_PREFIX)
                        s_pfx = topo_param.get("server_name_prefix", Topo.SERVER_NAME_PREFIX)
                        r_pfx = topo_param.get("router_name_prefix", Topo.ROUTER_NAME_PREFIX)
                        for i in range(c_count):
                            valid_nodes.add(f"{c_pfx}_{i}")
                            valid_nodes.add(f"Client_{i}")
                            valid_nodes.add(f"client_{i}")
                            valid_nodes.add(f"c{i}")
                        for i in range(s_count):
                            valid_nodes.add(f"{s_pfx}_{i}")
                            valid_nodes.add(f"Server_{i}")
                            valid_nodes.add(f"server_{i}")
                            valid_nodes.add(f"s{i}")
                        valid_nodes.add(f"{r_pfx}_0")
                        valid_nodes.add("Router_0")
                        valid_nodes.add("router_0")
                        valid_nodes.add("r0")
                    except Exception:
                        valid_nodes = None

                if valid_nodes:
                    for n in pcap_node_list:
                        if n not in valid_nodes:
                            report["valid"] = False
                            report["errors"].append(f"pcap_nodes references unknown node '{n}' not present in topology.")

                pcap_flag = xp_param.get(ExperimentParameter.PCAP, "no")
                if pcap_flag == 'yes' and pcap_node_list:
                    report["warnings"].append(
                        "Both 'pcap:yes' and 'pcap_nodes' are set. Any node covered by the global pcap/"
                        "clientPcap/serverPcap flags (all routers, plus clients/servers if those flags are "
                        "'yes') will be captured once via the global path; pcap_nodes will skip it there to "
                        "avoid a duplicate tcpdump process, not capture it twice."
                    )

            report["details"]["pcap_nodes"] = pcap_node_list

            # Check unwired parameters warning
            unwired = ["mptcpEnabled", "autocork", "earlyRetrans", "bufferAutotuning"]
            for u in unwired:
                if u in xp_param.parameters:
                    report["warnings"].append(f"Parameter '{u}' is declared in xp file but unwired in backend.")

            # WF-16 / P2-3 Static check: Universal MPTCP Feasibility
            mptcp_req = parse_bool(xp_param.get("mptcp", "no")) or parse_bool(xp_param.get("mptcpEnabled", "0"))
            if mptcp_req:
                if topo_type == "Generic":
                    client_max_eps = 1
                    server_max_eps = 1
                    report["valid"] = False
                    report["errors"].append(
                        "MPTCP multipath (mptcp:yes) is impossible on Generic topology because Generic hosts "
                        "have only a single IP address (client and server max endpoints < 2). "
                        "Use MultiIf topology with multiple paths for MPTCP experiments (WF-16)."
                    )
                elif client_max_eps < 2 and server_max_eps < 2:
                    report["valid"] = False
                    report["errors"].append(
                        f"MPTCP multipath (mptcp:yes) is impossible because topology '{topo_type}' has only "
                        f"{client_max_eps} client endpoint(s) and {server_max_eps} server endpoint(s). "
                        "Multipath requires at least 2 endpoints on client or server. "
                        "Use MultiIf topology with multiple paths for MPTCP experiments (WF-16)."
                    )
        except Exception as e:
            report["valid"] = False
            report["errors"].append(f"Failed to parse experiment parameter file: {e}")

    return report["valid"], report

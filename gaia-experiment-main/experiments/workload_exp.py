"""Per-node workload execution engine (WP-4a, D-8, D-8', D-9, Phase 5).

Executes heterogeneous per-node workloads (iperf3, custom client/server, mesh ping)
with fail-closed lifecycle guarantees, PID tracking, and rc polling.
"""
import os
import time
import json
import logging
from typing import Dict, List, Any, Optional

from core.experiment import Experiment, ExperimentParameter
from core.topo import parse_bool, iface_name
from core.workload import parse_workload_line, bind_placeholders, WorkloadItem, UnknownPlaceholderError


class WorkloadExp(Experiment):
    NAME = "workload_exp"

    def __init__(self, experiment_parameter_file, topo, topo_config, **kwargs):
        super(WorkloadExp, self).__init__(experiment_parameter_file, topo, topo_config, **kwargs)
        self.workload_items: List[WorkloadItem] = []
        self.bound_workloads: List[Dict[str, Any]] = []
        self.server_processes: List[Dict[str, Any]] = []
        self.client_rc_files: List[Dict[str, Any]] = []

    def prepare(self):
        super(WorkloadExp, self).prepare()
        logging.info("Preparing WorkloadExp...")

        duration = int(self.experiment_parameter.get("duration", 10))
        congctrl = str(self.experiment_parameter.get("congctrl", "cubic"))
        is_mptcp = parse_bool(self.experiment_parameter.get("mptcp", "no"))
        mptcp_flag = "--mptcp" if is_mptcp else ""

        # 1. Parse workload items from parameter
        raw_workloads = self.experiment_parameter.get_list("workload")
        self.workload_items = [parse_workload_line(w) for w in raw_workloads]

        # 2. Build IP context map from NetModel / TopoConfig
        node_ips = {}
        net_model = getattr(self.topo_config, 'net_model', None)
        if net_model and hasattr(net_model, 'hosts'):
            for h_name, h_spec in net_model.hosts.items():
                if h_spec.ip:
                    node_ips[h_name] = str(h_spec.ip)

        # Fallback to topology nodes IP resolution
        all_hosts = getattr(self.topo_config, 'all_hosts', [])
        for host in all_hosts:
            h_name = getattr(host, 'name', str(host))
            if h_name not in node_ips and hasattr(host, 'IP'):
                try:
                    ip_val = host.IP()
                    if ip_val:
                        node_ips[h_name] = str(ip_val)
                except Exception:
                    pass

        # Determine default server_ip and client_ip
        servers = getattr(self.topo_config, 'servers', [])
        clients = getattr(self.topo_config, 'clients', [])
        default_server_ip = node_ips.get(getattr(servers[0], 'name', 's0')) if servers else (
            next((ip for name, ip in node_ips.items() if name.startswith("s")), None)
        )
        default_client_ip = node_ips.get(getattr(clients[0], 'name', 'c0')) if clients else (
            next((ip for name, ip in node_ips.items() if name.startswith("c")), None)
        )

        # 3. Resolve and bind commands
        self.bound_workloads = []
        for idx, item in enumerate(self.workload_items):
            ctx = {
                "node": item.node,
                "iface": f"{item.node}-eth0",
                "duration": duration,
                "output_dir": self.output_dir,
                "node_ips": node_ips,
            }
            if default_server_ip:
                ctx["server_ip"] = default_server_ip
            if default_client_ip:
                ctx["client_ip"] = default_client_ip

            bound_cmd = None
            if item.kind == "iperf3_server":
                server_log = os.path.join(self.output_dir, f"iperf3-server-{item.node}.json")
                bound_cmd = f"iperf3 -s -J --logfile {server_log} {mptcp_flag}"
            elif item.kind == "iperf3_client":
                client_log = os.path.join(self.output_dir, f"iperf3-client-{item.node}.json")
                parallel = int(self.experiment_parameter.get("parallel", 1))
                target_ip = default_server_ip or "127.0.0.1"
                bound_cmd = f"iperf3 -c {target_ip} -t {duration} -P {parallel} -C {congctrl} -J --logfile {client_log} {mptcp_flag}"
            elif item.kind == "mesh_ping":
                target_ip = default_server_ip or "127.0.0.1"
                bound_cmd = f"ping -c 3 -W 1 {target_ip}"
            elif item.kind in ("custom_server", "custom_client"):
                bound_cmd = bind_placeholders(item.command or "", ctx)

            self.bound_workloads.append({
                "index": idx,
                "node": item.node,
                "kind": item.kind,
                "raw_command": item.command,
                "bound_command": bound_cmd,
            })

        # 4. Save workloads.json in output_dir (M-7 / D-12)
        os.makedirs(self.output_dir, exist_ok=True)
        workloads_json_path = os.path.join(self.output_dir, "workloads.json")
        with open(workloads_json_path, "w", encoding="utf-8") as f:
            json.dump({
                "xp_type": "workload_exp",
                "duration": duration,
                "workloads": self.bound_workloads
            }, f, indent=2)

    def run(self):
        logging.info("Running WorkloadExp...")
        duration = int(self.experiment_parameter.get("duration", 10))
        timeout_sec = duration + 60

        # Clean stale rc files before starting (H-2 rule)
        for w in self.bound_workloads:
            rc_file = os.path.join(self.output_dir, f"workload-{w['index']}-rc.txt")
            if os.path.exists(rc_file):
                try:
                    os.unlink(rc_file)
                except Exception:
                    pass

        # 1. Start Server Workloads in Background (D-8', WP-4a, P2-1)
        self.server_processes = []
        for w in self.bound_workloads:
            if w["kind"] in ("iperf3_server", "custom_server"):
                node = self.topo.get_host(w["node"])
                rc_file = os.path.join(self.output_dir, f"workload-{w['index']}-rc.txt")
                pid_file = os.path.join(self.output_dir, f"workload-{w['index']}-pid.txt")
                log_file = os.path.join(self.output_dir, f"workload-{w['index']}.log")

                cmd_str = f"{w['bound_command']} > {log_file} 2>&1 & echo $! > {pid_file}"
                self.topo.command_to(node, cmd_str)
                self.server_processes.append({
                    "node": node,
                    "node_name": w["node"],
                    "index": w["index"],
                    "pid_file": pid_file,
                    "rc_file": rc_file,
                    "log_file": log_file,
                })

        # 2. Wait 1.0s for server listeners to be established
        time.sleep(1.0)

        # 3. Start Client Workloads in Background (D-8', WP-4a)
        self.client_rc_files = []
        for w in self.bound_workloads:
            if w["kind"] in ("iperf3_client", "custom_client", "mesh_ping"):
                node = self.topo.get_host(w["node"])
                rc_file = os.path.join(self.output_dir, f"workload-{w['index']}-rc.txt")
                log_file = os.path.join(self.output_dir, f"workload-{w['index']}.log")

                cmd_str = f"{{ {w['bound_command']} > {log_file} 2>&1; echo rc=$? > {rc_file}; }} &"
                self.topo.command_to(node, cmd_str)
                self.client_rc_files.append({
                    "node": node,
                    "node_name": w["node"],
                    "index": w["index"],
                    "rc_file": rc_file,
                    "log_file": log_file,
                    "kind": w["kind"],
                })

        # 4. Poll Client Completion via RC Files (D-8' / H-3 polling)
        start_time = time.time()
        pending_clients = list(self.client_rc_files)
        while pending_clients and (time.time() - start_time) < timeout_sec:
            still_pending = []
            for client_info in pending_clients:
                rc_file = client_info["rc_file"]
                if os.path.exists(rc_file) and os.path.getsize(rc_file) > 0:
                    continue
                still_pending.append(client_info)
            pending_clients = still_pending
            if pending_clients:
                time.sleep(0.2)

        # 5. Handle Timeouts if Any Clients Remained Unfinished
        for client_info in pending_clients:
            logging.warning(f"Client workload on node {client_info['node_name']} timed out after {timeout_sec}s.")
            rc_file = client_info["rc_file"]
            if not os.path.exists(rc_file):
                with open(rc_file, "w") as f:
                    f.write("rc=124\n")

    def clean(self):
        logging.info("Cleaning up WorkloadExp...")
        # 1. Terminate server processes recorded during run
        for srv in self.server_processes:
            pid_file = srv["pid_file"]
            node = srv["node"]
            if os.path.exists(pid_file):
                try:
                    with open(pid_file, "r") as f:
                        pid = f.read().strip()
                    if pid and pid.isdigit():
                        self.topo.command_to(node, f"kill {pid} 2>/dev/null || true")
                        time.sleep(0.1)
                        self.topo.command_to(node, f"kill -9 {pid} 2>/dev/null || true")
                except Exception as e:
                    logging.warning(f"Error terminating server on {srv['node_name']}: {e}")

        # 2. Call base experiment clean (sysctl, tcpdump, etc.)
        super(WorkloadExp, self).clean()

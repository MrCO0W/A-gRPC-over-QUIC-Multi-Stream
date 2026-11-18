"""iperf3 MPTCP & TCP Performance Experiment — WF-5, WF-19, T-10, T-11.

Generates MPTCP/TCP traffic via iperf3, executes async sampling loop for subflows,
and records subflows.json.
"""
import json
import logging
import os
import re
import time
from typing import Any, Dict, List

from core.experiment import Experiment, ExperimentParameter
from core.topo import parse_bool


class Iperf3Exp(Experiment):
    EXPTYPE = "iperf3_exp"
    NAME = "iperf3_exp"

    def __init__(self, experiment_parameter_filename, topo, topo_config, **kwargs):
        super(Iperf3Exp, self).__init__(
            experiment_parameter_filename, topo, topo_config, **kwargs)
        self.subflows_data: Dict[str, Any] = {}

    def _parse_subflows_from_ss(self, ss_output: str) -> int:
        """
        Count active MPTCP subflows from 'ss -Mi' output.
        Accurately parses 'subflows:<N>' counter (N additional + 1 initial = N+1)
        or explicit 'subflow flags:' entry blocks.
        Returns 0 if no MPTCP connection is established.
        """
        if not ss_output or not ss_output.strip():
            return 0

        # Check if any MPTCP connection exists
        has_mptcp = bool(re.search(r"\b(?:mptcp|tcp-ulp-mptcp)\b", ss_output, re.IGNORECASE))
        has_subflow_flags = bool(re.search(r"\bsubflow\s+flags:", ss_output, re.IGNORECASE))
        if not has_mptcp and not has_subflow_flags:
            return 0

        # 1. Match subflows:<N> field in MPTCP connection summary
        subflow_num_match = re.search(r"\bsubflows:(\d+)\b", ss_output, re.IGNORECASE)
        if subflow_num_match:
            additional_subflows = int(subflow_num_match.group(1))
            return additional_subflows + 1

        # 2. Count distinct subflow descriptor lines (subflow flags:...)
        subflow_lines = re.findall(r"^\s*subflow\s+flags:", ss_output, re.MULTILINE | re.IGNORECASE)
        if subflow_lines:
            return len(subflow_lines)

        # 3. If MPTCP is active but 0 additional subflows reported
        return 1

    def run(self):
        clients = getattr(self.topo_config, 'clients', [])
        servers = getattr(self.topo_config, 'servers', [])
        if not clients or not servers:
            logging.warning("Iperf3Exp: no clients or servers found, skipping run.")
            return

        is_mptcp = parse_bool(self.experiment_parameter.get("mptcp", "no")) or parse_bool(self.experiment_parameter.get("mptcpEnabled", "0"))
        duration = int(self.experiment_parameter.get("duration", 10))
        parallel = int(self.experiment_parameter.get("parallel", 1))
        reverse = parse_bool(self.experiment_parameter.get("reverse", "no"))
        mptcp_flag = "--mptcp" if is_mptcp else ""
        reverse_flag = "-R" if reverse else ""

        # 1. Start iperf3 server(s) in background
        for s_idx, server in enumerate(servers):
            server_log = os.path.join(self.output_dir, f"server-{s_idx}.json")
            server_cmd = f"iperf3 -s -1 -J --logfile {server_log} {mptcp_flag} &"
            self.topo.command_to(server, server_cmd)

        # 2. Wait 1 second for server listener establishment
        time.sleep(1.0)

        # 3. Start iperf3 client(s) in background (T-11, P-7)
        target_ip = self.topo_config.get_server_ip(0, 0)
        client = clients[0]
        client_log = os.path.join(self.output_dir, "client-0.json")
        client_rc_file = os.path.join(self.output_dir, "client-0-rc.txt")
        client_cmd = f"{{ iperf3 -c {target_ip} -t {duration} -P {parallel} -J --logfile {client_log} {mptcp_flag} {reverse_flag}; echo rc=$? > {client_rc_file}; }} &"
        self.topo.command_to(client, client_cmd)

        # 4. Multi-sampling loop: 3 samples around t_sample (T-11)
        t_sample = max(0.5, min(2.0, duration * 0.5))
        sample_interval = max(0.2, t_sample / 2.0)
        samples = []
        max_subflows = 0

        time.sleep(sample_interval)
        for i in range(3):
            ss_raw = self.topo.command_to(client, "ss -Mi")
            ep_raw = self.topo.command_to(client, "ip mptcp endpoint show")
            subflows_count = self._parse_subflows_from_ss(str(ss_raw))
            if subflows_count > max_subflows:
                max_subflows = subflows_count

            samples.append({
                "sample_index": i + 1,
                "timestamp": time.time(),
                "ss_raw": str(ss_raw),
                "endpoint_raw": str(ep_raw),
                "subflows": subflows_count
            })
            if i < 2:
                time.sleep(sample_interval)

        # 5. Wait for background client & server processes to finish
        for node in clients + servers:
            self.topo.command_to(node, "wait")

        # 6. Read client return code and error if any (P-7)
        client_rc = 0
        if os.path.exists(client_rc_file):
            try:
                with open(client_rc_file, "r") as f:
                    content = f.read().strip()
                    if "rc=" in content:
                        client_rc = int(content.split("rc=")[-1].strip())
                    elif content.isdigit():
                        client_rc = int(content)
            except Exception:
                pass

        iperf3_error = None
        if os.path.exists(client_log):
            try:
                with open(client_log, "r", encoding="utf-8") as f:
                    jdata = json.load(f)
                    if "error" in jdata:
                        iperf3_error = jdata["error"]
            except Exception:
                pass

        if client_rc != 0 or iperf3_error:
            logging.warning(f"iperf3 client reported non-zero rc={client_rc}, error={iperf3_error}")

        # 7. Record subflows.json
        self.subflows_data = {
            "mptcp_requested": is_mptcp,
            "duration": duration,
            "max_subflows": max_subflows,
            "client_rc": client_rc,
            "error": iperf3_error,
            "samples": samples
        }
        subflows_file = os.path.join(self.output_dir, "subflows.json")
        try:
            with open(subflows_file, "w", encoding="utf-8") as f:
                json.dump(self.subflows_data, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to write subflows.json: {e}")

"""Endpoint-level Tiered Mesh Ping Experiment (S3 Preflight) — WF-4, T-5.

Runs reachability checks across host interfaces, enforces tiered pass/fail gates,
and outputs reachability.json.
"""
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Tuple

from core.experiment import Experiment, ExperimentParameter
from core.topo import parse_bool


class MeshPingExp(Experiment):
    EXPTYPE = "mesh_ping"
    NAME = "mesh_ping"

    def __init__(self, experiment_parameter_filename, topo, topo_config, **kwargs):
        super(MeshPingExp, self).__init__(
            experiment_parameter_filename, topo, topo_config, **kwargs)
        self.reachability_results: Dict[str, Any] = {}

    def _parse_ping_output(self, raw_output: str) -> Dict[str, Any]:
        """
        Parse standard Linux ping output for loss and RTT statistics.
        """
        sent, received, loss_pct = 0, 0, 100.0
        rtt_avg = None

        # Matches: "3 packets transmitted, 3 received, 0% packet loss, time 2004ms"
        loss_match = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received,.*?(\d+(?:\.\d+)?)%\s+packet loss", raw_output, re.IGNORECASE)
        if loss_match:
            sent = int(loss_match.group(1))
            received = int(loss_match.group(2))
            loss_pct = float(loss_match.group(3))

        # Matches: "rtt min/avg/max/mdev = 0.045/0.062/0.081/0.015 ms"
        rtt_match = re.search(r"(?:rtt|round-trip)\s+min/avg/max(?:/mdev)?\s*=\s*[\d\.]+/([\d\.]+)/", raw_output, re.IGNORECASE)
        if rtt_match:
            rtt_avg = float(rtt_match.group(1))

        return {
            "sent": sent,
            "received": received,
            "loss_pct": loss_pct,
            "rtt_avg_ms": rtt_avg,
            "ok": bool(received > 0 and loss_pct < 100.0)
        }

    def prepare(self):
        """
        Lightweight prepare for preflight reachability:
        Does not inject MPTCP endpoints or record heavy sysctls so reachability test
        is purely testing base IP routing and link connectivity (P2-7).
        """
        pass

    def run(self):
        clients = getattr(self.topo_config, 'clients', [])
        servers = getattr(self.topo_config, 'servers', [])

        ping_count = int(self.experiment_parameter.get("count", 3))
        ping_timeout = int(self.experiment_parameter.get("timeout", 1))
        max_probes = int(self.experiment_parameter.get("max_probes", 200))
        strict_mesh = parse_bool(self.experiment_parameter.get("strict_mesh", False))

        client_endpoints: List[Tuple[Any, str, str, bool]] = []
        for c in clients:
            specs = self.get_host_endpoint_specs(c)
            for intf, ip, backup in specs:
                client_endpoints.append((c, intf, ip, backup))

        server_endpoints: List[Tuple[Any, str, str, bool]] = []
        for s in servers:
            specs = self.get_host_endpoint_specs(s)
            for intf, ip, backup in specs:
                server_endpoints.append((s, intf, ip, backup))

        tier1_probes_count = len(client_endpoints) * len(server_endpoints) * 2
        tier2_probes_count = (len(clients) * (len(clients) - 1)) + (len(servers) * (len(servers) - 1))
        total_probes_count = tier1_probes_count + tier2_probes_count

        if total_probes_count > max_probes:
            raise ValueError(
                f"Total mesh ping probes ({total_probes_count}) exceeds max_probes limit ({max_probes}). "
                "Use --max-probes to increase or reduce topology scale."
            )

        topo_param = getattr(self.topo, 'topo_parameter', None)
        topo_type = topo_param.get("topoType") if topo_param else "unknown"
        all_endpoints_meta = []
        for c, intf, ip, backup in client_endpoints:
            all_endpoints_meta.append({
                "node": c.name if hasattr(c, 'name') else str(c),
                "role": "client",
                "iface": intf,
                "ip": ip,
                "backup": backup
            })
        for s, intf, ip, backup in server_endpoints:
            all_endpoints_meta.append({
                "node": s.name if hasattr(s, 'name') else str(s),
                "role": "server",
                "iface": intf,
                "ip": ip,
                "backup": backup
            })

        probes_results = []
        tier1_failed = 0
        tier2_failed = 0

        # --- Tier 1 (Mandatory): All Client Endpoints <-> All Server Endpoints ---
        # 1. Client -> Server
        for c, c_intf, c_ip, _ in client_endpoints:
            c_name = c.name if hasattr(c, 'name') else str(c)
            for s, s_intf, s_ip, _ in server_endpoints:
                s_name = s.name if hasattr(s, 'name') else str(s)
                cmd = f"ping -c {ping_count} -W {ping_timeout} -I {c_ip} {s_ip}"
                raw_out = self.topo.command_to(c, cmd)
                parsed = self._parse_ping_output(str(raw_out))
                record = {
                    "tier": 1,
                    "src": c_name, "src_iface": c_intf, "src_ip": c_ip,
                    "dst": s_name, "dst_iface": s_intf, "dst_ip": s_ip,
                    "sent": parsed["sent"], "received": parsed["received"],
                    "loss_pct": parsed["loss_pct"], "rtt_avg_ms": parsed["rtt_avg_ms"],
                    "ok": parsed["ok"]
                }
                probes_results.append(record)
                if not parsed["ok"]:
                    tier1_failed += 1

        # 2. Server -> Client
        for s, s_intf, s_ip, _ in server_endpoints:
            s_name = s.name if hasattr(s, 'name') else str(s)
            for c, c_intf, c_ip, _ in client_endpoints:
                c_name = c.name if hasattr(c, 'name') else str(c)
                cmd = f"ping -c {ping_count} -W {ping_timeout} -I {s_ip} {c_ip}"
                raw_out = self.topo.command_to(s, cmd)
                parsed = self._parse_ping_output(str(raw_out))
                record = {
                    "tier": 1,
                    "src": s_name, "src_iface": s_intf, "src_ip": s_ip,
                    "dst": c_name, "dst_iface": c_intf, "dst_ip": c_ip,
                    "sent": parsed["sent"], "received": parsed["received"],
                    "loss_pct": parsed["loss_pct"], "rtt_avg_ms": parsed["rtt_avg_ms"],
                    "ok": parsed["ok"]
                }
                probes_results.append(record)
                if not parsed["ok"]:
                    tier1_failed += 1

        # --- Tier 2 (Informational): Same Role Nodes (Primary IP only) ---
        # Client <-> Client
        for i, c1 in enumerate(clients):
            c1_name = c1.name if hasattr(c1, 'name') else str(c1)
            c1_specs = self.get_host_endpoint_specs(c1)
            c1_ip = c1_specs[0][1] if c1_specs else None
            if not c1_ip:
                continue
            for j, c2 in enumerate(clients):
                if i == j:
                    continue
                c2_name = c2.name if hasattr(c2, 'name') else str(c2)
                c2_specs = self.get_host_endpoint_specs(c2)
                c2_ip = c2_specs[0][1] if c2_specs else None
                if not c2_ip:
                    continue
                cmd = f"ping -c {ping_count} -W {ping_timeout} -I {c1_ip} {c2_ip}"
                raw_out = self.topo.command_to(c1, cmd)
                parsed = self._parse_ping_output(str(raw_out))
                record = {
                    "tier": 2,
                    "src": c1_name, "src_iface": c1_specs[0][0], "src_ip": c1_ip,
                    "dst": c2_name, "dst_iface": c2_specs[0][0], "dst_ip": c2_ip,
                    "sent": parsed["sent"], "received": parsed["received"],
                    "loss_pct": parsed["loss_pct"], "rtt_avg_ms": parsed["rtt_avg_ms"],
                    "ok": parsed["ok"]
                }
                probes_results.append(record)
                if not parsed["ok"]:
                    tier2_failed += 1

        # Server <-> Server (P-6)
        for i, s1 in enumerate(servers):
            s1_name = s1.name if hasattr(s1, 'name') else str(s1)
            s1_specs = self.get_host_endpoint_specs(s1)
            s1_ip = s1_specs[0][1] if s1_specs else None
            if not s1_ip:
                continue
            for j, s2 in enumerate(servers):
                if i == j:
                    continue
                s2_name = s2.name if hasattr(s2, 'name') else str(s2)
                s2_specs = self.get_host_endpoint_specs(s2)
                s2_ip = s2_specs[0][1] if s2_specs else None
                if not s2_ip:
                    continue
                cmd = f"ping -c {ping_count} -W {ping_timeout} -I {s1_ip} {s2_ip}"
                raw_out = self.topo.command_to(s1, cmd)
                parsed = self._parse_ping_output(str(raw_out))
                record = {
                    "tier": 2,
                    "src": s1_name, "src_iface": s1_specs[0][0], "src_ip": s1_ip,
                    "dst": s2_name, "dst_iface": s2_specs[0][0], "dst_ip": s2_ip,
                    "sent": parsed["sent"], "received": parsed["received"],
                    "loss_pct": parsed["loss_pct"], "rtt_avg_ms": parsed["rtt_avg_ms"],
                    "ok": parsed["ok"]
                }
                probes_results.append(record)
                if not parsed["ok"]:
                    tier2_failed += 1

        # Verdict calculation
        if tier1_failed > 0:
            verdict = "FAIL"
        elif strict_mesh and tier2_failed > 0:
            verdict = "FAIL"
        else:
            verdict = "PASS"

        self.reachability_results = {
            "topo_type": topo_type,
            "endpoints": all_endpoints_meta,
            "probes": probes_results,
            "tier1": {"total": tier1_probes_count, "failed": tier1_failed},
            "tier2": {"total": tier2_probes_count, "failed": tier2_failed},
            "verdict": verdict
        }

        # Save reachability.json
        out_path = os.path.join(self.output_dir, "reachability.json")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(self.reachability_results, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to write reachability.json: {e}")

        if verdict == "FAIL":
            raise PreflightReachabilityError(f"Preflight reachability test failed: {tier1_failed} Tier-1 probes failed.")


class PreflightReachabilityError(RuntimeError):
    """Raised when preflight mesh ping reachability fails (exit code 3)."""
    pass

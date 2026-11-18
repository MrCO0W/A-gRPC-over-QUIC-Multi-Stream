"""Deterministic experiment scenario generator engine (T-6b ~ T-11).
"""
import os
import sys
import json
import tempfile
import argparse
from typing import Dict, Any, Tuple, Optional

from core.validation import validate_config
from core.topo import parse_bool
from tools.gaia_skills.spec import ScenarioSpec


SUPPORTED_XP_TYPES = {
    "iperf3_exp",
    "mesh_ping",
    "performance-analysis",
    "workload_exp",
    "iperf_exp",
    "tcp_exp",
    "ping_exp",
}


from core.naming import validate_config_name


def generate_scenario(
    spec: ScenarioSpec,
    output_dir: str = "config/xp",
    force: bool = False
) -> Tuple[int, str, Dict[str, Any]]:
    """
    Generate experiment configuration file from ScenarioSpec with self-gating validation.
    Returns (exit_code, target_file_or_error, report_dict).
    Exit codes:
      0: Success
      1: Runtime error
      2: Spec rejection / Validation failure
    """
    # 1. Spec Name Validation (F-3)
    try:
        validate_config_name(spec.name)
    except ValueError as e:
        return 2, str(e), {"errors": [str(e)]}

    abs_out = os.path.abspath(output_dir)
    target_path = os.path.abspath(os.path.join(output_dir, spec.name))
    if not target_path.startswith(abs_out):
        return 2, f"Scenario name '{spec.name}' attempts to escape output directory.", {"errors": ["Directory traversal"]}

    # Topo file resolution and validation (X-2, X-3: fail-closed)
    topo_file = spec.topo_file or (spec.override or {}).get("topo_file")
    if not topo_file:
        return 2, "Referenced topo_file is required for scenario validation.", {"errors": ["topo_file required"]}
    if not os.path.exists(topo_file):
        return 2, f"Referenced topo_file '{topo_file}' does not exist.", {"errors": [f"Topo file not found: {topo_file}"]}

    is_topo_valid, topo_report = validate_config(topo_file)
    if not is_topo_valid:
        return 2, f"Referenced topology '{topo_file}' is invalid: {topo_report.get('errors')}", topo_report
    mptcp_capable = topo_report.get("details", {}).get("mptcp_capable", False)

    # 1-1. Base Scenario Override Mode (X-1, X-2, X-3, B-1~3)
    if spec.base_xp:
        if not os.path.exists(spec.base_xp):
            return 2, f"Base scenario file '{spec.base_xp}' not found.", {"errors": ["Base scenario not found"]}

        abs_base = os.path.abspath(spec.base_xp)
        if target_path == abs_base and not force:
            return 2, f"Target scenario file '{target_path}' is identical to base scenario. Use a different --name or pass --force to overwrite.", {"errors": ["Target matches base scenario without force"]}

        try:
            # Parse base scenario key-values and raw lines
            base_params = {}
            base_lines = []
            with open(spec.base_xp, "r") as f:
                for line in f:
                    base_lines.append(line)
                    sline = line.strip()
                    if sline and not sline.startswith("#") and ":" in sline:
                        k, v = sline.split(":", 1)
                        base_params[k.strip()] = v.strip()

            ov = spec.override or {}
            ov_type = ov.get("xp_type") or ov.get("type")
            base_type = base_params.get("xpType", "iperf3_exp")
            target_xp_type = ov_type or base_type

            if target_xp_type not in SUPPORTED_XP_TYPES:
                return 2, f"Unsupported xp_type '{target_xp_type}'. Supported: {sorted(SUPPORTED_XP_TYPES)}", {"errors": [f"Unsupported xp_type: {target_xp_type}"]}

            warnings = []

            # MPTCP resolution
            if "mptcp" in ov and ov["mptcp"] is not None:
                mptcp_raw = ov["mptcp"]
            else:
                mptcp_raw = base_params.get("mptcp", "auto")

            mptcp_str = str(mptcp_raw).strip().lower()
            if mptcp_str == "auto":
                mptcp_flag = "yes" if mptcp_capable else "no"
            elif mptcp_str in ("yes", "true", "1", "on"):
                if not mptcp_capable:
                    return 2, f"Topology '{topo_file}' does not support MPTCP.", {"errors": ["Topology not MPTCP capable"]}
                mptcp_flag = "yes"
            else:
                mptcp_flag = "no"

            if ov_type and ov_type != base_type:
                # Type changed -> template generation with warnings for discarded keys (X-1)
                for k in base_params:
                    if k != "xpType":
                        warnings.append(f"Discarded base key '{k}' due to xpType change ({base_type} -> {target_xp_type})")

                lines = []
                pcap_nodes_ov = ov.get("pcap_nodes") or getattr(spec, "pcap_nodes", [])
                pcap_nodes_str = ",".join(pcap_nodes_ov) if isinstance(pcap_nodes_ov, list) else str(pcap_nodes_ov).strip()

                if target_xp_type == "performance-analysis":
                    pa_exp = ov.get("pa_exp") or ov.get("exp") or "simple"
                    pa_count = ov.get("pa_count") if "pa_count" in ov and ov["pa_count"] is not None else (ov.get("nc") if "nc" in ov and ov["nc"] is not None else 10)
                    pa_size = ov.get("pa_packet_size") if "pa_packet_size" in ov and ov["pa_packet_size"] is not None else (ov.get("ps") if "ps" in ov and ov["ps"] is not None else 100)
                    pa_protocol = ov.get("pa_protocol") or ov.get("protocol") or "udp"
                    pcap = ov.get("pcap", "no")

                    lines.append("xpType:performance-analysis\n")
                    lines.append(f"exp:{pa_exp}\n")
                    lines.append(f"nc:{pa_count}\n")
                    lines.append(f"ps:{pa_size}\n")
                    lines.append(f"protocol:{pa_protocol}\n")
                    if str(pcap).lower() not in ("no", "false", "0"):
                        lines.append(f"pcap:{pcap}\n")
                    if pcap_nodes_str:
                        lines.append(f"pcap_nodes:{pcap_nodes_str}\n")

                elif target_xp_type == "mesh_ping":
                    ping_count = ov.get("ping_count") if "ping_count" in ov and ov["ping_count"] is not None else (ov.get("count") if "count" in ov and ov["count"] is not None else 3)
                    ping_timeout = ov.get("ping_timeout") if "ping_timeout" in ov and ov["ping_timeout"] is not None else (ov.get("timeout") if "timeout" in ov and ov["timeout"] is not None else 1)
                    max_probes = ov.get("max_probes") if "max_probes" in ov and ov["max_probes"] is not None else 200
                    strict_mesh = ov.get("strict_mesh", "no")

                    lines.append("xpType:mesh_ping\n")
                    lines.append(f"count:{ping_count}\n")
                    lines.append(f"timeout:{ping_timeout}\n")
                    lines.append(f"max_probes:{max_probes}\n")
                    lines.append(f"strict_mesh:{'yes' if str(strict_mesh).lower() in ('yes','true','1') else 'no'}\n")
                    if pcap_nodes_str:
                        lines.append(f"pcap_nodes:{pcap_nodes_str}\n")

                elif target_xp_type == "iperf3_exp":
                    duration = ov.get("duration") if "duration" in ov and ov["duration"] is not None else 10
                    parallel = ov.get("parallel") if "parallel" in ov and ov["parallel"] is not None else 1
                    congctrl = ov.get("congctrl") or "cubic"
                    reverse = ov.get("reverse", "no")
                    pcap = ov.get("pcap", "no")

                    lines.append("xpType:iperf3_exp\n")
                    lines.append(f"mptcp:{mptcp_flag}\n")
                    lines.append(f"duration:{duration}\n")
                    lines.append(f"parallel:{parallel}\n")
                    lines.append(f"congctrl:{congctrl}\n")
                    if str(reverse).lower() in ("yes", "true", "1"):
                        lines.append("reverse:yes\n")
                    if str(pcap).lower() not in ("no", "false", "0"):
                        lines.append(f"pcap:{pcap}\n")
                    if pcap_nodes_str:
                        lines.append(f"pcap_nodes:{pcap_nodes_str}\n")

                else:
                    duration = ov.get("duration") if "duration" in ov and ov["duration"] is not None else 10
                    lines.append(f"xpType:{target_xp_type}\n")
                    lines.append(f"mptcp:{mptcp_flag}\n")
                    lines.append(f"duration:{duration}\n")
                    if pcap_nodes_str:
                        lines.append(f"pcap_nodes:{pcap_nodes_str}\n")

                config_text = "".join(lines)
            else:
                # Same type -> line-by-line / key-preserving update! (X-1, P2-1)
                updates = {}
                if topo_file:
                    updates["topo_file"] = topo_file
                if "duration" in ov and ov["duration"] is not None:
                    updates["duration"] = str(ov["duration"])
                if "parallel" in ov and ov["parallel"] is not None:
                    updates["parallel"] = str(ov["parallel"])
                if "congctrl" in ov and ov["congctrl"] is not None:
                    updates["congctrl"] = str(ov["congctrl"])
                if "reverse" in ov and ov["reverse"] is not None:
                    updates["reverse"] = "yes" if str(ov["reverse"]).lower() in ("yes", "true", "1") else "no"
                if "pcap" in ov and ov["pcap"] is not None:
                    updates["pcap"] = str(ov["pcap"])
                if "pcap_nodes" in ov and ov["pcap_nodes"] is not None:
                    pn = ov["pcap_nodes"]
                    updates["pcap_nodes"] = ",".join(pn) if isinstance(pn, list) else str(pn)
                if "mptcp" in ov and ov["mptcp"] is not None:
                    updates["mptcp"] = mptcp_flag

                # mesh_ping options
                if "ping_count" in ov and ov["ping_count"] is not None:
                    updates["count"] = str(ov["ping_count"])
                elif "count" in ov and ov["count"] is not None:
                    updates["count"] = str(ov["count"])

                if "ping_timeout" in ov and ov["ping_timeout"] is not None:
                    updates["timeout"] = str(ov["ping_timeout"])
                elif "timeout" in ov and ov["timeout"] is not None:
                    updates["timeout"] = str(ov["timeout"])

                if "max_probes" in ov and ov["max_probes"] is not None:
                    updates["max_probes"] = str(ov["max_probes"])

                if "strict_mesh" in ov and ov["strict_mesh"] is not None:
                    updates["strict_mesh"] = "yes" if str(ov["strict_mesh"]).lower() in ("yes", "true", "1") else "no"

                # performance-analysis options
                if "pa_exp" in ov and ov["pa_exp"] is not None:
                    updates["exp"] = str(ov["pa_exp"])
                elif "exp" in ov and ov["exp"] is not None:
                    updates["exp"] = str(ov["exp"])

                if "pa_count" in ov and ov["pa_count"] is not None:
                    updates["nc"] = str(ov["pa_count"])
                elif "nc" in ov and ov["nc"] is not None:
                    updates["nc"] = str(ov["nc"])

                if "pa_packet_size" in ov and ov["pa_packet_size"] is not None:
                    updates["ps"] = str(ov["pa_packet_size"])
                elif "ps" in ov and ov["ps"] is not None:
                    updates["ps"] = str(ov["ps"])

                if "pa_protocol" in ov and ov["pa_protocol"] is not None:
                    updates["protocol"] = str(ov["pa_protocol"])
                elif "protocol" in ov and ov["protocol"] is not None:
                    updates["protocol"] = str(ov["protocol"])

                seen_keys = set()
                lines = []
                for line in base_lines:
                    sline = line.strip()
                    if not sline or sline.startswith("#"):
                        lines.append(line if line.endswith("\n") else line + "\n")
                        continue
                    if ":" in sline:
                        k, v = sline.split(":", 1)
                        k = k.strip()
                        seen_keys.add(k)
                        if k in updates:
                            lines.append(f"{k}:{updates[k]}\n")
                        else:
                            lines.append(line if line.endswith("\n") else line + "\n")
                    else:
                        lines.append(line if line.endswith("\n") else line + "\n")

                for k, v in updates.items():
                    if k not in seen_keys:
                        lines.append(f"{k}:{v}\n")

                config_text = "".join(lines)

        except Exception as e:
            return 1, f"Failed to override base scenario '{spec.base_xp}': {e}", {"errors": [str(e)]}

        # Validate with topo_file (X-2)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            tmp.write(config_text)
            tmp_path = tmp.name

        try:
            is_valid, report = validate_config(topo_file, tmp_path)
            if not is_valid:
                return 2, f"Static validation failed for derived scenario: {report.get('errors')}", report

            if warnings:
                report.setdefault("warnings", []).extend(warnings)

            os.makedirs(output_dir, exist_ok=True)
            with open(target_path, "w") as f:
                f.write(config_text)

            return 0, target_path, report
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    if spec.xp_type not in SUPPORTED_XP_TYPES:
        return 2, f"Unsupported xp_type '{spec.xp_type}'. Supported: {sorted(SUPPORTED_XP_TYPES)}", {"errors": [f"Unsupported xp_type: {spec.xp_type}"]}

    if os.path.exists(target_path) and not force:
        return 2, f"Scenario file '{target_path}' already exists. Use --force to overwrite.", {"errors": ["File already exists"]}

    # 2. Interpret MPTCP Flag (T-7)
    mptcp_str = str(spec.mptcp).strip().lower()
    if mptcp_str == "auto":
        mptcp_flag = "yes" if mptcp_capable else "no"
    elif mptcp_str in ("yes", "true", "1", "on"):
        if not mptcp_capable:
            return 2, f"Topology '{spec.topo_file}' does not support MPTCP (mptcp_capable=False). Use a MultiIf topology with >= 2 paths or set mptcp to 'no'.", {"errors": ["Topology not MPTCP capable"]}
        mptcp_flag = "yes"
    elif mptcp_str in ("no", "false", "0", "off"):
        mptcp_flag = "no"
    else:
        return 2, f"Invalid mptcp value '{spec.mptcp}': must be 'auto', 'yes', or 'no'.", {"errors": [f"Invalid mptcp: {spec.mptcp}"]}

    # 3. Emit Config Text with Mandatory Keys (T-6b, T-8, T-9)
    lines = []
    if spec.xp_type == "performance-analysis":
        # Mandatory keys: xpType, exp, nc, ps, protocol (C-2, T-6b)
        lines.append(f"xpType:performance-analysis\n")
        lines.append(f"exp:{spec.pa_exp}\n")
        lines.append(f"nc:{spec.pa_count}\n")
        lines.append(f"ps:{spec.pa_packet_size}\n")
        lines.append(f"protocol:{spec.pa_protocol}\n")
        if spec.pcap and spec.pcap != "no":
            lines.append(f"pcap:{spec.pcap}\n")
        if spec.pcap_nodes:
            lines.append(f"pcap_nodes:{','.join(spec.pcap_nodes)}\n")

    elif spec.xp_type == "mesh_ping":
        # Mandatory keys: xpType, count, timeout, max_probes, strict_mesh
        lines.append(f"xpType:mesh_ping\n")
        lines.append(f"count:{spec.ping_count}\n")
        lines.append(f"timeout:{spec.ping_timeout}\n")
        lines.append(f"max_probes:{spec.max_probes}\n")
        lines.append(f"strict_mesh:{'yes' if spec.strict_mesh else 'no'}\n")
        if spec.pcap_nodes:
            lines.append(f"pcap_nodes:{','.join(spec.pcap_nodes)}\n")

    elif spec.xp_type == "iperf3_exp":
        # Mandatory keys: xpType, mptcp, duration, parallel, congctrl
        lines.append(f"xpType:iperf3_exp\n")
        lines.append(f"mptcp:{mptcp_flag}\n")
        lines.append(f"duration:{spec.duration}\n")
        lines.append(f"parallel:{spec.parallel}\n")
        lines.append(f"congctrl:{spec.congctrl}\n")
        if spec.reverse:
            lines.append(f"reverse:yes\n")
        if spec.pcap and spec.pcap != "no":
            lines.append(f"pcap:{spec.pcap}\n")
        if spec.pcap_nodes:
            lines.append(f"pcap_nodes:{','.join(spec.pcap_nodes)}\n")

    elif spec.xp_type == "workload_exp":
        # Mandatory keys: xpType, duration, mptcp, pcap, congctrl, and workloads (WP-3, D-8, P2-3)
        lines.append(f"xpType:workload_exp\n")
        if spec.topo_file:
            lines.append(f"topo_file:{spec.topo_file}\n")
        lines.append(f"duration:{spec.duration}\n")
        lines.append(f"mptcp:{mptcp_flag}\n")
        lines.append(f"pcap:{spec.pcap}\n")
        if spec.pcap_nodes:
            lines.append(f"pcap_nodes:{','.join(spec.pcap_nodes)}\n")
        lines.append(f"congctrl:{spec.congctrl}\n")
        for w in spec.workloads:
            if w.command:
                lines.append(f"workload:{w.node},{w.kind},{w.command}\n")
            else:
                lines.append(f"workload:{w.node},{w.kind}\n")

    else:
        # Generic fallback
        lines.append(f"xpType:{spec.xp_type}\n")
        lines.append(f"mptcp:{mptcp_flag}\n")
        lines.append(f"duration:{spec.duration}\n")
        if spec.pcap_nodes:
            lines.append(f"pcap_nodes:{','.join(spec.pcap_nodes)}\n")

    config_text = "".join(lines)

    # 4. Fail-Closed Self-Gate Validation (T-11)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp.write(config_text)
        tmp_path = tmp.name

    try:
        is_valid, report = validate_config(spec.topo_file, tmp_path)
        if not is_valid:
            return 2, f"Static validation failed for generated scenario: {report.get('errors')}", report

        os.makedirs(output_dir, exist_ok=True)
        with open(target_path, "w") as f:
            f.write(config_text)

        return 0, target_path, report

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def parse_scenario_file(path: str, name: Optional[str] = None) -> ScenarioSpec:
    """
    Parse a scenario configuration file into ScenarioSpec dataclass (D-11a).
    """
    from core.parameter import Parameter
    from core.workload import parse_workload_line
    from tools.gaia_skills.spec import WorkloadSpec

    param = Parameter(path)
    spec_name = name or os.path.basename(path)
    xp_type = param.get("xpType", "iperf3_exp")
    mptcp = param.get("mptcp", "auto")
    pcap = param.get("pcap", "no")
    pcap_nodes_raw = param.get("pcap_nodes", "")
    pcap_nodes = [n.strip() for n in pcap_nodes_raw.split(",") if n.strip()]
    duration = int(param.get("duration", 10))
    parallel = int(param.get("parallel", 1))
    reverse = parse_bool(param.get("reverse", "no"))
    congctrl = param.get("congctrl", "cubic")

    ping_count = int(param.get("count", 3))
    ping_timeout = int(param.get("timeout", 1))
    max_probes = int(param.get("max_probes", 200))
    strict_mesh = parse_bool(param.get("strict_mesh", "no"))

    pa_protocol = param.get("protocol", "udp")
    pa_packet_size = int(param.get("ps", 100))
    pa_count = int(param.get("nc", 10))
    pa_exp = param.get("exp", "simple")

    workloads = []
    for wline in param.get_list("workload"):
        witem = parse_workload_line(wline)
        workloads.append(WorkloadSpec(node=witem.node, kind=witem.kind, command=witem.command))

    return ScenarioSpec(
        name=spec_name,
        topo_file=param.get("topo_file", default=None),
        xp_type=xp_type,
        mptcp=mptcp,
        pcap=pcap,
        pcap_nodes=pcap_nodes,
        duration=duration,
        parallel=parallel,
        reverse=reverse,
        congctrl=congctrl,
        ping_count=ping_count,
        ping_timeout=ping_timeout,
        max_probes=max_probes,
        strict_mesh=strict_mesh,
        pa_protocol=pa_protocol,
        pa_packet_size=pa_packet_size,
        pa_count=pa_count,
        pa_exp=pa_exp,
        workloads=workloads,
    )


def main():
    parser = argparse.ArgumentParser(description="GAIA Scenario Generator (T-6 ~ T-11)")
    parser.add_argument("--spec", help="Path to ScenarioSpec JSON file or JSON string")
    parser.add_argument("--base-xp", "--base-scenario", help="Path to base scenario file to derive and override from")
    parser.add_argument("--name", help="Name of the new scenario (required with --base-xp)")
    parser.add_argument("-t", "--topo", "--topo-file", help="Path to associated topology configuration file")
    parser.add_argument("--type", "--xp-type", help="Experiment type (iperf3_exp, mesh_ping, performance-analysis)")
    parser.add_argument("--duration", type=int, help="Traffic duration in seconds")
    parser.add_argument("--parallel", type=int, help="Number of parallel streams")
    parser.add_argument("--congctrl", help="TCP congestion control algorithm (e.g. cubic, reno)")
    parser.add_argument("--mptcp", help="MPTCP mode (auto, yes, no)")
    parser.add_argument("--pcap", help="PCAP packet capture (yes, no)")
    parser.add_argument("--reverse", action=argparse.BooleanOptionalAction, default=None, help="Reverse direction traffic (server to client)")
    parser.add_argument("--pa-protocol", help="Protocol for performance-analysis (udp, tcp, quic)")
    parser.add_argument("--pa-size", "--pa-packet-size", type=int, help="Packet size for performance-analysis (bytes)")
    parser.add_argument("--pa-count", type=int, help="Packet count for performance-analysis")
    parser.add_argument("--pa-exp", help="Experiment sub-type for performance-analysis (default: simple)")
    parser.add_argument("--ping-count", type=int, help="Ping count for mesh_ping")
    parser.add_argument("--ping-timeout", type=int, help="Ping timeout in seconds for mesh_ping")
    parser.add_argument("--max-probes", type=int, help="Max probes for mesh_ping")
    parser.add_argument("--strict-mesh", action=argparse.BooleanOptionalAction, default=None, help="Enforce strict mesh ping (no packet loss permitted)")
    parser.add_argument("--output-dir", default="config/xp", help="Output directory for generated scenario file")
    parser.add_argument("--force", action="store_true", help="Overwrite existing configuration file")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON result")

    args = parser.parse_args()

    # Parse spec or construct from base-xp CLI flags
    try:
        if args.base_xp:
            if not args.name:
                raise ValueError("Deriving from --base-xp requires --name <new_scenario_name>.")
            override_dict = {}
            if args.type:
                override_dict["xp_type"] = args.type
            if args.duration is not None:
                override_dict["duration"] = args.duration
            if args.parallel is not None:
                override_dict["parallel"] = args.parallel
            if args.congctrl:
                override_dict["congctrl"] = args.congctrl
            if args.mptcp:
                override_dict["mptcp"] = args.mptcp
            if args.pcap:
                override_dict["pcap"] = args.pcap
            if args.reverse is not None:
                override_dict["reverse"] = "yes" if args.reverse else "no"
            if args.pa_protocol:
                override_dict["pa_protocol"] = args.pa_protocol
            if args.pa_size is not None:
                override_dict["pa_packet_size"] = args.pa_size
            if args.pa_count is not None:
                override_dict["pa_count"] = args.pa_count
            if args.pa_exp:
                override_dict["pa_exp"] = args.pa_exp
            if args.ping_count is not None:
                override_dict["ping_count"] = args.ping_count
            if args.ping_timeout is not None:
                override_dict["ping_timeout"] = args.ping_timeout
            if args.max_probes is not None:
                override_dict["max_probes"] = args.max_probes
            if args.strict_mesh is not None:
                override_dict["strict_mesh"] = "yes" if args.strict_mesh else "no"
            if args.topo:
                override_dict["topo_file"] = args.topo

            spec = ScenarioSpec(
                name=args.name,
                topo_file=args.topo,
                base_xp=args.base_xp,
                override=override_dict
            )
        elif args.spec:
            if os.path.exists(args.spec):
                with open(args.spec, "r") as f:
                    spec_data = json.load(f)
            else:
                spec_data = json.loads(args.spec)
            spec = ScenarioSpec.from_dict(spec_data)
        else:
            raise ValueError("Either --spec <json> or --base-xp <file> --name <new_name> is required.")
    except Exception as e:
        err_out = {"valid": False, "rc": 2, "error": f"Failed to parse ScenarioSpec: {e}"}
        if args.json:
            print(json.dumps(err_out, indent=2))
        else:
            print(f"Error (rc=2): {err_out['error']}", file=sys.stderr)
        sys.exit(2)

    rc, result, report = generate_scenario(spec, output_dir=args.output_dir, force=args.force)

    if args.json:
        out = {
            "valid": (rc == 0),
            "rc": rc,
            "target_file": result if rc == 0 else None,
            "error": result if rc != 0 else None,
            "report": report,
        }
        print(json.dumps(out, indent=2))
    else:
        if rc == 0:
            print(f"Successfully generated scenario: {result}")
        else:
            print(f"Error (rc={rc}): {result}", file=sys.stderr)

    sys.exit(rc)


if __name__ == "__main__":
    main()

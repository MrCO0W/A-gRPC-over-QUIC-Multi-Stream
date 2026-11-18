"""Deterministic topology generator engine (T-2, T-3, T-4).
"""
import os
import sys
import json
import tempfile
import argparse
from typing import Dict, Any, Tuple, Optional

from core.netmodel import NetModel, LinkSpec
from core.topo import TopoParameter
from core.validation import validate_config
from tools.gaia_skills.spec import TopoSpec, PathSpec, NetemScheduleSpec


from core.naming import validate_config_name


def generate_topo(
    spec: TopoSpec,
    output_dir: str = "config/topo",
    force: bool = False
) -> Tuple[int, str, Dict[str, Any]]:
    """
    Generate topology configuration file from TopoSpec with self-gating validation.
    Returns (exit_code, target_file_or_error, report_dict).
    Exit codes:
      0: Success
      1: Runtime error
      2: Spec rejection / Validation failure
    """
    # 1. Spec Rejection Rules (T-2b, G-7, F-3)
    try:
        validate_config_name(spec.name)
    except ValueError as e:
        return 2, str(e), {"errors": [str(e)]}

    abs_out = os.path.abspath(output_dir)
    target_path = os.path.abspath(os.path.join(output_dir, spec.name))
    if not target_path.startswith(abs_out):
        return 2, f"Topology name '{spec.name}' attempts to escape output directory.", {"errors": ["Directory traversal"]}

    if os.path.exists(target_path) and not force:
        return 2, f"Topology file '{target_path}' already exists. Use --force to overwrite.", {"errors": ["File already exists"]}

    # 1-1. Base Topology Override Mode
    if spec.base_topo:
        if not os.path.exists(spec.base_topo):
            return 2, f"Base topology file '{spec.base_topo}' not found.", {"errors": ["Base topology not found"]}

        abs_base = os.path.abspath(spec.base_topo)
        if target_path == abs_base and not force:
            return 2, f"Target topology file '{target_path}' is identical to base topology. Use a different --name or pass --force to overwrite.", {"errors": ["Target matches base topology without force"]}

        try:
            base_param = TopoParameter(spec.base_topo)
            topo_type = base_param.get("topoType")
            ov = spec.override or {}
            ov_bw = ov.get("bandwidth_mbps") if "bandwidth_mbps" in ov else ov.get("bw")
            ov_delay = ov.get("delay_ms") if "delay_ms" in ov else ov.get("delay")
            ov_queue = ov.get("queue_size") if "queue_size" in ov else ov.get("queue")
            ov_loss = ov.get("loss_pct") if "loss_pct" in ov else ov.get("loss")

            if topo_type == "Generic":
                model = NetModel.from_parameter(base_param)
                for link in model.links:
                    if ov_bw is not None:
                        link.bandwidth = float(ov_bw)
                    if ov_delay is not None:
                        link.delay = float(ov_delay)
                    if ov_queue is not None:
                        link.queue_size = int(ov_queue)
                    if ov_loss is not None:
                        link.loss = float(ov_loss)

                if spec.netem_schedule:
                    model.netem_at_schedules = []
                    model.change_netem = True
                    for sched in spec.netem_schedule:
                        if sched.link_nodes:
                            link_idx = model.find_link_index(sched.link_nodes[0], sched.link_nodes[1])
                            if link_idx is None:
                                return 2, f"link_nodes {sched.link_nodes} not found in base topology.", {"errors": [f"link_nodes not found: {sched.link_nodes}"]}
                        elif sched.link_idx is not None:
                            link_idx = sched.link_idx
                        else:
                            link_idx = sched.path_id
                        model.netem_at_schedules.append((link_idx, sched.at_sec, sched.cmd))

                config_text = model.to_config_text()
            else:
                # MultiIf / MultiInterfaceMultiClients
                lines = []
                with open(spec.base_topo, "r") as f:
                    for line in f:
                        sline = line.strip()
                        if not sline or sline.startswith("#"):
                            lines.append(line)
                            continue
                        if ":" in sline:
                            k, v = sline.split(":", 1)
                            if k.startswith("path_c2r_") or k.startswith("path_r2s_"):
                                parts = [p.strip() for p in v.split(",")]
                                if len(parts) == 2:
                                    delay_val = str(ov_delay) if ov_delay is not None else parts[0]
                                    bw_val = str(ov_bw) if ov_bw is not None else parts[1]
                                    queue_val = str(ov_queue) if ov_queue is not None else "1000"
                                    loss_val = str(ov_loss) if ov_loss is not None else "0"
                                    backup_val = "0"
                                    lines.append(f"{k}:{delay_val},{queue_val},{bw_val},{loss_val},{backup_val}\n")
                                    continue
                                elif len(parts) >= 3:
                                    delay_val = str(ov_delay) if ov_delay is not None else parts[0]
                                    queue_val = str(ov_queue) if ov_queue is not None else parts[1]
                                    bw_val = str(ov_bw) if ov_bw is not None else parts[2]
                                    loss_val = str(ov_loss) if ov_loss is not None else (parts[3] if len(parts) > 3 else "0")
                                    backup_val = parts[4] if len(parts) > 4 else "0"
                                    lines.append(f"{k}:{delay_val},{queue_val},{bw_val},{loss_val},{backup_val}\n")
                                    continue
                            elif k == "changeNetem" and spec.netem_schedule:
                                continue
                            elif k.startswith("netemAt_") and spec.netem_schedule:
                                continue
                        lines.append(line if line.endswith("\n") else line + "\n")

                if spec.netem_schedule:
                    lines.append("changeNetem:true\n")
                    for sched in spec.netem_schedule:
                        ptype = sched.path_type or "c2r"
                        pid = sched.path_id if sched.path_id is not None else 0
                        lines.append(f"netemAt_{ptype}_{pid}:{sched.at_sec},{sched.cmd}\n")

                config_text = "".join(lines)
        except Exception as e:
            return 1, f"Failed to override base topology '{spec.base_topo}': {e}", {"errors": [str(e)]}

        # Validate and commit
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            tmp.write(config_text)
            tmp_path = tmp.name

        try:
            is_valid, report = validate_config(tmp_path)
            if not is_valid:
                return 2, f"Static validation failed for derived topology: {report.get('errors')}", report

            os.makedirs(output_dir, exist_ok=True)
            with open(target_path, "w") as f:
                f.write(config_text)

            return 0, target_path, report
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    if spec.intent == "mptcp_multipath":
        if len(spec.paths) < 2:
            return 2, "intent 'mptcp_multipath' requires at least 2 paths (paths >= 2).", {"errors": ["MPTCP requires >= 2 paths"]}
    elif spec.intent == "multi_router":
        if spec.routers_count < 2:
            return 2, "intent 'multi_router' requires routers_count >= 2.", {"errors": ["multi_router requires routers_count >= 2"]}

    # 2. Topology Family Selection & Emitter (T-2, T-3)
    try:
        config_text = ""
        if spec.intent == "mptcp_multipath":
            # MultiIf or MultiInterfaceMultiClients
            if spec.clients > 1 or spec.servers > 1:
                topo_type = "MultiInterfaceMultiClients"
            else:
                topo_type = "MultiIf"

            # Validate netem schedule targets for MultiIf (F-2)
            for sched in spec.netem_schedule:
                if sched.path_type == "c2r" and sched.path_id >= len(spec.paths):
                    return 2, f"netem_schedule path_id {sched.path_id} exceeds available c2r paths (0..{len(spec.paths)-1}).", {"errors": ["Invalid netem path_id"]}
                elif sched.path_type == "r2s" and sched.path_id >= len(spec.server_paths):
                    return 2, f"netem_schedule path_id {sched.path_id} exceeds available r2s paths (0..{len(spec.server_paths)-1}).", {"errors": ["Invalid netem path_id"]}

            lines = [f"topoType:{topo_type}\n"]
            if topo_type == "MultiInterfaceMultiClients":
                lines.append(f"clients:{spec.clients}\n")
                lines.append(f"servers:{spec.servers}\n")

            # c2r paths (G-6 order: delay, queue_size, bandwidth, loss, backup)
            for i, p in enumerate(spec.paths):
                lines.append(f"path_c2r_{i}:{p.delay_ms},{p.queue_size},{p.bandwidth_mbps},{p.loss_pct},{int(p.backup)}\n")

            # r2s paths
            for j, p in enumerate(spec.server_paths):
                lines.append(f"path_r2s_{j}:{p.delay_ms},{p.queue_size},{p.bandwidth_mbps},{p.loss_pct},{int(p.backup)}\n")

            # changeNetem deterministic derivation (T-2c, G-9)
            change_netem = len(spec.netem_schedule) > 0
            lines.append(f"changeNetem:{'true' if change_netem else 'false'}\n")

            # netem schedule
            for sched in spec.netem_schedule:
                lines.append(f"netemAt_{sched.path_type}_{sched.path_id}:{sched.at_sec},{sched.cmd}\n")

            config_text = "".join(lines)

        else:
            # Generic topology (single_path or multi_router)
            model = NetModel()
            model.change_netem = len(spec.netem_schedule) > 0

            if spec.intent == "multi_router":
                num_routers = spec.routers_count  # P2-2 clean
                for r_idx in range(num_routers):
                    r_name = f"r{r_idx}"
                    subnet = f"10.0.{r_idx + 1}.0/24"
                    ip = f"10.0.{r_idx + 1}.1"
                    model.add_router(r_name, subnet, ip)

                # Connect transit links between r_i and r_{i+1}
                transit_p = spec.transit_path or (spec.paths[0] if spec.paths else PathSpec())
                for r_idx in range(num_routers - 1):
                    model.add_link(
                        f"r{r_idx}",
                        f"r{r_idx + 1}",
                        delay=transit_p.delay_ms,
                        queue_size=transit_p.queue_size,
                        bandwidth=transit_p.bandwidth_mbps,
                        loss=transit_p.loss_pct,
                        backup=int(transit_p.backup),
                    )

                # Clients on r0
                for c in range(spec.clients):
                    c_name = f"c{c}"
                    if len(c_name) > 8:
                        return 2, f"Client node name '{c_name}' exceeds 8 character limit.", {"errors": ["Node name > 8 chars"]}
                    p = spec.paths[min(c, len(spec.paths) - 1)] if spec.paths else PathSpec()
                    model.add_host(c_name, "client", "r0", None)
                    model.add_link(
                        c_name,
                        "r0",
                        delay=p.delay_ms,
                        queue_size=p.queue_size,
                        bandwidth=p.bandwidth_mbps,
                        loss=p.loss_pct,
                        backup=int(p.backup),
                    )

                # Servers on r_{N-1}
                last_r = f"r{num_routers - 1}"
                for s in range(spec.servers):
                    s_name = f"s{s}"
                    if len(s_name) > 8:
                        return 2, f"Server node name '{s_name}' exceeds 8 character limit.", {"errors": ["Node name > 8 chars"]}
                    p = spec.server_paths[min(s, len(spec.server_paths) - 1)] if spec.server_paths else PathSpec()
                    model.add_host(s_name, "server", last_r, None)
                    model.add_link(
                        s_name,
                        last_r,
                        delay=p.delay_ms,
                        queue_size=p.queue_size,
                        bandwidth=p.bandwidth_mbps,
                        loss=p.loss_pct,
                        backup=int(p.backup),
                    )

            else:
                # single_path Generic
                model.add_router("r0", "10.0.1.0/24", "10.0.1.1")
                for c in range(spec.clients):
                    c_name = f"c{c}"
                    if len(c_name) > 8:
                        return 2, f"Client node name '{c_name}' exceeds 8 character limit.", {"errors": ["Node name > 8 chars"]}
                    p = spec.paths[min(c, len(spec.paths) - 1)] if spec.paths else PathSpec()
                    model.add_host(c_name, "client", "r0", None)
                    model.add_link(
                        c_name,
                        "r0",
                        delay=p.delay_ms,
                        queue_size=p.queue_size,
                        bandwidth=p.bandwidth_mbps,
                        loss=p.loss_pct,
                        backup=int(p.backup),
                    )

                for s in range(spec.servers):
                    s_name = f"s{s}"
                    if len(s_name) > 8:
                        return 2, f"Server node name '{s_name}' exceeds 8 character limit.", {"errors": ["Node name > 8 chars"]}
                    p = spec.server_paths[min(s, len(spec.server_paths) - 1)] if spec.server_paths else PathSpec()
                    model.add_host(s_name, "server", "r0", None)
                    model.add_link(
                        s_name,
                        "r0",
                        delay=p.delay_ms,
                        queue_size=p.queue_size,
                        bandwidth=p.bandwidth_mbps,
                        loss=p.loss_pct,
                        backup=int(p.backup),
                    )

            # Map netem schedules for Generic (Adjustment 1)
            for sched in spec.netem_schedule:
                link_idx = None
                if sched.link_nodes is not None:
                    node_a, node_b = sched.link_nodes
                    link_idx = model.find_link_index(node_a, node_b)
                    if link_idx is None:
                        return 2, f"link_nodes {sched.link_nodes} not found in generated topology links.", {"errors": [f"link_nodes not found: {sched.link_nodes}"]}
                elif sched.link_idx is not None:
                    link_idx = sched.link_idx
                else:
                    link_idx = sched.path_id

                model.netem_at_schedules.append((link_idx, sched.at_sec, sched.cmd))

            config_text = model.to_config_text()

    except Exception as e:
        return 1, f"Failed to construct topology text: {e}", {"errors": [str(e)]}

    # 3. Fail-Closed Self-Gate Validation (T-4)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp.write(config_text)
        tmp_path = tmp.name

    try:
        is_valid, report = validate_config(tmp_path)
        if not is_valid:
            return 2, f"Static validation failed for generated topology: {report.get('errors')}", report

        # Validation passed: write to target path
        os.makedirs(output_dir, exist_ok=True)
        with open(target_path, "w") as f:
            f.write(config_text)

        return 0, target_path, report

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def main():
    parser = argparse.ArgumentParser(description="GAIA Topo Generator (T-1 ~ T-4)")
    parser.add_argument("--spec", help="Path to TopoSpec JSON file or JSON string")
    parser.add_argument("--base-topo", help="Path to base topology file to derive and override from")
    parser.add_argument("--name", help="Name of the new topology (required with --base-topo)")
    parser.add_argument("--bw", "--bandwidth", type=float, help="Override bandwidth (Mbps)")
    parser.add_argument("--delay", type=float, help="Override delay (ms)")
    parser.add_argument("--queue", "--queue-size", type=int, help="Override queue size")
    parser.add_argument("--loss", type=float, help="Override packet loss percentage")
    parser.add_argument("--output-dir", default="config/topo", help="Output directory for generated topology file")
    parser.add_argument("--force", action="store_true", help="Overwrite existing configuration file")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON result")

    args = parser.parse_args()

    # Parse spec or construct from base-topo CLI flags
    try:
        if args.base_topo:
            if not args.name:
                raise ValueError("Deriving from --base-topo requires --name <new_topo_name>.")
            override_dict = {}
            if args.bw is not None:
                override_dict["bandwidth_mbps"] = args.bw
            if args.delay is not None:
                override_dict["delay_ms"] = args.delay
            if args.queue is not None:
                override_dict["queue_size"] = args.queue
            if args.loss is not None:
                override_dict["loss_pct"] = args.loss

            spec = TopoSpec(name=args.name, base_topo=args.base_topo, override=override_dict)
        elif args.spec:
            if os.path.exists(args.spec):
                with open(args.spec, "r") as f:
                    spec_data = json.load(f)
            else:
                spec_data = json.loads(args.spec)
            spec = TopoSpec.from_dict(spec_data)
        else:
            raise ValueError("Either --spec <json> or --base-topo <file> --name <new_name> is required.")
    except Exception as e:
        err_out = {"valid": False, "rc": 2, "error": f"Failed to parse TopoSpec: {e}"}
        if args.json:
            print(json.dumps(err_out, indent=2))
        else:
            print(f"Error (rc=2): {err_out['error']}", file=sys.stderr)
        sys.exit(2)

    rc, result, report = generate_topo(spec, output_dir=args.output_dir, force=args.force)

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
            print(f"Successfully generated topology: {result}")
        else:
            print(f"Error (rc={rc}): {result}", file=sys.stderr)

    sys.exit(rc)


if __name__ == "__main__":
    main()

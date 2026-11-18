"""Network Experiment Data Analysis & SVG Visualization Pipeline — WF-14, T-15, T-17, T-18.

Pure Python standard library implementation for parsing experiment results,
enforcing validity labels, and rendering standalone SVG charts.
"""
import csv
import glob
import html
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.pcapio import read_pcap_summary


def parse_ping_log(log_path: str) -> Dict[str, Any]:
    """Parse standard ping output log file."""
    result = {"sent": 0, "received": 0, "loss_pct": 100.0, "rtt_avg_ms": None, "rtt_min_ms": None, "rtt_max_ms": None}
    if not os.path.exists(log_path):
        return result
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    loss_match = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received,.*?(\d+(?:\.\d+)?)%\s+packet loss", content, re.IGNORECASE)
    if loss_match:
        result["sent"] = int(loss_match.group(1))
        result["received"] = int(loss_match.group(2))
        result["loss_pct"] = float(loss_match.group(3))

    rtt_match = re.search(r"(?:rtt|round-trip)\s+min/avg/max(?:/mdev)?\s*=\s*([\d\.]+)/([\d\.]+)/([\d\.]+)", content, re.IGNORECASE)
    if rtt_match:
        result["rtt_min_ms"] = float(rtt_match.group(1))
        result["rtt_avg_ms"] = float(rtt_match.group(2))
        result["rtt_max_ms"] = float(rtt_match.group(3))

    return result


def parse_iperf3_json(json_path: str) -> Dict[str, Any]:
    """Parse iperf3 JSON output file."""
    result = {
        "valid": False,
        "goodput_mbps": 0.0,
        "sent_bytes": 0,
        "received_bytes": 0,
        "retransmits": 0,
        "streams_count": 0,
        "duration_sec": 0.0,
        "intervals": []
    }
    if not os.path.exists(json_path):
        return result

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        end = data.get("end", {})
        sum_received = end.get("sum_received", {})
        sum_sent = end.get("sum_sent", {})

        bps = sum_received.get("bits_per_second", 0.0)
        result["goodput_mbps"] = bps / 1_000_000.0
        result["received_bytes"] = sum_received.get("bytes", 0)
        result["sent_bytes"] = sum_sent.get("bytes", 0)
        result["retransmits"] = sum_sent.get("retransmits", 0)
        result["duration_sec"] = sum_received.get("seconds", 0.0)
        result["streams_count"] = len(end.get("streams", []))

        # Extract timeseries intervals
        intervals = []
        for interval in data.get("intervals", []):
            sum_i = interval.get("sum", {})
            intervals.append({
                "start": sum_i.get("start", 0.0),
                "end": sum_i.get("end", 0.0),
                "bytes": sum_i.get("bytes", 0),
                "mbps": (sum_i.get("bits_per_second", 0.0)) / 1_000_000.0,
                "retransmits": sum_i.get("retransmits", 0)
            })
        result["intervals"] = intervals
        result["valid"] = True

    except Exception as e:
        result["error"] = str(e)

    return result


def parse_iperf2_log(log_path: str) -> Dict[str, Any]:
    """Parse legacy iperf2 text output log file."""
    result = {"valid": False, "bandwidth_mbps": 0.0}
    if not os.path.exists(log_path):
        return result
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    # Matches: "0.0-10.0 sec  114 MBytes  95.6 Mbits/sec"
    match = re.search(r"([\d\.]+)\s+([KMGT]?bits/sec)", content)
    if match:
        val = float(match.group(1))
        unit = match.group(2).lower()
        if "kbits" in unit:
            val /= 1000.0
        elif "gbits" in unit:
            val *= 1000.0
        result["bandwidth_mbps"] = val
        result["valid"] = True
    return result


def parse_subflows_json(json_path: str) -> Dict[str, Any]:
    """Parse subflows.json metadata."""
    if not os.path.exists(json_path):
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def render_svg_bar_chart(title: str, categories: List[str], values: List[float], output_path: str, unit: str = "Mbps"):
    """Render a standalone SVG bar chart without third-party dependencies (with XML escaping, P2-6)."""
    width = 600
    height = 350
    margin_left = 80
    margin_right = 40
    margin_top = 50
    margin_bottom = 60

    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    max_val = max(values) if values and max(values) > 0 else 100.0
    y_max = max_val * 1.15

    n = len(categories)
    bar_width = (plot_w / max(1, n)) * 0.6
    bar_spacing = plot_w / max(1, n)

    safe_title = html.escape(title)
    safe_unit = html.escape(unit)

    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        '  <style>',
        '    .title { font-family: sans-serif; font-size: 16px; font-weight: bold; fill: #1e293b; }',
        '    .axis-label { font-family: sans-serif; font-size: 11px; fill: #64748b; }',
        '    .bar-val { font-family: sans-serif; font-size: 11px; font-weight: bold; fill: #0f172a; }',
        '    .grid { stroke: #e2e8f0; stroke-dasharray: 4; }',
        '  </style>',
        f'  <rect width="{width}" height="{height}" fill="#ffffff" rx="8"/>',
        f'  <text x="{width / 2}" y="30" text-anchor="middle" class="title">{safe_title}</text>'
    ]

    # Draw horizontal grid lines (5 steps)
    for i in range(5):
        val_step = (y_max / 4) * i
        y_pos = margin_top + plot_h - (val_step / y_max) * plot_h
        svg_lines.append(f'  <line x1="{margin_left}" y1="{y_pos}" x2="{width - margin_right}" y2="{y_pos}" class="grid"/>')
        svg_lines.append(f'  <text x="{margin_left - 10}" y="{y_pos + 4}" text-anchor="end" class="axis-label">{val_step:.1f}</text>')

    # Draw Bars
    colors = ["#2563eb", "#059669", "#d97706", "#dc2626", "#7c3aed"]
    for i, (cat, val) in enumerate(zip(categories, values)):
        color = colors[i % len(colors)]
        bar_h = (val / y_max) * plot_h
        x_pos = margin_left + i * bar_spacing + (bar_spacing - bar_width) / 2
        y_pos = margin_top + plot_h - bar_h

        safe_cat = html.escape(cat)
        svg_lines.append(f'  <rect x="{x_pos}" y="{y_pos}" width="{bar_width}" height="{bar_h}" fill="{color}" rx="3"/>')
        svg_lines.append(f'  <text x="{x_pos + bar_width / 2}" y="{y_pos - 6}" text-anchor="middle" class="bar-val">{val:.1f} {safe_unit}</text>')
        svg_lines.append(f'  <text x="{x_pos + bar_width / 2}" y="{margin_top + plot_h + 20}" text-anchor="middle" class="axis-label">{safe_cat}</text>')

    svg_lines.append('</svg>')

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(svg_lines))


def analyze_run_dir(run_dir: str, strict: bool = False) -> Tuple[int, Dict[str, Any], List[Dict[str, Any]]]:
    """
    Analyze all metrics within an output run directory (S6).
    Integrates PCAPs, ping logs, iperf logs, failover intervals, and enforces validity labels.
    Outputs results.csv, summary.json, and charts.
    Returns (exit_code, summary_dict, rows).
    """
    if not os.path.exists(run_dir):
        return 0, {"error": f"Run directory '{run_dir}' does not exist."}, []

    run_id = os.path.basename(os.path.normpath(run_dir))
    subflows_path = os.path.join(run_dir, "subflows.json")
    has_subflows_file = os.path.exists(subflows_path)
    subflows_info = parse_subflows_json(subflows_path) if has_subflows_file else {}

    reachability_info = {}
    reach_path = os.path.join(run_dir, "reachability.json")
    if os.path.exists(reach_path):
        try:
            with open(reach_path, "r", encoding="utf-8") as f:
                reachability_info = json.load(f)
        except Exception:
            pass

    # 1. Parse Traffic Generator Logs (iperf3 / iperf2)
    iperf3_client = os.path.join(run_dir, "client-0.json")
    iperf3_res = parse_iperf3_json(iperf3_client) if os.path.exists(iperf3_client) else {}

    goodput_mbps = iperf3_res.get("goodput_mbps", 0.0)
    retransmits = iperf3_res.get("retransmits", 0)
    intervals = iperf3_res.get("intervals", [])

    # If iperf3 JSON is absent, check legacy iperf2 logs
    if not iperf3_res.get("valid", False):
        iperf2_logs = glob.glob(os.path.join(run_dir, "*iperf*.txt")) + glob.glob(os.path.join(run_dir, "client-*-iperf.log"))
        if iperf2_logs:
            ip2_res = parse_iperf2_log(iperf2_logs[0])
            if ip2_res.get("valid", False):
                goodput_mbps = ip2_res.get("bandwidth_mbps", 0.0)

    # 2. Parse Ping Logs (P-5)
    rtt_avg_ms = None
    loss_pct = None
    ping_logs = glob.glob(os.path.join(run_dir, "*ping*.txt")) + glob.glob(os.path.join(run_dir, "client-*-log.txt"))
    if ping_logs:
        ping_res = parse_ping_log(ping_logs[0])
        rtt_avg_ms = ping_res.get("rtt_avg_ms")
        loss_pct = ping_res.get("loss_pct")

    # 3. Parse Binary PCAPs (P-4)
    pcap_files = glob.glob(os.path.join(run_dir, "*.pcap"))
    pcaps_summary = [read_pcap_summary(p) for p in pcap_files]
    total_pcap_packets = sum(p.get("total_packets", 0) for p in pcaps_summary)
    total_pcap_bytes = sum(p.get("total_bytes", 0) for p in pcaps_summary)

    # 4. Extract Workloads (D-12, Phase 5)
    workloads_path = os.path.join(run_dir, "workloads.json")
    has_workloads_file = os.path.exists(workloads_path)
    workload_results = []
    all_clients_passed = True
    client_count = 0
    if has_workloads_file:
        try:
            with open(workloads_path, "r", encoding="utf-8") as f:
                wdata = json.load(f)
            for w in wdata.get("workloads", []):
                idx = w.get("index")
                node = w.get("node")
                kind = w.get("kind")
                rc_file = os.path.join(run_dir, f"workload-{idx}-rc.txt")
                log_file = os.path.join(run_dir, f"workload-{idx}.log")
                rc = None
                if os.path.exists(rc_file):
                    try:
                        with open(rc_file, "r", encoding="utf-8") as rf:
                            rc_content = rf.read().strip()
                            if "rc=" in rc_content:
                                rc = int(rc_content.split("rc=")[-1].strip())
                            elif rc_content.isdigit():
                                rc = int(rc_content)
                    except Exception:
                        rc = None

                workload_results.append({
                    "node": node,
                    "kind": kind,
                    "rc": rc,
                    "stdout_path": log_file if os.path.exists(log_file) else None
                })

                if kind in ("iperf3_client", "custom_client", "mesh_ping"):
                    client_count += 1
                    if rc is None or rc != 0:
                        all_clients_passed = False
        except Exception as e:
            logging.warning(f"Error parsing workloads.json: {e}")

    # 5. Extract Metadata
    metadata = {}
    meta_path = os.path.join(run_dir, "metadata.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception:
            pass

    mptcp_requested = subflows_info.get("mptcp_requested", False)
    if not mptcp_requested and metadata.get("protocol") == "MPTCP":
        mptcp_requested = True
    subflows = subflows_info.get("max_subflows", 0)
    topo_type = reachability_info.get("topo_type", metadata.get("topo_type", "unknown"))

    # 6. Compute Failover Metrics for SC-3 (P-5)
    failover_metrics = {}
    if intervals:
        before_5s = [i["mbps"] for i in intervals if i["end"] <= 5.0]
        after_5s = [i["mbps"] for i in intervals if i["start"] >= 5.0]
        avg_before = round(sum(before_5s) / len(before_5s), 3) if before_5s else None
        avg_after = round(sum(after_5s) / len(after_5s), 3) if after_5s else None
        ratio = round(avg_after / avg_before, 3) if (avg_before and avg_after and avg_before > 0) else None
        failover_metrics = {
            "before_5s_mbps": avg_before,
            "after_5s_mbps": avg_after,
            "failover_ratio": ratio
        }

    # 7. Validity Labeling & Empty Run Guard (P-3, D-12)
    has_any_data = bool(has_subflows_file or iperf3_res.get("valid") or ping_logs or pcap_files or reachability_info or has_workloads_file)
    is_valid = True
    labels = []

    if not has_any_data:
        is_valid = False
        labels.append("No Data")
    elif has_workloads_file:
        goodput_mbps = None  # None for custom workload mode, not 0.0 (D-12)
        if client_count == 0 or all_clients_passed:
            is_valid = True
            labels.append("Valid Custom Workload")
        else:
            is_valid = False
            failed_count = sum(
                1 for r in workload_results
                if r["kind"] in ("iperf3_client", "custom_client", "mesh_ping")
                and (r["rc"] is None or r["rc"] != 0)
            )
            labels.append(f"Custom Workload Failure({failed_count})")
    elif mptcp_requested:
        if not has_subflows_file:
            is_valid = False
            labels.append("No MPTCP Evidence")
        elif subflows < 2:
            is_valid = False
            labels.append("Single-Path Fallback")
        else:
            labels.append("Valid Multipath")
    else:
        labels.append("Valid Single-Path")

    unapplied = metadata.get("unapplied_parameters", [])
    if unapplied:
        labels.append(f"UnappliedParams({len(unapplied)})")

    label_str = "; ".join(labels)

    record = {
        "run_id": run_id,
        "scenario": metadata.get("scenario", "default"),
        "topo_type": topo_type,
        "protocol": "MPTCP" if mptcp_requested else "TCP",
        "mptcp_requested": "yes" if mptcp_requested else "no",
        "goodput_mbps": round(goodput_mbps, 3) if goodput_mbps is not None else None,
        "rtt_avg_ms": rtt_avg_ms,
        "loss_pct": loss_pct,
        "retransmits": retransmits,
        "subflows": subflows,
        "valid": "true" if is_valid else "false",
        "label": label_str
    }

    # Save results.csv
    csv_path = os.path.join(run_dir, "results.csv")
    fieldnames = list(record.keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(record)

    # Save summary.json
    summary = {
        "run_id": run_id,
        "valid": is_valid,
        "label": label_str,
        "metrics": {
            "goodput_mbps": goodput_mbps,
            "rtt_avg_ms": rtt_avg_ms,
            "loss_pct": loss_pct,
            "retransmits": retransmits,
            "subflows": subflows,
            "pcap_packets": total_pcap_packets,
            "pcap_bytes": total_pcap_bytes
        },
        "failover_metrics": failover_metrics,
        "pcaps": pcaps_summary,
        "reachability": reachability_info.get("verdict", "UNKNOWN"),
        "unapplied_parameters": unapplied
    }
    if has_workloads_file:
        summary["workload_results"] = workload_results

    summary_path = os.path.join(run_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    # Render standalone SVG chart
    svg_path = os.path.join(run_dir, "goodput_chart.svg")
    chart_val = goodput_mbps if goodput_mbps is not None else 0.0
    render_svg_bar_chart(
        title=f"Experiment Goodput — {run_id}",
        categories=[record["protocol"]],
        values=[chart_val],
        output_path=svg_path,
        unit="Mbps"
    )

    exit_code = 4 if (strict and not is_valid) else 0
    return exit_code, summary, [record]

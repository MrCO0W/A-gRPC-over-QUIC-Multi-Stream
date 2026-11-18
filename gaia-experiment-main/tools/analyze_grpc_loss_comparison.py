#!/usr/bin/env python3
"""Standalone Analysis & Visualization Engine for gRPC TCP vs QUIC Loss-Rate Comparison Sweep.

Parses custom workload client logs (workload-1.log) and per-node PCAP summaries (c0.pcap, s0.pcap),
aggregates results into CSV, and generates publication-grade comparison charts.
"""
import os
import sys
import json
import logging
import shutil
from typing import Dict, Any, List

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from core.pcapio import read_pcap_summary

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def parse_run_directory(run_dir: str, proto: str, loss: int) -> Dict[str, Any]:
    """Parse metrics from a single sweep run directory."""
    row = {
        "protocol": proto.upper(),
        "loss_pct": loss,
        "duration_sec": None,
        "goodput_mbps": None,
        "throughput_mbps": None,
        "app_bytes": None,
        "app_mb": None,
        "sent_packets": 0,
        "sent_bytes": 0,
        "sent_mb": 0.0,
        "delivered_packets": 0,
        "delivered_bytes": 0,
        "delivered_mb": 0.0,
        "s0_to_c0_packet_ratio_pct": None,
    }

    # 1. Parse client workload log (workload-1.log)
    client_log = os.path.join(run_dir, "workload-1.log")
    if os.path.exists(client_log):
        try:
            with open(client_log, "r", encoding="utf-8") as f:
                content = f.read().strip()
                # If wrapped with non-JSON text, find the outermost JSON block
                if "{" in content and "}" in content:
                    json_str = content[content.find("{"):content.rfind("}") + 1]
                    cdata = json.loads(json_str)
                    row["duration_sec"] = cdata.get("duration_sec")
                    row["goodput_mbps"] = cdata.get("goodput_mbps")
                    row["throughput_mbps"] = cdata.get("throughput_mbps")
                    total_bytes = cdata.get("total_bytes")
                    row["app_bytes"] = total_bytes
                    if total_bytes is not None:
                        row["app_mb"] = total_bytes / 1_000_000.0
                else:
                    logging.warning(f"No JSON object found in {client_log}")
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Failed to parse JSON from {client_log}: {e}")
    else:
        logging.warning(f"Client log not found: {client_log}")

    # 2. Parse sent PCAP (c0.pcap)
    c0_pcap = os.path.join(run_dir, "c0.pcap")
    if os.path.exists(c0_pcap):
        c0_summary = read_pcap_summary(c0_pcap)
        if c0_summary.get("valid"):
            s_pkts = c0_summary.get("total_packets", 0)
            s_bytes = c0_summary.get("total_bytes", 0)
            row["sent_packets"] = s_pkts
            row["sent_bytes"] = s_bytes
            row["sent_mb"] = s_bytes / 1_000_000.0

    # 3. Parse delivered PCAP (s0.pcap)
    s0_pcap = os.path.join(run_dir, "s0.pcap")
    if os.path.exists(s0_pcap):
        s0_summary = read_pcap_summary(s0_pcap)
        if s0_summary.get("valid"):
            d_pkts = s0_summary.get("total_packets", 0)
            d_bytes = s0_summary.get("total_bytes", 0)
            row["delivered_packets"] = d_pkts
            row["delivered_bytes"] = d_bytes
            row["delivered_mb"] = d_bytes / 1_000_000.0

    if row["sent_packets"] > 0:
        # Note: tcpdump -i any captures bidirectional packets (data + ACKs) on both endpoints
        row["s0_to_c0_packet_ratio_pct"] = (row["delivered_packets"] / row["sent_packets"]) * 100.0

    return row


def aggregate_sweep_results(base_output_dir: str) -> pd.DataFrame:
    """Collect metrics across all 12 loss-rate sweep directories."""
    records: List[Dict[str, Any]] = []
    protocols = ["tcp", "quic"]
    loss_levels = [0, 1, 2, 3, 4, 5]

    for loss in loss_levels:
        for proto in protocols:
            run_dir = os.path.join(base_output_dir, f"{proto}_loss{loss}")
            row = parse_run_directory(run_dir, proto, loss)
            records.append(row)

    df = pd.DataFrame(records)
    return df


def generate_charts(df: pd.DataFrame, output_dir: str, docs_fig_dir: str):
    """Render and save publication-quality comparison charts."""
    sns.set_theme(style="whitegrid", font_scale=1.1)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(docs_fig_dir, exist_ok=True)

    # Palette with distinct colors for TCP (blue) and QUIC (orange)
    palette = {"TCP": "#1f77b4", "QUIC": "#ff7f0e"}

    # -------------------------------------------------------------
    # Chart 1: Goodput vs Loss Rate (2-panel: Full Range & Loss 1-5% Detail)
    # -------------------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))

    # Panel 1: Full Range 0% - 5%
    sns.lineplot(
        data=df,
        x="loss_pct",
        y="goodput_mbps",
        hue="protocol",
        style="protocol",
        markers=True,
        dashes=False,
        markersize=9,
        linewidth=2.5,
        palette=palette,
        ax=axes[0]
    )
    axes[0].set_title("Full Range: Goodput vs Loss Rate (0%–5%)", fontsize=13, fontweight="bold", pad=10)
    axes[0].set_xlabel("Transit Link Loss Rate (%)", fontsize=11, labelpad=8)
    axes[0].set_ylabel("Goodput (Mbps)", fontsize=11, labelpad=8)
    axes[0].set_xticks(range(6))
    axes[0].set_xlim(-0.2, 5.2)
    axes[0].set_ylim(0, 100)
    axes[0].legend(title="Protocol", loc="upper right", frameon=True)

    # Panel 2: Degraded Link Detail (Loss 1% - 5%) to show 2.3x - 3.6x gap clearly
    df_loss = df[df["loss_pct"] >= 1].copy()
    sns.lineplot(
        data=df_loss,
        x="loss_pct",
        y="goodput_mbps",
        hue="protocol",
        style="protocol",
        markers=True,
        dashes=False,
        markersize=9,
        linewidth=2.5,
        palette=palette,
        ax=axes[1]
    )
    axes[1].set_title("Degraded Range Detail: Goodput vs Loss Rate (1%–5%)", fontsize=13, fontweight="bold", pad=10)
    axes[1].set_xlabel("Transit Link Loss Rate (%)", fontsize=11, labelpad=8)
    axes[1].set_ylabel("Goodput (Mbps)", fontsize=11, labelpad=8)
    axes[1].set_xticks(range(1, 6))
    axes[1].set_xlim(0.8, 5.2)
    axes[1].set_ylim(0, 5.5)
    axes[1].legend(title="Protocol", loc="upper right", frameon=True)

    fig.suptitle("gRPC over TCP vs QUIC: Goodput Under Packet Loss", fontsize=15, fontweight="bold", y=1.02)
    
    chart1_out = os.path.join(output_dir, "goodput_vs_loss.png")
    chart1_doc = os.path.join(docs_fig_dir, "goodput_vs_loss.png")
    fig.savefig(chart1_out, dpi=150, bbox_inches="tight")
    fig.savefig(chart1_doc, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logging.info(f"✓ Saved Chart 1 to: {chart1_out} and {chart1_doc}")

    # -------------------------------------------------------------
    # Chart 2: Delivered Packet Count & Byte Volume vs Loss Rate
    # -------------------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))

    # Subplot 1: Delivered Packet Count
    sns.lineplot(
        data=df,
        x="loss_pct",
        y="delivered_packets",
        hue="protocol",
        style="protocol",
        markers=True,
        dashes=False,
        markersize=9,
        linewidth=2.5,
        palette=palette,
        ax=axes[0]
    )
    axes[0].set_title("Delivered Packet Count vs Loss Rate", fontsize=13, fontweight="bold", pad=10)
    axes[0].set_xlabel("Transit Link Loss Rate (%)", fontsize=11, labelpad=8)
    axes[0].set_ylabel("Packets Captured at Server s0 (Total)", fontsize=11, labelpad=8)
    axes[0].set_xticks(range(6))
    axes[0].set_xlim(-0.2, 5.2)
    axes[0].legend(title="Protocol", loc="upper right", frameon=True)

    # Subplot 2: Delivered Byte Volume in Decimal Megabytes (MB = 10^6 bytes)
    sns.lineplot(
        data=df,
        x="loss_pct",
        y="delivered_mb",
        hue="protocol",
        style="protocol",
        markers=True,
        dashes=False,
        markersize=9,
        linewidth=2.5,
        palette=palette,
        ax=axes[1]
    )
    axes[1].set_title("Delivered Data Volume vs Loss Rate", fontsize=13, fontweight="bold", pad=10)
    axes[1].set_xlabel("Transit Link Loss Rate (%)", fontsize=11, labelpad=8)
    axes[1].set_ylabel("Delivered Volume at s0 (Decimal MB, 10⁶ B)", fontsize=11, labelpad=8)
    axes[1].set_xticks(range(6))
    axes[1].set_xlim(-0.2, 5.2)
    axes[1].legend(title="Protocol", loc="upper right", frameon=True)

    fig.suptitle("gRPC over TCP vs QUIC: Packet Transmission Volume vs Packet Loss", fontsize=15, fontweight="bold", y=1.02)
    
    chart2_out = os.path.join(output_dir, "packets_vs_loss.png")
    chart2_doc = os.path.join(docs_fig_dir, "packets_vs_loss.png")
    fig.savefig(chart2_out, dpi=150, bbox_inches="tight")
    fig.savefig(chart2_doc, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logging.info(f"✓ Saved Chart 2 to: {chart2_out} and {chart2_doc}")


def main():
    base_dir = os.path.join(PROJECT_ROOT, "output", "grpc_loss_comparison")
    docs_fig_dir = os.path.join(PROJECT_ROOT, "docs", "figures", "grpc_loss_comparison")
    logging.info(f"Aggregating results from: {base_dir}")

    df = aggregate_sweep_results(base_dir)

    # Save CSV to both output and docs/figures
    csv_out = os.path.join(base_dir, "aggregated_results.csv")
    csv_doc = os.path.join(docs_fig_dir, "aggregated_results.csv")
    df.to_csv(csv_out, index=False)
    df.to_csv(csv_doc, index=False)
    logging.info(f"✓ Saved aggregated dataset: {csv_out} and {csv_doc}")

    # Generate figures
    generate_charts(df, base_dir, docs_fig_dir)

    # Print summary table to stdout
    print("\n" + "=" * 90)
    print(" gRPC over TCP vs QUIC Loss-Rate Benchmark Summary (Decimal MB = 10^6 Bytes)")
    print("=" * 90)
    summary_cols = ["protocol", "loss_pct", "duration_sec", "goodput_mbps", "app_mb", "sent_packets", "delivered_packets", "delivered_mb", "s0_to_c0_packet_ratio_pct"]
    print(df[summary_cols].to_string(index=False))
    print("=" * 90 + "\n")


if __name__ == "__main__":
    main()

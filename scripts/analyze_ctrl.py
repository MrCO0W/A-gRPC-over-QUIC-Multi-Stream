#!/usr/bin/env python3
"""Aggregate Experiment C (downlink command delivery) results.

Finds every ctrlbench result file under --results (any layout; files are
recognised by their schema field), groups them by
(regime, app, proto, poll interval, loss) and writes:

  summary_ctrl.csv         one row per cell
  report_ctrl.md           tables ready to paste, one per regime and metric
  fig_c1_latency_<regime>.svg   p50 (dashed) and p99 (solid) of the
                                issue-to-Edge-Agent latency vs loss, log y
  fig_c2_cost_idle.svg     requests and bytes on the link per delivered command
                           (idle regime, 0 % loss)

Percentiles are taken over the POOLED latencies of all repetitions of a cell,
because a single 30 s run holds only ~150 commands and its own p99 is decided
by its top one or two samples. The per-run p99 median is reported alongside.

Python 3.8+, standard library only.

    python3 analyze_ctrl.py --results <gaia>/output/test_ctrl --out report_ctrl
"""

import argparse
import csv
import json
import math
import os
import statistics
import sys
from collections import defaultdict

SCHEMA = "test.ctrlbench.result/1"

APP_ORDER = ["poll", "lpoll", "sse", "grpc"]
PROTO_ORDER = ["h1", "h2", "h3"]
APP_NAME = {"poll": "REST polling", "lpoll": "REST long polling",
            "sse": "REST + SSE", "grpc": "gRPC stream"}
PROTO_NAME = {"h1": "HTTP/1.1", "h2": "HTTP/2", "h3": "HTTP/3"}


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = p * (len(sorted_vals) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - pos) + sorted_vals[hi] * (pos - lo)


def med(vals):
    vals = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return statistics.median(vals) if vals else float("nan")


def load(results_dir):
    runs = []
    for root, _dirs, files in os.walk(results_dir):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and d.get("schema") == SCHEMA:
                d["_path"] = path
                runs.append(d)
    return runs


def mode_label(app, proto, interval):
    s = f"{APP_NAME.get(app, app)}, {PROTO_NAME.get(proto, proto)}"
    if app == "poll":
        s += f", {interval * 1000:.0f} ms"
    return s


def mode_key(app, proto, interval):
    return (APP_ORDER.index(app) if app in APP_ORDER else 9,
            -interval, PROTO_ORDER.index(proto) if proto in PROTO_ORDER else 9)


def aggregate(runs):
    cells = defaultdict(list)
    for d in runs:
        p = d.get("params", {})
        regime = str(p.get("regime", "busy" if p.get("up_streams", 0) else "idle"))
        interval = float(p.get("poll_interval_s", 0) or 0) if d["app"] == "poll" else 0.0
        loss = float(p.get("loss_pct", float("nan")))
        cells[(regime, d["app"], d["proto"], interval, loss)].append(d)

    rows = []
    for (regime, app, proto, interval, loss), ds in cells.items():
        lat = sorted(x for d in ds for x in (d["downlink"].get("latencies_ms") or []))
        issued = sum(d["downlink"]["issued"] for d in ds)
        delivered = sum(d["downlink"]["delivered"] for d in ds)
        missing = sum(d["downlink"]["missing"] for d in ds)
        up = [d.get("uplink") for d in ds if d.get("uplink")]
        rows.append({
            "regime": regime, "app": app, "proto": proto,
            "poll_interval_ms": round(interval * 1000), "loss_pct": loss,
            "mode": mode_label(app, proto, interval), "reps": len(ds),
            "commands": len(lat), "issued": issued, "delivered": delivered, "missing": missing,
            "lat_p50_ms": percentile(lat, 0.50), "lat_p90_ms": percentile(lat, 0.90),
            "lat_p99_ms": percentile(lat, 0.99), "lat_max_ms": lat[-1] if lat else float("nan"),
            "lat_mean_ms": statistics.fmean(lat) if lat else float("nan"),
            "run_p99_median_ms": med([d["downlink"]["latency"]["p99_ms"] for d in ds]),
            "run_p99_min_ms": min(d["downlink"]["latency"]["p99_ms"] for d in ds),
            "run_p99_max_ms": max(d["downlink"]["latency"]["p99_ms"] for d in ds),
            "confirm_p99_median_ms": med([d["downlink"]["confirm"]["p99_ms"] for d in ds
                                          if d["downlink"].get("ledger_ok")]),
            "requests_per_cmd": med([d["control"]["requests_per_command"] for d in ds]),
            "empty_poll_frac": med([d["control"]["empty_polls"] / d["control"]["poll_requests"]
                                    for d in ds if d["control"]["poll_requests"]]),
            "bytes_per_cmd": med([d["link"]["bytes_per_command"] for d in ds]),
            "reconnects": sum(d["control"]["reconnects"] for d in ds),
            "errors": sum(d["control"]["errors"] for d in ds),
            "uplink_msg_per_s": med([u["messages_per_s"] for u in up]) if up else float("nan"),
            "uplink_rtt_p99_ms": med([u["rtt"]["p99_ms"] for u in up]) if up else float("nan"),
        })
    rows.sort(key=lambda r: (r["regime"], mode_key(r["app"], r["proto"], r["poll_interval_ms"] / 1000),
                             r["loss_pct"]))
    return rows


# ---------------------------------------------------------------------------
# SVG (no dependencies)
# ---------------------------------------------------------------------------

PALETTE = {  # one hue per app, darker for higher HTTP version
    ("poll", "h1"): "#c9a227", ("poll", "h2"): "#a07d10", ("poll", "h3"): "#6e5500",
    # short-period polling gets its own hue so the two periods are not confused
    ("pollfast", "h1"): "#f0a35e", ("pollfast", "h2"): "#d9772b", ("pollfast", "h3"): "#a8521a",
    ("lpoll", "h1"): "#8fb8de", ("lpoll", "h2"): "#4f86c6", ("lpoll", "h3"): "#2a5a96",
    ("sse", "h1"): "#9ccf9a", ("sse", "h2"): "#4e9a4a", ("sse", "h3"): "#2e6b2b",
    ("grpc", "h2"): "#d1495b", ("grpc", "h3"): "#8b1e2f",
}
MARKER = {"h1": "circle", "h2": "square", "h3": "triangle"}


def color_of(app, proto, interval_ms):
    if app == "poll" and interval_ms and interval_ms < 500:
        return PALETTE.get(("pollfast", proto), "#777")
    return PALETTE.get((app, proto), "#777")


def _marker(kind, x, y, color):
    if kind == "square":
        return f'<rect x="{x-3.2:.1f}" y="{y-3.2:.1f}" width="6.4" height="6.4" fill="{color}"/>'
    if kind == "triangle":
        return (f'<polygon points="{x:.1f},{y-4:.1f} {x-3.8:.1f},{y+3:.1f} {x+3.8:.1f},{y+3:.1f}" '
                f'fill="{color}"/>')
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{color}"/>'


def latency_svg(rows, regime, path):
    rs = [r for r in rows if r["regime"] == regime]
    if not rs:
        return False
    losses = sorted({r["loss_pct"] for r in rs})
    series = defaultdict(dict)
    for r in rs:
        series[(r["app"], r["proto"], r["poll_interval_ms"])][r["loss_pct"]] = r
    vals = [v for r in rs for v in (r["lat_p50_ms"], r["lat_p99_ms"]) if v and v > 0]
    lo = 10 ** math.floor(math.log10(min(vals)))
    hi = 10 ** math.ceil(math.log10(max(vals)))
    W, H, L, R, T, B = 760, 440, 70, 250, 30, 50
    pw, ph = W - L - R, H - T - B

    def xp(i):
        return L + (pw * i / max(1, len(losses) - 1))

    def yp(v):
        return T + ph * (1 - (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)))

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'font-family="Helvetica, Arial, sans-serif" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="white"/>']
    d = lo
    while d <= hi * 1.0001:
        for m in (1, 2, 5):
            v = d * m
            if lo <= v <= hi:
                y = yp(v)
                out.append(f'<line x1="{L}" x2="{L+pw}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e5e5e5"/>')
                out.append(f'<text x="{L-6}" y="{y+4:.1f}" text-anchor="end" fill="#555">{v:g}</text>')
        d *= 10
    for i, l in enumerate(losses):
        out.append(f'<text x="{xp(i):.1f}" y="{T+ph+18}" text-anchor="middle" fill="#555">{l:g}</text>')
    out.append(f'<line x1="{L}" x2="{L+pw}" y1="{T+ph}" y2="{T+ph}" stroke="#333"/>')
    out.append(f'<line x1="{L}" x2="{L}" y1="{T}" y2="{T+ph}" stroke="#333"/>')
    out.append(f'<text x="{L+pw/2}" y="{H-12}" text-anchor="middle">loss per direction (%)</text>')
    out.append(f'<text transform="translate(18,{T+ph/2}) rotate(-90)" text-anchor="middle">'
               f'command latency, issue to Edge Agent (ms, log)</text>')
    out.append(f'<text x="{L}" y="18" font-weight="bold">regime: {regime}  -  solid p99, dashed p50</text>')
    ly = T + 6
    for key in sorted(series, key=lambda k: mode_key(k[0], k[1], k[2] / 1000)):
        app, proto, iv = key
        color = color_of(app, proto, iv)
        pts50, pts99 = [], []
        for i, l in enumerate(losses):
            r = series[key].get(l)
            if not r:
                continue
            if r["lat_p50_ms"] > 0:
                pts50.append((xp(i), yp(r["lat_p50_ms"])))
            if r["lat_p99_ms"] > 0:
                pts99.append((xp(i), yp(r["lat_p99_ms"])))
        if len(pts99) > 1:
            out.append('<polyline fill="none" stroke="%s" stroke-width="1.8" points="%s"/>'
                       % (color, " ".join(f"{x:.1f},{y:.1f}" for x, y in pts99)))
        if len(pts50) > 1:
            out.append('<polyline fill="none" stroke="%s" stroke-width="1.2" stroke-dasharray="4 3" points="%s"/>'
                       % (color, " ".join(f"{x:.1f},{y:.1f}" for x, y in pts50)))
        for x, y in pts99:
            out.append(_marker(MARKER.get(proto, "circle"), x, y, color))
        out.append(_marker(MARKER.get(proto, "circle"), L + pw + 20, ly, color))
        out.append(f'<text x="{L+pw+30}" y="{ly+4}">{mode_label(app, proto, iv / 1000)}</text>')
        ly += 18
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return True


def cost_svg(rows, path):
    rs = [r for r in rows if r["regime"] == "idle" and r["loss_pct"] == 0]
    if not rs:
        return False
    rs.sort(key=lambda r: mode_key(r["app"], r["proto"], r["poll_interval_ms"] / 1000))
    W, rowh, L = 760, 22, 230
    H = 46 + rowh * len(rs)
    half = (W - L - 40) / 2
    maxreq = max(r["requests_per_cmd"] for r in rs) or 1
    maxb = max(r["bytes_per_cmd"] for r in rs) or 1
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'font-family="Helvetica, Arial, sans-serif" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="white"/>',
           f'<text x="{L}" y="20" font-weight="bold">requests per command</text>',
           f'<text x="{L+half+30}" y="20" font-weight="bold">bytes on the link per command</text>']
    for i, r in enumerate(rs):
        y = 36 + i * rowh
        c = color_of(r["app"], r["proto"], r["poll_interval_ms"])
        out.append(f'<text x="{L-8}" y="{y+13}" text-anchor="end">{r["mode"]}</text>')
        w1 = (half - 50) * r["requests_per_cmd"] / maxreq
        w2 = (half - 60) * r["bytes_per_cmd"] / maxb
        out.append(f'<rect x="{L}" y="{y+2}" width="{w1:.1f}" height="14" fill="{c}"/>')
        out.append(f'<text x="{L+w1+4:.1f}" y="{y+13}" fill="#333">{r["requests_per_cmd"]:.2f}</text>')
        x2 = L + half + 30
        out.append(f'<rect x="{x2}" y="{y+2}" width="{w2:.1f}" height="14" fill="{c}"/>')
        out.append(f'<text x="{x2+w2+4:.1f}" y="{y+13}" fill="#333">{r["bytes_per_cmd"]:.0f}</text>')
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return True


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def fmt(v, nd=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{v:,.{nd}f}"


def table(rows, regime, metric, nd=1):
    rs = [r for r in rows if r["regime"] == regime]
    losses = sorted({r["loss_pct"] for r in rs})
    modes = sorted({(r["app"], r["proto"], r["poll_interval_ms"]) for r in rs},
                   key=lambda k: mode_key(k[0], k[1], k[2] / 1000))
    by = {(r["app"], r["proto"], r["poll_interval_ms"], r["loss_pct"]): r for r in rs}
    lines = ["| mode | " + " | ".join(f"loss {l:g} %" for l in losses) + " |",
             "|---|" + "---|" * len(losses)]
    for app, proto, iv in modes:
        cells = [fmt(by[(app, proto, iv, l)][metric], nd) if (app, proto, iv, l) in by else "-"
                 for l in losses]
        lines.append(f"| {mode_label(app, proto, iv / 1000)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True, help="directory holding ctrlbench result files")
    ap.add_argument("--out", default="report_ctrl", help="output directory")
    args = ap.parse_args()

    runs = load(args.results)
    if not runs:
        print(f"error: no {SCHEMA} files under {args.results}", file=sys.stderr)
        return 2
    rows = aggregate(runs)
    os.makedirs(args.out, exist_ok=True)

    with open(os.path.join(args.out, "summary_ctrl.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    regimes = sorted({r["regime"] for r in rows})
    figs = []
    for rg in regimes:
        p = os.path.join(args.out, f"fig_c1_latency_{rg}.svg")
        if latency_svg(rows, rg, p):
            figs.append(os.path.basename(p))
    if cost_svg(rows, os.path.join(args.out, "fig_c2_cost_idle.svg")):
        figs.append("fig_c2_cost_idle.svg")

    md = ["# Experiment C - downlink command delivery", "",
          f"{len(runs)} runs, {len(rows)} cells. Latency = issue upstream to held by the Edge Agent "
          "(one-way, shared clock), percentiles over the pooled commands of all repetitions.", ""]
    for rg in regimes:
        md += [f"## Regime: {rg}", "",
               "### Command latency p50 (ms)", "", table(rows, rg, "lat_p50_ms"), "",
               "### Command latency p99 (ms)", "", table(rows, rg, "lat_p99_ms"), "",
               "### Decision-to-confirmation p99 (ms, median over runs)", "",
               table(rows, rg, "confirm_p99_median_ms"), "",
               "### Requests per delivered command (median over runs)", "",
               table(rows, rg, "requests_per_cmd", 2), ""]
        if rg == "idle":
            md += ["### Bytes on the link per delivered command (median over runs)", "",
                   table(rows, rg, "bytes_per_cmd", 0), ""]
        else:
            md += ["### Uplink telemetry delivered alongside (measurements/s, median over runs)", "",
                   table(rows, rg, "uplink_msg_per_s", 0), ""]
        md += ["### Missing commands (issued in window, never delivered; summed over runs)", "",
               table(rows, rg, "missing", 0), ""]
    md += ["## Figures", ""] + [f"- {f}" for f in figs] + [""]
    with open(os.path.join(args.out, "report_ctrl.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"{len(runs)} runs -> {len(rows)} cells -> {args.out}/ (summary_ctrl.csv, report_ctrl.md, "
          + ", ".join(figs) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())

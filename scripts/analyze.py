#!/usr/bin/env python3
"""Aggregate the test sweep and render the figures.

Reads every rpcbench.json under --results, aggregates repeats by median, and
writes CSV tables plus standalone SVG figures. Standard library only, so it runs
on the Mininet host without installing anything.

    python3 analyze.py --results /path/gaia/output/test \\
                       --manifest scripts/manifest.json \\
                       --out ./report

Outputs in --out:
    runs.csv                      one row per individual run
    summary.csv                   one row per (regime, mode, loss), median of repeats
    streams.csv                   one row per stream per run
    fig1_goodput_vs_loss.svg      throughput regime
    fig2_p99_vs_loss.svg          head-of-line regime
    fig3_stall_correlation.svg    shared-stall evidence
    fig5_goodput_{1conn,8conn}.svg  throughput, connection count held fixed
    fig6_p99_{1conn,8conn}.svg      tail latency, connection count held fixed
    fig4_progress_<cell>.svg      per-stream progress over time
    fig7_stream_scaling.svg       Experiment A: per-stream throughput against N
    fig8_stream_ratio.svg         Experiment A: rate and ratio against N\n    fig10_retention_vs_loss.svg   Experiment A: the same ratio against loss
    fig9_ramp_cost.svg            Experiment B: what one more stream costs
    stream_sweep.csv              Experiment A: both views as numbers
    ramp.csv                      Experiment B: stream add cost (only with --ramp runs)
    report.md                     the numbers written out as prose-ready text
"""

import argparse
import csv
import json
import math
import os
import statistics
import sys
from collections import defaultdict

# Bump alongside gen_scenarios.py; --version makes a stale copy obvious.
VERSION = "2026-09-18.6"

# --- Palette -------------------------------------------------------------
# Categorical slots 1-4 of the validated reference palette. Verified with the
# dataviz validator: all hard checks pass on the adjacent pairlist; two slots
# sit below 3:1 contrast on the light surface, which obliges the relief rule -
# hence every series is direct-labelled and every figure has a CSV twin.
# Slots 1-5 of the validated reference palette, in the documented order. That
# order is not cosmetic: the validator only clears the adjacent pairs in this
# sequence, so regrouping the arms by transport family (putting magenta next to
# orange, say) drops two pairs below the normal-vision floor. Marker shape, not
# hue, is what groups the arms for the reader.
SERIES = [
    # key,     display label,                    light,     dark,      marker
    # Label form: "<transport> - <C> conn x <S>", i.e. C connections carrying S
    # logical streams each. C x S is 8 in every arm, so the label states the one
    # thing that actually differs between them and removes the ambiguity of
    # "8 connections" (which used to be readable as either 8 sockets or 8
    # multiplexed streams on one socket).
    ("rest", "REST / HTTP1.1 - 8 conn x 1", "#2a78d6", "#3987e5", "circle"),
    ("grpc", "gRPC / HTTP2 - 1 conn x 8", "#eb6834", "#d95926", "square"),
    ("grpcm", "gRPC / HTTP2 - 8 conn x 1", "#1baf7a", "#199e70", "square"),
    ("h3", "gRPC / HTTP3 - 1 conn x 8", "#eda100", "#c98500", "triangle"),
    ("h3m", "gRPC / HTTP3 - 8 conn x 1", "#e87ba4", "#d55181", "triangle"),
    ("qtun", "gRPC / raw QUIC - 1 conn x 8", "#008300", "#008300", "diamond"),
]
SERIES_ORDER = [s[0] for s in SERIES]
SERIES_META = {s[0]: s for s in SERIES}

SVG_STYLE = """
  .surface { fill: #fcfcfb; }
  .ink     { fill: #0b0b0b; }
  .ink2    { fill: #52514e; }
  .grid    { stroke: #e3e2dd; stroke-width: 1; }
  .axis    { stroke: #b9b8b2; stroke-width: 1; }
  text     { font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
  .title   { font-size: 15px; font-weight: 600; }
  .subtitle{ font-size: 11.5px; }
  .axlabel { font-size: 11.5px; }
  .tick    { font-size: 10.5px; }
  .slabel  { font-size: 11px; font-weight: 600; }
  .line    { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
  .mark    { stroke: #fcfcfb; stroke-width: 2; }
  .err     { stroke-width: 1.5; stroke-linecap: round; opacity: .55; }
  .lbox    { fill: #fcfcfb; stroke: #8f8e88; stroke-width: 1; }
  @media (prefers-color-scheme: dark) {
    .surface { fill: #1a1a19; }
    .ink     { fill: #ffffff; }
    .ink2    { fill: #c3c2b7; }
    .grid    { stroke: #333331; }
    .axis    { stroke: #4d4c49; }
    .mark    { stroke: #1a1a19; }
    .lbox    { fill: #1a1a19; stroke: #6b6a65; }
  }
"""

# Loss rate gets its own hue so that "same colour = same loss" is the first
# thing the eye groups; transport is then carried by line style and by whether
# the marker is filled. Both encodings survive greyscale printing.
LOSS_COLOUR = {0.0: "#1baf7a", 1.0: "#2a78d6", 5.0: "#e0561f"}


# =========================================================================
# Loading
# =========================================================================

def load_runs(results_dir):
    """Walk the results tree and return one dict per completed run."""
    runs = []
    for root, _dirs, files in os.walk(results_dir):
        if "rpcbench.json" not in files:
            continue
        path = os.path.join(root, "rpcbench.json")
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"warning: skipping {path}: {e}", file=sys.stderr)
            continue
        params = doc.get("params", {})
        if doc.get("totals", {}).get("batches", 0) == 0:
            print(f"warning: {path} recorded no batches "
                  f"({doc.get('totals', {}).get('first_error', 'no error recorded')})",
                  file=sys.stderr)
            continue
        runs.append({
            "path": path,
            "doc": doc,
            "mode": doc.get("mode", "?"),
            "regime": params.get("regime", "?"),
            "loss": float(params.get("loss_pct", 0.0)),
            "rep": os.path.basename(root),
            "cell": os.path.basename(os.path.dirname(root)),
        })
    return runs


def median(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else 0.0


# =========================================================================
# CSV output
# =========================================================================

RUN_FIELDS = [
    "cell", "rep", "regime", "mode", "loss_pct", "streams", "ramp",
    "batches", "messages", "wire_bytes", "bytes_per_msg",
    "goodput_mbps", "messages_per_s",
    "rtt_p50_ms", "rtt_p90_ms", "rtt_p99_ms", "rtt_max_ms",
    "oneway_p50_ms", "oneway_p99_ms",
    "blocked_fraction", "stall_correlation", "mean_stalled_streams",
    "stall_buckets", "p99_spread_ms", "send_errors", "recv_errors",
]


def run_row(r):
    d, t, h = r["doc"], r["doc"]["totals"], r["doc"]["hol"]
    return {
        "cell": r["cell"], "rep": r["rep"], "regime": r["regime"],
        "mode": r["mode"], "loss_pct": r["loss"],
        # streams and ramp are part of the cell identity, not just description:
        # the stream-count sweep puts several N at the same (regime, mode, loss)
        # and a ramp run is a different experiment at the same coordinates, so
        # aggregating without them would silently average unrelated cells.
        "streams": h.get("streams", 0) or int(r["doc"].get("params", {}).get("streams", 0) or 0),
        "ramp": 1 if r["doc"].get("ramp") else 0,
        "batches": t["batches"], "messages": t["messages"],
        "wire_bytes": t["wire_bytes"],
        "bytes_per_msg": round(t["wire_bytes"] / max(t["messages"], 1), 2),
        "goodput_mbps": round(t["goodput_mbps"], 4),
        "messages_per_s": round(t["messages_per_s"], 2),
        "rtt_p50_ms": round(d["rtt"]["p50_ms"], 4),
        "rtt_p90_ms": round(d["rtt"]["p90_ms"], 4),
        "rtt_p99_ms": round(d["rtt"]["p99_ms"], 4),
        "rtt_max_ms": round(d["rtt"]["max_ms"], 4),
        "oneway_p50_ms": round(d["one_way"]["p50_ms"], 4),
        "oneway_p99_ms": round(d["one_way"]["p99_ms"], 4),
        # The fraction of streams that were stalled at the same time, averaged
        # over the windows in which at least one stalled. This is the metric
        # that actually separates a shared ordered byte stream from independent
        # ones: perfect isolation floors it at 1/N, while TCP head-of-line
        # blocking drags it towards 1 because a loss holds back every stream
        # whose bytes arrived after the gap. (stall_correlation, which demands
        # that ALL N stall in the same window, is far too strict: only a loss
        # in the very first packet of a round does that.)
        "blocked_fraction": round(h["mean_stalled_streams"] / h["streams"], 4)
        if h.get("streams") else 0.0,
        "stall_correlation": round(h["stall_correlation"], 4),
        "mean_stalled_streams": round(h["mean_stalled_streams"], 4),
        "stall_buckets": h["stall_buckets"],
        "p99_spread_ms": round(h["p99_spread_ms"], 4),
        "send_errors": t["send_errors"], "recv_errors": t["recv_errors"],
    }


def write_csvs(runs, out_dir):
    rows = [run_row(r) for r in runs]
    rows.sort(key=lambda x: (x["regime"], x["loss_pct"],
                             SERIES_ORDER.index(x["mode"]) if x["mode"] in SERIES_ORDER else 99,
                             x["rep"]))
    with open(os.path.join(out_dir, "runs.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RUN_FIELDS)
        w.writeheader()
        w.writerows(rows)

    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["regime"], row["mode"], row["loss_pct"],
                 row["streams"], row["ramp"])].append(row)

    numeric = [c for c in RUN_FIELDS if c not in ("cell", "rep", "regime", "mode")]
    summary = []
    for (regime, mode, loss, nstreams, ramp), group in sorted(
            grouped.items(),
            key=lambda kv: (kv[0][0], kv[0][4], kv[0][3], kv[0][2],
                            SERIES_ORDER.index(kv[0][1]) if kv[0][1] in SERIES_ORDER else 99)):
        agg = {"regime": regime, "mode": mode, "loss_pct": loss,
               "streams": nstreams, "ramp": ramp, "repeats": len(group)}
        for col in numeric:
            if col in ("loss_pct", "streams", "ramp"):
                continue
            agg[col] = round(median([g[col] for g in group]), 4)
        summary.append(agg)
    fields = (["regime", "mode", "loss_pct", "streams", "ramp", "repeats"]
              + [c for c in numeric if c not in ("loss_pct", "streams", "ramp")])
    with open(os.path.join(out_dir, "summary.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(summary)

    with open(os.path.join(out_dir, "streams.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cell", "rep", "regime", "mode", "loss_pct", "stream",
                    "batches", "messages", "goodput_mbps",
                    "rtt_p50_ms", "rtt_p99_ms", "max_stall_ms"])
        for r in runs:
            for s in r["doc"]["streams"]:
                w.writerow([r["cell"], r["rep"], r["regime"], r["mode"], r["loss"],
                            s["name"], s["batches"], s["messages"],
                            round(s["goodput_mbps"], 4),
                            round(s["rtt"]["p50_ms"], 4), round(s["rtt"]["p99_ms"], 4),
                            round(s["max_stall_ms"], 4)])
    return summary


# =========================================================================
# SVG primitives
# =========================================================================

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def nice_ticks(lo, hi, target=5):
    """Round axis bounds outward to a readable step."""
    if hi <= lo:
        hi = lo + 1.0
    span = hi - lo
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for mult in (1, 2, 2.5, 5, 10):
        step = mag * mult
        if raw <= step:
            break
    start = math.floor(lo / step) * step
    end = math.ceil(hi / step) * step
    ticks, v = [], start
    while v <= end + step * 1e-9:
        ticks.append(round(v, 10))
        v += step
    return start, end, ticks


def fmt_num(v):
    """Compact number for a category label: 0.5 not 0.50, 2 not 2.0."""
    if float(v).is_integer():
        return str(int(v))
    return f"{v:g}"


def fmt_tick(v):
    if v == 0:
        return "0"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    if abs(v) >= 10:
        return f"{v:.0f}"
    if abs(v) >= 1:
        return f"{v:.1f}"
    return f"{v:.2f}"


def marker(kind, cx, cy, color, r=4.5, tip=None):
    """One shape per series: a secondary encoding so identity survives print,
    colour-vision deficiency and forced-colours mode.

    A tip adds a <title> child, which browsers surface as a native tooltip; in
    a printed figure it is simply ignored.
    """
    if kind == "circle":
        tag, attrs = "circle", f'cx="{cx:.2f}" cy="{cy:.2f}" r="{r}"'
    elif kind == "square":
        s = r * 1.8
        tag, attrs = "rect", (f'x="{cx - s / 2:.2f}" y="{cy - s / 2:.2f}" '
                              f'width="{s:.2f}" height="{s:.2f}" rx="1"')
    elif kind == "triangle":
        s = r * 1.25
        pts = (f"{cx:.2f},{cy - s * 1.15:.2f} {cx - s:.2f},{cy + s * .8:.2f} "
               f"{cx + s:.2f},{cy + s * .8:.2f}")
        tag, attrs = "polygon", f'points="{pts}"'
    else:  # diamond
        s = r * 1.35
        pts = (f"{cx:.2f},{cy - s:.2f} {cx + s:.2f},{cy:.2f} "
               f"{cx:.2f},{cy + s:.2f} {cx - s:.2f},{cy:.2f}")
        tag, attrs = "polygon", f'points="{pts}"'

    head = f'<{tag} {attrs} fill="{color}" class="mark"'
    if tip:
        return f"{head}><title>{esc(tip)}</title></{tag}>"
    return head + "/>"


def mark2(kind, cx, cy, colour, hollow=False, r=4.5, tip=None):
    """marker() plus a hollow variant.

    Filled and hollow are a real encoding, not decoration: across the ramp
    figures filled always means "the add is one more stream" and hollow always
    means "the add is one more connection", so the reader can tell the two
    designs apart without relying on hue.
    """
    if not hollow:
        return marker(kind, cx, cy, colour, r=r, tip=tip)
    if kind == "circle":
        body = f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r}"'
    elif kind == "square":
        s = r * 1.75
        body = (f'<rect x="{cx - s / 2:.2f}" y="{cy - s / 2:.2f}" '
                f'width="{s:.2f}" height="{s:.2f}" rx="1"')
    elif kind == "triangle":
        s = r * 1.25
        body = (f'<polygon points="{cx:.2f},{cy - s * 1.15:.2f} '
                f'{cx - s:.2f},{cy + s * .8:.2f} {cx + s:.2f},{cy + s * .8:.2f}"')
    else:
        s = r * 1.3
        body = (f'<polygon points="{cx:.2f},{cy - s:.2f} {cx + s:.2f},{cy:.2f} '
                f'{cx:.2f},{cy + s:.2f} {cx - s:.2f},{cy:.2f}"')
    tag = body.split()[0][1:]
    head = f'{body} fill="none" stroke="{colour}" stroke-width="2.2"'
    if tip:
        return f"{head}><title>{esc(tip)}</title></{tag}>"
    return head + "/>"


def legend_box(parts, entries, centre_x, y_top, cols=2, row_h=19, col_w=None):
    """A framed legend panel above the plot, the way conference figures draw it.

    Trailing end-of-line labels work when there are two or three series and
    they end far apart; they fail as soon as lines converge, which is exactly
    what these figures do at N=2 and at 0 % loss. A bordered panel is read once
    and then applies to every panel below it.

    entries: (marker_kind, colour, hollow, dash, label). Returns the panel
    height so the caller can place the axes underneath.
    """
    rows = (len(entries) + cols - 1) // cols
    col_w = col_w or (max(len(e[4]) for e in entries) * 6.55 + 48)
    bw, bh = col_w * cols + 14, rows * row_h + 13
    x0 = centre_x - bw / 2
    parts.append(f'<rect x="{x0:.1f}" y="{y_top:.1f}" width="{bw:.1f}" '
                 f'height="{bh:.1f}" rx="3" class="lbox"/>')
    for i, (kind, colour, hollow, dash, label) in enumerate(entries):
        r, c = divmod(i, cols)
        ex, ey = x0 + 11 + c * col_w, y_top + 13 + r * row_h
        d = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(f'<line x1="{ex:.1f}" y1="{ey:.1f}" x2="{ex + 24:.1f}" '
                     f'y2="{ey:.1f}" class="line" stroke="{colour}"{d}/>')
        parts.append(mark2(kind, ex + 12, ey, colour, hollow))
        parts.append(f'<text x="{ex + 32:.1f}" y="{ey + 4:.1f}" '
                     f'class="tick ink">{esc(label)}</text>')
    return bh


def wrap(text, width):
    """Greedy word wrap, so a long subtitle never runs off the canvas."""
    out, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if len(candidate) > width and line:
            out.append(line)
            line = word
        else:
            line = candidate
    if line:
        out.append(line)
    return out


def svg_open(w, h, title, subtitle, sub_width=None):
    sub_width = sub_width or int((w - 56) / 5.9)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">',
        f"<style>{SVG_STYLE}</style>",
        f'<rect width="{w}" height="{h}" class="surface"/>',
        f'<text x="28" y="30" class="title ink">{esc(title)}</text>',
    ]
    for i, line in enumerate(wrap(subtitle, sub_width)):
        parts.append(f'<text x="28" y="{50 + i * 15}" class="subtitle ink2">{esc(line)}</text>')
    return parts


def legend(parts, x, y, keys, dark=False):
    """Legend is always present for >= 2 series; identity is never colour alone,
    so each entry pairs its marker with a text label."""
    cx = x
    for key in keys:
        _, label, light, darkc, mk = SERIES_META[key]
        color = darkc if dark else light
        parts.append(marker(mk, cx + 6, y, color))
        parts.append(f'<text x="{cx + 18}" y="{y + 4}" class="tick ink">{esc(label)}</text>')
        cx += 22 + 7.0 * len(label)
    return parts


# =========================================================================
# Experiment A - stream-count sensitivity
# =========================================================================

# The 2x2: for each transport, which arm carries all N streams on one
# connection and which opens one connection per stream.
CONN_PAIRS = [("TCP / HTTP2", "grpc", "grpcm"), ("QUIC / HTTP3", "h3", "h3m")]


def stream_sweep_table(summary, metric="messages_per_s", regime="hol"):
    """What one connection achieves relative to N connections, against N.

    This is the number the "one connection, streams added as needed" argument
    rests on, and sweeping N is what turns the paper's choice of N=8 from an
    assertion into a measured trend: head-of-line blocking should cost more the
    more streams share a connection, so the TCP column should fall with N while
    the QUIC column stays flat.
    """
    # Filter to ONE regime before building the lookup. Without this the tput
    # and hol rows collide on (mode, loss, streams) - they differ only by
    # regime - and whichever sorts last silently wins, which put the
    # link-saturating numbers in the N=8 row of a table about the telemetry
    # regime.
    rows = [r for r in summary if not r.get("ramp") and r["regime"] == regime]
    by = {(r["mode"], r["loss_pct"], r.get("streams") or 8): r[metric] for r in rows}
    ns = sorted({r.get("streams") or 8 for r in rows})
    losses = sorted({r["loss_pct"] for r in rows})
    out = []
    for label, one, many in CONN_PAIRS:
        for n in ns:
            for loss in losses:
                a, b = by.get((one, loss, n)), by.get((many, loss, n))
                if not a or not b:
                    continue
                out.append({"transport": label, "streams": n, "loss_pct": loss,
                            "one_conn": a, "many_conn": b, "ratio_pct": 100.0 * a / b,
                            # Per-stream throughput on the single connection is
                            # the clearest view: head-of-line blocking shows up
                            # as this falling with N, with no ratio to unpack.
                            "one_conn_per_stream": a / n})
    return out


def write_stream_sweep_csv(summary, out_dir):
    rows = stream_sweep_table(summary)
    if not rows:
        return None
    path = os.path.join(out_dir, "stream_sweep.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["transport", "streams", "loss_pct",
                                          "one_conn", "many_conn", "ratio_pct",
                                          "one_conn_per_stream"])
        w.writeheader()
        for r in rows:
            w.writerow({**r, "one_conn": round(r["one_conn"], 2),
                        "many_conn": round(r["many_conn"], 2),
                        "ratio_pct": round(r["ratio_pct"], 1),
                        "one_conn_per_stream": round(r["one_conn_per_stream"], 1)})
    return path


# =========================================================================
# Experiment B - what it costs to bring a stream online
# =========================================================================

def pct(sorted_vals, q):
    """Linear-interpolated percentile over an already-sorted list."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (q / 100.0)
    lo = int(math.floor(k))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def ramp_table(runs):
    """Per-arm add cost, aggregated over the ramp runs.

    open_latency is the transport's own Open() call: near zero when the stream
    is a frame on a connection that already exists, a full handshake when it is
    not. add_latency additionally includes the first batch and its ack, i.e.
    how long until the new data type is actually flowing.
    """
    by = defaultdict(lambda: {"open": [], "add": [], "before": [], "after": []})
    for r in runs:
        ramp = r["doc"].get("ramp")
        if not ramp:
            continue
        key = (r["mode"], r["loss"])
        for a in ramp.get("streams", []):
            if a.get("index", 0) == 0:
                continue
            by[key]["open"].append(a.get("open_latency_ms", 0.0))
            if a.get("add_latency_ms", 0.0) > 0:
                by[key]["add"].append(a["add_latency_ms"])
            by[key]["before"].append(a.get("peer_acks_before", 0))
            by[key]["after"].append(a.get("peer_acks_after", 0))
    out = []
    for (mode, loss), v in sorted(by.items(),
                                  key=lambda kv: (kv[0][1],
                                                  SERIES_ORDER.index(kv[0][0])
                                                  if kv[0][0] in SERIES_ORDER else 99)):
        if not v["add"]:
            continue
        add = sorted(v["add"])
        opn = sorted(v["open"])
        before, after = sum(v["before"]), sum(v["after"])
        out.append({
            "mode": mode, "loss_pct": loss, "adds": len(add),
            "open_p50_ms": pct(opn, 50), "open_p99_ms": pct(opn, 99),
            "add_p50_ms": pct(add, 50), "add_p99_ms": pct(add, 99),
            "add_max_ms": add[-1],
            # Below 100 % means the streams already in service lost progress
            # while the new one was coming up.
            "peer_progress_pct": (100.0 * after / before) if before else 0.0,
        })
    return out


def write_ramp_csv(runs, out_dir):
    rows = ramp_table(runs)
    if not rows:
        return None
    path = os.path.join(out_dir, "ramp.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["mode", "loss_pct", "adds",
                                          "open_p50_ms", "open_p99_ms",
                                          "add_p50_ms", "add_p99_ms", "add_max_ms",
                                          "peer_progress_pct"])
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v)
                        for k, v in r.items()})
    return path


# =========================================================================
# Figures
# =========================================================================

# What one "add" actually is, per arm - the ramp figures' real variable. The
# steady-state labels ("1 conn x 8") name the shape the run ENDS in; these name
# the action being measured, which is what a growing run is about.
RAMP_LABEL = {
    "grpc":  "gRPC / HTTP2  -  add 1 stream",
    "h3":    "gRPC / HTTP3  -  add 1 stream",
    "rest":  "REST / HTTP1.1  -  add 1 connection",
    "grpcm": "gRPC / HTTP2  -  add 1 connection",
    "h3m":   "gRPC / HTTP3  -  add 1 connection",
}
# Adding a stream keeps a solid line and a filled marker; adding a connection
# gets a dashed line and a hollow one. The encoding is the same in all three
# ramp figures so they can be read side by side.
ADDS_STREAM = ("grpc", "h3")

# The three cuts of Experiment B. Each isolates one comparison, because five
# lines on two panels is more than a reader can hold at once.
RAMP_CUTS = [
    ("fig9a_add_stream_vs_connection.svg", ["rest", "grpc", "h3"],
     "Growing by a stream, against the REST baseline",
     "REST cannot multiplex, so it must open a connection for every new sensor. "
     "gRPC can add a stream to the connection it already has - over TCP and over QUIC."),
    ("fig9b_add_connection.svg", ["rest", "grpcm", "h3m"],
     "Growing by a connection - what the handshake costs",
     "Every arm here opens a whole new connection per sensor. The only difference "
     "is the handshake underneath: 3 RTT for TCP+TLS, 1 RTT for QUIC."),
    ("fig9c_quic_stream_vs_connection.svg", ["h3", "h3m"],
     "The design choice, inside QUIC",
     "Same transport, same code path, same 8 streams at the end. The only variable "
     "is whether a new sensor gets a stream on the existing connection or its own connection."),
]


def single_panel(path, title, subtitle, entries, series, xticks, xlabel,
                 ylabel, yticks, y0, y1, ref=None, band=None, legend_cols=1,
                 W=580, PH=258):
    """One plot, one file.

    Every figure in this set used to pair two panels under a shared legend.
    Splitting them means each file carries its own title, legend and axis, so a
    figure can be placed on its own in a single column without the reader
    having to look elsewhere for the key. The cost is that two panels that were
    meant to be compared (the TCP and QUIC halves of the retention figure) now
    live in separate files, so their y axes are pinned by the caller rather
    than fitted per panel - otherwise the comparison silently breaks.

    entries: (marker_kind, colour, hollow, dash, label) for the legend.
    series:  (marker_kind, colour, hollow, dash, [values], [tips]).
    ref:     (value, label) for a dashed reference line, or None.
    band:    (half_width, label) shading +/- around ref, or None.
    """
    PL, RM, B = 92, 30, 66
    pw = W - PL - RM

    p = [f"<style>{SVG_STYLE}</style>",
         f'<text class="title ink" x="26" y="26">{esc(title)}</text>']
    lines = wrap(subtitle, 74)
    for i, line in enumerate(lines):
        p.append(f'<text class="subtitle ink2" x="26" y="{44 + i * 15}">{esc(line)}</text>')
    ly = 44 + len(lines) * 15 + 4
    lbh = legend_box(p, entries, W / 2, ly, cols=legend_cols)

    T = ly + lbh + 26
    H = T + PH + B
    p.insert(0, f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
                f'width="{W}" height="{H}" role="img" aria-label="{esc(title)}">')
    p.insert(2, f'<rect class="surface" width="{W}" height="{H}"/>')

    def xpos(i):
        return PL + (pw * i / max(len(xticks) - 1, 1))

    def ypos(v):
        return T + PH - (max(min(v, y1), y0) - y0) / (y1 - y0) * PH

    for t in yticks:
        y = ypos(t)
        p.append(f'<line class="grid" x1="{PL}" y1="{y:.1f}" x2="{PL + pw}" y2="{y:.1f}"/>')
        p.append(f'<text class="tick ink2" x="{PL - 9}" y="{y + 4:.1f}" '
                 f'text-anchor="end">{esc(fmt_tick(t) if not ylabel.rstrip().endswith("(%)") else f"{t:.0f}%")}</text>')
    if band and ref:
        yb0, yb1 = ypos(ref[0] + band[0]), ypos(ref[0] - band[0])
        p.append(f'<rect x="{PL}" y="{yb0:.1f}" width="{pw:.1f}" '
                 f'height="{abs(yb1 - yb0):.1f}" fill="#8f8e88" opacity="0.12"/>')
        p.append(f'<text class="tick ink2" x="{PL + pw - 4}" y="{yb1 + 14:.1f}" '
                 f'text-anchor="end">{esc(band[1])}</text>')
    if ref:
        y = ypos(ref[0])
        p.append(f'<line x1="{PL}" y1="{y:.1f}" x2="{PL + pw}" y2="{y:.1f}" '
                 f'class="axis" stroke-dasharray="3 3"/>')
        p.append(f'<text class="tick ink2" x="{PL + pw - 3}" y="{y - 6:.1f}" '
                 f'text-anchor="end">{esc(ref[1])}</text>')
    for i, t in enumerate(xticks):
        p.append(f'<text class="tick ink2" x="{xpos(i):.1f}" y="{T + PH + 22}" '
                 f'text-anchor="middle">{esc(t)}</text>')
    p.append(f'<text class="axlabel ink2" x="{PL + pw / 2:.0f}" y="{T + PH + 46}" '
             f'text-anchor="middle">{esc(xlabel)}</text>')
    for k, line in enumerate(wrap(ylabel, 30)):
        lx = 26 + k * 15
        p.append(f'<text class="axlabel ink2" transform="rotate(-90 {lx} {T + PH / 2:.0f})" '
                 f'x="{lx}" y="{T + PH / 2:.0f}" text-anchor="middle">{esc(line)}</text>')

    for kind, colour, hollow, dash, vals, tips in series:
        pts = [(xpos(i), ypos(v)) for i, v in enumerate(vals)]
        d = " ".join(("M" if i == 0 else "L") + f"{x:.1f},{y:.1f}"
                     for i, (x, y) in enumerate(pts))
        da = f' stroke-dasharray="{dash}"' if dash else ""
        p.append(f'<path class="line" d="{d}" stroke="{colour}"{da}/>')
        for (x, y), tip in zip(pts, tips):
            p.append(mark2(kind, x, y, colour, hollow, tip=tip))
    p.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(p))
    return True


def ramp_figure(runs, path, modes=None, title=None, subtitle=None,
                losses=(0.0, 1.0, 2.0, 5.0), rows=None):
    """Experiment B: how long a new sensor waits before its first measurement
    lands, at each loss rate.

    99th percentile, not the median: the median is an RTT multiple and says
    nothing, while what an operator feels is the slow add.

    The companion measurement - what the add costs the sensors ALREADY
    reporting - is reported in Table III rather than as a second panel. It
    moves over a range of a few percent against a metric resolution of the same
    order, so a plot of it invites a reader to rank differences that the
    measurement cannot resolve; a table with the resolution stated next to it
    does not.
    """
    rows = rows if rows is not None else ramp_table(runs)
    rows = [r for r in rows if r["loss_pct"] in losses]
    if modes:
        rows = [r for r in rows if r["mode"] in modes]
    if not rows:
        return False
    order = modes or list(RAMP_LABEL)
    present = [m for m in order if any(r["mode"] == m for r in rows)]
    ls = sorted({r["loss_pct"] for r in rows})
    if len(present) < 2 or len(ls) < 2:
        return False
    by = {(r["mode"], r["loss_pct"]): r for r in rows}

    amax = max(r["add_p99_ms"] for r in rows)
    a0, a1, aticks = nice_ticks(0.0, amax * 1.08)

    entries, series = [], []
    for m in present:
        meta = SERIES_META[m]
        hollow = m not in ADDS_STREAM
        dash = "6 3" if hollow else None
        entries.append((meta[4], meta[2], hollow, dash, RAMP_LABEL[m]))
        vals = [by[(m, l)]["add_p99_ms"] for l in ls]
        series.append((meta[4], meta[2], hollow, dash, vals,
                       [f"{RAMP_LABEL[m]}, loss {fmt_num(l)} %: {v:,.0f} ms"
                        for l, v in zip(ls, vals)]))

    return single_panel(
        path,
        title or "What it costs to bring one more sensor online",
        subtitle or ("One stream is live at the start; the run grows it to 8, "
                     "one every 3 s. 99th percentile of 42 adds per point."),
        entries, series,
        [fmt_num(l) for l in ls], "Packet loss (%)",
        "Time to the new sensor's first measurement (ms)",
        aticks, a0, a1, legend_cols=1)


def stream_ratio_figure(summary, path, panel="rel", losses=(1.0, 5.0),
                        regime="hol"):
    """What a single connection sustains, against the number of streams on it.

    Two views of one result, now written to separate files so each can stand in
    a column on its own:

      panel="abs" - measurements per second that ONE stream achieves while N of
          them share a single connection. The unit the application cares about.
      panel="rel" - that same number divided by what the stream achieves with a
          connection to itself. 100 % means the single-connection design cost
          nothing; 60 % means four measurements in ten were never sent.

    "rel" is NOT a deadline success rate: both arms run closed-loop with one
    batch in flight per stream, so neither has a deadline to miss.

    Colour groups by loss rate, because the comparison being asked for is TCP
    against QUIC at a fixed loss. Transport is carried by line style and marker
    fill as well, so the figure survives greyscale printing.
    """
    rows = [r for r in summary if not r.get("ramp") and r["regime"] == regime]
    if not rows:
        return False
    by = {(r["mode"], r["loss_pct"], r.get("streams") or 8): r["messages_per_s"]
          for r in rows}
    ns = sorted({r.get("streams") or 8 for r in rows})
    keep = []
    for label, one, many in CONN_PAIRS:
        for l in losses:
            if all(by.get((one, l, n)) and by.get((many, l, n)) for n in ns):
                keep.append((label, one, many, l))
    if len(ns) < 2 or not keep:
        return False
    keep.sort(key=lambda e: (e[3], 0 if e[1] == "grpc" else 1))

    entries, series, vmax = [], [], 0.0
    for label, one, many, l in keep:
        colour, hollow = LOSS_COLOUR[l], (one == "grpc")
        dash = "6 3" if hollow else None
        name = ("TCP / HTTP2" if one == "grpc" else "QUIC / HTTP3")
        entries.append((SERIES_META[one][4], colour, hollow, dash,
                        f"{name}  -  loss {fmt_num(l)} %"))
        if panel == "abs":
            vals = [by[(one, l, n)] / n for n in ns]
            tips = [f"{name}, loss {fmt_num(l)} %, N={n}: {v:,.0f} /s"
                    for n, v in zip(ns, vals)]
        else:
            vals = [100.0 * by[(one, l, n)] / by[(many, l, n)] for n in ns]
            tips = [f"{name}, loss {fmt_num(l)} %, N={n}: {v:.0f} %"
                    for n, v in zip(ns, vals)]
        vmax = max(vmax, max(vals))
        series.append((SERIES_META[one][4], colour, hollow, dash, vals, tips))

    if panel == "abs":
        y0, y1, yticks = nice_ticks(0.0, vmax * 1.06)
        return single_panel(
            path, "What one stream delivers as streams are added",
            "N streams share ONE connection; the line is the rate one of those "
            "streams achieves. At 0 % loss every arm reaches about 486 "
            "measurements/s per stream at every N.",
            entries, series, [str(n) for n in ns],
            "Number of streams in a connection",
            "Measurements per second, per stream",
            yticks, y0, y1, ref=(486.0, "0 % loss"), legend_cols=2)

    return single_panel(
        path, "What a single connection keeps as streams are added",
        "All N streams on ONE connection, as a percentage of the same N streams "
        "on one connection each. At 0 % loss both transports hold 100 % at "
        "every N.",
        entries, series, [str(n) for n in ns],
        "Number of streams in a connection",
        "Measurement rate retained on one connection (%)",
        [0, 20, 40, 60, 80, 100], 0.0, 110.0, ref=(100.0, "no cost"),
        legend_cols=2)


# Ordinal ramp for the stream count: one hue, light -> dark, so "more streams"
# reads as "darker" without a legend lookup. Steps 250/350/450/600 of the blue
# scale - validated for monotone lightness, adjacent dL and surface contrast on
# both the light and the dark surface. Marker shape varies too, so the series
# survive greyscale printing.
N_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#184f95"]
N_MARKS = ["circle", "square", "triangle", "diamond"]


def retention_vs_loss_figure(summary, path, transport="tcp", regime="hol"):
    """The same retention ratio as stream_ratio_figure, against LOSS instead of
    stream count, one transport per file.

    This is the shape of the claim: as the link gets worse the TCP lines fan
    downward, and they fan further the more streams share the connection, while
    the QUIC lines stay flat. The two transports are separate files now, so
    their y axes are PINNED to 0-110 % rather than fitted - otherwise the QUIC
    file would auto-scale to its own narrow range and the flatness that is the
    whole point would look like a collapse.
    """
    label, one, many = {"tcp": ("TCP / HTTP2", "grpc", "grpcm"),
                        "quic": ("QUIC / HTTP3", "h3", "h3m")}[transport]
    rows = [r for r in summary if not r.get("ramp") and r["regime"] == regime]
    if not rows:
        return False
    by = {(r["mode"], r["loss_pct"], r.get("streams") or 8): r["messages_per_s"]
          for r in rows}
    ns = sorted({r.get("streams") or 8 for r in rows})
    # Only the loss rates measured at EVERY stream count, so no line carries
    # more resolution than its neighbours.
    sets = []
    for n in ns:
        sets.append({l for (m, l, nn) in by if nn == n
                     and all((mm, l, n) in by
                             for mm in ("grpc", "grpcm", "h3", "h3m"))})
    losses = sorted(set.intersection(*sets)) if sets else []
    if len(ns) < 2 or len(losses) < 3:
        return False

    entries, series = [], []
    for j, n in enumerate(ns):
        colour, kind = N_RAMP[j % len(N_RAMP)], N_MARKS[j % len(N_MARKS)]
        entries.append((kind, colour, False, None, f"{n} streams"))
        vals = [100.0 * by[(one, l, n)] / by[(many, l, n)] for l in losses]
        series.append((kind, colour, False, None, vals,
                       [f"{n} streams, loss {fmt_num(l)} %: {v:.0f} %"
                        for l, v in zip(losses, vals)]))

    return single_panel(
        path, f"One connection under loss - {label}",
        "All N streams on ONE connection, as a percentage of the same N streams "
        "on one connection each. A flat line at 100 % means the design costs "
        "nothing at that loss rate. The companion figure uses the same y axis.",
        entries, series, [fmt_num(l) for l in losses], "Packet loss (%)",
        "Measurement rate retained on one connection (%)",
        [0, 20, 40, 60, 80, 100], 0.0, 110.0, ref=(100.0, "no cost"),
        legend_cols=len(ns))


def stream_scale_figure(summary, path, losses=(0.0, 1.0, 5.0), regime="hol"):
    """Per-stream throughput on ONE connection, against the number of streams.

    The most direct view of head-of-line blocking there is. Every stream offers
    the same load, so if the connection carried them independently this would be
    a flat line at whatever one stream achieves. It is flat for QUIC and falls
    for TCP, and it falls further the more streams share the connection - which
    is what turns the paper's choice of N=8 from an assertion into a trend.
    """
    rows = [r for r in summary
            if not r.get("ramp") and r["regime"] == regime and r["mode"] in ("grpc", "h3")]
    if not rows:
        return False
    ns = sorted({r.get("streams") or 8 for r in rows})
    losses = [l for l in losses
              if all(any(r["mode"] == m and r["loss_pct"] == l and (r.get("streams") or 8) == n
                         for r in rows) for m in ("grpc", "h3") for n in ns)]
    if len(ns) < 2 or not losses:
        return False
    by = {(r["mode"], r["loss_pct"], r.get("streams") or 8):
          r["messages_per_s"] / (r.get("streams") or 8) for r in rows}

    W, H = 800, 430
    L, R, T, B = 80, 236, 82, 66
    pw, ph = W - L - R, H - T - B
    hi = max(by.values())
    y0, y1, yticks = nice_ticks(0, hi * 1.08)

    def xpos(i):
        return L + (pw * i / max(len(ns) - 1, 1))

    def ypos(v):
        return T + ph - (max(min(v, y1), y0) - y0) / (y1 - y0) * ph

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'width="{W}" height="{H}" role="img">', f"<style>{SVG_STYLE}</style>",
         f'<rect class="surface" width="{W}" height="{H}"/>',
         f'<text class="title ink" x="{L - 24}" y="30">'
         f'Head-of-line blocking grows with the number of streams</text>',
         f'<text class="subtitle ink2" x="{L - 24}" y="50">'
         f'Measurements per second per stream, all streams on ONE connection. '
         f'A connection that kept its</text>',
         f'<text class="subtitle ink2" x="{L - 24}" y="65">'
         f'streams independent would hold a flat line. Telemetry regime; median of repeats.</text>']
    for t in yticks:
        y = ypos(t)
        p.append(f'<line class="grid" x1="{L}" y1="{y:.1f}" x2="{L + pw}" y2="{y:.1f}"/>')
        p.append(f'<text class="tick ink2" x="{L - 10}" y="{y + 4:.1f}" text-anchor="end">'
                 f'{fmt_tick(t)}</text>')
    for i, n in enumerate(ns):
        p.append(f'<text class="tick ink2" x="{xpos(i):.1f}" y="{T + ph + 22}" '
                 f'text-anchor="middle">{n}</text>')
    p.append(f'<text class="axlabel ink2" x="{L + pw / 2:.0f}" y="{H - 14}" '
             f'text-anchor="middle">Logical streams on the connection (N)</text>')
    p.append(f'<text class="axlabel ink2" transform="rotate(-90 22 {T + ph / 2:.0f})" '
             f'x="22" y="{T + ph / 2:.0f}" text-anchor="middle">'
             f'Measurements/s per stream</text>')

    # Loss level selects the dash, transport selects the colour and marker, so
    # the two variables never have to be read out of one channel.
    dashes = {losses[0]: "none", }
    for k, l in enumerate(losses[1:], start=1):
        dashes[l] = ["none", "6 3", "2 3"][min(k, 2)]
    labels = []
    for mode in ("grpc", "h3"):
        meta = SERIES_META[mode]
        for l in losses:
            pts = [(xpos(i), ypos(by[(mode, l, n)])) for i, n in enumerate(ns)]
            d = " ".join(("M" if i == 0 else "L") + f"{x:.1f},{y:.1f}"
                         for i, (x, y) in enumerate(pts))
            dash = f' stroke-dasharray="{dashes[l]}"' if dashes[l] != "none" else ""
            p.append(f'<path class="line" d="{d}" stroke="{meta[2]}"{dash}/>')
            for x, y in pts:
                p.append(marker(meta[4], x, y, meta[2]))
            labels.append((pts[-1][1], f'{"HTTP/2" if mode == "grpc" else "HTTP/3"}'
                                       f' - 1 conn, loss {l:g}%', meta[2]))
    labels.sort(key=lambda e: e[0])
    used = []
    for y, text, colour in labels:
        while any(abs(y - u) < 16 for u in used):
            y += 16
        used.append(y)
        p.append(f'<circle cx="{L + pw + 18}" cy="{y - 4:.1f}" r="4" fill="{colour}"/>')
        p.append(f'<text class="slabel ink" x="{L + pw + 30}" y="{y:.1f}">{esc(text)}</text>')
    p.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(p))
    return True


def _is_base_run(r, nstreams=8):
    """True for a steady-state run at the default stream count."""
    row = run_row(r)
    return not row.get("ramp") and (row.get("streams") or nstreams) == nstreams


def base_cells(rows, nstreams=8):
    """The steady-state cells at the paper's default stream count.

    Every figure and table written before the stream sweep existed assumes one
    row per (regime, mode, loss). Filtering here keeps that true rather than
    letting N=2/4/16 and ramp rows silently double up on the same x position.
    """
    return [r for r in rows
            if not r.get("ramp") and (r.get("streams") or nstreams) == nstreams]


def line_figure(summary, runs, regime, metric, title, subtitle, ylabel, path,
                logy=False, only=None):
    """Median line per transport against loss, with a min-max whisker.

    `only` restricts the plotted arms. Six lines on one axis answer two
    questions at once and therefore neither: the reader cannot tell whether a
    gap comes from the transport or from the connection count. Passing the
    1-connection arms and the 8-connection arms to separate figures holds the
    connection count fixed inside each panel, so every gap that remains is the
    transport.
    """
    summary = base_cells(summary)
    # The whiskers come from the individual runs, so they need the same filter
    # the summary got; run_row() is the one accessor that knows both shapes.
    runs = [r for r in runs if _is_base_run(r)]
    losses = sorted({row["loss_pct"] for row in summary if row["regime"] == regime})
    if not losses:
        return False
    keep = set(only) if only else None
    modes = [m for m in SERIES_ORDER
             if (keep is None or m in keep)
             and any(r["regime"] == regime and r["mode"] == m for r in summary)]
    if not modes:
        return False

    plotted = set(modes)
    by = {(r["mode"], r["loss_pct"]): r[metric] for r in summary
          if r["regime"] == regime and r["mode"] in plotted}
    spread = defaultdict(list)
    for r in runs:
        if r["regime"] == regime and r["mode"] in plotted:
            spread[(r["mode"], r["loss"])].append(run_row(r)[metric])

    W, H = 800, 430
    L, R, T, B = 78, 236, 76, 64
    pw, ph = W - L - R, H - T - B

    vals = [v for v in by.values() if v is not None]
    lo_v, hi_v = min(vals + [0.0]), max(vals)
    for pts in spread.values():
        if pts:
            hi_v = max(hi_v, max(pts))
    y0, y1, yticks = nice_ticks(0 if not logy else max(lo_v * 0.8, 0.01), hi_v * 1.08 or 1.0)

    def xpos(i):
        return L + (pw * i / max(len(losses) - 1, 1))

    def ypos(v):
        v = max(min(v, y1), y0)
        return T + ph - (v - y0) / (y1 - y0) * ph

    p = svg_open(W, H, title, subtitle)
    for t in yticks:
        y = ypos(t)
        p.append(f'<line x1="{L}" y1="{y:.2f}" x2="{L + pw}" y2="{y:.2f}" class="grid"/>')
        p.append(f'<text x="{L - 10}" y="{y + 3.5:.2f}" text-anchor="end" class="tick ink2">{fmt_tick(t)}</text>')
    p.append(f'<line x1="{L}" y1="{T + ph}" x2="{L + pw}" y2="{T + ph}" class="axis"/>')
    for i, ls in enumerate(losses):
        x = xpos(i)
        p.append(f'<text x="{x:.2f}" y="{T + ph + 22}" text-anchor="middle" class="tick ink2">{fmt_num(ls)}</text>')
    p.append(f'<text x="{L + pw / 2:.2f}" y="{H - 20}" text-anchor="middle" class="axlabel ink2">'
             f'WAN packet loss (%)</text>')
    p.append(f'<text transform="translate(22,{T + ph / 2:.2f}) rotate(-90)" text-anchor="middle" '
             f'class="axlabel ink2">{esc(ylabel)}</text>')

    ends = []
    for mode in modes:
        _, label, color, _dark, mk = SERIES_META[mode]
        pts = []
        for i, ls in enumerate(losses):
            v = by.get((mode, ls))
            if v is None:
                continue
            pts.append((xpos(i), ypos(v), v, ls))
        if not pts:
            continue
        d = " ".join(("M" if k == 0 else "L") + f"{x:.2f} {y:.2f}" for k, (x, y, _, _) in enumerate(pts))
        p.append(f'<path d="{d}" class="line" stroke="{color}"/>')
        for x, y, v, ls in pts:
            raw = spread.get((mode, ls), [])
            if len(raw) > 1:
                p.append(f'<line x1="{x:.2f}" y1="{ypos(min(raw)):.2f}" x2="{x:.2f}" '
                         f'y2="{ypos(max(raw)):.2f}" class="err" stroke="{color}"/>')
            p.append(marker(mk, x, y, color,
                            tip=f"{label} @ {fmt_num(ls)}% loss: {fmt_tick(v)}"))
        lx, ly, lv, _ = pts[-1]
        ends.append({"x": lx, "y": ly, "anchor": ly, "v": lv,
                     "label": label, "color": color, "mk": mk})

    # Direct labels at the right edge. Lines converge under heavy loss, so the
    # labels are pushed apart to a minimum spacing and a leader line reconnects
    # any label that had to move. Direct labelling is also the relief the
    # palette's two low-contrast slots require.
    if ends:
        min_gap = 17.0
        ends.sort(key=lambda e: e["y"])
        for i in range(1, len(ends)):
            if ends[i]["y"] - ends[i - 1]["y"] < min_gap:
                ends[i]["y"] = ends[i - 1]["y"] + min_gap
        overflow = (ends[-1]["y"]) - (T + ph)
        if overflow > 0:
            for e in ends:
                e["y"] -= overflow
        for e in ends:
            tx = e["x"] + 18
            if abs(e["y"] - e["anchor"]) > 1.5:
                p.append(f'<path d="M{e["x"] + 6:.2f} {e["anchor"]:.2f} '
                         f'L{tx - 8:.2f} {e["y"]:.2f}" fill="none" '
                         f'stroke="{e["color"]}" stroke-width="1" opacity=".45"/>')
            # The swatch carries identity; the words stay in ink tokens.
            p.append(marker(e["mk"], tx, e["y"], e["color"], r=3.8))
            p.append(f'<text x="{tx + 11:.2f}" y="{e["y"] + 4:.2f}" class="slabel ink">'
                     f'{esc(e["label"])}<tspan class="tick ink2" dx="7">'
                     f'{fmt_tick(e["v"])}</tspan></text>')

    p.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(p))
    return True


# A cell with very few stall windows gives a noisy fraction; mark it rather
# than letting the reader take it at face value.
MIN_STALL_BUCKETS = 10


def stall_figure(summary, regime, path):
    """Grouped bars: what fraction of the streams a stall took down with it."""
    rows = [r for r in base_cells(summary) if r["regime"] == regime]
    if not rows:
        return False
    losses = sorted({r["loss_pct"] for r in rows})
    modes = [m for m in SERIES_ORDER if any(r["mode"] == m for r in rows)]
    by = {(r["mode"], r["loss_pct"]): r["blocked_fraction"] for r in rows}
    buckets = {(r["mode"], r["loss_pct"]): r["stall_buckets"] for r in rows}
    nstreams = max((r.get("streams") or 0) for r in rows) or 8
    floor = 1.0 / nstreams

    # Cap the axis just above the data instead of at 1.0: the interesting range
    # here is 0.1-0.5, and a full-height axis squashes it into the bottom third.
    peak = max([v for v in by.values() if v is not None] + [floor])
    W, H = 760, 440
    L, R, T, B = 78, 150, 104, 84
    pw, ph = W - L - R, H - T - B
    y0, y1, yticks = nice_ticks(0.0, peak * 1.15, target=5)

    p = svg_open(W, H, "A loss takes down more streams on TCP than on QUIC",
                 f"Mean fraction of the {nstreams} streams stalled together, over the 100 ms windows in which "
                 f"at least one stalled. Independent streams floor at 1/{nstreams} = {floor:.3f}; "
                 f"shared ordered delivery pushes it up. Hollow bars had fewer than "
                 f"{MIN_STALL_BUCKETS} stall windows and are not reliable.")
    for t in yticks:
        y = T + ph - (t - y0) / (y1 - y0) * ph
        p.append(f'<line x1="{L}" y1="{y:.2f}" x2="{L + pw}" y2="{y:.2f}" class="grid"/>')
        p.append(f'<text x="{L - 10}" y="{y + 3.5:.2f}" text-anchor="end" class="tick ink2">{t:.2f}</text>')
    p.append(f'<line x1="{L}" y1="{T + ph}" x2="{L + pw}" y2="{T + ph}" class="axis"/>')

    # The 1/N floor: where a perfectly isolated transport would sit.
    fy = T + ph - (floor - y0) / (y1 - y0) * ph
    p.append(f'<line x1="{L}" y1="{fy:.2f}" x2="{L + pw}" y2="{fy:.2f}" '
             f'class="axis" stroke-dasharray="5 4"/>')
    p.append(f'<text x="{L + pw + 10:.0f}" y="{fy + 4:.2f}" class="tick ink2">'
             f'1/{nstreams} = {floor:.3f}</text>')
    p.append(f'<text x="{L + pw + 10:.0f}" y="{fy + 18:.2f}" class="tick ink2">'
             f'perfect isolation</text>')

    group_w = pw / max(len(losses), 1)
    # A 2 px surface gap between adjacent bars keeps the group readable.
    bar_w = (group_w * 0.74) / max(len(modes), 1) - 2
    for gi, ls in enumerate(losses):
        gx = L + group_w * gi + group_w * 0.13
        for mi, mode in enumerate(modes):
            v = by.get((mode, ls))
            if v is None:
                continue
            _, label, color, _d, _mk = SERIES_META[mode]
            x = gx + mi * (bar_w + 2)
            h = (v - y0) / (y1 - y0) * ph
            y = T + ph - h
            nb = buckets.get((mode, ls), 0)
            weak = nb < MIN_STALL_BUCKETS
            fill = "none" if weak else color
            extra = f' stroke="{color}" stroke-width="1.5" stroke-dasharray="3 3"' if weak else ""
            p.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_w:.2f}" height="{max(h, 1):.2f}" '
                     f'rx="4" fill="{fill}"{extra}><title>{esc(label)} @ {fmt_num(ls)}% loss: '
                     f'{v:.2f} of {nstreams} streams, from {nb} stall windows</title></rect>')
        p.append(f'<text x="{L + group_w * gi + group_w / 2:.2f}" y="{T + ph + 22}" '
                 f'text-anchor="middle" class="tick ink2">{fmt_num(ls)}%</text>')
    p.append(f'<text x="{L + pw / 2:.2f}" y="{H - 18}" text-anchor="middle" class="axlabel ink2">'
             f'WAN packet loss</text>')
    p.append(f'<text transform="translate(22,{T + ph / 2:.2f}) rotate(-90)" text-anchor="middle" '
             f'class="axlabel ink2">Streams stalled together (fraction)</text>')
    legend(p, L, T - 16, modes)
    p.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(p))
    return True


def progress_figure(runs, regime, loss, modes, path, window=100, offset=50):
    """Small multiples: per-stream completions per 100 ms bucket.

    This is the figure that shows the mechanism rather than the summary. With a
    shared ordered byte stream the pale gaps line up vertically across every
    stream; with independent streams they scatter.

    Only a `window` of buckets is drawn (10 s by default, starting `offset`
    buckets in). Thirty seconds of 100 ms buckets squeezed into a printable
    width gives 2 px per cell, which reads as noise; a 10 s excerpt keeps each
    cell wide enough to see. The summary metrics in fig3 cover the full run.
    """
    picked = []
    for mode in modes:
        cands = [r for r in runs if r["regime"] == regime and r["mode"] == mode
                 and abs(r["loss"] - loss) < 1e-9]
        if cands:
            picked.append((mode, cands[0]))
    if not picked:
        return False

    rowh, gap = 13, 3
    panel_pad = 46
    first_tl = picked[0][1]["doc"]["hol"]["timeline"]
    total_buckets = max((len(v) for v in first_tl.values()), default=0)
    if total_buckets == 0:
        return False
    start = min(offset, max(total_buckets - window, 0))
    nbuckets = min(window, total_buckets - start)

    W = 860
    L, R, T = 132, 28, 92
    pw = W - L - R
    def panel_h(tl):
        return len(tl) * (rowh + gap) + panel_pad - 10

    H = int(T + sum(panel_h(r["doc"]["hol"]["timeline"]) for _m, r in picked) + 30)

    p = svg_open(W, H, f"Per-stream progress at {fmt_num(loss)}% loss",
                 f"Each row is one sensor stream; each cell is a 100 ms window shaded by batches completed. "
                 f"Pale cells are stalls, and gaps that line up vertically mean a single loss event blocked "
                 f"every stream at once. Excerpt: {start / 10:.0f}-{(start + nbuckets) / 10:.0f} s of the run.")
    y = T
    for mode, run in picked:
        _, label, color, _d, _mk = SERIES_META[mode]
        tl = run["doc"]["hol"]["timeline"]
        peak = max((max(v) if v else 0) for v in tl.values()) or 1
        corr = run["doc"]["hol"]["stall_correlation"]
        p.append(f'<rect x="28" y="{y - 18}" width="11" height="11" rx="2" fill="{color}"/>')
        p.append(f'<text x="45" y="{y - 8}" class="slabel ink">{esc(label)}</text>')
        p.append(f'<text x="{L + pw:.0f}" y="{y - 8}" text-anchor="end" class="tick ink2">'
                 f'stall correlation {corr:.2f}</text>')
        cw = pw / nbuckets
        for name in sorted(tl):
            counts = tl[name]
            p.append(f'<text x="{L - 10}" y="{y + rowh - 3:.2f}" text-anchor="end" '
                     f'class="tick ink2">{esc(name)}</text>')
            for i, c in enumerate(counts[start:start + nbuckets]):
                # One hue, light to dark: a sequential encoding of magnitude.
                op = 0.12 + 0.83 * (c / peak) if c else 0.05
                p.append(f'<rect x="{L + i * cw:.2f}" y="{y:.2f}" width="{max(cw - 0.8, 0.8):.2f}" '
                         f'height="{rowh}" fill="{color}" opacity="{op:.3f}">'
                         f'<title>{esc(name)} @ {(start + i) / 10:.1f}s: {c} batches</title></rect>')
            y += rowh + gap
        y += panel_pad - 10
    p.append(f'<text x="{L}" y="{H - 10}" class="axlabel ink2">{start / 10:.0f} s</text>')
    p.append(f'<text x="{L + pw:.0f}" y="{H - 10}" text-anchor="end" class="axlabel ink2">'
             f'{(start + nbuckets) / 10:.0f} s</text>')
    p.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(p))
    return True


# =========================================================================
# Narrative report
# =========================================================================

def write_report(summary, runs, out_dir, manifest):
    lines = ["# test - REST vs gRPC vs gRPC over QUIC", ""]
    if manifest:
        n = manifest.get("network", {})
        wan = n.get("derived_wan", {})
        lines += [
            "## Emulated path", "",
            f"- Base RTT: {n.get('base_rtt_ms', '?')} ms "
            f"(access {n.get('access_delay_ms', '?')} ms + WAN {n.get('wan_delay_ms', '?')} ms one-way, "
            f"netem on both directions of every link)",
            f"- WAN bottleneck: {n.get('wan_bw_mbps', '?')} Mbit/s per direction, "
            f"buffer {wan.get('buffer_bytes', 0):.0f} B "
            f"(~{wan.get('buffer_pkts', 0):.0f} pkt = link BDP {wan.get('bdp_pkts', '?')} pkt "
            f"+ {wan.get('queuing_delay_ms', '?')} ms of queueing)",
            "- Topology: c0 (Edge GW) - r0 - r1 - s0 (Cloud GW); loss injected on the r0-r1 link, "
            "in both directions",
            "",
        ]
    lines += [f"- Runs analysed: {len(runs)}", ""]

    for regime, heading in (("tput", "Throughput regime (600 measurements per batch, 1 batch in flight per stream)"),
                            ("hol", "Head-of-line regime (20 measurements per batch, 1 batch in flight per stream)")):
        rows = [r for r in base_cells(summary) if r["regime"] == regime]
        if not rows:
            continue
        # Latency columns are printed as whole milliseconds on purpose. With
        # three repeats the p99 estimator moves by tens of percent between runs
        # (worst observed: 258 % of the mean), so a value like "378.08" claims a
        # precision the sample size does not support. Integers keep the column
        # honest: the QUIC/TCP separation the paper argues from is 110 vs 370 ms,
        # which survives rounding with room to spare.
        lines += [f"## {heading}", "",
                  "| loss % | transport | goodput Mbps | msg/s | bytes/msg | RTT p50 ms | RTT p99 ms | blocked frac. | stall wins |",
                  "|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
        for r in rows:
            label = SERIES_META.get(r["mode"], (None, r["mode"]))[1]
            lines.append(
                f"| {r['loss_pct']:g} | {label} | {r['goodput_mbps']:.2f} | "
                f"{r['messages_per_s']:.0f} | {r['bytes_per_msg']:.1f} | "
                f"{r['rtt_p50_ms']:.0f} | {r['rtt_p99_ms']:.0f} | "
                f"{r['blocked_fraction']:.2f} | {r['stall_buckets']:.0f} |")
        lines.append("")

        base = {r["mode"]: r for r in rows if r["loss_pct"] == 0}
        worst = max((r["loss_pct"] for r in rows), default=0)
        hi = {r["mode"]: r for r in rows if r["loss_pct"] == worst}
        if base and hi:
            lines += [f"### Degradation from 0% to {worst:g}% loss", ""]
            for m in SERIES_ORDER:
                if m in base and m in hi and base[m]["goodput_mbps"]:
                    keep = hi[m]["goodput_mbps"] / base[m]["goodput_mbps"] * 100
                    infl = (hi[m]["rtt_p99_ms"] / base[m]["rtt_p99_ms"]
                            if base[m]["rtt_p99_ms"] else float("nan"))
                    lines.append(f"- {SERIES_META[m][1]}: retains {keep:.0f}% of goodput "
                                 f"({base[m]['goodput_mbps']:.1f} -> {hi[m]['goodput_mbps']:.1f} Mbps, "
                                 f"{base[m]['messages_per_s']:.0f} -> {hi[m]['messages_per_s']:.0f} msg/s), "
                                 f"p99 latency x{infl:.0f} "
                                 f"({base[m]['rtt_p99_ms']:.0f} -> {hi[m]['rtt_p99_ms']:.0f} ms)")
            lines.append("")

    sweep = stream_sweep_table(summary)
    if sweep:
        lines += ["## Experiment A - one connection vs N connections, against N", "",
                  "Telemetry regime only. \"one conn\" carries all N streams on a single "
                  "connection; \"N conn\" opens one connection per stream. The ratio is "
                  "what choosing the single-connection design costs.", "",
                  "| transport | streams | loss % | one conn (msg/s) | per stream | N conn (msg/s) | one conn keeps |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for r in sweep:
            lines.append(f"| {r['transport']} | {r['streams']} | {r['loss_pct']:g} | "
                         f"{r['one_conn']:.0f} | {r['one_conn_per_stream']:.0f} | "
                         f"{r['many_conn']:.0f} | {r['ratio_pct']:.0f}% |")
        lines.append("")

    ramp = ramp_table(runs)
    if ramp:
        lines += ["## Experiment B - what it costs to bring a stream online", "",
                  "open = the transport's Open() call (a handshake when there is one). "
                  "add = until the new stream's first batch is acknowledged. "
                  "peer progress = acks completed by the already-running streams after "
                  "the add, as a percentage of the window before it.", "",
                  "| loss % | transport | adds | open p50 | open p99 | add p50 | add p99 | add max | peer progress |",
                  "|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
        for r in ramp:
            label = SERIES_META.get(r["mode"], (None, r["mode"]))[1]
            lines.append(f"| {r['loss_pct']:g} | {label} | {r['adds']} | "
                         f"{r['open_p50_ms']:.0f} | {r['open_p99_ms']:.0f} | "
                         f"{r['add_p50_ms']:.0f} | {r['add_p99_ms']:.0f} | "
                         f"{r['add_max_ms']:.0f} | {r['peer_progress_pct']:.0f}% |")
        lines += ["",
                  "> A ramp run's throughput totals are NOT comparable with a "
                  "steady-state run: its later streams were live for less of the "
                  "window. Use these runs for the add columns only.", ""]

    path = os.path.join(out_dir, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


# =========================================================================

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=None,
                    help="directory containing the run outputs "
                         "(default: <gaia>/output/test, read from the manifest)")
    ap.add_argument("--manifest", default=os.path.join(here, "manifest.json"),
                    help="manifest.json from gen_scenarios.py")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(here), "report"),
                    help="directory for CSVs and figures")
    ap.add_argument("--progress-loss", type=float, default=1.0,
                    help="loss percentage to draw the per-stream progress figure for")
    ap.add_argument("--progress-window", type=int, default=100,
                    help="number of 100 ms buckets to show in the progress figure")
    ap.add_argument("--progress-offset", type=int, default=50,
                    help="first bucket of the progress excerpt")
    ap.add_argument("--version", action="version", version=f"analyze.py {VERSION}")
    args = ap.parse_args()

    manifest = None
    if args.manifest and os.path.exists(args.manifest):
        with open(args.manifest, encoding="utf-8") as f:
            manifest = json.load(f)

    # The sweep writes into <gaia>/output/test, and the manifest already knows
    # where <gaia> is, so --results rarely needs to be typed.
    results = args.results
    if not results:
        if not manifest:
            print(f"error: no manifest at {args.manifest} and no --results given.\n"
                  f"  pass --results <dir>, or run gen_scenarios.py first so the "
                  f"manifest exists.", file=sys.stderr)
            return 2
        results = os.path.join(manifest["gaia"], "output", "test")
        print(f"results    : {results}  (from manifest)")
    results = os.path.abspath(os.path.expanduser(results))

    if not os.path.isdir(results):
        print(f"error: {results} does not exist. Has the sweep run yet?", file=sys.stderr)
        return 2

    runs = load_runs(results)
    if not runs:
        print(f"error: no usable rpcbench.json found under {results}", file=sys.stderr)
        return 1
    os.makedirs(args.out, exist_ok=True)

    summary = write_csvs(runs, args.out)
    made = []
    if write_stream_sweep_csv(summary, args.out):
        made.append("stream_sweep.csv")
    if write_ramp_csv(runs, args.out):
        made.append("ramp.csv")

    if line_figure(summary, runs, "tput", "goodput_mbps",
                   "Goodput collapses with loss - unevenly",
                   "All six arms carry 8 logical streams; \"C conn x S\" is C connections of S streams each. "
                   "600 measurements per batch, one batch in flight per stream. Median of repeats, whiskers min-max.",
                   "Application goodput (Mbit/s)",
                   os.path.join(args.out, "fig1_goodput_vs_loss.svg")):
        made.append("fig1_goodput_vs_loss.svg")

    if line_figure(summary, runs, "hol", "rtt_p99_ms",
                   "Tail latency under loss",
                   "99th percentile batch round trip. All six arms carry 8 logical streams; \"C conn x S\" is "
                   "C connections of S streams each. 20 measurements per batch. Median of repeats, whiskers min-max.",
                   "Batch RTT, 99th percentile (ms)",
                   os.path.join(args.out, "fig2_p99_vs_loss.svg")):
        made.append("fig2_p99_vs_loss.svg")

    if stall_figure(summary, "hol", os.path.join(args.out, "fig3_stall_correlation.svg")):
        made.append("fig3_stall_correlation.svg")

    if stream_scale_figure(summary, os.path.join(args.out, "fig7_stream_scaling.svg")):
        made.append("fig7_stream_scaling.svg")

    # Experiment A, one plot per file: the absolute rate and the ratio against
    # stream count, then the ratio against loss with one file per transport.
    for name, panel in (("fig8a_rate_vs_streams.svg", "abs"),
                        ("fig8b_retained_vs_streams.svg", "rel")):
        if stream_ratio_figure(summary, os.path.join(args.out, name), panel=panel):
            made.append(name)

    for name, tr in (("fig10a_retained_vs_loss_tcp.svg", "tcp"),
                     ("fig10b_retained_vs_loss_quic.svg", "quic")):
        if retention_vs_loss_figure(summary, os.path.join(args.out, name),
                                    transport=tr):
            made.append(name)

    # Experiment B, three cuts. Each answers one question; the paper picks the
    # cut that matches the claim it is making rather than showing all five arms.
    rt = ramp_table(runs)
    if rt:
        for name, modes, head, sub in RAMP_CUTS:
            if ramp_figure(runs, os.path.join(args.out, name), modes=modes,
                           title=head, subtitle=sub, rows=rt):
                made.append(name)

    # Connection count held fixed inside each panel. ONE_CONN isolates the
    # transport; EIGHT_CONN shows what is left once TCP has bought the same
    # delivery independence QUIC gets for free inside one connection.
    ONE_CONN = ["grpc", "h3", "qtun"]
    EIGHT_CONN = ["rest", "grpcm", "h3m"]

    for tag, only, label in (("1conn", ONE_CONN, "one connection"),
                             ("8conn", EIGHT_CONN, "eight connections")):
        name = f"fig5_goodput_{tag}.svg"
        if line_figure(summary, runs, "tput", "goodput_mbps",
                       f"Goodput under loss - {label}",
                       f"All arms use {label}, so every gap is the transport, not the connection count. "
                       "\"C conn x S\" is C connections of S logical streams each; C x S = 8 everywhere. "
                       "Median of repeats, whiskers min-max.",
                       "Application goodput (Mbit/s)",
                       os.path.join(args.out, name), only=only):
            made.append(name)

        name = f"fig6_p99_{tag}.svg"
        if line_figure(summary, runs, "hol", "rtt_p99_ms",
                       f"Tail latency under loss - {label}",
                       f"All arms use {label}. 99th percentile batch round trip: 99 of every 100 batches "
                       "completed faster than this. Lower is better. Median of repeats, whiskers min-max.",
                       "Batch RTT, 99th percentile (ms)",
                       os.path.join(args.out, name), only=only):
            made.append(name)

    tag = str(args.progress_loss).replace(".", "p")
    if progress_figure(runs, "hol", args.progress_loss, SERIES_ORDER,
                       os.path.join(args.out, f"fig4_progress_l{tag}.svg"),
                       window=args.progress_window, offset=args.progress_offset):
        made.append(f"fig4_progress_l{tag}.svg")

    report = write_report(summary, runs, args.out, manifest)

    print(f"runs analysed : {len(runs)}")
    print(f"summary cells : {len(summary)}")
    print(f"tables        : runs.csv, summary.csv, streams.csv")
    print(f"figures       : {', '.join(made) if made else '(none - not enough data)'}")
    print(f"report        : {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

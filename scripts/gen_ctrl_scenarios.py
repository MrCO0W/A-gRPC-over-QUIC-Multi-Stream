#!/usr/bin/env python3
"""Generate the GAIA scenarios for Experiment C: downlink command delivery.

Question: once an agent upstream (Edge Master) has decided on a command, how
long until the Edge Agent holds it, and what does the delivery mechanism cost?

The network is EXACTLY the one of the main sweep: this script imports the link
constants and the topology writer from gen_scenarios.py, so a change there
applies here too and the topology files (topo_test_l<loss>) are shared.

Matrix (defaults):

  delivery  poll   REST short polling, period 1 s and 0.1 s
            lpoll  REST long polling (request held up to 25 s)
            sse    REST + Server-Sent Events (one long-lived response)
            grpc   gRPC bidirectional stream (Control.Session)
  proto     h1 = HTTP/1.1/TLS/TCP, h2 = HTTP/2/TLS/TCP, h3 = HTTP/3/QUIC
            (grpc runs on h2 and h3 only)
  regime    idle   control plane only: latency and the cost of the mechanism
                   in requests and bytes per command
            busy   4 uplink telemetry streams (batch 20, one in flight) running
                   alongside; on h2/h3 they share the control connection, so a
                   lost telemetry packet can hold a command back (h2) or not (h3)
  loss      0, 1, 2, 5 % per direction on the WAN link

Commands are issued upstream on a Poisson schedule, 5 per second, 64 B of
padding each. Every run: 5 s warm-up, 30 s measured, 3 s grace.

Usage:
    python3 gen_ctrl_scenarios.py --explain
    python3 gen_ctrl_scenarios.py                    # paths auto-detected
    python3 gen_ctrl_scenarios.py --regimes idle --losses 0,1,5 --repeats 3

Writes:
    <gaia>/config/topo/topo_test_l<loss>            (same files as the main sweep)
    <gaia>/config/xp/xp_test_ctrl_<regime>_<app>_<proto>[_i<ms>]_l<loss>
    <out>/manifest_ctrl.json                          (read by run_ctrl_sweep.sh, analyze_ctrl.py)
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_scenarios as base  # noqa: E402  (network constants, topology writer, path discovery)

VERSION = "2026-11-09.1"

CTRL_LOSSES = [0.0, 1.0, 2.0, 5.0]

# (app, proto, poll interval in seconds or None)
CTRL_MODES = [
    ("poll", "h1", 1.0), ("poll", "h2", 1.0), ("poll", "h3", 1.0),
    ("poll", "h1", 0.1), ("poll", "h2", 0.1), ("poll", "h3", 0.1),
    ("lpoll", "h1", None), ("lpoll", "h2", None), ("lpoll", "h3", None),
    ("sse", "h1", None), ("sse", "h2", None), ("sse", "h3", None),
    ("grpc", "h2", None), ("grpc", "h3", None),
]

CTRL_REGIMES = {
    "idle": dict(up_streams=0, up_batch=20),
    "busy": dict(up_streams=4, up_batch=20),
}

CMD_RATE = 5.0       # commands per second, Poisson
CMD_BYTES = 64       # padding per command
DURATION_S = 30
WARMUP_S = 5
GRACE_S = 3
CTRL_PORT = 8450


def mode_tag(app, proto, interval):
    tag = f"{app}_{proto}"
    if app == "poll":
        tag += f"_i{int(round(interval * 1000))}"
    return tag


def xp_name(regime, app, proto, interval, loss):
    return f"xp_test_ctrl_{regime}_{mode_tag(app, proto, interval)}_l{base.loss_tag(loss)}"


def write_xp(gaia, rpcbench, regime, app, proto, interval, loss, topo_path, pcap):
    r = CTRL_REGIMES[regime]
    binaries = os.path.join(rpcbench, "bin")
    certs = os.path.join(rpcbench, "certs")
    rtt_ms = 2 * (2 * base.ACCESS_DELAY_MS + base.WAN_DELAY_MS)
    label = f"ctrl-{regime}-{mode_tag(app, proto, interval)}-loss{base.loss_tag(loss)}"

    server_cmd = f"{binaries}/ctrlgw -certs {certs} -bind 0.0.0.0 -port {CTRL_PORT}"
    client_cmd = (
        f"{binaries}/ctrledge"
        f" -server {{server_ip:s0}}"
        f" -server-name 10.0.2.2"
        f" -port {CTRL_PORT}"
        f" -app {app}"
        f" -proto {proto}"
        + (f" -poll-interval {int(round(interval * 1000))}ms" if app == "poll" else "")
        + f" -cmd-rate {CMD_RATE}"
        f" -cmd-bytes {CMD_BYTES}"
        f" -up-streams {r['up_streams']}"
        f" -up-batch {r['up_batch']}"
        f" -duration {DURATION_S}s"
        f" -warmup {WARMUP_S}s"
        f" -grace {GRACE_S}s"
        f" -ca {certs}/ca.pem"
        f" -label {label}"
        f" -out {{output_dir}}/ctrlbench.json"
        f" -param loss_pct={loss}"
        f" -param regime={regime}"
        f" -param wan_bw_mbps={base.WAN_BW_MBPS}"
        f" -param base_rtt_ms={rtt_ms}"
    )
    # warm-up + window + grace + up to ~20 s for the ledger fetch under loss.
    xp_duration = WARMUP_S + DURATION_S + GRACE_S + 20
    body = (
        "xpType:workload_exp\n"
        f"topo_file:{topo_path}\n"
        f"duration:{xp_duration}\n"
        "mptcp:no\n"
        f"pcap:{pcap}\n"
        "congctrl:cubic\n"
        f"workload:s0,custom_server,{server_cmd}\n"
        f"workload:c0,custom_client,{client_cmd}\n"
    )
    path = os.path.join(gaia, "config", "xp", xp_name(regime, app, proto, interval, loss))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def explain(losses, modes, regimes, repeats):
    base.explain(losses)
    print()
    print("Experiment C - downlink command delivery")
    print("=" * 78)
    rtt_ms = 2 * (2 * base.ACCESS_DELAY_MS + base.WAN_DELAY_MS)
    print(f"commands     : Poisson, {CMD_RATE}/s, {CMD_BYTES} B padding; "
          f"{WARMUP_S} s warm-up + {DURATION_S} s window + {GRACE_S} s grace")
    print(f"             -> about {int(CMD_RATE * DURATION_S)} commands per run")
    print("expected one-way latency without loss (base RTT %.0f ms):" % rtt_ms)
    for app, proto, iv in modes:
        if app == "poll":
            exp = f"~{iv * 500 + rtt_ms / 2:.0f} ms median (half the period + one way)"
        elif app == "lpoll":
            exp = f"~{rtt_ms / 2:.0f} ms, +1 RTT when a command lands between two polls"
        else:
            exp = f"~{rtt_ms / 2:.0f} ms (pushed on an open stream)"
        print(f"  {mode_tag(app, proto, iv):<14} {exp}")
    for name in regimes:
        r = CTRL_REGIMES[name]
        print(f"regime {name:<5}: {r['up_streams']} uplink telemetry streams, batch {r['up_batch']}")
    cells = len(modes) * len(losses) * len(regimes)
    per_run = WARMUP_S + DURATION_S + GRACE_S + 12
    print(f"cells        : {len(modes)} modes x {len(losses)} losses x {len(regimes)} regimes = {cells}")
    print(f"runs         : {cells} x {repeats} repeats = {cells * repeats}"
          f"  (~{cells * repeats * per_run / 3600:.1f} h at ~{per_run} s per run)")


def parse_modes(spec):
    """'all' or a comma list like poll_h1_i1000,sse_h2,grpc_h3."""
    if spec == "all":
        return list(CTRL_MODES)
    want = {s.strip() for s in spec.split(",") if s.strip()}
    picked = [m for m in CTRL_MODES if mode_tag(*m) in want]
    unknown = want - {mode_tag(*m) for m in picked}
    if unknown:
        raise SystemExit(f"error: unknown mode(s) {sorted(unknown)}; "
                         f"known: {', '.join(mode_tag(*m) for m in CTRL_MODES)}")
    return picked


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gaia", default=None)
    ap.add_argument("--rpcbench", default=None)
    ap.add_argument("--out", default=None, help="where to write manifest_ctrl.json (default: here)")
    ap.add_argument("--modes", default="all",
                    help="'all' or a comma list of mode tags, e.g. poll_h1_i1000,sse_h2,grpc_h3")
    ap.add_argument("--regimes", default=",".join(CTRL_REGIMES))
    ap.add_argument("--losses", default=",".join(str(x) for x in CTRL_LOSSES))
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--pcap", default="no", choices=["yes", "no"])
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--version", action="version", version=f"gen_ctrl_scenarios.py {VERSION}")
    args = ap.parse_args()

    modes = parse_modes(args.modes)
    regimes = [r.strip() for r in args.regimes.split(",") if r.strip()]
    for r in regimes:
        if r not in CTRL_REGIMES:
            raise SystemExit(f"error: unknown regime {r!r}")
    losses = [float(x) for x in args.losses.split(",") if x.strip()]

    if args.explain:
        explain(losses, modes, regimes, args.repeats)
        return 0

    gaia, err = base.resolve("gaia", args.gaia, "a gaia-experiment checkout")
    if err:
        print(err, file=sys.stderr)
        return 2
    rpcbench, err = base.resolve("rpcbench", args.rpcbench, "the rpcbench directory")
    if err:
        print(err, file=sys.stderr)
        return 2
    for sub, how in (("bin/ctrlgw", "./build.sh build"), ("bin/ctrledge", "./build.sh build"),
                     ("certs/ca.pem", "./build.sh certs")):
        if not os.path.exists(os.path.join(rpcbench, sub)):
            print(f"warning: {os.path.join(rpcbench, sub)} is missing - run `{how}` in {rpcbench}",
                  file=sys.stderr)

    out_dir = os.path.abspath(args.out) if args.out else HERE
    topos, runs = {}, []
    for loss in losses:
        topos[base.loss_tag(loss)] = base.write_topo(gaia, loss)
    for regime in regimes:
        for loss in losses:
            for app, proto, iv in modes:
                topo = topos[base.loss_tag(loss)]
                xp = write_xp(gaia, rpcbench, regime, app, proto, iv, loss, topo, args.pcap)
                runs.append({
                    "experiment": "ctrl",
                    "regime": regime, "app": app, "proto": proto,
                    "poll_interval_s": iv if app == "poll" else 0,
                    "loss_pct": loss, "topo": topo, "xp": xp,
                    "name": xp_name(regime, app, proto, iv, loss),
                })

    manifest = {
        "schema": "test.ctrlbench.manifest/1",
        "gaia": gaia, "rpcbench": rpcbench, "repeats": args.repeats, "pcap": args.pcap,
        "network": {
            "access_delay_ms": base.ACCESS_DELAY_MS, "wan_delay_ms": base.WAN_DELAY_MS,
            "base_rtt_ms": 2 * (2 * base.ACCESS_DELAY_MS + base.WAN_DELAY_MS),
            "wan_bw_mbps": base.WAN_BW_MBPS, "wan_queue_size": base.WAN_QUEUE,
        },
        "commands": {"rate_per_s": CMD_RATE, "bytes": CMD_BYTES,
                     "duration_s": DURATION_S, "warmup_s": WARMUP_S, "grace_s": GRACE_S},
        "regimes": {k: CTRL_REGIMES[k] for k in regimes},
        "topologies": topos, "runs": runs,
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "manifest_ctrl.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"scenarios  : {len(runs)}")
    print(f"repeats    : {args.repeats}  -> {len(runs) * args.repeats} runs total")
    print(f"manifest   : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

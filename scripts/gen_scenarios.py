#!/usr/bin/env python3
"""Generate the GAIA topology and scenario files for the test sweep.

The experiment matrix is (loss rate) x (transport) x (traffic regime):

  loss      0, 0.05, 0.1, 0.2, 0.5, 1, 2, 5 %  applied to the emulated WAN link
                               between the two routers, i.e. the Edge<->Cloud path
  transport rest, grpc, grpcm, h3, h3m, qtun
  regime    hol   - batch 20,  one batch in flight: isolates stall behaviour
            tput  - batch 600, one batch in flight: saturates the link

Writing forty-odd files by hand invites silent drift between arms, so they are
generated from one place. Every arm of a given regime differs ONLY in the
`-mode` flag handed to edgegw; everything else is identical by construction.

Usage:
    python3 gen_scenarios.py --gaia /path/to/gaia-experiment-main \\
                             --rpcbench /path/to/rpcbench

Writes:
    <gaia>/config/topo/topo_test_l<loss>
    <gaia>/config/xp/xp_test_<regime>_<mode>_l<loss>
    <out>/manifest.json    (consumed by run_sweep.sh and analyze.py)
"""

import argparse
import json
import math
import os
import sys

# Bump when the CLI or the network constants change, so a stale copy on the
# experiment host can be spotted with --version instead of by its symptoms.
VERSION = "2026-09-18.1"

# --- Network model -------------------------------------------------------
# One-way delay is 1 + 18 + 1 = 20 ms, so the base RTT is ~40 ms: a realistic
# regional edge-to-cloud path. The bottleneck is the 50 Mbit/s WAN link.
ACCESS_DELAY_MS = 1.0
WAN_DELAY_MS = 18.0
ACCESS_BW_MBPS = 1000.0
WAN_BW_MBPS = 50.0
ACCESS_QUEUE = 1000
# CAREFUL: queue_size is NOT a packet count. GAIA turns it into a queueing delay
# (core/topo.py LinkCharacteristics.extract_queuing_delay) and the actual tbf
# limit becomes one link BDP plus that much buffering. At 18 ms / 50 Mbit/s,
# queue_size=100 yields ~22 ms of extra queueing and a 362 kB (~242 packet)
# buffer, i.e. about 1.6x the link BDP: deep enough that tail drops never
# confound the loss we inject on purpose, shallow enough to avoid gross
# bufferbloat in the pipelined regime. Run `gen_scenarios.py --explain` after
# changing any link parameter to see what it actually produces.
WAN_QUEUE = 100

# 0.2 % was added after the first sweep: a single TCP flow had already lost 92 %
# of its goodput by 0.5 %, so the knee of the curve fell entirely between 0 and
# the first non-zero point and the figure showed a cliff rather than a shape.
#
# 0.05 % and 0.1 % were added after the third sweep for the same reason one
# level down. Between 0 and 0.2 % the single-connection HTTP/2 arm still falls
# 46.5 -> 7.3 Mbit/s, a 6.4x drop across one unsampled interval, and the
# crossover where the eight-connection HTTP/3 arm overtakes the eight-connection
# HTTP/2 arm also lies somewhere inside it. Two extra points turn both of those
# from a vertical edge into a measured curve.
#
# The low end is where the emulator's own resolution matters: at 0.05 % a
# 30 s tput run still drops on the order of 10^2 packets per direction, which
# is enough to average, but a shorter run or a lower rate would not be. Do not
# add points below 0.05 % without raising `duration` to match.
LOSSES = [0.0, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0]

# grpcm is the connection-count control. Without it, "REST vs gRPC" is really
# "N connections vs one connection" as much as it is a protocol comparison,
# because HTTP/1.1 cannot multiplex and must open one connection per stream.
# grpc/grpcm and h3/h3m form a 2x2 over {TCP, QUIC} x {1 connection, 8
# connections}. The first sweep showed why the second axis has to be there:
# under load, going from one TCP connection to eight buys more goodput than
# switching that one connection to QUIC does, so a comparison that varies both
# at once cannot attribute the difference to either.
MODES = ["rest", "grpc", "grpcm", "h3", "h3m", "qtun"]

# --- Experiment A: how the picture changes with the number of logical streams
#
# The main matrix fixes N at 8, which is defensible as the midpoint of what
# published gateway implementations handle (4-11 measurement types) but leaves
# the obvious question unanswered: does the conclusion depend on N? It should,
# and in a direction the paper can predict - head-of-line blocking costs more
# the more streams share one connection - so sweeping N turns "we picked 8" into
# a measured trend.
#
# Only the four arms of the 2x2 are swept: rest cannot multiplex at all and
# qtun answers a different question, so neither says anything about N.
# N=8 is deliberately absent: those cells are already in the main matrix and
# re-running them would only add a session effect between the two halves.
STREAM_SWEEP_COUNTS = [2, 4, 16]
STREAM_SWEEP_MODES = ["grpc", "grpcm", "h3", "h3m"]
# 2 % is the mid point between 1 % and 5 %. It is chosen over 3 % because the
# main N=8 matrix already measures 2 %, so the sweep's N=2/4/16 cells line up
# with an existing column instead of introducing a loss rate that appears
# nowhere else in the paper.
STREAM_SWEEP_LOSSES = [0.0, 0.5, 1.0, 2.0, 5.0]
STREAM_SWEEP_REGIME = "hol"

# --- Experiment B: what it costs to bring a stream online (opt-in, --ramp)
#
# Every run above opens all of its streams before the clock starts, so the cost
# of adding one is invisible. That cost is the whole design argument for "one
# connection, streams added as needed": on a single connection a new stream is
# a frame with a new ID (no round trip), while a per-stream-connection arm must
# complete a fresh handshake - and under loss the INITIAL retransmission
# timeout is one second, not RTO_MIN's 200 ms.
#
# The interval is 3 s rather than the 30 s a human would pick: the transient
# being measured lasts well under a second, so a longer spacing buys nothing
# and would push the run past the point where it can still be repeated cheaply.
RAMP_INTERVAL_S = 3.0
RAMP_MODES = ["rest", "grpc", "grpcm", "h3", "h3m"]
RAMP_LOSSES = [0.0, 1.0, 2.0, 5.0]   # see STREAM_SWEEP_LOSSES for why 2 %
RAMP_REGIME = "hol"
# Long enough for 8 streams at 3 s apart (21 s) plus a settled tail.
RAMP_DURATION_S = 35

REGIMES = {
    # Closed loop, small batches. Throughput is RTT-bound and roughly equal
    # across transports, which is the point: what differs is how long a stream
    # sits blocked when a packet is lost.
    "hol": dict(streams=8, batch=20, inflight=1, pad=0, duration=30, warmup=5),
    # Link-saturating, but still ONE batch in flight per stream.
    #
    # inflight was 8 in the first sweep and that was a mistake: on the REST arm
    # --inflight becomes connections-per-stream, so REST ran with 8 x 8 = 64 TCP
    # connections against one connection for every other arm. Sixty-four CUBIC
    # flows barely notice 5 % loss (they held 91 % of their goodput while the
    # single-connection arms fell to 3 %), so the regime was measuring
    # connection count, not protocol.
    #
    # The link is saturated with batch size instead: at 8 streams and a 40 ms
    # RTT, 600 protobuf measurements per batch offers ~54 Mbit/s and the same
    # batch of JSON offers ~129 Mbit/s, both above the 50 Mbit/s bottleneck.
    # Run --explain to re-check this after changing any of those numbers.
    "tput": dict(streams=8, batch=600, inflight=1, pad=0, duration=30, warmup=5),
}

# Rough encoded size of one Measurement, used only by --explain to predict
# whether a regime will saturate the link. Measured from the first sweep.
BYTES_PER_MSG = {"protobuf": 56.0, "json": 134.0}

# ---------------------------------------------------------------------------
# Derived link characteristics
#
# Reimplements the formulas in GAIA's core/topo.py (LinkCharacteristics) so that
# `--explain` can show what a set of link parameters actually produces before
# you spend two hours running the sweep. Keep in sync with that file.
# ---------------------------------------------------------------------------

def link_characteristics(delay_ms, bandwidth_mbps, queue_size, mtu=1500):
    """Return the values GAIA will derive for one link.

    delay_ms is the ONE-WAY delay; netem is installed on both directions of the
    link, so each link contributes 2 x delay to the round trip.
    """
    rtt_ms = 2.0 * delay_ms
    bdp_bytes = bandwidth_mbps * 125000.0 * (rtt_ms / 1000.0)
    bdp_pkts = int(math.ceil(bdp_bytes / mtu))
    queuing_delay_ms = max(int((queue_size * mtu * 8.0 * 1000.0) /
                               (bandwidth_mbps * 1024 * 1024)), 1)
    buffer_bytes = mtu * bdp_pkts + bandwidth_mbps * 1000.0 * queuing_delay_ms / 8
    return {
        "one_way_delay_ms": delay_ms,
        "rtt_contribution_ms": rtt_ms,
        "bdp_pkts": bdp_pkts,
        "queuing_delay_ms": queuing_delay_ms,
        "buffer_bytes": buffer_bytes,
        "buffer_pkts": buffer_bytes / mtu,
    }


def explain(losses):
    """Print the network the current constants describe."""
    links = [
        ("c0 <-> r0 (access)", ACCESS_DELAY_MS, ACCESS_BW_MBPS, ACCESS_QUEUE),
        ("r0 <-> r1 (WAN)   ", WAN_DELAY_MS, WAN_BW_MBPS, WAN_QUEUE),
        ("s0 <-> r1 (access)", ACCESS_DELAY_MS, ACCESS_BW_MBPS, ACCESS_QUEUE),
    ]
    print("Link characteristics as GAIA will derive them")
    print("=" * 78)
    total_rtt = 0.0
    for name, d, bw, q in links:
        c = link_characteristics(d, bw, q)
        total_rtt += c["rtt_contribution_ms"]
        print(f"{name}  one-way {d:>5} ms  {bw:>7} Mbit/s  queue_size {q}")
        print(f"{'':20}  -> adds {c['rtt_contribution_ms']:>5} ms to RTT, "
              f"BDP {c['bdp_pkts']} pkt, queueing {c['queuing_delay_ms']} ms, "
              f"buffer {c['buffer_bytes']:.0f} B ({c['buffer_pkts']:.0f} pkt)")
    print("-" * 78)
    print(f"base RTT (no queueing)        : {total_rtt:.1f} ms")
    wan = link_characteristics(WAN_DELAY_MS, WAN_BW_MBPS, WAN_QUEUE)
    print(f"bottleneck                    : {WAN_BW_MBPS} Mbit/s on the WAN link, per direction")
    print(f"worst-case RTT when buffer full: {total_rtt + wan['queuing_delay_ms']:.1f} ms")
    print(f"BDP of the whole path         : "
          f"{WAN_BW_MBPS * 125000.0 * total_rtt / 1000.0 / 1500:.0f} pkt "
          f"({WAN_BW_MBPS * 125000.0 * total_rtt / 1000.0 / 1000:.0f} kB)")
    print()
    print("Loss is installed with netem on BOTH directions of the WAN link, so a")
    print("configured p% means p% each way. Round-trip survival is (1-p)^2:")
    for ls in losses:
        p = ls / 100.0
        print(f"  configured {ls:>4}%  ->  {100 * (1 - (1 - p) ** 2):.2f}% of round trips lose a packet")
    print()
    print("Regimes and the load they offer")
    print("-" * 78)
    rtt_s = total_rtt / 1000.0
    for name, r in REGIMES.items():
        print(f"  {name:<5} streams {r['streams']}, batch {r['batch']}, "
              f"inflight {r['inflight']}, pad {r['pad']} B, "
              f"{r['warmup']} s warm-up + {r['duration']} s measured")
        # One batch in flight per stream means each stream can offer at most
        # one batch per round trip; --inflight multiplies that.
        for codec, bpm in BYTES_PER_MSG.items():
            per_batch = r["batch"] * (bpm + r["pad"])
            offered = per_batch * r["streams"] * r["inflight"] * 8 / rtt_s / 1e6
            verdict = "saturates" if offered >= WAN_BW_MBPS else "UNDER-LOADS the link"
            print(f"        {codec:<8} {per_batch / 1024:7.1f} kB/batch -> "
                  f"offers {offered:7.1f} Mbit/s  ({verdict})")
        if r["inflight"] > 1:
            print(f"        WARNING: inflight {r['inflight']} gives the REST arm "
                  f"{r['streams'] * r['inflight']} TCP connections against 1 for the "
                  f"multiplexed arms.")
            print(f"                 That confounds connection count with protocol. "
                  f"Prefer inflight 1 and a larger batch.")


TOPO_TEMPLATE = """topoType:Generic
router:r0,10.0.1.0/24,10.0.1.1
router:r1,10.0.2.0/24,10.0.2.1
client:c0,r0,10.0.1.2
server:s0,r1,10.0.2.2
link:c0,r0,{access_delay},{access_queue},{access_bw},0.0,0
link:r0,r1,{wan_delay},{wan_queue},{wan_bw},{loss},0
link:s0,r1,{access_delay},{access_queue},{access_bw},0.0,0
transitSubnet:172.31.0.0/16
changeNetem:false
"""


def loss_tag(loss: float) -> str:
    """Filename-safe loss tag: 0.5 -> '0p5', 1.0 -> '1'."""
    if float(loss).is_integer():
        return str(int(loss))
    return str(loss).replace(".", "p")


def topo_name(loss: float) -> str:
    return f"topo_test_l{loss_tag(loss)}"


def xp_name(regime: str, mode: str, loss: float,
            streams: int = 0, ramp: bool = False) -> str:
    """Scenario file name.

    `streams` and `ramp` append a suffix only when they are set, so every cell
    of the original N=8 steady-state matrix keeps the name it already has on
    disk and run_sweep.sh goes on skipping the results that exist.
    """
    name = f"xp_test_{regime}_{mode}"
    if ramp:
        name += "_ramp"
    if streams:
        name += f"_n{streams}"
    return name + f"_l{loss_tag(loss)}"


def write_topo(gaia: str, loss: float) -> str:
    path = os.path.join(gaia, "config", "topo", topo_name(loss))
    body = TOPO_TEMPLATE.format(
        access_delay=ACCESS_DELAY_MS,
        access_queue=ACCESS_QUEUE,
        access_bw=ACCESS_BW_MBPS,
        wan_delay=WAN_DELAY_MS,
        wan_queue=WAN_QUEUE,
        wan_bw=WAN_BW_MBPS,
        loss=loss,
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def write_xp(gaia: str, rpcbench: str, regime: str, mode: str, loss: float,
             topo_path: str, pcap: str,
             streams: int = 0, ramp: bool = False) -> str:
    """Write one scenario file.

    `streams` overrides the regime's stream count (Experiment A); `ramp` brings
    the streams online one at a time instead of before the clock (Experiment B).
    Both default to off, so the call with neither reproduces the original
    steady-state cell byte for byte.
    """
    r = dict(REGIMES[regime])
    if streams:
        r["streams"] = streams
    if ramp:
        # A ramp run measures add cost, not throughput, and its totals are not
        # comparable with a steady-state run anyway (the later streams were
        # live for less of the window). Warm-up is dropped so the schedule is
        # measured from t=0.
        r["duration"] = RAMP_DURATION_S
        r["warmup"] = 0
    binaries = os.path.join(rpcbench, "bin")
    certs = os.path.join(rpcbench, "certs")
    label = f"{regime}-{mode}"
    if ramp:
        label += "-ramp"
    if streams:
        label += f"-n{streams}"
    label += f"-loss{loss_tag(loss)}"
    rtt_ms = 2 * (2 * ACCESS_DELAY_MS + WAN_DELAY_MS)

    # The Cloud Gateway serves only the mode under test, so an unused listener
    # can never absorb CPU or UDP buffer space that the measured arm needs.
    server_cmd = (
        f"{binaries}/cloudgw -certs {certs} -bind 0.0.0.0 -modes {mode}"
    )
    client_cmd = (
        f"{binaries}/edgegw"
        f" -server {{server_ip:s0}}"
        f" -mode {mode}"
        f" -streams {r['streams']}"
        f" -batch {r['batch']}"
        f" -pad {r['pad']}"
        f" -inflight {r['inflight']}"
        f" -duration {r['duration']}s"
        f" -warmup {r['warmup']}s"
        f" -ca {certs}/ca.pem"
        f" -server-name 10.0.2.2"
        f" -label {label}"
        f" -out {{output_dir}}/rpcbench.json"
        f" -param loss_pct={loss}"
        f" -param regime={regime}"
        f" -param wan_bw_mbps={WAN_BW_MBPS}"
        f" -param base_rtt_ms={rtt_ms}"
    )
    if ramp:
        client_cmd += (
            f" -ramp-interval {RAMP_INTERVAL_S}s"
            f" -param ramp=1"
        )
    # streams is already a -streams flag above; record it as a parameter too so
    # the analyser can group by it without parsing the scenario name.
    client_cmd += f" -param streams={r['streams']}"

    # The GAIA workload runner waits duration+60 s for the client, so the xp
    # duration only needs to cover warm-up plus the measurement window.
    xp_duration = r["warmup"] + r["duration"] + 10

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
    path = os.path.join(gaia, "config", "xp",
                        xp_name(regime, mode, loss, streams, ramp))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


# ---------------------------------------------------------------------------
# Path discovery
#
# The three components normally sit side by side:
#
#   test/
#   ├── gaia-experiment-main/
#   ├── rpcbench/
#   └── scripts/          <- this file
#
# so both paths can be found without being told. Worth doing: the alternative
# is typing absolute paths, and `~` is a trap here because the sweep runs under
# sudo, where it expands to /root rather than the user's home.
# ---------------------------------------------------------------------------

def _repo_root() -> str:
    """The directory containing this script's parent (the project root)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _looks_like_gaia(path: str) -> bool:
    return os.path.isdir(os.path.join(path, "config")) and \
        os.path.isfile(os.path.join(path, "cli.py"))


def _looks_like_rpcbench(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "go.mod")) and \
        os.path.isdir(os.path.join(path, "cmd"))


def _candidates(names) -> list:
    """Places to look, nearest first: project root, then one level up."""
    root = _repo_root()
    out = []
    for base in (root, os.path.dirname(root), os.getcwd(), os.path.dirname(os.getcwd())):
        for name in names:
            out.append(os.path.join(base, name))
    # de-duplicate, preserving order
    seen, uniq = set(), []
    for p in out:
        ap_ = os.path.abspath(p)
        if ap_ not in seen:
            seen.add(ap_)
            uniq.append(ap_)
    return uniq


def discover(kind: str):
    """Locate gaia or rpcbench automatically. Returns (path, tried)."""
    if kind == "gaia":
        names = ("gaia-experiment-main", "gaia-experiment", "gaia")
        check = _looks_like_gaia
    else:
        names = ("rpcbench",)
        check = _looks_like_rpcbench
    tried = _candidates(names)
    for p in tried:
        if check(p):
            return p, tried
    return None, tried


def resolve(kind: str, given, label: str):
    """Resolve a path the user gave, or find it. Returns (path, error_message)."""
    check = _looks_like_gaia if kind == "gaia" else _looks_like_rpcbench
    hint = ("a config/ directory and cli.py" if kind == "gaia"
            else "go.mod and a cmd/ directory")

    if given:
        path = os.path.abspath(os.path.expanduser(given))
        if check(path):
            return path, None
        msg = [f"error: {path} does not look like {label} (expected {hint} inside it)."]
        # The shell expands '~' before we ever see it, so check the resolved
        # path instead: under root, '~' becomes /root, which is almost never
        # where the project actually lives.
        root_home = os.path.expanduser("~")
        if os.geteuid() == 0 and path.startswith(root_home + os.sep):
            note = ("  note: you are running as root, so '~' expanded to "
                    f"{root_home} rather than the login user's home.")
            sudo_user = os.environ.get("SUDO_USER")
            if sudo_user:
                note += f" Try /home/{sudo_user}/... instead."
            msg.append(note)
        found, _ = discover(kind)
        if found:
            msg.append(f"  did you mean: {found}")
        msg.append(f"  tip: omit --{kind} entirely and it will be found automatically.")
        return None, "\n".join(msg)

    found, tried = discover(kind)
    if found:
        return found, None
    msg = [f"error: could not find {label} automatically. Looked in:"]
    msg += [f"    {p}" for p in tried[:6]]
    msg.append(f"  pass --{kind} <path> explicitly (a directory containing {hint}).")
    return None, "\n".join(msg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gaia", default=None,
                    help="path to the gaia-experiment checkout "
                         "(default: found next to this project)")
    ap.add_argument("--rpcbench", default=None,
                    help="path to the rpcbench directory, which must contain bin/ and certs/ "
                         "(default: found next to this project)")
    ap.add_argument("--out", default=None,
                    help="where to write manifest.json (default: alongside this script)")
    ap.add_argument("--modes", default=",".join(MODES),
                    help="comma-separated transports to include")
    ap.add_argument("--regimes", default=",".join(REGIMES),
                    help="comma-separated regimes to include")
    ap.add_argument("--losses", default=",".join(str(x) for x in LOSSES),
                    help="comma-separated loss percentages")
    ap.add_argument("--pcap", default="no", choices=["yes", "no"],
                    help="capture pcap during each run (heavy at high rates)")
    ap.add_argument("--repeats", type=int, default=3,
                    help="repetitions per cell, recorded in the manifest")
    ap.add_argument("--explain", action="store_true",
                    help="print the derived link characteristics and exit, without writing files")
    ap.add_argument("--no-stream-sweep", dest="stream_sweep", action="store_false",
                    help="omit Experiment A (the stream-count sensitivity cells)")
    ap.set_defaults(stream_sweep=True)
    ap.add_argument("--ramp", action="store_true",
                    help="also emit Experiment B: streams brought online one at a "
                         "time, to measure what adding one costs")
    ap.add_argument("--version", action="version", version=f"gen_scenarios.py {VERSION}")
    args = ap.parse_args()

    if args.explain:
        explain([float(x) for x in args.losses.split(",") if x.strip()])
        return 0

    gaia, err = resolve("gaia", args.gaia, "a gaia-experiment checkout")
    if err:
        print(err, file=sys.stderr)
        return 2
    rpcbench, err = resolve("rpcbench", args.rpcbench, "the rpcbench directory")
    if err:
        print(err, file=sys.stderr)
        return 2
    if not args.gaia:
        print(f"gaia       : {gaia}  (auto-detected)")
    if not args.rpcbench:
        print(f"rpcbench   : {rpcbench}  (auto-detected)")

    out_dir = os.path.abspath(args.out) if args.out else os.path.dirname(os.path.abspath(__file__))

    # The generated scenarios hard-code these paths, so flag a missing build now
    # rather than letting every run in the sweep fail two hours from now.
    for sub, how in (("bin/cloudgw", "./build.sh build"), ("certs/ca.pem", "./build.sh certs")):
        if not os.path.exists(os.path.join(rpcbench, sub)):
            print(f"warning: {os.path.join(rpcbench, sub)} is missing — "
                  f"run `{how}` in {rpcbench} before the sweep", file=sys.stderr)

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    regimes = [r.strip() for r in args.regimes.split(",") if r.strip()]
    losses = [float(x) for x in args.losses.split(",") if x.strip()]

    for m in modes:
        if m not in MODES:
            print(f"error: unknown mode {m!r}", file=sys.stderr)
            return 2
    for r in regimes:
        if r not in REGIMES:
            print(f"error: unknown regime {r!r}", file=sys.stderr)
            return 2

    runs = []
    topos = {}

    def topo_for(loss):
        tag = loss_tag(loss)
        if tag not in topos:
            topos[tag] = write_topo(gaia, loss)
        return topos[tag]

    def emit(regime, mode, loss, streams=0, ramp=False):
        xp_path = write_xp(gaia, rpcbench, regime, mode, loss,
                           topo_for(loss), args.pcap, streams=streams, ramp=ramp)
        runs.append({
            "regime": regime,
            "mode": mode,
            "loss_pct": loss,
            "streams": streams or REGIMES[regime]["streams"],
            "ramp": bool(ramp),
            "topo": topo_for(loss),
            "xp": xp_path,
            "name": xp_name(regime, mode, loss, streams, ramp),
        })

    # --- main steady-state matrix (unchanged names, so existing results are
    #     still found and skipped by run_sweep.sh) --------------------------
    for loss in losses:
        topo_for(loss)
    for regime in regimes:
        for loss in losses:
            for mode in modes:
                emit(regime, mode, loss)

    # --- Experiment A: stream-count sensitivity (on by default) -----------
    if args.stream_sweep:
        for n in STREAM_SWEEP_COUNTS:
            for loss in STREAM_SWEEP_LOSSES:
                for mode in STREAM_SWEEP_MODES:
                    emit(STREAM_SWEEP_REGIME, mode, loss, streams=n)

    # --- Experiment B: stream add cost (opt-in) ---------------------------
    if args.ramp:
        for loss in RAMP_LOSSES:
            for mode in RAMP_MODES:
                emit(RAMP_REGIME, mode, loss, ramp=True)

    manifest = {
        "schema": "test.rpcbench.manifest/1",
        "gaia": gaia,
        "rpcbench": rpcbench,
        "repeats": args.repeats,
        "pcap": args.pcap,
        "network": {
            "access_delay_ms": ACCESS_DELAY_MS,
            "wan_delay_ms": WAN_DELAY_MS,
            "base_rtt_ms": 2 * (2 * ACCESS_DELAY_MS + WAN_DELAY_MS),
            "wan_bw_mbps": WAN_BW_MBPS,
            "wan_queue_size": WAN_QUEUE,
            "access_queue_size": ACCESS_QUEUE,
            "derived_wan": link_characteristics(WAN_DELAY_MS, WAN_BW_MBPS, WAN_QUEUE),
            "derived_access": link_characteristics(ACCESS_DELAY_MS, ACCESS_BW_MBPS, ACCESS_QUEUE),
        },
        "regimes": {k: REGIMES[k] for k in regimes},
        "topologies": topos,
        "runs": runs,
    }
    os.makedirs(out_dir, exist_ok=True)
    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"topologies : {len(topos)}")
    print(f"scenarios  : {len(runs)}")
    print(f"repeats    : {args.repeats}  -> {len(runs) * args.repeats} runs total")
    print(f"manifest   : {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

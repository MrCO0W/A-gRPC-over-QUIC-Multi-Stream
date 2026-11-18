#!/usr/bin/env python3
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

import template


def get_latest_output_dir():
    if not os.path.exists("output"):
        os.makedirs("output", exist_ok=True)
        return "output"
    subdirs = [
        os.path.join("output", d)
        for d in os.listdir("output")
        if os.path.isdir(os.path.join("output", d))
    ]
    if not subdirs:
        return "output"
    return max(subdirs, key=os.path.getmtime)


def experiment(xpType='performance-analysis', exp='simple', pcap='no', nc=0, ps=400,
               protocol='quic', delay=15, queueSize=1000, bandwidth=1000, loss=1):
    os.makedirs('./config/xp', exist_ok=True)
    os.makedirs('./config/topo', exist_ok=True)

    xpTmpl = template.xp_template.xp
    topoTmpl = template.topology_template.topo

    xpFile = f'./config/xp/{xpType}-{exp}-{pcap}-{nc}-{ps}-{protocol}'
    if not os.path.exists(xpFile):
        with open(xpFile, 'w') as f:
            f.write(xpTmpl.format(xpType, exp, pcap, nc, ps, protocol))

    topoFile = f'./config/topo/topo-{delay}_{queueSize}_{bandwidth}_{loss}'
    if not os.path.exists(topoFile):
        with open(topoFile, 'w') as f:
            f.write(topoTmpl.format(5, delay, queueSize, bandwidth, loss))

    cmd = [sys.executable, 'runner.py', '-t', topoFile, '-x', xpFile]
    ret = subprocess.run(cmd)

    latest_dir = get_latest_output_dir()

    avg_path = os.path.join(latest_dir, 'avg')
    if os.path.exists(avg_path):
        with open(avg_path, 'r') as stream, open(os.path.join(latest_dir, f"{protocol}-atd-nc:{nc}"), 'a') as out_f:
            for line in stream:
                line_str = line.strip()
                print(line_str)
                out_f.write(f"{line_str}\n")

    packets_path = os.path.join(latest_dir, 'packets')
    if os.path.exists(packets_path):
        with open(packets_path, 'r') as stream, open(os.path.join(latest_dir, f"{protocol}-packets-nc:{nc}"), 'a') as out_f:
            for line in stream:
                line_str = line.strip()
                print(line_str)
                out_f.write(f"{line_str}\n")

    netstat_path = os.path.join(latest_dir, 'netstat_router_after')
    if os.path.exists(netstat_path):
        with open(netstat_path, 'r') as stream:
            for line in stream:
                if line.startswith('    InNoECTPkts: '):
                    l = line[len('    InNoECTPkts: '):].strip()
                    with open(os.path.join(latest_dir, f"{protocol}-netstat-nc:{nc}"), 'a') as out_f:
                        out_f.write(f"{l}\n")
                if line.startswith('    InOctets: '):
                    l = line[len('    InOctets: '):].strip()
                    with open(os.path.join(latest_dir, f"{protocol}-netstat-inoctets-nc:{nc}"), 'a') as out_f:
                        out_f.write(f"{l}\n")

    return ret.returncode


def runIperf(xpType='iperf_exp', exp='simple', pcap='no', delay=15,
             queueSize=1000, bandwidth=100, loss=0):
    os.makedirs('./config/xp', exist_ok=True)
    os.makedirs('./config/topo', exist_ok=True)

    xpTmpl = template.xp_template.xp
    topoTmpl = template.topology_template.topo

    xpFile = f'./config/xp/{xpType}-{exp}-{pcap}'
    if not os.path.exists(xpFile):
        with open(xpFile, 'w') as f:
            f.write(xpTmpl.format(xpType, exp, pcap, 0, 0, 0))

    topoFile = f'./config/topo/topo-{delay}_{queueSize}_{bandwidth}_{loss}'
    if not os.path.exists(topoFile):
        with open(topoFile, 'w') as f:
            f.write(topoTmpl.format(5, delay, queueSize, bandwidth, loss))

    cmd = [sys.executable, 'runner.py', '-t', topoFile, '-x', xpFile]
    ret = subprocess.run(cmd)
    return ret.returncode


def clean_artifacts(purge_configs=False):
    # Remove files in root directory
    patterns = ['netstat*', '*.pcap', 'server-log.txt', 'client-log.txt', '*.log', '*.out']
    for pat in patterns:
        for f in glob.glob(pat):
            try:
                os.remove(f)
            except OSError:
                pass

    # Clean output directory contents
    if os.path.exists('output'):
        for item in os.listdir('output'):
            if item == '.gitkeep':
                continue
            item_path = os.path.join('output', item)
            try:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
            except OSError:
                pass

    # Remove ssl-key.log recursively
    for root, _, files in os.walk('.'):
        if root.startswith("./.") or "/." in root:
            continue
        for f in files:
            if f == 'ssl-key.log':
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass

    # Purge configs only if explicitly requested (BE-3)
    if purge_configs:
        print("Purging config/topo and config/xp contents as --purge-configs was specified...")
        for cdir in ['config/topo', 'config/xp']:
            if os.path.exists(cdir):
                for f in os.listdir(cdir):
                    fpath = os.path.join(cdir, f)
                    try:
                        if os.path.isfile(fpath):
                            os.remove(fpath)
                    except OSError:
                        pass

        # Re-create default sample configs
        os.makedirs('./config/xp', exist_ok=True)
        os.makedirs('./config/topo', exist_ok=True)
        xpFile = './config/xp/iperf_exp-simple-no'
        if not os.path.exists(xpFile):
            with open(xpFile, 'w') as f:
                f.write(template.xp_template.xp.format('iperf_exp', 'simple', 'no', 0, 0, 0))

        topoFile = './config/topo/topo-15_1000_100_0'
        if not os.path.exists(topoFile):
            with open(topoFile, 'w') as f:
                f.write(template.topology_template.topo.format(5, 15, 1000, 100, 0))

    os.makedirs('output', exist_ok=True)
    with open('output/.gitkeep', 'w') as f:
        f.write('# Keep output directory in git repo\n')


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="GAIA Experimentation CLI and automation runner"
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # Subcommand: run
    run_parser = subparsers.add_parser("run", help="Run performance analysis experiment")
    run_parser.add_argument("--protocol", default="quic", choices=["quic", "tcp", "udp"], help="Transport protocol")
    run_parser.add_argument("--nc", type=int, default=0, help="Number of clients / client index multiplier")
    run_parser.add_argument("--ps", type=int, default=400, help="Packet size")
    run_parser.add_argument("--delay", type=float, default=15, help="Link delay in ms")
    run_parser.add_argument("--queue-size", type=int, default=1000, help="Queue size in packets")
    run_parser.add_argument("--bandwidth", type=float, default=1000, help="Link bandwidth in Mbps")
    run_parser.add_argument("--loss", type=float, default=1, help="Packet loss percentage")
    run_parser.add_argument("--exp", default="simple", help="Experiment name")
    run_parser.add_argument("--xp-type", default="performance-analysis", help="Experiment type")
    run_parser.add_argument("--pcap", default="no", choices=["yes", "no"], help="Capture pcap")

    # Subcommand: iperf
    iperf_parser = subparsers.add_parser("iperf", help="Run iperf experiment")
    iperf_parser.add_argument("--delay", type=float, default=15, help="Link delay in ms")
    iperf_parser.add_argument("--queue-size", type=int, default=1000, help="Queue size in packets")
    iperf_parser.add_argument("--bandwidth", type=float, default=100, help="Link bandwidth in Mbps")
    iperf_parser.add_argument("--loss", type=float, default=0, help="Packet loss percentage")
    iperf_parser.add_argument("--exp", default="simple", help="Experiment name")
    iperf_parser.add_argument("--xp-type", default="iperf_exp", help="Experiment type")
    iperf_parser.add_argument("--pcap", default="no", choices=["yes", "no"], help="Capture pcap")

    # Subcommand: verify (S2, WF-11, WF-16, T-4)
    verify_parser = subparsers.add_parser("verify", help="Validate topology and experiment configurations without root")
    verify_parser.add_argument("-t", "--topo", required=True, help="Path to topology parameter file")
    verify_parser.add_argument("-x", "--xp", default=None, help="Path to experiment parameter file")
    verify_parser.add_argument("--json", action="store_true", help="Output validation report in JSON")

    # Subcommand: preflight (S3, WF-4, T-5, T-6)
    preflight_parser = subparsers.add_parser("preflight", help="Run preflight mesh ping reachability gate")
    preflight_parser.add_argument("-t", "--topo", required=True, help="Path to topology parameter file")
    preflight_parser.add_argument("--count", type=int, default=3, help="Number of ping packets per probe")
    preflight_parser.add_argument("--timeout", type=int, default=1, help="Ping timeout in seconds")
    preflight_parser.add_argument("--max-probes", type=int, default=200, help="Maximum allowed probes")
    preflight_parser.add_argument("--strict-mesh", action="store_true", help="Fail if Tier-2 probes fail")

    # Subcommand: exec (S4, WF-3, T-8)
    exec_parser = subparsers.add_parser("exec", help="Execute an experiment workflow with verification gates")
    exec_parser.add_argument("-t", "--topo", required=True, help="Path to topology parameter file")
    exec_parser.add_argument("-x", "--xp", required=True, help="Path to experiment parameter file")
    exec_parser.add_argument("-o", "--output-dir", default=None, help="Custom output directory")
    exec_parser.add_argument("--skip-preflight", action="store_true", help="Skip preflight mesh ping reachability test")
    exec_parser.add_argument("--pcap", default="no", choices=["yes", "no"], help="Capture pcap files")
    exec_parser.add_argument("--allow-custom-cmd", action="store_true", help="Explicitly permit execution of custom workload shell commands (S-4)")

    # Subcommand: analyze (S6, T-17)
    analyze_parser = subparsers.add_parser("analyze", help="Analyze experiment results and generate CSV and SVG charts")
    analyze_parser.add_argument("--run-dir", default=None, help="Output directory to analyze (default: latest)")
    analyze_parser.add_argument("--strict", action="store_true", help="Return rc=4 if experiment validity check fails")

    # Subcommand: inspect (TLS-decrypted PCAP exploration)
    inspect_parser = subparsers.add_parser("inspect", help="Inspect PCAP files with SSL/TLS keylog decryption in isolated XDG sandbox")
    inspect_parser.add_argument("target", nargs="?", default=None, help="Target .pcap file or experiment run directory (default: latest run)")
    inspect_parser.add_argument("-k", "--keylog", default=None, help="Explicit path to SSLKEYLOGFILE")
    inspect_parser.add_argument("-t", "--tool", default="auto", choices=["auto", "termshark", "tshark", "wireshark"], help="Tool to launch (default: auto)")
    inspect_parser.add_argument("-Y", "--display-filter", default=None, help="Display filter (e.g. 'tls', 'http2')")
    inspect_parser.add_argument("--dry-run", action="store_true", help="Print sandbox environment and command without executing")

    # Subcommand: merge
    merge_parser = subparsers.add_parser("merge", help="Merge pcap files from latest experiment")
    merge_parser.add_argument("output_file", help="Name of merged pcap file")

    # Subcommand: clean
    clean_parser = subparsers.add_parser("clean", help="Clean experiment output and temporary files")
    clean_parser.add_argument("--purge-configs", action="store_true", default=False,
                              help="Also purge user configs in config/topo and config/xp")

    # Subcommand: mkexp
    mkexp_parser = subparsers.add_parser("mkexp", help="Scaffold a new experiment template")
    mkexp_parser.add_argument("class_name", help="Name of experiment class")
    mkexp_parser.add_argument("exp_name", help="File name for experiment module")

    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 1

    if args.subcommand == "verify":
        from core.validation import validate_config
        is_valid, report = validate_config(args.topo, args.xp)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            if is_valid:
                print(f"VERIFY PASS: Topology '{args.topo}' is valid.")
                if report.get("warnings"):
                    for w in report["warnings"]:
                        print(f"  WARNING: {w}")
            else:
                print(f"VERIFY FAIL: Topology '{args.topo}' has {len(report['errors'])} error(s):")
                for err in report["errors"]:
                    print(f"  - {err}")
        return 0 if is_valid else 2

    elif args.subcommand == "preflight":
        # S3 Preflight mesh ping gate (rc=0 or rc=3, preserving non-zero runner codes)
        temp_dir = tempfile.mkdtemp(prefix="gaia_preflight_")
        temp_xp = os.path.join(temp_dir, "xp_preflight")
        with open(temp_xp, "w") as f:
            f.write(f"xpType:mesh_ping\ncount:{args.count}\ntimeout:{args.timeout}\nmax_probes:{args.max_probes}\nstrict_mesh:{'yes' if args.strict_mesh else 'no'}\n")

        cmd = [sys.executable, "runner.py", "-t", args.topo, "-x", temp_xp, "-o", temp_dir]
        ret = subprocess.run(cmd)
        reach_path = os.path.join(temp_dir, "reachability.json")
        reach_verdict = None
        if os.path.exists(reach_path):
            try:
                with open(reach_path, "r", encoding="utf-8") as f:
                    res = json.load(f)
                reach_verdict = res.get('verdict')
                print(f"Preflight Verdict: {reach_verdict} (Tier-1 failed: {res.get('tier1', {}).get('failed', 0)})")
            except Exception:
                pass
        shutil.rmtree(temp_dir, ignore_errors=True)
        if ret.returncode == 0 and reach_verdict == "PASS":
            return 0
        elif reach_verdict == "FAIL":
            return 3
        else:
            return ret.returncode if ret.returncode != 0 else 3

    elif args.subcommand == "exec":
        # 1. Static Validation (S2)
        from core.validation import validate_config
        is_valid, report = validate_config(args.topo, args.xp)
        if not is_valid:
            print(f"Execution blocked by Static Validation (rc=2): {'; '.join(report['errors'])}")
            return 2

        # 1-1. S-4 Gate: Custom commands require --allow-custom-cmd
        custom_cmds = report.get("details", {}).get("custom_commands", [])
        if custom_cmds and not getattr(args, 'allow_custom_cmd', False):
            print("Execution blocked by Security Gate (rc=2): Experiment contains custom commands but --allow-custom-cmd was not specified.")
            for c in custom_cmds:
                print(f"  - Node '{c['node']}' ({c['kind']}): {c.get('command')}")
            return 2

        # 2. Preflight Gate (S3) unless skipped
        if not args.skip_preflight:
            print("Running Preflight Mesh Ping Gate (S3)...")
            preflight_rc = subprocess.run([
                sys.executable, "cli.py", "preflight", "-t", args.topo
            ]).returncode
            if preflight_rc != 0:
                print("Execution blocked by Preflight Gate (rc=3).")
                return 3

        # 3. Experiment Execution (S4)
        out_dir = args.output_dir or get_latest_output_dir()
        cmd = [sys.executable, "runner.py", "-t", args.topo, "-x", args.xp]
        if args.output_dir:
            cmd.extend(["-o", args.output_dir])
        run_ret = subprocess.run(cmd)
        if run_ret.returncode != 0:
            return run_ret.returncode

        # 4. Analysis (S6)
        from core.analysis import analyze_run_dir
        actual_out = args.output_dir or get_latest_output_dir()
        ana_rc, summary, _ = analyze_run_dir(actual_out, strict=False)
        gp = summary.get('metrics', {}).get('goodput_mbps')
        gp_str = f"{gp:.2f} Mbps" if gp is not None else "N/A"
        print(f"Execution complete. Summary: {summary.get('label')} (Goodput: {gp_str})")
        return ana_rc

    elif args.subcommand == "analyze":
        from core.analysis import analyze_run_dir
        target_dir = args.run_dir or get_latest_output_dir()
        rc, summary, _ = analyze_run_dir(target_dir, strict=args.strict)
        print(json.dumps(summary, indent=2))
        return rc

    elif args.subcommand == "inspect":
        from tools.inspect_pcap import inspect_pcap
        return inspect_pcap(
            target=args.target,
            keylog=args.keylog,
            tool=args.tool,
            display_filter=args.display_filter,
            dry_run=args.dry_run,
        )

    elif args.subcommand == "run":
        return experiment(
            xpType=args.xp_type,
            exp=args.exp,
            pcap=args.pcap,
            nc=args.nc,
            ps=args.ps,
            protocol=args.protocol,
            delay=args.delay,
            queueSize=args.queue_size,
            bandwidth=args.bandwidth,
            loss=args.loss
        )

    elif args.subcommand == "iperf":
        return runIperf(
            xpType=args.xp_type,
            exp=args.exp,
            pcap=args.pcap,
            delay=args.delay,
            queueSize=args.queue_size,
            bandwidth=args.bandwidth,
            loss=args.loss
        )

    elif args.subcommand == "merge":
        latest_dir = get_latest_output_dir()
        out_pcap = os.path.join(latest_dir, args.output_file)
        pcap_files = glob.glob(os.path.join(latest_dir, "*.pcap"))
        if not pcap_files:
            print(f"No pcap files found in {latest_dir} to merge.")
            return 0
        cmd = ["mergecap", "-F", "pcap"] + pcap_files + ["-w", out_pcap]
        ret = subprocess.run(cmd)
        return ret.returncode

    elif args.subcommand == "clean":
        clean_artifacts(purge_configs=args.purge_configs)
        print("Clean complete.")
        return 0

    elif args.subcommand == "mkexp":
        expCode = f'./experiments/{args.exp_name}.py'
        if not os.path.exists(expCode):
            with open(expCode, 'w') as f:
                f.write(template.exp_code_template.getExpCode(args.class_name, args.exp_name))
            print(f"Created experiment template at {expCode}")
        else:
            print(f"File {expCode} already exists.")
        return 0

    return 0


if __name__ == '__main__':
    sys.exit(main())

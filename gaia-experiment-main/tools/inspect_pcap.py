#!/usr/bin/env python3
"""GAIA PCAP & SSL/TLS Keylog Inspector — Ephemeral XDG Sandbox for termshark / tshark / wireshark.

Provides non-invasive, per-run TLS decryption inspection without polluting or hardcoding
global `~/.config/wireshark/preferences`.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple


KEYLOG_CANDIDATES = [
    "sslkeys.log",
    "sslkey.log",
    "tlskeys.log",
    "tls.keylog",
    "keylog.txt",
    "keys.log",
    "sslkeylog.log",
]


def find_latest_output_dir(base_dir: str = "output") -> Optional[str]:
    """Find the most recently modified run directory under base_dir that contains PCAP files."""
    if not os.path.exists(base_dir):
        return None
    subdirs = [
        os.path.join(base_dir, d)
        for d in os.listdir(base_dir)
        if os.path.isdir(os.path.join(base_dir, d))
    ]
    if not subdirs:
        return None
    pcap_dirs = [
        d for d in subdirs
        if glob.glob(os.path.join(d, "*.pcap")) or glob.glob(os.path.join(d, "*.pcapng"))
    ]
    if pcap_dirs:
        return max(pcap_dirs, key=os.path.getmtime)
    return max(subdirs, key=os.path.getmtime)


def discover_pcap_files(target_path: str) -> List[str]:
    """Discover pcap files from a file path or directory."""
    if os.path.isfile(target_path):
        return [target_path]
    if os.path.isdir(target_path):
        pcaps = glob.glob(os.path.join(target_path, "*.pcap")) + glob.glob(os.path.join(target_path, "*.pcapng"))
        # Sort so merged or primary pcaps come first
        pcaps.sort(key=lambda p: (0 if "merged" in os.path.basename(p) else 1, p))
        return pcaps
    return []


def discover_keylog_file(target_path: str, explicit_keylog: Optional[str] = None) -> Optional[str]:
    """Discover SSLKEYLOGFILE candidate in target path or its directory."""
    if explicit_keylog:
        if os.path.exists(explicit_keylog):
            return os.path.abspath(explicit_keylog)
        else:
            print(f"Warning: Specified keylog file '{explicit_keylog}' not found.", file=sys.stderr)
            return None

    search_dir = target_path if os.path.isdir(target_path) else os.path.dirname(os.path.abspath(target_path))

    for candidate in KEYLOG_CANDIDATES:
        cand_path = os.path.join(search_dir, candidate)
        if os.path.isfile(cand_path) and os.path.getsize(cand_path) > 0:
            if not os.access(cand_path, os.R_OK):
                print(f"[!] Warning: Found keylog file '{cand_path}' but permission is denied for current user. Run inspect as the file owner user or with sudo to enable decryption.", file=sys.stderr)
                continue
            return os.path.abspath(cand_path)

    # Check for any *.keylog or *keys*.log in search_dir
    keylogs = glob.glob(os.path.join(search_dir, "*key*.log")) + glob.glob(os.path.join(search_dir, "*.keylog"))
    for k in keylogs:
        if os.path.isfile(k) and os.path.getsize(k) > 0:
            if not os.access(k, os.R_OK):
                print(f"[!] Warning: Found keylog file '{k}' but permission is denied for current user. Run inspect as the file owner user or with sudo to enable decryption.", file=sys.stderr)
                continue
            return os.path.abspath(k)

    return None


def setup_xdg_wireshark_env(keylog_path: Optional[str], temp_dir: str) -> Dict[str, str]:
    """Create isolated XDG_CONFIG_HOME environment with Wireshark preferences pointing to keylog."""
    ws_dir = os.path.join(temp_dir, "wireshark")
    os.makedirs(ws_dir, exist_ok=True)

    prefs_path = os.path.join(ws_dir, "preferences")

    # Inherit existing user settings if available
    user_ws_dir = os.path.expanduser("~/.config/wireshark")
    existing_lines = []
    if os.path.isdir(user_ws_dir):
        user_prefs = os.path.join(user_ws_dir, "preferences")
        if os.path.isfile(user_prefs):
            try:
                with open(user_prefs, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if not line.strip().startswith("tls.keylog_file") and not line.strip().startswith("ssl.keylog_file"):
                            existing_lines.append(line)
            except Exception:
                pass

        # Copy colorfilters if present
        colorfilters = os.path.join(user_ws_dir, "colorfilters")
        if os.path.isfile(colorfilters):
            try:
                shutil.copy2(colorfilters, os.path.join(ws_dir, "colorfilters"))
            except Exception:
                pass

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(prefs_path, flags, 0o600), "w", encoding="utf-8") as f:
        f.write("# GAIA Ephemeral Wireshark Preferences (Auto-generated)\n")
        if keylog_path:
            f.write(f"tls.keylog_file: {keylog_path}\n")
            f.write(f"ssl.keylog_file: {keylog_path}\n")
        for line in existing_lines:
            f.write(line)

    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = temp_dir
    if keylog_path:
        env["SSLKEYLOGFILE"] = keylog_path

    return env


def resolve_tool(requested_tool: str) -> str:
    """Resolve which tool executable to use."""
    if requested_tool in ("termshark", "tshark", "wireshark"):
        tool_bin = shutil.which(requested_tool)
        if tool_bin:
            return requested_tool
        print(f"Warning: Requested tool '{requested_tool}' not found in PATH.", file=sys.stderr)

    # Auto-detection
    if sys.stdout.isatty() and shutil.which("termshark"):
        return "termshark"
    if shutil.which("tshark"):
        return "tshark"
    if shutil.which("wireshark"):
        return "wireshark"
    return "tshark"


def build_command(tool: str, pcap_file: str, display_filter: Optional[str] = None, extra_args: Optional[List[str]] = None) -> List[str]:
    """Build the command invocation for the chosen tool."""
    cmd = [tool, "-r", pcap_file]
    if display_filter:
        cmd.extend(["-Y", display_filter])
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def inspect_pcap(
    target: Optional[str] = None,
    keylog: Optional[str] = None,
    tool: str = "auto",
    display_filter: Optional[str] = None,
    dry_run: bool = False,
    extra_args: Optional[List[str]] = None,
) -> int:
    """Main entry point for inspecting PCAP files with ephemeral TLS keylog integration."""
    target_path = target
    if not target_path:
        target_path = find_latest_output_dir()
        if not target_path:
            print("Error: No target PCAP file or output run directory specified, and no output runs found.", file=sys.stderr)
            return 1

    if not os.path.exists(target_path):
        print(f"Error: Target path '{target_path}' does not exist.", file=sys.stderr)
        return 1

    pcaps = discover_pcap_files(target_path)
    if not pcaps:
        print(f"Error: No .pcap / .pcapng files found in '{target_path}'.", file=sys.stderr)
        return 1

    chosen_pcap = pcaps[0]
    chosen_keylog = discover_keylog_file(target_path, explicit_keylog=keylog)
    selected_tool = resolve_tool(tool)

    if dry_run:
        print("=== GAIA PCAP Inspector (Dry Run) ===")
        print(f"Target Path:     {target_path}")
        print(f"Selected PCAP:   {chosen_pcap}")
        print(f"Available PCAPs: {len(pcaps)} ({', '.join(os.path.basename(p) for p in pcaps)})")
        print(f"TLS Keylog:      {chosen_keylog or 'None (TLS decryption disabled)'}")
        print(f"Selected Tool:   {selected_tool}")
        print(f"Display Filter:  {display_filter or 'None'}")

        with tempfile.TemporaryDirectory(prefix="gaia_termshark_dry_") as tmp:
            env = setup_xdg_wireshark_env(chosen_keylog, tmp)
            prefs_file = os.path.join(tmp, "wireshark", "preferences")
            print(f"Generated XDG:   {tmp}")
            print("Preferences:\n---")
            if os.path.exists(prefs_file):
                with open(prefs_file, "r") as f:
                    for l in f.readlines()[:5]:
                        print("  " + l.strip())
            print(f"---\nCommand Line:    {' '.join(build_command(selected_tool, chosen_pcap, display_filter, extra_args))}")
        return 0

    with tempfile.TemporaryDirectory(prefix="gaia_termshark_") as tmp_xdg:
        env = setup_xdg_wireshark_env(chosen_keylog, tmp_xdg)
        cmd = build_command(selected_tool, chosen_pcap, display_filter, extra_args)

        if chosen_keylog:
            print(f"[*] Attaching SSL/TLS keylog: {chosen_keylog}")
        print(f"[*] Launching {selected_tool} with PCAP: {chosen_pcap}")

        try:
            res = subprocess.run(cmd, env=env)
            return res.returncode
        except KeyboardInterrupt:
            return 130
        except Exception as e:
            print(f"Error launching {selected_tool}: {e}", file=sys.stderr)
            return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect PCAP files with automatic SSL/TLS keylog decryption via ephemeral XDG sandboxing."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Path to .pcap file or experiment run directory (default: latest output run)",
    )
    parser.add_argument(
        "-k", "--keylog",
        default=None,
        help="Explicit path to SSLKEYLOGFILE (auto-discovered from run directory if omitted)",
    )
    parser.add_argument(
        "-t", "--tool",
        default="auto",
        choices=["auto", "termshark", "tshark", "wireshark"],
        help="Inspection tool to execute (default: auto -> termshark if interactive, else tshark)",
    )
    parser.add_argument(
        "-Y", "--display-filter",
        default=None,
        help="Wireshark display filter to apply on launch (e.g. 'tls', 'http2', 'quic')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved paths, environment, and command without launching UI",
    )

    args, extra_args = parser.parse_known_args(argv)
    return inspect_pcap(
        target=args.target,
        keylog=args.keylog,
        tool=args.tool,
        display_filter=args.display_filter,
        dry_run=args.dry_run,
        extra_args=extra_args,
    )


if __name__ == "__main__":
    sys.exit(main())

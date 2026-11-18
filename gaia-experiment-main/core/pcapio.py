"""Standard Library Binary PCAP Reader — WF-12, T-16.

Parses binary pcap files without external dependencies (no scapy, dpkt, or tshark).
Handles endianness and link-layer encapsulation types with capinfos fallback.
"""
import os
import struct
import subprocess
from typing import Any, Dict, Optional


def read_pcap_summary(pcap_path: str) -> Dict[str, Any]:
    """
    Read summary metrics (packet count, byte count, timestamps, link type) from a pcap file.
    """
    result: Dict[str, Any] = {
        "file": pcap_path,
        "valid": False,
        "total_packets": 0,
        "total_bytes": 0,
        "link_type": None,
        "link_type_name": "UNKNOWN",
        "start_ts": None,
        "end_ts": None,
        "duration_sec": 0.0,
        "avg_bandwidth_mbps": 0.0,
        "error": None
    }

    if not os.path.exists(pcap_path):
        result["error"] = f"File '{pcap_path}' not found"
        return result

    file_size = os.path.getsize(pcap_path)
    if file_size < 24:
        result["error"] = "File too small to be a valid pcap (less than 24 bytes)"
        return result

    try:
        with open(pcap_path, "rb") as f:
            global_header = f.read(24)
            if len(global_header) < 24:
                result["error"] = "Truncated pcap global header"
                return result

            magic_bytes = global_header[:4]
            if magic_bytes == b"\xa1\xb2\xc3\xd4":
                endian = ">"
            elif magic_bytes == b"\xd4\xc3\xb2\xa1":
                endian = "<"
            elif magic_bytes == b"\n\r\r\n":
                return _fallback_capinfos(pcap_path, result, "pcapng format detected")
            else:
                return _fallback_capinfos(pcap_path, result, f"Unknown magic: {magic_bytes.hex()}")

            major, minor, thiszone, sigfigs, snaplen, network = struct.unpack(f"{endian}HHiIII", global_header[4:])
            result["link_type"] = network
            if network == 1:
                result["link_type_name"] = "LINKTYPE_ETHERNET"
            elif network == 113:
                result["link_type_name"] = "LINKTYPE_LINUX_SLL"
            else:
                result["link_type_name"] = f"LINKTYPE_{network}"

            packet_count = 0
            total_bytes = 0
            first_ts: Optional[float] = None
            last_ts: Optional[float] = None

            while True:
                rec_hdr = f.read(16)
                if len(rec_hdr) < 16:
                    break
                ts_sec, ts_usec, incl_len, orig_len = struct.unpack(f"{endian}IIII", rec_hdr)
                ts = ts_sec + (ts_usec / 1_000_000.0)

                if first_ts is None:
                    first_ts = ts
                last_ts = ts

                packet_count += 1
                total_bytes += orig_len

                # Skip packet payload
                f.seek(incl_len, os.SEEK_CUR)

            result["valid"] = True
            result["total_packets"] = packet_count
            result["total_bytes"] = total_bytes
            result["start_ts"] = first_ts
            result["end_ts"] = last_ts
            if first_ts is not None and last_ts is not None and last_ts > first_ts:
                duration = last_ts - first_ts
                result["duration_sec"] = duration
                result["avg_bandwidth_mbps"] = (total_bytes * 8) / (duration * 1_000_000.0)

    except Exception as exc:
        return _fallback_capinfos(pcap_path, result, str(exc))

    return result


def _fallback_capinfos(pcap_path: str, result: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """Fallback to system capinfos binary if stdlib parser encounters pcapng or errors."""
    result["error"] = f"stdlib parse note: {reason}; attempting capinfos fallback"
    try:
        proc = subprocess.run(["capinfos", "-c", "-d", "-u", "-s", pcap_path], capture_output=True, text=True)
        if proc.returncode == 0:
            result["valid"] = True
            for line in proc.stdout.splitlines():
                if "Number of packets:" in line:
                    result["total_packets"] = int(line.split(":")[-1].strip().replace("k", "000"))
                elif "File size:" in line:
                    parts = line.split(":")[-1].strip().split()
                    if parts:
                        result["total_bytes"] = int(parts[0])
                elif "Capture duration:" in line:
                    parts = line.split(":")[-1].strip().split()
                    if parts:
                        result["duration_sec"] = float(parts[0])
    except Exception:
        pass
    return result

"""Workload schema, parser, and runtime placeholder binder (WP-3, D-8, D-9).

Defines per-node workload specification and secure placeholder resolution
for custom commands without str.format risks.
"""
import re
from dataclasses import dataclass
from typing import Dict, Any, List, Optional


VALID_WORKLOAD_KINDS = {
    "none",
    "iperf3_server",
    "iperf3_client",
    "mesh_ping",
    "custom_server",
    "custom_client"
}

ALLOWED_PLACEHOLDER_NAMES = {
    "server_ip",
    "client_ip",
    "node",
    "iface",
    "duration",
    "output_dir"
}

PLACEHOLDER_REGEX = re.compile(r"\{([a-z_]+)(?::([A-Za-z0-9_-]+))?\}")
ALL_BRACES_REGEX = re.compile(r"\{[^{}]*\}")


class UnknownPlaceholderError(ValueError):
    """Raised when an unknown, unresolvable, or malformed placeholder is encountered."""
    pass


class InvalidWorkloadError(ValueError):
    """Raised when a workload line is syntactically or semantically invalid."""
    pass


@dataclass
class WorkloadItem:
    node: str
    kind: str
    command: Optional[str] = None

    def to_line(self) -> str:
        if self.command:
            return f"workload:{self.node},{self.kind},{self.command}\n"
        return f"workload:{self.node},{self.kind}\n"


def parse_workload_line(line: str) -> WorkloadItem:
    """
    Parse a 'workload:<node>,<kind>[,<command>]' line into WorkloadItem.
    """
    if line.startswith("workload:"):
        line = line[len("workload:"):].strip()
    else:
        line = line.strip()

    parts = [p.strip() for p in line.split(",", 2)]
    if len(parts) < 2:
        raise InvalidWorkloadError(f"Invalid workload format '{line}': expected 'node,kind[,command]'.")

    node = parts[0]
    kind = parts[1]
    command = parts[2] if len(parts) >= 3 and parts[2] else None

    if not node:
        raise InvalidWorkloadError(f"Workload node name cannot be empty in '{line}'.")

    if kind not in VALID_WORKLOAD_KINDS:
        raise InvalidWorkloadError(
            f"Invalid workload kind '{kind}' for node '{node}'. Allowed kinds: {sorted(VALID_WORKLOAD_KINDS)}."
        )

    if command and ("\n" in command or "\r" in command):
        raise InvalidWorkloadError(f"Workload command for node '{node}' cannot contain newline characters.")

    if kind in ("custom_server", "custom_client"):
        if not command:
            raise InvalidWorkloadError(f"Workload kind '{kind}' on node '{node}' requires a non-empty command.")
    else:
        if command:
            raise InvalidWorkloadError(
                f"Workload kind '{kind}' on node '{node}' does not accept custom command (got '{command}')."
            )

    return WorkloadItem(node=node, kind=kind, command=command)


def bind_placeholders(cmd: str, context: Dict[str, Any]) -> str:
    """
    Bind placeholders in cmd using context dictionary with fail-closed security (D-9).
    
    Placeholder syntax:
      {<name>} or {<name>:<target>}
    
    Allowed names:
      - server_ip, client_ip, node, iface, duration, output_dir
    """
    if not cmd:
        return ""

    # 1. First find all brace groups and verify they strictly match PLACEHOLDER_REGEX
    raw_braces = ALL_BRACES_REGEX.findall(cmd)
    for brace_token in raw_braces:
        m = PLACEHOLDER_REGEX.fullmatch(brace_token)
        if not m:
            raise UnknownPlaceholderError(
                f"Unknown or invalid placeholder syntax '{brace_token}' in command '{cmd}'."
            )
        name = m.group(1)
        if name not in ALLOWED_PLACEHOLDER_NAMES:
            raise UnknownPlaceholderError(
                f"Forbidden or unknown placeholder '{name}' in token '{brace_token}'."
            )

    # 2. Substitution function
    def _repl(match: re.Match) -> str:
        name = match.group(1)
        target = match.group(2)

        if name == "node":
            val = context.get("node")
            if val is None:
                raise UnknownPlaceholderError("Context missing required key 'node'.")
            return str(val)

        elif name == "iface":
            val = context.get("iface")
            if val is None:
                raise UnknownPlaceholderError("Context missing required key 'iface'.")
            return str(val)

        elif name == "duration":
            val = context.get("duration")
            if val is None:
                raise UnknownPlaceholderError("Context missing required key 'duration'.")
            return str(val)

        elif name == "output_dir":
            val = context.get("output_dir")
            if val is None:
                raise UnknownPlaceholderError("Context missing required key 'output_dir'.")
            return str(val)

        elif name in ("server_ip", "client_ip"):
            # If target specified (e.g. {server_ip:s0}), look up by target
            node_ips = context.get("node_ips", {})
            if target:
                if target in node_ips and node_ips[target] is not None:
                    return str(node_ips[target])
                key = f"{name}:{target}"
                if key in context and context[key] is not None:
                    return str(context[key])
                raise UnknownPlaceholderError(
                    f"Unable to resolve IP for node '{target}' in placeholder '{{{name}:{target}}}'."
                )
            else:
                # Default server/client IP
                if name in context and context[name] is not None:
                    return str(context[name])
                raise UnknownPlaceholderError(f"Context missing default '{name}'.")

        raise UnknownPlaceholderError(f"Unhandled placeholder '{name}'.")

    return PLACEHOLDER_REGEX.sub(_repl, cmd)

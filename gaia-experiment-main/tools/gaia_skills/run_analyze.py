"""Orchestration and analysis skill engine (T-12 ~ T-15).
"""
import os
import sys
import json
import time
import shlex
import shutil
import argparse
import subprocess
from datetime import datetime
from typing import Dict, Any, Tuple, Optional


def _normalize_herdr_args(
    herdr_session: Optional[str] = None,
    runner_name: Optional[str] = None,
    herdr_workspace: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Normalize Herdr session and runner workspace names with backward-compatibility.

    - Session: Herdr session daemon (e.g. 'gaia-experiment')
    - Runner: Herdr runner workspace label (e.g. 'gaia-root-runner')
    """
    if herdr_workspace and not runner_name:
        # Legacy mapping: --herdr-workspace gaia-experiment --herdr-session gaia-root-runner
        actual_session = herdr_workspace
        actual_runner = herdr_session or "gaia-root-runner"
    elif runner_name and not herdr_session and not herdr_workspace:
        actual_session = None
        actual_runner = runner_name
    elif herdr_session:
        actual_session = herdr_session
        actual_runner = runner_name or "gaia-root-runner"
    else:
        actual_session = None
        actual_runner = None
    return actual_session, actual_runner


def _herdr_base_cmd(herdr_session: Optional[str] = None) -> list:
    """Construct base herdr command with optional --session."""
    cmd = ["herdr"]
    if herdr_session:
        cmd.extend(["--session", herdr_session])
    return cmd


def _evaluate_verdict(
    summary: Dict[str, Any],
    strict: bool,
    output_dir: str,
    final_rc: int = 0,
    extra_fields: Optional[Dict[str, Any]] = None
) -> Tuple[int, Dict[str, Any]]:
    """Unified helper for verdict evaluation and report construction (DRY)."""
    label = summary.get("label", "Unknown")
    is_valid = summary.get("valid", True)
    if strict and (label in ("Single-Path Fallback", "No Data", "No MPTCP Evidence") or not is_valid):
        rc = 4
    else:
        rc = final_rc

    res = {
        "valid": is_valid and (rc == 0),
        "rc": rc,
        "label": label,
        "output_dir": output_dir,
        "max_subflows": summary.get("metrics", {}).get("subflows", summary.get("max_subflows", 0)),
        "summary": summary,
    }
    if extra_fields:
        res.update(extra_fields)
    if rc == 4:
        res["hint"] = "Strict validity failure (e.g. Single-Path Fallback or No Data). Check kernel MPTCP configuration."
    return rc, res


def _save_runner_state(
    cwd: str,
    session: str,
    runner_name: str,
    pane_id: Optional[str],
    workspace: Optional[str] = None
):
    """Save runner session mapping in .mam/herdr_runners.json."""
    if not pane_id:
        return
    mam_dir = os.path.join(cwd, ".mam")
    os.makedirs(mam_dir, exist_ok=True)
    state_file = os.path.join(mam_dir, "herdr_runners.json")
    runners = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                runners = json.load(f)
        except Exception:
            runners = {}
    runners[runner_name] = {
        "session": session,
        "runner_name": runner_name,
        "workspace": workspace or session,  # backward compatibility field
        "pane_id": pane_id,
        "updated_at": datetime.now().isoformat(),
    }
    try:
        with open(state_file, "w") as f:
            json.dump(runners, f, indent=2)
    except Exception:
        pass


def _resolve_target_pane(
    herdr_session: Optional[str],
    target: str,
    workspace_root: Optional[str] = None
) -> str:
    """
    Resolve target session identifier to a specific herdr pane ID (format wXX:pY).
    1. Direct pane ID (e.g. w18:p1)
    2. Runner state file lookup (.mam/herdr_runners.json)
    3. Query herdr workspace list -> find matching label -> query pane list
    4. Query herdr agent list -> find matching agent name
    5. Fallback to target as given
    """
    import re
    if re.match(r"^w[0-9A-Za-z]+:p[0-9A-Za-z]+$", target):
        return target

    cwd = workspace_root or os.getcwd()
    state_file = os.path.join(cwd, ".mam", "herdr_runners.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                runners = json.load(f)
            if target in runners and "pane_id" in runners[target]:
                return runners[target]["pane_id"]
        except Exception:
            pass

    base_cmd = _herdr_base_cmd(herdr_session)

    # Try resolving via herdr workspace list + pane list
    try:
        ws_proc = subprocess.run(base_cmd + ["workspace", "list"], capture_output=True, text=True)
        if ws_proc.returncode == 0:
            ws_data = json.loads(ws_proc.stdout)
            workspaces = ws_data.get("result", {}).get("workspaces", [])
            target_ws_id = None
            for ws in workspaces:
                if ws.get("label") == target or ws.get("workspace_id") == target:
                    target_ws_id = ws.get("workspace_id")
                    break

            if target_ws_id:
                pane_proc = subprocess.run(base_cmd + ["pane", "list"], capture_output=True, text=True)
                if pane_proc.returncode == 0:
                    pane_data = json.loads(pane_proc.stdout)
                    for pane in pane_data.get("result", {}).get("panes", []):
                        if pane.get("workspace_id") == target_ws_id:
                            return pane.get("pane_id", target)
    except Exception:
        pass

    # Try resolving via herdr agent list
    try:
        agent_proc = subprocess.run(base_cmd + ["agent", "list"], capture_output=True, text=True)
        if agent_proc.returncode == 0:
            agent_data = json.loads(agent_proc.stdout)
            for ag in agent_data.get("result", {}).get("agents", []):
                if ag.get("name") == target or ag.get("agent") == target:
                    return ag.get("pane_id", target)
    except Exception:
        pass

    return target


def setup_herdr_runner(
    herdr_session: Optional[str] = None,
    runner_name: Optional[str] = None,
    workspace_root: Optional[str] = None,
    herdr_workspace: Optional[str] = None,
) -> Tuple[int, Dict[str, Any]]:
    """
    Initialize a dedicated root runner workspace in the specified Herdr session.
    """
    actual_session, actual_runner = _normalize_herdr_args(herdr_session, runner_name, herdr_workspace)
    if not actual_session:
        return 2, {
            "valid": False,
            "rc": 2,
            "error": "Herdr session name is required for setup (e.g. --herdr-session gaia-experiment).",
        }

    runner_label = actual_runner or "gaia-root-runner"

    if not shutil.which("herdr"):
        return 1, {
            "valid": False,
            "rc": 1,
            "error": "herdr CLI binary not found in PATH.",
        }

    cwd = workspace_root or os.getcwd()
    base_cmd = _herdr_base_cmd(actual_session)
    pane_id = None

    # 1. Check if workspace / runner already exists in the session
    try:
        ws_proc = subprocess.run(base_cmd + ["workspace", "list"], capture_output=True, text=True)
        if ws_proc.returncode == 0:
            ws_data = json.loads(ws_proc.stdout)
            for ws in ws_data.get("result", {}).get("workspaces", []):
                if ws.get("label") == runner_label:
                    ws_id = ws.get("workspace_id")
                    pane_proc = subprocess.run(base_cmd + ["pane", "list"], capture_output=True, text=True)
                    if pane_proc.returncode == 0:
                        pdata = json.loads(pane_proc.stdout)
                        for p in pdata.get("result", {}).get("panes", []):
                            if p.get("workspace_id") == ws_id:
                                pane_id = p.get("pane_id")
                                break
                    break
        if pane_id:
            _save_runner_state(cwd, actual_session, runner_label, pane_id)
            return 0, {
                "valid": True,
                "rc": 0,
                "herdr_session": actual_session,
                "runner_name": runner_label,
                "pane_id": pane_id,
                "status": "already_running",
                "message": f"Herdr runner '{runner_label}' (pane: {pane_id}) is already running in session '{actual_session}'.",
                "attach_command": f"herdr --session {actual_session} session attach {actual_session}",
                "guide": "Please attach to the session, run 'sudo su', and cd to the project workspace if not already done.",
            }
    except Exception:
        pass

    # 2. Create workspace / runner session
    try:
        create_cmd = base_cmd + ["workspace", "create", "--cwd", cwd, "--label", runner_label]
        proc = subprocess.run(create_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            create_cmd_alt = ["herdr", "workspace", "create", "--cwd", cwd, "--label", runner_label]
            proc = subprocess.run(create_cmd_alt, capture_output=True, text=True)
            if proc.returncode != 0:
                return 1, {
                    "valid": False,
                    "rc": 1,
                    "error": f"Failed to create herdr runner '{runner_label}' in session '{actual_session}': {proc.stderr or proc.stdout}",
                }
            else:
                try:
                    cdata = json.loads(proc.stdout)
                    pane_id = cdata.get("result", {}).get("root_pane", {}).get("pane_id")
                except Exception:
                    pass
        else:
            try:
                cdata = json.loads(proc.stdout)
                pane_id = cdata.get("result", {}).get("root_pane", {}).get("pane_id")
            except Exception:
                pass
    except Exception as e:
        return 1, {
            "valid": False,
            "rc": 1,
            "error": f"Failed to invoke herdr: {e}",
        }

    if not pane_id:
        pane_id = _resolve_target_pane(actual_session, runner_label, cwd)

    _save_runner_state(cwd, actual_session, runner_label, pane_id)

    return 0, {
        "valid": True,
        "rc": 0,
        "herdr_session": actual_session,
        "runner_name": runner_label,
        "pane_id": pane_id,
        "status": "created",
        "message": f"Herdr runner '{runner_label}' (pane: {pane_id}) created in session '{actual_session}'.",
        "attach_command": f"herdr --session {actual_session} session attach {actual_session}",
        "guide": "Please attach to the session, run 'sudo su', and cd to the project workspace. Once ready, run_analyze can delegate root emulation commands to this session.",
    }


def run_via_herdr(
    topo_file: str,
    xp_file: str,
    herdr_session: Optional[str] = None,
    runner_name: Optional[str] = None,
    output_dir: Optional[str] = None,
    skip_preflight: bool = False,
    allow_custom_cmd: bool = False,
    timeout: int = 300,
    herdr_workspace: Optional[str] = None,
) -> Tuple[int, str, Optional[str]]:
    """
    Delegate execution to root herdr runner workspace and wait for completion.
    Returns (exec_rc, actual_output_dir, error_msg).
    """
    actual_session, actual_runner = _normalize_herdr_args(herdr_session, runner_name, herdr_workspace)
    if not actual_session:
        return 1, (output_dir or ""), "Herdr session name is required for delegated execution."

    runner_label = actual_runner or "gaia-root-runner"

    if not shutil.which("herdr"):
        return 1, (output_dir or ""), "herdr CLI binary not found in PATH."

    out_path = output_dir or os.path.join("output", datetime.now().strftime("%Y%m%d-%H%M%S"))
    abs_out = os.path.abspath(out_path)
    os.makedirs(abs_out, exist_ok=True)

    summary_file = os.path.join(abs_out, "summary.json")
    rc_file = os.path.join(abs_out, "exec_rc")

    # H-2: Clean up pre-existing marker & summary files before injecting command
    for f_path in (rc_file, summary_file):
        if os.path.exists(f_path):
            try:
                os.remove(f_path)
            except Exception:
                pass

    venv_py = os.path.join(os.getcwd(), ".venv", "bin", "python")
    py_bin = venv_py if os.path.exists(venv_py) else sys.executable

    # H-1: Quote all command paths with shlex.quote
    quoted_py = shlex.quote(py_bin)
    quoted_topo = shlex.quote(topo_file)
    quoted_xp = shlex.quote(xp_file)
    quoted_out = shlex.quote(abs_out)
    quoted_rc_file = shlex.quote(rc_file)

    exec_cmd = f"{quoted_py} cli.py exec -t {quoted_topo} -x {quoted_xp} -o {quoted_out}"
    if skip_preflight:
        exec_cmd += " --skip-preflight"
    if allow_custom_cmd:
        exec_cmd += " --allow-custom-cmd"

    # H-3: Wrap in curly braces group and capture exit code to exec_rc file
    full_cmd = f"{{ {exec_cmd}; echo rc=$? > {quoted_rc_file}; }}"

    base_cmd = _herdr_base_cmd(actual_session)
    target_pane = _resolve_target_pane(actual_session, runner_label)

    try:
        errors = []
        # Primary: pane run <target_pane> <full_cmd>
        proc = subprocess.run(base_cmd + ["pane", "run", target_pane, full_cmd], capture_output=True, text=True)
        if proc.returncode != 0:
            errors.append(f"pane run: {proc.stderr.strip() or proc.stdout.strip()}")
            # Fallback 1: pane send-text + send-keys Enter
            p_text = subprocess.run(base_cmd + ["pane", "send-text", target_pane, full_cmd], capture_output=True, text=True)
            p_keys = subprocess.run(base_cmd + ["pane", "send-keys", target_pane, "Enter"], capture_output=True, text=True)
            if p_text.returncode != 0 or p_keys.returncode != 0:
                errors.append(f"pane send-text/keys: {p_text.stderr.strip() or p_keys.stderr.strip()}")
                # Fallback 2: agent send-keys
                p_ag = subprocess.run(base_cmd + ["agent", "send-keys", runner_label, full_cmd, "Enter"], capture_output=True, text=True)
                if p_ag.returncode != 0:
                    errors.append(f"agent send-keys: {p_ag.stderr.strip() or p_ag.stdout.strip()}")
                    # Fallback 3: agent prompt
                    p_prompt = subprocess.run(base_cmd + ["agent", "prompt", runner_label, full_cmd], capture_output=True, text=True)
                    if p_prompt.returncode != 0:
                        errors.append(f"agent prompt: {p_prompt.stderr.strip() or p_prompt.stdout.strip()}")
                        return 1, abs_out, f"Failed to send execution command to herdr target '{runner_label}' / '{target_pane}': {'; '.join(errors)}"

        # Poll for exec_rc marker file
        start_time = time.time()
        while time.time() - start_time < timeout:
            if os.path.exists(rc_file) and os.path.getsize(rc_file) > 0:
                time.sleep(0.5)
                try:
                    with open(rc_file, "r") as f:
                        rc_content = f.read().strip()
                    if "rc=" in rc_content:
                        captured_rc = int(rc_content.split("rc=")[1].strip())
                    else:
                        captured_rc = int(rc_content)
                    return captured_rc, abs_out, None
                except Exception as e:
                    return 1, abs_out, f"Failed to parse exec_rc: {e}"
            time.sleep(1.0)

        return 1, abs_out, f"Execution timed out after {timeout}s waiting for {rc_file} in herdr runner '{runner_label}' (session: '{actual_session}', pane: '{target_pane}')."

    except Exception as e:
        return 1, abs_out, f"Failed to execute via herdr: {e}"


def run_and_analyze(
    topo_file: Optional[str] = None,
    xp_file: Optional[str] = None,
    output_dir: Optional[str] = None,
    dry_run: bool = False,
    analyze_only: bool = False,
    run_dir: Optional[str] = None,
    skip_preflight: bool = False,
    strict: bool = False,
    allow_custom_cmd: bool = False,
    herdr_session: Optional[str] = None,
    runner_name: Optional[str] = None,
    herdr_workspace: Optional[str] = None,
    setup: bool = False,
    timeout: int = 300,
    workspace_root: Optional[str] = None,
) -> Tuple[int, Dict[str, Any]]:
    """
    Orchestrate experiment execution and analysis verdict reporting.
    Returns (exit_code, result_dict).
    Exit codes:
      0: Success / Valid experiment
      1: General runtime error
      2: Static validation error (verify failed) / parameter error
      3: Preflight reachability failure
      4: Strict validity failure (Single-Path Fallback, No Data)
    """
    actual_session, actual_runner = _normalize_herdr_args(herdr_session, runner_name, herdr_workspace)

    if runner_name and not actual_session:
        return 2, {
            "valid": False,
            "rc": 2,
            "error": "--runner-name requires --herdr-session.",
        }

    # 0. Setup Mode
    if setup:
        if not actual_session:
            return 2, {
                "valid": False,
                "rc": 2,
                "error": "--setup requires --herdr-session (e.g. --herdr-session gaia-experiment).",
            }
        return setup_herdr_runner(actual_session, actual_runner, workspace_root=workspace_root)

    # 1. Analyze-Only Mode (T-13b, C-3)
    if analyze_only:
        target_dir = run_dir or output_dir
        if not target_dir or not os.path.exists(target_dir):
            return (
                4 if strict else 0,
                {
                    "valid": False,
                    "rc": 4 if strict else 0,
                    "label": "No Data",
                    "error": f"Run directory '{target_dir}' does not exist.",
                    "hint": "Ensure output directory exists and contains experiment artifacts.",
                },
            )

        summary_file = os.path.join(target_dir, "summary.json")
        if not os.path.exists(summary_file):
            return (
                4 if strict else 0,
                {
                    "valid": False,
                    "rc": 4 if strict else 0,
                    "label": "No Data",
                    "output_dir": target_dir,
                    "error": f"summary.json not found in '{target_dir}'.",
                    "hint": "Experiment run directory does not contain summary.json.",
                },
            )

        try:
            with open(summary_file, "r") as f:
                summary = json.load(f)
        except Exception as e:
            return (
                1,
                {
                    "valid": False,
                    "rc": 1,
                    "label": "No Data",
                    "error": f"Failed to read summary.json: {e}",
                },
            )

        return _evaluate_verdict(summary, strict=strict, output_dir=target_dir)

    allow_custom = allow_custom_cmd

    # 2. Dry-Run Mode (T-13, G-5)
    if dry_run:
        if not topo_file or not xp_file:
            return 2, {"valid": False, "rc": 2, "error": "--dry-run requires both --topo and --xp."}

        out_path = output_dir or os.path.join("output", f"dryrun-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        planned_cmd = [sys.executable, "cli.py", "exec", "-t", topo_file, "-x", xp_file, "-o", out_path]
        if skip_preflight:
            planned_cmd.append("--skip-preflight")
        if allow_custom:
            planned_cmd.append("--allow-custom-cmd")

        res_dry = {
            "valid": True,
            "rc": 0,
            "dry_run": True,
            "planned_command": " ".join(planned_cmd),
            "command_args": planned_cmd,
            "expected_output_dir": out_path,
            "topo_file": topo_file,
            "xp_file": xp_file,
        }
        if actual_session:
            res_dry["delegation"] = {
                "herdr_session": actual_session,
                "runner_name": actual_runner,
                "herdr_workspace": actual_session,
            }

        return 0, res_dry

    # 3. Herdr Delegated Execution Mode
    if actual_session:
        if not topo_file or not xp_file:
            return 2, {"valid": False, "rc": 2, "error": "Execution requires both --topo and --xp."}

        exec_rc, out_path, err_msg = run_via_herdr(
            topo_file=topo_file,
            xp_file=xp_file,
            herdr_session=actual_session,
            runner_name=actual_runner,
            output_dir=output_dir,
            skip_preflight=skip_preflight,
            allow_custom_cmd=allow_custom,
            timeout=timeout,
        )
        if exec_rc != 0:
            hint = "Check herdr runner session status, root permissions, and experiment configuration."
            if exec_rc == 2:
                hint = "Static verification failed (S2). Verify syntax with 'cli.py verify'."
            elif exec_rc == 3:
                hint = "Preflight reachability gate failed (S3). Check reachability.json in output dir."

            return exec_rc, {
                "valid": False,
                "rc": exec_rc,
                "output_dir": out_path,
                "error": err_msg or f"Execution failed with rc={exec_rc}",
                "hint": hint,
            }

        summary_file = os.path.join(out_path, "summary.json")
        if os.path.exists(summary_file):
            try:
                with open(summary_file, "r") as f:
                    summary = json.load(f)
            except Exception as e:
                summary = {"label": "No Data", "valid": False, "error": str(e)}
        else:
            summary = {"label": "No Data", "valid": False}

        extra = {
            "herdr_session": actual_session,
            "runner_name": actual_runner,
            "herdr_workspace": actual_session,
        }
        return _evaluate_verdict(summary, strict=strict, output_dir=out_path, final_rc=0, extra_fields=extra)

    # 4. Standard Direct Subprocess Execution Mode (T-12, T-12b, T-14)
    if not topo_file or not xp_file:
        return 2, {"valid": False, "rc": 2, "error": "Execution requires both --topo and --xp."}

    out_path = output_dir or os.path.join("output", datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out_path, exist_ok=True)

    cmd = [sys.executable, "cli.py", "exec", "-t", topo_file, "-x", xp_file, "-o", out_path]
    if skip_preflight:
        cmd.append("--skip-preflight")
    if allow_custom:
        cmd.append("--allow-custom-cmd")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    exec_rc = proc.returncode

    if exec_rc != 0:
        hint = "Check runtime logs."
        if exec_rc == 2:
            hint = "Static verification failed (S2). Verify syntax with 'cli.py verify'."
        elif exec_rc == 3:
            hint = "Preflight reachability gate failed (S3). Check reachability.json in output dir."

        return exec_rc, {
            "valid": False,
            "rc": exec_rc,
            "output_dir": out_path,
            "error": proc.stderr or proc.stdout,
            "hint": hint,
        }

    summary_file = os.path.join(out_path, "summary.json")
    if os.path.exists(summary_file):
        try:
            with open(summary_file, "r") as f:
                summary = json.load(f)
        except Exception as e:
            summary = {"label": "No Data", "valid": False, "error": str(e)}
    else:
        summary = {"label": "No Data", "valid": False}

    return _evaluate_verdict(summary, strict=strict, output_dir=out_path, final_rc=0)


def main():
    parser = argparse.ArgumentParser(description="GAIA Run & Analyze Skill (T-12 ~ T-15)")
    parser.add_argument("-t", "--topo", help="Path to topology configuration file")
    parser.add_argument("-x", "--xp", help="Path to experiment scenario configuration file")
    parser.add_argument("-o", "--output-dir", help="Output directory for artifacts")
    parser.add_argument("--dry-run", action="store_true", help="Print planned execution command without running")
    parser.add_argument("--analyze-only", action="store_true", help="Analyze existing output run directory without running Mininet")
    parser.add_argument("--run-dir", help="Path to run directory for --analyze-only")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip preflight mesh ping reachability test")
    parser.add_argument("--strict", action="store_true", help="Fail with rc=4 on single-path fallback or no data")
    parser.add_argument("--allow-custom-cmd", action="store_true", help="Explicitly permit execution of custom workload shell commands (S-4)")
    parser.add_argument("--herdr-session", "--session", help="Herdr session daemon name (e.g. gaia-experiment)")
    parser.add_argument("--runner-name", "--runner-workspace", "--runner-label", "--runner", default=None, help="Herdr runner workspace label (default: gaia-root-runner)")
    # Backward compatibility alias
    parser.add_argument("--herdr-workspace", "--workspace", help="[Legacy alias for --herdr-session] Herdr session daemon name")
    parser.add_argument("--setup", action="store_true", help="Initialize herdr runner workspace in specified session")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds for delegated herdr execution (default: 300)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON result")

    args = parser.parse_args()

    try:
        rc, result = run_and_analyze(
            topo_file=args.topo,
            xp_file=args.xp,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
            analyze_only=args.analyze_only,
            run_dir=args.run_dir,
            skip_preflight=args.skip_preflight,
            strict=args.strict,
            allow_custom_cmd=args.allow_custom_cmd,
            herdr_session=args.herdr_session,
            runner_name=args.runner_name,
            herdr_workspace=args.herdr_workspace,
            setup=args.setup,
            timeout=args.timeout,
        )
    except Exception as e:
        rc = 1
        result = {
            "valid": False,
            "rc": 1,
            "error": f"Unhandled error in run_and_analyze: {e}",
        }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if rc == 0:
            if args.setup:
                print(f"Herdr Setup Complete: {result.get('message')}")
                print(f"Attach Command: {result.get('attach_command')}")
                print(f"Guide: {result.get('guide')}")
            else:
                print(f"Run & Analyze complete (rc=0): label={result.get('label')}, output_dir={result.get('output_dir')}")
        else:
            print(f"Run & Analyze failed (rc={rc}): label={result.get('label')}, error={result.get('error')}", file=sys.stderr)
            if "hint" in result:
                print(f"Hint: {result['hint']}", file=sys.stderr)

    sys.exit(rc)


if __name__ == "__main__":
    main()

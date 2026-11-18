import json
import os
import sys
import argparse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.netmodel import NetModel
from core.parameter import Parameter
from core.naming import validate_config_name
from tools.gaia_skills.scenario_gen import generate_scenario, parse_scenario_file
from tools.gaia_skills.spec import ScenarioSpec, WorkloadSpec

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
CONFIG_TOPO_DIR = os.path.join(BASE_DIR, "config", "topo")
CONFIG_XP_DIR = os.path.join(BASE_DIR, "config", "xp")


class WebUIRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.path = "/index.html"
            return super().do_GET()

        if path == "/api/configs":
            self.handle_list_configs()
            return

        if path == "/api/configs/xp":
            self.handle_list_xp_configs()
            return

        if path.startswith("/api/configs/xp/"):
            filename = path.replace("/api/configs/xp/", "")
            self.handle_get_xp_config(filename)
            return

        if path.startswith("/api/configs/"):
            filename = path.replace("/api/configs/", "")
            self.handle_get_config(filename)
            return

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # S-2: Check Origin / Referer against bound host
        origin = self.headers.get("Origin") or self.headers.get("Referer")
        if origin:
            parsed_origin = urlparse(origin)
            allowed_hosts = {"localhost", "127.0.0.1"}
            bound_host = getattr(self.server, "bound_host", "127.0.0.1")
            if bound_host:
                allowed_hosts.add(bound_host)
            if parsed_origin.hostname not in allowed_hosts:
                self.send_error_json({"error": "Forbidden: Cross-origin request rejected (S-2)"}, status=403)
                return

        # S-2: Enforce Content-Type application/json
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            self.send_error_json({"error": "Content-Type must be application/json (S-2)"}, status=400)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except Exception as e:
            self.send_error_json({"error": f"Invalid JSON body: {str(e)}"}, status=400)
            return

        if path == "/api/validate":
            self.handle_validate(data)
            return
        elif path == "/api/generate":
            self.handle_generate(data)
            return
        elif path == "/api/export/scenario":
            self.handle_export_scenario(data)
            return
        else:
            self.send_error_json({"error": "Endpoint not found"}, status=404)

    def handle_validate(self, data):
        try:
            model = NetModel.from_dict(data)
            errors = model.validate()
            unreachable_pairs = model.get_unreachable_router_pairs()
            result = {
                "valid": len(errors) == 0,
                "errors": errors,
                "unreachable_pairs": unreachable_pairs,
                "model": model.to_dict(),
                "routes": model.compute_routes()
            }
            self.send_json(result)
        except Exception as e:
            self.send_json({"valid": False, "errors": [str(e)], "unreachable_pairs": []}, status=400)

    def handle_generate(self, data):
        try:
            model = NetModel.from_dict(data)
            errors = model.validate()
            if errors:
                self.send_json({"success": False, "errors": errors}, status=400)
                return

            os.makedirs(CONFIG_TOPO_DIR, exist_ok=True)
            custom_name = data.get("filename")
            force = bool(data.get("force", False))

            if custom_name:
                try:
                    filename = validate_config_name(custom_name)
                except ValueError as ve:
                    self.send_json({"success": False, "error": str(ve), "errors": [str(ve)]}, status=400)
                    return
                target_path = os.path.join(CONFIG_TOPO_DIR, filename)
                if os.path.exists(target_path) and not force:
                    self.send_json({
                        "error": "exists",
                        "filename": filename,
                        "message": f"Topology '{filename}' already exists. Use force=True to overwrite."
                    }, status=409)
                    return
            else:
                # Find representative link for filename
                min_bw = 999999
                rep_delay, rep_qs, rep_bw, rep_loss = 15, 1000, 100, 0
                for l in model.links:
                    if l.bandwidth < min_bw:
                        min_bw = l.bandwidth
                        rep_delay, rep_qs, rep_bw, rep_loss = int(l.delay), int(l.queue_size), int(l.bandwidth), int(l.loss)
                filename = f"topo-{rep_delay}_{rep_qs}_{rep_bw}_{rep_loss}"
                target_path = os.path.join(CONFIG_TOPO_DIR, filename)
                idx = 1
                original_filename = filename
                while os.path.exists(target_path) and not force:
                    filename = f"{original_filename}-{idx}"
                    target_path = os.path.join(CONFIG_TOPO_DIR, filename)
                    idx += 1

            config_text = model.to_config_text()
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(config_text)

            self.send_json({
                "success": True,
                "filename": filename,
                "path": target_path,
                "content": config_text
            })
        except Exception as e:
            self.send_json({"success": False, "error": str(e)}, status=500)

    def handle_export_scenario(self, data):
        """Export ScenarioSpec JSON to config/xp/<name> via gaia-scenario-gen engine (WP-5)."""
        try:
            scenario_name = data.get("name")
            if not scenario_name:
                self.send_json({"success": False, "error": "Scenario name is required."}, status=400)
                return

            try:
                name = validate_config_name(scenario_name)
            except ValueError as ve:
                self.send_json({"success": False, "error": str(ve), "errors": [str(ve)]}, status=400)
                return

            force = bool(data.get("force", False))
            target_path = os.path.join(CONFIG_XP_DIR, name)
            if os.path.exists(target_path) and not force:
                self.send_json({
                    "error": "exists",
                    "filename": name,
                    "message": f"Scenario '{name}' already exists. Use force=True to overwrite."
                }, status=409)
                return

            spec = ScenarioSpec.from_dict(data)
            exit_code, out_path_or_err, report = generate_scenario(spec, output_dir=CONFIG_XP_DIR, force=force)
            if exit_code != 0:
                self.send_json({
                    "success": False,
                    "error": out_path_or_err,
                    "report": report
                }, status=400)
                return

            content = ""
            if os.path.exists(out_path_or_err):
                with open(out_path_or_err, "r", encoding="utf-8") as f:
                    content = f.read()

            self.send_json({
                "success": True,
                "filename": name,
                "path": out_path_or_err,
                "content": content,
                "spec": spec.to_dict(),
                "report": report
            })
        except Exception as e:
            self.send_json({"success": False, "error": str(e)}, status=500)

    def handle_list_configs(self):
        try:
            os.makedirs(CONFIG_TOPO_DIR, exist_ok=True)
            files = [f for f in os.listdir(CONFIG_TOPO_DIR) if not f.startswith(".")]
            self.send_json({"configs": sorted(files)})
        except Exception as e:
            self.send_json({"error": str(e)}, status=500)

    def handle_list_xp_configs(self):
        """List scenario configuration files (D-11)."""
        try:
            os.makedirs(CONFIG_XP_DIR, exist_ok=True)
            files = [f for f in os.listdir(CONFIG_XP_DIR) if not f.startswith(".")]
            self.send_json({"configs": sorted(files)})
        except Exception as e:
            self.send_json({"error": str(e)}, status=500)

    def handle_get_config(self, filename):
        try:
            safe_filename = validate_config_name(filename)
        except ValueError:
            self.send_error_json({"error": "Config file not found"}, status=404)
            return

        target_path = os.path.abspath(os.path.join(CONFIG_TOPO_DIR, safe_filename))
        config_dir_abs = os.path.abspath(CONFIG_TOPO_DIR)

        if not target_path.startswith(config_dir_abs) or not os.path.exists(target_path):
            self.send_error_json({"error": "Config file not found"}, status=404)
            return

        try:
            param = Parameter(target_path)
            model = NetModel.from_parameter(param)
            self.send_json({
                "filename": safe_filename,
                "model": model.to_dict(),
                "content": model.to_config_text()
            })
        except ValueError as e:
            self.send_json({"error": str(e)}, status=400)
        except Exception as e:
            self.send_json({"error": f"Failed to parse config: {str(e)}"}, status=500)

    def handle_get_xp_config(self, filename):
        """Get parsed scenario specification (D-11)."""
        try:
            safe_filename = validate_config_name(filename)
        except ValueError:
            self.send_error_json({"error": "Scenario config file not found"}, status=404)
            return

        target_path = os.path.abspath(os.path.join(CONFIG_XP_DIR, safe_filename))
        config_dir_abs = os.path.abspath(CONFIG_XP_DIR)

        if not target_path.startswith(config_dir_abs) or not os.path.exists(target_path):
            self.send_error_json({"error": "Scenario config file not found"}, status=404)
            return

        try:
            spec = parse_scenario_file(target_path, name=safe_filename)
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.send_json({
                "filename": safe_filename,
                "spec": spec.to_dict(),
                "content": content
            })
        except ValueError as e:
            self.send_json({"error": str(e)}, status=400)
        except Exception as e:
            self.send_json({"error": f"Failed to parse scenario config: {str(e)}"}, status=500)

    def send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, data, status=400):
        self.send_json(data, status=status)


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


def run_server(port=8899, server_ready_event=None, server_holder=None, host="127.0.0.1"):
    # S-1: Bind default to 127.0.0.1 for local isolation
    if isinstance(port, str) and not port.isdigit() and (port in ("127.0.0.1", "0.0.0.0", "localhost") or "." in port):
        host, port = port, 8899
    server_address = (host, int(port))
    httpd = ReusableHTTPServer(server_address, WebUIRequestHandler)
    httpd.bound_host = host
    actual_port = httpd.server_port
    if server_holder is not None:
        server_holder["port"] = actual_port
        server_holder["httpd"] = httpd
    if server_ready_event is not None:
        server_ready_event.set()
    print(f"GAIA Visual Topology Editor Web UI running at http://{host}:{actual_port}")
    if host == "0.0.0.0" or host == "":
        print("WARNING: WebUI is bound to all network interfaces. Exercise caution.")
    httpd.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GAIA Visual Topology & Scenario Web UI Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", "-p", type=int, default=8899, help="Bind port (default: 8899)")
    args = parser.parse_args()
    run_server(port=args.port, host=args.host)

"""
TEST INFRASTRUCTURE ONLY -- not for production use.

Implements just enough of Piston's HTTP API (/api/v2/runtimes,
/api/v2/execute) to let piston_client.py and run_sandbox.py be validated
against REAL problem data with REAL code execution, without needing Docker
(which isn't available in this environment).

This is backed by subprocess.run(), which means it has the SAME safety
caveats as a bare subprocess that were flagged earlier (no OS-level
isolation, no true sandboxing) -- it exists purely to prove the sandbox
LOGIC is correct (sampling, ranking, error formatting, whitespace
normalization) against real data. For actual production use, point
PISTON_BASE_URL at a real self-hosted Piston Docker container instead --
see README.md for that setup. Nothing about swapping back needs to change
in piston_client.py or run_sandbox.py, since they only depend on the API
shape, not on how it's implemented behind that URL.
"""

import subprocess
import tempfile
import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class PistonMockHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silence default request logging

    def do_GET(self):
        if self.path == "/api/v2/runtimes":
            self._send_json([{"language": "python", "version": "3.11.0", "aliases": ["python3"]}])
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/api/v2/execute":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        code = body["files"][0]["content"]
        stdin_text = body.get("stdin", "")
        run_timeout_ms = body.get("run_timeout", 5000)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp_path = f.name

        try:
            result = subprocess.run(
                ["python3", tmp_path],
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=run_timeout_ms / 1000.0,
            )
            self._send_json({
                "run": {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "code": result.returncode,
                    "signal": None,
                }
            })
        except subprocess.TimeoutExpired:
            self._send_json({
                "run": {"stdout": "", "stderr": "timed out", "code": -1, "signal": "SIGKILL"}
            })
        finally:
            os.unlink(tmp_path)

    def _send_json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(port: int = 2000):
    server = HTTPServer(("localhost", port), PistonMockHandler)
    print(f"[mock piston] serving on http://localhost:{port} (test infra only, not production)")
    server.serve_forever()


if __name__ == "__main__":
    run_server()

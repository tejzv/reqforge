from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from reqforge.pipeline import ForgeOptions, run_pipeline, write_generated_files
from reqforge.probe import ProbeConfig


class _LiveApiHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.headers.get("Authorization") != "Bearer e2e_secret":
            body = json.dumps({"error": "missing auth"}).encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/v1/projects/12":
            body = json.dumps({"id": 12, "name": "ReqForge", "model_config": "v1"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()


class TestEndToEndSmokeExecution(unittest.TestCase):
    server: ThreadingHTTPServer
    thread: threading.Thread
    base_url: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _LiveApiHandler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_generated_smoke_scripts_execute_against_live_server(self) -> None:
        curl_input = (
            f"curl '{self.base_url}/v1/projects/12' "
            "-H 'Authorization: Bearer e2e_secret' "
            "-H 'sec-ch-ua: \"Chromium\"' "
            "-H 'user-agent: Mozilla/5.0' "
            "-b '_ga=GA1.2; _fbp=fb.1'"
        )
        result, generated = run_pipeline(
            curl_input,
            options=ForgeOptions(
                target="all",
                client_name="LiveE2E",
                probe=True,
                probe_config=ProbeConfig(transport="httpx", max_probes=30),
            ),
        )
        self.assertEqual(len(result.endpoints), 1)
        ep = result.endpoints[0]
        self.assertEqual(set(ep.required_headers.keys()), {"Authorization"})
        self.assertEqual(ep.required_cookies, {})
        self.assertEqual(ep.response_model_name, "GetProjectResponse")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            write_generated_files(root, generated)

            env = dict(os.environ)
            env["API_BEARER_TOKEN"] = "e2e_secret"
            env["API_BASE_URL"] = self.base_url

            py_proc = subprocess.run(
                [sys.executable, "smoke_test.py"],
                cwd=root / "python",
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(py_proc.returncode, 0, py_proc.stderr)
            self.assertIn("OK get_project: GetProjectResponse", py_proc.stdout)

            js_proc = subprocess.run(
                ["node", "smoke_test.mjs"],
                cwd=root / "javascript",
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(js_proc.returncode, 0, js_proc.stderr)
            self.assertIn("OK getProject", js_proc.stdout)

            ts_proc = subprocess.run(
                ["node", "--experimental-strip-types", "smoke_test.ts"],
                cwd=root / "typescript",
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(ts_proc.returncode, 0, ts_proc.stderr)
            self.assertIn("OK getProject", ts_proc.stdout)

    def test_curl_comment_skipping_and_empty_body_handling(self) -> None:
        raw = (
            "# pasted dev tools command\n"
            "curl 'https://api.example.com/v1/empty' -X POST -d ''"
        )
        result, _ = run_pipeline(raw, options=ForgeOptions(target="python"))
        self.assertEqual(result.endpoints[0].request_body_kind, "none")

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from reqforge.pipeline import ForgeOptions, run_pipeline
from reqforge.probe import ProbeConfig
from reqforge.utils import parse_cookie_header


class _MockTargetHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        auth = self.headers.get("Authorization", "")
        custom = self.headers.get("X-Client-Id", "")
        cookies = parse_cookie_header(self.headers.get("Cookie", ""))

        if self.path == "/api/strict":
            if auth != "Bearer ok_token" or custom != "visor" or cookies.get("sid") != "sess_ok":
                self._send_json(401, {"error": "unauthorized"})
                return
            self._send_json(200, {"status": "ok", "items": [{"id": 1, "name": "alpha"}]})
            return

        if self.path == "/api/soft-block":
            if auth != "Bearer ok_token":
                self._send_json(200, {"error": "missing token", "code": 401})
                return
            self._send_json(200, {"user": {"id": 9, "plan": "pro"}, "active": True})
            return

        if self.path == "/api/html-redirect":
            if self.headers.get("X-Api-Version") != "2":
                html = b"<html><body>Please login</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            self._send_json(200, {"ok": True, "version": 2})
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        if self.path == "/api/echo":
            if self.headers.get("X-Secret") != "yes":
                self._send_json(403, {"error": "forbidden"})
                return
            self._send_json(201, {"received": body, "ok": True, "count": 5})
            return
        self._send_json(404, {"error": "not found"})


class TestLiveProbe(unittest.TestCase):
    server: ThreadingHTTPServer
    thread: threading.Thread
    base_url: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockTargetHandler)
        port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_ddmin_isolates_minimal_headers_and_cookies(self) -> None:
        curl_cmd = (
            f"curl '{self.base_url}/api/strict' "
            "-H 'Authorization: Bearer ok_token' "
            "-H 'X-Client-Id: visor' "
            "-H 'User-Agent: Mozilla/5.0' "
            "-H 'sec-ch-ua: \"Chromium\"' "
            "-H 'sec-fetch-mode: cors' "
            "-H 'sec-fetch-site: same-origin' "
            "-H 'Origin: https://app.example.com' "
            "-H 'Referer: https://app.example.com/' "
            "-H 'Accept-Language: en-US' "
            "-H 'DNT: 1' "
            "-b '_ga=GA1; _fbp=fb1; _gid=gid1; sid=sess_ok; prefs=dark; _hj=1'"
        )
        result, _ = run_pipeline(
            curl_cmd,
            options=ForgeOptions(
                target="python",
                probe=True,
                probe_config=ProbeConfig(transport="httpx", max_probes=60),
            ),
        )
        self.assertEqual(len(result.endpoints), 1)
        ep = result.endpoints[0]
        self.assertEqual(set(ep.required_headers.keys()), {"Authorization", "X-Client-Id"})
        self.assertEqual(set(ep.required_cookies.keys()), {"sid"})
        self.assertIsNotNone(ep.probe_report)
        assert ep.probe_report is not None
        self.assertTrue(ep.probe_report.baseline_matched)
        self.assertLess(ep.probe_report.total_probes, 25)
        self.assertEqual(ep.response_model_name, "GetStrictResponse")

    def test_soft_200_error_and_html_mismatch_are_rejected(self) -> None:
        curl_cmd = (
            f"curl '{self.base_url}/api/soft-block' "
            "-H 'Authorization: Bearer ok_token' "
            "-H 'User-Agent: Mozilla/5.0' "
            "-H 'DNT: 1'\n"
            f"curl '{self.base_url}/api/html-redirect' "
            "-H 'X-Api-Version: 2' "
            "-H 'Accept-Language: en-US'"
        )
        result, _ = run_pipeline(
            curl_cmd,
            options=ForgeOptions(
                target="python",
                probe=True,
                probe_config=ProbeConfig(transport="httpx"),
            ),
        )
        self.assertEqual(len(result.endpoints), 2)
        self.assertEqual(set(result.endpoints[0].required_headers.keys()), {"Authorization"})
        self.assertEqual(set(result.endpoints[1].required_headers.keys()), {"X-Api-Version"})

    def test_post_curl_probe_infers_response_schema(self) -> None:
        curl_cmd = (
            f"curl '{self.base_url}/api/echo' "
            "-H 'Content-Type: application/json' "
            "-H 'X-Secret: yes' "
            "-H 'sec-fetch-dest: empty' "
            "--data-raw '{\"hello\":\"world\"}'"
        )
        result, generated = run_pipeline(
            curl_cmd,
            options=ForgeOptions(
                target="python",
                probe=True,
                probe_config=ProbeConfig(transport="httpx"),
            ),
        )
        ep = result.endpoints[0]
        self.assertEqual(ep.response_status, 201)
        self.assertEqual(ep.response_model_name, "CreateEchoResponse")
        models_py = next(f.content for f in generated["python"] if f.path.endswith("models.py"))
        self.assertIn("class CreateEchoResponse", models_py)

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from pathlib import Path

from reqforge.cli import main as cli_main
from reqforge.server import create_server


class TestCliAndServer(unittest.TestCase):
    def test_cli_analyze_generate_and_export_web(self) -> None:
        sample_curl = (
            "curl 'https://api.example.com/v1/items/99?limit=5' "
            "-H 'X-Api-Key: secret_key_abc' "
            "-H 'sec-fetch-mode: cors'"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            in_file = tmp / "req.curl"
            in_file.write_text(sample_curl, encoding="utf-8")

            rc_analyze = cli_main(["analyze", str(in_file), "--json"])
            self.assertEqual(rc_analyze, 0)

            out_dir = tmp / "sdks"
            rc_gen = cli_main(
                ["generate", str(in_file), "--target", "all", "--out", str(out_dir)]
            )
            self.assertEqual(rc_gen, 0)
            self.assertTrue((out_dir / "python" / "smoke_test.py").is_file())
            self.assertTrue((out_dir / "typescript" / "client.ts").is_file())
            self.assertTrue((out_dir / "javascript" / "client.mjs").is_file())

            web_out = tmp / "web_bundle"
            rc_web = cli_main(["export-web", "--out", str(web_out)])
            self.assertEqual(rc_web, 0)
            zip_path = web_out / "reqforge_core.zip"
            self.assertTrue(zip_path.is_file())
            with zipfile.ZipFile(zip_path) as zf:
                names = set(zf.namelist())
                self.assertIn("reqforge/pipeline.py", names)
                self.assertIn("reqforge/ingest/curl.py", names)

    def test_local_web_server_endpoints(self) -> None:
        server = create_server(host="127.0.0.1", port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                health = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(health["status"], "ok")
                self.assertTrue(health["capabilities"]["liveProbe"])

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                html = resp.read().decode("utf-8")
                self.assertIn("ReqForge Studio", html)

            req_body = json.dumps(
                {
                    "input": "curl 'https://api.example.com/v1/ping' -H 'X-Api-Key: abc123456'",
                    "target": "typescript",
                }
            ).encode("utf-8")
            http_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/forge",
                data=req_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(http_req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                payload = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(payload["analysis"]["kept_endpoints"], 1)
                self.assertIn("typescript", payload["files"])
        finally:
            server.shutdown()
            server.server_close()

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
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

            buf_analyze = io.StringIO()
            with contextlib.redirect_stdout(buf_analyze):
                rc_analyze = cli_main(["analyze", str(in_file), "--json"])
            self.assertEqual(rc_analyze, 0)
            analyzed = json.loads(buf_analyze.getvalue())
            self.assertEqual(analyzed["kept_endpoints"], 1)
            self.assertEqual(analyzed["endpoints"][0]["operation_id"], "get_item")

            out_dir = tmp / "sdks"
            buf_gen = io.StringIO()
            with contextlib.redirect_stdout(buf_gen):
                rc_gen = cli_main(
                    ["generate", str(in_file), "--target", "all", "--out", str(out_dir)]
                )
            self.assertEqual(rc_gen, 0)
            self.assertIn("ReqForge Analysis:", buf_gen.getvalue())
            self.assertTrue((out_dir / "python" / "smoke_test.py").is_file())
            self.assertTrue((out_dir / "typescript" / "client.ts").is_file())
            self.assertTrue((out_dir / "typescript" / "tsconfig.json").is_file())
            self.assertTrue((out_dir / "javascript" / "client.mjs").is_file())

            web_out = tmp / "web_bundle"
            buf_web = io.StringIO()
            with contextlib.redirect_stdout(buf_web):
                rc_web = cli_main(["export-web", "--out", str(web_out)])
            self.assertEqual(rc_web, 0)
            self.assertIn("Exported 4 static web bundle file(s)", buf_web.getvalue())
            zip_path = web_out / "reqforge_core.zip"
            self.assertTrue(zip_path.is_file())
            with zipfile.ZipFile(zip_path) as zf:
                names = set(zf.namelist())
                self.assertIn("reqforge/pipeline.py", names)
                self.assertIn("reqforge/ingest/curl.py", names)

            unzipped = tmp / "pyodide_sim"
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(unzipped)

            code = (
                "import json, pathlib, reqforge\n"
                "from reqforge.pipeline import forge_json_sync\n"
                "assert pathlib.Path(reqforge.__file__).resolve().is_relative_to(pathlib.Path.cwd().resolve())\n"
                "res = json.loads(forge_json_sync(json.dumps({\n"
                "  'input': \"curl 'https://api.example.com/v1/items/99' -H 'X-Api-Key: abc'\",\n"
                "  'target': 'python'\n"
                "})))\n"
                "assert res['analysis']['kept_endpoints'] == 1\n"
                "assert len(res['files']['python']) >= 4\n"
            )
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=unzipped,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"{proc.stdout}\n{proc.stderr}")

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
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/../pyproject.toml", timeout=5)
                self.fail("Expected 404 on parent traversal path")
            except urllib.error.HTTPError as err:
                self.assertEqual(err.code, 404)

            with urllib.request.urlopen(http_req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                payload = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(payload["analysis"]["kept_endpoints"], 1)
                self.assertIn("typescript", payload["files"])
        finally:
            server.shutdown()
            server.server_close()

    def test_web_zip_builder_produces_valid_zip(self) -> None:
        app_js = (Path(__file__).resolve().parent.parent / "src" / "reqforge" / "web" / "app.js").read_text(
            encoding="utf-8"
        )
        start = app_js.index("function crc32(bytes)")
        end = app_js.index("async function runForge()")
        zip_funcs = app_js[start:end]
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_zip = tmp / "bundle.zip"
            script = tmp / "mkzip.mjs"
            script.write_text(
                'import fs from "node:fs";\n'
                + zip_funcs
                + "\nconst blob = buildZipBlob([\n"
                '  { label: "python/client.py", content: "print(1)\\n" },\n'
                '  { label: "python/models.py", content: "x = 2\\n" }\n'
                "]);\n"
                "const buf = Buffer.from(await blob.arrayBuffer());\n"
                f'fs.writeFileSync({json.dumps(str(out_zip))}, buf);\n',
                encoding="utf-8",
            )
            proc = subprocess.run(["node", str(script)], capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with zipfile.ZipFile(out_zip) as zf:
                self.assertEqual(zf.namelist(), ["python/client.py", "python/models.py"])
                self.assertEqual(zf.read("python/client.py").decode("utf-8"), "print(1)\n")

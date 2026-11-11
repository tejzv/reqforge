from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from reqforge.pipeline import ForgeOptions, run_pipeline, write_generated_files

MULTI_HAR = json.dumps(
    {
        "log": {
            "entries": [
                {
                    "_resourceType": "fetch",
                    "request": {
                        "method": "GET",
                        "url": "https://api.demo.test/v1/users/42?expand=roles",
                        "headers": [
                            {"name": "Authorization", "value": "Bearer super_secret_token"},
                            {"name": "sec-ch-ua", "value": '"Chrome"'},
                        ],
                    },
                    "response": {
                        "status": 200,
                        "content": {
                            "mimeType": "application/json",
                            "text": json.dumps({"id": 42, "from": "oauth", "roles": ["admin"]}),
                        },
                    },
                },
                {
                    "_resourceType": "fetch",
                    "request": {
                        "method": "POST",
                        "url": "https://api.demo.test/v1/users/42/items",
                        "headers": [
                            {"name": "Authorization", "value": "Bearer super_secret_token"},
                            {"name": "Content-Type", "value": "application/json"},
                        ],
                        "postData": {
                            "mimeType": "application/json",
                            "text": json.dumps({"title": "Notebook", "qty": 2}),
                        },
                    },
                    "response": {
                        "status": 201,
                        "content": {
                            "mimeType": "application/json",
                            "text": json.dumps({"created": True, "item_id": "itm_1"}),
                        },
                    },
                },
            ]
        }
    }
)


class TestCodegenAndExecution(unittest.TestCase):
    def test_python_httpx_sdk_compiles_imports_and_executes(self) -> None:
        result, generated = run_pipeline(
            MULTI_HAR,
            source_name="demo.har",
            options=ForgeOptions(target="python", transport="httpx", client_name="Demo"),
        )
        files = generated["python"]
        for gf in files:
            if gf.path.endswith(".py"):
                ast.parse(gf.content, filename=gf.path)
            self.assertNotIn("super_secret_token", gf.content)

        with tempfile.TemporaryDirectory() as tmpdir:
            write_generated_files(tmpdir, generated)
            sys.path.insert(0, tmpdir)
            try:
                importlib.invalidate_caches()
                pkg = importlib.import_module("demo")
                importlib.reload(pkg)

                def handler(request: httpx.Request) -> httpx.Response:
                    self.assertEqual(request.headers.get("Authorization"), "Bearer env_tok_999")
                    if request.method == "GET" and request.url.path == "/v1/users/42":
                        return httpx.Response(
                            200,
                            json={"id": 42, "from": "oauth", "roles": ["admin"]},
                        )
                    if request.method == "POST" and request.url.path == "/v1/users/42/items":
                        body = json.loads(request.content.decode("utf-8"))
                        self.assertEqual(body, {"title": "Notebook", "qty": 2})
                        return httpx.Response(201, json={"created": True, "item_id": "itm_1"})
                    return httpx.Response(404, text="not found")

                async def run_client() -> None:
                    with patch.dict(os.environ, {"API_BEARER_TOKEN": "env_tok_999"}):
                        client = pkg.DemoClient.from_env(retry_base_delay=0.01)
                    await client._client.aclose()
                    attempts = {"count": 0}

                    def flaky_handler(request: httpx.Request) -> httpx.Response:
                        if request.method == "GET" and request.url.path == "/v1/users/42":
                            attempts["count"] += 1
                            if attempts["count"] == 1:
                                return httpx.Response(429, headers={"Retry-After": "0.01"}, text="slow down")
                        return handler(request)

                    client._client = httpx.AsyncClient(
                        base_url="https://api.demo.test",
                        transport=httpx.MockTransport(flaky_handler),
                    )
                    async with client:
                        user = await client.get_user(user_id=42)
                        self.assertEqual(attempts["count"], 2)
                        self.assertEqual(user.id, 42)
                        self.assertEqual(user.from_, "oauth")
                        created = await client.create_user_items(
                            user_id=42,
                            payload=pkg.CreateUserItemsRequest(title="Notebook", qty=2),
                        )
                        self.assertTrue(created.created)
                        self.assertEqual(created.item_id, "itm_1")

                asyncio.run(run_client())
            finally:
                if tmpdir in sys.path:
                    sys.path.remove(tmpdir)
                for mod_name in list(sys.modules):
                    if mod_name == "demo" or mod_name.startswith("demo."):
                        sys.modules.pop(mod_name, None)

    def test_python_curl_cffi_and_single_file_ast(self) -> None:
        _, generated = run_pipeline(
            MULTI_HAR,
            source_name="demo.har",
            options=ForgeOptions(
                target="python",
                transport="curl_cffi",
                single_file=True,
                client_name="Stealth",
            ),
        )
        paths = [gf.path for gf in generated["python"]]
        self.assertEqual(paths, ["stealth_client.py", "smoke_test.py"])
        for gf in generated["python"]:
            ast.parse(gf.content, filename=gf.path)
        self.assertIn("from curl_cffi.requests import AsyncSession", generated["python"][0].content)

    def test_javascript_sdk_syntax_and_runtime_execution(self) -> None:
        _, generated = run_pipeline(
            MULTI_HAR,
            source_name="demo.har",
            options=ForgeOptions(target="javascript", client_name="Demo"),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            write_generated_files(tmpdir, generated)
            for fname in ("client.mjs", "smoke_test.mjs"):
                proc = subprocess.run(
                    ["node", "--check", str(Path(tmpdir) / fname)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)

            runner = Path(tmpdir) / "run_check.mjs"
            runner.write_text(
                'import assert from "node:assert/strict";\n'
                'import { DemoClient } from "./client.mjs";\n'
                "let captured = null;\n"
                "const client = new DemoClient({\n"
                '  baseUrl: "https://api.demo.test",\n'
                '  bearerToken: "tok_js_1",\n'
                "  fetchFn: async (url, init) => {\n"
                "    captured = { url, init };\n"
                '    return new Response(JSON.stringify({ id: 42, from: "oauth", roles: ["admin"] }), {\n'
                "      status: 200,\n"
                '      headers: { "content-type": "application/json" }\n'
                "    });\n"
                "  }\n"
                "});\n"
                "const res = await client.getUser(42);\n"
                "assert.equal(res.id, 42);\n"
                'assert.equal(captured.init.headers["Authorization"], "Bearer tok_js_1");\n',
                encoding="utf-8",
            )
            exec_proc = subprocess.run(
                ["node", str(runner)],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(exec_proc.returncode, 0, exec_proc.stderr)

    def test_typescript_sdk_strip_types_execution(self) -> None:
        _, generated = run_pipeline(
            MULTI_HAR,
            source_name="demo.har",
            options=ForgeOptions(target="typescript", client_name="Demo"),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            write_generated_files(tmpdir, generated)
            runner = Path(tmpdir) / "run_ts_check.ts"
            runner.write_text(
                'import assert from "node:assert/strict";\n'
                'import { DemoClient } from "./index.ts";\n'
                "const client = new DemoClient({\n"
                '  baseUrl: "https://api.demo.test",\n'
                '  bearerToken: "tok_ts_1",\n'
                "  fetchFn: async (_url, _init) => {\n"
                '    return new Response(JSON.stringify({ created: true, item_id: "itm_9" }), {\n'
                "      status: 201,\n"
                '      headers: { "content-type": "application/json" }\n'
                "    });\n"
                "  }\n"
                "});\n"
                'const out = await client.createUserItems(42, { title: "Book", qty: 1 });\n'
                'assert.equal(out.item_id, "itm_9");\n',
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["node", "--experimental-strip-types", str(runner)],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"{proc.stdout}\n{proc.stderr}")

    def test_multihost_and_multipart_across_all_targets(self) -> None:
        raw = (
            "curl 'https://api.demo.test/v1/users/42' -H 'Accept: application/json'\n"
            "curl 'https://upload.demo.test/v1/files/77' "
            "-H 'Content-Type: multipart/form-data; boundary=----WebKitFormBoundaryABC' "
            "-F 'title=report' -F 'kind=pdf'"
        )
        _, generated = run_pipeline(
            raw,
            options=ForgeOptions(target="all", client_name="Multi"),
        )
        py_client = next(f.content for f in generated["python"] if f.path.endswith("client.py"))
        self.assertNotIn("WebKitFormBoundaryABC", py_client)
        self.assertIn("https://upload.demo.test/v1/files/", py_client)
        self.assertIn("files_data=", py_client)
        ast.parse(py_client)

        ts_client = next(f.content for f in generated["typescript"] if f.path == "client.ts")
        self.assertNotIn("WebKitFormBoundaryABC", ts_client)
        self.assertIn("new FormData()", ts_client)

        js_client = next(f.content for f in generated["javascript"] if f.path == "client.mjs")
        self.assertNotIn("WebKitFormBoundaryABC", js_client)
        self.assertIn("new FormData()", js_client)

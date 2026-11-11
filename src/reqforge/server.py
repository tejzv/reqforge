from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from reqforge.errors import ReqForgeError
from reqforge.filter import FilterOptions
from reqforge.pipeline import ForgeOptions, run_pipeline_async
from reqforge.probe import ProbeConfig, has_curl_cffi, has_httpx

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY_BYTES = 25 * 1024 * 1024

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


def build_forge_payload_options(body: dict[str, Any]) -> ForgeOptions:
    filter_cfg = body.get("filter") if isinstance(body.get("filter"), dict) else {}
    probe_cfg = body.get("probeConfig") if isinstance(body.get("probeConfig"), dict) else {}

    filter_opts = FilterOptions(
        drop_static=bool(filter_cfg.get("dropStatic", True)),
        drop_trackers=bool(filter_cfg.get("dropTrackers", True)),
        drop_options=bool(filter_cfg.get("dropOptions", True)),
        drop_html_documents=bool(filter_cfg.get("dropHtml", True)),
        only_json=bool(filter_cfg.get("onlyJson", False)),
    )
    probe_options = ProbeConfig(
        transport=str(probe_cfg.get("transport", "auto")),
        timeout_seconds=float(probe_cfg.get("timeoutSeconds", 10.0)),
        delay_seconds=float(probe_cfg.get("delaySeconds", 0.0)),
        max_probes=int(probe_cfg.get("maxProbes", 60)),
        minimize_cookies=bool(probe_cfg.get("minimizeCookies", True)),
    )
    return ForgeOptions(
        client_name=str(body.get("clientName", "") or ""),
        base_url=str(body.get("baseUrl", "") or ""),
        target=str(body.get("target", "python") or "python"),
        transport=str(body.get("transport", "auto") or "auto"),
        single_file=bool(body.get("singleFile", False)),
        probe=bool(body.get("probe", False)),
        probe_config=probe_options,
        filter_options=filter_opts,
        keep_user_agent=bool(body.get("keepUserAgent", False)),
        keep_origin_referer=bool(body.get("keepOriginReferer", False)),
    )


async def execute_forge_request(body: dict[str, Any]) -> dict[str, Any]:
    raw_input = str(body.get("input", "") or "")
    source_name = str(body.get("sourceName", "") or "")
    options = build_forge_payload_options(body)
    result, generated = await run_pipeline_async(
        raw_input,
        source_name=source_name,
        options=options,
    )
    files_payload: dict[str, list[dict[str, str]]] = {}
    for target_name, gfiles in generated.items():
        files_payload[target_name] = [{"path": gf.path, "content": gf.content} for gf in gfiles]
    return {
        "analysis": result.to_dict(),
        "files": files_payload,
    }


class ReqForgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_static(self, rel_path: str) -> None:
        clean = rel_path.lstrip("/") or "index.html"
        target = (WEB_DIR / clean).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        raw = target.read_bytes()
        ct = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "mode": "local-server",
                    "capabilities": {
                        "liveProbe": True,
                        "httpx": has_httpx(),
                        "curl_cffi": has_curl_cffi(),
                    },
                },
            )
            return
        self._send_static(path)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/api/forge":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
            return

        length_hdr = self.headers.get("Content-Length", "0")
        try:
            length = int(length_hdr)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
            return

        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "Payload size out of bounds"},
            )
            return

        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid JSON: {exc}"})
            return

        if not isinstance(body, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "JSON body must be an object"})
            return

        try:
            payload = asyncio.run(execute_forge_request(body))
            self._send_json(HTTPStatus.OK, payload)
        except ReqForgeError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})


def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), ReqForgeHandler)


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = create_server(host=host, port=port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

from __future__ import annotations

import asyncio
import importlib.util
import ssl
import urllib.error
import urllib.request
from typing import Any, Protocol
from urllib.parse import urlencode

from reqforge.errors import ProbeError
from reqforge.models import CapturedRequest
from reqforge.probe.matcher import ResponseSnapshot
from reqforge.utils import format_cookie_header


def has_httpx() -> bool:
    return importlib.util.find_spec("httpx") is not None


def has_curl_cffi() -> bool:
    return importlib.util.find_spec("curl_cffi") is not None


class ProbeTransport(Protocol):
    name: str

    async def send(
        self,
        req: CapturedRequest,
        headers: dict[str, str],
        cookies: dict[str, str],
    ) -> ResponseSnapshot:
        ...

    async def aclose(self) -> None:
        ...


def _build_target_url(req: CapturedRequest) -> str:
    base = f"{req.base_url()}{req.path}"
    if req.query_params:
        return f"{base}?{urlencode(req.query_params)}"
    return base


def _prepare_wire_payload(
    req: CapturedRequest,
    headers: dict[str, str],
    cookies: dict[str, str],
) -> tuple[str, dict[str, str], bytes | None]:
    url = _build_target_url(req)
    wire_headers = dict(headers)
    if cookies:
        wire_headers["Cookie"] = format_cookie_header(cookies)

    body_bytes: bytes | None = None
    if req.method.upper() not in {"GET", "HEAD"}:
        if req.body_kind == "json" and req.body_text is not None:
            body_bytes = req.body_text.encode("utf-8")
        elif req.body_kind == "form" and req.body_form:
            body_bytes = urlencode(req.body_form).encode("utf-8")
        elif req.body_text is not None:
            body_bytes = req.body_text.encode("utf-8")
    return url, wire_headers, body_bytes


class HttpxProbeTransport:
    name = "httpx"

    def __init__(self, timeout: float = 10.0, verify_ssl: bool = True) -> None:
        import httpx

        http2_available = importlib.util.find_spec("h2") is not None
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=verify_ssl,
            http2=http2_available,
        )

    async def send(
        self,
        req: CapturedRequest,
        headers: dict[str, str],
        cookies: dict[str, str],
    ) -> ResponseSnapshot:
        url, wire_headers, body_bytes = _prepare_wire_payload(req, headers, cookies)
        self._client.cookies.clear()
        try:
            resp = await self._client.request(
                method=req.method,
                url=url,
                headers=wire_headers,
                content=body_bytes,
            )
        except Exception as exc:
            raise ProbeError(f"httpx request failed: {exc}") from exc
        ct = resp.headers.get("content-type", "")
        return ResponseSnapshot.from_raw(resp.status_code, ct, resp.text)

    async def aclose(self) -> None:
        await self._client.aclose()


class CurlCffiProbeTransport:
    name = "curl_cffi"

    def __init__(
        self,
        timeout: float = 10.0,
        verify_ssl: bool = True,
        impersonate: str = "chrome",
    ) -> None:
        self._timeout = timeout
        self._verify = verify_ssl
        self._impersonate = impersonate

    async def send(
        self,
        req: CapturedRequest,
        headers: dict[str, str],
        cookies: dict[str, str],
    ) -> ResponseSnapshot:
        from curl_cffi.requests import AsyncSession

        url, wire_headers, body_bytes = _prepare_wire_payload(req, headers, cookies)
        try:
            async with AsyncSession(
                impersonate=self._impersonate,
                timeout=self._timeout,
                verify=self._verify,
            ) as session:
                resp = await session.request(
                    method=req.method,
                    url=url,
                    headers=wire_headers,
                    data=body_bytes,
                    allow_redirects=True,
                )
        except Exception as exc:
            raise ProbeError(f"curl_cffi request failed: {exc}") from exc
        ct = resp.headers.get("content-type", "") if resp.headers else ""
        return ResponseSnapshot.from_raw(int(resp.status_code), str(ct), resp.text or "")

    async def aclose(self) -> None:
        return None


class UrllibProbeTransport:
    name = "httpx"

    def __init__(self, timeout: float = 10.0, verify_ssl: bool = True) -> None:
        self._timeout = timeout
        self._verify = verify_ssl

    def _send_sync(
        self,
        req: CapturedRequest,
        headers: dict[str, str],
        cookies: dict[str, str],
    ) -> ResponseSnapshot:
        url, wire_headers, body_bytes = _prepare_wire_payload(req, headers, cookies)
        request_obj = urllib.request.Request(
            url=url,
            data=body_bytes,
            headers=wire_headers,
            method=req.method,
        )
        ctx = None if self._verify else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(request_obj, timeout=self._timeout, context=ctx) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                ct = resp.headers.get("Content-Type", "")
                return ResponseSnapshot.from_raw(int(resp.status), ct, raw)
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8", errors="replace")
            ct = err.headers.get("Content-Type", "") if err.headers else ""
            return ResponseSnapshot.from_raw(int(err.code), ct, raw)
        except Exception as exc:
            raise ProbeError(f"urllib request failed: {exc}") from exc

    async def send(
        self,
        req: CapturedRequest,
        headers: dict[str, str],
        cookies: dict[str, str],
    ) -> ResponseSnapshot:
        return await asyncio.to_thread(self._send_sync, req, headers, cookies)

    async def aclose(self) -> None:
        return None


def create_transport(
    kind: str,
    *,
    timeout: float = 10.0,
    verify_ssl: bool = True,
    impersonate: str = "chrome",
) -> Any:
    if kind == "curl_cffi":
        if not has_curl_cffi():
            raise ProbeError("curl_cffi is not installed")
        return CurlCffiProbeTransport(
            timeout=timeout,
            verify_ssl=verify_ssl,
            impersonate=impersonate,
        )
    if has_httpx():
        return HttpxProbeTransport(timeout=timeout, verify_ssl=verify_ssl)
    return UrllibProbeTransport(timeout=timeout, verify_ssl=verify_ssl)

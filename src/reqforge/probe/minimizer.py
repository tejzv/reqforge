from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from reqforge.errors import ProbeError
from reqforge.models import CapturedRequest, ProbeReport, TransportMode
from reqforge.probe.matcher import ResponseSnapshot, is_equivalent_response
from reqforge.probe.transports import create_transport, has_curl_cffi

WIRE_MANAGED_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-connection",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "accept-encoding",
    "cookie",
}


@dataclass(slots=True)
class ProbeConfig:
    transport: TransportMode = "auto"
    timeout_seconds: float = 10.0
    delay_seconds: float = 0.0
    max_probes: int = 80
    minimize_cookies: bool = True
    minimize_query: bool = False
    impersonate: str = "chrome"
    verify_ssl: bool = True


@dataclass(slots=True)
class ProbeOutcome:
    kept_headers: dict[str, str] = field(default_factory=dict)
    dropped_headers: list[str] = field(default_factory=list)
    kept_cookies: dict[str, str] = field(default_factory=dict)
    dropped_cookies: list[str] = field(default_factory=list)
    kept_query_keys: list[str] = field(default_factory=list)
    dropped_query_keys: list[str] = field(default_factory=list)
    report: ProbeReport | None = None
    baseline_snapshot: ResponseSnapshot | None = None


def _build_baseline_headers(req: CapturedRequest) -> tuple[dict[str, str], dict[str, str]]:
    pinned: dict[str, str] = {}
    candidates: dict[str, str] = {}
    for name, value in req.headers:
        lower = name.lower()
        if lower.startswith(":") or lower in WIRE_MANAGED_HEADERS:
            continue
        if lower == "content-type" and req.body_kind != "none":
            pinned[name] = value
        else:
            candidates[name] = value
    return pinned, candidates


def _is_acceptable_baseline(req: CapturedRequest, snap: ResponseSnapshot) -> bool:
    if req.response_status is not None and req.response_status < 400:
        return snap.status == req.response_status or 200 <= snap.status < 300
    return 200 <= snap.status < 400


class _DeltaMinimizer:
    def __init__(
        self,
        *,
        max_probes: int,
        delay_seconds: float,
    ) -> None:
        self.max_probes = max_probes
        self.delay_seconds = delay_seconds
        self.probe_count = 0

    async def find_minimal_keys(
        self,
        keys: list[str],
        tester,
    ) -> list[str]:
        if not keys:
            return []

        if self.probe_count < self.max_probes:
            if await self._call(tester, []):
                return []

        required = await self._ddmin_required(keys, [], tester)

        if 1 < len(required) <= 8:
            confirmed = list(required)
            for key in list(required):
                if self.probe_count >= self.max_probes:
                    break
                without = [k for k in confirmed if k != key]
                if await self._call(tester, without):
                    confirmed = without
            return confirmed

        return required

    async def _call(self, tester, subset: list[str]) -> bool:
        if self.probe_count >= self.max_probes:
            return False
        if self.delay_seconds > 0 and self.probe_count > 0:
            await asyncio.sleep(self.delay_seconds)
        self.probe_count += 1
        return await tester(subset)

    async def _ddmin_required(
        self,
        candidates: list[str],
        must_keep: list[str],
        tester,
    ) -> list[str]:
        if not candidates:
            return must_keep
        if len(candidates) == 1:
            if self.probe_count >= self.max_probes:
                return must_keep + candidates
            if await self._call(tester, must_keep):
                return must_keep
            return must_keep + candidates

        if self.probe_count >= self.max_probes:
            return must_keep + candidates

        mid = len(candidates) // 2
        left = candidates[:mid]
        right = candidates[mid:]

        if await self._call(tester, must_keep + right):
            return await self._ddmin_required(right, must_keep, tester)

        if await self._call(tester, must_keep + left):
            return await self._ddmin_required(left, must_keep, tester)

        kept_after_left = await self._ddmin_required(left, must_keep + right, tester)
        newly_required_left = [k for k in left if k in kept_after_left]
        return await self._ddmin_required(right, must_keep + newly_required_left, tester)


async def minimize_request_live(
    req: CapturedRequest,
    config: ProbeConfig | None = None,
) -> ProbeOutcome:
    cfg = config or ProbeConfig()
    pinned_headers, candidate_headers = _build_baseline_headers(req)
    all_headers = {**pinned_headers, **candidate_headers}
    all_cookies = dict(req.cookies)

    notes: list[str] = []
    httpx_ok: bool | None = None
    curl_ok: bool | None = None
    chosen_transport = None
    baseline_snap: ResponseSnapshot | None = None
    recommended: Literal["httpx", "curl_cffi"] = "httpx"
    used_name: Literal["httpx", "curl_cffi"] = "httpx"

    if cfg.transport == "curl_cffi":
        t = create_transport(
            "curl_cffi",
            timeout=cfg.timeout_seconds,
            verify_ssl=cfg.verify_ssl,
            impersonate=cfg.impersonate,
        )
        try:
            baseline_snap = await t.send(req, all_headers, all_cookies)
            curl_ok = _is_acceptable_baseline(req, baseline_snap)
            chosen_transport = t
            used_name = "curl_cffi"
            recommended = "curl_cffi"
        except ProbeError:
            await t.aclose()
            raise
    elif cfg.transport == "httpx":
        t = create_transport(
            "httpx",
            timeout=cfg.timeout_seconds,
            verify_ssl=cfg.verify_ssl,
        )
        try:
            baseline_snap = await t.send(req, all_headers, all_cookies)
            httpx_ok = _is_acceptable_baseline(req, baseline_snap)
            chosen_transport = t
            used_name = "httpx"
            recommended = "httpx"
        except ProbeError:
            await t.aclose()
            raise
    else:
        t_httpx = create_transport(
            "httpx",
            timeout=cfg.timeout_seconds,
            verify_ssl=cfg.verify_ssl,
        )
        httpx_snap: ResponseSnapshot | None = None
        try:
            httpx_snap = await t_httpx.send(req, all_headers, all_cookies)
            httpx_ok = _is_acceptable_baseline(req, httpx_snap)
        except ProbeError as exc:
            httpx_ok = False
            notes.append(f"httpx baseline failed: {exc}")

        if httpx_ok and httpx_snap is not None:
            chosen_transport = t_httpx
            baseline_snap = httpx_snap
            used_name = "httpx"
            recommended = "httpx"
        elif has_curl_cffi():
            await t_httpx.aclose()
            t_curl = create_transport(
                "curl_cffi",
                timeout=cfg.timeout_seconds,
                verify_ssl=cfg.verify_ssl,
                impersonate=cfg.impersonate,
            )
            try:
                curl_snap = await t_curl.send(req, all_headers, all_cookies)
                curl_ok = _is_acceptable_baseline(req, curl_snap)
                if curl_ok:
                    chosen_transport = t_curl
                    baseline_snap = curl_snap
                    used_name = "curl_cffi"
                    recommended = "curl_cffi"
                    notes.append(
                        "Target blocked standard HTTP client; curl_cffi TLS impersonation succeeded"
                    )
                else:
                    chosen_transport = t_curl
                    baseline_snap = curl_snap
                    used_name = "curl_cffi"
                    recommended = "curl_cffi"
                    notes.append(
                        f"Both httpx and curl_cffi baseline returned status {curl_snap.status}"
                    )
            except ProbeError:
                await t_curl.aclose()
                if httpx_snap is not None:
                    chosen_transport = create_transport(
                        "httpx",
                        timeout=cfg.timeout_seconds,
                        verify_ssl=cfg.verify_ssl,
                    )
                    baseline_snap = httpx_snap
                    used_name = "httpx"
                else:
                    raise
        else:
            chosen_transport = t_httpx
            baseline_snap = httpx_snap
            used_name = "httpx"
            if not httpx_ok:
                notes.append("Install curl_cffi if endpoint enforces browser TLS fingerprinting")

    assert chosen_transport is not None
    assert baseline_snap is not None

    try:
        baseline_matched = _is_acceptable_baseline(req, baseline_snap)
        if not baseline_matched:
            notes.append(
                f"Baseline probe returned status {baseline_snap.status}; keeping original headers conservatively"
            )
            report = ProbeReport(
                transport_used=used_name,
                recommended_transport=recommended,
                baseline_status=baseline_snap.status,
                baseline_matched=False,
                httpx_baseline_ok=httpx_ok,
                curl_cffi_baseline_ok=curl_ok,
                total_probes=1,
                required_headers=list(all_headers.keys()),
                removed_headers=[],
                required_cookies=list(all_cookies.keys()),
                removed_cookies=[],
                notes=notes,
            )
            return ProbeOutcome(
                kept_headers=all_headers,
                dropped_headers=[],
                kept_cookies=all_cookies,
                dropped_cookies=[],
                report=report,
                baseline_snapshot=baseline_snap,
            )

        minimizer = _DeltaMinimizer(
            max_probes=max(1, cfg.max_probes - 1),
            delay_seconds=cfg.delay_seconds,
        )

        async def test_headers_subset(subset_keys: list[str]) -> bool:
            test_h = dict(pinned_headers)
            for k in subset_keys:
                test_h[k] = candidate_headers[k]
            try:
                snap = await chosen_transport.send(req, test_h, all_cookies)
            except ProbeError:
                return False
            matched, _ = is_equivalent_response(baseline_snap, snap)
            return matched

        kept_header_keys = await minimizer.find_minimal_keys(
            list(candidate_headers.keys()),
            test_headers_subset,
        )
        kept_headers = dict(pinned_headers)
        for k in kept_header_keys:
            kept_headers[k] = candidate_headers[k]
        dropped_headers = [k for k in candidate_headers if k not in kept_header_keys]

        kept_cookie_keys = list(all_cookies.keys())
        if cfg.minimize_cookies and all_cookies and minimizer.probe_count < cfg.max_probes - 1:
            async def test_cookies_subset(subset_keys: list[str]) -> bool:
                test_c = {k: all_cookies[k] for k in subset_keys}
                try:
                    snap = await chosen_transport.send(req, kept_headers, test_c)
                except ProbeError:
                    return False
                matched, _ = is_equivalent_response(baseline_snap, snap)
                return matched

            kept_cookie_keys = await minimizer.find_minimal_keys(
                list(all_cookies.keys()),
                test_cookies_subset,
            )

        kept_cookies = {k: all_cookies[k] for k in kept_cookie_keys}
        dropped_cookies = [k for k in all_cookies if k not in kept_cookie_keys]

        distinct_q_keys = list(dict.fromkeys(k for k, _ in req.query_params))
        kept_query_keys = list(distinct_q_keys)
        if cfg.minimize_query and distinct_q_keys and minimizer.probe_count < cfg.max_probes - 1:
            async def test_query_subset(subset_keys: list[str]) -> bool:
                allowed = set(subset_keys)
                qp = [(k, v) for k, v in req.query_params if k in allowed]
                try:
                    snap = await chosen_transport.send(req, kept_headers, kept_cookies, qp)
                except ProbeError:
                    return False
                matched, _ = is_equivalent_response(baseline_snap, snap)
                return matched

            kept_query_keys = await minimizer.find_minimal_keys(
                distinct_q_keys,
                test_query_subset,
            )

        dropped_query_keys = [k for k in distinct_q_keys if k not in kept_query_keys]

        report = ProbeReport(
            transport_used=used_name,
            recommended_transport=recommended,
            baseline_status=baseline_snap.status,
            baseline_matched=True,
            httpx_baseline_ok=httpx_ok,
            curl_cffi_baseline_ok=curl_ok,
            total_probes=minimizer.probe_count + 1,
            required_headers=list(kept_headers.keys()),
            removed_headers=dropped_headers,
            required_cookies=list(kept_cookies.keys()),
            removed_cookies=dropped_cookies,
            required_query_params=kept_query_keys,
            removed_query_params=dropped_query_keys,
            notes=notes,
        )
        return ProbeOutcome(
            kept_headers=kept_headers,
            dropped_headers=dropped_headers,
            kept_cookies=kept_cookies,
            dropped_cookies=dropped_cookies,
            kept_query_keys=kept_query_keys,
            dropped_query_keys=dropped_query_keys,
            report=report,
            baseline_snapshot=baseline_snap,
        )
    finally:
        await chosen_transport.aclose()

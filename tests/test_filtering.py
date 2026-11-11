from __future__ import annotations

import unittest

from reqforge.filter import FilterOptions, filter_requests, minimalize_headers_and_cookies
from reqforge.models import CapturedRequest


def _make_req(
    req_id: str,
    url: str,
    *,
    method: str = "GET",
    host: str = "api.example.com",
    path: str = "/v1/data",
    headers: list[tuple[str, str]] | None = None,
    cookies: dict[str, str] | None = None,
    resource_type: str | None = None,
    response_ct: str | None = "application/json",
    response_json: object = None,
) -> CapturedRequest:
    return CapturedRequest(
        id=req_id,
        method=method,
        url=url,
        scheme="https",
        host=host,
        port=None,
        path=path,
        headers=headers or [],
        cookies=cookies or {},
        resource_type=resource_type,
        response_content_type=response_ct,
        response_json=response_json,
    )


class TestFiltering(unittest.TestCase):
    def test_drops_static_trackers_options_and_html(self) -> None:
        reqs = [
            _make_req("1", "https://cdn.example.com/styles.css", host="cdn.example.com", path="/styles.css", response_ct="text/css"),
            _make_req("2", "https://cdn.example.com/app.js", host="cdn.example.com", path="/app.js", resource_type="script", response_ct="application/javascript"),
            _make_req("3", "https://www.google-analytics.com/g/collect", method="POST", host="www.google-analytics.com", path="/g/collect"),
            _make_req("4", "https://api.example.com/v1/items", method="OPTIONS", path="/v1/items"),
            _make_req("5", "https://app.example.com/dashboard", host="app.example.com", path="/dashboard", resource_type="document", response_ct="text/html"),
            _make_req("6", "https://api.example.com/v1/items", path="/v1/items", resource_type="fetch", response_json={"items": []}),
        ]
        kept, dropped = filter_requests(reqs, FilterOptions())
        self.assertEqual([r.id for r in kept], ["6"])
        reasons = {d.reason for d in dropped}
        self.assertEqual(
            reasons,
            {"static_asset", "tracker_or_telemetry", "preflight_options", "html_document"},
        )

    def test_header_cookie_minimization_and_auth_flagging(self) -> None:
        req = _make_req(
            "1",
            "https://api.example.com/v1/me?api_key=secret_q_key",
            headers=[
                ("Authorization", "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.sig"),
                ("X-CSRF-Token", "csrf_99887766"),
                ("Content-Type", "application/json"),
                ("sec-ch-ua", '"Chromium";v="124"'),
                ("sec-fetch-mode", "cors"),
                ("user-agent", "Mozilla/5.0"),
                ("origin", "https://app.example.com"),
                ("referer", "https://app.example.com/"),
                ("priority", "u=1, i"),
                ("accept", "*/*"),
            ],
            cookies={
                "_ga": "GA1.1.123",
                "_ga_XYZ": "GS1.1.123",
                "_fbp": "fb.1.1",
                "cf_clearance": "cf_val_secret_123",
                "session_id": "sess_val_secret_999",
            },
        )
        req.query_params = [("api_key", "secret_q_key")]

        res = minimalize_headers_and_cookies(req)
        self.assertEqual(
            set(res.kept_headers.keys()),
            {"Authorization", "X-CSRF-Token", "Content-Type"},
        )
        self.assertIn("sec-ch-ua", res.dropped_headers)
        self.assertIn("user-agent", res.dropped_headers)
        self.assertEqual(set(res.kept_cookies.keys()), {"cf_clearance", "session_id"})
        self.assertEqual(set(res.dropped_cookies), {"_ga", "_ga_XYZ", "_fbp"})

        auth_kinds = {(a.location, a.kind, a.name) for a in res.auth_findings}
        self.assertIn(("header", "bearer", "Authorization"), auth_kinds)
        self.assertIn(("header", "csrf", "X-CSRF-Token"), auth_kinds)
        self.assertIn(("cookie", "session_cookie", "cf_clearance"), auth_kinds)
        self.assertIn(("cookie", "session_cookie", "session_id"), auth_kinds)
        self.assertIn(("query", "api_key", "api_key"), auth_kinds)

        for finding in res.auth_findings:
            self.assertNotIn("secret_999", finding.masked_preview)
            self.assertNotIn("secret_q_key", finding.masked_preview)


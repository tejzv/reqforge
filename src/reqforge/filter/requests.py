from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from reqforge.models import CapturedRequest, FilteredEntry

STATIC_EXTENSIONS = {
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".map",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".avif",
    ".bmp",
    ".tiff",
    ".mp4",
    ".webm",
    ".ogg",
    ".mp3",
    ".wav",
    ".flac",
    ".aac",
    ".m4a",
    ".m4v",
    ".wasm",
    ".pdf",
    ".zip",
    ".gz",
    ".br",
}

STATIC_RESOURCE_TYPES = {
    "stylesheet",
    "script",
    "font",
    "image",
    "media",
    "manifest",
    "texttrack",
}

TRACKER_RESOURCE_TYPES = {
    "ping",
    "csp_report",
}

TRACKER_HOST_SUFFIXES = (
    "google-analytics.com",
    "analytics.google.com",
    "googletagmanager.com",
    "googletagservices.com",
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "connect.facebook.net",
    "analytics.tiktok.com",
    "ads.tiktok.com",
    "snapchat.com",
    "ads-twitter.com",
    "analytics.twitter.com",
    "px.ads.linkedin.com",
    "ct.pinterest.com",
    "alb.reddit.com",
    "cdn.segment.com",
    "api.segment.io",
    "api-js.mixpanel.com",
    "api.mixpanel.com",
    "mixpanel.com",
    "api.amplitude.com",
    "api2.amplitude.com",
    "heapanalytics.com",
    "rs.fullstory.com",
    "edge.fullstory.com",
    "static.hotjar.com",
    "vars.hotjar.com",
    "clarity.ms",
    "mouseflow.com",
    "logrocket.io",
    "sentry.io",
    "ingest.sentry.io",
    "browser-intake-datadoghq.com",
    "logs.browser-intake-datadoghq.com",
    "rum.browser-intake-datadoghq.com",
    "bam.nr-data.net",
    "js-agent.newrelic.com",
    "notify.bugsnag.com",
    "api.rollbar.com",
    "app.posthog.com",
    "us.i.posthog.com",
    "eu.i.posthog.com",
    "plausible.io",
    "api-iam.intercom.io",
    "widget.intercom.io",
    "client.crisp.chat",
    "events.launchdarkly.com",
    "app.launchdarkly.com",
    "events.statsigapi.net",
    "api.statsig.com",
    "cdn.rudderlabs.com",
    "dataplane.rudderstack.com",
    "bat.bing.com",
    "mc.yandex.ru",
    "onesignal.com",
    "track.hubspot.com",
    "forms.hubspot.com",
    "cdn.cookielaw.org",
    "geolocation.onetrust.com",
    "consent.cookiebot.com",
)

TRACKER_PATH_RE = re.compile(
    r"(?:^|/)(?:gtag/js|gtm\.js|analytics\.js|fbevents\.js|pixel(?:\.gif)?|1x1\.gif|cdn-cgi/rum|cdn-cgi/bm/cv|__analytics|telemetry/ingest)(?:$|/|\?)",
    re.IGNORECASE,
)

TRACKER_HOST_EXACT_PATHS = {
    ("www.facebook.com", "/tr"),
    ("facebook.com", "/tr"),
    ("www.google.com", "/ccm/collect"),
    ("www.google.com", "/pagead/1p-conversion"),
}


@dataclass(slots=True)
class FilterOptions:
    drop_static: bool = True
    drop_trackers: bool = True
    drop_options: bool = True
    drop_html_documents: bool = True
    only_json: bool = False
    include_hosts: list[str] = field(default_factory=list)
    exclude_hosts: list[str] = field(default_factory=list)


def _matches_host_filter(host: str, patterns: list[str]) -> bool:
    h = host.lower()
    for pat in patterns:
        p = pat.lower().strip()
        if not p:
            continue
        if h == p or h.endswith(f".{p}"):
            return True
    return False


def _is_tracker(req: CapturedRequest) -> bool:
    host = req.host.lower()
    if any(host == suffix or host.endswith(f".{suffix}") for suffix in TRACKER_HOST_SUFFIXES):
        return True
    path_lower = req.path.lower()
    if (host, path_lower.rstrip("/") or "/") in TRACKER_HOST_EXACT_PATHS:
        return True
    if req.resource_type in TRACKER_RESOURCE_TYPES:
        return True
    if TRACKER_PATH_RE.search(path_lower):
        return True
    if host.startswith(("analytics.", "telemetry.", "metrics.", "tracking.", "pixel.", "stats.")):
        return True
    return False


def _is_static(req: CapturedRequest) -> bool:
    if req.response_json is not None:
        return False
    if req.resource_type in STATIC_RESOURCE_TYPES:
        return True
    ext = PurePosixPath(req.path.lower()).suffix
    if ext in STATIC_EXTENSIONS:
        return True
    sec_dest = (req.get_header("sec-fetch-dest") or "").lower()
    if sec_dest in {"style", "script", "font", "image", "audio", "video", "manifest"}:
        return True
    return False


def _is_html_document(req: CapturedRequest) -> bool:
    if req.response_json is not None:
        return False
    if req.resource_type in {"xhr", "fetch"}:
        return False
    ct = (req.response_content_type or "").lower()
    if "text/html" in ct or "application/xhtml+xml" in ct:
        return True
    if req.resource_type == "document" and req.method == "GET":
        return True
    return False


def filter_requests(
    requests: list[CapturedRequest],
    options: FilterOptions | None = None,
) -> tuple[list[CapturedRequest], list[FilteredEntry]]:
    opts = options or FilterOptions()
    kept: list[CapturedRequest] = []
    filtered: list[FilteredEntry] = []

    for req in requests:
        if opts.include_hosts and not _matches_host_filter(req.host, opts.include_hosts):
            filtered.append(FilteredEntry(url=req.url, method=req.method, reason="host_excluded"))
            continue

        if opts.exclude_hosts and _matches_host_filter(req.host, opts.exclude_hosts):
            filtered.append(FilteredEntry(url=req.url, method=req.method, reason="host_excluded"))
            continue

        if opts.drop_options and req.method.upper() == "OPTIONS":
            filtered.append(FilteredEntry(url=req.url, method=req.method, reason="preflight_options"))
            continue

        if opts.drop_trackers and _is_tracker(req):
            filtered.append(FilteredEntry(url=req.url, method=req.method, reason="tracker_or_telemetry"))
            continue

        if opts.drop_static and _is_static(req):
            filtered.append(FilteredEntry(url=req.url, method=req.method, reason="static_asset"))
            continue

        if opts.drop_html_documents and _is_html_document(req):
            filtered.append(FilteredEntry(url=req.url, method=req.method, reason="html_document"))
            continue

        if opts.only_json and req.response_json is None and req.body_kind != "json":
            filtered.append(FilteredEntry(url=req.url, method=req.method, reason="non_json"))
            continue

        kept.append(req)

    return kept, filtered


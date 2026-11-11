from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field

from reqforge.models import AuthFinding, AuthKind, CapturedRequest
from reqforge.utils import mask_secret, to_snake_case

NOISE_HEADERS = {
    "host",
    "connection",
    "keep-alive",
    "proxy-connection",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "content-length",
    "accept-encoding",
    "expect",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-ch-ua-arch",
    "sec-ch-ua-bitness",
    "sec-ch-ua-full-version",
    "sec-ch-ua-full-version-list",
    "sec-ch-ua-model",
    "sec-ch-ua-wow64",
    "sec-ch-prefers-color-scheme",
    "sec-ch-prefers-reduced-motion",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "sec-fetch-user",
    "sec-purpose",
    "purpose",
    "sec-gpc",
    "upgrade-insecure-requests",
    "priority",
    "dnt",
    "pragma",
    "cache-control",
    "if-modified-since",
    "if-none-match",
    "if-match",
    "if-unmodified-since",
    "if-range",
    "x-devtools-emulate-network-conditions-client-id",
    "user-agent",
    "accept-language",
    "origin",
    "referer",
}

TRACKER_COOKIE_EXACT = {
    "_ga",
    "_gid",
    "_gat",
    "_gcl_au",
    "_gcl_aw",
    "_gcl_dc",
    "_fbp",
    "_fbc",
    "_tt_enable_cookie",
    "_ttp",
    "_clck",
    "_clsk",
    "_scid",
    "_scid_r",
    "_rdt_uuid",
    "_pin_unauth",
    "_uetsid",
    "_uetvid",
    "ajs_anonymous_id",
    "ajs_user_id",
    "ajs_group_id",
    "_dd_s",
    "OptanonConsent",
    "OptanonAlertBoxClosed",
    "CookieConsent",
    "__qca",
    "__gads",
    "__gpi",
    "__eoi",
    "FPAU",
    "FPID",
}

TRACKER_COOKIE_PREFIXES = (
    "_ga_",
    "_gat_",
    "_hj",
    "__utm",
    "mp_",
    "amplitude_id",
    "intercom-",
    "_pk_",
    "ph_",
    "_ym_",
    "hubspotutk",
    "__hs",
    "_sp_",
)

CSRF_HEADER_RE = re.compile(r"(?:csrf|xsrf)", re.IGNORECASE)
API_KEY_HEADER_RE = re.compile(
    r"^(?:x-)?(?:api[-_]?key|apikey|rapidapi[-_]?key|subscription[-_]?key|private[-_]?token|client[-_]?secret|access[-_]?key)$",
    re.IGNORECASE,
)
SIG_HEADER_RE = re.compile(r"(?:signature|hmac|x-amz-date|x-amz-security-token)", re.IGNORECASE)
TOKEN_HEADER_RE = re.compile(
    r"(?:auth|token|secret|bearer|session|jwt|key|credential)", re.IGNORECASE
)

SESSION_COOKIE_RE = re.compile(
    r"(?:session|sess|sid|auth|token|jwt|csrf|xsrf|remember|logged|user_id|clearance|__host-|__secure-)",
    re.IGNORECASE,
)

AUTH_QUERY_RE = re.compile(
    r"^(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|auth|key|sig|signature|jwt|secret)$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class HeaderMinimizationResult:
    kept_headers: dict[str, str] = field(default_factory=dict)
    dropped_headers: list[str] = field(default_factory=list)
    kept_cookies: dict[str, str] = field(default_factory=dict)
    dropped_cookies: list[str] = field(default_factory=list)
    auth_findings: list[AuthFinding] = field(default_factory=list)


def _decode_jwt_hint(token: str) -> str:
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        return ""
    try:
        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore"))
        if isinstance(payload, dict):
            fields = [k for k in ("sub", "iss", "exp", "scope") if k in payload]
            if fields:
                return f"JWT payload claims: {', '.join(fields)}"
        return "JWT formatted token"
    except (ValueError, TypeError):
        return ""


def _classify_header_auth(name: str, value: str) -> AuthFinding | None:
    lower = name.lower()
    if lower in {"content-type", "accept", "x-requested-with", "accept-charset"}:
        return None

    if lower in {"authorization", "proxy-authorization"}:
        trimmed = value.strip()
        prefix = trimmed.split(" ", 1)[0].lower() if " " in trimmed else ""
        if prefix == "bearer":
            raw_tok = trimmed.split(" ", 1)[1].strip()
            hint = _decode_jwt_hint(raw_tok)
            return AuthFinding(
                kind="bearer",
                location="header",
                name=name,
                masked_preview=mask_secret(value),
                env_var="API_BEARER_TOKEN",
                confidence=0.99,
                param_name="bearer_token",
                notes=hint or "Bearer token extracted to client credentials",
            )
        if prefix == "basic":
            return AuthFinding(
                kind="basic",
                location="header",
                name=name,
                masked_preview=mask_secret(value),
                env_var="API_BASIC_AUTH",
                confidence=0.99,
                param_name="basic_auth",
                notes="HTTP Basic authorization header",
            )
        if "hmac" in trimmed.lower() or "aws4" in trimmed.lower():
            return AuthFinding(
                kind="hmac_signature",
                location="header",
                name=name,
                masked_preview=mask_secret(value),
                env_var="API_AUTH_HEADER",
                confidence=0.95,
                param_name="auth_header",
                notes="Signed authorization header",
            )
        return AuthFinding(
            kind="custom_token",
            location="header",
            name=name,
            masked_preview=mask_secret(value),
            env_var="API_AUTHORIZATION",
            confidence=0.92,
            param_name="authorization",
            notes="Custom Authorization header",
        )

    if CSRF_HEADER_RE.search(name):
        return AuthFinding(
            kind="csrf",
            location="header",
            name=name,
            masked_preview=mask_secret(value),
            env_var=to_snake_case(name, avoid_python_kw=False).upper(),
            confidence=0.96,
            param_name=to_snake_case(name),
            notes="Anti-CSRF token header",
        )

    if API_KEY_HEADER_RE.match(name) or lower in {"ocp-apim-subscription-key"}:
        return AuthFinding(
            kind="api_key",
            location="header",
            name=name,
            masked_preview=mask_secret(value),
            env_var=to_snake_case(name, avoid_python_kw=False).upper(),
            confidence=0.96,
            param_name=to_snake_case(name),
            notes="API key header",
        )

    if SIG_HEADER_RE.search(name):
        return AuthFinding(
            kind="hmac_signature",
            location="header",
            name=name,
            masked_preview=mask_secret(value),
            env_var=to_snake_case(name, avoid_python_kw=False).upper(),
            confidence=0.9,
            param_name=to_snake_case(name),
            notes="Request signature header (may vary per request)",
        )

    if TOKEN_HEADER_RE.search(name):
        return AuthFinding(
            kind="custom_token",
            location="header",
            name=name,
            masked_preview=mask_secret(value),
            env_var=to_snake_case(name, avoid_python_kw=False).upper(),
            confidence=0.82,
            param_name=to_snake_case(name),
            notes="Likely authentication or token header",
        )

    return None


def _classify_cookie_auth(name: str, value: str) -> AuthFinding | None:
    lower = name.lower()
    if CSRF_HEADER_RE.search(name):
        return AuthFinding(
            kind="csrf",
            location="cookie",
            name=name,
            masked_preview=mask_secret(value),
            env_var=f"COOKIE_{to_snake_case(name, avoid_python_kw=False).upper()}",
            confidence=0.92,
            param_name=f"cookie_{to_snake_case(name)}",
            notes="CSRF cookie",
        )
    if lower == "cf_clearance":
        return AuthFinding(
            kind="session_cookie",
            location="cookie",
            name=name,
            masked_preview=mask_secret(value),
            env_var="CF_CLEARANCE",
            confidence=0.98,
            param_name="cf_clearance",
            notes="Cloudflare clearance cookie (bound to TLS/IP)",
        )
    if SESSION_COOKIE_RE.search(name):
        return AuthFinding(
            kind="session_cookie",
            location="cookie",
            name=name,
            masked_preview=mask_secret(value),
            env_var=f"COOKIE_{to_snake_case(name, avoid_python_kw=False).upper()}",
            confidence=0.88,
            param_name=f"cookie_{to_snake_case(name)}",
            notes="Session or auth cookie",
        )
    return None


def is_tracking_cookie(name: str) -> bool:
    if name in TRACKER_COOKIE_EXACT:
        return True
    lower = name.lower()
    if lower in {c.lower() for c in TRACKER_COOKIE_EXACT}:
        return True
    return any(lower.startswith(prefix.lower()) for prefix in TRACKER_COOKIE_PREFIXES)


def classify_auth(req: CapturedRequest) -> list[AuthFinding]:
    findings: list[AuthFinding] = []
    seen: set[tuple[str, str]] = set()

    for name, value in req.headers:
        finding = _classify_header_auth(name, value)
        if finding is not None:
            key = (finding.location, finding.name.lower())
            if key not in seen:
                seen.add(key)
                findings.append(finding)

    for cname, cval in req.cookies.items():
        if is_tracking_cookie(cname):
            continue
        finding = _classify_cookie_auth(cname, cval)
        if finding is not None:
            key = (finding.location, finding.name.lower())
            if key not in seen:
                seen.add(key)
                findings.append(finding)

    for qname, qval in req.query_params:
        if AUTH_QUERY_RE.match(qname) and qval:
            kind: AuthKind = "api_key" if "key" in qname.lower() else "custom_token"
            key = ("query", qname.lower())
            if key not in seen:
                seen.add(key)
                findings.append(
                    AuthFinding(
                        kind=kind,
                        location="query",
                        name=qname,
                        masked_preview=mask_secret(qval),
                        env_var=to_snake_case(qname, avoid_python_kw=False).upper(),
                        confidence=0.86,
                        param_name=to_snake_case(qname),
                        notes="Credential passed via query parameter",
                    )
                )

    return findings


def minimalize_headers_and_cookies(
    req: CapturedRequest,
    *,
    keep_origin_referer: bool = False,
    keep_user_agent: bool = False,
) -> HeaderMinimizationResult:
    result = HeaderMinimizationResult()

    for name, value in req.headers:
        lower = name.lower()
        if lower == "cookie":
            continue
        if lower == "user-agent" and keep_user_agent:
            result.kept_headers[name] = value
            continue
        if lower in {"origin", "referer"} and keep_origin_referer:
            result.kept_headers[name] = value
            continue
        if lower in NOISE_HEADERS or lower.startswith("sec-ch-") or lower.startswith("sec-fetch-"):
            result.dropped_headers.append(name)
            continue
        if lower == "accept" and value.strip() in {"*/*", ""}:
            result.dropped_headers.append(name)
            continue
        result.kept_headers[name] = value

    for cname, cval in req.cookies.items():
        if is_tracking_cookie(cname):
            result.dropped_cookies.append(cname)
        else:
            result.kept_cookies[cname] = cval

    result.auth_findings = classify_auth(req)
    return result


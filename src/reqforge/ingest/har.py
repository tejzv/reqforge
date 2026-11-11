from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qsl

from reqforge.errors import ParseError
from reqforge.models import BodyKind, CapturedRequest
from reqforge.utils import parse_cookie_header, parse_url_parts, try_parse_json


def _decode_har_content(content: dict[str, Any]) -> str | None:
    text = content.get("text")
    if text is None:
        return None
    if not isinstance(text, str):
        return str(text)
    if content.get("encoding") == "base64":
        try:
            raw_bytes = base64.b64decode(text, validate=False)
            return raw_bytes.decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return text
    return text


def _parse_har_body(
    post_data: dict[str, Any] | None,
    content_type_header: str | None,
) -> tuple[BodyKind, Any, list[tuple[str, str]], list[dict[str, Any]], str | None]:
    if not isinstance(post_data, dict) or not post_data:
        return "none", None, [], [], None

    mime = (post_data.get("mimeType") or content_type_header or "").lower()
    text = post_data.get("text")
    params = post_data.get("params")

    if "multipart/form-data" in mime:
        parts: list[dict[str, Any]] = []
        if isinstance(params, list):
            for p in params:
                if isinstance(p, dict) and p.get("name"):
                    parts.append(
                        {
                            "name": str(p["name"]),
                            "value": str(p.get("value", "")),
                            "filename": p.get("fileName"),
                            "content_type": p.get("contentType"),
                        }
                    )
        return "multipart", None, [], parts, text if isinstance(text, str) else None

    if "application/x-www-form-urlencoded" in mime:
        form_pairs: list[tuple[str, str]] = []
        if isinstance(params, list) and params:
            for p in params:
                if isinstance(p, dict) and "name" in p:
                    form_pairs.append((str(p["name"]), str(p.get("value", ""))))
        elif isinstance(text, str) and text:
            form_pairs = parse_qsl(text, keep_blank_values=True)
        return "form", None, form_pairs, [], text if isinstance(text, str) else None

    if isinstance(text, str):
        is_json, parsed_json = try_parse_json(text)
        if is_json and ("json" in mime or not mime or isinstance(parsed_json, (dict, list))):
            return "json", parsed_json, [], [], text
        if "json" in mime and text.strip():
            return "text", None, [], [], text
        if text:
            return "text", None, [], [], text

    if isinstance(params, list) and params:
        form_pairs = [
            (str(p["name"]), str(p.get("value", "")))
            for p in params
            if isinstance(p, dict) and "name" in p
        ]
        return "form", None, form_pairs, [], None

    return "none", None, [], [], None


def parse_har(raw_text: str) -> list[CapturedRequest]:
    cleaned = raw_text.lstrip("\ufeff").strip()
    if not cleaned:
        raise ParseError("Empty HAR input")

    try:
        payload = json.loads(cleaned)
    except ValueError as exc:
        raise ParseError(f"Invalid HAR JSON: {exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("log"), dict):
        raise ParseError("Invalid HAR structure: missing 'log' object")

    entries = payload["log"].get("entries")
    if not isinstance(entries, list):
        raise ParseError("Invalid HAR structure: missing 'log.entries' array")

    results: list[CapturedRequest] = []
    for idx, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        req = entry.get("request")
        if not isinstance(req, dict):
            continue

        raw_url = str(req.get("url") or "").strip()
        if not raw_url or raw_url.startswith(("data:", "blob:", "chrome-extension:", "about:")):
            continue

        method = str(req.get("method") or "GET").upper().strip()
        scheme, host, port, path, url_query = parse_url_parts(raw_url)
        if not host:
            continue

        query_params = list(url_query)
        if not query_params and isinstance(req.get("queryString"), list):
            for q in req["queryString"]:
                if isinstance(q, dict) and "name" in q:
                    query_params.append((str(q["name"]), str(q.get("value", ""))))

        headers: list[tuple[str, str]] = []
        cookies: dict[str, str] = {}
        content_type_header: str | None = None

        raw_headers = req.get("headers")
        if isinstance(raw_headers, list):
            for h in raw_headers:
                if not isinstance(h, dict):
                    continue
                name = str(h.get("name") or "").strip()
                value = str(h.get("value") or "")
                if not name:
                    continue
                lower = name.lower()
                if lower.startswith(":"):
                    continue
                if lower == "cookie":
                    cookies.update(parse_cookie_header(value))
                    continue
                if lower == "content-type":
                    content_type_header = value
                headers.append((name, value))

        raw_cookies = req.get("cookies")
        if isinstance(raw_cookies, list):
            for c in raw_cookies:
                if isinstance(c, dict) and c.get("name"):
                    cookies[str(c["name"])] = str(c.get("value", ""))

        body_kind, body_json, body_form, body_multipart, body_text = _parse_har_body(
            req.get("postData"),
            content_type_header,
        )

        resp = entry.get("response") if isinstance(entry.get("response"), dict) else {}
        resp_status = resp.get("status") if isinstance(resp.get("status"), int) else None
        resp_headers: list[tuple[str, str]] = []
        resp_ct: str | None = None
        if isinstance(resp.get("headers"), list):
            for rh in resp["headers"]:
                if isinstance(rh, dict) and rh.get("name"):
                    rname = str(rh["name"]).strip()
                    rval = str(rh.get("value") or "")
                    resp_headers.append((rname, rval))
                    if rname.lower() == "content-type":
                        resp_ct = rval

        resp_content = resp.get("content") if isinstance(resp.get("content"), dict) else {}
        if not resp_ct and isinstance(resp_content.get("mimeType"), str):
            resp_ct = resp_content["mimeType"]

        resp_text = _decode_har_content(resp_content)
        resp_json: Any = None
        if resp_text is not None:
            is_json, parsed_resp = try_parse_json(resp_text)
            if is_json:
                resp_json = parsed_resp

        resource_type = entry.get("_resourceType")
        if resource_type is not None:
            resource_type = str(resource_type).lower()

        results.append(
            CapturedRequest(
                id=f"har_{idx}",
                method=method,
                url=raw_url,
                scheme=scheme,
                host=host,
                port=port,
                path=path,
                query_params=query_params,
                headers=headers,
                cookies=cookies,
                body_kind=body_kind,
                body_json=body_json,
                body_form=body_form,
                body_multipart=body_multipart,
                body_text=body_text,
                response_status=resp_status,
                response_headers=resp_headers,
                response_content_type=resp_ct,
                response_body_text=resp_text,
                response_json=resp_json,
                source_type="har",
                timestamp=str(entry.get("startedDateTime")) if entry.get("startedDateTime") else None,
                resource_type=resource_type,
            )
        )

    if not results:
        raise ParseError("HAR file contained no valid HTTP requests")
    return results


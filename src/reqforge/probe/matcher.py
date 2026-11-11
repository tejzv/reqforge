from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reqforge.utils import try_parse_json

BLOCK_MARKERS = (
    "attention required! | cloudflare",
    "just a moment...",
    "cf-browser-verification",
    "access denied",
    "request blocked",
)


@dataclass(slots=True)
class ResponseSnapshot:
    status: int
    content_type: str
    body_text: str
    json_body: Any = None

    @classmethod
    def from_raw(cls, status: int, content_type: str, body_text: str) -> ResponseSnapshot:
        _, parsed = try_parse_json(body_text)
        return cls(
            status=status,
            content_type=(content_type or "").split(";", 1)[0].strip().lower(),
            body_text=body_text,
            json_body=parsed,
        )


def _media_family(content_type: str) -> str:
    ct = content_type.lower().strip()
    if not ct:
        return ""
    if "json" in ct:
        return "json"
    if "html" in ct:
        return "html"
    if "xml" in ct:
        return "xml"
    if "text/" in ct:
        return "text"
    return ct


def _contains_block_marker(text: str) -> bool:
    lower = text[:4096].lower()
    return any(marker in lower for marker in BLOCK_MARKERS)


def _json_shapes_match(baseline: Any, candidate: Any) -> tuple[bool, str]:
    if type(baseline) is not type(candidate):
        return False, f"json root type mismatch: {type(baseline).__name__} vs {type(candidate).__name__}"

    if isinstance(baseline, dict) and isinstance(candidate, dict):
        base_keys = set(baseline.keys())
        cand_keys = set(candidate.keys())
        if not base_keys and not cand_keys:
            return True, "both empty objects"
        error_keys = {"error", "errors", "detail", "message"}
        unexpected_err = (cand_keys & error_keys) - base_keys
        if unexpected_err and not (base_keys & cand_keys):
            return False, f"error payload returned: {sorted(unexpected_err)}"
        if len(base_keys) <= 2:
            if not base_keys.issubset(cand_keys):
                return False, f"missing keys: {sorted(base_keys - cand_keys)}"
        else:
            overlap = len(base_keys & cand_keys) / len(base_keys)
            if overlap < 0.75:
                return False, f"key overlap too low ({overlap:.2f})"

        for k in base_keys & cand_keys:
            bval = baseline[k]
            cval = candidate[k]
            if isinstance(bval, dict) and isinstance(cval, dict) and bval:
                nested_keys = set(bval.keys())
                cand_nested = set(cval.keys())
                if len(nested_keys & cand_nested) / len(nested_keys) < 0.5:
                    return False, f"nested object mismatch on '{k}'"
        return True, "json object shape matched"

    if isinstance(baseline, list) and isinstance(candidate, list):
        if len(baseline) > 0 and len(candidate) == 0:
            return False, "non-empty baseline array became empty"
        if baseline and candidate and isinstance(baseline[0], dict) and isinstance(candidate[0], dict):
            bkeys = set(baseline[0].keys())
            ckeys = set(candidate[0].keys())
            if bkeys and (len(bkeys & ckeys) / len(bkeys)) < 0.5:
                return False, "array item keys mismatch"
        return True, "json array shape matched"

    return True, "json primitive matched"


def is_equivalent_response(
    baseline: ResponseSnapshot,
    candidate: ResponseSnapshot,
) -> tuple[bool, str]:
    if candidate.status != baseline.status:
        return False, f"status {candidate.status} != baseline {baseline.status}"

    base_fam = _media_family(baseline.content_type)
    cand_fam = _media_family(candidate.content_type)
    if base_fam and cand_fam and base_fam != cand_fam:
        return False, f"media type {cand_fam} != baseline {base_fam}"

    if _contains_block_marker(candidate.body_text) and not _contains_block_marker(baseline.body_text):
        return False, "response contains anti-bot block marker"

    if baseline.json_body is not None:
        if candidate.json_body is None:
            return False, "candidate body failed JSON parsing"
        return _json_shapes_match(baseline.json_body, candidate.json_body)

    base_len = len(baseline.body_text)
    cand_len = len(candidate.body_text)
    if base_len > 0 and cand_len == 0:
        return False, "candidate body empty"
    if base_len > 50:
        ratio = cand_len / base_len
        if ratio < 0.4 or ratio > 2.5:
            return False, f"body length ratio {ratio:.2f} out of bounds"

    return True, "matched"


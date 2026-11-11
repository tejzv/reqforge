from __future__ import annotations

import re
from typing import Any

from reqforge.filter.headers import AUTH_QUERY_RE
from reqforge.models import CapturedRequest, ParamSpec
from reqforge.utils import infer_scalar_type, to_snake_case

TRACKER_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "dclid",
    "gbraid",
    "wbraid",
    "mc_cid",
    "mc_eid",
    "yclid",
    "msclkid",
    "_ga",
    "_gl",
    "hsCtaTracking",
    "_hsenc",
    "_hsmi",
}

CACHEBUSTER_RE = re.compile(r"^(?:_|_t|ts|timestamp|cb|cachebuster|rnd|rand|nonce)$", re.IGNORECASE)
PAGINATION_RE = re.compile(
    r"^(?:page|per_page|page_size|limit|offset|cursor|next_cursor|next_token|page_token|after|before|skip|take)$",
    re.IGNORECASE,
)


def _coerce_sample(raw: str, inferred_type: str) -> Any:
    if inferred_type == "int":
        try:
            return int(raw)
        except ValueError:
            return raw
    if inferred_type == "float":
        try:
            return float(raw)
        except ValueError:
            return raw
    if inferred_type == "bool":
        return raw.strip().lower() == "true"
    return raw


def _unique_param_name(raw_name: str, used: set[str]) -> str:
    candidate = to_snake_case(raw_name)
    while candidate in used:
        candidate = f"{candidate}_param"
    used.add(candidate)
    return candidate


def infer_query_params(
    samples: list[CapturedRequest],
    *,
    drop_trackers: bool = True,
) -> list[ParamSpec]:
    if not samples:
        return []

    by_wire: dict[str, list[str]] = {}
    presence_count: dict[str, int] = {}

    for req in samples:
        seen_in_req: set[str] = set()
        for k, v in req.query_params:
            if drop_trackers and k.lower() in TRACKER_QUERY_PARAMS:
                continue
            by_wire.setdefault(k, []).append(v)
            if k not in seen_in_req:
                seen_in_req.add(k)
                presence_count[k] = presence_count.get(k, 0) + 1

    total_samples = len(samples)
    used_names: set[str] = set()
    specs: list[ParamSpec] = []

    for wire_name, raw_values in by_wire.items():
        inferred_type, _ = infer_scalar_type(raw_values)
        typed_samples = [_coerce_sample(v, inferred_type) for v in raw_values]
        distinct_values = list(dict.fromkeys(typed_samples))
        always_present = presence_count.get(wire_name, 0) == total_samples
        is_noise = wire_name.lower() in TRACKER_QUERY_PARAMS
        is_auth = bool(AUTH_QUERY_RE.match(wire_name))

        reason = "query_param"
        if is_auth:
            reason = "auth_query_param"
        elif CACHEBUSTER_RE.match(wire_name):
            reason = "ephemeral_timestamp_or_nonce"
        elif PAGINATION_RE.match(wire_name):
            reason = "pagination"
        elif len(distinct_values) > 1:
            reason = "varies_across_requests"

        default_val: Any = None
        if not is_auth and reason != "ephemeral_timestamp_or_nonce" and len(distinct_values) == 1:
            default_val = distinct_values[0]

        required = always_present and (default_val is None) and not is_auth

        specs.append(
            ParamSpec(
                name=_unique_param_name(wire_name, used_names),
                wire_name=wire_name,
                location="query",
                inferred_type=inferred_type,
                required=required,
                default_value=default_val,
                sample_values=distinct_values,
                dynamic_reason=reason,
                is_noise=is_noise,
            )
        )

    return specs


def infer_form_params(samples: list[CapturedRequest]) -> list[ParamSpec]:
    if not samples:
        return []

    by_wire: dict[str, list[str]] = {}
    presence_count: dict[str, int] = {}

    for req in samples:
        pairs: list[tuple[str, str]] = []
        if req.body_kind == "form":
            pairs = req.body_form
        elif req.body_kind == "multipart":
            pairs = [(str(p.get("name", "")), str(p.get("value", ""))) for p in req.body_multipart if p.get("name")]

        seen_in_req: set[str] = set()
        for k, v in pairs:
            by_wire.setdefault(k, []).append(v)
            if k not in seen_in_req:
                seen_in_req.add(k)
                presence_count[k] = presence_count.get(k, 0) + 1

    total_samples = len(samples)
    used_names: set[str] = set()
    specs: list[ParamSpec] = []

    for wire_name, raw_values in by_wire.items():
        inferred_type, _ = infer_scalar_type(raw_values)
        typed_samples = [_coerce_sample(v, inferred_type) for v in raw_values]
        distinct_values = list(dict.fromkeys(typed_samples))
        always_present = presence_count.get(wire_name, 0) == total_samples
        default_val = distinct_values[0] if len(distinct_values) == 1 else None

        specs.append(
            ParamSpec(
                name=_unique_param_name(wire_name, used_names),
                wire_name=wire_name,
                location="body",
                inferred_type=inferred_type,
                required=always_present and default_val is None,
                default_value=default_val,
                sample_values=distinct_values,
                dynamic_reason="form_field",
            )
        )

    return specs


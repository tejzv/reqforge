from __future__ import annotations

import re
from typing import Any

from reqforge.filter.headers import HeaderMinimizationResult, minimalize_headers_and_cookies
from reqforge.infer.params import infer_form_params, infer_query_params
from reqforge.infer.schema import infer_schema_from_samples
from reqforge.models import AuthFinding, CapturedRequest, EndpointGroup, ParamSpec
from reqforge.utils import (
    infer_scalar_type,
    is_object_id,
    is_uuid,
    singularize,
    to_pascal_case,
    to_snake_case,
)

STATIC_SEGMENT_KEYWORDS = {
    "api",
    "v1",
    "v2",
    "v3",
    "v4",
    "v5",
    "rest",
    "graphql",
    "oauth",
    "oauth2",
    "auth",
    "login",
    "logout",
    "token",
    "refresh",
    "me",
    "profile",
    "settings",
    "health",
    "status",
    "search",
    "query",
    "batch",
    "public",
    "private",
    "internal",
    "admin",
    "metadata",
    "config",
    "webhook",
    "webhooks",
    "callback",
}

NUMERIC_SEG_RE = re.compile(r"^\d+$")
PREFIXED_ID_RE = re.compile(r"^[a-zA-Z]{2,6}[_-][a-zA-Z0-9]{4,}$")
HIGH_ENTROPY_RE = re.compile(r"^[a-zA-Z0-9_-]{16,}$")
VERSION_RE = re.compile(r"^v\d+$", re.IGNORECASE)
GQL_QUERY_NAME_RE = re.compile(r"^\s*(?:query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def _split_path(path: str) -> list[str]:
    return [seg for seg in path.split("/") if seg != ""]


def _detect_single_dynamic_segment(seg: str, idx: int) -> str | None:
    lower = seg.lower()
    if lower in STATIC_SEGMENT_KEYWORDS or VERSION_RE.match(seg):
        return None
    if is_uuid(seg):
        return "uuid_segment"
    if NUMERIC_SEG_RE.match(seg):
        return "numeric_id"
    if is_object_id(seg):
        return "object_id"
    if PREFIXED_ID_RE.match(seg) and any(ch.isdigit() for ch in seg):
        return "prefixed_id"
    if HIGH_ENTROPY_RE.match(seg) and any(ch.isdigit() for ch in seg):
        return "opaque_token_or_id"
    if idx > 0 and "." not in seg and len(seg) >= 20 and re.search(r"[A-Z].*[0-9]|[0-9].*[A-Z]", seg):
        return "opaque_token_or_id"
    return None


def _graphql_op_name(req: CapturedRequest) -> str | None:
    if isinstance(req.body_json, dict):
        op = req.body_json.get("operationName")
        if isinstance(op, str) and op.strip():
            return op.strip()
        q = req.body_json.get("query")
        if isinstance(q, str):
            m = GQL_QUERY_NAME_RE.match(q)
            if m:
                return m.group(1)
    return None


def _jsonrpc_method(req: CapturedRequest) -> str | None:
    if isinstance(req.body_json, dict) and "jsonrpc" in req.body_json:
        method = req.body_json.get("method")
        if isinstance(method, str) and method.strip():
            return method.strip()
    return None


def _derive_param_name(segments: list[str], param_idx: int, used: set[str]) -> str:
    candidate = "item_id"
    if param_idx > 0:
        prev = segments[param_idx - 1]
        if prev.lower() not in {"api", "v1", "v2", "v3", "rest"} and _detect_single_dynamic_segment(prev, param_idx - 1) is None:
            base = singularize(to_snake_case(prev, avoid_python_kw=False))
            if base and base != "value":
                candidate = f"{base}_id"
    name = candidate
    counter = 2
    while name in used:
        name = f"{candidate}_{counter}"
        counter += 1
    used.add(name)
    return name


def _is_generic_prefix(seg: str) -> bool:
    low = seg.lower()
    return low in {"api", "rest", "public", "private", "internal"} or bool(VERSION_RE.match(low))


def _can_cluster(segs_a: list[str], segs_b: list[str]) -> bool:
    if len(segs_a) != len(segs_b):
        return False
    if not segs_a:
        return True
    diff_positions = 0
    meaningful_matches = 0
    for i, (sa, sb) in enumerate(zip(segs_a, segs_b)):
        if sa == sb:
            if not _is_generic_prefix(sa):
                meaningful_matches += 1
            continue
        if VERSION_RE.match(sa) or VERSION_RE.match(sb):
            return False
        if sa.lower() in STATIC_SEGMENT_KEYWORDS or sb.lower() in STATIC_SEGMENT_KEYWORDS:
            return False
        if i == 0 and (_is_generic_prefix(sa) or _is_generic_prefix(sb)):
            return False
        diff_positions += 1
    if diff_positions == 0:
        return True
    if meaningful_matches == 0:
        return False
    return diff_positions <= max(1, len(segs_a) // 2)


def _derive_operation_id(
    method: str,
    path_template: str,
    samples: list[CapturedRequest],
    used_ops: set[str],
) -> str:
    gql_names = [_graphql_op_name(s) for s in samples]
    valid_gql = [g for g in gql_names if g]
    rpc_names = [_jsonrpc_method(s) for s in samples]
    valid_rpc = [r for r in rpc_names if r]
    if valid_gql and "graphql" in path_template.lower():
        base = f"graphql_{to_snake_case(valid_gql[0], avoid_python_kw=False)}"
    elif valid_rpc:
        base = f"rpc_{to_snake_case(valid_rpc[0], avoid_python_kw=False)}"
    else:
        segs = _split_path(path_template)
        meaningful: list[str] = []
        for idx, seg in enumerate(segs):
            if seg.startswith("{") and seg.endswith("}"):
                continue
            low = seg.lower()
            if low in {"api", "rest"} or VERSION_RE.match(low):
                continue
            word = to_snake_case(seg, avoid_python_kw=False)
            if idx + 1 < len(segs) and segs[idx + 1].startswith("{"):
                word = singularize(word)
            meaningful.append(word)

        ends_with_param = bool(segs) and segs[-1].startswith("{")
        m_upper = method.upper()
        if not meaningful:
            verb = m_upper.lower()
            base = f"{verb}_root"
        else:
            last_seg = meaningful[-1]
            if m_upper == "GET":
                has_list_resp = any(isinstance(s.response_json, list) for s in samples)
                if ends_with_param:
                    meaningful[-1] = singularize(last_seg)
                    verb = "get"
                elif has_list_resp or (last_seg.endswith("s") and singularize(last_seg) != last_seg):
                    verb = "list"
                else:
                    verb = "get"
            elif m_upper == "POST":
                verb = "create"
            elif m_upper == "PUT":
                if ends_with_param:
                    meaningful[-1] = singularize(last_seg)
                verb = "update"
            elif m_upper == "PATCH":
                if ends_with_param:
                    meaningful[-1] = singularize(last_seg)
                verb = "patch"
            elif m_upper == "DELETE":
                if ends_with_param:
                    meaningful[-1] = singularize(last_seg)
                verb = "delete"
            else:
                verb = m_upper.lower()

            if meaningful and meaningful[0] in {"get", "list", "create", "update", "delete", "search", "fetch"}:
                base = "_".join(meaningful)
            else:
                base = f"{verb}_{'_'.join(meaningful)}"

    base = to_snake_case(base)
    candidate = base
    counter = 2
    while candidate in used_ops:
        candidate = f"{base}_{counter}"
        counter += 1
    used_ops.add(candidate)
    return candidate


def _merge_auth_findings(all_findings: list[list[AuthFinding]]) -> list[AuthFinding]:
    merged: list[AuthFinding] = []
    seen: set[tuple[str, str]] = set()
    for group in all_findings:
        for f in group:
            key = (f.location, f.name.lower())
            if key not in seen:
                seen.add(key)
                merged.append(f)
    return merged


def _build_endpoint_group(
    samples: list[CapturedRequest],
    *,
    minimization_results: dict[str, HeaderMinimizationResult],
    used_ops: set[str],
    used_model_names: set[str],
    drop_trackers: bool,
) -> EndpointGroup:
    first = samples[0]
    base_url = first.base_url()
    method = first.method.upper()
    split_paths = [_split_path(s.path) for s in samples]
    seg_count = len(split_paths[0])

    template_parts: list[str] = []
    path_params: list[ParamSpec] = []
    used_param_names: set[str] = set()

    for idx in range(seg_count):
        values_at_idx = [sp[idx] for sp in split_paths]
        distinct = list(dict.fromkeys(values_at_idx))
        reason = ""
        if len(distinct) > 1:
            reason = "varies_across_requests"
        else:
            detected = _detect_single_dynamic_segment(distinct[0], idx)
            if detected:
                reason = detected

        if reason:
            param_name = _derive_param_name(split_paths[0], idx, used_param_names)
            inferred_type, _ = infer_scalar_type(distinct)
            typed_samples: list[Any] = []
            for v in distinct:
                if inferred_type == "int":
                    typed_samples.append(int(v))
                else:
                    typed_samples.append(v)
            path_params.append(
                ParamSpec(
                    name=param_name,
                    wire_name=param_name,
                    location="path",
                    inferred_type=inferred_type,
                    required=True,
                    sample_values=typed_samples,
                    dynamic_reason=reason,
                )
            )
            template_parts.append(f"{{{param_name}}}")
        else:
            template_parts.append(distinct[0])

    path_template = "/" + "/".join(template_parts) if template_parts else "/"
    operation_id = _derive_operation_id(method, path_template, samples, used_ops)

    kept_headers: dict[str, str] = {}
    dropped_headers_set: list[str] = []
    kept_cookies: dict[str, str] = {}
    dropped_cookies_set: list[str] = []
    auth_lists: list[list[AuthFinding]] = []

    for s in samples:
        m = minimization_results[s.id]
        for hk, hv in m.kept_headers.items():
            kept_headers.setdefault(hk, hv)
        for dh in m.dropped_headers:
            if dh not in dropped_headers_set:
                dropped_headers_set.append(dh)
        for ck, cv in m.kept_cookies.items():
            kept_cookies.setdefault(ck, cv)
        for dc in m.dropped_cookies:
            if dc not in dropped_cookies_set:
                dropped_cookies_set.append(dc)
        auth_lists.append(m.auth_findings)

    auth_findings = _merge_auth_findings(auth_lists)
    query_params = infer_query_params(samples, drop_trackers=drop_trackers)

    body_kinds = [s.body_kind for s in samples if s.body_kind != "none"]
    request_body_kind = body_kinds[0] if body_kinds else "none"

    req_json_samples = [s.body_json for s in samples if s.body_kind == "json" and s.body_json is not None]
    request_schema = None
    request_model_name = None
    request_example = req_json_samples[0] if req_json_samples else None
    if req_json_samples:
        req_root = f"{to_pascal_case(operation_id)}Request"
        request_schema = infer_schema_from_samples(
            req_json_samples,
            root_name=req_root,
            used_names=used_model_names,
        )
        if request_schema and request_schema.kind == "object" and request_schema.model_name:
            request_model_name = request_schema.model_name

    request_form_params: list[ParamSpec] = []
    if request_body_kind in {"form", "multipart"}:
        request_form_params = infer_form_params(samples)

    resp_json_samples = [s.response_json for s in samples if s.response_json is not None]
    response_schema = None
    response_model_name = None
    response_example = resp_json_samples[0] if resp_json_samples else None
    if resp_json_samples:
        resp_root = f"{to_pascal_case(operation_id)}Response"
        response_schema = infer_schema_from_samples(
            resp_json_samples,
            root_name=resp_root,
            used_names=used_model_names,
        )
        if response_schema and response_schema.kind == "object" and response_schema.model_name:
            response_model_name = response_schema.model_name

    statuses = [s.response_status for s in samples if s.response_status is not None]
    response_status = statuses[0] if statuses else 200

    return EndpointGroup(
        operation_id=operation_id,
        method=method,
        base_url=base_url,
        path_template=path_template,
        path_params=path_params,
        query_params=query_params,
        required_headers=kept_headers,
        required_cookies=kept_cookies,
        auth_findings=auth_findings,
        request_body_kind=request_body_kind,
        request_schema=request_schema,
        request_model_name=request_model_name,
        request_example=request_example,
        request_form_params=request_form_params,
        response_schema=response_schema,
        response_model_name=response_model_name,
        response_example=response_example,
        response_status=response_status,
        dropped_headers=dropped_headers_set,
        dropped_cookies=dropped_cookies_set,
        samples=samples,
    )


def cluster_and_infer_endpoints(
    requests: list[CapturedRequest],
    *,
    minimization_results: dict[str, HeaderMinimizationResult] | None = None,
    drop_trackers: bool = True,
) -> list[EndpointGroup]:
    if not requests:
        return []

    mins = minimization_results or {r.id: minimalize_headers_and_cookies(r) for r in requests}

    buckets: dict[tuple[str, str, int, str | None], list[CapturedRequest]] = {}
    for req in requests:
        segs = _split_path(req.path)
        op_disc: str | None = None
        if "graphql" in req.path.lower():
            op_disc = _graphql_op_name(req)
        elif _jsonrpc_method(req):
            op_disc = _jsonrpc_method(req)
        key = (req.base_url(), req.method.upper(), len(segs), op_disc)
        buckets.setdefault(key, []).append(req)

    clusters: list[list[CapturedRequest]] = []
    for bucket_reqs in buckets.values():
        local_clusters: list[list[CapturedRequest]] = []
        for req in bucket_reqs:
            req_segs = _split_path(req.path)
            placed = False
            for cl in local_clusters:
                rep_segs = _split_path(cl[0].path)
                if _can_cluster(rep_segs, req_segs):
                    cl.append(req)
                    placed = True
                    break
            if not placed:
                local_clusters.append([req])
        clusters.extend(local_clusters)

    used_ops: set[str] = set()
    used_model_names: set[str] = set()
    endpoints: list[EndpointGroup] = []
    for cl in clusters:
        endpoints.append(
            _build_endpoint_group(
                cl,
                minimization_results=mins,
                used_ops=used_ops,
                used_model_names=used_model_names,
                drop_trackers=drop_trackers,
            )
        )

    return endpoints

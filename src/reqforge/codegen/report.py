from __future__ import annotations

import json

from reqforge.models import ForgeResult


def result_to_json(result: ForgeResult) -> str:
    return json.dumps(result.to_dict(), indent=2)


def format_terminal_report(result: ForgeResult) -> str:
    lines: list[str] = [
        f"ReqForge Analysis: {result.client_name}",
        f"Base URL: {result.base_url}",
        f"Requests: {result.total_ingested} ingested -> {len(result.endpoints)} endpoint(s) kept, {len(result.filtered_out)} filtered",
        f"Recommended Transport: {result.recommended_transport}",
    ]

    if result.filtered_out:
        by_reason: dict[str, int] = {}
        for item in result.filtered_out:
            by_reason[item.reason] = by_reason.get(item.reason, 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(by_reason.items()))
        lines.append(f"Filtered Breakdown: {summary}")

    if result.auth_findings:
        lines.append("Auth Findings:")
        for af in result.auth_findings:
            lines.append(
                f"  - [{af.kind}] {af.location}:{af.name} ({af.masked_preview}) -> env {af.env_var}"
            )

    lines.append("Endpoints:")
    for ep in result.endpoints:
        kept_h = len(ep.required_headers)
        drop_h = len(ep.dropped_headers)
        kept_c = len(ep.required_cookies)
        drop_c = len(ep.dropped_cookies)
        lines.append(
            f"  - {ep.method} {ep.path_template} -> {ep.operation_id} "
            f"(headers: {kept_h} kept / {drop_h} stripped; cookies: {kept_c} kept / {drop_c} stripped)"
        )
        if ep.path_params:
            p_desc = ", ".join(f"{p.name}:{p.inferred_type}" for p in ep.path_params)
            lines.append(f"      path params: {p_desc}")
        if ep.query_params:
            q_desc = ", ".join(f"{p.wire_name}:{p.inferred_type}" for p in ep.query_params)
            lines.append(f"      query params: {q_desc}")
        if ep.request_model_name or ep.response_model_name:
            lines.append(
                f"      models: req={ep.request_model_name or '-'} resp={ep.response_model_name or '-'}"
            )
        if ep.probe_report:
            pr = ep.probe_report
            lines.append(
                f"      probe: status={pr.baseline_status} transport={pr.recommended_transport} "
                f"probes={pr.total_probes} kept_headers={pr.required_headers}"
            )

    if result.warnings:
        lines.append("Warnings:")
        for w in result.warnings:
            lines.append(f"  ! {w}")

    return "\n".join(lines)

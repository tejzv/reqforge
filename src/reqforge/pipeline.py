from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from reqforge.codegen import (
    GeneratedFile,
    generate_javascript_sdk,
    generate_python_sdk,
    generate_typescript_sdk,
)
from reqforge.filter import FilterOptions, filter_requests, minimalize_headers_and_cookies
from reqforge.infer import cluster_and_infer_endpoints, infer_schema_from_samples
from reqforge.ingest import ingest_text
from reqforge.models import (
    AuthFinding,
    CapturedRequest,
    EndpointGroup,
    ForgeResult,
    TargetLanguage,
    TransportMode,
)
from reqforge.probe import ProbeConfig, minimize_request_live
from reqforge.utils import to_pascal_case


@dataclass(slots=True)
class ForgeOptions:
    client_name: str = ""
    base_url: str = ""
    target: TargetLanguage = "python"
    transport: TransportMode = "auto"
    single_file: bool = False
    probe: bool = False
    probe_config: ProbeConfig = field(default_factory=ProbeConfig)
    filter_options: FilterOptions = field(default_factory=FilterOptions)
    keep_user_agent: bool = False
    keep_origin_referer: bool = False


def _derive_client_name(host: str) -> str:
    clean = host.lower().strip()
    if not clean or clean in {"127.0.0.1", "localhost", "::1"}:
        return "LocalApi"
    parts = [p for p in clean.split(".") if p and p not in {"www", "api", "app", "m", "v1", "v2"}]
    if not parts:
        parts = [p for p in clean.split(".") if p]
    if len(parts) >= 2 and parts[-1] in {"com", "org", "net", "io", "dev", "ai", "co", "in", "app"}:
        core = parts[-2]
    else:
        core = parts[0] if parts else "Api"
    return to_pascal_case(core)


def _pick_primary_base_url(endpoints: list[EndpointGroup], fallback: list[CapturedRequest]) -> tuple[str, str]:
    if endpoints:
        counts = Counter(ep.base_url for ep in endpoints)
        primary_base = counts.most_common(1)[0][0]
        primary_host = endpoints[0].samples[0].host if endpoints[0].samples else ""
        for ep in endpoints:
            if ep.base_url == primary_base and ep.samples:
                primary_host = ep.samples[0].host
                break
        return primary_base, primary_host
    if fallback:
        return fallback[0].base_url(), fallback[0].host
    return "https://api.example.com", "api.example.com"


def _refresh_auth_after_probe(ep: EndpointGroup) -> None:
    kept_header_lower = {k.lower() for k in ep.required_headers}
    kept_cookie_lower = {k.lower() for k in ep.required_cookies}
    filtered_auth: list[AuthFinding] = []
    for af in ep.auth_findings:
        if af.location == "header" and af.name.lower() not in kept_header_lower:
            continue
        if af.location == "cookie" and af.name.lower() not in kept_cookie_lower:
            continue
        filtered_auth.append(af)
    ep.auth_findings = filtered_auth


def _collect_global_auth(endpoints: list[EndpointGroup]) -> list[AuthFinding]:
    merged: list[AuthFinding] = []
    seen: set[tuple[str, str]] = set()
    for ep in endpoints:
        for af in ep.auth_findings:
            key = (af.location, af.name.lower())
            if key not in seen:
                seen.add(key)
                merged.append(af)
    return merged


def _build_targets(result: ForgeResult, options: ForgeOptions) -> dict[str, list[GeneratedFile]]:
    py_transport: Literal["httpx", "curl_cffi"] = (
        options.transport if options.transport in {"httpx", "curl_cffi"} else result.recommended_transport
    )
    outputs: dict[str, list[GeneratedFile]] = {}
    if options.target == "python":
        outputs["python"] = generate_python_sdk(
            result,
            transport=py_transport,
            single_file=options.single_file,
        )
    elif options.target == "typescript":
        outputs["typescript"] = generate_typescript_sdk(result)
    elif options.target == "javascript":
        outputs["javascript"] = generate_javascript_sdk(result)
    else:
        outputs["python"] = generate_python_sdk(
            result,
            transport=py_transport,
            single_file=options.single_file,
        )
        outputs["typescript"] = generate_typescript_sdk(result)
        outputs["javascript"] = generate_javascript_sdk(result)
    return outputs


def _prepare_stage(
    raw_input: str,
    *,
    source_name: str,
    opts: ForgeOptions,
) -> tuple[int, list[CapturedRequest], list[Any], list[EndpointGroup], list[str]]:
    ingested = ingest_text(raw_input, source_name=source_name)
    total_ingested = len(ingested)

    kept, filtered_out = filter_requests(ingested, opts.filter_options)
    warnings: list[str] = []

    if (
        not kept
        and len(ingested) == 1
        and ingested[0].source_type == "curl"
        and not opts.filter_options.exclude_hosts
    ):
        kept = ingested
        filtered_out = []
        warnings.append(
            "Single explicit cURL matched a noise heuristic; preserved automatically"
        )

    min_results = {
        req.id: minimalize_headers_and_cookies(
            req,
            keep_origin_referer=opts.keep_origin_referer,
            keep_user_agent=opts.keep_user_agent,
        )
        for req in kept
    }

    endpoints = cluster_and_infer_endpoints(
        kept,
        minimization_results=min_results,
        drop_trackers=opts.filter_options.drop_trackers,
    )
    return total_ingested, kept or ingested, filtered_out, endpoints, warnings


def _finalize_stage(
    *,
    total_ingested: int,
    fallback_reqs: list[CapturedRequest],
    filtered_out: list[Any],
    endpoints: list[EndpointGroup],
    warnings: list[str],
    recommended_transport: Literal["httpx", "curl_cffi"],
    opts: ForgeOptions,
) -> tuple[ForgeResult, dict[str, list[GeneratedFile]]]:
    if not opts.probe:
        for ep in endpoints:
            if any(af.name.lower() == "cf_clearance" for af in ep.auth_findings) and opts.transport == "auto":
                recommended_transport = "curl_cffi"
                warnings.append(
                    "Detected cf_clearance cookie; recommending curl_cffi TLS impersonation"
                )
                break

    primary_base, primary_host = _pick_primary_base_url(endpoints, fallback_reqs)
    base_url = opts.base_url.rstrip("/") if opts.base_url else primary_base
    client_name = opts.client_name.strip() or _derive_client_name(primary_host)

    result = ForgeResult(
        base_url=base_url,
        client_name=client_name,
        endpoints=endpoints,
        filtered_out=filtered_out,
        auth_findings=_collect_global_auth(endpoints),
        recommended_transport=recommended_transport,
        warnings=list(dict.fromkeys(warnings)),
        total_ingested=total_ingested,
    )

    generated = _build_targets(result, opts)
    return result, generated


async def run_pipeline_async(
    raw_input: str,
    *,
    source_name: str = "",
    options: ForgeOptions | None = None,
) -> tuple[ForgeResult, dict[str, list[GeneratedFile]]]:
    opts = options or ForgeOptions()
    total_ingested, fallback_reqs, filtered_out, endpoints, warnings = _prepare_stage(
        raw_input,
        source_name=source_name,
        opts=opts,
    )

    recommended_transport: Literal["httpx", "curl_cffi"] = (
        opts.transport if opts.transport in {"httpx", "curl_cffi"} else "httpx"
    )

    if opts.probe and endpoints:
        used_model_names: set[str] = set()
        for ep in endpoints:
            if ep.request_model_name:
                used_model_names.add(ep.request_model_name)
            if ep.response_model_name:
                used_model_names.add(ep.response_model_name)

        for ep in endpoints:
            if not ep.samples:
                continue
            rep = ep.samples[0]
            outcome = await minimize_request_live(rep, opts.probe_config)
            ep.probe_report = outcome.report
            if outcome.report and outcome.report.baseline_matched:
                ep.required_headers = outcome.kept_headers
                ep.dropped_headers = outcome.dropped_headers
                ep.required_cookies = outcome.kept_cookies
                ep.dropped_cookies = outcome.dropped_cookies
                _refresh_auth_after_probe(ep)

                if outcome.report.recommended_transport == "curl_cffi" and opts.transport == "auto":
                    recommended_transport = "curl_cffi"

                if outcome.baseline_snapshot is not None:
                    snap = outcome.baseline_snapshot
                    ep.response_status = snap.status
                    if ep.response_schema is None and snap.json_body is not None:
                        ep.response_example = snap.json_body
                        resp_root = f"{to_pascal_case(ep.operation_id)}Response"
                        inferred = infer_schema_from_samples(
                            [snap.json_body],
                            root_name=resp_root,
                            used_names=used_model_names,
                        )
                        ep.response_schema = inferred
                        if inferred and inferred.kind == "object" and inferred.model_name:
                            ep.response_model_name = inferred.model_name

            if outcome.report and outcome.report.notes:
                warnings.extend(outcome.report.notes)

    return _finalize_stage(
        total_ingested=total_ingested,
        fallback_reqs=fallback_reqs,
        filtered_out=filtered_out,
        endpoints=endpoints,
        warnings=warnings,
        recommended_transport=recommended_transport,
        opts=opts,
    )


def run_pipeline(
    raw_input: str,
    *,
    source_name: str = "",
    options: ForgeOptions | None = None,
) -> tuple[ForgeResult, dict[str, list[GeneratedFile]]]:
    opts = options or ForgeOptions()
    if opts.probe:
        return asyncio.run(
            run_pipeline_async(
                raw_input,
                source_name=source_name,
                options=opts,
            )
        )
    total_ingested, fallback_reqs, filtered_out, endpoints, warnings = _prepare_stage(
        raw_input,
        source_name=source_name,
        opts=opts,
    )
    recommended_transport: Literal["httpx", "curl_cffi"] = (
        opts.transport if opts.transport in {"httpx", "curl_cffi"} else "httpx"
    )
    return _finalize_stage(
        total_ingested=total_ingested,
        fallback_reqs=fallback_reqs,
        filtered_out=filtered_out,
        endpoints=endpoints,
        warnings=warnings,
        recommended_transport=recommended_transport,
        opts=opts,
    )


def forge_json_sync(payload_json: str) -> str:
    import json

    body = json.loads(payload_json)
    filter_cfg = body.get("filter") if isinstance(body.get("filter"), dict) else {}
    filter_opts = FilterOptions(
        drop_static=bool(filter_cfg.get("dropStatic", True)),
        drop_trackers=bool(filter_cfg.get("dropTrackers", True)),
        drop_options=bool(filter_cfg.get("dropOptions", True)),
        drop_html_documents=bool(filter_cfg.get("dropHtml", True)),
        only_json=bool(filter_cfg.get("onlyJson", False)),
    )
    opts = ForgeOptions(
        client_name=str(body.get("clientName", "") or ""),
        base_url=str(body.get("baseUrl", "") or ""),
        target=str(body.get("target", "python") or "python"),
        transport=str(body.get("transport", "auto") or "auto"),
        single_file=bool(body.get("singleFile", False)),
        probe=False,
        filter_options=filter_opts,
        keep_user_agent=bool(body.get("keepUserAgent", False)),
        keep_origin_referer=bool(body.get("keepOriginReferer", False)),
    )
    result, generated = run_pipeline(
        str(body.get("input", "") or ""),
        source_name=str(body.get("sourceName", "") or ""),
        options=opts,
    )
    files_payload = {
        target_name: [{"path": gf.path, "content": gf.content} for gf in gfiles]
        for target_name, gfiles in generated.items()
    }
    return json.dumps({"analysis": result.to_dict(), "files": files_payload})


def write_generated_files(
    out_dir: str | Path,
    generated: dict[str, list[GeneratedFile]],
) -> list[Path]:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    multi = len(generated) > 1
    for target_name, files in generated.items():
        target_root = root / target_name if multi else root
        for gf in files:
            dest = target_root / gf.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(gf.content, encoding="utf-8")
            written.append(dest)
    return written

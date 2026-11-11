from __future__ import annotations

import json

from reqforge.codegen.common import (
    AuthSlot,
    GeneratedFile,
    collect_client_auth_slots,
    is_auth_query_param,
    resolve_endpoint_arg_names,
    safe_default_cookies,
    safe_default_headers,
)
from reqforge.models import EndpointGroup, ForgeResult
from reqforge.utils import to_camel_case, to_pascal_case


def _render_js_method(
    ep: EndpointGroup,
    auth_slots: list[AuthSlot],
    client_base_url: str,
) -> list[str]:
    path_args, query_args, form_args, body_arg = resolve_endpoint_arg_names(
        ep,
        auth_slots,
        camel_case=True,
    )
    method_name = to_camel_case(ep.operation_id)
    has_any_query = bool(query_args)
    has_required_query = any(p.required for p, _ in query_args)
    has_required_form = any(p.required for p, _ in form_args)
    req_sig_parts: list[str] = [arg_name for _, arg_name in path_args]
    opt_sig_parts: list[str] = []

    if ep.request_body_kind == "json":
        req_sig_parts.append(body_arg)
    elif ep.request_body_kind in {"form", "multipart"} and form_args:
        if has_required_form:
            req_sig_parts.append("form")
        else:
            opt_sig_parts.append("form = {}")
    elif ep.request_body_kind == "text":
        req_sig_parts.append(body_arg)

    if has_any_query:
        if has_required_query:
            req_sig_parts.append("query")
        else:
            opt_sig_parts.append("query = {}")

    opt_sig_parts.append("options = {}")
    sig_parts = req_sig_parts + opt_sig_parts

    lines: list[str] = [f"  async {method_name}({', '.join(sig_parts)}) {{"]

    prefix = "this.baseUrl" if ep.base_url == client_base_url else json.dumps(ep.base_url)
    path_tpl = ep.path_template
    for p, arg_name in path_args:
        path_tpl = path_tpl.replace(f"{{{p.name}}}", f"${{encodeURIComponent(String({arg_name}))}}")
    lines.append(f"    const url = new URL(`${{{prefix}}}{path_tpl}`);")

    for p, arg_name in query_args:
        lines.append(f"    if (query?.{arg_name} !== undefined && query.{arg_name} !== null) {{")
        lines.append(
            f"      url.searchParams.set({json.dumps(p.wire_name)}, String(query.{arg_name}));"
        )
        lines.append("    }")
    for p in ep.query_params:
        slot = is_auth_query_param(p.wire_name, auth_slots)
        if slot is not None:
            lines.append(f"    if (this.{slot.ts_param_name}) {{")
            lines.append(
                f"      url.searchParams.set({json.dumps(p.wire_name)}, this.{slot.ts_param_name});"
            )
            lines.append("    }")

    default_headers = safe_default_headers(ep, auth_slots)
    default_cookies = safe_default_cookies(ep, auth_slots)
    lines.append(f"    const headers = {json.dumps(default_headers)};")
    lines.append(f"    const cookies = {json.dumps(default_cookies)};")
    lines.append("    if (options?.headers) Object.assign(headers, options.headers);")
    lines.append("    if (options?.cookies) Object.assign(cookies, options.cookies);")

    if ep.request_body_kind == "json":
        lines.append(f"    const body = JSON.stringify({body_arg});")
        lines.append('    if (!headers["Content-Type"] && !headers["content-type"]) {')
        lines.append('      headers["Content-Type"] = "application/json";')
        lines.append("    }")
    elif ep.request_body_kind in {"form", "multipart"} and form_args:
        lines.append("    const formParams = new URLSearchParams();")
        for p, arg_name in form_args:
            lines.append(f"    if (form?.{arg_name} !== undefined && form.{arg_name} !== null) {{")
            lines.append(f"      formParams.set({json.dumps(p.wire_name)}, String(form.{arg_name}));")
            lines.append("    }")
        lines.append("    const body = formParams.toString();")
    elif ep.request_body_kind == "text":
        lines.append(f"    const body = {body_arg};")
    else:
        lines.append("    const body = undefined;")

    lines.append(
        f"    return this.request({json.dumps(ep.method)}, url.toString(), headers, cookies, body);"
    )
    lines.append("  }")
    lines.append("")
    return lines


def generate_javascript_sdk(result: ForgeResult) -> list[GeneratedFile]:
    client_cls = to_pascal_case(result.client_name)
    if not client_cls.endswith("Client"):
        client_cls = f"{client_cls}Client"

    auth_slots = collect_client_auth_slots(result)

    lines: list[str] = [
        "export class ApiError extends Error {",
        "  constructor(status, url, body) {",
        "    super(`HTTP ${status} for ${url}: ${body.slice(0, 300)}`);",
        '    this.name = "ApiError";',
        "    this.status = status;",
        "    this.url = url;",
        "    this.body = body;",
        "  }",
        "}",
        "",
        "const RETRYABLE_STATUSES = new Set([429, 500, 502, 503, 504]);",
        "",
        f"export class {client_cls} {{",
        "  constructor(options = {}) {",
        f'    this.baseUrl = (options.baseUrl ?? {json.dumps(result.base_url)}).replace(/\\/+$/, "");',
    ]
    for slot in auth_slots:
        lines.append(f"    this.{slot.ts_param_name} = options.{slot.ts_param_name};")
    lines.extend(
        [
            "    this.defaultHeaders = { ...(options.defaultHeaders ?? {}) };",
            "    this.defaultCookies = { ...(options.defaultCookies ?? {}) };",
            "    this.timeoutMs = options.timeoutMs ?? 30000;",
            "    this.maxRetries = options.maxRetries ?? 3;",
            "    this.retryDelayMs = options.retryDelayMs ?? 500;",
            "    this.fetchFn = options.fetchFn ?? fetch;",
            "  }",
            "",
            "  static fromEnv(overrides = {}) {",
            '    const env = typeof process !== "undefined" ? process.env : {};',
            f"    return new {client_cls}({{",
            f"      baseUrl: overrides.baseUrl ?? env.API_BASE_URL ?? {json.dumps(result.base_url)},",
        ]
    )
    for slot in auth_slots:
        lines.append(
            f"      {slot.ts_param_name}: overrides.{slot.ts_param_name} ?? env[{json.dumps(slot.env_var)}],"
        )
    lines.extend(
        [
            "      ...overrides,",
            "    });",
            "  }",
            "",
            "  buildHeaders(extraHeaders = {}, extraCookies = {}) {",
            "    const headers = { ...this.defaultHeaders, ...extraHeaders };",
        ]
    )
    for slot in auth_slots:
        if slot.location == "header":
            lines.append(f"    if (this.{slot.ts_param_name}) {{")
            if slot.prefix:
                lines.append(
                    f"      const val = this.{slot.ts_param_name}.toLowerCase().startsWith({json.dumps(slot.prefix.lower())}) ? this.{slot.ts_param_name} : {json.dumps(slot.prefix)} + this.{slot.ts_param_name};"
                )
                lines.append(f"      headers[{json.dumps(slot.wire_name)}] = val;")
            else:
                lines.append(
                    f"      headers[{json.dumps(slot.wire_name)}] = this.{slot.ts_param_name};"
                )
            lines.append("    }")
    lines.append("    const cookies = { ...this.defaultCookies, ...extraCookies };")
    for slot in auth_slots:
        if slot.location == "cookie":
            lines.append(f"    if (this.{slot.ts_param_name}) {{")
            lines.append(
                f"      cookies[{json.dumps(slot.wire_name)}] = this.{slot.ts_param_name};"
            )
            lines.append("    }")
    lines.extend(
        [
            "    const cookieEntries = Object.entries(cookies);",
            "    if (cookieEntries.length > 0) {",
            '      headers["Cookie"] = cookieEntries.map(([k, v]) => `${k}=${v}`).join("; ");',
            "    }",
            "    return headers;",
            "  }",
            "",
            "  async request(method, url, headers, cookies, body) {",
            "    const mergedHeaders = this.buildHeaders(headers, cookies);",
            "    let lastError;",
            "    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {",
            "      const controller = new AbortController();",
            "      const timer = setTimeout(() => controller.abort(), this.timeoutMs);",
            "      try {",
            "        const resp = await this.fetchFn(url, {",
            "          method,",
            "          headers: mergedHeaders,",
            "          body,",
            "          signal: controller.signal,",
            "        });",
            "        clearTimeout(timer);",
            "        if (RETRYABLE_STATUSES.has(resp.status) && attempt < this.maxRetries) {",
            '          const retryAfter = resp.headers.get("retry-after");',
            "          const parsedRetry = retryAfter ? Number(retryAfter) : NaN;",
            "          const delay =",
            "            !Number.isNaN(parsedRetry) && parsedRetry > 0",
            "              ? parsedRetry * 1000",
            "              : this.retryDelayMs * 2 ** attempt + Math.random() * 200;",
            "          await new Promise((r) => setTimeout(r, delay));",
            "          continue;",
            "        }",
            "        const text = await resp.text();",
            "        if (!resp.ok) {",
            "          throw new ApiError(resp.status, url, text);",
            "        }",
            "        if (!text) return undefined;",
            '        const ct = (resp.headers.get("content-type") ?? "").toLowerCase();',
            '        if (ct.includes("json") || text.trimStart().startsWith("{") || text.trimStart().startsWith("[")) {',
            "          try {",
            "            return JSON.parse(text);",
            "          } catch {",
            "            return text;",
            "          }",
            "        }",
            "        return text;",
            "      } catch (err) {",
            "        clearTimeout(timer);",
            "        if (err instanceof ApiError) throw err;",
            "        lastError = err;",
            "        if (attempt >= this.maxRetries) throw err;",
            "        const delay = this.retryDelayMs * 2 ** attempt + Math.random() * 200;",
            "        await new Promise((r) => setTimeout(r, delay));",
            "      }",
            "    }",
            "    throw lastError;",
            "  }",
            "",
        ]
    )

    for ep in result.endpoints:
        lines.extend(_render_js_method(ep, auth_slots, result.base_url))

    lines.append("}")
    client_code = "\n".join(lines).rstrip() + "\n"

    smoke_lines = [
        f'import {{ ApiError, {client_cls} }} from "./client.mjs";',
        "",
        "async function main() {",
        f"  const client = {client_cls}.fromEnv();",
    ]
    get_eps = [ep for ep in result.endpoints if ep.method.upper() == "GET"]
    targets = get_eps if get_eps else result.endpoints[:1]
    for ep in targets:
        path_args, query_args, form_args, _ = resolve_endpoint_arg_names(ep, auth_slots, camel_case=True)
        req_args: list[str] = []
        for p, _ in path_args:
            sample = p.sample_values[0] if p.sample_values else (1 if p.inferred_type == "int" else "example")
            req_args.append(json.dumps(sample))
        if ep.request_body_kind == "json":
            req_args.append(json.dumps(ep.request_example or {}))
        elif ep.request_body_kind in {"form", "multipart"} and form_args:
            if any(p.required for p, _ in form_args):
                obj = {
                    arg_name: (p.sample_values[0] if p.sample_values else "example")
                    for p, arg_name in form_args
                    if p.required
                }
                req_args.append(json.dumps(obj))
        elif ep.request_body_kind == "text":
            req_args.append('""')
        if any(p.required for p, _ in query_args):
            qobj = {
                arg_name: (p.sample_values[0] if p.sample_values else "example")
                for p, arg_name in query_args
                if p.required
            }
            req_args.append(json.dumps(qobj))
        args = req_args
        m_name = to_camel_case(ep.operation_id)
        smoke_lines.extend(
            [
                "  try {",
                f"    const res = await client.{m_name}({', '.join(args)});",
                f'    console.log("OK {m_name}", res);',
                "  } catch (err) {",
                "    if (err instanceof ApiError) {",
                f'      console.error("API_ERROR {m_name}", err.status, err.body.slice(0, 200));',
                "    } else {",
                "      throw err;",
                "    }",
                "  }",
            ]
        )
    smoke_lines.extend(["}", "", "main();", ""])

    pkg_json = json.dumps(
        {
            "name": f"{to_camel_case(result.client_name).lower()}-sdk",
            "version": "0.1.0",
            "type": "module",
            "main": "./client.mjs",
            "scripts": {"smoke": "node smoke_test.mjs"},
        },
        indent=2,
    ) + "\n"

    return [
        GeneratedFile(path="client.mjs", content=client_code),
        GeneratedFile(path="smoke_test.mjs", content="\n".join(smoke_lines)),
        GeneratedFile(path="package.json", content=pkg_json),
    ]

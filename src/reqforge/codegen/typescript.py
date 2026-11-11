from __future__ import annotations

import json
import re

from reqforge.codegen.common import (
    AuthSlot,
    GeneratedFile,
    collect_client_auth_slots,
    is_auth_query_param,
    resolve_endpoint_arg_names,
    safe_default_cookies,
    safe_default_headers,
)
from reqforge.infer.schema import collect_named_models
from reqforge.models import EndpointGroup, ForgeResult, JsonSchemaNode, ParamSpec
from reqforge.utils import to_camel_case, to_pascal_case

TS_IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _ts_scalar_type(inferred_type: str) -> str:
    if inferred_type in {"int", "float"}:
        return "number"
    if inferred_type == "bool":
        return "boolean"
    if inferred_type == "json":
        return "unknown"
    return "string"


def _ts_schema_type(node: JsonSchemaNode) -> str:
    if node.kind == "string":
        return "string"
    if node.kind in {"integer", "number"}:
        return "number"
    if node.kind == "boolean":
        return "boolean"
    if node.kind == "null":
        return "null"
    if node.kind == "any":
        return "unknown"
    if node.kind == "array":
        inner = _ts_schema_type(node.items) if node.items is not None else "unknown"
        if "|" in inner:
            return f"Array<{inner}>"
        return f"{inner}[]"
    if node.kind == "object":
        if node.model_name and node.fields:
            return node.model_name
        return "Record<string, unknown>"
    if node.kind == "union":
        parts: list[str] = []
        for v in node.variants:
            t = _ts_schema_type(v)
            if t not in parts:
                parts.append(t)
        return " | ".join(parts) if parts else "unknown"
    return "unknown"


def _ts_prop_key(wire_name: str) -> str:
    if TS_IDENT_RE.match(wire_name):
        return wire_name
    return json.dumps(wire_name)


def _render_ts_models(result: ForgeResult) -> tuple[str, set[str]]:
    models: list[JsonSchemaNode] = []
    seen_names: set[str] = set()
    for ep in result.endpoints:
        for schema in (ep.request_schema, ep.response_schema):
            for node in collect_named_models(schema):
                if node.model_name and node.model_name not in seen_names:
                    seen_names.add(node.model_name)
                    models.append(node)

    if not models:
        return "export type JsonRecord = Record<string, unknown>;\n", seen_names

    lines: list[str] = []
    for node in models:
        lines.append(f"export interface {node.model_name} {{")
        for fld in node.fields.values():
            base_type = _ts_schema_type(fld.node)
            if fld.nullable and base_type != "null" and "null" not in base_type.split(" | "):
                base_type = f"{base_type} | null"
            opt_mark = "?" if not fld.required else ""
            lines.append(f"  {_ts_prop_key(fld.wire_name)}{opt_mark}: {base_type};")
        lines.append("}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", seen_names


def _ts_return_type(ep: EndpointGroup) -> str:
    if ep.response_schema is None:
        return "unknown"
    return _ts_schema_type(ep.response_schema)


def _render_ts_method(
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
    has_required_query = any(p.required for p, _ in query_args)
    has_any_query = bool(query_args)
    has_required_form = any(p.required for p, _ in form_args)

    req_sig_parts: list[str] = []
    opt_sig_parts: list[str] = []
    for p, arg_name in path_args:
        req_sig_parts.append(f"{arg_name}: {_ts_scalar_type(p.inferred_type)}")

    if ep.request_body_kind == "json":
        req_type = ep.request_model_name or "unknown"
        req_sig_parts.append(f"{body_arg}: {req_type}")
    elif ep.request_body_kind in {"form", "multipart"} and form_args:
        form_fields: list[str] = []
        for p, arg_name in form_args:
            mark = "" if p.required else "?"
            form_fields.append(f"{arg_name}{mark}: {_ts_scalar_type(p.inferred_type)}")
        if has_required_form:
            req_sig_parts.append(f"form: {{ {'; '.join(form_fields)} }}")
        else:
            opt_sig_parts.append(f"form?: {{ {'; '.join(form_fields)} }}")
    elif ep.request_body_kind == "text":
        req_sig_parts.append(f"{body_arg}: string")

    if has_any_query:
        q_fields: list[str] = []
        for p, arg_name in query_args:
            mark = "" if p.required else "?"
            q_fields.append(f"{arg_name}{mark}: {_ts_scalar_type(p.inferred_type)}")
        if has_required_query:
            req_sig_parts.append(f"query: {{ {'; '.join(q_fields)} }}")
        else:
            opt_sig_parts.append(f"query?: {{ {'; '.join(q_fields)} }}")

    opt_sig_parts.append("options?: RequestOverrides")
    sig_parts = req_sig_parts + opt_sig_parts
    ret_type = _ts_return_type(ep)

    lines: list[str] = [
        f"  async {method_name}({', '.join(sig_parts)}): Promise<{ret_type}> {{"
    ]

    prefix = "this.baseUrl" if ep.base_url == client_base_url else json.dumps(ep.base_url)
    path_tpl = ep.path_template
    for p, arg_name in path_args:
        path_tpl = path_tpl.replace(f"{{{p.name}}}", f"${{encodeURIComponent(String({arg_name}))}}")
    lines.append(f"    const url = new URL(`${{{prefix}}}{path_tpl}`);")

    for p, arg_name in query_args:
        lines.append(f"    if (query?.{arg_name} !== undefined) {{")
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
    lines.append(f"    const headers: Record<string, string> = {json.dumps(default_headers)};")
    if default_cookies:
        lines.append(f"    const cookies: Record<string, string> = {json.dumps(default_cookies)};")
    else:
        lines.append("    const cookies: Record<string, string> = {};")

    lines.append("    if (options?.headers) Object.assign(headers, options.headers);")
    lines.append("    if (options?.cookies) Object.assign(cookies, options.cookies);")

    if ep.request_body_kind == "json":
        lines.append(f"    const body = JSON.stringify({body_arg});")
        lines.append('    if (!headers["Content-Type"] && !headers["content-type"]) {')
        lines.append('      headers["Content-Type"] = "application/json";')
        lines.append("    }")
    elif ep.request_body_kind == "multipart" and form_args:
        lines.append("    const body = new FormData();")
        for p, arg_name in form_args:
            lines.append(f"    if (form?.{arg_name} !== undefined) {{")
            lines.append(f"      body.append({json.dumps(p.wire_name)}, String(form.{arg_name}));")
            lines.append("    }")
    elif ep.request_body_kind == "form" and form_args:
        lines.append("    const formParams = new URLSearchParams();")
        for p, arg_name in form_args:
            lines.append(f"    if (form?.{arg_name} !== undefined) {{")
            lines.append(f"      formParams.set({json.dumps(p.wire_name)}, String(form.{arg_name}));")
            lines.append("    }")
        lines.append("    const body = formParams.toString();")
    elif ep.request_body_kind == "text":
        lines.append(f"    const body = {body_arg};")
    else:
        lines.append("    const body = undefined;")

    lines.append(
        f"    return this.request<{ret_type}>({json.dumps(ep.method)}, url.toString(), headers, cookies, body);"
    )
    lines.append("  }")
    lines.append("")
    return lines


def generate_typescript_sdk(result: ForgeResult) -> list[GeneratedFile]:
    client_cls = to_pascal_case(result.client_name)
    if not client_cls.endswith("Client"):
        client_cls = f"{client_cls}Client"

    auth_slots = collect_client_auth_slots(result)
    models_code, model_names = _render_ts_models(result)

    client_lines: list[str] = []
    if model_names:
        client_lines.append(
            f'import type {{ {", ".join(sorted(model_names))} }} from "./models.ts";'
        )
        client_lines.append("")

    client_lines.extend(
        [
            "export class ApiError extends Error {",
            "  public readonly status: number;",
            "  public readonly url: string;",
            "  public readonly body: string;",
            "",
            "  constructor(status: number, url: string, body: string) {",
            "    super(`HTTP ${status} for ${url}: ${body.slice(0, 300)}`);",
            '    this.name = "ApiError";',
            "    this.status = status;",
            "    this.url = url;",
            "    this.body = body;",
            "  }",
            "}",
            "",
            "export interface RequestOverrides {",
            "  headers?: Record<string, string>;",
            "  cookies?: Record<string, string>;",
            "}",
            "",
            f"export interface {client_cls}Options {{",
            "  baseUrl?: string;",
        ]
    )
    for slot in auth_slots:
        client_lines.append(f"  {slot.ts_param_name}?: string;")
    client_lines.extend(
        [
            "  defaultHeaders?: Record<string, string>;",
            "  defaultCookies?: Record<string, string>;",
            "  timeoutMs?: number;",
            "  maxRetries?: number;",
            "  retryDelayMs?: number;",
            "  fetchFn?: typeof fetch;",
            "}",
            "",
            "const RETRYABLE_STATUSES = new Set([429, 500, 502, 503, 504]);",
            "",
            f"export class {client_cls} {{",
            "  private readonly baseUrl: string;",
        ]
    )
    for slot in auth_slots:
        client_lines.append(f"  private readonly {slot.ts_param_name}?: string;")
    client_lines.extend(
        [
            "  private readonly defaultHeaders: Record<string, string>;",
            "  private readonly defaultCookies: Record<string, string>;",
            "  private readonly timeoutMs: number;",
            "  private readonly maxRetries: number;",
            "  private readonly retryDelayMs: number;",
            "  private readonly fetchFn: typeof fetch;",
            "",
            f"  constructor(options: {client_cls}Options = {{}}) {{",
            f'    this.baseUrl = (options.baseUrl ?? {json.dumps(result.base_url)}).replace(/\\/+$/, "");',
        ]
    )
    for slot in auth_slots:
        client_lines.append(f"    this.{slot.ts_param_name} = options.{slot.ts_param_name};")
    client_lines.extend(
        [
            "    this.defaultHeaders = { ...(options.defaultHeaders ?? {}) };",
            "    this.defaultCookies = { ...(options.defaultCookies ?? {}) };",
            "    this.timeoutMs = options.timeoutMs ?? 30000;",
            "    this.maxRetries = options.maxRetries ?? 3;",
            "    this.retryDelayMs = options.retryDelayMs ?? 500;",
            "    this.fetchFn = options.fetchFn ?? fetch;",
            "  }",
            "",
            f"  static fromEnv(overrides: Partial<{client_cls}Options> = {{}}): {client_cls} {{",
            '    const env = typeof process !== "undefined" ? process.env : {};',
            f"    return new {client_cls}({{",
            f"      baseUrl: overrides.baseUrl ?? env.API_BASE_URL ?? {json.dumps(result.base_url)},",
        ]
    )
    for slot in auth_slots:
        client_lines.append(
            f"      {slot.ts_param_name}: overrides.{slot.ts_param_name} ?? env[{json.dumps(slot.env_var)}],"
        )
    client_lines.extend(
        [
            "      ...overrides,",
            "    });",
            "  }",
            "",
            "  private buildHeaders(",
            "    extraHeaders: Record<string, string>,",
            "    extraCookies: Record<string, string>",
            "  ): Record<string, string> {",
            "    const headers: Record<string, string> = { ...this.defaultHeaders, ...extraHeaders };",
        ]
    )
    for slot in auth_slots:
        if slot.location == "header":
            client_lines.append(f"    if (this.{slot.ts_param_name}) {{")
            if slot.prefix:
                client_lines.append(
                    f"      const val = this.{slot.ts_param_name}.toLowerCase().startsWith({json.dumps(slot.prefix.lower())}) ? this.{slot.ts_param_name} : {json.dumps(slot.prefix)} + this.{slot.ts_param_name};"
                )
                client_lines.append(f"      headers[{json.dumps(slot.wire_name)}] = val;")
            else:
                client_lines.append(
                    f"      headers[{json.dumps(slot.wire_name)}] = this.{slot.ts_param_name};"
                )
            client_lines.append("    }")

    client_lines.extend(
        [
            "    const cookies: Record<string, string> = { ...this.defaultCookies, ...extraCookies };",
        ]
    )
    for slot in auth_slots:
        if slot.location == "cookie":
            client_lines.append(f"    if (this.{slot.ts_param_name}) {{")
            client_lines.append(
                f"      cookies[{json.dumps(slot.wire_name)}] = this.{slot.ts_param_name};"
            )
            client_lines.append("    }")

    client_lines.extend(
        [
            "    const cookieEntries = Object.entries(cookies);",
            "    if (cookieEntries.length > 0) {",
            '      headers["Cookie"] = cookieEntries.map(([k, v]) => `${k}=${v}`).join("; ");',
            "    }",
            "    return headers;",
            "  }",
            "",
            "  private async request<T>(",
            "    method: string,",
            "    url: string,",
            "    headers: Record<string, string>,",
            "    cookies: Record<string, string>,",
            "    body?: string | FormData",
            "  ): Promise<T> {",
            "    const mergedHeaders = this.buildHeaders(headers, cookies);",
            "    let lastError: unknown;",
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
            "        if (!text) {",
            "          return undefined as T;",
            "        }",
            '        const ct = (resp.headers.get("content-type") ?? "").toLowerCase();',
            '        if (ct.includes("json") || text.trimStart().startsWith("{") || text.trimStart().startsWith("[")) {',
            "          try {",
            "            return JSON.parse(text) as T;",
            "          } catch {",
            "            return text as unknown as T;",
            "          }",
            "        }",
            "        return text as unknown as T;",
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
        client_lines.extend(_render_ts_method(ep, auth_slots, result.base_url))

    client_lines.append("}")
    client_code = "\n".join(client_lines).rstrip() + "\n"

    index_lines = [
        f'export {{ ApiError, {client_cls} }} from "./client.ts";',
        f'export type {{ {client_cls}Options, RequestOverrides }} from "./client.ts";',
    ]
    if model_names:
        index_lines.append('export type * from "./models.ts";')

    smoke_lines = [
        f'import {{ ApiError, {client_cls} }} from "./index.ts";',
        "",
        "async function main(): Promise<void> {",
        f"  const client = {client_cls}.fromEnv();",
    ]
    get_eps = [ep for ep in result.endpoints if ep.method.upper() == "GET"]
    targets = get_eps if get_eps else result.endpoints[:1]
    for ep in targets:
        path_args, query_args, form_args, _ = resolve_endpoint_arg_names(ep, auth_slots, camel_case=True)
        req_args: list[str] = []
        for p, arg_name in path_args:
            sample = p.sample_values[0] if p.sample_values else (1 if p.inferred_type == "int" else "example")
            req_args.append(json.dumps(sample))
        if ep.request_body_kind == "json":
            ex = ep.request_example if ep.request_example is not None else {}
            req_args.append(json.dumps(ex))
        elif ep.request_body_kind in {"form", "multipart"} and form_args:
            has_req_form = any(p.required for p, _ in form_args)
            if has_req_form:
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
    smoke_lines.extend(["}", "", "void main();", ""])

    pkg_json = json.dumps(
        {
            "name": f"{to_camel_case(result.client_name).lower()}-sdk",
            "version": "0.1.0",
            "type": "module",
            "main": "./client.ts",
            "scripts": {
                "smoke": "node --experimental-strip-types smoke_test.ts",
            },
        },
        indent=2,
    ) + "\n"

    tsconfig = json.dumps(
        {
            "compilerOptions": {
                "target": "ES2022",
                "module": "NodeNext",
                "moduleResolution": "NodeNext",
                "strict": True,
                "allowImportingTsExtensions": True,
                "noEmit": True,
                "skipLibCheck": True,
            },
            "include": ["*.ts"],
        },
        indent=2,
    ) + "\n"

    return [
        GeneratedFile(path="models.ts", content=models_code),
        GeneratedFile(path="client.ts", content=client_code),
        GeneratedFile(path="index.ts", content="\n".join(index_lines) + "\n"),
        GeneratedFile(path="smoke_test.ts", content="\n".join(smoke_lines)),
        GeneratedFile(path="package.json", content=pkg_json),
        GeneratedFile(path="tsconfig.json", content=tsconfig),
    ]

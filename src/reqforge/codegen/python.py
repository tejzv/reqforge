from __future__ import annotations

import json
from typing import Any

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
from reqforge.utils import to_pascal_case, to_snake_case

PYDANTIC_RESERVED_ATTRS = {
    "model_config",
    "model_fields",
    "model_computed_fields",
    "model_extra",
    "model_fields_set",
    "model_dump",
    "model_dump_json",
    "model_validate",
    "model_validate_json",
    "model_rebuild",
    "model_copy",
}


def _py_scalar_type(inferred_type: str) -> str:
    mapping = {
        "int": "int",
        "float": "float",
        "bool": "bool",
        "uuid": "str",
        "datetime": "str",
        "str": "str",
        "json": "Any",
    }
    return mapping.get(inferred_type, "str")


def _py_schema_type(node: JsonSchemaNode) -> str:
    if node.kind == "string":
        return "str"
    if node.kind == "integer":
        return "int"
    if node.kind == "number":
        return "float"
    if node.kind == "boolean":
        return "bool"
    if node.kind == "null":
        return "None"
    if node.kind == "any":
        return "Any"
    if node.kind == "array":
        inner = _py_schema_type(node.items) if node.items is not None else "Any"
        return f"list[{inner}]"
    if node.kind == "object":
        if node.model_name and node.fields:
            return node.model_name
        return "dict[str, Any]"
    if node.kind == "union":
        parts: list[str] = []
        for v in node.variants:
            t = _py_scalar_union_part(v)
            if t not in parts:
                parts.append(t)
        return " | ".join(parts) if parts else "Any"
    return "Any"


def _py_scalar_union_part(node: JsonSchemaNode) -> str:
    return _py_schema_type(node)


def _render_pydantic_models(result: ForgeResult) -> tuple[str, set[str]]:
    models: list[JsonSchemaNode] = []
    seen_names: set[str] = set()

    for ep in result.endpoints:
        for schema in (ep.request_schema, ep.response_schema):
            for node in collect_named_models(schema):
                if node.model_name and node.model_name not in seen_names:
                    seen_names.add(node.model_name)
                    models.append(node)

    lines: list[str] = [
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        "from pydantic import BaseModel, ConfigDict, Field",
        "",
    ]

    if not models:
        lines.extend(
            [
                "",
                "class EmptyModel(BaseModel):",
                '    model_config = ConfigDict(populate_by_name=True, extra="ignore")',
                "",
            ]
        )
        return "\n".join(lines), seen_names

    for node in models:
        lines.append("")
        lines.append(f"class {node.model_name}(BaseModel):")
        lines.append('    model_config = ConfigDict(populate_by_name=True, extra="ignore")')
        lines.append("")
        for fld in node.fields.values():
            py_attr = f"{fld.name}_" if fld.name in PYDANTIC_RESERVED_ATTRS else fld.name
            base_type = _py_schema_type(fld.node)
            is_optional = (not fld.required) or fld.nullable
            if is_optional and base_type != "None" and not base_type.endswith("| None"):
                type_ann = f"{base_type} | None"
            else:
                type_ann = base_type

            needs_alias = py_attr != fld.wire_name
            if is_optional:
                if needs_alias:
                    lines.append(
                        f"    {py_attr}: {type_ann} = Field(default=None, alias={json.dumps(fld.wire_name)})"
                    )
                else:
                    lines.append(f"    {py_attr}: {type_ann} = None")
            else:
                if needs_alias:
                    lines.append(
                        f"    {py_attr}: {type_ann} = Field(alias={json.dumps(fld.wire_name)})"
                    )
                else:
                    lines.append(f"    {py_attr}: {type_ann}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n", seen_names


def _py_return_type(ep: EndpointGroup) -> tuple[str, str]:
    if ep.response_schema is None:
        return "Any", "raw"
    if ep.response_schema.kind == "object" and ep.response_schema.model_name:
        return ep.response_schema.model_name, "model"
    if (
        ep.response_schema.kind == "array"
        and ep.response_schema.items is not None
        and ep.response_schema.items.kind == "object"
        and ep.response_schema.items.model_name
    ):
        item_cls = ep.response_schema.items.model_name
        return f"list[{item_cls}]", f"list:{item_cls}"
    return _py_schema_type(ep.response_schema), "raw"


def _py_default_literal(param: ParamSpec) -> str:
    if param.default_value is None:
        return "None"
    return repr(param.default_value)


def _format_dict_literal(data: dict[str, str], indent: int = 8) -> str:
    if not data:
        return "{}"
    pad = " " * indent
    inner = " " * (indent + 4)
    rows = [f"{inner}{json.dumps(k)}: {json.dumps(v)}," for k, v in data.items()]
    return "{\n" + "\n".join(rows) + f"\n{pad}}}"


def _render_endpoint_method(
    ep: EndpointGroup,
    auth_slots: list[AuthSlot],
    client_base_url: str,
) -> list[str]:
    path_args, query_args, form_args, body_arg = resolve_endpoint_arg_names(ep, auth_slots)
    req_sig_args: list[str] = ["self"]
    opt_sig_args: list[str] = []

    for p, arg_name in path_args:
        req_sig_args.append(f"{arg_name}: {_py_scalar_type(p.inferred_type)}")

    if ep.request_body_kind == "json":
        if ep.request_model_name:
            req_sig_args.append(f"{body_arg}: {ep.request_model_name} | dict[str, Any]")
        else:
            req_sig_args.append(f"{body_arg}: Any")
    elif ep.request_body_kind in {"form", "multipart"}:
        for p, arg_name in form_args:
            t = _py_scalar_type(p.inferred_type)
            if p.required:
                req_sig_args.append(f"{arg_name}: {t}")
            else:
                opt_sig_args.append(f"{arg_name}: {t} | None = {_py_default_literal(p)}")
    elif ep.request_body_kind == "text":
        req_sig_args.append(f"{body_arg}: str")

    for p, arg_name in query_args:
        t = _py_scalar_type(p.inferred_type)
        if p.required:
            req_sig_args.append(f"{arg_name}: {t}")
        else:
            opt_sig_args.append(f"{arg_name}: {t} | None = {_py_default_literal(p)}")

    opt_sig_args.append("headers: dict[str, str] | None = None")
    opt_sig_args.append("cookies: dict[str, str] | None = None")

    sig_parts = req_sig_args + opt_sig_args
    ret_type, parse_kind = _py_return_type(ep)

    lines: list[str] = []
    if len(", ".join(sig_parts)) > 88:
        lines.append(f"    async def {ep.operation_id}(")
        for sp in sig_parts:
            lines.append(f"        {sp},")
        lines.append(f"    ) -> {ret_type}:")
    else:
        lines.append(f"    async def {ep.operation_id}({', '.join(sig_parts)}) -> {ret_type}:")

    prefix_literal = ep.base_url if ep.base_url != client_base_url else ""
    if path_args:
        fmt_expr = ep.path_template
        for p, arg_name in path_args:
            fmt_expr = fmt_expr.replace(f"{{{p.name}}}", f"{{quote(str({arg_name}), safe='')}}")
        lines.append(f'        path = f"{prefix_literal}{fmt_expr}"')
    else:
        lines.append(f"        path = {json.dumps(prefix_literal + ep.path_template)}")

    query_auth_lines: list[str] = []
    for p in ep.query_params:
        slot = is_auth_query_param(p.wire_name, auth_slots)
        if slot is not None:
            query_auth_lines.append((p.wire_name, slot.param_name))

    if query_args or query_auth_lines:
        lines.append("        params: dict[str, Any] = {}")
        for p, arg_name in query_args:
            lines.append(f"        if {arg_name} is not None:")
            if p.inferred_type == "bool":
                lines.append(
                    f'            params[{json.dumps(p.wire_name)}] = "true" if {arg_name} else "false"'
                )
            else:
                lines.append(f"            params[{json.dumps(p.wire_name)}] = {arg_name}")
        for wire_name, slot_param in query_auth_lines:
            lines.append(f"        if self.{slot_param} is not None:")
            lines.append(f"            params[{json.dumps(wire_name)}] = self.{slot_param}")
    else:
        lines.append("        params = None")

    default_headers = safe_default_headers(ep, auth_slots)
    default_cookies = safe_default_cookies(ep, auth_slots)

    lines.append(f"        req_headers = {_format_dict_literal(default_headers)}")
    lines.append("        if headers:")
    lines.append("            req_headers.update(headers)")

    if default_cookies:
        lines.append(f"        req_cookies = {_format_dict_literal(default_cookies)}")
        lines.append("        if cookies:")
        lines.append("            req_cookies.update(cookies)")
    else:
        lines.append("        req_cookies = dict(cookies) if cookies else None")

    if ep.request_body_kind == "json":
        lines.append(
            f"        json_body = {body_arg}.model_dump(by_alias=True, exclude_none=True) if isinstance({body_arg}, BaseModel) else {body_arg}"
        )
        body_kwarg = "json_data=json_body"
    elif ep.request_body_kind == "multipart":
        lines.append("        files_data: dict[str, Any] = {}")
        for p, arg_name in form_args:
            lines.append(f"        if {arg_name} is not None:")
            lines.append(f"            files_data[{json.dumps(p.wire_name)}] = (None, str({arg_name}))")
        body_kwarg = "files_data=files_data"
    elif ep.request_body_kind == "form":
        lines.append("        form_data: dict[str, Any] = {}")
        for p, arg_name in form_args:
            lines.append(f"        if {arg_name} is not None:")
            lines.append(f"            form_data[{json.dumps(p.wire_name)}] = {arg_name}")
        body_kwarg = "form_data=form_data"
    elif ep.request_body_kind == "text":
        body_kwarg = f"content_data={body_arg}"
    else:
        body_kwarg = ""

    call_args = [
        f"{json.dumps(ep.method)}",
        "path",
        "params=params",
        "headers=req_headers",
        "cookies=req_cookies",
    ]
    if body_kwarg:
        call_args.append(body_kwarg)

    lines.append(f"        data = await self._request({', '.join(call_args)})")

    if parse_kind == "model" and ep.response_model_name:
        lines.append(f"        return {ep.response_model_name}.model_validate(data)")
    elif parse_kind.startswith("list:"):
        item_cls = parse_kind.split(":", 1)[1]
        lines.append(f"        return [{item_cls}.model_validate(item) for item in data]")
    else:
        lines.append("        return data")
    lines.append("")
    return lines


def _render_python_client(
    result: ForgeResult,
    *,
    transport: str,
    model_names: set[str],
    single_file: bool = False,
) -> str:
    client_cls = to_pascal_case(result.client_name)
    if not client_cls.endswith("Client"):
        client_cls = f"{client_cls}Client"

    auth_slots = collect_client_auth_slots(result)

    lines: list[str] = [
        "from __future__ import annotations",
        "",
        "import asyncio",
        "import os",
        "import random",
        "from typing import Any",
        "from urllib.parse import quote",
        "",
    ]

    if transport == "curl_cffi":
        lines.append("from curl_cffi.requests import AsyncSession")
    else:
        lines.append("import httpx")

    lines.append("from pydantic import BaseModel")

    if not single_file and model_names:
        sorted_models = ", ".join(sorted(model_names))
        lines.append(f"from .models import {sorted_models}")

    lines.extend(
        [
            "",
            "",
            "RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}",
            "",
            "",
            "class ApiError(Exception):",
            "    def __init__(self, status_code: int, url: str, body: str) -> None:",
            "        self.status_code = status_code",
            "        self.url = url",
            "        self.body = body",
            '        super().__init__(f"HTTP {status_code} for {url}: {body[:300]}")',
            "",
            "",
            f"class {client_cls}:",
            "    def __init__(",
            "        self,",
            f"        base_url: str = {json.dumps(result.base_url)},",
        ]
    )

    for slot in auth_slots:
        lines.append(f"        {slot.param_name}: str | None = None,")

    lines.extend(
        [
            "        default_headers: dict[str, str] | None = None,",
            "        default_cookies: dict[str, str] | None = None,",
            "        timeout: float = 30.0,",
            "        max_retries: int = 3,",
            "        retry_base_delay: float = 0.5,",
        ]
    )
    if transport == "curl_cffi":
        lines.append('        impersonate: str = "chrome",')
        lines.append("        max_clients: int = 20,")
    else:
        lines.append("        max_connections: int = 20,")
        lines.append("        http2: bool = False,")

    lines.extend(
        [
            "    ) -> None:",
            '        self.base_url = base_url.rstrip("/")',
        ]
    )
    for slot in auth_slots:
        lines.append(f"        self.{slot.param_name} = {slot.param_name}")

    lines.extend(
        [
            "        self.default_headers = dict(default_headers or {})",
            "        self.default_cookies = dict(default_cookies or {})",
            "        self.timeout = timeout",
            "        self.max_retries = max_retries",
            "        self.retry_base_delay = retry_base_delay",
        ]
    )

    if transport == "curl_cffi":
        lines.extend(
            [
                "        self._session = AsyncSession(",
                "            impersonate=impersonate,",
                "            timeout=timeout,",
                "            max_clients=max_clients,",
                "        )",
            ]
        )
    else:
        lines.extend(
            [
                "        self._client = httpx.AsyncClient(",
                "            base_url=self.base_url,",
                "            timeout=timeout,",
                "            follow_redirects=True,",
                "            http2=http2,",
                "            limits=httpx.Limits(",
                "                max_connections=max_connections,",
                "                max_keepalive_connections=max_connections,",
                "            ),",
                "        )",
            ]
        )

    lines.extend(
        [
            "",
            "    @classmethod",
            "    def from_env(",
            "        cls,",
            "        base_url: str | None = None,",
            "        **kwargs: Any,",
            f"    ) -> {client_cls}:",
            "        return cls(",
            f'            base_url=base_url or os.getenv("API_BASE_URL", {json.dumps(result.base_url)}),',
        ]
    )
    for slot in auth_slots:
        lines.append(f'            {slot.param_name}=os.getenv({json.dumps(slot.env_var)}),')
    lines.extend(
        [
            "            **kwargs,",
            "        )",
            "",
            f"    async def __aenter__(self) -> {client_cls}:",
            "        return self",
            "",
            "    async def __aexit__(self, *_: Any) -> None:",
            "        await self.aclose()",
            "",
            "    async def aclose(self) -> None:",
        ]
    )
    if transport == "curl_cffi":
        lines.append("        await self._session.close()")
    else:
        lines.append("        await self._client.aclose()")

    lines.extend(
        [
            "",
            "    def _build_headers(self, extra: dict[str, str] | None) -> dict[str, str]:",
            "        merged = dict(self.default_headers)",
            "        if extra:",
            "            merged.update(extra)",
        ]
    )
    for slot in auth_slots:
        if slot.location == "header":
            lines.append(f"        if self.{slot.param_name}:")
            if slot.prefix:
                lines.append(
                    f"            val = self.{slot.param_name}"
                )
                lines.append(
                    f'            if not val.lower().startswith(("bearer ", "basic ", "token ")):'
                )
                lines.append(f'                val = f"{slot.prefix}{{val}}"')
                lines.append(f"            merged[{json.dumps(slot.wire_name)}] = val")
            else:
                lines.append(
                    f"            merged[{json.dumps(slot.wire_name)}] = self.{slot.param_name}"
                )
    lines.extend(
        [
            "        return merged",
            "",
            "    def _build_cookies(self, extra: dict[str, str] | None) -> dict[str, str]:",
            "        merged = dict(self.default_cookies)",
            "        if extra:",
            "            merged.update(extra)",
        ]
    )
    for slot in auth_slots:
        if slot.location == "cookie":
            lines.append(f"        if self.{slot.param_name}:")
            lines.append(f"            merged[{json.dumps(slot.wire_name)}] = self.{slot.param_name}")
    lines.extend(
        [
            "        return merged",
            "",
            "    async def _request(",
            "        self,",
            "        method: str,",
            "        path: str,",
            "        *,",
            "        params: dict[str, Any] | None = None,",
            "        headers: dict[str, str] | None = None,",
            "        cookies: dict[str, str] | None = None,",
            "        json_data: Any = None,",
            "        form_data: dict[str, Any] | None = None,",
            "        files_data: dict[str, Any] | None = None,",
            "        content_data: str | None = None,",
            "    ) -> Any:",
            "        req_headers = self._build_headers(headers)",
            "        req_cookies = self._build_cookies(cookies)",
        ]
    )

    if transport == "curl_cffi":
        lines.extend(
            [
                '        url = path if path.startswith(("http://", "https://")) else f"{self.base_url}{path}"',
            ]
        )
    else:
        lines.append("        url = path")

    lines.extend(
        [
            "        last_error: Exception | None = None",
            "        for attempt in range(self.max_retries + 1):",
            "            try:",
        ]
    )
    if transport == "curl_cffi":
        lines.extend(
            [
                "                resp = await self._session.request(",
                "                    method=method,",
                "                    url=url,",
                "                    params=params,",
                "                    headers=req_headers,",
                "                    cookies=req_cookies or None,",
                "                    json=json_data,",
                "                    data=form_data if form_data is not None else content_data,",
                "                    files=files_data,",
                "                    allow_redirects=True,",
                "                )",
            ]
        )
    else:
        lines.extend(
            [
                "                resp = await self._client.request(",
                "                    method=method,",
                "                    url=url,",
                "                    params=params,",
                "                    headers=req_headers,",
                "                    cookies=req_cookies or None,",
                "                    json=json_data,",
                "                    data=form_data,",
                "                    files=files_data,",
                "                    content=content_data,",
                "                )",
            ]
        )

    lines.extend(
        [
            "                if resp.status_code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:",
            '                    retry_after = resp.headers.get("Retry-After")',
            "                    delay = (",
            "                        float(retry_after)",
            "                        if retry_after and retry_after.replace('.', '', 1).isdigit()",
            "                        else self.retry_base_delay * (2**attempt) + random.uniform(0.0, 0.25)",
            "                    )",
            "                    await asyncio.sleep(delay)",
            "                    continue",
            "                if resp.status_code >= 400:",
            "                    raise ApiError(resp.status_code, str(resp.url), resp.text)",
            '                if not resp.text:',
            "                    return None",
            '                content_type = (resp.headers.get("content-type") or "").lower()',
            '                if "json" in content_type or resp.text.lstrip().startswith(("{", "[")):',
            "                    try:",
            "                        return resp.json()",
            "                    except ValueError:",
            "                        return resp.text",
            "                return resp.text",
            "            except ApiError:",
            "                raise",
            "            except Exception as exc:",
            "                last_error = exc",
            "                if attempt >= self.max_retries:",
            "                    raise",
            "                delay = self.retry_base_delay * (2**attempt) + random.uniform(0.0, 0.25)",
            "                await asyncio.sleep(delay)",
            "        if last_error is not None:",
            "            raise last_error",
            '        raise RuntimeError("Request failed unexpectedly")',
            "",
        ]
    )

    for ep in result.endpoints:
        lines.extend(_render_endpoint_method(ep, auth_slots, result.base_url))

    return "\n".join(lines).rstrip() + "\n"


def _render_smoke_test(result: ForgeResult, package_name: str, client_cls: str) -> str:
    auth_slots = collect_client_auth_slots(result)
    lines: list[str] = [
        "from __future__ import annotations",
        "",
        "import asyncio",
        "import os",
        "",
        f"from {package_name} import {client_cls}, ApiError",
        "",
        "",
        "async def run_smoke_test() -> None:",
    ]

    if auth_slots:
        env_list = ", ".join(repr(slot.env_var) for slot in auth_slots)
        lines.extend(
            [
                f"    required_envs = [{env_list}]",
                "    missing = [name for name in required_envs if not os.getenv(name)]",
                "    if missing:",
                '        print(f"Missing env credentials: {missing}")',
            ]
        )

    lines.append(f"    async with {client_cls}.from_env() as client:")
    get_endpoints = [ep for ep in result.endpoints if ep.method.upper() == "GET"]
    target_endpoints = get_endpoints if get_endpoints else result.endpoints[:1]

    if not target_endpoints:
        lines.append('        print("Client initialized successfully")')
    else:
        for ep in target_endpoints:
            path_args, query_args, form_args, _ = resolve_endpoint_arg_names(ep, auth_slots)
            call_parts: list[str] = []
            for p, arg_name in path_args:
                sample = p.sample_values[0] if p.sample_values else ("1" if p.inferred_type == "int" else "example")
                val_expr = repr(sample)
                call_parts.append(f"{arg_name}={val_expr}")
            if ep.request_body_kind == "json":
                ex = ep.request_example if ep.request_example is not None else {}
                call_parts.append(f"payload={repr(ex)}")
            elif ep.request_body_kind in {"form", "multipart"}:
                for p, arg_name in form_args:
                    if p.required:
                        sample = p.sample_values[0] if p.sample_values else "example"
                        call_parts.append(f"{arg_name}={repr(sample)}")
            elif ep.request_body_kind == "text":
                call_parts.append('payload=""')
            for p, arg_name in query_args:
                if p.required:
                    sample = p.sample_values[0] if p.sample_values else (1 if p.inferred_type == "int" else "example")
                    call_parts.append(f"{arg_name}={repr(sample)}")

            call_str = ", ".join(call_parts)
            lines.extend(
                [
                    "        try:",
                    f"            res = await client.{ep.operation_id}({call_str})",
                    f'            print("OK {ep.operation_id}:", type(res).__name__)',
                    "        except ApiError as exc:",
                    f'            print("API_ERROR {ep.operation_id}:", exc.status_code, exc.body[:200])',
                ]
            )

    lines.extend(
        [
            "",
            "",
            'if __name__ == "__main__":',
            "    asyncio.run(run_smoke_test())",
            "",
        ]
    )
    return "\n".join(lines)


def generate_python_sdk(
    result: ForgeResult,
    *,
    transport: str | None = None,
    single_file: bool = False,
) -> list[GeneratedFile]:
    chosen_transport = transport or result.recommended_transport
    if chosen_transport not in {"httpx", "curl_cffi"}:
        chosen_transport = result.recommended_transport

    package_name = to_snake_case(result.client_name, avoid_python_kw=True)
    if package_name.endswith("_client"):
        pkg_dir = package_name[:-7] or package_name
    else:
        pkg_dir = package_name

    client_cls = to_pascal_case(result.client_name)
    if not client_cls.endswith("Client"):
        client_cls = f"{client_cls}Client"

    models_code, model_names = _render_pydantic_models(result)

    if single_file:
        client_code = _render_python_client(
            result,
            transport=chosen_transport,
            model_names=model_names,
            single_file=True,
        )
        models_body_lines = models_code.splitlines()
        filtered_model_lines = [
            line
            for line in models_body_lines
            if line != "from __future__ import annotations"
            and line != "from typing import Any"
            and line != "from pydantic import BaseModel, ConfigDict, Field"
        ]
        combined_header = [
            "from __future__ import annotations",
            "",
            "import asyncio",
            "import os",
            "import random",
            "from typing import Any",
            "from urllib.parse import quote",
            "",
            "from curl_cffi.requests import AsyncSession"
            if chosen_transport == "curl_cffi"
            else "import httpx",
            "from pydantic import BaseModel, ConfigDict, Field",
        ]
        client_lines = client_code.splitlines()
        body_start = 0
        for i, ln in enumerate(client_lines):
            if ln.startswith("RETRYABLE_STATUS_CODES"):
                body_start = i
                break
        single_content = "\n".join(
            combined_header
            + [""]
            + filtered_model_lines
            + [""]
            + client_lines[body_start:]
        ).rstrip() + "\n"
        return [
            GeneratedFile(path=f"{pkg_dir}_client.py", content=single_content),
            GeneratedFile(
                path="smoke_test.py",
                content=_render_smoke_test(result, f"{pkg_dir}_client", client_cls),
            ),
        ]

    client_code = _render_python_client(
        result,
        transport=chosen_transport,
        model_names=model_names,
        single_file=False,
    )

    init_exports = ["ApiError", client_cls, *sorted(model_names)]
    init_lines = [
        f"from .client import ApiError, {client_cls}",
    ]
    if model_names:
        init_lines.append(f"from .models import {', '.join(sorted(model_names))}")
    init_lines.extend(["", f"__all__ = {json.dumps(init_exports)}", ""])

    deps = ["curl_cffi>=0.7.0" if chosen_transport == "curl_cffi" else "httpx>=0.27.0", "pydantic>=2.0.0"]

    return [
        GeneratedFile(path=f"{pkg_dir}/__init__.py", content="\n".join(init_lines)),
        GeneratedFile(path=f"{pkg_dir}/models.py", content=models_code),
        GeneratedFile(path=f"{pkg_dir}/client.py", content=client_code),
        GeneratedFile(
            path="smoke_test.py",
            content=_render_smoke_test(result, pkg_dir, client_cls),
        ),
        GeneratedFile(path="requirements.txt", content="\n".join(deps) + "\n"),
    ]

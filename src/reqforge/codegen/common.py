from __future__ import annotations

from dataclasses import dataclass

from reqforge.models import AuthKind, EndpointGroup, ForgeResult, ParamSpec
from reqforge.utils import to_camel_case, to_snake_case


@dataclass(slots=True)
class GeneratedFile:
    path: str
    content: str


@dataclass(slots=True)
class AuthSlot:
    param_name: str
    ts_param_name: str
    env_var: str
    location: str
    wire_name: str
    kind: AuthKind
    prefix: str = ""


def collect_client_auth_slots(result: ForgeResult) -> list[AuthSlot]:
    slots: list[AuthSlot] = []
    seen_wires: set[tuple[str, str]] = set()
    used_params: set[str] = set()
    used_envs: set[str] = set()

    all_findings = list(result.auth_findings)
    for ep in result.endpoints:
        all_findings.extend(ep.auth_findings)

    for finding in all_findings:
        wire_key = (finding.location, finding.name.lower())
        if wire_key in seen_wires:
            continue
        seen_wires.add(wire_key)

        param = to_snake_case(finding.param_name)
        while param in used_params:
            param = f"{param}_auth"
        used_params.add(param)

        ts_param = to_camel_case(param)
        env = finding.env_var
        while env in used_envs:
            env = f"{env}_AUTH"
        used_envs.add(env)

        prefix = ""
        if finding.kind == "bearer":
            prefix = "Bearer "
        elif finding.kind == "basic":
            prefix = "Basic "

        slots.append(
            AuthSlot(
                param_name=param,
                ts_param_name=ts_param,
                env_var=env,
                location=finding.location,
                wire_name=finding.name,
                kind=finding.kind,
                prefix=prefix,
            )
        )

    return slots


def safe_default_headers(endpoint: EndpointGroup, auth_slots: list[AuthSlot]) -> dict[str, str]:
    auth_header_names = {
        slot.wire_name.lower() for slot in auth_slots if slot.location == "header"
    }
    cleaned: dict[str, str] = {}
    for k, v in endpoint.required_headers.items():
        if k.lower() in auth_header_names:
            continue
        if (
            endpoint.request_body_kind == "multipart"
            and k.lower() == "content-type"
            and "boundary=" in v.lower()
        ):
            continue
        cleaned[k] = v
    return cleaned


def safe_default_cookies(endpoint: EndpointGroup, auth_slots: list[AuthSlot]) -> dict[str, str]:
    auth_cookie_names = {
        slot.wire_name.lower() for slot in auth_slots if slot.location == "cookie"
    }
    return {
        k: v
        for k, v in endpoint.required_cookies.items()
        if k.lower() not in auth_cookie_names
    }


def is_auth_query_param(wire_name: str, auth_slots: list[AuthSlot]) -> AuthSlot | None:
    lower = wire_name.lower()
    for slot in auth_slots:
        if slot.location == "query" and slot.wire_name.lower() == lower:
            return slot
    return None


def resolve_endpoint_arg_names(
    endpoint: EndpointGroup,
    auth_slots: list[AuthSlot],
    *,
    camel_case: bool = False,
) -> tuple[list[tuple[ParamSpec, str]], list[tuple[ParamSpec, str]], list[tuple[ParamSpec, str]], str]:
    used: set[str] = {
        "self",
        "headers",
        "cookies",
        "payload",
        "body",
        "options",
        "query",
        "form",
        "params",
        "url",
        "path",
        "data",
        "req_headers",
        "req_cookies",
        "json_body",
        "form_data",
        "files_data",
        "form_params",
    }
    path_args: list[tuple[ParamSpec, str]] = []
    query_args: list[tuple[ParamSpec, str]] = []
    form_args: list[tuple[ParamSpec, str]] = []

    def alloc(raw: str, suffix: str) -> str:
        base = to_camel_case(raw) if camel_case else to_snake_case(raw)
        cand = base
        if cand in used:
            cand = f"{base}{suffix}" if camel_case else f"{base}_{suffix}"
        counter = 2
        stem = cand
        while cand in used:
            cand = f"{stem}{counter}"
            counter += 1
        used.add(cand)
        return cand

    for p in endpoint.path_params:
        path_args.append((p, alloc(p.name, "Path" if camel_case else "path")))

    for p in endpoint.query_params:
        if is_auth_query_param(p.wire_name, auth_slots) is not None:
            continue
        query_args.append((p, alloc(p.name, "Query" if camel_case else "query")))

    for p in endpoint.request_form_params:
        form_args.append((p, alloc(p.name, "Field" if camel_case else "field")))

    body_arg = "payload"
    return path_args, query_args, form_args, body_arg

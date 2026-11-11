from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
BodyKind = Literal["none", "json", "form", "multipart", "text", "binary"]
TransportMode = Literal["auto", "httpx", "curl_cffi"]
TargetLanguage = Literal["python", "typescript", "javascript", "all"]
AuthKind = Literal[
    "bearer",
    "basic",
    "api_key",
    "csrf",
    "session_cookie",
    "hmac_signature",
    "oauth",
    "custom_token",
]


@dataclass(slots=True)
class AuthFinding:
    kind: AuthKind
    location: Literal["header", "cookie", "query"]
    name: str
    masked_preview: str
    env_var: str
    confidence: float
    param_name: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "location": self.location,
            "name": self.name,
            "masked_preview": self.masked_preview,
            "env_var": self.env_var,
            "confidence": self.confidence,
            "param_name": self.param_name,
            "notes": self.notes,
        }


@dataclass(slots=True)
class ParamSpec:
    name: str
    wire_name: str
    location: Literal["path", "query", "header", "cookie", "body"]
    inferred_type: Literal["str", "int", "float", "bool", "uuid", "datetime", "json"]
    required: bool = True
    default_value: Any = None
    sample_values: list[Any] = field(default_factory=list)
    dynamic_reason: str = ""
    is_noise: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "wire_name": self.wire_name,
            "location": self.location,
            "inferred_type": self.inferred_type,
            "required": self.required,
            "default_value": self.default_value,
            "sample_values": self.sample_values[:5],
            "dynamic_reason": self.dynamic_reason,
            "is_noise": self.is_noise,
        }


@dataclass(slots=True)
class SchemaField:
    name: str
    wire_name: str
    node: JsonSchemaNode
    required: bool = True
    nullable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "wire_name": self.wire_name,
            "required": self.required,
            "nullable": self.nullable,
            "schema": self.node.to_dict(),
        }


@dataclass(slots=True)
class JsonSchemaNode:
    kind: Literal["object", "array", "string", "integer", "number", "boolean", "null", "any", "union"]
    fields: dict[str, SchemaField] = field(default_factory=dict)
    items: JsonSchemaNode | None = None
    variants: list[JsonSchemaNode] = field(default_factory=list)
    format_hint: str | None = None
    example: Any = None
    model_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.model_name:
            out["model_name"] = self.model_name
        if self.format_hint:
            out["format"] = self.format_hint
        if self.fields:
            out["fields"] = {k: v.to_dict() for k, v in self.fields.items()}
        if self.items is not None:
            out["items"] = self.items.to_dict()
        if self.variants:
            out["variants"] = [v.to_dict() for v in self.variants]
        return out


@dataclass(slots=True)
class CapturedRequest:
    id: str
    method: str
    url: str
    scheme: str
    host: str
    port: int | None
    path: str
    query_params: list[tuple[str, str]] = field(default_factory=list)
    headers: list[tuple[str, str]] = field(default_factory=list)
    cookies: dict[str, str] = field(default_factory=dict)
    body_kind: BodyKind = "none"
    body_json: Any = None
    body_form: list[tuple[str, str]] = field(default_factory=list)
    body_multipart: list[dict[str, Any]] = field(default_factory=list)
    body_text: str | None = None
    response_status: int | None = None
    response_headers: list[tuple[str, str]] = field(default_factory=list)
    response_content_type: str | None = None
    response_body_text: str | None = None
    response_json: Any = None
    source_type: Literal["har", "curl"] = "curl"
    timestamp: str | None = None
    resource_type: str | None = None

    def base_url(self) -> str:
        if self.port is not None:
            is_default = (self.scheme == "https" and self.port == 443) or (
                self.scheme == "http" and self.port == 80
            )
            if not is_default:
                return f"{self.scheme}://{self.host}:{self.port}"
        return f"{self.scheme}://{self.host}"

    def header_map(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for k, v in self.headers:
            merged[k] = v
        return merged

    def get_header(self, name: str) -> str | None:
        target = name.lower()
        for k, v in reversed(self.headers):
            if k.lower() == target:
                return v
        return None


@dataclass(slots=True)
class FilteredEntry:
    url: str
    method: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "method": self.method, "reason": self.reason}


@dataclass(slots=True)
class ProbeReport:
    transport_used: Literal["httpx", "curl_cffi"]
    recommended_transport: Literal["httpx", "curl_cffi"]
    baseline_status: int
    baseline_matched: bool
    httpx_baseline_ok: bool | None = None
    curl_cffi_baseline_ok: bool | None = None
    total_probes: int = 0
    required_headers: list[str] = field(default_factory=list)
    removed_headers: list[str] = field(default_factory=list)
    required_cookies: list[str] = field(default_factory=list)
    removed_cookies: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport_used": self.transport_used,
            "recommended_transport": self.recommended_transport,
            "baseline_status": self.baseline_status,
            "baseline_matched": self.baseline_matched,
            "httpx_baseline_ok": self.httpx_baseline_ok,
            "curl_cffi_baseline_ok": self.curl_cffi_baseline_ok,
            "total_probes": self.total_probes,
            "required_headers": self.required_headers,
            "removed_headers": self.removed_headers,
            "required_cookies": self.required_cookies,
            "removed_cookies": self.removed_cookies,
            "notes": self.notes,
        }


@dataclass(slots=True)
class EndpointGroup:
    operation_id: str
    method: str
    base_url: str
    path_template: str
    path_params: list[ParamSpec] = field(default_factory=list)
    query_params: list[ParamSpec] = field(default_factory=list)
    required_headers: dict[str, str] = field(default_factory=dict)
    required_cookies: dict[str, str] = field(default_factory=dict)
    auth_findings: list[AuthFinding] = field(default_factory=list)
    request_body_kind: BodyKind = "none"
    request_schema: JsonSchemaNode | None = None
    request_model_name: str | None = None
    request_example: Any = None
    request_form_params: list[ParamSpec] = field(default_factory=list)
    response_schema: JsonSchemaNode | None = None
    response_model_name: str | None = None
    response_example: Any = None
    response_status: int = 200
    dropped_headers: list[str] = field(default_factory=list)
    dropped_cookies: list[str] = field(default_factory=list)
    probe_report: ProbeReport | None = None
    samples: list[CapturedRequest] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "method": self.method,
            "base_url": self.base_url,
            "path_template": self.path_template,
            "path_params": [p.to_dict() for p in self.path_params],
            "query_params": [p.to_dict() for p in self.query_params],
            "required_headers": dict(self.required_headers),
            "required_cookies": {k: "***" for k in self.required_cookies},
            "auth_findings": [a.to_dict() for a in self.auth_findings],
            "request_body_kind": self.request_body_kind,
            "request_model_name": self.request_model_name,
            "request_schema": self.request_schema.to_dict() if self.request_schema else None,
            "request_form_params": [p.to_dict() for p in self.request_form_params],
            "response_status": self.response_status,
            "response_model_name": self.response_model_name,
            "response_schema": self.response_schema.to_dict() if self.response_schema else None,
            "dropped_headers": self.dropped_headers,
            "dropped_cookies": self.dropped_cookies,
            "probe_report": self.probe_report.to_dict() if self.probe_report else None,
            "sample_count": len(self.samples),
        }


@dataclass(slots=True)
class ForgeResult:
    base_url: str
    client_name: str
    endpoints: list[EndpointGroup] = field(default_factory=list)
    filtered_out: list[FilteredEntry] = field(default_factory=list)
    auth_findings: list[AuthFinding] = field(default_factory=list)
    recommended_transport: Literal["httpx", "curl_cffi"] = "httpx"
    warnings: list[str] = field(default_factory=list)
    total_ingested: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "client_name": self.client_name,
            "total_ingested": self.total_ingested,
            "kept_endpoints": len(self.endpoints),
            "filtered_count": len(self.filtered_out),
            "recommended_transport": self.recommended_transport,
            "auth_findings": [a.to_dict() for a in self.auth_findings],
            "warnings": self.warnings,
            "endpoints": [e.to_dict() for e in self.endpoints],
            "filtered_out": [f.to_dict() for f in self.filtered_out],
        }

from __future__ import annotations

import json
import keyword
import re
from typing import Any
from urllib.parse import parse_qsl, urlparse

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
ISO_DT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
OBJECT_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")
INT_RE = re.compile(r"^-?\d+$")
FLOAT_RE = re.compile(r"^-?\d+\.\d+$")

PYTHON_RESERVED = set(keyword.kwlist) | {"None", "True", "False", "self", "cls"}
TS_RESERVED = {
    "break",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "debugger",
    "default",
    "delete",
    "do",
    "else",
    "enum",
    "export",
    "extends",
    "false",
    "finally",
    "for",
    "function",
    "if",
    "import",
    "in",
    "instanceof",
    "new",
    "null",
    "return",
    "super",
    "switch",
    "this",
    "throw",
    "true",
    "try",
    "typeof",
    "var",
    "void",
    "while",
    "with",
    "as",
    "implements",
    "interface",
    "let",
    "package",
    "private",
    "protected",
    "public",
    "static",
    "yield",
    "any",
    "boolean",
    "constructor",
    "declare",
    "get",
    "module",
    "require",
    "number",
    "set",
    "string",
    "symbol",
    "type",
    "from",
    "of",
}

NON_SINGULAR_WORDS = {
    "status",
    "news",
    "data",
    "analytics",
    "settings",
    "metrics",
    "series",
    "species",
    "process",
    "address",
    "access",
    "class",
    "alias",
    "canvas",
    "redis",
    "oauth",
    "graphql",
    "cors",
}


def split_words(text: str) -> list[str]:
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text.strip())
    s2 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s1)
    parts = re.split(r"[^a-zA-Z0-9]+", s2)
    return [p for p in parts if p]


def to_snake_case(text: str, *, avoid_python_kw: bool = True) -> str:
    words = [w.lower() for w in split_words(text)]
    if not words:
        return "value"
    joined = "_".join(words)
    if joined[0].isdigit():
        joined = f"field_{joined}"
    if avoid_python_kw and joined in PYTHON_RESERVED:
        joined = f"{joined}_"
    return joined


def to_camel_case(text: str, *, avoid_ts_kw: bool = True) -> str:
    words = split_words(text)
    if not words:
        return "value"
    first = words[0].lower()
    rest = [w.capitalize() for w in words[1:]]
    joined = first + "".join(rest)
    if joined[0].isdigit():
        joined = f"field{joined}"
    if avoid_ts_kw and joined in TS_RESERVED:
        joined = f"{joined}Value"
    return joined


def to_pascal_case(text: str) -> str:
    words = split_words(text)
    if not words:
        return "Model"
    joined = "".join(w[:1].upper() + w[1:] for w in words)
    if joined[0].isdigit():
        joined = f"Model{joined}"
    return joined


def singularize(word: str) -> str:
    lower = word.lower()
    if lower in NON_SINGULAR_WORDS or len(lower) <= 3:
        return lower
    if lower.endswith("ies") and len(lower) > 4:
        return lower[:-3] + "y"
    if lower.endswith(("ses", "xes", "zes", "ches", "shes")) and len(lower) > 4:
        return lower[:-2]
    if lower.endswith("s") and not lower.endswith("ss"):
        return lower[:-1]
    return lower


def mask_secret(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        return "***"
    parts = trimmed.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() in {"bearer", "basic", "token", "apikey", "digest"}:
        scheme, token = parts[0], parts[1].strip()
        return f"{scheme} {mask_token_core(token)}"
    return mask_token_core(trimmed)


def mask_token_core(token: str) -> str:
    if len(token) <= 8:
        return "*" * max(3, len(token))
    return f"{token[:4]}...{token[-4:]}"


def parse_cookie_header(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for chunk in raw.split(";"):
        part = chunk.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        key = k.strip()
        if key:
            cookies[key] = v.strip()
    return cookies


def format_cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def is_uuid(val: str) -> bool:
    return bool(UUID_RE.match(val))


def is_iso_datetime(val: str) -> bool:
    return bool(ISO_DT_RE.match(val))


def is_email(val: str) -> bool:
    return bool(EMAIL_RE.match(val))


def is_url(val: str) -> bool:
    if not val.startswith(("http://", "https://")):
        return False
    parsed = urlparse(val)
    return bool(parsed.netloc)


def is_object_id(val: str) -> bool:
    return bool(OBJECT_ID_RE.match(val))


def infer_scalar_type(
    values: list[Any],
) -> tuple[str, str | None]:
    non_empty = [v for v in values if v is not None and v != ""]
    if not non_empty:
        return "str", None
    if all(isinstance(v, bool) for v in non_empty):
        return "bool", None
    if all(isinstance(v, int) and not isinstance(v, bool) for v in non_empty):
        return "int", None
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_empty):
        return "float", None

    strs = [str(v).strip() for v in non_empty]
    lowers = [s.lower() for s in strs]
    if all(s in {"true", "false"} for s in lowers):
        return "bool", None
    if all(INT_RE.match(s) for s in strs):
        return "int", None
    if all(INT_RE.match(s) or FLOAT_RE.match(s) for s in strs):
        return "float", None
    if all(is_uuid(s) for s in strs):
        return "uuid", "uuid"
    if all(is_iso_datetime(s) for s in strs):
        return "datetime", "date-time"
    return "str", None


def try_parse_json(raw: str | None) -> tuple[bool, Any]:
    if raw is None:
        return False, None
    stripped = raw.strip()
    if not stripped:
        return False, None
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return False, None
    try:
        return True, json.loads(stripped, strict=False)
    except (ValueError, TypeError):
        return False, None


def parse_url_parts(raw_url: str) -> tuple[str, str, int | None, str, list[tuple[str, str]]]:
    cleaned = raw_url.strip()
    if cleaned and "://" not in cleaned and not cleaned.startswith("/"):
        cleaned = f"https://{cleaned}"
    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "https").lower()
    host = parsed.hostname or ""
    port = parsed.port
    path = parsed.path or "/"
    query = parse_qsl(parsed.query, keep_blank_values=True)
    return scheme, host, port, path, query

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from reqforge.errors import ParseError
from reqforge.ingest.curl import parse_curls
from reqforge.ingest.har import parse_har
from reqforge.models import CapturedRequest


def detect_input_format(raw_text: str, source_name: str = "") -> Literal["har", "curl"]:
    cleaned = raw_text.lstrip("\ufeff").strip()
    if not cleaned:
        raise ParseError("Input is empty")

    if source_name.lower().endswith(".har"):
        return "har"

    if cleaned.startswith("{"):
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict) and "log" in obj:
                return "har"
        except ValueError:
            pass

    return "curl"


def ingest_text(raw_text: str, *, source_name: str = "") -> list[CapturedRequest]:
    fmt = detect_input_format(raw_text, source_name=source_name)
    if fmt == "har":
        return parse_har(raw_text)
    return parse_curls(raw_text)


def ingest_file(path: str | Path) -> list[CapturedRequest]:
    p = Path(path)
    if not p.exists():
        raise ParseError(f"Input file not found: {p}")
    content = p.read_text(encoding="utf-8", errors="replace")
    return ingest_text(content, source_name=p.name)

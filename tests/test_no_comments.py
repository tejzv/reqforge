from __future__ import annotations

import io
import os
import re
import tempfile
import tokenize
import unittest
from pathlib import Path

from reqforge.pipeline import ForgeOptions, run_pipeline

ROOT = Path(__file__).resolve().parent.parent

SAMPLE_CURL = (
    "curl 'https://api.example.com/v1/users/42?active=true' "
    "-H 'Authorization: Bearer tok_123456' "
    "-H 'Content-Type: application/json' "
    "-H 'sec-fetch-mode: cors' "
    "--data-raw '{\"from\":\"web\",\"limit\":10}'"
)


def _assert_no_python_comments_or_docstrings(test_case: unittest.TestCase, source: str, label: str) -> None:
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    for tok in tokens:
        test_case.assertNotEqual(
            tok.type,
            tokenize.COMMENT,
            f"Found Python comment in {label} at line {tok.start[0]}: {tok.string}",
        )


def _strip_js_strings(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_quote: str | None = None
    while i < n:
        ch = text[i]
        if in_quote is not None:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == in_quote:
                in_quote = None
            i += 1
            continue
        if ch in {'"', "'", "`"}:
            in_quote = ch
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class TestNoCommentsInvariant(unittest.TestCase):
    def test_repo_files_have_no_comments(self) -> None:
        for folder in (ROOT / "src", ROOT / "tests"):
            for path in sorted(folder.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                content = path.read_text(encoding="utf-8")
                if path.suffix == ".py":
                    _assert_no_python_comments_or_docstrings(self, content, str(path))
                elif path.suffix in {".js", ".ts", ".mjs", ".css"}:
                    stripped = _strip_js_strings(content)
                    self.assertNotIn("//", stripped, f"Found // comment in {path}")
                    self.assertNotIn("/*", stripped, f"Found /* comment in {path}")
                elif path.suffix == ".html":
                    self.assertNotIn("<!--", content, f"Found HTML comment in {path}")

        for cfg in (ROOT / "pyproject.toml", ROOT / ".gitignore"):
            content = cfg.read_text(encoding="utf-8")
            for idx, line in enumerate(content.splitlines(), start=1):
                self.assertFalse(
                    line.lstrip().startswith("#"),
                    f"Found # comment in {cfg} line {idx}",
                )

    def test_generated_sdks_have_no_comments(self) -> None:
        for target in ("python", "typescript", "javascript"):
            for transport in ("httpx", "curl_cffi"):
                if target != "python" and transport == "curl_cffi":
                    continue
                _, generated = run_pipeline(
                    SAMPLE_CURL,
                    options=ForgeOptions(target=target, transport=transport),
                )
                for gf in generated[target]:
                    if gf.path.endswith(".py"):
                        _assert_no_python_comments_or_docstrings(self, gf.content, gf.path)
                    elif gf.path.endswith((".ts", ".mjs", ".js")):
                        stripped = _strip_js_strings(gf.content)
                        self.assertNotIn("//", stripped, f"Found // in generated {gf.path}")
                        self.assertNotIn("/*", stripped, f"Found /* in generated {gf.path}")

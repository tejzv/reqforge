from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode

from reqforge.errors import ParseError
from reqforge.models import BodyKind, CapturedRequest
from reqforge.utils import parse_cookie_header, parse_url_parts, try_parse_json

ANSI_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}

VALUE_FLAGS = {
    "-X",
    "--request",
    "-H",
    "--header",
    "-b",
    "--cookie",
    "-c",
    "--cookie-jar",
    "-A",
    "--user-agent",
    "-e",
    "--referer",
    "-u",
    "--user",
    "-d",
    "--data",
    "--data-raw",
    "--data-ascii",
    "--data-binary",
    "--data-urlencode",
    "--json",
    "-F",
    "--form",
    "--form-string",
    "--url",
    "-o",
    "--output",
    "-x",
    "--proxy",
    "--proxy-user",
    "-U",
    "--connect-timeout",
    "-m",
    "--max-time",
    "--retry",
    "--resolve",
    "--interface",
    "--cacert",
    "--cert",
    "--key",
    "-w",
    "--write-out",
    "-T",
    "--upload-file",
}

SHORT_VALUE_FLAGS = {f[1] for f in VALUE_FLAGS if len(f) == 2 and f.startswith("-")}


def _decode_ansi_c_chunk(text: str, start: int) -> tuple[str, int]:
    out: list[str] = []
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "'":
            return "".join(out), i + 1
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in ANSI_ESCAPES:
                out.append(ANSI_ESCAPES[nxt])
                i += 2
                continue
            if nxt == "x" and i + 2 < n:
                hex_match = re.match(r"[0-9a-fA-F]{1,2}", text[i + 2 : i + 4])
                if hex_match:
                    out.append(chr(int(hex_match.group(0), 16)))
                    i += 2 + len(hex_match.group(0))
                    continue
            if nxt == "u" and i + 5 < n:
                hex_match = re.match(r"[0-9a-fA-F]{4}", text[i + 2 : i + 6])
                if hex_match:
                    out.append(chr(int(hex_match.group(0), 16)))
                    i += 6
                    continue
            if nxt == "U" and i + 9 < n:
                hex_match = re.match(r"[0-9a-fA-F]{8}", text[i + 2 : i + 10])
                if hex_match:
                    out.append(chr(int(hex_match.group(0), 16)))
                    i += 10
                    continue
            if nxt in "01234567":
                oct_match = re.match(r"[0-7]{1,3}", text[i + 1 : i + 4])
                if oct_match:
                    out.append(chr(int(oct_match.group(0), 8)))
                    i += 1 + len(oct_match.group(0))
                    continue
            out.append("\\" + nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    raise ParseError("Unterminated $'...' ANSI-C quoted string in cURL command")


def _is_cmd_style(raw: str) -> bool:
    if re.search(r"\^\r?\n", raw):
        return True
    if re.search(r'\^["&%<>|]', raw):
        return True
    return False


def _preprocess_continuations(raw: str) -> tuple[str, bool]:
    cmd_style = _is_cmd_style(raw)
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if cmd_style:
        text = re.sub(r"\^\n[ \t]*", " ", text)
    else:
        text = re.sub(r"\\\n[ \t]*", " ", text)
        text = re.sub(r"`\n[ \t]*", " ", text)
    return text, cmd_style


def _tokenize_cmd(line: str) -> list[str]:
    tokens: list[str] = []
    buf: list[str] = []
    has_token = False
    i = 0
    n = len(line)
    quote_mode: str | None = None

    while i < n:
        ch = line[i]
        if quote_mode == "caret":
            if line[i : i + 2] == '^"' and (i + 2 >= n or line[i + 2] in " \t\n"):
                quote_mode = None
                i += 2
                continue
            if line[i : i + 3] == '\\^"':
                buf.append('"')
                i += 3
                continue
            if line[i : i + 4] == '^\\^"':
                buf.append('"')
                i += 4
                continue
            if ch == "^" and i + 1 < n:
                nxt = line[i + 1]
                if nxt == '"' and i + 2 < n and line[i + 2] not in " \t\n":
                    buf.append('"')
                    i += 2
                    continue
                if nxt == '"':
                    quote_mode = None
                    i += 2
                    continue
                buf.append(nxt)
                i += 2
                continue
            if ch == "\\" and i + 1 < n and line[i + 1] == '"':
                buf.append('"')
                i += 2
                continue
            buf.append(ch)
            i += 1
            continue

        if quote_mode == "plain":
            if ch == "^" and i + 1 < n:
                nxt = line[i + 1]
                if nxt == '"':
                    buf.append('"')
                    i += 2
                    continue
                if nxt in "^&|<>%":
                    buf.append(nxt)
                    i += 2
                    continue
            if ch == "\\" and i + 1 < n and line[i + 1] == '"':
                buf.append('"')
                i += 2
                continue
            if ch == '"':
                if i + 1 < n and line[i + 1] == '"':
                    buf.append('"')
                    i += 2
                    continue
                quote_mode = None
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue

        if ch in " \t\n":
            if has_token:
                tokens.append("".join(buf))
                buf.clear()
                has_token = False
            i += 1
            continue
        if line[i : i + 2] == '^"':
            quote_mode = "caret"
            has_token = True
            i += 2
            continue
        if ch == "^" and i + 1 < n:
            buf.append(line[i + 1])
            has_token = True
            i += 2
            continue
        if ch == '"':
            quote_mode = "plain"
            has_token = True
            i += 1
            continue
        buf.append(ch)
        has_token = True
        i += 1

    if quote_mode is not None:
        raise ParseError("Unterminated double quote in Windows CMD cURL command")
    if has_token:
        tokens.append("".join(buf))
    return tokens


def _tokenize_posix(text: str) -> list[list[str]]:
    commands: list[list[str]] = []
    tokens: list[str] = []
    buf: list[str] = []
    has_token = False
    i = 0
    n = len(text)
    quote: str | None = None

    def flush_token() -> None:
        nonlocal has_token
        if has_token:
            tokens.append("".join(buf))
            buf.clear()
            has_token = False

    def flush_command() -> None:
        flush_token()
        if tokens:
            commands.append(list(tokens))
            tokens.clear()

    while i < n:
        ch = text[i]
        if quote == "'":
            if ch == "'":
                quote = None
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue

        if quote == '"':
            if ch in {"\\", "`"} and i + 1 < n:
                nxt = text[i + 1]
                if nxt in {'"', "\\", "$", "`"}:
                    buf.append(nxt)
                    i += 2
                    continue
            if ch == '"':
                quote = None
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue

        if ch == "\n" or ch == ";":
            flush_command()
            i += 1
            continue
        if ch == "&" and i + 1 < n and text[i + 1] == "&":
            flush_command()
            i += 2
            continue
        if ch in " \t":
            flush_token()
            i += 1
            continue
        if ch == "#" and not has_token and not tokens:
            nl = text.find("\n", i)
            if nl == -1:
                break
            i = nl + 1
            continue
        if ch == "$" and i + 1 < n and text[i + 1] == "'":
            decoded, next_idx = _decode_ansi_c_chunk(text, i + 2)
            buf.append(decoded)
            has_token = True
            i = next_idx
            continue
        if ch == "'":
            quote = "'"
            has_token = True
            i += 1
            continue
        if ch == '"':
            quote = '"'
            has_token = True
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            buf.append(text[i + 1])
            has_token = True
            i += 2
            continue

        buf.append(ch)
        has_token = True
        i += 1

    if quote is not None:
        raise ParseError("Unterminated quote in cURL command")
    flush_command()
    return commands


def _split_curl_commands(raw: str) -> list[list[str]]:
    text, cmd_style = _preprocess_continuations(raw.strip())
    if cmd_style:
        commands: list[list[str]] = []
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            toks = _tokenize_cmd(stripped)
            if toks:
                commands.append(toks)
        return commands
    return _tokenize_posix(text)


def _encode_urlencoded_item(item: str) -> str:
    if "=" in item:
        key, val = item.split("=", 1)
        if not key:
            return quote_plus(val)
        return f"{quote_plus(key)}={quote_plus(val)}"
    return quote_plus(item)


def _parse_single_curl_tokens(tokens: list[str], idx: int) -> CapturedRequest:
    if not tokens:
        raise ParseError("Empty cURL command")

    pos = 0
    first = tokens[0].lower()
    if first in {"curl", "curl.exe"} or first.endswith("/curl") or first.endswith("\\curl.exe"):
        pos = 1

    method_explicit: str | None = None
    is_head = False
    is_get = False
    use_json_flag = False
    url: str | None = None
    headers: list[tuple[str, str]] = []
    cookies: dict[str, str] = {}
    data_chunks: list[str] = []
    form_parts: list[dict[str, Any]] = []
    basic_auth: str | None = None
    end_of_flags = False

    n = len(tokens)
    while pos < n:
        tok = tokens[pos]
        if end_of_flags:
            if url is None:
                url = tok
            pos += 1
            continue

        if tok == "--":
            end_of_flags = True
            pos += 1
            continue

        if tok.startswith("--") and "=" in tok:
            flag, val = tok.split("=", 1)
            pos += 1
        elif tok in VALUE_FLAGS:
            flag = tok
            if pos + 1 >= n:
                raise ParseError(f"cURL flag {flag} is missing a value")
            val = tokens[pos + 1]
            pos += 2
        elif (
            len(tok) > 2
            and tok.startswith("-")
            and not tok.startswith("--")
            and tok[1] in SHORT_VALUE_FLAGS
        ):
            flag = tok[:2]
            val = tok[2:]
            pos += 1
        elif tok.startswith("-"):
            if tok in {"-I", "--head"}:
                is_head = True
            elif tok in {"-G", "--get"}:
                is_get = True
            elif (
                not tok.startswith("--")
                and len(tok) > 1
                and all(c in "IkGLsSvifk12346" for c in tok[1:])
            ):
                if "I" in tok[1:]:
                    is_head = True
                if "G" in tok[1:]:
                    is_get = True
            pos += 1
            continue
        else:
            if url is None:
                url = tok
            pos += 1
            continue

        if flag in {"-X", "--request"}:
            method_explicit = val.upper().strip()
        elif flag == "--url":
            url = val
        elif flag in {"-H", "--header"}:
            if ":" in val:
                h_name, h_val = val.split(":", 1)
                name = h_name.strip()
                value = h_val.lstrip(" ")
                if name and not name.startswith(":"):
                    if name.lower() == "cookie":
                        cookies.update(parse_cookie_header(value))
                    else:
                        headers.append((name, value))
            elif val.endswith(";"):
                name = val[:-1].strip()
                if name and not name.startswith(":"):
                    headers.append((name, ""))
        elif flag in {"-b", "--cookie"}:
            if "=" in val:
                cookies.update(parse_cookie_header(val))
        elif flag in {"-A", "--user-agent"}:
            headers.append(("User-Agent", val))
        elif flag in {"-e", "--referer"}:
            headers.append(("Referer", val))
        elif flag in {"-u", "--user"}:
            encoded = base64.b64encode(val.encode("utf-8")).decode("ascii")
            basic_auth = f"Basic {encoded}"
        elif flag in {"-d", "--data", "--data-raw", "--data-ascii", "--data-binary"}:
            data_chunks.append(val)
        elif flag == "--data-urlencode":
            data_chunks.append(_encode_urlencoded_item(val))
        elif flag == "--json":
            use_json_flag = True
            data_chunks.append(val)
        elif flag in {"-F", "--form", "--form-string"}:
            if "=" in val:
                fname, fval = val.split("=", 1)
                if fval.startswith("@") and flag != "--form-string":
                    form_parts.append({"name": fname, "value": "", "filename": fval[1:]})
                else:
                    form_parts.append({"name": fname, "value": fval, "filename": None})

    if not url:
        raise ParseError("No URL found in cURL command")

    if basic_auth and not any(k.lower() == "authorization" for k, _ in headers):
        headers.append(("Authorization", basic_auth))

    if use_json_flag:
        if not any(k.lower() == "content-type" for k, _ in headers):
            headers.append(("Content-Type", "application/json"))
        if not any(k.lower() == "accept" for k, _ in headers):
            headers.append(("Accept", "application/json"))

    scheme, host, port, path, query_params = parse_url_parts(url)
    if not host:
        raise ParseError(f"Invalid URL in cURL command: {url}")

    raw_body = "&".join(data_chunks) if data_chunks else None

    if method_explicit:
        method = method_explicit
    elif is_head:
        method = "HEAD"
    elif is_get:
        method = "GET"
    elif data_chunks or form_parts:
        method = "POST"
    else:
        method = "GET"

    if is_get and raw_body:
        extra_params = parse_qsl(raw_body, keep_blank_values=True)
        query_params.extend(extra_params)
        raw_body = None

    content_type = ""
    for k, v in reversed(headers):
        if k.lower() == "content-type":
            content_type = v.lower()
            break

    body_kind: BodyKind = "none"
    body_json: Any = None
    body_form: list[tuple[str, str]] = []
    body_text: str | None = None

    if form_parts:
        body_kind = "multipart"
    elif raw_body is not None and raw_body != "":
        body_text = raw_body
        is_json, parsed_json = try_parse_json(raw_body)
        if is_json and ("json" in content_type or not content_type):
            body_kind = "json"
            body_json = parsed_json
        elif "application/x-www-form-urlencoded" in content_type or (
            not content_type and "=" in raw_body and not raw_body.lstrip().startswith("<")
        ):
            body_kind = "form"
            body_form = parse_qsl(raw_body, keep_blank_values=True)
        else:
            body_kind = "text"

    full_url = url
    if is_get and query_params and "?" not in url:
        full_url = f"{url}?{urlencode(query_params)}"

    return CapturedRequest(
        id=f"curl_{idx}",
        method=method,
        url=full_url,
        scheme=scheme,
        host=host,
        port=port,
        path=path,
        query_params=query_params,
        headers=headers,
        cookies=cookies,
        body_kind=body_kind,
        body_json=body_json,
        body_form=body_form,
        body_multipart=form_parts,
        body_text=body_text,
        source_type="curl",
    )


def parse_curl(raw: str) -> CapturedRequest:
    parsed = parse_curls(raw)
    return parsed[0]


def parse_curls(raw: str) -> list[CapturedRequest]:
    stripped = raw.lstrip("\ufeff").strip()
    if not stripped:
        raise ParseError("Empty cURL input")

    commands = _split_curl_commands(stripped)
    if not commands:
        raise ParseError("No cURL commands found")

    results: list[CapturedRequest] = []
    for idx, tokens in enumerate(commands, start=1):
        results.append(_parse_single_curl_tokens(tokens, idx))
    return results

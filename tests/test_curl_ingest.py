from __future__ import annotations

import unittest

from reqforge.errors import ParseError
from reqforge.ingest import detect_input_format, parse_curl, parse_curls


class TestCurlIngest(unittest.TestCase):
    def test_chrome_bash_curl_with_ansi_c_quoting(self) -> None:
        raw = (
            "curl 'https://api.example.com/v1/search?q=reqforge&page=2' \\\n"
            "  -H 'accept: application/json' \\\n"
            "  -H $'cookie: _ga=GA1.2; session=abc\\'def' \\\n"
            "  -H 'authorization: Bearer tok_xyz' \\\n"
            "  --data-raw $'{\"query\":\"hello\\nworld\",\"ok\":true}' \\\n"
            "  --compressed"
        )
        self.assertEqual(detect_input_format(raw), "curl")
        req = parse_curl(raw)
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.host, "api.example.com")
        self.assertEqual(req.path, "/v1/search")
        self.assertEqual(req.query_params, [("q", "reqforge"), ("page", "2")])
        self.assertEqual(req.cookies["session"], "abc'def")
        self.assertEqual(req.cookies["_ga"], "GA1.2")
        self.assertEqual(req.body_kind, "json")
        self.assertEqual(req.body_json, {"query": "hello\nworld", "ok": True})

    def test_windows_cmd_curl(self) -> None:
        raw = (
            'curl ^"https://api.example.com/v1/items^" ^\r\n'
            '  -X POST ^\r\n'
            '  -H ^"Content-Type: application/json^" ^\r\n'
            '  -H ^"X-Api-Key: key_123^" ^\r\n'
            '  --data-raw ^"^{^\\^"name^\\^":^\\^"widget^\\^"^}^"'
        )
        req = parse_curl(raw)
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.path, "/v1/items")
        self.assertEqual(req.get_header("X-Api-Key"), "key_123")
        self.assertEqual(req.body_kind, "json")
        self.assertEqual(req.body_json, {"name": "widget"})

    def test_multiple_curls_and_get_data_conversion(self) -> None:
        raw = (
            "curl -G 'https://api.example.com/items' -d 'limit=10' --data-urlencode 'q=hello world'\n"
            "curl 'https://api.example.com/items' --json '{\"name\":\"new\"}' -u alice:secret"
        )
        reqs = parse_curls(raw)
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[0].method, "GET")
        self.assertEqual(reqs[0].query_params, [("limit", "10"), ("q", "hello world")])
        self.assertEqual(reqs[0].body_kind, "none")

        self.assertEqual(reqs[1].method, "POST")
        self.assertEqual(reqs[1].body_json, {"name": "new"})
        self.assertEqual(reqs[1].get_header("Content-Type"), "application/json")
        self.assertTrue((reqs[1].get_header("Authorization") or "").startswith("Basic "))

    def test_attached_short_flags_and_cookie_flag(self) -> None:
        raw = "curl -XPUT -H\"X-Mode: fast\" -b 'sid=s123; theme=dark' --oauth2-bearer 'oa_tok_77' --url=https://api.example.com:8443/cfg"
        req = parse_curl(raw)
        self.assertEqual(req.method, "PUT")
        self.assertEqual(req.port, 8443)
        self.assertEqual(req.base_url(), "https://api.example.com:8443")
        self.assertEqual(req.get_header("X-Mode"), "fast")
        self.assertEqual(req.get_header("Authorization"), "Bearer oa_tok_77")
        self.assertEqual(req.cookies, {"sid": "s123", "theme": "dark"})

    def test_cmd_caret_literal_inside_quotes(self) -> None:
        raw = 'curl ^"https://api.example.com/v1/echo^" --data-raw ^"^{^\\^"expr^\\^":^\\^"a^^b^\\^"^}^"'
        req = parse_curl(raw)
        self.assertEqual(req.body_json, {"expr": "a^b"})

    def test_invalid_curl_raises_parse_error(self) -> None:
        with self.assertRaises(ParseError):
            parse_curl("curl 'unterminated")
        with self.assertRaises(ParseError):
            parse_curl("curl -X GET")
        with self.assertRaises(ParseError):
            parse_curl("   ")


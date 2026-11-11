from __future__ import annotations

import base64
import json
import unittest

from reqforge.errors import ParseError
from reqforge.ingest import detect_input_format, parse_har


class TestHarIngest(unittest.TestCase):
    def test_har_with_bom_base64_and_pseudo_headers(self) -> None:
        resp_obj = {"user": {"id": 7, "handle": "tej"}}
        encoded = base64.b64encode(json.dumps(resp_obj).encode("utf-8")).decode("ascii")
        har = {
            "log": {
                "version": "1.2",
                "entries": [
                    {
                        "_resourceType": "fetch",
                        "request": {
                            "method": "GET",
                            "url": "https://api.example.com/users/7?include=profile",
                            "headers": [
                                {"name": ":authority", "value": "api.example.com"},
                                {"name": ":method", "value": "GET"},
                                {"name": "Authorization", "value": "Bearer tok_abc"},
                                {"name": "Cookie", "value": "sid=xyz; _ga=GA1"},
                            ],
                            "queryString": [{"name": "include", "value": "profile"}],
                            "cookies": [{"name": "prefs", "value": "dark"}],
                        },
                        "response": {
                            "status": 200,
                            "headers": [{"name": "Content-Type", "value": "application/json"}],
                            "content": {
                                "mimeType": "application/json",
                                "text": encoded,
                                "encoding": "base64",
                            },
                        },
                    }
                ],
            }
        }
        raw = "\ufeff" + json.dumps(har)
        self.assertEqual(detect_input_format(raw), "har")
        reqs = parse_har(raw)
        self.assertEqual(len(reqs), 1)
        req = reqs[0]
        self.assertIsNone(req.get_header(":authority"))
        self.assertEqual(req.query_params, [("include", "profile")])
        self.assertEqual(req.cookies, {"sid": "xyz", "_ga": "GA1", "prefs": "dark"})
        self.assertEqual(req.response_status, 200)
        self.assertEqual(req.response_json, resp_obj)

    def test_har_form_post_data(self) -> None:
        har = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "method": "POST",
                            "url": "https://api.example.com/auth/login",
                            "headers": [
                                {"name": "Content-Type", "value": "application/x-www-form-urlencoded"}
                            ],
                            "postData": {
                                "mimeType": "application/x-www-form-urlencoded",
                                "params": [
                                    {"name": "username", "value": "tej"},
                                    {"name": "remember", "value": "true"},
                                ],
                            },
                        },
                        "response": {"status": 200, "content": {"mimeType": "application/json", "text": "{\"ok\":true}"}},
                    }
                ]
            }
        }
        reqs = parse_har(json.dumps(har))
        self.assertEqual(reqs[0].body_kind, "form")
        self.assertEqual(reqs[0].body_form, [("username", "tej"), ("remember", "true")])

    def test_invalid_har_errors(self) -> None:
        with self.assertRaises(ParseError):
            parse_har("{not valid json")
        with self.assertRaises(ParseError):
            parse_har(json.dumps({"log": {"entries": []}}))

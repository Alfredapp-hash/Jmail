"""Gateway auth, MCP, and REST tests. The Alfred hub is never contacted."""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import grok_api
import jmail_lib as jl


class ApiTests(unittest.TestCase):
    def test_bearer_compare(self):
        self.assertTrue(grok_api.bearer_ok("Bearer secret", "secret"))
        self.assertTrue(grok_api.bearer_ok("token secret", "secret"))
        self.assertFalse(grok_api.bearer_ok("Bearer nope", "secret"))
        self.assertFalse(grok_api.bearer_ok("Bearer secre", "secret"))
        self.assertFalse(grok_api.bearer_ok("", "secret"))
        self.assertFalse(grok_api.bearer_ok("Bearer secret", ""))

    def test_mcp_initialize_and_unknown_tool(self):
        init = grok_api.mcp_handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "jmail")
        listed = grok_api.mcp_handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertIn("message_pcs", names)
        self.assertIn("jmail_im", names)
        note = grok_api.mcp_handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(note)
        bad = grok_api.mcp_handle({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        })
        self.assertTrue(bad["result"]["isError"])

    def test_mcp_send_is_forced_to_grok(self):
        seen = {}

        def fake_send(to, subject, body, **kwargs):
            seen["kwargs"] = kwargs
            seen["to"] = to
            return {"from": kwargs.get("sender"), "sent": [{"to": "operator", "id": "1"}], "truncated": False}

        original = jl.send_mail
        jl.send_mail = fake_send
        try:
            reply = grok_api.mcp_handle({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "message_pcs", "arguments": {"to": "operator", "subject": "hi", "body": "yo"}},
            })
        finally:
            jl.send_mail = original
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(seen["kwargs"]["sender"], "grok")
        payload = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(payload["from"], "grok")

    def test_rest_unknown_route(self):
        with self.assertRaises(jl.JmailError) as raised:
            grok_api.dispatch_rest("/v1/missing", {}, {})
        self.assertEqual(raised.exception.status, 404)


class HttpTests(unittest.TestCase):
    def setUp(self):
        grok_api.Handler.token = "test-token"
        self.httpd = grok_api.ThreadingHTTPServer(("127.0.0.1", 0), grok_api.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _req(self, method, path, body=None, token=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            parsed = json.loads(raw) if raw else {}
            return exc.code, parsed

    def test_health_is_public_and_send_requires_token(self):
        status, body = self._req("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["mailbox"], "grok")
        status, body = self._req("POST", "/v1/send", {"to": "operator", "subject": "a", "body": "b"})
        self.assertEqual(status, 401)

    def test_send_uses_gateway_identity(self):
        seen = {}

        def fake_send(to, subject, body, **kwargs):
            seen.update(to=to, subject=subject, body=body, sender=kwargs.get("sender"))
            return {"from": "grok", "sent": [{"to": "operator", "id": "9"}], "truncated": False}

        original = jl.send_mail
        jl.send_mail = fake_send
        try:
            status, body = self._req(
                "POST",
                "/v1/send",
                {"to": "operator", "subject": "a", "body": "b"},
                token="test-token",
            )
        finally:
            jl.send_mail = original
        self.assertEqual(status, 200)
        self.assertEqual(seen["sender"], "grok")
        self.assertEqual(body["sent"][0]["id"], "9")

    def test_bad_json_and_oversize(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/send", data=b"not-json", method="POST")
        req.add_header("Authorization", "Bearer test-token")
        req.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(raised.exception.code, 400)

        huge = b'{"a":"' + (b"x" * (grok_api.MAX_BODY + 10)) + b'"}'
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/send", data=huge, method="POST")
        req.add_header("Authorization", "Bearer test-token")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(raised.exception.code, 413)


if __name__ == "__main__":
    unittest.main()

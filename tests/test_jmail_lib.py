"""Unit tests for the Jmail bus client. No network."""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jmail_lib as jl


class _Resp:
    def __init__(self, payload, raw: bytes | None = None):
        self._raw = raw if raw is not None else json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class LibTests(unittest.TestCase):
    def test_aliases_and_ids(self):
        self.assertEqual(jl.resolve_targets("pcs"), ["claude-heavy", "claude-command"])
        self.assertEqual(jl.resolve_targets("Heavy"), ["claude-heavy"])
        self.assertEqual(jl.resolve_targets("brian"), ["operator"])
        self.assertEqual(jl.resolve_targets("jbot"), ["alfred"])
        self.assertEqual(jl.resolve_targets("sentinel"), ["sentinel"])

    def test_reject_bad_targets(self):
        with self.assertRaises(jl.JmailError):
            jl.resolve_targets("")
        with self.assertRaises(jl.JmailError):
            jl.resolve_targets("has space")
        with self.assertRaises(jl.JmailError):
            jl.resolve_targets("../etc")

    def test_clip_and_required_fields(self):
        text, cut = jl.clip("abc", 2)
        self.assertEqual((text, cut), ("ab", True))
        env = {"ALFRED_API_TOKEN": "t", "COMMAND_API_URL": "http://hub.test"}
        with self.assertRaises(jl.JmailError):
            jl.send_mail("operator", "", "body", env=env, opener=lambda *_a, **_k: None)
        with self.assertRaises(jl.JmailError):
            jl.send_im("operator", "   ", env=env, opener=lambda *_a, **_k: None)

    def test_identity_override_and_fallback(self):
        self.assertEqual(jl.identity("claude-heavy", {}), "claude-heavy")
        with self.assertRaises(jl.JmailError):
            jl.identity("Not Valid!", {})
        saved = {key: os.environ.pop(key, None) for key in ("JMAIL_AGENT_ID", "GROK_IM_ID")}
        try:
            self.assertEqual(jl.me({"GROK_IM_ID": "not valid id"}), "grok")
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_read_env_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('# c\nFOO="bar baz"\nBAZ=\'q\'\n\nnope\n', encoding="utf-8")
            env = jl.read_env(path)
        self.assertEqual(env, {"FOO": "bar baz", "BAZ": "q"})

    def test_request_posts_token_and_parses_json(self):
        seen = {}

        def opener(req, timeout=0):
            seen["url"] = req.full_url
            seen["token"] = req.get_header("X-alfred-token")
            seen["method"] = req.get_method()
            seen["body"] = json.loads(req.data.decode())
            return _Resp({"id": "m1"})

        env = {"ALFRED_API_TOKEN": "secret", "COMMAND_API_URL": "http://hub.test"}
        # Isolate from a developer machine that already exports the hub token.
        saved = os.environ.pop("ALFRED_API_TOKEN", None)
        saved_hub = {k: os.environ.pop(k, None) for k in ("ALFRED_HUB_URL", "ALFRED_URL", "COMMAND_API_URL")}
        try:
            result = jl.send_mail("heavy", "hello", "there", env=env, sender="grok", opener=opener)
        finally:
            if saved is not None:
                os.environ["ALFRED_API_TOKEN"] = saved
            for key, value in saved_hub.items():
                if value is not None:
                    os.environ[key] = value
        self.assertEqual(result["sent"], [{"to": "claude-heavy", "id": "m1"}])
        self.assertFalse(result["truncated"])
        self.assertEqual(seen["url"], "http://hub.test/api/im/mail/send")
        self.assertEqual(seen["token"], "secret")
        self.assertEqual(seen["body"]["from_agent"], "grok")
        self.assertEqual(seen["method"], "POST")

    def test_http_error_and_missing_token(self):
        def opener(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 401, "no", hdrs=None, fp=io.BytesIO(b"denied"))

        env = {"ALFRED_API_TOKEN": "t", "COMMAND_API_URL": "http://hub.test"}
        saved = os.environ.pop("ALFRED_API_TOKEN", None)
        try:
            with self.assertRaises(jl.JmailError) as raised:
                jl.get_directory(env=env, opener=opener)
            self.assertEqual(raised.exception.status, 502)
            with self.assertRaises(jl.JmailError) as missing:
                jl.request("GET", "/api/im/directory", env={}, opener=opener)
            self.assertEqual(missing.exception.status, 503)
        finally:
            if saved is not None:
                os.environ["ALFRED_API_TOKEN"] = saved

    def test_mark_read_requires_ids(self):
        with self.assertRaises(jl.JmailError):
            jl.mark_read(["", None], env={"ALFRED_API_TOKEN": "t"}, agent="grok")

    def test_inbox_rejects_bad_channel(self):
        with self.assertRaises(jl.JmailError):
            jl.get_inbox(channel="sms", env={"ALFRED_API_TOKEN": "t"}, agent="grok", opener=lambda *_a, **_k: None)


if __name__ == "__main__":
    unittest.main()

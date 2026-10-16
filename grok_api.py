"""Grok-facing HTTP and MCP gateway for in-house Jmail.

Grok.com custom connectors need a public HTTPS URL. This process is that
gateway: it always sends and reads as `grok`, never as operator or another
agent, and fans `to=pcs` out to Heavy and Command.

  python grok_api.py

REST (Bearer GROK_JMAIL_TOKEN):
  POST /v1/send       {to, subject, body}
  POST /v1/im         {to, body}
  POST /v1/read       {id} or {message_ids}
  GET  /v1/inbox
  GET  /v1/directory
  GET  /v1/health     (no auth)
  GET  /v1/openapi.json

MCP:
  POST /mcp           streamable HTTP JSON-RPC

The gateway token lives in data/grok-token.txt or GROK_JMAIL_TOKEN.
Give Grok that token, never ALFRED_API_TOKEN.
Binds 127.0.0.1 by default. Publish it with `tailscale funnel <port>`.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jmail_lib as jl  # noqa: E402

DATA_DIR = HERE / "data"
TOKEN_FILE = DATA_DIR / "grok-token.txt"
HOST = os.environ.get("GROK_JMAIL_HOST", "127.0.0.1")
PORT = int(os.environ.get("GROK_JMAIL_PORT", "9847"))
PROTOCOL = "2025-03-26"
MAX_BODY = 200_000
GATEWAY_AGENT = "grok"

_TO_ENUM = ["pcs", "heavy", "command", "operator"]

MCP_TOOLS = [
    {
        "name": "message_pcs",
        "description": (
            "Send in-house Jmail from grok to Brian's main PCs over the Alfred "
            "IM bus (not Gmail). to=pcs messages both Heavy and Command; "
            "heavy = claude-heavy; command = claude-command; operator = Brian."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "pcs | heavy | command | operator", "enum": _TO_ENUM},
                "subject": {"type": "string", "description": "Short subject"},
                "body": {"type": "string", "description": "Mail body"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "jmail_im",
        "description": "Send a short in-house IM from grok. Same recipients as message_pcs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "enum": _TO_ENUM},
                "body": {"type": "string"},
            },
            "required": ["to", "body"],
        },
    },
    {
        "name": "jmail_inbox",
        "description": "Read grok's in-house Jmail inbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "unread_only": {"type": "boolean"},
                "channel": {"type": "string", "enum": ["mail", "im"]},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "jmail_directory",
        "description": "List mailboxes on the in-house Jmail bus.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _restrict_token_file(path: Path) -> None:
    """Keep a newly created gateway token readable by this user only."""
    if os.name != "nt":
        path.chmod(0o600)
        return
    user = os.environ.get("USERNAME") or ""
    if not user:
        return
    import subprocess
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R)"],
        capture_output=True,
        check=False,
    )


def grok_token() -> str:
    tok = (os.environ.get("GROK_JMAIL_TOKEN") or "").strip()
    if tok:
        return tok
    env = jl.read_env()
    tok = (env.get("GROK_JMAIL_TOKEN") or "").strip()
    if tok:
        return tok
    if TOKEN_FILE.is_file():
        existing = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(tok + "\n", encoding="utf-8")
    _restrict_token_file(TOKEN_FILE)
    return tok


def bearer_ok(header: str, expected: str) -> bool:
    raw = (header or "").strip()
    got = ""
    lowered = raw.lower()
    if lowered.startswith("bearer "):
        got = raw[7:].strip()
    elif lowered.startswith("token "):
        got = raw[6:].strip()
    if not got or not expected:
        return False
    return hmac.compare_digest(got, expected)


def _args(params: dict[str, Any]) -> dict[str, Any]:
    raw = params.get("arguments") or {}
    return raw if isinstance(raw, dict) else {}


def openapi_spec(base: str = "") -> dict[str, Any]:
    bearer = [{"bearerAuth": []}]
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Grok Jmail",
            "version": jl.VERSION,
            "description": "In-house Jmail for Grok. Sends as grok. to=pcs hits both main PCs.",
        },
        "servers": [{"url": base or f"http://127.0.0.1:{PORT}"}],
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
        },
        "paths": {
            "/v1/health": {
                "get": {"operationId": "health", "summary": "Liveness, no auth"},
            },
            "/v1/send": {
                "post": {
                    "operationId": "sendJmail",
                    "summary": "Send Jmail as grok",
                    "security": bearer,
                    "requestBody": _json_body(["to", "subject", "body"], {
                        "to": {"type": "string", "enum": _TO_ENUM},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    }),
                }
            },
            "/v1/im": {
                "post": {
                    "operationId": "sendIm",
                    "summary": "Send an IM as grok",
                    "security": bearer,
                    "requestBody": _json_body(["to", "body"], {
                        "to": {"type": "string", "enum": _TO_ENUM},
                        "body": {"type": "string"},
                    }),
                }
            },
            "/v1/read": {
                "post": {
                    "operationId": "markRead",
                    "summary": "Mark grok's messages read",
                    "security": bearer,
                    "requestBody": _json_body([], {
                        "id": {"type": "string"},
                        "message_ids": {"type": "array", "items": {"type": "string"}},
                    }),
                }
            },
            "/v1/inbox": {
                "get": {"operationId": "inbox", "summary": "Read grok's inbox", "security": bearer},
            },
            "/v1/directory": {
                "get": {"operationId": "directory", "summary": "List mailboxes", "security": bearer},
            },
        },
    }


def _json_body(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {"required": True, "content": {"application/json": {"schema": schema}}}


def mcp_handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC MCP message. Returns None for notifications."""
    method = str(msg.get("method") or "")
    rpc_id = msg.get("id")
    if rpc_id is None and method.startswith("notifications/"):
        return None

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    if method == "initialize":
        client_proto = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        proto = client_proto if isinstance(client_proto, str) else PROTOCOL
        return ok({
            "protocolVersion": proto,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "jmail", "version": jl.VERSION},
            "instructions": (
                "You are grok on Brian's in-house Jmail. Mailbox id is grok. "
                "Use message_pcs with to=pcs to reach both main PCs "
                "(Heavy and Command). This is not Gmail."
            ),
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": MCP_TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = str(params.get("name") or "")
        args = _args(params if isinstance(params, dict) else {})
        try:
            if name == "message_pcs":
                result = jl.send_mail(
                    args.get("to"),
                    args.get("subject") or "",
                    args.get("body") or "",
                    sender=GATEWAY_AGENT,
                )
            elif name == "jmail_im":
                result = jl.send_im(args.get("to"), args.get("body") or "", sender=GATEWAY_AGENT)
            elif name == "jmail_inbox":
                result = jl.get_inbox(
                    unread_only=bool(args.get("unread_only")),
                    channel=str(args.get("channel") or ""),
                    limit=int(args.get("limit") or 20),
                    agent=GATEWAY_AGENT,
                )
            elif name == "jmail_directory":
                result = jl.get_directory()
            else:
                raise jl.JmailError(f"unknown tool {name}")
            text = json.dumps(result)
            return ok({"content": [{"type": "text", "text": text}], "isError": False})
        except jl.JmailError as exc:
            return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except (TypeError, ValueError) as exc:
            return ok({"content": [{"type": "text", "text": f"bad arguments: {exc}"}], "isError": True})
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": f"method not found: {method}"}}


def dispatch_rest(path: str, payload: dict[str, Any], query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    """Authenticated REST routes. Raises JmailError for hub and validation failures."""
    if path == "/v1/send":
        return 200, jl.send_mail(payload.get("to"), payload.get("subject") or "", payload.get("body") or "", sender=GATEWAY_AGENT)
    if path == "/v1/im":
        return 200, jl.send_im(payload.get("to"), payload.get("body") or "", sender=GATEWAY_AGENT)
    if path == "/v1/read":
        raw_ids = payload.get("message_ids")
        if raw_ids is None and payload.get("id"):
            raw_ids = [payload.get("id")]
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        if not isinstance(raw_ids, list):
            raise jl.JmailError("message_ids must be a list, or pass id")
        return 200, jl.mark_read(raw_ids, agent=GATEWAY_AGENT)
    if path == "/v1/inbox":
        limit_raw = (query.get("limit") or ["20"])[0]
        return 200, jl.get_inbox(
            unread_only=str((query.get("unread_only") or ["false"])[0]).lower() in ("1", "true", "yes"),
            channel=(query.get("channel") or [""])[0],
            limit=int(limit_raw),
            agent=GATEWAY_AGENT,
        )
    if path == "/v1/directory":
        return 200, jl.get_directory()
    raise jl.JmailError("not found", 404)


class Handler(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("grok-jmail: " + (fmt % args) + "\n")

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Mcp-Session-Id, X-Grok-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(
        self,
        status: int,
        body: Any,
        *,
        content_type: str = "application/json",
        extra: dict[str, str] | None = None,
    ) -> None:
        raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self._cors()
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization") or ""
        extra = self.headers.get("X-Grok-Token") or ""
        if bearer_ok(header, self.token) or bearer_ok("Bearer " + extra, self.token):
            return True
        self._send(401, {"error": "unauthorized — use Authorization: Bearer <GROK_JMAIL_TOKEN>"})
        return False

    def _read_json(self) -> Any | None:
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            return {}
        try:
            length = int(raw_len)
        except ValueError:
            self._send(400, {"error": "bad Content-Length"})
            return None
        if length < 0 or length > MAX_BODY:
            self._send(413, {"error": "payload too large"})
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            self._send(400, {"error": "invalid JSON"})
            return None

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/", "/v1/health"):
            self._send(200, {
                "ok": True,
                "service": "grok-jmail",
                "version": jl.VERSION,
                "mailbox": GATEWAY_AGENT,
                "pcs": ["claude-heavy", "claude-command"],
                "mcp": "/mcp",
            })
            return
        if path == "/v1/openapi.json":
            host = self.headers.get("Host") or f"127.0.0.1:{PORT}"
            scheme = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
            self._send(200, openapi_spec(f"{scheme}://{host}"))
            return
        if not self._authorized():
            return
        query = parse_qs(urlparse(self.path).query)
        try:
            status, body = dispatch_rest(path, {}, query)
        except jl.JmailError as exc:
            self._send(exc.status, {"error": str(exc)})
            return
        except (TypeError, ValueError):
            self._send(400, {"error": "bad query"})
            return
        self._send(status, body)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        payload = self._read_json()
        if payload is None:
            return
        if path == "/mcp" and isinstance(payload, list):
            if not self._authorized():
                return
            out = [mcp_handle(item) for item in payload if isinstance(item, dict)]
            self._send(200, [item for item in out if item is not None])
            return
        if not isinstance(payload, dict):
            self._send(400, {"error": "JSON object required"})
            return
        if path == "/mcp":
            if not self._authorized():
                return
            reply = mcp_handle(payload)
            session = self.headers.get("Mcp-Session-Id") or str(uuid.uuid4())
            if reply is None:
                self.send_response(202)
                self._cors()
                self.send_header("Mcp-Session-Id", session)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send(200, reply, extra={"Mcp-Session-Id": session})
            return
        if not self._authorized():
            return
        try:
            status, body = dispatch_rest(path, payload, {})
        except jl.JmailError as exc:
            self._send(exc.status, {"error": str(exc)})
            return
        self._send(status, body)


def serve(host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    Handler.token = grok_token()
    return ThreadingHTTPServer((host, port), Handler)


def main() -> int:
    try:
        port = int(os.environ.get("GROK_JMAIL_PORT", str(PORT)))
    except ValueError:
        print("GROK_JMAIL_PORT must be an integer", file=sys.stderr)
        return 2
    host = os.environ.get("GROK_JMAIL_HOST", HOST)
    httpd = serve(host, port)
    print(f"Grok Jmail API {jl.VERSION}  http://{host}:{port}")
    print("  REST send     POST /v1/send   {to, subject, body}")
    print("  REST im       POST /v1/im     {to, body}")
    print("  MCP           POST /mcp")
    print(f"  token file    {TOKEN_FILE}")
    print("Paste the public MCP URL and Bearer token into grok.com/connectors (Custom).")
    print(f"Publish localhost with:  tailscale funnel {port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

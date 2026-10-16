"""Shared in-house Jmail bus client.

Stdlib only. Used by the CLI, the Grok HTTP/MCP gateway, and the node watcher.
This talks to Alfred's IM bus. It is not Gmail and not the web mail UI.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

VERSION = "1.1.0"

ALFRED_ROOT = Path(os.environ.get("ALFRED_ROOT", r"C:\AI\Alfred"))
DEFAULT_HUB = "http://100.72.22.124:8000"
ME_DEFAULT = "grok"
_AGENT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
BODY_CAP = 20_000
SUBJECT_CAP = 200
REQUEST_TIMEOUT = 20

# Friendly names the CLI and gateway accept -> real mailbox ids on the bus.
ALIASES: dict[str, tuple[str, ...]] = {
    "pcs": ("claude-heavy", "claude-command"),
    "mains": ("claude-heavy", "claude-command"),
    "main": ("claude-heavy", "claude-command"),
    "both": ("claude-heavy", "claude-command"),
    "heavy": ("claude-heavy",),
    "command": ("claude-command",),
    "brian": ("operator",),
    "jbot": ("alfred",),
}


class JmailError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def read_env(path: Path | None = None) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file. Missing file yields {}."""
    env: dict[str, str] = {}
    p = path or (ALFRED_ROOT / ".env")
    if not p.is_file():
        return env
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _pick(*values: str | None, default: str = "") -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def me(env: dict[str, str] | None = None) -> str:
    e = read_env() if env is None else env
    ident = _pick(
        os.environ.get("JMAIL_AGENT_ID"),
        os.environ.get("GROK_IM_ID"),
        e.get("GROK_IM_ID"),
        default=ME_DEFAULT,
    )
    return ident if _AGENT_RE.match(ident) else ME_DEFAULT


def identity(explicit: str | None, env: dict[str, str] | None = None) -> str:
    """Resolve the sending mailbox. An explicit id is checked, never guessed."""
    if explicit is not None and str(explicit).strip():
        ident = str(explicit).strip().lower()
        if not _AGENT_RE.match(ident):
            raise JmailError("sender id is invalid")
        return ident
    return me(env)


def hub(env: dict[str, str] | None = None) -> str:
    e = read_env() if env is None else env
    return _pick(
        os.environ.get("ALFRED_HUB_URL"),
        os.environ.get("ALFRED_URL"),
        os.environ.get("COMMAND_API_URL"),
        e.get("ALFRED_HUB_URL"),
        e.get("ALFRED_URL"),
        e.get("COMMAND_API_URL"),
        default=DEFAULT_HUB,
    ).rstrip("/")


def alfred_token(env: dict[str, str] | None = None) -> str:
    e = read_env() if env is None else env
    return _pick(os.environ.get("ALFRED_API_TOKEN"), e.get("ALFRED_API_TOKEN"))


def clean_agent(raw: Any) -> str:
    return str(raw or "").strip().lower().replace("@", "").split(".", 1)[0]


def resolve_targets(raw: Any) -> list[str]:
    """Map a friendly `to` onto one or more bus ids. `pcs` fans out to both main PCs."""
    key = clean_agent(raw)
    if not key:
        raise JmailError("'to' is required (pcs, heavy, command, or operator)")
    if key in ALIASES:
        return list(ALIASES[key])
    if _AGENT_RE.match(key):
        return [key]
    raise JmailError(
        "'to' must be pcs, heavy, command, operator, or a mailbox id "
        "(claude-heavy, claude-command, alfred, …)"
    )


def clip(text: str, cap: int) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    return text[:cap], True


def request(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    opener: Any = None,
    timeout: float = REQUEST_TIMEOUT,
) -> dict[str, Any]:
    token = alfred_token(env)
    if not token:
        raise JmailError("no ALFRED_API_TOKEN — cannot reach the Jmail hub", 503)
    url = hub(env) + path
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Alfred-Token": token,
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")[:240].replace("\n", " ")
        raise JmailError(f"hub HTTP {exc.code} {err}".strip(), 502) from exc
    except json.JSONDecodeError as exc:
        raise JmailError("hub returned non-JSON", 502) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise JmailError(f"hub unreachable: {exc}", 503) from exc
    if not isinstance(parsed, dict):
        raise JmailError("hub returned an unexpected payload", 502)
    return parsed


def _message_id(data: dict[str, Any] | None) -> str:
    if not data:
        return ""
    return str(data.get("id") or data.get("message_id") or "")


def send_mail(
    to: Any,
    subject: str,
    body: str,
    *,
    env: dict[str, str] | None = None,
    sender: str | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    subject_text = str(subject or "").strip()
    body_text = str(body or "").strip()
    if not subject_text:
        raise JmailError("subject is required")
    if not body_text:
        raise JmailError("body is required")
    who = identity(sender, env)
    subject_out, subject_cut = clip(subject_text, SUBJECT_CAP)
    body_out, body_cut = clip(body_text, BODY_CAP)
    sent: list[dict[str, Any]] = []
    for dest in resolve_targets(to):
        data = request(
            "POST",
            "/api/im/mail/send",
            body={
                "from_agent": who,
                "to_agent": dest,
                "subject": subject_out,
                "body": body_out,
            },
            env=env,
            opener=opener,
        )
        sent.append({"to": dest, "id": _message_id(data)})
    return {
        "from": who,
        "subject": subject_out,
        "sent": sent,
        "truncated": subject_cut or body_cut,
    }


def send_im(
    to: Any,
    body: str,
    *,
    env: dict[str, str] | None = None,
    sender: str | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    body_text = str(body or "").strip()
    if not body_text:
        raise JmailError("body is required")
    who = identity(sender, env)
    body_out, body_cut = clip(body_text, BODY_CAP)
    sent: list[dict[str, Any]] = []
    for dest in resolve_targets(to):
        data = request(
            "POST",
            "/api/im/send",
            body={
                "from_agent": who,
                "to_agent": dest,
                "body": body_out,
                "channel": "im",
            },
            env=env,
            opener=opener,
        )
        sent.append({"to": dest, "id": _message_id(data)})
    return {"from": who, "sent": sent, "truncated": body_cut}


def get_inbox(
    *,
    unread_only: bool = False,
    channel: str = "",
    limit: int = 20,
    env: dict[str, str] | None = None,
    agent: str | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    who = identity(agent, env)
    try:
        capped = max(1, min(int(limit or 20), 50))
    except (TypeError, ValueError) as exc:
        raise JmailError("limit must be an integer") from exc
    query: dict[str, Any] = {
        "agent": who,
        "unread_only": str(bool(unread_only)).lower(),
        "limit": str(capped),
    }
    ch = str(channel or "").strip().lower()
    if ch:
        if ch not in ("im", "mail"):
            raise JmailError("channel must be 'im' or 'mail'")
        query["channel"] = ch
    data = request("GET", "/api/im/inbox", query=query, env=env, opener=opener)
    messages = data.get("messages") or []
    if not isinstance(messages, list):
        raise JmailError("hub inbox payload is invalid", 502)
    return {"agent": who, "count": len(messages), "messages": messages}


def get_directory(
    env: dict[str, str] | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    data = request("GET", "/api/im/directory", env=env, opener=opener)
    contacts = data.get("contacts") or []
    if not isinstance(contacts, list):
        raise JmailError("hub directory payload is invalid", 502)
    return {"contacts": contacts}


def mark_read(
    message_ids: list[Any],
    *,
    env: dict[str, str] | None = None,
    agent: str | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    ids = [str(i).strip() for i in message_ids if str(i or "").strip()]
    if not ids:
        raise JmailError("message id is required")
    who = identity(agent, env)
    request(
        "POST",
        "/api/im/read",
        body={"agent": who, "message_ids": ids},
        env=env,
        opener=opener,
    )
    return {"agent": who, "marked": len(ids), "message_ids": ids}

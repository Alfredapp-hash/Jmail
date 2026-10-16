"""CLI for the in-house Jmail bus (Alfred IM, not Gmail).

Stdlib only. Reads Alfred's .env for the hub URL and token, same as the watcher.

  python jmail.py doctor
  python jmail.py directory
  python jmail.py inbox [--unread] [--channel mail|im] [--json]
  python jmail.py send --to pcs --subject "hello" --body "..."
  python jmail.py im --to heavy --body "short ping"
  python jmail.py read --id <message-id>

Mailbox defaults to grok. `pcs` fans out to Heavy and Command.
Override the mailbox with JMAIL_AGENT_ID. Pass --body - to read stdin.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jmail_lib as jl


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")


def _body_arg(raw: str) -> str:
    if raw == "-":
        return sys.stdin.read()
    return raw


def cmd_doctor(args: argparse.Namespace) -> int:
    token_state = "set" if jl.alfred_token() else "missing"
    report = {
        "version": jl.VERSION,
        "agent": jl.me(),
        "hub": jl.hub(),
        "token": token_state,
    }
    try:
        directory = jl.get_directory()
        report["hub_ok"] = True
        report["contacts"] = len(directory.get("contacts") or [])
    except jl.JmailError as exc:
        report["hub_ok"] = False
        report["error"] = str(exc)
        if args.json:
            _emit(report, True)
        else:
            print(f"agent={report['agent']}")
            print(f"hub={report['hub']}")
            print(f"token={token_state}")
            print(f"hub_ok=false ({exc})")
        return 2 if exc.status >= 500 else 1
    if args.json:
        _emit(report, True)
    else:
        print(f"agent={report['agent']}")
        print(f"hub={report['hub']}")
        print(f"token={token_state}")
        print(f"hub_ok=true contacts={report['contacts']}")
    return 0


def cmd_directory(args: argparse.Namespace) -> int:
    data = jl.get_directory()
    if args.json:
        _emit(data, True)
        return 0
    contacts = data.get("contacts") or []
    if not contacts:
        print("directory: empty")
        return 0
    for contact in contacts:
        unread = (contact.get("unread") or {}).get("total") or 0
        mark = f"  unread={unread}" if unread else ""
        cid = str(contact.get("id") or "")
        print(f"{cid:22} {str(contact.get('name') or ''):28} {str(contact.get('kind') or ''):8} {contact.get('presence') or ''}{mark}")
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    data = jl.get_inbox(unread_only=bool(args.unread), channel=args.channel or "", limit=args.limit)
    if args.json:
        _emit(data, True)
        return 0
    messages = data.get("messages") or []
    if not messages:
        print(f"inbox for {data.get('agent')}: empty")
        return 0
    for message in messages:
        print(
            f"[{message.get('id')}] {message.get('from_agent')} -> {message.get('to_agent')} "
            f"({message.get('channel')}) {message.get('created_at') or ''}"
        )
        if message.get("subject"):
            print(f"  Subject: {message.get('subject')}")
        body = str(message.get("body") or "").strip()
        if body:
            print("  " + body.replace("\n", "\n  "))
        print()
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    data = jl.send_mail(args.to, args.subject, _body_arg(args.body))
    if args.json:
        _emit(data, True)
        return 0
    for row in data.get("sent") or []:
        mid = row.get("id") or ""
        line = f"sent mail {data.get('from')} -> {row.get('to')}"
        if mid:
            line += f" id={mid}"
        print(line)
    if data.get("truncated"):
        print("note: subject or body was trimmed to the bus limit", file=sys.stderr)
    return 0


def cmd_im(args: argparse.Namespace) -> int:
    data = jl.send_im(args.to, _body_arg(args.body))
    if args.json:
        _emit(data, True)
        return 0
    for row in data.get("sent") or []:
        mid = row.get("id") or ""
        line = f"sent im {data.get('from')} -> {row.get('to')}"
        if mid:
            line += f" id={mid}"
        print(line)
    if data.get("truncated"):
        print("note: body was trimmed to the bus limit", file=sys.stderr)
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    ids = [part.strip() for part in args.id.replace(",", " ").split() if part.strip()]
    data = jl.mark_read(ids)
    if args.json:
        _emit(data, True)
    else:
        print(f"marked {data['marked']} read for {data['agent']}")
    return 0


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print the hub payload as JSON")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"In-house Jmail CLI (v{jl.VERSION}). Not Gmail.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    doctor = sub.add_parser("doctor", help="show mailbox, hub, and whether the bus answers")
    _add_json(doctor)

    directory = sub.add_parser("directory", help="list mailbox ids")
    _add_json(directory)

    inbox = sub.add_parser("inbox", help="read this agent's inbox")
    _add_json(inbox)
    inbox.add_argument("--unread", action="store_true")
    inbox.add_argument("--channel", choices=["mail", "im"])
    inbox.add_argument("--limit", type=int, default=20)

    send = sub.add_parser("send", help="send mail (to=pcs reaches both main PCs)")
    _add_json(send)
    send.add_argument("--to", required=True)
    send.add_argument("--subject", required=True)
    send.add_argument("--body", required=True, help="message text, or - to read stdin")

    im = sub.add_parser("im", help="send a short IM ping")
    _add_json(im)
    im.add_argument("--to", required=True)
    im.add_argument("--body", required=True, help="message text, or - to read stdin")

    read = sub.add_parser("read", help="mark message(s) read")
    _add_json(read)
    read.add_argument("--id", required=True, help="one id, or several separated by comma or space")

    args = parser.parse_args(argv)
    commands = {
        "doctor": cmd_doctor,
        "directory": cmd_directory,
        "inbox": cmd_inbox,
        "send": cmd_send,
        "im": cmd_im,
        "read": cmd_read,
    }
    try:
        return commands[args.cmd](args)
    except jl.JmailError as exc:
        print(f"jmail: {exc}", file=sys.stderr)
        return 2 if exc.status >= 500 else 1


if __name__ == "__main__":
    raise SystemExit(main())

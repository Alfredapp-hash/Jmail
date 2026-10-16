# Jmail

In-house mail for Alfred's agents. Command, Heavy, Grok, JBot, and the operator share one bus (`/api/im`). This repo is the client, the Grok gateway, and the node watcher. The bus itself lives in Alfred.

Formerly Agent-Mail. The hub paths are unchanged.

This is not Gmail, and it is not the web mail UI.

## What each piece does

| Piece | File | Role |
|---|---|---|
| Client | `jmail_lib.py` | Hub calls, aliases, size limits |
| CLI | `jmail.py` | Read and send as the local mailbox (default `grok`) |
| Gateway | `grok_api.py` | REST + MCP for grok.com. Always mailbox `grok` |
| Watcher | `claude_mail_watch.py` | Operator `BUILD:` / `TASK:` jobs, plus read-only triage of other agents |

## Addresses

| id | who | aliases |
|---|---|---|
| `operator` | Brian | `brian` |
| `claude-heavy` | Heavy | `heavy` |
| `claude-command` | Command | `command` |
| `grok` | Grok | |
| `alfred` | JBot | `jbot` |
| `claude` | Claude (generic) | |
| `sentinel` | Sentinel | |

`pcs`, `mains`, `main`, and `both` fan out to Heavy and Command.

## Security model

- The gateway sends and reads only as `grok`. It cannot impersonate `operator`.
- Its bearer token is `GROK_JMAIL_TOKEN` (or `data/grok-token.txt`). That is not the Alfred hub token.
- Only mail from `operator` whose subject starts with `BUILD:` or `TASK:` can start work on a node.
- Mail from any other agent is summarized for the operator. The body is treated as data. The triage session does not get permission to execute it.
- One watcher action runs at a time. A build job is marked read before it starts, so a crash cannot run it again. Triage mail stays unread and is deduped in `data/triaged.json`.
- `data/`, `.env`, and token files are gitignored. Do not commit them.

## Config

The client reads process environment first, then `C:\AI\Alfred\.env` (`ALFRED_ROOT` overrides that directory).

| Variable | Used by | Meaning |
|---|---|---|
| `ALFRED_API_TOKEN` | all | Hub token. Required. |
| `ALFRED_HUB_URL`, `ALFRED_URL`, `COMMAND_API_URL` | all | Hub base URL |
| `JMAIL_AGENT_ID` | CLI | Mailbox. Default `grok` |
| `CLAUDE_IM_ID` | watcher | Mailbox to poll. Default `claude-heavy` |
| `CLAUDE_CLI` | watcher | Full path to the `claude` launcher |
| `CLAUDE_WATCH_ROOT` | watcher | Build folders. Default `C:\AI\builds` |
| `CLAUDE_WATCH_TIMEOUT_MIN` | watcher | Job wall clock. Default 90 |
| `CLAUDE_WATCH_PERMISSION_MODE` | watcher | Default `bypassPermissions` (operator-gated) |
| `CLAUDE_TRIAGE_PERMISSION_MODE` | watcher | Default `plan` |
| `GROK_JMAIL_TOKEN` | gateway | Bearer token for grok.com |
| `GROK_JMAIL_HOST` | gateway | Default `127.0.0.1` |
| `GROK_JMAIL_PORT` | gateway | Default `9847` |

A build body may start with `repo: C:\path\to\repo` to pin the working directory. Otherwise the watcher creates `CLAUDE_WATCH_ROOT\<slug>`.

## CLI

```powershell
python C:\AI\Jmail\jmail.py doctor
python C:\AI\Jmail\jmail.py directory
python C:\AI\Jmail\jmail.py inbox --unread
python C:\AI\Jmail\jmail.py send --to operator --subject "hello" --body "from grok"
python C:\AI\Jmail\jmail.py im --to heavy --body "ping"
python C:\AI\Jmail\jmail.py read --id <message-id>
```

`--json` prints the payload. `--body -` reads stdin. Exit code 1 is a rejected request. Exit code 2 means the hub is down or refused the call.

## Grok gateway

```powershell
python C:\AI\Jmail\grok_api.py
powershell -ExecutionPolicy Bypass -File C:\AI\Jmail\Install-GrokJmailApi.ps1
```

The installer registers `GrokJmailApi` at logon, restarts it if it dies, and passes the port through.

- `GET /v1/health` is public.
- `POST /v1/send` `{to, subject, body}` with `Authorization: Bearer <token>`
- `POST /v1/im` `{to, body}`
- `POST /v1/read` `{id}` or `{message_ids}`
- `GET /v1/inbox` and `GET /v1/directory`
- `POST /mcp` for a grok.com custom connector

MCP tools: `message_pcs`, `jmail_im`, `jmail_inbox`, `jmail_directory`.

Grok.com needs public HTTPS. The process listens on localhost:

```powershell
tailscale funnel 9847
```

The connector URL is the funnel URL plus `/mcp`.

## Watcher

```powershell
powershell -ExecutionPolicy Bypass -File C:\AI\Jmail\Install-ClaudeMailWatch.ps1
```

Task name: `JmailWatch`, every 2 minutes, on Heavy. The repetition does not expire after a day.

Subject contract, from `operator`:

```text
BUILD: short-title
TASK: short-title
```

The body is the instruction. Peer agents cannot start either one.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Stdlib only. Python 3.10+. The tests do not call the hub or start Claude.

## Layout

```text
jmail.py                      CLI
jmail_lib.py                  shared hub client
grok_api.py                   REST + MCP gateway
claude_mail_watch.py          scheduled poller
Install-ClaudeMailWatch.ps1
Install-GrokJmailApi.ps1
tests/
```

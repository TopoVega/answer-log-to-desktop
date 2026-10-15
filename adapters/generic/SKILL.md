---
name: agent-workbench-generic
description: |
  Vendor-neutral adapter for the local Agent Workbench board. Use when the user
  says 接入工作台 / 归档到工作台 / "log this turn", asks who else is working on this
  machine, wants a per-platform handoff document, or says another agent (any
  tool) is involved in the same task. Also use before starting non-trivial work
  so you do not redo what a peer session already did. This version works on any
  platform — you declare your own identity in step 0 instead of copying a
  hardcoded one.
metadata:
  category: productivity
  requires:
    bins: []
---

# Agent Workbench — generic adapter

A shared board for heterogeneous agent platforms on this machine. One **turn**
(one user message + your reply) is the unit of record; turns become a
per-platform timeline and a per-platform handoff dossier.

Use this file when there is no platform-specific adapter for you. The
`workbuddy` and `qoder` adapters are the same contract with their paths filled
in — if you are one of those, use theirs.

**Where to install it**: as `agent-workbench-generic/SKILL.md` inside your own
tool's skill directory. `~/.agents/skills/` is a convention several tools share
and is the default the workbench suggests, but if your platform keeps its skills
elsewhere, use that — nothing in this file depends on where it sits.

## 0. Identity — yours to declare, not to copy

Two fields. Both appear on the board exactly as you write them.

| Field | Rule | Example |
|---|---|---|
| `platform` | The product you are running **inside**. One lowercase token, no spaces. Every session of the same tool shares it: it is the column on the board and the filename of your dossier. | `claude-code`, `cursor`, `codex`, `trae` |
| `agent_id` | `"<platform>-<role>"`, unique per concurrent session. It is the row under that column, and what per-agent resets act on. | `cursor-review`, `codex-backend-1` |

Do **not** reuse an example value from another tool's adapter. Two sessions that
share an `agent_id` are indistinguishable on the board and their timelines get
merged — that is the one failure mode this adapter exists to prevent.

If you genuinely do not know which platform you are, use `platform: "unknown"`
and say so to the user once; do not guess a competitor's name.

## 1. Where the board lives on this machine

| What | Windows | macOS | Linux |
|---|---|---|---|
| port + token | `%LOCALAPPDATA%\AgentWorkbench\` | `~/Library/Application Support/AgentWorkbench/` | `~/.local/share/agentworkbench/` |
| shared root | `%USERPROFILE%\agent-workbench\` | `~/agent-workbench` | `~/agent-workbench` |

Below, `DATA` is the first row and `SHARED` the second. Environment overrides:
`AGENT_WORKBENCH_DATA`, `AGENT_WORKBENCH_HOME`.

The app writes `DATA/port.json` while it runs and deletes it on clean shutdown,
so **`port.json` existing is how you know the board is live**. The port may not
be 8734: if 8734 was taken the app fell back to an ephemeral one. Read the file,
never assume the number.

Three ways in, in order of preference. Use the first one available to you.

## 2. Channel A — MCP (best: identity binds once)

Two shapes, whichever your platform's connector config accepts:

| Shape | Config | Requires |
|---|---|---|
| stdio | `{"command": "<workbench>\\AgentWorkbench-mcp.exe", "args": ["mcp"]}` | the platform can spawn a process on this machine |
| streamable HTTP | `{"type": "streamableHttp", "url": "http://127.0.0.1:<port>/mcp?platform=<yours>&agent_id=<yours>", "headers": {"Authorization": "Bearer <token>"}}` | only network access to this machine |

Most of the MCP ecosystem is URL-based, so prefer the second when in doubt — it
also contains no machine-specific path. `platform` and `agent_id` on that URL
bind your identity at `initialize`, so `wb_register` becomes optional (call it
anyway to declare capabilities or a note). The token is in `DATA/token.json`;
`X-Workbench-Token` works as an alternative header.

Once connected, prefer these tools over shelling out:

| Tool | When |
|---|---|
| `wb_register` | Once, first thing — `agent_id`, `platform`, `display_name` |
| `wb_post_log` | After **every** completed turn (see §6) |
| `wb_supersede` | When something you already posted turned out wrong |
| `wb_read_board` | Your own platform's recent turns + the roster |
| `wb_get_dossier` | Your own platform's handoff document |
| `wb_list_dossiers` | Which dossier files exist and how fresh they are |
| `wb_find_agents` | Which other platforms exist — names only |

## 3. Channel B — HTTP (any platform that can run one command)

Write the body as a **UTF-8 file** with your file-writing tool, then post it:

```json
{ "agent_id": "<yours>", "platform": "<yours>", "question": "<用户原话，逐字>",
  "actions": "- 读了 `x.py`\n- 改了 `y.md`", "answer": "<回复全文>", "status": "done" }
```

```bash
PORT=$(grep -o '"port"[^,}]*' "$DATA/port.json" | tr -dc '0-9')
TOKEN=$(grep -o '"token"[^,}]*' "$DATA/token.json" | sed 's/.*"token" *: *"\([^"]*\)".*/\1/')

curl -s -X POST "http://127.0.0.1:$PORT/api/turn-log" \
  -H "Content-Type: application/json" \
  -H "X-Workbench-Token: $TOKEN" \
  --data-binary @turn.json
```

Do **not** inline the JSON in `-d '...'`. On Windows shells the argument leaves in
the local code page (GBK here), so any Chinese arrives as
`body 不是 UTF-8 JSON: ...`, and a `--data-binary @file` written in UTF-8 is the
fix. PowerShell is wide-argv, so it is safe inline:

```powershell
$port = (Get-Content "$env:LOCALAPPDATA\AgentWorkbench\port.json" | ConvertFrom-Json).port
$token = (Get-Content "$env:LOCALAPPDATA\AgentWorkbench\token.json" | ConvertFrom-Json).token
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/turn-log" `
  -Headers @{ "X-Workbench-Token" = $token } -ContentType "application/json; charset=utf-8" `
  -Body (@{ agent_id = "<yours>"; platform = "<yours>"; question = "<用户原话>"; answer = "<回复全文>" } | ConvertTo-Json)
```

A missing token is a `401`, not a silent no-op. `"ok": true` in the response
means it is on the board; it also returns `path`, the file it wrote.

## 4. Channel C — file drop (lowest common denominator)

Write into `SHARED/inbox/`. The running app picks it up within ~3 seconds and
moves it to `inbox/_processed/`; if the app is not running, it ingests the pile
on next start. Prefer `.json` — it carries structure exactly and never relies on
heading text being recognised:

```json
{ "agent_id": "<yours>", "platform": "<yours>", "question": "<用户原话>",
  "actions": "- 读了 `x.py`", "answer": "<回复全文>", "next_steps": "- 补测试",
  "status": "done" }
```

Markdown works too. Use those three heading names verbatim, or the parse falls
back to positional order and then to "the whole document is the answer":

```markdown
# <用户原话>

## 一、本次做了什么
- 读了 `x.py` —— 目的：…
- 改了 `y.md` —— 改动：…

## 二、回答内容
<本轮给用户的回复原文>

## 三、后续待办
- …

<!-- agent-workbench:entry:v1 {"agent_id":"<yours>","platform":"<yours>"} -->
```

The trailing comment is what fixes your identity; without it a drop lands as
`agent_id: file-drop`, which is a row nobody owns.

## 5. Python shortcut (source checkouts only)

If a workbench checkout happens to be on this machine, `scripts/post_turn_log.py`
does B and C for you and prints which one it used. It is **not** bundled with the
packaged app, so do not go hunting for it — the two commands above are enough.

Never invoke it as `python …` on Windows: `python` / `python3` can resolve to the
broken app-execution alias, which exits 49 with **zero output**, so a failed
archive looks exactly like a successful one. Use `py`, or the packaged exe, or an
absolute `python.exe`.

## 6. The archive test is about your answer, not their question

Archive unless **both** of these hold:

- your reply was empty, or a single bare acknowledgment with no information in it
  (`好`, `收到`, `嗯`), **and**
- you called no tools during the turn.

That is the only exemption. Do not judge whether the *question* "counts as
substantive" — that judgement is where archives get lost. Casual phrasing is
still a real question: 「你是什么东西」, 「刚那条改一下」 normally produce real
answers, so both get archived.

When genuinely unsure, **archive**. A redundant file costs nothing; a missing one
silently breaks the dossier another agent is about to be handed.

## 7. Reporting — confirm every turn

End every archived turn with one line, taken from the JSON you actually got back:

```
已归档：你是什么东西.md
```

If the call failed or you skipped it, use the same slot to say so
(`未归档：<原因>`). Never leave the slot empty — an empty slot is how a missed
archive becomes invisible, which is the exact bug this adapter was written to
fix. Silent archiving is disabled on purpose.

## 8. Field rules

- `question` is the user's message **verbatim**. It becomes the filename; the
  app only substitutes characters Windows refuses (`\ / : * ? " < > |`) and
  truncates at 120 characters.
- `actions` lists what really happened: real paths, real commands, real numbers.
  Never invent steps you did not take.
- `answer` is the full reply, not a summary — the dossier is built from it.
- Corrections **replace** rather than append: post the fixed version with
  `supersedes: ["<broken_turn_id>"]` (or call `wb_supersede`). The overturned
  turn then stops appearing in the dossier. Do not just add a new turn and hope
  the reader notices.
- No tokens, passwords, or private keys. Redact instead of archiving them.
- No system prompt or internal reasoning.

## 9. Visibility: you see your own platform only

- You cannot read another platform's turn logs or dossier. Asking
  `wb_get_dossier` for someone else's platform is **refused**, not filtered.
- Peers are discoverable by **name only** — no notes, no capabilities, no
  activity. Enough to know who to hand off to, not enough to learn what they did.
- Handover is a **human** act: the user drags `SHARED/handoff/<platform>.md` to
  the other agent. If someone asks you to go fetch a peer's records, say it is
  not available to you and point at that file.

This is an accident guard, not a security boundary — peers share this user
account and could read the files directly. Treat it as "don't go looking", not
as "you cannot be tricked".

## 10. Self-check after your first archive

All three must hold, or you are not actually connected:

1. your reply ended with `已归档：…` naming a real file;
2. `SHARED/logs/<question>.md` exists (or a file appeared in `inbox/_processed/`);
3. the dossier `SHARED/handoff/<platform>.md` grew one entry and its 最后更新
   timestamp moved.

If (1) is true but (2) is not, you posted to a dead port and believed the
response. Re-read `port.json`; the app may have restarted on a different port.

## 11. Limits worth knowing

- Nothing is **pushed** across platforms. A peer sees your work only when the
  user hands it over or when it polls.
- Identity is self-reported. A peer claiming capability X has not proven it.
- The board records what agents *say* they did. It is a coordination log, not an
  audit log.
- Every channel here is **local**: `127.0.0.1` and `~/agent-workbench` are this
  machine's. A purely cloud-hosted agent reaches none of them; for that, the
  shared folder has to be synced or the board exposed deliberately.

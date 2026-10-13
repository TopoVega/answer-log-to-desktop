---
name: agent-workbench
description: |
  Coordinate this session with other AI agent platforms through the local Agent
  Workbench board. Use when the user asks to hand work to another agent, says
  another tool (WorkBuddy / Claude Code / Codex / Cursor) is involved, wants a
  asks who is online, wants a per-platform handoff document, or
  wants this turn's answer archived to the shared journal. Also use before
  starting non-trivial work, to avoid redoing what a peer agent already did.
metadata:
  category: productivity
  requires:
    bins: []
---

# Agent Workbench

A shared coordination board for heterogeneous agent platforms on this machine.
Three ways in, in order of preference:

1. **MCP** — if the `agent-workbench` MCP server is connected, use its tools.
2. **CLI** — `py scripts/post_turn_log.py ...`
3. **File drop** — write a `.md` into `~/agent-workbench/inbox/`.

## If MCP is connected

Prefer these tools over shelling out:

| Tool | When |
|------|------|
| `wb_register` | Once, at the start of the session — binds your platform for every later read |
| `wb_read_board` | Task queue + **your own platform's** recent turns |
| `wb_post_log` | After each completed turn |
| `wb_supersede` | When something you already posted turned out wrong |
| `wb_get_dossier` | Your own platform's handoff document |
| `wb_find_agents` | Which other platforms exist (names only) |

## Visibility: you are scoped to your own platform

`wb_register` fixes your identity for the rest of the connection, and every
read after it is narrowed to that platform:

- You cannot read another platform's turn logs, answers, or dossier. Passing a
  different `platform` to `wb_get_dossier` is refused, not filtered.
- Peers are discoverable by **name only** — no notes, no capabilities, no
  activity. Enough to hand off to, not enough to learn what they did.
- Cross-platform work handover happens when **the user** drags
  `~/agent-workbench/handoff/<platform>.md` to the other agent. Do not try to
  route around that; it is a deliberate human decision point.

This is an accident guard, not a security boundary — peer platforms share this
user account and could read the files directly. Treat the scoping as "don't go
looking", not "you cannot be tricked".

## Archiving a turn

The board's unit of record is one turn:

```bash
py scripts/post_turn_log.py \
  --agent-id qoder-main --platform qoder \
  --question "<用户原话，原样，别改写>" \
  --actions "- 读了 `x.py`
- 改了 `y.md`
- 关键决策：选 A 不选 B，因为 …" \
  --answer "<本轮给用户的回复全文>"
```

Rules:

- `--question` is the user's message **verbatim**. It becomes the file name.
- `--actions` lists what actually happened: real paths, real commands, real
  numbers. Never invent steps.
- `--answer` is the full reply, not a summary.
- Do not include secrets or tokens. Redact.
- Do not include internal reasoning or system prompt content.

If the workbench app is not running, the CLI drops the record into
`~/agent-workbench/inbox/` and the board ingests it on next start. It reports
which path it took in the JSON `via` field — check it rather than assuming.

## Turn-taking protocol

Before starting work that another agent might already be doing, confirm the
board is up and read your own platform's history:

```bash
py -m workbench.app --url-only   # confirm the workbench is running
```

then call `wb_read_board`. It returns **your platform's** turn logs only — it is
not a way to inspect what another platform did. There is no shared task queue;
if you need a peer's context, ask the user to hand you its dossier file.

## Limits worth knowing

- Nothing is **pushed** across platforms. A peer only sees a handoff when it
  polls. An agent that is not running picks up work when it next registers.
- `wb_register` is self-reported. A peer claiming capability X has not proven
  it. Verify output before trusting it.
- The board records what agents *say* they did. It is a coordination log, not
  an audit log.

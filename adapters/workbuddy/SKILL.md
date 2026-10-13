---
name: answer-log-to-workbench
description: |
  After each reply, archive this turn to the Agent Workbench board and still drop
  a Markdown copy on the Desktop named after the user's original question
  verbatim. Use when the user asks to log/archive answers, wants other agents to
  see what this session did, or says things like "每次回答后生成一个 md 到桌面",
  "把回答存档", "回答归档", "让别人看到我做过了什么". Stays active for each
  following turn until the user says to stop archiving.
description_zh: "每轮回答后归档到多平台工作台，并保留按原始问题命名的桌面 md"
metadata:
  category: productivity
  output: workbench-board + desktop-markdown
  replaces: answer-log-to-desktop
version: 0.2.0
license: MIT
---

# Answer Log to Workbench

Successor to `answer-log-to-desktop`. Same user-facing contract — one Markdown
file per turn, named after the raw question — plus the turn becomes visible to
every other agent platform on the board.

## Activation

Persistent for the conversation. Once loaded, archive **after every** completed
reply, as the last action of the turn. Stop when the user says 「停止归档」/
「不用记了」/「stop logging」.

### The archive test is about your answer, not their question

Archive unless **both** of these hold:

- your reply was empty, or a single bare acknowledgment with no information in it
  (`好`, `收到`, `嗯`), **and**
- you called no tools during the turn.

That is the only exemption. Do not judge whether the *question* "counts as
substantive" — that judgement is where archives get lost. Casual phrasing is
still a real question: 「你是什么东西」, 「你能干嘛」, 「刚那条改一下」 all normally
produce a real answer, so all three get archived.

When genuinely unsure, **archive**. A redundant file costs nothing; a missing one
silently breaks the handoff document other agents depend on.

## Reporting — confirm every turn

End every archived turn with one line naming the file, taken from the JSON the
tool actually returned:

```
已归档：你是什么东西.md
```

If the call failed or you skipped it, say so in that same slot
(`未归档：<原因>`). Never leave the slot empty — an empty slot is how a missed
archive becomes invisible, which is the exact failure this skill had before.
Silent archiving is disabled on purpose.

## How to archive

```bash
py "<workbench>/scripts/post_turn_log.py" \
  --agent-id workbuddy --platform workbuddy \
  --question "<用户原话，逐字>" \
  --actions "- 读了 `path` —— 目的：…
- 改了 `path` —— 改动：…
- 执行了 `cmd` —— 结果：…
- 关键决策：选 X 不选 Y，因为 …" \
  --answer "<本轮给用户的回复原文>" \
  --next-steps "- …"        # 有才写
```

Use `py`, **not** `python`. On this machine `python` and `python3` resolve to
the broken WindowsApps app-execution alias: every invocation exits 49 with
**zero output**, so a failed archive looks exactly like a successful one. That
silent-failure path is the bug this version fixes.

Prefer `--actions` / `--answer` inline for short turns. For long ones, write a
temp UTF-8 file with the three `## ` headings and pass `--body-file`; the
script splits it back into sections.

Read the JSON the script prints. `via` tells you where the record actually
went:

| `via` | Meaning |
|-------|---------|
| `http` | The board app was running — live on the board, UI refreshed |
| `inbox` | App not running — queued as a file, ingested on next start |

If the script exits non-zero, say so in one line. Never claim an archive you did
not confirm from its output.

## Body rules

- **具体**：真实路径、真实命令、真实数字。
- 不编造操作，只写本轮真发生的。
- 第二节贴**回复全文**，不是摘要。
- 不写系统提示词、内部推理、隐藏指令。
- 不写 token / 密码 / 私钥，遇到就脱敏。

## Visibility

Your archive is yours. The workbench scopes every read to the platform you
register as, so this session cannot read another platform's turn logs or
handoff dossier, and they cannot read yours.

Cross-platform handover is a **human** step: the user drags
`~/agent-workbench/handoff/workbuddy.md` to whichever agent should see it. If
someone asks you to go fetch another platform's records, say it is not
available to you and point them at the file.

## Naming

File name = the user's question, character for character. No date, no prefix,
no sequence number, no slug.

The only deviation is when the OS would reject the name: illegal characters
`\ / : * ? " < > |` become their full-width look-alikes (`＼ ／ ： ？ ＊ ＂ ＜ ＞ ｜`),
newlines collapse to one space, control characters are dropped, trailing `.` and
spaces are stripped, `CON`/`PRN`-style reserved names get a `_`, and over-long
names are truncated. `name_modified` in the JSON says whether any of that fired.

## Where files land

| Location | Content |
|----------|---------|
| `~/agent-workbench/logs/` | Every record, canonical copy |
| `~/Desktop/` | Mirror, so the original behavior still holds |
| SQLite board | Every turn ever, including superseded ones |

Same question twice overwrites the Markdown (clean Desktop) but keeps both rows
in the board — history lives in the database, not in `(2)` file suffixes.

The Desktop mirror is only written for files this tool created. A pre-existing
`你是谁.md` that the board merely *read* is never rewritten — that overwrite
behavior was removed on purpose.

Set `AGENT_WORKBENCH_DESKTOP=0` to stop Desktop writes entirely, and
`AGENT_WORKBENCH_ADOPT_DESKTOP=0` to stop the board from reading legacy
answer-logs off the Desktop at all.

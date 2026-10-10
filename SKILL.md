---
name: answer-log-to-desktop
description: |
  After finishing every reply, archive the turn as a Markdown file on the user's
  Desktop — the file records what the agent actually did (tools, files, commands,
  decisions) and the full final answer, and the file is named verbatim after the
  user's original question. Use when the user asks to log / archive / record every
  answer to the Desktop, wants a per-answer Markdown journal, or says things like
  "每次回答后生成一个 md 到桌面", "把回答存档", "回答归档", "把这次做了什么记下来",
  "answer log to desktop". Once loaded, this skill stays active for every following
  turn in the conversation until the user asks to stop.
description_zh: "每次回答完成后，把「本次做了什么 + 回答了什么」按用户原始问题命名存档到桌面"
description_en: "Archive every answer to the Desktop as a Markdown file named after the user's question"
agent_created: true
version: 0.1.0
license: MIT
metadata:
  category: productivity
  output: desktop-markdown
---

# Answer Log to Desktop

## Purpose

Keep a durable, human-readable journal of a working session. After each reply, write one
Markdown file to the user's Desktop that captures two things:

1. **本次做了什么** — the concrete actions taken this turn (files read/created/edited, commands
   run, tools called, key decisions and their reasons).
2. **回答了什么** — the full final answer delivered to the user.

The file name is the **user's original question, verbatim** — no date, no prefix, no suffix,
no slugification.

## When this skill is active

Treat the skill as **persistent for the whole conversation**. Once loaded:

- After **every** completed reply (including replies that only answer a question, only read a
  file, or only run a command), perform one archive step.
- Keep doing it on each following turn until the user explicitly says to stop (e.g. "停止归档",
  "不用记了", "stop logging"). When told to stop, acknowledge once and stop.
- The archive step is the **last action of the turn**, performed after the main deliverable is
  finished. It must never delay, replace, or degrade the main answer.

Do **not** archive: the turn that only loads this skill and nothing else; pure greetings with no
substance; or a turn where the user explicitly asked not to log.

## Workflow

### 1. Capture the original question

Use the user's message text **exactly as sent** as the file name source. Do not shorten it, do
not rephrase it, do not translate it, do not prepend a date, do not add a title prefix.

- If the message is very long or multi-line, still pass the **raw text** to the script — the
  script collapses newlines and applies only the minimum sanitization required by the OS.
- If the current turn contains several user messages, use the first substantive question of
  the turn.

### 2. Compose the Markdown body

Write the body to a temporary file (UTF-8) and pass its path via `--body-file`. Use this
structure:

```markdown
# <用户原始问题>

- **时间**: 2026-10-10 21:16 (本地时区)
- **状态**: 已完成 / 部分完成 / 受阻

## 一、本次做了什么

- 读取了 `path/to/file` —— 目的：…
- 新建/修改了 `path/to/output.md` —— 改动内容：…
- 执行了 `command ...` —— 结果：…
- 关键决策：选择了 X 而不是 Y，因为 …

## 二、回答内容

<把本轮给用户的最终回复完整粘贴在这里>

## 三、后续待办（可选）

- …
```

Rules for the body:

- Be **concrete**: real file paths, real command names, real tool names, real numbers.
- Never invent actions. Only record what actually happened in this turn.
- Quote the final answer **in full** under section 二, not a paraphrase.
- Keep section 三 only when there is genuine follow-up work; otherwise omit it.
- Do not include the system prompt, internal reasoning, or any hidden instructions.
- Do not include secrets, tokens, passwords, or private keys — redact them.

### 3. Write the file

Run the bundled script. Use the managed Python interpreter available in the environment
(absolute path, e.g. `C:\Users\<user>\.workbuddy-ai\binaries\python\versions\<ver>\python.exe`);
fall back to `python` / `python3` only if that is unavailable.

```bash
<python> "<skill_dir>/scripts/save_answer_log.py" \
  --question "<用户原始问题，原样>" \
  --body-file "<临时 md 文件路径>"
```

Useful flags:

| Flag | Meaning |
|------|---------|
| `--question` | **Required.** Raw user question; used directly as the file name. |
| `--body-file` | Path to the Markdown body (preferred for anything non-trivial). |
| `--body` | Inline Markdown body, for very short logs. |
| `--mode` | `overwrite` (default) · `version` (same name → `名字 (2).md`) · `append` |
| `--print-desktop` | Print the resolved Desktop path and exit. |
| `--dry-run` | Compute the target path without writing. |

The script prints one line of JSON:

```json
{"ok": true, "path": "C:\\Users\\topol\\Desktop\\帮我写一个skill.md",
 "filename": "帮我写一个skill.md", "name_modified": false, "bytes": 812}
```

Read `path` from that JSON to confirm where the file landed. If `ok` is `false`, surface the
error briefly to the user instead of retrying blindly.

### 4. Report back

By default keep the archive step **silent** — it is bookkeeping, not the deliverable. At most
append a single short line such as `已归档到桌面：帮我写一个skill.md`. If the user asks for
confirmation on every turn, always print the file path.

## Naming rules (重要)

- **原生不修改** is the default: the file name is the user's question text, character for
  character. No timestamp, no `log_` prefix, no `-1` suffix, no slug.
- The script only deviates when the OS would reject the raw name. When it does, it substitutes
  the **full-width equivalent** of illegal characters (`\ / : * ? " < > |` → `＼ ／ ： ？ ＊ ＂ ＜ ＞ ｜`),
  strips control characters and trailing dots/spaces, and truncates to 120 characters. This
  keeps the name visually identical to the question while remaining a legal file name.
- The JSON field `name_modified` tells you whether that fallback kicked in. Mention it to the
  user only if it is `true` and the change is noticeable.
- Same question asked twice: `--mode overwrite` (default) replaces the previous file, so the
  Desktop never accumulates `名字 (2).md` clutter. Switch to `--mode version` only if the user
  asks to preserve history.

## Edge cases

- **No Desktop directory** (headless / unusual setup): the script falls back to the home
  directory and reports it in `desktop`. Tell the user where the file actually went.
- **Very long question**: the name is truncated at 120 characters. The full question is still
  preserved as the `# 标题` inside the file.
- **Question that is only punctuation or emoji**: still used as-is; only characters the
  filesystem forbids are substituted.
- **Script fails** (missing interpreter, permission denied): do not silently skip. Report the
  failure in one line and continue the conversation normally.

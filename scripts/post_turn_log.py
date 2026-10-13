#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一轮回答归档到 Agent Workbench。

给任何 agent 平台用的命令行入口：WorkBuddy / Qoder 的 skill 里直接调它，
不需要 MCP 客户端，也不需要 Python SDK。

投递顺序（前一条不通就退到下一条，永不静默失败）：
  1. HTTP 打到正在运行的工作台进程 —— 立刻上板、UI 实时刷新
  2. 落一个 .md 到投递目录 —— 工作台下次启动时自动摄取

用法:
    py post_turn_log.py --agent-id workbuddy --platform workbuddy ^
       --question "用户原话" --body-file turn.md

退出码: 0 成功 / 1 失败（stderr 有原因）/ 2 参数错
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# 这台机器上 `python` 是坏的 WindowsApps 别名（exit 49 零输出），只有 `py` 能用；
# 而任何被冻结/管道化的进程默认 stdout 是 gbk，中文 JSON 会直接烂掉。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

_ILLEGAL = {"\\": "＼", "/": "／", ":": "：", "*": "＊", "?": "？",
            '"': "＂", "<": "＜", ">": "＞", "|": "｜"}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_stem(question: str) -> tuple[str, bool]:
    """文件名 = 问题原文；只有系统会拒绝时才做最小替换。返回 (stem, 是否改过)。"""
    raw = (question or "").strip()
    stem = " ".join(raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
    changed = stem != raw
    if not stem:
        return "未命名回答", True
    buf = []
    for ch in stem:
        if ch in _ILLEGAL:
            buf.append(_ILLEGAL[ch]); changed = True
        elif ord(ch) < 32:
            changed = True
        else:
            buf.append(ch)
    stem = "".join(buf).rstrip(". ")
    changed = changed or stem != "".join(buf)
    if not stem:
        return "未命名回答", True
    if stem.upper() in _RESERVED:
        stem += "_"; changed = True
    while stem and (len(stem) > 120 or len(stem.encode("utf-8", "ignore")) > 200):
        stem = stem[:-1].rstrip(". "); changed = True
    return stem, changed


def shared_root() -> Path:
    return Path(os.environ.get("AGENT_WORKBENCH_HOME") or (Path.home() / "agent-workbench")).expanduser()


def render(args, now_ts: float) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))
    parts = [
        f"# {args.question}", "",
        f"- **时间**: {ts}",
        f"- **执行方**: {args.agent_id} (`{args.platform}`)",
        f"- **状态**: {args.status}",
    ]
    parts += ["", "## 一、本次做了什么", "", args.actions or "_（本轮未记录具体操作）_",
              "", "## 二、回答内容", "", args.answer or "_（空）_"]
    if args.next_steps:
        parts += ["", "## 三、后续待办", "", args.next_steps]
    if args.artifact:
        parts += ["", "## 产出文件", ""] + [f"- `{a}`" for a in args.artifact]
    parts += ["", f"<!--wb: {json.dumps({'agent_id': args.agent_id, 'platform': args.platform}, ensure_ascii=False)} -->", ""]
    return "\n".join(parts)


def post_http(port: int, token: str, payload: dict) -> tuple[bool, str]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/turn-log",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Workbench-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return bool(body.get("ok")), body.get("path", "")
    except urllib.error.HTTPError as exc:
        try:
            return False, json.loads(exc.read().decode("utf-8")).get("error", f"HTTP {exc.code}")
        except Exception:
            return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser(description="归档一轮回答到 Agent Workbench")
    ap.add_argument("--question", required=True, help="用户原始问题，原样传入，直接作为文件名")
    ap.add_argument("--agent-id", default=os.environ.get("WB_AGENT_ID", "cli"))
    ap.add_argument("--platform", default=os.environ.get("WB_PLATFORM", "cli"))
    ap.add_argument("--actions", default="", help="本次做了什么（markdown）")
    ap.add_argument("--answer", default="", help="回答内容全文")
    ap.add_argument("--body-file", help="从文件读取 markdown 正文，自动按三段标题切分")
    ap.add_argument("--next-steps", default="")
    ap.add_argument("--status", default="done", choices=["done", "partial", "blocked"])
    ap.add_argument("--artifact", action="append", default=[], help="可重复，产出文件路径")
    ap.add_argument("--dry-run", action="store_true", help="只打印目标路径，不写不投")
    args = ap.parse_args()

    if args.body_file:
        text = Path(args.body_file).read_text(encoding="utf-8", errors="replace")
        for tag, attr in (("本次做了什么", "actions"), ("回答内容", "answer"), ("后续待办", "next_steps")):
            if getattr(args, attr):
                continue
            i = text.find(tag)
            if i >= 0:
                rest = text[i:].split("\n", 1)[1] if "\n" in text[i:] else ""
                nxt = min((rest.find("\n## ") if "\n## " in rest else len(rest)), len(rest))
                setattr(args, attr, rest[:nxt].strip())
    if not args.answer and not args.actions:
        args.answer = "（未提供 --answer / --body-file）"

    stem, modified = safe_stem(args.question)
    now_ts = time.time()
    payload = {"agent_id": args.agent_id, "platform": args.platform, "question": args.question,
               "actions": args.actions, "answer": args.answer, "next_steps": args.next_steps,
               "status": args.status, "artifacts": args.artifact, "source": "cli"}

    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "filename": f"{stem}.md", "name_modified": modified},
                         ensure_ascii=False))
        return 0

    body = render(args, now_ts)
    port_file = Path(os.environ.get("AGENT_WORKBENCH_DATA") or
                     (os.environ.get("LOCALAPPDATA", str(Path.home())) + os.sep + "AgentWorkbench")) / "port.json"
    token_file = port_file.parent / "token.json"
    via = "inbox"
    path = ""
    if port_file.exists() and token_file.exists():
        try:
            port = json.loads(port_file.read_text(encoding="utf-8"))["port"]
            token = json.loads(token_file.read_text(encoding="utf-8"))["token"]
            ok, info = post_http(port, token, payload)
            if ok:
                via, path = "http", info
            else:
                print(f"[warn] HTTP 投递失败，退回文件：{info}", file=sys.stderr)
        except Exception as exc:
            print(f"[warn] 端口文件不可用，退回文件：{exc}", file=sys.stderr)

    if via == "inbox":
        inbox = shared_root() / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / f"{int(now_ts)}_{stem}.md"
        dest.write_text(body, encoding="utf-8", newline="\n")
        path = str(dest)

    print(json.dumps({"ok": True, "via": via, "path": path, "filename": f"{stem}.md",
                      "name_modified": modified}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)

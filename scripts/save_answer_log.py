#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一次回答的归档 Markdown 写到桌面。

文件名 = 用户原始问题（尽量保持原样，不做任何装饰性修改）。

用法示例:
    python save_answer_log.py --question "帮我写一个skill" --body-file body.md
    python save_answer_log.py --question "帮我写一个skill" --body "# 标题\n正文"
    python save_answer_log.py --print-desktop

输出: 一行 JSON，形如
    {"ok": true, "path": "C:\\Users\\x\\Desktop\\帮我写一个skill.md", ...}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Windows 文件名保留字（不区分大小写）
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Windows 文件名非法字符 -> 全角等价字符（视觉上最接近，不破坏可读性）
_ILLEGAL_MAP = {
    "\\": "＼",
    "/": "／",
    ":": "：",
    "*": "＊",
    "?": "？",
    '"': "＂",
    "<": "＜",
    ">": "＞",
    "|": "｜",
}

MAX_STEM_LEN = 120  # 文件名主体最大字符数，防止超出 255 字节上限


def get_desktop_dir() -> Path:
    """定位桌面目录：Windows 走 Shell API（兼容 OneDrive 重定向），其余走约定路径。"""
    if sys.platform.startswith("win"):
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(260)
            # CSIDL_DESKTOPDIRECTORY = 0x10
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:
                p = Path(buf.value)
                if p.is_dir():
                    return p
        except Exception:
            pass
        candidate = Path(os.path.expanduser("~")) / "Desktop"
        if candidate.is_dir():
            return candidate
        return Path(os.path.expanduser("~"))

    if sys.platform == "darwin":
        return Path(os.path.expanduser("~/Desktop"))

    # Linux: 优先 XDG
    try:
        import subprocess

        out = subprocess.run(
            ["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and out.stdout.strip():
            p = Path(out.stdout.strip())
            if p.is_dir():
                return p
    except Exception:
        pass
    return Path(os.path.expanduser("~/Desktop"))


def sanitize_stem(question: str) -> tuple[str, bool]:
    """返回 (文件名主体, 是否做过修改)。

    优先原样使用；只有当原始字符串无法作为合法文件名时才做最小化替换。
    """
    stem = (question or "").replace("\r\n", "\n").replace("\r", "\n")
    stem = re.sub(r"\s*\n\s*", " ", stem)  # 换行折叠成空格（文件名不能含换行）
    stem = stem.strip()
    if not stem:
        stem = "未命名回答"

    changed = stem != (question or "").strip()
    out = []
    for ch in stem:
        if ch in _ILLEGAL_MAP:
            out.append(_ILLEGAL_MAP[ch])
            changed = True
        elif ord(ch) < 32:
            changed = True  # 控制字符直接丢弃
        else:
            out.append(ch)
    stem = "".join(out)

    # Windows 不允许以点或空格结尾
    if stem != stem.rstrip(". "):
        stem = stem.rstrip(". ")
        changed = True
    if not stem:
        stem = "未命名回答"

    if stem.upper() in _RESERVED:
        stem = stem + "_"
        changed = True

    if len(stem) > MAX_STEM_LEN:
        stem = stem[:MAX_STEM_LEN].rstrip(". ")
        changed = True

    return stem, changed


def unique_path(target: Path) -> Path:
    """同名时追加 (2)、(3) ..."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(2, 1000):
        cand = target.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
    return target.with_name(f"{stem} ({os.getpid()}){suffix}")


def main() -> int:
    ap = argparse.ArgumentParser(description="把回答归档写到桌面")
    ap.add_argument("--question", help="用户原始问题，直接作为文件名")
    ap.add_argument("--body", help="Markdown 正文（短文本）")
    ap.add_argument("--body-file", help="Markdown 正文文件路径（UTF-8）")
    ap.add_argument("--desktop", help="覆盖桌面目录")
    ap.add_argument(
        "--mode",
        choices=["overwrite", "version", "append"],
        default="overwrite",
        help="同名文件处理方式：overwrite 覆盖（默认）/ version 追加序号 / append 追加内容",
    )
    ap.add_argument("--print-desktop", action="store_true", help="只打印桌面路径")
    ap.add_argument("--dry-run", action="store_true", help="只计算路径，不写文件")
    args = ap.parse_args()

    desktop = Path(args.desktop) if args.desktop else get_desktop_dir()

    if args.print_desktop:
        print(json.dumps({"ok": True, "desktop": str(desktop)}, ensure_ascii=False))
        return 0

    if not args.question:
        print(
            json.dumps(
                {"ok": False, "error": "缺少 --question"}, ensure_ascii=False
            )
        )
        return 2

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif args.body is not None:
        body = args.body
    else:
        body = sys.stdin.read()

    if not body.endswith("\n"):
        body += "\n"

    stem, name_changed = sanitize_stem(args.question)

    desktop.mkdir(parents=True, exist_ok=True)
    target = desktop / f"{stem}.md"

    if args.mode == "overwrite":
        final = target
    else:
        final = unique_path(target)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "desktop": str(desktop),
                    "path": str(final),
                    "filename": final.name,
                    "name_modified": name_changed,
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.mode == "append" and final.exists():
        with final.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n\n---\n\n")
            fh.write(body)
    else:
        final.write_text(body, encoding="utf-8", newline="\n")

    print(
        json.dumps(
            {
                "ok": True,
                "desktop": str(desktop),
                "path": str(final),
                "filename": final.name,
                "name_modified": name_changed,
                "bytes": final.stat().st_size,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

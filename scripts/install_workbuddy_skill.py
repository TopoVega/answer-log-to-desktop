"""Install the WorkBuddy adapter skill into WorkBuddy's user skills dir.

Paths derive from the home directory so this works for any user and any clone
location.
"""

from __future__ import annotations

import argparse
import pathlib

SKILL_NAME = "answer-log-to-workbench"
PLACEHOLDER = 'py "<workbench>/scripts/post_turn_log.py"'


def real_command(repo_root: pathlib.Path) -> str:
    return f'py "{repo_root / "scripts" / "post_turn_log.py"}"'


def main() -> int:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description="安装 WorkBuddy 版 skill")
    ap.add_argument("--home", default=str(pathlib.Path.home()), help="用户主目录（默认当前用户）")
    ap.add_argument("--print-path", action="store_true", help="只打印目标路径")
    args = ap.parse_args()

    dst = pathlib.Path(args.home) / ".workbuddy" / "skills" / SKILL_NAME / "SKILL.md"
    if args.print_path:
        print(dst)
        return 0

    src = repo_root / "adapters" / "workbuddy" / "SKILL.md"
    text = src.read_text(encoding="utf-8")
    if PLACEHOLDER not in text:
        raise SystemExit(f"占位路径没找到，检查源文件: {src}")

    text = text.replace(PLACEHOLDER, real_command(repo_root))
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        backup = dst.with_name("SKILL.md.bak")
        backup.write_bytes(dst.read_bytes())
        print("已备份旧文件:", backup)
    dst.write_text(text, encoding="utf-8", newline="\n")

    print("written:", dst)
    print("bytes:", dst.stat().st_size)
    print("real path embedded:", real_command(repo_root) in dst.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

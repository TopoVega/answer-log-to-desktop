"""Install the WorkBuddy-family adapter skills into each product's skills dir.

This machine can host several products of the same family (WorkBuddy and
WorkBuddy AI keep separate `~/.workbuddy` and `~/.workbuddy-ai` trees). They must
archive under different `platform` values or the board merges them into one
column, so each gets its own adapter — and installing only one of them is how
that merge happens by accident. Default is therefore `--product all`.

Paths derive from the home directory so this works for any user and any clone
location.
"""

from __future__ import annotations

import argparse
import pathlib

SKILL_NAME = "answer-log-to-workbench"
PLACEHOLDER = 'py "<workbench>/scripts/post_turn_log.py"'

# product -> (dot-dir under home, adapters/ subdir)
TARGETS = {
    "workbuddy": (".workbuddy", "workbuddy"),
    "workbuddy-ai": (".workbuddy-ai", "workbuddy-ai"),
}


def real_command(repo_root: pathlib.Path) -> str:
    return f'py "{repo_root / "scripts" / "post_turn_log.py"}"'


def install(repo_root: pathlib.Path, home: pathlib.Path, product: str) -> pathlib.Path:
    dot_dir, adapter = TARGETS[product]
    src = repo_root / "adapters" / adapter / "SKILL.md"
    text = src.read_text(encoding="utf-8")
    if PLACEHOLDER not in text:
        raise SystemExit(f"占位路径没找到，检查源文件: {src}")
    text = text.replace(PLACEHOLDER, real_command(repo_root))

    dst = home / dot_dir / "skills" / SKILL_NAME / "SKILL.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        backup = dst.with_name("SKILL.md.bak")
        backup.write_bytes(dst.read_bytes())
        print(f"  已备份旧文件: {backup}")
    dst.write_text(text, encoding="utf-8", newline="\n")

    written = dst.read_text(encoding="utf-8")
    ok_cmd = real_command(repo_root) in written
    ok_id = f"--platform {product}" in written
    print(f"  written: {dst}")
    print(f"  bytes: {dst.stat().st_size} | 真实脚本路径已嵌入: {ok_cmd} | 身份 = {product}: {ok_id}")
    if not (ok_cmd and ok_id):
        raise SystemExit(f"安装后校验失败: {dst}")
    return dst


def main() -> int:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description="安装 WorkBuddy 系列 skill")
    ap.add_argument("--home", default=str(pathlib.Path.home()), help="用户主目录（默认当前用户）")
    ap.add_argument("--product", choices=[*TARGETS, "all"], default="all",
                    help="装给哪个产品；默认两个都装，免得它们挤进同一列")
    ap.add_argument("--print-path", action="store_true", help="只打印目标路径")
    args = ap.parse_args()

    home = pathlib.Path(args.home)
    products = list(TARGETS) if args.product == "all" else [args.product]
    if args.print_path:
        for p in products:
            print(home / TARGETS[p][0] / "skills" / SKILL_NAME / "SKILL.md")
        return 0

    for p in products:
        print(f"== {p} ==")
        install(repo_root, home, p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

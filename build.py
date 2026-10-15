"""Build the desktop exe.

Run it with the project venv: `.venv\\Scripts\\python.exe build.py`. Plain `py`
has no PyInstaller on this machine and fails with "No module named PyInstaller"
after printing nothing useful.

Two artifacts on purpose:

  AgentWorkbench.exe      windowed (no console) — the thing you double-click
  AgentWorkbench-mcp.exe  console build — spawned by MCP clients, which speak
                          over stdio and therefore need a real stdout

A single --noconsole build cannot serve MCP: PyInstaller replaces sys.stdout
with a null writer, and the JSON-RPC handshake would silently produce nothing.
That is the same silent-failure shape as the broken `python` alias this project
exists to route around, so it is split instead of papered over.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UI_SEP = ";" if sys.platform != "win32" else ":"

COMMON = [
    "--noconfirm",
    "--clean",
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build" / "work"),
    "--specpath", str(ROOT / "build" / "specs"),
    # PyInstaller resolves the source side of --add-data against --specpath,
    # not cwd, so it has to be absolute here.
    "--add-data", f"{ROOT / 'workbench' / 'ui'}{UI_SEP}ui",
    "--add-data", f"{ROOT / 'adapters'}{UI_SEP}adapters",
    "--collect-submodules", "webview",
    "--collect-data", "webview",
    "--hidden-import", "clr",
    "--hidden-import", "webview.platforms.winforms",
    "--hidden-import", "webview.platforms.edgechromium",
    "--exclude-module", "tkinter",
    "--exclude-module", "PyQt5",
    "--exclude-module", "PySide6",
]

TARGETS = [
    ("AgentWorkbench", ["--windowed", "--name", "AgentWorkbench"]),
    ("AgentWorkbench-mcp", ["--console", "--name", "AgentWorkbench-mcp"]),
]


def _running_targets() -> list[str]:
    """Names of our own executables that are currently running.

    PyInstaller wipes dist/<name> before it starts writing. If the packaged app
    is open, the delete half-succeeds on a locked DLL and you are left with an
    install that is present but missing its resources — which looks exactly like
    "the guide shows no skills". Check first, for free.
    """
    if sys.platform != "win32":
        return []
    import csv
    import subprocess

    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=15).stdout
    except Exception:
        return []
    names = set()
    for row in csv.reader(out.splitlines()):
        if row:
            names.add(row[0].strip().lower())
    wanted = {f"{n}.exe".lower() for n, _ in TARGETS}
    return sorted(names & wanted)


def build() -> int:
    # Wrong-interpreter runs used to delete dist/ first and fail afterwards,
    # leaving the packaged app present but unusable. Check before touching dist/.
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build] 这个解释器没有 PyInstaller。用项目 venv 跑："
              f" {ROOT / '.venv' / 'Scripts' / 'python.exe'} build.py", file=sys.stderr)
        return 1
    if "--force" not in sys.argv:
        live = _running_targets()
        if live:
            print(f"[build] 拒绝构建：{', '.join(live)} 正在运行。"
                  "先关掉工作台窗口（和用它拉起的 MCP 进程），或加 --force 明知故犯。",
                  file=sys.stderr)
            return 1
    for name, mode in TARGETS:
        cmd = [sys.executable, "-m", "PyInstaller", *COMMON, *mode, str(ROOT / "main.py")]
        print(f"\n=== building {name} ===", flush=True)
        proc = subprocess.run(cmd, cwd=str(ROOT))
        if proc.returncode != 0:
            print(f"[build] {name} failed (exit {proc.returncode})", file=sys.stderr)
            return proc.returncode
    return 0


def report() -> None:
    dist = ROOT / "dist"
    print("\n=== artifacts (onedir) ===")
    for name, _ in TARGETS:
        exe = dist / name / f"{name}.exe"
        if exe.exists():
            folder = exe.parent
            size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
            print(f"  {exe}\n      exe {exe.stat().st_size / 1024 / 1024:.1f} MB · 整个目录 {size / 1024 / 1024:.1f} MB")
        else:
            print(f"  MISSING: {exe}")


if __name__ == "__main__":
    rc = build()
    report()
    raise SystemExit(rc)

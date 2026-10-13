"""Desktop entry point.

    python -m workbench.app            # open the window
    python -m workbench.app --mcp      # serve MCP on stdio (for agent clients)
    python -m workbench.app --no-gui   # headless board (server + inbox only)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from . import config, db, inbox, log, server

APP_EXE = Path(sys.executable).name if getattr(sys, "frozen", False) else "python"


class JsApi:
    """Methods the UI reaches through window.pywebview.api.*"""

    def __init__(self, port: int) -> None:
        self.port = port

    def open_path(self, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return True

    def open_url(self, url: str) -> bool:
        webbrowser.open(url)
        return True

    def quit(self) -> None:
        import webview

        webview.windows[0].close()


def already_running() -> int | None:
    port = config.discover_port()
    if not port:
        return None
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as r:
            if json.loads(r.read().decode()).get("ok"):
                return port
    except Exception:
        return None
    return None


def run_gui(port: int) -> int:
    import webview

    api = JsApi(port)
    win = webview.create_window(
        "Agent Workbench · 多平台协作工作台",
        url=f"http://127.0.0.1:{port}/",
        width=1440,
        height=920,
        min_size=(1024, 660),
        js_api=api,
        background_color="#0e1116",
        text_select=True,
    )
    # js_api already publishes every public method as
    # window.pywebview.api.<method>, so no win.expose() call is needed.

    def on_shown() -> None:
        log.emit(f"窗口已显示 http://127.0.0.1:{port}/")
        try:
            win.evaluate_js(f"window.__WB_INFO__={{app_exe:{json.dumps(config.APP_NAME + '.exe')},port:{port}}};")
        except Exception:
            pass

    win.events.shown += on_shown
    webview.start(debug=bool(os.environ.get("WB_DEBUG")), gui="edgechromium")
    return 0


def run_headless(port: int) -> int:
    log.emit(f"headless board on http://127.0.0.1:{port}")
    log.emit(f"inbox  {config.INBOX_DIR}")
    log.emit(f"export {config.EXPORT_DIR}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


def _stdout_line(text: str) -> None:
    """Machine-consumed output (--url-only). Never let a dead stdout break it."""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except Exception:
        log.emit(text)


def main(argv: list[str] | None = None) -> int:
    # A GUI subsystem that inherits the broken WindowsApps `python` alias gets
    # exit 49 and no output, so stdout is pinned to UTF-8 before anything else.
    log.setup_streams()

    ap = argparse.ArgumentParser(prog=config.APP_NAME, description="多 Agent 平台协作工作台")
    # MCP clients are configured with args: ["mcp"], so accept the bare word too.
    ap.add_argument("command", nargs="?", default=None, choices=["mcp", "gui", "headless"])
    ap.add_argument("--mcp", action="store_true", help="以 MCP stdio server 运行（给 agent 客户端用）")
    ap.add_argument("--no-gui", action="store_true", help="只跑服务端和收件箱，不开窗口")
    ap.add_argument("--port", type=int, default=0, help="监听端口（默认自动）")
    ap.add_argument("--url-only", action="store_true", help="打印地址后退出")
    args = ap.parse_args(argv)

    if args.mcp or args.command == "mcp":
        from . import mcp_server

        return mcp_server.main()

    config.ensure_dirs()
    db.init_db()

    live = already_running()
    if live and not args.port:
        if args.url_only:
            _stdout_line(f"http://127.0.0.1:{live}/")
            return 0
        log.emit(f"已有实例在跑 http://127.0.0.1:{live} — 复用之，不再开第二个")
        webbrowser.open(f"http://127.0.0.1:{live}/")
        return 0

    httpd, _ = server.serve(args.port)
    port = httpd.server_address[1]
    inbox.start_watcher()
    log.emit(f"http://127.0.0.1:{port}")

    if args.url_only:
        _stdout_line(f"http://127.0.0.1:{port}/")
        return 0
    if args.no_gui or args.command == "headless":
        return run_headless(port)
    try:
        return run_gui(port)
    except Exception as exc:
        log.emit(f"桌面窗口起不来（{type(exc).__name__}: {exc}），改用浏览器")
        webbrowser.open(f"http://127.0.0.1:{port}/")
        return run_headless(port)


if __name__ == "__main__":
    raise SystemExit(main())

"""Logging that cannot fail.

A --windowed PyInstaller build has no valid stdout handle: any print() can raise
OSError [Errno 22]. That killed the GUI's own fallback path during testing, so
every diagnostic in the app goes through here instead.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_MAX_LOG_BYTES = 512 * 1024


def _log_file() -> Path | None:
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / "AgentWorkbench.log"
        env = os.environ.get("AGENT_WORKBENCH_LOG")
        if env:
            return Path(env)
    except Exception:
        pass
    return None


def _to_stream(text: str) -> None:
    """Diagnostics go to stderr, never stdout.

    In MCP stdio mode stdout *is* the JSON-RPC transport; a single log line
    written there desynchronises every client reading the stream.
    """
    stream = sys.stderr
    try:
        if stream is not None:
            stream.write(text)
            stream.flush()
    except Exception:
        pass


def emit(msg: str) -> None:
    text = f"{time.strftime('%H:%M:%S')} {msg}\n"
    _to_stream(text)
    path = _log_file()
    if path is None:
        return
    try:
        if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
            path.write_text("", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass  # logging must never be the thing that breaks a turn


def setup_streams() -> None:
    """Pin stdout/stderr to UTF-8 where a real handle exists.

    Without this, a piped process defaults to the ANSI codepage (gbk here) and
    every non-ASCII JSON response comes out mangled.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass

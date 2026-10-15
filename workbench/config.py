"""Runtime paths and network settings for the Agent Workbench."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "AgentWorkbench"
SCHEMA_VERSION = 1
DEFAULT_PORT = 8734


def _data_root() -> Path:
    # Separate from AGENT_WORKBENCH_HOME on purpose: tests must redirect both,
    # otherwise a fixture wipes the real board database.
    override = os.environ.get("AGENT_WORKBENCH_DATA")
    if override:
        return Path(override).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME.lower()


def _shared_root() -> Path:
    """Directory other agent platforms can reach without knowing our data dir."""
    override = os.environ.get("AGENT_WORKBENCH_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / "agent-workbench"


DATA_ROOT = _data_root()
SHARED_ROOT = _shared_root()

DB_PATH = DATA_ROOT / "workbench.db"
PORT_FILE = DATA_ROOT / "port.json"
TOKEN_FILE = DATA_ROOT / "token.json"
EXPORT_DIR = SHARED_ROOT / "logs"
INBOX_DIR = SHARED_ROOT / "inbox"
# Per-platform handoff dossiers live here, ready to be dragged into another agent.
HANDOFF_DIR = SHARED_ROOT / "handoff"
DESKTOP_EXPORT = os.environ.get("AGENT_WORKBENCH_DESKTOP", "1") not in ("0", "", "no")
# Adopt legacy answer-log .md files already on the Desktop (marker-gated).
DESKTOP_ADOPT = os.environ.get("AGENT_WORKBENCH_ADOPT_DESKTOP", "1") not in ("0", "", "no")
# A Desktop file with no marker of its own carries no identity. The skill that
# wrote the legacy ones was WorkBuddy's, so that is the default attribution —
# but this machine may host several products, and guessing wrong silently files
# one product's work under another's column. Override rather than guess.
DESKTOP_ADOPT_AGENT = os.environ.get("AGENT_WORKBENCH_ADOPT_AGENT", "workbuddy")
DESKTOP_ADOPT_PLATFORM = os.environ.get("AGENT_WORKBENCH_ADOPT_PLATFORM", "workbuddy")


def ensure_dirs() -> None:
    for p in (DATA_ROOT, SHARED_ROOT, EXPORT_DIR, INBOX_DIR, HANDOFF_DIR):
        p.mkdir(parents=True, exist_ok=True)


def get_desktop_dir() -> Path:
    """Windows Shell API so OneDrive-redirected Desktops resolve correctly."""
    if sys.platform.startswith("win"):
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:
                p = Path(buf.value)
                if p.is_dir():
                    return p
        except Exception:
            pass
        candidate = Path.home() / "Desktop"
        return candidate if candidate.is_dir() else Path.home()
    if sys.platform == "darwin":
        return Path.home() / "Desktop"
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
    return Path.home() / "Desktop"


def read_session_token() -> str:
    """Localhost is not a security boundary, but a token stops other users'
    processes from writing into the board unnoticed."""
    ensure_dirs()
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))["token"]
        except Exception:
            pass
    import secrets

    token = secrets.token_hex(16)
    TOKEN_FILE.write_text(json.dumps({"token": token}), encoding="utf-8")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return token


def resource_path(*parts: str) -> Path:
    """Resolve a bundled read-only asset in dev and under PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def adapters_dir() -> Path:
    """The adapters/ tree, in both run modes.

    Not `resource_path("adapters")`: in a source checkout adapters/ sits one
    level above the package, while a frozen build carries it at _MEIPASS/adapters.
    Getting this wrong reads as "no skills available" only in the packaged app.
    """
    if getattr(sys, "_MEIPASS", None):
        frozen = Path(sys._MEIPASS) / "adapters"  # type: ignore[attr-defined]
        if frozen.is_dir():
            return frozen
    here = Path(__file__).resolve().parents[1] / "adapters"
    return here if here.is_dir() else Path(getattr(sys, "_MEIPASS", ".")) / "adapters"


def publish_port(port: int) -> None:
    ensure_dirs()
    PORT_FILE.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")


def discover_port() -> int | None:
    if not PORT_FILE.exists():
        return None
    try:
        return int(json.loads(PORT_FILE.read_text(encoding="utf-8"))["port"])
    except Exception:
        return None

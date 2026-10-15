"""File intake: the lowest-common-denominator adapter.

Any agent that can write a file can join the board — no MCP client, no HTTP
client, no token. Two sources are watched:

  inbox/    drop .md or .json here; the file is moved to _processed/ after ingest
  Desktop   optional adoption of the legacy answer-log files, gated on the
            marker heading so unrelated user Markdown is never touched
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
from pathlib import Path

from . import config, core, log, marker

MARKER = "本次做了什么"
STOP = threading.Event()
_seen: set[str] = set()


def _ledger_path() -> Path:
    return config.DATA_ROOT / "adopted.json"


def _load_ledger() -> set[str]:
    try:
        return set(json.loads(_ledger_path().read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_ledger(entries: set[str]) -> None:
    try:
        _ledger_path().write_text(json.dumps(sorted(entries)[-4000:]), encoding="utf-8")
    except OSError:
        pass


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _sidecar_meta(path: Path) -> dict:
    """Identity carried in-file: either our own `agent-workbench:entry:v1`
    marker, or a hand-written `<!--wb: {...}-->` comment."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:6000]
    except OSError:
        return {}
    embedded = marker.read_entry(head)
    if embedded:
        return embedded
    m = re.search(r"<!--\s*wb:\s*(\{.*?\})\s*-->", head, re.DOTALL)
    if not m:
        return {}
    try:
        meta = json.loads(m.group(1))
        return meta if isinstance(meta, dict) else {}
    except json.JSONDecodeError:
        return {}


def looks_like_answer_log(text: str) -> bool:
    """Is this a turn archive we should adopt? Marker first, then heading
    aliases, then the legacy marker string as a last resort."""
    if marker.read_entry(text) or marker.read_doc(text):
        return True
    return core.has_known_section_heading(text) or MARKER in (text or "")


def ingest_file(path: Path, source: str = "inbox", agent_default: str = "file-drop") -> dict | None:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta = _sidecar_meta(path)
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise core.IngestError(f"invalid json: {exc}") from exc
        if not isinstance(payload, dict):
            raise core.IngestError("json drop must be an object")
    else:
        fallback = meta.get("question") or path.stem
        payload = core.parse_inbox_markdown(raw, fallback, {**{"agent_id": agent_default}, **meta})
    payload.setdefault("agent_id", agent_default)
    payload.setdefault("platform", meta.get("platform", "file-drop"))
    payload["source"] = source
    return core.ingest_turn_log(payload, source=source)


def scan_inbox() -> int:
    config.ensure_dirs()
    processed = config.INBOX_DIR / "_processed"
    processed.mkdir(exist_ok=True)
    n = 0
    candidates = sorted(
        p for p in config.INBOX_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in (".md", ".json", ".markdown") and not p.name.startswith(".")
    )
    for path in candidates:
        try:
            ingest_file(path)
            n += 1
        except Exception as exc:
            log.emit(f"[inbox] rejected {path.name}: {exc}")
        try:
            dest = processed / f"{int(time.time())}_{path.name}"
            shutil.move(str(path), dest)
        except OSError as exc:
            log.emit(f"[inbox] could not move {path.name}: {exc}")
    return n


def scan_desktop(ledger: set[str]) -> int:
    """Adopt legacy WorkBuddy answer-logs already sitting on the Desktop."""
    if not config.DESKTOP_ADOPT:
        return 0
    try:
        desk = config.get_desktop_dir()
    except Exception:
        return 0
    if not desk.is_dir():
        return 0
    n = 0
    for path in sorted(desk.glob("*.md")):
        if core.is_desktop_export(path.name):
            continue  # our own mirror — adopting it would record the turn twice
        digest = _digest(path)
        if not digest or digest in ledger or digest in _seen:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not looks_like_answer_log(text):
            ledger.add(digest)  # not ours; remember so we never re-read it
            continue
        try:
            meta = _sidecar_meta(path)
            payload = core.parse_inbox_markdown(
                text, path.stem,
                {**{"agent_id": config.DESKTOP_ADOPT_AGENT,
                    "platform": config.DESKTOP_ADOPT_PLATFORM}, **meta}
            )
            payload["source"] = "desktop-adopt"
            core.ingest_turn_log(payload, source="desktop-adopt")
            n += 1
        except Exception as exc:
            log.emit(f"[desktop] rejected {path.name}: {exc}")
        _seen.add(digest)
        ledger.add(digest)
    if n:
        _save_ledger(ledger)
    return n


def watch(interval: float = 3.0) -> None:
    config.ensure_dirs()
    ledger = _load_ledger()
    log.emit(f"[inbox] watching {config.INBOX_DIR}"
          + (f" + Desktop adoption" if config.DESKTOP_ADOPT else ""))
    while not STOP.is_set():
        try:
            scan_inbox()
            scan_desktop(ledger)
            _save_ledger(ledger)
        except Exception as exc:
            log.emit(f"[inbox] error: {exc}")
        STOP.wait(interval)


def start_watcher(interval: float = 3.0) -> threading.Thread:
    STOP.clear()
    t = threading.Thread(target=watch, args=(interval,), daemon=True, name="wb-inbox")
    t.start()
    return t

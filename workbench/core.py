"""Service layer: validate a turn record, render the Markdown export, write it
to disk, store it, and notify listeners.

Reuses the naming contract from the original answer-log-to-desktop skill
(file name = the user's question verbatim) with two bugs fixed: stdout is
forced to UTF-8 and the illegal-character fallback is reported honestly.
"""

from __future__ import annotations

import json
import re
import threading
import time

from . import config, db, log, marker

_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_ILLEGAL_MAP = {
    "\\": "＼", "/": "／", ":": "：", "*": "＊", "?": "？",
    '"': "＂", "<": "＜", ">": "＞", "|": "｜",
}

MAX_STEM_LEN = 120

_SUBSCRIBERS: list[queue_listener] = []
_SUB_LOCK = threading.Lock()


class IngestError(ValueError):
    pass


def sanitize_stem(question: str) -> tuple[str, bool]:
    """Return (file stem, name_modified). Verbatim unless the OS would reject it."""
    raw = (question or "").strip()
    stem = raw.replace("\r\n", "\n").replace("\r", "\n")
    stem = re.sub(r"\s*\n\s*", " ", stem).strip()
    changed = stem != raw
    if not stem:
        return "未命名回答", True

    out = []
    for ch in stem:
        if ch in _ILLEGAL_MAP:
            out.append(_ILLEGAL_MAP[ch])
            changed = True
        elif ord(ch) < 32:
            changed = True
        else:
            out.append(ch)
    stem = "".join(out).rstrip(". ")
    changed = changed or stem != "".join(out)
    if not stem:
        return "未命名回答", True
    if stem.upper() in _RESERVED:
        stem += "_"
        changed = True
    # 255-byte cap, not 120 chars: CJK is 3 bytes per char in UTF-8/NTFS terms.
    encoded = stem.encode("utf-8", errors="ignore")
    if len(encoded) > 200 or len(stem) > MAX_STEM_LEN:
        while stem and (len(stem.encode("utf-8", errors="ignore")) > 200 or len(stem) > MAX_STEM_LEN):
            stem = stem[:-1]
        stem = stem.rstrip(". ")
        changed = True
    return (stem or "未命名回答"), changed


def render_markdown(rec: dict) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec.get("created_at") or db.now()))
    lines = [
        f"# {rec['question']}",
        "",
        f"- **时间**: {ts}",
        f"- **执行方**: {rec.get('display_name') or rec['agent_id']} (`{rec.get('platform') or 'unknown'}`)",
        f"- **状态**: {rec.get('status', 'done')}",
    ]
    lines.append(f"- **来源**: {rec.get('source', 'http')}")
    lines += ["", "## 一、本次做了什么", ""]
    lines.append((rec.get("actions") or "_（本轮未记录具体操作）_").strip())
    lines += ["", "## 二、回答内容", ""]
    lines.append((rec.get("answer") or "_（空）_").strip())
    next_steps = (rec.get("next_steps") or "").strip()
    if next_steps:
        lines += ["", "## 三、后续待办", "", next_steps]
    artifacts = rec.get("artifacts") or []
    if artifacts:
        lines += ["", "## 产出文件", ""]
        lines += [f"- `{a}`" for a in artifacts]
    lines += ["", marker.entry_marker({
        "agent_id": rec.get("agent_id"), "platform": rec.get("platform"),
        "question": rec.get("question"), "status": rec.get("status", "done"),
        "created_at": rec.get("created_at"), "task_id": rec.get("task_id"),
        "schema": marker.SCHEMA,
    }), ""]
    return "\n".join(lines)


_MIRROR_LEDGER = None
_MIRROR_LOCK = threading.Lock()


def _mirror_ledger() -> set[str]:
    """Names this app itself put on the Desktop. Without this, a Desktop
    file we merely *read* (desktop-adopt) would get rewritten on the next
    ingest, clobbering the user's original."""
    global _MIRROR_LEDGER
    if _MIRROR_LEDGER is None:
        try:
            _MIRROR_LEDGER = set(json.loads(
                (config.DATA_ROOT / "desktop_mirror.json").read_text(encoding="utf-8")))
        except Exception:
            _MIRROR_LEDGER = set()
    return _MIRROR_LEDGER


def _record_mirror(name: str) -> None:
    with _MIRROR_LOCK:
        led = _mirror_ledger()
        if name not in led:
            led.add(name)
            try:
                (config.DATA_ROOT / "desktop_mirror.json").write_text(
                    json.dumps(sorted(led)[-3000:]), encoding="utf-8")
            except OSError:
                pass


def is_desktop_export(name: str) -> bool:
    """Did this app put that Desktop file there itself?

    A mirror is an **output**. The adoption watcher reads any Desktop Markdown
    that looks like an answer log, and our own export carries the entry marker,
    so without this check every turn gets ingested a second time as a
    `desktop-adopt` clone of itself.
    """
    return name in _mirror_ledger()


def write_markdown(rec: dict) -> tuple[str | None, bool]:
    """Export to the shared log dir, and mirror to Desktop to keep the original
    skill's promise. Returns (primary path, name_modified).

    Same question overwrites: SQLite holds every turn, so the Markdown layer
    stays a clean one-file-per-question view instead of accumulating (2), (3).
    """
    stem, changed = sanitize_stem(rec["question"])
    body = render_markdown(rec)
    primary = config.EXPORT_DIR / f"{stem}.md"
    primary.write_text(body, encoding="utf-8", newline="\n")
    name = f"{stem}.md"
    if config.DESKTOP_EXPORT and (rec.get("source") or "") != "desktop-adopt":
        try:
            dest = config.get_desktop_dir()
            target = dest / name
            ours = name in _mirror_ledger()
            if dest.is_dir() and (ours or not target.exists()):
                target.write_text(body, encoding="utf-8", newline="\n")
                _record_mirror(name)
        except OSError:
            pass
    return str(primary), changed




class queue_listener:
    """Thread-safe pub/sub for board events (SSE + UI refresh)."""

    def __init__(self, maxlen: int = 64) -> None:
        import queue

        self.q: queue.Queue = queue.Queue(maxsize=maxlen)
        self._closed = False

    def put(self, event: dict) -> None:
        import queue

        if self._closed:
            return
        try:
            self.q.put_nowait(event)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(event)
            except queue.Full:
                pass

    def close(self) -> None:
        self._closed = True

    def get(self, timeout: float = 15.0):
        import queue

        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None


def subscribe() -> queue_listener:
    lst = queue_listener()
    with _SUB_LOCK:
        _SUBSCRIBERS.append(lst)
    return lst


def unsubscribe(lst: queue_listener) -> None:
    lst.close()
    with _SUB_LOCK:
        if lst in _SUBSCRIBERS:
            _SUBSCRIBERS.remove(lst)


def publish(kind: str, payload: dict | None = None) -> None:
    event = {"kind": kind, "at": db.now(), "payload": payload or {}}
    with _SUB_LOCK:
        targets = list(_SUBSCRIBERS)
    for lst in targets:
        lst.put(event)


def ingest_turn_log(payload: dict, source: str = "http") -> dict:
    """Accept a turn record from any intake path (MCP, HTTP, inbox file)."""
    question = (payload.get("question") or "").strip()
    agent_id = (payload.get("agent_id") or "").strip()
    if not question:
        raise IngestError("缺少 question")
    if not agent_id:
        raise IngestError("缺少 agent_id")

    platform = payload.get("platform") or "unknown"
    rec = {
        "agent_id": agent_id,
        "platform": platform,
        "display_name": payload.get("display_name"),
        "question": question,
        "actions": payload.get("actions") or "",
        "answer": payload.get("answer") or "",
        "next_steps": payload.get("next_steps") or "",
        "status": payload.get("status") or "done",
        "artifacts": payload.get("artifacts") or [],
        "supersedes": payload.get("supersedes") or [],
        "source": payload.get("source") or source,
        "created_at": payload.get("created_at") or db.now(),
    }
    if isinstance(rec["artifacts"], str):
        rec["artifacts"] = [rec["artifacts"]]
    if isinstance(rec["supersedes"], str):
        rec["supersedes"] = [rec["supersedes"]]
    # A typo'd supersede id would silently retire nothing, so reject unknowns up front.
    if rec["supersedes"]:
        known = {r["id"] for r in db.list_turn_logs(limit=1000)}
        unknown = [x for x in rec["supersedes"] if x not in known]
        if unknown:
            raise IngestError(f"supersedes 里有不存在的记录 id: {', '.join(unknown)}")

    md_path, name_modified = write_markdown(rec)
    rec["md_path"] = md_path
    rec["name_modified"] = name_modified
    stored = db.add_turn_log(rec)

    db.upsert_agent(agent_id, platform, payload.get("display_name"), status="idle")
    refresh_dossier(platform)
    publish("turn_log", {"id": stored["id"], "agent_id": agent_id, "question": question,
                         "md_path": md_path, "platform": platform})
    return stored


def refresh_dossier(platform: str) -> dict | None:
    """Rewrite one platform's handoff dossier. A render failure must not lose
    the turn that triggered it, so the error is reported, not raised."""
    from . import dossier

    try:
        meta = dossier.rebuild(platform)
    except Exception as exc:
        log.emit(f"dossier rebuild failed for {platform}: {type(exc).__name__}: {exc}")
        publish("dossier_error", {"platform": platform, "error": str(exc)})
        return None
    publish("dossier_updated", meta)
    return meta


def apply_supersede(winner_id: str, loser_ids: list[str]) -> dict | None:
    """Retroactively overturn entries, then refresh the affected dossier."""
    from . import dossier

    result = db.supersede(winner_id, loser_ids)
    if not result:
        return None
    with db.session() as conn:
        row = conn.execute("SELECT platform FROM turn_logs WHERE id=?", (winner_id,)).fetchone()
    platform = row["platform"] if row and row["platform"] else "unknown"
    refresh_dossier(platform)
    publish("superseded", {**result, "platform": platform})
    return {**result, "platform": platform}


# Heading aliases, normalised before comparison so wording drift, numbering,
# emoji and full/half-width punctuation all stop mattering.
_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "actions": ("本次做了什么", "做了什么", "执行过程", "操作记录", "本次操作", "actions", "what was done",
                "work done", "steps", "过程"),
    "answer": ("回答内容", "回答", "结论", "输出", "答复", "answer", "response", "result", "summary"),
    "next_steps": ("后续待办", "待办", "后续", "下一步", "next steps", "todo", "follow up", "follow-up"),
}
# Order used when a document has sections we cannot name at all.
_POSITIONAL = ("actions", "answer", "next_steps")


def _norm_heading(text: str) -> str:
    text = re.sub(r"<\\?/?[^>]*>", "", text)
    text = re.sub(r"[#*`>_\[\]()【】\[\]]", "", text)
    text = re.sub(r"^[\s0-9.、()（）一二三四五六七八九十]+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff ]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _match_heading(heading: str) -> str | None:
    norm = _norm_heading(heading)
    if not norm:
        return None
    for field, aliases in _SECTION_ALIASES.items():
        for alias in aliases:
            a = _norm_heading(alias)
            if a and (a == norm or a in norm):
                return field
    return None


def has_known_section_heading(text: str) -> bool:
    """True if any `## ` heading in the document names a known section."""
    return any(_match_heading(h) for h in re.findall(r"^##[^\n]*$", text or "", re.MULTILINE))


def parse_inbox_markdown(text: str, fallback_question: str, meta: dict) -> dict:
    """Turn a hand-written md drop into a record.

    Three tiers, tried in order, so a peer platform rewording its headings
    degrades gracefully instead of losing the section:

      1. an embedded `agent-workbench:entry:v1` marker (exact, no guessing);
      2. `## ` headings matched against normalised aliases;
      3. positional: with two or more unnamed sections, assume the original
         做了什么 / 回答 / 待办 order.

    Anything still unrecognised becomes the answer, so no drop is silently lost.
    """
    embedded = marker.read_entry(text) or {}
    clean = marker.strip_markers(text)

    title = fallback_question
    m = re.search(r"^#\s+(.+)$", clean, re.MULTILINE)
    if m:
        title = m.group(1).strip()

    parts = re.split(r"^##[^\n]*$", clean, flags=re.MULTILINE)
    heads = re.findall(r"^##[^\n]*$", clean, flags=re.MULTILINE)
    bodies = parts[1:]

    sections: dict[str, str] = {}
    matched: list[str | None] = []
    for head, body in zip(heads, bodies):
        matched.append(_match_heading(head))

    named = {f: b.strip() for f, b in zip(matched, bodies) if f and b.strip()}
    sections.update(named)

    unnamed = [b.strip() for f, b in zip(matched, bodies) if not f and b.strip()]
    if not sections and len(unnamed) >= 2:
        sections.update({f: b for f, b in zip(_POSITIONAL, unnamed) if b})

    # The marker wins for identity; prose only fills gaps it did not declare.
    for field in ("actions", "answer", "next_steps"):
        if embedded.get(field) and not sections.get(field):
            sections[field] = str(embedded[field])
    if "answer" not in sections:
        # Nothing claimed the answer slot: keep the whole document rather than
        # drop it. A noisy answer beats a silently empty archive.
        sections["answer"] = clean.strip()

    for key in ("agent_id", "platform", "question", "task_id", "status"):
        if embedded.get(key) and not meta.get(key):
            meta[key] = embedded[key]

    meta.setdefault("question", title)
    return {
        "question": meta["question"],
        "actions": sections.get("actions", ""),
        "answer": sections.get("answer", ""),
        "next_steps": sections.get("next_steps", ""),
        "agent_id": meta.get("agent_id", "unknown"),
        "platform": meta.get("platform", "unknown"),
        "display_name": meta.get("display_name"),
        "status": meta.get("status", "done"),
    }


def health() -> dict:
    config.ensure_dirs()
    return {
        "ok": True,
        "app": config.APP_NAME,
        "version": "0.1.0",
        "db": str(config.DB_PATH),
        "inbox": str(config.INBOX_DIR),
        "export": str(config.EXPORT_DIR),
        "desktop_export": config.DESKTOP_EXPORT,
        "stats": db.stats(),
    }


def json_dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)

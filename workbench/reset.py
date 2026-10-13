"""Two independent resets.

  timeline  — wipes the archive: DB rows, the exported Markdown, and Desktop
              files that share a name with an archived question.
  dossier   — rolls a platform's handoff document back to empty via a
              watermark. History survives; the derived document starts clean
              and stays clean, because the filter is applied at render time.

The Desktop deletion is deliberately name-scoped: it only touches
`<stem>.md` for stems that came from an archive entry. Nothing here ever
globs the Desktop, so unrelated notes and the user's own files are out of
reach even when a reset is confirmed carelessly.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import config, core, db, dossier


def _mirror_ledger() -> set[str]:
    """File names this app actually wrote to the Desktop. Anything on the
    Desktop that is not in here was put there by the user or by another tool,
    and deleting it is a materially different decision."""
    try:
        return set(json.loads((config.DATA_ROOT / "desktop_mirror.json").read_text(encoding="utf-8")))
    except Exception:
        return set()


def _scope_sql(platform: str | None, agent_id: str | None) -> tuple[str, tuple]:
    where, params = [], []
    if platform:
        where.append("platform=?")
        params.append(platform)
    if agent_id:
        where.append("agent_id=?")
        params.append(agent_id)
    return (" WHERE " + " AND ".join(where) if where else ""), tuple(params)


def _timeline_targets(platform: str | None = None, agent_id: str | None = None):
    """(rows, export files, Desktop files we created, Desktop files we did not).

    Scoped calls only ever see files whose name came from a row inside the
    scope. The unscoped "delete the whole export dir" behaviour is reachable
    only when both arguments are None, so a scoped reset can never widen by
    accident if the matched rows happen to have blank questions.
    """
    clause, params = _scope_sql(platform, agent_id)
    with db.session() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT id, platform, agent_id, question, md_path FROM turn_logs{clause}", params).fetchall()]

    stems = {core.sanitize_stem(r["question"])[0] for r in rows if (r.get("question") or "").strip()}
    scoped = bool(platform or agent_id)

    export_hits: list[Path] = []
    if config.EXPORT_DIR.is_dir():
        if scoped:
            export_hits = sorted(p for p in config.EXPORT_DIR.glob("*.md") if p.stem in stems)
        else:
            export_hits = sorted(config.EXPORT_DIR.glob("*.md"))

    ours: list[Path] = []
    foreign: list[Path] = []
    ledger = _mirror_ledger()
    try:
        desk = config.get_desktop_dir()
    except Exception:
        desk = None
    if desk and desk.is_dir():
        for p in sorted(desk.glob("*.md")):
            if p.stem not in stems:
                continue
            (ours if p.name in ledger else foreign).append(p)

    return rows, export_hits, ours, foreign


def preview_timeline(platform: str | None = None, agent_id: str | None = None,
                     delete_foreign_desktop: bool = True) -> dict:
    rows, export_hits, ours, foreign = _timeline_targets(platform, agent_id)
    desktop_hits = ours + (foreign if delete_foreign_desktop else [])
    return {
        "scope": {"platform": platform, "agent_id": agent_id},
        "entries": len(rows),
        "agents": sorted({r["agent_id"] for r in rows if r.get("agent_id")}),
        "export_files": [str(p) for p in export_hits],
        "desktop_files": [str(p) for p in desktop_hits],
        "desktop_ours": [p.name for p in ours],
        "desktop_foreign": [p.name for p in foreign],
        "export_count": len(export_hits),
        "desktop_count": len(desktop_hits),
        "export_dir": str(config.EXPORT_DIR),
        "desktop_dir": str(config.get_desktop_dir()),
        "note": "桌面只删与归档同名的文件，不会清空桌面。",
    }


def reset_timeline(platform: str | None = None, agent_id: str | None = None,
                   delete_foreign_desktop: bool = True) -> dict:
    """Wipe archives and their Markdown, optionally scoped to one platform or agent."""
    rows, export_hits, ours, foreign = _timeline_targets(platform, agent_id)
    desktop_hits = ours + (foreign if delete_foreign_desktop else [])
    platforms = sorted({r["platform"] for r in rows if r.get("platform")})

    deleted: list[str] = []
    kept: list[str] = []
    for path in export_hits:
        try:
            path.unlink()
            deleted.append(str(path))
        except OSError as exc:
            kept.append(f"{path} ({exc.strerror or exc})")

    # Desktop files are the user's territory: report every outcome, never assume.
    desktop_deleted, desktop_kept = [], []
    for path in desktop_hits:
        try:
            path.unlink()
            desktop_deleted.append(str(path))
        except OSError as exc:
            desktop_kept.append(f"{path} ({exc.strerror or exc})")

    clause, params = _scope_sql(platform, agent_id)
    with db.session() as conn:
        conn.execute(f"DELETE FROM turn_logs{clause}", params)

    # Dossiers are derived from what just got deleted; rebuild so they agree.
    rebuilt = [core.refresh_dossier(p) for p in platforms]
    rebuilt = [m for m in rebuilt if m]
    # Only a full reset may prune dossier files: on a scoped one every other
    # platform's document is absent from `rebuilt` by design, not because it is stale.
    if not platform and not agent_id:
        for stale in (config.HANDOFF_DIR.glob("*.md") if config.HANDOFF_DIR.is_dir() else []):
            if stale.stem not in {m["platform"] for m in rebuilt}:
                try:
                    stale.unlink()
                except OSError:
                    pass

    core.publish("reset_timeline", {"entries": len(rows), "deleted": len(deleted),
                                    "desktop_deleted": len(desktop_deleted),
                                    "platform": platform, "agent_id": agent_id})
    return {
        "scope": {"platform": platform, "agent_id": agent_id},
        "entries_removed": len(rows),
        "export_files_deleted": deleted,
        "export_files_failed": kept,
        "desktop_files_deleted": desktop_deleted,
        "desktop_files_failed": desktop_kept,
        "dossiers_rebuilt": [m["platform"] for m in rebuilt],
    }


def _known_platforms() -> set[str]:
    with db.session() as conn:
        rows = conn.execute("SELECT DISTINCT platform FROM turn_logs WHERE platform IS NOT NULL").fetchall()
    return {r["platform"] for r in rows}


def preview_dossier(platforms: list[str] | None = None) -> dict:
    targets = platforms or sorted({m["platform"] for m in dossier.platforms()})
    return {
        "platforms": targets,
        "current_entries": {p: len(db.current_logs(p)) for p in targets},
        "watermarks": {p: db.get_watermark(p) for p in targets},
    }


def reset_dossier(platforms: list[str] | None = None) -> dict:
    """Empty each platform's handoff document and keep it empty."""
    known = _known_platforms() | {m["platform"] for m in dossier.platforms()}
    targets = platforms or sorted(known)
    unknown = [p for p in targets if p not in known]
    if unknown:
        # A typo'd name would otherwise report success while resetting nothing.
        raise ValueError(f"未知平台: {', '.join(unknown)}")
    ts = db.now()
    done = {}
    for platform in targets:
        hidden = len(db.current_logs(platform))
        db.set_watermark(platform, ts)
        meta = core.refresh_dossier(platform)
        done[platform] = {
            "hidden_entries": hidden,
            "path": str(meta["path"]) if meta else str(config.HANDOFF_DIR / f"{platform}.md"),
            "bytes": meta["bytes"] if meta else 0,
        }
    core.publish("reset_dossier", {"platforms": list(done)})
    return {"platforms": done, "watermark": ts}


def undo_dossier(platform: str) -> dict | None:
    """Lift a watermark: the archived history re-renders into the dossier."""
    if not db.get_watermark(platform):
        return None
    db.clear_watermark(platform)
    meta = core.refresh_dossier(platform)
    return {"platform": platform, "restored_entries": len(db.current_logs(platform)),
            "path": meta["path"] if meta else None}

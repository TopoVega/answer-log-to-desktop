"""SQLite storage. One connection per operation so the HTTP server, inbox
watcher and UI thread can all touch the board without sharing handles."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    display_name TEXT,
    capabilities TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'idle',
    note TEXT,
    registered_at REAL NOT NULL,
    last_seen REAL NOT NULL
);


CREATE TABLE IF NOT EXISTS turn_logs (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    platform TEXT,
    question TEXT NOT NULL,
    actions TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL DEFAULT '',
    next_steps TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'done',
    artifacts TEXT NOT NULL DEFAULT '[]',
    supersedes TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'http',
    md_path TEXT,
    name_modified INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);


CREATE INDEX IF NOT EXISTS idx_logs_created ON turn_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_agent ON turn_logs (agent_id, created_at DESC);
"""

AGENT_STATUSES = ("idle", "working", "blocked", "offline")


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", str(config.SCHEMA_VERSION)),
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            ("created_at", str(time.time())),
        )


def _migrate(conn: sqlite3.Connection) -> None:
    """Reconcile an existing DB with the current schema."""
    conn.executescript("DROP TABLE IF EXISTS tasks; DROP TABLE IF EXISTS messages;")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(turn_logs)").fetchall()}
    if "task_id" in cols:
        # The delegation subsystem is gone; the column is unreferenced. SQLite
        # drops it in place from 3.35 on, so no table rebuild is needed.
        conn.execute("ALTER TABLE turn_logs DROP COLUMN task_id")
    have = {r[1] for r in conn.execute("PRAGMA table_info(turn_logs)").fetchall()}
    if "supersedes" not in have:
        conn.execute("ALTER TABLE turn_logs ADD COLUMN supersedes TEXT NOT NULL DEFAULT '[]'")


@contextmanager
def session():
    """A connection that actually closes.

    `with sqlite3.connect(...)` only manages the transaction — the handle stays
    open, which on Windows pins the .db file so it can never be deleted or
    replaced. Every caller here wants both.
    """
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def now() -> float:
    return time.time()


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def upsert_agent(
    agent_id: str,
    platform: str,
    display_name: str | None = None,
    capabilities: list[str] | None = None,
    status: str = "idle",
    note: str | None = None,
) -> dict:
    if status not in AGENT_STATUSES:
        raise ValueError(f"bad agent status: {status}")
    t = now()
    with session() as conn:
        existing = _one(conn, "SELECT * FROM agents WHERE agent_id=?", (agent_id,))
        if existing:
            conn.execute(
                """UPDATE agents SET platform=?, display_name=COALESCE(?, display_name),
                       capabilities=?, status=?, note=COALESCE(?, note), last_seen=?
                   WHERE agent_id=?""",
                (
                    platform,
                    display_name,
                    json.dumps(capabilities or [], ensure_ascii=False),
                    status,
                    note,
                    t,
                    agent_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO agents (agent_id, platform, display_name, capabilities,
                       status, note, registered_at, last_seen)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    agent_id,
                    platform,
                    display_name or agent_id,
                    json.dumps(capabilities or [], ensure_ascii=False),
                    status,
                    note,
                    t,
                    t,
                ),
            )
        return _one(conn, "SELECT * FROM agents WHERE agent_id=?", (agent_id,))  # type: ignore[returned-value]


def heartbeat(agent_id: str, status: str | None = None) -> None:
    t = now()
    with session() as conn:
        if status and status in AGENT_STATUSES:
            conn.execute("UPDATE agents SET last_seen=?, status=? WHERE agent_id=?", (t, status, agent_id))
        else:
            conn.execute("UPDATE agents SET last_seen=? WHERE agent_id=?", (t, agent_id))


def last_log_times() -> dict[str, float]:
    """Newest archive per agent_id. Used to spot the leak signature: an agent
    that keeps heartbeating but has stopped posting turn logs."""
    with session() as conn:
        rows = _rows(conn, "SELECT agent_id, MAX(created_at) AS t FROM turn_logs GROUP BY agent_id")
    return {r["agent_id"]: r["t"] for r in rows if r["t"]}


def list_agents(stale_after: float = 900.0, silent_after: float = 600.0) -> list[dict]:
    t = now()
    last_log = last_log_times()
    with session() as conn:
        rows = _rows(conn, "SELECT * FROM agents ORDER BY last_seen DESC")
    for r in rows:
        r["capabilities"] = json.loads(r["capabilities"] or "[]")
        r["online"] = (t - r["last_seen"]) < stale_after
        r["effective_status"] = r["status"] if r["online"] or r["status"] == "blocked" else "offline"
        r["seen_seconds_ago"] = int(t - r["last_seen"])
        r["last_log_at"] = last_log.get(r["agent_id"])
        if r["last_log_at"]:
            gap = t - r["last_log_at"]
            r["silent_seconds"] = int(gap)
            # Heartbeat is fresh but no archive for a while == it is talking to
            # the board and skipping the log step.
            r["suspected_missed_archive"] = bool(r["online"] and gap > silent_after)
        else:
            r["silent_seconds"] = None
            r["suspected_missed_archive"] = bool(r["online"] and (t - r["registered_at"]) > silent_after)
    return rows


def add_turn_log(record: dict) -> dict:
    import uuid

    lid = record.get("id") or uuid.uuid4().hex[:12]
    with session() as conn:
        conn.execute(
            """INSERT INTO turn_logs (id, agent_id, platform, question, actions, answer,
                       next_steps, status, artifacts, supersedes, source, md_path, name_modified, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                lid,
                record["agent_id"],
                record.get("platform"),
                record["question"],
                record.get("actions", ""),
                record.get("answer", ""),
                record.get("next_steps", ""),
                record.get("status", "done"),
                json.dumps(record.get("artifacts", []), ensure_ascii=False),
                json.dumps([str(x) for x in (record.get("supersedes") or [])], ensure_ascii=False),
                record.get("source", "http"),
                record.get("md_path"),
                1 if record.get("name_modified") else 0,
                record.get("created_at") or now(),
            ),
        )
        return _one(conn, "SELECT * FROM turn_logs WHERE id=?", (lid,))  # type: ignore[returned-value]


def retired_ids(conn: sqlite3.Connection | None = None, platform: str | None = None) -> set[str]:
    """Ids overturned by some later entry. Read-side computation, so a
    supersede never has to rewrite history and un-doing one is free."""
    sql = "SELECT id, supersedes FROM turn_logs"
    params: tuple = ()
    if platform:
        sql += " WHERE platform=?"
        params = (platform,)
    own = conn is None
    if own:
        conn = connect()
    retired: set[str] = set()
    for row in conn.execute(sql, params):
        try:
            retired.update(json.loads(row["supersedes"] or "[]"))
        except json.JSONDecodeError:
            continue
    if own:
        conn.close()
    return retired


def supersede(winner_id: str, loser_ids: list[str]) -> dict | None:
    """Record that `winner_id` overturns each of `loser_ids`.

    Refuses a winner overturning itself, and refuses to point at unknown ids,
    so a typo cannot silently retire nothing.
    """
    losers = [x for x in dict.fromkeys(loser_ids) if x and x != winner_id]
    with session() as conn:
        winner = _one(conn, "SELECT id FROM turn_logs WHERE id=?", (winner_id,))
        if not winner:
            return None
        known = {r["id"] for r in _rows(conn, "SELECT id FROM turn_logs")}
        missing = [x for x in losers if x not in known]
        if missing:
            raise ValueError(f"unknown turn_log ids: {', '.join(missing)}")
        row = _one(conn, "SELECT supersedes FROM turn_logs WHERE id=?", (winner_id,))
        merged = list(dict.fromkeys(json.loads((row or {}).get("supersedes") or "[]") + losers))
        conn.execute("UPDATE turn_logs SET supersedes=? WHERE id=?",
                     (json.dumps(merged, ensure_ascii=False), winner_id))
        return {"winner": winner_id, "supersedes": merged}


def list_turn_logs(
    agent_id: str | None = None,
    since: float | None = None,
    limit: int = 50,
) -> list[dict]:
    sql = "SELECT * FROM turn_logs"
    where: list[str] = []
    params: list = []
    if agent_id:
        where.append("agent_id=?")
        params.append(agent_id)
    if since is not None:
        where.append("created_at>?")
        params.append(since)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with session() as conn:
        rows = _rows(conn, sql, tuple(params))
    for r in rows:
        r["artifacts"] = json.loads(r["artifacts"] or "[]")
        r["supersedes"] = json.loads(r["supersedes"] or "[]")
    return rows


def get_watermark(platform: str) -> float:
    """Epoch below which a platform's entries are hidden from its dossier.

    Set when the user resets the handoff area. History stays in the database;
    only the derived document is rolled back to a clean slate, and it stays
    clean because the filter lives here rather than in the file.
    """
    with session() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (f"dossier_watermark:{platform}",)).fetchone()
    return float(row[0]) if row else 0.0


def set_watermark(platform: str, ts: float) -> None:
    with session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (f"dossier_watermark:{platform}", str(ts)),
        )


def clear_watermark(platform: str) -> None:
    with session() as conn:
        conn.execute("DELETE FROM meta WHERE key=?", (f"dossier_watermark:{platform}",))


def watermarks() -> dict[str, float]:
    prefix = "dossier_watermark:"
    with session() as conn:
        rows = conn.execute("SELECT key, value FROM meta WHERE key LIKE ?", (prefix + "%",)).fetchall()
    return {r[0][len(prefix):]: float(r[1]) for r in rows}


def current_logs(platform: str, limit: int = 500) -> list[dict]:
    """Surviving entries for one platform, oldest first — the dossier's input.

    Retired (superseded) rows are dropped here rather than at write time, so
    the document shows the corrected state without the DB losing history.
    Entries at or below the platform's reset watermark are hidden too.
    """
    floor = get_watermark(platform)
    with session() as conn:
        retired = retired_ids(conn, platform)
        rows = _rows(
            conn,
            "SELECT * FROM turn_logs WHERE platform=? AND created_at>? ORDER BY created_at ASC LIMIT ?",
            (platform, floor, limit),
        )
    out = []
    for r in rows:
        if r["id"] in retired:
            continue
        r["artifacts"] = json.loads(r["artifacts"] or "[]")
        r["supersedes"] = [x for x in json.loads(r["supersedes"] or "[]") if x not in {q["id"] for q in out}]
        out.append(r)
    return out


def latest_touch(platform: str) -> float | None:
    with session() as conn:
        row = conn.execute(
            "SELECT MAX(created_at) AS t FROM turn_logs WHERE platform=?", (platform,)
        ).fetchone()
    return row["t"] if row and row["t"] else None


def get_turn_log(log_id: str) -> dict | None:
    with session() as conn:
        row = _one(conn, "SELECT * FROM turn_logs WHERE id=?", (log_id,))
    if row:
        row["artifacts"] = json.loads(row["artifacts"] or "[]")
    return row


def board_state(stale_after: float = 900.0, scope: str | None = None) -> dict:
    """Board snapshot. `scope` restricts every content-bearing field to one
    platform; peers stay visible as bare names so an agent knows the board has
    company without learning what they did."""
    agents = list_agents(stale_after)
    logs = list_turn_logs(limit=40) if scope is None else list_turn_logs(limit=400)
    if scope is not None:
        logs = [r for r in logs if r.get("platform") == scope][:40]
        agents = [
            {"agent_id": a["agent_id"], "platform": a["platform"], "online": a["online"],
             "seen_seconds_ago": a["seen_seconds_ago"], "effective_status": a["effective_status"],
             "display_name": a["display_name"] if a["platform"] == scope else a["platform"],
             "capabilities": a["capabilities"] if a["platform"] == scope else [],
             "note": a["note"] if a["platform"] == scope else None}
            for a in agents
        ]
    return {
        "agents": agents,
        "logs": logs,
        "scope": scope,
        "generated_at": now(),
        "paths": {
            "db": str(config.DB_PATH),
            "inbox": str(config.INBOX_DIR),
            "export": str(config.EXPORT_DIR),
        },
    }


def stats() -> dict:
    with session() as conn:
        total_logs = conn.execute("SELECT COUNT(*) FROM turn_logs").fetchone()[0]
        total_agents = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        by_platform = {
            r["platform"]: r["n"]
            for r in conn.execute(
                "SELECT COALESCE(platform,'?') AS platform, COUNT(*) AS n FROM turn_logs GROUP BY platform"
            ).fetchall()
        }
    return {
        "total_logs": total_logs,
        "total_agents": total_agents,
        "by_platform": by_platform,
    }

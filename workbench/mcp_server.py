"""MCP server for the Agent Workbench (stdio **and** streamable-HTTP JSON-RPC 2.0).

This is the layer that makes the workbench platform-agnostic: Qoder, WorkBuddy,
Claude Code, Codex, Cursor and anything else that speaks MCP attaches with a
one-line config, no bespoke plugin per vendor.

Two transports, because clients split into two camps:

  * **stdio** (`main()`): the client spawns us as a process. One process = one
    client = one identity, which is why identity used to be a single module global.
  * **HTTP** (`new_session()` + `handle(msg, session)`, served by server.py at
    `/mcp`): the client only gives us a URL. Many hosted platforms cannot spawn
    a local process at all, so without this transport they cannot attach,
    however good the skill is. Here one process serves many clients, so identity
    lives in a per-session dict rather than in the module.

Both talk to the board through bridge.py (HTTP to the running app, with a
direct-SQLite fallback).
"""

from __future__ import annotations

import contextvars
import json
import os
import sys
import time

from . import bridge, config, db

PROTOCOL_FALLBACK = "2024-11-05"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}

TOOLS = [
    {
        "name": "wb_register",
        "description": (
            "Announce this agent to the shared workbench and bind your identity for the rest "
            "of this connection. Every later read is automatically limited to your own platform, "
            "so declare the platform you actually are."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Stable id for this agent/session, e.g. 'qoder-main'."},
                "platform": {"type": "string", "description": "Host platform name, e.g. 'qoder', 'workbuddy', 'claude-code'."},
                "display_name": {"type": "string"},
                "capabilities": {"type": "array", "items": {"type": "string"},
                                 "description": "Short skills, e.g. ['python','frontend','review']."},
                "note": {"type": "string", "description": "One line about what you are working on."},
            },
            "required": ["agent_id", "platform"],
        },
    },
    {
        "name": "wb_post_log",
        "description": (
            "Archive one completed turn to the shared board: what the user asked, what you "
            "actually did, and the full answer. The Markdown file is named after the raw "
            "question. Other agents read these to avoid duplicating or contradicting work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "platform": {"type": "string"},
                "question": {"type": "string", "description": "The user's original request, verbatim."},
                "actions": {"type": "string", "description": "Markdown list of files read/changed, commands run, decisions taken. No padding."},
                "answer": {"type": "string", "description": "The final reply given to the user, in full."},
                "next_steps": {"type": "string", "description": "Open follow-ups, if any."},
                "status": {"type": "string", "enum": ["done", "partial", "blocked"]},
                "artifacts": {"type": "array", "items": {"type": "string"}},
                "supersedes": {"type": "array", "items": {"type": "string"},
                               "description": ("Ids of earlier turn logs this one overturns. Required for "
                                               "any correction: without it both versions stay live and the "
                                               "handoff dossier accumulates a mistake you already fixed.")},
            },
            "required": ["agent_id", "question"],
        },
    },
    {
        "name": "wb_supersede",
        "description": (
            "Retroactively overturn one or more turn logs, then rebuild the platform dossier. "
            "Use when a record you already posted turned out to be wrong and posting a fresh "
            "log is not an option — the superseded text drops out of 'current state' and moves "
            "to the '被推翻的记录' section."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "winner_id": {"type": "string", "description": "The log id that is now believed correct."},
                "loser_ids": {"type": "array", "items": {"type": "string"},
                              "description": "Log ids this one overturns."},
            },
            "required": ["winner_id", "loser_ids"],
        },
    },
    {
        "name": "wb_list_dossiers",
        "description": "Your own platform's handoff dossier: path, last update, live vs overturned entry counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "wb_get_dossier",
        "description": (
            "Fetch YOUR OWN platform's handoff dossier as Markdown — a state document, not a "
            "journal, so superseded content is already excluded. Asking for another platform's "
            "dossier is refused: the user hands those over by dragging the file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Must equal the platform you registered as."},
                "refresh": {"type": "boolean", "description": "Rebuild before reading."},
            },
            "required": [],
        },
    },
    {
        "name": "wb_read_board",
        "description": "Your own platform's recent turn logs and the roster. Other platforms appear by name only — their work is handed over by the user, not read here.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Max recent logs, default 15."}},
        },
    },
    {
        "name": "wb_find_agents",
        "description": "Other platforms present on the board, by name only. Their notes, capabilities and work are not exposed.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _text(obj) -> dict:
    payload = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    return {"content": [{"type": "text", "text": payload}], "isError": False}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": f"error: {msg}"}], "isError": True}


# Identity is bound once per MCP session, not asserted on every call. Under
# stdio the session is this whole process; over HTTP it is one dict per client,
# and a ContextVar picks the right one without threading it through every tool.
#
# This is an accident guard, not a security boundary: a peer platform shares
# this user account and can read the SQLite file or handoff folder directly.
# The real control is that the user drags a dossier over by hand.
_STDIO_SESSION: dict = {"agent_id": None, "platform": None}
_SESSION: contextvars.ContextVar[dict] = contextvars.ContextVar("mcp_session", default=_STDIO_SESSION)


def _sess() -> dict:
    return _SESSION.get()


def new_session(hint: dict | None = None) -> dict:
    """Identity for one HTTP connection.

    `hint` lets the URL carry a platform up front
    (`/mcp?platform=trae&agent_id=trae-review`), so a client that never calls
    wb_register still archives under a real column rather than `unknown`.
    """
    sess: dict = {"agent_id": None, "platform": None}
    if hint:
        if hint.get("platform"):
            sess["platform"] = str(hint["platform"])[:40]
        if hint.get("agent_id"):
            sess["agent_id"] = str(hint["agent_id"])[:60]
    return sess


def handle(msg: dict, session: dict | None = None) -> str | None:
    """Run one JSON-RPC message under `session`; stdio passes None."""
    token = _SESSION.set(_STDIO_SESSION if session is None else session)
    try:
        return _handle(msg)
    finally:
        _SESSION.reset(token)


def _own_platform() -> str | None:
    return _sess().get("platform")


def _need_identity() -> dict | None:
    if _sess().get("platform"):
        return None
    return {"error": "先调用 wb_register 声明你的 agent_id 和 platform，再读取看板。"}


def _t_register(a: dict) -> dict:
    _sess()["agent_id"] = a["agent_id"]
    _sess()["platform"] = a.get("platform") or "unknown"
    agent = bridge.register(
        a["agent_id"], _sess()["platform"], a.get("display_name"),
        a.get("capabilities"), "idle", a.get("note"),
    )
    return {"registered": True, "agent": agent, "app_reachable": bridge.probe_app(),
            "visibility": f"你只能读到 platform={_sess()['platform']} 的归档与档案；"
                          "其他平台的内容由用户手动拖给你看。"}


def _t_post_log(a: dict) -> dict:
    if not a.get("platform"):
        a["platform"] = _own_platform() or "unknown"
    if not a.get("agent_id") and _sess().get("agent_id"):
        a["agent_id"] = _sess()["agent_id"]
    if not a.get("platform") and a.get("agent_id"):
        with db.session() as conn:
            row = conn.execute("SELECT platform FROM agents WHERE agent_id=?", (a["agent_id"],)).fetchone()
        if row:
            a["platform"] = row["platform"]
    out = bridge.post_log({k: v for k, v in a.items() if v is not None})
    return {"archived": True, **out, "desktop_md": config.DESKTOP_EXPORT}


def _t_read_board(a: dict) -> dict:
    blocked = _need_identity()
    if blocked:
        return blocked
    return bridge.board(limit=int(a.get("limit") or 15), scope=_sess()["platform"])


def _t_find_agents(a: dict) -> dict:
    """Peers are discoverable by name only — enough to hand off to, not enough
    to learn what they have been doing."""
    agents = db.list_agents()
    me = _own_platform()

    def shape(x: dict, full: bool) -> dict:
        base = {"agent_id": x["agent_id"], "platform": x["platform"], "status": x["effective_status"]}
        if full:
            base.update({"display_name": x["display_name"], "capabilities": x["capabilities"], "note": x["note"]})
        return base

    return {
        "you": {"agent_id": _sess().get("agent_id"), "platform": me},
        "your_platform_peers": [shape(x, True) for x in agents if x["online"] and x["platform"] == me],
        "other_platforms_present": sorted({x["platform"] for x in agents if x["online"] and x["platform"] != me}),
        "note": "其他平台只暴露名字，看不到它们做了什么——那是用户手动交接的内容。",
    }


def _t_supersede(a: dict) -> dict:
    losers = a.get("loser_ids") or []
    if isinstance(losers, str):
        losers = [losers]
    if not a.get("winner_id") or not losers:
        return {"error": "需要 winner_id 和 loser_ids"}
    out = bridge.supersede(a["winner_id"], [str(x) for x in losers])
    if not out:
        return {"error": "winner_id 不存在"}
    return {"superseded": True, **out}


def _t_list_dossiers(a: dict) -> dict:
    blocked = _need_identity()
    if blocked:
        return blocked
    mine = [d for d in bridge.list_dossiers() if d["platform"] == _sess()["platform"]]
    return {"dossiers": mine,
            "note": "只有你自己平台的档案。别的平台档案由用户手动拖给对方读。"}


def _t_get_dossier(a: dict) -> dict:
    blocked = _need_identity()
    if blocked:
        return blocked
    wanted = a.get("platform") or _sess()["platform"]
    if wanted != _sess()["platform"]:
        return {"error": (f"拒绝：你以 {_sess()['platform']} 身份接入，不能读取 {wanted} 的内容产出区。"
                          "跨平台交接由用户把 handoff/<平台>.md 手动交给对方。")}
    out = bridge.get_dossier(wanted, bool(a.get("refresh")))
    if not out or not out.get("ok"):
        return {"error": (out or {}).get("error") or "档案不可用"}
    text = out.get("text")
    if not text:
        return {"archived_pending": True, **(out.get("meta") or {})}
    # Raw passthrough: an agent asked to read a document, not to parse JSON.
    return {"_raw_text": text, "meta": out.get("meta")}


_DISPATCH = {
    "wb_register": _t_register,
    "wb_post_log": _t_post_log,
    "wb_supersede": _t_supersede,
    "wb_list_dossiers": _t_list_dossiers,
    "wb_get_dossier": _t_get_dossier,
    "wb_read_board": _t_read_board,
    "wb_find_agents": _t_find_agents,
}

RESOURCES = [
    {"uri": "workbench://board", "name": "workbench-board",
     "description": "Live snapshot of agents, task queue and recent turn logs.", "mimeType": "application/json"},
    {"uri": "workbench://inbox", "name": "workbench-inbox",
     "description": f"Directory to drop .md/.json turn logs into: {config.INBOX_DIR}", "mimeType": "text/plain"},
]


def _result(rid, result) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}, ensure_ascii=False)


def _fault(rid, code: int, message: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}, ensure_ascii=False)


def _handle(msg: dict) -> str | None:
    method = msg.get("method")
    rid = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        want = params.get("protocolVersion") or PROTOCOL_FALLBACK
        return _result(rid, {
            "protocolVersion": want if want in SUPPORTED_PROTOCOLS else PROTOCOL_FALLBACK,
            "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
            "serverInfo": {"name": "agent-workbench", "version": "0.1.0"},
            "instructions": (
                "Agent Workbench: a per-platform work journal. Call wb_register at session "
                "start, wb_post_log after each completed turn, wb_supersede when something you "
                "posted turns out wrong, and wb_get_dossier to read your own platform's handoff "
                "document. You cannot read another platform's records — the user hands those "
                "over as a file."
            ),
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": TOOLS})
    if method == "resources/list":
        return _result(rid, {"resources": RESOURCES})
    if method == "resources/templates/list":
        return _result(rid, {"resourceTemplates": []})
    if method == "resources/read":
        uri = params.get("uri")
        if uri == "workbench://board":
            return _result(rid, {"contents": [{"uri": uri, "mimeType": "application/json",
                                               "text": json.dumps(bridge.board(), ensure_ascii=False, default=str)}]})
        if uri == "workbench://inbox":
            return _result(rid, {"contents": [{"uri": uri, "mimeType": "text/plain", "text": str(config.INBOX_DIR)}]})
        return _fault(rid, -32002, "unknown resource")
    if method == "tools/call":
        name = params.get("name")
        fn = _DISPATCH.get(name)
        if not fn:
            return _fault(rid, -32602, f"unknown tool: {name}")
        try:
            payload = fn(params.get("arguments") or {})
        except Exception as exc:  # surface tool failures as isError, not transport failures
            return _result(rid, _err(f"{type(exc).__name__}: {exc}"))
        if isinstance(payload, dict) and payload.get("error"):
            return _result(rid, _err(str(payload["error"])))
        if isinstance(payload, dict) and "_raw_text" in payload:
            extra = {k: v for k, v in payload.items() if k != "_raw_text"}
            return _result(rid, {"content": [{"type": "text", "text": payload["_raw_text"]}],
                                 "isError": False, "structuredContent": extra})
        return _result(rid, _text(payload) | {"structuredContent": payload})
    if rid is not None:
        return _fault(rid, -32601, f"method not found: {method}")
    return None


def main() -> int:
    """Serve MCP on stdio. Run as: python -m workbench.mcp_server"""
    config.ensure_dirs()
    db.init_db()
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"[mcp] agent-workbench stdio server up (pid {os.getpid()})", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(_fault(None, -32700, "parse error") + "\n")
            sys.stdout.flush()
            continue
        try:
            reply = handle(msg)
        except Exception as exc:
            reply = _fault(msg.get("id"), -32603, f"internal error: {exc}")
        if reply:
            sys.stdout.write(reply + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

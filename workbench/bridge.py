"""Bridge used by out-of-process clients (the MCP server, CLI helpers).

Prefers the running desktop app's HTTP API so board events fan out to the UI
immediately. Falls back to direct SQLite/file writes when the app is not
running, because an agent must never be blocked by a closed window.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config, core, db

_TIMEOUT = 4.0
_app_down = False


def _base_url() -> str | None:
    port = config.discover_port()
    return f"http://127.0.0.1:{port}" if port else None


def _call(method: str, path: str, body: dict | None = None) -> dict | None:
    """Return the parsed response, or None if the app is unreachable."""
    global _app_down
    base = _base_url()
    if not base or _app_down:
        return None
    url = base + path
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Workbench-Token", config.read_session_token())
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        _app_down = False
        return payload
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, TimeoutError):
        _app_down = True
        return None


def probe_app() -> bool:
    global _app_down
    _app_down = False
    out = _call("GET", "/health")
    return bool(out and out.get("ok"))


def register(agent_id, platform, display_name=None, capabilities=None, status="idle", note=None) -> dict:
    body = {"agent_id": agent_id, "platform": platform, "display_name": display_name,
            "capabilities": capabilities or [], "status": status, "note": note}
    out = _call("POST", "/api/agents", body)
    if out and out.get("ok"):
        return out["agent"]
    return db.upsert_agent(agent_id, platform, display_name, capabilities, status, note)


def post_log(payload: dict) -> dict:
    out = _call("POST", "/api/turn-log", payload)
    if out and out.get("ok"):
        return {"id": out["id"], "md_path": out["path"], "name_modified": out["name_modified"]}
    rec = core.ingest_turn_log(payload, source=payload.get("source") or "mcp")
    return {"id": rec["id"], "md_path": rec["md_path"], "name_modified": rec["name_modified"]}


def board(limit: int | None = None, scope: str | None = None) -> dict:
    """`scope` keeps one platform's content and strips the rest to bare names."""
    out = _call("GET", f"/api/board{'?scope=' + scope if scope else ''}")
    if out and out.get("ok"):
        out.pop("ok", None)
        if limit:
            out["logs"] = out["logs"][:limit]
        return out
    state = db.board_state(scope=scope)
    if limit:
        state["logs"] = state["logs"][:limit]
    return state


def heartbeat(agent_id, status=None) -> None:
    out = _call("POST", f"/api/agents/{agent_id}/heartbeat", {"agent_id": agent_id, "status": status})
    if out is None:
        db.heartbeat(agent_id, status)


def list_dossiers() -> list[dict]:
    out = _call("GET", "/api/dossiers")
    if out and out.get("ok"):
        return out["dossiers"]
    from . import dossier

    return dossier.platforms()


def get_dossier(platform: str, refresh: bool = False) -> dict | None:
    out = _call("GET", f"/api/dossiers/{platform}{'?refresh=1' if refresh else ''}")
    if out and out.get("ok"):
        return out
    from . import core, dossier

    if refresh:
        return {"ok": True, "platform": platform, "text": None, "meta": core.refresh_dossier(platform)}
    text = dossier.read(platform)
    return {"ok": bool(text), "platform": platform, "text": text,
            "meta": None, "error": None if text else "尚无该平台档案"}


def supersede(winner_id: str, loser_ids: list[str]) -> dict | None:
    out = _call("POST", "/api/supersede", {"winner_id": winner_id, "loser_ids": loser_ids})
    if out and out.get("ok"):
        return out["result"]
    from . import core

    return core.apply_supersede(winner_id, loser_ids)

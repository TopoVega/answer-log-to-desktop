"""Localhost HTTP API + SSE event stream.

Bound to 127.0.0.1 only. Mutating calls need the session token from
config.TOKEN_FILE, which stops another user on a shared machine from writing
into the board. The desktop UI is served from the same origin with the token
injected, so it authenticates without a login step.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config, core, db

_UI_CACHE: dict[str, tuple[bytes, str, float]] = {}

# MCP over HTTP: many clients share this process, so identity cannot be a module
# global. Keyed by the Mcp-Session-Id we hand out at `initialize`.
MCP_SESSIONS: dict[str, dict] = {}
_MCP_TTL = 6 * 3600


def _load_ui(name: str) -> tuple[bytes, str] | None:
    path = config.resource_path("ui", name)
    if not path.is_file():
        return None
    mtime = path.stat().st_mtime
    hit = _UI_CACHE.get(name)
    if hit and hit[2] == mtime:
        return hit[0], hit[1]
    data = path.read_bytes()
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    _UI_CACHE[name] = (data, ctype, mtime)
    return data, ctype


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentWorkbench/0.1"
    protocol_version = "HTTP/1.1"
    token: str = ""

    def log_message(self, fmt, *args):  # noqa: A003
        return

    # ---------- plumbing ----------
    def _send(self, code: int, body: bytes, ctype: str, extra=None, no_store=False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, core.json_dump(obj).encode("utf-8"), "application/json; charset=utf-8", no_store=True)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 8 * 1024 * 1024))
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "json" not in ctype:
            try:
                raw = json.dumps(json.loads(raw.decode("utf-8"))).encode("utf-8")
            except Exception:
                raw = json.dumps({"text": raw.decode("utf-8", "replace")}).encode("utf-8")
        try:
            data = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            # On Windows this is nearly always a shell that sent the body in the
            # local code page (GBK here), not a broken client. Name the fix, or
            # the agent re-runs the same command forever and calls it a mystery.
            raise core.IngestError(
                f"body 不是 UTF-8 JSON: {exc}. 中文别用 -d 内联传参；"
                "把请求体存成 UTF-8 文件，再 --data-binary @turn.json"
            ) from exc
        except Exception as exc:
            raise core.IngestError(f"body 不是合法 JSON: {exc}") from exc
        return data if isinstance(data, dict) else {"value": data}

    def _authorized(self, mutating: bool) -> bool:
        if not mutating:
            return True
        given = (self.headers.get("X-Workbench-Token")
                 or (parse_qs(urlparse(self.path).query).get("token") or [""])[0])
        if not given:
            # MCP clients that only take a URL usually speak Bearer, not a
            # custom header, so accept it too rather than making them impossible.
            auth = self.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                given = auth[7:].strip()
        return bool(given) and given == self.token

    # ---------- routing ----------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain", {"Access-Control-Allow-Origin": "*",
                                             "Access-Control-Allow-Headers": "Content-Type,X-Workbench-Token,Authorization,Mcp-Session-Id",
                                             "Access-Control-Expose-Headers": "Mcp-Session-Id",
                                             "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS"})

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        try:
            if path == "/api/events":
                return self._sse(parse_qs(url.query))
            url_path = path
            matched = self.ROUTES.get((method, url_path))
            params: dict[str, str] = {}
            if matched is None:
                matched, params = self._match(method, url_path)
            if matched is None and path.startswith("/api/"):
                return self._json(404, {"ok": False, "error": f"no route {method} {path}"})
            if matched is not None:
                if not self._authorized(method in ("POST", "DELETE")):
                    return self._json(401, {"ok": False, "error": "bad or missing X-Workbench-Token"})
                seg = [s for s in path.split("/") if s]
                return matched(self, seg, parse_qs(url.query), params)
            if method == "GET":
                return self._static(path)
            self._json(405, {"ok": False, "error": "method not allowed"})
        except core.IngestError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    # ---------- handlers ----------
    @staticmethod
    def _h_health(h: "Handler", seg, qs, params=None):
        h._json(200, core.health())

    # ---------- MCP over HTTP ----------
    def _mcp_session(self, create: bool, qs: dict) -> tuple[str | None, dict | None]:
        """This connection's identity, minted on `initialize`.

        Sessions expire so a client that connects once and never returns cannot
        pile up unbounded state in the app process.
        """
        sid = self.headers.get("Mcp-Session-Id")
        now = time.time()
        for dead in [k for k, v in MCP_SESSIONS.items() if now - v["seen"] > _MCP_TTL]:
            MCP_SESSIONS.pop(dead, None)
        ent = MCP_SESSIONS.get(sid) if sid else None
        if ent:
            ent["seen"] = now
            return sid, ent["session"]
        if not create:
            return None, None
        from . import mcp_server

        sess = mcp_server.new_session({"platform": (qs.get("platform") or [None])[0],
                                       "agent_id": (qs.get("agent_id") or [None])[0]})
        sid = secrets.token_urlsafe(16)
        MCP_SESSIONS[sid] = {"session": sess, "seen": now, "created": now}
        return sid, sess

    @staticmethod
    def _h_mcp_get(h: "Handler", seg, qs, params=None):
        # A spec-strict client GETs the endpoint expecting a server->client SSE
        # stream. We do not offer one, and the spec says answer 405 in that case
        # — a 200 with JSON would look like a stream that immediately ended.
        if "text/event-stream" in (h.headers.get("Accept") or ""):
            return h._send(405, b"", "text/plain", {"Allow": "POST, DELETE"})
        h._json(200, {"ok": True, "transport": "mcp-streamable-http", "endpoint": "/mcp",
                      "session_header": "Mcp-Session-Id",
                      "auth": f"X-Workbench-Token 或 Authorization: Bearer <见 {config.TOKEN_FILE}>",
                      "identity": "initialize 时可用 /mcp?platform=<平台>&agent_id=<id> 预绑定身份，"
                                  "也可以之后调 wb_register"})

    @staticmethod
    def _h_mcp_post(h: "Handler", seg, qs, params=None):
        from . import mcp_server

        msg = h._body()
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return h._json(400, {"ok": False, "error": "需要一个 JSON-RPC 2.0 消息体"})
        sid, sess = h._mcp_session(create=msg.get("method") == "initialize", qs=qs)
        if sess is None:
            # 404 is what the transport spec means here: "start a new session".
            return h._json(404, {"jsonrpc": "2.0", "id": msg.get("id"), "error": {
                "code": -32000, "message": "Mcp-Session-Id 缺失或已过期，请重新 initialize"}})
        try:
            reply = mcp_server.handle(msg, sess)
        except Exception as exc:
            reply = json.dumps({"jsonrpc": "2.0", "id": msg.get("id"),
                                "error": {"code": -32603, "message": f"internal error: {exc}"}},
                               ensure_ascii=False)
        if reply is None:  # a notification: accepted, nothing to answer
            return h._send(202, b"", "application/json", {"Mcp-Session-Id": sid})
        h._send(200, reply.encode("utf-8"), "application/json; charset=utf-8",
                {"Mcp-Session-Id": sid}, no_store=True)

    @staticmethod
    def _h_mcp_delete(h: "Handler", seg, qs, params=None):
        sid = h.headers.get("Mcp-Session-Id")
        h._json(200, {"ok": True, "terminated": MCP_SESSIONS.pop(sid, None) is not None})

    @staticmethod
    def _h_board(h: "Handler", seg, qs, params=None):
        scope = (qs.get("scope") or [None])[0]
        h._json(200, {"ok": True, **db.board_state(scope=scope)})

    @staticmethod
    def _h_logs(h: "Handler", seg, qs, params=None):
        h._json(200, {"ok": True, "logs": db.list_turn_logs(
            agent_id=(qs.get("agent_id") or [None])[0],
            limit=min(int((qs.get("limit") or ["50"])[0]), 500),
        )})

    @staticmethod
    def _h_log_one(h: "Handler", seg, qs, params):
        row = db.get_turn_log(params["id"])
        return h._json(200 if row else 404, {"ok": bool(row), **({"log": row} if row else {"error": "not found"})})

    @staticmethod
    def _h_post_turn_log(h: "Handler", seg, qs, params=None):
        rec = core.ingest_turn_log(h._body(), source="http")
        h._json(201, {"ok": True, "id": rec["id"], "path": rec["md_path"], "name_modified": bool(rec["name_modified"])})

    @staticmethod
    def _h_register(h: "Handler", seg, qs, params=None):
        b = h._body()
        if not (b.get("agent_id") or "").strip():
            raise core.IngestError("缺少 agent_id")
        a = db.upsert_agent(
            b["agent_id"], b.get("platform", "unknown"), b.get("display_name"),
            b.get("capabilities"), b.get("status", "idle"), b.get("note"),
        )
        core.publish("agent_registered", a)
        h._json(200, {"ok": True, "agent": a})

    @staticmethod
    def _h_heartbeat(h: "Handler", seg, qs, params):
        b = h._body()
        agent_id = b.get("agent_id") or params.get("id")
        db.heartbeat(agent_id, b.get("status"))
        h._json(200, {"ok": True})

    @staticmethod
    def _h_stats(h: "Handler", seg, qs, params=None):
        h._json(200, {"ok": True, **db.stats()})

    @staticmethod
    def _h_dossiers(h: "Handler", seg, qs, params=None):
        from . import dossier

        h._json(200, {"ok": True, "dossiers": dossier.platforms(),
                      "dir": str(config.HANDOFF_DIR)})

    @staticmethod
    def _h_dossier_one(h: "Handler", seg, qs, params):
        from . import core, dossier

        platform = params["id"]
        if (qs.get("refresh") or ["0"])[0] == "1":
            meta = core.refresh_dossier(platform)
            return h._json(200, {"ok": bool(meta), "platform": platform, "meta": meta,
                                 "text": dossier.read(platform)})
        text = dossier.read(platform)
        if text is None:
            return h._json(404, {"ok": False, "error": f"尚无 {platform} 档案"})
        h._send(200, text.encode("utf-8"), "text/markdown; charset=utf-8", no_store=True)

    @staticmethod
    def _h_setup(h: "Handler", seg, qs, params=None):
        from . import setup as setup_mod

        if (qs.get("format") or [""])[0] == "text":
            h._send(200, setup_mod.render_text().encode("utf-8"), "text/plain; charset=utf-8", no_store=True)
            return
        h._json(200, {"ok": True, **setup_mod.guide()})

    @staticmethod
    def _h_reset_preview(h: "Handler", seg, qs, params=None):
        from . import reset

        plat = (qs.get("platform") or [None])[0]
        agent = (qs.get("agent_id") or [None])[0]
        want_foreign = (qs.get("foreign") or ["1"])[0] != "0"
        h._json(200, {"ok": True,
                      "timeline": reset.preview_timeline(plat, agent, want_foreign),
                      "dossier": reset.preview_dossier([plat] if plat else None)})

    @staticmethod
    def _h_reset_timeline(h: "Handler", seg, qs, params=None):
        from . import reset

        b = h._body()
        if not b.get("confirm"):
            raise core.IngestError("需要 confirm:true —— 该操作会删除归档记录和对应的 md 文件")
        h._json(200, {"ok": True, "result": reset.reset_timeline(
            platform=b.get("platform"), agent_id=b.get("agent_id"),
            delete_foreign_desktop=bool(b.get("delete_foreign_desktop", True)))})

    @staticmethod
    def _h_reset_dossier(h: "Handler", seg, qs, params=None):
        from . import reset

        b = h._body()
        if not b.get("confirm"):
            raise core.IngestError("需要 confirm:true —— 该操作会清空内容产出区文档")
        if b.get("undo"):
            platform = b.get("platform")
            if not platform:
                raise core.IngestError("撤销重置需要 platform")
            out = reset.undo_dossier(platform)
            return h._json(200, {"ok": bool(out), **({"result": out} if out else {"error": "该平台没有重置水位线"})})
        plats = b.get("platforms") or None
        if isinstance(plats, str):
            plats = [plats]
        try:
            h._json(200, {"ok": True, "result": reset.reset_dossier(plats)})
        except ValueError as exc:
            raise core.IngestError(str(exc)) from exc

    @staticmethod
    def _h_shell(h: "Handler", seg, qs, params=None):
        from . import shell

        b = h._body()
        action = (qs.get("action") or [b.get("action") or ""])[0]
        path = b.get("path") or ""
        try:
            if action == "copy-file":
                out = shell.copy_file_to_clipboard(path)
            elif action == "reveal":
                out = shell.reveal_in_folder(path)
            elif action == "open-folder":
                out = shell.open_folder(path)
            else:
                raise core.IngestError(f"未知 shell 动作: {action}")
        except shell.ShellError as exc:
            raise core.IngestError(str(exc)) from exc
        h._json(200, {"ok": True, "action": action, "path": out})

    @staticmethod
    def _h_supersede(h: "Handler", seg, qs, params=None):
        b = h._body()
        winner, losers = b.get("winner_id"), b.get("loser_ids") or []
        if isinstance(losers, str):
            losers = [losers]
        if not winner or not losers:
            raise core.IngestError("需要 winner_id 和 loser_ids")
        try:
            out = core.apply_supersede(winner, [str(x) for x in losers])
        except ValueError as exc:
            raise core.IngestError(str(exc)) from exc
        if not out:
            raise core.IngestError("winner_id 不存在")
        h._json(200, {"ok": True, "result": out})

    ROUTES: dict = {}

    @staticmethod
    def _match(method: str, path: str) -> tuple | None:
        """Segment matcher. Platform names and task ids are not hex-only, so a
        regex on the trailing segment would miss `/api/dossiers/workbuddy` and
        platform names are not hex at all."""
        want = [s for s in path.split("/") if s]
        best = None
        for (m, pattern), handler in Handler.ROUTES.items():
            if m != method:
                continue
            tmpl = [s for s in pattern.split("/") if s]
            if len(tmpl) != len(want):
                continue
            params: dict[str, str] = {}
            ok = True
            for i, seg in enumerate(tmpl):
                if seg.startswith(":"):
                    params[seg[1:]] = want[i]
                elif seg != want[i]:
                    ok = False
                    break
            if ok:
                # Prefer the most literal match (fewest wildcards).
                wildcards = sum(1 for s in tmpl if s.startswith(":"))
                if best is None or wildcards < best[2]:
                    best = (handler, params, wildcards)
        if best is None:
            return None, {}
        return best[0], best[1]

    # ---------- static + SSE ----------
    _UI_ALLOW = {"index.html", "app.js", "style.css", "favicon.svg"}

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        if name not in self._UI_ALLOW:
            return self._json(404, {"ok": False, "error": f"unknown asset {name!r}"})
        asset = _load_ui(name)
        if not asset:
            return self._json(404, {"ok": False, "error": f"missing asset {name}"})
        data, ctype = asset
        # The token placeholder lives in app.js, not the HTML shell — substitute
        # whichever asset carries it so the UI never ships a raw placeholder.
        if b"__WB_TOKEN__" in data or b"__WB_PORT__" in data:
            real_port = self.server.server_address[1]
            data = (data.replace(b"__WB_TOKEN__", self.token.encode())
                        .replace(b"__WB_PORT__", str(real_port).encode()))
        self._send(200, data, ctype if "text" in ctype or "json" in ctype else f"{ctype}; charset=utf-8",
                   {"Access-Control-Allow-Origin": "*"})

    def _sse(self, qs: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        lst = core.subscribe()
        try:
            self.wfile.write(f"retry: 3000\n\n".encode())
            while True:
                evt = lst.get(timeout=15.0)
                if evt is None:
                    self.wfile.write(b": keepalive\n\n")
                else:
                    self.wfile.write(f"event: {evt['kind']}\ndata: {core.json_dump(evt)}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            core.unsubscribe(lst)


def _register_routes() -> None:
    Handler.ROUTES = {
        ("GET", "/health"): Handler._h_health,
        ("GET", "/api/board"): Handler._h_board,
        ("GET", "/api/logs"): Handler._h_logs,
        ("GET", "/api/logs/:id"): Handler._h_log_one,
        ("GET", "/api/stats"): Handler._h_stats,
        ("GET", "/api/dossiers"): Handler._h_dossiers,
        ("GET", "/api/setup"): Handler._h_setup,
        ("GET", "/api/reset/preview"): Handler._h_reset_preview,
        ("POST", "/api/reset/timeline"): Handler._h_reset_timeline,
        ("POST", "/api/reset/dossier"): Handler._h_reset_dossier,
        ("POST", "/api/shell"): Handler._h_shell,
        ("GET", "/api/dossiers/:id"): Handler._h_dossier_one,
        ("POST", "/api/supersede"): Handler._h_supersede,
        ("POST", "/api/turn-log"): Handler._h_post_turn_log,
        ("POST", "/api/agents"): Handler._h_register,
        ("POST", "/api/agents/:id/heartbeat"): Handler._h_heartbeat,
        ("GET", "/mcp"): Handler._h_mcp_get,
        ("POST", "/mcp"): Handler._h_mcp_post,
        ("DELETE", "/mcp"): Handler._h_mcp_delete,
    }


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # SO_REUSEADDR on Windows does not mean what it means on POSIX: it lets a
    # second process bind a port that is already in use, so a "fallback to an
    # ephemeral port" path silently hijacks the live instance instead. Only
    # enable it where it actually just avoids TIME_WAIT.
    allow_reuse_address = sys.platform != "win32"

    def handle_error(self, request, client_address):  # noqa: ARG002
        exc = traceback.format_exc()
        if "BrokenPipe" in exc or "ConnectionReset" in exc:
            return
        traceback.print_exc()


def stop(httpd: Server) -> None:
    """Shut down and retire our port pointer.

    A leftover port.json pointing at a dead process makes other processes
    (the MCP bridge, the CLI) believe the app is live; they then split their
    writes between a phantom HTTP target and the real database.
    """
    httpd.shutdown()
    httpd.server_close()
    try:
        if config.PORT_FILE.exists():
            import json as _json

            if _json.loads(config.PORT_FILE.read_text(encoding="utf-8")).get("pid") == os.getpid():
                config.PORT_FILE.unlink()
    except Exception:
        pass


def serve(port: int | None = None, host: str = "127.0.0.1") -> tuple[Server, threading.Thread]:
    """port=None → preferred port, falling back to any free one.
    port=0 → always take an ephemeral free port (what tests want)."""
    _register_routes()
    Handler.token = config.read_session_token()
    if port is None:
        try:
            httpd = Server((host, config.DEFAULT_PORT), Handler)
        except OSError:
            httpd = Server((host, 0), Handler)
    else:
        httpd = Server((host, port), Handler)
    actual = httpd.server_address[1]
    config.publish_port(actual)
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True, name="wb-http")
    t.start()
    return httpd, t

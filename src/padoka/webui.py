"""
Web UI for Padoka.

A small, dependency-free (stdlib only) HTTP server that lets you:
  * view recent messages per channel (in-memory backlog),
  * follow a live message stream (Server-Sent Events),
  * post PRIVMSG/NOTICE to a channel or nick,
  * join/part channels, change nick, run raw IRC commands,
  * tail the on-disk log file for a channel,
  * reload / reconnect / quit the proxy.

It is enabled via the ``webui`` section of the YAML config and started as
a thread by :class:`~padoka.server.PDKServer`.  The server feeds it every
upstream message through :meth:`WebUI.on_message`.

Security: when ``webui.pass`` is set, every ``/api/*`` route (and the SSE
stream) requires the token — supplied as an ``Authorization: Bearer`` header
or a ``?token=`` query param (needed for ``EventSource``).
"""

from __future__ import annotations

import hmac
import json
import os
import queue
import re
import threading
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from . import VERSION
from .irc.message import IRCMessage, Command

__all__ = ["WebUI"]

# IRC command -> is it a channel-scoped event?
_CHAN_CMD = re.compile(r"^[#&+!%]")


class _QuietHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that doesn't dump thread tracebacks to stderr."""

    daemon_threads = True

    def handle_error(self, request, client_address):  # noqa: D102
        # Client disconnects on SSE / broken pipes are normal; stay quiet.
        pass


class WebUI:
    def __init__(self, config, manager):
        self.config = config
        self.manager = manager
        self.host: str = getattr(config, "webui_host", "127.0.0.1")
        self.port: int = int(getattr(config, "webui_port", 8080))
        self.buffer_size: int = int(getattr(config, "webui_buffer", 200))

        self._buffers: Dict[str, deque] = {}
        # listeners: (queue_id, channel_filter) -> queue
        self._listeners: Dict[Tuple[int, str], queue.Queue] = {}
        self._lock = threading.Lock()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ---------------------------------------------------------------- config
    def _password(self) -> str:
        return getattr(self.config, "webui_pass", "") or ""

    def _title(self) -> str:
        return getattr(self.config, "webui_title", "Padoka WebUI")

    def _dlog(self, msg: str) -> None:
        try:
            self.config.logger.dlog(msg)
        except Exception:
            pass

    # ------------------------------------------------------------------ life
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        handler = _make_handler(self)
        try:
            self._httpd = _QuietHTTPServer((self.host, self.port), handler)
        except OSError as e:
            self._running = False
            self._dlog("webui: cannot bind %s:%s (%s)" % (self.host, self.port, e))
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True, name="webui")
        self._thread.start()
        self._slog("WebUI listening on http://%s:%s/" % (self.host, self.port))

    def _slog(self, msg: str) -> None:
        try:
            self.config.logger.slog(msg)
        except Exception:
            pass

    def stop(self) -> None:
        self._running = False
        # wake any SSE handlers so they can exit cleanly
        with self._lock:
            for que in list(self._listeners.values()):
                que.put(None)
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    # ------------------------------------------------------------- messaging
    def on_message(self, msg: IRCMessage) -> None:
        """Called by the server for every upstream message."""
        try:
            if msg.command.isdigit():
                # numeric replies (NAMES, ISUPPORT, ...) are noise for a chat view
                return
            rec = self._normalize(msg)
        except Exception:
            return
        key = rec["channel"]
        with self._lock:
            buf = self._buffers.get(key)
            if buf is None:
                buf = self._buffers[key] = deque(maxlen=self.buffer_size)
            buf.append(rec)
        self._emit(rec)

    def _emit(self, rec: dict) -> None:
        ch = rec["channel"]
        with self._lock:
            items = list(self._listeners.items())
        if not items:
            return
        for (lid, lch), que in items:
            if lch in ("", ch):
                try:
                    que.put(json.dumps(rec, ensure_ascii=False))
                except Exception:
                    pass

    def _nick_of(self, msg: IRCMessage) -> str:
        m = re.match(r"^([^!]+)\!?", msg.prefix or "")
        if m:
            return m.group(1)
        return self.manager.state.nick() or ""

    def _channel_key(self, msg: IRCMessage) -> str:
        cmd = msg.command
        p = msg.params
        if cmd in ("PRIVMSG", "NOTICE"):
            target = p[0] if p else ""
            return target if _CHAN_CMD.match(target) else "pm"
        if cmd in ("JOIN", "PART", "TOPIC", "KICK", "MODE"):
            target = p[0] if p else ""
            return target if _CHAN_CMD.match(target) else "system"
        return "system"

    def _display(self, msg: IRCMessage) -> str:
        cmd = msg.command
        p = msg.params
        nick = self._nick_of(msg)
        if cmd == "PRIVMSG":
            return "<%s> %s" % (nick, p[1] if len(p) > 1 else "")
        if cmd == "NOTICE":
            return "*%s* %s" % (nick, p[1] if len(p) > 1 else "")
        if cmd == "JOIN":
            return "+ %s" % nick
        if cmd == "PART":
            return "- %s (%s)" % (nick, p[1] if len(p) > 1 else "")
        if cmd == "QUIT":
            return "- %s (QUIT: %s)" % (nick, p[0] if p else "")
        if cmd == "NICK":
            return "* %s is now known as %s" % (nick, p[0] if p else "")
        if cmd == "KICK":
            return "! %s kicked %s (%s)" % (
                nick, p[1] if len(p) > 1 else "", p[2] if len(p) > 2 else "")
        if cmd == "MODE":
            return "* %s MODE %s %s" % (
                nick, p[0] if p else "", " ".join(p[1:]) if len(p) > 1 else "")
        if cmd == "TOPIC":
            return "* TOPIC %s (by %s)" % (p[1] if len(p) > 1 else "", nick)
        return msg.to_s().strip()

    def _normalize(self, msg: IRCMessage) -> dict:
        now = datetime.now()
        return {
            "ts": now.timestamp(),
            "time": now.strftime("%H:%M:%S"),
            "command": msg.command,
            "nick": self._nick_of(msg),
            "channel": self._channel_key(msg),
            "text": msg.params[1] if len(msg.params) > 1 else "",
            "raw": self._display(msg),
        }

    # --------------------------------------------------------------- queries
    def status(self) -> dict:
        conn = getattr(self.manager, "_conn", None)
        st = self.manager.state
        return {
            "connected": self.manager.connected,
            "nick": st.nick(),
            "server": ("%s:%s" % (conn.host, conn.port)) if conn else None,
            "clients": self.manager.client_count(),
            "version": VERSION,
            "pass_required": bool(self._password()),
            "channels": self.channels(),
        }

    def channels(self) -> List[dict]:
        out: List[dict] = []
        st = self.manager.state
        # The server thread mutates current_channels / member concurrently;
        # a dict can raise RuntimeError if resized mid-iteration, so snapshot
        # defensively and fall back to empty on contention.
        try:
            items = list(st.current_channels.items())
        except RuntimeError:
            return out
        for ccn, cs in items:
            try:
                names = cs.names()
            except RuntimeError:
                names = []
            out.append({
                "name": cs.name,
                "ccn": ccn,
                "topic": cs.topic,
                "members": names,
                "member_count": len(names),
            })
        return out

    def recent(self, channel: str, limit: int) -> List[dict]:
        limit = max(1, int(limit))
        with self._lock:
            buf = self._buffers.get(channel)
            if buf:
                return list(buf)[-limit:]
        return []

    def logfile_tail(self, channel: str, limit: int) -> List[str]:
        st = self.manager.state
        raw = channel
        try:
            items = list(st.current_channels.items())
        except RuntimeError:
            items = []
        for ccn, cs in items:
            if ccn == channel or cs.name == channel:
                raw = cs.name
                break
        tmpl = (self.config.default_log or {}).get(
            "file", "${setting_name}-${channel_name}/%y%m%d.log")
        try:
            base = self.config.make_logfilename(tmpl, raw, None)
        except Exception:
            return []
        logdir = os.path.abspath(self.config.log_dir or "./log")
        path = os.path.abspath(os.path.join(logdir, base))
        if not path.startswith(logdir + os.sep):
            return []
        if not os.path.isfile(path):
            return []
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return []
        return [l.rstrip("\n") for l in lines[-limit:]]

    # ----------------------------------------------------------- subscriber
    def _subscribe(self, que: "queue.Queue", channel: str) -> Tuple[int, str]:
        key = (id(que), channel)
        with self._lock:
            self._listeners[key] = que
        return key

    def _unsubscribe(self, key: Tuple[int, str]) -> None:
        with self._lock:
            self._listeners.pop(key, None)

    # ------------------------------------------------------------- actions
    def send_message(self, data: dict) -> Tuple[int, dict]:
        target = data.get("channel") or data.get("target") or ""
        text = data.get("text", "")
        kind = (data.get("kind") or "privmsg").lower()
        if not target or not text:
            return 400, {"ok": False, "error": "channel and text required"}
        if not self.manager.connected:
            return 503, {"ok": False, "error": "not connected to IRC"}
        cmd = Command.privmsg(target, text) if kind == "privmsg" else Command.notice(target, text)
        try:
            self.manager.send_to_server(cmd)
            # Reflect our own message in the WebUI buffers/SSE (the upstream does
            # not echo our own nick), then relay to the other attached IRC
            # clients (irssi etc.) + log, mirroring a bot's send_notice.
            self.on_message(cmd)
            self.manager.send_to_clients_otherwise(cmd, None)
            return 200, {"ok": True}
        except Exception as e:
            return 500, {"ok": False, "error": str(e)}

    def join(self, data: dict) -> Tuple[int, dict]:
        ch = data.get("channel", "")
        if not ch:
            return 400, {"ok": False, "error": "channel required"}
        if not self.manager.connected:
            return 503, {"ok": False, "error": "not connected to IRC"}
        try:
            self.manager.join_to_channel(ch)
            return 200, {"ok": True}
        except Exception as e:
            return 500, {"ok": False, "error": str(e)}

    def part(self, data: dict) -> Tuple[int, dict]:
        ch = data.get("channel", "")
        if not ch:
            return 400, {"ok": False, "error": "channel required"}
        if not self.manager.connected:
            return 503, {"ok": False, "error": "not connected to IRC"}
        try:
            self.manager.send_to_server(Command.part(ch))
            return 200, {"ok": True}
        except Exception as e:
            return 500, {"ok": False, "error": str(e)}

    def set_nick(self, data: dict) -> Tuple[int, dict]:
        nick = data.get("nick", "")
        if not nick:
            return 400, {"ok": False, "error": "nick required"}
        if not self.manager.connected:
            return 503, {"ok": False, "error": "not connected to IRC"}
        try:
            self.manager.send_to_server(Command.nick(nick))
            return 200, {"ok": True}
        except Exception as e:
            return 500, {"ok": False, "error": str(e)}

    def raw_command(self, data: dict) -> Tuple[int, dict]:
        raw = (data.get("command") or "").strip()
        if not raw:
            return 400, {"ok": False, "error": "command required"}
        if not self.manager.connected:
            return 503, {"ok": False, "error": "not connected to IRC"}
        try:
            m = IRCMessage.parse(raw)
        except ValueError as e:
            return 400, {"ok": False, "error": str(e)}
        try:
            self.manager.send_to_server(m)
            return 200, {"ok": True}
        except Exception as e:
            return 500, {"ok": False, "error": str(e)}

    def reload(self, data: dict) -> Tuple[int, dict]:
        self.manager.invoke_event("reload_config")
        return 200, {"ok": True}

    def reconnect(self, data: dict) -> Tuple[int, dict]:
        self.manager.invoke_event("reconnect_to_server")
        return 200, {"ok": True}

    def quit(self, data: dict) -> Tuple[int, dict]:
        self.manager.invoke_event("quit_program")
        return 200, {"ok": True}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
def _token_from(handler) -> str:
    # query param first (works for EventSource), then Authorization header
    try:
        q = parse_qs(urlsplit(handler.path).query)
        if q.get("token"):
            return q["token"][0]
    except Exception:
        pass
    auth = handler.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _make_handler(webui: WebUI):
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "padoka-webui/" + VERSION

        # -- plumbing -----------------------------------------------------
        def log_message(self, fmt, *args):  # quiet: route to debug log
            try:
                webui._dlog("http " + (fmt % args))
            except Exception:
                pass

        def _auth_ok(self) -> bool:
            pw = webui._password()
            if not pw:
                return True
            return hmac.compare_digest(_token_from(self), pw)

        def _send(self, code: int, ctype: str, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj) -> None:
            self._send(200, "application/json",
                       json.dumps(obj, ensure_ascii=False))

        def _read_body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            if length <= 0:
                return b""
            length = min(length, 1_000_000)  # 1MB cap (JSON payloads are tiny)
            return self.rfile.read(length)

        def _cors_preflight(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.end_headers()

        # -- GET ----------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            path = parsed.path
            q = parse_qs(parsed.query)
            if path == "/" :
                self._serve_page()
            elif path == "/api/status":
                if not self._auth_ok():
                    return self._send(401, "application/json",
                                       '{"error":"unauthorized"}')
                self._json(webui.status())
            elif path == "/api/channels":
                if not self._auth_ok():
                    return self._send(401, "application/json",
                                       '{"error":"unauthorized"}')
                self._json({"channels": webui.channels()})
            elif path == "/api/messages":
                if not self._auth_ok():
                    return self._send(401, "application/json",
                                       '{"error":"unauthorized"}')
                ch = (q.get("channel") or [""])[0]
                limit = _to_int((q.get("limit") or ["200"])[0], 200)
                self._json({"channel": ch, "messages": webui.recent(ch, limit)})
            elif path == "/api/logfile":
                if not self._auth_ok():
                    return self._send(401, "application/json",
                                       '{"error":"unauthorized"}')
                ch = (q.get("channel") or [""])[0]
                limit = _to_int((q.get("limit") or ["200"])[0], 200)
                self._json({"channel": ch, "lines": webui.logfile_tail(ch, limit)})
            elif path == "/api/events":
                self._serve_sse(q)
            else:
                self._send(404, "application/json", '{"error":"not found"}')

        # -- POST ---------------------------------------------------------
        def do_POST(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            path = parsed.path
            if not self._auth_ok():
                return self._send(401, "application/json",
                                  '{"error":"unauthorized"}')
            body = self._read_body()
            try:
                data = json.loads(body) if body else {}
            except (ValueError, OSError):
                return self._send(400, "application/json",
                                  '{"error":"bad json"}')
            dispatch: Dict[str, Callable[[dict], Tuple[int, dict]]] = {
                "/api/send": webui.send_message,
                "/api/join": webui.join,
                "/api/part": webui.part,
                "/api/nick": webui.set_nick,
                "/api/command": webui.raw_command,
                "/api/reload": webui.reload,
                "/api/reconnect": webui.reconnect,
                "/api/quit": webui.quit,
            }
            fn = dispatch.get(path)
            if fn is None:
                return self._send(404, "application/json",
                                  '{"error":"not found"}')
            code, payload = fn(data)
            self._send(code, "application/json",
                       json.dumps(payload, ensure_ascii=False))

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._cors_preflight()

        # -- page / sse ---------------------------------------------------
        def _serve_page(self) -> None:
            html = _INDEX_HTML.replace("__TITLE__", webui._title())
            self._send(200, "text/html", html)

        def _serve_sse(self, q) -> None:
            if not self._auth_ok():
                return self._send(401, "application/json",
                                  '{"error":"unauthorized"}')
            channel = (q.get("channel") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            que: "queue.Queue" = queue.Queue()
            key = webui._subscribe(que, channel)
            try:
                self.wfile.write(b"retry: 3000\n\n")
                self.wfile.flush()
                while webui._running:
                    try:
                        ev = que.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    if ev is None:
                        break
                    self.wfile.write(("data: %s\n\n" % ev).encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                webui._unsubscribe(key)

    return _Handler


def _to_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Embedded single-page UI (vanilla HTML/CSS/JS, no external assets)
# ---------------------------------------------------------------------------
_INDEX_HTML = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#11151c;--panel:#1a212b;--panel2:#212a36;--fg:#d7dee8;--dim:#7d8aa0;
--accent:#4da3ff;--line:#2b3542;--ok:#3ddc84;--bad:#ff6b6b;--notice:#c792ea;}
*{box-sizing:border-box;}
html,body{height:100%;margin:0;}
body{background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans JP",sans-serif;}
header{display:flex;align-items:center;gap:12px;padding:10px 14px;
background:var(--panel);border-bottom:1px solid var(--line);}
.title{font-weight:600;font-size:15px;}
.status{color:var(--dim);font-size:12px;margin-left:auto;display:flex;gap:10px;align-items:center;}
.dot{width:9px;height:9px;border-radius:50%;background:var(--bad);display:inline-block;}
.dot.on{background:var(--ok);}
.main{display:flex;height:calc(100% - 46px);}
aside{width:220px;background:var(--panel);border-right:1px solid var(--line);
overflow-y:auto;flex-shrink:0;}
aside h3{margin:10px 12px 4px;font-size:11px;color:var(--dim);text-transform:uppercase;}
.chan{display:block;width:100%;text-align:left;background:none;border:0;color:var(--fg);
padding:7px 14px;cursor:pointer;font-size:13px;}
.chan:hover{background:var(--panel2);}
.chan.active{background:var(--panel2);color:var(--accent);border-left:2px solid var(--accent);}
.chan .cnt{float:right;color:var(--dim);font-size:11px;}
.chat{display:flex;flex-direction:column;flex:1;min-width:0;}
.chat-header{padding:8px 14px;background:var(--panel);border-bottom:1px solid var(--line);
display:flex;gap:10px;align-items:center;flex-wrap:wrap;}
.chat-header .name{font-weight:600;}
.chat-header .topic{color:var(--dim);font-size:12px;flex:1;min-width:120px;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap;}
.chat-header button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
border-radius:5px;padding:3px 8px;cursor:pointer;font-size:12px;}
.chat-header button:hover{border-color:var(--accent);}
.messages{flex:1;overflow-y:auto;padding:8px 14px;word-wrap:break-word;}
.line{padding:2px 0;border-bottom:1px solid rgba(255,255,255,.02);}
.line .time{color:var(--dim);font-size:11px;margin-right:8px;font-variant-numeric:tabular-nums;}
.line .nick{color:var(--accent);font-weight:600;}
.line.notice .nick{color:var(--notice);}
.line .event{color:var(--dim);}
.line.sys .raw{color:var(--dim);}
.send{display:flex;gap:8px;padding:10px 14px;background:var(--panel);border-top:1px solid var(--line);}
.send input{flex:1;background:var(--panel2);border:1px solid var(--line);color:var(--fg);
border-radius:6px;padding:9px 11px;font-size:14px;}
.send input:focus{outline:none;border-color:var(--accent);}
.send button{background:var(--accent);color:#04121f;border:0;border-radius:6px;
padding:0 18px;font-weight:600;cursor:pointer;}
.send button:disabled{opacity:.5;cursor:default;}
.err{color:var(--bad);font-size:12px;margin:4px 0;}
.hidden{display:none;}
.login{position:fixed;inset:0;background:var(--bg);display:flex;
align-items:center;justify-content:center;}
.login-box{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:28px 32px;width:320px;}
.login-box h2{margin:0 0 6px;font-size:16px;}
.login-box p{color:var(--dim);font-size:12px;margin:0 0 16px;}
.login-box input{width:100%;background:var(--panel2);border:1px solid var(--line);
color:var(--fg);border-radius:6px;padding:9px 11px;font-size:14px;margin-bottom:10px;}
.login-box button{width:100%;background:var(--accent);color:#04121f;border:0;
border-radius:6px;padding:9px;font-weight:600;cursor:pointer;}
</style>
</head>
<body>
<div id="app" class="hidden">
  <header>
    <div class="title">__TITLE__</div>
    <div class="status">
      <span id="statustext">…</span>
      <span class="dot" id="dot"></span>
    </div>
  </header>
  <div class="main">
    <aside>
      <h3>Channels</h3>
      <div id="chanlist"></div>
    </aside>
    <section class="chat">
      <div class="chat-header">
        <span class="name" id="channame">-</span>
        <span class="topic" id="chantopic"></span>
        <span style="flex:1"></span>
        <button id="btnlog" title="ログファイルを再読込">ログ読込</button>
        <button id="btnpart">Part</button>
        <button id="btnreload">Reload</button>
      </div>
      <div class="messages" id="messages"></div>
      <form class="send" id="sendform">
        <input id="msginput" autocomplete="off"
          placeholder="メッセージを入力（ENTER で送信）">
        <button type="submit" id="sendbtn">送信</button>
      </form>
    </section>
  </div>
</div>
<div id="login" class="login">
  <div class="login-box">
    <h2>__TITLE__</h2>
    <p>トークン（webui.pass）を入力してください</p>
    <input id="tokeninput" autocomplete="off">
    <button id="loginbtn">ログイン</button>
    <div id="loginerr" class="err"></div>
  </div>
</div>
<script>
"use strict";
const TOKEN_KEY = "padoka_token";
let token = sessionStorage.getItem(TOKEN_KEY) || "";
let current = null;      // current channel key ("" = system/all)
let es = null;           // EventSource
let statusTimer = null;

const $ = (id) => document.getElementById(id);
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}

async function api(path, opts){
  opts = opts || {};
  const headers = Object.assign({}, opts.headers || {});
  if (token) headers["Authorization"] = "Bearer " + token;
  if (opts.body) headers["Content-Type"] = "application/json";
  const r = await fetch(path, Object.assign({}, opts, {headers}));
  if (r.status === 401){ showLogin(); throw new Error("unauthorized"); }
  return r.json();
}
async function post(path, body){
  return api(path, {method:"POST", body: JSON.stringify(body || {})});
}

function showLogin(){ $("login").classList.remove("hidden"); $("app").classList.add("hidden"); }
function hideLogin(){ $("login").classList.add("hidden"); $("app").classList.remove("hidden"); }

function renderStatus(st){
  const dot = $("dot");
  dot.classList.toggle("on", !!st.connected);
  $("statustext").textContent =
    (st.connected ? "接続中 " : "未接続 ") +
    (st.nick ? st.nick : "") +
    (st.server ? " @ " + st.server : "");
}

function renderChannels(st){
  const list = $("chanlist");
  list.innerHTML = "";
  addChan(list, "system", "system / 全イベント", 0);
  (st.channels||[]).forEach(c=>addChan(list, c.name, c.name, c.member_count));
}
function addChan(parent, key, label, cnt){
  const b = document.createElement("button");
  b.className = "chan" + (key===current ? " active" : "");
  b.innerHTML = esc(label) + (cnt ? ' <span class="cnt">'+cnt+'</span>' : "");
  b.onclick = ()=>selectChannel(key);
  parent.appendChild(b);
}

function renderLine(rec){
  const div = document.createElement("div");
  div.className = "line " + (rec.command||"").toLowerCase();
  if (rec.command === "PRIVMSG" || rec.command === "NOTICE"){
    const nickcls = rec.command === "NOTICE" ? "nick notice" : "nick";
    div.innerHTML = '<span class="time">'+esc(rec.time)+'</span>' +
      '<span class="'+nickcls+'">'+esc(rec.nick)+'</span> '+esc(rec.text);
  } else {
    div.innerHTML = '<span class="time">'+esc(rec.time)+'</span>' +
      '<span class="event">'+esc(rec.raw)+'</span>';
  }
  $("messages").appendChild(div);
  scrollBottom();
}
function scrollBottom(){ const m=$("messages"); m.scrollTop = m.scrollHeight; }

function closeEs(){ if (es){ try{es.close();}catch(e){} es=null; } }

async function selectChannel(ch){
  current = ch;
  closeEs();
  $("messages").innerHTML = "";
  $("channame").textContent = ch || "system";
  $("chantopic").textContent = "";
  document.querySelectorAll(".chan").forEach(e=>e.classList.remove("active"));
  try{
    const data = await api("/api/messages?channel="+encodeURIComponent(ch)+"&limit=200");
    (data.messages||[]).forEach(renderLine);
  }catch(e){}
  // live stream
  const url = "/api/events?channel="+encodeURIComponent(ch)+"&token="+encodeURIComponent(token);
  es = new EventSource(url);
  es.onmessage = (e)=>{ try{ renderLine(JSON.parse(e.data)); }catch(_){ } };
  es.onerror = ()=>{ /* EventSource auto-reconnects */ };
  scrollBottom();
}

async function refresh(){
  try{
    const st = await api("/api/status");
    renderStatus(st);
    renderChannels(st);
    // fill topic for current
    const c = (st.channels||[]).find(x=>x.name===current);
    if (c) $("chantopic").textContent = c.topic || "";
  }catch(e){}
}

function defaultChannel(){
  // pick the first real channel; none -> system
  return null; // resolved after first status
}

async function init(){
  await refresh();
  // choose default channel from status
  if (!current){
    let st = await api("/api/status").catch(()=>null);
    current = (st && st.channels && st.channels.length) ? st.channels[0].name : "system";
  }
  await selectChannel(current);
  if (statusTimer) clearInterval(statusTimer);
  statusTimer = setInterval(refresh, 15000);
  scrollBottom();
}

function doLogin(){
  const t = $("tokeninput").value.trim();
  const headers = {"Authorization":"Bearer "+t};
  fetch("/api/status",{headers}).then(r=>{
    if (r.status===401){ $("loginerr").textContent="トークンが正しくありません"; return; }
    token = t; sessionStorage.setItem(TOKEN_KEY, token);
    $("loginerr").textContent=""; hideLogin(); init();
  }).catch(()=>{ $("loginerr").textContent="接続できません"; });
}

$("sendform").addEventListener("submit", async (e)=>{
  e.preventDefault();
  const text = $("msginput").value.trim();
  if (!text || !current) return;
  $("sendbtn").disabled = true;
  try{
    await post("/api/send", {channel: current, text: text, kind: "privmsg"});
    $("msginput").value = "";
  }catch(err){}
  $("sendbtn").disabled = false;
  $("msginput").focus();
});
$("btnpart").onclick = async ()=>{ if(current) await post("/api/part",{channel:current}).catch(()=>{}); };
$("btnreload").onclick = async ()=>{ await post("/api/reload",{}).catch(()=>{}); setTimeout(refresh,800); };
$("btnlog").onclick = async ()=>{
  if(!current) return;
  try{
    const data = await api("/api/logfile?channel="+encodeURIComponent(current)+"&limit=300");
    const box = document.createElement("div");
    (data.lines||[]).forEach(l=>{
      const d=document.createElement("div"); d.className="line sys";
      d.innerHTML='<span class="time">log</span> <span class="raw">'+esc(l)+'</span>';
      box.appendChild(d);
    });
    if ((data.lines||[]).length){ $("messages").prepend(box); }
  }catch(e){}
};
$("loginbtn").onclick = doLogin;
$("tokeninput").addEventListener("keydown", (e)=>{ if(e.key==="Enter") doLogin(); });

// boot
api("/api/status").then((st)=>{ hideLogin(); init(); }).catch(()=>showLogin());
</script>
</body>
</html>
"""

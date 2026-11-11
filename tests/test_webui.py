# -*- coding: utf-8 -*-
"""
Unit tests for the WebUI (src/padoka/webui.py).

Covers:
  * message normalization / channel routing (on_message -> buffers)
  * status / channels queries
  * actions: send_message / join / part / raw_command (with a fake manager)
  * auth (password required)
  * a live HTTP smoke test: GET / , GET /api/status, POST /api/send,
    GET /api/events (SSE content-type)
"""

import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

# Bypass any ambient HTTP proxy so tests always hit 127.0.0.1 directly.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url):
    return _OPENER.open(url, timeout=5)


def _post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST")
    return _OPENER.open(req, timeout=5)

from padoka.webui import WebUI            # noqa: E402
from padoka.irc.message import IRCMessage  # noqa: E402
from padoka.state import ChannelState     # noqa: E402


_passed = 0
_failed = 0


def check(desc, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print("  ok  - %s" % desc)
    else:
        _failed += 1
        print("  FAIL- %s" % desc)


def section(name):
    print("\n== %s ==" % name)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---- fakes ----------------------------------------------------------------
class FakeLog:
    def slog(self, m, ns=False):
        pass
    def dlog(self, m):
        pass


class FakeState:
    def __init__(self):
        self.current_channels = {}
        self._nick = "bot"
    def nick(self):
        return self._nick


class FakeManager:
    def __init__(self, connected=True):
        self.state = FakeState()
        self.connected = connected
        self.sent = []
        self.relayed = []
        self.joined = []
        self.events = []
        self._conn = type("C", (), {"host": "irc.example", "port": 6697})()

    def client_count(self):
        return 1

    def send_to_server(self, m):
        self.sent.append(m)

    def send_to_clients_otherwise(self, m, elt):
        self.relayed.append(m)

    def join_to_channel(self, ch):
        self.joined.append(ch)

    def invoke_event(self, name, *args):
        self.events.append((name,) + args)


class FakeConfig:
    def __init__(self):
        self.logger = FakeLog()
        self.default_log = {"file": "${setting_name}-${channel_name}/%y%m%d.log"}
        self.log_dir = tempfile.mkdtemp(prefix="webui_log_")
        self.setting_name = "webui"
        self.webui_pass = ""
        self.webui_title = "Test WebUI"

    def identical_channel_name(self, ch):
        return ch.lower().translate(str.maketrans("[]\\~", "{}|^"))

    def canonical_channel_name(self, ch):
        return self.identical_channel_name(ch)

    def make_logfilename(self, tmpl, rch, cn):
        if not cn:
            cn = rch.lower()
            cn = cn.replace("#", "CS-").replace("&", "CA-").translate(
                str.maketrans("*:/", "__I"))
        return tmpl.replace("${setting_name}", self.setting_name).replace(
            "${channel_name}", cn).replace("%y%m%d.log", "20261017.log")


def make_webui(config=None, manager=None, port=None):
    config = config or FakeConfig()
    manager = manager or FakeManager()
    w = WebUI(config, manager)
    w.port = port or free_port()
    return w


# ---------------------------------------------------------------------------
section("on_message normalization / routing")
w = make_webui()
w.on_message(IRCMessage.parse(":alice!a@h PRIVMSG #chan :hello world"))
recent = w.recent("#chan", 100)
check("privmsg buffered to #chan", len(recent) == 1)
check("privmsg text", recent[0]["text"] == "hello world")
check("privmsg nick", recent[0]["nick"] == "alice")
check("privmsg display", recent[0]["raw"] == "<alice> hello world")
check("privmsg command", recent[0]["command"] == "PRIVMSG")

w.on_message(IRCMessage.parse(":bob!b@h NOTICE #chan :note here"))
check("notice buffered to #chan", len(w.recent("#chan", 100)) == 2)
check("notice display", w.recent("#chan", 100)[1]["raw"] == "*bob* note here")

# PM goes to "pm"
w.on_message(IRCMessage.parse(":carol!c@h PRIVMSG dave :psst"))
check("pm routed to pm", len(w.recent("pm", 100)) == 1)

# join routed to channel
w.on_message(IRCMessage.parse(":eve!e@h JOIN #chan"))
check("join routed to #chan", len(w.recent("#chan", 100)) == 3)
check("join display", w.recent("#chan", 100)[2]["raw"] == "+ eve")

# numeric skipped
w.on_message(IRCMessage.parse(":server 353 bot = #chan :bot alice"))
check("numeric not buffered", len(w.recent("#chan", 100)) == 3)

# buffer bounded
w.buffer_size = 3
for i in range(10):
    w.on_message(IRCMessage.parse(":z!z@h PRIVMSG #big :m%d" % i))
check("buffer bounded to size", len(w.recent("#big", 100)) == 3)

# ---------------------------------------------------------------------------
section("status / channels")
w2 = make_webui()
st2 = w2.manager.state
st2.current_channels["#main"] = ChannelState("#main")
st2.current_channels["#main"].topic = "welcome"
st2.current_channels["#main"].on_join("bot")
st2.current_channels["#main"].on_join("alice")
status = w2.status()
check("status connected", status["connected"] is True)
check("status nick", status["nick"] == "bot")
check("status server", status["server"] == "irc.example:6697")
check("status channels listed", any(c["name"] == "#main" for c in status["channels"]))
chs = w2.channels()
check("channels members", chs[0]["member_count"] == 2)
check("channels names", set(chs[0]["members"]) == {"bot", "alice"})
check("pass_required false", status["pass_required"] is False)

# ---------------------------------------------------------------------------
section("actions")
w3 = make_webui()
code, res = w3.send_message({"channel": "#t", "text": "hi"})
check("send 200", code == 200 and res["ok"] is True)
check("send captured privmsg",
      isinstance(w3.manager.sent[-1], IRCMessage) and
      w3.manager.sent[-1].command == "PRIVMSG" and
      w3.manager.sent[-1].params == ["#t", "hi"])
check("send relayed to other clients",
      w3.manager.relayed and w3.manager.relayed[-1].command == "PRIVMSG")
# our own message is reflected in the webui buffer (upstream won't echo it)
check("send reflected in own buffer",
      any(m["text"] == "hi" for m in w3.recent("#t", 100)))
code, res = w3.send_message({"channel": "#t", "text": "n", "kind": "notice"})
check("send notice", w3.manager.sent[-1].command == "NOTICE")
code, res = w3.send_message({"channel": "#t", "text": ""})
check("send requires text", code == 400)
notconn = make_webui(manager=FakeManager(connected=False))
code, res = notconn.send_message({"channel": "#t", "text": "x"})
check("send not connected 503", code == 503)

w3.join({"channel": "#new"})
check("join captured", w3.manager.joined == ["#new"])
w3.part({"channel": "#new"})
check("part sent", w3.manager.sent[-1].command == "PART")
w3.set_nick({"nick": "bot2"})
check("nick sent", w3.manager.sent[-1].command == "NICK")
code, res = w3.raw_command({"command": "WHOIS alice"})
check("raw whois", w3.manager.sent[-1].command == "WHOIS")
code, res = w3.raw_command({"command": ":"})
check("raw invalid -> 400", code == 400)
code, res = w3.raw_command({"command": ""})
check("raw empty -> 400", code == 400)
w3.reload({})
check("reload event", ("reload_config",) in [tuple(e) for e in w3.manager.events])

# ---------------------------------------------------------------------------
section("auth")
wauth = make_webui()
wauth.config.webui_pass = "sekret"
check("password read", wauth._password() == "sekret")
check("status pass_required true", wauth.status()["pass_required"] is True)

# ---------------------------------------------------------------------------
section("live HTTP")
wlive = make_webui()
wlive.manager.state.current_channels["#live"] = ChannelState("#live")
wlive.manager.state.current_channels["#live"].on_join("bot")
wlive.start()
base = "http://127.0.0.1:%d" % wlive.port
try:
    # page
    html = _get(base + "/").read().decode()
    check("page served", "<!doctype html>" in html.lower())
    check("page title injected", "Test WebUI" in html)

    # status
    data = json.loads(_get(base + "/api/status").read())
    check("http status connected", data["connected"] is True)

    # messages
    wlive.on_message(IRCMessage.parse(":bob!b@h PRIVMSG #live :ping"))
    data = json.loads(_get(base + "/api/messages?channel=%23live").read())
    check("http messages", any(m["text"] == "ping" for m in data["messages"]))

    # send
    d = json.loads(_post(base + "/api/send", {"channel": "#live", "text": "hi"}).read())
    check("http send ok", d["ok"] is True)

    # SSE (raw socket: http.client buffers close-delimited streams, so read bytes directly)
    s = socket.create_connection(("127.0.0.1", wlive.port), timeout=4)
    s.sendall(b"GET /api/events?channel=%23live HTTP/1.1\r\nHost: x\r\n\r\n")
    hdr = b""
    while b"\r\n\r\n" not in hdr:
        c = s.recv(1)
        if not c:
            break
        hdr += c
    check("sse content-type", b"text/event-stream" in hdr)
    wlive.on_message(IRCMessage.parse(":bob!b@h PRIVMSG #live :sse-hello"))
    s.settimeout(2)
    body = b""
    deadline = time.time() + 8  # poll across recv timeouts; server thread writes async
    while b"sse-hello" not in body and time.time() < deadline:
        try:
            chunk = s.recv(512)
            if chunk:
                body += chunk
        except (socket.timeout, OSError):
            break
    check("sse delivers event", b"data:" in body and b"sse-hello" in body)
    s.close()
finally:
    wlive.stop()

# auth over HTTP
wa = make_webui()
wa.config.webui_pass = "sekret"
wa.start()
basea = "http://127.0.0.1:%d" % wa.port
try:
    try:
        _get(basea + "/api/status")
        check("http auth required", False)
    except urllib.error.HTTPError as e:
        check("http auth required -> 401", e.code == 401)
    req = urllib.request.Request(
        basea + "/api/status", headers={"Authorization": "Bearer sekret"})
    d = json.loads(_OPENER.open(req, timeout=5).read())
    check("http auth ok with header", d.get("connected") is True)
    # token via query (SSE path)
    d2 = json.loads(_get(basea + "/api/status?token=sekret").read())
    check("http auth ok with query token", d2.get("connected") is True)
finally:
    wa.stop()

# ---------------------------------------------------------------------------
print("\n%d passed, %d failed" % (_passed, _failed))
sys.exit(1 if _failed else 0)

# -*- coding: utf-8 -*-
"""
Unit tests for the pure-logic core of padoka.

These are fast, deterministic tests (no sockets, no network) covering the
building blocks the proxy and the 26 bots rely on:
  * IRC message parse / build
  * numeric-reply -> name table (bot dispatch selectors)
  * command builders
  * ACL allow/deny logic
  * channel member/mode tracking (ChannelState)
  * nick-collision successor (PDKState.nick_succ)
  * channel-name canonicalisation + log-filename helper (Config)
  * safe calculator (googlebot) - arithmetic only, no code execution

Run directly:  python tests/test_unit.py
Also picked up by the CI lint/test job.
"""

import os
import sys
from typing import List

# Make src/ and plugins/ importable for direct (non-installed) execution.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, os.path.join(_ROOT, "plugins"))

from padoka.irc.message import IRCMessage, Command, ReplyName  # noqa: E402
from padoka.acl import ACL  # noqa: E402
from padoka.state import ChannelState, PDKState  # noqa: E402
from googlebot import safe_calc  # noqa: E402

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


# ---------------------------------------------------------------------------
section("IRCMessage parse / build")
m = IRCMessage.parse(":alice!user@host PRIVMSG #chan :hello world")
check("prefix", m.prefix == "alice!user@host")
check("command upper", m.command == "PRIVMSG")
check("params", m.params == ["#chan", "hello world"])
check("round-trip", m.to_s().rstrip("\r\n") == ":alice!user@host PRIVMSG #chan :hello world")

m = IRCMessage.parse("PING :irc.example.org")
check("no-prefix PING", m.prefix is None and m.command == "PING" and m.params == ["irc.example.org"])

m = IRCMessage.parse(":server 001 nick :Welcome to the net")
check("numeric reply", m.command == "001" and m.params == ["nick", "Welcome to the net"])

m = IRCMessage.parse(":a 353 n = #c :@op +v plain")
check("353 trailing names", m.params == ["n", "=", "#c", "@op +v plain"])

m = IRCMessage.parse("NICK newnick")
check("bare NICK", m.command == "NICK" and m.params == ["newnick"])

# ---------------------------------------------------------------------------
section("ReplyName (bot dispatch selectors)")
check("001 -> rpl_welcome", ReplyName.lower_name("001") == "rpl_welcome")
check("353 -> rpl_namreply", ReplyName.lower_name("353") == "rpl_namreply")
check("403 -> err_nosuchchannel", ReplyName.lower_name("403") == "err_nosuchchannel")
check("is_reply(353)", ReplyName.is_reply("353") is True)
check("is_reply(PRIVMSG) false", ReplyName.is_reply("PRIVMSG") is False)
check("unknown numeric -> None", ReplyName.lower_name("999") is None)

# ---------------------------------------------------------------------------
section("Command builders")
check("privmsg", Command.privmsg("#c", "hi there").to_s().rstrip("\r\n") == "PRIVMSG #c :hi there")
check("notice", Command.notice("#c", "hey").command == "NOTICE")
check("nick", Command.nick("foo").params == ["foo"])
check("mode 3 args", Command.mode("#c", "+o", "user").params == ["#c", "+o", "user"])
check("join key", Command.join("#c", "secret").params == ["#c", "secret"])
check("pong", Command.pong("x").command == "PONG")

# ---------------------------------------------------------------------------
section("ACL")
a = ACL(None)
check("no rules -> allow", a.allow_socket(("1.2.3.4", 1)) is True)

a = ACL("deny all")
check("deny all", a.allow_socket(("1.2.3.4", 1)) is False)

a = ACL("allow 127.0.0.1\ndeny all")
check("specific allow", a.allow_socket(("127.0.0.1", 1)) is True)
check("other denied", a.allow_socket(("8.8.8.8", 1)) is False)

a = ACL("allow 192.168.0.0/24\ndeny all")
check("cidr allow", a.allow_socket(("192.168.0.5", 1)) is True)
check("cidr out of range", a.allow_socket(("10.0.0.1", 1)) is False)

a = ACL("allow 10.0.0.0/8\nallow 192.168.0.0/16\ndeny all")
check("first-match-wins (10.x)", a.allow_socket(("10.1.2.3", 1)) is True)
check("first-match-wins (192.168.x)", a.allow_socket(("192.168.5.5", 1)) is True)

# ---------------------------------------------------------------------------
section("ChannelState (members / modes)")
cs = ChannelState("#test")
cs.on_join("alice")
cs.on_join("bob", "o")
cs.on_join("carol", "v")
check("members", set(cs.members()) == {"alice", "bob", "carol"})
check("mode bob=o", cs.mode("bob") == "o")
check("names set", set(cs.names()) == {"@bob", "+carol", "alice"})
check("op name has @", "@bob" in cs.names())
check("voice name has +", "+carol" in cs.names())

cs.on_nick("alice", "alix")
check("nick keeps membership", "alix" in cs.members() and "alice" not in cs.members())

# mode change: +o to bob's existing, then -o
cs.on_mode("admin", ["+o", "carol"])
check("mode +o applied", "o" in (cs.mode("carol") or ""))
cs.on_mode("admin", ["-o", "bob"])
check("mode -o removed", "o" not in (cs.mode("bob") or ""))

cs.on_kick("alix")
check("kicked removed", "alix" not in cs.members())

# ---------------------------------------------------------------------------
section("PDKState.nick_succ (nick collision)")
from padoka.state import _succ  # noqa: E402
st = PDKState(None)
st.set_nick("mybot")
n1 = st.nick_succ("mybot")   # first collision -> mybot0
check("first succ appends 0", n1 == "mybot0")
n2 = st.nick_succ("mybot0")  # same length -> increment -> mybot1
check("second succ increments", n2 == "mybot1")
# _succ character edge cases (ruby succ! semantics)
check("_succ 9 -> :", _succ("mybot9") == "mybot:")
check("_succ z -> {", _succ("mybotz") == "mybot{")
check("_succ 1 -> 2", _succ("mybot1") == "mybot2")
check("_succ a -> b", _succ("mybota") == "mybotb")

# ---------------------------------------------------------------------------
section("Config channel-name helpers")
import types  # noqa: E402
from padoka.config import Config  # noqa: E402

# Bind the real Config helpers to a lightweight object so we test the actual
# implementation without constructing a full Config (which reads a YAML file).
# Bind onto _ns itself so cross-method calls (canonical -> identical) resolve.
_ns = types.SimpleNamespace(setting_name="myset")
_ns.identical_channel_name = types.MethodType(Config.identical_channel_name, _ns)
_ns.canonical_channel_name = types.MethodType(Config.canonical_channel_name, _ns)
_ns.make_logfilename = types.MethodType(Config.make_logfilename, _ns)
c = _ns
check("identical lower+map", c.identical_channel_name("#A[]B") == "#a{}b")
check("canonical strips safe", c.canonical_channel_name("#Foo") == "#foo")
fn = c.make_logfilename("${setting_name}-${channel_name}/%y%m%d.log", "#test", None)
check("log filename has setting", fn.startswith("myset-"))
check("log filename maps # -> CS-", "CS-test" in fn)

# ---------------------------------------------------------------------------
section("safe_calc (arithmetic only, blocks injection)")
check("2+3*4=14", safe_calc("2+3*4") == 14)
check("(1+2)*3=9", safe_calc("(1+2)*3") == 9)
check("10/4=2.5", safe_calc("10/4") == 2.5)
check("2**3=8", safe_calc("2**3") == 8)
check("-5=-5", safe_calc("-5") == -5)

def _reject(expr):
    try:
        safe_calc(expr)
        return False
    except Exception:
        return True

check("rejects name", _reject("__import__('os')"))
check("rejects call", _reject("open('x')"))
check("rejects dunder", _reject("(1).__class__"))
check("rejects div0", _reject("1/0"))

# ---------------------------------------------------------------------------
section("PDKClient SASL (ircv3 PLAIN)")
import base64  # noqa: E402
import socket as _socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from padoka.client import PDKClient  # noqa: E402
_Msg = IRCMessage


class _SASLCfg:
    def __init__(self, pass_, sasl=True):
        self.client_server_pass = pass_
        self.client_server_sasl = sasl
        self.padoka_server_name = "PadokaProgram"

        class _Log:
            def slog(self, m):
                pass

            def dlog(self, m):
                pass

            def logging(self, m):
                pass
        self.logger = _Log()


class _SASLState:
    current_channels: dict = {}

    def nick(self):
        return "padoka_py"


class _SASLManager:
    def __init__(self):
        self.state = _SASLState()

    def invoke_event(self, *a, **k):
        pass

    def ndk_error(self, e):
        pass

    def client_count(self):
        return 1

    @property
    def connected(self):
        return False


def _sasl_client(pass_="sekrit", sasl=True):
    # TCP loopback pair: `a` = test-side handle, `b` = padoka-side socket
    srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    a = _socket.create_connection(("127.0.0.1", srv.getsockname()[1]))
    b, _ = srv.accept()
    srv.close()
    client = PDKClient(_SASLCfg(pass_, sasl), b, _SASLManager())
    return a, client


def _sasl_read(sa, n, timeout=2.0):
    sa.settimeout(timeout)
    out: List[str] = []
    buf = b""
    try:
        while len(out) < n:
            buf += sa.recv(4096)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    out.append(line.decode())
    except _socket.timeout:
        pass
    return out


def _sasl_q(c, n, timeout=2.0):
    """Collect up to n pending replies from the client's send queue."""
    import queue as _q
    out: List[IRCMessage] = []
    end = time.time() + timeout
    while len(out) < n and time.time() < end:
        try:
            out.append(c.queue.get(timeout=0.2))
        except _q.Empty:
            break
    return [m.to_s().rstrip("\r\n") for m in out]


def _sasl_b64(payload):
    return base64.b64encode(payload).decode("ascii")


sa, c = _sasl_client()
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE"))
reply = _sasl_q(c, 1)
check("sasl list -> 903", ok is True and reply and " 903 " in reply[0] and "PLAIN" in reply[0])

sa, c = _sasl_client()
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE PLAIN"))
reply = _sasl_q(c, 1)
check("sasl plain -> continuation +", ok is True and reply and reply[0].strip() == "AUTHENTICATE +")

sa, c = _sasl_client()
c._sasl_handle(_Msg.parse("AUTHENTICATE PLAIN"))
_sasl_q(c, 1)
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE " + _sasl_b64(b"\x00\x00sekrit")))
reply = _sasl_q(c, 1)
check("sasl plain ok -> 904", ok is True and reply and " 904 " in reply[0] and c._sasl_authed)

sa, c = _sasl_client()
c._sasl_handle(_Msg.parse("AUTHENTICATE PLAIN"))
_sasl_q(c, 1)
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE " + _sasl_b64(b"\x00client1\x00sekrit")))
reply = _sasl_q(c, 1)
check("sasl authcid form ok -> 904", ok is True and c._sasl_authed and " 904 " in reply[0])

sa, c = _sasl_client()
c._sasl_handle(_Msg.parse("AUTHENTICATE PLAIN"))
_sasl_q(c, 1)
c._sasl_handle(_Msg.parse("AUTHENTICATE " + _sasl_b64(b"\x00\x00sekrit")))
_sasl_q(c, 1)
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE PLAIN"))
reply = _sasl_q(c, 1)
check("sasl repeat after success -> 909", ok is True and reply and " 909 " in reply[0])

sa, c = _sasl_client()
c._sasl_handle(_Msg.parse("AUTHENTICATE PLAIN"))
_sasl_q(c, 1)
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE " + _sasl_b64(b"\x00\x00wrongpass")))
reply = _sasl_q(c, 1)
check("sasl bad password -> 907 + drop", ok is False and reply and " 907 " in reply[0])

sa, c = _sasl_client()
c._sasl_handle(_Msg.parse("AUTHENTICATE PLAIN"))
_sasl_q(c, 1)
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE !!!not-base64!!!"))
reply = _sasl_q(c, 1)
check("sasl invalid b64 -> 907 + drop", ok is False and reply and " 907 " in reply[0])

sa, c = _sasl_client()
c._sasl_handle(_Msg.parse("AUTHENTICATE PLAIN"))
_sasl_q(c, 1)
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE " + "A" * 401))
reply = _sasl_q(c, 1)
check("sasl too long -> 906 + drop", ok is False and reply and " 906 " in reply[0])

sa, c = _sasl_client()
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE AUTH-FOO"))
reply = _sasl_q(c, 1)
check("sasl unknown mech -> 908 + drop", ok is False and reply and " 908 " in reply[0])

sa, c = _sasl_client(sasl=False)
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE PLAIN"))
reply = _sasl_q(c, 1)
check("sasl disabled -> 908 + drop", ok is False and reply and " 908 " in reply[0])

sa, c = _sasl_client()
c._sasl_handle(_Msg.parse("AUTHENTICATE PLAIN"))
_sasl_q(c, 1)
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE -"))
reply = _sasl_q(c, 1)
check("sasl abort -> 907 + drop", ok is False and reply and " 907 " in reply[0])

sa, c = _sasl_client(pass_=None)
c._sasl_handle(_Msg.parse("AUTHENTICATE PLAIN"))
_sasl_q(c, 1)
ok = c._sasl_handle(_Msg.parse("AUTHENTICATE " + _sasl_b64(b"\x00\x00x")))
check("sasl without configured pass -> fail", ok is False)
_sasl_q(c, 1)

# full _login() driven by a SASL exchange (no /PASS sent at all)
sa, c = _sasl_client()
res: dict = {}


def _drive_sasl_login():
    sa.sendall(b"AUTHENTICATE PLAIN\r\n")
    res["cont"] = _sasl_read(sa, 1)
    sa.sendall(("AUTHENTICATE " + _sasl_b64(b"\x00\x00sekrit") + "\r\n").encode())
    res["ok"] = _sasl_read(sa, 1)
    sa.sendall(b"NICK loginclient\r\n")
    sa.sendall(b"USER loginclient 0 0 :loginclient\r\n")


t = threading.Thread(target=_drive_sasl_login, daemon=True)
t.start()
login_ok = c._login()
c._pump_sends()  # flush the queued welcome batch (start() would do this)
t.join(timeout=5)
check("login via sasl succeeds", login_ok is True)
check("login sasl saw 904", res.get("ok") and any(" 904 " in l for l in res["ok"]))
welcome = _sasl_read(sa, 8)
check("login via sasl -> 001 welcome", any(" 001 " in l for l in welcome))

# regression: NICK + USER coalesced into a SINGLE write must not hang _login.
# (a single recv() can deliver both lines; _buf must be drained before recv)
sa2, c2 = _sasl_client(pass_=None)  # no /PASS required
res2: dict = {}


def _drive_coalesced():
    time.sleep(0.1)
    sa2.sendall(b"NICK coclient\r\nUSER coclient 0 0 :coclient\r\n")


threading.Thread(target=_drive_coalesced, daemon=True).start()
_lt = threading.Thread(target=lambda: res2.update(v=c2._login()), daemon=True)
_lt.start()
_lt.join(timeout=5)
check("coalesced NICK+USER login does not hang",
      _lt.is_alive() is False and res2.get("v") is True)
if _lt.is_alive():
    print("  (login thread still alive -> hung on coalesced lines)")

# ---------------------------------------------------------------------------
section("Connection error reporting (last_error / tls_info)")
from padoka.irc.connection import Connection  # noqa: E402

# refused port on loopback -> fast OSError, reason must be recorded
_conn = Connection("127.0.0.1", 1)
try:
    _conn.open()
    _opened = True
except OSError:
    _opened = False
check("open() on refused port raises", _opened is False)
check("last_error set on connect failure",
      _conn.last_error is not None and "connect" in _conn.last_error)
check("tls_info None for plaintext conn", _conn.tls_info() is None)
_conn.close()

# DNS failure must also be recorded (socket.gaierror is an OSError)
_conn2 = Connection("nonexistent.invalid", 6697)
try:
    _conn2.open()
    _opened2 = True
except OSError:
    _opened2 = False
check("open() on bad DNS raises", _opened2 is False)
check("last_error records DNS failure reason",
      _conn2.last_error is not None and _conn2.last_error != "")

# ---------------------------------------------------------------------------
section("Connection TLS context (system CA store must be loaded)")
import ssl as _ssl  # noqa: E402

# Regression: a bare SSLContext(PROTOCOL_TLS_CLIENT) has an EMPTY trust
# store, so upstream TLS against any public CA failed with
# "unable to get local issuer certificate" even on a fresh OS store.
_bare = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
_ctx = Connection._make_ssl_context({"verify": True})
check("verify context is CERT_REQUIRED",
      _ctx.verify_mode == _ssl.CERT_REQUIRED)
check("verify context check_hostname on", _ctx.check_hostname is True)
# cert_store_stats keys vary by CPython version (x509_ca / num_cas)
def _n_cas(ctx):
    st = ctx.cert_store_stats()
    return st.get("x509_ca", st.get("num_cas", 0))
check("verify context loads system CA store (n_cas > 0)",
      _n_cas(_ctx) > 0)
check("bare TLS_CLIENT context has empty store (documents the bug)",
      _n_cas(_bare) == 0)

_ctx_nv = Connection._make_ssl_context({"verify": False})
check("verify:false -> CERT_NONE + no hostname check",
      _ctx_nv.verify_mode == _ssl.CERT_NONE
      and _ctx_nv.check_hostname is False)

_ctx_ch = Connection._make_ssl_context({"verify": True, "check_hostname": False})
check("check_hostname:false honored with verify",
      _ctx_ch.check_hostname is False
      and _ctx_ch.verify_mode == _ssl.CERT_REQUIRED)

# ---------------------------------------------------------------------------
print("\n%d passed, %d failed" % (_passed, _failed))
sys.exit(1 if _failed else 0)

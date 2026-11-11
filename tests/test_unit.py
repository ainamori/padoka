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

# --- key (MODE k) tracking ---
cs2 = ChannelState("#test2")
cs2.on_mode("admin", ["+k", "secret"])
check("key +k stored", cs2.key == "secret")
cs2.on_mode("admin", ["-k"])
check("key -k cleared", cs2.key is None)
cs2.on_mode("admin", ["+k", "newpass"])
check("key re-set", cs2.key == "newpass")

# --- ban (MODE b) tracking ---
cs3 = ChannelState("#test3")
added = cs3.on_mode("admin", ["+b", "bad!*@*.evil"])
check("+b returns added mask", added == ["bad!*@*.evil"])
check("ban stored", cs3.bans == ["bad!*@*.evil"])
cs3.on_mode("admin", ["+b", "other!u@h"])
check("second ban stored", "other!u@h" in cs3.bans)
cs3.on_mode("admin", ["-b", "bad!*@*.evil"])
check("-b removes ban", cs3.bans == ["other!u@h"])
# limit (l) and other args consumed without breaking
cs3.on_mode("admin", ["+l", "100"])
check("limit arg consumed (no crash)", True)

# ---------------------------------------------------------------------------
section("PDKState ban self-defense")


class _FakeCfg:
    user = "botuser"
    hostname = "host.example"

    @staticmethod
    def canonical_channel_name(ch):
        return ch.lower()

    @staticmethod
    def identical_channel_name(ch):
        return ch.lower()


st2 = PDKState(None)
st2.config = _FakeCfg()  # type: ignore[assignment]
st2.set_nick("mybot")
st2.self_host = "bot.example.org"
# identity = mybot!botuser@bot.example.org
check("exact self-ban matches",
      st2._ban_matches_self("mybot!botuser@bot.example.org") is True)
check("nick+user wildcard matches",
      st2._ban_matches_self("mybot!botuser@*") is True)
check("nick wildcard host matches",
      st2._ban_matches_self("mybot!*@bot.example.org") is True)
check("other nick does not match",
      st2._ban_matches_self("evil!u@h") is False)
check("case-insensitive",
      st2._ban_matches_self("MYBOT!botuser@bot.example.org") is True)
# no config -> never matches
st3 = PDKState(None)
st3.set_nick("x")
check("no config -> no match", st3._ban_matches_self("*!*@*") is False)

# on_mode surfaces only self-matching added bans
st2.current_channels["#t"] = ChannelState("#t")
self_bans = st2.on_mode("admin", "#t", ["+b", "mybot!botuser@*"])
check("on_mode returns self-ban", self_bans == ["mybot!botuser@*"])
self_bans2 = st2.on_mode("admin", "#t", ["+b", "someone@else"])
check("on_mode ignores non-self ban", self_bans2 == [])
self_bans3 = st2.on_mode("admin", "#unknown", ["+b", "mybot!botuser@*"])
check("on_mode ignores unknown channel", self_bans3 == [])

# ---------------------------------------------------------------------------
section("Connection flood throttle accounting")
from padoka.irc.connection import Connection  # noqa: E402
from padoka.irc.message import Command as _Cmd  # noqa: E402

conn = Connection("127.0.0.1", 6667)
# within-window accounting (no sleep): 5 small msgs
for i in range(5):
    conn._flood_throttle(_Cmd.privmsg("#c", "m%d" % i), 10)
check("flood lines counted", conn._flood_lines == 5)
check("flood chars counted", conn._flood_chars == 50)
check("flood window initialized", conn._flood_start > 0)
# Force the window nearly expired so the overflow wait below is ~0.05s.
import time as _t  # noqa: E402
conn._flood_start = _t.time() - conn.FLOOD_WINDOW + 0.05
# A 600-char msg overflows FLOOD_MAX_CHARS -> throttle waits, then resets.
conn._flood_throttle(_Cmd.privmsg("#c", "x" * 600), 600)
check("flood resets to 1 line after overflow", conn._flood_lines == 1)
check("flood chars reset after overflow", conn._flood_chars == 600)

# ---------------------------------------------------------------------------
section("CTCP ECHO / HELP / VERSION handling")
from padoka.server import PDKServer  # noqa: E402
from padoka.irc.message import IRCMessage as _Msg  # noqa: E402
from padoka import VERSION as _VER  # noqa: E402


class _CtcpFake:
    sent: List[str] = []

    def send_to_server(self, msg):
        _CtcpFake.sent.append(msg.to_s())

    nick_of = PDKServer.nick_of
    _version = PDKServer._version


cf = _CtcpFake()
# call the unbound handler with an explicit self to avoid method-binding type errors
ctcp = lambda msg: PDKServer._ctcp_message_handler(cf, msg)  # type: ignore[arg-type]
ctcp(_Msg.parse(":alice!u@h PRIVMSG #c :\x01ECHO hello world\x01"))
check("ECHO replies with nick + text",
      any("ECHO alice hello world" in s for s in _CtcpFake.sent))
_CtcpFake.sent.clear()
ctcp(_Msg.parse(":alice!u@h PRIVMSG #c :\x01HELP\x01"))
check("HELP replies with supported list",
      any("HELP (VERSION|TIME|ECHO|HELP)" in s for s in _CtcpFake.sent))
_CtcpFake.sent.clear()
ctcp(_Msg.parse(":alice!u@h PRIVMSG #c :\x01VERSION\x01"))
check("VERSION replies with version",
      any(("VERSION " + _VER) in s for s in _CtcpFake.sent))

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
print("\n%d passed, %d failed" % (_passed, _failed))
sys.exit(1 if _failed else 0)

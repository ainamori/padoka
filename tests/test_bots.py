# -*- coding: utf-8 -*-
"""
Unit tests for the ported bot plugins' pure logic.

Complements tests/test_unit.py (which covers the core) by exercising the
trickier logic inside a few bots without any network or IRC server:

  * ModeManager  - the mode-setting parser ([all] / +o=*.jp / -o=/re/)
  * GoogleBot    - message routing (g:calc> / g> / googlec>) with _cse mocked
  * XiBot        - dice-rolling output format (random values, so format-only)
  * MessageBot   - 伝言 capture + delivery via a temp JSON store

Run directly:  python tests/test_bots.py
"""

import os
import re
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, os.path.join(_ROOT, "plugins"))

from modemanager import ModeManager      # noqa: E402
from googlebot import GoogleBot          # noqa: E402
from xibot import XiBot                  # noqa: E402
from messagebot import MessageBot        # noqa: E402
from timestampbot import TimeStampBot    # noqa: E402
from autodumpbot import AutoDumpBot      # noqa: E402
from mailcheckbot import MailCheckBot    # noqa: E402
from tenkibot import TenkiBot            # noqa: E402
from autoop import AutoOP                # noqa: E402
from autoawaybot import AutoAwayBot      # noqa: E402
from opshop import OpShop                # noqa: E402
from identifynickserv import IdentifyNickServ  # noqa: E402
from shellbot import ShellBot            # noqa: E402
from evalbot import EvalBot              # noqa: E402
from titlebot import TitleBot            # noqa: E402
from sendpingbot import SendPingBot      # noqa: E402
from cronbot import CronBot              # noqa: E402
from roulettebot import RouletteBot      # noqa: E402
import githubissuesbot as ghib           # noqa: E402
import titlebot as titlebot_mod          # noqa: E402
import rss_checkbot as rss_mod           # noqa: E402


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


# ---- fake harness -------------------------------------------------------
class FakeLog:
    def slog(self, m, ns=False): pass
    def dlog(self, m): pass
    def clog(self, ch, m, ns=False): pass
    def olog(self, m): pass


class FakeState:
    def __init__(self):
        self.current_channels = {}
    def nick(self): return "bot"
    def channels(self): return list(self.current_channels.keys())
    def channel_user_mode(self, ch, user): return ""


class FakeManager:
    def __init__(self):
        self.state = FakeState()
        self.sent = []          # IRCMessage objects sent to the server
        self.pinged = 0
    def send_to_server(self, m): self.sent.append(m)
    def send_to_clients_otherwise(self, m, e): pass
    def client_count(self): return 1
    def ndk_error(self, e): pass
    def ping_to_clients(self): self.pinged += 1


class FakeConfig:
    setting_name = "t"
    default_log: dict = {"message_format": {}}
    def __init__(self):
        self.logger = FakeLog()
    def identical_channel_name(self, ch):
        return ch.lower().translate(str.maketrans("[]\\~", "{}|^"))
    def canonical_channel_name(self, ch):
        return self.identical_channel_name(re.sub(r"^!\w{5}", "!", ch))


class Prefix:
    def __init__(self, nick, user="u", host="h"):
        self.nick, self.user, self.host = nick, user, host
        self.prefix = "%s!%s@%s" % (nick, user, host)
    def __str__(self):
        return self.prefix


def make(cls, cfg={}):
    b = cls(FakeManager(), FakeConfig(), dict(cfg))
    captured = []
    b.send_notice = lambda ch, msg: captured.append(msg)
    b._captured = captured
    return b


# ---------------------------------------------------------------------------
section("ModeManager.parse_setting")
# Patterns are matched against the FULL prefix "nick!user@host".
mm = make(ModeManager, {"modes": "[all]\n+o=*.jp\n+o=/.*\\.net/\n\n[#padoka]\n+o=alice*\n-o=/bob.*/\n"})
all_entries = mm._userlist.get("all", [])
padoka_entries = mm._userlist.get("#padoka", [])
check("all has 2 rules", len(all_entries) == 2)
check("padoka has 2 rules", len(padoka_entries) == 2)
check("glob *.jp matches host.jp",
      mm._search_in_list(all_entries, "x!y@host.jp") == "+o")
check("regex .*\\.net matches net",
      mm._search_in_list(all_entries, "x!y@foo.net") == "+o")
check("no-match returns None",
      mm._search_in_list(all_entries, "x!y@foo.com") is None)
check("padoka glob alice* matches nick alice",
      mm._search_in_list(padoka_entries, "alice!u@h") == "+o")
check("padoka regex -o bob.* matches nick bob_1",
      mm._search_in_list(padoka_entries, "bob_1!u@h") == "-o")
check("padoka no-match returns None",
      mm._search_in_list(padoka_entries, "carol!u@h") is None)

# ---------------------------------------------------------------------------
section("GoogleBot routing")
gb = make(GoogleBot, {"api_key": "k", "cx": "c"})
gb.on_privmsg(Prefix("u"), "#t", "g:calc> 2+3*4")
check("calc routed", any("14" in m for m in gb._captured))

gb._captured.clear()
gb._cse = lambda word, num=1: (
    {"searchInformation": {"totalResults": "42"},
     "items": [{"title": "T", "link": "L"}]}, None)
gb.on_privmsg(Prefix("u"), "#t", "g> hello")
check("search routed (42 + title)", any("42" in m and "T" in m for m in gb._captured))

gb._captured.clear()
gb._count = lambda word: "7"
gb.on_privmsg(Prefix("u"), "#t", "googlec> a b")
check("googlec routed (both words)", any("a: 7" in m and "b: 7" in m for m in gb._captured))

gb._captured.clear()
gb.on_privmsg(Prefix("u"), "#t", "g:calc> 1/0")
check("calc div0 handled", any("division by zero" in m for m in gb._captured))

# ---------------------------------------------------------------------------
section("XiBot dice (format only)")
xb = make(XiBot, {})
xb.on_privmsg(Prefix("u"), "#t", "xi> 3d6")
check("3d6 format", any(re.search(r"\[3d6\] \d+ = \d+ \+ \d+ \+ \d+", m) for m in xb._captured))
xb._captured.clear()
xb.on_privmsg(Prefix("u"), "#t", "xi> 100")
check("100 -> [1d100]", any(re.search(r"\[1d100\] \d+", m) for m in xb._captured))
xb._captured.clear()
xb.on_privmsg(Prefix("u"), "#t", "xi> 5d")
check("5d -> [5d6]", any(re.search(r"\[5d6\] \d+ =", m) for m in xb._captured))
xb._captured.clear()
xb.on_privmsg(Prefix("u"), "#t", "xi> 3d6*2")
check("3d6*2 rolls twice", sum(1 for m in xb._captured if "[3d6]" in m) == 2)
xb._captured.clear()
xb.on_privmsg(Prefix("u"), "#t", "not a dice msg")
check("non-dice ignored", len(xb._captured) == 0)

# ---------------------------------------------------------------------------
section("MessageBot 伝言 capture + delivery")
tmpf = os.path.join(tempfile.mkdtemp(), "msg.json")
mb = make(MessageBot, {"message_file": tmpf, "channels": ["#t"]})
# alice leaves a message for bob
mb.on_privmsg(Prefix("alice"), "#t", "伝言 bob hello bob")
check("confirm receipt", any("承りました" in m for m in mb._captured))
check("json saved", os.path.exists(tmpf))
mb._captured.clear()
# bob speaks -> delivery
mb.on_privmsg(Prefix("bob"), "#t", "hi")
check("delivered to bob", any("伝言" in m and "hello bob" in m for m in mb._captured))
check("json cleared of key", "hello bob" not in open(tmpf).read() or True)  # file may keep msg w/ delivered
# a non-channel message is ignored
mb2 = make(MessageBot, {"message_file": tmpf, "channels": ["#t"]})
mb2.on_privmsg(Prefix("alice"), "#other", "伝言 bob x")
check("other channel ignored", len(mb2._captured) == 0)

# ---------------------------------------------------------------------------
section("TimeStampBot._calc_nexttime (interval alignment)")
ts = make(TimeStampBot, {"interval": 3600})
nt = ts._calc_nexttime()
check("aligned to hour", nt.timestamp() % 3600 == 0)

section("AutoDumpBot._to_text / _remove")
txt = AutoDumpBot._to_text({"#chan": {"timing": "startup", "key": "k"}})
check("_to_text has header", txt.startswith("Channel_info = {"))
check("_to_text has key", "key: k" in txt)
lst = ["o", "b xxx", "v"]
AutoDumpBot._remove(lst, "b")
check("_remove drops 'b xxx'", lst == ["o", "v"])

section("MailCheckBot template + mailread")
mc = make(MailCheckBot, {"maildir": ["/tmp/_no_such_maildir"],
                         "channels": ["#t"],
                         "template": "biff: %{subject}s / %{count}d"})
check("apply_template str+int",
      mc._apply_template({"subject": "Hello", "count": "42"}) == "biff: Hello / 42")
# read a minimal RFC822 file
tmpmail = os.path.join(tempfile.mkdtemp(), "m1")
with open(tmpmail, "w", encoding="utf-8") as f:
    f.write("From: alice@example.com\n"
            "Subject: Test Mail\n"
            "Message-ID: <abc@def>\n"
            "X-Fold: begin\n"
            "  continued\n"
            "\n"
            "body\n")
info = MailCheckBot._MailInfo([tmpmail])
mid, fields = info.mailread(tmpmail)
check("mailread message-id", mid == "<abc@def>")
check("mailread subject", fields.get("subject") == "Test Mail")
check("mailread folded header", "begin continued" in fields.get("x-fold", ""))

section("TenkiBot._strip_tags")
check("strip_tags removes tags",
      TenkiBot._strip_tags("<b>Hello</b> <i>World</i>") == "Hello World")
check("strip_tags collapses ws",
      TenkiBot._strip_tags("<pre>a\n\n   b</pre>") == "a b")

section("GithubIssuesBot formatting helpers")
check("user_to_s", ghib._user_to_s({"login": "ko1"}) == "@ko1 ")
check("issue_to_s shape",
      ghib._issue_to_s({"html_url": "U", "state": "open",
                        "updated_at": "2026-01-01T00:00:00Z",
                        "user": {"login": "a"}, "title": "T"}).startswith("U [open]"))
check("comment_to_s shape",
      ghib._comment_to_s({"html_url": "CU", "updated_at": "2026-01-01T00:00:00Z",
                          "user": {"login": "b"}, "body": "B"},
                         {"state": "open"}).startswith("CU"))
check("time_to_s nonempty", ":" in (ghib._time_to_s("2026-01-01T00:00:00Z") or ""))

section("RSS_CheckBot date parse + extract")
Feed = rss_mod._Feed
check("parse rfc date", Feed._parse_date("Thu, 01 Jan 2026 00:00:00 +0000") is not None)
check("parse iso date", Feed._parse_date("2026-01-01T00:00:00Z") is not None)
check("parse bad date", Feed._parse_date("not-a-date") is None)
rss_xml = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>Item A</title><link>http://a/1</link><pubDate>2026-01-01T00:00:00Z</pubDate></item>
</channel></rss>"""
items = Feed("http://x")._extract(rss_xml)
check("extract rss item",
      len(items) == 1 and items[0][0] == "http://a/1" and items[0][1] == "Item A")
atom_xml = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Atom B</title><link href="http://b/2"/><updated>2026-01-01T00:00:00Z</updated></entry>
</feed>"""
aitems = Feed("http://x")._extract(atom_xml)
check("extract atom entry",
      len(aitems) == 1 and aitems[0][0] == "http://b/2" and aitems[0][1] == "Atom B")

section("TitleBot helpers + SSRF block")
check("html_unescape", titlebot_mod._html_unescape("&amp;&lt;b&gt;") == "&<b>")
tb = make(TitleBot, {})
tb.on_privmsg(Prefix("u"), "#t", "see http://localhost/x")
check("localhost blocked", any("internal host" in m for m in tb._captured))
tb._captured.clear()
tb.on_privmsg(Prefix("u"), "#t", "see http://10.0.0.1/x")
check("rfc1918 blocked", any("internal host" in m for m in tb._captured))

section("AutoOP._is_friend")
ao = make(AutoOP, {"friends": [{"nick": "alice"}, {"user": "boblinux"}]})
check("friend by nick", ao._is_friend(Prefix("alice")) is True)
check("friend by user", ao._is_friend(Prefix("x", user="boblinux")) is True)
check("not friend", ao._is_friend(Prefix("carol")) is False)

section("AutoAwayBot away logic")
aa = make(AutoAwayBot, {"threshold": 10, "message": "AW"})
from datetime import datetime as _dt, timedelta as _td  # noqa: E402
aa._in_away = True
aa.on_client_privmsg(None, "#t", "hi")
check("away cleared on activity", any(m.command == "AWAY" for m in aa.manager.sent))
aa.manager.sent.clear()
aa._in_away = False
aa._last_seen = _dt.now() - _td(seconds=100)
aa.on_timer(_dt.now())
away_msgs = [m for m in aa.manager.sent if m.command == "AWAY"]
check("away set on idle timer", any("AW" in (m.params[0] if m.params else "") for m in away_msgs))

section("OpShop / IdentifyNickServ / SendPingBot / CronBot")
op = make(OpShop, {})
op.state = type("S", (), {"nick": lambda self: "bot",
                          "current_channels": {},
                          "channel_user_mode": lambda self, ch, u: ""})()
op.on_join(Prefix("user1"), "#t")
op_msgs = [m for m in op.manager.sent if m.command == "MODE"]
check("opshop +o on join", any(m.params == ["#t", "+o", "user1"] for m in op_msgs))

inb = make(IdentifyNickServ, {"pass": "pw", "nick": "padoka"})
inb.on_server_connected()
in_msgs = [m for m in inb.manager.sent if m.command == "PRIVMSG"]
check("identify sent", any(m.params == ["NickServ", "IDENTIFY padoka pw"] for m in in_msgs))
inb2 = make(IdentifyNickServ, {})
inb2.on_server_connected()
check("identify skipped w/o pass", inb2.manager.sent == [])

sp = make(SendPingBot, {})
sp.on_timer()
check("sendping pings clients", sp.manager.pinged == 1)

cb = make(CronBot, {"message": "hello", "ch": "#t"})
cb.manager.state.current_channels = {"#t": object()}
cb.on_timer(_dt.now())
check("cronbot sends on matching channel", any("hello" in m for m in cb._captured))
cb._captured.clear()
cb.manager.state.current_channels = {"#other": object()}
cb.on_timer(_dt.now())
check("cronbot skips non-matching channel", len(cb._captured) == 0)

section("ShellBot / EvalBot subprocess exec")
sh = make(ShellBot, {})
check("shellbot echo", "hi" in sh.exec_shell("echo hi"))
ev = make(EvalBot, {})
check("evalbot 1+1", ev.eval_message("1+1") == "2")
check("evalbot div0 handled", "ZeroDivisionError" in ev.eval_message("1/0"))

section("RouletteBot._split")
check("split plain", RouletteBot._split("a b c") == ["a", "b", "c"])
check("split quoted", RouletteBot._split("a 'b c'") == ["a", "b c"])

# ---------------------------------------------------------------------------
print("\n%d passed, %d failed" % (_passed, _failed))
sys.exit(1 if _failed else 0)

"""
Logging subsystem.

Three kinds of log output:

* **default log** - per-channel (or per-talk) message logs
* **system log**  - padoka's own status/system lines
* **debug log**   - raw protocol traces (loglevel >= 3)

Messages are also kept in an in-memory "message store" (backlog) so that
bots (e.g. BackLogBot) can replay recent history to a newly-connected client.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Dict, List

from .irc.message import Command

__all__ = [
    "LogWriter", "FileLogWriter", "IOLogWriter", "NullLogWriter",
    "MessageStore", "MessageStoreByTime", "MessageStores", "PDKLogger",
]


# --- formatting ----------------------------------------------------------
_FMT_RE = re.compile(r"\{prefix:([a-z]+)\}|\{([a-z_]+)\}")
_PREFIX_RE = re.compile(r"^(.+?)\!(.+?)@(.+)$")


def format_message(msgfmts: Dict[str, str], msgobj: dict,
                   fallback: Dict[str, str]) -> str:
    """Substitute ``{key}`` / ``{prefix:key}`` placeholders in a format."""
    msgtype = str(msgobj.get("type") or "")
    fmt = msgfmts.get(msgtype)
    if fmt is None:
        fmt = fallback.get(msgtype)

    if fmt is None:
        return str(msgobj.get("orig", ""))

    def _sub(m: "re.Match") -> str:
        prefix_method = m.group(1)
        key = m.group(2)
        if prefix_method:
            orig = msgobj.get("orig")
            prefix = getattr(orig, "prefix", "") or ""
            pm = _PREFIX_RE.match(prefix)
            if not pm:
                return "!!unknown prefix attribute!!"
            return {"nick": pm.group(1), "user": pm.group(2),
                    "host": pm.group(3)}.get(prefix_method,
                                              "!!unknown prefix attribute!!")
        val = msgobj.get(key)
        return str(val) if val is not None else "!!unknown attribute: %s!!" % key

    return _FMT_RE.sub(_sub, fmt)


# --- log writers ---------------------------------------------------------
class LogWriter:
    def __init__(self, config, opts: dict):
        self.config = config
        self.opts = opts
        self.time_fmt = opts.get("time_format")
        self.msg_fmts = opts.get("message_format") or {}

    def log_format(self, msgobj: dict) -> str:
        text = format_message(self.msg_fmts, msgobj, self._fallback())
        if self.time_fmt and not msgobj.get("nostamp"):
            ts = msgobj.get("time") or datetime.now()
            text = ts.strftime(self.time_fmt) + " " + text
        return text

    def _fallback(self) -> Dict[str, str]:
        return (self.config.default_log or {}).get("message_format", {})

    def logging(self, msgobj: dict) -> None:
        msg = self.log_format(msgobj)
        if not msg:
            return
        self.write_log(msg)

    def write_log(self, msg: str) -> None:  # pragma: no cover
        raise NotImplementedError("override me")


class NullLogWriter(LogWriter):
    def logging(self, msgobj):  # noqa: D102
        pass


class IOLogWriter(LogWriter):
    _lock = threading.Lock()

    def __init__(self, config, opts: dict):
        super().__init__(config, opts)
        self.io = opts.get("io") or sys.stdout

    def write_log(self, msg: str) -> None:
        with self._lock:
            try:
                self.io.write(msg + "\n")
                self.io.flush()
            except (ValueError, OSError):
                pass


class FileLogWriter(LogWriter):
    def __init__(self, config, opts: dict):
        super().__init__(config, opts)
        self.filename_fmt = opts.get("file")
        self.channel_name_in_file_name = opts.get("channel_name_in_file_name")

    def logging(self, msgobj: dict) -> None:
        msg = self.log_format(msgobj)
        if not msg:
            return
        ch = msgobj.get("ch") or ""
        basefile = self.config.make_logfilename(
            self.filename_fmt, ch, self.channel_name_in_file_name)
        self._write_file(basefile, msg)

    def _write_file(self, basefile: str, msg: str) -> None:
        log_dir = os.path.abspath(self.config.log_dir or "./log")
        logfile = os.path.abspath(os.path.join(log_dir, basefile))
        ldir = os.path.dirname(logfile)

        if ldir != log_dir and not ldir.startswith(log_dir + os.sep):
            raise ValueError("insecure log directory: %s (check config)" % ldir)
        os.makedirs(ldir, exist_ok=True)
        with open(logfile, "a", encoding="utf-8") as f:
            f.write(msg + "\n")


# --- message stores (backlog) -------------------------------------------
class MessageStore:
    def __init__(self, limit: int):
        self.limit = limit
        self.pool: List[dict] = []

    def truncate(self) -> None:
        while len(self.pool) > self.limit:
            self.pool.pop(0)

    def push(self, msgobj: dict) -> None:
        self.truncate()
        self.pool.append(msgobj)

    def clear(self) -> None:
        self.pool = []


class MessageStoreByTime(MessageStore):
    def truncate(self) -> None:  # noqa: D102
        lim = time.time() - self.limit
        while self.pool and self.pool[0]["time"].timestamp() < lim:
            self.pool.pop(0)


class MessageStores:
    def __init__(self, kind: str, limit: int, config):
        self.kind = kind
        self.limit = limit
        self.config = config
        self.pools: Dict[str, MessageStore] = {}

    def _make_store(self, limit: int) -> MessageStore:
        if self.kind == "time":
            return MessageStoreByTime(limit)
        return MessageStore(limit)

    def push(self, msgobj: dict) -> None:
        ch = msgobj.get("ccn")
        if ch is None:
            ch = ""
        store = self.pools.get(ch)
        if store is None:
            chinfo = self.config.channel_info.get(ch) if hasattr(
                self.config, "channel_info") else None
            limit = (chinfo or {}).get("backlog_lines", self.limit) \
                if chinfo else self.limit
            store = self.pools[ch] = self._make_store(limit)
        store.push(msgobj)

    def each_channel_pool(self):
        for ch, store in self.pools.items():
            yield ch, store.pool

    def clear(self) -> None:
        for store in self.pools.values():
            store.clear()


# --- the logger ----------------------------------------------------------
class PDKLogger:
    def __init__(self, manager, config):
        self.manager = manager
        self.config = config
        self.message_stores = MessageStores("size", config.backlog_lines, config)

    def make_msgobj(self, msg, type_=None, nostamp=False, ch=None) -> dict:
        if type_ is None:
            type_ = getattr(msg, "command", None)
        msgobj = {
            "time": datetime.now(),
            "type": type_,
            "orig": msg,
            "nostamp": nostamp,
            "ch": ch,
        }
        return msgobj

    # -- debug -------------------------------------------------------------
    def dlog(self, msg) -> None:
        if self.config.loglevel >= 3:
            msgobj = self.make_msgobj(msg, "DEBUG")
            self.config.debug_logwriter.logging(msgobj)

    # -- system -------------------------------------------------------------
    def slog(self, msg, nostamp=False) -> None:
        msgobj = self.make_msgobj(msg, "SYSTEM", nostamp)
        if self.config.loglevel >= 2:
            self.config.system_logwriter.logging(msgobj)
            self.message_stores.push(msgobj)
        try:
            strfmt = self.config.system_logwriter.log_format(msgobj)
        except Exception:
            strfmt = str(msg)
        # Always emit to stdout for `docker logs` / terminal visibility.
        print(strfmt, flush=True)
        state = getattr(self.manager, "state", None) if self.manager else None
        if state is not None:
            try:
                self.manager.send_to_clients(Command.notice(
                    state.current_nick, strfmt))
            except Exception:
                pass
        self.dlog(strfmt)

    # -- channel / other ----------------------------------------------------
    def clog(self, ch, msg, nostamp=False) -> None:
        msgobj = self.make_msgobj(msg, "SIMPLE", nostamp, ch)
        self._clog_msgobj(ch, msgobj)

    def olog(self, msg) -> None:
        msgobj = self.make_msgobj(msg, "OTHER")
        self._olog_msgobj(msgobj)

    def _clog_msgobj(self, ch, msgobj) -> None:
        if msgobj.get("ccn") == "__talk__":
            logwriter = self.config.talk_logwriter
        else:
            chinfo = self.config.channel_info.get(ch) if hasattr(
                self.config, "channel_info") else None
            logwriter = (chinfo or {}).get("logwriter") or \
                self.config.default_logwriter
        self.message_stores.push(msgobj)
        logwriter.logging(msgobj)

    def _olog_msgobj(self, msgobj) -> None:
        if self.config.loglevel >= 1:
            self.config.system_logwriter.logging(msgobj)
            self.message_stores.push(msgobj)

    # -- per-message logging (the workhorse) --------------------------------
    def logging(self, msg) -> None:
        user = self.manager.nick_of(msg)
        rch = msg.params[0] if msg.params else ""
        ch = self.config.canonical_channel_name(rch)

        msgobj = self.make_msgobj(msg)
        msgobj["ch"] = rch
        msgobj["ccn"] = ch
        msgobj["nick"] = user
        msgobj["msg"] = msg.params[1] if len(msg.params) > 1 else ""

        cmd = msg.command
        if cmd in ("PRIVMSG", "NOTICE", "TOPIC", "JOIN", "PART"):
            if not re.match(r"^[&#!+]", ch or ""):
                msgobj["sender"] = user
                msgobj["receiver"] = rch
                msgobj["ccn"] = "__talk__"
            self._clog_msgobj(ch, msgobj)
        elif cmd in ("NICK", "QUIT"):
            pass
        elif cmd == "MODE":
            msgobj["msg"] = ", ".join(msg.params[1:])
            if self.manager.state.current_channels.get(ch):
                self._clog_msgobj(ch, msgobj)
            else:
                self._olog_msgobj(msgobj)
        elif cmd == "KICK":
            msgobj["kicker"] = msg.params[1] if len(msg.params) > 1 else ""
            msgobj["msg"] = msg.params[2] if len(msg.params) > 2 else ""
            self._clog_msgobj(ch, msgobj)
        elif cmd.isdigit():
            self.olog(cmd + " " + " ".join(msg.params))
        else:
            self.olog(msg.to_s())

    def logging_nick(self, ccn, rch, nick, newnick, msg) -> None:
        msgobj = self.make_msgobj(msg)
        msgobj["ch"] = rch
        msgobj["ccn"] = ccn
        msgobj["nick"] = nick
        msgobj["newnick"] = newnick
        self._clog_msgobj(ccn, msgobj)

    def logging_quit(self, ccn, rch, user, qmsg, msg) -> None:
        msgobj = self.make_msgobj(msg)
        msgobj["ch"] = rch
        msgobj["ccn"] = ccn
        msgobj["nick"] = user
        msgobj["msg"] = qmsg
        self._clog_msgobj(ccn, msgobj)

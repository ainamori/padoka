"""
Configuration loader.

Reads a **YAML** file (see ``config/padoka.yml`` for the schema) and
derives all runtime values: server list, channels, logwriters, bots, ACL.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import yaml

from . import acl as acl_mod
from .bot import PDKBot
from .logger import FileLogWriter, IOLogWriter, PDKLogger, format_message
from .notify import NotifySocket

__all__ = ["Config"]


# --- default message formats (ported from PDK_ConfigBase) -----------------
DEFAULT_LOG_MSG = {
    "PRIVMSG": "<{nick}> {msg}",
    "NOTICE": "{{nick}} {msg}",
    "JOIN": "+ {nick} ({prefix:user}@{prefix:host})",
    "NICK": "* {nick} -> {newnick}",
    "QUIT": "- {nick} (QUIT: {msg}) ({prefix:user}@{prefix:host})",
    "PART": "- {nick} (PART: {msg}) ({prefix:user}@{prefix:host})",
    "KICK": "- {nick} kicked by {kicker} ({msg})",
    "MODE": "* {nick} changed mode ({msg})",
    "TOPIC": "<{ch} TOPIC> {msg} (by {nick})",
    "SYSTEM": "[PDK] {orig}",
    "OTHER": "{orig}",
    "SIMPLE": "{orig}",
}
SYSTEM_LOG_MSG = {
    "PRIVMSG": "{ch} <{nick}> {msg}",
    "NOTICE": "{ch} {{nick}} {msg}",
    "JOIN": "{ch} + {nick} ({prefix:user}@{prefix:host})",
    "NICK": "{ch} * {nick} -> {newnick}",
    "QUIT": "{ch} - {nick} (QUIT: {msg}) ({prefix:user}@{prefix:host})",
    "PART": "{ch} - {nick} (PART: {msg}) ({prefix:user}@{prefix:host})",
    "KICK": "{ch} - {nick} kicked by {kicker} ({msg})",
    "MODE": "{ch} * {nick} changed mode ({msg})",
    "TOPIC": "{ch} <{ch} TOPIC> {msg} (by {nick})",
    "SYSTEM": "[PDK] {orig}",
    "OTHER": None,
    "SIMPLE": None,
}
TALK_LOG_MSG = {
    "PRIVMSG": "[{sender} => {receiver}] {msg}",
    "NOTICE": "{{sender} -> {receiver}} {msg}",
}

_RNAME = {"#": "CS-", "&": "CA-", "+": "CP-", "!": "CE-"}


def _default_log(loglevel: int) -> Dict[str, Any]:
    return {
        "file": "${setting_name}-${channel_name}/%y%m%d.log",
        "time_format": "%H:%M:%S",
        "message_format": dict(DEFAULT_LOG_MSG),
    }


class Config:
    def __init__(self, manager, rcfile: Optional[str] = None):
        self.manager = manager
        self.rc = rcfile or os.environ.get("PADOKA_CONFIG", "padoka.yml")
        self._raw: Dict[str, Any] = {}
        self.bots: List[PDKBot] = []
        self.logger: Optional[PDKLogger] = None
        self._notify = NotifySocket()
        self.load_config(self.rc)

    # ------------------------------------------------------------------ load
    def load_config(self, rcfile: str) -> None:
        with open(rcfile, "r", encoding="utf-8") as f:
            self._raw = yaml.safe_load(f) or {}

        r = self._raw

        # system
        self.setting_name = r.get("setting_name") or \
            re.sub(r"\.(yml|yaml|rc)$", "", os.path.basename(rcfile))
        self.loglevel = int(r.get("loglevel", 2))
        if os.environ.get("PADOKA_DEBUG") and "loglevel" not in r:
            self.loglevel = 3
        self.padoka_server_name = r.get("padoka_server_name", "PadokaProgram")
        self.log_dir = r.get("log_dir", "./log")
        self.plugins_dir = r.get("plugins_dir", "./plugins")
        self.backlog_lines = int(r.get("backlog_lines", 20))
        self.filename_encoding = r.get("filename_encoding", "utf8")
        self.reconnect_delay = int(r.get("reconnect_delay", 150))

        # user / nick
        env_user = os.environ.get("USER") or "padoka"
        self.user = r.get("user", env_user)
        self.nick = r.get("nick", env_user)
        self.hostname = r.get("hostname", os.uname().nodename)
        self.servername = r.get("servername", "*")
        self.realname = r.get("realname", "padoka user")
        self.mode = r.get("mode")
        self.away_message = r.get("away_message", "away")
        self.away_nick = r.get("away_nick")
        self.quit_message = r.get("quit_message", "Quit Padoka")

        # client server
        cs = r.get("client_server", {}) or {}
        self.client_server_host = cs.get("host")
        self.client_server_port = cs.get("port")
        self.client_server_pass = cs.get("pass")
        self.client_server_sasl = bool(cs.get("sasl", True))
        self.client_server_acl = cs.get("acl")
        self.client_server_ssl_cert_file = cs.get("ssl_cert_file")
        self.client_server_ssl_key_file = cs.get("ssl_key_file")

        # log writers config
        self.default_log = {**_default_log(self.loglevel),
                            **(r.get("default_log") or {})}
        self.system_log = {"file": "${setting_name}-system.log",
                           "time_format": "%y/%m/%d-%H:%M:%S",
                           "message_format": dict(SYSTEM_LOG_MSG),
                           **(r.get("system_log") or {})}
        self.talk_log = {"file": "${setting_name}-talk/%y%m%d.log",
                         "time_format": self.default_log["time_format"],
                         "message_format": dict(TALK_LOG_MSG),
                         **(r.get("talk_log") or {})}
        self.debug_log = {"io": sys.stdout,
                          "time_format": "%y/%m/%d-%H:%M:%S",
                          "message_format": dict(SYSTEM_LOG_MSG),
                          **(r.get("debug_log") or {})}

        # filters (optional; callables configured later if present)
        self.privmsg_filter = r.get("privmsg_filter")
        self.notice_filter = r.get("notice_filter")
        self.primitive_filters = r.get("primitive_filters") or {}
        self.privmsg_filter_light = r.get("privmsg_filter_light") or []

        # channel info
        self.channel_info_raw = r.get("channel_info") or {}

        self.acl_object: Optional[acl_mod.ACL] = None

        # build derived values
        self.server_list = self._build_server_list(r.get("servers"))
        self.channel_info: Dict[str, dict] = {}
        self.default_channels: List[str] = []
        self.login_channels: List[str] = []

        # logwriters + logger (needed by channel_setting)
        self._make_default_logwriter()
        self.logger = PDKLogger(self.manager, self)
        self.logger.slog("load config: %s" % rcfile)

        self._channel_setting()

        self._acl_setting()
        self._load_bots(r.get("bots"))

    # ------------------------------------------------------------ servers
    def _build_server_list(self, servers) -> List[dict]:
        if not servers:
            return []
        out = []
        for si in servers:
            port = si.get("port", 6667)
            host = si["host"]
            passw = si.get("pass")
            sslp = si.get("ssl_params")
            if isinstance(port, list) or (isinstance(port, str) and ".." in port):
                for p in _expand_ports(port):
                    out.append({"host": host, "port": p, "pass": passw,
                                "ssl_params": sslp})
            else:
                out.append({"host": host, "port": int(port), "pass": passw,
                            "ssl_params": sslp})
        return out

    # ------------------------------------------------------------- channels
    def _channel_setting(self) -> None:
        for ch, setting in self.channel_info_raw.items():
            ch = self.identical_channel_name(ch)
            setting = dict(setting) if isinstance(setting, dict) else {}
            timing = setting.get("timing", "startup")
            if timing == "login":
                self.login_channels.append(ch)
            else:
                self.default_channels.append(ch)
            if not setting.get("logwriter"):
                lw = self._make_logwriter(setting.get("log")) \
                    or self.default_logwriter
                setting["logwriter"] = lw
            self.channel_info[ch] = setting

    # ------------------------------------------------------------ logwriters
    def _make_logwriter(self, log) -> Optional[object]:
        if not log:
            return None
        if isinstance(log, dict):
            if log.get("logwriter") is not None:
                return log["logwriter"]
            opts = {**self.default_log, **log}
            if log.get("io") is not None:
                return IOLogWriter(self, opts)
            return FileLogWriter(self, opts)
        if isinstance(log, str):
            opts = dict(self.default_log)
            opts["file"] = log
            return FileLogWriter(self, opts)
        raise ValueError("Unknown logwriter setting: %r" % log)

    def _make_default_logwriter(self) -> None:
        self.default_logwriter = self._make_logwriter(
            {"file": self.default_log["file"],
             "time_format": self.default_log["time_format"],
             "message_format": self.default_log["message_format"]}) or \
            IOLogWriter(self, self.default_log)
        self.system_logwriter = self._make_logwriter({
            "file": self.system_log["file"],
            "time_format": self.system_log["time_format"],
            "message_format": self.system_log["message_format"]})
        self.talk_logwriter = self._make_logwriter({
            "file": self.talk_log["file"],
            "time_format": self.talk_log["time_format"],
            "message_format": self.talk_log["message_format"]})
        self.debug_logwriter = IOLogWriter(self, self.debug_log)

    # ------------------------------------------------------------------ acl
    def _acl_setting(self) -> None:
        assert self.logger is not None
        if self.client_server_acl and not self.acl_object:
            self.acl_object = acl_mod.ACL(self.client_server_acl)
            self.logger.slog("ACL configured")

    # ----------------------------------------------------------------- bots
    def _load_bots(self, bot_specs) -> None:
        if not bot_specs:
            return
        for spec in bot_specs:
            if isinstance(spec, dict):
                if spec.get("disable"):
                    continue
                name = spec["name"]
                cfg = spec
            else:
                name = spec
                cfg = None
            bot = self._make_bot(name, cfg)
            if bot is not None:
                self.bots.append(bot)

    def _make_bot(self, name: str, cfg: Optional[dict]) -> Optional[PDKBot]:
        assert self.logger is not None
        cls = self._load_botfile(name)
        if cls is None:
            self.logger.slog("WARNING: bot not found: %s" % name)
            return None
        bot = cls(self.manager, self, cfg or {})
        self.logger.slog("bot instance: %s" % bot.bot_state())
        return bot

    def _load_botfile(self, name: str) -> Optional[type]:
        candidates = [name, name.lower(), _snake(name)]
        dirs = self.plugins_dir if isinstance(self.plugins_dir, (list, tuple)) \
            else [self.plugins_dir]
        for d in dirs:
            d = os.path.abspath(d)
            for cand in candidates:
                path = os.path.join(d, cand + ".py")
                if not os.path.exists(path):
                    continue
                mod = self._import_module(path)
                if mod is None:
                    continue
                # find the matching bot class
                cls = getattr(mod, name, None)
                if cls is None:
                    for attr in vars(mod).values():
                        if isinstance(attr, type) and issubclass(attr, PDKBot) \
                                and attr is not PDKBot:
                            cls = attr
                            break
                if cls is not None:
                    return cls
        return None

    @staticmethod
    def _import_module(path: str):
        module_name = "padoka_plugin_" + os.path.basename(path)[:-3]
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:  # pragma: no cover - surfaced via logger
            raise RuntimeError("failed to load bot %s: %s" % (path, e))
        return mod

    # ------------------------------------------------------- channel naming
    def canonical_channel_name(self, ch: str) -> str:
        ch = re.sub(r"^!\w{5}", "!", ch)
        return self.identical_channel_name(ch)

    def identical_channel_name(self, ch: str) -> str:
        return ch.lower().translate(str.maketrans("[]\\~", "{}|^"))

    # ------------------------------------------------------------ filenames
    def make_logfilename(self, tmpl: str, rch: str, cn: Optional[str]) -> str:
        if not cn:
            cn = re.sub(r"^!\w{5}", "!", rch)
            cn = cn.lower()
            m = re.match(r"^[&#!+]", cn)
            if m:
                cn = _RNAME[m.group(0)] + cn[1:]
            cn = cn.translate(str.maketrans("*:/", "__I"))
        str_ = datetime.now().strftime(tmpl)
        str_ = str_.replace("${setting_name}", self.setting_name)
        str_ = re.sub(r"\$\{channel_name\}|\{ch\}", cn, str_)
        return str_

    # ----------------------------------------------------------- log format
    def log_format(self, timefmt, msgfmts, msgobj) -> str:
        text = format_message(msgfmts or {}, msgobj,
                              self.default_log["message_format"])
        if timefmt and not msgobj.get("nostamp"):
            text = msgobj["time"].strftime(timefmt) + " " + text
        return text

    def log_format_message(self, msgfmts, msgobj) -> str:
        return format_message(msgfmts or {}, msgobj,
                              self.default_log["message_format"])

    # ------------------------------------------------------------ notify
    @property
    def notify(self) -> NotifySocket:
        return self._notify


def _expand_ports(port) -> List[int]:
    if isinstance(port, list):
        return [int(p) for p in port]
    m = re.match(r"^(\d+)\.\.(\d+)$", str(port))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return list(range(min(a, b), max(a, b) + 1))
    return [int(port)]


def _snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

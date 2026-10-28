# -*- coding: utf-8 -*-
"""
AutoDumpBot - 参加中のチャネル情報を自動でダンプするボット

参加中の各チャネルの名前・参加タイミング（startup/login）・キー・
初期モードを追跡し、最後のクライアントがログアウトした際にファイルへ
ダンプします。``/PADOKA dump`` でも手動ダンプできます。

YAML 設定:

    - name: autodumpbot
      auto_dump: true
      dump_style: "yaml"        # yaml / json / text
      dump_timing: "startup"    # startup / login
"""

import json
import os
import re

import yaml

from padoka.bot import PDKBot
from padoka.irc.message import Command
from padoka.errors import PDKBotSendCancel


class AutoDumpBot(PDKBot):
    MODE_WITH_NICK_ARG = "ov"
    MODE_WITH_ARGS = "klbeI"
    MODE_WITHOUT_ARGS = "aimnqpsrt"
    _CHAN_RE = re.compile(r"^[\#&\+\!]")

    def bot_initialize(self):
        self._auto_dump = self.bot_config.get("auto_dump", True)
        self._default_dump_style = self.bot_config.get("dump_style", "yaml")
        self._default_timing = self.bot_config.get("dump_timing", "startup")
        self._cinfo = {}
        for ch in self.state.channels():
            self.manager.send_to_server(Command.mode(ch))

    def on_join(self, prefix, ch):
        if self.state.nick() == prefix.nick:
            dch = self.ccn(ch)
            self._cinfo.setdefault(dch, {"name": ch, "mode": []})
            self._cinfo[dch]["name"] = ch
            self.manager.send_to_server(Command.mode(ch))

    def on_part(self, prefix, ch, msg=""):
        if self.manager.client_count() > 0 and self.state.nick() == prefix.nick:
            self._cinfo.pop(self.ccn(ch), None)

    def on_client_logout(self, count, client):
        if count == 1 and self._auto_dump:
            self.dump_cinfo()

    def on_mode(self, prefix, ch, mode, *args):
        if self._CHAN_RE.match(ch or ""):
            self._track(ch, mode, list(args))

    def on_rpl_channelmodeis(self, prefix, nick, ch, *args):
        if self._CHAN_RE.match(ch or ""):
            mode = args[0] if args else ""
            self._track(ch, mode, list(args[1:]))

    def _track(self, ch, mode, args):
        dch = self.ccn(ch)
        info = self._cinfo.setdefault(dch, {"name": ch, "mode": []})
        info["name"] = ch
        args = list(args)
        for token in (mode or "").split():
            flag = token[0]
            for m in token[1:]:
                if m in self.MODE_WITH_NICK_ARG:
                    if args:
                        args.pop(0)
                elif m in self.MODE_WITH_ARGS:
                    arg = args.pop(0) if args else ""
                    if flag == "+":
                        info["mode"].append("%s %s" % (m, arg))
                    else:
                        self._remove(info["mode"], m)
                elif m in self.MODE_WITHOUT_ARGS:
                    if flag == "+":
                        if m not in info["mode"]:
                            info["mode"].append(m)
                    else:
                        self._remove(info["mode"], m)

    @staticmethod
    def _remove(lst, m):
        for x in [y for y in lst if y == m or y.startswith(m + " ")]:
            lst.remove(x)

    def on_padoka_command(self, client, command, *params):
        if (command or "").lower() == "dump":
            self.dump_cinfo(params[0] if params else None)
            raise PDKBotSendCancel

    def dump_cinfo(self, style=None):
        style = style or self._default_dump_style
        channel_info = {}
        for ch, info in self._cinfo.items():
            name = info.get("name")
            cs = self.state.current_channels.get(ch)
            if cs:
                name = cs.name
            cinfo = {}
            if any(self.ccn(t) == ch for t in self.config.default_channels):
                cinfo["timing"] = "startup"
            elif any(self.ccn(t) == ch for t in self.config.login_channels):
                cinfo["timing"] = "login"
            else:
                cinfo["timing"] = self._default_timing
            modes = info.get("mode", [])
            if "i" in modes:
                cinfo["timing"] = "startup"
            for m in modes:
                if m.startswith("k "):
                    cinfo["key"] = m[2:]
            letters = [m for m in modes if len(m) == 1]
            if letters:
                cinfo["initial_mode"] = "+" + "".join(letters)
            channel_info[name] = cinfo

        log_dir = self.config.log_dir or "./log"
        dump_file = os.path.join(log_dir,
                                 self.config.setting_name + "-channel_info.dump")
        try:
            self._write_dump(channel_info, style, dump_file)
            self.logger.slog("current channel information was dumped (%s)" % style)
        except Exception as e:
            self.logger.slog("current channel information could not be dumped")
            self.logger.slog(str(e))

    def _write_dump(self, channel_info, style, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if style == "yaml":
            text = yaml.safe_dump(channel_info, allow_unicode=True,
                                  default_flow_style=False, sort_keys=False)
        elif style == "json":
            text = json.dumps(channel_info, ensure_ascii=False, indent=2)
        elif style == "text":
            text = self._to_text(channel_info)
        else:
            raise ValueError("unsupported dump style: %r" % style)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _to_text(channel_info):
        lines = ["Channel_info = {"]
        for ch, info in channel_info.items():
            lines.append("  %s => {" % ch)
            for tag, value in info.items():
                lines.append("    %s: %s," % (tag, value))
            lines.append("  },")
        lines.append("}")
        return "\n".join(lines) + "\n"

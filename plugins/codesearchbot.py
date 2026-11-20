# Copyright (c) 2026 Akira Inamori
#
# Padoka is a Python re-implementation of nadoka
# (https://github.com/nadoka/nadoka), which was itself modeled on
# madoka (https://www.madoka.org/), the original IRC client-server
# program by The madoka project.
#
# Ported from the madoka/nadoka plugin collection (same bot names,
# reimplemented in Python).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program. If not, see
# <https://www.gnu.org/licenses/>.

# -*- coding: utf-8 -*-
"""
CodeSearchBot - Debian コード検索ボット

``codesearch> keyword`` で codesearch.debian.net を検索し、
regexp / source のヒット数を通知します。

YAML 設定:

    - name: codesearchbot
      ch: ".*"
"""

import re
import urllib.request
from urllib.parse import quote

from padoka.bot import PDKBot


class CodeSearchBot(PDKBot):
    RESULT_RE = re.compile(
        r"number of (regexp results: \d+)<br>\s*number of (source results: \d+)")
    ENGINE_URI = {"debian": "http://codesearch.debian.net/"}

    def bot_initialize(self):
        if "channels" in self.bot_config:
            self._available_channel = re.compile(
                r"\A(?:" + "|".join(re.escape(c) for c in self.bot_config["channels"]) + r")\Z")
        else:
            ac = self.bot_config.get("ch")
            self._available_channel = re.compile(ac) if isinstance(ac, str) else (ac or re.compile(r".*"))

    def on_privmsg(self, prefix, ch, msg):
        m = re.match(r"\Acodesearch(\:([a-z]+))?>\s*(.+)", msg)
        if not m or not self._available_channel.search(ch):
            return
        self.run_async(self._do_result, ch, m.group(2), m.group(3))

    def _do_result(self, ch, engine, word):
        self.send_notice(ch, "codesearch%s bot: %s" % (
            ("#%s" % engine) if engine else "",
            self.result(engine, word)))

    def result(self, engine, key):
        engine = (engine or "debian").lower()
        base = self.ENGINE_URI.get(engine)
        if not base:
            return "unknown engine: %s" % engine
        url = "%ssearch?q=%s" % (base, quote(key))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PadokaCodeSearchBot/1.0"})
            with urllib.request.urlopen(req, timeout=15) as f:
                data = f.read().decode("utf-8", "replace")
        except Exception:
            return "%s - error fetching %s" % (key, url)
        m = self.RESULT_RE.search(data)
        if m:
            return "%s, %s for %s - %s" % (m.group(1), m.group(2), key, url)
        return "%s - not found in %s codesearch" % (key, engine)

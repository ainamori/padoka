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
OpenSearchBot - OpenSearch RSS 経由で検索結果数を取得するボット（汎用）

設定で指定した html / rss テンプレートの ``{searchTerms}`` にクエリを埋め込み、
RSS の ``<...:totalResults>`` から結果数を取得します。

YAML 設定:

    - name: opensearchbot
      bot_name: "bing"
      html: "http://www.bing.com/search?q={searchTerms}"
      rss: "http://api.search.live.com/rss.aspx?source=web&query={searchTerms}"
      referer: "https://github.com/padoka/padoka"
      ch: ".*"

利用:

    bing> keyword
"""

import re
import urllib.request
from urllib.parse import quote

from padoka.bot import PDKBot


class OpenSearchBot(PDKBot):
    def bot_initialize(self):
        if "channels" in self.bot_config:
            self._available_channel = re.compile(
                r"\A(?:" + "|".join(re.escape(c) for c in self.bot_config["channels"]) + r")\Z")
        else:
            ac = self.bot_config.get("ch")
            self._available_channel = re.compile(ac) if isinstance(ac, str) else (ac or re.compile(r".*"))
        self._bot_name = self.bot_config.get("bot_name", "OpenSearchBot")
        self._html = self.bot_config["html"]
        self._rss = self.bot_config["rss"]
        self._referer = self.bot_config.get("referer", "https://github.com/padoka/padoka")
        pat = self.bot_config.get("pattern")
        if pat is None:
            pat = r"\A%s\s*[<:>]\s*(.+)" % re.escape(self._bot_name)
        self._pattern = re.compile(pat)

    def on_privmsg(self, prefix, ch, msg):
        if not self._available_channel.search(ch):
            return
        m = self._pattern.search(msg)
        if m:
            self.run_async(self._do_result, ch, m.group(1))

    def _do_result(self, ch, word):
        self.send_notice(ch, "%s bot: %s" % (self._bot_name, self.result(word)))

    def result(self, key):
        escaped = quote(key)
        link = self._html.replace("{searchTerms}", escaped)
        uri = self._rss.replace("{searchTerms}", escaped)
        req = urllib.request.Request(
            uri, headers={"Referer": self._referer,
                          "User-Agent": "PadokaOpenSearchBot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as f:
                data = f.read().decode("utf-8", "replace")
        except Exception:
            return "%s - not found in %s" % (key, link)
        m = re.search(r"<([A-Za-z]+):totalResults>(\d+)</\1:totalResults>", data)
        if m:
            total = int(m.group(2))
            return "%s result%s in %s" % (
                format(total, ","), "s" if total > 1 else "", link)
        return "%s - not found in %s" % (key, link)

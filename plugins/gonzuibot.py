# -*- coding: utf-8 -*-
"""
GonzuiBot - Ruby API / モジュール検索ボット

``gonzui> keyword`` で Ruby の API ドキュメントを検索し、ヒット数を通知します。
``gonzui:engine> keyword`` で検索エンジンを指定できます。

利用可能なエンジン:

    raa (default)  - http://raa.ruby-lang.org/gonzui/
    gnome          - http://gonzui.tagonome.org/
    cpan           - http://cpansearch.bulknews.net/
    b-src          - http://b-src.cbrc.jp/

YAML 設定:

    - name: gonzuibot
      ch: ".*"
"""

import re
import urllib.request
from urllib.parse import quote

from padoka.bot import PDKBot


class GonzuiBot(PDKBot):
    RESULT_RE = re.compile(
        r">Results <strong\n>(\d+)</strong\n> - <strong\n>(\d+)</strong\n> of <strong\n>(\d+)</strong")
    ENGINE_URI = {
        "raa": "http://raa.ruby-lang.org/gonzui/",
        "gnome": "http://gonzui.tagonome.org/",
        "cpan": "http://cpansearch.bulknews.net/",
        "b-src": "http://b-src.cbrc.jp/",
    }

    def bot_initialize(self):
        ac = self.bot_config.get("ch")
        self._available_channel = re.compile(ac) if isinstance(ac, str) else (ac or re.compile(r".*"))

    def on_privmsg(self, prefix, ch, msg):
        if not self._available_channel.search(ch):
            return
        m = re.match(r"\Agonzui(?:\:([\w\-\+]+))?>\s*(.+)", msg)
        if m:
            self.send_notice(ch, "gonzui bot: %s" % self.result(m.group(1), m.group(2)))

    def result(self, engine, key):
        engine = (engine or "raa").lower()
        base = self.ENGINE_URI.get(engine)
        if not base:
            return "unknown engine: %s" % engine
        url = "%ssearch?q=%s" % (base, quote(key))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PadokaGonzuiBot/1.0"})
            with urllib.request.urlopen(req, timeout=15) as f:
                data = f.read().decode("utf-8", "replace")
        except Exception:
            return "%s - error fetching %s" % (key, url)
        m = self.RESULT_RE.search(data)
        if m:
            return "%s for %s - %s" % (m.group(3), key, url)
        return "%s - not found in %s" % (key, engine)

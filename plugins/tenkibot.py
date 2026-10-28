# -*- coding: utf-8 -*-
"""
TenkiBot - 気象庁天気予報ボット

気象庁の天気予報ページ (https://www.jma.go.jp/jp/yoho/) から都道府県ごとの
天気テキストサマリーを取得して通知します。

利用:

    tenki>                 -> 利用可能な都道府県名を例示
    tenki> 東京            -> 東京の天気予報

注: 旧版は Nokogiri で天気テーブル（降水確率/気温）を全て取得していましたが、
標準ライブラリのみで堅牢に HTML スクレイピングするため、本実装は JMA の
テキストフレーム (pre.textframe) のサマリーを返します。都道府県名はインデックス
ページから自動取得します。

YAML 設定:

    - name: tenkibot
      ch: ".*"
      timeout: 15
"""

import re
import urllib.request

from padoka.bot import PDKBot


class TenkiBot(PDKBot):
    INDEX_URL = "https://www.jma.go.jp/jp/yoho/"

    def bot_initialize(self):
        ac = self.bot_config.get("ch")
        self._available_channel = re.compile(ac) if isinstance(ac, str) else (ac or re.compile(r".*"))
        self._timeout = float(self.bot_config.get("timeout", 15))
        self._area = {}
        self._init_area()

    def _fetch(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "PadokaTenkiBot/1.0"})
        with urllib.request.urlopen(req, timeout=self._timeout) as f:
            return f.read().decode("utf-8", "replace")

    def _init_area(self):
        try:
            html = self._fetch(self.INDEX_URL)
        except Exception:
            return
        for m in re.finditer(r'<a[^>]+href="(\d+)\.html"[^>]*>([^<]+)</a>', html):
            self._area[m.group(2).strip()] = self.INDEX_URL + m.group(1) + ".html"

    @staticmethod
    def _strip_tags(html):
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()

    def on_privmsg(self, prefix, ch, msg):
        if not self._available_channel.search(ch):
            return
        if self.has_same_bot(ch):
            return
        m = re.match(r"\Atenki>\s*(.*)$", msg)
        if not m:
            return
        area = m.group(1).strip()
        try:
            results = self._tenki(area)
        except Exception as e:
            results = ["%s" % e]
        for r in results:
            self.send_notice(ch, "tenki bot: %s" % re.sub(r"\s+", " ", r))

    def _tenki(self, area):
        url = self._area.get(area)
        if not url:
            matches = [k for k in self._area if area and area in k]
            if len(matches) == 1:
                area, url = matches[0], self._area[matches[0]]
            else:
                examples = list(self._area.keys())[:10]
                raise ValueError("例: %s" % ", ".join(examples))
        html = self._fetch(url)

        texts = []
        m = re.search(r'<pre[^>]*class="textframe"[^>]*>(.*?)</pre>', html, re.S | re.I)
        if m:
            texts.append(self._strip_tags(m.group(1)))
        if not texts:
            m = re.search(r"<pre[^>]*>(.*?)</pre>", html, re.S | re.I)
            if m:
                texts.append(self._strip_tags(m.group(1)))
        if not texts:
            raise ValueError("天気情報が取得できませんでした (%s)" % url)

        texts.insert(0, area)
        return texts

# -*- coding: utf-8 -*-
"""
RSS_CheckBot - RSS / Atom フィードの更新チェックボット

設定したフィードを定期チェックし、新しいエントリがあれば通知します。
初回チェックはベースライン取得のみで通知しませんが、以降は更新分を通知します。

RSS 2.0 / RSS 1.0(RDF) / Atom を xml.etree で解析します。

YAML 設定:

    - name: rss_checkbot
      rss_paths:
        - "https://www.python.org/feed/"
        - "https://example.com/atom.xml"
      ch: "#padoka_check"
      tm: 30                    # チェック間隔（分）
      over_message: nil         # 末尾に追加メッセージ（省略可）
"""

import threading
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from padoka.bot import PDKBot

_ATOM = "{http://www.w3.org/2005/Atom}"


class _Feed:
    def __init__(self, url):
        self.url = url
        self._last = None  # baseline (datetime, aware)

    @staticmethod
    def _parse_date(s):
        if not s:
            return None
        s = s.strip()
        try:
            return parsedate_to_datetime(s)
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _get(self):
        req = urllib.request.Request(self.url,
                                     headers={"User-Agent": "PadokaRSSBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as f:
            return f.read()

    def _extract(self, data):
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return []
        out = []
        # RSS 2.0 / RDF items
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            date = self._parse_date(it.findtext("pubDate")
                                     or it.findtext("dc:date"))
            out.append((link, title, date))
        # Atom entries
        for e in root.iter(_ATOM + "entry"):
            title = (e.findtext(_ATOM + "title") or "").strip()
            le = e.find(_ATOM + "link")
            link = (le.get("href") or "") if le is not None else ""
            date = self._parse_date(e.findtext(_ATOM + "updated")
                                    or e.findtext(_ATOM + "published"))
            out.append((link, title, date))
        return out

    def check(self):
        items = self._extract(self._get())
        dates = [d for _, _, d in items if d is not None]
        if self._last is None:
            # baseline on first sight; report nothing to avoid spam
            self._last = max(dates) if dates else datetime.now(timezone.utc)
            return []
        now = datetime.now(timezone.utc)
        new = []
        for about, title, d in items:
            dt = d or now
            if dt > self._last:
                self._last = dt
                new.append({"about": about, "title": title})
        return new


class RSS_CheckBot(PDKBot):
    def bot_initialize(self):
        self._paths = self.bot_config.get(
            "rss_paths", ["https://www.python.org/feed/"])
        self._ch = self.bot_config.get("ch", "#padoka_check")
        self._tm = int(self.bot_config.get("tm", 30))
        self._over = self.bot_config.get("over_message")
        self._prevtm = datetime.now()
        self._feeds = [_Feed(u) for u in self._paths]

    def bot_state(self):
        nt = datetime.fromtimestamp(self._prevtm.timestamp() + self._tm * 60)
        return "#<RSS_CheckBot: next check at %s@%s>" % (nt, self._ch)

    def on_timer(self, tm):
        self.check()

    def check(self):
        tm = datetime.now()
        if tm.timestamp() - self._tm * 60 > self._prevtm.timestamp():
            self._prevtm = tm
            self.make_notice()

    def make_notice(self):
        items = []
        try:
            for feed in self._feeds:
                items.extend(feed.check())
        except Exception as e:
            self.send_notice(self._ch, "rss bot error: %s" % e)
            self.manager.ndk_error(e)
            return
        threading.Thread(target=self._notify, args=(items,),
                         daemon=True).start()

    def _notify(self, items):
        for e in items:
            title = e["title"].replace("\uFF5E", "\u301C")  # 全角チルダ -> 波ダッシュ
            self.send_notice(self._ch, "rss bot: %s - %s" % (title, e["about"]))
        if self._over:
            self.send_notice(self._ch, "rss bot: %s" % self._over)

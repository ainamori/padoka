# -*- coding: utf-8 -*-
"""
TitleBot - URL のタイトルを自動取得して通知するボット

PRIVMSG で URL（http:// 或 https://）が送信されると、そのページの
HTML <title> を取得して NOTICE で返信します。

- 外部ネットワークへのアクセスのみ（localhost / RFC1918 は無視 - SSRF 防止）
- タイムアウト（デフォルト 10 秒）
- レスポンス 1MB 超過はスキップ
- 並列取得を避けるため、単一ワーカーキュー方式

YAML 設定:

    - name: titlebot
      ch: ".*"                 # 対象チャネル（regex、デフォルトは全チャネル）
      timeout: 10              # 秒
      user_agent: "PadokaTitleBot/1.0"
      headers:                 # 追加 HTTP ヘッダー
        Accept-Language: "ja"
"""

import queue
import re
import socket
import threading
import urllib.request
import urllib.error
from urllib.parse import urlparse

from padoka.bot import PDKBot

# SSRF 防止: 内部ホストを拒否
_BLOCK_HOSTS = (
    re.compile(r"\blocalhost\b", re.I),
    re.compile(r"^\s*127\."),
    re.compile(r"^\s*192\.168\."),
    re.compile(r"^\s*10\."),
    re.compile(r"^\s*169\.254\."),
    re.compile(r"^\s*\[?::1\]?"),
    re.compile(r"^\s*0\.0\.0\.0"),
)

# HTML <title>...</title> 抽出（大文字小文字無視、WS 圧縮）
_TITLE_RE = re.compile(
    r"<title[^>]*>(.*?)</title>",
    re.I | re.S,
)
_WS_RE = re.compile(r"\s+")


def _html_unescape(s: str) -> str:
    """Minimal HTML entity unescape (covers common cases)."""
    replacements = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
        "&apos;": "'",
        "&nbsp;": " ",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    return s


def _fetch_title(url: str, timeout: float, headers: dict) -> str:
    """Fetch a URL and return its <title>. Raises on error."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # Limit read to 1 MB
        body = resp.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError("response too large")
        charset = resp.headers.get_content_charset() or "utf-8"
        try:
            text = body.decode(charset, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
    m = _TITLE_RE.search(text)
    if not m:
        return ""
    title = _html_unescape(m.group(1)).strip()
    return _WS_RE.sub(" ", title)


class TitleBot(PDKBot):
    def bot_initialize(self):
        self._ch = self.bot_config.get("ch", ".*")
        if isinstance(self._ch, str):
            self._ch = re.compile(self._ch)
        self._timeout = float(self.bot_config.get("timeout", 10))
        self._headers = dict(self.bot_config.get("headers") or {})
        self._headers.setdefault("User-Agent",
                                 self.bot_config.get("user_agent",
                                                     "PadokaTitleBot/1.0"))
        self._headers.setdefault("Accept", "text/html,application/xhtml+xml")
        self._headers.setdefault("Accept-Encoding", "identity")
        self._queue: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True,
                                        name="titlebot-worker")
        self._thread.start()

    def on_privmsg(self, prefix, ch, msg):
        if not self._ch.search(ch):
            return
        # Find first http(s) URL in the message
        m = re.search(r"https?://\S+", msg)
        if not m:
            return
        url = m.group(0)
        # SSRF check
        try:
            host = urlparse(url).hostname or ""
        except ValueError:
            return
        if not host:
            return
        for pat in _BLOCK_HOSTS:
            if pat.search(host):
                self.send_notice(ch, "(title: internal host ignored)")
                return
        self._queue.put((ch, url))

    def _worker(self):
        while True:
            ch, url = self._queue.get()
            try:
                title = _fetch_title(url, self._timeout, self._headers)
                if title:
                    self.send_notice(ch, "%s - %s" % (title[:100], url))
                else:
                    self.send_notice(ch, "(title: not found) %s" % url)
            except urllib.error.HTTPError as e:
                self.send_notice(ch, "(title: HTTP %s) %s" % (e.code, url))
            except urllib.error.URLError as e:
                self.send_notice(ch, "(title: %s) %s" % (e.reason, url))
            except (socket.timeout, TimeoutError):
                self.send_notice(ch, "(title: timeout) %s" % url)
            except ValueError as e:
                self.send_notice(ch, "(title: %s) %s" % (e, url))
            except Exception:
                self.send_notice(ch, "(title: error) %s" % url)

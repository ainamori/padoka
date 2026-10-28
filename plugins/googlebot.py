# -*- coding: utf-8 -*-
"""
GoogleBot - Google カスタム検索 (CSE) ボット + 電卓

注意: 旧版は Google AJAX Search API を使用していましたが、この API は
非推奨・廃止のため、本実装は **Custom Search JSON API** をバックエンドに
使います。検索には ``api_key`` と ``cx`` の設定が必須です。

利用:

    g> keyword            -> CSE 検索（上位結果 + 合計ヒット数）
    google> keyword       -> 同上
    googlec> w1 w2 w3     -> 各単語のヒット数（最大 googlec_maxwords 語）
    g:calc> 2+2*3         -> 電卓（安全な数式評価のみ）

YAML 設定:

    - name: googlebot
      api_key: "YOUR_API_KEY"
      cx: "YOUR_CSE_ID"
      ch: ".*"
      googlec_maxwords: 5
      search_default_lang: "ja"
"""

import ast
import json
import operator
import re
import urllib.request
from typing import Callable, Dict
import urllib.error
from urllib.parse import quote

from padoka.bot import PDKBot

_CALC_OPS: Dict[type, Callable[..., object]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_calc(expr):
    """Evaluate a pure arithmetic expression (no names, no calls)."""

    def _ev(node):
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _CALC_OPS:
            return _CALC_OPS[type(node.op)](_ev(node.left), _ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _CALC_OPS:
            return _CALC_OPS[type(node.op)](_ev(node.operand))
        raise ValueError("unsupported expression")

    return _ev(ast.parse(expr, mode="eval"))


class GoogleBot(PDKBot):
    def bot_initialize(self):
        if "channels" in self.bot_config:
            self._available_channel = re.compile(
                r"\A(?:" + "|".join(re.escape(c) for c in self.bot_config["channels"]) + r")\Z")
        else:
            ac = self.bot_config.get("ch")
            self._available_channel = re.compile(ac) if isinstance(ac, str) else (ac or re.compile(r".*"))
        self._search_default_lang = re.sub(r"^lang_", "",
                                           self.bot_config.get("search_default_lang") or "ja")
        self._googlec_maxwords = int(self.bot_config.get("googlec_maxwords", 5))
        self._api_key = self.bot_config.get("api_key")
        self._cx = self.bot_config.get("cx")
        self._uri_slog = self.bot_config.get("uri_slog", False)

    def on_privmsg(self, prefix, ch, msg):
        if not self._available_channel.search(ch):
            return
        # calculator: g:calc> / google:calc> / calc>
        m = re.match(r"^\s*(?:g(?:oogle)?\s*:\s*)?calc\s*>\s*(.+)$", msg, re.I)
        if m:
            try:
                val = safe_calc(m.group(1).strip())
                self.send_notice(ch, "calc: %s = %s" % (m.group(1).strip(), val))
            except ZeroDivisionError:
                self.send_notice(ch, "calc: division by zero")
            except Exception as e:
                self.send_notice(ch, "calc: %s" % e)
            return
        # search: g> / google>
        m = re.match(r"^\s*g(?:oogle)?\s*>\s*(.+)$", msg)
        if m:
            self.send_notice(ch, "google bot: %s" % self._search(m.group(1).strip()))
            return
        # counts: googlec>
        m = re.match(r"^\s*googlec\s*>\s*(.+)$", msg)
        if m:
            words = m.group(1).split()[:self._googlec_maxwords]
            if not words:
                return
            parts = ["%s: %s" % (w, self._count(w)) for w in words]
            self.send_notice(ch, "googlec bot: %s" % ", ".join(parts))

    def _cse(self, word, num=1):
        if not (self._api_key and self._cx):
            return None, "CSE api_key and cx are required"
        uri = ("https://www.googleapis.com/customsearch/v1?key=%s&cx=%s&q=%s&num=%d"
               % (quote(self._api_key), quote(self._cx), quote(word), num))
        if self._uri_slog:
            self.logger.slog("GoogleBot: %s" % uri)
        req = urllib.request.Request(uri, headers={"User-Agent": "PadokaGoogleBot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as f:
                return json.loads(f.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            try:
                reason = json.loads(e.read().decode("utf-8"))["error"]["errors"][0]["reason"]
            except Exception:
                reason = "HTTP %s" % e.code
            return None, reason
        except Exception as e:
            return None, str(e)

    def _search(self, word):
        result, err = self._cse(word, num=1)
        if err:
            return err
        try:
            count = int(result["searchInformation"]["totalResults"])
        except (KeyError, TypeError, ValueError):
            count = 0
        if count == 0:
            return "no match"
        items = result.get("items") or []
        if not items:
            return "%s hits" % format(count, ",")
        item = items[0]
        return "%s - %s (and %s hits)" % (item["title"], item["link"], format(count, ","))

    def _count(self, word):
        result, err = self._cse(word, num=1)
        if err:
            return err
        try:
            return format(int(result["searchInformation"]["totalResults"]), ",")
        except (KeyError, TypeError, ValueError):
            return "0"

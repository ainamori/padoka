# -*- coding: utf-8 -*-
"""
MessageBot - 伝言板ボット

指定チャネルで ``伝言 <nick> <本文>`` とすると、その nick が次にチャネルで
発言した際に伝言を通知します。伝言は JSON ファイルに永続化されます。

YAML 設定:

    - name: messagebot
      message_file: "message.json"
      root_key: "mypadoka"         # デフォルトは setting_name
      channels:
        - "#padoka"
        - "#padoka_check"
"""

import json
import os
import re
from datetime import datetime

from padoka.bot import PDKBot


class MessageBot(PDKBot):
    def bot_initialize(self):
        self._file = self.bot_config.get("message_file", "message.json")
        self._root_key = self.bot_config.get("root_key", self.config.setting_name)
        self._channels = [ch.lower() for ch in self.bot_config.get("channels", [])]
        self._list = {}
        self._message = {}
        self._load()

    def _load(self):
        if os.path.exists(self._file):
            try:
                with open(self._file, encoding="utf-8") as f:
                    data = json.load(f)
                h = data.get(self._root_key, {})
                self._list = h.get("list", {})
                self._message = h.get("message", {})
            except (json.JSONDecodeError, OSError):
                self._list, self._message = {}, {}

    def _save(self):
        data = {self._root_key: {"list": self._list, "message": self._message}}
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def on_privmsg(self, prefix, ch, msg):
        c = ch.lower()
        if c not in self._channels:
            return
        user = prefix.nick
        u = user.lower()
        now = datetime.now()
        key = "%s %s" % (c, u)
        message_id = "<%s.%d.%s@%s>" % (now.strftime("%Y%m%d%H%M%S"),
                                        now.microsecond, u, c)

        # Deliver any pending messages addressed to this user.
        if key in self._list:
            for mid in self._list[key]:
                h = self._message.get(mid)
                if not h or "delivered" in h:
                    continue
                self.send_notice(ch, "%sさんから%sさんへ伝言「%s」"
                                 % (h["from"], h["to"], h["body"]))
                h["delivered"] = now.isoformat()
            del self._list[key]
            self._save()

        # Accept a new message.
        m = re.match(r"^伝言 (\S+) (.+)$", msg)
        if m:
            to_nick, body = m.group(1), m.group(2)
            self._message[message_id] = {
                "from": user,
                "to": to_nick,
                "date": now.isoformat(),
                "channel": ch,
                "body": body,
            }
            tkey = "%s %s" % (c, to_nick.lower())
            self._list.setdefault(tkey, []).append(message_id)
            self._save()
            self.send_notice(ch, "%sさんへの伝言を承りました > %sさん"
                             % (to_nick, user))

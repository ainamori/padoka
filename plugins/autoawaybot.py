# -*- coding: utf-8 -*-
"""
AutoAwayBot - 自動 Away 管理ボット

一定時間クライアントからの操作がない場合、自動的に AWAY 状態になります。
クライアントが再び操作した際に AWAY を解除します。

YAML 設定:

    - name: autoawaybot
      threshold: 900        # 秒（デフォルト 15分）
      message: "Away"       # AWAY メッセージ
"""

from datetime import datetime

from padoka.bot import PDKBot
from padoka.irc.message import Command


class AutoAwayBot(PDKBot):
    def bot_initialize(self):
        self._threshold = int(self.bot_config.get("threshold", 15 * 60))
        self._message = self.bot_config.get("message", "Away")
        self._last_seen = datetime.now()
        self._in_away = False

    def on_client_privmsg(self, client, ch, msg):
        if self._threshold > 0:
            self._last_seen = datetime.now()
        if self._in_away:
            self.manager.send_to_server(Command.away())

    def on_timer(self, now):
        if (self._threshold > 0 and
                (now - self._last_seen).total_seconds() > self._threshold and
                not self._in_away):
            self.manager.send_to_server(Command.away(self._message))

    def on_rpl_unaway(self, *arg):
        """305 - RPL_UNAWAY"""
        self._in_away = False

    def on_rpl_nowaway(self, *arg):
        """306 - RPL_NOWAWAY"""
        self._in_away = True

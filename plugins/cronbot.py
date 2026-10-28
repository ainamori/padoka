# -*- coding: utf-8 -*-
"""
CronBot - 定期メッセージ送信ボット（雛形）

on_timer イベント（約 60 秒ごと）に、参加中の各チャネルへメッセージを送ります。
本番では on_timer を cron 式で制御し、特定時刻にのみ発火させる拡張が望ましいです。

YAML 設定:

    - name: cronbot
      message: "Hello from cronbot"
      # 特定チャネルにだけ送る場合は:
      ch: "#specific"
"""

import re

from padoka.bot import PDKBot


class CronBot(PDKBot):
    def bot_initialize(self):
        self._message = self.bot_config.get("message", "Hello from cronbot")
        self._ch = self.bot_config.get("ch")
        if self._ch is not None and isinstance(self._ch, str):
            self._ch = re.compile(self._ch)

    def on_timer(self, now):
        for ch in self.state.channels():
            if self._ch is not None and not self._ch.match(ch):
                continue
            self.send_notice(ch, "%s (%s)" % (self._message, now))

# -*- coding: utf-8 -*-
"""
SendPingBot - 定期 PING 送信ボット

on_timer イベント（約 60 秒ごと）にクライアント全員へ PING を送ります。
接続の健全性維持や、クライアント側の自動再接続機構を促すのに役立ちます。

YAML 設定:

    - name: sendpingbot
"""

from padoka.bot import PDKBot


class SendPingBot(PDKBot):
    def on_timer(self, *args):
        self.manager.ping_to_clients()

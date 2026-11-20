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

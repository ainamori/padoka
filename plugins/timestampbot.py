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
TimeStampBot - ログにタイムスタンプを挿入するボット

一定間隔で参加中のチャネルログに区切り（タイムスタンプ）を挿入します。

YAML 設定:

    - name: timestampbot
      interval: 3600                          # 秒（デフォルト 1時間）
      stampformat: "== %y/%m/%d-%H:%M:%S ===="
      client: false                           # true: システムログにも出力
"""

import time
from datetime import datetime

from padoka.bot import PDKBot


class TimeStampBot(PDKBot):
    def bot_initialize(self):
        self._interval = int(self.bot_config.get("interval", 3600))
        self._stampformat = self.bot_config.get(
            "stampformat", "== %y/%m/%d-%H:%M:%S ==========================================")
        self._client = self.bot_config.get("client", False)
        self._nexttime = self._calc_nexttime()

    def _calc_nexttime(self) -> datetime:
        now = time.time()
        t = now + self._interval
        aligned = t - (t % self._interval)
        return datetime.fromtimestamp(aligned)

    def on_timer(self, now: datetime) -> None:
        if now >= self._nexttime:
            self._stamp_log()
            self._nexttime = self._calc_nexttime()

    def _stamp_log(self) -> None:
        msg = self._nexttime.strftime(self._stampformat)
        for ch in self.state.channels():
            self.logger.clog(ch, msg, nostamp=True)
        if self._client:
            self.logger.slog(msg, nostamp=True)

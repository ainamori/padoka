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
CheckBot - 定期チェック用ボットの基底クラス

約 60 秒ごとの on_timer を受け、設定した間隔（分）を超えた時点で
``need_check()`` / ``check()`` を呼び出します。子类で ``check()`` を
オーバーライドして具体的なチェックを実装します。

YAML 設定:

    - name: mycheckbot       # CheckBot を継いだ子类
      ch: "#padoka"          # 通知先（文字列 or リスト）
      tm: 30                 # チェック間隔（分、デフォルト 30）
"""

import time
from datetime import datetime

from padoka.bot import PDKBot


class CheckBot(PDKBot):
    def bot_initialize(self):
        self._ch = self.bot_config.get("ch", "#padoka")
        self._tm = int(self.bot_config.get("tm", 30))
        self._prevtm = datetime.now()
        self.botheader = "checkbot"

    def on_timer(self, tm):
        if self.time_ok() and self.need_check():
            self.check()

    def time_ok(self):
        tm = datetime.now()
        if tm.timestamp() - self._tm * 60 > self._prevtm.timestamp():
            self._prevtm = tm
            return True
        return False

    def check_notice(self, text):
        if isinstance(self._ch, (list, tuple)):
            for ch in self._ch:
                self.send_notice(ch, "%s: %s" % (self.botheader, text))
                time.sleep(5)  # flood protection
        else:
            self.send_notice(self._ch, "%s: %s" % (self.botheader, text))
            time.sleep(5)

    def need_check(self):
        return True

    def check(self):
        # override me
        pass

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
IdentifyNickServ - 接続時に NickServ /identify を自動実行するボット

IRC サーバーへの接続が完了したとき、設定されたパスワードで NickServ に
IDENTIFY を送信します（登録済み nick の自動識別）。

YAML 設定:

    - name: identifynickserv
      nickserv: "NickServ"     # NickServ のニックネーム
      nick: "padoka"           # 識別対象 nick（省略可）
      pass: "your_password"    # 未設定の場合は実行しない
"""

from padoka.bot import PDKBot


class IdentifyNickServ(PDKBot):
    def bot_initialize(self):
        self._nickserv = self.bot_config.get("nickserv", "NickServ")
        self._nick = self.bot_config.get("nick", False)
        self._pass = self.bot_config.get("pass", False)

    def on_server_connected(self, *args):
        if self._pass:
            if self._nick:
                self.send_privmsg(self._nickserv,
                                  "IDENTIFY %s %s" % (self._nick, self._pass))
            else:
                self.send_privmsg(self._nickserv, "IDENTIFY %s" % self._pass)

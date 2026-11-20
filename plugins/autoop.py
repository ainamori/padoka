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
AutoOP - 指定ユーザーが参加した際に自動で OP 権限を与えるボット

YAML 設定:

    - name: autoop
      friends:
        - nick: "trusted_user1"
        - nick: "trusted_user2"
        - user: "linux_username"   # 実ユーザ名でマッチ（~ 接頭辞を除去）
"""

import re

from padoka.bot import PDKBot


class AutoOP(PDKBot):
    def bot_initialize(self):
        self._friends = self.bot_config.get("friends", [])

    def _is_friend(self, prefix) -> bool:
        for friend in self._friends:
            if "nick" in friend and friend["nick"] == prefix.nick:
                return True
            if "user" in friend and prefix.user:
                # strip leading ~ from ident
                matched = re.match(r"^~?(.*)$", prefix.user)
                if matched and matched.group(1) == friend["user"]:
                    return True
        return False

    def on_join(self, prefix, ch) -> None:
        # only act if we have OP in this channel
        my_mode = self.state.channel_user_mode(ch, self.state.nick()) or ""
        if "o" not in my_mode:
            return
        # don't OP ourselves
        if prefix.nick == self.state.nick():
            return
        if self._is_friend(prefix):
            self.change_mode(ch, "+o", prefix.nick)

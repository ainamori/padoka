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

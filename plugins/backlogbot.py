# -*- coding: utf-8 -*-
"""
BackLogBot - メッセージバックログ再送ボット

クライアント接続時に、直近のメッセージ履歴を再送します。
接続中の欠損を埋めるのに役立ちます。

YAML 設定（bots エントリー）:

    - name: backlogbot
      clear: false            # true の場合、送出後にバックログをクリア
      talker: false           # true の場合、bot 自身の nick として再送
      prefix: "BL"
      sender: "backlogbot"
      time_format: "%m/%d-%H:%M"
"""

from padoka.bot import PDKBot
from padoka.irc.message import Command


class BackLogBot(PDKBot):
    def bot_initialize(self):
        self._stores = self.logger.message_stores
        self._talker = self.bot_config.get("talker", False)
        self._clear = self.bot_config.get("clear", False)
        self._prefix = self.bot_config.get("prefix", "BL")
        self._sender = self.bot_config.get("sender", "backlogbot")
        self._tmfmt = self.bot_config.get("time_format", "%m/%d-%H:%M")
        self._msgfmts = self.bot_config.get(
            "message_format", self.config.default_log["message_format"])
        self._pattern = self.bot_config.get("pattern", None)
        if self._pattern is not None and isinstance(self._pattern, str):
            import re
            self._pattern = re.compile(self._pattern)

    def on_client_login(self, client_count, client):
        for ch, msgobjs in self._stores.each_channel_pool():
            if self.state.current_channels.get(ch):
                for msgobj in msgobjs:
                    msg = self.config.log_format_message(self._msgfmts, msgobj)
                    rch = msgobj["ch"]
                    time = msgobj["time"]
                    cmd = Command.notice(
                        rch, "%s(%s) %s" % (self._prefix,
                                            time.strftime(self._tmfmt), msg))
                    client.add_prefix2(cmd, self.state.nick() if self._talker
                                       else self._sender)
                    client.push(cmd)
            else:
                for msgobj in msgobjs:
                    if ch == "__talk__":
                        msg = self.config.log_format_message(
                            self.config.talk_log["message_format"], msgobj)
                    else:
                        msg = self.config.log_format_message(
                            self.config.system_log["message_format"], msgobj)
                    if not (self._pattern is None or
                            self._pattern.search(msg or "")):
                        continue
                    rch = msgobj["ch"]
                    time = msgobj["time"]
                    cmd = Command.notice(
                        self.state.nick(), "%s(%s) %s"
                        % (self._prefix, time.strftime(self._tmfmt), msg))
                    client.push(cmd)
        if self._clear:
            self._stores.clear()

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

# -*- coding: utf-8 -*-
"""
SampleBot - サンプルボット

すべての PRIVMSG に対して NOTICE で応答します。
クライアント → サーバー → ボット → クライアント のループを確認するための
テスト用プラグインです。
"""

from padoka.bot import PDKBot


class SampleBot(PDKBot):
    def on_privmsg(self, prefix, ch, msg):
        self.send_notice(ch, "Yes, %s!" % prefix.nick)

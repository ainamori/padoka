# -*- coding: utf-8 -*-
"""
OpShop - 参加者に自動で OP 権限を販売（付与）するボット

自分が参加しているチャネルで、他のユーザーが参加すると自動的に +o
（OP）を付与します。

YAML 設定:

    - name: opshop
"""

from padoka.bot import PDKBot


class OpShop(PDKBot):
    def on_join(self, prefix, ch):
        if prefix.nick != self.state.nick():
            self.change_mode(ch, "+o", prefix.nick)

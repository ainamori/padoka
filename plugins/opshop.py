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

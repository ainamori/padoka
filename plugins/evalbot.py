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
EvalBot - IRC 経由で Python 式を評価するボット

セキュリティ上、チャンネルではなく **専用ニックネーム宛の個人メッセージ**
(``padoka_eval``) のみを受け付けます。評価は 3 秒タイムアウト付きの
サブプロセスで実行し、親プロセスとは分離します。

利用:

    /PADOKA eval                 -> エvaluator が起動したことを表示
    /WHISPER padoka_eval <式>    -> 式を評価して "ans: <結果>" で返信

⚠️  注意: 任意の Python 式が実行されます。信頼できるクライアントからのみ
   接続を許可するよう ACL で制限してください。

YAML 設定:

    - name: evalbot
"""

import subprocess
import sys

from padoka.bot import PDKBot
from padoka.irc.message import Command
from padoka.errors import PDKBotSendCancel


class EvalBot(PDKBot):
    EVAL_NICK = "padoka_eval"

    def on_client_privmsg(self, client, ch, message):
        if ch == self.EVAL_NICK:
            ans = self.eval_message(message)
            msg = Command.privmsg(self.state.nick(), "ans: " + ans)
            msg.prefix = self.EVAL_NICK
            client.send_to_client(msg)
            raise PDKBotSendCancel

    def on_padoka_command(self, client, command, *params):
        if (command or "").lower() == "eval":
            msg = Command.privmsg(self.state.nick(),
                                  "Hello, this is a python evaluator")
            msg.prefix = self.EVAL_NICK
            client.send_to_client(msg)
            raise PDKBotSendCancel

    def eval_message(self, message):
        code = "print(eval(%r))" % message
        try:
            proc = subprocess.run([sys.executable, "-c", code],
                                  capture_output=True, text=True, timeout=3)
            out = (proc.stdout + proc.stderr).strip()
            return out or "OK"
        except subprocess.TimeoutExpired:
            return "timeout"
        except Exception as e:
            return str(e)

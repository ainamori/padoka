# -*- coding: utf-8 -*-
"""
ShellBot - IRC 経由で shell コマンドを実行するボット

セキュリティ上、チャンネルではなく **専用ニックネーム宛の個人メッセージ**
(``padoka_shell``) のみを受け付けます。実行は 3 秒タイムアウト付き。

利用:

    /PADOKA shell                -> シェルエグゼキュータが起動したことを表示
    /WHISPER padoka_shell <cmd>  -> コマンドを実行し "ans: <結果>" で返信

⚠️  注意: 任意の shell コマンドが実行されます。信頼できるクライアントからのみ
   接続を許可するよう ACL で制限してください。

YAML 設定:

    - name: shellbot
"""

import subprocess

from padoka.bot import PDKBot
from padoka.irc.message import Command
from padoka.errors import PDKBotSendCancel


class ShellBot(PDKBot):
    SHELL_NICK = "padoka_shell"

    def on_client_privmsg(self, client, ch, message):
        if ch == self.SHELL_NICK:
            for line in self.exec_shell(message):
                msg = Command.privmsg(self.state.nick(), "ans: " + line)
                msg.prefix = self.SHELL_NICK
                client.send_to_client(msg)
            raise PDKBotSendCancel

    def on_padoka_command(self, client, command, *params):
        if (command or "").lower() == "shell":
            msg = Command.privmsg(self.state.nick(),
                                  "Hello, this is a shell command executor")
            msg.prefix = self.SHELL_NICK
            client.send_to_client(msg)
            raise PDKBotSendCancel

    def exec_shell(self, message):
        try:
            proc = subprocess.run(message, shell=True,
                                  capture_output=True, text=True, timeout=3)
            out = (proc.stdout + proc.stderr)
            return out.splitlines() or ["OK"]
        except subprocess.TimeoutExpired:
            return ["timeout"]
        except Exception as e:
            return [str(e)]

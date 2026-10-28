# -*- coding: utf-8 -*-
"""
RouletteBot - ランダム抽選 / シャッフル / ランダム数値ボット

使い方（PRIVMSG）:

    roulette> A B C        -> 抽選結果を 1 つ通知
    shuffle> A B C D       -> 単語をランダム順に並べ替え
    random>                -> 1〜1000 のランダム数値
    random> 100            -> 1〜100 のランダム数値

YAML 設定:

    - name: roulettebot
      ch: ".*"
"""

import random
import re
import shlex

from padoka.bot import PDKBot


class RouletteBot(PDKBot):
    def bot_initialize(self):
        self._available_channel = self.bot_config.get("ch") or re.compile(r".*")
        if isinstance(self._available_channel, str):
            self._available_channel = re.compile(self._available_channel)

    def on_privmsg(self, prefix, ch, msg):
        if not self._available_channel.search(ch):
            return

        m = re.match(r"\Aroulette>\s*(.+)\s*", msg)
        if m:
            items = self._split(m.group(1))
            if items:
                random.shuffle(items)
                self.send_notice(ch, "roulette bot: %s" % items[0])
            return

        m = re.match(r"\Ashuffle>\s*(.+)\s*", msg)
        if m:
            items = self._split(m.group(1))
            random.shuffle(items)
            self.send_notice(ch, "shuffle bot: %s" % " ".join(items))
            return

        m = re.match(r"\Arandom>\s*((\d+)|)", msg)
        if m:
            num = int(m.group(2)) if m.group(2) else 1000
            self.send_notice(ch, "random bot: %s -> %d" % (prefix.nick, random.randrange(num)))

    @staticmethod
    def _split(text):
        try:
            return shlex.split(text)
        except ValueError:
            return text.split()

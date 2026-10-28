# -*- coding: utf-8 -*-
"""
XiBot - サイコロ振りボット（CoC 風）

使い方（PRIVMSG）:

    xi> 2d10        -> [2d10] 13 = 7 + 6
    xi> 5d          -> [5d6] 14 = 3 + 1 + 3 + 1 + 6
    xi> 100         -> [1d100] 26
    xi> character   -> 6 属性それぞれ 3d6 の合計
    xi> char        -> 6 属性それぞれ 4d6 の上 3 個の合計
    xi> san         -> コール・オブ・クトゥルフ風 SAN ロール

YAML 設定:

    - name: xibot
      ch: ".*"        # 対象チャネル（regex、デフォルトは全て）
"""

import random
import re

from padoka.bot import PDKBot

_ATTRS = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
_INDEFINITE = (
    "緊張症・痴呆症",
    "記憶喪失",
    "サンチョ・パンザ症、ドンキホーテ症",
    "偏執症",
    "恐怖症、フェティッシュ",
    "強迫観念、中毒、けいれん発作",
    "誇大妄想",
    "精神分裂症",
    "犯罪性精神異常",
    "多重人格",
)


class XiBot(PDKBot):
    def bot_initialize(self):
        self._available_channel = self.bot_config.get("ch") or re.compile(r".*")
        if isinstance(self._available_channel, str):
            self._available_channel = re.compile(self._available_channel)

    def dice(self, count=1, max_=6):
        for _ in range(count):
            count += random.randrange(max_)
        return count

    def on_privmsg(self, prefix, ch, msg):
        if not self._available_channel.search(ch):
            return
        m = re.match(r"\Axi\s*>\s*", msg)
        if not m:
            return
        rest = msg[m.end():].lower()

        if "character" in rest:
            for name in _ATTRS:
                values = [random.randint(1, 6) for _ in range(3)]
                total = sum(values)
                self.send_notice(ch, "%s: %2d = %s" % (name, total, " + ".join(map(str, values))))
        elif "char" in rest:
            parts = []
            for name in _ATTRS:
                rolls = sorted(random.randint(1, 6) for _ in range(4))
                total = sum(rolls[-3:])
                parts.append("%s: %2d" % (name, total))
            self.send_notice(ch, "%s: %s" % (prefix.nick, ", ".join(parts)))
        elif "san" in rest:
            self.send_notice(ch, "%s: %s" % (prefix.nick, self._san_check()))
        else:
            self._dice_roll(prefix, ch, rest)

    def _dice_roll(self, prefix, ch, rest):
        dm = re.search(r"(?:(\d+)d)?(\d+)?(?:\*([1-9]))?", rest)
        if dm is None:
            return
        count = int(dm.group(1)) if dm.group(1) else 0
        count = 1 if not (1 <= count <= 100) else count
        max_ = int(dm.group(2)) if dm.group(2) else 0
        max_ = 6 if not (1 <= max_ <= 1000000000) else max_
        mult = int(dm.group(3)) if dm.group(3) else 1
        for _ in range(mult):
            values = [random.randint(1, max_) for _ in range(count)]
            total = sum(values)
            if count == 1:
                self.send_notice(ch, "%s: [%dd%d] %d" % (prefix.nick, count, max_, total))
            else:
                self.send_notice(ch, "%s: [%dd%d] %d = %s" % (
                    prefix.nick, count, max_, total, " + ".join(map(str, values))))

    def _san_check(self):
        int_ = self.dice(2, 6) + 6
        pow_ = self.dice(3, 6)
        san0 = pow_ * 5
        current = san0
        result = "int:%d pow:%d san0:%d" % (int_, pow_, san0)

        roll = random.randrange(10)
        if roll == 9:
            result += " you saw Great Cthulhu."
            losts = [self.dice(1, 10), self.dice(1, 100)]
        elif roll in (7, 8):
            result += " you saw a living dead."
            losts = [1, self.dice(1, 10)]
        elif roll in (4, 5, 6):
            result += " you saw a Dimension-Shambler."
            losts = [0, self.dice(1, 10)]
        elif roll in (2, 3):
            result += " you woke up in the grave."
            losts = [0, self.dice(1, 6)]
        else:
            result += " you find a dead body."
            losts = [0, self.dice(1, 3)]

        check = self.dice(1, 100)
        result += " check:%d" % check
        lost = losts[1] if check > current else losts[0]

        insane = False
        if lost > 0:
            result += " you lost %d SAN point." % lost
            if lost >= current:
                result += " you went mad. (eternal)"
                insane = True
            elif lost * 5 > current:
                result += " you went mad. (indefinite %s)" % _INDEFINITE[random.randrange(10)]
                insane = True
            elif lost >= 5:
                idearoll = self.dice(1, 100)
                result += " idearoll:%d" % idearoll
                if idearoll <= int_ * 5:
                    result += " you went mad. (temporary)"
                    insane = True
        if not insane:
            result += " you kept sanity."
        return result

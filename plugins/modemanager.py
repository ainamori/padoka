# -*- coding: utf-8 -*-
"""
ModeManager - 参加時にモードを自動で付与/解除するボット

設定ファイル（またはインライン文字列）で「誰にどんなモードを適用するか」を
定義します。指定ユーザーが参加したとき、自分がそのチャネルで OP なら、
ランダムな遅延の後に MODE を送ります。

設定形式:

    [<チャネル名> or 'all']
    <+-><mode> <pattern> ...

    <mode>  : b / v / o（組み合わせ可、例 +ov）
    <pattern>: glob 形式（例 *.jp）または /正規表現/

例:

    [all]
    +o=*.jp
    +o=/.*\\.net/

    [#padoka]
    +o=kiyoya* unak*
    -o=ko1*

YAML 設定:

    - name: modemanager
      file: "modes"            # or: modes: |
      # modes: |
      #   [all]
      #   +o=*.jp
      wait: 5                  # 適用までの待機（秒、平均）
"""

import fnmatch
import random
import re
import threading
import time
from collections import defaultdict

from padoka.bot import PDKBot


class _Fnmatch:
    def __init__(self, pattern):
        self.pattern = pattern.lower()

    def match(self, s):
        return fnmatch.fnmatchcase(s.lower(), self.pattern)


class ModeManager(PDKBot):
    def bot_initialize(self):
        self._userlist = defaultdict(list)
        path = self.bot_config.get("modes_file") or self.bot_config.get("file")
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    self.parse_setting(f.read())
            except OSError:
                pass
        setting = self.bot_config.get("modes")
        if setting:
            self.parse_setting(setting)
        self._wait = int(self.bot_config.get("wait", 0))
        self._wait = 3 if self._wait <= 0 else self._wait

    def parse_setting(self, setting):
        s = None
        for raw in setting.splitlines():
            line = raw.strip()
            if not line:
                continue
            m = re.match(r"^\[(all|[\#\&\!\+].+)\]", line)
            if m:
                s = self.config.identical_channel_name(m.group(1))
                continue
            m = re.match(r"^\[%(.+)\]", line)
            if m:
                s = self.config.identical_channel_name("#%s:*.jp" % m.group(1))
                continue
            if s:
                mm = re.match(r"^([+-][bvo]+)\s*=\s*(.*)$", line)
                if mm:
                    mode = mm.group(1)
                    for token in mm.group(2).split():
                        # /regex/ form: the trailing slash is part of the
                        # delimiter, not the pattern (matches the original).
                        rm = re.match(r"^/(.*)/$", token)
                        if rm:
                            self._userlist[s].append(
                                (mode, re.compile(rm.group(1), re.I)))
                        else:
                            self._userlist[s].append((mode, _Fnmatch(token)))

    def _search_in_list(self, entries, prefix):
        for mode, matcher in entries:
            if isinstance(matcher, re.Pattern):
                if matcher.search(prefix):
                    return mode
            elif matcher.match(prefix):
                return mode
        return None

    def _search_mode(self, ch, prefix):
        return (self._search_in_list(self._userlist.get("all", []), prefix)
                or self._search_in_list(self._userlist.get(ch, []), prefix))

    def on_join(self, prefix, rch):
        ch = self.config.canonical_channel_name(rch)
        full = str(prefix)
        nick = prefix.nick
        delay = random.random() * self._wait * 2
        rch_local = rch

        def work():
            time.sleep(delay)
            if nick == self.state.nick():
                return
            my_mode = self.state.channel_user_mode(ch, self.state.nick()) or ""
            if "o" not in my_mode:
                return
            mode = self._search_mode(ch, full)
            if not mode:
                return
            current = self.state.channel_user_mode(ch, nick) or ""
            chars = mode[1:]
            if mode.startswith("+"):
                noneed = all(c in current for c in chars)
            else:
                noneed = all(c not in current for c in chars)
            if not noneed:
                self.change_mode(rch_local, mode, nick)

        threading.Thread(target=work, daemon=True).start()

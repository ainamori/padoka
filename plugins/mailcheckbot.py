# -*- coding: utf-8 -*-
"""
MailCheckBot - メール受信チェックボット（biff）

Maildir または mh フォーマットのディレクトリを監視し、新しいメールを検出
するとテンプレート付きで通知（biff）します。message-id で既通知を追跡します。

テンプレートのプレースホルダー: ``%{field}s``（文字列）/ ``%{field}d``（数値）。
例: ``biff: %{subject}s``

YAML 設定:

    - name: mailcheckbot
      maildir:
        - "~/Maildir/.ml.example"
      # または mh:
      # mh_dir:
      #   - "~/Mail/ml/example"
      channels:
        - "#padoka"
      template: "biff: %{subject}s"
"""

import glob
import os
import re
from email.header import decode_header, make_header
from typing import List

from padoka.bot import PDKBot


class MailCheckBot(PDKBot):
    class Error(Exception):
        pass

    class _MailInfo:
        def __init__(self, patterns):
            self._patterns = patterns
            self._biffed = {}

        def entries(self):
            files = []
            for p in self._patterns:
                files.extend(glob.glob(p))
            return files

        @staticmethod
        def _decode(value):
            try:
                return str(make_header(decode_header(value)))
            except Exception:
                return value

        def mailread(self, filename):
            fields = {}
            message_id = None
            try:
                header_lines = []
                with open(filename, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.rstrip("\r\n")
                        if line.strip() == "":
                            break
                        header_lines.append(line)
            except OSError:
                return None, {}

            # unfold continuation lines
            unfolded: List[str] = []
            for line in header_lines:
                if line[:1] in (" ", "\t") and unfolded:
                    unfolded[-1] += " " + line.strip()
                else:
                    unfolded.append(line)

            for line in unfolded:
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                key = key.strip().lower()
                value = value.strip()
                if key == "message-id":
                    message_id = value
                fields[key] = self._decode(value)
            return message_id, fields

        def update_entries(self, callback):
            old = list(self._biffed.keys())
            new = self.entries()
            for fn in new:
                mid, fields = self.mailread(fn)
                if not mid:
                    continue
                if mid in self._biffed:
                    continue
                callback(fields)
                self._biffed[mid] = True
            if old and not new:
                self._biffed.clear()

    def bot_initialize(self):
        self._m_infos = {}
        self._channels = [ch.lower() for ch in self.bot_config.get("channels", [])]
        self._template = self.bot_config.get("template", "biff: %{subject}s")
        maildir = self.bot_config.get("maildir") or self.bot_config.get("Maildir")
        mh_dir = self.bot_config.get("mh_dir")

        if maildir and mh_dir:
            raise self.Error("both :maildir and :mh_dir found in config")
        if maildir:
            for d in self._as_list(maildir):
                p = os.path.expanduser(d)
                self._m_infos[d] = self._MailInfo(
                    [os.path.join(p, "cur", "*"), os.path.join(p, "new", "*")])
        elif mh_dir:
            for d in self._as_list(mh_dir):
                p = os.path.expanduser(d)
                self._m_infos[d] = self._MailInfo([os.path.join(p, "*")])
        else:
            raise self.Error(":maildir or :mh_dir not found in config")

    @staticmethod
    def _as_list(x):
        return x if isinstance(x, list) else [x]

    def bot_state(self):
        keys = list(self._m_infos.keys())
        base = os.path.basename(keys[0]) if keys else ""
        if len(keys) > 1:
            base += ", ..."
        return "#<MailCheckBot: %s -> %s>" % (base, " ".join(self._channels))

    def on_timer(self, t):
        for m_info in self._m_infos.values():
            m_info.update_entries(
                lambda fields: self._bark(self._apply_template(fields)))

    def _apply_template(self, fields):
        def sub(m):
            name = m.group(1).lower()
            typ = m.group(2)
            val = fields.get(name, "")
            if typ == "s":
                return val
            if typ == "d":
                try:
                    return str(int(val))
                except ValueError:
                    return val
            return "(field type bug: `%s`)" % typ
        return re.sub(r"%\{([a-z0-9_\-]+)\}([sd])", sub, self._template, flags=re.I)

    def _bark(self, msg):
        for ch in self._channels:
            self.send_notice(ch, msg)

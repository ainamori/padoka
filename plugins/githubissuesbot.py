# -*- coding: utf-8 -*-
"""
GithubIssuesBot - GitHub Issues / コメント更新チェックボット

約 60 秒ごとの on_timer を監視し、設定間隔（分）ごとに対象リポジトリの
開いている issues / コメント更新と閉じられた issues をチェックして通知します。

YAML 設定:

    - name: githubissuesbot
      ch: "#padoka_check"
      tm: 30                    # チェック間隔（分）
      owner: "padoka"
      repo: "padoka"
      # token: "ghp_xxx"        # 任意: ラートリミット増加のため
"""

import json
import re
import urllib.request
from datetime import datetime, timezone

from padoka.bot import PDKBot

DEFAULT_HEADER = {"User-Agent": "Padoka-GithubIssuesBot/0.1"}


def _read(uri, header):
    req = urllib.request.Request(uri, headers=header)
    with urllib.request.urlopen(req, timeout=15) as f:
        return f.read().decode("utf-8")


def _time_to_s(ts):
    if not ts:
        return ""
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return t.astimezone().strftime("%H:%M ")
    except ValueError:
        return ""


def _user_to_s(user):
    return "@%s " % user["login"] if user else ""


def _issue_to_s(issue):
    if not issue:
        return None
    return "%s [%s] %s%s%s" % (
        issue["html_url"], issue["state"],
        _time_to_s(issue.get("updated_at")),
        _user_to_s(issue.get("user")),
        issue["title"])


def _comment_to_s(comment, issue=None):
    if not comment:
        return None
    info = "[%s] " % issue["state"] if issue else ""
    return "%s %s%s%s%s" % (
        comment["html_url"], info,
        _time_to_s(comment.get("updated_at")),
        _user_to_s(comment.get("user")),
        comment["body"])


def issues(owner, repo, since, state="open", header=None):
    """Yield (kind, obj, formatted_string) for updates since `since`."""
    hdr = dict(DEFAULT_HEADER)
    if header:
        hdr.update(header)
    since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = "https://api.github.com/repos/%s/%s" % (owner, repo)
    data = json.loads(_read("%s/issues?since=%s&state=%s"
                            % (base, since_iso, state), hdr))
    for issue in data:
        comments = json.loads(_read("%s?since=%s"
                                    % (issue["comments_url"], since_iso), hdr))
        if not comments:
            s = _issue_to_s(issue)
            if s:
                yield ("issue", issue, s)
        else:
            for c in comments:
                s = _comment_to_s(c, issue)
                if s:
                    yield ("comment", c, s)


class GithubIssuesBot(PDKBot):
    def bot_initialize(self):
        self._ch = self.bot_config.get("ch", "#padoka_check")
        self._tm = int(self.bot_config.get("tm", 30))
        self._prevtm = datetime.now()
        self._owner = self.bot_config.get("owner", "padoka")
        self._repo = self.bot_config.get("repo", "padoka")
        self._header = {}
        token = self.bot_config.get("token")
        if token:
            self._header["Authorization"] = "token %s" % token

    def send_notice(self, ch, msg):
        super().send_notice(ch, re.sub(r"\s+", " ", msg))

    def bot_state(self):
        nt = self._prevtm.timestamp() + self._tm * 60
        return "#<GithubIssuesBot: next check at %s@%s>" % (
            datetime.fromtimestamp(nt).strftime("%a %b %d %H:%M:%S %Y"), self._ch)

    def on_timer(self, tm):
        self.check()

    def check(self):
        tm = datetime.now()
        if tm.timestamp() - self._tm * 60 > self._prevtm.timestamp():
            self.make_notice(tm)

    def make_notice(self, tm):
        since = self._prevtm
        self._prevtm = tm
        try:
            for _, _, s in issues(self._owner, self._repo, since, "open", self._header):
                self.send_notice(self._ch, s)
            for _, _, s in issues(self._owner, self._repo, since, "closed", self._header):
                self.send_notice(self._ch, s)
        except Exception as e:
            self.send_notice(self._ch,
                             "github issues bot error for %s/%s: %s"
                             % (self._owner, self._repo, e))
            self.manager.ndk_error(e)

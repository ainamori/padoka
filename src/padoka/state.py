"""
Channel / membership / nick state tracking.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Dict, List, Optional

from .irc.message import Command

if TYPE_CHECKING:
    from .config import Config
    from .logger import PDKLogger

__all__ = ["ChannelState", "PDKState"]


class ChannelState:
    MODE_WITH_NICK_ARG = "ov"
    MODE_WITH_ARGS = "klbeI"
    MODE_WITHOUT_ARGS = "aimnqpsrt"

    def __init__(self, name: str):
        self.name = name
        self.topic: Optional[str] = None
        self.member: Dict[str, str] = {}  # nick -> mode string

    # -- accessors ---------------------------------------------------------
    def members(self) -> List[str]:
        return list(self.member.keys())

    def mode(self, nick: str) -> Optional[str]:
        return self.member.get(nick)

    def names(self) -> List[str]:
        out = []
        for nick, mode in self.member.items():
            if "o" in (mode or ""):
                p = "@"
            elif "v" in (mode or ""):
                p = "+"
            else:
                p = ""
            out.append(p + nick)
        return out

    @property
    def state(self) -> str:
        return "="

    def clear_members(self) -> None:
        self.member = {}

    # -- event handlers ----------------------------------------------------
    def on_join(self, nick: str, mode: str = "") -> None:
        self.member[nick] = mode

    def on_part(self, nick: str) -> None:
        self.member.pop(nick, None)

    def on_nick(self, nick: str, newnick: str) -> bool:
        if nick in self.member:
            self.member[newnick] = self.member[nick]
            del self.member[nick]
            return True
        return False

    def on_kick(self, nick: str) -> None:
        self.member.pop(nick, None)

    def on_mode(self, nick: str, args: List[str]) -> None:
        args = list(args)
        while args:
            mode = args.pop(0)
            flag = mode[0]
            for m in mode[1:]:
                if m in self.MODE_WITH_NICK_ARG:
                    target = args.pop(0) if args else None
                    if target is not None:
                        self._chg_mode(target, flag, m)
                elif m in self.MODE_WITH_ARGS:
                    if args:
                        args.pop(0)
                # MODE_WITHOUT_ARGS: ignore

    def _chg_mode(self, nick: str, flag: str, mode: str) -> None:
        if nick in self.member:
            if flag == "+":
                self.member[nick] = (self.member[nick] or "") + mode
            elif flag == "-":
                self.member[nick] = (self.member[nick] or "").replace(mode, "")

    def __repr__(self):  # pragma: no cover
        return "ChannelState(%s: %r)" % (self.name, dict(self.member))


_NICK_LETTERS = "abcdefghijklmnopqrstuvwxyz0123456789"


def _succ(nick: str) -> str:
    """Increment the trailing character of a nick string."""
    last = nick[-1]
    if last == "z":
        return nick[:-1] + "{"
    if last == "9":
        return nick[:-1] + ":"
    return nick[:-1] + chr(ord(last) + 1)


class PDKState:
    def __init__(self, manager):
        self.manager = manager
        self.config: Optional["Config"] = None
        self.logger: Optional["PDKLogger"] = None
        self.current_nick: Optional[str] = None
        self.original_nick: Optional[str] = None
        self._try_nick: Optional[str] = None
        self.current_channels: Dict[str, ChannelState] = {}

    # -- nick --------------------------------------------------------------
    def set_nick(self, n: str) -> None:
        self._try_nick = None
        self.current_nick = n

    def nick(self) -> Optional[str]:
        return self.current_nick

    def nick_succ(self, fail_nick: str) -> str:
        if self._try_nick:
            if len(self._try_nick) == len(fail_nick):
                self._try_nick = _succ(self._try_nick)
            else:
                self._try_nick = fail_nick[:-1] + "0"
        else:
            self._try_nick = fail_nick + "0"
        return self._try_nick

    # -- channels ----------------------------------------------------------
    def channels(self) -> List[str]:
        return list(self.current_channels.keys())

    def channel_raw_names(self) -> List[str]:
        return [cs.name for cs in self.current_channels.values()]

    def channel_users(self, ch: str) -> List[str]:
        cs = self.current_channels.get(ch)
        return cs.members() if cs else []

    def channel_user_mode(self, ch: str, user: str) -> str:
        if user in self.channel_users(ch):
            return self.current_channels[ch].mode(user) or ""
        return ""

    def canonical_channel_name(self, ch: str) -> str:
        if self.config is None:
            return ch
        return self.config.canonical_channel_name(ch)

    def clear_channels_member(self) -> None:
        for cs in self.current_channels.values():
            cs.clear_members()

    # -- event handlers ----------------------------------------------------
    def on_join(self, user: str, rch: str) -> None:
        ch = self.canonical_channel_name(rch)
        if user == self.current_nick:
            self.current_channels[ch] = ChannelState(rch)
        elif ch in self.current_channels:
            self.current_channels[ch].on_join(user)

    def on_part(self, user: str, rch: str) -> None:
        ch = self.canonical_channel_name(rch)
        if user == self.current_nick:
            self.current_channels.pop(ch, None)
        elif ch in self.current_channels:
            self.current_channels[ch].on_part(user)

    def on_nick(self, user: str, newnick: str, msg) -> None:
        if user == self.current_nick:
            self.current_nick = newnick
            self._try_nick = None
        for ch, chs in list(self.current_channels.items()):
            if chs.on_nick(user, newnick):
                if self.config is not None and self.config.logger is not None:
                    self.config.logger.logging_nick(ch, chs.name, user, newnick, msg)

    def on_quit(self, user: str, qmsg: str, msg) -> None:
        if user == self.current_nick:
            self.current_channels = {}
        else:
            for ch, chs in list(self.current_channels.items()):
                if chs.member and user in chs.member:
                    chs.on_part(user)
                    if self.config is not None and self.config.logger is not None:
                        self.config.logger.logging_quit(ch, chs.name, user, qmsg, msg)
                    self.manager.invoke_event(
                        "invoke_bot", "quit_from_channel", chs.name, user, qmsg)

    def on_mode(self, user: str, rch: str, args: List[str]) -> None:
        ch = self.canonical_channel_name(rch)
        if ch in self.current_channels:
            self.current_channels[ch].on_mode(user, args)

    def on_kick(self, kicker: str, rch: str, user: str, comment: str) -> None:
        ch = self.canonical_channel_name(rch)
        if user == self.current_nick:
            self.current_channels.pop(ch, None)
        elif ch in self.current_channels:
            self.current_channels[ch].on_kick(user)

    def on_topic(self, user: str, rch: str, topic: str) -> None:
        ch = self.canonical_channel_name(rch)
        if ch in self.current_channels:
            self.current_channels[ch].topic = topic

    def on_332(self, rch: str, topic: str) -> None:
        ch = self.canonical_channel_name(rch)
        if ch in self.current_channels:
            self.current_channels[ch].topic = topic

    def on_353(self, rch: str, users: str) -> None:
        ch = self.canonical_channel_name(rch)
        cs = self.current_channels.get(ch)
        if not cs:
            return
        for e in users.split(" "):
            m = re.match(r"^([@+])?(.+)$", e)
            if not m:
                continue
            if m.group(1) == "@":
                mode = "o"
            elif m.group(1) == "+":
                mode = "v"
            else:
                mode = ""
            cs.on_join(m.group(2), mode)

        # apply initial mode
        chinfo = self.config.channel_info.get(ch) if self.config else None
        im = chinfo.get("initial_mode") if chinfo else None
        if im and len(cs.members()) == 1:
            self.manager.send_to_server(Command.mode(rch, im))

    def safe_channel(self, ch: str) -> bool:
        return ch[:1] == "!"

    def on_403(self, ch: str) -> None:
        if self.safe_channel(ch) and ch[1:2] != "!":
            chinfo = self.config.channel_info.get(ch) if self.config else None
            if chinfo and chinfo.get("auto_create"):
                self.manager.join_to_channel("!" + ch)

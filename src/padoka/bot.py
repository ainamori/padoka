"""
Bot plugin base class.

Subclass :class:`PDKBot` and implement ``on_<event>`` handlers.
The server dispatches events by calling a method named ``on_<selector>``.

Selector naming:
* commands  -> lower-cased IRC command, e.g. PRIVMSG -> ``on_privmsg``
* numeric   -> reply name lower-cased, e.g. 001 -> ``on_rpl_welcome``
* events    -> event name, e.g. ``on_client_login``, ``on_timer``

Handler signature:
* IRC messages: ``on_xxx(self, prefix, *params)``
  (``prefix`` is a :class:`~padoka.server.PrefixObject`)
* Synthetic events: ``on_xxx(self, *args)``

Available handlers:

    on_privmsg(prefix, ch, msg)
    on_notice(prefix, ch, msg)
    on_join(prefix, ch)
    on_part(prefix, ch, msg='')
    on_quit(prefix, msg='')
    on_nick(prefix, newnick)
    on_kick(prefix, ch, victim, comment)
    on_mode(prefix, ch, *args)
    on_topic(prefix, ch, topic)
    on_timer(now)
    on_client_login(client_count, client)
    on_client_logout(client_count, client)
    on_quit_from_channel(ch, nick, qmsg)
    on_server_connected()
    on_every_message(prefix, command, *args)
"""

from __future__ import annotations

import re
from typing import Optional

from .irc.message import Command

__all__ = ["PDKBot"]


class PDKBot:
    def __init__(self, manager, config, bot_config: Optional[dict] = None):
        self.manager = manager
        self.config = config
        self.logger = config.logger
        self.state = manager.state
        self.bot_config = bot_config or {}
        self.bot_initialize()

    # -- lifecycle ---------------------------------------------------------
    def bot_initialize(self) -> None:
        """Override to set up the bot (called from ``__init__``)."""

    def bot_destruct(self) -> None:
        """Override to clean up (called on config reload)."""

    def bot_state(self) -> str:
        info = "#<%s: %r>" % (type(self).__name__, self.bot_config)
        return info[:100] + "..." if len(info) > 100 else info

    @property
    def bot_config_dict(self):
        return self.bot_config

    # -- channel / bot helpers --------------------------------------------
    def bot_init_utils(self) -> None:
        self.bot_init_available_channel()
        self.bot_init_same_bot()

    def bot_init_available_channel(self) -> None:
        if "channels" in self.bot_config:
            pat = r"\A(?:" + "|".join(
                re.escape(ch) for ch in self.bot_config["channels"]) + r")\Z"
            self.available_channel = re.compile(pat)
        else:
            self.available_channel = self.bot_config.get("ch") or re.compile(r".*")

    def bot_init_same_bot(self) -> None:
        self.same_bot = self.bot_config.get("same_bot") or re.compile(r"(?!")
        self.same_bot = re.compile(self.same_bot) \
            if isinstance(self.same_bot, str) else self.same_bot

    def has_same_bot(self, ch) -> bool:
        for x in self.state.channel_users(self.ccn(ch)):
            if re.search(self.same_bot, x):
                return True
        return False

    def ccn2rcn(self, ccn: str) -> str:
        chs = self.state.current_channels.get(ccn)
        return chs.name if chs else ccn

    # -- sending -----------------------------------------------------------
    def send_notice(self, ch, msg) -> None:
        rch = self.ccn2rcn(ch)
        m = Command.notice(rch, msg)
        self.manager.send_to_server(m)
        self.manager.send_to_clients_otherwise(m, None)

    def send_privmsg(self, ch, msg) -> None:
        rch = self.ccn2rcn(ch)
        m = Command.privmsg(rch, msg)
        self.manager.send_to_server(m)
        self.manager.send_to_clients_otherwise(m, None)

    def change_mode(self, ch, mode, user) -> None:
        rch = self.ccn2rcn(ch)
        self.send_msg(Command.mode(rch, mode, user))

    def change_nick(self, nick) -> None:
        self.send_msg(Command.nick(nick))

    def send_msg(self, msg) -> None:
        self.manager.send_to_server(msg)

    # -- alias -------------------------------------------------------------
    def ccn(self, ch) -> str:
        return self.config.canonical_channel_name(ch)

    canonical_channel_name = ccn

"""
IRC message parsing / serialization.

Parses and builds RFC1459 IRC protocol messages.  Also provides the
numeric-reply → name table used to construct bot dispatch selectors
(``on_rpl_welcome``, ``on_err_nosuchchannel``, etc.).
"""

from __future__ import annotations

from typing import List, Optional

__all__ = ["IRCMessage", "ReplyName", "Command"]


# ---------------------------------------------------------------------------
# Numeric reply / error name table.
# Bot handlers are named after the *lower-cased* value here, e.g. ``001``
# -> ``rpl_welcome`` so a bot may implement ``on_rpl_welcome``.
# ---------------------------------------------------------------------------
_REPLY_NAMES = {
    "001": "RPL_WELCOME", "002": "RPL_YOURHOST", "003": "RPL_CREATED",
    "004": "RPL_MYINFO", "005": "RPL_BOUNCE", "010": "RPL_REDIR",
    "015": "RPL_MAP", "016": "RPL_MAPMORE", "017": "RPL_MAPEND",
    "018": "RPL_MAPSTART", "020": "RPL_HELLO", "042": "RPL_YOURID",
    "043": "RPL_SAVENICK",
    "302": "RPL_USERHOST", "303": "RPL_ISON", "301": "RPL_AWAY",
    "304": "RPL_TEXT", "305": "RPL_UNAWAY", "306": "RPL_NOWAWAY",
    "311": "RPL_WHOISUSER", "312": "RPL_WHOISSERVER",
    "313": "RPL_WHOISOPERATOR", "317": "RPL_WHOISIDLE",
    "318": "RPL_ENDOFWHOIS", "319": "RPL_WHOISCHANNELS",
    "314": "RPL_WHOWASUSER", "369": "RPL_ENDOFWHOWAS",
    "321": "RPL_LISTSTART", "322": "RPL_LIST", "323": "RPL_LISTEND",
    "325": "RPL_UNIQOPIS", "324": "RPL_CHANNELMODEIS", "331": "RPL_NOTOPIC",
    "332": "RPL_TOPIC", "333": "RPL_TOPICWHOTIME", "341": "RPL_INVITING",
    "342": "RPL_SUMMONING", "344": "RPL_REOPLIST",
    "345": "RPL_ENDOFREOPLIST", "346": "RPL_INVITELIST",
    "347": "RPL_ENDOFINVITELIST", "348": "RPL_EXCEPTLIST",
    "349": "RPL_ENDOFEXCEPTLIST", "351": "RPL_VERSION",
    "352": "RPL_WHOREPLY", "315": "RPL_ENDOFWHO", "353": "RPL_NAMREPLY",
    "366": "RPL_ENDOFNAMES", "364": "RPL_LINKS", "365": "RPL_ENDOFLINKS",
    "367": "RPL_BANLIST", "368": "RPL_ENDOFBANLIST", "371": "RPL_INFO",
    "374": "RPL_ENDOFINFO", "375": "RPL_MOTDSTART", "372": "RPL_MOTD",
    "376": "RPL_ENDOFMOTD", "381": "RPL_YOUREOPER", "382": "RPL_REHASHING",
    "383": "RPL_YOURESERVICE", "391": "RPL_TIM", "392": "RPL_",
    "393": "RPL_USERS", "394": "RPL_ENDOFUSERS", "395": "RPL_NOUSERS",
    "903": "RPL_SASLMECHS", "904": "RPL_SASLSUCCESS", "905": "RPL_SASLFACTOR",
    "906": "ERR_SASLTOOLONG", "907": "ERR_SASLFAIL",
    "908": "ERR_SASLWISSAUTHMECH", "909": "ERR_SASLALREADYAUTH",
    "200": "RPL_TRACELINK", "201": "RPL_TRACECONNECTING",
    "202": "RPL_TRACEHANDSHAKE", "203": "RPL_TRACEUNKNOWN",
    "204": "RPL_TRACEOPERATOR", "205": "RPL_TRACEUSER",
    "206": "RPL_TRACESERVER", "207": "RPL_TRACESERVICE",
    "208": "RPL_TRACENEWTYPE", "209": "RPL_TRACECLASS",
    "210": "RPL_TRACERECONNECT", "261": "RPL_TRACELOG",
    "262": "RPL_TRACEEND", "211": "RPL_STATSLINKINFO",
    "212": "RPL_STATSCOMMANDS", "219": "RPL_ENDOFSTATS",
    "242": "RPL_STATSUPTIME", "243": "RPL_STATSOLINE",
    "221": "RPL_UMODEIS", "234": "RPL_SERVLIST",
    "235": "RPL_SERVLISTEND", "251": "RPL_LUSERCLIENT",
    "252": "RPL_LUSEROP", "253": "RPL_LUSERUNKNOWN",
    "254": "RPL_LUSERCHANNELS", "255": "RPL_LUSERME",
    "256": "RPL_ADMINME", "257": "RPL_ADMINLOC1", "258": "RPL_ADMINLOC2",
    "259": "RPL_ADMINEMAIL", "263": "RPL_TRYAGAIN",
    "265": "RPL_LOCALUSERS", "266": "RPL_GLOBALUSERS",
    "401": "ERR_NOSUCHNICK", "402": "ERR_NOSUCHSERVER",
    "403": "ERR_NOSUCHCHANNEL", "404": "ERR_CANNOTSENDTOCHAN",
    "405": "ERR_TOOMANYCHANNELS", "406": "ERR_WASNOSUCHNICK",
    "407": "ERR_TOOMANYTARGETS", "408": "ERR_NOSUCHSERVICE",
    "409": "ERR_NOORIGIN", "411": "ERR_NORECIPIENT",
    "412": "ERR_NOTEXTTOSEND", "413": "ERR_NOTOPLEVEL",
    "414": "ERR_WILDTOPLEVEL", "415": "ERR_BADMASK",
    "421": "ERR_UNKNOWNCOMMAND", "422": "ERR_NOMOTD",
    "423": "ERR_NOADMININFO", "424": "ERR_FILEERROR",
    "431": "ERR_NONICKNAMEGIVEN", "432": "ERR_ERRONEUSNICKNAME",
    "433": "ERR_NICKNAMEINUSE", "436": "ERR_NICKCOLLISION",
    "437": "ERR_UNAVAILRESOURCE", "441": "ERR_USERNOTINCHANNEL",
    "442": "ERR_NOTONCHANNEL", "443": "ERR_USERONCHANNEL",
    "444": "ERR_NOLOGIN", "445": "ERR_SUMMONDISABLED",
    "446": "ERR_USERSDISABLED", "451": "ERR_NOTREGISTERED",
    "461": "ERR_NEEDMOREPARAMS", "462": "ERR_ALREADYREGISTRED",
    "463": "ERR_NOPERMFORHOST", "464": "ERR_PASSWDMISMATCH",
    "465": "ERR_YOUREBANNEDCREEP", "466": "ERR_YOUWILLBEBANNED",
    "467": "ERR_KEYSE", "471": "ERR_CHANNELISFULL",
    "472": "ERR_UNKNOWNMODE", "473": "ERR_INVITEONLYCHAN",
    "474": "ERR_BANNEDFROMCHAN", "475": "ERR_BADCHANNELKEY",
    "476": "ERR_BADCHANMASK", "477": "ERR_NOCHANMODES",
    "478": "ERR_BANLISTFULL", "481": "ERR_NOPRIVILEGES",
    "482": "ERR_CHANOPRIVSNEEDED", "483": "ERR_CANTKILLSERVER",
    "484": "ERR_RESTRICTED", "485": "ERR_UNIQOPPRIVSNEEDED",
    "491": "ERR_NOOPERHOST", "501": "ERR_UMODEUNKNOWNFLAG",
    "502": "ERR_USERSDONTMATCH", "231": "RPL_SERVICEINFO",
    "232": "RPL_ENDOFSERVICES", "233": "RPL_SERVICE", "300": "RPL_NONE",
    "316": "RPL_WHOISCHANOP", "361": "RPL_KILLDONE", "362": "RPL_CLOSING",
    "363": "RPL_CLOSEEND", "373": "RPL_INFOSTART", "384": "RPL_MYPORTIS",
    "213": "RPL_STATSCLINE", "214": "RPL_STATSNLINE", "215": "RPL_STATSILINE",
    "216": "RPL_STATSKLINE", "217": "RPL_STATSQLINE",
    "218": "RPL_STATSYLINE", "240": "RPL_STATSVLINE",
    "241": "RPL_STATSLLINE", "244": "RPL_STATSHLINE",
    "246": "RPL_STATSPING", "247": "RPL_STATSBLINE",
    "250": "RPL_STATSDLINE", "492": "ERR_NOSERVICEHOST",
}


class ReplyName:
    """Lookup helper for numeric reply -> canonical name."""

    @staticmethod
    def name(num: str) -> Optional[str]:
        return _REPLY_NAMES.get(num)

    @staticmethod
    def lower_name(num: str) -> Optional[str]:
        n = _REPLY_NAMES.get(num)
        return n.lower() if n else None

    @staticmethod
    def is_reply(command: str) -> bool:
        return command.isdigit() and command in _REPLY_NAMES


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------
class IRCMessage:
    """A single IRC protocol message: ``:prefix COMMAND param ... :trailing``."""

    __slots__ = ("prefix", "command", "params")

    def __init__(self, prefix: Optional[str], command: str,
                 params: Optional[List[str]] = None):
        self.prefix = prefix
        self.command = (command or "").upper()
        self.params = list(params or [])

    # -- parsing -----------------------------------------------------------
    @staticmethod
    def parse(line: str) -> "IRCMessage":
        """Parse one IRC line (with or without trailing CRLF)."""
        line = line.rstrip("\r\n")
        if not line:
            raise ValueError("empty IRC message")

        prefix = None
        if line.startswith(":"):
            prefix, _, line = line[1:].partition(" ")
            if not line:
                raise ValueError("missing command in: %r" % line)

        command, _, rest = line.partition(" ")
        params = _split_params(rest)
        return IRCMessage(prefix, command, params)

    # -- serialization -----------------------------------------------------
    def to_s(self) -> str:
        parts = []
        if self.prefix:
            parts.append(":" + self.prefix)
        parts.append(self.command)
        for i, p in enumerate(self.params):
            last = i == len(self.params) - 1
            if last and (p == "" or p.startswith(":") or " " in p):
                parts.append(":" + p)
            else:
                parts.append(p)
        return " ".join(parts) + "\r\n"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return ("<IRCMessage prefix=%r command=%r params=%r>"
                % (self.prefix, self.command, self.params))


def _split_params(rest: str) -> List[str]:
    """Split the params section, honouring a single trailing parameter."""
    rest = rest.strip()
    if not rest:
        return []
    if rest.startswith(":"):
        return [rest[1:]]
    idx = rest.find(" :")
    if idx != -1:
        head = rest[:idx]
        tail = rest[idx + 2:]
        return head.split(" ") + [tail]
    return rest.split(" ")


# ---------------------------------------------------------------------------
# Convenience command builders
# ---------------------------------------------------------------------------
class Command:
    """Factory of commonly-sent IRC commands."""

    @staticmethod
    def _build(cmd: str, params: List[str], prefix=None) -> IRCMessage:
        return IRCMessage(prefix, cmd, params)

    @classmethod
    def pass_(cls, password): return cls._build("PASS", [password])
    @classmethod
    def authenticate(cls, *params): return cls._build("AUTHENTICATE", list(params))
    @classmethod
    def nick(cls, nick): return cls._build("NICK", [nick])
    @classmethod
    def user(cls, user, hostname, servername, realname):
        return cls._build("USER", [user, hostname, servername, realname])
    @classmethod
    def quit(cls, message=""): return cls._build("QUIT", [message])
    @classmethod
    def mode(cls, *params): return cls._build("MODE", list(params))
    @classmethod
    def join(cls, *params): return cls._build("JOIN", list(params))
    @classmethod
    def part(cls, *params): return cls._build("PART", list(params))
    @classmethod
    def topic(cls, *params): return cls._build("TOPIC", list(params))
    @classmethod
    def invite(cls, *params): return cls._build("INVITE", list(params))
    @classmethod
    def kick(cls, *params): return cls._build("KICK", list(params))
    @classmethod
    def privmsg(cls, target, msg): return cls._build("PRIVMSG", [target, msg])
    @classmethod
    def notice(cls, target, msg): return cls._build("NOTICE", [target, msg])
    @classmethod
    def ping(cls, *params): return cls._build("PING", list(params))
    @classmethod
    def pong(cls, *params): return cls._build("PONG", list(params))
    @classmethod
    def away(cls, message=""): return cls._build("AWAY", [message])
    @classmethod
    def whois(cls, *params): return cls._build("WHOIS", list(params))
    @classmethod
    def who(cls, *params): return cls._build("WHO", list(params))
    @classmethod
    def names(cls, *params): return cls._build("NAMES", list(params))
    @classmethod
    def version(cls, *params): return cls._build("VERSION", list(params))
    @classmethod
    def time(cls, *params): return cls._build("TIME", list(params))
    @classmethod
    def motd(cls, *params): return cls._build("MOTD", list(params))

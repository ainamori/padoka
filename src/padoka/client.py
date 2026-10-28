"""
Client-facing connection handler.

Each IRC client that attaches to padoka's listening port gets one of these.
It authenticates the client (PASS/NICK/USER), hands it the current nick,
channel list (353/332/366) and MOTD, then proxies traffic both ways and
handles the ``/PADOKA <cmd>`` control command.
"""

from __future__ import annotations

import base64
import binascii
import re
import socket
import queue
import time
from datetime import datetime
from typing import Optional

from . import errors as E
from .irc.message import IRCMessage, Command
from . import VERSION

__all__ = ["PDKClient"]


class PDKClient:
    # All socket I/O (read and write) happens in the single handler thread:
    # an SSLSocket must not be read and written from different threads
    # concurrently (records get lost / the peer sees a spurious EOF).
    # The handler thread therefore polls the socket with a short timeout and
    # flushes the send queue before every read.
    IO_POLL_TIMEOUT = 0.25

    def __init__(self, config, sock: socket.socket, manager):
        self.config = config
        self.logger = config.logger
        self.manager = manager
        self._state = manager.state
        self.sock = sock
        self.sock.settimeout(self.IO_POLL_TIMEOUT)
        self.remote_host: str = sock.getpeername()[0]
        self.queue: "queue.Queue" = queue.Queue()
        self._buf = b""
        self.connected = False
        self._realname: Optional[str] = None
        self._username: Optional[str] = None
        self._hostname: Optional[str] = None
        self._servername: Optional[str] = None
        self._stop = False
        self._nick: Optional[str] = None
        # SASL state: idle -> in_progress -> done | failed
        self._sasl_state = "idle"
        self._sasl_authed = False

    def _rpl(self, num: str, nick: str, *params) -> IRCMessage:
        """Build a numeric reply prefixed by the configured server name."""
        return IRCMessage(self.config.padoka_server_name, num,
                          [nick] + list(params))

    # ---------------------------------------------------------------- start
    def start(self) -> None:
        try:
            if self._login():
                self.connected = True
                self.manager.invoke_event("leave_away", self.manager.client_count())
                self.manager.invoke_event("invoke_bot", "client_login",
                                          self.manager.client_count(), self)
                while not self._stop:
                    msg = self._recv_from_client()
                    if msg is None:
                        break
                    self._send_from_client(msg, self)
        except E.PDKQuitClient:
            pass
        except Exception as ex:
            self.manager.ndk_error(ex)
        finally:
            self.manager.invoke_event("invoke_bot", "client_logout",
                                      self.manager.client_count(), self)
            if self.connected:
                # successful logins are announced; failed/probe connections
                # already logged their specific reason (464 / 907 / ...)
                self.logger.slog("Client %s@%s disconnected."
                                 % (self._realname, self.remote_host))
            # flush any pending final replies (e.g. 907 / 464) before close
            self._pump_sends()
            try:
                self.sock.close()
            except OSError:
                pass

    def kill(self) -> None:
        self._stop = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    # ---------------------------------------------------------------- login
    def _login(self) -> bool:
        pass_: Optional[str] = None
        nick: Optional[str] = None
        self._username = None

        while nick is None or self._username is None:
            msg = self._recv_from_client()
            if msg is None:
                return False
            if msg.command == "USER":
                self._username = msg.params[0] if len(msg.params) > 0 else ""
                self._hostname = msg.params[1] if len(msg.params) > 1 else ""
                self._servername = msg.params[2] if len(msg.params) > 2 else ""
                self._realname = msg.params[3] if len(msg.params) > 3 else ""
            elif msg.command == "NICK":
                nick = msg.params[0] if msg.params else ""
                self._nick = nick
            elif msg.command == "PASS":
                pass_ = msg.params[0] if msg.params else ""
            elif msg.command == "AUTHENTICATE":
                if not self._sasl_handle(msg):
                    return False
            else:
                raise E.PDKError("Illegal login sequence: %s" % msg)

        if not self._sasl_authed and self.config.client_server_pass and \
                self.config.client_server_pass != pass_:
            self._send(self._rpl("464", nick, "Password Incorrect."))
            return False

        self._send(self._rpl("001", nick,
                        "Welcome to the Internet Relay Network "
                        "%s!%s@%s" % (nick, self._username, self.remote_host)))
        self._send(self._rpl("002", nick,
                        "Your host is padoka, running version %s" % VERSION))
        self._send(self._rpl("003", nick,
                        "This server was created "
                        + datetime.now().strftime("%a %b %d %H:%M:%S %Y")))
        self._send(self._rpl("004", nick,
                        "padoka %s" % VERSION, "unknown", "abefghijklmnopqrstv"))

        self._send_motd(nick)

        # The state dump below needs the proxy's upstream nick.  If a client
        # connects before the proxy finishes registering upstream (startup or
        # mid-reconnect), the nick is None and sending NICK/331/353 with it is
        # a protocol error that crashes serialization.  Wait (keeping the
        # connection healthy) until the nick is available; reject on timeout.
        if not self._wait_for_ready(30.0):
            self._send(self._rpl("401", nick,
                                  "Padoka not registered upstream yet; please retry."))
            return False

        self._send_command(Command.nick(self._state.nick()))
        cur_nick = self.manager.state.nick()

        for ch, chs in list(self.manager.state.current_channels.items()):
            self._send_command(Command.join(chs.name))
            if chs.topic:
                self._send(self._rpl("332", cur_nick, chs.name, chs.topic))
            else:
                self._send(self._rpl("331", cur_nick, chs.name, "No topic is set."))
            self._send(self._rpl("353", cur_nick, chs.state, chs.name,
                            " ".join(chs.names())))
            self._send(self._rpl("366", cur_nick, chs.name, "End of NAMES list."))

        self.logger.slog("Client %s@%s connected."
                         % (self._realname, self.remote_host))
        return True

    def _send_motd(self, nick) -> None:
        self._send(self._rpl("375", nick, "- Padoka Message of the Day -"))
        self._send(self._rpl("372", nick, "- Enjoy IRC chat with Padoka chan!"))
        self._send(self._rpl("376", nick, "End of MOTD command."))

    def _wait_for_ready(self, timeout: float) -> bool:
        """Wait until the proxy has registered its nick upstream.

        Keeps the connection healthy while waiting (pumps the send queue,
        answers PING, honours QUIT).  Returns True once a nick is available,
        False on timeout or a closed/dead connection.
        """
        deadline = time.time() + timeout
        while not self._stop:
            if self.manager.state.nick():
                return True
            if time.time() >= deadline:
                return False
            if not self._pump_sends():
                return False
            # drain any complete buffered lines first (answer PING, honour
            # QUIT) so a multi-line recv is not stranded behind a timeout
            drained = False
            while b"\n" in self._buf:
                drained = True
                line, self._buf = self._buf.split(b"\n", 1)
                if not line.strip():
                    continue
                msg = self._parse_line(line)
                if msg is None:
                    continue
                if msg.command == "PING":
                    self._send(Command.pong(*msg.params), logging=False)
                elif msg.command == "QUIT":
                    raise E.PDKQuitClient
            if drained:
                continue
            # nothing buffered: bounded recv
            try:
                data = self.sock.recv(65535)  # governed by IO_POLL_TIMEOUT
            except socket.timeout:
                continue
            except OSError:
                return False
            if not data:
                return False
            self._buf += data
        return False

    # ------------------------------------------------------------ SASL (ircv3)
    # Supported mechanism: PLAIN, whose payload is
    #   base64( [authzid NUL] authcid NUL passwd )
    # ``passwd`` is checked against ``client_server.pass`` (the same shared
    # secret as /PASS), so a SASL-authenticated client needs no /PASS.
    # NOTE: PLAIN sends the password in clear text, pair it with TLS.
    SASL_MECHS = ("PLAIN",)

    def _sasl_handle(self, msg: IRCMessage) -> bool:
        """Process an ``AUTHENTICATE`` message during login.

        Returns ``False`` when the connection must be dropped.
        Replies follow the ircv3.sasl spec (903..909).
        """
        arg = (msg.params[0] if msg.params else "").strip()
        if not self.config.client_server_sasl:
            self._sasl_reply("908", "SASL: disabled")
            return False
        if self._sasl_state == "in_progress":
            if arg == "-":
                return self._sasl_fail("aborted by client")
            return self._sasl_finish(arg)
        if self._sasl_state == "done":
            self._sasl_reply("909", "SASL: already authenticated")
            return True
        if self._sasl_state == "failed":
            self._sasl_reply("909", "SASL: already failed")
            return False
        # idle
        if arg == "":
            self._sasl_reply("903", "SASL: " + " ".join(self.SASL_MECHS))
            return True
        if arg == "-":
            return True  # nothing in progress; ignore
        if arg.upper() in self.SASL_MECHS:
            self._sasl_state = "in_progress"
            self._send(Command.authenticate("+"))
            return True
        self._sasl_state = "failed"
        self.logger.slog("SASL failed from %s: unknown mechanism %r"
                         % (self.remote_host, arg))
        self._sasl_reply("908", "SASL: unknown mechanism")
        return False

    def _sasl_finish(self, data: str) -> bool:
        """Verify the base64 PLAIN credential data; disconnect on failure."""
        if len(data) > 400:
            self._sasl_state = "failed"
            self._sasl_reply("906", "SASL: data too long")
            self.logger.slog("SASL failed: data too long from %s" % self.remote_host)
            return False
        try:
            raw = base64.b64decode(data.encode("ascii"), validate=True)
        except (binascii.Error, ValueError, UnicodeEncodeError):
            return self._sasl_fail("invalid base64 data")
        parts = raw.split(b"\x00")
        # 3 fields: authzid NUL authcid NUL passwd (authzid usually empty)
        # 2 fields: authcid NUL passwd (also accepted for leniency)
        if len(parts) not in (2, 3):
            return self._sasl_fail("malformed PLAIN payload")
        _authcid, passwd = parts[-2:]
        expected = self.config.client_server_pass
        if not expected or passwd.decode("utf-8", "replace") != expected:
            return self._sasl_fail("invalid credentials")
        self._sasl_state = "done"
        self._sasl_authed = True
        self._sasl_reply("904", "SASL: success")
        return True

    def _sasl_fail(self, reason: str) -> bool:
        self._sasl_state = "failed"
        self.logger.slog("SASL failed from %s: %s" % (self.remote_host, reason))
        self._sasl_reply("907", "SASL: " + reason)
        return False

    def _sasl_reply(self, num: str, text: str) -> None:
        self._send(self._rpl(num, self._nick or "*", text))

    # ------------------------------------------------------------------ recv
    def _pump_sends(self) -> bool:
        """Flush queued messages to the socket (handler thread only).

        Returns False when the write failed; the caller should treat the
        connection as dead.
        """
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                return True
            try:
                self.sock.sendall(item.to_s().encode("utf-8", errors="replace"))
            except OSError:
                self._stop = True
                return False

    def _recv_from_client(self) -> Optional[IRCMessage]:
        """Return the next IRC message, or None on EOF / write error.

        All socket I/O stays on this single thread: the send queue is pumped
        before every step, and buffered lines are drained BEFORE a blocking
        (short-timeout) recv.  A single recv() can deliver several lines at
        once (clients commonly send NICK and USER together), so we must not
        wait for a fresh recv before returning the lines already in _buf --
        doing so strands them and hangs the login.
        """
        while not self._stop:
            if not self._pump_sends():
                return None
            # 1) drain any complete lines already buffered
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                if not line.strip():
                    continue
                msg = self._parse_line(line)
                if msg is None:
                    continue
                if msg.command == "PING":
                    self._send(Command.pong(*msg.params), logging=False)
                    continue
                if msg.command == "PONG":
                    continue
                self.logger.dlog("[C>] %s" % msg.to_s())
                return msg
            # 2) no complete line buffered: wait (bounded) for more data
            try:
                data = self.sock.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                return None
            if not data:
                # EOF: return any trailing partial line, else None
                if self._buf:
                    line, self._buf = self._buf.split(b"\n", 1)
                    return self._parse_line(line)
                return None
            self._buf += data
        return None

    def _parse_line(self, line: bytes) -> Optional[IRCMessage]:
        try:
            return IRCMessage.parse(line.decode("utf-8", errors="replace"))
        except ValueError as ex:
            self.logger.slog("Invalid Message: %s" % ex)
            return None

    # ---------------------------------------------------------- client->server
    def _send_from_client(self, msg: IRCMessage, from_) -> None:
        if not self.manager.connected:
            return
        cmd = msg.command
        if cmd == "QUIT":
            raise E.PDKQuitClient
        if cmd in ("PRIVMSG", "NOTICE"):
            text = msg.params[1] if len(msg.params) > 1 else ""
            if text.startswith("/padoka"):
                _, *rest = text.split()
                c = rest[0] if rest else ""
                a = rest[1:] if len(rest) > 1 else []
                self._padoka_client_command(c, a)
                return
            if self.manager.send_to_bot("client_privmsg", self,
                                        msg.params[0], text):
                self.manager.send_to_server(msg)
                self.manager.send_to_clients_otherwise(msg, from_)
        else:
            self.manager.send_to_server(msg)

    # -------------------------------------------------------- PADOKA commands
    PDK_COMMANDS = {
        "QUIT": "quit padoka program",
        "RESTART": "restart padoka program (not reload *.py programs)",
        "RELOAD": "reload configurations and bot programs",
        "RECONNECT": "reconnect to next server",
        "STATUS": "show padoka running status",
        "HELP": "show help",
    }

    def _padoka_client_command(self, cmd, args) -> None:
        cmd = (cmd or "").upper()
        if cmd == "QUIT":
            self.manager.invoke_event("quit_program")
            self.client_notice("padoka will be quit. bye!")
        elif cmd == "RESTART":
            self.manager.invoke_event("restart_program", self)
            self.client_notice("padoka will be restart. see you later.")
        elif cmd == "RELOAD":
            self.manager.invoke_event("reload_config", self)
        elif cmd == "RECONNECT":
            self.manager.invoke_event("reconnect_to_server", self)
        elif cmd == "STATUS":
            for line in self.manager.ndk_status():
                self.client_notice(line)
        elif cmd == "HELP":
            self.client_notice("available: " + ", ".join(self.PDK_COMMANDS))
            if len(args) > 1:
                self.client_notice("%s: %s" % (args[1],
                                               self.PDK_COMMANDS.get(args[1].upper(), "")))
        else:
            if not self.manager.send_to_bot("padoka_command", self, cmd, *args):
                self.client_notice("No such command. Use /PADOKA HELP.")

    def client_notice(self, msg: str) -> None:
        self.push(Command.notice(self._state.nick(), msg))

    # ---------------------------------------------------------------- sending
    def push(self, msg: IRCMessage) -> None:
        if self.connected:
            self.queue.put(msg)

    def add_prefix(self, cmd: IRCMessage) -> None:
        cmd.prefix = "%s!%s@%s" % (self._state.nick(), self._username,
                                   self.remote_host)

    def add_prefix2(self, cmd: IRCMessage, nick) -> None:
        cmd.prefix = "%s!%s@%s" % (nick, self._username, self.remote_host)

    def _send(self, msg: IRCMessage, logging=True) -> None:
        if logging:
            self.logger.dlog("[C<] %s" % msg)
        self.queue.put(msg)

    def _send_command(self, cmd: IRCMessage, nick=None) -> None:
        prefix = "%s!%s@%s" % (nick or self.manager.state.nick(),
                               self._username, self.remote_host)
        self.queue.put(self.add_prefix_to(cmd, prefix))

    def add_prefix_to(self, cmd: IRCMessage, prefix: str) -> IRCMessage:
        cmd.prefix = prefix
        return cmd

    def send_to_client(self, msg: IRCMessage) -> None:
        """Send a message to this client.

        Mirrors the Ruby ``send_to_client``: numeric replies are treated as
        server replies (prefix = padoka_server_name) when no prefix is set.
        """
        if re.match(r"^\d+", msg.command or ""):
            if not msg.prefix:
                msg.prefix = self.config.padoka_server_name
            self._send(msg)
        else:
            self._send(msg)

    # ---------------------------------------------------------------- state
    @property
    def state_str(self) -> str:
        return ("client from %s(%s, %s, %s, %s)"
                % (self.remote_host, self._username, self._hostname,
                   self._servername, self._realname))

    @property
    def state(self) -> str:
        return self.state_str

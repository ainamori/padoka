"""
Main server.

The server:
* connects to a real IRC server and registers (nick/user/realname),
* keeps a client-listener socket (default 6667) that IRC clients attach to,
* proxies traffic both directions,
* tracks channel membership/modes,
* runs an away-mode + reconnect + PING watchdog,
* dispatches events to clients and to the plugin bots.

All upstream IRC protocol handling runs in a single "server thread"
that consumes one shared input queue (both IRC messages and internal
events), ensuring serialized processing.
"""

from __future__ import annotations

import base64
import random
import re
import signal
import socket
import ssl
import threading
import queue
import time
from datetime import datetime
from typing import Dict, List, Optional

from . import errors as E
from .client import PDKClient
from .config import Config
from .irc.message import IRCMessage, Command, ReplyName
from .irc.connection import Connection, ConnectionClosed
from .logger import PDKLogger
from .state import PDKState
from .webui import WebUI

__all__ = ["PDKServer", "PrefixObject", "sasl_plain_payload"]

TIMER_INTERVAL_SEC = 60
MAX_PONG_FAIL = 5
# Max time to wait for the upstream SASL exchange (905 prompt / 904 success).
# Some servers (e.g. ircnet dev) hold the connection in "processing" (020
# "Please wait...") for a while; a fully silent server must not hang us
# forever.
SASL_TIMEOUT = 60


def sasl_plain_payload(username: str, password: str) -> str:
    """ircv3 SASL PLAIN credential data: base64(authzid NUL authcid NUL
    passwd) with an empty authzid."""
    return base64.b64encode(
        b"\x00" + str(username).encode("utf-8")
        + b"\x00" + str(password).encode("utf-8")).decode("ascii")


# sentinel tag used to mark internal events in the shared input queue
EVENT = object()


class PrefixObject:
    """Parsed ``nick!user@host`` (or a bare server name)."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.nick = None
        self.user = None
        self.host = None
        m = re.match(r"^(.+?)\!(.+?)@(.+)$", prefix or "")
        if m:
            self.nick, self.user, self.host = m.group(1), m.group(2), m.group(3)
        else:
            self.host = prefix

    def __str__(self):
        return self.prefix


class PDKServer:
    def __init__(self, rcfile: str):
        self._rc = rcfile
        self._clients: List[PDKClient] = []
        self.state = PDKState(self)
        self._in_q: "queue.Queue" = queue.Queue()
        self._ctrl_q: "queue.Queue" = queue.Queue()
        self._connected = False
        self._exiting = False
        self._pong_received = True
        self._isupport: Dict[str, object] = {}
        self._conn: Optional[Connection] = None
        self._server_pass: Optional[str] = None
        self._server_sasl: Optional[dict] = None
        self._wait_deadline: Optional[float] = None
        self._client_srv: Optional[socket.socket] = None
        self._server_thread: Optional[threading.Thread] = None
        self._clients_thread: Optional[threading.Thread] = None
        self._timer_thread: Optional[threading.Thread] = None
        self._webui = None  # type: Optional["WebUI"]
        # Assigned by reload_config() during __init__.
        self._config: Config
        self._logger: PDKLogger

        self.reload_config()
        self._set_signal_traps()

    # -- accessors ---------------------------------------------------------
    @property
    def config(self) -> Config:
        return self._config

    @property
    def logger(self) -> PDKLogger:
        return self._logger

    @property
    def connected(self) -> bool:
        return self._connected

    def client_count(self) -> int:
        return len(self._clients)

    # -- config -----------------------------------------------------------
    def reload_config(self) -> None:
        old = getattr(self, "_config", None)
        if old is not None:
            for b in old.bots:
                try:
                    b.bot_destruct()
                except Exception:
                    pass
        self._config = Config(self, self._rc)
        assert self._config.logger is not None
        self._logger = self._config.logger
        self.state.logger = self._config.logger
        self.state.config = self._config
        for c in self._clients:
            c.logger = self._config.logger

    # ---------------------------------------------------------------- start
    def start(self) -> None:
        self._server_thread = threading.Thread(
            target=self._server_thread_proc, daemon=True, name="ndk-server")
        self._server_thread.start()
        self._clients_thread = threading.Thread(
            target=self._clients_thread_proc, daemon=True, name="ndk-clients")
        self._clients_thread.start()
        self._timer_thread = threading.Thread(
            target=self._timer_loop, daemon=True, name="ndk-timer")
        self._timer_thread.start()
        self._init_webui()

        try:
            while not self._exiting:
                item = self._ctrl_q.get()
                if item == "quit":
                    return
                elif item == "restart":
                    raise E.PDKRestartProgram
                elif item == "stop":
                    return
        finally:
            self._exiting = True
            self._stop_webui()
            # wake a clients thread blocked in accept(): closing the
            # listener from another thread does NOT interrupt accept() on
            # Linux, so self-connect (the pending conn is discarded in the
            # accept loop once _exiting is set) and then close
            srv = self._client_srv
            if srv is not None:
                try:
                    wake = socket.create_connection(
                        srv.getsockname()[:2], timeout=1)
                    wake.close()
                except OSError:
                    pass
                try:
                    srv.close()
                except OSError:
                    pass
            for t in (self._server_thread, self._clients_thread, self._timer_thread):
                if t and t.is_alive():
                    t.join(timeout=3)
            if self._conn:
                self._conn.close()

    # ------------------------------------------------------------- webui
    def _init_webui(self) -> None:
        if not self._config.webui_enabled:
            self._webui = None
            return
        if self._webui is None:
            self._webui = WebUI(self._config, self)
            self._webui.start()

    def _stop_webui(self) -> None:
        if self._webui is not None:
            self._webui.stop()
            self._webui = None

    def _webui_push(self, msg: IRCMessage) -> None:
        w = self._webui
        if w is not None:
            try:
                w.on_message(msg)
            except Exception:
                pass

    # --------------------------------------------------------- server thread
    def next_server_info(self):
        """(host, port, pass, ssl_params, sasl) of the next server in rotation."""
        lst = self._config.server_list
        sv = lst.pop(random.randrange(len(lst)))
        lst.append(sv)
        return (sv["host"], sv["port"], sv["pass"], sv["ssl_params"],
                sv.get("sasl"))

    def _make_server_conn(self) -> Connection:
        host, port, self._server_pass, sslp, self._server_sasl = \
            self.next_server_info()
        conn = Connection(host, port, sslp)
        conn.queue = self._in_q  # feed parsed messages to our dispatcher
        return conn

    def _server_thread_proc(self) -> None:
        while not self._exiting:
            conn = None
            try:
                conn = self._make_server_conn()
                self._conn = conn
                self._logger.slog(
                    "Server connection to %s:%s." % (conn.host, conn.port))
                self._logger.dlog("  ssl_params=%r" % (conn.ssl_params,))
                self._pong_received = True
                try:
                    conn.open()
                    info = conn.tls_info()
                    if info:
                        self._logger.slog("TLS handshake ok (%s)." % info)
                    else:
                        self._logger.dlog("plaintext upstream connection.")
                    self._server_main_proc(conn)
                finally:
                    if self._config.quit_message:
                        try:
                            conn.send(Command.quit(self._config.quit_message))
                        except Exception:
                            pass
                    conn.close()
            except E.PDKReconnectToServer:
                self._connected = False
                self._part_from_all_channels()
                self._logger.slog("Reconnect request (no server response, or client request).")
                self._sleep_reconnect()
            except E.PDKWaitTimeout:
                self._connected = False
                self._part_from_all_channels()
                self._wait_deadline = None
                last = conn.last_line if conn is not None else None
                self._logger.slog(
                    "SASL: no server reply (905/904) within %d seconds "
                    "(last line: %s). Trying to reconnect."
                    % (SASL_TIMEOUT, last or "<none>"))
                self._sleep_reconnect()
            except ssl.SSLError as e:
                # TLS handshake / mid-connection alert failure (SSLError is
                # an OSError, so this must come before the generic handler)
                self._connected = False
                self._part_from_all_channels()
                h = conn.host if conn is not None else "?"
                p = conn.port if conn is not None else "?"
                self._logger.slog(
                    "TLS failed for %s:%s (%s: %s). Trying to reconnect."
                    % (h, p, type(e).__name__, e))
                self._sleep_reconnect()
            except ConnectionClosed:
                self._connected = False
                self._part_from_all_channels()
                detail = conn.last_error if conn is not None else None
                last = conn.last_line if conn is not None else None
                if detail:
                    self._logger.slog(
                        "Connection error (%s). Last line from server: %s. "
                        "Trying to reconnect." % (detail, last or "<none>"))
                else:
                    self._logger.slog(
                        "Connection closed by server. Last line from server: "
                        "%s. Trying to reconnect." % (last or "<none>"))
                self._sleep_reconnect()
            except (OSError, IOError) as e:
                self._connected = False
                self._part_from_all_channels()
                h = conn.host if conn is not None else "?"
                p = conn.port if conn is not None else "?"
                self._logger.slog(
                    "Upstream connect to %s:%s failed (%s: %s). "
                    "Trying to reconnect." % (h, p, type(e).__name__, e))
                self._sleep_reconnect()
            except E.PDKQuitProgram:
                break
            except Exception as e:
                self.ndk_error(e)
                break

    def _sleep_reconnect(self) -> None:
        delay = self._config.reconnect_delay
        end = time.time() + delay
        while not self._exiting and time.time() < end:
            time.sleep(1)

    def _register(self, conn: Connection) -> None:
        """Send NICK + USER (away_nick handling included)."""
        if self._config.away_nick and self.client_count() == 0:
            self.state.original_nick = self._config.nick
            self.state.set_nick(self._config.away_nick)
        else:
            self.state.set_nick(self._config.nick)
        conn.send(Command.nick(self.state.nick()))
        conn.send(Command.user(self._config.user, self._config.hostname,
                               self._config.servername, self._config.realname))

    def _server_main_proc(self, conn: Connection) -> None:
        # -- login ----------------------------------------------------------
        if self._server_pass:
            conn.send(Command.pass_(self._server_pass))

        # -- upstream SASL (ircv3 PLAIN), must finish before NICK/USER ------
        # servers:
        #   - host: dev.ircnet.ne.jp
        #     sasl:
        #       username: mynick
        #       password: nickserv_password
        sasl = self._server_sasl
        sasl_done = sasl is None
        sasl_sent = False
        sasl_user = None
        if sasl and not sasl.get("password"):
            # configured but unusable: say so instead of failing with 907
            self._logger.slog(
                "WARNING: SASL configured for %s but no password set; "
                "skipping SASL (set servers[].sasl.password)." % conn.host)
            sasl = None
            sasl_done = True
        if sasl:
            sasl_user = str(sasl.get("username") or self._config.user)
            password = str(sasl.get("password") or "")
            self._sasl_payload = sasl_plain_payload(sasl_user, password)
            conn.send(Command.authenticate("PLAIN"))
            self._logger.slog("SASL PLAIN started (username=%s)." % sasl_user)
            self._wait_deadline = time.time() + SASL_TIMEOUT

        if not sasl:
            self._register(conn)

        # wait for welcome
        while True:
            q = self._recv_from_server(conn)
            cmd = q.command
            self._logger.dlog("[login] %s" % q)
            if not sasl_done:
                p0 = q.params[0] if q.params else ""
                if cmd == "AUTHENTICATE" and p0 == "+":
                    # some servers use `AUTHENTICATE +` for continuation
                    conn.send(Command.authenticate(self._sasl_payload))
                    sasl_sent = True
                    continue
                if cmd == "905" and not sasl_sent:
                    # 905: `SASL: +` continuation
                    conn.send(Command.authenticate(self._sasl_payload))
                    sasl_sent = True
                    continue
                if cmd == "904":
                    sasl_done = True
                    self._wait_deadline = None
                    self._logger.slog("SASL success (username=%s)." % sasl_user)
                    self._register(conn)
                    continue
                if cmd in ("906", "907", "908"):
                    self._logger.slog(
                        "SASL failed (username=%s): %s" % (sasl_user, q))
                    raise E.PDKReconnectToServer
                if cmd == "ERROR":
                    self._logger.slog("Server sent ERROR during login: %s" % q)
                    raise E.PDKReconnectToServer
                # 903 (mechanism list), 909, notices, ... -> keep waiting
                continue
            if cmd == "001":
                break
            if cmd in ("433", "437"):
                # 433/437: params[0] is the target nick ("433 nick :in use")
                nick = self.state.nick_succ(q.params[0] if q.params else "")
                self.state.set_nick(nick)
                conn.send(Command.nick(nick))
            elif cmd == "ERROR":
                # log the server's reason -- a bare reconnect hides it
                self._logger.slog("Server sent ERROR during login: %s" % q)
                raise E.PDKReconnectToServer
            elif cmd == "002":
                # RPL_YOURHOST — capture the server-assigned host for bans
                self._capture_self_host(q)
            elif cmd in ("NOTICE", "020", "PRIVMSG", "004", "003"):
                continue
            else:
                self._logger.slog("Server login fail!(%s)" % q)
                raise E.PDKReconnectToServer

        if self._config.mode:
            conn.send(Command.mode(self.state.nick(), self._config.mode))

        # -- join channels --------------------------------------------------
        if len(self.state.current_channels) > 0:
            for ch in self.state.channels():
                self._join_to_channel(ch)
        else:
            for ch in self._config.default_channels:
                self._join_to_channel(ch)

        self._connected = True
        self._isupport = {}

        if self.client_count() == 0:
            self._enter_away()

        self.invoke_event("invoke_bot", "server_connected")
        self._config.notify.ready()

        # -- main loop ------------------------------------------------------
        while True:
            q = self._recv_from_server(conn)
            cmd = q.command
            if cmd == "PRIVMSG" and len(q.params) > 1 and self._ctcp_message(q.params[1]):
                self._ctcp_message_handler(q)
            elif cmd == "JOIN":
                self.state.on_join(self.nick_of(q), q.params[0])
            elif cmd == "PART":
                self.state.on_part(self.nick_of(q), q.params[0])
            elif cmd == "NICK":
                self.state.on_nick(self.nick_of(q), q.params[0], q)
            elif cmd == "QUIT":
                self.state.on_quit(self.nick_of(q),
                                   q.params[0] if q.params else "", q)
            elif cmd == "TOPIC":
                self.state.on_topic(self.nick_of(q), q.params[0],
                                    q.params[1] if len(q.params) > 1 else "")
            elif cmd == "MODE":
                if len(q.params) > 0 and re.match(r"^[#&+!]", q.params[0]):
                    self_bans = self.state.on_mode(self.nick_of(q), q.params[0],
                                                    q.params[1:])
                    for mask in self_bans:
                        self._logger.slog(
                            "Removing self-ban %s on %s" % (mask, q.params[0]))
                        self.send_to_server(Command.mode(q.params[0], "-b", mask))
                else:
                    self.state.on_mode(self.nick_of(q), q.params[0], q.params[1:])
            elif cmd == "002":
                # RPL_YOURHOST — (re)capture our host after nick changes
                self._capture_self_host(q)
            elif cmd == "KICK":
                self.state.on_kick(self.nick_of(q), q.params[0],
                                   q.params[1] if len(q.params) > 1 else "",
                                   q.params[2] if len(q.params) > 2 else "")
            elif cmd == "353":
                self.state.on_353(q.params[2], q.params[3])
            elif cmd == "332":
                self.state.on_332(q.params[1], q.params[2] if len(q.params) > 2 else "")
            elif cmd == "403":
                self.state.on_403(q.params[1])
            elif cmd in ("433", "436", "437"):
                p = q.params[1] if len(q.params) > 1 else ""
                if re.match(r"^[#&!+]", p):
                    threading.Thread(target=self._delayed_join, args=(p,),
                                     daemon=True).start()
                else:
                    nick = self.state.nick_succ(p)
                    conn.send(Command.nick(nick))
                    self._logger.slog("Retry nick setting: %s" % nick)
            elif cmd == "005":
                self._parse_isupport(q)

            self._send_to_clients(q)
            self._logger.logging(q)
            self.send_to_bot(q)
            self._webui_push(q)

    def _recv_from_server(self, conn: Connection) -> IRCMessage:
        while True:
            # bounded get so a shutdown (_exiting) is noticed within ~1s even
            # while idle/connected upstream; raises PDKQuitProgram to unwind
            # the server thread cleanly instead of waiting for the join.
            try:
                q = self._in_q.get(timeout=1.0)
            except queue.Empty:
                if self._exiting:
                    raise E.PDKQuitProgram
                dl = self._wait_deadline
                if dl is not None and time.time() > dl:
                    raise E.PDKWaitTimeout()
                continue
            if q is None:  # EOF sentinel from reader
                raise ConnectionClosed("upstream connection closed")
            if isinstance(q, tuple) and q[0] is EVENT:
                self._exec_event(q)
                continue

            cmd = q.command
            if cmd == "PING":
                conn.send(Command.pong(q.params[0] if q.params else conn.host))
                continue
            if cmd == "PONG":
                self._pong_received = True
                continue
            if cmd == "NOTICE":
                self._logger.dlog("[<S] %s" % q)
                if self._config.notice_filter:
                    if self._filter_message(self._config.notice_filter, q):
                        return q
                    continue
                return q
            if cmd == "PRIVMSG":
                self._logger.dlog("[<S] %s" % q)
                if self._config.privmsg_filter:
                    if self._filter_message(self._config.privmsg_filter, q):
                        return q
                    continue
                return q
            self._logger.dlog("[<S] %s" % q)
            return q

    # ------------------------------------------------------------- helpers
    def _capture_self_host(self, q) -> None:
        # RPL_YOURHOST (002): params are [nick, "Your host is <host>"]
        if q.params:
            host = q.params[-1].split()[-1]
            self.state.set_self_host(host)

    def join_to_channel(self, ch) -> None:
        self._join_to_channel(ch)

    def _join_to_channel(self, ch) -> None:
        # Prefer the live-tracked key (MODE k), else the key from YAML config.
        key = None
        cs = self.state.current_channels.get(ch)
        if cs is not None and cs.key:
            key = cs.key
        if key is None:
            chinfo = self._config.channel_info.get(ch)
            if chinfo and chinfo.get("key"):
                key = chinfo["key"]
        if key:
            self.send_to_server(Command.join(ch, key))
        else:
            self.send_to_server(Command.join(ch))

    def _delayed_join(self, ch) -> None:
        time.sleep(60)
        if not self._exiting:
            self._join_to_channel(ch)

    def _enter_away(self) -> None:
        if self._exiting or not self._connected:
            return
        if self._config.away_message:
            self.send_to_server(Command.away(self._config.away_message))
        if self.state.nick() != self._config.away_nick and self._config.away_nick:
            self.state.original_nick = self.state.nick()
            self.send_to_server(Command.nick(self._config.away_nick))
        for ch in self._config.login_channels:
            chinfo = self._config.channel_info.get(ch)
            if chinfo and self.state.canonical_channel_name(ch) in self.state.current_channels:
                if chinfo.get("part_message"):
                    self.send_to_server(Command.part(ch, chinfo["part_message"]))
                else:
                    self.send_to_server(Command.part(ch))

    def _leave_away(self) -> None:
        if self._exiting or not self._connected:
            return
        self.send_to_server(Command.away())
        if self._config.away_nick and self.state.original_nick:
            time.sleep(2)
            self.send_to_server(Command.nick(self.state.original_nick))
            self.state.original_nick = None
            time.sleep(1)
        for ch in self._config.login_channels:
            self._join_to_channel(ch)

    def send_to_server(self, msg: IRCMessage) -> None:
        s = msg.to_s().rstrip("\r\n")
        if "\r" in s or "\n" in s:
            raise E.PDKInvalidMessage("message must not include CR/LF: %r" % s)
        self._logger.dlog("[>S] %s" % msg.to_s())
        if self._conn:
            self._conn.send(msg)

    # ------------------------------------------------------------ filtering
    def _filter_message(self, flt, msg: IRCMessage) -> Optional[IRCMessage]:
        if not flt:
            return msg
        try:
            if isinstance(flt, (list, tuple)):
                for f in flt:
                    f(msg)
            else:
                flt(msg)
            return msg
        except E.PDKFilterMessageSendCancel:
            self._logger.dlog("[PDK] Message Canceled")
            return None
        except E.PDKFilterMessageReplace as e:
            self._logger.dlog("[PDK] Message Replaced: %s" % e)
            return e.msg
        except E.PDKFilterMessageOnlyBot:
            self._logger.dlog("[PDK] Message only bot")
            self.send_to_bot(msg)
            return None
        except E.PDKFilterMessageOnlyLog:
            self._logger.dlog("[PDK] Message only log")
            self._logger.logging(msg)
            return None
        except E.PDKFilterMessageBotAndLog:
            self._logger.dlog("[PDK] Message log and bot")
            self.send_to_bot(msg)
            self._logger.logging(msg)
            return None

    # ------------------------------------------------------------- events
    def invoke_event(self, name: str, *args) -> None:
        self._in_q.put((EVENT, name, *args))

    def _exec_event(self, ev) -> None:
        name = ev[1]
        if name == "reload_config":
            try:
                self.reload_config()
                self._logger.slog("configuration is reloaded")
            except Exception as e:
                self._logger.slog("error occurred while reloading configuration")
                self.ndk_error(e)
        elif name == "quit_program":
            self._exiting = True
            self._ctrl_q.put("quit")
        elif name == "restart_program":
            self._exiting = True
            self._ctrl_q.put("restart")
        elif name == "reconnect_to_server":
            self._connected = False
            raise E.PDKReconnectToServer
        elif name == "invoke_bot":
            self.send_to_bot(ev[2], *ev[3:])
        elif name == "enter_away":
            if ev[2] == 0:
                self._enter_away()
        elif name == "leave_away":
            if ev[2] == 1:
                self._leave_away()

    # ------------------------------------------------------------- signals
    def _signal_quit(self) -> None:
        """SIGINT (Ctrl+C) / SIGTERM: stop the program promptly.

        The main thread (blocked in start() on _ctrl_q) must be woken
        directly.  Routing the quit through _in_q (invoke_event) would
        only be seen when the *server* thread happens to be waiting in
        _recv_from_server -- i.e. while connected.  During a failed
        connect (no upstream) or the reconnect_delay sleep the server
        thread never drains _in_q, so Ctrl+C would appear dead for up
        to reconnect_delay seconds (plus any connect hang).
        Setting _exiting also lets _sleep_reconnect / _recv_from_server
        unwind within ~1s.
        """
        self._exiting = True
        self._ctrl_q.put("quit")

    def _set_signal_traps(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        try:
            signal.signal(signal.SIGINT, lambda *a: self._signal_quit())
            signal.signal(signal.SIGTERM, lambda *a: self._signal_quit())
            if hasattr(signal, "SIGHUP"):
                signal.signal(signal.SIGHUP,
                              lambda *a: self.invoke_event("reload_config"))
            if hasattr(signal, "SIGUSR1"):
                signal.signal(signal.SIGUSR1,
                              lambda *a: self.invoke_event("invoke_bot", "sigusr1"))
            if hasattr(signal, "SIGUSR2"):
                signal.signal(signal.SIGUSR2,
                              lambda *a: self.invoke_event("invoke_bot", "sigusr2"))
        except ValueError:
            pass

    # ------------------------------------------------------------- clients
    def _clients_thread_proc(self) -> None:
        port = self._config.client_server_port
        if not port:
            self._logger.slog("No client_server.port set; not listening.")
            return
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((self._config.client_server_host or "0.0.0.0", int(port)))
        except OSError as e:
            self.ndk_error(e)
            return
        srv.listen(5)
        self._client_srv = srv
        host, p = srv.getsockname()[:2]
        self._logger.slog("Open Client Server Port: %s %s" % (host, p))

        ctx = None
        if self._config.client_server_ssl_cert_file and \
                self._config.client_server_ssl_key_file:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self._config.client_server_ssl_cert_file,
                                self._config.client_server_ssl_key_file)

        try:
            while not self._exiting:
                try:
                    csock, addr = srv.accept()
                except OSError:
                    break
                if ctx:
                    try:
                        csock = ctx.wrap_socket(csock, server_side=True)
                    except ssl.SSLError:
                        csock.close()
                        continue
                threading.Thread(target=self._handle_client,
                                 args=(csock, addr), daemon=True).start()
        finally:
            for cl in list(self._clients):
                cl.kill()
            try:
                srv.close()
            except OSError:
                pass
            self._logger.slog("Close Client Server Port")

    def _handle_client(self, sock, addr) -> None:
        client = None
        try:
            if self._config.acl_object and \
                    not self._config.acl_object.allow_socket((addr[0], addr[1])):
                self._logger.slog("ACL denied: %s" % (addr,))
                return
            client = PDKClient(self._config, sock, self)
            self._clients.append(client)
            client.start()
        except Exception as e:
            self.ndk_error(e)
        finally:
            if client in self._clients:
                self._clients.remove(client)
            self.invoke_event("enter_away", self.client_count())
            try:
                sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------- ping
    def _timer_loop(self) -> None:
        self._pong_received = True
        pong_fail = 0
        while not self._exiting:
            slp = time.time() % TIMER_INTERVAL_SEC
            slp = TIMER_INTERVAL_SEC if slp < TIMER_INTERVAL_SEC / 2 else slp
            end = time.time() + slp
            while not self._exiting and time.time() < end:
                time.sleep(1)
            if self._exiting:
                break
            self.send_to_bot("timer", datetime.now())

            if self._connected:
                if self._pong_received:
                    pong_fail = 0
                    self._config.notify.watchdog()
                else:
                    pong_fail += 1
                    self._logger.slog("PONG MISS: %d" % pong_fail)
                    if pong_fail > MAX_PONG_FAIL:
                        pong_fail = 0
                        self.invoke_event("reconnect_to_server")
                self._pong_received = False
                if self._conn:
                    self._conn.send(Command.ping(self._conn.host))
            else:
                self._pong_received = True
                pong_fail = 0

    # ------------------------------------------------------------- routing
    def _about_me(self, msg) -> bool:
        nick = self.state.nick()
        if not nick:
            return False
        return (msg.prefix or "").startswith(nick + "!")

    def _own_nick_change(self, msg):
        if msg.command == "NICK" and msg.params and msg.params[0] == self.state.nick():
            return self.nick_of(msg)
        return False

    def _part_from_all_channels(self) -> None:
        for ch, cs in list(self.state.current_channels.items()):
            cmd = Command.part(cs.name)
            cmd.prefix = self.state.nick()
            self._send_to_clients(cmd)
        self.state.clear_channels_member()

    def _send_to_clients(self, msg: IRCMessage) -> None:
        if msg.command == "PRIVMSG" and self._config.privmsg_filter_light:
            if not self._filter_message(self._config.privmsg_filter_light, msg):
                return
        old_nick = self._own_nick_change(msg)
        for cl in list(self._clients):
            if old_nick:
                cl.add_prefix2(msg, old_nick)
                cl.push(msg)
            elif self._about_me(msg):
                cl.add_prefix(msg)
                cl.push(msg)
            else:
                cl.push(msg)

    def send_to_clients_otherwise(self, msg: IRCMessage, elt) -> None:
        for cl in list(self._clients):
            if cl is not elt:
                if not msg.prefix:
                    cl.add_prefix(msg)
                cl.push(msg)
        if elt is not None:
            self.invoke_event("invoke_bot", msg)
        self._logger.logging(msg)

    def ping_to_clients(self) -> None:
        """Send PING to all attached clients (with their remote host as param)."""
        for cl in list(self._clients):
            cl.push(Command.ping(cl.remote_host))

    # ------------------------------------------------------------- ctcp
    def _ctcp_message(self, arg) -> bool:
        return arg[:1] == "\x01"

    def _ctcp_message_handler(self, msg) -> None:
        m = re.match(r"^\x01([^\x01]+)\x01", msg.params[1])
        if not m:
            return
        ctcp = m.group(1)
        nick = self.nick_of(msg)
        name, _, arg = ctcp.partition(" ")
        name = name.upper()
        if name == "VERSION":
            self.send_to_server(Command.notice(
                nick, "\x01VERSION " + self._version() + "\x01"))
        elif name == "TIME":
            self.send_to_server(Command.notice(
                nick, "\x01TIME " + str(datetime.now()) + "\x01"))
        elif name == "ECHO":
            self.send_to_server(Command.notice(
                nick, "\x01ECHO %s %s\x01" % (nick, arg)))
        elif name == "HELP":
            self.send_to_server(Command.notice(
                nick, "\x01HELP (VERSION|TIME|ECHO|HELP)\x01"))

    def _version(self) -> str:
        from . import VERSION
        return VERSION

    def nick_of(self, msg) -> str:
        m = re.match(r"^([^!]+)\!?", msg.prefix or "")
        if m:
            return m.group(1)
        return self.state.nick() or ""

    def _parse_isupport(self, q) -> None:
        if not q.params or "SUPPORTED" not in q.params[-1].upper():
            return
        for param in q.params[1:-1]:
            m = re.match(r"^(-)?([A-Z0-9]+)(?:=(.*))?$", param)
            if m:
                negate, key, value = m.group(1), m.group(2), m.group(3)
                if negate:
                    self._isupport.pop(key, None)
                else:
                    self._isupport[key] = value if value is not None else True
        self._logger.dlog("isupport: %r" % self._isupport)

    # ------------------------------------------------------------- bots
    def _make_prefix_object(self, msg) -> PrefixObject:
        if msg.prefix:
            return PrefixObject(msg.prefix)
        if msg.command.isdigit():
            return PrefixObject(self._config.padoka_server_name)
        return PrefixObject("%s!%s@%s" % (self.state.nick(), self._config.user,
                                          self._config.padoka_server_name))

    def send_to_bot(self, msg_or_name, *args) -> bool:
        if isinstance(msg_or_name, IRCMessage):
            prefix = self._make_prefix_object(msg_or_name)
            if msg_or_name.command.isdigit():
                name = ReplyName.lower_name(msg_or_name.command)
            else:
                name = msg_or_name.command.lower()
            if not name:
                return True
            selector = "on_" + name
            for bot in self._config.bots:
                try:
                    fn = getattr(bot, selector, None)
                    if fn:
                        fn(prefix, *msg_or_name.params)
                    every = getattr(bot, "on_every_message", None)
                    if every:
                        every(prefix, msg_or_name.command, *msg_or_name.params)
                except E.PDKBotBreak:
                    break
                except E.PDKBotSendCancel:
                    return False
                except Exception as ex:
                    self.ndk_error(ex)
            return True

        # synthetic event (a string/bytes name)
        if isinstance(msg_or_name, bytes):
            name = msg_or_name.decode()
        else:
            name = str(msg_or_name)
        selector = "on_" + name
        for bot in self._config.bots:
            try:
                fn = getattr(bot, selector, None)
                if fn:
                    fn(*args)
            except E.PDKBotBreak:
                break
            except E.PDKBotSendCancel:
                return False
            except Exception as ex:
                self.ndk_error(ex)
        return True

    # ------------------------------------------------------------- misc
    def ndk_status(self) -> List[str]:
        lines = [
            "== Padoka Running Status ==",
            "- padoka version: " + self._version(),
        ]
        if self._conn:
            lines.append("- connecting to %s:%s" % (self._conn.host, self._conn.port))
        lines.append("- clients status:")
        lines += ["  -- " + c.state for c in self._clients]
        lines.append("- Bots status:")
        lines += ["  -- " + b.bot_state() for b in self._config.bots]
        lines.append("== End of Status ==")
        return lines

    def ndk_error(self, err: Exception) -> None:
        self._logger.slog("Exception %s - %s" % (type(err).__name__, err))
        self._logger.slog("-- backtrace --")
        import traceback
        for line in traceback.format_exc().splitlines():
            self._logger.slog(line)

"""
Upstream IRC socket connection with a background reader thread.

The reader thread reads newline-delimited IRC messages from the socket and
pushes them onto an in-memory queue.  The consumer (the server main loop)
pulls ``IRCMessage`` objects from :pyattr:`queue`.

Sending is guarded by a coarse flood-control throttle so we never emit two
``PRIVMSG``/``NOTICE`` in the same second.
"""

from __future__ import annotations

import socket
import ssl
import queue
import threading
import time
from typing import Optional

from .message import IRCMessage

__all__ = ["Connection", "ConnectionClosed"]


class ConnectionClosed(Exception):
    """Raised when reading from a closed connection."""


class Connection:
    # The reader thread performs ALL socket I/O (reads plus draining the
    # outbound queue): an SSLSocket must not be read and written from
    # different threads concurrently.  A short recv timeout bounds the
    # latency an outbound message waits for the next read cycle.
    IO_POLL_TIMEOUT = 0.2
    # bound TCP connect + TLS handshake so a blackholed route fails over
    # to the next server quickly instead of hanging for the OS timeout
    CONNECT_TIMEOUT = 30.0

    def __init__(self, host: str, port: int,
                 ssl_params: Optional[dict] = None):
        self.host = host
        self.port = port
        self.ssl_params = ssl_params or None
        self.queue: "queue.Queue[object]" = queue.Queue()
        self._sock: Optional[socket.socket] = None
        self._tls: Optional[ssl.SSLSocket] = None
        self._buf = b""
        self._reader: Optional[threading.Thread] = None
        self._out_q: "queue.Queue[bytes]" = queue.Queue()
        self._last_send = 0.0
        self._closed = threading.Event()
        # human-readable reason for the most recent failure (connect / TLS
        # handshake / read), so callers can log *why* without re-raising
        self.last_error: Optional[str] = None

    def tls_info(self) -> Optional[str]:
        """Negotiated TLS version + cipher, or None when not using TLS."""
        t = self._tls
        if t is None:
            return None
        try:
            return "%s %s" % (t.version(), t.cipher())
        except (OSError, ValueError, AttributeError):
            return "TLS"

    @property
    def _io(self) -> Optional[socket.socket]:
        # SSLSocket is a socket subclass, so one type covers both
        return self._tls if self._tls is not None else self._sock

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> None:
        self.last_error = None
        try:
            raw = socket.create_connection(
                (self.host, self.port), timeout=self.CONNECT_TIMEOUT)
            self._sock = raw
            if self.ssl_params:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                if self.ssl_params.get("verify") is False:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                else:
                    ctx.check_hostname = bool(self.ssl_params.get("check_hostname", True))
                if self.ssl_params.get("ca_cert"):
                    ctx.load_verify_locations(self.ssl_params["ca_cert"])
                if self.ssl_params.get("cert_file"):
                    ctx.load_cert_chain(
                        self.ssl_params["cert_file"],
                        self.ssl_params.get("key_file"))
                self._tls = ctx.wrap_socket(raw, server_hostname=self.host)
        except (OSError, ssl.SSLError) as e:
            # socket.gaierror (DNS), ECONNREFUSED, TLS handshake/CA errors,
            # missing ca_cert files, ... -- all land here with the real reason
            stage = "connect" if self._sock is None else "tls handshake"
            self.last_error = "failed during %s: %s: %s" % (
                stage, type(e).__name__, e)
            self.close()
            raise
        self._buf = b""
        self._out_q = queue.Queue()
        self._closed.clear()
        # short recv timeout so the reader thread can pump the outbound
        # queue between reads.  Must be set on self._io itself: the
        # SSLSocket copies the timeout at wrap time, so changing `raw`
        # afterwards would not govern TLS recv().
        io = self._io
        if io is not None:
            io.settimeout(self.IO_POLL_TIMEOUT)
        self._start_reader()

    def _start_reader(self) -> None:
        self._reader = threading.Thread(target=self._read_loop,
                                        daemon=True, name="irc-reader")
        self._reader.start()

    def _read_loop(self) -> None:
        try:
            while not self._closed.is_set():
                if not self._pump_sends():
                    break
                io = self._io
                if io is None:
                    break
                try:
                    data = io.recv(4096)
                except socket.timeout:
                    continue
                except (OSError, ConnectionResetError) as e:
                    self.last_error = "read error: %s: %s" % (
                        type(e).__name__, e)
                    break
                if not data:
                    break
                self._buf += data
                while b"\n" in self._buf:
                    line, self._buf = self._buf.split(b"\n", 1)
                    self._handle_line(line)
        except (OSError, ConnectionResetError):
            pass
        finally:
            self.queue.put(None)  # EOF sentinel
            self._closed.set()

    def _pump_sends(self) -> bool:
        """Flush queued outbound bytes (reader thread only)."""
        io = self._io
        if io is None:
            return False
        while True:
            try:
                data = self._out_q.get_nowait()
            except queue.Empty:
                return True
            try:
                io.sendall(data)
            except (OSError, ConnectionResetError):
                return False

    def _handle_line(self, line: bytes) -> None:
        try:
            text = line.decode("utf-8", errors="replace")
            self.queue.put(IRCMessage.parse(text))
        except ValueError:
            pass  # skip malformed lines

    def close(self) -> None:
        # NB: do not null out self._sock / self._tls here; the reader thread
        # may still reference them (it exits on _closed / EOF).
        self._closed.set()
        for s in (self._tls, self._sock):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # -- sending -----------------------------------------------------------
    def send(self, msg: IRCMessage) -> None:
        if msg.command in ("PRIVMSG", "NOTICE"):
            now = time.time()
            if now - self._last_send < 1.0:
                time.sleep(1.0)
            self._last_send = time.time()
        if self._sock is None or self.closed:
            return
        data = msg.to_s().encode("utf-8", errors="replace")
        self._out_q.put(data)

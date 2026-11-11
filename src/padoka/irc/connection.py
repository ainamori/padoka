"""
Upstream IRC socket connection with a background reader thread.

The reader thread reads newline-delimited IRC messages from the socket and
pushes them onto an in-memory queue.  The consumer (the server main loop)
pulls ``IRCMessage`` objects from :pyattr:`queue`.

Sending is guarded by a sliding-window flood control (madoka-style): at most
500 characters or 8 ``PRIVMSG``/``NOTICE`` lines per 8-second window, waiting
out the window otherwise.  This avoids server-side flood bans during bursts
(e.g. backlog replay).
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
    # Flood-control window parameters (mirrors the original madoka flood.mpi).
    FLOOD_WINDOW = 8.0        # seconds
    FLOOD_MAX_CHARS = 500     # max PRIVMSG/NOTICE bytes per window
    FLOOD_MAX_LINES = 8       # max PRIVMSG/NOTICE lines per window

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
        self._send_lock = threading.Lock()
        self._flood_lock = threading.Lock()
        self._flood_start = 0.0
        self._flood_chars = 0
        self._flood_lines = 0
        self._closed = threading.Event()
        # diagnostics: what happened last (read by the server thread when the
        # connection drops, so the reason shows up in the log)
        self.last_error: Optional[str] = None
        self.last_line: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------
    CONNECT_TIMEOUT = 30  # seconds; bounds blackholed upstream connects

    def open(self) -> None:
        # A timeout so an unreachable upstream (dropped SYNs) cannot block
        # the server thread for minutes; raises OSError -> clean reconnect.
        raw = socket.create_connection((self.host, self.port),
                                       timeout=self.CONNECT_TIMEOUT)
        raw.settimeout(None)  # reader loop wants blocking I/O
        self._sock = raw
        if self.ssl_params:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = bool(self.ssl_params.get("check_hostname", True))
            if self.ssl_params.get("verify") is False:
                ctx.verify_mode = ssl.CERT_NONE
            if self.ssl_params.get("ca_cert"):
                ctx.load_verify_locations(self.ssl_params["ca_cert"])
            if self.ssl_params.get("cert_file"):
                ctx.load_cert_chain(
                    self.ssl_params["cert_file"],
                    self.ssl_params.get("key_file"))
            self._tls = ctx.wrap_socket(raw, server_hostname=self.host)
        self._buf = b""
        self._closed.clear()
        self._start_reader()

    def _start_reader(self) -> None:
        self._reader = threading.Thread(target=self._read_loop,
                                        daemon=True, name="irc-reader")
        self._reader.start()

    def _read_loop(self) -> None:
        try:
            while not self._closed.is_set():
                data = self._recv(4096)
                if not data:
                    break
                self._buf += data
                while b"\n" in self._buf:
                    line, self._buf = self._buf.split(b"\n", 1)
                    self._handle_line(line)
        except (OSError, ConnectionResetError) as e:
            self.last_error = "%s: %s" % (type(e).__name__, e)
        finally:
            self.queue.put(None)  # EOF sentinel
            self._closed.set()

    def _recv(self, n: int) -> bytes:
        if self._tls is not None:
            return self._tls.recv(n)
        if self._sock is None:
            return b""
        return self._sock.recv(n)

    def _handle_line(self, line: bytes) -> None:
        try:
            text = line.decode("utf-8", errors="replace")
            self.last_line = text
            self.queue.put(IRCMessage.parse(text))
        except ValueError:
            pass  # skip malformed lines

    def close(self) -> None:
        self._closed.set()
        for s in (self._tls, self._sock):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass
        self._tls = self._sock = None

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def tls_info(self) -> Optional[str]:
        """Human-readable TLS state of this connection, or None if plaintext."""
        if self._tls is None:
            return None
        try:
            ver = self._tls.version()
        except Exception:
            ver = "?"
        try:
            cert = self._tls.getpeercert()
        except Exception:
            cert = {}
        if cert:
            return "TLS %s, cert expires %s" % (ver, cert.get("notAfter", "?"))
        return "TLS %s (unverified)" % ver

    # -- sending -----------------------------------------------------------
    def _flood_throttle(self, msg: IRCMessage, nbytes: int) -> None:
        """Block (without holding the send lock) until this PRIVMSG/NOTICE fits
        the flood window.  The flood counters are guarded by a dedicated lock so
        concurrent senders (server thread + webui thread) can't race."""
        while True:
            with self._flood_lock:
                now = time.time()
                if now - self._flood_start >= self.FLOOD_WINDOW:
                    self._flood_start = now
                    self._flood_chars = 0
                    self._flood_lines = 0
                if (self._flood_chars + nbytes > self.FLOOD_MAX_CHARS or
                        self._flood_lines + 1 > self.FLOOD_MAX_LINES):
                    if self._flood_chars == 0 and self._flood_lines == 0:
                        # Empty window: allow a single oversized message
                        # immediately (matches madoka flood_send behaviour).
                        self._flood_chars = nbytes
                        self._flood_lines = 1
                        return
                    wait = self._flood_start + self.FLOOD_WINDOW - now
                else:
                    self._flood_chars += nbytes
                    self._flood_lines += 1
                    return
            if wait > 0:
                time.sleep(wait)

    def send(self, msg: IRCMessage) -> None:
        data = msg.to_s().encode("utf-8", errors="replace")
        if msg.command in ("PRIVMSG", "NOTICE"):
            self._flood_throttle(msg, len(data))
        with self._send_lock:
            if self._sock is None or self.closed:
                return
            try:
                if self._tls is not None:
                    self._tls.sendall(data)
                else:
                    self._sock.sendall(data)
            except OSError:
                self._closed.set()

    # -- blocking receive used by client handlers --------------------------
    def recv_line(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Blocking read of one full line (used by the client side)."""
        deadline = None if timeout is None else time.time() + timeout
        while b"\n" not in self._buf:
            remaining = None
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
            try:
                if self._tls is not None:
                    data = self._tls.recv(4096)
                elif self._sock is not None:
                    data = self._sock.recv(4096)
                else:
                    return None
            except OSError:
                return None
            if not data:
                return None
            self._buf += data
        line, self._buf = self._buf.split(b"\n", 1)
        return line

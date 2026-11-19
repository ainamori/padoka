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
        self._flood_lock = threading.Lock()
        self._flood_start = 0.0
        self._flood_chars = 0
        self._flood_lines = 0
        self._closed = threading.Event()
        # Serializes ALL socket I/O: an SSLSocket must not be read and
        # written from different threads concurrently (OpenSSL is not
        # thread-safe per-connection; the race loses records and can even
        # abort the process).  The reader thread polls non-blocking under
        # this lock; senders take it briefly around sendall.
        self._io_lock = threading.Lock()
        # diagnostics: what happened last (read by the server thread when the
        # connection drops, so the reason shows up in the log)
        self.last_error: Optional[str] = None
        self.last_line: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------
    CONNECT_TIMEOUT = 30  # seconds; bounds blackholed upstream connects

    def build_ssl_context(self) -> "ssl.SSLContext":
        """Build the TLS client context for the upstream connection.

        PROTOCOL_TLS_CLIENT enables CERT_REQUIRED + check_hostname but
        loads NO CA trust store (unlike ssl.create_default_context()), so
        a trust anchor must be added explicitly; otherwise verification
        of every public-CA certificate fails with "unable to get local
        issuer certificate":
        - ssl_params.ca_cert set  -> trust only that file (pinning)
        - otherwise (verify on)   -> the system default CA bundle
        """
        sp = self.ssl_params or {}
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if sp.get("verify") is False:
            # Disabling verification requires check_hostname to be off as
            # well (OpenSSL refuses CERT_NONE while check_hostname is on).
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx.check_hostname = bool(sp.get("check_hostname", True))
        if sp.get("ca_cert"):
            ctx.load_verify_locations(sp["ca_cert"])
        elif ctx.verify_mode != ssl.CERT_NONE:
            ctx.load_default_certs()
        if sp.get("cert_file"):
            ctx.load_cert_chain(
                sp["cert_file"],
                sp.get("key_file"))
        return ctx

    def open(self) -> None:
        # A timeout so an unreachable upstream (dropped SYNs) cannot block
        # the server thread for minutes; raises OSError -> clean reconnect.
        raw = socket.create_connection((self.host, self.port),
                                       timeout=self.CONNECT_TIMEOUT)
        self._sock = raw
        if self.ssl_params:
            ctx = self.build_ssl_context()
            # handshake on the (still blocking) socket, then switch to
            # non-blocking: the reader polls under _io_lock, so a long
            # idle recv never blocks a sender (see _read_loop / send)
            self._tls = ctx.wrap_socket(raw, server_hostname=self.host)
            # NB: wrap_socket() dups the fd into the SSLSocket and closes
            # the original object -- set non-blocking on _tls, not on raw.
            self._tls.setblocking(False)
        else:
            self._tls = None
            raw.setblocking(False)
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
                data = self._poll_recv()
                if data is None:      # no data yet (non-blocking poll)
                    time.sleep(0.01)
                    continue
                if data == b"":       # EOF / closed
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

    def _poll_recv(self) -> Optional[bytes]:
        """One non-blocking recv under the io lock.

        Returns the bytes received, None when no data is available yet,
        or b"" when the connection is closed/EOF.
        """
        with self._io_lock:
            sock = self._tls if self._tls is not None else self._sock
            if sock is None or self._closed.is_set():
                return b""
            try:
                return sock.recv(4096)
            except (BlockingIOError, ssl.SSLWantReadError,
                    socket.timeout):
                return None

    def _handle_line(self, line: bytes) -> None:
        try:
            text = line.decode("utf-8", errors="replace")
            self.last_line = text
            self.queue.put(IRCMessage.parse(text))
        except ValueError:
            pass  # skip malformed lines

    def close(self) -> None:
        self._closed.set()
        with self._io_lock:
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
        with self._io_lock:
            tls = self._tls
            if tls is None:
                return None
            try:
                ver = tls.version()
            except Exception:
                ver = "?"
            try:
                cert = tls.getpeercert()
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
        # non-blocking socket: sendall() is replaced by a retrying send()
        # under the io lock (concurrent SSL read/write is unsafe).  The
        # retries only spin while the send buffer is full (backpressure).
        with self._io_lock:
            sock = self._tls if self._tls is not None else self._sock
            if sock is None or self.closed:
                return
            view = memoryview(data)
            while view:
                try:
                    sent = sock.send(view)
                except (BlockingIOError, ssl.SSLWantWriteError):
                    time.sleep(0.01)
                    continue
                except OSError:
                    self._closed.set()
                    return
                view = view[sent:]

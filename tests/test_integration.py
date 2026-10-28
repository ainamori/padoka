# -*- coding: utf-8 -*-
"""
End-to-end integration test.

Spins up:
  * a fake upstream IRC server (127.0.0.1, random port)
  * PDKServer pointed at it (client port TLS-only + SASL PLAIN enabled)
  * fake IRC clients that log in via SASL or /PASS and check the proxy

This proves the client <-> padoka <-> server proxy loop works without any
real IRC network, including: plaintext rejection, SASL PLAIN
success/failure, and /PASS over TLS.
Run:  python3 tests/test_integration.py  (requires the openssl CLI)
"""

import base64
import os
import queue
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

# Add src/ to path for direct (non-installed) execution.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from padoka.server import PDKServer  # noqa: E402
from padoka.irc.message import IRCMessage  # noqa: E402


PADOKA_NICK = "padoka_py"


# ---------------------------------------------------------------------------
# Fake upstream IRC server
# ---------------------------------------------------------------------------
class FakeIRCServer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.conn = None
        self.got_privmsg = threading.Event()
        self.received = []

    def run(self):
        self.conn, _ = self.sock.accept()
        self.conn.settimeout(10)
        f = self.conn.makefile("rb")
        # keep a PING timer going
        threading.Thread(target=self._ping_loop, daemon=True).start()
        for line in f:
            text = line.decode("utf-8", "replace").strip()
            self.received.append(text)
            self._handle(text)

    def _ping_loop(self):
        for _ in range(3):
            time.sleep(0.3)
            try:
                self._send("PING :fake")
            except Exception:
                return

    def _send(self, text):
        if self.conn is None:
            return
        self.conn.sendall((text + "\r\n").encode("utf-8"))

    def _handle(self, text):
        m = IRCMessage.parse(text + "\r\n")
        cmd = m.command
        p = m.params
        if cmd == "NICK":
            self._send(":fake 001 %s :Welcome to the fake net" % PADOKA_NICK)
            self._send(":fake 004 %s fake 1.0 f :net v" % PADOKA_NICK)
            self._send(":fake 005 %s :CHANTYPES=# MAXLEN=400 :fake" % PADOKA_NICK)
        elif cmd == "JOIN":
            ch = p[0]
            self._send(":%s JOIN %s" % (PADOKA_NICK, ch))
            self._send(":fake 353 %s = %s :%s remote" % (PADOKA_NICK, ch, PADOKA_NICK))
            self._send(":fake 366 %s %s :End of NAMES list" % (PADOKA_NICK, ch))
        elif cmd == "PART":
            self._send(":%s PART %s" % (PADOKA_NICK, p[0]))
        elif cmd == "PRIVMSG":
            # echo back as a remote user so the bot sees it
            self.got_privmsg.set()
            self._send(":remote!user@host PRIVMSG %s :hi from remote" % p[0])
        elif cmd == "PING":
            # padoka already answers its own; ignore
            pass


# ---------------------------------------------------------------------------
# Fake IRC client
# ---------------------------------------------------------------------------
def _gen_self_signed_cert(cert_path, key_path):
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-days", "2", "-keyout", key_path, "-out", cert_path,
         "-subj", "/CN=localhost",
         "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True)
    return cert_path


class FakeClient:
    def __init__(self, port, tls_cert=None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        if tls_cert:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self.sock = ctx.wrap_socket(self.sock, server_hostname="localhost")
        self.sock.settimeout(0.25)  # reader polls; see _reader
        self.lines = []
        self._buf = b""
        self._lock = threading.Lock()
        self._out_q: "queue.Queue[bytes]" = queue.Queue()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self):
        # single I/O thread: an SSLSocket must not be read and written
        # from different threads concurrently (records get lost)
        while True:
            while True:
                try:
                    data = self._out_q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.sock.sendall(data)
                except OSError:
                    return
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            self._buf += data
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                with self._lock:
                    self.lines.append(line.decode("utf-8", "replace"))

    def send(self, *parts):
        self._out_q.put((" ".join(parts) + "\r\n").encode("utf-8"))

    def sasl_plain(self, password, expect_ok=True):
        """Drive a SASL PLAIN exchange.  Returns True when 904 (success) is
        seen; with expect_ok=False it returns True when 907 is seen."""
        self.send("AUTHENTICATE", "PLAIN")
        if not self.wait_for(lambda l: l.strip().endswith("AUTHENTICATE +")):
            return False
        payload = b"\x00\x00" + password.encode()
        self.send("AUTHENTICATE", base64.b64encode(payload).decode())
        want = " 904 " if expect_ok else " 907 "
        return bool(self.wait_for(lambda l: want in l))

    def wait_for(self, predicate, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                for l in self.lines:
                    if predicate(l):
                        return l
            time.sleep(0.05)
        return None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
def main():
    tmpdir = tempfile.mkdtemp(prefix="padoka_test_")
    client_port = _free_port()
    cert = _gen_self_signed_cert(os.path.join(tmpdir, "cert.pem"),
                                 os.path.join(tmpdir, "key.pem"))

    cfg_path = os.path.join(tmpdir, "test.yml")
    server = FakeIRCServer()
    server.start()

    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(f"""
setting_name: pytest
loglevel: 2
client_server:
  host: 127.0.0.1
  port: {client_port}
  pass: testpass
  sasl: true
  acl: null
  ssl_cert_file: {cert}
  ssl_key_file: {os.path.join(tmpdir, 'key.pem')}
servers:
  - host: 127.0.0.1
    port: {server.port}
user: tester
nick: {PADOKA_NICK}
realname: integration test
away_message: away
away_nick: null
reconnect_delay: 5
log_dir: {tmpdir}/log
plugins_dir: {os.path.dirname(os.path.abspath(__file__))}/../plugins
bots:
  - name: samplebot
  - name: backlogbot
    talker: false
    prefix: "BL"
    sender: "backlogbot"
channel_info:
  "#test":
    timing: startup
""")

    ndk = PDKServer(cfg_path)
    srv_thread = threading.Thread(target=ndk.start, daemon=True)
    srv_thread.start()

    try:
        time.sleep(0.5)
        assert ndk.connected, "padoka did not connect to fake server"
        print("[ok] padoka connected to fake upstream server")

        # plaintext (non-TLS) client must not get through the IRC layer:
        # either a 908 reply or a TLS-level reset/closed connection
        raw = socket.create_connection(("127.0.0.1", client_port), timeout=5)
        raw.sendall(b"NICK plainx\r\n")
        raw.settimeout(5)
        blob = b""
        try:
            while True:
                chunk = raw.recv(4096)
                if not chunk or b"908" in blob:
                    break
                blob += chunk
        except (socket.timeout, OSError):
            pass
        raw.close()
        # acceptable rejections: TLS alert/reset (empty blob) or 908 ERR_UNENCRYPTED
        assert b"001" not in blob and b"Welcome" not in blob, \
            "plaintext client reached the IRC layer: %r" % blob
        print("[ok] plaintext client rejected (TLS alert/reset or 908)")

        # a fake client logs in over TLS using SASL PLAIN (no /PASS)
        cl = FakeClient(client_port, tls_cert=cert)
        assert cl.sasl_plain("testpass"), "SASL PLAIN authentication failed"
        print("[ok] client authenticated via SASL PLAIN (904)")
        cl.send("NICK", "client1")
        cl.send("USER", "client1", "0", "0", ":client1")

        welcome = cl.wait_for(lambda l: " 001 " in l or "001" in l)
        assert welcome, "client did not receive 001 welcome"
        print("[ok] SASL client received 001 welcome:", welcome)

        # second client logs in over TLS with /PASS (classic path)
        cl2 = FakeClient(client_port, tls_cert=cert)
        cl2.send("PASS", "testpass")
        cl2.send("NICK", "passclient")
        cl2.send("USER", "passclient", "0", "0", ":passclient")
        w2 = cl2.wait_for(lambda l: " 001 " in l)
        assert w2, "/PASS-over-TLS client did not receive 001 welcome"
        print("[ok] /PASS over TLS client received 001 welcome")

        # SASL with a wrong password must fail with 907
        cl3 = FakeClient(client_port, tls_cert=cert)
        assert cl3.sasl_plain("wrongpass", expect_ok=False), \
            "SASL wrong password was not rejected (907)"
        print("[ok] SASL wrong password rejected with 907")
        cl3.close()

        # padoka should have joined #test -> client gets 353 names
        names = cl.wait_for(lambda l: "353" in l and "#test" in l)
        assert names, "client did not receive channel NAMES (353)"
        print("[ok] client received NAMES:", names)

        # PING/PONG watchdog: padoka must answer the fake server's PING
        time.sleep(1.2)
        pongs = [l for l in server.received if l.startswith("PONG")]
        assert pongs, "padoka did not PONG the upstream PING (received: %r)" % server.received
        print("[ok] padoka answered upstream PING with PONG")

        # client -> server: send a PRIVMSG, fake server echoes as remote,
        # samplebot replies to the channel.
        cl.send("PRIVMSG", "#test", ":hello bot")
        reply = cl.wait_for(lambda l: "NOTICE" in l and "Yes" in l, timeout=5.0)
        assert reply, "did not receive samplebot notice reply"
        print("[ok] samplebot replied:", reply)

        # the echoed remote PRIVMSG should be relayed to the client
        remote = cl.wait_for(lambda l: "PRIVMSG" in l and "hi from remote" in l,
                             timeout=5.0)
        assert remote, "remote PRIVMSG was not relayed to the client"
        print("[ok] upstream message relayed to client:", remote)

        # /PADOKA STATUS control command
        cl.send("PRIVMSG", PADOKA_NICK, ":/padoka status")
        status = cl.wait_for(lambda l: "Running Status" in l, timeout=5.0)
        assert status, "did not receive /padoka status"
        print("[ok] /PADOKA STATUS works:", status)

        cl.close()
        cl2.close()
        time.sleep(0.5)
        print("\n=== ALL INTEGRATION CHECKS PASSED ===")
    finally:
        ndk.invoke_event("quit_program")
        time.sleep(0.3)
        try:
            server.sock.close()
        except OSError:
            pass
        try:
            server.conn and server.conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()

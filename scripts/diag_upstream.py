#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
上流 IRC サーバー接続診断スクリプト（padoka が接続できないときの切り分け用）。

設定ファイル（YAML）の上流サーバーへ、
  1) TCP 接続
  2) TLS ハンドシェイク（検証失敗時は証明書情報付きで無検証リトライ）
  3) SASL PLAIN 認証（ircv3 AUTHENTICATE）
  4) NICK/USER 登録
の各ステップを順番に行い、サーバーからの生レスポンスをそのまま表示します。

使いかた:
    python3 scripts/diag_upstream.py -c config/padoka.yml
    python3 scripts/diag_upstream.py -c config/padoka.yml --no-sasl   # SASL なし登録だけ
    python3 scripts/diag_upstream.py --host dev.ircnet.ne.jp --port 6697 --user NAME --pass PW

終了コード: 0 = 登録完了(001), 1 = 失敗, 2 = 引数エラー
"""

import argparse
import base64
import select
import socket
import ssl
import subprocess
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser(
        description="upstream IRC connection diagnostic (TCP/TLS/SASL/login)")
    ap.add_argument("-c", "--config", default="config/padoka.yml",
                    help="YAML config to read servers[0] from")
    ap.add_argument("--host", help="override server host")
    ap.add_argument("--port", type=int, help="override server port")
    ap.add_argument("--user", help="override SASL username")
    ap.add_argument("--pass", dest="password", help="override SASL password")
    ap.add_argument("--nick", help="override nickname")
    ap.add_argument("--no-sasl", action="store_true",
                    help="skip SASL (register without authentication)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--tls", dest="tls", action="store_true",
                   default=None, help="force TLS")
    g.add_argument("--no-tls", dest="tls", action="store_false",
                   help="force plaintext")
    ap.add_argument("--timeout", type=float, default=15.0,
                    help="connect timeout seconds (default 15)")
    ap.add_argument("--sasl-wait", type=float, default=30.0,
                    help="seconds to wait for the 905 SASL continuation "
                         "prompt after AUTHENTICATE PLAIN (default 30)")
    args = ap.parse_args()

    # --- resolve parameters ------------------------------------------------
    host = args.host
    port = args.port
    user = args.user
    password = args.password
    nick = args.nick
    sasl_cfg = {}
    use_tls = args.tls  # None = auto
    try:
        import yaml  # type: ignore
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        sv = (cfg.get("servers") or [{}])[0]
        host = host or sv.get("host")
        port = port or int(sv.get("port", 6667))
        if sv and use_tls is None:  # explicit --tls/--no-tls wins
            use_tls = bool(sv.get("ssl_params"))
        if not args.no_sasl:
            sasl_cfg = sv.get("sasl") or {}
        user = user or sasl_cfg.get("username") or cfg.get("user")
        if password is None:
            password = sasl_cfg.get("password")
        nick = nick or cfg.get("nick") or user
    except FileNotFoundError:
        print("(config %s not found; relying on --host/--port)" % args.config)
    if use_tls is None:
        use_tls = port in (6697, 6679)  # common TLS ports

    if not host:
        print("FAIL: no host (set --host or servers[0].host in config)")
        return 2
    sasl_on = not args.no_sasl and bool(sasl_cfg)
    print("target : %s:%d  (%s)" % (host, port,
                                    "TLS" if use_tls else "plaintext"))
    print("sasl   : %s%s" % ("on" if sasl_on else "off",
                             ("  (user=%s, password %s)"
                              % (user, "set" if password else "MISSING"))
                             if sasl_on else ""))
    print("nick   : %s" % nick)
    print()

    # --- 1) TCP ------------------------------------------------------------
    t0 = time.time()
    try:
        raw = socket.create_connection((host, port), timeout=args.timeout)
    except OSError as e:
        print("1) TCP  : FAIL  %s: %s" % (type(e).__name__, e))
        print("   -> DNS / 経路 / firewall の問題。"
              " `getent hosts %s` と `nc -vz %s %d` を確認" % (host, host, port))
        return 1
    print("1) TCP  : OK    (%.2fs)" % (time.time() - t0))

    def _cert_details(der: bytes) -> str:
        # getpeercert() is empty when verification is off; use the openssl
        # CLI on the DER cert if available
        try:
            out = subprocess.run(
                ["openssl", "x509", "-inform", "DER", "-noout",
                 "-subject", "-issuer", "-enddate"],
                input=der, capture_output=True, timeout=10)
            if out.returncode == 0:
                return " | ".join(
                    l.decode().strip() for l in out.stdout.splitlines())
        except (OSError, subprocess.SubprocessError):
            pass
        return "<openssl unavailable>"

    # --- 2) TLS ------------------------------------------------------------
    tls = raw
    tls_unverified = False
    if use_tls:
        try:
            ctx = ssl.create_default_context()
            tls = ctx.wrap_socket(raw, server_hostname=host)
        except ssl.SSLCertVerificationError as e:
            print("2) TLS  : FAIL  証明書検証エラー: %s" % e)
            # unverified retry: prove the handshake itself works, then keep
            # diagnosing (SASL/login) on that connection
            try:
                raw2 = socket.create_connection(
                    (host, port), timeout=args.timeout)
                t2 = ssl._create_unverified_context().wrap_socket(
                    raw2, server_hostname=host)
                der = t2.getpeercert(binary_form=True)
                print("   (無検証リトライ: OK  %s)" % t2.version())
                if der:
                    print("   サーバ証明書: %s" % _cert_details(der))
                print("   -> サーバー側の TLS は正常。ローカルホストの CA"
                      "トラストストアが古い/不足（Let's Encrypt 中間証明書など）"
                      "の可能性が高い。")
                print("      更新例: dnf update ca-certificates && update-ca-trust")
                print("   無検証接続で SASL/登録の診断を続行...")
                raw.close()
                tls, tls_unverified = t2, True
            except Exception as e2:
                print("   (無検証リトライも失敗: %s: %s)"
                      % (type(e2).__name__, e2))
                raw.close()
                return 1
        except (ssl.SSLError, OSError) as e:
            print("2) TLS  : FAIL  %s: %s" % (type(e).__name__, e))
            raw.close()
            return 1
        if not tls_unverified:
            cert = tls.getpeercert()
            print("2) TLS  : OK    %s, cert notAfter=%s"
                  % (tls.version(), cert.get("notAfter", "?")))
    else:
        print("2) TLS  : SKIP  plaintext")

    # --- helpers -----------------------------------------------------------
    _rb = [b""]  # receive buffer (select-driven; makefile().peek() would
                 # block on the raw socket and break the bounded wait)

    def read_bounded(timeout: float):
        """Read one line within timeout seconds.

        Returns the line (str), None on timeout, or "" if the
        connection was closed.
        """
        end = time.time() + max(0.0, timeout)
        while b"\n" not in _rb[0]:
            remaining = end - time.time()
            if remaining <= 0:
                return None
            try:
                r, _, _ = select.select([tls], [], [], min(remaining, 1.0))
            except OSError:
                return ""
            if not r:
                continue
            try:
                data = tls.recv(4096)
            except ssl.SSLWantReadError:
                continue
            except (OSError, TimeoutError):
                return ""
            if not data:
                return ""
            _rb[0] += data
        line, _rb[0] = _rb[0].split(b"\n", 1)
        return line.decode("utf-8", "replace").strip()

    def cmd_of(line: str) -> str:
        """IRC command of a line, tolerant of a :server prefix."""
        parts = line.split(None, 2)
        if line.startswith(":") and len(parts) >= 2:
            return parts[1].upper()
        return (parts[0] if parts else "").upper()

    def rd(n: int = 3, wait: float = 5.0, stop_when=None):
        """read up to n lines; stop early on EOF/close or stop_when(line)."""
        out = []
        end = time.time() + wait
        while len(out) < n and time.time() < end:
            t = read_bounded(max(0.2, end - time.time()))
            if t is None:
                continue
            if t == "":
                out.append("<EOF: connection closed>")
                break
            out.append(t)
            print("     R| %s" % t[:200])
            if stop_when and stop_when(t):
                break
        return out

    def sd(line: str):
        print("     S| %s" % line[:120])
        try:
            tls.sendall((line + "\r\n").encode())
        except OSError as e:
            print("     (send failed: %s)" % type(e).__name__)

    # --- 3) SASL ------------------------------------------------------------
    sasl_ok = False
    sasl_state = "off"  # off | waiting905 | sent | ok | fail | stall | closed
    if sasl_on:
        if not password:
            print("3) SASL : SKIP  sasl ブロックはあるが password が空")
        else:
            tok = base64.b64encode(
                b"\x00" + str(user).encode() + b"\x00"
                + str(password).encode()).decode()
            t0 = time.time()

            def ts():
                return "[%5.1fs]" % (time.time() - t0)

            print("3) SASL : AUTHENTICATE PLAIN (user=%s)" % user)
            sd("AUTHENTICATE PLAIN")
            sasl_state = "waiting905"
            # Wait ONLY for the 905 continuation prompt (or an immediate
            # 904/907/908/ERROR).  Never send the token unprompted -- doing
            # so aborts the server's connection processing.
            end = time.time() + args.sasl_wait
            while sasl_state == "waiting905" and time.time() < end:
                t = read_bounded(min(2.0, end - time.time()))
                if t is None:
                    continue
                if t == "":
                    print("     %s R| <EOF: connection closed by server>" % ts())
                    sasl_state = "closed"
                    break
                print("     %s R| %s" % (ts(), t[:200]))
                c = cmd_of(t)
                if c == "905":
                    sasl_state = "sent"
                elif c == "AUTHENTICATE":  # some servers: "AUTHENTICATE +"
                    sasl_state = "sent"
                elif c == "904":
                    sasl_state = "ok"
                    sasl_ok = True
                elif c in ("906", "907", "908"):
                    sasl_state = "fail"
                elif c == "ERROR":
                    sasl_state = "closed"
            if sasl_state == "waiting905":
                sasl_state = "stall"
            if sasl_state == "sent":
                sd("AUTHENTICATE " + tok)
                r = rd(n=4, wait=8,
                       stop_when=lambda t: cmd_of(t) in ("904", "906",
                                                          "907", "908",
                                                          "ERROR")
                       or t.startswith("<EOF")
                       or t.startswith("<closed"))
                if any(cmd_of(t) == "904" for t in r):
                    sasl_state = "ok"
                    sasl_ok = True
                elif any(cmd_of(t) == "907" for t in r):
                    sasl_state = "fail"
            # --- report -------------------------------------------------
            if sasl_state == "ok":
                print("   -> OK (904)  SASL 認証成功")
            elif sasl_state == "fail":
                print("   -> FAIL (906/907/908)  認証情報不一致 or PLAIN 非対応")
            elif sasl_state == "stall":
                print("   -> STALL: サーバーが 905 (SASL 継続プロンプト) を "
                      "%ds 間送らず。" % int(args.sasl_wait))
                print("      受信したのは ircnet 固有の 020 "
                      "'Please wait while we process your connection' だけ。")
                print("      => サーバー側の『接続処理』が SASL 進捗前に完了していない。")
                print("      次に行うこと:")
                print("        1) --sasl-wait 60 で待ち時間を伸ばして再試行")
                print("           （905 が遅れて来るだけなら接続が成立する）")
                print("        2) それでも STALL なら、この IP (GCP 等) に対する")
                print("           サーバー側処理 (rDNS 検証等) が失敗している可能性。")
                print("           別環境(日本国内)から同じ資格情報で試す。")
            elif sasl_state == "closed":
                print("   -> FAIL: SASL 処理中にサーバーが接続を切断。")
            else:
                print("   -> state=%s" % sasl_state)
    else:
        print("3) SASL : SKIP")

    # --- 4) register ----------------------------------------------------------
    print("4) LOGIN: NICK/USER"
          + ("  [SASL OK 済み]" if sasl_ok else ""))
    time.sleep(0.3)
    sd("NICK " + str(nick))
    sd("USER %s 0 * :diag" % (user or "diag"))
    r = rd(n=8, wait=6,
           stop_when=lambda t: " 001 " in " " + t or t.startswith("ERROR"))
    joined = any(" 001 " in " " + t for t in r)
    if joined:
        if sasl_on and not sasl_ok:
            print("   -> OK  登録は完了したが SASL 認証は失敗(907 等)。")
            print("      本番 ircnet では SASL 失敗時は接続が拒否されます。")
        else:
            print("   -> OK  登録完了 (001 welcome)。padoka 側で再接続しない場合、")
            print("      config/padoka.yml の servers / sasl がこちらと異なる可能性を確認。")
        if tls_unverified:
            print("   注意: TLS は無検証で続行したもの。CA 証明書ストアの更新"
                  "（上記）を忘れずに行うこと。")
        tls.close()
        return 0
    err = [t for t in r if t.startswith("ERROR")]
    if err:
        print("   -> FAIL サーバーが ERROR を返して切断: %s" % err[0])
    elif "<EOF: connection closed>" in r:
        if sasl_on and not sasl_ok:
            print("   -> FAIL SASL 未取得のまま登録して切断された")
        else:
            print("   -> FAIL 応答なしで切断された。海外からの SASL なし接続は")
            print("      ircnet が拒否します（--no-sasl ではなく SASL 認証を試す）。")
    else:
        print("   -> FAIL 001 未取得（上記 R| 行を確認）")
    try:
        tls.close()
    except OSError:
        pass
    return 1


if __name__ == "__main__":
    sys.exit(main())

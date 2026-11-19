#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
padoka クライアントポート（既定 6667）への接続診断スクリプト。

IRC クライアント（irssi / weechat 等）をクライアントポートにつないで
「何も起こらない / ログに痕跡がない」場合の切り分け用。
本スクリプトが最小ログイン（PASS/NICK/USER）を実行し、
サーバーの生応答をそのまま表示して原因を判定します。

確認する事項:
  1) ポートが平文専用か TLS 専用か
  2) PASS 照合（464 Password Incorrect）
  3) ACL による拒否
  4) ログイン成立（001 welcome / 上流未登録なら 401）

注意: padoka は単一ニック名のプロキシです。クライアントは上流 nick
（config の nick:）を借りて動作し、ネットワーク上に自分固有の nick として
現れません。「自分の nick がチャネルに見えない」のは設計どおりです。

使いかた:
    python3 scripts/diag_client.py -c config/padoka.yml
    python3 scripts/diag_client.py --host 127.0.0.1 --port 6667 --pass PW

終了コード: 0 = ログイン成立, 1 = 失敗, 2 = 引数エラー
"""

import argparse
import socket
import ssl
import sys


def load_config(path):
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except (OSError, ValueError):
        return {}
    cs = cfg.get("client_server") or {}
    return {
        "port": cs.get("port"),
        "pass": cs.get("pass"),
        "tls": bool(cs.get("ssl_cert_file") and cs.get("ssl_key_file")),
        "acl": bool(cs.get("acl")),
    }


def probe(host, port, use_tls, passw, timeout=8.0, nick="diagcl"):
    """Minimal login attempt.  Returns (lines, note).

    lines: list of IRC lines received (empty when nothing arrives)
    note:  "" or an error/hint string
    """
    try:
        s = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)
    if use_tls:
        try:
            ctx = ssl._create_unverified_context()
            s = ctx.wrap_socket(s, server_hostname=host)
        except (ssl.SSLError, OSError) as e:
            s.close()
            return [], "TLS handshake failed: %s: %s" % (type(e).__name__, e)
    s.settimeout(timeout)
    if passw:
        try:
            s.sendall(("PASS %s\r\n" % passw).encode())
        except OSError:
            pass
    try:
        s.sendall(("NICK %s\r\nUSER %s 0 * :diag\r\n" % (nick, nick)).encode())
    except OSError:
        pass
    lines, buf, binary = [], b"", False
    try:
        while True:
            d = s.recv(4096)
            if not d:
                break
            if d[:1] == b"\x15":  # TLS alert record: the port wants TLS
                binary = True
                break
            buf += d
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                lines.append(line.decode("utf-8", "replace").rstrip("\r"))
    except socket.timeout:
        pass
    except OSError:
        pass
    finally:
        try:
            s.close()
        except OSError:
            pass
    note = ""
    if binary:
        note = "(TLS alert を受信: ポートは TLS 接続を要求しています)"
    return lines, note


def _has(lines, num):
    return any(" %s " % num in " " + l for l in lines)


def main():
    ap = argparse.ArgumentParser(description="padoka client-port diagnostic")
    ap.add_argument("-c", "--config", default="config/padoka.yml")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int)
    ap.add_argument("--pass", dest="passw")
    ap.add_argument("--timeout", type=float, default=8.0,
                    help="seconds to wait for login replies (default 8)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    host = args.host
    port = args.port or cfg.get("port")
    passw = args.passw if args.passw is not None else cfg.get("pass")
    tls_cfg = bool(cfg.get("tls"))
    acl_cfg = bool(cfg.get("acl"))
    if not port:
        print("FAIL: port が決まりません (client_server.port / --port)")
        return 2

    print("target  : %s:%d" % (host, port))
    print("config  : %s  (pass=%s, tls=%s, acl=%s)" % (
        args.config, "set" if passw else "none",
        "on" if tls_cfg else "off", "on" if acl_cfg else "off"))
    print("note    : padoka は単一ニック名のプロキシ: クライアントは上流")
    print("          nick を借りて動作し、固有 nick でチャネルに入りません")
    print()

    ok = False

    # --- 1) plaintext ------------------------------------------------------
    print("1) 平文ログイン")
    lines, note = probe(host, port, False, passw, args.timeout)
    if lines is None:
        print("   FAIL  接続できません: %s" % note)
        print("   -> padoka が起動しているか / ポート番号を確認")
        return 1
    for l in lines:
        print("   R| %s" % l[:200])
    if note:
        print("   %s" % note)
    if lines and _has(lines, "001"):
        print("   -> OK  ログイン成立 (001 受信)")
        n_ch = sum(1 for l in lines if " 353 " in l)
        if n_ch:
            print("   -> 状態ダンプ: %d チャンネルを受信 (353)" % n_ch)
            for l in lines:
                if " 353 " in l:
                    print("      %s" % l[:200])
        else:
            print("   -> WARN  状態ダンプにチャンネルがない (353 受信なし)。")
            print("      padoka は上流でどのチャンネルにも参加していない可能性が高い:")
            print("      config の channel_info に対象チャンネルがあるか、上流ログに")
            print("      'JOIN <ch>' / 353 が出ているかを確認してください。")
        ok = True
    elif lines and _has(lines, "464"):
        print("   -> FAIL  464 Password Incorrect: PASS が一致しません")
        print("      (client_server.pass とクライアント側の /PASS を一致させる)")
    elif lines and _has(lines, "401"):
        print("   -> WARN  401: padoka が上流に未登録のままです。")
        print("      上流接続（SASL）が成立してからの再実行で確認してください。")
    elif not lines:
        print("   -> 応答なし（すぐ切断された）。")
        if tls_cfg:
            print("      想定内: クライアントポートは TLS 専用です。")
        elif acl_cfg:
            print("      最有力: ACL 拒否 (client_server.acl がこの IP を外している)")
        else:
            print("      想定外: padoka ログ / ポート開放を確認")

    # --- 2) TLS --------------------------------------------------------------
    if tls_cfg or (lines is not None and not lines):
        print()
        print("2) TLS ログイン（証明書は無検証）")
        lines2, note2 = probe(host, port, True, passw, args.timeout)
        if note2.startswith("TLS handshake failed"):
            print("   %s" % note2)
            if not tls_cfg:
                print("   -> 設定が tls=off のため想定内（平文専用ポート）")
        else:
            for l in lines2 or []:
                print("   R| %s" % l[:200])
            if lines2 and _has(lines2, "001"):
                print("   -> OK  TLS でログイン成立 (001 受信)")
                n_ch = sum(1 for l in lines2 if " 353 " in l)
                print("   -> 状態ダンプ: %d チャンネルを受信 (353)" % n_ch
                      if n_ch else
                      "   -> WARN  状態ダンプにチャンネルがない (353 受信なし)。")
                ok = True
                print("   クライアントは TLS で接続する必要があります。例 (irssi):")
                print("       /SERVER ADD -tls -notls_verify %s %d %s"
                      % (host, port, passw or ""))
                print("       /CONNECT %s %d" % (host, port))
            elif not lines2:
                print("   -> TLS でも応答なし。設定 / ポート開放を確認")

    print()
    if ok:
        print("=== クライアントポート ログイン OK ===")
        print("実際のクライアントで痕跡がない場合は、接続方式（平文/TLS）と")
        print("PASS が本スクリプトと一致しているか照合してください。")
        return 0
    print("=== クライアントポート ログイン失敗 ===")
    return 1


if __name__ == "__main__":
    sys.exit(main())

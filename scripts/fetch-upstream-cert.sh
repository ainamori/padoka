#!/bin/sh
# 上流 IRC サーバーの TLS 証明書を取得・診断し、必要なら padoka の
# 信頼アンカー (ssl_params.ca_cert) として固定する。
#
# 用法:   scripts/fetch-upstream-cert.sh <host> <port> [out.pem]
# 例:     scripts/fetch-upstream-cert.sh dh.ircnet.ne.jp 6697 config/certs/ircnet-ca.pem
#         （out.pem を省略すると診断のみ。ファイルは書出されない）
#
# 判定ロジック（padoka と同じ条件: SNI + ホスト名検証）:
#   テスト1: システム CA ストアのみ（ca_cert なし）
#   テスト2: システムストア + 本スクリプトが作る ca_cert
#   書出ルール:
#     * 公的 CA（Let's Encrypt 等）: ca_cert は不要で、中間CAだけのファイルは
#       検証を壊す（OpenSSL の挙動）ため書出しない。ストアが古い場合の
#       対処は `dnf update ca-certificates`。
#     * 自己署名: leaf（= root）を書出。
#     * 私有 CA: 中間CA + root を書出（leaf は除外）。
#
# 手順:
#   1. サーバーが到達できるマシンで実行
#   2. 表示された issuer / 指紋と「判定」に従って対処
#   3. 対処後に再実行し、テスト1が成功することを確認
#
# 証明書がローテーションされた場合は再実行し、指紋が変わったことを
# 確認のうえ padoka 設定を点検してください。

set -u

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    echo "usage: $0 <host> <port> [out.pem]" >&2
    exit 2
fi
host=$1
port=$2
out=${3:-}

pem=""
raw=""
pyraw=""

# --- 1) openssl s_client（チェーン全体。stdout のみ = PEM を汚さない） ---
if command -v openssl >/dev/null 2>&1; then
    raw=$(openssl s_client -showcerts -connect "${host}:${port}" \
            -servername "${host}" </dev/null 2>/dev/null) || true
    pem=$(printf '%s\n' "$raw" | sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p')
fi

# --- 2) python3 フォールバック（leaf のみ） --------------------------------
method="openssl s_client"
if [ -z "$pem" ] && command -v python3 >/dev/null 2>&1; then
    pyraw=$(python3 - "$host" "$port" 2>&1 <<'PYEOF'
import base64, socket, ssl, sys
host, port = sys.argv[1], int(sys.argv[2])
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
raw = socket.create_connection((host, port), timeout=30)
s = ctx.wrap_socket(raw, server_hostname=host)
try:
    der = s.getpeercert(binary_form=True)
finally:
    s.close()
b64 = base64.encodebytes(der).decode().rstrip()
sys.stdout.write("-----BEGIN CERTIFICATE-----\n" + b64 + "\n"
                 + "-----END CERTIFICATE-----\n")
PYEOF
) || pyraw=""
    if [ -n "$pyraw" ]; then
        pem=$(printf '%s\n' "$pyraw" | sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p')
        [ -n "$pem" ] && method="python3 (leaf のみ)"
    fi
fi

# --- 3) 取得失敗時は生出力で原因を提示 --------------------------------------
if [ -z "$pem" ]; then
    echo "ERROR: ${host}:${port} から証明書を取得できません。" >&2
    if [ -n "$raw" ]; then
        echo "--- openssl s_client の出力（末尾 25 行） ---" >&2
        printf '%s\n' "$raw" | tail -n 25 >&2
    fi
    if [ -n "$pyraw" ]; then
        echo "--- python3 の出力（末尾 25 行） ---" >&2
        printf '%s\n' "$pyraw" | tail -n 25 >&2
    fi
    echo "---" >&2
    echo "確認: (1) ${host}:${port}/tcp に出せるか  (2) TLS を要求しているか  (3) openssl/python3 のいずれかがあるか" >&2
    exit 1
fi

# --- 4) 診断 + 検証テスト + 書出 -------------------------------------------
# 注: PEM は stdin ではなく一時ファイル経由（heredoc プログラムと stdin が
#     衝突するため）。
if command -v python3 >/dev/null 2>&1; then
    pemfile=$(mktemp) || exit 1
    printf '%s\n' "$pem" > "$pemfile"
    python3 - "$host" "$port" "${out:-}" "$method" "$pemfile" <<'PYEOF'
import base64, hashlib, os, re, socket, ssl, sys, tempfile
host, port, out, method, pemfile = (sys.argv[1], int(sys.argv[2]),
                                     sys.argv[3], sys.argv[4], sys.argv[5])
pem = open(pemfile).read()
os.unlink(pemfile)
blocks = re.findall(r"-----BEGIN CERTIFICATE-----\n(.*?)\n-----END CERTIFICATE-----",
                    pem, re.S)
if not blocks:
    print("ERROR: PEM block が 1 つもありません。再取得してください。")
    sys.exit(1)

import subprocess as _sp
for i, b in enumerate(blocks):
    try:
        der = base64.b64decode(re.sub(r"\s+", "", b), validate=True)
    except Exception as e:
        print("ERROR: block " + str(i) + " の base64 が壊れています（" + repr(e) + "）。再取得してください。")
        sys.exit(1)
    fd, path = tempfile.mkstemp(suffix=".der"); os.close(fd)
    with open(path, "wb") as f: f.write(der)
    try:
        _sp.run(["openssl", "x509", "-inform", "DER", "-in", path, "-noout"],
                check=True, capture_output=True, timeout=10)
    except Exception:
        print("ERROR: block " + str(i) + " は証明書として parse できません。再取得してください。")
        sys.exit(1)
    finally:
        os.unlink(path)

print("== 提示証明書: " + host + ":" + str(port) + " (取得方法: " + method + ", " + str(len(blocks)) + " blocks) ==")

def fmt(t):
    return ", ".join("%s=%s" % (k, v) for pair in t for k, v in pair)

infos = []
for b64body in blocks:
    der = base64.b64decode(re.sub(r"\s+", "", b64body))
    info = {"fp": ":".join("%02X" % x for x in hashlib.sha256(der).digest())}
    try:
        fd, path = tempfile.mkstemp(suffix=".pem"); os.close(fd)
        with open(path, "w") as f:
            f.write("-----BEGIN CERTIFICATE-----\n%s\n-----END CERTIFICATE-----\n" % b64body)
        try:
            d = ssl._ssl._test_decode_cert(path)
            info["subject"] = fmt(d["subject"]); info["issuer"] = fmt(d["issuer"])
            info["notAfter"] = d.get("notAfter")
            info["selfsigned"] = (info["subject"] == info["issuer"])
        finally:
            os.unlink(path)
    except Exception:
        pass
    infos.append(info)

for i, info in enumerate(infos):
    print("cert[" + str(i) + "] sha256=" + info["fp"])
    if "subject" in info:
        print("         subject: " + info["subject"])
        print("         issuer:  " + info["issuer"])
        print("         notAfter: " + str(info["notAfter"]))

PUBLIC_CA_KEYS = ("let's encrypt", "lets encrypt", "isrg", "google trust services",
                  "gts root", "gts r", "digicert", "sectigo", "usertrust",
                  "comodo", "amazon", "microsoft", "entrust", "globalsign", "quovadis")
leaf_issuer = (infos[0].get("issuer", "").lower())
selfsigned = infos[0].get("selfsigned", False)
if selfsigned:
    kind = "selfsigned"
elif any(k in leaf_issuer for k in PUBLIC_CA_KEYS):
    kind = "public"
else:
    kind = "private"

def attempt(cafile):
    ctx = ssl.create_default_context(cafile=cafile) if cafile else \
        ssl.create_default_context()
    try:
        s = ctx.wrap_socket(socket.create_connection((host, port), timeout=30),
                            server_hostname=host)
        s.close()
        return True, None
    except ssl.SSLCertVerificationError as e:
        return False, str(e.verify_message)
    except OSError as e:
        return False, "接続不可: %s" % e

# テスト1: システムストアのみ
ok1, err1 = attempt(None)
print("テスト1（システム CA ストアのみ）: " + ("成功" if ok1 else "失敗 (" + str(err1) + ")"))

# テスト2: ストア + 本スクリプトが作る ca_cert
#   公的 CA: 中間CAのみの ca_cert は検証を壊すためテスト2は実施しない
#   自己署名: leaf（=root） / 私有 CA: 中間CA + root（leaf 除外、重複除去）
if kind == "public":
    ok2, err2 = None, None
    kept = []
    print("テスト2（ca_cert 固定）: 公的 CA は対象外（ストア更新が正しい対処）")
else:
    seen, kept = set(), []
    for i, (b, info) in enumerate(zip(blocks, infos)):
        fp = info["fp"]
        if fp in seen:
            continue
        seen.add(fp)
        if i == 0 and not selfsigned:
            continue
        kept.append("-----BEGIN CERTIFICATE-----\n%s\n-----END CERTIFICATE-----\n" % b)
    keptfile = None
    if kept:
        fd, keptfile = tempfile.mkstemp(suffix=".pem"); os.close(fd)
        with open(keptfile, "w") as f:
            f.write("".join(kept))
    ok2, err2 = attempt(keptfile)
    if keptfile:
        os.unlink(keptfile)
    print("テスト2（ca_cert 固定、" + str(len(kept)) + " blocks）: " + ("成功" if ok2 else "失敗 (" + str(err2) + ")"))

# 書出
if out:
    out_abs = out if out.startswith("/") else os.path.abspath(out)
    if kind == "public":
        if ok1:
            print("書出: スキップ（公的 CA かつストア検証成功。ca_cert は不要です）")
            if os.path.exists(out_abs):
                print("注意: 既存の " + out_abs + " は leaf を含む旧ファイルの可能性が高く、ca_cert として"
                "残すと検証を壊します。padoka.yml の ca_cert を外すか、ファイルを削除してください。")
        else:
            print("書出: スキップ（公的 CA。中間CAのみの ca_cert は検証を壊すため）")
            print("      対処: dnf update ca-certificates && update-ca-trust (root)")
    else:
        os.makedirs(os.path.dirname(out_abs), exist_ok=True)
        with open(out_abs, "w") as f:
            f.write("".join(kept))
        print("書出先: " + out_abs + " (" + str(len(kept)) + " blocks, leaf 除外済)")
        if not ok2:
            print("注意: テスト2 が失敗しているため、このファイルだけでは"
                  "検証を通りません（root が不足）。CA 側の root 証明書を入手し"
                  "ファイルに追加してください。")

print()
if ok1:
    print(">> 結論: システムストアのままで接続できます。ca_cert は不要です。")
elif kind == "public":
    print(">> 結論: 公的 CA 発行。システム CA ストアが古い（root 不足）と見られる:")
    print("     dnf update ca-certificates && update-ca-trust   (root 権限)")
    print("     更新後に再実行し、テスト1が成功することを確認。")
    if out and os.path.exists(out):
        print("     また、以前に取得した ca_cert ファイル（leaf を含むもの）が")
        print("     残っていると検証を壊すため、padoka.yml の ca_cert 行を")
        print("     コメントアウトするかファイルを削除してください。")
elif kind == "selfsigned":
    print(">> 結論: 自己署名。テスト2 成功なら ca_cert を padoka.yml に設定してください。")
    if out:
        print("     ca_cert: " + out)
else:
    print(">> 結論: 私有 CA。root 含む完全チェーン（中間+root）を ca_cert に設定してください。")
    if ok2 and out:
        print("     ca_cert: " + out)
    else:
        print("     現在の取得チェーンでは検証不能。CA 側の root 証明書を入手。")
PYEOF
    rc=$?
    rm -f "$pemfile"
    exit $rc
else
    if [ -n "$out" ]; then
        case "$out" in
            /*) : ;;
            *) out="$PWD/$out" ;;
        esac
        mkdir -p "$(dirname "$out")"
        printf '%s\n' "$pem" > "$out"
        echo "警告: python3 が見つからないため診断を実行できません。"
        echo "警告: $out は leaf を含む生 PEM です。ca_cert 利用前に leaf を除去してください。"
        printf '%s\n' "$pem" | openssl x509 -noout -subject -issuer -dates 2>/dev/null
        printf '%s\n' "$pem" | openssl x509 -noout -fingerprint -sha256 2>/dev/null
    else
        echo "警告: python3 が見つからないため診断を実行できません。"
        printf '%s\n' "$pem" | openssl x509 -noout -subject -issuer -dates
        printf '%s\n' "$pem" | openssl x509 -noout -fingerprint -sha256
    fi
fi

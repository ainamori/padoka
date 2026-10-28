#!/bin/sh
# 上流 IRC サーバーの TLS 証明書を取得・診断し、必要なら padoka の
# 信頼アンカー (ssl_params.ca_cert) として固定する。
#
# 用法:   scripts/fetch-upstream-cert.sh <host> <port> [out.pem]
# 例:     scripts/fetch-upstream-cert.sh dh.ircnet.ne.jp 6697              # 診断のみ
#         scripts/fetch-upstream-cert.sh dh.ircnet.ne.jp 6697 config/certs/ircnet-ca.pem
#
# 判定ロジック（padoka と同じ条件: SNI + ホスト名検証）:
#   テスト1: システム CA ストアのみ
#   テスト2: システムストア + このスクリプトが作る ca_cert
#   書出・ピンニングルール（共通）:
#     * ca_cert には leaf を含めない（信頼ストアに leaf が入ると
#       OpenSSL が検証を壊す = "unable to get local issuer certificate"）
#     * 自己署名      -> leaf（= root）を固定
#     * 公的 CA (LE) -> 中間 CA 群 + 取得チェーンに root が無ければ
#                       ISRG Root（letsencrypt.org から自動取得）を添えて固定
#     * 私有 CA      -> 中間 CA + root（leaf 除外、重複除去）
#
# よくあるケース（ircnet.ne.jp = Let's Encrypt 発行）:
#   テスト1 が失敗して `dnf update ca-certificates` でも直らない
#   （"Nothing to do"）場合 -> 本スクリプトに out.pem を指定してピンニング
#   するか、root 権限で ISRG Root X2 を信頼アンカーに追加:
#     curl -fsSL https://letsencrypt.org/certs/isrg-root-x2.pem \
#        -o /etc/pki/ca-trust/source/anchors/isrg-root-x2.pem
#     update-ca-trust
#
# 手順:
#   1. サーバーが到達できるマシンで実行（まず out.pem なしで診断）
#   2. 表示された issuer / 指紋 / 判定に従って対処
#   3. 対処後に再実行し、テスト1（またはピンニング時のテスト2）成功を確認
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
    # ISRG Root (LE) の事前取得（out.pem 指定時のみ。timeout で
    # DNS/接続ハングを強制打ち切り。取得できなくても python 側で
    # 中間 CA 単体の pin を試みる）。
    leroot_file=$(mktemp) || exit 1
    if [ -n "$out" ] && command -v curl >/dev/null 2>&1; then
        # 注: SSL_CERT_FILE/SSL_CERT_DIR が異常な値（空ファイル等）だと
        # curl 自身の TLS 検証まで壊れるため、取得時には除去する
        env -u SSL_CERT_FILE -u SSL_CERT_DIR \
            timeout 15 curl -fsSL "https://letsencrypt.org/certs/isrg-root-x2.pem" \
            >> "$leroot_file" 2>/dev/null || true
        env -u SSL_CERT_FILE -u SSL_CERT_DIR \
            timeout 15 curl -fsSL "https://letsencrypt.org/certs/isrg-root-x1.pem" \
            >> "$leroot_file" 2>/dev/null || true
    fi
    python3 - "$host" "$port" "${out:-}" "$method" "$pemfile" "$leroot_file" <<'PYEOF'
import base64, hashlib, os, re, socket, ssl, sys, tempfile
host, port, out, method, pemfile, leroot_file = (sys.argv[1], int(sys.argv[2]),
                                                  sys.argv[3], sys.argv[4],
                                                  sys.argv[5], sys.argv[6])
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
# 注意: 自己署名判定を最優先（issuer == subject）。issuer 名での公的 CA
# 判定は leaf が本当に他 CA 発行の場合のみ有効。
if selfsigned:
    kind = "selfsigned"
elif leaf_issuer and any(k in leaf_issuer for k in PUBLIC_CA_KEYS):
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
        return False, "接続不可: " + str(e)

ok1, err1 = attempt(None)
print("テスト1（システム CA ストアのみ）: " + ("成功" if ok1 else "失敗 (" + str(err1) + ")"))

if ok1:
    if out and os.path.exists(out):
        print("注意: 既存の " + out + " が leaf を含む旧ファイルだと ca_cert として")
        print("      検証を壊します。padoka.yml の ca_cert を外し、ファイルは削除を。")
    print()
    print(">> 結論: システムストアのままで接続できます。ca_cert は不要です。")
    sys.exit(0)

# --- ピンニング候補: leaf を除き、重複除去 --------------------------------
kept_idx = []
seen = set()
for i, info in enumerate(infos):
    if info["fp"] in seen:
        continue
    seen.add(info["fp"])
    if i == 0 and not selfsigned:
        continue
    kept_idx.append(i)
kept_pem = "".join("-----BEGIN CERTIFICATE-----\n%s\n-----END CERTIFICATE-----\n"
                   % blocks[i] for i in kept_idx)
has_root = any(infos[i].get("selfsigned") for i in kept_idx)


if out:
    # 取得チェーンに root が無ければ（LE サーバーは root を送らない）
    # シェルが取得した ISRG Root を添える（取得失敗でも中間 CA のみ pin で
    # 検証可能なケースが多い: 信頼ストアの中間 CA がチェーン終端となる）
    extra_pem = ""
    if not has_root and kind == "public":
        try:
            data = open(leroot_file).read()
        except OSError:
            data = ""
        if "BEGIN CERTIFICATE" in data:
            extra_pem = data
            print("(ISRG Root X2/X1 を添えました)")
        else:
            print("(ISRG Root の取得に失敗: 中間 CA のみで pin を試みます)")
    out_abs = out if out.startswith("/") else os.path.abspath(out)
    os.makedirs(os.path.dirname(out_abs), exist_ok=True)
    pin = kept_pem + extra_pem
    with open(out_abs, "w") as f:
        f.write(pin)
    print("書出先: " + out_abs + " (" + str(len(kept_idx)) + " blocks, leaf 除外済"
          + (", root 追加済" if extra_pem else "") + ")")
    ok2, err2 = attempt(out_abs)
    print("テスト2（ストア + ca_cert 固定）: " + ("成功" if ok2 else "失敗 (" + str(err2) + ")"))
    print()
    if ok2:
        print(">> 結論: ピンニング有効。padoka.yml の servers[].ssl_params に:")
        print(">>       ca_cert: " + out_abs)
        sys.exit(0)
    # ピンでも通らなかった場合
    if kind == "public":
        print(">> 結論: ストアもピンニングも失敗。ISRG Root 取得に失敗した可能性。")
        print(">>       手動で: curl -fsSL https://letsencrypt.org/certs/isrg-root-x2.pem")
        print(">>       を ca_cert ファイルに追加し、再検証。")
        print(">>       代替（root 権限・システム全体修正）:")
        print(">>       curl -fsSL https://letsencrypt.org/certs/isrg-root-x2.pem \\")
        print(">>         -o /etc/pki/ca-trust/source/anchors/isrg-root-x2.pem")
        print(">>       update-ca-trust")
    else:
        print(">> 結論: 私有 CA。root 証明書を含む完全チェーン（中間+root）が")
        print(">>       必要です。CA 管理者から root を入手してファイルに追加。")
    sys.exit(1)

# out.pem 未指定: 診断のみ
print()
if kind == "public":
    print(">> 結論: 公的 CA (Let's Encrypt 等) 発行。システムストアで検証失敗 =")
    print(">>       ストアに ISRG Root が無い、または Python がシステムストアを")
    print(">>       参照していない。対処（いずれか）:")
    print(">>       a) root 権限で X2 をアンカーに追加:")
    print(">>          curl -fsSL https://letsencrypt.org/certs/isrg-root-x2.pem \\")
    print(">>            -o /etc/pki/ca-trust/source/anchors/isrg-root-x2.pem")
    print(">>          update-ca-trust")
    print(">>       b) ピンニング（ストア不問）: 本スクリプトに out.pem を指定して再実行:")
    print(">>          scripts/fetch-upstream-cert.sh " + host + " " + str(port) + " config/certs/ircnet-ca.pem")
    print(">>       c) どのストアを使っているか確認:")
    print(">>          python3 -c \"import ssl; print(ssl.create_default_context().cert_store_stats())\"")
    print(">>          （ca_file が /etc/pki/tls/certs/ca-bundle.crt 以外なら、")
    print(">>           その Python ビルドの CA 参照先が別ということです）")
elif kind == "selfsigned":
    print(">> 結論: 自己署名。out.pem を指定して再実行し、書出されたファイルで")
    print(">>       ca_cert: <out.pem> を設定してください。")
else:
    print(">> 結論: 私有 CA。out.pem を指定して再実行（中間+root を書出）。")
    print(">>       取得チェーンに root が無い場合は CA 管理者から入手。")
PYEOF
    rc=$?
    rm -f "$pemfile" "$leroot_file"
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

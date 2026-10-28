#!/bin/sh
# 上流 IRC サーバーの TLS 証明書を取得し、padoka の信頼アンカー
# (ssl_params.ca_cert) として固定する。
#
# 用法:   scripts/fetch-upstream-cert.sh <host> <port> <out.pem>
# 例:     scripts/fetch-upstream-cert.sh dh.ircnet.ne.jp 6697 config/certs/ircnet-ca.pem
#
# 背景: ircnet.ne.jp 等は公的 CA 発行ではなく自己署名（または私有 CA）
# 証明書を使うため、システム CA ストアでは
#   SSLCertVerificationError: unable to get local issuer certificate
# となり接続できない。このスクリプトはサーバーが実際に提示している
# 証明書（openssl 版はチェーン全体）をファイルに固定し、padoka が
# 「その証明書だけが信頼できる」状態にする。 ホスト名検証は維持されるため
# MITM 対策を捨てる verify: false より安全。
#
# 取得方法（順に試行）:
#   1. openssl s_client -showcerts （チェーン全体）
#   2. python3 ssl モジュール     （leaf のみ。openssl 未インストール環境用）
# 失敗時は openssl/python の生出力（末尾 25 行）を表示して原因を特定する。
#
# 手順:
#   1. サーバーが到達できるマシン（実運用インスタンス等）で実行
#   2. 表示された subject / issuer / 有効期間 / SHA256 指紋を確認
#      （subject == issuer なら自己署名。指紋が想定と一致すること）
#   3. 書き出された PEM を config/certs/ircnet-ca.pem として配置
#   4. config/padoka.yml の servers[].ssl_params.ca_cert をそれに向ける
#      （サンプル設定は既に如此）
#
# 証明書がローテーションされた場合は再実行し、指紋が変わったことを
# 確認のうえ差し替えてください。

set -u

if [ "$#" -ne 3 ]; then
    echo "usage: $0 <host> <port> <out.pem>" >&2
    exit 2
fi
host=$1
port=$2
out=$3

pem=""
raw=""
pyraw=""

# --- 1) openssl s_client（チェーン全体） -----------------------------------
if command -v openssl >/dev/null 2>&1; then
    raw=$(openssl s_client -showcerts -connect "${host}:${port}" \
            -servername "${host}" </dev/null 2>&1) || true
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

# --- 3) 失敗時は生出力で原因を提示 ------------------------------------------
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

case "$out" in
    /*) : ;;
    *) out="$PWD/$out" ;;
esac
mkdir -p "$(dirname "$out")"
printf '%s\n' "$pem" > "$out"
echo "== 提示証明書: ${host}:${port} (取得方法: $method) =="
echo "証明書ブロック数: $(printf '%s\n' "$pem" | grep -c 'BEGIN CERTIFICATE')"
echo "書き出しました: $out"
echo

# --- 4) subject/issuer 表示 + 指紋 + 検証テスト ---------------------------
# 検証テストは padoka と同じ条件（システムストア + ca_cert、ホスト名検証
# 有効、SNI 送信）で接続を試みる。leaf のみ取得で私有 CA 発行の証明書を
# ピンした場合は「検証テスト: 失敗」で止まるので、そのまま padoka に
# 渡す前にここで分かる。
if command -v python3 >/dev/null 2>&1; then
    python3 - "$host" "$port" "$out" <<'PYEOF'
import base64, hashlib, os, re, socket, ssl, sys, tempfile
host, port, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
pem = open(out).read()
blocks = re.findall(r"-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----",
                    pem, re.S)
for i, b in enumerate(blocks):
    der = base64.decodebytes(b.encode())
    print("cert[%d] sha256=%s" % (i, ":".join("%02X" % x
          for x in hashlib.sha256(der).digest())))
# subject / issuer（CPython 内部 API。使えない環境では指紋のみ）
selfsigned = None
try:
    td = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
    td.write(pem); td.close()
    try:
        info = ssl._ssl._test_decode_cert(td.name)
    finally:
        os.unlink(td.name)
    def fmt(t):
        return ", ".join("%s=%s" % (k, v) for pair in t for k, v in pair)
    subj, iss = fmt(info["subject"]), fmt(info["issuer"])
    print("subject: " + subj)
    print("issuer:  " + iss)
    print("notAfter: " + str(info.get("notAfter")))
    selfsigned = (subj == iss)
    print(">> " + ("自己署名（leaf 単体で固定可）" if selfsigned
                    else "** 私有 CA 発行 **（leaf 単体では固定不可: CA も必要）"))
except Exception as e:
    print("(subject/issuer 非表示: %r)" % e)
# padoka と同条件で検証テスト
ctx = ssl.create_default_context(cafile=out)
try:
    s = ctx.wrap_socket(socket.create_connection((host, port), timeout=30),
                        server_hostname=host)
    s.close()
    print("検証テスト: 成功 -> この PEM だけで padoka は接続できます")
except ssl.SSLCertVerificationError as e:
    r = str(e.verify_message)
    if "local issuer" in r:
        print("検証テスト: 失敗 (unable to get local issuer certificate)")
        if selfsigned is True:
            print("  -> 自己署名なのに検証失敗。PEM の破損・ホスト不一致を確認。")
        else:
            print("  -> CA 証明書が必要です（leaf 単体では検証できない）:")
            print("     a) openssl CLI をインストールして本スクリプトを再実行"
                  "（-showcerts でチェーン全体を取得）")
            print("     b) openssl がある別マシンで取得し、この PEM を差し替え")
            print("     c) 暫定策として verify: false（MITM 耐性を捨てるため非推奨）")
    elif "hostname" in r.lower() or "match" in r.lower():
        print("検証テスト: 失敗 (ホスト名不一致)。ssl_params.check_hostname: false を検討。")
    else:
        print("検証テスト: 失敗 (%s)" % r)
except OSError as e:
    print("検証テスト: 接続不可 (%s)" % e)
PYEOF
else
    printf '%s\n' "$pem" | openssl x509 -noout -subject -issuer -dates
    printf '%s\n' "$pem" | openssl x509 -noout -fingerprint -sha256
fi
echo
[ "$(printf '%s\n' "$pem" | grep -c 'BEGIN CERTIFICATE')" -gt 1 ] && \
    echo "注: チェーン複数取得。padoka の ca_cert は leaf+CA 全体で良い。"

if command -v python3 >/dev/null 2>&1; then
    echo "接続を確認:  padoka -c config/padoka.yml --debug"
else
    echo "警告: python3 が見つからないため検証テスト未実行。手動で接続を確認してください。"
fi

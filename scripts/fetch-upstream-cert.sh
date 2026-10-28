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

echo "== 提示証明書: ${host}:${port} (取得方法: $method) =="
# チェーン全体（leaf + 中間 CA）を出力する
n=$(printf '%s\n' "$pem" | grep -c 'BEGIN CERTIFICATE')
echo "証明書ブロック数: $n"
if command -v openssl >/dev/null 2>&1; then
    printf '%s\n' "$pem" | openssl x509 -noout -subject -issuer -dates
    printf '%s\n' "$pem" | openssl x509 -noout -fingerprint -sha256
else
    python3 - "$pem" <<'PYEOF'
import base64, hashlib, re, sys
pem = sys.argv[1]
blocks = re.findall(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----",
                    pem, re.S)
for i, b in enumerate(blocks):
    der = base64.decodebytes(b.encode())
    print("cert[%d] sha256 fingerprint=%s"
          % (i, ":".join("%02X" % x for x in hashlib.sha256(der).digest())))
print("注: openssl 未インストールのため subject/issuer は非表示。指紋で確認してください。")
PYEOF
fi
[ "$method" = "python3 (leaf のみ)" ] && \
    echo "注: openssl 未インストールのため leaf のみ取得。チェーン複数（私有 CA）の場合は openssl 版で取得を。"
echo
echo ">> subject == issuer なら自己署名です。指紋を確認のうえ固定してください。"
echo

case "$out" in
    /*) : ;;
    *) out="$PWD/$out" ;;
esac
mkdir -p "$(dirname "$out")"
printf '%s\n' "$pem" > "$out"
echo "書き出しました: $out"
echo "次の検証で接続成功するはず:  padoka -c config/padoka.yml --debug"

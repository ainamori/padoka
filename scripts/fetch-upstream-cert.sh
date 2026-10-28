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
# 証明書（チェーン全体を含む）をファイルに固定し、padoka が
# 「その証明書だけが信頼できる」状態にする。 ホスト名検証は維持されるため
# MITM 対策を捨てる verify: false より安全。
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

set -eu

if [ "$#" -ne 3 ]; then
    echo "usage: $0 <host> <port> <out.pem>" >&2
    exit 2
fi
host=$1
port=$2
out=$3

pem="$(openssl s_client -connect "${host}:${port}" -servername "${host}" </dev/null 2>/dev/null \
    | sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p')"

if [ -z "$pem" ]; then
    echo "ERROR: ${host}:${port} から証明書を取得できません（TLS 到達不能?）。" >&2
    exit 1
fi

echo "== 提示証明書: ${host}:${port} =="
printf '%s\n' "$pem" | openssl x509 -noout -subject -issuer -dates
printf '%s\n' "$pem" | openssl x509 -noout -fingerprint -sha256
# チェーン全体（leaf + 中間 CA）を出力する
n=$(printf '%s\n' "$pem" | grep -c 'BEGIN CERTIFICATE')
echo "証明書ブロック数: $n"
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

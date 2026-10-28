# Docker 利用ガイド

## ビルド

```bash
docker build -t padoka:py .
```

## docker compose で起動（推奨）

### bridge ネットワーク（デフォルト）

```bash
docker compose up -d
```

- `PADOKA_CLIENT_PORT` 環境変数で公開ポートを変更可能（デフォルト 6667）:
  ```bash
  PADOKA_CLIENT_PORT=16667 docker compose up -d
  ```
- ログは `padoka-log` ボリュームに保存
- 設定ファイルのオーバーライド:
  ```bash
  PADOKA_CONFIG_HOST=/path/to/my.yml docker compose up -d
  ```

### host ネットワーク（Linux）

```bash
docker compose -f docker-compose.host.yml up -d
```

- ポート公開なし、ホストのポートに直接バインド
- `client_server.port`（デフォルト 6667）がホストで空いていれば接続可能

## 確認

```bash
# ログ確認
docker compose logs -f padoka

# 内部で動作確認
docker compose exec padoka padoka --version

# 停止
docker compose down
```

## 設定の永続化

`config/padoka.yml` はイメージに焼き込まれています。
ホスト側で編集したい場合は compose の volume マウントを変更してください:

```yaml
volumes:
  - /path/to/your/padoka.yml:/app/config/padoka.yml:ro
```

## ポート競合について

| 方式 | ポート | 利点 | 注意点 |
|------|--------|------|--------|
| bridge (compose.yml) | ホスト:コンテナ (例 6667:6667) | 安全、任意ポートにマッピング | NAT 経由 |
| host (compose.host.yml) | ホストのポート直接使用 | 高速、単純 | ポート競合に注意 |

IRC クライアント側（irssi 等）は `PADOKA_CLIENT_PORT` で指定した
ホストポートに向けて接続します。

## TLS / SASL

クライアントポート（デフォルト 6667）は **TLS のみ** で動きます。
イメージビルド時に自己署名証明書が `config/certs/padoka.crt` /
`padoka.key` として生成されます（`config/certs/` は gitignore 済み）。
SASL PLAIN 認証も有効で、パスワードは `client_server.pass` です。

irssi での接続例:

```bash
irssi
/SETTINGS SET -re save.sasl.mechanism PLAIN
/SETTINGS SET -re save.sasl.username myuser
/SETTINGS SET -re save.sasl.password PadokaPassWord
/SAVE
/conn -server 127.0.0.1 -port 6667 -ssl -sasl
```

自己署名のため SSL 検証エラーが出ます。指紋を登録するか、開発時のみ
`-ssl_verify=off` を使ってください。**公開ホストで運用する場合は
`ssl_cert_file` / `ssl_key_file` を CA 発行証明書（Let's Encrypt 等）へ
差し替えてください。**

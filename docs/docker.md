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
- **WebUI ポート**: 8080 を公開（`PADOKA_WEBUI_PORT` で変更可能）。
  利用するには `config/padoka.yml` の `webui.enabled: true`。
  `http://<ホスト>:8080/` でアクセス。

### host ネットワーク（Linux）

```bash
docker compose -f docker-compose.host.yml up -d
```

- ポート公開なし、ホストのポートに直接バインド
- `client_server.port`（デフォルト 6667）がホストで空いていれば接続可能
- WebUI は 8080 に直接バインドされる（`webui.enabled: true` の場合）

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

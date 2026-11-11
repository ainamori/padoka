# Quick Start

Padoka を**ゼロから起動して、IRC クライアントから接続する**までの最短手順です。
10 分で「IRC に 1 つの nick で常駐し、irssi 等の複数クライアントから使える
プロキシ」が手に入ります。

## 手に入るとは

```
        ┌──────────── Padoka (1 つの nick) ────────────┐
上流    │                                                │
libera ─┤   接続・ Away ・再接続 ・ ログ ・ ボット      ├─ 下流
.chat   │                                                │   6667
        └────────────────────────────────────────────────┘
                        ▲  ▲  ▲
                        │  │  └─ weechat
                        │  └───── irssi
                        └──────── irssi (別マシン)
```

- IRC サーバーに **1 つの nick** として常駐
- **同じ nick を複数の IRC クライアントで共有**（接続切替・複数端末）
- クライアントが离线中でもボットが動き続ける
- ログ・Away・再接続をプロキシが管理

---

## 前提

| 方法 | 必要ツール |
|------|-----------|
| **A. Docker（推奨）** | Docker + docker compose |
| **B. 直接実行** | Python 3.8+（推奨 3.11 / 3.12）, pip |

接続先 IRC（libera.chat 等）への通信ができる環境であれば OK です。

---

## 方法 A: Docker で起動（推奨）

```bash
# 1. イメージをビルド
docker build -t padoka:py .

# 2. 起動（クライアントポート 6667 をホスト公開）
docker compose up -d

# 3. 起動ログを確認
docker compose logs -f padoka
```

次の 3 行が現れれば起動成功です:

```
[PDK] load config: /app/config/padoka.yml
[PDK] Server connection to irc.libera.chat:6697.
[PDK] Open Client Server Port: 0.0.0.0 6667
```

> **ポート競合の場合**: ホストの 6667 が使用済みなら別ポートに:
> `PADOKA_CLIENT_PORT=16667 docker compose up -d`
> 以降の irssi の `-port` も `16667` に合わせてください。

### irssi で接続する

```bash
irssi
/conn -server 127.0.0.1 -port 6667
/PASS PadokaPassWord      <- config/padoka.yml の client_server.pass
/NICK myclient            <- 任意のニックネーム
/USER myclient 0 * :myclient
/#yourchannel             <- channel_info で startup にしたチャネル
```

`/msg padoka_py status`（ボットの nick に合わせる）やチャネル内のやり取りで
動作していることを確認できます。

### 停止

```bash
docker compose down
```

---

## 方法 B: Python で直接実行

```bash
# 1. 依存とパッケージのインストール
pip install -e .

# 2. 起動（--debug はログレベルを上げます）
padoka -c config/padoka.yml --debug
#   または: python -m padoka -c config/padoka.yml --debug
```

起動したら、別ターミナルで irssi から接続します（方法 A と同じ）:

```bash
irssi
/conn -server 127.0.0.1 -port 6667
/PASS PadokaPassWord
/NICK myclient
/USER myclient 0 * :myclient
/#yourchannel
```

---

## まず確認すること

1. **設定の最小項目**（`config/padoka.yml`）

   | 項目 | 変更する理由 |
   |------|-------------|
   | `servers` | 接続先 IRC を自分の環境に合わせる |
   | `nick` | 使う nick（衝突すると自動で `0` 付加して再試行） |
   | `client_server.pass` | **必ずデフォルト `PadokaPassWord` から変更** |
   | `client_server.acl` | **接続元を制限**（`deny all` + 個別 `allow`） |
   | `channel_info` | 常駐するチャネル |

2. **ログ**: Docker は `docker compose logs -f padoka`、直接実行は
   `log/` 配下のファイル（`mypadoka-system.log` 等）。

3. **IRC 経由の制御**: クライアントから
   `/PADOKA STATUS`（状態表示）・`/PADOKA RELOAD`（設定リロード）を試せます。

---

## ボットを 1 つ有効にする（任意）

`config/padoka.yml` の `bots:` に追加し、`/PADOKA RELOAD`（または再起動）:

```yaml
bots:
  - name: backlogbot          # クライアント再接続時に履歴を再送
    talker: false
    prefix: "BL"
    sender: "backlogbot"
  - name: xibot               # サイコロ: チャネルで "xi> 3d6"
    ch: ".*"
```

利用可能な 26 ボットの一覧は
[README 「利用可能なボットプラグイン」](../README.md) や
[architecture.md](architecture.md#ボットプラグインplugins) を参照してください。

---

## WebUI でブラウザから見る・発言する（任意）

IRC クライアントがなくても、ブラウザからログ閲覧・発言できます
（標準ライブラリのみ・依存なし）。`config/padoka.yml` に:

```yaml
webui:
  enabled: true
  host: 0.0.0.0            # ローカルのみなら 127.0.0.1
  port: 8080
  pass: "my-secret-token"  # 公開する場合は必ず設定（認証トークン）
```

再起動して `http://<host>:8080/` を開く。チャネル一覧・ライブ表示
（自動更新）・発言・join/part をブラウザから操作できます。
Docker compose では 8080 が公開されるので `http://<ホスト>:8080/`。
詳細は [webui.md](webui.md) を参照。

---

## 次へ

| 目的 | 参照 |
|------|------|
| 設定の全項目 | [configuration.md](configuration.md) |
| **WebUI（ブラウザ閲覧・発言）** | [webui.md](webui.md) |
| 実 IRC 環境での本番デプロイ | [deploy-checklist.md](deploy-checklist.md) |
| ボットの自作 | [bot-development.md](bot-development.md) |
| 内部構造（スレッド/メッセージ流れ） | [architecture.md](architecture.md) |
| Docker の詳細 | [docker.md](docker.md) |

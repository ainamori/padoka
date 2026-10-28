# Quick Start

Padoka を**ゼロから起動して、IRC クライアントから接続する**までの最短手順です。
10 分で「IRC に 1 つの nick で常駐し、irssi 等の複数クライアントから使える
プロキシ」が手に入ります。

## 手に入るとは

```
        ┌──────────── Padoka (1 つの nick) ────────────┐
上流    │                                                │
ircnet ─┤   接続・ Away ・再接続 ・ ログ ・ ボット      ├─ 下流
.ne.jp  │                                                │   6667
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

接続先 IRC（ircnet.ne.jp 等）への通信ができる環境であれば OK です。

> 注意: 上流 ircnet への TLS(6697) 接続が可能な環境であること。
> 開発環境等で IRC ポートが外出りブロックされている場合は接続できません。

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
[NDK] load config: /app/config/padoka.yml
[NDK] Server connection to irc.ircnet.ne.jp:6697.
[NDK] Open Client Server Port: 0.0.0.0 6667
```

> **ポート競合の場合**: ホストの 6667 が使用済みなら別ポートに:
> `PADOKA_CLIENT_PORT=16667 docker compose up -d`
> 以降の irssi の `-port` も `16667` に合わせてください。

### irssi で接続する（TLS + SASL）

クライアントポートは **TLS のみ** + **SASL PLAIN 認証**（パスワードは
`client_server.pass`）で動きます。

```bash
irssi
/SETTINGS SET -re save.sasl.mechanism PLAIN
/SETTINGS SET -re save.sasl.username myuser
/SETTINGS SET -re save.sasl.password PadokaPassWord   <- client_server.pass
/SAVE
/conn -server 127.0.0.1 -port 6667 -ssl -sasl
/#yourchannel             <- channel_info で startup にしたチャネル
```

> **自己署名証明書のため**: 接続時に SSL 検証エラーが出たら、
> `ssl_verify` を OFF にするか（`/conn ... -ssl_verify=off`）、
> 証明書の指紋を irssi に登録してください（本番は CA 発行証明書へ）。

`/msg padoka_py status`（ボットの nick に合わせる）やチャネル内のやり取りで
動作していることを確認できます。

TLS/SASL の詳細・weechat の設定例は [configuration.md](configuration.md#tls) を参照。

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

起動したら、別ターミナルで irssi から接続します（方法 A と同じ、
TLS + SASL）:

```bash
irssi
/SETTINGS SET -re save.sasl.mechanism PLAIN
/SETTINGS SET -re save.sasl.username myuser
/SETTINGS SET -re save.sasl.password PadokaPassWord
/SAVE
/conn -server 127.0.0.1 -port 6667 -ssl -sasl
/#yourchannel
```

> 自己署名のサンプル証明書が `config/certs/` にリポジトリ同梱されているため、
> 追加の証明書作成は不要です（本番は CA 発行へ差し替え）。

---

## まず確認すること

1. **設定の最小項目**（`config/padoka.yml`）

   | 項目 | 変更する理由 |
   |------|-------------|
   | `servers` | 接続先 IRC を自分の環境に合わせる |
   | `nick` | 使う nick（衝突すると自動で `0` 付加して再試行） |
   | `client_server.pass` | **必ずデフォルト `PadokaPassWord` から変更**（/PASS と SASL PLAIN の両方に使用） |
   | `client_server.acl` | **接続元を制限**（`deny all` + 個別 `allow`） |
   | `client_server.ssl_cert_file` / `ssl_key_file` | 公開ホストで使う場合は **CA 発行証明書へ差し替え**（Docker イメージ内は自己署名） |
   | `servers[].sasl` | **海外から ircnet に接続する場合必須**。NickServ アカウントの `username` / `password` を設定（[configuration.md「上流 SASL」](configuration.md#上流-saslircv3-plain) 参照） |
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

## 次へ

| 目的 | 参照 |
|------|------|
| 設定の全項目 | [configuration.md](configuration.md) |
| **上流 SASL（NickServ / 海外接続）** | [configuration.md「上流 SASL」](configuration.md#上流-saslircv3-plain) |
| 実 IRC 環境での本番デプロイ | [deploy-checklist.md](deploy-checklist.md) |
| ボットの自作 | [bot-development.md](bot-development.md) |
| 内部構造（スレッド/メッセージ流れ） | [architecture.md](architecture.md) |
| Docker の詳細 | [docker.md](docker.md) |

# 実 IRC 環境でのデプロイ / 検証チェックリスト

本ドキュメントは、開発環境（フェイク IRC サーバーでの統合テスト）とは別に、
**実際の IRC ネットワーク**（ircnet.ne.jp 等）に対して Padoka をデプロイし、
動作を検証する手順です。

> 注: Docker ビルドに PyPI アクセスが必要な場合は `vendor/` に入れた wheel を
> 使う構成になっているため、ビルド自体はオフラインで完了します。
> 実行時（IRC 接続）は当然ながら外部ネットワークが必要です。

## 1. 事前確認

```bash
# 1) Python / Docker のバージョン
python3 --version        # 3.8 以上（推奨 3.11 / 3.12）
docker --version

# 2) 対象ポートが空きポートか確認（ホスト側）
ss -tlnp | grep 6667     # 使用済みなら PADOKA_CLIENT_PORT を変える
```

## 2. 設定の調整（`config/padoka.yml`）

最低限、以下を決めてください。

| 項目 | 説明 |
|------|------|
| `servers` | 接続先 IRC サーバー（host / port / ssl_params） |
| `nick` | IRC 上で使う nick（衝突すると自動で `0` が付加されて再試行） |
| `client_server.pass` | IRC クライアント側の接続パスワード |
| `client_server.acl` | 接続元を制限（`deny all` + 個別 `allow` を推奨） |
| `channel_info` | 常駐するチャネル |
| `bots` | 有効にするボット |

### 接続先の例（ircnet.ne.jp / TLS）

```yaml
servers:
  - host: irc.ircnet.ne.jp
    port: 6697
    pass: null
    ssl_params:
      verify: true
  - host: dh.ircnet.ne.jp
    port: 6697
    pass: null
    ssl_params:
      verify: true
```

## 3. 起動

```bash
# ビルド
docker build -t padoka:py .

# 起動（bridge、ポート 6667 をホスト公開）
PADOKA_CLIENT_PORT=6667 docker compose up -d

# 起動確認
docker compose ps                      # STATUS が Up (healthy) になることを確認
docker compose logs -f padoka          # ログを追う
```

ログで以下が出れば正常起動しています:

```
[NDK] load config: /app/config/padoka.yml
[NDK] Server connection to irc.ircnet.ne.jp:6697.
[NDK] Open Client Server Port: 0.0.0.0 6667
```

## 4. IRC クライアントでの接続確認

別ターミナル（または別マシン）から irssi で接続します。
クライアントポートは **TLS のみ**（自己署名証明書）+ **SASL PLAIN**
（パスワードは `client_server.pass`）です。

```
/SETTINGS SET -re save.sasl.mechanism PLAIN
/SETTINGS SET -re save.sasl.username myuser
/SETTINGS SET -re save.sasl.password PadokaPassWord   <- client_server.pass
/SAVE
/conn -server 127.0.0.1 -port 6667 -ssl -sasl
```

自己署名のため SSL 検証エラーが出る場合は、指紋を登録するか
`-ssl_verify=off`（検証 OFF）で接続し、**本番では CA 発行証明書へ差し替え**て
ください。

接続後に:

```
/#yourchannel            <- channel_info で startup にしたチャネル
```

- 353 (NAMES) / 332 (TOPIC) が返ってきてチャネルのメンバーが見えること
- メッセージが往復すること
- `/#yourchannel` で `!hi` 等を送り、ボットが反応すること（有効なボットによる）

### weechat の例

```
/irc add -srv 127.0.0.1 -port 6667 -nick myclient -ssl -sasl-mechanism plain -sasl-username myuser -sasl-password PadokaPassWord mynet
/irc connect mynet
```

> 自己署名のため weechat でも証明書検証エラーが出ます。
> `irc.network.<net>.ssl_fingerprint` へ指紋を登録してください。

## 5. 制御コマンドの確認

IRC クライアントで以下を試します（`/PADOKA <cmd>`）:

```
/PADOKA STATUS      <- 実行状態表示
/PADOKA RELOAD     <- 設定リロード（config/padoka.yml を編集してから）
/PADOKA RECONNECT  <- 上流サーバーに再接続
```

シグナルでも操作できます（コンテナ内で `docker compose exec` は `padoka`
ユーザーなので、PID 1 への信号は `docker kill -s HUP padoka` 等を使用）:

```bash
docker kill -s HUP padoka      # 設定リロード
docker kill -s USR1 padoka     # on_sigusr1
docker kill -s USR2 padoka     # on_sigusr2
docker stop padoka             # 正常終了 (SIGTERM)
```

## 6. ボットの動作確認（有効にしたもの）

| ボット | 確認方法 |
|--------|---------|
| `backlogbot` | クライアント再接続時に直近のメッセージが再送される |
| `autoawaybot` | 15分操作なしで AWAY、操作で復帰（`/whois <nick>` で確認） |
| `xibot` | `xi> 3d6` |
| `roulettebot` | `roulette> A B C` / `random>` |
| `titlebot` | チャネルに `http://example.com` を投稿 |
| `tenkibot` | `tenki> 東京` |
| `messagebot` | `伝言 <nick> <本文>` |

## 7. よくある問題

| 症状 | 原因 / 対処 |
|------|-----------|
| 起動直後に `Restarting` | ポート競合 or 設定エラー。`docker compose logs` を確認 |
| `Connection closed by server` の繰り返し | nick 衝突 or ACL 拒否。nick 変更 / サーバー ACL を確認 |
| IRC 接続できない（TLS） | `ssl_params.verify` / 証明書ストア。必要なら CA 指定 |
| クライアントが TLS で接続できない | 証明書指紋の未登録（自己署名）/ `-ssl_verify=off`。ホスト側のポート公開 (`PADOKA_CLIENT_PORT`) / `client_server.acl` も確認 |
| SASL 認証で 907 が返る | `client_server.pass` とクライアント側の SASL パスワード不一致 |
| ボットが動かない | `bots:` に name が入っているか / プラグインの読み込みエラーをログで確認 |
| ログが出ない | `loglevel` が低すぎる（デバッグは 3）。`log/` のボリュームも確認 |

## 8. 本番運用の注意点

- **`client_server.pass` と `client_server.acl` を必ず設定**してください
  （デフォルトの `PadokaPassWord` / 全許可のままでは危険です）。
- **TLS 証明書**: Docker イメージには自己署名証明書が焼き込まれています。
  公開ホストでクライアントに接続させる場合は `ssl_cert_file` / `ssl_key_file`
  を CA 発行証明書（Let's Encrypt 等）へ差し替えてください。
  `client_server.sasl` が有効な場合、SASL PLAIN のパスワードもこの
  証明書経路で保護されます（平文ポートでは絶対に使わないこと）。
- `evalbot` / `shellbot` を使う場合は、**必ず ACL で信頼クライアントのみに
  限定**してください（任意コード / コマンドの実行が可能です）。
- 設定ファイルには NickServ パスワード等が入る場合があるため、ファイル
  権限に注意（Dockerfile では `/app/log` を 0600 相当で運用するよう
  ボリューム化しています）。設定本体はイメージに焼き込まれているため、
  機密を含む場合は compose で外部ファイルマウントに切り替えてください。

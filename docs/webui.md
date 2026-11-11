# WebUI ガイド

Padoka は標準ライブラリのみ（外部依存なし）で動作する軽量な WebUI を内蔵しています。
ブラウザから IRC のログを閲覧し、メッセージを発言できます。

## 有効化

`config/padoka.yml` に `webui:` セクションを追記し、再起動します。

```yaml
webui:
  enabled: true            # false で無効（デフォルト）
  host: 0.0.0.0            # バインド対象。ローカルのみなら 127.0.0.1
  port: 8080
  pass: ""                 # 空 = 認証なし（危険）。値を設定するとトークン必須
  title: "Padoka WebUI"
  buffer: 200              # チャネルごとのインメモリ保持行数
```

- **`host: 127.0.0.1`** → 実行マシンからのみアクセス可能（安全）
- **`host: 0.0.0.0`** → LAN / リモートからアクセス可能。**必ず `pass` を設定**してください

起動後、`http://<host>:<port>/` をブラウザで開きます。
`pass` を設定している場合、初回にトークン入力のログイン画面が表示されます。

## 画面の機能

- **左：チャネル一覧** — 参加中のチャネル + `system`（ジョイン/キック/ニック変更等）。
  件数はメンバー数。
- **中央：メッセージ** — 選択チャネルの直近 `buffer` 行 + **ライブ表示**
  （Server-Sent Events で自動更新）。
- **入力欄** — ENTER で選択チャネルに `PRIVMSG` 送信。
- **ヘッダー** — 接続状態・nick・上流サーバー。
- **ボタン**:
  - `ログ読込` — 該当チャネルのディスクログ（`log/`）の末尾を表示
  - `Part` — 選択チャネルから離脱
  - `Reload` — 設定をリロード

## 発言

WebUI での発言は、IRC 上の padoka の nick（`nick:` 設定）から送信されます。
送信したメッセージは上流 IRC サーバーからエコーとして戻り、
ライブ表示に反映されます。

## API（JSON）

`pass` を設定している場合は、`Authorization: Bearer <pass>` ヘッダー、
またはクエリ `?token=<pass>`（EventSource 用）が必要です。

| 方法 | パス | 説明 |
|------|------|------|
| GET  | `/`                      | WebUI ページ |
| GET  | `/api/status`            | 接続状態・nick・サーバー・チャネル一覧 |
| GET  | `/api/channels`          | 参加チャネル（名 / topic / メンバー） |
| GET  | `/api/messages?channel=<ch>&limit=<n>` | 直近メッセージ（インメモリ） |
| GET  | `/api/logfile?channel=<ch>&limit=<n>`  | ディスクログ末尾（履歴） |
| GET  | `/api/events?channel=<ch>` | SSE でライブストリーム（`channel` 省略 = 全チャネル） |
| POST | `/api/send`              | `{"channel","text","kind":"privmsg"\|"notice"}` で発言 |
| POST | `/api/join`              | `{"channel"}` で参加（key 自動適用） |
| POST | `/api/part`              | `{"channel"}` で離脱 |
| POST | `/api/nick`              | `{"nick"}` でニック変更 |
| POST | `/api/command`           | `{"command":"WHOIS foo"}` で生 IRC コマンド |
| POST | `/api/reload`            | 設定リロード |
| POST | `/api/reconnect`         | 上流サーバーへ再接続 |
| POST | `/api/quit`              | シャットダウン |

### SSE 例

```js
const es = new EventSource("/api/events?channel=%23chan&token=" + encodeURIComponent(TOKEN));
es.onmessage = (e) => {
  const rec = JSON.parse(e.data);
  // rec = {time, nick, channel, command, text, raw, ...}
};
```

## セキュリティ

- **発言・制御コマンドが実行できる**ため、`host: 0.0.0.0` での公開時は
  **必ず `pass` を設定**してください。
- `pass` は定長比較（`hmac.compare_digest`）で行い、タイミング攻撃に強くしています。
- Docker で公開する場合は、可能ならリバースプロキシ（nginx 等）の背後に置き、
  TLS と IP 制限を付けましょう。

## 実装

- `src/padoka/webui.py` — HTTP サーバー（`http.server.ThreadingHTTPServer`）、
  SSE、組み込み UI（HTML/JS/CSS）。
- `PDKServer` がサーバーメッセージのディスパッチ内で `WebUI.on_message()` に
  全メッセージを渡し、チャネル別にバッファリング + SSE 配信します。
- 数値応答（NAMES / ISUPPORT 等）はチャネル表示を汚さないよう除外しています。

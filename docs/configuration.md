# Padoka 設定ガイド

YAML 形式の設定ファイル（`config/padoka.yml`）の全項目について解説します。

## 基本設定

| キー | デフォルト | 説明 |
|------|-----------|------|
| `setting_name` | 設定ファイル名 | ログファイル名の接頭辞に使用 |
| `loglevel` | `2` | 0:quiet, 1:チャネルログ, 2:通常, 3:デバッグ |
| `padoka_server_name` | `PadokaProgram` | クライアントに表示されるサーバー名 |

## クライアントサーバー

```yaml
client_server:
  host: null        # バインド対象。null = 0.0.0.0
  port: 6667        # null = リスニングしない
  pass: PadokaPassWord  # クライアントの /PASS 値（SASL PLAIN でも同じ値を検証）
  sasl: true        # SASL (ircv3) PLAIN 認証。false = /PASS のみ
  acl: null         # null = 全許可。文字列で ACL ルール
  ssl_cert_file:    # TLS 有効化（省略可）
  ssl_key_file:
```

### TLS

- `ssl_cert_file` / `ssl_key_file` の**両方**を指定すると、クライアントポートは
  **TLS のみ**になります（平文接続は TLS レベルで拒否される）。
  片方だけの指定は無効です。
- 証明書の読み込みに失敗した場合はクライアントポート自体を開きません
  （fail closed。平文へフォールバックしません）。
- 自己署名のサンプル証明書・秘密鍵が `config/certs/padoka.crt` /
  `padoka.key` としてリポジトリに同梱されています（初期動作確認用）。
  Docker イメージでは `COPY config/` で取り込まれ、存在しない場合のみ
  ビルド時に再生成されます。
  公開ホストで運用する場合は CA 発行の証明書に差し替えてください。

### SASL（ircv3）

`AUTHENTICATE PLAIN` をサポートします（`sasl: true` がデフォルト）。

- クライアントは `AUTHENTICATE PLAIN` → サーバの `AUTHENTICATE +` に対して
  `AUTHENTICATE <base64( [authzid] NUL authcid NUL pass )>` を送ります。
- `pass` は `client_server.pass` と同じ値を検証します。成功で `904`
  （SASL: success）、失敗で `907` を返して切断します。
- SASL 成功時は `/PASS` を送らずにログインできます。
  `/PASS` を送る場合は従来どおり `pass` と照合します。
- `AUTHENTICATE`（メカニズム一覧要求）には `903` で `PLAIN` を返します。
- **注意**: PLAIN はパスワードをクリアで送信するため、必ず TLS と併用してください。

#### irssi の設定例（TLS + SASL）

```ini
/SETTINGS SET -re save.sasl.mechanism PLAIN
/SETTINGS SET -re save.sasl.username myuser
/SETTINGS SET -re save.sasl.password mypassword
/SAVE
```

```ini
servers {
  { host = "127.0.0.1"; port = "6667"; ssl = true;
    ssl_verify = true; sasl = true; }   # 自己署名は ssl_verify = false
}
```

#### weechat の設定例（TLS + SASL）

```weechat
/weechat set irc.server.padoka.address "127.0.0.1"
/weechat set irc.server.padoka.port 6667
/weechat set irc.server.padoka.ssl 1
/weechat set irc.server.padoka.sasl_mechanism plain
/weechat set irc.server.padoka.sasl_username myuser
/weechat set irc.server.padoka.sasl_password mypassword
```

### ACL 形式

```
allow 192.168.0.0/24
deny all
```

- 上から順に判定、最初一致で確定
- 全ルール未一致時は許可
- 対象: IP / CIDR / ホスト名 / `all`

## IRC サーバー接続

```yaml
servers:
  - host: irc.ircnet.ne.jp
    port: 6697
    pass: null              # 上流サーバーのサーバーパス（省略可）
    ssl_params:
      verify: true          # 証明書検証
      # ca_cert: /path      # 独自CA（自己署名上流のピンニングに使用）
      # check_hostname: false
```

#### 自己署名上流への接続（ircnet.ne.jp 等）

ircnet 等は公的 CA 発行ではなく自己署名（または私有 CA）証明書を使うため、
default のままでは `certificate verify failed: unable to get local issuer
 certificate` になります。**`verify: false` にしない**で、上流が提示する
証明書を `ca_cert` でピンニングします:

```bash
# サーバーが到達できるマシンで実行（指紋を確認して）
scripts/fetch-upstream-cert.sh dh.ircnet.ne.jp 6697 config/certs/ircnet-ca.pem
```

```yaml
servers:
  - host: dh.ircnet.ne.jp
    port: 6697
    ssl_params:
      verify: true
      ca_cert: config/certs/ircnet-ca.pem   # ホスト名検証は維持される
```

証明書がローテーションされた場合はスクリプトを再実行し、指紋が変わった
ことを確認して差し替えます。

複数指定するとランダム選択し、再接続時にローテーションします。

## ユーザー情報

```yaml
user: padoka
nick: padoka_py
hostname: null              # null = システムホスト名
servername: "*"
realname: padoka user
mode: null                  # ユーザーモード（例: "+i"）
away_message: away
away_nick: null             # null = 非接続時 nick 変更なし
quit_message: "Quit Padoka"
```

## チャネル

```yaml
channel_info:
  "#example":
    timing: startup         # startup（起動時参加）| login（クライアント接続時）
    key: null               # チャネルキー
    initial_mode: "+nt"    # 353 受信後の初期モード設定
    auto_create: false      # 403 受信時に安全チャネルを作成
    backlog_lines: 20       # 该当チャネルのバックログ行数
    log:                    # 該当チャネル専用ログ
      file: "example/%y%m%d.log"
      time_format: "%H:%M:%S"
      message_format:
        PRIVMSG: "<{nick}> {msg}"
```

### ログメッセージのプレースホルダー

| プレースホルダー | 意味 |
|-----------------|------|
| `{nick}` | 送信者 nick |
| `{msg}` | メッセージ本文 |
| `{ch}` | チャネル名 |
| `{newnick}` | NICK 変更後の新 nick |
| `{kicker}` | KICK 実行者 |
| `{sender}` / `{receiver}` | 個人チャットの送受信先 |
| `{prefix:nick}` | プレフィックスから nick |
| `{prefix:user}` | プレフィックスから user |
| `{prefix:host}` | プレフィックスから host |
| `{orig}` | 元メッセージ全文 |

## ログ出力

```yaml
log_dir: ./log

default_log:              # チャネル別メッセージログ
  file: "${setting_name}-${channel_name}/%y%m%d.log"
  time_format: "%H:%M:%S"

# system_log:             # システムログ（設定変更、接続状態等）
#   file: "${setting_name}-system.log"

# talk_log:               # 個人チャットログ
#   file: "${setting_name}-talk/%y%m%d.log"

# debug_log:              # デバッグログ（loglevel 3 のみ）
#   io: stdout
```

## ボット

```yaml
plugins_dir: ./plugins
bots:
  - backlogbot            # 単純な名前指定
  - name: samplebot       # 名前 + 設定
    ch: "#specific"       # 対象チャネル制限
  - name: mybot           # カスタムボット
    option1: value1
    option2: value2
```

## その他

| キー | 説明 |
|------|------|
| `reconnect_delay` | 再接続待ち秒数（デフォルト 150） |
| `backlog_lines` | デフォルトバックログ行数（デフォルト 20） |
| `filename_encoding` | ログファイル名のエンコーディング（`utf8`） |

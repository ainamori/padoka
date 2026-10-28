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
    # 上流 SASL（ircv3 PLAIN）。NICK 送信前に AUTHENTICATE を行い、
    # 904 (success) を確認してから登録する。多くの IRC ネットワークで
    # 登録 nick の使用に必須（SASL 必須サーバー）。
    # password が空の sasl ブロックは警告を出して SASL をスキップします。
    # sasl:
    #   username: mynick      # NickServ アカウント（省略すると user を使用）
    #   password: nickserv_password
```

#### 上流 SASL（ircv3 PLAIN）

上流 IRC ネットワークへの SASL 認証（登録済み nick を使うための
NickServ 認証）です。**クライアント側の SASL**（irssi → padoka の
`client_server.sasl` / `client_server.pass`）とは別物です。

**いつ必要か**

- ircnet の**海外からの接続は必須**（irc / dh は日本国内限定、dev は
  SASL 認証ありでのみ海外から接続可）
- SASL 必須の上流ネットワークで登録 nick を使う場合
- 接続元 IP によるニックネーム保護が欲しい場合

**1. NickServ アカウントを用意する**

まだ持っていない場合、irssi 等で上流 IRC に**一度だけ**接続して登録します:

```
/msg NickServ register padoka_py <あなたのパスワード>
```

登録済みの nick を使う（またはパスワード変更）場合は:

```
/msg NickServ setpass <あなたのパスワード>
```

ircnet への登録手順の詳細は ircnet のヘルプページを参照してください。

**2. `servers[]` に `sasl:` を設定する**

```yaml
servers:
  - host: dev.ircnet.ne.jp
    port: 6697
    pass: null
    ssl_params:
      verify: true
    sasl:
      username: padoka_py        # NickServ アカウント名（= 使う nick）
      password: "nickserv_pass"  # 上記で設定したパスワード
```

- `username` を省略すると `user:` の値が authcid になります
  （NickServ アカウント名と一致させるべき）
- `password` が空の `sasl:` は警告を出して **SASL をスキップ**します
  （空パスワードで 907 になるのを避けるため）。接続したい場合は必ず記入。
- サーバー毎に設定できます（irc/dh は SASL 不要、dev は SASL 必要、
  といった区別が可能です）

**3. 動作の確認**

```
padoka -c config/padoka.yml --debug
```

正常ならこの順のログが出ます:

```
[PDK] SASL PLAIN started (username=padoka_py).
[PDK] SASL success (username=padoka_py).
[<S] ... command='001' ...          <- welcome（登録完了）
```

padoka は NICK/USER を送る**前**に `AUTHENTICATE PLAIN`（ircv3）を行い、
904 (SASL: success) を確認してから登録します。906/907/908（失敗）や
ERROR が来たら原因をログに出して再接続します。

**セキュリティ注意**

- `password` は設定ファイルに平文で入るため、ファイル権限を
  `chmod 600 config/padoka.yml` 程度にすること
- 設定を git 等のバージョン管理に**コミットしない**こと
  （`padoka.ircnet.yml` 等のサンプルは password を空欄にして同梱）
- Docker で設定をボリュームマウントしている場合はホスト側のファイル
  権限を同様に管理する

#### 上流 TLS 検証の失敗（`CERTIFICATE_VERIFY_FAILED`）

`verify: true` のまま `certificate verify failed` が出た場合は、
**まず診断スクリプトで原因を判定**してください:

```bash
# サーバーが到達できるマシンで実行（診断のみ。ファイルは書出しない）
scripts/fetch-upstream-cert.sh dh.ircnet.ne.jp 6697
```

スクリプトは提示チェーンの subject / issuer / 指紋を表示し、
**padoka と同じ条件（SNI + ホスト名検証）で検証テスト**を実行します:

| 診断結果 | 原因 | 対処 |
|----------|------|------|
| issuer に Let's Encrypt 等（公的 CA）+ テスト1 失敗 | **OS の CA ストアが古い**（ISRG Root X2 等が不足） | `dnf update ca-certificates && update-ca-trust`（root）→ 再実行してテスト1 成功を確認。**ca_cert は不要** |
| issuer == subject（自己署名） | 上流が自己署名証明書 | `scripts/fetch-upstream-cert.sh <host> <port> config/certs/<name>-ca.pem` を実行し、`ca_cert` で指定 |
| 公的 CA でもない（私有 CA） | 私有 CA の root がストアに無い | 上から取得した PEM（中間+root、leaf 除外）を `ca_cert` で指定 |

**やってはいけないこと**:

- `verify: false` — MITM 対策が完全に無効になる（SASL PLAIN を扱う以上不可）
- **leaf を含むファイルを `ca_cert` に設定** — OpenSSL は信頼ストア内の
  leaf をチェーン終端のアンカー扱いするため、OS の CA ストアが最新でも
  検証が**壊れる**（`unable to get local issuer certificate` になる）

自己署名 / 私有 CA での pinning の例:

```yaml
servers:
  - host: some.example
    port: 6697
    ssl_params:
      verify: true
      ca_cert: config/certs/some-ca.pem   # leaf を含まないファイル
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

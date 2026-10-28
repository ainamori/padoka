# Padoka - IRC クライアント/サーバー プロキシ

Padoka は IRC サーバーに接続し、独自に IRC クライアントポート
（デフォルト `6667`）を公開するプロキシです。
irssi や weechat などの通常の IRC クライアントから接続できます。
また、Python 製のボットプラグインフレームワークを内蔵しています。

> 🚀 **まずは動かしたい方**: [Quick Start](docs/quickstart.md) を見てください。
> 10 分で「IRC に 1 nick で常駐し、複数クライアントから使えるプロキシ」が手に入ります。

## 機能

- 上流 IRC サーバーへの自動再接続、ニックネーム衝突処理、PING/PONG ワッチドッグ
- クライアントリスナー（**TLS** 対応、**SASL (PLAIN)** / PASS / NICK / USER 認証、MOTD、チャネル NAMES 再現）
- クライアント不在時の Away モード（ニックネーム変更も可能）
- チャネル別 / システム / 個人チャット / デバッグ ログ（フォーマット可変）
- メモリ内メッセージバックログ
- Python ボットプラグイン（`on_privmsg`、`on_join`、`on_timer` など）
- YAML 設定、Docker / docker-compose パッケージング

## クイックスタート（Docker なし）

```bash
pip install -e .
padoka -c config/padoka.yml --debug
```

IRC クライアントから `localhost:6667` に**TLS + SASL** で接続します
（SASL のパスワード: `PadokaPassWord` = `client_server.pass`）。

## Docker Compose で実行

```bash
docker build -t padoka:py .
docker compose up -d
docker compose logs -f padoka     # 起動ログ確認
```

### IRC クライアントで接続する（irssi の例、TLS + SASL）

```bash
irssi
/SETTINGS SET -re save.sasl.mechanism PLAIN
/SETTINGS SET -re save.sasl.username myuser
/SETTINGS SET -re save.sasl.password PadokaPassWord   # client_server.pass と同じ
/SAVE
/conn -server 127.0.0.1 -port 6667 -ssl -sasl        # PADOKA_CLIENT_PORT と一致させる
/#yourchannel                                         # channel_info で startup にしたチャネル
```

クライアントポートは TLS のみです。Docker イメージには自己署名証明書が
焼き込まれているため、SSL 検証エラーが出たら指紋を登録するか（開発時のみ）
`-ssl_verify=off` を使ってください。本番は CA 発行証明書へ差し替えを。

接続すると現在参加中のチャネルのメンバー（353 NAMES）と TOPIC が再現され、
往復メッセージやボットが動作します。本番デプロイの手順・よくある問題は
[docs/deploy-checklist.md](docs/deploy-checklist.md) を参照してください。
TLS / SASL の設定詳細は [docs/configuration.md](docs/configuration.md) を参照。

### 上流 SASL（NickServ で登録済み nick を使う場合）

上流 IRC ネットワークに SASL 認証で接続したい場合（ircnet では**海外からの
接続に必須**、登録 nick の使用で推奨）は、`servers[]` に `sasl:` を追加します:

```yaml
servers:
  - host: dev.ircnet.ne.jp
    port: 6697
    ssl_params:
      verify: true
    sasl:
      username: padoka_py        # NickServ アカウント名
      password: "<nickservパスワード>"
```

- NickServ アカウントの登録・パスワード設定手順: **irssi 等で ircnet に接続** →
  `/msg NickServ register padoka_py <パスワード>`（新規）または
  `/msg NickServ setpassword <パスワード>`（変更）。
  `padoka -c config/padoka.yml --debug` のログに `SASL success (username=...)`
  が出て 001 welcome が来れば成功です。
- `password` が空だと警告を出して SASL をスキップします（匿名接続になる）。
- 詳細（ircnet の地理制約を含む）: [docs/configuration.md](docs/configuration.md#上流-saslircv3-plain)

### ネットワーク構成

デフォルトでは bridge ネットワークで `client_server.port` をホストに公開します。
ホスト側のポート競合を避ける場合は以下のようにポートを変更できます:

```bash
PADOKA_CLIENT_PORT=16667 docker compose up -d
```

別として、`network_mode: host`（Linux）でホストのポートに直接バインドする
方式もあります（`docker-compose.host.yml` を参照）。

## 設定

`config/padoka.yml` を参照してください。すべての項目にインラインコメントがあります。

## ドキュメント

| ドキュメント | 内容 |
|-------------|------|
| [docs/quickstart.md](docs/quickstart.md) | **Quick Start**（ゼロから起動 → IRC クライアント接続 → ボット有効化） |
| [docs/architecture.md](docs/architecture.md) | **全体構成ガイド**（モジュール / スレッド / メッセージの流れ / ライフサイクル） |
| [docs/deploy-checklist.md](docs/deploy-checklist.md) | 実 IRC 環境でのデプロイ / 検証チェックリスト |
| [docs/configuration.md](docs/configuration.md) | 設定項目の詳細解説 |
| [docs/bot-development.md](docs/bot-development.md) | ボットプラグイン開発ガイド |
| [docs/docker.md](docs/docker.md) | Docker / docker-compose 運用ガイド |

## ボットの作成

`plugins/mybot.py` を作成します:

```python
from padoka.bot import NDKBot

class MyBot(NDKBot):
    def on_privmsg(self, prefix, ch, msg):
        if msg.startswith("!hi"):
            self.send_notice(ch, "Hello, %s!" % prefix.nick)
```

YAML の `bots:` に `- name: mybot` を追加すれば有効になります。
利用可能なハンドラーやヘルパーメソッドの詳細は `src/padoka/bot.py` の
ドキュメントを参照してください。

## シグナル

| シグナル | 動作 |
|----------|------|
| SIGINT / SIGTERM | 終了 |
| SIGHUP | 設定のリロード |
| SIGUSR1 / SIGUSR2 | ボットイベント発火 |

IRC 経由でも制御できます:
`/PADOKA RELOAD`, `/PADOKA RECONNECT`, `/PADOKA STATUS`,
`/PADOKA QUIT`, `/PADOKA RESTART`

## 利用可能なボットプラグイン

`plugins/` に用意されているボットです。設定の `bots:` に `name` で追加すると
有効になります。全 26 ボットを移植済みです。

### 常駐 / 接続管理
| プラグイン | 機能 |
|-----------|------|
| `backlogbot` | クライアント接続時に直近のメッセージ履歴を再送 |
| `autoawaybot` | 操作がない一定時間後に自動 Away、操作で復帰 |
| `sendpingbot` | 定期 PING をクライアント全員へ送信（接続維持） |
| `identifynickserv` | 接続時に NickServ `/identify` を自動実行 |
| `autodumpbot` | 参加中のチャネル情報をファイルへ自動ダンプ |
| `modemanager` | 参加時に設定したモード（+o/-b 等）を自動適用 |
| `autoop` | 指定ユーザーが参加時に自動で OP 権限を付与 |
| `opshop` | 参加した全員に自動で OP 権限を付与 |

### ログ / 定期
| プラグイン | 機能 |
|-----------|------|
| `timestampbot` | 一定間隔でログにタイムスタンプ区切りを挿入 |
| `cronbot` | 定期メッセージ送信（雛形） |
| `checkbot` | 定期チェック用ボットの基底クラス（継承して使う） |

### 伝言 / メール
| プラグイン | 機能 |
|-----------|------|
| `messagebot` | 伝言板（`伝言 <nick> <本文>` で保留、本人発言時に通知） |
| `mailcheckbot` | Maildir / mh の受信チェック（biff） |

### サイコロ / ランダム
| プラグイン | 機能 |
|-----------|------|
| `xibot` | サイコロ振り（`xi> 3d6`、CoC 風 SAN ロール） |
| `roulettebot` | ランダム抽選 / シャッフル / 乱数 |

### Web / 検索
| プラグイン | 機能 |
|-----------|------|
| `titlebot` | URL の HTML `<title>` を取得して返信（SSRF 防御付き） |
| `rss_checkbot` | RSS / Atom フィードの更新チェック |
| `githubissuesbot` | GitHub Issues / コメント更新チェック |
| `googlebot` | Google CSE 検索 + 電卓（`g>`, `googlec>`, `g:calc>`）* |
| `opensearchbot` | OpenSearch RSS で検索結果数を取得（汎用） |
| `codesearchbot` | Debian コード検索（`codesearch>`） |
| `gonzuibot` | Ruby API / モジュール検索（`gonzui>`） |
| `tenkibot` | 気象庁の天気予報テキスト取得（`tenki>`）* |

### 危険機能（ACL による制限を強く推奨）
| プラグイン | 機能 |
|-----------|------|
| `evalbot` | 個人メッセージ宛に Python 式を評価（サブプロセス） |
| `shellbot` | 個人メッセージ宛に shell コマンドを実行 |

### テスト
| プラグイン | 機能 |
|-----------|------|
| `samplebot` | テスト用。PRIVMSG に NOTICE で応答 |

\* `googlebot` は旧 AJAX Search API が廃止のため Custom Search JSON API へ移行
（`api_key` + `cx` 必須）。`tenkibot` は標準ライブラリのみで堅牢にするため
テキストサマリー（pre.textframe）を返します。

## 移植を見送ったボット

外部ネイティブ依存、または閉鎖サービスの依存のため移植していません:

| プラグイン | 理由 |
|-----------|------|
| `crubybot` | libclang / ffi-clang のネイティブ依存 |
| `drbot` | DRb（Ruby 固有 RPC）プロトコルの再設計が必要 |
| `weba` | WEBrick による IRC Web フロントエンド（構造が複雑） |
| `marldiabot` | Marldia 外部サービス + プロキシ設定（ニッチ） |
| `twitterbot` | OAuth 1.0a + API キー（外部サービス） |
| `pastebot` | 依存先 atdot.net が閉鎖済み |
| `dictbot` | 依存 Yahoo 辞書 API が閉鎖済み |
| `sixamobot` | Ruby `sixamo` ライブラリ依存 |

## ディレクトリ構成

```
.
├── src/padoka/        # Python パッケージ本体
│   ├── irc/           #   IRC プロトコル（メッセージ/接続）
│   ├── server.py      #   メインサーバー（上流接続/クライアント管理）
│   ├── client.py      #   クライアント接続ハンドラー
│   ├── config.py      #   YAML 設定読み込み
│   ├── state.py       #   チャネル/メンバー/モード追跡
│   ├── logger.py      #   ログ出力 + バックログ保存
│   ├── bot.py         #   ボット基盤クラス
│   ├── acl.py         #   アクセス制御リスト
│   ├── notify.py      #   systemd sd_notify
│   └── errors.py      #   例外階層
├── plugins/           # ボットプラグイン（.py）
├── config/            # 設定ファイル
├── docs/              # 詳細ドキュメント
├── vendor/            # オフライン Docker build 用の wheel
├── tests/             # テスト
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## テスト

```bash
python tests/test_integration.py
```

フェイク IRC サーバー + NDKServer + フェイククライアントを起動し、
接続・ログイン・PONG・ボット応答・メッセージ転送・/PADOKA STATUS を確認します。

## CI

GitHub Actions（`.github/workflows/ci.yml`）が push / PR で自動的に実行します。

- **test**: 全ファイルのコンパイルチェック + 上記統合テスト（Python 3.10 / 3.12）。
  外部ネットワークに依らず（フェイク IRC サーバー）確定的に回帰を検出します。
- **docker**: イメージのビルド + 起動スクリプト / import の確認。

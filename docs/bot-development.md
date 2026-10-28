# ボットプラグイン開発ガイド

## 基本構造

`plugins/` 配下に `.py` ファイルを作成し、`NDKBot` を継承したクラスを定義します。

```python
from padoka.bot import NDKBot

class MyBot(NDKBot):
    """bot_initialize で設定を読み込む"""

    def bot_initialize(self):
        self._keyword = self.bot_config.get("keyword", "!hello")

    def on_privmsg(self, prefix, ch, msg):
        if msg.startswith(self._keyword):
            self.send_notice(ch, "Hello, %s!" % prefix.nick)
```

YAML に追加:
```yaml
bots:
  - name: mybot
    keyword: "!こんにちは"
```

## 利用可能なハンドラー

ハンドラーは `on_<セレクタ>` の形式で、**存在する場合のみ**呼び出されます。

### IRC メッセージ系

| ハンドラー | シグネチャ | 発火条件 |
|-----------|-----------|----------|
| `on_privmsg` | `(prefix, ch, msg)` | PRIVMSG 受信 |
| `on_notice` | `(prefix, ch, msg)` | NOTICE 受信 |
| `on_join` | `(prefix, ch)` | 誰かがチャネルに参加 |
| `on_part` | `(prefix, ch, msg)` | 誰かがチャネルを離脱 |
| `on_nick` | `(prefix, newnick)` | ニック変更 |
| `on_quit` | `(prefix, msg)` | 誰かが IRC を終了 |
| `on_kick` | `(prefix, ch, victim, comment)` | KICK |
| `on_mode` | `(prefix, ch, *args)` | MODE 変更 |
| `on_topic` | `(prefix, ch, topic)` | TOPIC 変更 |

### 数値応答系（例）

| ハンドラー | 対応数値 |
|-----------|---------|
| `on_rpl_welcome` | 001 |
| `on_rpl_namreply` | 353 |
| `on_rpl_topic` | 332 |
| `on_err_nosuchchannel` | 403 |

### イベント系

| ハンドラー | シグネチャ | 発火条件 |
|-----------|-----------|----------|
| `on_timer` | `(now)` | 約60秒ごと |
| `on_client_login` | `(count, client)` | クライアント接続 |
| `on_client_logout` | `(count, client)` | クライアント切断 |
| `on_server_connected` | `()` | 上流接続完了 |
| `on_quit_from_channel` | `(ch, nick, qmsg)` | チャンネル内のメンバーが IRC 終了 |
| `on_every_message` | `(prefix, command, *args)` | 全メッセージ（常に発火） |
| `on_client_privmsg` | `(client, ch, msg)` | クライアントからの PRIVMSG |
| `on_padoka_command` | `(client, cmd, *args)` | /PADOKA 未対応コマンド |

### `prefix` 引数

`PrefixObject` で、以下にアクセスできます:

- `prefix.nick` - nick
- `prefix.user` - user
- `prefix.host` - host
- `str(prefix)` - 元プレフィックス文字列

## ヘルパーメソッド

| メソッド | 説明 |
|---------|------|
| `self.send_notice(ch, msg)` | NOTICE を送信（上流+クライアント両方） |
| `self.send_privmsg(ch, msg)` | PRIVMSG を送信 |
| `self.send_msg(cmd)` | 任意のコマンドをサーバーへ送信 |
| `self.change_mode(ch, mode, user)` | ユーザーモード変更 |
| `self.change_nick(nick)` | 自身の nick 変更 |
| `self.ccn(ch)` | チャネル名正規化 |
| `self.ccn2rcn(ccn)` | 正規化名 → 元のチャネル名 |
| `self.has_same_bot(ch)` | 同じボットが他クライアントにいるか |

## 状態アクセス

```python
self.state.nick()                # 現在の nick
self.state.channels()            # 参加中のチャネル一覧（正規化名）
self.state.channel_users(ch)     # チャネルメンバー（正規化 ch）
self.state.channel_user_mode(ch, nick)  # ユーザーモード
```

## 設定値のアクセス

```python
self.bot_config  # YAML の bots: エントリーの dict
self.config      # 全体設定（PadokaConfig インスタンス）
self.logger     # ロガー
self.manager    # サーバー（詳細操作用）
```

## 例外を使った制御

ハンドラー内で以下の例外を送出すると特殊な処理を行います:

| 例外 | 動作 |
|------|------|
| `NDKBotBreak` | ボットディスパッチループを中断 |
| `NDKBotSendCancel` | メッセージのクライアント送信をキャンセル |

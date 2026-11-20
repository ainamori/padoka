# 系譜と移植説明（madoka → nadoka → padoka）

Padoka は「IRC に 1 つの nick で常駐し、複数の IRC クライアントからその
セッションを共有できる IRC クライアント / サーバー プロキシ」という
概念の系譜を受け継いでいます。

| 世代 | 言語 | 時期 | 作成者 | 備考 |
|------|------|------|--------|------|
| [madoka](https://www.madoka.org/)（まどかちゃん） | Perl | 1998-（最終 4.2.10, 2003） | The madoka project | 概念の原点。「IRC 常駐ソフト」として開発開始 |
| [nadoka](https://github.com/nadoka/nadoka) | Ruby | 2004-2005-（最終 0.10.1） | SASADA Koichi, Kazuhiro NISHIYAMA ほか | madoka の Ruby 再実装（「It's same concept as madoka」） |
| **padoka**（本プロジェクト） | Python | 2026- | Akira Inamori | nadoka / madoka の機能設計を Python に再実装 |

## 移植の内容

Padoka は本リポジトリ内に上流（Perl / Ruby）のソースコードを含んで
いません。madoka / nadoka の**機能・設計**を Python で書き直した
もので、元の設計を参照した箇所はコード内に注記があります
（例: `irc/connection.py` の flood 制御は madoka の `flood.mpi` に
倣う、`state.py` の自己防御挙動など）。

### コア機能（madoka / nadoka 由来の設計）

- 上流 IRC サーバーへの 1 nick 常駐 + クライアントポート公開（プロキシ方式）
- 送信 flood 制御（スライディングウィンドウ）
- チャネルの自動再参加（403 等）
- ボットプラグイン方式（`on_<event>` ハンドラー。nadoka の
  `Ndk::Bot` 相当が `padoka.bot.PDKBot`）
- `plugins/` の 26 ボットはすべて madoka / nadoka の同名プラグインの
  機能移植です（README「利用可能なボットプラグイン」参照）

### padoka 独自の新規機能（madoka / nadoka にないもの）

- **WebUI**（stdlib のみ: ログ閲覧 / 発言 / SSE ライブ表示）
- **上流 SASL（ircv3 PLAIN）+ CAP 交渉**（ircnet dev 等の海外接続対応）
- **TLS 対応**（上流・クライアント両端、システム CA ストア利用）
- **IRC ワイヤ文字コード設定**（`irc_encoding`、Shift_JIS 等のレガシー網対応）
- **systemd sd_notify**（READY / WATCHDOG / RELOADING）
- **Docker / docker-compose パッケージング**（複数 instance 起動構成）
- **ACL**（クライアントのソースアドレス制御）
- 診断スクリプト（`scripts/diag_client.py`, `scripts/diag_upstream.py`）

### 移植を見送ったボット

外部ネイティブ依存または閉鎖サービスの依存のため移植していません
（理由は README「移植を見送ったボット」を参照）:
`crubybot`, `drbot`, `weba`, `marldiabot`, `twitterbot`, `pastebot`,
`dictbot`, `sixamobot`。

## ライセンス

### Padoka（本リポジトリ）

- **GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later)**
- 全文: リポジトリ根の [`LICENSE`](../LICENSE)
- 出典表明: [`NOTICE`](../NOTICE)

AGPL-3.0 はネットワーク利用限定の copyleft ライセンスです。
Padoka（または改変版）を**ネットワークサービスとして提供する場合、
対応するソースコードの公開が義務**になります。
詳しくは [AGPL v3 第 13 条](https://www.gnu.org/licenses/agpl-3.0.en.html#section13)
を参照してください。

### 上流との関係

- **madoka**: 著作権表示（Copyright (c) 1998-, The madoka project）の
  みで明示的な公開ライセンスを定めていません。コードは一切含んで
  おらず、機能概念の参考です。
- **nadoka**: 「Ruby のライセンスと同条項で再配布・修改可能」（BSD 系の
  寛容ライセンス。リポジトリ内には LICENSE ファイルがありません）。
  同様にコードを含んでおらず、出典は [`NOTICE`](../NOTICE) に明記しています。

### サードパーティ依存

- **PyYAML**（MIT License）— 唯一のランタイム依存。
  `vendor/` にオフライン Docker build 用の wheel を同梱しています。

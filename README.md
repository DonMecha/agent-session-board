# agent-session-board

[日本語](README.md) | [English](README.en.md)

Claude CodeやCodexを何本も同時に動かしていると、どの会話が作業中で、どれが返事を待っているのか分からなくなります。agent-session-boardは、手元で動いているClaude CodeとCodexの会話をブラウザで一覧にする、小さなローカル画面です。起動方法は変えずに使えます。

- 会話ごとに、話題・状態（作業中／入力待ち／承認待ち／回答待ち／状態不明）・作業ディレクトリ・最終更新を出します。
- Claude Codeは、実行中のプロセスと、Claude Code自身が書いている会話記録から読み取ります。
- Codexは、Codex app-serverへの読み取り専用の問い合わせで、会話の一覧と状態を取ります。
- 2秒ごとに更新します。AIやAPIは呼ばないので、トークンは消費しません。

## 動作要件

- Linux（`/proc` を読みます）
- Python 3.8以降（標準ライブラリだけで動きます）
- Codexも表示する場合: Node.js 18以降と `npm install`（`ws` を入れます）、動作中のCodex app-server

Codexの条件が揃っていなくても、Claude Codeの一覧は表示できます。

## 使い方

```sh
git clone https://github.com/DonMecha/agent-session-board.git
cd agent-session-board
npm install            # Codexも表示する場合だけ

python3 agent_session_board.py --open      # サーバを立てて既定のブラウザで開く
python3 agent_session_board.py             # URLだけ表示する
python3 agent_session_board.py --snapshot  # 1回分の一覧をJSONで出す
```

- サーバは `127.0.0.1` の空いているポートだけで待ち受けます。ほかのホスト名からの要求は拒否します。
- 画面の「表示を終了」を押すと止まります。ブラウザを閉じた場合も、閲覧が5分途絶えると自動で終了します。止まるのは一覧だけで、Claude CodeやCodexの会話には影響しません。
- 一覧はどこにも保存しません。

## 何をどこから読むか

| 対象 | 読むもの | 上限 |
|---|---|---|
| Claude Code | 自分のユーザーの `claude` プロセス、`~/.claude/sessions/<pid>.json`、`~/.claude/projects/**/<session>.jsonl` | 1ファイル末尾256KiB（話題は先頭256KiBも）、最大128会話 |
| Codex | `~/.codex/app-server-control/app-server-control.sock` への読み取り専用RPC（`thread/loaded/list`、`thread/list`、`thread/read`） | 読込中128件＋最近32件＋アーカイブ16件、1.5秒で打ち切り |

- 話題には、会話のタイトルを使います。タイトルがなければ、最初の依頼の先頭100文字を使います。ツールの結果やコマンドの引数は表示しません。
- Claude Codeの「閉じた会話」は、この画面を起動している間に終了を確認した分だけです。
- Codexのソケットの場所は、環境変数 `CODEX_APP_SERVER_SOCKET` で変えられます。

## 注意

- Claude CodeとCodexの内部記録の形式に依存しています。どちらかの更新で形式が変わると、状態や話題を取れなくなることがあります。
- 画面には会話の話題（依頼の冒頭）が出ます。画面共有のときは気をつけてください。

## 検査

```sh
python3 agent_session_board.py --selftest
```

一時ディレクトリに偽の `/proc` と会話記録を作り、次を確かめます。

- 話題と状態の読み取り
- 大きな記録の末尾だけを読むこと
- PIDの再利用で古い会話を取り違えないこと
- 同じプロセスでの会話の切り替え
- Codexの会話が開く・閉じる・再び開く流れと、接続が切れたときの表示

## ライセンス

MIT

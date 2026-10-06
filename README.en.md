# agent-session-board

[日本語](README.md) | [English](README.en.md)

When you run many Claude Code and Codex conversations at once, it gets hard to tell which ones are working and which are waiting for you. agent-session-board is a small local web page that lists the Claude Code and Codex conversations running on your machine. You keep launching them the way you already do.

- For each conversation it shows the topic, state (working / waiting for input / waiting for approval / waiting for an answer / unknown), working directory and last update.
- Claude Code is read from running processes and the conversation logs Claude Code already writes.
- Codex is read through read-only queries to the Codex app-server.
- It refreshes every 2 seconds. It never calls an AI model or API, so it costs no tokens.

## Requirements

- Linux (reads `/proc`)
- Python 3.8+ (standard library only)
- To include Codex: Node.js 18+, `npm install` (installs `ws`), and a running Codex app-server

Claude Code conversations are listed even if the Codex requirements are not met.

## Usage

```sh
git clone https://github.com/DonMecha/agent-session-board.git
cd agent-session-board
npm install            # only needed for Codex

python3 agent_session_board.py --open      # start the server and open your default browser
python3 agent_session_board.py             # just print the URL
python3 agent_session_board.py --snapshot  # print one snapshot as JSON
```

- The server listens only on a free port on `127.0.0.1` and rejects requests for any other host name.
- Press "表示を終了" (stop) in the page to shut it down. If you just close the browser, it exits after 5 minutes without a viewer. Only the board stops; your Claude Code and Codex conversations are not affected.
- Nothing is saved to disk.

## What it reads

| Source | What | Limits |
|---|---|---|
| Claude Code | your own `claude` processes, `~/.claude/sessions/<pid>.json`, `~/.claude/projects/**/<session>.jsonl` | last 256 KiB per file (first 256 KiB too, for the topic), up to 128 conversations |
| Codex | read-only RPC on `~/.codex/app-server-control/app-server-control.sock` (`thread/loaded/list`, `thread/list`, `thread/read`) | 128 loaded + 32 recent + 16 archived, 1.5 s timeout |

- The topic is the conversation title, or the first 100 characters of the first request if there is no title. Tool results and command arguments are never shown.
- "Closed" Claude Code conversations are only those whose exit was observed while the board was running.
- Set `CODEX_APP_SERVER_SOCKET` to use a different Codex socket path.

## Caveats

- It depends on the internal record formats of Claude Code and Codex. An update to either may change those formats and break state or topic detection.
- The page shows conversation topics (the start of your requests). Be careful when screen sharing.
- The UI text is in Japanese.

## Tests

```sh
python3 agent_session_board.py --selftest
```

It builds a fake `/proc` and conversation logs in a temp directory and checks:

- topic and state parsing
- reading only the tail of large logs
- not attaching an old conversation after PID reuse
- switching conversations within one process
- the open / closed / reopened lifecycle of Codex threads, and what is shown when the connection drops

## License

MIT

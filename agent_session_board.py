#!/usr/bin/env python3
"""手元で動いている Claude Code / Codex の会話を、起動方法を変えずにブラウザで一覧にする。

tmux の一覧ではアプリ内の複数会話を拾えないため、/proc と各製品が既に書いている記録から導出する。
会話のタイトル、なければ依頼本文の短い抜粋を表示する。ツール結果やコマンド引数は出さない。

起動:     python3 agent_session_board.py --open   (サーバを立てて既定のブラウザで開く)
サーバ:   python3 agent_session_board.py          (URLを標準出力へ)
1回分:    python3 agent_session_board.py --snapshot (JSONを標準出力へ)
確認:     python3 agent_session_board.py --selftest
停止:     画面の「表示を終了」。閲覧が5分途絶えても終了する。常駐・自動起動はしない。
上限:     2秒に1回、1ファイル末尾256KiB(Claudeの話題は先頭256KiBも読む)、最大128会話。AI/API呼出0回。
開閉:     Claude Code はプロセスで確認。Codex は Codex app-server の読取専用RPCが正本(node と ws が要る)。
          Claude Code の観測中の終了履歴はメモリのみ(サーバを止めると消える)。
サーバは 127.0.0.1 限定、一覧は保存せず、読み取りだけ。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

HOME = Path.home()
HERE = Path(__file__).resolve().parent
HTML = HERE / 'board.html'
CODEX_SCRIPT = HERE / 'codex_threads.mjs'
LIMIT = 256 * 1024
MAX_SESSIONS = 128
IDLE_EXIT_SEC = 300


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def records(path, from_start=False):
    try:
        with path.open('rb') as f:
            size = os.fstat(f.fileno()).st_size
            f.seek(0 if from_start else max(0, size - LIMIT))
            if not from_start and size > LIMIT:
                f.readline()  # skip partial record
            data = f.read(LIMIT)
        result = []
        for line in data.splitlines():
            try:
                result.append(json.loads(line))
            except ValueError:
                pass
        return result
    except OSError:
        return []


def claude_state(rows, meta):
    for r in reversed(rows):
        if r.get('type') == 'system' and r.get('subtype') in ('turn_duration', 'stop_hook_summary'):
            return ('入力待ち', 'idle')
        if r.get('type') == 'assistant':
            reason = r.get('message', {}).get('stop_reason')
            if reason == 'end_turn':
                return ('入力待ち', 'idle')
            return ('作業中', 'busy')
        if r.get('type') == 'user' and not r.get('isMeta'):
            return ('作業中', 'busy')
    # registry status may be stale: only trust it briefly
    if time.time() - meta.get('statusUpdatedAt', 0) / 1000 < 30:
        s = meta.get('status')
        if s == 'idle':
            return ('入力待ち', 'idle')
        if s in ('working', 'busy', 'running'):
            return ('作業中', 'busy')
    return ('状態不明', 'unknown')


def claude_topic(rows, meta):
    # Auto-derived names identify a process, not its subject.
    title = meta.get('name') if meta.get('nameSource') in ('user', 'custom') else None
    for row in reversed(rows):
        if row.get('type') == 'custom-title' and row.get('customTitle'):
            title = row['customTitle']
            break
    if title:
        return ' '.join(str(title).split())[:120]
    for row in rows:
        if row.get('type') != 'user' or row.get('isSidechain'):
            continue
        content = row.get('message', {}).get('content', '')
        if isinstance(content, list):
            content = '\n'.join(block.get('text', '') for block in content if block.get('type') == 'text')
        if not isinstance(content, str):
            continue
        # Channel messages are marked isMeta; only take the body, never attributes.
        channel = re.fullmatch(r'\s*<channel\b[^>]*>(.*?)</channel>\s*', content, re.S)
        if channel:
            content = channel.group(1)
        elif row.get('isMeta'):
            continue
        content = content.strip()
        if not content or content.startswith(('<', '/', '# AGENTS.md', 'Stop hook feedback:')):
            continue
        content = ' '.join(content.split())
        return content[:100] + ('…' if len(content) > 100 else '')
    return '話題を取得できません'


def snapshot(proc=Path('/proc'), home=HOME):
    start = time.monotonic()
    entries = []
    for p in proc.iterdir():
        if not p.name.isdigit():
            continue
        try:
            if p.stat().st_uid != os.getuid():
                continue
            if (p / 'comm').read_text().strip() != 'claude':
                continue
            stat = (p / 'stat').read_text().rsplit(')', 1)[1].split()
            if stat[0] == 'Z':
                continue
            cwd = os.readlink(p / 'cwd')
            pid = int(p.name)
            meta = read_json(home / '.claude/sessions' / f'{pid}.json')
            if str(meta.get('procStart')) != stat[19]:
                meta = {}  # PID reuse must not attach an old conversation
            sid = meta.get('sessionId', '')
            log = None
            if re.fullmatch(r'[0-9a-f-]{36}', sid):
                project = re.sub(r'[^a-zA-Z0-9-]', '-', cwd)
                candidate = home / '.claude/projects' / project / f'{sid}.jsonl'
                if candidate.is_file():
                    log = candidate
            tail = records(log) if log else []
            state, tone = claude_state(tail, meta)
            topic = claude_topic((records(log, from_start=True) + tail) if log else [], meta)
            entries.append(dict(id=sid or f'pid-{pid}', tool='Claude Code', pid=pid, cwd=cwd,
                                topic=topic, state=state, tone=tone, source='CLI',
                                updated=log.stat().st_mtime if log else None))
        except (OSError, ValueError, IndexError):
            continue  # process may exit between reads
        if len(entries) >= MAX_SESSIONS:
            break
    for entry in entries:
        if entry['tone'] == 'busy' and entry['updated'] and time.time() - entry['updated'] > 120:
            entry['state'], entry['tone'] = '更新停滞・状態不明', 'unknown'
    entries.sort(key=lambda x: (x['tone'] != 'busy', -(x['updated'] or 0), x['id']))
    return dict(sessions=entries, time=time.time(), scan_ms=round((time.monotonic() - start) * 1000, 1),
                limited=len(entries) >= MAX_SESSIONS)


def codex_sessions():
    # The app server owns runtime status. Never substitute open files or CLI PIDs.
    node = os.environ.get('NODE') or shutil.which('node')
    if not node:
        return None
    try:
        result = subprocess.run([node, str(CODEX_SCRIPT)], capture_output=True, text=True, timeout=2)
        if result.returncode:
            return None
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def codex_entry(thread):
    status = thread['status']
    active = status in ('active', 'idle')
    closed = thread.get('archived') or status == 'notLoaded'
    flags = thread.get('flags', [])
    state = '入力待ち' if status == 'idle' else '作業中' if status == 'active' else '状態不明'
    if 'waitingOnApproval' in flags:
        state = '承認待ち'
    if 'waitingOnUserInput' in flags:
        state = '回答待ち'
    return dict(id=thread['id'], tool='Codex', pid=None, cwd=thread['cwd'], topic=thread['topic'],
                updated=thread['updated'], source='Codex直接照会',
                lifecycle='active' if active else 'closed' if closed else 'unknown',
                life_label='アクティブ' if active else 'アーカイブ' if thread.get('archived') else '閉じた会話' if closed else '確認できません',
                life_reason='Codexが会話を読み込み中' if active else 'Codexが会話を読み込んでいません' if closed else 'Codex側の状態を確認できません',
                state=state if not closed else '終了',
                tone='busy' if status == 'active' else 'idle' if status == 'idle' else 'unknown')


class Lifecycle:
    """Claude process lifecycle is observed in memory; Codex comes only from RPC.
    No durable mirror. Missing RPC preserves the last classification with an explicit stale timestamp.
    """
    def __init__(self):
        self.previous = {}
        self.codex_last_success = None

    def apply(self, data, proc=Path('/proc'), home=HOME, codex=None):
        current = {}
        for original in data['sessions']:
            entry = dict(original)
            key = (entry['tool'], entry['id'])
            entry['lifecycle'] = 'active'
            entry['life_label'] = 'アクティブ'
            entry['life_reason'] = '対応するClaude Codeのプロセスが起動中'
            try:
                entry['_start'] = (proc / str(entry['pid']) / 'stat').read_text().rsplit(')', 1)[1].split()[19]
            except (OSError, IndexError):
                entry['_start'] = None
                entry['lifecycle'], entry['life_label'] = 'unknown', '開閉不明'
            current[key] = entry
        for key, old in self.previous.items():
            if key in current or old['pid'] is None:
                continue
            entry = dict(old)
            # A live process can switch conversations without exiting. Only a
            # resolved new conversation in the same process proves the switch;
            # missing registry data (pid-* placeholder) is not evidence.
            replacement = next((e for e in current.values()
                                if e['tool'] == old['tool'] and e['pid'] == old['pid']
                                and e['lifecycle'] == 'active'
                                and not e['id'].startswith('pid-')
                                and e.get('_start') == old.get('_start')), None)
            if replacement:
                if old['id'].startswith('pid-'):
                    continue  # the previously unidentified process now has an ID
                entry.update(lifecycle='closed', life_label='会話切替を確認',
                             life_reason='同じClaude Codeプロセスが別の会話を読み込み中',
                             state='終了', tone='unknown')
                current[key] = entry
                continue
            path = proc / str(entry['pid'])
            try:
                fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
                ended = fields[0] == 'Z' or (entry.get('_start') is not None and fields[19] != entry['_start'])
            except FileNotFoundError:
                ended = not path.exists()
            except (OSError, IndexError):
                ended = False
            if ended:
                entry.update(lifecycle='closed', life_label='終了確認', life_reason='一覧で観測したプロセスの終了を確認',
                             state='終了', tone='unknown')
            elif entry['lifecycle'] != 'closed':
                entry.update(lifecycle='unknown', life_label='開閉不明', life_reason='対応する会話を現在確認できません',
                             state='状態不明', tone='unknown')
            current[key] = entry
        if codex is None and home == HOME and proc == Path('/proc'):
            codex = codex_sessions()
        data['codex_available'] = codex is not None
        if codex is not None:
            self.codex_last_success = time.time()
            for thread in codex['threads']:
                entry = codex_entry(thread)
                current[('Codex', entry['id'])] = entry
            data['limited'] = data['limited'] or codex.get('limited', False)
        else:
            for key, old in self.previous.items():
                if key[0] == 'Codex':
                    current[key] = dict(old)
        data['codex_last_success'] = self.codex_last_success
        values = sorted(current.values(),
                        key=lambda e: ({'active': 0, 'unknown': 1, 'closed': 2}[e['lifecycle']], -(e['updated'] or 0)))
        data['limited'] = data['limited'] or len(values) > MAX_SESSIONS
        self.previous = {(e['tool'], e['id']): e for e in values[:MAX_SESSIONS]}
        data['sessions'] = [{k: v for k, v in e.items() if not k.startswith('_')} for e in values[:MAX_SESSIONS]]
        return data


def selftest():
    import tempfile
    assert claude_topic([], {'name': 'auto-6a', 'nameSource': 'derived'}) == '話題を取得できません'
    channel = {'type': 'user', 'isMeta': True, 'message': {'content': '<channel source="chat">朝の通知項目を確認して</channel>'}}
    assert claude_topic([channel], {}) == '朝の通知項目を確認して'
    assert claude_topic([channel, {'type': 'custom-title', 'customTitle': '通知の改善'}], {}) == '通知の改善'
    assert claude_topic([{'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': '結果'}]}}], {}) == '話題を取得できません'
    assert claude_state([], {'status': 'idle', 'statusUpdatedAt': 0})[1] == 'unknown'
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        log = root / 'tail.jsonl'
        log.write_bytes(b'x' * (LIMIT + 100) + b'\n' + b'{"type":"event_msg","payload":{"type":"task_complete"}}\n{"partial":')
        assert records(log) == [{'type': 'event_msg', 'payload': {'type': 'task_complete'}}]
        proc = root / 'proc'
        proc.mkdir()
        p = proc / '123'
        p.mkdir()
        (p / 'comm').write_text('claude')
        fields = ['S'] + ['0'] * 18 + ['12345']
        (p / 'stat').write_text('123 (claude) ' + ' '.join(fields))
        (p / 'cwd').symlink_to(root)
        registry = root / '.claude/sessions'
        registry.mkdir(parents=True)
        (registry / '123.json').write_text(json.dumps({'procStart': 'WRONG', 'sessionId': 'a' * 36, 'status': 'idle',
                                                       'statusUpdatedAt': time.time() * 1000}))
        assert snapshot(proc, root)['sessions'][0]['id'] == 'pid-123'
        # A matching registry entry resolves the conversation and its topic.
        sid = '0123abcd-0000-0000-0000-000000000000'
        (registry / '123.json').write_text(json.dumps({'procStart': '12345', 'sessionId': sid}))
        project = root / '.claude/projects' / re.sub(r'[^a-zA-Z0-9-]', '-', str(root))
        project.mkdir(parents=True)
        (project / f'{sid}.jsonl').write_text(json.dumps({'type': 'user', 'message': {'content': 'READMEを直して'}}) + '\n')
        found = snapshot(proc, root)['sessions'][0]
        assert (found['id'], found['topic'], found['tone']) == (sid, 'READMEを直して', 'busy'), found
        (registry / '123.json').write_text(json.dumps({'procStart': 'WRONG', 'sessionId': sid}))
        tracker = Lifecycle()
        observation = dict(snapshot(proc, root)['sessions'][0])

        def observe(items):
            return tracker.apply(dict(sessions=items, limited=False), proc, root)['sessions']
        assert observe([observation])[0]['lifecycle'] == 'active'
        assert observe([])[0]['lifecycle'] != 'closed'  # missing log alone is insufficient
        (p / 'stat').write_text('123 (claude) ' + ' '.join(fields[:-1] + ['67890']))
        assert all(e['lifecycle'] == 'closed' for e in observe([]))  # PID reuse means old run ended
        assert observe([observation])[0]['lifecycle'] == 'active'  # resumed run returns to top
        resolved = dict(observation, id='conversation-a')
        assert len(observe([resolved])) == 1  # replace unresolved process placeholder
        switched = observe([dict(resolved, id='conversation-b')])
        assert {e['id']: e['lifecycle'] for e in switched} == {'conversation-a': 'closed', 'conversation-b': 'active'}
        assert next(e for e in observe([resolved]) if e['id'] == 'conversation-a')['lifecycle'] == 'active'
        example = dict(id='rpc', cwd='/tmp', topic='test', updated=1, status='idle')
        assert codex_entry(example)['lifecycle'] == 'active'
        assert codex_entry(dict(example, status='notLoaded'))['lifecycle'] == 'closed'
        assert codex_entry(dict(example, status='active', flags=['waitingOnApproval']))['state'] == '承認待ち'
        rpc_tracker = Lifecycle()

        def poll(result):
            return rpc_tracker.apply(dict(sessions=[], limited=False), proc, root, codex=result)['sessions']
        assert poll({'threads': [example]})[0]['lifecycle'] == 'active'
        saved = poll({'threads': [example]})
        confirmed = rpc_tracker.codex_last_success
        assert poll(None) == saved
        assert poll(None) == saved
        assert rpc_tracker.codex_last_success == confirmed
        assert poll({'threads': [dict(example, status='notLoaded')]})[0]['lifecycle'] == 'closed'
        assert poll({'threads': [example]})[0]['lifecycle'] == 'active'
        (p / 'comm').unlink()
        assert snapshot(proc, root)['sessions'] == []
    print('PASS: topic/state, bounded logs, Claude registry and PID reuse, conversation switch, Codex RPC idle/closed/reopen/disconnect')


def serve(open_browser=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, data, kind='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', kind + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy',
                             "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            # Reject DNS rebinding: only the exact loopback host:port is served.
            return self.headers.get('Host') == self.server.address

        def do_GET(self):
            if not self.allowed():
                return self.send(403, b'{}')
            if self.path == '/':
                self.server.last_seen = time.monotonic()
                return self.send(200, HTML.read_bytes(), 'text/html')
            if self.path == '/api/sessions':
                now = time.monotonic()
                self.server.last_seen = now
                if now - self.server.scanned >= 2:
                    self.server.data = json.dumps(lifecycle.apply(snapshot()), ensure_ascii=False).encode()
                    self.server.scanned = now
                return self.send(200, self.server.data)
            self.send(404, b'{}')

        def do_POST(self):
            if not self.allowed() or self.headers.get('Origin') != 'http://' + self.server.address:
                return self.send(403, b'{}')
            if self.path != '/stop':
                return self.send(404, b'{}')
            self.server.stopping = True
            self.send(200, b'{}')

    lifecycle = Lifecycle()
    server = HTTPServer(('127.0.0.1', 0), Handler)
    server.address = '127.0.0.1:' + str(server.server_port)
    server.timeout = 1
    server.last_seen = time.monotonic()
    server.scanned = 0
    server.stopping = False
    server.data = b'{}'
    url = 'http://' + server.address
    print(url, flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        while not server.stopping and time.monotonic() - server.last_seen < IDLE_EXIT_SEC:
            server.handle_request()
    finally:
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--open', action='store_true', help='既定のブラウザで開く')
    parser.add_argument('--selftest', action='store_true')
    parser.add_argument('--snapshot', action='store_true', help='1回分の一覧をJSONで出す')
    args = parser.parse_args()
    if args.selftest:
        selftest()
    elif args.snapshot:
        print(json.dumps(Lifecycle().apply(snapshot()), ensure_ascii=False))
    else:
        serve(open_browser=args.open)

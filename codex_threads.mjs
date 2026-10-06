// Codex owns thread lifecycle; process IDs and open rollout files are not sessions.
// Read-only local RPC to the Codex app-server: loaded/list + list + read. No resume, subscribe or turn calls.
// At most 128 loaded + 32 recent + 16 archived; 1.5 seconds; zero model/API cost.
// Invoked by agent_session_board.py per refresh; prints one JSON snapshot and exits.
import os from 'node:os';
import path from 'node:path';
import WebSocket from 'ws';

const socket = process.env.CODEX_APP_SERVER_SOCKET
  || path.join(os.homedir(), '.codex/app-server-control/app-server-control.sock');
const w = new WebSocket(`ws+unix://${socket}:/`, {headers: {Host: 'localhost'}, perMessageDeflate: false, maxPayload: 4 * 1024 * 1024});
let sequence = 0;
const pending = new Map();
const timer = setTimeout(() => { w.terminate(); process.exit(1); }, 1500);

function rpc(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, {resolve, reject});
    w.send(JSON.stringify({id, method, params}));
  });
}

w.on('message', raw => {
  const message = JSON.parse(raw);
  const p = pending.get(message.id);
  if (!p) return;
  pending.delete(message.id);
  if (message.error) p.reject(Error('Codex RPC unavailable'));
  else p.resolve(message.result);
});
w.on('error', () => { clearTimeout(timer); process.exitCode = 1; });
w.on('open', async () => {
  try {
    await rpc('initialize', {clientInfo: {name: 'agent_session_board', version: '1'}, capabilities: {experimentalApi: true, requestAttestation: false}});
    const [loaded, recent, archived] = await Promise.all([
      rpc('thread/loaded/list', {limit: 128}),
      rpc('thread/list', {limit: 32, sortKey: 'updated_at', useStateDbOnly: true}),
      rpc('thread/list', {limit: 16, archived: true, sortKey: 'updated_at', useStateDbOnly: true}),
    ]);
    const active = await Promise.all(loaded.data.slice(0, 128).map(threadId =>
      rpc('thread/read', {threadId, includeTurns: false}).then(r => r.thread)));
    const threads = new Map();
    for (const t of archived.data) threads.set(t.id, {...t, archived: true});
    for (const t of recent.data) threads.set(t.id, t);
    for (const t of active) threads.set(t.id, t);
    // Empty start screens have no persisted conversation; never count a TUI process.
    const data = [...threads.values()].filter(t => !t.parentThreadId && (t.name || t.preview)).map(t => ({
      id: t.id,
      topic: t.name || t.preview?.replace(/\s+/g, ' ').slice(0, 100) || 'タイトル未設定',
      cwd: t.cwd, updated: t.updatedAt, status: t.status.type, flags: t.status.activeFlags || [], archived: !!t.archived,
    }));
    console.log(JSON.stringify({threads: data, limited: !!loaded.nextCursor || !!recent.nextCursor || !!archived.nextCursor}));
  } catch {
    process.exitCode = 1;
  } finally {
    clearTimeout(timer);
    w.close();
  }
});

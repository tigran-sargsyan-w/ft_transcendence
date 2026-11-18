#!/usr/bin/env node
/**
 * Development-only terminal observer for the existing Nest Socket.IO protocol.
 * Must live under backend/ so the existing socket.io-client dependency resolves.
 * Does not create incidents or change the monitored infrastructure.
 */
import { io } from 'socket.io-client';
import { createInterface } from 'node:readline/promises';

const PREFIX = 'svc:ft-demo-lab:';
const DEFAULT_URL = 'https://transcendence.test:8443';

function options(argv) {
  const parsed = { url: DEFAULT_URL, email: null, analyze: false, all: false };
  for (let i = 0; i < argv.length; i += 1) {
    switch (argv[i]) {
      case '--url':
        if (!argv[i + 1]) throw new Error('--url requires a value');
        parsed.url = argv[++i];
        break;
      case '--email':
        if (!argv[i + 1]) throw new Error('--email requires a value');
        parsed.email = argv[++i];
        break;
      case '--analyze':
        parsed.analyze = true;
        break;
      case '--all':
        parsed.all = true;
        break;
      case '-h':
      case '--help':
        console.log(`Usage: node backend/scripts/demo-watch.mjs [--url ${DEFAULT_URL}] [--email user@example.com] [--analyze] [--all]\n\n` +
          'Logs in over HTTPS, then listens to authenticated Nest topology Socket.IO events.\n' +
          '--analyze requests real blast_radius + critical_nodes for degraded/down demo nodes.\n' +
          '--all displays updates of every topology node (default: lab nodes only).');
        process.exit(0);
      default:
        throw new Error(`Unknown option: ${argv[i]}`);
    }
  }
  const target = new URL(parsed.url);
  if (target.protocol !== 'https:') {
    throw new Error('Only HTTPS is accepted for account login; do not disable TLS checks.');
  }
  if (target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
    throw new Error('--url must be an HTTPS origin without credentials, path or query');
  }
  parsed.url = target.origin;
  return parsed;
}

async function askEmail() {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    return (await rl.question('Existing demo account email: ')).trim();
  } finally {
    rl.close();
  }
}

// No shell command line argument, environment variable, or displayed password.
function askHiddenPassword() {
  if (!process.stdin.isTTY || typeof process.stdin.setRawMode !== 'function') {
    throw new Error('Password input requires an interactive terminal.');
  }
  process.stdout.write('Password (not echoed): ');
  process.stdin.setRawMode(true);
  process.stdin.resume();
  return new Promise((resolve, reject) => {
    let value = '';
    const done = (error) => {
      process.stdin.off('data', onInput);
      process.stdin.setRawMode(false);
      process.stdin.pause();
      process.stdout.write('\n');
      if (error) reject(error);
      else resolve(value);
    };
    const onInput = (chunk) => {
      for (const char of chunk.toString('utf8')) {
        if (char === '\r' || char === '\n') {
          done(null);
          return;
        }
        if (char === '\u0003') {
          done(new Error('Cancelled'));
          return;
        }
        if (char === '\u007f' || char === '\b') {
          value = [...value].slice(0, -1).join('');
          continue;
        }
        if (char !== '\u001b' && char >= ' ') value += char;
      }
    };
    process.stdin.on('data', onInput);
  });
}

async function login(origin, email, password) {
  const response = await fetch(`${origin}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email, password }),
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(`Login HTTP ${response.status}: ${body?.error?.code ?? 'authentication_failed'}`);
  }
  const cookies = response.headers.getSetCookie();
  const cookie = cookies.find((entry) => entry.startsWith('sid='));
  const token = cookie?.match(/^sid=([^;]+)/)?.[1];
  if (!token) throw new Error('Login succeeded without the expected sid session cookie');
  return token;
}

function now() {
  return new Date().toISOString().slice(11, 19);
}

function report(line) {
  console.log(`${now()} ${line}`);
}

async function analyze(origin, sid, environmentId, seedId) {
  try {
    const response = await fetch(
      `${origin}/api/v1/environments/${encodeURIComponent(environmentId)}/analyses`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json', cookie: `sid=${sid}` },
        body: JSON.stringify({ analyses: ['blast_radius', 'critical_nodes'], seedNodeIds: [seedId] }),
        signal: AbortSignal.timeout(10000),
      },
    );
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.data) {
      report(`ANALYSIS ERROR HTTP ${response.status}: ${payload?.error?.code ?? 'unknown_error'}`);
      return;
    }
    const { freshness, topologyRevision, results } = payload.data;
    report(`ANALYSIS seed=${seedId} freshness=${freshness} revision=${topologyRevision}`);
    console.log(JSON.stringify({ blastRadius: results?.blastRadius, criticalNodes: results?.criticalNodes }, null, 2));
  } catch (error) {
    report(`ANALYSIS ERROR: ${error.message}`);
  }
}

async function main() {
  const opts = options(process.argv.slice(2));
  const email = opts.email ?? await askEmail();
  if (!email) throw new Error('Email cannot be empty');
  const password = await askHiddenPassword();
  if (!password) throw new Error('Password cannot be empty');
  const sid = await login(opts.url, email, password);
  report('Authenticated; connecting to Socket.IO (session cookie will not be displayed).');

  const nodes = new Map();
  let position = null;
  let environmentId = null;
  let resyncing = false;
  let analysisTasks = 0;
  const socket = io(opts.url, {
    transports: ['websocket'],
    extraHeaders: { cookie: `sid=${sid}` },
    reconnection: true,
    timeout: 10000,
    autoConnect: false,
  });
  const interesting = (id) => opts.all || id.startsWith(PREFIX);

  const requestResync = () => {
    if (!socket.connected || resyncing) return;
    resyncing = true;
    socket.timeout(5000).emit('topology:resync', {}, (err, reply) => {
      if (err || reply?.error) {
        report(`Resync refused: ${err?.message ?? reply.error.code}`);
        resyncing = false;
      }
    });
  };

  const maybeAnalyze = (id, previous, status) => {
    if (!opts.analyze || !id.startsWith(PREFIX) || !environmentId) return;
    if (previous === status || !['degraded', 'down'].includes(status)) return;
    // Keep analysis separate from the event stream; avoid blocking the socket handler.
    analysisTasks += 1;
    if (analysisTasks > 12) {
      report('Analysis limit reached for this watcher; topology continues normally.');
      return;
    }
    void analyze(opts.url, sid, environmentId, id);
  };

  socket.on('connect', () => {
    report('Socket connected; subscribing to topology.');
    position = null;
    resyncing = false;
    socket.timeout(5000).emit('topology:subscribe', {}, (err, reply) => {
      if (err || reply?.error) report(`Subscribe failed: ${err?.message ?? reply.error.code}`);
    });
  });
  socket.on('connect_error', (error) => {
    report(`Socket error: ${error.data?.code ?? error.message}`);
    if (['UNAUTHORIZED', 'FORBIDDEN'].includes(error.data?.code)) {
      socket.close();
      process.exitCode = 1;
    }
  });
  socket.on('disconnect', (reason) => report(`Socket disconnected: ${reason}`));

  socket.on('topology:snapshot', ({ payload }) => {
    if (!payload?.nodes || !Array.isArray(payload.nodes)) return;
    environmentId = payload.environmentId;
    position = { epoch: payload.epoch, revision: payload.revision };
    resyncing = false;
    nodes.clear();
    for (const node of payload.nodes) nodes.set(node.id, node);
    report(`SNAPSHOT environment=${environmentId} revision=${position.revision}`);
    let count = 0;
    for (const node of nodes.values()) {
      if (interesting(node.id)) {
        report(`  ${node.id}: ${node.status}`);
        count += 1;
      }
    }
    if (!count) report('  No ft-demo-lab service nodes yet; run ./demo-lab/demo.sh up.');
    for (const edge of payload.edges ?? []) {
      if (edge.kind === 'depends_on' && edge.source.startsWith(PREFIX) && edge.target.startsWith(PREFIX)) {
        report(`  DEPENDENCY ${edge.source} -> ${edge.target}`);
      }
    }
  });

  socket.on('topology:delta', ({ payload }) => {
    // A delta arriving before the first snapshot may already be included in it.
    if (!position) return;
    if (!payload || position.epoch !== payload.epoch || position.revision !== payload.baseRevision) {
      report('Gap/epoch mismatch in topology stream; requesting a full resync.');
      requestResync();
      return;
    }
    position.revision = payload.revision;
    for (const operation of payload.operations ?? []) {
      if (operation.op === 'node.upsert') {
        const node = operation.node;
        const old = nodes.get(node.id);
        nodes.set(node.id, node);
        if (interesting(node.id) && (!old || old.status !== node.status)) {
          report(`DELTA r${position.revision}: ${node.id} ${old?.status ?? 'new'} -> ${node.status}`);
          maybeAnalyze(node.id, old?.status, node.status);
        }
      } else if (operation.op === 'node.remove') {
        const old = nodes.get(operation.id);
        nodes.delete(operation.id);
        if (old && interesting(operation.id)) {
          report(`DELTA r${position.revision}: ${operation.id} removed`);
        }
      }
    }
  });

  socket.on('topology:status', ({ payload }) => {
    report(`COLLECTOR ${payload?.stale ? 'STALE' : 'CURRENT'}${payload?.since ? ` since=${payload.since}` : ''}`);
  });

  process.once('SIGINT', () => {
    socket.close();
    report('Stopped watcher.');
    process.exit(0);
  });
  socket.connect();
}

main().catch((error) => {
  console.error(`Demo watcher failed: ${error.message}`);
  process.exitCode = 1;
});

// GitHub API 客户端（token 从 token.txt 读取，绝不打印）
// 用法:
//   node tools/release/gh-api.mjs <api-path>            # 输出 JSON（大字段截断）
//   node tools/release/gh-api.mjs --logs <run-id> <out> # 下载 run 日志 zip
import fs from 'node:fs';
import http from 'node:http';
import tls from 'node:tls';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const TOKEN_FILE = path.join(ROOT, 'token.txt');
const OUT_FILE = path.join(ROOT, 'probe-reports', 'gh-out.json');
const PROXY = {
  host: process.env.GH_PROXY_HOST || '127.0.0.1',
  port: Number(process.env.GH_PROXY_PORT || 7897),
};

function token() {
  const t = fs.readFileSync(TOKEN_FILE, 'utf8').trim();
  if (!t) throw new Error('token.txt is empty');
  return t;
}

function decodeChunked(buf) {
  const parts = [];
  let pos = 0;
  while (pos < buf.length) {
    const lineEnd = buf.indexOf('\r\n', pos);
    if (lineEnd < 0) break;
    const size = parseInt(buf.slice(pos, lineEnd).toString('ascii').trim(), 16);
    if (!size || Number.isNaN(size)) break;
    const start = lineEnd + 2;
    parts.push(buf.slice(start, start + size));
    pos = start + size + 2;
  }
  return Buffer.concat(parts);
}

function request(host, pathname, extraHeaders = {}, method = 'GET', body = null) {
  return new Promise((resolve, reject) => {
    const headers = {
      'User-Agent': 'sandboxsensor-ci-probe',
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      ...extraHeaders,
    };
    const connectReq = http.request({
      host: PROXY.host,
      port: PROXY.port,
      method: 'CONNECT',
      path: `${host}:443`,
      timeout: 20000,
    });
    connectReq.on('connect', (res, socket) => {
      if (res.statusCode !== 200) {
        socket.destroy();
        return reject(new Error(`CONNECT ${res.statusCode}`));
      }
      const s = tls.connect({ socket, servername: host }, () => {
        const head = Object.entries(headers).map(([k, v]) => `${k}: ${v}`).join('\r\n');
        const bodyBuf = body == null ? null : Buffer.from(body, 'utf8');
        const bodyHead = bodyBuf ? `Content-Length: ${bodyBuf.length}\r\n` : '';
        s.write(
          `${method} ${pathname} HTTP/1.1\r\nHost: ${host}\r\n${head}\r\n${bodyHead}Connection: close\r\n\r\n`
        );
        if (bodyBuf) s.write(bodyBuf);
      });
      const chunks = [];
      s.on('data', (c) => chunks.push(c));
      s.on('end', () => {
        const buf = Buffer.concat(chunks);
        const idx = buf.indexOf('\r\n\r\n');
        const head = buf.slice(0, idx).toString('utf8');
        let body = buf.slice(idx + 4);
        if (/transfer-encoding:\s*chunked/i.test(head)) {
          body = decodeChunked(body);
        }
        const status = Number((head.match(/HTTP\/1\.\d (\d+)/) || [, '0'])[1]);
        const loc = (head.match(/^location:\s*(.+)$/im) || [, ''])[1].trim();
        resolve({ status, location: loc, body });
      });
      s.on('error', reject);
      s.setTimeout(30000, () => { s.destroy(); reject(new Error('tls timeout')); });
    });
    connectReq.on('error', reject);
    connectReq.on('timeout', () => { connectReq.destroy(); reject(new Error('proxy timeout')); });
    connectReq.end();
  });
}

async function apiGet(pathname, extraHeaders = {}) {
  const host = 'api.github.com';
  let p = pathname;
  for (let hop = 0; hop < 4; hop++) {
    const r = await request(host, p, { Authorization: `Bearer ${token()}`, ...extraHeaders });
    if (r.status >= 300 && r.status < 400 && r.location) {
      // 日志下载会跳到 S3 预签名 URL：此时不能再带 Authorization
      if (r.location.startsWith('https://')) {
        const u = new URL(r.location);
        const r2 = await request(u.hostname, u.pathname + u.search, {});
        return { status: r2.status, body: r2.body };
      }
      p = r.location;
      continue;
    }
    return { status: r.status, body: r.body };
  }
  throw new Error('too many redirects');
}

const args = process.argv.slice(2);

if (args[0] === '--dispatch') {
  // 手动触发 workflow_dispatch
  // 用法: node tools/release/gh-api.mjs --dispatch <workflow-file> [workflow-ref] [k=v ...]
  // 例:   node tools/release/gh-api.mjs --dispatch container-build.yml main ref=ec12b31
  const wf = args[1] || 'container-build.yml';
  const wfRef = args[2] || 'main';
  const inputs = {};
  // 支持两种写法：k=v（一个参数）或 k v（两个参数）
  for (let i = 3; i < args.length; i++) {
    const a = args[i];
    const eq = a.indexOf('=');
    if (eq > 0) {
      inputs[a.slice(0, eq)] = a.slice(eq + 1);
    } else if (i + 1 < args.length) {
      inputs[a] = args[++i];
    }
  }
  const payload = JSON.stringify({ ref: wfRef, inputs });
  const r = await request(
    'api.github.com',
    `/repos/wentureing/sandboxsensor/actions/workflows/${wf}/dispatches`,
    { Authorization: `Bearer ${token()}`, 'Content-Type': 'application/json' },
    'POST',
    payload
  );
  console.log(`dispatch ${wf} (workflow-ref=${wfRef}) inputs=${JSON.stringify(inputs)} -> HTTP ${r.status}`);
  if (r.status !== 204 && r.status !== 200) {
    console.error(r.body.toString('utf8').slice(0, 400));
    process.exit(1);
  }
} else if (args[0] === '--asset') {
  // 下载 apk-latest 里的某个 asset：node tools/release/gh-api.mjs --asset <name> [out]
  const name = args[1];
  const out = args[2] || name;
  if (!name) {
    console.error('usage: gh-api.mjs --asset <asset-name> [out-file]');
    process.exit(2);
  }
  const rel = await apiGet('/repos/wentureing/sandboxsensor/releases/tags/apk-latest');
  if (rel.status !== 200) {
    console.error(`get release failed: HTTP ${rel.status}`);
    process.exit(1);
  }
  const release = JSON.parse(rel.body.toString('utf8'));
  const asset = (release.assets || []).find((a) => a.name === name);
  if (!asset) {
    console.error(`asset not found: ${name}`);
    console.error('available: ' + (release.assets || []).map((a) => a.name).join(', '));
    process.exit(1);
  }
  const r = await apiGet(`/repos/wentureing/sandboxsensor/releases/assets/${asset.id}`, {
    Accept: 'application/octet-stream',
  });
  if (r.status !== 200) {
    console.error(`download failed: HTTP ${r.status}`);
    process.exit(1);
  }
  fs.mkdirSync(path.dirname(path.resolve(out)), { recursive: true });
  fs.writeFileSync(out, r.body);
  console.log(`saved ${out} (${r.body.length} bytes)`);
} else if (args[0] === '--logs') {
  const runId = args[1];
  const out = args[2] || `probe-reports/run-${runId}-logs.zip`;
  const r = await apiGet(`/repos/wentureing/sandboxsensor/actions/runs/${runId}/logs`);
  if (r.status !== 200) {
    console.error(`logs download failed: HTTP ${r.status}`);
    process.exit(1);
  }
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, r.body);
  console.log(`saved ${out} (${r.body.length} bytes)`);
} else {
  const p = args[0];
  if (!p) {
    console.error('usage: gh-api.mjs <api-path> | --logs <run-id> [out]');
    process.exit(2);
  }
  const r = await apiGet(p);
  if (r.status !== 200) {
    console.error(`HTTP ${r.status}`);
    console.error(r.body.toString('utf8').slice(0, 500));
    process.exit(1);
  }
  fs.mkdirSync(path.dirname(OUT_FILE), { recursive: true });
  fs.writeFileSync(OUT_FILE, r.body);
  console.log(`HTTP 200, ${r.body.length} bytes -> ${OUT_FILE}`);
}

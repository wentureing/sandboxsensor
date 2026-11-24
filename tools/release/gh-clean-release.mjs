// 清理 apk-latest Release 里的旧 assets：只保留最新的容器包与探针包。
//
// 用法:
//   node tools/release/gh-clean-release.mjs            # 干跑，只列出将要删除的
//   node tools/release/gh-clean-release.mjs --apply    # 真正删除
//
// 保留规则（按名字前缀取 mtime/size 最大者不可靠，直接按「最新构建的 SHA」）：
//   由调用方通过环境变量或 argv 指定要保留的完整文件名：
//   --keep container-shitjumpingvm-xxxx.apk --keep probe-yyyy-arm64-v8a.apk
//
// 默认保留策略：容器包保留「最新的一个」（按 Release asset 的 created_at），
// 探针包同理。可用 --keep 覆盖。
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
  // 2026-11-24：与 gh-api.mjs 保持一致改为 12450。
  //   7897 实测 3/3 ECONNRESET（TLS 握手前被断开），12450 3/3 HTTP 200。
  port: Number(process.env.GH_PROXY_PORT || 12450),
};
const OWNER = 'wentureing';
const REPO = 'sandboxsensor';

const argv = process.argv.slice(2);
const APPLY = argv.includes('--apply');
const KEEP = [];
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--keep' && argv[i + 1]) KEEP.push(argv[++i]);
}

// 按行读 token（token.txt 里可能有多个 PAT，见 gh-api.mjs 的完整说明）。
// 之前用 readFileSync(...).trim()，.trim() 不去中间换行，
// 用户加第二行后 Authorization 会变成 "Bearer <t1>\n<t2>"，认证必然失败。
function token() {
  const lines = fs.readFileSync(TOKEN_FILE, 'utf8')
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0 && !s.startsWith('#'));
  if (lines.length === 0) throw new Error('token.txt has no token');
  return lines[0];     // 清理 Release 属于常规 API，用第 1 行
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

function request(host, pathname, method, extraHeaders = {}) {
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
        s.write(
          `${method} ${pathname} HTTP/1.1\r\nHost: ${host}\r\n${head}\r\n` +
            `Content-Length: 0\r\nConnection: close\r\n\r\n`
        );
      });
      const chunks = [];
      s.on('data', (c) => chunks.push(c));
      s.on('end', () => {
        const buf = Buffer.concat(chunks);
        const idx = buf.indexOf('\r\n\r\n');
        const head = buf.slice(0, idx).toString('utf8');
        let body = buf.slice(idx + 4);
        if (/transfer-encoding:\s*chunked/i.test(head)) body = decodeChunked(body);
        const status = Number((head.match(/HTTP\/1\.\d (\d+)/) || [, '0'])[1]);
        resolve({ status, body });
      });
      s.on('error', reject);
      s.setTimeout(30000, () => { s.destroy(); reject(new Error('tls timeout')); });
    });
    connectReq.on('error', reject);
    connectReq.on('timeout', () => { connectReq.destroy(); reject(new Error('proxy timeout')); });
    connectReq.end();
  });
}

async function api(method, pathname) {
  return request('api.github.com', pathname, method, {
    Authorization: `Bearer ${token()}`,
  });
}

const rel = await api('GET', `/repos/${OWNER}/${REPO}/releases/tags/apk-latest`);
if (rel.status !== 200) {
  console.error(`get release failed: HTTP ${rel.status}`);
  process.exit(1);
}
const release = JSON.parse(rel.body.toString('utf8'));
const assets = release.assets || [];
console.log(`release apk-latest has ${assets.length} assets`);

// 决定保留集合
let keepSet = new Set(KEEP);
if (keepSet.size === 0) {
  // 默认：容器 / 探针 各保留 created_at 最新的一个
  const pick = (pred) =>
    assets
      .filter((a) => pred(a.name))
      .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))[0];
  const c = pick((n) => n.startsWith('container-'));
  const p = pick((n) => n.startsWith('probe-'));
  if (c) keepSet.add(c.name);
  if (p) keepSet.add(p.name);
}

console.log('keep:');
for (const k of keepSet) console.log(`  KEEP ${k}`);

const toDelete = assets.filter((a) => !keepSet.has(a.name));
console.log(`to delete: ${toDelete.length}`);
for (const a of toDelete) {
  console.log(`  DEL  ${a.name}  (${a.size} B, id=${a.id})`);
}

if (!APPLY) {
  console.log('\n[dry-run] 加 --apply 才会真正删除');
  process.exit(0);
}

for (const a of toDelete) {
  const r = await api('DELETE', `/repos/${OWNER}/${REPO}/releases/assets/${a.id}`);
  console.log(`  DELETE ${a.name} -> HTTP ${r.status}`);
  if (r.status !== 204 && r.status !== 200) {
    console.error(`    body: ${r.body.toString('utf8').slice(0, 200)}`);
  }
}

console.log('\ndone');

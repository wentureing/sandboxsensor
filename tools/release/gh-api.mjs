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
  // 默认走代理（用户 2026-11-27：速度无异常，统一走 12450）；直连可用 GH_PROXY_MODE=direct 显式切换。
  enabled: (process.env.GH_PROXY_MODE || 'proxy') !== 'direct',
  host: process.env.GH_PROXY_HOST || '127.0.0.1',
  port: Number(process.env.GH_PROXY_PORT || 12450),
};

/**
 * token.txt 的存储约定（2026-11-24 定）
 *
 * 每行一个 PAT，按"用途"分配行序（1-based），空行和 # 开头的注释行会被跳过：
 *
 *   第 1 行：默认 token —— 常规 API
 *            列 runs / 查 release / dispatch workflow / 删 cache / 查元数据
 *   第 2 行：内容读取 token —— 大响应
 *            下日志包（--logs）、下 Release asset（--asset）
 *            和默认 token 分开，两个 PAT 各 5000/h，互不挤占配额
 *
 * 为什么必须按行读：之前实现是 readFileSync(...).trim()，
 * .trim() 只去首尾空白，中间的换行会保留 —— 用户加第二行之后，
 * Authorization 头会变成 "Bearer <token1>\n<token2>"，认证必然失败。
 *
 * 覆盖方式：
 *   GH_TOKEN_N        直接用第 N 行（1-based），优先于用途映射
 *   GH_TOKEN_PURPOSE  'default' | 'content'，默认 'default'
 */
function tokenLines() {
  const raw = fs.readFileSync(TOKEN_FILE, 'utf8');
  return raw
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0 && !s.startsWith('#'));
}

function token(purpose = process.env.GH_TOKEN_PURPOSE || 'default') {
  const lines = tokenLines();
  if (lines.length === 0) throw new Error('token.txt has no token (empty or all comments)');

  const forced = Number(process.env.GH_TOKEN_N || 0);
  if (forced > 0) {
    const t = lines[forced - 1];
    if (!t) throw new Error(`GH_TOKEN_N=${forced} but token.txt only has ${lines.length} tokens`);
    return t;
  }

  // 用途 -> 行索引（0-based）。缺行时回退到第 1 行，保证单 token 也能用。
  const byPurpose = { default: 0, content: 1 };
  const idx = Object.prototype.hasOwnProperty.call(byPurpose, purpose) ? byPurpose[purpose] : 0;
  return lines[Math.min(idx, lines.length - 1)];
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

function request(host, pathname, extraHeaders = {}, method = 'GET', body = null, opts = {}) {
  // 2026-11-27：加"总超时"与进度输出。
  // 之前的坑：只有 CONNECT(20s)/TLS 空闲(30s) 超时，**响应体没有总超时** → 下载中途半开连接
  // 会让脚本永久挂住（宿主只能强杀）。大文件必须给 totalTimeoutMs，并且失败要自己报错退出。
  const totalTimeoutMs = Number(opts.totalTimeoutMs || 60000);
  const showProgress = !!opts.progress;
  return new Promise((resolve, reject) => {
    let settled = false;
    let received = 0;
    let lastTick = 0;
    const finish = (fn, v) => {
      if (settled) return;
      settled = true;
      clearTimeout(totalTimer);
      fn(v);
    };
    const totalTimer = setTimeout(() => {
      console.error(`[total timeout ${totalTimeoutMs}ms] ${host}${pathname} (received=${received} bytes)`);
      finish(reject, new Error(`total timeout ${totalTimeoutMs}ms`));
    }, totalTimeoutMs);

    const headers = {
      'User-Agent': 'sandboxsensor-ci-probe',
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      ...extraHeaders,
    };
    const sendRequest = (s) => {
      const head = Object.entries(headers).map(([k, v]) => `${k}: ${v}`).join('\r\n');
      const bodyBuf = body == null ? null : Buffer.from(body, 'utf8');
      const bodyHead = bodyBuf ? `Content-Length: ${bodyBuf.length}\r\n` : '';
      s.write(
        `${method} ${pathname} HTTP/1.1\r\nHost: ${host}\r\n${head}\r\n${bodyHead}Connection: close\r\n\r\n`
      );
      if (bodyBuf) s.write(bodyBuf);
    };

    const attach = (s) => {
      const chunks = [];
      // 兜底：对端在 Connection: close 之后发 RST 会触发 ECONNRESET；
      // finish() 已 settled 时它只是噪音，不能让它变成 unhandled error 把进程带崩。
      s.on('error', () => {});
      s.on('data', (c) => {
        chunks.push(c);
        received += c.length;
        if (showProgress && received - lastTick >= 4 * 1024 * 1024) {
          lastTick = received;
          console.error(`[dl] ${Math.round(received / 1024)} KiB`);
        }
      });
      s.on('end', () => {
        const buf = Buffer.concat(chunks);
        const idx = buf.indexOf('\r\n\r\n');
        const head = buf.slice(0, idx).toString('utf8');
        let bodyBuf2 = buf.slice(idx + 4);
        if (/transfer-encoding:\s*chunked/i.test(head)) {
          bodyBuf2 = decodeChunked(bodyBuf2);
        }
        const status = Number((head.match(/HTTP\/1\.\d (\d+)/) || [, '0'])[1]);
        const loc = (head.match(/^location:\s*(.+)$/im) || [, ''])[1].trim();
        finish(resolve, { status, location: loc, body: bodyBuf2 });
      });
      s.setTimeout(30000, () => {
        console.error(`[tls idle timeout 30s] ${host}${pathname} (received=${received} bytes)`);
        s.destroy();
        finish(reject, new Error('tls idle timeout'));
      });
      sendRequest(s);
    };

    // 直连（默认）：实测 1588 KiB/s，比 12450 代理快 7.7 倍
    if (!PROXY.enabled) {
      const s = tls.connect({ host, port: 443, servername: host }, () => attach(s));
      s.on('error', (e) => {
        console.error(`[tls-direct] ${host}${pathname}: ${e.code || ''} ${e.message}`);
        finish(reject, e);
      });
      return;
    }

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
        return finish(reject, new Error(`CONNECT ${res.statusCode}`));
      }
      const s = tls.connect({ socket, servername: host }, () => attach(s));
      s.on('error', (e) => {
        console.error(`[tls->github] ${host}${pathname}: ${e.code || ''} ${e.message}`);
        finish(reject, e);
      });
    });
    connectReq.on('error', (e) => {
      console.error(`[connect->proxy] ${PROXY.host}:${PROXY.port}: ${e.code || ''} ${e.message}`);
      finish(reject, e);
    });
    connectReq.on('timeout', () => {
      console.error(`[proxy timeout 20s] ${PROXY.host}:${PROXY.port}`);
      connectReq.destroy();
      finish(reject, new Error('proxy timeout'));
    });
    connectReq.end();
  });
}

async function apiGet(pathname, extraHeaders = {}, purpose = 'default', opts = {}) {
  const host = 'api.github.com';
  let p = pathname;
  for (let hop = 0; hop < 4; hop++) {
    const r = await request(host, p, { Authorization: `Bearer ${token(purpose)}`, ...extraHeaders }, 'GET', null, opts);
    if (r.status >= 300 && r.status < 400 && r.location) {
      // 日志下载会跳到 S3 预签名 URL：此时不能再带 Authorization
      if (r.location.startsWith('https://')) {
        const u = new URL(r.location);
        const r2 = await request(u.hostname, u.pathname + u.search, {}, 'GET', null, opts);
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
} else if (args[0] === '--cancel') {
  // 取消正在跑的 run：node tools/release/gh-api.mjs --cancel <run-id>
  // 用途：代码/配置已更新后，立即停掉旧 run 再触发新的（用户 2026-11-25 允许）。
  const runId = args[1];
  if (!runId) {
    console.error('usage: gh-api.mjs --cancel <run-id>');
    process.exit(2);
  }
  const r = await request(
    'api.github.com',
    `/repos/wentureing/sandboxsensor/actions/runs/${runId}/cancel`,
    { Authorization: `Bearer ${token()}`, 'Content-Type': 'application/json' },
    'POST',
    ''
  );
  console.log(`cancel run ${runId} -> HTTP ${r.status}`);
  if (r.status !== 202 && r.status !== 204) {
    console.error(r.body.toString('utf8').slice(0, 300));
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
  // 下载方式（2026-11-27 用户实测）：**浏览器点 releases 页面能下、脚本不能** → 用浏览器式 URL +
  // 浏览器 headers（UA/Referer/Accept-Language），避开 `api.github.com/.../assets/<id>` 这条
  // API 下载路径（易被判风控/限速）。302 后再跟随到对象存储。
  const tag = release.tag_name || 'apk-latest';
  const htmlPath =
    `/wentureing/sandboxsensor/releases/download/${tag}/${encodeURIComponent(name)}`;
  const browserHeaders = {
    'User-Agent':
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    Referer: `https://github.com/wentureing/sandboxsensor/releases/tag/${tag}`,
  };
  const opts = { totalTimeoutMs: 150000, progress: true };
  let r = await request('github.com', htmlPath, browserHeaders, 'GET', null, opts);
  for (let hop = 0; hop < 4 && r.status >= 300 && r.status < 400 && r.location; hop++) {
    const u = new URL(r.location);
    r = await request(
      u.hostname,
      u.pathname + u.search,
      { 'User-Agent': browserHeaders['User-Agent'], Accept: '*/*' },
      'GET',
      null,
      opts,
    );
  }
  if (r.status !== 200) {
    console.error(`download failed: HTTP ${r.status} (url=${htmlPath})`);
    process.exit(1);
  }
  fs.mkdirSync(path.dirname(path.resolve(out)), { recursive: true });
  fs.writeFileSync(out, r.body);
  console.log(`saved ${out} (${r.body.length} bytes) via releases/download/${tag}`);
} else if (args[0] === '--logs') {
  const runId = args[1];
  const out = args[2] || `probe-reports/run-${runId}-logs.zip`;
  const r = await apiGet(
    `/repos/wentureing/sandboxsensor/actions/runs/${runId}/logs`,
    {},
    'content',
    { totalTimeoutMs: 150000, progress: true },
  );
  if (r.status !== 200) {
    console.error(`logs download failed: HTTP ${r.status}`);
    process.exit(1);
  }
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, r.body);
  console.log(`saved ${out} (${r.body.length} bytes)`);
} else if (args[0] === '--delete-cache') {
  // 删 Actions 缓存：node tools/release/gh-api.mjs --delete-cache <cache-id> [...more]
  // 用法场景：CACHE_EPOCH 统一后，旧 epoch 的缓存没人用但继续占配额。
  const ids = args.slice(1).filter(Boolean);
  if (ids.length === 0) {
    console.error('usage: gh-api.mjs --delete-cache <cache-id> [<cache-id> ...]');
    process.exit(2);
  }
  let failed = 0;
  for (const id of ids) {
    const r = await request(
      'api.github.com',
      `/repos/wentureing/sandboxsensor/actions/caches/${id}`,
      { Authorization: `Bearer ${token()}` },
      'DELETE'
    );
    if (r.status === 204 || r.status === 200) {
      console.log(`DELETED cache id=${id} -> HTTP ${r.status}`);
    } else {
      failed++;
      console.error(`DELETE cache id=${id} failed: HTTP ${r.status} ${r.body.toString('utf8').slice(0, 200)}`);
    }
  }
  if (failed > 0) process.exit(1);
} else if (args[0] === '--delete-asset') {
  // 删 apk-latest 里的旧产物：node tools/release/gh-api.mjs --delete-asset <asset-id> [...more]
  // 用法场景：每次编译都往 apk-latest 追加一个 22MB 的 APK，只留最新版本。
  // 注意：删了要回滚旧版本就得重新编译（产物可由 CI 重建，所以是安全的清理）。
  const ids = args.slice(1).filter(Boolean);
  if (ids.length === 0) {
    console.error('usage: gh-api.mjs --delete-asset <asset-id> [<asset-id> ...]');
    process.exit(2);
  }
  let failed = 0;
  for (const id of ids) {
    const r = await request(
      'api.github.com',
      `/repos/wentureing/sandboxsensor/releases/assets/${id}`,
      { Authorization: `Bearer ${token()}` },
      'DELETE'
    );
    if (r.status === 204 || r.status === 200) {
      console.log(`DELETED asset id=${id} -> HTTP ${r.status}`);
    } else {
      failed++;
      console.error(`DELETE asset id=${id} failed: HTTP ${r.status} ${r.body.toString('utf8').slice(0, 200)}`);
    }
  }
  if (failed > 0) process.exit(1);
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


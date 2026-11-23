// 通过 HTTP CONNECT 代理抓取 AOSP 官方文档并保存本地副本
// 用法: node fetch-aosp.mjs [proxyHost:port]
import http from 'node:http';
import tls from 'node:tls';
import fs from 'node:fs';
import path from 'node:path';

const proxyArg = process.argv[2] || '127.0.0.1:12450';
const [proxyHost, proxyPort] = proxyArg.split(':');
const outDir = path.resolve('_ref/aosp/raw');
fs.mkdirSync(outDir, { recursive: true });

const custom = process.argv[3];
const targets = custom
  ? custom.split(',').map((s) => {
      const i = s.indexOf('=');
      return [s.slice(0, i), s.slice(i + 1)];
    })
  : [
  ['page-sizes-16kb', 'https://developer.android.com/guide/practices/page-sizes'],
  ['non-sdk-restrictions', 'https://developer.android.com/guide/app-compatibility/restrictions-non-sdk-interfaces'],
  ['non-sdk-16', 'https://developer.android.com/about/versions/16/changes/non-sdk-16'],
  ['app-sandbox', 'https://source.android.com/docs/security/app-sandbox'],
  ['a14-behavior-all', 'https://developer.android.com/about/versions/14/behavior-changes-all'],
  ['a14-behavior-14', 'https://developer.android.com/about/versions/14/behavior-changes-14'],
  ['a15-behavior-all', 'https://developer.android.com/about/versions/15/behavior-changes-all'],
  ['a15-behavior-15', 'https://developer.android.com/about/versions/15/behavior-changes-15'],
  ['a16-behavior-all', 'https://developer.android.com/about/versions/16/behavior-changes-all'],
  ['a16-behavior-16', 'https://developer.android.com/about/versions/16/behavior-changes-16'],
  ['bal-restrictions', 'https://developer.android.com/guide/components/activities/background-starts'],
];

function viaProxy(url) {
  return new Promise((resolve) => {
    const u = new URL(url);
    const port = u.port || 443;
    const req = http.request({
      host: proxyHost,
      port: Number(proxyPort),
      method: 'CONNECT',
      path: `${u.hostname}:${port}`,
      timeout: 25000,
    });
    req.on('connect', (res, socket) => {
      if (res.statusCode !== 200) {
        socket.destroy();
        return resolve({ ok: false, status: `CONNECT ${res.statusCode}` });
      }
      const s = tls.connect({ socket, servername: u.hostname }, () => {
        s.write(
          `GET ${u.pathname}${u.search} HTTP/1.1\r\n` +
            `Host: ${u.hostname}\r\n` +
            'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36\r\n' +
            'Accept: text/html,application/xhtml+xml\r\n' +
            'Accept-Encoding: identity\r\n' +
            'Connection: close\r\n\r\n'
        );
      });
      const chunks = [];
      s.on('data', (c) => chunks.push(c));
      s.on('end', () => {
        const buf = Buffer.concat(chunks);
        const text = buf.toString('utf8');
        const idx = text.indexOf('\r\n\r\n');
        const head = text.slice(0, idx);
        const body = text.slice(idx + 4);
        const status = (head.match(/HTTP\/1\.\d (\d+)/) || [, '0'])[1];
        const loc = (head.match(/^location:\s*(.+)$/im) || [, ''])[1].trim();
        resolve({ ok: status === '200', status, body, loc });
      });
      s.on('error', (e) => resolve({ ok: false, status: 'TLS ' + e.code }));
      s.setTimeout(30000, () => { s.destroy(); resolve({ ok: false, status: 'timeout' }); });
    });
    req.on('error', (e) => resolve({ ok: false, status: 'proxy ' + e.code }));
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, status: 'proxy timeout' }); });
    req.end();
  });
}

let okCount = 0;
for (const [name, url] of targets) {
  let cur = url;
  let r = await viaProxy(cur);
  for (let hop = 0; hop < 5 && r.status && /^30[12378]$/.test(r.status) && r.loc; hop++) {
    const next = new URL(r.loc, cur).toString();
    console.log(`     -> ${r.status} redirect ${next}`);
    cur = next;
    r = await viaProxy(cur);
  }
  if (r.ok && r.body && r.body.length > 5000) {
    fs.writeFileSync(path.join(outDir, `${name}.html`), r.body, 'utf8');
    const title = (r.body.match(/<title>([\s\S]{0,120}?)<\/title>/i) || [, '?'])[1].replace(/\s+/g, ' ').trim();
    console.log(`OK   ${name.padEnd(24)} ${String(r.body.length).padStart(8)} bytes  ${title}`);
    okCount++;
  } else {
    console.log(`FAIL ${name.padEnd(24)} status=${r.status} loc=${r.loc || ''}`);
  }
}
console.log(`\n代理 ${proxyArg}: 成功 ${okCount}/${targets.length}`);

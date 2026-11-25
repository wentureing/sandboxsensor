// 临时校验：用与 path_rules.cpp 相同的算法跑 TSV 向量，确认向量与算法一致。
// 目的只是省一轮 CI；不进入仓库逻辑。
import fs from 'node:fs';

const tsv = process.argv[2];
const sandbox = '/sandbox/i1';
const pkg = 'mark.via';

const startsWith = (s, p) => s.length >= p.length && s.startsWith(p);

function redirect(rules, path) {
  if (!path || rules.size === 0) return '';
  let bestFrom = '', bestTo = '', bestLen = 0, bestIsRoot = false;
  for (const [from, to] of rules) {
    const isPrefix = startsWith(path, from);
    const isRoot = !isPrefix && from.endsWith('/') && from.length === path.length + 1 && from.startsWith(path);
    if (!isPrefix && !isRoot) continue;
    const len = isRoot ? path.length : from.length;
    const better = len > bestLen || (len === bestLen && bestIsRoot && !isRoot);
    if (!better) continue;
    bestLen = len;
    bestFrom = isPrefix ? from : path;
    bestTo = isRoot ? to.replace(/\/+$/, '') : to;
    bestIsRoot = isRoot;
  }
  if (!bestFrom) return '';
  const rest = path.slice(bestFrom.length);
  if (!rest) return bestTo;
  const toSlash = bestTo.endsWith('/');
  const restSlash = rest.startsWith('/');
  if (toSlash && restSlash) return bestTo + rest.slice(1);
  if (toSlash) return bestTo + rest;
  if (restSlash) return bestTo + rest;
  return bestTo + '/' + rest;
}

function set(name) {
  const rules = new Map();
  const hidden = new Set();
  if (name === 'app_i1') {
    const ext = `${sandbox}/sdcard/Android/data/${pkg}`;
    rules.set(`/data/data/${pkg}/`, `${sandbox}/`);
    rules.set(`/data/user/0/${pkg}/`, `${sandbox}/`);
    rules.set(`/storage/emulated/0/Android/data/${pkg}/`, `${ext}/`);
    rules.set(`/sdcard/Android/data/${pkg}/`, `${ext}/`);
    rules.set(`/storage/emulated/0/Android/data/${pkg}`, ext);
    rules.set(`/sdcard/Android/data/${pkg}`, ext);
  } else if (name === 'external') {
    const root = `${sandbox}/sdcard`;
    for (const p of ['/sdcard/', '/storage/emulated/0/', '/mnt/sdcard/', '/storage/self/primary/']) rules.set(p, `${root}/`);
    rules.set('/storage/emulated/0', root);
    rules.set('/sdcard', root);
  } else if (name === 'hidden_su') {
    hidden.add('/system/bin/su');
    rules.set('/system/', `${sandbox}/system/`);
  }
  return { rules, hidden };
}

let total = 0, fail = 0;
for (const line of fs.readFileSync(tsv, 'utf8').split(/\r?\n/)) {
  if (!line || line.startsWith('#')) continue;
  const c = line.split('\t');
  if (c.length < 4) continue;
  const [id, setName, path] = c;
  let expected = c[3] === '-' ? path : c[3].replaceAll('<sandbox>', sandbox);
  const { rules, hidden } = set(setName);
  const actual = hidden.has(path) ? '/dev/null' : (redirect(rules, path) || path);
  total++;
  if (actual !== expected) {
    fail++;
    console.log(`FAIL ${id} ${setName} ${path}\n  期望 ${expected}\n  实际 ${actual}`);
  }
}
console.log(`vectors: ${total - fail}/${total} 一致`);
process.exit(fail ? 1 : 0);

// 同步结构整理：docs/代码镜像/core/virtualization/lifecycle/diag/* -> diag/{dump,probe,trace,suppress}/*
// 并把文档内容里的旧路径替换成新路径。
import fs from 'node:fs';
import path from 'node:path';

const OLD = 'docs/代码镜像/core/virtualization/lifecycle/diag';
const NEW = 'docs/代码镜像/core/virtualization/diag';

const MOVES = [
  ['GuestMemoryDump.md', 'dump/GuestMemoryDump.md'],
  ['MapsSnapshot.md', 'dump/MapsSnapshot.md'],
  ['InitTrace.md', 'trace/InitTrace.md'],
  ['PluginProbes.md', 'probe/PluginProbes.md'],
];

for (const [from, to] of MOVES) {
  const src = path.join(OLD, from);
  const dst = path.join(NEW, to);
  if (!fs.existsSync(src)) { console.log(`skip ${src}`); continue; }
  fs.mkdirSync(path.dirname(dst), { recursive: true });
  fs.writeFileSync(dst, fs.readFileSync(src, 'utf8'));
  fs.unlinkSync(src);
  console.log(`moved ${from} -> ${to}`);
}

// 替换 docs 与 代码里的旧路径字符串
const REPLACEMENTS = [
  ['com/nextvm/core/virtualization/lifecycle/diag/GuestMemoryDump', 'com/nextvm/core/virtualization/diag/dump/GuestMemoryDump'],
  ['com/nextvm/core/virtualization/lifecycle/diag/MapsSnapshot', 'com/nextvm/core/virtualization/diag/dump/MapsSnapshot'],
  ['com/nextvm/core/virtualization/lifecycle/diag/InitTrace', 'com/nextvm/core/virtualization/diag/trace/InitTrace'],
  ['com/nextvm/core/virtualization/lifecycle/diag/PluginProbes', 'com/nextvm/core/virtualization/diag/probe/PluginProbes'],
  ['com/nextvm/core/virtualization/lifecycle/diag/HijackProbes', 'com/nextvm/core/virtualization/diag/probe/HijackProbes'],
  ['com/nextvm/core/virtualization/lifecycle/diag/AutofillProbes', 'com/nextvm/core/virtualization/diag/suppress/AutofillSuppressor'],
  ['代码镜像/core/virtualization/lifecycle/diag/', '代码镜像/core/virtualization/diag/'],
];

function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.name.endsWith('.md') || e.name.endsWith('.kt')) out.push(p);
  }
  return out;
}

let changed = 0;
for (const f of [...walk('docs'), ...walk('core/virtualization/src')]) {
  let text = fs.readFileSync(f, 'utf8');
  let next = text;
  for (const [a, b] of REPLACEMENTS) next = next.split(a).join(b);
  if (next !== text) { fs.writeFileSync(f, next); changed++; }
}
console.log(`paths updated in ${changed} files`);

try {
  if (fs.existsSync(OLD) && fs.readdirSync(OLD).length === 0) fs.rmdirSync(OLD);
} catch (_) {}

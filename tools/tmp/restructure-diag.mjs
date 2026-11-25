// 结构整理：lifecycle/diag/* -> diag/{dump,probe,trace,suppress}/*，并改名 AutofillProbes -> AutofillSuppressor。
// 只做机械移动 + package/import 替换，不改逻辑。
import fs from 'node:fs';
import path from 'node:path';

const ROOT = 'core/virtualization/src';
const OLD = path.join(ROOT, 'main/java/com/nextvm/core/virtualization/lifecycle/diag');
const NEW = path.join(ROOT, 'main/java/com/nextvm/core/virtualization/diag');
const TEST_OLD = path.join(ROOT, 'test/java/com/nextvm/core/virtualization/lifecycle/diag');
const TEST_NEW = path.join(ROOT, 'test/java/com/nextvm/core/virtualization/diag');

const MOVES = [
  ['GuestMemoryDump.kt', 'dump/GuestMemoryDump.kt', 'dump'],
  ['MapsSnapshot.kt', 'dump/MapsSnapshot.kt', 'dump'],
  ['InitTrace.kt', 'trace/InitTrace.kt', 'trace'],
  ['PluginProbes.kt', 'probe/PluginProbes.kt', 'probe'],
  ['HijackProbes.kt', 'probe/HijackProbes.kt', 'probe'],
  ['AutofillProbes.kt', 'suppress/AutofillSuppressor.kt', 'suppress'],
];

const TEST_MOVES = [
  ['GuestMemoryDumpTest.kt', 'dump/GuestMemoryDumpTest.kt', 'dump'],
  ['MapsSnapshotTest.kt', 'dump/MapsSnapshotTest.kt', 'dump'],
  ['InitTraceTest.kt', 'trace/InitTraceTest.kt', 'trace'],
  ['PluginProbesTest.kt', 'probe/PluginProbesTest.kt', 'probe'],
];

const IMPORT_REPLACEMENTS = [
  ['com.nextvm.core.virtualization.lifecycle.diag.GuestMemoryDump',
    'com.nextvm.core.virtualization.diag.dump.GuestMemoryDump'],
  ['com.nextvm.core.virtualization.lifecycle.diag.MapsSnapshot',
    'com.nextvm.core.virtualization.diag.dump.MapsSnapshot'],
  ['com.nextvm.core.virtualization.lifecycle.diag.RealSnap',
    'com.nextvm.core.virtualization.diag.dump.RealSnap'],
  ['com.nextvm.core.virtualization.lifecycle.diag.InitTrace',
    'com.nextvm.core.virtualization.diag.trace.InitTrace'],
  ['com.nextvm.core.virtualization.lifecycle.diag.PluginProbes',
    'com.nextvm.core.virtualization.diag.probe.PluginProbes'],
  ['com.nextvm.core.virtualization.lifecycle.diag.HijackProbes',
    'com.nextvm.core.virtualization.diag.probe.HijackProbes'],
  ['com.nextvm.core.virtualization.lifecycle.diag.AutofillProbes',
    'com.nextvm.core.virtualization.diag.suppress.AutofillSuppressor'],
];

function move(srcDir, dstDir, moves) {
  for (const [from, to, sub] of moves) {
    const src = path.join(srcDir, from);
    const dst = path.join(dstDir, to);
    if (!fs.existsSync(src)) {
      console.log(`skip (missing): ${src}`);
      continue;
    }
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    let text = fs.readFileSync(src, 'utf8');
    // package 行
    text = text.replace(
      /^package [\w.]+$/m,
      sub === 'suppress'
        ? 'package com.nextvm.core.virtualization.diag.suppress'
        : `package com.nextvm.core.virtualization.diag.${sub}`
    );
    // 类/对象改名
    if (from === 'AutofillProbes.kt') {
      text = text.replace(/\bAutofillProbes\b/g, 'AutofillSuppressor');
    }
    fs.writeFileSync(dst, text);
    fs.unlinkSync(src);
    console.log(`moved ${from} -> ${to}`);
  }
}

move(OLD, NEW, MOVES);
move(TEST_OLD, TEST_NEW, TEST_MOVES);

// 全局 import 替换（main + test）
function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.name.endsWith('.kt')) out.push(p);
  }
  return out;
}

let changed = 0;
for (const f of walk(ROOT)) {
  let text = fs.readFileSync(f, 'utf8');
  let next = text;
  for (const [from, to] of IMPORT_REPLACEMENTS) next = next.split(from).join(to);
  if (next !== text) {
    fs.writeFileSync(f, next);
    changed++;
  }
}
console.log(`imports updated in ${changed} files`);

// 清理空目录
for (const d of [OLD, TEST_OLD]) {
  try {
    if (fs.existsSync(d) && fs.readdirSync(d).length === 0) fs.rmdirSync(d);
  } catch (_) {}
}

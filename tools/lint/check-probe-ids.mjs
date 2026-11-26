#!/usr/bin/env node
// 探针「项集合契约」检查（毫秒级，不需要设备 / Gradle）。
//
// 为什么需要：2026-11-26 实测同一个 suite 跑出 98 项和 94 项（P24–P27 静默消失），
// 两次都判绿。拆 ProbeRunner 之前必须先有「项集合不许变」的契约，否则拆的过程丢项没人发现。
//
// 覆盖三处注册点：
//   probe/src/main/java/**.java   put("J01", ...) / putSkipped(...) / 固定表 {"P24", "..."}
//   probe/src/main/cpp/*.cpp      add("N01.identity", ...)（成功/失败分支会重复出现，正常）
//
// 用法：
//   node tools/lint/check-probe-ids.mjs                 # 比对
//   node tools/lint/check-probe-ids.mjs --write          # 重新生成 golden（改探针项时手动跑）
// 退出码：0 通过；1 失败；2 输入缺失。

import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.argv[2] && !process.argv[2].startsWith('--') ? process.argv[2] : '.';
const JAVA_DIR = path.join(ROOT, 'probe/src/main/java/com/sandbox/probe');
const CPP_DIR = path.join(ROOT, 'probe/src/main/cpp');
const GOLDEN = path.join(ROOT, 'probe/src/test/resources/expected_probe_ids.txt');
const WRITE = process.argv.includes('--write');

const ID_RE_JAVA = [
  /\bput(?:Skipped)?\(\s*"([A-Z]\d+(?:\.\w+)?)"/g, // put("J01", ...)
  /\{\s*"([A-Z]\d+(?:\.\w+)?)"\s*,\s*"/g, // {"P24", "..."}
];
const ID_RE_CPP = [/\badd\(\s*"([A-Z]\d+(?:\.\w+)?)"/g]; // add("N01.identity", ...)

function walk(dir, filter, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, filter, out);
    else if (filter(e.name)) out.push(p);
  }
  return out;
}

const javaFiles = walk(JAVA_DIR, (n) => n.endsWith('.java'));
const cppFiles = walk(CPP_DIR, (n) => n.endsWith('.cpp'));
if (javaFiles.length === 0) {
  console.error(`✗ 找不到探针 Java 源码：${JAVA_DIR}`);
  process.exit(2);
}

/** id -> 出现过的文件集合（同文件多次 = 互斥分支，不算重复） */
const ids = new Map();
function collect(files, regexes) {
  for (const f of files) {
    const text = fs.readFileSync(f, 'utf8');
    const rel = path.relative(ROOT, f);
    for (const re of regexes) {
      for (const m of text.matchAll(re)) {
        const id = m[1];
        if (!ids.has(id)) ids.set(id, new Set());
        ids.get(id).add(rel);
      }
    }
  }
}
collect(javaFiles, ID_RE_JAVA);
collect(cppFiles, ID_RE_CPP);

const actual = [...ids.keys()].sort();
const crossFileDups = [...ids.entries()]
  .filter(([, files]) => files.size > 1)
  .map(([id, files]) => `${id}（${[...files].join(', ')}）`);

if (WRITE) {
  fs.mkdirSync(path.dirname(GOLDEN), { recursive: true });
  fs.writeFileSync(
    GOLDEN,
    `# 探针的固定项集合（每行一个 id，排序）。\n` +
      `# 增删探针项时必须同步本文件：node tools/lint/check-probe-ids.mjs --write\n` +
      `# 由 tools/lint/check-probe-ids.mjs 校验；总数 ${actual.length}\n` +
      `# 来源：probe/src/main/java（J/P/F/D 等）+ probe/src/main/cpp（N 系列，长名带 .后缀）\n` +
      actual.join('\n') +
      '\n'
  );
  console.log(`✓ 已写入 ${path.relative(ROOT, GOLDEN)}（${actual.length} 项）`);
  process.exit(0);
}

if (!fs.existsSync(GOLDEN)) {
  console.error(`✗ 缺少 golden：${path.relative(ROOT, GOLDEN)}（先跑 --write）`);
  process.exit(1);
}

const expected = fs
  .readFileSync(GOLDEN, 'utf8')
  .split(/\r?\n/)
  .map((l) => l.trim())
  .filter((l) => l && !l.startsWith('#'));

let failed = false;
const missing = expected.filter((id) => !ids.has(id));
const extra = actual.filter((id) => !expected.includes(id));

if (missing.length > 0) {
  console.error(`✗ 项缺失（golden 有、源码没有）：${missing.join(', ')}`);
  failed = true;
}
if (extra.length > 0) {
  console.error(`✗ 新增项未登记到 golden：${extra.join(', ')}`);
  failed = true;
}
if (crossFileDups.length > 0) {
  console.error(`✗ id 在多个文件重复注册：${crossFileDups.join('; ')}`);
  failed = true;
}

if (!failed) {
  console.log(`✓ 探针项集合与 golden 一致（${actual.length} 项）`);
}
process.exit(failed ? 1 : 0);

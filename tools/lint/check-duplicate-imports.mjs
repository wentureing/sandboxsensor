#!/usr/bin/env node
// 重复 import 检查（毫秒级）。
//
// 为什么需要：Kotlin 对**同名 import 出现两次**报
//   "Conflicting import: imported name 'X' is ambiguous"
// （CI 2026-11-26 container-build 就是这样红的）。本地编译器不可用时，
// 靠这个脚本在提交前拦住。
//
// 用法：node tools/lint/check-duplicate-imports.mjs <目录或仓库根，默认 .>
// 退出码：0 通过；1 有重复。

import fs from 'node:fs';
import path from 'node:path';

const root = process.argv[2] || '.';
const problems = [];
let scanned = 0;

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === '.git' || entry.name === 'build' || entry.name === 'node_modules') continue;
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(p);
    else if (entry.name.endsWith('.kt')) check(p);
  }
}

function check(file) {
  scanned++;
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  const seen = new Map();
  lines.forEach((line, i) => {
    const m = line.match(/^\s*import\s+([\w.`]+)\s*$/);
    if (!m) return;
    const name = m[1];
    if (seen.has(name)) {
      problems.push(`${path.relative(root, file)}:${i + 1} 重复 import ${name}（首次在 ${seen.get(name)} 行）`);
    } else {
      seen.set(name, i + 1);
    }
  });
}

walk(root);

if (problems.length > 0) {
  problems.forEach((p) => console.error(`✗ ${p}`));
  console.error(`\n共 ${problems.length} 处重复 import —— 编译会报 "Conflicting import"`);
  process.exit(1);
}
console.log(`✓ 重复 import 检查通过（${scanned} 个 .kt 文件）`);

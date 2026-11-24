#!/usr/bin/env node
// 检测「hook 入口是否缺少幂等保护」。
//
// 为什么需要：重复 hook 通常意味着职责不清或调用点失控，而且有的 hook 重复安装
// 会造成真实故障（例：MainLooperGuard 重复 install 会嵌套 Looper.loop()，
// 同一条消息被分发两次）。
//
// 用法：node tools/lint/check-hook-idempotent.mjs <目录或文件> [...]
// 退出码：0 = 全部有保护，1 = 存在疑似缺少保护的入口。

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

const targets = process.argv.slice(2);
if (targets.length === 0) {
  console.error('用法: node tools/lint/check-hook-idempotent.mjs <文件或目录> [...]');
  process.exit(2);
}

function collect(p) {
  const out = [];
  const st = statSync(p);
  if (st.isDirectory()) {
    for (const n of readdirSync(p)) {
      if (n === 'build' || n === '.git' || n === 'node_modules') continue;
      out.push(...collect(join(p, n)));
    }
  } else if (extname(p) === '.kt') out.push(p);
  return out;
}

// 哪些函数名算「hook 入口」
const ENTRY = /^\s*(?:(?:private|internal|public|suspend)\s+)*fun\s+(install\w*|hook\w*)\s*\(/;
// 只关心「公共入口」：private 的是内部辅助，通常只被单一入口调用一次
const PUBLIC_ENTRY = /^\s*(?:(?:internal|public)\s+)+fun\s+(install\w*|hook\w*)\s*\(|^\s*fun\s+(install\w*|hook\w*)\s*\(/;
// 幂等保护的信号（出现在函数体前 12 行内即可）
const GUARD = /(installed|initialized|hooked|compareAndSet|AtomicBoolean|synchronized\s*\(|if\s*\([^)]*\)\s*\{?\s*return|already)/i;

let problems = 0;
let skippedPrivate = 0;
const files = targets.flatMap(collect);

for (const file of files) {
  const lines = readFileSync(file, 'utf8').split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (!ENTRY.test(lines[i])) continue;
    if (!PUBLIC_ENTRY.test(lines[i])) { skippedPrivate++; continue; }
    let guarded = false;
    for (let j = i; j < Math.min(i + 13, lines.length); j++) {
      if (GUARD.test(lines[j])) { guarded = true; break; }
    }
    if (!guarded) {
      console.log(`${file}:${i + 1}: 公共 hook 入口缺少幂等保护 -> ${lines[i].trim()}`);
      problems++;
    }
  }
}

if (problems) {
  console.log(`\n共 ${problems} 个公共 hook 入口未见幂等保护（已跳过 ${skippedPrivate} 个 private 辅助函数）。`);
  console.log('重复调用可能导致重复 hook / 嵌套。');
  console.log('注意：参数化的入口（如 hookStaticField(字段)）本来就可以多次调用，属正常；');
  console.log('      自幂等的实现（run 后 remove、有 installed 标志）也会被误报，需人工判断。');
  console.log('本检查仅供参考，不阻塞提交。');
  process.exit(0);
}
console.log(`hook 幂等检查通过（${files.length} 个文件，跳过 ${skippedPrivate} 个 private 辅助）`);

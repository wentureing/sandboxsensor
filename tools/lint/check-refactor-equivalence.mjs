#!/usr/bin/env node
// 重构等价性检查：对比「重构前的原始函数体」与「现在的函数体」的逻辑。
//
// 为什么需要：
//   golden 只能证明"当前实现与录制的输出一致"，若重构时逻辑被改动，golden 会一起错。
//   这个脚本直接对照原始代码，证明"重构只是搬了代码，逻辑没动"。
//
// 用法（旧版本先用 git 导出到文件，避免脚本内起子进程 —— 沙箱下 spawn 会 EPERM）：
//   git -C _ref/ShitJumpingVM show <commit>:<path> > tmp/old.kt
//   node tools/lint/check-refactor-equivalence.mjs \
//     --old-file tmp/old.kt --old-fn describeTransactionItem \
//     --new-file <当前文件> --new-fn describe
//
// 判定：归一化后（去注释、字符串内容、空白差异）函数体逐字相同 -> 等价。
// 退出码：0 = 等价，1 = 有差异。

import { readFileSync } from 'node:fs';

const argv = process.argv.slice(2);
const arg = (n, d = null) => {
  const i = argv.indexOf(`--${n}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};

const oldFile = arg('old-file');
const oldFn = arg('old-fn');
const newFile = arg('new-file');
const newFn = arg('new-fn');
// --ignore <正则>：在比较前删除匹配的整行（用于忽略新增的插桩行）
const ignore = arg('ignore', null);
const ignoreRe = ignore ? new RegExp(ignore) : null;

if (!oldFile || !oldFn || !newFile || !newFn) {
  console.error('用法: --old-file <导出后的旧文件> --old-fn <函数名> --new-file <当前文件> --new-fn <函数名> [--ignore <正则>]');
  process.exit(2);
}

/** 删除匹配 ignore 正则的整行（只对函数体内部生效）。 */
function dropIgnored(body) {
  if (!ignoreRe) return body;
  return body
    .split('\n')
    .filter((line) => !ignoreRe.test(line))
    .join('\n');
}

/** 从源码里截出函数体（按花括号配对）。支持扩展函数 `fun Receiver.name(`。 */
function extractFunction(src, fnName) {
  const re = new RegExp(
    `(?:private|internal|public)?\\s*fun\\s+(?:[A-Z]\\w*\\.)?${fnName}\\s*\\(`
  );
  const m = re.exec(src);
  if (!m) return null;
  let i = m.index + m[0].length - 1;
  let depth = 0;
  let bodyStart = -1;
  for (; i < src.length; i++) {
    const c = src[i];
    if (c === '(' || c === '<') depth++;
    else if (c === ')' || c === '>') depth--;
    else if (c === '{' && depth === 0) { bodyStart = i; break; }
  }
  if (bodyStart < 0) return null;

  let braces = 0;
  for (let j = bodyStart; j < src.length; j++) {
    if (src[j] === '{') braces++;
    else if (src[j] === '}') { braces--; if (braces === 0) return src.slice(bodyStart, j + 1); }
  }
  return null;
}

/** 归一化：去注释、去字符串内容、空白归一。 */
function normalize(body) {
  let out = '', i = 0, state = 'code';
  while (i < body.length) {
    const c = body[i], d = body[i + 1], e = body[i + 2];
    if (state === 'code') {
      if (c === '/' && d === '/') { state = 'line'; i += 2; continue; }
      if (c === '/' && d === '*') { state = 'block'; i += 2; continue; }
      if (c === '"' && d === '"' && e === '"') { state = 'raw'; i += 3; continue; }
      if (c === '"') { state = 'str'; out += '""'; i++; continue; }
      out += c; i++; continue;
    }
    if (state === 'line') { if (c === '\n') state = 'code'; i++; continue; }
    if (state === 'block') { if (c === '*' && d === '/') { state = 'code'; i += 2; } else i++; continue; }
    if (state === 'str') { if (c === '\\') { i += 2; continue; } if (c === '"') { state = 'code'; i++; continue; } i++; continue; }
    if (state === 'raw') { if (c === '"' && d === '"' && e === '"') { state = 'code'; i += 3; continue; } i++; continue; }
  }
  return out.replace(/\s+/g, ' ').replace(/\(\s+/g, '(').replace(/\s+\)/g, ')').replace(/\s*,\s*/g, ',').trim();
}

const oldBody = extractFunction(readFileSync(oldFile, 'utf8'), oldFn);
const newBody = extractFunction(readFileSync(newFile, 'utf8'), newFn);

if (!oldBody) { console.error(`未在 ${oldFile} 找到函数 ${oldFn}`); process.exit(2); }
if (!newBody) { console.error(`未在 ${newFile} 找到函数 ${newFn}`); process.exit(2); }

const a = normalize(dropIgnored(oldBody));
const b = normalize(dropIgnored(newBody));

if (a === b) {
  console.log(`等价 ✓  ${oldFn} == ${newFn}`);
  console.log(`  归一化后逐字相同（已忽略注释/字符串内容/空白），${a.length} 字符`);
  process.exit(0);
}

let k = 0;
while (k < a.length && k < b.length && a[k] === b[k]) k++;
const ctx = 80;
console.log(`不等价 ✗  ${oldFn} vs ${newFn}，第 ${k} 字符处开始不同:`);
console.log(`  - 旧: ...${a.slice(Math.max(0, k - ctx), k + ctx)}...`);
console.log(`  + 新: ...${b.slice(Math.max(0, k - ctx), k + ctx)}...`);
process.exit(1);

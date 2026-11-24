#!/usr/bin/env node
// 检查「同模块跨包引用是否漏了 import」。
//
// 为什么需要：拆分包/移文件后，原来同包的类型不再自动可见，必须显式 import。
// 本项目已连续两次因此编译失败：
//   1) CustomerProbes 迁移后漏 import findField
//   2) LegacyMessageHandlers 移包后漏 import ActivityThreadHook
// 编译器能查，但要等 2 分钟 CI；这个脚本毫秒级。
//
// 用法：node tools/lint/check-imports.mjs <模块 src 目录> [...]
// 退出码：0 = 没发现缺失，1 = 发现缺失（有误报时请先确认再决定是否忽略）。

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, extname, basename } from 'node:path';

const targets = process.argv.slice(2);
if (targets.length === 0) {
  console.error('用法: node tools/lint/check-imports.mjs <模块 src 目录> [...]');
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

/** 剥离注释与字符串，只留代码（用于找标识符） */
function stripCode(src) {
  let out = '', i = 0, state = 'code';
  while (i < src.length) {
    const c = src[i], d = src[i + 1], e = src[i + 2];
    if (state === 'code') {
      if (c === '/' && d === '/') { state = 'line'; i += 2; continue; }
      if (c === '/' && d === '*') { state = 'block'; i += 2; continue; }
      if (c === '"' && d === '"' && e === '"') { state = 'raw'; i += 3; continue; }
      if (c === '"') { state = 'str'; i++; continue; }
      if (c === "'") { state = 'chr'; i++; continue; }
      out += c; i++; continue;
    }
    if (state === 'line') { if (c === '\n') { state = 'code'; out += '\n'; } i++; continue; }
    if (state === 'block') { if (c === '*' && d === '/') { state = 'code'; i += 2; } else i++; continue; }
    if (state === 'str') { if (c === '\\') { i += 2; continue; } if (c === '"') { state = 'code'; i++; continue; } i++; continue; }
    if (state === 'chr') { if (c === '\\') { i += 2; continue; } if (c === "'") { state = 'code'; i++; continue; } i++; continue; }
    if (state === 'raw') { if (c === '"' && d === '"' && e === '"') { state = 'code'; i += 3; continue; } i++; continue; }
  }
  return out;
}

const files = targets.flatMap(collect);

// 第一遍：建立「类型名 -> 所在包」表（仅同模块内定义的类型）
const typeToPackage = new Map();
const fileInfo = new Map(); // file -> {pkg, imports:Set, ownTypes:Set, code}
const TYPE_DECL = /\b(?:class|interface|object|enum\s+class|typealias)\s+([A-Z]\w*)/g;

for (const file of files) {
  const src = readFileSync(file, 'utf8');
  const pkg = (src.match(/^\s*package\s+([\w.]+)/m) || [, ''])[1];
  const imports = new Set();
  for (const m of src.matchAll(/^\s*import\s+([\w.]+)/gm)) {
    imports.add(m[1]);                       // 全限定
    imports.add(m[1].split('.').pop());      // 简单名
  }
  const ownTypes = new Set();
  const code = stripCode(src);
  for (const m of code.matchAll(TYPE_DECL)) {
    ownTypes.add(m[1]);
    if (!typeToPackage.has(m[1])) typeToPackage.set(m[1], pkg);
  }
  fileInfo.set(file, { pkg, imports, ownTypes, code });
}

// 第二遍：找「引用了别包类型但没 import」
let problems = 0;
for (const [file, info] of fileInfo) {
  const referenced = new Set();
  for (const m of info.code.matchAll(/\b([A-Z]\w{2,})\b/g)) referenced.add(m[1]);

  for (const name of referenced) {
    const pkgOfType = typeToPackage.get(name);
    if (!pkgOfType) continue;                 // 模块外类型（JDK/Android/三方）不检查
    if (pkgOfType === info.pkg) continue;      // 同包，自动可见
    if (info.ownTypes.has(name)) continue;     // 本文件定义
    if (info.imports.has(name)) continue;      // 已 import（简单名）
    if (info.imports.has(`${pkgOfType}.${name}`)) continue;

    // 排除嵌套类访问：Outer.Inner（此时只需要 Outer 可见）
    if (new RegExp(`\\b[A-Z]\\w*\\.${name}\\b`).test(info.code)) continue;
    // 排除全限定写法：com.foo.Bar
    if (new RegExp(`\\b[a-z]\\w*(\\.[a-z]\\w*)+\\.[A-Z]\\w*\\.${name}\\b`).test(info.code)) continue;

    console.log(`${file}: 引用了 ${pkgOfType}.${name}，但未 import`);
    problems++;
  }
}

if (problems) {
  console.log(`\n共 ${problems} 处「同模块跨包引用缺 import」—— 编译会报 Unresolved reference。`);
  console.log('修法：在文件 import 区加上对应的全限定名。');
  console.log('注意：若报的是同名局部变量/类型别名等误报，请人工确认后忽略。');
  process.exit(1);
}
console.log(`import 完整性检查通过（${files.length} 个文件，${typeToPackage.size} 个类型）`);

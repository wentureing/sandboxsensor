#!/usr/bin/env node
// 检查三类 import 问题（毫秒级，不需要 Gradle / SDK）：
//   1) 同模块跨包引用漏 import（拆包/移文件后最常见）
//   2) 嵌套类被当顶层类 import（ApplicationRecord 踩过）
//   3) **import 路径写错**：类型名对但包不对（VmResult 踩过：写在 engine 包，实际在 core:model）
//
// 为什么要跨模块一起扫：第 3 类只有把 core/* + app + feature/* 一起传进来，
// `typeToPackage` 才知道类型的真实包；只传单个模块时它会把跨模块类型当"三方类型"跳过。
//
// 用法：node tools/lint/check-imports.mjs <目录> [...]
//   单模块：  node tools/lint/check-imports.mjs _ref/ShitJumpingVM/core/virtualization/src
//   跨模块：  node tools/lint/check-imports.mjs _ref/ShitJumpingVM/core _ref/ShitJumpingVM/app _ref/ShitJumpingVM/feature
// 退出码：0 = 通过，1 = 有问题（有误报时请先确认再决定是否忽略）。

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

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
      // Kotlin 反引号标识符 `T35 SandboxManager ...`：里面的词不是代码引用，必须剥离
      if (c === '`') { state = 'tick'; i++; continue; }
      out += c; i++; continue;
    }
    if (state === 'line') { if (c === '\n') { state = 'code'; out += '\n'; } i++; continue; }
    if (state === 'block') { if (c === '*' && d === '/') { state = 'code'; i += 2; } else i++; continue; }
    if (state === 'str') { if (c === '\\') { i += 2; continue; } if (c === '"') { state = 'code'; i++; continue; } i++; continue; }
    if (state === 'chr') { if (c === '\\') { i += 2; continue; } if (c === "'") { state = 'code'; i++; continue; } i++; continue; }
    if (state === 'raw') { if (c === '"' && d === '"' && e === '"') { state = 'code'; i += 3; continue; } i++; continue; }
    if (state === 'tick') { if (c === '`') state = 'code'; i++; continue; }
  }
  return out;
}

const files = targets.flatMap(collect);

// 第一遍：建立「类型名 -> 所在包」表（仅同模块内定义的类型）
// 同名类型出现在多个包时视为 ambiguous，直接不判（例如 feature 里有 sealed class Intent，
// 而其它模块里的 Intent 是 android.content.Intent —— 判谁都会误报）。
const typePackages = new Map(); // 类型名 -> Set<包>
const typeToPackage = new Map(); // 类型名 -> 唯一所在包（ambiguous 的不放进来）
const nestedTypes = new Map(); // 类型名 -> 所在包（声明带缩进 = 嵌套在某个类里）
const fileInfo = new Map(); // file -> {pkg, imports:Set, wildcards:Set, ownTypes:Set, code}
// java.lang 里与容器自有类型同名的（不排除会误报：core:model 里有 `System` 类型，
// 而代码里的 `System.currentTimeMillis()` 是 java.lang.System，无需 import）
const JAVA_LANG_NAMES = new Set([
  'System', 'String', 'StringBuilder', 'Object', 'Class', 'Thread', 'Math',
  'Exception', 'RuntimeException', 'Error', 'Throwable', 'Boolean', 'Integer',
  'Long', 'Float', 'Double', 'Byte', 'Short', 'Character', 'Number', 'Void',
  'Iterable', 'Comparable', 'Enum', 'CharSequence', 'Runnable', 'AutoCloseable',
  'Cloneable', 'Deprecated', 'Override', 'SuppressWarnings',
]);

const TYPE_DECL = /\b(?:class|interface|object|enum\s+class|typealias)\s+([A-Z]\w*)/g;

for (const file of files) {
  const src = readFileSync(file, 'utf8');
  const pkg = (src.match(/^\s*package\s+([\w.]+)/m) || [, ''])[1];
  const imports = new Set();
  const wildcards = new Set();
  for (const m of src.matchAll(/^\s*import\s+([\w.]+?)(\.\*)?\s*$/gm)) {
    const fq = m[1];
    if (m[2]) { wildcards.add(fq); continue; }   // import a.b.*
    imports.add(fq);                             // 全限定
    // 每一段都算「已 import」：嵌套类 import（a.b.Outer.Inner）会让 Outer 出现在代码里，
    // 不登记就会误报「引用了 Outer 但未 import」
    for (const seg of fq.split('.')) imports.add(seg);
  }
  const ownTypes = new Set();
  const code = stripCode(src);
  for (const m of code.matchAll(TYPE_DECL)) {
    ownTypes.add(m[1]);
    // 声明所在行的缩进 > 0 ⇒ 嵌套在某个类体里（顶层的 data class 前缀里没有缩进）
    const lineStart = code.lastIndexOf('\n', m.index) + 1;
    const indent = (code.slice(lineStart, m.index).match(/^[ \t]*/) || [''])[0].length;
    if (indent > 0) nestedTypes.set(m[1], pkg);
    if (!JAVA_LANG_NAMES.has(m[1])) {
      if (!typePackages.has(m[1])) typePackages.set(m[1], new Set());
      typePackages.get(m[1]).add(pkg);
    }
  }
  fileInfo.set(file, { pkg, imports, wildcards, ownTypes, code });
}

for (const [name, pkgs] of typePackages) {
  if (pkgs.size === 1) typeToPackage.set(name, [...pkgs][0]);
}

// 第二遍之前：嵌套类不能用「顶层 import」形式（真实踩过：ApplicationRecord 是
// VirtualApplicationManager 的嵌套类，写成 lifecycle.ApplicationRecord 编译报 Unresolved reference）
let nestedImportProblems = 0;
for (const [file, info] of fileInfo) {
  const src = readFileSync(file, 'utf8');
  for (const m of src.matchAll(/^\s*import\s+([\w.]+)/gm)) {
    const fq = m[1];
    const simple = fq.split('.').pop();
    const nestedPkg = nestedTypes.get(simple);
    if (!nestedPkg) continue;
    const importedPkg = fq.split('.').slice(0, -1).join('.');
    if (importedPkg === nestedPkg) {
      nestedImportProblems++;
      console.log(
        `${file}: 嵌套类 ${simple} 被当作顶层类 import —— 应写成 ${nestedPkg}.<Outer>.${simple}`,
      );
    }
  }
}
if (nestedImportProblems > 0) {
  console.log(`\n共 ${nestedImportProblems} 处嵌套类 import 写法错误——编译会报 Unresolved reference。`);
}

// 第 2.5 遍：import 路径写错（类型名对、包错）—— 真实踩过：
//   CmdReceiver.kt 写 import com.nextvm.core.virtualization.engine.VmResult，
//   而 VmResult 实际在 com.nextvm.core.model  → 编译报 Unresolved reference，
//   但"缺 import"检查抓不到（名字在 import 列表里）。需要跨模块扫描才能发现。
let wrongImportProblems = 0;
for (const [file, info] of fileInfo) {
  const src = readFileSync(file, 'utf8');
  for (const m of src.matchAll(/^\s*import\s+([\w.]+)/gm)) {
    const fq = m[1];
    const simple = fq.split('.').pop();
    if (nestedTypes.has(simple)) continue;   // 嵌套类 import 由上一段专门检查
    const realPkg = typeToPackage.get(simple);
    if (!realPkg) continue;                  // 本仓未定义（JDK/Android/三方）→ 不判
    const importedPkg = fq.split('.').slice(0, -1).join('.');
    if (importedPkg !== realPkg) {
      wrongImportProblems++;
      console.log(`${file}: import ${fq} 路径不对 —— ${simple} 实际在 ${realPkg}`);
    }
  }
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
    if (info.wildcards.has(pkgOfType)) continue;  // import a.b.* 也算已引入

    // 排除嵌套类访问：Outer.Inner（此时只需要 Outer 可见）
    if (new RegExp(`\\b[A-Z]\\w*\\.${name}\\b`).test(info.code)) continue;
    // 排除全限定写法：com.foo.Bar 或 com.foo.diag.Bar.xxx（允许换行/空白，见 StateProbe 的拼接写法）
    if (new RegExp(`\\b[a-z]\\w*(?:\\s*\\.\\s*[a-z]\\w*)*\\s*\\.\\s*${name}\\b`).test(info.code)) continue;

    console.log(`${file}: 引用了 ${pkgOfType}.${name}，但未 import`);
    problems++;
  }
}

if (problems) {
  console.log(`\n共 ${problems} 处「同模块跨包引用缺 import」—— 编译会报 Unresolved reference。`);
  console.log('修法：在文件 import 区加上对应的全限定名。');
  console.log('注意：若报的是同名局部变量/类型别名等误报，请人工确认后忽略。');
}
if (nestedImportProblems) {
  console.log(`\n共 ${nestedImportProblems} 处嵌套类 import 写法错误 —— 编译会报 Unresolved reference。`);
  console.log('修法：把 `<pkg>.<Nested>` 改成 `<pkg>.<Outer>.<Nested>`。');
}
if (wrongImportProblems) {
  console.log(`\n共 ${wrongImportProblems} 处「import 路径写错」—— 编译会报 Unresolved reference。`);
  console.log('修法：把包路径改成类型实际所在的包（见每行的"实际在 ..."）。');
}
if (problems || nestedImportProblems) process.exit(1);
if (wrongImportProblems) process.exit(1);
console.log(`import 完整性检查通过（${files.length} 个文件，${typeToPackage.size} 个类型）`);

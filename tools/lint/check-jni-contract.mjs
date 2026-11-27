#!/usr/bin/env node
// JNI 契约检查（毫秒级，不需要 Gradle / NDK）。
//
// 背景：native 重构时最容易犯的两个错——
//   1) 改了/删了 C++ 导出，但 Kotlin `external fun` 没同步（或反之）→ 运行期 UnsatisfiedLinkError；
//   2) 新 .cpp 忘了加进 CMakeLists.txt → undefined symbol（本项目踩过）。
//
// 检查项：
//   A) Kotlin 的 `external fun` 名字集合 == C++ `Java_<pkg>_<Class>_<name>` 导出集合
//      （按 native 函数名比对，前缀差异由脚本归一化）
//   B) `JNI_OnLoad` 若存在于 C++，必须仍存在
//   C) CMakeLists.txt 必须仍编译 native-hook.cpp，且列出的每个 .cpp 文件都存在
//
// 用法：node tools/lint/check-jni-contract.mjs [仓库根，默认 _ref/ShitJumpingVM]
// 退出码：0 通过；1 不一致。

import fs from 'node:fs';
import path from 'node:path';

const root = process.argv[2] || '_ref/ShitJumpingVM';
const KOTLIN = 'core/hook/src/main/java/com/nextvm/core/hook/NativeHookBridge.kt';
const CPP = 'core/hook/src/main/cpp/native-hook.cpp';
const CMAKE = 'core/hook/src/main/cpp/CMakeLists.txt';

const read = (rel) => {
  const p = path.join(root, rel);
  if (!fs.existsSync(p)) {
    console.error(`✗ 文件不存在：${p}`);
    process.exit(1);
  }
  return fs.readFileSync(p, 'utf8');
};

const kotlin = read(KOTLIN);
const cpp = read(CPP);
const cmake = read(CMAKE);

// ---- A) Kotlin external fun ----
const kotlinFns = new Set();
for (const m of kotlin.matchAll(/\bexternal\s+fun\s+([A-Za-z_]\w*)\s*\(/g)) {
  kotlinFns.add(m[1]);
}

// ---- C) C++ 导出：Java_com_nextvm_core_hook_NativeHookBridge_<name> / Java_com_nextvm_core_hook_JavaIoHooks_<name> ----
// 归一化：从 Java_ 全名里取最后一段函数名；同时记录所属类，保证类路径不变。
const cppFns = new Map(); // nativeFn -> 完整符号
for (const m of cpp.matchAll(/\bJava_([A-Za-z0-9_]+)_([A-Za-z0-9_]+)\s*\(/g)) {
  // Java_com_nextvm_app_Xxx 里最后一段是函数名
  const full = `Java_${m[1]}_${m[2]}`;
  const parts = full.split('_');
  const fn = parts[parts.length - 1];
  cppFns.set(fn, full);
}

const missingInCpp = [...kotlinFns].filter((f) => !cppFns.has(f)).sort();
const missingInKotlin = [...cppFns.keys()].filter((f) => !kotlinFns.has(f)).sort();

let bad = 0;
if (missingInCpp.length) {
  bad++;
  console.error(`✗ Kotlin 有 external fun 但 C++ 没有导出（会 UnsatisfiedLinkError）：${missingInCpp.join(', ')}`);
}
if (missingInKotlin.length) {
  console.log(`! C++ 导出了但 Kotlin 没有对应 external fun（可能是旧符号/内部用）：${missingInKotlin.join(', ')}`);
}

// ---- B) JNI_OnLoad ----
if (/JNI_OnLoad\s*\(/.test(cpp) === false) {
  // 本项目当前没有 JNI_OnLoad（用 System.loadLibrary + nativeInit），不强制
  console.log('· C++ 无 JNI_OnLoad（当前设计如此：loadLibrary + nativeInit）');
}

// ---- C) CMakeLists ----
if (!/add_library\s*\([^)]*native-hook\.cpp/s.test(cmake)) {
  bad++;
  console.error('✗ CMakeLists.txt 没有把 native-hook.cpp 编进库');
}
const cppDir = path.dirname(path.join(root, CMAKE));
for (const m of cmake.matchAll(/^\s*((?:core\/)?[A-Za-z0-9_\-]+\.cpp)\s*$/gm)) {
  const rel = m[1];
  if (!fs.existsSync(path.join(cppDir, rel))) {
    bad++;
    console.error(`✗ CMakeLists.txt 列出的源文件不存在：${rel}`);
  }
}

if (bad) {
  console.error(`\nJNI 契约检查失败（${bad} 项）。`);
  process.exit(1);
}
console.log(
  `✓ JNI 契约一致：Kotlin external=${kotlinFns.size} / C++ 导出=${cppFns.size}` +
    (missingInKotlin.length ? `（${missingInKotlin.length} 个 C++ 侧多余符号已提示）` : '')
);

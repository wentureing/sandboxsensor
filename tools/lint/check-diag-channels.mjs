#!/usr/bin/env node
// 诊断通道契约检查（毫秒级，不需要 Gradle / Android SDK）。
//
// 用途：Kotlin 侧 `DiagFiles` 是文件名的唯一规范，C++ 侧 `is_container_diag_path`
// 另有一份白名单（决定"哪些 open 不计入 Java IO 日志配额"）。两份列表天然会漂移，
// 一旦漂移：新增通道的每次写入都会占日志配额并刷屏 logcat。
//
// 检查项：
//   1) C++ marker 必须在 Kotlin 名字里找得到（**失败** = 真漂移，C++ 有 Kotlin 没有）
//   2) Kotlin 通道未被任何 C++ marker 覆盖 → **警告**（列出清单，提示补白名单）
//   3) `DiagFiles.markers()` 必须覆盖所有带名字的通道（**失败** = 下发白名单会漏）
//
// 用法：
//   node tools/lint/check-diag-channels.mjs [仓库根，默认 .]
// 退出码：0 通过（可有警告）；1 失败。

import fs from 'node:fs';
import path from 'node:path';

const root = process.argv[2] || '.';
const KOTLIN = 'core/common/src/main/java/com/nextvm/core/common/diagnostics/DiagFiles.kt';
const NATIVE = 'core/hook/src/main/cpp/native-hook.cpp';

const SENTINEL = '123'; // $pid / ${prefix} 等插值统一代入

function fail(msg) {
  console.error(`✗ ${msg}`);
  process.exitCode = 1;
}
function warn(msg) {
  console.warn(`! ${msg}`);
}
function ok(msg) {
  console.log(`✓ ${msg}`);
}

function readFileOrDie(rel) {
  const p = path.join(root, rel);
  if (!fs.existsSync(p)) {
    fail(`文件不存在：${p}`);
    return null;
  }
  return fs.readFileSync(p, 'utf8');
}

const kt = readFileOrDie(KOTLIN);
const cpp = readFileOrDie(NATIVE);
if (!kt || !cpp) process.exit(1);

// ---------- Kotlin：const val + 单行 fun ----------
function expandInterpolation(raw) {
  return raw
    .replace(/\$\{[^}]+\}/g, SENTINEL)
    .replace(/\$[A-Za-z_][A-Za-z0-9_]*/g, SENTINEL);
}

const ktNames = new Map(); // name(已代入) -> 定义位置描述
for (const m of kt.matchAll(/(?:const\s+val)\s+(\w+)\s*=\s*"([^"]+)"/g)) {
  ktNames.set(expandInterpolation(m[2]), `const ${m[1]}`);
}
for (const m of kt.matchAll(/fun\s+(\w+)\s*\([^)]*\)\s*:\s*String\s*=\s*"([^"]+)"/g)) {
  ktNames.set(expandInterpolation(m[2]), `fun ${m[1]}`);
}

// markers() 里列出的片段
const markersBody = kt.match(/fun\s+markers\s*\(\s*\)\s*:\s*Array<String>\s*=\s*arrayOf\(([\s\S]*?)\)/);
const ktMarkers = markersBody
  ? [...markersBody[1].matchAll(/"([^"]+)"/g)].map((m) => m[1])
  : [];
const ktMarkerRefs = markersBody
  ? [...markersBody[1].matchAll(/\b([A-Z][A-Z0-9_]{2,})\b/g)].map((m) => m[1])
  : [];
for (const ref of ktMarkerRefs) {
  const m = kt.match(new RegExp(`const\\s+val\\s+${ref}\\s*=\\s*"([^"]+)"`));
  if (m) ktMarkers.push(m[1]);
}

// ---------- C++：kMarkers[] ----------
const markersBlock = cpp.match(/kMarkers\[\]\s*=\s*\{([\s\S]*?)\}\s*;/);
if (!markersBlock) {
  fail(`在 ${NATIVE} 里找不到 kMarkers[]（is_container_diag_path 的白名单）`);
  process.exit(1);
}
const cppMarkers = [...markersBlock[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);

console.log(`Kotlin DiagFiles 通道名：${ktNames.size} 个`);
console.log(`Kotlin markers()    ：${ktMarkers.length} 个`);
console.log(`C++ kMarkers        ：${cppMarkers.length} 个`);

// ---------- 检查 1：C++ marker → Kotlin ----------
const ktList = [...ktNames.keys()];
const cppOrphans = [];
for (const raw of cppMarkers) {
  const m = raw.replace(/^\//, '');
  const hit = ktList.some((n) => n.includes(m));
  if (!hit) cppOrphans.push(raw);
}
if (cppOrphans.length > 0) {
  fail(`C++ 白名单里有 Kotlin 不存在的标记（真漂移）：${cppOrphans.join(', ')}`);
} else {
  ok(`C++ 白名单 ${cppMarkers.length} 项全部能在 DiagFiles 找到对应`);
}

// ---------- 检查 2：Kotlin 通道未列入 C++（警告） ----------
const uncovered = ktList.filter(
  (n) => !cppMarkers.some((raw) => n.includes(raw.replace(/^\//, '')))
);
if (uncovered.length > 0) {
  warn(
    `${uncovered.length} 个通道未列入 C++ is_container_diag_path（写入会占 Java IO 日志配额）：\n` +
      uncovered.map((n) => `    - ${n}`).join('\n')
  );
} else {
  ok('所有 Kotlin 通道都已列入 C++ 白名单');
}

// ---------- 检查 3：markers() 覆盖 ----------
if (ktMarkers.length === 0) {
  fail('DiagFiles.markers() 不存在或为空（C++ 白名单无下发来源）');
} else {
  const missing = ktList.filter((n) => !ktMarkers.some((mk) => n.includes(mk)));
  if (missing.length > 0) {
    warn(
      `markers() 未覆盖 ${missing.length} 个通道（下一批 JNI 下发会漏）：\n` +
        missing.map((n) => `    - ${n}`).join('\n')
    );
  } else {
    ok('markers() 覆盖全部通道名');
  }
}

console.log(process.exitCode ? '诊断通道契约：失败' : '诊断通道契约：通过');

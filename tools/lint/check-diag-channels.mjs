#!/usr/bin/env node
// 诊断通道契约检查（毫秒级，不需要 Gradle / SDK）。
//
// 唯一来源：core/common/src/main/resources/diag_channels.tsv
//   shared  两端都识别
//   kotlin  只 Kotlin 生产，C++ 仍须识别
//   cpp     只 C++ 生产，Kotlin 不判定（差异区，必须与 Kotlin 通道名不相交）
//
// 检查：
//   1) TSV 的 marker 都能在 DiagFiles 的通道名里找到（防陈旧 marker）
//   2) DiagFiles 的每个通道名都被某个 marker 覆盖（防漏配 -> 占 Java IO 配额）
//   3) cpp 段 marker 不得与 Kotlin 通道名相交（差异区隔离）
//   4) 生成的 .inc 与 TSV 一致（等价于 gen-diag-channels.mjs --check）
//   5) native-hook.cpp 的 is_container_diag_path 使用三段数组
//
// 用法：node tools/lint/check-diag-channels.mjs [仓库根，默认 .]
// 退出码：0 通过；1 失败。

import fs from 'node:fs';
import path from 'node:path';

const root = process.argv[2] || '.';
const TSV = 'core/common/src/main/resources/diag_channels.tsv';
const KOTLIN = 'core/common/src/main/java/com/nextvm/core/common/diagnostics/DiagFiles.kt';
const INC = 'core/hook/src/main/cpp/core/diag_channels.inc';
const NATIVE = 'core/hook/src/main/cpp/native-hook.cpp';
const SENTINEL = '123';

let exitCode = 0;
const fail = (m) => {
  console.error(`✗ ${m}`);
  exitCode = 1;
};
const warn = (m) => console.warn(`! ${m}`);
const ok = (m) => console.log(`✓ ${m}`);

function read(rel) {
  const p = path.join(root, rel);
  if (!fs.existsSync(p)) {
    fail(`文件不存在：${p}`);
    return null;
  }
  return fs.readFileSync(p, 'utf8');
}

const tsv = read(TSV);
const kt = read(KOTLIN);
const native = read(NATIVE);
if (!tsv || !kt || !native) process.exit(1);

// ---------- 1. TSV ----------
const sections = { shared: [], kotlin: [], cpp: [] };
tsv.split(/\r?\n/).forEach((raw, i) => {
  const line = raw.trim();
  if (!line || line.startsWith('#')) return;
  const cols = line.split('\t').map((c) => c.trim());
  if (cols.length < 2 || !sections[cols[0]]) {
    fail(`TSV 第 ${i + 1} 行格式错误：${line}`);
    return;
  }
  sections[cols[0]].push(cols[1]);
});
const allMarkers = [...sections.shared, ...sections.kotlin, ...sections.cpp];
console.log(
  `TSV: shared=${sections.shared.length} kotlin=${sections.kotlin.length} cpp=${sections.cpp.length}`
);

// ---------- 2. DiagFiles 通道名 ----------
const expand = (s) =>
  s.replace(/\$\{[^}]+\}/g, SENTINEL).replace(/\$[A-Za-z_][A-Za-z0-9_]*/g, SENTINEL);
const ktNames = new Set();
for (const m of kt.matchAll(/(?:const\s+val)\s+(\w+)\s*=\s*"([^"]+)"/g)) {
  if (m[1] === 'CONFIG_RESOURCE') continue; // 资源路径，不是诊断通道名
  ktNames.add(expand(m[2]));
}
for (const m of kt.matchAll(/fun\s+(\w+)\s*\([^)]*\)\s*:\s*String\s*=\s*"([^"]+)"/g))
  ktNames.add(expand(m[2]));
const ktList = [...ktNames].filter((n) => !n.startsWith('/'));
console.log(`DiagFiles 通道名：${ktList.length} 个`);

// ---------- 3. 检查 ----------
const stale = [...sections.shared, ...sections.kotlin].filter((mk) => !ktList.some((n) => n.includes(mk)));
if (stale.length > 0) fail(`TSV 里有 DiagFiles 不产生的 marker（陈旧）：${stale.join(', ')}`);
else ok('TSV marker 全部对应真实通道');

const uncovered = ktList.filter((n) => !allMarkers.some((mk) => n.includes(mk)));
if (uncovered.length > 0) {
  fail(`DiagFiles 有通道未被 TSV 覆盖（会占 Java IO 日志配额）：\n${uncovered.map((n) => `    - ${n}`).join('\n')}`);
} else ok('所有通道名都被 TSV 覆盖');

const cppLeak = sections.cpp.filter((mk) => ktList.some((n) => n.includes(mk)));
if (cppLeak.length > 0) fail(`cpp 段 marker 与 Kotlin 通道相交（差异区不隔离）：${cppLeak.join(', ')}`);
else ok('cpp 差异区与 Kotlin 通道不相交');

// ---------- 4. .inc 一致性 ----------
const incPath = path.join(root, INC);
if (!fs.existsSync(incPath)) {
  fail(`缺少生成物 ${INC}，请运行 node tools/gen/gen-diag-channels.mjs`);
} else {
  const inc = fs.readFileSync(incPath, 'utf8');
  const present = (name, markers) =>
    markers.every((m) => inc.includes(`"${m}"`)) && inc.includes(name);
  if (
    !present('kDiagMarkersShared', sections.shared) ||
    !present('kDiagMarkersKotlinOnly', sections.kotlin) ||
    !present('kDiagMarkersCppOnly', sections.cpp)
  ) {
    fail(`.inc 与 TSV 不一致，请运行 node tools/gen/gen-diag-channels.mjs`);
  } else ok('.inc 与 TSV 一致');
}

// ---------- 5. native 使用三段 ----------
const usesAll =
  native.includes('kDiagMarkersShared') &&
  native.includes('kDiagMarkersKotlinOnly') &&
  native.includes('kDiagMarkersCppOnly');
if (!usesAll) fail('native-hook.cpp 的 is_container_diag_path 没有使用三段数组');
else ok('native-hook.cpp 使用 shared + kotlin + cpp 三段');

if (sections.cpp.length > 0) {
  warn(`差异区（cpp 段，Kotlin 不判定）：${sections.cpp.join(', ')}`);
}

console.log(exitCode === 0 ? '诊断通道契约：通过' : '诊断通道契约：失败');
process.exit(exitCode);

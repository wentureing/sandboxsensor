#!/usr/bin/env node
// 从 diag_channels.tsv 生成 C++ include（诊断白名单两端共用一份配置）。
//
//   node tools/gen/gen-diag-channels.mjs           # 生成/覆盖 .inc
//   node tools/gen/gen-diag-channels.mjs --check    # 只校验：生成物与 TSV 一致（CI 用）
//
// 为什么需要：Kotlin 的 DiagFiles 与 C++ 的 is_container_diag_path 曾是两份手写列表，
// 已经漂移过（6 项 vs 20+ 项）。现在 TSV 是唯一来源，`.inc` 由本脚本产出并提交入库
// （C++ 运行时无法读 Android 资源）。
//
// 退出码：0 一致/已生成；1 不一致（--check）；2 输入缺失。

import fs from 'node:fs';
import path from 'node:path';

const REPO = process.argv[2] && !process.argv[2].startsWith('--') ? process.argv[2] : '.';
const CHECK = process.argv.includes('--check');

const TSV = path.join(REPO, 'core/common/src/main/resources/diag_channels.tsv');
const OUT = path.join(REPO, 'core/hook/src/main/cpp/core/diag_channels.inc');

if (!fs.existsSync(TSV)) {
  console.error(`✗ 找不到配置：${TSV}`);
  process.exit(2);
}

const sections = { shared: [], kotlin: [], cpp: [] };
const problems = [];

fs.readFileSync(TSV, 'utf8')
  .split(/\r?\n/)
  .forEach((raw, i) => {
    const line = raw.trim();
    if (!line || line.startsWith('#')) return;
    const cols = line.split('\t').map((c) => c.trim());
    if (cols.length < 2) {
      problems.push(`第 ${i + 1} 行：少于 2 列（需要 section<TAB>marker）`);
      return;
    }
    const [section, marker] = cols;
    if (!sections[section]) {
      problems.push(`第 ${i + 1} 行：未知 section "${section}"（只允许 shared/kotlin/cpp）`);
      return;
    }
    if (!marker) {
      problems.push(`第 ${i + 1} 行：marker 为空`);
      return;
    }
    sections[section].push(marker);
  });

// 同一 marker 不得跨段重复（重复意味着"差异区"语义不清）
const seen = new Map();
for (const [section, markers] of Object.entries(sections)) {
  for (const m of markers) {
    if (seen.has(m)) problems.push(`marker "${m}" 同时出现在 ${seen.get(m)} 与 ${section}`);
    else seen.set(m, section);
  }
}

if (problems.length > 0) {
  problems.forEach((p) => console.error(`✗ ${p}`));
  process.exit(1);
}

function arrayBlock(name, markers) {
  if (markers.length === 0) return `static const char* const ${name}[] = { nullptr };\n`;
  const body = markers.map((m) => `    "${m}",`).join('\n');
  return `static const char* const ${name}[] = {\n${body}\n};\n`;
}

const content = `// 由 tools/gen/gen-diag-channels.mjs 从
// core/common/src/main/resources/diag_channels.tsv 生成。**不要手改**。
//
// 消费者：native-hook.cpp 的 is_container_diag_path()（shared + kotlin + cpp）
#pragma once

${arrayBlock('kDiagMarkersShared', sections.shared)}
${arrayBlock('kDiagMarkersKotlinOnly', sections.kotlin)}
${arrayBlock('kDiagMarkersCppOnly', sections.cpp)}`;

if (CHECK) {
  const current = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8') : '';
  if (current !== content) {
    console.error('✗ .inc 与 TSV 不一致，请运行：node tools/gen/gen-diag-channels.mjs');
    process.exit(1);
  }
  console.log(
    `✓ 诊断白名单一致（shared=${sections.shared.length} kotlin=${sections.kotlin.length} cpp=${sections.cpp.length}）`
  );
  process.exit(0);
}

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, content);
console.log(
  `✓ 已生成 ${path.relative(REPO, OUT)}（shared=${sections.shared.length} kotlin=${sections.kotlin.length} cpp=${sections.cpp.length}）`
);

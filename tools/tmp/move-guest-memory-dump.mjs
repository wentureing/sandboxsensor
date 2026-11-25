// 把 VirtualApplicationManager 里的三个纯转储函数搬到 lifecycle/diag/GuestMemoryDump.kt
// 步骤：抽文本 -> 生成新文件（imports 按引用自动挑选）-> 从原文件删除（从后往前）
import fs from 'node:fs';

const root = 'core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle';
const srcPath = `${root}/VirtualApplicationManager.kt`;
const outDir = `${root}/diag`;
const outPath = `${outDir}/GuestMemoryDump.kt`;

const ranges = [
  { name: 'dumpGuestStacks', start: 977, end: 1037 },
  { name: 'dumpMemoryDex', start: 2757, end: 2961 },
  { name: 'le32', start: 2963, end: 2968 },
];

const lines = fs.readFileSync(srcPath, 'utf8').split('\n');
const picked = ranges.map((r) => ({
  name: r.name,
  text: lines.slice(r.start - 1, r.end).join('\n'),
}));

const body = picked
  .map((p) => p.text.replace(/^(\s*)private fun /gm, '$1fun '))
  .join('\n\n');
const imports = lines
  .filter((l) => l.startsWith('import '))
  .filter((l) => {
    const cls = l.split('.').pop().trim();
    return cls && body.includes(cls);
  });

// 同包（lifecycle）的类搬迁后需要显式 import
const needed = [];
if (body.includes('GuestBreadcrumb')) needed.push('import com.nextvm.core.virtualization.lifecycle.GuestBreadcrumb');

const header = [
  'package com.nextvm.core.virtualization.lifecycle.diag',
  '',
  ...Array.from(new Set([...imports, ...needed])).sort(),
  '',
  '/**',
  ' * guest 侧转储工具：线程栈 / 内存 dex / 小端读取。',
  ' *',
  ' * 从 VirtualApplicationManager 抽出（纯诊断，不含启动链控制流）；',
  ' * 说明见 docs/输出/变更/20261125-B4-VirtualApplicationManager拆包.md',
  ' */',
  'internal object GuestMemoryDump {',
  '',
  body.split('\n').map((l) => (l.trim().length ? '    ' + l : l)).join('\n'),
  '}',
  '',
].join('\n');

fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(outPath, header);

// 从原文件删除（倒序，保持行号有效）
let out = lines.slice();
for (const r of [...ranges].sort((a, b) => b.start - a.start)) {
  out.splice(r.start - 1, r.end - r.start + 1);
}
let updated = out.join('\n');
// 调用点改为 GuestMemoryDump.xxx
updated = updated.replace(/\bdumpGuestStacks\(/g, 'GuestMemoryDump.dumpGuestStacks(');
updated = updated.replace(/\bdumpMemoryDex\(/g, 'GuestMemoryDump.dumpMemoryDex(');
updated = updated.replace(/(?<!\.)\ble32\(/g, 'GuestMemoryDump.le32(');
// 加 import（放在其它 core.virtualization import 之后；没有则加在最后一条 import 后）
const importLine = 'import com.nextvm.core.virtualization.lifecycle.diag.GuestMemoryDump';
if (!updated.includes(importLine)) {
  const idx = updated.lastIndexOf('\nimport ');
  const end = updated.indexOf('\n', idx + 1);
  updated = updated.slice(0, end) + '\n' + importLine + updated.slice(end);
}
fs.writeFileSync(srcPath, updated);

console.log('wrote', outPath, header.split('\n').length, 'lines');
console.log('imports:', [...new Set([...imports, ...needed])].join(', ') || '(none)');

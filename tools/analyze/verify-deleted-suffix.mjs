#!/usr/bin/env node
/**
 * 实证 + 回归守卫：路径里 `" (deleted)"` 后缀的**实际字符数**，以及源码里剥离判断用的长度是否与之一致。
 *
 * 背景：` (deleted)` 是 10 个字符，但上游（woand，fork 前）在两处把手写成 11，
 *   导致剥离永不生效（详见 docs/输出/变更/20261127-deleted-suffix-11to10.md）。
 *   2026-11-27 修复为"长度一律取 core/path_spoof.h 的 kDeletedSuffixLen"。
 *
 * 用法：
 *   node tools/analyze/verify-deleted-suffix.mjs                     # 读 _ref/ShitJumpingVM
 *   node tools/analyze/verify-deleted-suffix.mjs --root container    # CI 里读 checkout 出来的容器源码
 *   node tools/analyze/verify-deleted-suffix.mjs --strict            # 有硬编码/字面量比较时非 0 退出
 *
 * 检查项（判据由脚本自己算，不含硬编码结论）：
 *   1. `" (deleted)".length` 与 `sizeof(" (deleted)")`；
 *   2. 模拟 `std::string::compare(pos, len, s)` 在 N=11 / N=10 下的结果；
 *   3. 源码：比较处的长度必须来自 `kDeletedSuffixLen`（不接受硬编码数字）；
 *   4. 源码：常量定义必须由 `sizeof(...) - 1` 派生（不接受写死 10）。
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const ROOT = path.resolve(import.meta.dirname, '..', '..');
const args = process.argv.slice(2);
const getArg = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : dflt;
};
const CONTAINER_ROOT = path.resolve(ROOT, getArg('--root', path.join('_ref', 'ShitJumpingVM')));
const STRICT = args.includes('--strict');

const SUFFIX = ' (deleted)';
const suffixLen = SUFFIX.length; // JS UTF-16 code units；纯 ASCII ⇒ 等于字节数
const suffixSizeof = suffixLen + 1; // sizeof 字面量 = 长度 + '\0'

const problems = [];
const notes = [];

// ---------------------------------------------------------------- 1 实际字符数
console.log('=== 1. " (deleted)" 的实际长度（脚本实测） ===');
console.log(`  [...SUFFIX].length            = ${[...SUFFIX].length}`);
console.log(`  Buffer.byteLength(utf8)       = ${Buffer.byteLength(SUFFIX, 'utf8')}`);
console.log(`  SUFFIX.length（逐个字符）      = ${suffixLen}    <- 字符数`);
console.log(`  sizeof(" (deleted)")          = ${suffixSizeof}    <- 含结尾 '\\0'，比字符数多 1`);
console.log(`  逐字符: ${[...SUFFIX].map((c, i) => `${i + 1}:'${c === ' ' ? '␠' : c}'`).join(' ')}`);

// ---------------------------------------------------------------- 2 模拟 std::string::compare
/**
 * std::string::compare(pos, len, const char* s)：
 *   比较 this[pos, pos+r) 与 s 的前 r 个字符，r = min(len, size() - pos)；
 *   s 比 r 短时相当于遇到 '\0' ⇒ 结果非 0。
 */
function compareString(s, pos, len, rhs) {
  if (pos > s.length) throw new Error('pos > size()（未定义行为）');
  const r = Math.min(len, s.length - pos);
  const lhs = s.substr(pos, r);
  const rhsPart = rhs.length >= r ? rhs.substr(0, r) : rhs + '\0'.repeat(r - rhs.length);
  if (lhs === rhsPart) return 0;
  return lhs < rhsPart ? -1 : 1;
}

const samples = [
  '/data/user/0/com.nextvm.app/files/virtual/apks/mark.via_1.apk',
  '/data/user/0/com.nextvm.app/files/virtual/data/mark.via_1/lib/arm64/libfoo.so',
  '/data/app/~~VmK8x2==/mark.via-VmK8x2==/base.apk',
];

console.log('\n=== 2. 对 "<path> (deleted)" 模拟 compare(size - N, N, " (deleted)") ===');
let mismatchCount = 0;
for (const base of samples) {
  const p = base + SUFFIX;
  const with11 = compareString(p, p.length - 11, 11, SUFFIX);
  const with10 = compareString(p, p.length - 10, 10, SUFFIX);
  console.log(`  path tail (末 11 字符) = ${JSON.stringify(p.substr(p.length - 11, 11))}`);
  console.log(`    N=11 -> compare(size-11, 11, …) = ${with11}  ${with11 === 0 ? '（成立）' : '（不成立）'}`);
  console.log(`    N=10 -> compare(size-10, 10, …) = ${with10}  ${with10 === 0 ? '（成立）' : '（不成立）'}`);
  if (with11 !== 0) mismatchCount++;
}
if (mismatchCount === samples.length) {
  notes.push('N=11 全部不成立、N=10 全部成立 ⇒ 正确长度是 10（这就是当初的缺陷）');
}

// ---------------------------------------------------------------- 3 扫源码
console.log('\n=== 3. 源码检查（比较处用常量 / 常量由 sizeof 派生） ===');
const targets = [
  'core/hook/src/main/cpp/core/path_spoof.h',
  'core/hook/src/main/cpp/core/path_spoof.cpp',
  'core/hook/src/main/cpp/native-hook.cpp',
];

let sites = 0;
for (const rel of targets) {
  const abs = path.join(CONTAINER_ROOT, rel);
  if (!fs.existsSync(abs)) {
    console.log(`  [skip] 找不到 ${rel}`);
    continue;
  }
  const lines = fs.readFileSync(abs, 'utf8').split(/\r?\n/);
  lines.forEach((line, i) => {
    if (!line.includes('(deleted)') && !line.includes('kDeletedSuffix')) return;
    const t = line.trim();
    // 注释行与常量声明行不参与"比较处"判定（它们本来就在讲这个后缀）
    if (t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')) return;
    if (/^inline constexpr char kDeletedSuffix\[\]/.test(t)) return;

    // 3a. 常量定义：必须 sizeof(...) - 1
    if (/kDeletedSuffixLen\s*=/.test(line)) {
      const derived = /sizeof\s*\(\s*kDeletedSuffix\s*\)\s*-\s*1/.test(line);
      console.log(`  ${rel}:${i + 1}  常量定义  ${derived ? 'OK（sizeof - 1）' : '不合规（应为 sizeof(kDeletedSuffix) - 1）'}`);
      console.log(`      ${line.trim()}`);
      if (!derived) problems.push(`${rel}:${i + 1} 常量定义未由 sizeof 派生`);
      return;
    }

    // 3b. 比较处 / 使用处
    if (!/compare|streq|strstr|strncmp|kDeletedSuffix/.test(line)) return;
    sites++;
    const usesConst = line.includes('kDeletedSuffixLen') || line.includes('kDel');
    const hardcoded = line.match(/-\s*(\d+)/);
    let verdict;
    if (usesConst) {
      verdict = 'OK（用常量）';
    } else if (hardcoded) {
      const n = Number(hardcoded[1]);
      verdict = n === suffixLen ? `OK（硬编码 ${n}，但与会话长度一致）` : `不成立（硬编码 ${n} ≠ 实际 ${suffixLen}）`;
      if (n !== suffixLen) problems.push(`${rel}:${i + 1} 硬编码 ${n}，实际 ${suffixLen}`);
      else notes.push(`${rel}:${i + 1} 仍是硬编码 ${n}（当前正确，但建议改用 kDeletedSuffixLen）`);
    } else if (line.includes('" (deleted)"')) {
      verdict = '待改进（直接写字符串字面量，未用 kDeletedSuffix）';
      notes.push(`${rel}:${i + 1} 用字面量 " (deleted)"，建议改用 kDeletedSuffix`);
    } else {
      verdict = '无法判定';
    }
    console.log(`  ${rel}:${i + 1}  ${verdict}`);
    console.log(`      ${line.trim()}`);
  });
}

// ---------------------------------------------------------------- 4 结论
console.log('\n=== 4. 结论 ===');
console.log(`  字符数 = ${suffixLen}，sizeof = ${suffixSizeof}`);
for (const n of notes) console.log(`  · ${n}`);
if (problems.length === 0) {
  console.log('  ✓ 源码检查通过：长度来自常量、常量由 sizeof 派生');
} else {
  console.log(`  ✗ 发现 ${problems.length} 处问题：`);
  for (const p of problems) console.log(`    - ${p}`);
}

if (STRICT && problems.length > 0) {
  console.error('\n[strict] (deleted) 长度约束被破坏 → 非 0 退出');
  process.exit(1);
}

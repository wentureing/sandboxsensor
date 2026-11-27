#!/usr/bin/env node
/**
 * 实证：路径里 `" (deleted)"` 后缀的**实际字符数**，以及源码里 `compare(size - N, N, " (deleted)")`
 * 这类判断在当前 N 下**到底能不能成立**。用可复跑的脚本代替"我说它是 10 个字符"。
 *
 * 背景：N1a（2026-11-27）抽取 `path_spoof` 时，golden 期望"保留 (deleted) 后缀"而实测被丢弃，
 *   排查发现两处源码都按 **11** 比较。本脚本把这条结论固化为可执行证据。
 *
 * 用法：
 *   node tools/analyze/verify-deleted-suffix.mjs                     # 读 _ref/ShitJumpingVM
 *   node tools/analyze/verify-deleted-suffix.mjs --root container    # CI 里读 checkout 出来的容器源码
 *   node tools/analyze/verify-deleted-suffix.mjs --strict            # 仍按 11 比较时以非 0 退出（防回退用）
 *
 * 判据（脚本自己算，不含硬编码结论）：
 *   1. `" (deleted)".length` 与 `sizeof(" (deleted)")`（= length + 1，含结尾 '\0'）；
 *   2. 对每个真实形态的路径，模拟 `std::string::compare(pos, len, s)` 的结果；
 *   3. 扫源码中所有 `(deleted)` 比较点，报出它用的长度与判定结果是否可能成立。
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

const SUFFIX = ' (deleted)'; // 空间 + "(deleted)"

// ---------------------------------------------------------------- 1 实际字符数
const suffixLen = SUFFIX.length; // JS UTF-16 code units；纯 ASCII ⇒ 等于字节数
const suffixSizeof = suffixLen + 1; // sizeof 字面量 = 长度 + '\0'

console.log('=== 1. " (deleted)" 的实际长度（脚本实测） ===');
console.log(`  [...SUFFIX].length            = ${[...SUFFIX].length}`);
console.log(`  Buffer.byteLength(utf8)       = ${Buffer.byteLength(SUFFIX, 'utf8')}`);
console.log(`  SUFFIX.length（逐个字符）      = ${suffixLen}    <- 字符数`);
console.log(`  sizeof(" (deleted)")          = ${suffixSizeof}    <- 含结尾 '\\0'，比字符数多 1`);
console.log(`  逐字符: ${[...SUFFIX].map((c, i) => `${i + 1}:'${c === ' ' ? '␠' : c}'`).join(' ')}`);

// ---------------------------------------------------------------- 2 模拟 std::string::compare
/**
 * std::string::compare(pos, len, const char* s) 的语义：
 *   比较 this[pos, pos+r) 与 s 的前 r 个字符，其中 r = min(len, size() - pos)。
 *   s 若比 r 短，则相当于在 s 的结尾遇到 '\0' —— 结果非 0。
 */
function compareString(s, pos, len, rhs) {
  const r = Math.min(len, s.length - pos);
  if (pos > s.length) throw new Error('pos > size()（未定义行为）');
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
  const tail11 = p.substr(p.length - 11, 11);
  console.log(`  path tail (末 11 字符) = ${JSON.stringify(tail11)}`);
  console.log(`    N=11 -> compare(size-11, 11, " (deleted)") = ${with11}  ${with11 === 0 ? '（成立）' : '（不成立）'}`);
  console.log(`    N=10 -> compare(size-10, 10, " (deleted)") = ${with10}  ${with10 === 0 ? '（成立）' : '（不成立）'}`);
  if (with11 !== 0) mismatchCount++;
}

// ---------------------------------------------------------------- 3 扫源码
console.log('\n=== 3. 源码里的 (deleted) 比较点 ===');
const targets = [
  'core/hook/src/main/cpp/core/path_spoof.cpp',
  'core/hook/src/main/cpp/native-hook.cpp',
];
let sites = 0;
const defects = [];
for (const rel of targets) {
  const abs = path.join(CONTAINER_ROOT, rel);
  if (!fs.existsSync(abs)) {
    console.log(`  [skip] 找不到 ${rel}`);
    continue;
  }
  const lines = fs.readFileSync(abs, 'utf8').split(/\r?\n/);
  lines.forEach((line, i) => {
    if (!line.includes('(deleted)')) return;
    if (!/compare|streq|strstr|strncmp/.test(line)) return;
    sites++;
    // 抽出这一行里参与比较的长度常量：形如 size() - 11 / clen - 11
    const m = line.match(/-\s*(\d+)/);
    const n = m ? Number(m[1]) : null;
    const verdict = n === suffixLen ? 'OK' : n === null ? '无法判定' : `不成立（N=${n} ≠ 实际 ${suffixLen}）`;
    console.log(`  ${rel}:${i + 1}  N=${n === null ? '?' : n}  ${verdict}`);
    console.log(`      ${line.trim()}`);
    if (n !== null && n !== suffixLen) defects.push(`${rel}:${i + 1} 用 ${n}，实际 ${suffixLen}`);
  });
}

// ---------------------------------------------------------------- 4 结论
console.log('\n=== 4. 结论（脚本推出，非人为断言） ===');
console.log(`  字符数 = ${suffixLen}，sizeof = ${suffixSizeof}`);
console.log(`  用 N=11 比较：${mismatchCount === samples.length ? '三个样本全部不成立 ⇒ 剥离永不生效' : '部分成立（异常，需复核）'}`);
console.log(`  用 N=10 比较：成立 ⇒ 正确写法是 10`);
if (defects.length === 0) {
  console.log('  源码比较点：无缺陷（已全部使用正确长度）');
} else {
  console.log(`  源码比较点：发现 ${defects.length} 处长度与字符数不符`);
  for (const d of defects) console.log(`    - ${d}`);
  console.log('  说明：这是 N1a 之前既有的缺陷（见 docs/代码镜像/core/hook/cpp/core/path_spoof.md 偏差登记 #6）；');
  console.log('        修它属行为变更（R3 类），需同时改 sc_rewrite_fd_target 并重记 maps_lines/maps_hidden 基线。');
}

if (STRICT && defects.length > 0) {
  console.error('\n[strict] 仍存在 (deleted) 长度缺陷 → 非 0 退出');
  process.exit(1);
}

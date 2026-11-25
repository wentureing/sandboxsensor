// 用本地 AOSP 源码核对「Java 层 hook 覆盖」：三族方法 × 是否已 RegisterNatives。
//
// 用法：
//   node tools/lint/check-hook-coverage.mjs
//   node tools/lint/check-hook-coverage.mjs --aosp tools/tmp/libcore-src --impl _ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp
//   --strict  : 有 P0 缺口时退出码 1（默认只提示）
//
// 契约基线见 docs/输出/调研/调研-AOSP-hook契约对照.md
import fs from 'node:fs';
import path from 'node:path';

const args = process.argv.slice(2);
const getArg = (name, def) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : def;
};
const AOSP = getArg('--aosp', 'tools/tmp/libcore-src');
const IMPL = getArg('--impl', '_ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp');
const STRICT = args.includes('--strict');

const FILES = {
  linux: 'luni/src/main/java/libcore/io/Linux.java',
  ufs: 'ojluni/src/main/java/java/io/UnixFileSystem.java',
  nio: 'ojluni/src/main/java/sun/nio/fs/UnixNativeDispatcher.java',
};

function readAosp(rel) {
  const p = path.join(AOSP, rel);
  if (!fs.existsSync(p)) return null;
  return fs.readFileSync(p, 'utf8');
}

/** 从 AOSP 源码提取 native 方法名（跳过整行注释）。 */
function nativeNames(src, re) {
  if (!src) return [];
  const out = [];
  for (const line of src.split('\n')) {
    if (/^\s*\/\//.test(line)) continue;
    const m = line.match(re);
    if (m) out.push(m[1]);
  }
  return out;
}

/** 从 native-hook.cpp 的 install_java_io_hooks 里提取已注册的方法名。 */
function registered(src, startMark, endMark) {
  const s = src.indexOf(startMark);
  if (s < 0) return [];
  const e = src.indexOf(endMark, s);
  const seg = src.slice(s, e < 0 ? src.length : e);
  return [...seg.matchAll(/\{const_cast<char\*>\("([A-Za-z0-9_]+)"\)/g)].map((m) => m[1]);
}

// Linux 里与「路径翻译」相关的子集（其余是 socket/进程/内存，不需要路径规则）
const LINUX_PATH = new Set([
  'open', 'access', 'stat', 'lstat', 'fstat', 'mkdir', 'unlink', 'rename',
  'readlink', 'chmod', 'fchmod', 'chown', 'fchown', 'lchown',
  'link', 'symlink', 'statvfs', 'fstatvfs', 'ftruncate', 'opendir', 'readdir',
]);
// NIO 里与路径/目录相关的子集
const NIO_PATH = new Set([
  'open0', 'openat0', 'access0', 'exists0', 'stat0', 'stat1', 'lstat0', 'fstat', 'fstatat0',
  'mkdir0', 'rmdir0', 'unlink0', 'unlinkat0', 'rename0', 'renameat0',
  'link0', 'symlink0', 'readlink0', 'realpath0', 'chmod0', 'lchown0', 'chown0', 'fchmod', 'fchown',
  'opendir0', 'fdopendir', 'closedir', 'readdir', 'statvfs0', 'mknod0', 'utimes0', 'futimes',
  'fopen0', 'close', 'fclose', 'dup', 'pathconf0', 'fpathconf',
]);

// 三类标注（判据见 docs/输出/调研/调研-AOSP-hook契约对照.md §五）
// 设计取舍：不影响隔离/可用/泄露，或补齐风险大于收益
const DELIBERATE = new Set([
  'getSpace0', 'statvfs', 'fstatvfs', 'statvfs0', 'pathconf0', 'fpathconf',
  'getNameMax0', 'ftruncate', 'lseek', 'fsync', 'fdatasync', 'utimes', 'utimes0', 'futimes',
  'chown', 'fchown', 'lchown', 'chown0', 'lchown0',
  'link', 'symlink', 'link0', 'symlink0', 'mknod0',
  'dup', 'dup2', 'close', 'fclose', 'fopen0',
]);
// 疑似缺陷：泄露真实路径 / 影响可用性
const SUSPECT = new Set(['readlink', 'readlink0', 'setPermission0', 'chmod', 'fchmod', 'chmod0']);
// 待评估：NIO 主族（是否构成隔离漏洞需探针实测）
const EVAL = new Set([
  'open0', 'openat0', 'access0', 'exists0', 'stat0', 'stat1', 'lstat0', 'fstat', 'fstatat0',
  'mkdir0', 'rmdir0', 'unlink0', 'unlinkat0', 'rename0', 'renameat0',
  'opendir0', 'fdopendir', 'closedir', 'readdir', 'realpath0',
]);

function classify(name) {
  if (SUSPECT.has(name)) return '疑似缺陷';
  if (DELIBERATE.has(name)) return '设计取舍';
  if (EVAL.has(name)) return '待评估';
  return '待评估';
}

const implSrc = fs.readFileSync(IMPL, 'utf8');
const haveAosp = fs.existsSync(path.join(AOSP, FILES.linux));
if (!haveAosp) {
  console.log(`⚠️ 未找到 AOSP 源码（${AOSP}）—— 先执行：`);
  console.log(`   git -C ${AOSP} checkout HEAD -- ${FILES.linux} ${FILES.ufs} ${FILES.nio}`);
  console.log('   （浅克隆时文件只在 git 对象里，需要 checkout 到工作区）');
}
const regLinux = new Set(registered(implSrc, '"libcore/io/Linux"', '"java/io/UnixFileSystem"'));
const regUfs = new Set(registered(implSrc, '"java/io/UnixFileSystem"', 'installJavaIoHooks: SUCCESS'));

const aospLinux = nativeNames(readAosp(FILES.linux), /^\s*public native [\w.\[\]<>]+ (\w+)\(/).filter((n) => LINUX_PATH.has(n));
const aospUfs = nativeNames(readAosp(FILES.ufs), /^\s*private native [\w.\[\]<>]+ (\w+)\(/);
const aospNio = nativeNames(readAosp(FILES.nio), /^\s*(?:private )?static native [\w.\[\]<>]+ (\w+)\(/).filter((n) => NIO_PATH.has(n));

function report(title, aospNames, registeredSet, note) {
  const missing = aospNames.filter((n) => !registeredSet.has(n));
  const extra = [...registeredSet].filter((n) => !aospNames.includes(n));
  console.log(`\n=== ${title}（AOSP ${aospNames.length} 个相关方法）${note ? ' — ' + note : ''}`);
  for (const n of aospNames) {
    const covered = registeredSet.has(n);
    const tag = covered ? '' : `  [${classify(n)}]`;
    console.log(`  ${(covered ? '✅' : '❌').padEnd(3)} ${n}${tag}`);
  }
  if (extra.length) console.log(`  ⚠️ 注册了但 AOSP 未声明/不在相关集合：${extra.join(', ')}`);
  console.log(`  -> 已覆盖 ${aospNames.length - missing.length}/${aospNames.length}，缺 ${missing.length}`);
  return missing;
}

const missLinux = report('A 族 libcore.io.Linux', aospLinux, regLinux);
const missUfs = report('B 族 java.io.UnixFileSystem', aospUfs, regUfs);
const missNio = report('NIO sun.nio.fs.UnixNativeDispatcher', aospNio, new Set(), '整族未覆盖');

const allMissing = [...missLinux, ...missUfs, ...missNio];
const byClass = { '疑似缺陷': [], '待评估': [], '设计取舍': [] };
for (const n of allMissing) byClass[classify(n)].push(n);

console.log('\n=== 汇总（按三类）');
console.log(`  疑似缺陷（该补）：${byClass['疑似缺陷'].join(', ') || '无'}`);
console.log(`  待评估（先探针）：${byClass['待评估'].length} 个${byClass['待评估'].length ? '：' + byClass['待评估'].slice(0, 12).join(', ') + (byClass['待评估'].length > 12 ? ' …' : '') : ''}`);
console.log(`  设计取舍（登记不补）：${byClass['设计取舍'].length} 个`);
console.log('  契约基线：docs/输出/调研/调研-AOSP-hook契约对照.md');

if (STRICT && byClass['疑似缺陷'].length) process.exit(1);

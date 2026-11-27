#!/usr/bin/env node
/**
 * 归一化工作区换行符：文本 → LF，Windows 批处理 → CRLF，二进制不碰。
 *
 * 为什么需要：容器仓库历史上没有 `.gitattributes`，而本机 `core.autocrlf=true`
 *   ⇒ checkout 出来的 .kt/.xml/.kts 是 CRLF，git 反复打
 *   `warning: LF will be replaced by CRLF …`，且**任何**逐字节比较的资产
 *   （`src/test/resources/golden/*.tsv`、`breadcrumb-golden/*.log`）一旦被 checkout 成 CRLF，
 *   本地/CI 的字符串断言就会因为行尾多一个 `\r` 而红。
 *
 * 用法：
 *   node tools/git/normalize-eol.mjs _ref/ShitJumpingVM            # 就地归一
 *   node tools/git/normalize-eol.mjs _ref/ShitJumpingVM --dry-run  # 只报告
 *
 * 约束：不 spawn 子进程（受限沙箱里 stdio 管道会 EPERM），只用 fs 遍历。
 *       批处理（.bat/.cmd）反而要确保 CRLF，否则 Windows 上跑不起来。
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const ROOT = path.resolve(process.argv[2] ?? '.');
const DRY = process.argv.includes('--dry-run');

// 文本扩展名（其余一律当二进制跳过）
const TEXT_EXT = new Set([
  '.kt', '.kts', '.java', '.xml', '.yml', '.yaml', '.md', '.txt', '.json',
  '.cpp', '.h', '.c', '.cc', '.hpp', '.inc', '.cmake', '.gradle', '.properties',
  '.pro', '.tsv', '.log', '.cfg', '.ini', '.pro', '.py', '.mjs', '.js', '.ts',
  '.ps1', '.sh', '.gitignore', '.gitattributes', '.editorconfig', '.toml', '',
]);
// 必须 CRLF 的（Windows 批处理）
const CRLF_EXT = new Set(['.bat', '.cmd']);
// 跳过的目录
const SKIP_DIRS = new Set([
  '.git', 'build', '.gradle', 'node_modules', '.idea', '.cxx', 'obj', 'intermediates',
  'tmp', 'dist', 'out', '.kotlin',
]);

const stats = { scanned: 0, toLf: 0, toCrlf: 0, skippedBinary: 0, unchanged: 0, errors: 0 };

function toLf(buf) {
  // \r\n → \n，孤立的 \r（老 Mac）也一起归一
  const s = buf.toString('utf8');
  return s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}

function toCrlf(buf) {
  const s = buf.toString('utf8').replace(/\r\n/g, '\n');
  return s.replace(/\n/g, '\r\n');
}

function walk(dir) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const e of entries) {
    const abs = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (SKIP_DIRS.has(e.name)) continue;
      walk(abs);
      continue;
    }
    if (!e.isFile()) continue;

    const ext = path.extname(e.name).toLowerCase();
    const isGitFile = e.name === '.gitignore' || e.name === '.gitattributes';
    if (!TEXT_EXT.has(ext) && !isGitFile) {
      stats.skippedBinary++;
      continue;
    }

    stats.scanned++;
    let buf;
    try {
      buf = fs.readFileSync(abs);
    } catch {
      stats.errors++;
      continue;
    }
    // 含 NUL 的当二进制
    if (buf.includes(0)) {
      stats.skippedBinary++;
      continue;
    }

    const want = CRLF_EXT.has(ext) ? toCrlf(buf) : toLf(buf);
    if (want === buf.toString('utf8')) {
      stats.unchanged++;
      continue;
    }
    const crlf = CRLF_EXT.has(ext);
    if (DRY) {
      if (crlf) stats.toCrlf++; else stats.toLf++;
      continue;
    }
    try {
      fs.writeFileSync(abs, want, 'utf8');
      if (crlf) stats.toCrlf++; else stats.toLf++;
    } catch {
      stats.errors++;
    }
  }
}

if (!fs.existsSync(ROOT)) {
  console.error(`[normalize-eol] 路径不存在：${ROOT}`);
  process.exit(2);
}

walk(ROOT);

const rel = path.relative(process.cwd(), ROOT) || '.';
console.log(`[normalize-eol] root=${rel}${DRY ? '（dry-run，不写盘）' : ''}`);
console.log(`  文本文件扫描 : ${stats.scanned}`);
console.log(`  转为 LF      : ${stats.toLf}`);
console.log(`  转为 CRLF    : ${stats.toCrlf}   （.bat/.cmd）`);
console.log(`  已是目标行尾 : ${stats.unchanged}`);
console.log(`  跳过（二进制）: ${stats.skippedBinary}`);
if (stats.errors) console.log(`  读取/写入失败 : ${stats.errors}`);
console.log('  提示：写完用 `git add --renormalize .` 让 index 与工作区一致（index 若本来就是 LF，不会产生 diff）。');

process.exit(stats.errors > 0 ? 1 : 0);

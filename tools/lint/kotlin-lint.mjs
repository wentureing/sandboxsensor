#!/usr/bin/env node
// 本地快速 Kotlin 静态检查（毫秒级，不需要 Gradle / Android SDK）。
//
// 为什么自写：detekt/ktlint/semgrep 都不做完整类型解析，查不出「未定义符号 / 私有成员越界」；
// 那类错误只有编译器能查（要 Gradle+SDK，分钟级）。所以这里只补「编译器之前就能抓到的低级错误」。
//
// 用法：
//   node tools/lint/kotlin-lint.mjs <文件或目录> [...]
//   node tools/lint/kotlin-lint.mjs --deps <被迁移的源文件> <引用它的新文件> [...]
//
// 检查项：
//   1. 括号平衡（剥离字符串/注释后）
//   2. 注释块内出现 `/*`（Kotlin 块注释可嵌套 -> 会导致 Unclosed comment）
//   3. 文件结束时注释/括号未闭合
//   4. --deps：源文件里的 private 成员是否被新文件引用（拆分迁移最常见的坑）
//
// 退出码：0 = 通过，1 = 发现问题。

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

const args = process.argv.slice(2);
const depsMode = args[0] === '--deps';
const targets = depsMode ? args.slice(1) : args;

if (targets.length === 0) {
  console.error('用法: node tools/lint/kotlin-lint.mjs <文件或目录> [...]');
  console.error('      node tools/lint/kotlin-lint.mjs --deps <源文件> <新文件> [...]');
  process.exit(2);
}

/** 递归收集 .kt 文件 */
function collect(p) {
  const out = [];
  const st = statSync(p);
  if (st.isDirectory()) {
    for (const name of readdirSync(p)) {
      if (name === 'build' || name === '.git' || name === 'node_modules') continue;
      out.push(...collect(join(p, name)));
    }
  } else if (extname(p) === '.kt') {
    out.push(p);
  }
  return out;
}

/**
 * 扫描一个 Kotlin 文件。
 * 返回 { issues: string[], code: string }，code 是剥离字符串/注释后的代码（供括号检查）。
 */
function scan(file) {
  const src = readFileSync(file, 'utf8');
  const issues = [];
  let i = 0;
  let line = 1;
  let state = 'code'; // code | line | block | str | chr | raw
  let commentDepth = 0;
  let commentStartLine = 0;
  let code = '';

  while (i < src.length) {
    const c = src[i];
    const d = src[i + 1];
    const e = src[i + 2];

    if (c === '\n') line++;

    if (state === 'code') {
      if (c === '/' && d === '/') { state = 'line'; i += 2; continue; }
      if (c === '/' && d === '*') {
        state = 'block'; commentDepth = 1; commentStartLine = line; i += 2; continue;
      }
      if (c === '"' && d === '"' && e === '"') { state = 'raw'; i += 3; continue; }
      if (c === '"') { state = 'str'; i++; continue; }
      if (c === "'") { state = 'chr'; i++; continue; }
      code += c; i++; continue;
    }

    if (state === 'line') {
      if (c === '\n') { state = 'code'; code += '\n'; }
      i++; continue;
    }

    if (state === 'block') {
      if (c === '/' && d === '*') {
        // Kotlin 块注释可嵌套：KDoc 里写 `/proc/self/*` 这类内容会打开新注释，
        // 之后那个 KDoc 的 `*/` 只能关掉内层，外层一路裸到文件末尾 -> Unclosed comment。
        // 一旦发生，后续所有注释判断都会连锁失效，所以第一个必须当成根因修。
        if (commentDepth === 1) {
          issues.push(
            `${file}:${line}: 注释块内出现 '/*' —— 这是根因（Kotlin 块注释可嵌套，` +
            `这个 '/*' 会吞掉后面所有内容，最终报 Unclosed comment）。` +
            `改成「... 下的任意文件」这类文字描述。`
          );
        } else {
          issues.push(`${file}:${line}: 注释块内出现 '/*'（嵌套深度 ${commentDepth}，属上面的连锁）`);
        }
        commentDepth++; i += 2; continue;
      }
      if (c === '*' && d === '/') {
        commentDepth--;
        if (commentDepth === 0) state = 'code';
        i += 2; continue;
      }
      i++; continue;
    }

    if (state === 'str') {
      if (c === '\\') { i += 2; continue; }
      if (c === '"') { state = 'code'; i++; continue; }
      i++; continue;
    }

    if (state === 'chr') {
      if (c === '\\') { i += 2; continue; }
      if (c === "'") { state = 'code'; i++; continue; }
      i++; continue;
    }

    if (state === 'raw') {
      if (c === '"' && d === '"' && e === '"') { state = 'code'; i += 3; continue; }
      i++; continue;
    }
  }

  if (state === 'block') {
    issues.push(`${file}:${commentStartLine}: 注释未闭合（从第 ${commentStartLine} 行的 '/*' 开始）`);
  }

  return { issues, code };
}

/** 括号平衡检查 */
function checkBrackets(file, code) {
  const issues = [];
  const pairs = { '(': ')', '[': ']', '{': '}' };
  const stack = [];
  let line = 1;
  for (const c of code) {
    if (c === '\n') line++;
    if (pairs[c]) { stack.push({ c, line }); continue; }
    if (c === ')' || c === ']' || c === '}') {
      const top = stack.pop();
      if (!top || pairs[top.c] !== c) {
        issues.push(`${file}:${line}: 多余/不匹配的 '${c}'（栈顶=${top ? `'${top.c}'@${top.line}` : '空'}）`);
        return issues;
      }
    }
  }
  if (stack.length) {
    issues.push(`${file}: 括号未闭合 ${stack.map(s => `'${s.c}'@${s.line}`).join(', ')}`);
  }
  return issues;
}

/** 提取 Kotlin 文件里的 private 成员名 */
function privateMembers(file) {
  const src = readFileSync(file, 'utf8');
  const out = new Map();
  const re = /\bprivate\s+(?:@\w+\s+)?(?:var|val|fun|class|object)\s+(\w+)/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    const line = src.slice(0, m.index).split('\n').length;
    out.set(m[1], line);
  }
  return out;
}

// ---------------- 主流程 ----------------

if (depsMode) {
  // 跨文件私有依赖：源文件的 private 成员被新文件引用 -> 编译期必挂
  const files = targets.flatMap(collect);
  if (files.length < 2) {
    console.error('--deps 需要至少两个文件：先是源文件，再是引用它的新文件');
    process.exit(2);
  }
  const srcFile = files[0];
  const others = files.slice(1);
  const privs = privateMembers(srcFile);
  let bad = 0;
  for (const other of others) {
    // 用剥离字符串/注释后的代码判断，避免 `contains("V125ClickWrapper")` 这类误报
    const { code } = scan(other);
    const lines = code.split('\n');
    for (const [name, line] of privs) {
      for (let i = 0; i < lines.length; i++) {
        if (new RegExp(`\\b${name}\\b`).test(lines[i])) {
          console.log(`${other}:${i + 1}: 引用了 ${srcFile.split(/[\\/]/).pop()} 的 private 成员 '${name}'（定义在第 ${line} 行）`);
          bad++;
        }
      }
    }
  }
  if (bad) { console.log(`\n共 ${bad} 处：跨文件访问 private -> 编译必失败`); process.exit(1); }
  console.log('跨文件 private 依赖检查 OK');
  process.exit(0);
}

const files = targets.flatMap(collect);
let problems = 0;
for (const file of files) {
  const { issues, code } = scan(file);
  const bracketIssues = checkBrackets(file, code);
  for (const s of [...issues, ...bracketIssues]) { console.log(s); problems++; }
}
if (problems) { console.log(`\n共 ${problems} 处问题`); process.exit(1); }
console.log(`检查通过（${files.length} 个文件）`);

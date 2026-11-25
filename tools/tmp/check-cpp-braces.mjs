// 轻量 C++ 结构自检：跳过注释/字符串/字符字面量后检查 () {} 是否配对。
// 不能替代编译，只用来在无编译器环境下拦住"少一个右括号"这类低级错误。
import fs from 'node:fs';

const files = process.argv.slice(2);
let bad = 0;

for (const f of files) {
  const src = fs.readFileSync(f, 'utf8');
  const stack = [];
  let i = 0;
  let line = 1;
  let err = null;
  const push = (ch, ln) => stack.push({ ch, ln });
  const pop = (want, ln) => {
    const top = stack.pop();
    if (!top) { err = `第 ${ln} 行: 多出 '${want}'`; return; }
    if (top.ch !== want) { err = `第 ${ln} 行: 期望 '${want}' 但栈顶是 '${top.ch}' (第 ${top.ln} 行)`; }
  };
  while (i < src.length && !err) {
    const c = src[i];
    const n = src[i + 1];
    if (c === '\n') { line++; i++; continue; }
    if (c === '/' && n === '/') { while (i < src.length && src[i] !== '\n') i++; continue; }
    if (c === '/' && n === '*') { i += 2; while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) { if (src[i] === '\n') line++; i++; } i += 2; continue; }
    if (c === '"' || c === "'") {
      const quote = c; i++;
      while (i < src.length && src[i] !== quote) { if (src[i] === '\\') i++; if (src[i] === '\n') line++; i++; }
      i++; continue;
    }
    if (c === '(' || c === '[' || c === '{') push(c, line);
    else if (c === ')') pop('(', line);
    else if (c === ']') pop('[', line);
    else if (c === '}') pop('{', line);
    i++;
  }
  if (!err && stack.length) err = `未闭合: ${stack.map((s) => `'${s.ch}'(第 ${s.ln} 行)`).join(', ')}`;
  if (err) { console.log(`FAIL ${f}: ${err}`); bad++; } else { console.log(`OK   ${f}`); }
}
process.exit(bad === 0 ? 0 : 1);

// 定位 Kotlin 顶层/成员函数的行区间（含前置 KDoc），用于机械搬家。
import fs from 'node:fs';

const file = process.argv[2];
const names = process.argv.slice(3);
const src = fs.readFileSync(file, 'utf8');
const lines = src.split('\n');

function findFnStart(name) {
  const re = new RegExp(`^\\s*(private |internal |public )?fun ${name}\\(`);
  for (let i = 0; i < lines.length; i++) {
    if (re.test(lines[i])) return i;
  }
  return -1;
}

function fnEnd(startIdx) {
  // 从 fun 行开始找第一个 '{'，然后括号配对（跳过字符串/注释）
  let i = startIdx;
  let started = false;
  let depth = 0;
  while (i < lines.length) {
    const line = lines[i];
    for (let j = 0; j < line.length; j++) {
      const c = line[j];
      const n = line[j + 1];
      if (c === '/' && n === '/') break;
      if (c === '"') {
        // 跳过字符串（本文件无复杂模板）
        j++;
        while (j < line.length && line[j] !== '"') { if (line[j] === '\\') j++; j++; }
        continue;
      }
      if (c === '{') { depth++; started = true; }
      else if (c === '}') {
        depth--;
        if (started && depth === 0) {
          // 函数结束行 = i
          // 向前找 KDoc 起点
          let s = startIdx;
          while (s > 0) {
            const prev = lines[s - 1].trim();
            if (prev.startsWith('*') || prev.startsWith('/**') || prev.startsWith('//')) s--;
            else break;
          }
          return { start: s + 1, end: i + 1 }; // 1-based
        }
      }
    }
    i++;
  }
  return { start: startIdx + 1, end: -1 };
}

for (const name of names) {
  const s = findFnStart(name);
  if (s < 0) { console.log(`${name}: NOT FOUND`); continue; }
  const r = fnEnd(s);
  console.log(`${name}: ${r.start}-${r.end} (${r.end - r.start + 1} 行)`);
}

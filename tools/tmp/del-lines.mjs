// 按行号区间删除（1-based，含端点），从后往前删以免行号漂移。
// 用法：node tools/tmp/del-lines.mjs <file> <start> <end> [<start2> <end2> ...]
import fs from 'node:fs';

const [file, ...rest] = process.argv.slice(2);
if (!file || rest.length < 2) {
  console.error('usage: del-lines.mjs <file> <start> <end> [...]');
  process.exit(2);
}
const ranges = [];
for (let i = 0; i + 1 < rest.length; i += 2) {
  ranges.push([Number(rest[i]), Number(rest[i + 1])]);
}
ranges.sort((a, b) => b[0] - a[0]);

const lines = fs.readFileSync(file, 'utf8').split('\n');
for (const [start, end] of ranges) {
  if (!(start >= 1 && end >= start && end <= lines.length)) {
    console.error(`bad range ${start}-${end} (file has ${lines.length} lines)`);
    process.exit(2);
  }
  // 校验首行包含内容（防止删错位置）
  lines.splice(start - 1, end - start + 1);
}
fs.writeFileSync(file, lines.join('\n'));
console.log(`deleted ${ranges.length} range(s), now ${lines.length} lines`);

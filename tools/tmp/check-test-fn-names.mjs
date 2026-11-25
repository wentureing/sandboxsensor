// 检查 Kotlin 反引号函数名里是否含 JVM 非法字符（. ; [ ] / < > : \）
import fs from 'node:fs';

const files = process.argv.slice(2);
const ILLEGAL = /[.;\[\]\/<>:\\]/;
let bad = 0;
for (const f of files) {
  const src = fs.readFileSync(f, 'utf8');
  const re = /fun\s+`([^`]+)`/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    if (ILLEGAL.test(m[1])) {
      console.log(`${f}: 非法函数名 -> ${m[1]}`);
      bad++;
    }
  }
}
console.log(bad ? `共 ${bad} 个非法函数名` : '函数名全部合法');
process.exit(bad ? 1 : 0);

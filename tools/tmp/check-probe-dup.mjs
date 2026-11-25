// 检查探针检测项 ID 是否有重复注册（异常重复）。
// 只认三参形式 put("<ID>", "<group>", ...)，避免把 JSONObject.put("schema", ...) 当 ID。
import fs from 'node:fs';
import path from 'node:path';

const dir = process.argv[2] || 'probe/src/main/java/com/sandbox/probe';
const files = fs.readdirSync(dir).filter((f) => f.endsWith('.java'));
const byId = new Map();

for (const f of files) {
  const src = fs.readFileSync(path.join(dir, f), 'utf8');
  for (const m of src.matchAll(/\bput(?:Skipped)?\(\s*"([A-Za-z0-9_.]+)"\s*,\s*"[A-Za-z0-9_]+"\s*,/g)) {
    const id = m[1];
    if (!byId.has(id)) byId.set(id, []);
    byId.get(id).push(f);
  }
}

const dups = [...byId.entries()].filter(([, where]) => where.length > 1);
const byPrefix = new Map();
for (const id of byId.keys()) {
  const p = /^[A-Za-z]+/.exec(id)?.[0] ?? '?';
  byPrefix.set(p, (byPrefix.get(p) ?? 0) + 1);
}
console.log(`共 ${byId.size} 个探针 ID（${files.length} 个文件）`);
console.log('按前缀：' + [...byPrefix.entries()].map(([k, v]) => `${k}=${v}`).join(', '));
if (dups.length === 0) {
  console.log('没有重复 ID');
} else {
  console.log('重复 ID：');
  for (const [id, where] of dups) console.log(`  ${id} -> ${where.join(', ')}`);
}

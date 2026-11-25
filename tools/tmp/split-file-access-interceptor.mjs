// 一次性脚本：把 NativeHookBridge.kt 里的第二个类 FileAccessInterceptor 切到独立文件。
// 规则：head 原样写回；tail 加 package 与所需 import。
import fs from 'node:fs';
import path from 'node:path';

const dir = 'core/hook/src/main/java/com/nextvm/core/hook';
const bridge = path.join(dir, 'NativeHookBridge.kt');
const target = path.join(dir, 'FileAccessInterceptor.kt');
const src = fs.readFileSync(bridge, 'utf8');

const marker = '/**\n * FileAccessInterceptor';
const idx = src.indexOf(marker);
if (idx < 0) {
  console.error('marker not found');
  process.exit(1);
}

const head = src.slice(0, idx).replace(/\s+$/, '\n');
const tail = src.slice(idx);

const header = [
  'package com.nextvm.core.hook',
  '',
  'import timber.log.Timber',
  'import java.io.File',
  'import java.io.FileInputStream',
  'import java.io.FileOutputStream',
  '',
].join('\n');

fs.writeFileSync(bridge, head);
fs.writeFileSync(target, header + tail);

const lineOf = (s, i) => s.slice(0, i).split('\n').length;
console.log(`split at line ${lineOf(src, idx)}`);
console.log(`NativeHookBridge.kt -> ${head.split('\n').length} lines`);
console.log(`FileAccessInterceptor.kt -> ${(header + tail).split('\n').length} lines`);

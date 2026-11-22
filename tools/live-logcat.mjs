// 后台把 adb logcat 写入文件（不使用 pipe，避免受限模式 EPERM 与对话中断）
// 用法: node tools/live-logcat.mjs <serial|-> <outfile>
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const serialArg = process.argv[2] || '-';
const out = process.argv[3] || 'probe-reports/live-logcat.txt';
const dir = out.includes('/') ? out.substring(0, out.lastIndexOf('/')) : '.';
fs.mkdirSync(dir, { recursive: true });

const fd = fs.openSync(out, 'a');
const args = [];
if (serialArg && serialArg !== '-') args.push('-s', serialArg);
args.push('logcat', '-v', 'threadtime', '-b', 'all');

const child = spawn('adb', args, {
  stdio: ['ignore', fd, fd],
  detached: true,
  windowsHide: true,
});
child.unref();

const pidFile = out + '.pid';
fs.writeFileSync(pidFile, String(child.pid), 'utf8');
fs.writeSync(fd, `\n===== capture started ${new Date().toISOString()} pid=${child.pid} =====\n`);
console.log(`logcat started pid=${child.pid}`);
console.log(`out=${out}`);
console.log(`pidfile=${pidFile}`);
console.log('停止：taskkill /PID <pid> /T /F');

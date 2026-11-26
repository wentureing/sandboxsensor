#!/usr/bin/env node
/**
 * 把设备采集的 breadcrumb golden 归一化后写进容器仓库的测试资源。
 *
 * 用法：
 *   node tools/gen/gen-breadcrumb-golden.mjs            # 归一化 + 写盘
 *   node tools/gen/gen-breadcrumb-golden.mjs --check    # 只校验已入库 golden 是否一致（不写）
 *   node tools/gen/gen-breadcrumb-golden.mjs --in <dir> --out <dir>
 *
 * 为什么归一化：golden 必须是"可重复的行为样本"。设备原文里每轮都变的量
 * （pid / wall / identity hash / instance 时间戳 / 文件长度 / 规则条数）会让
 * 「两次启动是否一致」永远不成立，也会把 golden 变成"对当前输出的复制"。
 * 不归一化（否则契约失效）：包名、类名、authority、路径族名、事件名。
 *
 * 守卫（任一不满足即非 0 退出）：
 *   1. 同一 guest 的两个样本归一化后**逐行相等** —— 不等说明启动链不稳定
 *      （早期窗口 / 进程复用），这类样本不能当 golden；
 *   2. 每个样本末行是 `cb onActivityResumed` 或 `bind complete` 开头；
 *   3. 行数 >= 8，且含本进程的 `breadcrumb begin pkg=`。
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const ROOT = path.resolve(import.meta.dirname, '..', '..');
const DEFAULT_IN = path.join(ROOT, 'probe-reports', 'golden');
const DEFAULT_OUT = path.join(
  ROOT, '_ref', 'ShitJumpingVM', 'core', 'virtualization',
  'src', 'test', 'resources', 'breadcrumb-golden',
);

/** 采样组：同一 guest 的两个样本必须逐行一致（同一容器版本、同一路径） */
const GROUPS = [
  { guest: 'mark.via', tags: ['via-1', 'via-2'] },
  { guest: 'com.sandbox.probe', tags: ['probe-1', 'probe-2'] },
];

/** 单行归一化：去掉时间前缀 + 抹掉每轮必变、以及"重构就会变"的字面量 */
export function normalizeLine(line) {
  return line
    .replace(/^\[\+\d+ms\] \[[^\]]+\] /, '')  // [+123ms] [main]：顺序由行序表达，时间不锁
    .replace(/#\d+/g, '#<ID>')                // identity hash
    .replace(/_\d{13}/g, '_<TS>')             // instance 时间戳
    .replace(/\bpid=\d+/g, 'pid=<PID>')
    .replace(/\bwall=\d+/g, 'wall=<WALL>')
    .replace(/\bentries=\d+/g, 'entries=<N>')
    .replace(/\blen=\d+/g, 'len=<N>')
    .replace(/\brules=\d+/g, 'rules=<N>')
    .replace(/\bclId=\d+/g, 'clId=<CLID>')    // ClassLoader 的 identity hash
    // 堆栈里的构建指纹与行号：容器重编译 / 重构挪代码都会变，不属启动链行为
    .replace(/r8-map-id-[0-9a-f]+/g, 'r8-map-id-<MAPID>')
    .replace(/\.(kt|java):\d+/g, '.$1:<LINE>')
    .replace(/r8-map-id-<MAPID>:\d+/g, 'r8-map-id-<MAPID>:<LINE>');
}

function parseArgs(argv) {
  const opts = { in: DEFAULT_IN, out: DEFAULT_OUT, check: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--check') opts.check = true;
    else if (a === '--in') opts.in = path.resolve(argv[++i]);
    else if (a === '--out') opts.out = path.resolve(argv[++i]);
    else throw new Error(`未知参数：${a}`);
  }
  return opts;
}

function readSample(dir, tag) {
  const file = path.join(dir, `${tag}.log`);
  if (!fs.existsSync(file)) {
    throw new Error(`缺少样本 ${file}（先用 tools/device/capture-breadcrumb-golden.ps1 采集）`);
  }
  const raw = fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
  return raw.split('\n').filter((l) => l.trim() !== '').map(normalizeLine);
}

function assertUsable(tag, lines) {
  if (lines.length < 8) throw new Error(`${tag}: 只有 ${lines.length} 行，不像启动链样本`);
  const last = lines[lines.length - 1];
  if (!/^cb onActivityResumed /.test(last) && !/^bind complete /.test(last)) {
    throw new Error(`${tag}: 末行不是 resume/bind complete：${last.slice(0, 80)}`);
  }
  if (!lines.some((l) => /^breadcrumb begin pkg=/.test(l))) {
    throw new Error(`${tag}: 缺少本进程的 breadcrumb begin 行`);
  }
}

function assertStable(guest, a, b) {
  if (a.lines.length !== b.lines.length) {
    throw new Error(
      `${guest}: ${a.tag}(${a.lines.length} 行) 与 ${b.tag}(${b.lines.length} 行) 行数不同 —— 启动链不稳定`,
    );
  }
  for (let i = 0; i < a.lines.length; i++) {
    if (a.lines[i] !== b.lines[i]) {
      throw new Error(
        `${guest}: 第 ${i + 1} 行不一致\n  ${a.tag}: ${a.lines[i]}\n  ${b.tag}: ${b.lines[i]}`,
      );
    }
  }
}

function main() {
  const opts = parseArgs(process.argv);
  const out = new Map();
  const stable = [];

  for (const g of GROUPS) {
    const [a, b] = g.tags.map((tag) => ({ tag, lines: readSample(opts.in, tag) }));
    [a, b].forEach((s) => assertUsable(s.tag, s.lines));
    assertStable(g.guest, a, b);
    stable.push(`${g.guest}: ${a.tag}/${b.tag} 各 ${a.lines.length} 行，逐行一致 ✓`);
    out.set(a.tag, a.lines);
    out.set(b.tag, b.lines);
  }

  fs.mkdirSync(opts.out, { recursive: true });
  const done = [];
  for (const [tag, lines] of out) {
    const target = path.join(opts.out, `${tag}.log`);
    const body = lines.join('\n') + '\n';
    if (opts.check) {
      const old = fs.existsSync(target) ? fs.readFileSync(target, 'utf8') : '';
      if (old !== body) throw new Error(`${tag}: 入库 golden 与当前样本不一致（${target}）`);
      done.push(`${tag}: 一致 ✓`);
    } else {
      fs.writeFileSync(target, body, 'utf8');
      done.push(`${tag}: ${lines.length} 行 -> ${path.relative(ROOT, target)}`);
    }
  }

  console.log('归一化：去时间前缀 / #ID / _<TS> / pid / wall / entries / len / rules');
  stable.forEach((s) => console.log(`  ${s}`));
  done.forEach((s) => console.log(`  ${s}`));
  console.log(opts.check ? 'CHECK OK' : `已写入 ${path.relative(ROOT, opts.out)}`);
}

try {
  main();
} catch (e) {
  console.error(`[gen-breadcrumb-golden] ${e.message}`);
  process.exit(1);
}

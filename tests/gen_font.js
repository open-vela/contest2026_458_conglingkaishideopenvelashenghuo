// Generate the WristClaw LVGL bitmap font.
//
// The symbol list is passed as a raw argv entry (spawnSync without a shell),
// which keeps the 6800+ Chinese characters intact — going through a shell
// mangles them and risks hitting the command-line length limit.
//
// Produces a plain "lvgl" format C array that is memory-mapped from flash
// (--no-compress), so it can be XIP-read directly on the SF32LB52.

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const TESTS = __dirname;                       // <repo>/tests
const REPO = path.dirname(TESTS);              // <repo>

// lv_font_conv 的入口脚本。可用环境变量 LV_FONT_CONV 覆盖，或全局安装：
//   npm i -g lv_font_conv   ->  然后设 LV_FONT_CONV=$(npm root -g)/lv_font_conv/lv_font_conv.js
const CLI = process.env.LV_FONT_CONV || path.join(
  process.env.APPDATA || '', 'npm', 'node_modules', 'lv_font_conv', 'lv_font_conv.js');
const FONT = process.argv[2] || 'C:/Windows/Fonts/simhei.ttf';
const SYMBOLS = process.argv[3] || path.join(TESTS, 'font_symbols.txt');
const OUT = path.join(REPO, 'app', 'wristclaw', 'wc_font_16.h');

if (!fs.existsSync(CLI)) {
  console.error('找不到 lv_font_conv：' + CLI);
  console.error('请先 `npm i -g lv_font_conv`，或设 LV_FONT_CONV 指向 lv_font_conv.js');
  process.exit(1);
}
if (!fs.existsSync(SYMBOLS)) {
  console.error('找不到符号表：' + SYMBOLS);
  console.error('先跑 `python3 tests/gen_font_symbols.py tests/font_symbols.txt`');
  process.exit(1);
}

const symbols = fs.readFileSync(SYMBOLS, 'utf8');
const args = [
  CLI,
  '--font', FONT,
  // Explicit ASCII block: the symbol list is harvested from source literals,
  // which misses rare characters such as '?', '@' and '~'.
  '-r', '0x20-0x7F',
  '--symbols', symbols,
  '--size', '16',
  '--bpp', '4',
  '--format', 'lvgl',
  '--no-compress',
  '--lv-include', 'lvgl.h',
  '--lv-font-name', 'wc_font_16',
  '-o', OUT,
];

console.log('symbols:', symbols.length, '| font:', FONT);
const r = spawnSync(process.execPath, args, { encoding: 'utf8', maxBuffer: 1 << 28 });

if (r.stdout) process.stdout.write(r.stdout);
if (r.stderr) process.stderr.write(r.stderr);
if (r.status !== 0) {
  console.error('FAILED, status =', r.status);
  process.exit(r.status || 1);
}
const sz = fs.statSync(OUT).size;
console.log('OK ->', OUT, (sz / 1024 / 1024).toFixed(2), 'MB of C source');

// Bake a fallback into the public font descriptor.  The LVGL LV_SYMBOL_*
// icons live in the Private Use Area (0xF000-0xF8FF), which SimHei does not
// cover, so those glyphs resolve through the built-in Montserrat font.
// It has to be done statically: lv_font_conv emits `const lv_font_t`, so the
// field cannot be assigned at run time.
let src = fs.readFileSync(OUT, 'utf8');
const patched = src.replace('.fallback = NULL,', '.fallback = &lv_font_montserrat_16,');
if (patched === src) {
  console.error('WARN: could not find `.fallback = NULL,` to replace');
  process.exit(1);
}
fs.writeFileSync(OUT, patched);
console.log('OK    fallback -> lv_font_montserrat_16');

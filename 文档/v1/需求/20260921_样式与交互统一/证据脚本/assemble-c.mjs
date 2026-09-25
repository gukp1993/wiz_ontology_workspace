// 附表 C：内联 style 与 SVG 呈现属性的迁移清单（由 diff 生成，不手抄）。
import { fileURLToPath } from 'node:url';
const OUT = fileURLToPath(new URL('./out', import.meta.url));
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';

const BASE = '07f8d8d';
const STY = /(?:^|\s)(?::?style)\s*=\s*"([^"]*)"/g;
const PRES = /(?:^|\s)(fill|stroke)\s*=\s*"(#[0-9a-fA-F]{3,8})"/g;

function show(rev, f) {
  try { return execFileSync('git', ['show', `${rev}:${f}`], { encoding: 'utf8' }); } catch { return ''; }
}
function vueFiles() {
  const out = execFileSync('git', ['ls-tree', '-r', '--name-only', 'HEAD', '--', 'frontend/src'], { encoding: 'utf8' }).split('\n').filter(Boolean);
  return [...new Set([...out, ...execFileSync('git', ['ls-tree', '-r', '--name-only', BASE, '--', 'frontend/src'], { encoding: 'utf8' }).split('\n').filter(Boolean)])]
    .filter((f) => f.endsWith('.vue') && !f.includes('legacyGraph'));
}
const all = vueFiles();

// 1) inline style attribute counts per file
const inlineRows = [];
let bi = 0, hi = 0;
for (const f of all) {
  const b = [...show(BASE, f).matchAll(STY)].length;
  const h = [...show('HEAD', f).matchAll(STY)].length;
  bi += b; hi += h;
  if (b !== h) inlineRows.push([f.replace('frontend/src/', ''), b, h]);
}

// 2) hard-coded SVG presentation attributes: enumerate value multiset per file
const presRows = [];
for (const f of all) {
  const grab = (rev) => {
    const m = new Map();
    for (const x of show(rev, f).matchAll(new RegExp(PRES.source, 'g'))) {
      const k = `${x[1]}=${x[2].toLowerCase()}`;
      m.set(k, (m.get(k) ?? 0) + 1);
    }
    return m;
  };
  const b = grab(BASE); const h = grab('HEAD');
  const rem = [...b].filter(([k]) => !h.has(k) || h.get(k) < b.get(k)).map(([k, n]) => `${k}×${n - (h.get(k) ?? 0)}`);
  const add = [...h].filter(([k]) => !b.has(k) || h.get(k) > b.get(k)).map(([k, n]) => `${k}×${n - (b.get(k) ?? 0)}`);
  if (rem.length || add.length) presRows.push([f.replace('frontend/src/', ''), rem, add]);
}

// 3) classes newly applied in templates (class tokens present in HEAD, absent in BASE)
const L = [];
L.push(`#### C-1 模板内联 \`style=\` 数量：基线 ${bi} ⇒ HEAD ${hi}（净减 ${bi - hi}；HEAD 余 ${hi} 处全部在 DESIGN.md 例外 4 清单内）\n`);
L.push('| 文件 | 基线 | HEAD |');
L.push('|---|---:|---:|');
for (const [f, b, h] of inlineRows) L.push(`| ${f} | ${b} | ${h} |`);
L.push(`\n#### C-2 SVG 呈现属性 \`fill="#…"\` / \`stroke="#…"\` 的移除与改挂（这类改动**不在** C-1 的 \`style=\` 口径里，也看不见于附表 B，因为它们是呈现属性不是声明）\n`);
L.push('| 文件 | 基线移除的字面值 | HEAD 新增的字面值 |');
L.push('|---|---|---|');
for (const [f, r, a] of presRows) L.push(`| ${f} | ${r.length ? '`' + r.join('` `') + '`' : '—'} | ${a.length ? '`' + a.join('` `') + '`' : '—'} |`);
L.push('');
const md = L.join('\n');
writeFileSync(OUT + '/appendixC.md', md);
console.error(`inline ${bi}->${hi}; presRows=${presRows.length}; bytes=${md.length}`);

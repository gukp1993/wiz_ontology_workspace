import { fileURLToPath } from 'node:url';
const OUT = fileURLToPath(new URL('./out', import.meta.url));
// 权威色值计数：与 audit-design-debt.mjs 同源正则、逐行、跳过 `--token:` 定义行。
// 同时给出「不应用」与「应用 debt.allowedColorValues」两个口径。
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';

const root = process.argv[2];
const rx = /(^|[^\w])#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![\w])/g;
const exclD = ['frontend/node_modules', 'frontend/dist', 'frontend/src/ontology/legacyGraph', '.design-qa'];
const exclF = ['frontend/src/shared/graphStyle.ts'];
const exts = new Set(['.ts', '.tsx', '.js', '.jsx', '.css', '.vue']);
const allow = new Set((process.argv[3] ?? '#000,#fff,transparent').split(',').filter(Boolean));

function walk(d) {
  const out = [];
  for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    const rel = path.relative(root, p).split(path.sep).join('/');
    if (e.isDirectory()) { if (!exclD.includes(rel)) out.push(...walk(p)); }
    else if (exts.has(path.extname(e.name)) && !exclF.includes(rel)) out.push(p);
  }
  return out;
}
let total = 0; let allowed = 0; const byVal = {};
const files = walk(path.join(root, 'frontend/src')).sort();
for (const f of files) {
  for (const line of readFileSync(f, 'utf8').split(/\r?\n/)) {
    if (/^\s*--[A-Za-z0-9-_]+\s*:/u.test(line)) continue;
    for (const m of line.matchAll(new RegExp(rx.source, 'g'))) {
      const hex = m[0][0] === '#' ? m[0] : m[0].slice(1);
      total++;
      if (allow.has(hex.toLowerCase())) { allowed++; byVal[hex.toLowerCase()] = (byVal[hex.toLowerCase()] ?? 0) + 1; }
    }
  }
}
console.log(JSON.stringify({ files: files.length, raw: total, allowed, net: total - allowed, byVal }));

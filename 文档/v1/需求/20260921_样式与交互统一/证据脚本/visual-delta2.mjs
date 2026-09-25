// Resolved visual-delta v2: expands var(--token), merges repeated selectors per key
// (last declaration wins, as CSS does), and buckets results so selector-splitting or
// class-ownership moves are never reported as a rendered-value change.
// Usage: node visual-delta2.mjs <revA> [revB=WORKTREE]
import { fileURLToPath } from 'node:url';
const OUT = fileURLToPath(new URL('./out', import.meta.url));
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const revA = process.argv[2] ?? 'main';
const revB = process.argv[3] ?? 'WORKTREE';
const excl = /legacyGraph/;

function show(rev, file) {
  if (rev === 'WORKTREE') { try { return readFileSync(file, 'utf8'); } catch { return ''; } }
  try { return execFileSync('git', ['show', `${rev}:${file}`], { encoding: 'utf8' }); } catch { return ''; }
}
function styleBlocks(src, isVue) {
  if (!isVue) return src;
  const out = []; const re = /<style[^>]*>([\s\S]*?)<\/style>/g; let m;
  while ((m = re.exec(src))) out.push(m[1]);
  return out.join('\n');
}
function parseRoot(css) {
  const map = new Map();
  const m = css.match(/:root\s*\{([\s\S]*?)\}/);
  if (!m) return map;
  for (const d of m[1].replace(/\/\*[\s\S]*?\*\//g, '').split(';')) {
    const i = d.indexOf(':');
    if (i < 0) continue;
    const k = d.slice(0, i).trim();
    if (k.startsWith('--')) map.set(k, d.slice(i + 1).trim());
  }
  return map;
}
function findClose(s, open) {
  let d = 0;
  for (let j = open; j < s.length; j++) {
    if (s[j] === '{') d++;
    else if (s[j] === '}') { d--; if (d === 0) return j; }
  }
  return -1;
}
function rules(src) {
  const clean = src.replace(/\/\*[\s\S]*?\*\//g, '');
  const out = new Map();
  let i = 0; const stack = [];
  while (i < clean.length) {
    while (stack.length && stack[stack.length - 1].end <= i) stack.pop();
    const brace = clean.indexOf('{', i);
    if (brace < 0) break;
    const head = clean.slice(i, brace).replace(/^[}\s]+/, '').replace(/\s+/g, ' ').trim();
    const close = findClose(clean, brace);
    if (close < 0) break;
    if (head.startsWith('@')) { stack.push({ tag: head.replace(/@/, ''), end: close }); i = brace + 1; continue; }
    const inner = clean.slice(brace + 1, close);
    if (!/[{}]/.test(inner) && head) {
      const sel = stack.length ? `${stack.map((s) => s.tag).join(' / ')} \u203a ${head}` : head;
      if (!out.has(sel)) out.set(sel, new Map());
      const decls = out.get(sel);
      for (const d of inner.split(';')) {
        const k = d.indexOf(':');
        if (k < 0) continue;
        decls.set(d.slice(0, k).trim().toLowerCase(), d.slice(k + 1).trim().replace(/\s+/g, ' '));
      }
    }
    i = close + 1;
  }
  return out;
}
const VARS = [revA, revB].map((r) => parseRoot(show(r, 'frontend/src/style.css')));
function expand(v, tokens) {
  let prev; let cur = v; let n = 0;
  while (cur !== prev && n++ < 12) {
    prev = cur;
    cur = cur.replace(/var\(\s*(--[\w-]+)\s*(?:,([^()]*))?\)/g, (_, name, fb) => tokens.get(name) ?? (fb ? fb.trim() : `«${name}?»`));
  }
  return cur;
}
function norm(v) {
  let s = v.toLowerCase().replace(/\s+/g, ' ').trim();
  s = s.replace(/#([0-9a-f])([0-9a-f])([0-9a-f])([0-9a-f])\b/g, '#$1$1$2$2$3$3$4$4');
  s = s.replace(/#([0-9a-f])([0-9a-f])([0-9a-f])\b/g, '#$1$1$2$2$3$3');
  s = s.replace(/#([0-9a-f]{6})\b/g, '$1').replace(/\bwhite\b/g, 'ffffff').replace(/\bblack\b/g, '000000');
  s = s.replace(/(^|[ ,])([0-9]*\.?0)(px|em|rem|s|%|deg)?(?=$|[ ,;)])/g, (_, p, num, u) => `${p}${parseFloat(num)}${u ?? ''}`);
  return s;
}
const res = (raw, tok) => raw === undefined ? null : norm(expand(raw, tok));
const files = execFileSync('git', ['diff', '--name-only', revA, '--', 'frontend/src'], { encoding: 'utf8' })
  .split('\n').filter((f) => /\.(vue|css)$/.test(f) && !excl.test(f));

// global B-side pool: prop -> resolved value -> set of "file :: selector"  (moves may cross files)
const bPool = new Map();
for (const f of files) {
  const B = rules(styleBlocks(show(revB, f), f.endsWith('.vue')));
  const short = f.replace('frontend/src/', '');
  for (const [sel, decls] of B) for (const [p, v] of decls) {
    const r = res(v, VARS[1]); if (r === null) continue;
    if (!bPool.has(p)) bPool.set(p, new Map());
    if (!bPool.get(p).has(r)) bPool.get(p).set(r, new Set());
    bPool.get(p).get(r).add(`${short} :: ${sel}`);
  }
}
const changed = [], dropped = [], added = [], unresolved = [], newSel = [];
// A-side pool: the loop below only walks base selectors, so a rule that exists ONLY on the
// HEAD side (an up-migrated class, a merged duplicate, a brand-new utility) is invisible to it.
// Index the base side the same way so those declarations can be classified as move vs new value.
const aPool = new Map();
// Same value may sit in the template rather than in a `<style>` block on the base side
// (`style="…"` bindings and SVG `fill=`/`stroke=` presentation attributes). Index those too,
// otherwise an inline→class migration with an identical value is flagged as "new visual".
const aTpl = new Map();
const put = (pool, p, r, key) => {
  if (!pool.has(p)) pool.set(p, new Map());
  if (!pool.get(p).has(r)) pool.get(p).set(r, new Set());
  pool.get(p).get(r).add(key);
};
const baseRules = new Map();
for (const f of files) {
  const src = show(revA, f);
  const short = f.replace('frontend/src/', '');
  if (f.endsWith('.vue')) {
    for (const m of src.matchAll(/\sstyle="([^"]*)"/g)) {
      for (const d of m[1].split(';')) {
        const i = d.indexOf(':'); if (i < 0) continue;
        const r = res(d.slice(i + 1), VARS[0]); if (r === null) continue;
        put(aTpl, d.slice(0, i).trim().toLowerCase(), r, `${short} :: 模板内联 style="${m[1]}"`);
      }
    }
    for (const m of src.matchAll(/\s(fill|stroke)="(#[0-9a-fA-F]{3,8})"/g)) {
      const r = res(m[2], VARS[0]); if (r === null) continue;
      put(aTpl, m[1].toLowerCase(), r, `${short} :: 呈现属性 ${m[1]}="${m[2]}"`);
    }
  }
  const A = rules(styleBlocks(src, f.endsWith('.vue')));
  baseRules.set(f, A);
  for (const [sel, decls] of A) for (const [p, v] of decls) {
    const r = res(v, VARS[0]); if (r === null) continue;
    put(aPool, p, r, `${short} :: ${sel}`);
  }
}
for (const f of files) {
  const A = baseRules.get(f);
  const B = rules(styleBlocks(show(revB, f), f.endsWith('.vue')));
  const short = f.replace('frontend/src/', '');
  for (const [sel, b] of B) {
    if (A.has(sel)) continue;
    for (const [p, v] of b) {
      if (`${v}`.includes('v-bind(')) { newSel.push({ f: short, sel, p, bv: v, br: '(v-bind)', where: 'v-bind' }); continue; }
      const br = res(v, VARS[1]); if (br === null) continue;
      const rank = (x) => (x.startsWith(`${short} :: `) ? 0 : x.startsWith('style.css :: ') ? 1 : 2);
      const decl = [...(aPool.get(p)?.get(br) ?? [])].sort((x, y) => rank(x) - rank(y) || x.localeCompare(y));
      const tpl = [...(aTpl.get(p)?.get(br) ?? [])].sort();
      const all = [...decl.map((x) => `声明 ${x}`), ...tpl.map((x) => `模板 ${x}`)];
      const kind = decl.length ? 'move-decl' : tpl.length ? (tpl[0].includes('呈现属性') ? 'move-pres' : 'move-inline') : 'new-value';
      newSel.push({ f: short, sel, p, bv: v, br, kind, where: all.length ? all.slice(0, 2).join(' , ') + (all.length > 2 ? ` …（基线共 ${all.length} 处同值）` : '') : '(基线任何通道都无同值——真实新增视觉效果)' });
    }
  }
  for (const [sel, a] of A) {
    const b = B.get(sel);
    for (const p of new Set([...a.keys(), ...(b ? b.keys() : [])])) {
      const av = a.get(p), bv = b ? b.get(p) : undefined;
      if (av === bv) continue;
      const raw = `${av ?? ''}${bv ?? ''}`;
      if (raw.includes('v-bind(')) { unresolved.push({ f: short, sel, p, av, bv }); continue; }
      const ar = res(av, VARS[0]), br = res(bv, VARS[1]);
      if (ar !== null && br !== null) { if (ar !== br) changed.push({ f: short, sel, p, av, bv, ar, br }); continue; }
      if (ar !== null && br === null) {
        const all = [...(bPool.get(p)?.get(ar) ?? [])];
        const rank = (x) => (x.startsWith(`${short} :: `) ? 0 : x.startsWith('style.css :: ') ? 1 : 2);
        all.sort((x, y) => rank(x) - rank(y) || x.localeCompare(y));
        // A truly dead declaration: the value exists nowhere on the B side for this property.
        const moved = all.length
          ? all.slice(0, 3).map((x) => `\`${x}\``).join(' , ') + (all.length > 3 ? ` …（全仓共 ${all.length} 处同值）` : '')
          : '';
        dropped.push({ f: short, sel, p, av, ar, moved });
      } else if (ar === null && br !== null) added.push({ f: short, sel, p, bv, br });
    }
  }
}
const rootA = VARS[0], rootB = VARS[1], tok = [];
for (const k of rootA.keys()) if (!rootB.has(k)) tok.push(`REMOVED token ${k} = ${rootA.get(k)}`);
for (const k of rootB.keys()) if (!rootA.has(k)) tok.push(`ADDED   token ${k} = ${rootB.get(k)}`);
for (const k of rootA.keys()) if (rootB.has(k) && norm(rootA.get(k)) !== norm(rootB.get(k))) tok.push(`CHANGED token ${k}: ${rootA.get(k)} -> ${rootB.get(k)}`);

const byFile = (arr, line) => {
  let last = ''; const out = [];
  for (const r of arr) { if (r.f !== last) { out.push(`\n### ${r.f}`); last = r.f; } out.push(line(r)); }
  return out.join('\n');
};
console.log('**`:root` 令牌级差异**\n');
tok.forEach((t) => console.log('- ' + t));
console.log(`\n### B-1 决议值确实改变（同一选择器同一属性，两侧都有该声明）：${changed.length} 行 / ${new Set(changed.map((r) => r.f)).size} 文件\n`);
console.log(byFile(changed, (r) => `- \`${r.sel}\` → **${r.p}**: \`${r.av}\` ⇒ \`${r.bv}\`（解析后 ${r.ar} ⇒ ${r.br}）`));
console.log(`\n### B-2 声明从该选择器上消失：${dropped.length} 行 / ${new Set(dropped.map((r) => r.f)).size} 文件（"改挂"列非空 = 该解析值在全仓仍有声明，属选择器归属迁移；为空 = 真实删除）\n`);
console.log(byFile(dropped, (r) => `- \`${r.sel}\` → **${r.p}** \`${r.av}\`（解析 ${r.ar}）${r.moved ? ` ⇒ 改挂 ${r.moved}` : ' ⇒ 全仓再无该属性的同值声明（真实删除）'}`));
console.log(`\n### B-3 该选择器新增声明：${added.length} 行\n`);
console.log(byFile(added, (r) => `- \`${r.sel}\` → **${r.p}**: \`${r.bv}\`（解析 ${r.br}）`));
console.log(`\n### B-4 含 v-bind() 无法静态解析：${unresolved.length} 行\n`);
console.log(byFile(unresolved, (r) => `- \`${r.sel}\` → **${r.p}**: \`${r.av ?? '(absent)'}\` ⇒ \`${r.bv ?? '(absent)'}\``));
console.log(`\n### B-5 基线完全没有该选择器（HEAD 新增规则，前四个桶按基线选择器遍历因而看不见）：${newSel.length} 条声明 / ${new Set(newSel.map((r) => `${r.f} :: ${r.sel}`)).size} 个选择器\n`);
console.log(byFile(newSel, (r) => `- \`${r.sel}\` → **${r.p}**: \`${r.bv}\`（解析 ${r.br}）｜基线同值出处 ${r.where}`));

// machine-readable dump for the collapse step
import { writeFileSync } from 'node:fs';
writeFileSync(OUT + '/vd2.json', JSON.stringify({
  token: tok, changed, dropped, added, unresolved, newSel
}, null, 1));

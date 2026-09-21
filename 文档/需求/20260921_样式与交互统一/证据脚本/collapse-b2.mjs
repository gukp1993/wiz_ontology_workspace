import { fileURLToPath } from 'node:url';
const OUT = fileURLToPath(new URL('./out', import.meta.url));
// Split vd2.json B-2 into "value replaced / truly deleted" (annotated) and
// "selector-ownership move" (collapsed per file+selector), and emit markdown.
import { readFileSync, writeFileSync } from 'node:fs';
const d = JSON.parse(readFileSync(OUT + '/vd2.json', 'utf8'));

// Hand-verified replacement note for every row whose resolved value disappears repo-wide.
const NOTE = {
  'flow/FlowEditor.vue|.primary-run:hover|opacity': '`.primary-run` 类整体废弃（模板 0 命中），改挂全局 `.primary`；hover 反馈由 `opacity:.92` 改为全局 `.primary:where(:not(:disabled)):hover{background:var(--blue-deep)}`（刻意收敛到全局主按钮契约）',
  'ontology/ObjectWorkspace.vue|.ow-more-menu|min-width': '类改挂全局 `.row-menu-list`，`min-width` 150px ⇒ **158px**（与 `.row-menu` 系列统一，刻意）',
  'project/MappingDescription.vue|textarea|border': '组件内 border 声明删除，落到全局 `input,select,textarea{border:1px solid var(--line-2)}` ⇒ `#ccd8e6` ⇒ **`#c9d5e0`**（ΔRGB 3/11/9，刻意收敛到 `--line-2`）',
  'project/MappingDescription.vue|textarea:focus|outline-offset': '`:focus` 规则删除，落到全局 `textarea:focus-visible{outline-offset:2px}` ⇒ `-1px` ⇒ **`2px`**，且触发条件由 `:focus` 收窄为 `:focus-visible`（鼠标点击不再画环，刻意）',
  'style.css|:root|--ok-bright': '令牌删除（全仓 0 引用，实测 `grep -R -- "--ok-bright" frontend/src` 仅此行）；`--paper-float` 为同轮新增，A6 已登记这一对替换',
  'tools/InstanceGraph.vue|.graph-toolbar button:hover:not(:disabled)|border-color': '组件 hover 规则删除，落到全局 `button:where(:not(:disabled)):hover` ⇒ `#a8c2e9` ⇒ **`var(--blue)` #245cdf**；同时失去原 hover 的 `background:var(--blue-soft)`（全局基元 hover 不改背景），字色改为 `var(--blue)`（刻意统一）',
  'tools/InstanceGraph.vue|.graph-toolbar button:disabled|opacity': '落到全局 `button:disabled{opacity:.5;cursor:not-allowed}` ⇒ `.45` ⇒ **`.5`**',
  'tools/InstanceGraph.vue|.graph-toolbar button:disabled|cursor': '同上 ⇒ `default` ⇒ **`not-allowed`**',
  'tools/InstanceGraph.vue|.graph-hint|padding': '类改挂全局 `.canvas-hint` ⇒ `5px 8px` ⇒ **`5px 12px`**（`.canvas-hint{padding:5px 12px}`）',
  'tools/InstanceGraph.vue|.graph-hint|background': '同上 ⇒ `#ffffffdd` ⇒ **`var(--paper-float)` #fffffff0**',
  'tools/InstanceTable.vue|.instance-table button:disabled|opacity': '落到全局 `button:disabled` ⇒ `.45` ⇒ **`.5`**',
  'tools/InstanceTable.vue|.instance-table button:disabled|cursor': '同上 ⇒ `default` ⇒ **`not-allowed`**',
  'tools/KnowledgeCanvas.vue|.kc-toolbar button:hover:not(:disabled)|border-color': '同 InstanceGraph：`#a8c2e9` ⇒ **`var(--blue)`**，并失去 `background:var(--blue-soft)`',
  'tools/KnowledgeCanvas.vue|.kc-toolbar button:disabled|opacity': '⇒ `.45` ⇒ **`.5`**',
  'tools/KnowledgeCanvas.vue|.kc-toolbar button:disabled|cursor': '⇒ `default` ⇒ **`not-allowed`**',
  'tools/KnowledgeCanvas.vue|.kc-hint|right': '`right:12px` 未迁移，但 `.kc-surface+.canvas-hint{width:fit-content}` 使元素按内容定宽、左锚 `left:16px`，**布局效果等价**（不再是左右各 12px 拉伸后 fit-content）',
  'tools/KnowledgeCanvas.vue|.kc-hint|padding': '类改挂 `.canvas-hint` ⇒ `5px 8px` ⇒ **`5px 12px`**',
  'tools/KnowledgeCanvas.vue|.kc-hint|background': '同上 ⇒ `#ffffffed` ⇒ **`var(--paper-float)` #fffffff0**',
  'tools/OntologyDiscover.vue|.eyebrow|color': '组件内覆盖删除，改由全局 `.eyebrow{color:var(--muted)}` 生效 ⇒ `#5275aa` ⇒ **`#5b6d7e`**（与 main 的 style.css `.eyebrow` 原值一致，属向全局基线收敛）',
  'tools/OntologyDiscover.vue|button:focus-visible,input:focus-visible|outline': '组件内覆盖删除，落到全局 `button/input/select/textarea:focus-visible{outline:2px solid var(--focus)}` ⇒ `#3478d6` ⇒ **`#245cdf`**',
  'tools/OntologyDiscover.vue|button:focus-visible,input:focus-visible|outline-offset': '同上 ⇒ `3px` ⇒ **`2px`**，且全局规则额外覆盖 select/textarea',
};

const key = (r) => `${r.f}|${r.sel}|${r.p}`;
const dels = d.dropped.filter((r) => !r.moved);
const moves = d.dropped.filter((r) => r.moved);
const missing = dels.filter((r) => !(key(r) in NOTE));
if (missing.length) {
  console.error('UNANNOTATED:');
  missing.forEach((r) => console.error(' ', key(r)));
}
const unkeyed = Object.keys(NOTE).filter((k) => !dels.some((r) => key(r) === k));
if (unkeyed.length) console.error('STALE NOTES:', unkeyed.join('\n  '));
console.error(`dels=${dels.length} moves=${moves.length} annotated=${dels.length - missing.length}`);

let out = '';
out += `#### B-2a 该解析值在全仓不再出现：${dels.length} 行（逐条已人工核对替代声明，非"漏删"）\n\n`;
out += '| 文件 | 选择器 | 属性 | 基线值（解析后） | 替代与决议值变化 |\n|---|---|---|---|---|\n';
for (const r of dels) out += `| ${r.f} | \`${r.sel}\` | \`${r.p}\` | \`${r.av}\`（${r.ar}） | ${NOTE[key(r)] ?? '（未登记，视为缺陷）'} |\n`;

// collapse moves by file + selector
const groups = new Map();
for (const r of moves) {
  const k = `${r.f}\u0000${r.sel}`;
  if (!groups.has(k)) groups.set(k, []);
  groups.get(k).push(r);
}
out += `\n#### B-2b 声明换挂到别的选择器、解析值本身仍在全仓声明：${moves.length} 行 / ${groups.size} 个「文件 · 选择器」（这类行不是视觉改动，只做归属追溯）\n\n`;
out += '| 文件 | 基线选择器 | 迁移的属性 | 改挂目标（示例，≤2） |\n|---|---|---|---|\n';
const dest = (r) => r.moved.replace(/ …（全仓共 \d+ 处同值）/, '');
const filesInMove = new Set(moves.map((r) => r.f));
out += `> 口径：判定"改挂"只看**该解析值在 HEAD 全仓（排除 \`legacyGraph/**\`）是否仍有同属性声明**，不要求视觉位置一致；因此本表只说明"值没丢"，不承诺"渲染位置没变"。位置/取舍变化一律落在 B-2a 与 B-1。\n\n`;
for (const [k, rows] of groups) {
  const [f, sel] = k.split('\u0000');
  const props = rows.map((r) => `\`${r.p}\``).join(' ');
  const ds = [...new Set(rows.map((r) => r.moved.split(' , ')[0]))].slice(0, 2).join(' / ');
  out += `| ${f} | \`${sel}\` | ${props} | ${ds} |\n`;
}
writeFileSync(OUT + '/appendixE.md', out);
console.error('wrote appendixE.md bytes=' + out.length);

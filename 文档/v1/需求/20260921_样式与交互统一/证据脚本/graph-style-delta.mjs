// 附表 C-6：cytoscape 有效样式表的跨修订比对。
//
// 为什么需要独立一个脚本：shared/graphStyle.ts 同时被 design.qa.yaml 的 code.exclude
// 与其余证据脚本的扩展名过滤（只收 .vue/.css）双重排除，而它在本次改动里动了 +192/-101 行。
// 眼看这份文件不可信，因此这里用「执行比对」：把两版模块真的 import 进来，
// 从基线 .vue 源码里按括号配平抽出内联数组并求值，再比 (selector, property, value) 三元组
// 与块顺序（cytoscape 对同一 selector 后者覆盖前者，重排即改渲染）。
//
// 用法（cwd = 工作树根）：
//   node --experimental-strip-types "文档/需求/20260921_样式与交互统一/证据脚本/graph-style-delta.mjs" [revA]
// revA 默认 07f8d8d。退出码：有 BLOCKER 时为 1。
import { execFileSync } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

const REV = process.argv[2] ?? '07f8d8d'
const dir = mkdtempSync(path.join(tmpdir(), 'graphstyle-'))
const show = (rev, p) => execFileSync('git', ['show', `${rev}:${p}`], { encoding: 'utf8' })
const file = (rev, p, name) => {
  const src = show(rev, p)
  writeFileSync(path.join(dir, name), src)
  return src
}

const GS = 'frontend/src/shared/graphStyle.ts'
file(REV, GS, 'graphStyle.base.ts')
file('HEAD', GS, 'graphStyle.head.ts')
const BASE = await import(path.join(dir, 'graphStyle.base.ts'))
const HEAD = await import(path.join(dir, 'graphStyle.head.ts'))

const VUE = {
  flow: 'frontend/src/flow/FlowCanvas.vue',
  instance: 'frontend/src/tools/InstanceGraph.vue',
  knowledge: 'frontend/src/tools/KnowledgeCanvas.vue',
}
const vbase = {}
const vhead = {}
for (const [k, p] of Object.entries(VUE)) {
  vbase[k] = file(REV, p, `${k}.base.vue`)
  vhead[k] = show('HEAD', p)
}

// ---- 抽取：从 `from` 起第一个平衡的 [...] / {...} 字面量 ----
function balanced(text, from, open = '[', close = ']') {
  const s = text.indexOf(open, from)
  if (s < 0) throw new Error(`源码里找不到 ${open}（from=${from}）`)
  let depth = 0
  let inStr = null
  for (let i = s; i < text.length; i++) {
    const c = text[i]
    if (inStr) {
      if (c === '\\') i++
      else if (c === inStr) inStr = null
      continue
    }
    if (c === '"' || c === "'" || c === '`') { inStr = c; continue }
    if (c === open) depth++
    else if (c === close) {
      depth--
      if (depth === 0) return text.slice(s, i + 1)
    }
  }
  throw new Error('括号不平衡')
}

// cytoscape({ ..., style: [ ... ] }) 里的那个数组
function extractCytStyle(text, label) {
  const m = /style\s*:\s*\[/.exec(text)
  if (!m) throw new Error(`${label} 里找不到 style: [ 数组`)
  return { src: balanced(text, m.index), line: text.slice(0, m.index).split('\n').length }
}

const stripTs = (x) => x.replace(/\s+as\s+(any|unknown|string|[A-Za-z_$][\w$]*(\[\])?)/g, '')
const run = (names, values, exprSrc, label) => {
  try {
    return Function(...names, `return (${stripTs(exprSrc)})`)(...values)
  } catch (e) {
    throw new Error(`求值 ${label} 失败：${e.message}`)
  }
}

// ---- 色值归一：只承认下列等价，alpha≠1 绝不与不透明 hex 判等 ----
function normColor(v) {
  if (typeof v !== 'string') return { kind: 'noncolor', v: String(v), raw: String(v) }
  const s = v.trim()
  let m = /^#([0-9a-fA-F]{3})$/.exec(s)
  if (m) return { kind: 'hex', raw: s, v: ('#' + [...m[1]].map((c) => c + c).join('')).toLowerCase() }
  m = /^#([0-9a-fA-F]{6})$/.exec(s)
  if (m) return { kind: 'hex', raw: s, v: s.toLowerCase() }
  m = /^#([0-9a-fA-F]{8})$/.exec(s)
  if (m) {
    const a = parseInt(s.slice(7, 9), 16)
    return a === 255
      ? { kind: 'hex', raw: s, v: s.slice(0, 7).toLowerCase() }
      : { kind: 'alpha', raw: s, v: `${s.slice(0, 7).toLowerCase()}/a=${(a / 255).toFixed(3)}` }
  }
  m = /^rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)$/.exec(s)
  if (m) {
    const a = parseFloat(m[4])
    const hex = '#' + [1, 2, 3].map((i) => (+m[i]).toString(16).padStart(2, '0')).join('')
    return a === 1
      ? { kind: 'hex', raw: s, v: hex.toLowerCase() }
      : { kind: 'alpha', raw: s, v: `${hex.toLowerCase()}/a=${a.toFixed(3)}` }
  }
  if (/^hsla?\(/.test(s)) return { kind: 'functional', raw: s, v: s.replace(/\s+/g, '').toLowerCase() }
  return { kind: 'other', raw: s, v: s.toLowerCase() }
}

let blockers = 0
const say = (s) => console.log(s)

function compare(name, baseStyle, headStyle, meta) {
  const keyOf = (t) => `${t.selector} :: ${t.prop}`
  const triples = (style) => style.flatMap((blk, bi) => Object.entries(blk.style).map(([prop, raw]) => {
    const v = typeof raw === 'function' ? `<fn:${raw.name}>` : raw
    const col = /color|background$|fill|stroke/.test(prop) ? normColor(String(v)) : null
    return { block: bi, selector: blk.selector, prop, raw: v, kind: col?.kind ?? 'plain', norm: col?.v ?? String(v), src: col?.raw ?? String(v) }
  }))
  const A = triples(baseStyle)
  const B = triples(headStyle)
  const orderSame = JSON.stringify(baseStyle.map((b) => b.selector)) === JSON.stringify(headStyle.map((b) => b.selector))
  const group = (rows) => {
    const m = new Map()
    for (const r of rows) {
      if (!m.has(keyOf(r))) m.set(keyOf(r), [])
      m.get(keyOf(r)).push(r)
    }
    return m
  }
  const mapA = group(A)
  const mapB = group(B)
  const diffs = []
  for (const k of new Set([...mapA.keys(), ...mapB.keys()])) {
    const a = mapA.get(k) ?? []
    const b = mapB.get(k) ?? []
    if (a.length !== b.length) {
      diffs.push(['BLOCKER', `${k} 基数 base=${a.length} head=${b.length}`])
      continue
    }
    for (let i = 0; i < a.length; i++) {
      if (a[i].norm !== b[i].norm || a[i].kind !== b[i].kind) {
        diffs.push(['BLOCKER', `${k}: ${a[i].src} => ${b[i].src}（解析 ${a[i].norm} vs ${b[i].norm}）`])
      } else if (a[i].src !== b[i].src) {
        diffs.push(['INFO', `${k}: ${a[i].src} => ${b[i].src}（解析后同为 ${a[i].norm}，值等价）`])
      }
    }
  }
  say(`\n### ${name}`)
  say(`  ${meta}`)
  say(`  块数 base=${baseStyle.length} head=${headStyle.length} | 声明三元组 base=${A.length} head=${B.length} | 块顺序相同=${orderSame} | 差异=${diffs.length}`)
  if (!orderSame) {
    say(`  ORDER base: ${JSON.stringify(baseStyle.map((b) => b.selector))}`)
    say(`  ORDER head: ${JSON.stringify(headStyle.map((b) => b.selector))}`)
  }
  for (const [lv, msg] of diffs) say(`  [${lv}] ${msg}`)
  blockers += diffs.filter(([lv]) => lv === 'BLOCKER').length
  if (!orderSame) blockers += 1
}

// 1/2 两版模块里都存在的公共导出
compare('GRAPH_STYLE', BASE.GRAPH_STYLE, HEAD.GRAPH_STYLE,
  `base=${REV}:${GS} | head=HEAD:${GS}`)
compare('PREVIEW_GRAPH_STYLE', BASE.PREVIEW_GRAPH_STYLE, HEAD.PREVIEW_GRAPH_STYLE,
  `base=${REV}:${GS} | head=HEAD:${GS}（注意：本导出在两侧都无消费者，见文末）`)

// 3 编排画布：有效样式表 = GRAPH_STYLE ++ 内联 extras
{
  const ex = extractCytStyle(vbase.flow, 'FlowCanvas base')
  const baseStyle = run(['GRAPH_STYLE'], [BASE.GRAPH_STYLE], ex.src, 'FlowCanvas base')
  const hx = extractCytStyle(vhead.flow, 'FlowCanvas head')
  const headStyle = run(['GRAPH_STYLE', 'FLOW_EXTRA_STYLE'], [HEAD.GRAPH_STYLE, HEAD.FLOW_EXTRA_STYLE], hx.src, 'FlowCanvas head')
  compare('FLOW 画布有效样式表（GRAPH_STYLE ++ extras）', baseStyle, headStyle,
    `base=${VUE.flow}:${ex.line} 内联 | head=shared/graphStyle.ts FLOW_EXTRA_STYLE`)
  compare('FLOW_EXTRA_STYLE（隔离）', baseStyle.slice(BASE.GRAPH_STYLE.length), HEAD.FLOW_EXTRA_STYLE,
    `base=${VUE.flow}:${ex.line} 内联尾部 | head=shared/graphStyle.ts`)
  const fd = []
  const cats = { python: '[type = "python"]', sql: '[type = "sql"]', redis: '[type = "redis"]', http: '[type = "http"]', calc: '[type = "calc"]', boundary: '[type = "input"],[type = "output"]' }
  const extras = baseStyle.slice(BASE.GRAPH_STYLE.length)
  const bySel = (s) => extras.find((b) => b.selector === s)
  for (const [k, sel] of Object.entries(cats)) {
    const blk = bySel(sel)
    if (!blk) { fd.push(`基线找不到 selector ${sel}`); continue }
    const same = normColor(blk.style['background-color']).v === normColor(HEAD.FLOW_CATEGORY[k].fill).v
      && normColor(blk.style['border-color']).v === normColor(HEAD.FLOW_CATEGORY[k].border).v
    if (!same) fd.push(`FLOW_CATEGORY.${k}: base=${JSON.stringify(blk.style)} head=${JSON.stringify(HEAD.FLOW_CATEGORY[k])}`)
  }
  for (const [k, sel] of Object.entries({ failed: 'node.run-failed', waiting: 'node.run-waiting', checked: 'node.test-checked', bindSrc: 'node.bind-src' })) {
    const blk = bySel(sel)
    if (!blk) { fd.push(`基线找不到状态 selector ${sel}`); continue }
    if (normColor(blk.style['border-color']).v !== normColor(HEAD.FLOW_STATE[k]).v) {
      fd.push(`FLOW_STATE.${k}: base=${blk.style['border-color']} head=${HEAD.FLOW_STATE[k]}`)
    }
  }
  say('\n### FLOW_CATEGORY / FLOW_STATE（分类调色板）')
  say(`  6 类 + 4 态 | 差异=${fd.length}`)
  fd.forEach((m) => say(`  [BLOCKER] ${m}`))
  blockers += fd.length
}

// 4 实例图
{
  const ex = extractCytStyle(vbase.instance, 'InstanceGraph base')
  const baseStyle = run([], [], ex.src, 'InstanceGraph base')
  const hx = extractCytStyle(vhead.instance, 'InstanceGraph head')
  compare('INSTANCE_GRAPH 样式数组', baseStyle,
    run(['INSTANCE_GRAPH'], [HEAD.INSTANCE_GRAPH], hx.src, 'InstanceGraph head'),
    `base=${VUE.instance}:${ex.line} 内联 | head=shared/graphStyle.ts`)
  const pi = vbase.instance.indexOf('const palette')
  const basePal = run([], [], balanced(vbase.instance, pi, '[', ']'), 'palette')
  const palSame = JSON.stringify(basePal) === JSON.stringify([...HEAD.INSTANCE_NODE_PALETTE])
  say('\n### INSTANCE_NODE_PALETTE')
  say(`  条目 base=${basePal.length} head=${HEAD.INSTANCE_NODE_PALETTE.length} | 完全相同=${palSame}`)
  if (!palSame) {
    say(`  [BLOCKER] ${JSON.stringify(basePal)} => ${JSON.stringify(HEAD.INSTANCE_NODE_PALETTE)}`)
    blockers += 1
  }
  const colorLine = vbase.instance.split('\n').find((l) => l.trim().startsWith('function color(id'))
  if (!colorLine) throw new Error('基线 InstanceGraph 里找不到 color() 单行函数，取色等价性未验证')
  const body = colorLine.slice(colorLine.indexOf('return') + 'return'.length, colorLine.lastIndexOf('}'))
  const baseColorAt = Function('palette', 'index', `return (${body})`).bind(null, basePal)
  const mismatch = []
  for (let i = -5; i <= 60; i++) {
    if (baseColorAt(i) !== HEAD.instanceNodeColor(i)) mismatch.push(`index ${i}: base=${baseColorAt(i)} head=${HEAD.instanceNodeColor(i)}`)
  }
  say('\n### instanceNodeColor(index) vs 基线 color()')
  say(`  实测索引 -5..60（66 例）| 差异=${mismatch.length}`)
  mismatch.forEach((m) => say(`  [BLOCKER] ${m}`))
  blockers += mismatch.length
}

// 5 知识画布
{
  const ex = extractCytStyle(vbase.knowledge, 'KnowledgeCanvas base')
  const baseStyle = run([], [], ex.src, 'KnowledgeCanvas base')
  const hx = extractCytStyle(vhead.knowledge, 'KnowledgeCanvas head')
  compare('KNOWLEDGE_CANVAS 样式数组', baseStyle,
    run(['KNOWLEDGE_CANVAS'], [HEAD.KNOWLEDGE_CANVAS], hx.src, 'KnowledgeCanvas head'),
    `base=${VUE.knowledge}:${ex.line} 内联 | head=shared/graphStyle.ts`)
  const kindsLit = (text) => {
    const gt = text.indexOf('>={', text.indexOf('const kinds'))
    if (gt < 0) throw new Error('找不到 kinds 字面量')
    return balanced(text, gt + 2, '{', '}')
  }
  const bk = run([], [], kindsLit(vbase.knowledge), 'kinds base')
  const hh = run(['KNOWLEDGE_KIND_FILL'], [HEAD.KNOWLEDGE_KIND_FILL], kindsLit(vhead.knowledge), 'kinds head')
  let kd = 0
  for (const k of Object.keys(hh)) {
    const a = bk[k]
    const b = hh[k]
    const same = normColor(a.color).v === normColor(b.color).v
      && normColor(a.color).kind === normColor(b.color).kind
      && a.shape === b.shape && a.label === b.label
    if (!same) {
      kd++
      say(`  [BLOCKER] kinds.${k}: base=${JSON.stringify(a)} head=${JSON.stringify(b)}`)
    }
  }
  say('\n### KNOWLEDGE_KIND_FILL（图例色 + 节点填充）')
  say(`  类别=${Object.keys(hh).length} | 差异=${kd} | 键序相同=${JSON.stringify(Object.keys(bk)) === JSON.stringify(Object.keys(hh))}`)
  blockers += kd
}

// 6 本体分类（基线无符号，值内联在 GRAPH_STYLE 的 [type=...] 块里）
{
  say('\n### ONTOLOGY_CATEGORY')
  let od = 0
  for (const [k, sel] of Object.entries({ entity: '[type = "实体"]', attribute: '[type = "属性"]', rule: '[type = "规则"]' })) {
    const blk = BASE.GRAPH_STYLE.find((b) => b.selector === sel)
    const ok = normColor(blk.style['background-color']).v === normColor(HEAD.ONTOLOGY_CATEGORY[k].fill).v
      && normColor(blk.style['border-color']).v === normColor(HEAD.ONTOLOGY_CATEGORY[k].border).v
    if (!ok) {
      od++
      say(`  [BLOCKER] ${k}: base=${JSON.stringify(blk.style)} head=${JSON.stringify(HEAD.ONTOLOGY_CATEGORY[k])}`)
    }
  }
  say(`  entity/attribute/rule 的 fill+border | 差异=${od}`)
  blockers += od
}

// 7 消费者与 legacyGraph 边界
{
  const consumers = execFileSync('git', ['grep', '-l', 'graphStyle', 'HEAD', '--', 'frontend/src'], { encoding: 'utf8' })
    .trim().split('\n').filter(Boolean).map((s) => s.replace(/^HEAD:/, ''))
  say('\n### 范围核对')
  say(`  HEAD 引用 graphStyle 的文件（${consumers.length}）：\n    ${consumers.join('\n    ')}`)
  const legacy = execFileSync('git', ['diff', '--name-only', `${REV}`, 'HEAD', '--', 'frontend/src/ontology/legacyGraph'], { encoding: 'utf8' }).trim()
  say(`  legacyGraph/ 是否被动过：${legacy === '' ? '否（diff 为空）' : `\n${legacy}`}`)
  if (legacy !== '') blockers += 1
  // legacyGraph/ 里那份同名 graphStyle 是独立副本，它的使用者不算 src/shared 这份的消费者
  const pvUsers = execFileSync('git', ['grep', '-l', 'PREVIEW_GRAPH_STYLE', 'HEAD', '--', 'frontend/src'], { encoding: 'utf8' })
    .trim().split('\n').filter((l) => !l.includes('legacyGraph') && !l.endsWith('shared/graphStyle.ts')).length
  say(`  PREVIEW_GRAPH_STYLE（src/shared 那份）在 legacyGraph 之外的消费者数：${pvUsers}（0 = 死导出，登记不处理）`)
}

// 8 阴性对照：本脚本必须在"人为改一个字节"时报出差异，否则上面所有的"差异=0"都无意义。
{
  say('\n### 阴性对照（harness 自证）')
  const headArr = run(['KNOWLEDGE_CANVAS'], [HEAD.KNOWLEDGE_CANVAS],
    extractCytStyle(vhead.knowledge, 'KnowledgeCanvas head').src, 'KnowledgeCanvas head')
  let touched = 0
  const mutated = headArr.map((b) => {
    if (b.selector !== 'edge' || b.style['text-background-color'] === undefined) return b
    touched++
    return { ...b, style: { ...b.style, 'text-background-color': '#8b9db7' } }
  })
  if (touched === 0) throw new Error('阴性对照没找到可改的目标声明，对照无效')
  const before = blockers
  blockers = 0
  compare('KNOWLEDGE_CANVAS（人为把 edge 的 text-background-color 改成 #8b9db7）',
    run([], [], extractCytStyle(vbase.knowledge, 'KnowledgeCanvas base').src, 'neg base'),
    mutated, `期望：报出 BLOCKER；若报 0 差异则本脚本的等价判定失效（实改 ${touched} 处）`)
  const caught = blockers
  blockers = before + (caught === 0 ? 1 : 0)
  say(caught > 0
    ? `  对照结果：人为注入 1 处改动，报出 ${caught} 条差异 -> harness 有效`
    : '  对照结果：未报出注入的改动 -> **harness 失效，本报告的全部"差异=0"不可信**')
}

console.log(`\n${'='.repeat(60)}`)
console.log(blockers === 0
  ? 'VERDICT: NEUTRAL —— 两版 cytoscape 有效样式表逐三元组、逐块顺序等价（或仅值等价差异）'
  : `VERDICT: CHANGED —— ${blockers} 处 BLOCKER，画布外观确实改变`)
process.exit(blockers === 0 ? 0 : 1)

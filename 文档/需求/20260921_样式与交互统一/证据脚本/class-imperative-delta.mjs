// 附表 C-4 / C-5 的**穷举口径**脚本：
//   C-4：模板 class 令牌的多重集差异（按文件）。附表 B 按声明配对、C-1 数 style=、C-2 数
//        SVG 呈现属性，三者都看不见"`class=\"a\"` 改成 `class=\"b\"` 而 CSS 一字未动"这一类
//        变化——声明集合完全相同，元素却换了整套规则。本脚本只负责**穷举出候选位点**；
//        每个位点的计算样式差异必须人工穿透 :root 与 scoped/global 优先级来判（见 §9.5 表）。
//   C-5：命令式 DOM 样式通道的命中数（className / el.style.x / cssText / classList / …）。
//
// 用法（cwd = 工作树根）：node "文档/需求/20260921_样式与交互统一/证据脚本/class-imperative-delta.mjs" [revA]
import { execFileSync } from 'node:child_process'

const REV = process.argv[2] ?? '07f8d8d'
const git = (args) => execFileSync('git', args, { encoding: 'utf8' })
const files = git(['diff', '--name-only', REV, 'HEAD', '--', 'frontend/src'])
  .trim().split('\n').filter((f) => f.endsWith('.vue'))

// 取外层 <template> 块的完整区间，避免把 <script> 里的字符串字面量当成模板 class。
// 必须同时识别带属性的 `<template #slot>` / `v-if` 开标签：只匹配裸 `<template>` 的话，
// 内层插槽的 `</template>` 会把深度提前扣到 0，导致区间被截断、后半部分模板**静默漏检**。
const OPEN = /<template(?=[\s>])/g
const CLOSE = /<\/template\s*>/g
function templateOf(text) {
  const s = text.indexOf('<template')
  if (s < 0) return ''
  const ends = [...text.matchAll(CLOSE)].map((m) => m.index)
  let depth = 0
  const opens = [...text.matchAll(OPEN)].map((m) => m.index).filter((i) => i >= s)
  const events = [...opens.map((i) => [i, 1]), ...ends.filter((i) => i >= s).map((i) => [i, -1])]
    .sort((x, y) => x[0] - y[0])
  for (const [i, d] of events) {
    depth += d
    if (depth === 0) return text.slice(s, i + text.slice(i).match(/^<\/template\s*>/)[0].length)
  }
  return text.slice(s)
}

// 静态 class="…" 令牌多重集；动态 :class="…" 只登记原始表达式（令牌无法静态求值）
function tokens(text) {
  const tpl = templateOf(text)
  const stat = []
  for (const m of tpl.matchAll(/\sclass\s*=\s*"([^"]*)"/g)) {
    for (const t of m[1].trim().split(/\s+/)) if (t) stat.push(t)
  }
  const dyn = [...tpl.matchAll(/\s:class\s*=\s*"([^"]*)"/g)].map((m) => m[1])
  return { stat: stat.sort(), dyn }
}
const multisetDiff = (a, b) => {
  const cnt = new Map()
  for (const x of a) cnt.set(x, (cnt.get(x) ?? 0) - 1)
  for (const x of b) cnt.set(x, (cnt.get(x) ?? 0) + 1)
  return [...cnt.entries()].filter(([, n]) => n !== 0).sort()
}

console.log(`### C-4 模板 class 令牌多重集差异（${REV} -> HEAD，仅 .vue）\n`)
let sites = 0
let groups = 0
for (const f of files) {
  let a, b
  try {
    a = tokens(git(['show', `${REV}:${f}`]))
  } catch {
    a = { stat: [], dyn: [] }
  }
  b = tokens(git(['show', `HEAD:${f}`]))
  const d = multisetDiff(a.stat, b.stat)
  const dynChanged = JSON.stringify(a.dyn.sort()) !== JSON.stringify(b.dyn.sort())
  if (!d.length && !dynChanged) continue
  groups++
  sites += d.reduce((s, [, n]) => s + Math.abs(n), 0)
  console.log(`- ${f}`)
  if (d.length) console.log(`    令牌增减：${d.map(([t, n]) => `${t} ${n > 0 ? `+${n}` : n}`).join('  ')}`)
  if (dynChanged) {
    console.log(`    :class 表达式变化（需人工判定其中的类名）：`)
    for (const x of a.dyn) if (!b.dyn.includes(x)) console.log(`      - 基线 \`${x}\``)
    for (const x of b.dyn) if (!a.dyn.includes(x)) console.log(`      + HEAD \`${x}\``)
  }
}
console.log(`\n  合计：${groups} 个文件有模板类名/动态类变化，静态令牌增减位点 ${sites} 个（+N=HEAD 新增，-N=基线移除）。`)
console.log('  注意：本数是**令牌出现次数的增减**，不等于"改名位点数"；一个位点会同时贡献 -旧名 +新名。')
console.log('  逐位点的计算样式差异必须人工核对（见 §9.5 附表 C-4）：脚本只保证"没有漏掉候选"。')

// ---------- 逐令牌归因：把上面的"增减数"落到"该元素是否换了规则集"上 ----------
// 只靠"多了一个类名"不能判定视觉变化：新挂的类可能是**空类**（无任何声明）。
// 因此对每个发生变化的静态令牌，给出**挂类位点**与该修订版的**声明出处**：
//   scoped   = 该文件 <style> 内声明（Vue scoped 只在同文件命中，别的文件的同名类对该元素不生效）
//   global   = frontend/src/style.css 内声明
//   other:X  = 仅其他 .vue 的 scoped 里有（对本元素不生效，登记出来防误读）
//   none     = 该修订版全仓无声明 → 空类，挂上即值中性
// **必须按修订版分别查**：移除类令牌的声明只在基线侧存在，拿 HEAD 查会一律得 none，
// 从而把"删掉了一条真在用的规则"错报成"本来就没声明"。
const declRe = (tok) => new RegExp(`\\.${tok}(?![\\w-])`)
const styleBlock = (text) => { const i = text.indexOf('<style'); return i < 0 ? '' : text.slice(i) }
function tree(rev) {
  const cache = {}
  const get = (f) => (cache[f] ??= (() => { try { return git(['show', `${rev}:${f}`]) } catch { return '' } })())
  const list = git(['ls-tree', '-r', '--name-only', rev, '--', 'frontend/src'])
    .trim().split('\n').filter((f) => f.endsWith('.vue') && !f.includes('legacyGraph/'))
  return {
    get,
    declWhere(tok, file) {
      const where = []
      if (declRe(tok).test(get('frontend/src/style.css'))) where.push('global')
      if (declRe(tok).test(styleBlock(get(file)))) where.push('scoped')
      for (const f of list) {
        if (f === file) continue
        if (declRe(tok).test(styleBlock(get(f)))) { where.push('other:' + f.replace('frontend/src/', '')); break }
      }
      return where.length ? where.join('+') : 'none(空类)'
    },
    sites(tok, file) {
      const text = get(file)
      const tpl = templateOf(text)
      const off = text.indexOf(tpl)
      const out = []
      for (const m of tpl.matchAll(/\sclass\s*=\s*"([^"]*)"/g)) {
        if (m[1].trim().split(/\s+/).includes(tok)) out.push(text.slice(0, off + m.index).split('\n').length)
      }
      return out
    },
  }
}
const H = tree('HEAD'), B = tree(REV)

console.log('\n### C-4b 逐令牌归因（挂/摘类名是否真的换掉了该元素的规则集）\n')
const changed = new Map()
for (const f of files) {
  const a = tokens(B.get(f)), b = tokens(H.get(f))
  for (const [t, n] of multisetDiff(a.stat, b.stat)) {
    if (!changed.has(t)) changed.set(t, [])
    changed.get(t).push({ f, n })
  }
}
const rows = [...changed.entries()].sort((x, y) => y[1].reduce((s, e) => s + Math.abs(e.n), 0) - x[1].reduce((s, e) => s + Math.abs(e.n), 0))
for (const [t, es] of rows) {
  const sign = es.map((e) => `${e.f.replace('frontend/src/', '')} ${e.n > 0 ? `+${e.n}` : e.n}`).join('、')
  const adds = es.filter((e) => e.n > 0), dels = es.filter((e) => e.n < 0)
  const kind = !dels.length ? '纯新增' : !adds.length ? '纯移除' : '增减并存(改名)'
  console.log(`- .${t}  [${kind}]  ${sign}`)
  if (adds.length) {
    const f = adds[0].f
    console.log(`    HEAD: 声明 ${H.declWhere(t, f)}，挂类位点(${f.replace('frontend/src/', '')}:${H.sites(t, f).join(',')})`)
  }
  if (dels.length) {
    const f = dels[0].f
    console.log(`    基线: 声明 ${B.declWhere(t, f)}，挂类位点(${f.replace('frontend/src/', '')}:${B.sites(t, f).join(',')})`)
  }
}
console.log(`\n  变化令牌共 ${rows.length} 个。判定口径：声明=none 即空类、挂上或摘掉都值中性；` +
  `纯新增且有声明 → 该元素**新获得**这组声明；纯移除且有声明 → **失去**；改名 → 两侧声明配对逐属性算。`)
console.log('  本表给的是"规则集成不成立"，**不等于**计算样式差异：同值并档、选择器优先级、继承与 UA 回退仍须人工穿透（§9.5 附表 C-4）。')

console.log(`\n### C-5 命令式 DOM 样式通道命中数（对整份 diff）\n`)
const diff = git(['diff', REV, 'HEAD', '--', 'frontend/src'])
const pats = {
  'className =': /\.className\s*=/,
  'el.style.x =': /\.style\.[A-Za-z]+\s*=/,
  'cssText': /cssText/,
  'classList.': /classList\.(add|remove|toggle)/,
  "setAttribute('style'": /setAttribute\(\s*['"]style/,
  'insertAdjacentHTML': /insertAdjacentHTML/,
  'innerHTML': /innerHTML\s*=/,
  '注入 <style>': /document\.head|createElement\(\s*['"]style/,
  'cytoscape .css()': /\.css\(/,
}
for (const [label, re] of Object.entries(pats)) {
  const hits = diff.split('\n').filter((l) => (l.startsWith('+') || l.startsWith('-')) && re.test(l))
  console.log(`- ${label}: ${hits.length}`)
  hits.forEach((h) => console.log(`    ${h.slice(0, 150)}`))
}
const varInTs = execFileSync('bash', ['-lc', "git grep -n 'var(--' HEAD -- 'frontend/src/**/*.ts' | wc -l"], { encoding: 'utf8' }).trim()
console.log(`\n- var(--token) 出现在 .ts 里的行数（应为 0：cytoscape 不解析自定义属性）: ${varInTs}`)

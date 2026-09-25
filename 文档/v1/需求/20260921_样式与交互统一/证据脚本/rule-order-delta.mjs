// 附表 B 的补充通道 **B-6：同内容规则在文件内的相对顺序变化（级联顺序通道）**
//
// 为什么附表 B 结构性看不见它：B 表按 (选择器 → 属性 → 值) 配对，一条规则**内容逐字不变、
// 只是在 style.css 里往前或往后挪了若干条**，B 表读作"无变化"。而 CSS 的胜负规则是
// "同特异性时后出现者胜"——位移会翻转它与其他同特异性规则的胜负。
//
// 口径：把两份 CSS 解析成规则序列（跳过 `@` 开头的 at-rule 与注释），取两版**都存在且内容逐字
// 相同**的规则，把基线顺序映射到 HEAD 顺序，求最长递增子序列（LIS）；不在 LIS 里的即"相对顺序
// 被改变"。再对每条被位移的规则，列出它与谁发生了**次序倒置**、其中哪些**共享类令牌且同特异性**
// （只有这种倒置才可能翻转决议值）。
//
// 用法（cwd = 工作树根）：node "文档/需求/20260921_样式与交互统一/证据脚本/rule-order-delta.mjs" [revA] [path]
import { execFileSync } from 'node:child_process'

const REV = process.argv[2] ?? '07f8d8d'
const PATH = process.argv[3] ?? 'frontend/src/style.css'
const git = (a) => execFileSync('git', a, { encoding: 'utf8' })

function rules(text) {
  const t = text.replace(/\/\*[\s\S]*?\*\//g, '')
  const out = []
  let i = 0
  const n = t.length
  while (i < n) {
    const open = t.indexOf('{', i)
    if (open < 0) break
    let depth = 0, j = open
    for (; j < n; j++) {
      if (t[j] === '{') depth++
      else if (t[j] === '}') { depth--; if (!depth) break }
    }
    const prelude = t.slice(i, open).replace(/\s+/g, ' ').trim()
    const body = t.slice(open + 1, j).replace(/\s+/g, ' ').trim()
    if (!prelude.startsWith('@') && prelude && body) out.push({ sel: prelude, body })
    i = j + 1
  }
  return out
}
const show = (rev) => git(['show', `${rev}:${PATH}`])
const key = (r) => `${r.sel}\u0000${r.body}`
const clsOf = (sel) => [...sel.matchAll(/\.([\w-]+)/g)].map((x) => x[1])
const spec = (sel) => {
  const c = (sel.match(/\.[\w-]+|\[[^\]]*\]|:[\w-]+/g) ?? []).filter((x) => !/^::/.test(x) && !/^:(is|where|not|has)$/.test(x)).length
  const e = (sel.match(/(^|[\s>+~(])\w[\w-]*|$/g) ?? []).filter((x) => /\w/.test(x)).length
  return [c, e]
}

const A = rules(show(REV))
const B = rules(show('HEAD'))
const ka = A.map(key)
const posB = new Map()
B.map(key).forEach((k, i) => { if (!posB.has(k)) posB.set(k, i) })
const SA = []
{ const seen = new Set(); ka.forEach((k, i) => { if (posB.has(k) && !seen.has(k)) { seen.add(k); SA.push({ k, i, sel: A[i].sel, body: A[i].body, ai: SA.length } ) } }) }
const mapped = SA.map((x) => posB.get(x.k))

// 最长递增子序列（ patience + 回溯）
const tails = [], tIdx = [], parent = []
mapped.forEach((v, i) => {
  let lo = 0, hi = tails.length
  while (lo < hi) { const mid = (lo + hi) >> 1; if (tails[mid] < v) lo = mid + 1; else hi = mid }
  tails[lo] = v; tIdx[lo] = i; parent[i] = lo ? tIdx[lo - 1] : -1
})
const inLIS = new Set()
for (let p = tIdx.length ? tIdx[tIdx.length - 1] : -1; p >= 0; p = parent[p]) inLIS.add(p)
const moved = SA.map((x, i) => ({ ...x, i, v: mapped[i] })).filter((x) => !inLIS.has(x.i))

console.log(`### B-6 同内容规则的相对顺序位移（${PATH}）\n`)
console.log(`规则条数：基线 ${A.length} → HEAD ${B.length}；两版**逐字相同**的规则 ${SA.length} 条；其中相对顺序被改变的 **${moved.length}** 条。`)
console.log('（附表 B 对这批规则一律判"无变化"，因为选择器与值都没动。）\n')
for (const r of moved) {
  const inv = SA.filter((x, i) => i !== r.i && ((i < r.i) !== (mapped[i] < r.v)))
  const myCls = new Set(clsOf(r.sel))
  const [rc] = spec(r.sel)
  const rivals = inv.filter((x) => {
    const shared = clsOf(x.sel).some((c) => myCls.has(c))
    return shared && spec(x.sel)[0] === rc
  })
  console.log(`- ${r.sel}`)
  console.log(`    值：${r.body}`)
  console.log(`    位次：基线第 ${r.i + 1}/${SA.length} 条 → HEAD 第 ${r.v + 1}/${B.length} 条；次序倒置 ${inv.length} 条`)
  console.log(`    倒置对象中**共享类令牌且同特异性**的：${rivals.length} 条`)
  rivals.slice(0, 10).forEach((x) => console.log(`      · ${x.sel}  {${x.body.slice(0, 70)}}`))
  if (!rivals.length) console.log('      ·（无 → 位移不改变任何位点的决议值，属纯排版噪音）')
}
console.log('\n判定口径：只有"倒置对象里存在共享同一类令牌、且类计数相同的规则"才可能翻转决议值；其余为噪音。')
console.log('范围：仅同一文件内的顺序。跨文件（style.css ↔ 组件 scoped 块）的先后由打包顺序决定，不在本表口径内。')
if (!moved.length) console.log('\n结论：本文件无同内容规则位移。')

// ---------- 第二阶段：真正能翻转决议值的竞争者 ----------
// 上一段的"共享类令牌"筛法**太窄也太宽**：太窄——`.card{padding:20px}` 与 `.empty{padding:45px}`
// 不共享任何令牌，却会因元素 `class="card empty"` 共挂而直接竞争同一属性；太宽——只看类计数、
// 不看属性是否重叠。本阶段改成正确的三条件：
//   ① 该竞争类与位移规则的类**在同一元素上共挂**（从全仓 `class="…"` 属性实测）；
//   ② 特异性**相同**（这里取"同为单类选择器、无元素/无伪类"这一可判定的保守子集）；
//   ③ 声明的属性集合**有重叠**（含 shorthand 前缀关系：`padding` 与 `padding-top` 算重叠）。
// 只有三个条件同时成立、且它与位移规则的**先后关系在两版之间翻转**，才是真实的渲染变化。
const propsOf = (body) => body.split(';').map((d) => d.split(':')[0]).map((s) => s && s.trim()).filter(Boolean)
const overlaps = (a, b) => a.some((p) => b.some((q) => p === q || p.startsWith(`${q}-`) || q.startsWith(`${p}-`)))

// 收集两个修订版里"与位移类共挂"的类令牌集合（读 .vue 模板的静态 class 属性）
const coTokens = (rev, tok) => {
  const files = git(['ls-tree', '-r', '--name-only', rev, '--', 'frontend/src']).trim().split('\n').filter((f) => f.endsWith('.vue') && !f.includes('legacyGraph/'))
  const set = new Set()
  for (const f of files) {
    let text
    try { text = git(['show', `${rev}:${f}`]) } catch { continue }
    for (const m of text.matchAll(/\sclass\s*=\s*"([^"]*)"/g)) {
      const list = m[1].trim().split(/\s+/)
      if (list.includes(tok)) for (const c of list) if (c !== tok) set.add(c)
    }
  }
  return set
}
const singleClass = (sel) => { const m = /^\.([\w-]+)$/.exec(sel); return m ? m[1] : null }

console.log('\n### B-6 第二阶段：同元素共挂 + 同特异性 + 同属性的真实竞争者\n')
let anyFlip = false
for (const r of moved) {
  const tok = singleClass(r.sel)
  if (!tok) { console.log(`- ${r.sel}：非单类选择器，第二阶段未覆盖（人工判定）`); continue }
  const rp = propsOf(r.body)
  for (const rev of [REV, 'HEAD']) {
    const R = rules(show(rev))
    const mine = R.findIndex((x) => key(x) === r.k)
    const co = coTokens(rev, tok)
    const rivals = []
    R.forEach((x, i) => {
      const c = singleClass(x.sel)
      if (!c || !co.has(c) || i === mine) return
      if (!overlaps(rp, propsOf(x.body))) return
      rivals.push({ c, i, after: i > mine, body: x.body })
    })
    // 位次一律 1 基，与第一阶段 `${r.v + 1}/${B.length}` 同源，否则同一规则在两阶段差 1
    const mark = rivals.length ? rivals.map((x) => `.${x.c}(位次${x.i + 1},${x.after ? '后' : '前'})`).join(' ') : '无'
    console.log(`- [${rev}] .${tok} 位次 ${mine + 1}/${R.length}；与之共挂、同特异性、争同一属性的类：${mark}`)
    if (rev === REV) globalThis.__base = rivals.map((x) => `${x.c}:${x.after ? 'after' : 'before'}`).sort().join(' ')
    else {
      const now = rivals.map((x) => `${x.c}:${x.after ? 'after' : 'before'}`).sort().join(' ')
      if (now !== globalThis.__base) { anyFlip = true; console.log(`    ⇒ **胜负翻转**：基线 [${globalThis.__base}] → HEAD [${now}]`) }
      else console.log(`    ⇒ 先后关系未变 ⇒ 决议值不变（${now || '无竞争者'}）`)
    }
  }
}
console.log(`\n第二阶段结论：${anyFlip ? '存在真实翻转，必须按逐属性登记为视觉变化' : '无翻转——位移不改变任何位点的决议值（此结论只对"单类 vs 单类"这一可判定子集负责）'}。`)


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
const perFile = []
let sites = 0
let groups = 0
for (const f of files) {
  let a, b, headText
  try {
    a = tokens(git(['show', `${REV}:${f}`]))
  } catch {
    a = { stat: [], dyn: [] }
  }
  headText = git(['show', `HEAD:${f}`])
  b = tokens(headText)
  const d = multisetDiff(a.stat, b.stat)
  const dynChanged = JSON.stringify(a.dyn.sort()) !== JSON.stringify(b.dyn.sort())
  if (!d.length && !dynChanged) continue
  perFile.push({ f, d, headText })
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

// ---------- C-4c：C-4 的结构性盲点（内联 style 搬进**基线已存在**的类） ----------
// C-4 只做 class 令牌多重集差。若某个 `style="a:b"` 被删掉、其值改由该元素**本来就挂着**的类
// 提供，则令牌集合完全不变 → C-4 输出 0 行，附表看起来"该文件无通道变化"。
// 本段按 (标签 + class 属性原文) 配对，只对**同一 key** 比较 style 属性多重集：
// key 相同 ⇒ 该元素没换类；style 不同 ⇒ 变化发生在 C-4 看不见的那条通道上。
function byKey(text) {
  const tpl = templateOf(text)
  const m = new Map()
  for (const x of tpl.matchAll(/<([A-Za-z][\w-]*)((?:\s+[^<>]*?)?)\s*\/?>/g)) {
    const tag = x[1]
    if (tag === 'template') continue
    const attrs = x[2] ?? ''
    const c = /\sclass\s*=\s*"([^"]*)"/.exec(attrs)
    const s = /\sstyle\s*=\s*"([^"]*)"/.exec(attrs)
    if (!s && !c) continue
    const k = `${tag} ${c ? c[1].trim().split(/\s+/).sort().join(' ') : ''}`.trim()
    if (!m.has(k)) m.set(k, [])
    m.get(k).push(s ? s[1].replace(/\s+/g, ' ').trim() : '')
  }
  for (const v of m.values()) v.sort()
  return m
}
const show = (rev, f) => { try { return git(['show', `${rev}:${f}`]) } catch { return '' } }
const tagFacts = (line) => {
  const cls = [], sty = []
  for (const x of line.matchAll(/<([A-Za-z][\w-]*)((?:\s+[^<>]*?)?)\s*\/?>/g)) {
    if (x[1] === 'template') continue
    const c = /\sclass\s*=\s*"([^"]*)"/.exec(x[2] ?? '')
    const s = /\sstyle\s*=\s*"([^"]*)"/.exec(x[2] ?? '')
    if (!c && !s) continue
    cls.push(`${x[1]} ${c ? c[1].trim().split(/\s+/).sort().join(' ') : ''}`.trim())
    sty.push(s ? s[1].replace(/\s+/g, ' ').trim() : '')
  }
  return { cls: cls.sort(), sty: sty.sort() }
}
// 窄口径：只在**同一 hunk 内 1:1 配对的行**上比较——同行号替换几乎必然是同一个 DOM 位点。
console.log('\n### C-4c（窄口径·行级配对）class 未变而内联 style 变化\n')
let narrow = 0
const narrowHits = []
{
  const raw = git(['diff', '-U3', REV, 'HEAD', '--', 'frontend/src'])
  let f = '', bl = 0, hl = 0, minus = [], plus = []
  const flush = () => {
    if (minus.length && minus.length === plus.length) {
      for (let i = 0; i < minus.length; i++) {
        const a = tagFacts(minus[i].t), b = tagFacts(plus[i].t)
        if (!a.cls.length && !b.cls.length) continue
        if (a.cls.join('\u0000') !== b.cls.join('\u0000')) continue
        if (a.sty.join('\u0000') === b.sty.join('\u0000')) continue
        if (!a.sty.some((s) => s) && !b.sty.some((s) => s)) continue
        narrow++
        narrowHits.push(`${f.replace('frontend/src/', '')}@${minus[i].n}`)
        console.log(`- ${f.replace('frontend/src/', '')}:${minus[i].n}→${plus[i].n}  〈${a.cls.join(' / ')}〉`)
        console.log(`    基线 style: ${a.sty.filter(Boolean).join(' | ') || '(无)'}`)
        console.log(`    HEAD style: ${b.sty.filter(Boolean).join(' | ') || '(无)'}`)
      }
    }
    minus = []; plus = []
  }
  for (const line of raw.split('\n')) {
    if (line.startsWith('diff --git ')) { flush(); f = line.split(' b/')[1] ?? ''; continue }
    if (!f.endsWith('.vue')) continue
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line)
    if (h) { flush(); bl = +h[1]; hl = +h[2]; continue }
    if (line.startsWith('-')) { minus.push({ n: bl, t: line.slice(1) }); bl++ }
    else if (line.startsWith('+')) { plus.push({ n: hl, t: line.slice(1) }); hl++ }
    else { flush(); bl++; hl++ }
  }
  flush()
}
console.log(`\n  窄口径命中 ${narrow} 处：这类位点 **C-4 完全看不见**（class 令牌零变化），必须单独进附表。`)
console.log('  口径限制：只覆盖"同一 hunk 内行数相等且逐位对齐"的替换；整块搬移/插入会退化为宽口径候选，故两口径并列输出。')

console.log('\n### C-4c（宽口径·文件级多重集配对）同上，但按整文件配对，含假象\n')
let blind = 0
let oneSided = 0
let noise = 0
for (const f of files) {
  const a = byKey(show(REV, f))
  const b = byKey(show('HEAD', f))
  const keys = new Set([...a.keys(), ...b.keys()])
  for (const k of [...keys].sort()) {
    const xa = a.get(k), yb = b.get(k)
    const x = (xa ?? []).join(' | ')
    const y = (yb ?? []).join(' | ')
    if (x === y) continue
    if (!xa || !yb) { oneSided++; continue }
    // 两侧都保有同一 (标签+class) 键 ⇒ 该元素**没换类**，C-4 的令牌差为 0；
    // 再要求至少一侧带**非空** style，排除"同键元素数量变化但都没写 style"这类无值影响的噪声。
    const hasVal = (arr) => arr.some((s) => s !== '')
    if (!hasVal(xa) && !hasVal(yb)) { noise++; continue }
    blind++
    console.log(`- ${f.replace('frontend/src/', '')}  〈${k}〉`)
    console.log(`    基线 style: ${x || '(该键存在，但元素无 style 属性)'}`)
    console.log(`    HEAD style: ${y || '(该键存在，但元素无 style 属性)'}`)
  }
}
console.log(`\n  宽口径命中 ${blind} 组 (文件, 标签+class) 键：**含多重集配对假象**（元素其实换了类名、已被 C-4 记过），只作兜底候选网。`)
console.log(`  另有 ${oneSided} 组"键只在一侧存在"（元素换了类名，或该元素已无 class/style）——这些由 C-4 的令牌差覆盖，此处不重复登记。`)
console.log(`  噪声 ${noise} 组：两侧同键但元素都没写 style，只是同键元素数量变化，无值影响，不登记。`)
console.log('  **配对口径**：本表按 (标签+class 集合) 的**多重集**配对，不保证两行是同一个 DOM 元素；')
console.log('  它只保证"存在一个 class 未变、而内联 style 动了的候选"，逐位点结论仍须按上表行号回原文核对。')

// ---------- C-4d：第八条通道——**元素/组件类型替换**（换标签，既不换 class 也不改声明） ----------
// `<select>` 整体换成 `<AppSelect>`：`<style>` 一条没改（B 看不见）、静态 class 令牌没变（C-4 看不见），
// 但该位点的**整套计算样式**从 UA 原生控件换成了组件自己的 trigger/面板。按标签多重集差穷举。
const NATIVE = new Set(['select', 'option', 'input', 'button', 'textarea', 'table', 'tr', 'td', 'th'])
console.log('\n### C-4d 标签多重集差异：换元素/换组件（class 与声明都不动的一条通道）\n')
const tagDeltaTotal = new Map()
let swapped = 0
for (const f of files) {
  const count = (rev) => {
    const c = new Map()
    for (const m of templateOf(show(rev, f)).matchAll(/<([A-Za-z][\w.-]*)/g)) c.set(m[1], (c.get(m[1]) ?? 0) + 1)
    return c
  }
  const a = count(REV), b = count('HEAD')
  const d = [...new Set([...a.keys(), ...b.keys()])]
    .map((t) => [t, (b.get(t) ?? 0) - (a.get(t) ?? 0)])
    .filter(([t, n]) => n !== 0 && (NATIVE.has(t) || t === 'option' || /^[A-Z]/.test(t)))
    .sort((x, y) => Math.abs(y[1]) - Math.abs(x[1]))
  const lost = d.filter(([, n]) => n < 0).map(([t]) => t)
  // 只在"原生标签减少 + 自定义组件增加"同时出现时报为**替换**，纯增删标签不属本通道
  if (!lost.length || !d.some(([t, n]) => n > 0 && /^[A-Z]/.test(t))) continue
  swapped++
  console.log(`- ${f.replace('frontend/src/', '')}  ${d.map(([t, n]) => `${t} ${n > 0 ? '+' : ''}${n}`).join('、')}`)
  for (const [t, n] of d) tagDeltaTotal.set(t, (tagDeltaTotal.get(t) ?? 0) + n)
}
console.log(`\n  命中 ${swapped} 个文件；全仓标签净变化：` +
  [...tagDeltaTotal.entries()].sort((x, y) => Math.abs(y[1]) - Math.abs(x[1])).map(([t, n]) => `${t} ${n > 0 ? '+' : ''}${n}`).join('、'))
console.log('  判定：UA 原生控件的整套默认样式（下拉面板、option 排版、focus 环）被组件规则替换，**不可能值中性**；')
console.log('  它是"统一控件"的既定需求项（见 §9.1 A5 的 9 ⇒ 0），但**渲染换轨必须逐位点登记**，不能只记一个计数。')

// ---------- 自检：本脚本自身必须先被验一次 ----------
// 附表曾把"穷举"当成脚本的固有能力，而本轮恰恰是脚本的 `<template>` 截断 bug 让 11 个文件
// 静默漏检。因此这里固化三条断言，任一不过即**非零退出**，让下游复核者无法拿到一份空跑的产物。
const fails = []
let HAND = 0
const check = (cond, msg) => { if (!cond) fails.push(msg) }

// ① 抽取器单元断言：带属性的开标签不得提前结束区间（截断 bug 的回归守卫）
//    关键在 after-slot：它位于**内层插槽的 `</template>` 之后**，若深度被提前扣到 0 就会被静默丢掉。
{
  const t = `<template>\n  <div class="outer">\n    <template #slot>\n      <button class="in-slot-btn">x</button>\n    </template>\n    <span class="after-slot-danger">y</span>\n  </div>\n</template>\n<script>const s = 'class="injected-string"</script>`
  const tk = tokens(t).stat
  check(tk.includes('in-slot-btn'), '① 抽取器漏掉插槽内部类名')
  check(tk.includes('after-slot-danger'), '① 抽取器截断：嵌套 <template #slot> 的 </template> 之后的类名被静默丢掉')
  check(tk.includes('outer'), '① 抽取器漏掉外层类名')
  check(!tk.includes('injected-string'), '① 抽取器越界：<script> 字符串字面量被当成模板 class')
}
// ② 阴性对照（mutate-and-detect）：改一个真实位点的类名，穷举必须**只**报出这一处改动
{
  let probe = null
  for (const r of perFile) {
    for (const [tok, n] of r.d) {
      if (n <= 0) continue
      const needle = `class="${tok}"`
      const i = r.headText.indexOf(needle)
      if (i >= 0 && r.headText.indexOf(needle, i + 1) < 0) { probe = { ...r, tok, i, needle }; break }
    }
    if (probe) break
  }
  check(!!probe, '② 无法取得阴性对照样本（找不到可唯一替换的完整 class 属性）')
  if (probe) {
    const { f, tok, i, needle, headText } = probe
    const mutated = headText.slice(0, i) + 'class="zz-mutated"' + headText.slice(i + needle.length)
    const d2 = multisetDiff(tokens(headText).stat, tokens(mutated).stat).map(([t, n]) => `${t}${n > 0 ? '+' : ''}${n}`).sort()
    const want = ['zz-mutated+1', `${tok}-1`].sort()
    check(JSON.stringify(d2) === JSON.stringify(want),
      `② 阴性对照失效：在 ${f} 把 class="${tok}" 换成 zz-mutated 后差分应为 [${want}]，实得 [${d2}]`)
  }
}
// ④ 窄口径必须命中已人工核对的两处（这两处正是附表 C-4 整表看不见的那条通道）
{
  const want = ['flow/FlowList.vue@70', 'project/ProjectBinding.vue@114'].sort()
  const got = [...new Set(narrowHits)].sort()
  check(JSON.stringify(got) === JSON.stringify(want),
    `④ 窄口径漂移：期望 [${want}]，实得 [${got}] → 第七通道的穷举不再覆盖已知位点`)
}
// ⑤ 换标签通道的总数与文件数（第五轮 W4 人工查出、此前八表全无的那条）
{
  const want = { select: -9, option: -20, AppSelect: 9 }
  for (const [t, n] of Object.entries(want))
    check(tagDeltaTotal.get(t) === n, `⑤ 标签净变化漂移：${t} 期望 ${n}，实得 ${tagDeltaTotal.get(t) ?? '(无)'}`)
  check(swapped === 2, `⑤ 换标签位点文件数期望 2（BuildReviewPage + FlowTestWorkspace），实得 ${swapped}`)
}
// ③ 手工核对位点必须出现在穷举输出中（防"穷举"退化成子集）
{
  const hand = [

    ['frontend/src/project/LinkMappings.vue', 'danger-ghost', -1],
    ['frontend/src/project/LinkMappings.vue', 'danger-btn', 1],
    ['frontend/src/ontology/ObjectWorkspace.vue', 'ow-more-menu', -1],
    ['frontend/src/ontology/ObjectWorkspace.vue', 'row-menu-list', 1],
    ['frontend/src/tools/InstanceGraph.vue', 'graph-hint', -1],
    ['frontend/src/tools/InstanceGraph.vue', 'canvas-hint', 1],
    ['frontend/src/tools/KnowledgeCanvas.vue', 'kc-hint', -1],
    ['frontend/src/flow/FlowList.vue', 'is-warn', 1],
    ['frontend/src/project/ImplementationManager.vue', 'under-check', 2],
    ['frontend/src/settings/ConfigurationTransfer.vue', 'tools-below', 1],
  ]
  HAND = hand.length
  const got = new Map()
  for (const r of perFile) for (const [t, n] of r.d) got.set(`${r.f}\u0000${t}`, n)
  for (const [f, t, n] of hand) check(got.get(`${f}\u0000${t}`) === n, `③ 手工位点缺失/数值不符：${f} .${t} 期望 ${n > 0 ? '+' : ''}${n}，实得 ${got.get(`${f}\u0000${t}`) ?? '(无)'}`)
  check(sites === 88 && groups === 26, `③ 总数漂移：实测 ${groups} 文件 / ${sites} 位点，附表登记为 26 / 88`)
}
console.log(`\n### 自检\n`)
if (fails.length) {
  for (const m of fails) console.log(`SELF-CHECK FAIL: ${m}`)
  console.log('\n⇒ 本报告不可用于验收：先修脚本再重跑。')
  process.exit(1)
}
console.log(`SELF-CHECK OK：① 抽取器（嵌套插槽/越界字符串）② 阴性对照（改类名必报）③ ${HAND} 个手工位点 + 总数 26/88 全部命中；④ 第七通道窄口径 2 处对齐；⑤ 换标签通道 select−9/option−20/AppSelect+9、2 文件。`)
console.log('  边界（诚实声明）：本脚本**只看静态模板属性**。它不覆盖 :class/:style 的动态求值、:deep() 与继承、')
console.log('  以及 CSS 侧的选择器/值改动（那些由附表 B / C-1 / C-2 / C-4c / C-5 / C-6 负责）。')

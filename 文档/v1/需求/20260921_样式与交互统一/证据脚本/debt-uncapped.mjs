// 一次性测量脚本：复刻 audit-design-debt.mjs 的规则与排除口径，但**不做 1000 条截断**，
// 用于拿到可信的分类计数。不入库。
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'

const root = process.argv[2]
const rules = [
  ['hard-coded-color', /(^|[^\w])#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![\w])/g],
  ['functional-color', /\b(?:rgb|rgba|hsl|hsla)\([^)]*\)/g],
  ['tailwind-arbitrary-value', /(?:[A-Za-z0-9_:/-]+-\[[^\]]+\])/g],
  ['inline-style', /\bstyle\s*=\s*(?:\{\{|"|')/g],
  ['px-magic-number', /\b(?:[2-9]|[1-9]\d{1,3})px\b/g],
  ['custom-shadow', /\bbox-shadow\s*:|shadow-\[[^\]]+\]/g],
]
const exts = new Set(['.ts', '.tsx', '.js', '.jsx', '.css', '.vue'])
const excludeDirs = ['frontend/node_modules', 'frontend/dist', 'frontend/src/ontology/legacyGraph', '.design-qa']
const excludeFiles = ['frontend/src/shared/graphStyle.ts']

function walk(dir) {
  const out = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    const rel = path.relative(root, p).split(path.sep).join('/')
    if (e.isDirectory()) {
      if (!excludeDirs.includes(rel)) out.push(...walk(p))
    } else if (exts.has(path.extname(e.name)) && !excludeFiles.includes(rel)) {
      out.push(p)
    }
  }
  return out
}

const files = walk(path.join(root, 'frontend/src')).sort()
const total = {}
const hits = {}
for (const f of files) {
  const rel = path.relative(root, f).split(path.sep).join('/')
  const lines = readFileSync(f, 'utf8').split(/\r?\n/)
  lines.forEach((line, i) => {
    if (/^\s*--[A-Za-z0-9-_]+\s*:/u.test(line)) return
    for (const [type, re] of rules) {
      const m = line.match(new RegExp(re.source, 'g'))
      if (!m) continue
      total[type] = (total[type] || 0) + m.length
      ;(hits[type] ||= []).push(`${rel}:${i + 1} ${m.join(' ')}`.slice(0, 150))
    }
  })
}
console.log('scanned', files.length)
console.log(JSON.stringify(total, null, 2))
for (const t of ['functional-color', 'hard-coded-color', 'tailwind-arbitrary-value', 'inline-style', 'custom-shadow']) {
  console.log('\n## ' + t + ' = ' + (total[t] || 0))
  ;(hits[t] || []).slice(0, 40).forEach(l => console.log('  ' + l))
}

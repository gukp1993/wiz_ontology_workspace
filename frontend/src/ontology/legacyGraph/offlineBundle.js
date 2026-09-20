// offlineBundle —— 图谱包（坐标 + 离线预览 HTML）与 jsonId 导出的客户端实现。
// 旧版走后端 /export/bundle、/export/jsonid；当前工作台没有等价服务，且这两类导出
// 只消费画布显示数据（不含凭据/密码），因此在浏览器本地生成（需求 P3：经当前边界输出）。
// 离线 HTML 内嵌全部渲染数据 + 纯 SVG 渲染脚本，无远程依赖、文本全部转义，断网可查看。

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const trim = (v) => String(v ?? '').trim()

// ---------- jsonId（旧语义格式；有损检测 + 干净路径导出） ----------

/** 旧格式无法表达的内容清单；非空 = 阻止导出（需求 §2：默认阻止有损导出）。 */
export function jsonIdLosses(draft) {
  const losses = []
  for (const n of draft.nodes) {
    if (n.type === '动作') losses.push(`动作「${n.name}」：旧格式没有动作定义`)
    if (n.type === '私有属性') losses.push(`私有属性「${n.name}」：旧格式不携带所属对象`)
    if ((n.type === '共享属性' || n.type === '私有属性') && n.data?.dataType === '时间序列') losses.push(`属性「${n.name}」的时间序列声明`)
  }
  for (const e of draft.edges) {
    if (e.kind === '共享引用') losses.push('共享引用结构：旧格式只有普通连线，不保留「对象属性定义引用共享定义」的身份')
    if (e.kind === '私有属性') losses.push('私有归属关系：旧格式无此语义')
    if (e.kind === '规则关联' || e.kind === '动作关联') losses.push(`${e.kind}：旧格式仅普通连线，无关联身份`)
  }
  if (losses.length) {
    // 同类损失合并展示（最多列 8 条 + 计数）
    const seen = new Map()
    for (const l of losses) seen.set(l, (seen.get(l) || 0) + 1)
    const lines = [...seen.entries()].slice(0, 8).map(([l, c]) => (c > 1 ? `${l}（${c} 处）` : l))
    if (seen.size > 8) lines.push(`…共 ${seen.size} 类`)
    return lines
  }
  return []
}

const JSON_TYPE_URI = { 对象: 'czy:Entity', 共享属性: 'czy:Attribute', 规则: 'czy:Rule' }
const JSON_PREFIX = { 对象: 'entity', 共享属性: 'attribute', 规则: 'rule' }

/** 干净路径（无损失）的 jsonId 构建：镜像旧后端 build_jsonid 的结构与编号规则。 */
export function buildJsonIdLocal(draft, graphName) {
  const counters = { 对象: 0, 共享属性: 0, 规则: 0 }
  const idMap = {}
  const graph = []
  for (const n of draft.nodes) {
    counters[n.type] += 1
    const newId = `czy:${JSON_PREFIX[n.type]}:${String(counters[n.type]).padStart(2, '0')}`
    idMap[n.id] = newId
    graph.push({ '@id': newId, '@type': JSON_TYPE_URI[n.type], name: n.name, nodeType: n.type === '对象' ? '实体' : n.type === '共享属性' ? '属性' : '规则', data: safeData(n) })
  }
  let rel = 0
  for (const e of draft.edges) {
    if (!idMap[e.source] || !idMap[e.target]) continue
    rel += 1
    graph.push({ '@id': `czy:relation:${rel}`, '@type': 'czy:Relationship', source: { '@id': idMap[e.source] }, relation: e.relation, target: { '@id': idMap[e.target] }, description: e.description || '' })
  }
  return {
    '@context': { czy: 'https://example.com/czy/', mx: 'https://example.com/microgrid/' },
    '@id': 'czy:knowledge-graph', '@type': 'czy:KnowledgeGraph',
    version: graphName ? `${graphName}_V1` : (draft.name || ''),
    statistics: { entities: counters['对象'], attributes: counters['共享属性'], rules: counters['规则'], nodes: draft.nodes.length, relationships: rel },
    '@graph': graph,
  }
}
function safeData(n) {
  const out = {}
  for (const [k, v] of Object.entries(n.data || {})) {
    if (k === 'dataType' && v === '时间序列') continue
    out[k] = v
  }
  return out
}

// ---------- 图谱包（store-only ZIP：坐标 JSON + 离线预览 HTML） ----------

export function buildOfflineBundle(draft, graphName) {
  const coordinates = {}
  for (const n of draft.nodes) coordinates[n.id] = { x: Math.round(n.x || 0), y: Math.round(n.y || 0) }
  const coordText = JSON.stringify(coordinates, null, 1)
  const html = offlineHtml(draft, graphName)
  const base = (graphName || draft.name || 'graph').replace(/[\\/:*?"<>|]/g, '_')
  const files = [
    [`${base}.coordinates.json`, new TextEncoder().encode(coordText)],
    [`${base}.preview.html`, new TextEncoder().encode(html)],
  ]
  return { blob: makeZip(files), name: base + '-图谱包.zip' }
}

export function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// --- 最小 store-only ZIP（无压缩；CRC-32 + 本地文件头 + 中央目录），避免引入依赖 ---
function crc32(bytes) {
  let table = crc32._table
  if (!table) {
    table = crc32._table = new Uint32Array(256)
    for (let n = 0; n < 256; n++) {
      let c = n
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
      table[n] = c >>> 0
    }
  }
  let c = 0xffffffff
  for (let i = 0; i < bytes.length; i++) c = table[(c ^ bytes[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}
function makeZip(files) {
  const chunks = []
  const central = []
  let offset = 0
  const u16 = (v) => [v & 0xff, (v >> 8) & 0xff]
  const u32 = (v) => [v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, (v >>> 24) & 0xff]
  for (const [name, data] of files) {
    const nameBytes = new TextEncoder().encode(name)
    const crc = crc32(data)
    const local = new Uint8Array([
      ...u32(0x04034b50), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(0),
      ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(nameBytes.length), ...u16(0),
      ...nameBytes, ...data,
    ])
    chunks.push(local)
    central.push(new Uint8Array([
      ...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(0),
      ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(nameBytes.length), ...u16(0), ...u16(0),
      ...u16(0), ...u16(0), ...u32(0), ...u32(offset), ...nameBytes,
    ]))
    offset += local.length
  }
  const centralStart = offset
  let centralLen = 0
  for (const c of central) { chunks.push(c); centralLen += c.length }
  chunks.push(new Uint8Array([
    ...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length),
    ...u32(centralLen), ...u32(centralStart), ...u16(0),
  ]))
  return new Blob(chunks, { type: 'application/zip' })
}

// --- 离线预览 HTML：内嵌数据 + 纯 SVG（平移/缩放/图例），无网络、无脚本依赖 ---
function offlineHtml(draft, graphName) {
  const COLORS = { 对象: '#3978c5', 共享属性: '#d15e9a', 私有属性: '#d18f3c', 规则: '#35a167', 动作: '#7a5fb5' }
  const payload = {
    name: trim(graphName) || draft.name || '本体图谱',
    nodes: draft.nodes.map((n) => ({ id: n.id, name: n.name, type: n.type, x: n.x || 0, y: n.y || 0 })),
    edges: draft.edges.map((e) => ({ source: e.source, target: e.target, relation: e.relation })),
  }
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>${esc(payload.name)} · 离线图谱</title>
<style>
body{margin:0;font:13px -apple-system,'PingFang SC',sans-serif;color:#172126;background:#f3f7f6}
header{padding:12px 18px;background:#fff;border-bottom:1px solid #d9e0e2;display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
h1{font-size:16px;margin:0}
small{color:#6b777c}
svg{display:block;width:100vw;height:calc(100vh - 54px);cursor:grab;touch-action:none}
.legend{position:fixed;right:14px;top:60px;background:#fffffff0;border:1px solid #d9e0e2;border-radius:8px;padding:8px 12px;font-size:12px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.edge{stroke:#4d7770;stroke-opacity:.45;fill:none;stroke-width:1.5}
.elabel{font-size:10px;fill:#87500f;paint-order:stroke;stroke:#fff;stroke-width:3px;stroke-linejoin:round;text-anchor:middle;pointer-events:none}
.nlabel{font-size:12px;font-weight:700;fill:#172126;text-anchor:middle;pointer-events:none}
.nkind{font-size:9px;fill:#6b777c;text-anchor:middle;pointer-events:none}
</style>
<body>
<header><h1>${esc(payload.name)}</h1><small>离线图谱预览 · ${payload.nodes.length} 节点 / ${payload.edges.length} 连线 · 拖拽平移，滚轮缩放</small></header>
<svg id="s"><g id="w"></g></svg>
<div class="legend">${Object.entries(COLORS).map(([k, c]) => `<div><i style="background:${c}"></i>${esc(k)}</div>`).join('')}</div>
<script>
const DATA = ${JSON.stringify(payload).replace(/</g, '\\u003c')};
const C = ${JSON.stringify(COLORS)};
const svg = document.getElementById('s'), world = document.getElementById('w');
const byId = Object.fromEntries(DATA.nodes.map(n => [n.id, n]));
let z = 1, tx = 0, ty = 0;
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function render() {
  let out = '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="#657d78"/></marker></defs>';
  out += '<g transform="translate('+tx+','+ty+') scale('+z+')">';
  for (const e of DATA.edges) {
    const a = byId[e.source], b = byId[e.target];
    if (!a || !b) continue;
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    out += '<path class="edge" d="M' + a.x + ',' + a.y + ' L' + b.x + ',' + b.y + '" marker-end="url(#a)"/>';
    out += '<text class="elabel" x="' + mx + '" y="' + (my - 4) + '">' + esc(e.relation) + '</text>';
  }
  for (const n of DATA.nodes) {
    const c = C[n.type] || '#94a3a3';
    const w = Math.min(205, Math.max(112, 76 + [...n.name].length * 9.2)) / 2, h = 20;
    const shape = n.type.includes('属性') ? '<ellipse cx="'+n.x+'" cy="'+n.y+'" rx="'+w+'" ry="'+h+'" fill="'+c+'" fill-opacity=".18" stroke="'+c+'" stroke-width="2"/>' : '<rect x="'+(n.x-w)+'" y="'+(n.y-h)+'" width="'+(w*2)+'" height="'+(h*2)+'" rx="8" fill="'+c+'" fill-opacity=".18" stroke="'+c+'" stroke-width="2"/>';
    out += shape;
    out += '<text class="nlabel" x="'+n.x+'" y="'+(n.y+1)+'">' + esc(n.name) + '</text>';
    out += '<text class="nkind" x="'+n.x+'" y="'+(n.y+15)+'">' + esc(n.type) + '</text>';
  }
  out += '</g>';
  world.innerHTML = out;
}
svg.addEventListener('wheel', e => { e.preventDefault(); const f = e.deltaY < 0 ? 1.12 : 0.89; const nz = Math.max(0.05, Math.min(6, z * f)); tx = e.offsetX - (e.offsetX - tx) * nz / z; ty = e.offsetY - (e.offsetY - ty) * nz / z; z = nz; render(); }, { passive: false });
let drag = null;
svg.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, tx, ty }; svg.setPointerCapture(e.pointerId); });
svg.addEventListener('pointermove', e => { if (!drag) return; tx = drag.tx + e.clientX - drag.x; ty = drag.ty + e.clientY - drag.y; render(); });
svg.addEventListener('pointerup', () => drag = null);
// 初始 fit
(function fit(){ if (!DATA.nodes.length) { render(); return } let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9; for (const n of DATA.nodes){x0=Math.min(x0,n.x);y0=Math.min(y0,n.y);x1=Math.max(x1,n.x);y1=Math.max(y1,n.y)} const W=innerWidth,H=innerHeight-54,pad=60; z=Math.max(0.05,Math.min(2,(W-pad*2)/Math.max(1,x1-x0+220),(H-pad*2)/Math.max(1,y1-y0+120))); tx=W/2-(x0+x1)/2*z; ty=54+H/2-(y0+y1)/2*z; render(); })();
</script></body></html>`
}

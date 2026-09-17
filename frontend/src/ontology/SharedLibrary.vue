<!-- ─── 共享属性库（T03/D05/D10 对齐原型 libraryView + editorView 的 shared 分支）───
     挂载点：App.vue view==='library'。
     props：state — 当前本体草稿（JSON-LD 内存形态；共享属性为 '@type':'mg:SharedProperty' 记录）。
     emits（协议冻结，勿改）：
       before-change — 写内存前发出（App 撤销快照）；
       changed       — 已写入内存（App 自动保存）；
       navigate(view, focus?:{type?,property?,...}) — 「查看引用」跳回具体对象属性：
                       navigate('objects',{type:对象类型id, property:属性id})。
     主入口是列表（原型 libraryView 非挑选态）：每行「维护定义」「查看引用」及
     引用/复制/删除次级动作；「维护定义」「＋ 新建共享属性」进入独立属性表单
     （PropertyManager kind='shared'，主内容整体替换；新建不先插入空记录，
     保存走 form-save 一次落盘，取消不产生记录——T00 契约）。
     「为某对象添加属性」的挑选态（原型 libraryView(true)）在 ObjectWorkspace 编辑态内完成，
     本页不再承担；本页保留跨对象批量能力：引用到对象、复制为私有、粘贴多行、批量复用，
     低频项收进「更多操作」。修改共享定义的引用影响在表单内如实展示（usage 信息）；
     单值与序列不能引用同一份形态不兼容的共享定义（shapeConflict）。 -->
<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import RowMenu from '../shared/RowMenu.vue'
import PropertyManager from './PropertyManager.vue'
import { shortType } from './editorModel'
import { listShared, referencesOf, externalReferencesOf, shapeConflict, copyAsPrivate, propertyTypeLabel, valueShapeOf, detachProperty, makeProperty, localProperties, effectiveProperty, addReference, asShared, parsePropertyRows } from './propertyModel'

const props = defineProps<{ state: any }>()
const emit = defineEmits(['before-change', 'changed', 'navigate'])

// 编辑态：维护定义 / 新建共享定义 → 独立属性表单整体替换主内容（id='' 为新建）。
const editor = ref<null | { id: string }>(null)
const query = ref(''), feedback = ref(''), dialog = ref(''), acting = ref<any>(null)
const targets = ref<string[]>([]), copyTarget = ref('')
// 更多操作（低频批量能力收在列表页，不占属性表单主流程）
const pasteTarget = ref(''), pasted = ref('')
const batchSource = ref(''), batchProps = ref<string[]>([]), batchTargets = ref<string[]>([])
const highlightId = ref('')

const graph = computed(() => props.state.ontology['@graph'])
const types = computed(() => graph.value.filter((n: any) => n['@type'] === 'owl:Class'))
const definitions = computed(() => listShared(graph.value))
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return definitions.value
  return definitions.value.filter((s: any) => ((s['rdfs:label'] || '') + ' ' + (s['rdfs:comment'] || '')).toLowerCase().includes(q))
})
const typeOptions = computed(() => types.value.map((t: any) => ({ value: t['@id'], label: t['rdfs:label'] || t['@id'] })))
const batchSourceProps = computed(() => batchSource.value ? localProperties(graph.value, batchSource.value) : [])
watch(batchSource, () => { batchProps.value = [] })

function before() { emit('before-change') }
function changed() { emit('changed') }
function mutate(fn: any) { before(); fn(); changed() }
const typeName = (id: string) => types.value.find((n: any) => n['@id'] === id)?.['rdfs:label'] || id
const rangeOf = (s: any) => s['rdfs:range']?.['@id'] || 'xsd:string'
const typeLabel = (s: any) => shortType(rangeOf(s))
const summary = (s: any) => { const c = s['rdfs:comment'] || ''; return c.length > 60 ? c.slice(0, 60) + '…' : (c || '暂无业务定义') }
const usageCount = (s: any) => referencesOf(graph.value, s['@id']).length
const totalUsages = computed(() => definitions.value.reduce((n: number, s: any) => n + usageCount(s), 0))

// ── 维护定义 / 新建：独立表单（PropertyManager kind='shared'），保存成功后定位行 ──
function openEditor(id = '') { editor.value = { id }; feedback.value = '' }
function onSaved(payload: { id: string }) { editor.value = null; locate(payload.id) }
async function locate(id: string) {
  if (!id) return
  highlightId.value = id
  await nextTick()
  document.querySelector(`[data-lib-row="${id}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  setTimeout(() => { if (highlightId.value === id) highlightId.value = '' }, 2600)
}

// ── 查看引用：引用位置清单，可跳回具体对象属性（D05 反向链路） ──
const usageRows = computed(() => acting.value ? referencesOf(graph.value, acting.value['@id']).map((p: any) => ({ id: p['@id'], domain: p['rdfs:domain']?.['@id'] || '', name: typeName(p['rdfs:domain']?.['@id']), api: p['mg:apiName'] || String(p['@id']).replace(/^mg:/, '') })) : [])
function openRef(r: { domain: string; id: string }) { dialog.value = ''; emit('navigate', 'objects', { type: r.domain, property: r.id }) }

function openDialog(name: string, s: any = null) { dialog.value = name; acting.value = s; targets.value = []; copyTarget.value = ''; pasted.value = ''; batchProps.value = []; batchTargets.value = []; feedback.value = '' }

// ── 引用到对象：形态一致性检查 + addReference（同 label / 已有引用则跳过并提示） ──
function referenceMany() {
  const s = acting.value
  if (!s || !targets.value.length) return
  let ok = 0, skip = 0
  const blocked: string[] = []
  mutate(() => {
    for (const t of targets.value) {
      if (shapeConflict(graph.value, s, t)) { blocked.push(typeName(t)); continue }
      if (addReference(graph.value, s, t)) ok++; else skip++
    }
  })
  dialog.value = ''
  let msg = `已引用到 ${ok} 个对象`
  if (skip) msg += `，跳过 ${skip} 个（已有同名属性或引用）`
  if (blocked.length) msg += `；${blocked.join('、')}：单值与时间序列不能引用同一份完整共享定义`
  feedback.value = msg
}

// ── 复制为私有：目标对象已有同名属性则提示，否则生成带完整元数据的新本地属性 ──
function copyOne() {
  const s = acting.value, t = copyTarget.value
  if (!s || !t) return
  const dup = localProperties(graph.value, t).find((p: any) => effectiveProperty(p, graph.value)['rdfs:label'] === (s['rdfs:label'] || ''))
  if (dup) { dialog.value = ''; feedback.value = `${typeName(t)} 已有同名属性「${s['rdfs:label']}」，为避免重复未复制；如需统一维护请改用「引用到对象」。`; return }
  mutate(() => copyAsPrivate(s, graph.value, t))
  dialog.value = ''; copyTarget.value = ''
  feedback.value = `已在 ${typeName(t)} 创建私有副本（含完整元数据与新属性标识），此后可独立修改，不再跟随共享定义。`
}

// 行内更多操作（G4）：只承载已有能力；引用到对象/复制为私有仍走原有弹窗与转换语义
function rowMenuItems(s: any) {
  return [
    { id: 'reference', label: '引用到对象' },
    { id: 'copy', label: '复制为私有' },
    { id: 'delete', label: '删除定义', danger: true },
  ]
}
function onRowMenu(s: any, id: string) {
  if (id === 'delete') { removeShared(s); return }
  openDialog(id, s)
}

// ── 删除定义：契约/接口/项目映射直接引用时禁止；仅有本地属性引用时先解除引用并拷贝内容，防静默断链 ──
function removeShared(s: any) {
  if (!s) return
  const external = externalReferencesOf(props.state, s['@id'])
  if (external.length) { feedback.value = '暂不能删除：此共享定义仍被契约、接口或项目映射直接引用，请先处理：' + external.join('、'); return }
  const usages = referencesOf(graph.value, s['@id'])
  const count = usages.length
  const tip = count
    ? `此共享定义被 ${count} 个对象属性引用。删除后会把这些引用转为各自对象的本地私有属性（复制名称、类型、单位等完整内容，此后不再跟随共享定义同步），再删除共享定义本身；可通过顶部撤销恢复。`
    : `删除共享定义「${s['rdfs:label'] || '未命名'}」？删除后各对象若仍需要该属性，只能改为本地私有属性单独维护；可通过顶部撤销恢复。`
  if (!confirm(tip)) return
  mutate(() => {
    for (const p of usages) detachProperty(p, graph.value)
    props.state.ontology['@graph'] = graph.value.filter((n: any) => n['@id'] !== s['@id'])
  })
  feedback.value = count ? `已删除共享定义，并已把共享内容拷贝到 ${count} 个原引用属性，各自独立维护。` : '已删除共享定义。'
}

// ── 更多操作 · 粘贴多行（原对象属性页能力移入，功能不删） ──
const typeLabels: Record<string, string> = { string: '文本', double: '数值', decimal: '数值', integer: '数值', boolean: '是／否', date: '日期', dateTime: '日期', array: '数组', struct: '结构体' }
const importPreview = computed(() => { try { return { rows: parsePropertyRows(pasted.value), error: '' } } catch (e: any) { return { rows: [], error: e.message } } })
function importRows() {
  if (importPreview.value.error || !importPreview.value.rows.length || !pasteTarget.value) return
  let count = 0, skip = 0
  const t = pasteTarget.value
  mutate(() => {
    const names = new Set(localProperties(graph.value, t).map((p: any) => effectiveProperty(p, graph.value)['rdfs:label']))
    for (const row of importPreview.value.rows) {
      if (names.has(row.name)) { skip++; continue }
      graph.value.push(makeProperty(row, t)); names.add(row.name); count++
    }
  })
  dialog.value = ''; pasted.value = ''
  feedback.value = `已为 ${typeName(t)} 添加 ${count} 项属性${skip ? `，跳过 ${skip} 项同名属性` : ''}`
}

// ── 更多操作 · 批量复用：选源对象属性 → 转共享定义 → 引用到多个目标对象（原能力移入） ──
function distribute() {
  if (!batchSource.value || !batchProps.value.length || !batchTargets.value.length) return
  let count = 0, skip = 0
  mutate(() => {
    for (const pid of batchProps.value) {
      const p = graph.value.find((n: any) => n['@id'] === pid)
      if (!p) continue
      const s = asShared(p, graph.value)
      for (const t of batchTargets.value) { if (addReference(graph.value, s, t)) count++; else skip++ }
    }
  })
  dialog.value = ''
  feedback.value = `已新增 ${count} 个共享引用${skip ? `，跳过 ${skip} 个已有引用或同名属性` : ''}`
}
</script>

<template>
<!-- 编辑态：主内容整体替换为独立共享属性表单 -->
<PropertyManager v-if="editor" :key="editor.id || 'new'" :state="state" kind="shared" :property-id="editor.id" @close="editor = null" @saved="onSaved"/>
<template v-else>
<section class="card library-head"><div class="panelhead"><div><h2>共享属性库</h2><p class="muted">集中维护跨对象复用的属性定义：一处修改，所有引用同步。复制后独立维护；引用时沿用共享定义。各项目的取值实现按对象分别配置。</p></div><button class="primary" @click="openEditor('')">＋ 新建共享属性</button></div></section>
<div class="library-toolbar">
  <input v-model="query" type="search" placeholder="搜索名称或业务定义…" aria-label="搜索共享属性">
  <span class="library-note">共 {{ definitions.length }} 项共享定义 · {{ totalUsages }} 处引用</span>
</div>
<section v-if="filtered.length" class="card library-rows">
  <div v-for="s in filtered" :key="s['@id']" class="line-row" :class="{ 'lib-flash': highlightId === s['@id'] }" :data-lib-row="s['@id']">
    <div class="row-main">
      <strong>{{ s['rdfs:label'] || '未命名共享属性' }}</strong>
      <small>{{ propertyTypeLabel(s, graph) }}{{ s['mg:valueSuffix'] ? ' · 单位 ' + s['mg:valueSuffix'] : '' }} · {{ usageCount(s) }} 处引用</small>
      <small class="muted">{{ summary(s) }}</small>
    </div>
    <div class="tools lib-row-actions">
      <button class="primary" @click="openEditor(s['@id'])">维护定义</button>
      <button class="row-link" @click="openDialog('usages', s)">查看引用</button>
      <!-- G4：引用到对象／复制为私有／删除定义收进更多操作（能力不变，删除置底且危险色） -->
      <RowMenu :items="rowMenuItems(s)" :aria-label="'更多操作 · ' + (s['rdfs:label'] || '未命名共享属性')" @pick="onRowMenu(s, $event)"/>
    </div>
  </div>
</section>
<section v-else class="card">
  <div class="empty-state">
    <span class="empty-state-ico">◇</span>
    <p>{{ query ? '没有匹配的共享定义，可调整搜索条件。' : '共享属性库为空。可新建共享定义，或在对象属性中选择「转为共享属性」。' }}</p>
    <button v-if="!query" type="button" class="primary" @click="openEditor('')">＋ 新建共享属性</button>
  </div>
</section>

<!-- 更多操作：粘贴多行、批量复用等低频能力（功能不删，不占属性表单主流程） -->
<details class="technical-section library-more">
  <summary>更多操作</summary>
  <p class="field-help">粘贴多行、批量复用等跨对象操作收在这里；单个定义的维护在每行「维护定义」中完成。</p>
  <div class="tools">
    <button @click="openDialog('paste')">粘贴多行属性到对象</button>
    <button @click="openDialog('batch')">批量复用对象属性</button>
  </div>
</details>
<p v-if="feedback" :class="feedback.startsWith('已') ? 'inline-success' : 'inline-error'" role="status">{{ feedback }}</p>

<div v-if="dialog" class="modal-backdrop" @click.self="dialog = ''" @keydown.esc.stop="dialog = ''">
  <section class="modal-card sheet-dialog dialog-lg" role="dialog" aria-modal="true" :aria-label="dialog === 'usages' ? '查看引用' : dialog === 'reference' ? '引用共享属性到对象' : dialog === 'copy' ? '复制为私有属性' : dialog === 'paste' ? '粘贴多行属性' : '批量复用对象属性'">
    <div class="panelhead"><h2>{{ { usages: '查看引用 · ' + (acting?.['rdfs:label'] || ''), reference: '引用共享属性到对象', copy: '复制为私有属性', paste: '粘贴多行属性到对象', batch: '批量复用对象属性' }[dialog] }}</h2><button aria-label="关闭" @click="dialog = ''">×</button></div>
    <template v-if="dialog === 'usages'">
      <p v-if="!usageRows.length" class="field-help">当前没有共享引用；对象上复制的私有副本不跟随此定义更新。</p>
      <div v-for="r in usageRows" :key="r.id" class="line-row"><div class="row-main"><strong>{{ r.name }}</strong><small class="code-like">{{ r.api }}</small></div><button class="row-link" @click="openRef(r)">{{ r.name }} 的属性 →</button></div>
      <p v-if="acting && externalReferencesOf(state, acting['@id']).length" class="inline-error">此定义还被以下内容直接引用，删除前需先处理：{{ externalReferencesOf(state, acting['@id']).join('、') }}</p>
    </template>
    <template v-else-if="dialog === 'reference'">
      <p>把「{{ acting?.['rdfs:label'] }}」引用到以下对象；引用属性的名称、类型、单位跟随此定义统一维护。已有同名属性或引用的对象会跳过。</p>
      <label v-for="t in types" :key="t['@id']" class="check-option"><input v-model="targets" type="checkbox" :value="t['@id']"><span>{{ t['rdfs:label'] }}<small v-if="shapeConflict(graph, acting, t['@id'])" class="field-help">已有同名但形态不同的属性，无法引用</small></span></label>
      <p v-if="!types.length" class="field-help">还没有对象类型，请先到「对象建模」创建。</p>
      <button class="primary" :disabled="!targets.length || !types.length" @click="referenceMany">引用到 {{ targets.length }} 个对象</button>
    </template>
    <template v-else-if="dialog === 'copy'">
      <p>在目标对象上创建一份独立副本：包含完整的名称、类型、单位等元数据，使用新的属性标识，之后可单独修改，不再跟随共享定义。</p>
      <label>目标对象类型<AppSelect :model-value="copyTarget" :options="typeOptions" placeholder="选择对象类型" aria-label="选择目标对象类型" @update:model-value="copyTarget = $event"/></label>
      <button class="primary" :disabled="!copyTarget" @click="copyOne">创建私有副本</button>
    </template>
    <template v-else-if="dialog === 'paste'">
      <p>从 Excel 复制四列：名称、业务描述、类型、单位。支持列标题，目标对象已有同名属性会跳过。</p>
      <pre>SOC　荷电状态，70表示70%　数值　%</pre>
      <label>目标对象类型<AppSelect :model-value="pasteTarget" :options="typeOptions" placeholder="选择对象类型" aria-label="选择目标对象类型" @update:model-value="pasteTarget = $event"/></label>
      <textarea v-model="pasted" aria-label="粘贴属性表格" placeholder="请粘贴以制表符分列的内容"/>
      <p v-if="importPreview.error" class="inline-error" role="alert">{{ importPreview.error }}</p>
      <div v-if="importPreview.rows.length" class="scroll sheet-import-preview"><table><thead><tr><th>名称</th><th>业务描述</th><th>类型</th><th>单位</th></tr></thead><tbody><tr v-for="(r, i) in importPreview.rows" :key="i"><td>{{ r.name }}</td><td>{{ r.description }}</td><td>{{ typeLabels[r.type] }}</td><td>{{ r.suffix }}</td></tr></tbody></table></div>
      <button class="primary" :disabled="!!importPreview.error || !importPreview.rows.length || !pasteTarget" @click="importRows">添加 {{ importPreview.rows.length }} 项</button>
    </template>
    <template v-else>
      <p>选择源对象的属性转为共享定义，再引用到其他对象；已有引用或同名属性的目标会跳过。</p>
      <label>源对象类型<AppSelect :model-value="batchSource" :options="typeOptions" placeholder="选择源对象类型" aria-label="选择源对象类型" @update:model-value="batchSource = $event"/></label>
      <template v-if="batchSource">
        <div class="batch-panel">
          <strong>选择要复用的属性</strong>
          <label v-for="p in batchSourceProps" :key="p['@id']" class="check-option"><input v-model="batchProps" type="checkbox" :value="p['@id']"><span>{{ effectiveProperty(p, graph)['rdfs:label'] || '未命名' }}{{ p['mg:sharedProperty'] ? '（已是共享引用）' : '' }}</span></label>
        </div>
        <div class="batch-panel">
          <strong>引用到以下对象</strong>
          <label v-for="t in types.filter((x: any) => x['@id'] !== batchSource)" :key="t['@id']" class="check-option"><input v-model="batchTargets" type="checkbox" :value="t['@id']"><span>{{ t['rdfs:label'] }}</span></label>
        </div>
      </template>
      <button class="primary" :disabled="!batchSource || !batchProps.length || !batchTargets.length" @click="distribute">复用 {{ batchProps.length }} 项到 {{ batchTargets.length }} 类对象</button>
    </template>
  </section>
</div>
</template>
</template>

<style scoped>
.library-head .panelhead{margin-bottom:0}
.library-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:16px 0;flex-wrap:wrap}
.library-toolbar input[type=search]{width:min(300px,100%);margin:0}
.library-toolbar .library-note{margin:0}
.library-rows{padding:8px 20px}
.line-row{display:flex;align-items:center;gap:14px;padding:13px 4px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.line-row:last-child{border-bottom:0}
.line-row>.tools{flex:none;flex-wrap:wrap}
.row-main{flex:1;min-width:0}
.row-main strong{display:block;font-size:14px;font-weight:600}
.row-main small{display:block;margin-top:3px;font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.lib-row-actions .primary{padding:5px 10px;font-size:12px}
.lib-flash{outline:2px solid var(--focus);outline-offset:-2px;border-radius:7px}
.library-more .tools{margin-top:8px}
.code-like{font-family:ui-monospace,SFMono-Regular,monospace}
.sheet-import-preview{max-height:240px;overflow:auto;margin:15px 0}
.batch-panel{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}
.batch-panel .check-option{font-size:13px}
@media(max-width:800px){.line-row{align-items:flex-start;flex-direction:column}.lib-row-actions{width:100%}}
</style>

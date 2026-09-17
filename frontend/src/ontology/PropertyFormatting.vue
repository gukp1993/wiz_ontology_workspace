<!-- 显示格式编辑器（显示格式优化需求 v1 · 任务 A）：
     只改 PropertyManager 传入的本地草稿节点（保存属性时一次落盘，取消不落盘）。
     A2 未配置语义：组件内部维护本地草稿 local（现有配置的克隆或 null），打开/折叠/切换属性绝不写
       props.property；仅用户实际操作（选方式/样式/输入参数）后 sync——生效配置为 null（未配置或
       natural 且规则为空）时删除 mg:formatting，否则写 {'@type':'@json','@value':clean}。
     A3 类型联动：kind 由 propertyDataType(property, graph) 推导，时间序列另记观测值子类型。
     历史兼容（第 7 节）：mode=code 只读横幅 + 显式替换；历史样式以「历史样式：…（兼容保留）」
       只读摘要保留，选择新样式才替换（清样式专属键、保留通用键）；min/max 小数位保留并提示。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import Field from '../shared/EditorField.vue'
import { formatPreview } from './api'
import { propertyDataType } from './propertyModel'
import {
  decimalLegacyHint, defaultStyle, effectiveConfig, elementTypeOptions, legacyStyleLabel,
  modeOptions, sampleDefault, styleOptions, STYLE_SPECIFIC_KEYS,
} from './formattingOptions'

const props = defineProps<{ property: any; state: any; disabled?:boolean }>(), emit = defineEmits(['before-change', 'changed'])

// ── A3 类型联动：type timeSeries→timeSeries（valueType→观测值子类型）；double/decimal/integer→number；
//    date→date；dateTime→time；array/struct/string/boolean 原样 ──
const graph = computed(() => props.state?.ontology?.['@graph'] || [])
const dataType = computed(() => propertyDataType(props.property, graph.value))
const scalarKind = (t: any): string => ['double', 'decimal', 'integer'].includes(t) ? 'number'
  : t === 'dateTime' ? 'time' : ['date', 'string', 'boolean', 'array', 'struct'].includes(t) ? t : 'string'
const kind = computed(() => dataType.value?.type === 'timeSeries' ? 'timeSeries' : scalarKind(dataType.value?.type))
const valueKind = computed(() => scalarKind(dataType.value?.valueType))
const valueKindLabel = computed(() => (({ string: '文本', number: '数值', boolean: '是／否', date: '日期', time: '时间戳' } as any)[valueKind.value] || '观测值'))

// ── A2 本地草稿：打开/折叠绝不写 props.property ──
const rawConfig = computed(() => props.property?.['mg:formatting']?.['@value'] ?? null)
const clone = (x: any) => x == null ? null : JSON.parse(JSON.stringify(x))
const local = ref<any>(clone(rawConfig.value))
// 外部变化（切换属性 / PropertyManager 按类型清理）才重建草稿；自身 sync 写回的等价内容不重置，
// 保证方式/样式来回切换、清空自然语言规则时表单内已填内容不丢。
watch(() => JSON.stringify(rawConfig.value), json => {
  if (json === JSON.stringify(effectiveConfig(local.value))) return
  local.value = json === 'null' ? null : JSON.parse(json)
})

const isLegacyCode = computed(() => local.value?.mode === 'code')
const displayMode = computed(() => local.value?.mode === 'builtin' ? 'builtin' : 'natural')
const legacyLabel = computed(() => displayMode.value === 'builtin' ? legacyStyleLabel(kind.value, local.value) : '')
const style = computed(() => local.value?.style || defaultStyle(kind.value))
const styleDisplay = computed(() => legacyLabel.value ? `历史样式：${legacyLabel.value}（兼容保留）` : style.value)
const canRestore = computed(() => !!local.value && effectiveConfig(local.value) !== null)
const styleChoices = computed(() => styleOptions(kind.value))
const datePatternWarn = computed(() => kind.value === 'date' && style.value === 'custom' && /HH|mm|ss/.test(String(local.value?.pattern || '')))

function touch() { emit('before-change') }
function ensureLocal() { if (!local.value || typeof local.value !== 'object') local.value = { mode: 'natural' }; return local.value }
// 用户操作后的唯一同步点：生效配置为 null（未配置 / natural 规则为空）→ 删除；否则写干净克隆。
function sync() {
  const clean = effectiveConfig(local.value)
  if (clean && props.property) props.property['mg:formatting'] = { '@type': '@json', '@value': clone(clean) }
  else if (props.property) delete props.property['mg:formatting']
  emit('changed')
}

function setMode(v: string) {
  if (props.disabled || v === displayMode.value) return
  touch()
  const l = ensureLocal()
  l.mode = v
  if (v === 'builtin') {
    if (!styleChoices.value.some(o => o.value === l.style)) l.style = defaultStyle(kind.value)
    if (kind.value === 'number') { // 旧单位/小数位兼容读取：转为常用格式时作为起始参数（仅在用户显式操作后写入）
      if (l.decimals === undefined && l.minDecimals === undefined && l.maxDecimals === undefined && typeof props.property?.['mg:decimalPlaces'] === 'number') l.decimals = props.property['mg:decimalPlaces']
      if (l.suffix === undefined && props.property?.['mg:valueSuffix']) l.suffix = props.property['mg:valueSuffix']
    }
  }
  sync()
}
function setStyle(v: string) {
  if (props.disabled) return
  const l = ensureLocal()
  if (!legacyLabel.value && v === (l.style || defaultStyle(kind.value))) return
  touch()
  if (legacyLabel.value) for (const k of STYLE_SPECIFIC_KEYS) delete l[k] // 历史样式→新样式：清样式专属键，保留通用键
  l.style = v
  sync()
}
// 历史 code 的显式替换（第 7 节）：不做自动转换，替换即重建本地草稿。
function replaceCode(mode: 'builtin' | 'natural') {
  if (props.disabled) return
  touch()
  local.value = mode === 'builtin' ? { mode: 'builtin', style: defaultStyle(kind.value) } : { mode: 'natural' }
  sync()
}
// 恢复原样显示（需求 2）：确认语义、无弹窗，仅清本地草稿，随属性保存生效；无配置时按钮隐藏。
function restore() {
  if (props.disabled) return
  touch()
  local.value = null
  if (props.property) delete props.property['mg:formatting']
  result.value = ''; error.value = ''
  emit('changed')
}

// ── 路径式读写：base '' 为根配置，'elementFormat'/'timeFormat'/'valueFormat' 为子配置 ──
function at(base: string): any {
  let o: any = local.value
  for (const k of base ? base.split('.') : []) o = o?.[k]
  return o || {}
}
function containerOf(base: string) {
  const l = ensureLocal()
  let o: any = l
  for (const k of base ? base.split('.') : []) { if (typeof o[k] !== 'object' || o[k] === null) o[k] = {}; o = o[k] }
  return o
}
function put(base: string, key: string, value: any) {
  if (props.disabled) return
  touch()
  const o = containerOf(base)
  if (value === '' || value === undefined) delete o[key]; else o[key] = value
  sync()
}
// 小数位数：填写固定精度时同时删除历史 minDecimals/maxDecimals（第 7 节）；切样式不清 decimals。
function putDecimals(base: string, value: any) {
  if (props.disabled) return
  touch()
  const o = containerOf(base)
  if (value === '' || value === undefined) delete o.decimals
  else { o.decimals = value; delete o.minDecimals; delete o.maxDecimals }
  sync()
}
function setElementType(v: string) {
  if (props.disabled) return
  touch()
  const l = ensureLocal()
  if (v) l.elementType = v
  else { delete l.elementType; delete l.elementFormat } // 回到「原样」时清理元素格式子配置
  sync()
}

// ── 文本值映射行（string 根配置 / 数组元素 / 序列观测值共用；from 重复就地报错，不用后行覆盖前行）──
function mappingRowsOf(base: string): any[] { const m = at(base).mappings; return Array.isArray(m) ? m : [] }
function setMappingAt(base: string, index: number, key: 'from' | 'to', value: string) {
  if (props.disabled) return
  const rows = mappingRowsOf(base)
  if (rows[index] === undefined) return
  touch(); rows[index][key] = value; sync()
}
function addMappingRow(base: string) {
  if (props.disabled) return
  touch(); containerOf(base).mappings = [...mappingRowsOf(base), { from: '', to: '' }]; sync()
}
function removeMappingAt(base: string, index: number) {
  if (props.disabled) return
  const rows = mappingRowsOf(base)
  touch(); rows.splice(index, 1)
  if (!rows.length) delete containerOf(base).mappings
  sync()
}
function mappingDup(base: string): string {
  const seen = new Set<string>(), dups = new Set<string>()
  for (const row of mappingRowsOf(base)) {
    const f = String(row?.from ?? '')
    if (!f) continue
    if (seen.has(f)) dups.add(f); else seen.add(f)
  }
  return dups.size ? `原始值「${[...dups][0]}」重复：请修改或移除重复的映射行。` : ''
}

// ── 结构体字段顺序行：上移/下移/移除/添加；未配置（或清空）时按输入顺序展示 ──
function fieldRows(): any[] { return Array.isArray(local.value?.fields) ? local.value.fields : [] }
function commitFields() { const l = local.value; if (l && Array.isArray(l.fields) && !l.fields.length) delete l.fields }
function setFieldAt(index: number, value: string) {
  if (props.disabled) return
  const rows = fieldRows()
  if (rows[index] === undefined) return
  touch(); rows[index] = value; sync()
}
function moveField(index: number, step: -1 | 1) {
  if (props.disabled) return
  const rows = fieldRows(), j = index + step
  if (j < 0 || j >= rows.length) return
  touch(); [rows[index], rows[j]] = [rows[j], rows[index]]; sync()
}
function removeField(index: number) {
  if (props.disabled) return
  touch(); fieldRows().splice(index, 1); commitFields(); sync()
}
function addField() {
  if (props.disabled) return
  touch(); ensureLocal().fields = [...fieldRows(), '']; sync()
}

// ── 子配置的样式/类型推导（数组元素格式、序列观测值格式）──
function subKind(base: string): string {
  if (base === 'elementFormat') return String(local.value?.elementType || '') || 'string'
  if (base === 'valueFormat') return valueKind.value
  return 'time'
}
function subStyle(base: string): string { return at(base).style || defaultStyle(subKind(base)) }

// ── 样本与预览（第 5 节）：样本为临时状态不落盘；null 样本用勾选；序号防旧响应 ──
const jsonKinds = ['number', 'boolean', 'array', 'struct', 'timeSeries']
const sample = ref(''), sampleNull = ref(false), result = ref(''), error = ref(''), busy = ref(false)
watch([() => props.property?.['@id'], kind], () => {
  sample.value = sampleDefault(kind.value); sampleNull.value = false; result.value = ''; error.value = ''
}, { immediate: true })
let sequence = 0
// 改样本/配置（含切换方式/样式，同一 local 对象原位改、已填字段保留）/属性即失效旧预览。
watch([sample, sampleNull, () => JSON.stringify(local.value), () => props.property?.['@id']], () => {
  sequence++; busy.value = false; result.value = ''; error.value = ''
})
onBeforeUnmount(() => { sequence++ }) // 关闭编辑器后旧异步响应不覆盖任何状态

const naturalExamples: Record<string, string> = {
  string: '把设备编号转为大写并加前缀 ESS-，仅输出结果。',
  number: '输入为 0—100 的 SOC，保留一位小数并加百分号，仅输出结果。',
  boolean: 'true 显示为“运行中”，false 显示为“停机”，仅输出结果。',
  date: '按 YYYY年MM月DD日 输出，仅输出结果。',
  time: '转为东八区 YYYY-MM-DD HH:mm 格式，仅输出结果。',
  array: '每行显示一条告警名称，最多 5 条，超出以“…”结尾，仅输出结果。',
  struct: '按“厂商 / 型号”输出，仅输出结果。',
  timeSeries: '每行输出“时间 数值”，时间到分钟、数值保留一位小数，仅输出结果。',
}
const naturalExample = computed(() => naturalExamples[kind.value] || naturalExamples.string)
const sampleHelp = computed(() => jsonKinds.includes(kind.value)
  ? '按 JSON 输入样本；空值（null）请勾选下方选项。'
  : '直接输入样本内容；空值（null）请勾选下方选项，不要输入 “null” 字样。')

function toggleNull(e: Event) { sampleNull.value = (e.target as HTMLInputElement).checked }

async function preview() {
  if (props.disabled || busy.value) return
  error.value = ''; result.value = ''
  const l = local.value
  if (l?.mode !== 'code') {
    const mode = l?.mode ?? 'natural'
    if (mode === 'natural' && !String(l?.instruction || '').trim()) { error.value = '请填写自然语言格式规则'; return }
    if (mode === 'builtin') {
      if (kind.value === 'number' && style.value === 'percent' && !l.percentInput) { error.value = '请选择百分比原值口径'; return }
      if (kind.value === 'string' && style.value === 'mapping') { const dup = mappingDup(''); if (dup) { error.value = dup; return } }
    }
  }
  let value: any
  if (sampleNull.value) value = null
  else if (jsonKinds.includes(kind.value)) {
    try { value = JSON.parse(sample.value) } catch { error.value = '样本不是有效的 JSON，请检查输入。'; return }
  } else value = sample.value
  const seq = ++sequence
  busy.value = true
  try {
    // 历史代码/历史样式原样发送预览；dataType：'time' 为时间戳、'date' 纯日期、'timeSeries' 序列。
    const d = await formatPreview({ state: props.state, value, dataType: kind.value, config: effectiveConfig(l) || {} })
    if (seq === sequence) result.value = String(d?.formatted ?? '')
  } catch (e) { if (seq === sequence) error.value = (e as Error).message }
  finally { if (seq === sequence) busy.value = false }
}
</script>
<template><section class="sample-panel" aria-label="显示格式"><h3>显示格式</h3>
<p>输入属性原值，输出显示文本；不改变计算、筛选、排序使用的原值。</p>

<!-- 历史 JavaScript 格式（第 7 节）：只读横幅 + 显式替换，替换前可预览现有配置 -->
<div v-if="isLegacyCode" class="legacy-box">
  <p><b>历史 JavaScript 格式（兼容保留）</b>：未改动前继续按此函数显示。</p>
  <pre class="legacy-code" aria-label="历史格式函数内容">{{ local?.code || '' }}</pre>
  <div class="tools">
    <button type="button" :disabled="disabled" @click="replaceCode('builtin')">改用常用格式</button>
    <button type="button" :disabled="disabled" @click="replaceCode('natural')">改用自然语言</button>
  </div>
  <small>可先用下方样本预览当前历史配置的效果；替换后原代码不再保留。</small>
</div>

<!-- A1 格式化方式恰好两项；未配置时默认偏好显示「自然语言」 -->
<Field v-else label="格式化方式" type="select" :model-value="displayMode" :disabled="disabled" :options="modeOptions" @update:model-value="setMode"/>

<template v-if="!isLegacyCode && displayMode==='builtin'">
  <p v-if="kind==='timeSeries'" class="muted">显示样式：时间和值（时间格式与观测值格式分别配置，样式固定）。</p>
  <Field v-else label="显示样式" type="select" :model-value="styleDisplay" :disabled="disabled" :options="styleChoices" @update:model-value="setStyle"/>
  <p v-if="legacyLabel" class="muted legacy-note">当前保留历史样式「{{ legacyLabel }}」的原显示效果；选择新样式后替换，样式专属参数清除，通用参数（小数位、千位分隔、前后缀、空值显示等）保留。</p>

  <!-- 文本：原样 / 模板 / 值映射 -->
  <template v-if="kind==='string'">
    <Field v-if="style==='template'" label="显示模板" :model-value="local?.template||''" example="ESS-{value}" help="用 {value} 代表原值，其余文字原样输出；不执行代码。" :disabled="disabled" @update:model-value="put('','template',$event)"/>
    <div v-if="style==='mapping'" class="mapping-editor">
      <p class="muted">维护「原始值 → 显示文字」：按原始文本精确匹配，未匹配时保留原值。</p>
      <div v-for="(row,i) in mappingRowsOf('')" :key="i" class="map-row">
        <input :value="String(row.from??'')" :disabled="disabled" :aria-label="`原始值 ${i+1}`" placeholder="原始值" @input="setMappingAt('',i,'from',($event.target as HTMLInputElement).value)">
        <span aria-hidden="true">→</span>
        <input :value="String(row.to??'')" :disabled="disabled" :aria-label="`显示文字 ${i+1}`" placeholder="显示文字" @input="setMappingAt('',i,'to',($event.target as HTMLInputElement).value)">
        <button type="button" class="mini" :disabled="disabled" @click="removeMappingAt('',i)">移除</button>
      </div>
      <button type="button" class="mini" :disabled="disabled" @click="addMappingRow('')">添加映射行</button>
      <p v-if="mappingDup('')" class="inline-error" role="alert">{{ mappingDup('') }}</p>
    </div>
    <div v-if="style==='standard'" class="form-grid">
      <Field label="显示前缀" :model-value="local?.prefix||''" :disabled="disabled" @update:model-value="put('','prefix',$event)"/>
      <Field label="显示后缀" :model-value="local?.suffix||''" :disabled="disabled" @update:model-value="put('','suffix',$event)"/>
    </div>
  </template>

  <!-- 数值：小数位 / 千位分隔 / 百分比口径 / 币种 / 前后缀 -->
  <template v-if="kind==='number'">
    <div class="form-grid">
      <Field label="小数位数" type="number" :min="0" :max="20" :model-value="local?.decimals??''" help="0—20 的整数；不填则沿用默认数值精度，填写后为固定小数位。" :disabled="disabled" @update:model-value="putDecimals('',$event)"/>
      <Field v-if="style!=='scientific'" label="千位分隔" type="select" :model-value="local?.grouping===false?'no':'yes'" :options="[{value:'yes',label:'使用'},{value:'no',label:'不使用'}]" :disabled="disabled" @update:model-value="put('','grouping',$event==='yes')"/>
      <Field v-if="style==='percent'" label="百分比原值口径" type="select" :model-value="local?.percentInput||''" :options="[{value:'ratio',label:'0～1（0.7 → 70%）'},{value:'hundred',label:'0～100（70 → 70%）'}]" help="新配置必须选择口径后才能预览。" :disabled="disabled" @update:model-value="put('','percentInput',$event)"/>
      <Field v-if="style==='currency'" label="币种" :model-value="local?.currency??'CNY'" example="CNY、USD、EUR" help="默认 CNY；可输入有效货币代码。" :disabled="disabled" @update:model-value="put('','currency',$event)"/>
      <Field label="显示前缀" :model-value="local?.prefix||''" :disabled="disabled" @update:model-value="put('','prefix',$event)"/>
      <Field label="显示后缀" :model-value="local?.suffix||''" example="kW、kWh、%" help="仅作为显示文字拼接，不做单位换算（1000 加 kV 不会除以 1000）。" :disabled="disabled" @update:model-value="put('','suffix',$event)"/>
    </div>
    <p v-if="decimalLegacyHint(local)" class="muted">{{ decimalLegacyHint(local) }}</p>
  </template>

  <!-- 布尔：默认 / 自定义文字 -->
  <template v-if="kind==='boolean' && style==='custom'">
    <div class="form-grid">
      <Field label="是（true）显示为" :model-value="local?.trueText??'是'" example="在线、启用" :disabled="disabled" @update:model-value="put('','trueText',$event)"/>
      <Field label="否（false）显示为" :model-value="local?.falseText??'否'" example="离线、停用" :disabled="disabled" @update:model-value="put('','falseText',$event)"/>
    </div>
  </template>

  <!-- 日期：仅日期（年/月/日）/ 自定义格式（禁时分秒，无时区） -->
  <template v-if="kind==='date'">
    <Field v-if="style==='date'" label="展示到" type="select" :model-value="local?.precision||'day'" :options="[{value:'year',label:'年（2026）'},{value:'month',label:'月（2026-09）'},{value:'day',label:'日（2026-09-15）'}]" :disabled="disabled" @update:model-value="put('','precision',$event)"/>
    <template v-if="style==='custom'">
      <Field label="格式模板" :model-value="local?.pattern||''" example="YYYY年MM月DD日" help="支持 YYYY、MM、DD；其他文字按原样显示。纯日期不设时区、不补时分秒。" :disabled="disabled" @update:model-value="put('','pattern',$event)"/>
      <p v-if="datePatternWarn" class="inline-error" role="alert">纯日期的格式模板不能包含时分秒（HH/mm/ss），请调整。</p>
    </template>
  </template>

  <!-- 时间戳：仅日期/日期时间/仅时间/相对时间/自定义 + 精度 + 时区 -->
  <template v-if="kind==='time'">
    <Field v-if="style==='date'" label="展示到" type="select" :model-value="local?.precision||'day'" :options="[{value:'year',label:'年'},{value:'month',label:'月'},{value:'day',label:'日'}]" :disabled="disabled" @update:model-value="put('','precision',$event)"/>
    <Field v-if="style==='datetime'||style==='time'" label="展示到" type="select" :model-value="local?.precision||'second'" :options="[{value:'minute',label:'分钟（14:30）'},{value:'second',label:'秒（14:30:25）'}]" :disabled="disabled" @update:model-value="put('','precision',$event)"/>
    <Field v-if="style!=='iso'" label="显示时区" :model-value="local?.timezone??'Asia/Shanghai'" example="Asia/Shanghai、UTC" help="时间戳按此时区显示；可输入 IANA 时区名。默认 Asia/Shanghai。" :disabled="disabled" @update:model-value="put('','timezone',$event)"/>
    <Field v-if="style==='custom'" label="时间模板" :model-value="local?.pattern||''" example="YYYY-MM-DD HH:mm:ss" help="支持 YYYY、MM、DD、HH、mm、ss；其他文字按原样显示。" :disabled="disabled" @update:model-value="put('','pattern',$event)"/>
  </template>

  <!-- 数组：列表 / 分隔文本 + 最多项数 + 折叠的元素格式 -->
  <template v-if="kind==='array'">
    <div class="form-grid">
      <Field v-if="style==='list'||style==='join'" label="最多显示项数" type="number" :min="1" :max="100" :model-value="local?.maxItems??10" help="超出后显示省略提示；不截断真实数组。默认 10，范围 1—100。" :disabled="disabled" @update:model-value="put('','maxItems',$event)"/>
      <Field v-if="style==='join'" label="分隔符" :model-value="local?.separator??'、'" :disabled="disabled" @update:model-value="put('','separator',$event)"/>
    </div>
    <details v-if="style==='list'||style==='join'" class="element-section">
      <summary>元素格式（选填）</summary>
      <p class="muted">默认按原样显示；仅在本处主动选择元素解释类型，不修改属性的数据类型，也不按样本自动推断。</p>
      <Field label="元素解释类型" type="select" :model-value="local?.elementType||''" :options="elementTypeOptions" :disabled="disabled" @update:model-value="setElementType"/>
      <template v-if="local?.elementType">
        <Field label="显示样式" type="select" :model-value="subStyle('elementFormat')" :options="styleOptions(subKind('elementFormat'))" :disabled="disabled" @update:model-value="put('elementFormat','style',$event)"/>
        <template v-if="subKind('elementFormat')==='string'">
          <Field v-if="subStyle('elementFormat')==='template'" label="显示模板" :model-value="at('elementFormat').template||''" example="ESS-{value}" :disabled="disabled" @update:model-value="put('elementFormat','template',$event)"/>
          <div v-if="subStyle('elementFormat')==='mapping'">
            <div v-for="(row,i) in mappingRowsOf('elementFormat')" :key="i" class="map-row">
              <input :value="String(row.from??'')" :disabled="disabled" :aria-label="`元素原始值 ${i+1}`" placeholder="原始值" @input="setMappingAt('elementFormat',i,'from',($event.target as HTMLInputElement).value)">
              <span aria-hidden="true">→</span>
              <input :value="String(row.to??'')" :disabled="disabled" :aria-label="`元素显示文字 ${i+1}`" placeholder="显示文字" @input="setMappingAt('elementFormat',i,'to',($event.target as HTMLInputElement).value)">
              <button type="button" class="mini" :disabled="disabled" @click="removeMappingAt('elementFormat',i)">移除</button>
            </div>
            <button type="button" class="mini" :disabled="disabled" @click="addMappingRow('elementFormat')">添加映射行</button>
            <p v-if="mappingDup('elementFormat')" class="inline-error" role="alert">{{ mappingDup('elementFormat') }}</p>
          </div>
          <div v-if="subStyle('elementFormat')==='standard'" class="form-grid">
            <Field label="显示前缀" :model-value="at('elementFormat').prefix||''" :disabled="disabled" @update:model-value="put('elementFormat','prefix',$event)"/>
            <Field label="显示后缀" :model-value="at('elementFormat').suffix||''" :disabled="disabled" @update:model-value="put('elementFormat','suffix',$event)"/>
          </div>
        </template>
        <div v-if="subKind('elementFormat')==='number'" class="form-grid">
          <Field label="小数位数" type="number" :min="0" :max="20" :model-value="at('elementFormat').decimals??''" :disabled="disabled" @update:model-value="putDecimals('elementFormat',$event)"/>
          <Field v-if="subStyle('elementFormat')!=='scientific'" label="千位分隔" type="select" :model-value="at('elementFormat').grouping===false?'no':'yes'" :options="[{value:'yes',label:'使用'},{value:'no',label:'不使用'}]" :disabled="disabled" @update:model-value="put('elementFormat','grouping',$event==='yes')"/>
          <Field v-if="subStyle('elementFormat')==='percent'" label="百分比原值口径" type="select" :model-value="at('elementFormat').percentInput||''" :options="[{value:'ratio',label:'0～1（0.7 → 70%）'},{value:'hundred',label:'0～100（70 → 70%）'}]" :disabled="disabled" @update:model-value="put('elementFormat','percentInput',$event)"/>
          <Field v-if="subStyle('elementFormat')==='currency'" label="币种" :model-value="at('elementFormat').currency??'CNY'" example="CNY、USD、EUR" :disabled="disabled" @update:model-value="put('elementFormat','currency',$event)"/>
          <Field label="显示前缀" :model-value="at('elementFormat').prefix||''" :disabled="disabled" @update:model-value="put('elementFormat','prefix',$event)"/>
          <Field label="显示后缀" :model-value="at('elementFormat').suffix||''" :disabled="disabled" @update:model-value="put('elementFormat','suffix',$event)"/>
        </div>
        <div v-if="subKind('elementFormat')==='boolean' && subStyle('elementFormat')==='custom'" class="form-grid">
          <Field label="是（true）显示为" :model-value="at('elementFormat').trueText??'是'" :disabled="disabled" @update:model-value="put('elementFormat','trueText',$event)"/>
          <Field label="否（false）显示为" :model-value="at('elementFormat').falseText??'否'" :disabled="disabled" @update:model-value="put('elementFormat','falseText',$event)"/>
        </div>
        <template v-if="subKind('elementFormat')==='date'">
          <Field v-if="subStyle('elementFormat')==='date'" label="展示到" type="select" :model-value="at('elementFormat').precision||'day'" :options="[{value:'year',label:'年'},{value:'month',label:'月'},{value:'day',label:'日'}]" :disabled="disabled" @update:model-value="put('elementFormat','precision',$event)"/>
          <Field v-if="subStyle('elementFormat')==='custom'" label="格式模板" :model-value="at('elementFormat').pattern||''" example="YYYY年MM月DD日" help="仅支持 YYYY、MM、DD。" :disabled="disabled" @update:model-value="put('elementFormat','pattern',$event)"/>
        </template>
        <template v-if="subKind('elementFormat')==='time'">
          <Field v-if="subStyle('elementFormat')==='date'" label="展示到" type="select" :model-value="at('elementFormat').precision||'day'" :options="[{value:'year',label:'年'},{value:'month',label:'月'},{value:'day',label:'日'}]" :disabled="disabled" @update:model-value="put('elementFormat','precision',$event)"/>
          <Field v-if="subStyle('elementFormat')==='datetime'||subStyle('elementFormat')==='time'" label="展示到" type="select" :model-value="at('elementFormat').precision||'second'" :options="[{value:'minute',label:'分钟'},{value:'second',label:'秒'}]" :disabled="disabled" @update:model-value="put('elementFormat','precision',$event)"/>
          <Field label="显示时区" :model-value="at('elementFormat').timezone??'Asia/Shanghai'" example="Asia/Shanghai、UTC" :disabled="disabled" @update:model-value="put('elementFormat','timezone',$event)"/>
          <Field v-if="subStyle('elementFormat')==='custom'" label="时间模板" :model-value="at('elementFormat').pattern||''" example="YYYY-MM-DD HH:mm" :disabled="disabled" @update:model-value="put('elementFormat','pattern',$event)"/>
        </template>
      </template>
    </details>
  </template>

  <!-- 结构体：字段列表（可排序）/ 模板 -->
  <template v-if="kind==='struct'">
    <template v-if="style==='pairs'">
      <p class="muted">按以下顺序展示字段；未配置时按输入字段顺序展示。</p>
      <div v-for="(f,i) in fieldRows()" :key="i" class="map-row">
        <input :value="String(f)" :disabled="disabled" :aria-label="`字段 ${i+1}`" placeholder="字段名" @input="setFieldAt(i,($event.target as HTMLInputElement).value)">
        <button type="button" class="mini" :disabled="disabled||i===0" :aria-label="`上移字段 ${i+1}`" @click="moveField(i,-1)">上移</button>
        <button type="button" class="mini" :disabled="disabled||i===fieldRows().length-1" :aria-label="`下移字段 ${i+1}`" @click="moveField(i,1)">下移</button>
        <button type="button" class="mini" :disabled="disabled" :aria-label="`移除字段 ${i+1}`" @click="removeField(i)">移除</button>
      </div>
      <button type="button" class="mini" :disabled="disabled" @click="addField">添加字段</button>
      <Field label="分隔符" :model-value="local?.separator??'；'" :disabled="disabled" @update:model-value="put('','separator',$event)"/>
    </template>
    <Field v-if="style==='template'" label="显示模板" :model-value="local?.template||''" example="{manufacturer} / {model}" help="以 {字段名} 引用顶层字段；缺失字段按空值显示，不执行表达式。" :disabled="disabled" @update:model-value="put('','template',$event)"/>
  </template>

  <!-- 时间序列：时间格式 + 观测值格式分别复用对应常用格式子配置 -->
  <template v-if="kind==='timeSeries'">
    <div class="sub-block">
      <h4>时间格式</h4>
      <Field label="显示样式" type="select" :model-value="subStyle('timeFormat')" :options="styleOptions('time')" :disabled="disabled" @update:model-value="put('timeFormat','style',$event)"/>
      <Field v-if="subStyle('timeFormat')==='date'" label="展示到" type="select" :model-value="at('timeFormat').precision||'day'" :options="[{value:'year',label:'年'},{value:'month',label:'月'},{value:'day',label:'日'}]" :disabled="disabled" @update:model-value="put('timeFormat','precision',$event)"/>
      <Field v-if="subStyle('timeFormat')==='datetime'||subStyle('timeFormat')==='time'" label="展示到" type="select" :model-value="at('timeFormat').precision||'second'" :options="[{value:'minute',label:'分钟（14:30）'},{value:'second',label:'秒（14:30:25）'}]" :disabled="disabled" @update:model-value="put('timeFormat','precision',$event)"/>
      <Field label="显示时区" :model-value="at('timeFormat').timezone??'Asia/Shanghai'" example="Asia/Shanghai、UTC" :disabled="disabled" @update:model-value="put('timeFormat','timezone',$event)"/>
      <Field v-if="subStyle('timeFormat')==='custom'" label="时间模板" :model-value="at('timeFormat').pattern||''" example="YYYY-MM-DD HH:mm:ss" help="支持 YYYY、MM、DD、HH、mm、ss。" :disabled="disabled" @update:model-value="put('timeFormat','pattern',$event)"/>
    </div>
    <div class="sub-block">
      <h4>观测值格式（{{ valueKindLabel }}）</h4>
      <Field label="显示样式" type="select" :model-value="subStyle('valueFormat')" :options="styleOptions(subKind('valueFormat'))" :disabled="disabled" @update:model-value="put('valueFormat','style',$event)"/>
      <template v-if="subKind('valueFormat')==='string'">
        <Field v-if="subStyle('valueFormat')==='template'" label="显示模板" :model-value="at('valueFormat').template||''" example="ESS-{value}" :disabled="disabled" @update:model-value="put('valueFormat','template',$event)"/>
        <div v-if="subStyle('valueFormat')==='mapping'">
          <div v-for="(row,i) in mappingRowsOf('valueFormat')" :key="i" class="map-row">
            <input :value="String(row.from??'')" :disabled="disabled" :aria-label="`原始值 ${i+1}`" placeholder="原始值" @input="setMappingAt('valueFormat',i,'from',($event.target as HTMLInputElement).value)">
            <span aria-hidden="true">→</span>
            <input :value="String(row.to??'')" :disabled="disabled" :aria-label="`显示文字 ${i+1}`" placeholder="显示文字" @input="setMappingAt('valueFormat',i,'to',($event.target as HTMLInputElement).value)">
            <button type="button" class="mini" :disabled="disabled" @click="removeMappingAt('valueFormat',i)">移除</button>
          </div>
          <button type="button" class="mini" :disabled="disabled" @click="addMappingRow('valueFormat')">添加映射行</button>
          <p v-if="mappingDup('valueFormat')" class="inline-error" role="alert">{{ mappingDup('valueFormat') }}</p>
        </div>
        <div v-if="subStyle('valueFormat')==='standard'" class="form-grid">
          <Field label="显示前缀" :model-value="at('valueFormat').prefix||''" :disabled="disabled" @update:model-value="put('valueFormat','prefix',$event)"/>
          <Field label="显示后缀" :model-value="at('valueFormat').suffix||''" :disabled="disabled" @update:model-value="put('valueFormat','suffix',$event)"/>
        </div>
      </template>
      <div v-if="subKind('valueFormat')==='number'" class="form-grid">
        <Field label="小数位数" type="number" :min="0" :max="20" :model-value="at('valueFormat').decimals??''" :disabled="disabled" @update:model-value="putDecimals('valueFormat',$event)"/>
        <Field v-if="subStyle('valueFormat')!=='scientific'" label="千位分隔" type="select" :model-value="at('valueFormat').grouping===false?'no':'yes'" :options="[{value:'yes',label:'使用'},{value:'no',label:'不使用'}]" :disabled="disabled" @update:model-value="put('valueFormat','grouping',$event==='yes')"/>
        <Field v-if="subStyle('valueFormat')==='percent'" label="百分比原值口径" type="select" :model-value="at('valueFormat').percentInput||''" :options="[{value:'ratio',label:'0～1（0.7 → 70%）'},{value:'hundred',label:'0～100（70 → 70%）'}]" :disabled="disabled" @update:model-value="put('valueFormat','percentInput',$event)"/>
        <Field v-if="subStyle('valueFormat')==='currency'" label="币种" :model-value="at('valueFormat').currency??'CNY'" example="CNY、USD、EUR" :disabled="disabled" @update:model-value="put('valueFormat','currency',$event)"/>
        <Field label="显示前缀" :model-value="at('valueFormat').prefix||''" :disabled="disabled" @update:model-value="put('valueFormat','prefix',$event)"/>
        <Field label="显示后缀" :model-value="at('valueFormat').suffix||''" :disabled="disabled" @update:model-value="put('valueFormat','suffix',$event)"/>
      </div>
      <div v-if="subKind('valueFormat')==='boolean' && subStyle('valueFormat')==='custom'" class="form-grid">
        <Field label="是（true）显示为" :model-value="at('valueFormat').trueText??'是'" :disabled="disabled" @update:model-value="put('valueFormat','trueText',$event)"/>
        <Field label="否（false）显示为" :model-value="at('valueFormat').falseText??'否'" :disabled="disabled" @update:model-value="put('valueFormat','falseText',$event)"/>
      </div>
      <template v-if="subKind('valueFormat')==='date'">
        <Field v-if="subStyle('valueFormat')==='date'" label="展示到" type="select" :model-value="at('valueFormat').precision||'day'" :options="[{value:'year',label:'年'},{value:'month',label:'月'},{value:'day',label:'日'}]" :disabled="disabled" @update:model-value="put('valueFormat','precision',$event)"/>
        <Field v-if="subStyle('valueFormat')==='custom'" label="格式模板" :model-value="at('valueFormat').pattern||''" example="YYYY年MM月DD日" help="仅支持 YYYY、MM、DD。" :disabled="disabled" @update:model-value="put('valueFormat','pattern',$event)"/>
      </template>
      <template v-if="subKind('valueFormat')==='time'">
        <Field v-if="subStyle('valueFormat')==='date'" label="展示到" type="select" :model-value="at('valueFormat').precision||'day'" :options="[{value:'year',label:'年'},{value:'month',label:'月'},{value:'day',label:'日'}]" :disabled="disabled" @update:model-value="put('valueFormat','precision',$event)"/>
        <Field v-if="subStyle('valueFormat')==='datetime'||subStyle('valueFormat')==='time'" label="展示到" type="select" :model-value="at('valueFormat').precision||'second'" :options="[{value:'minute',label:'分钟'},{value:'second',label:'秒'}]" :disabled="disabled" @update:model-value="put('valueFormat','precision',$event)"/>
        <Field label="显示时区" :model-value="at('valueFormat').timezone??'Asia/Shanghai'" example="Asia/Shanghai、UTC" :disabled="disabled" @update:model-value="put('valueFormat','timezone',$event)"/>
        <Field v-if="subStyle('valueFormat')==='custom'" label="时间模板" :model-value="at('valueFormat').pattern||''" example="YYYY-MM-DD HH:mm" :disabled="disabled" @update:model-value="put('valueFormat','pattern',$event)"/>
      </template>
    </div>
  </template>
</template>

<!-- 自然语言（第 4 节）：规则必填后才可预览 -->
<template v-if="!isLegacyCode && displayMode==='natural'">
  <Field label="自然语言格式规则" type="textarea" required :model-value="local?.instruction||''" :example="naturalExample" help="试运行会将此规则和样本值发送到已配置的大模型；未配置接口时可先保存规则。空值（null）直接显示空值文字，不发起模型请求。" :disabled="disabled" @update:model-value="put('','instruction',$event)"/>
</template>

<Field v-if="!isLegacyCode" label="空值显示为" :model-value="local?.emptyText??'—'" help="仅空值（null／缺失）显示此文字；0、false、空字符串按原值格式化。" :disabled="disabled" @update:model-value="put('','emptyText',$event)"/>

<div v-if="canRestore" class="restore-row">
  <button type="button" :disabled="disabled" @click="restore">恢复原样显示</button>
  <small class="muted">清除显示格式配置；随属性保存生效，无配置时直接显示原值。</small>
</div>

<Field label="输入样本" type="textarea" v-model="sample" :example="sampleDefault(kind)" :help="sampleHelp" :disabled="disabled"/>
<label class="check-option"><input type="checkbox" :checked="sampleNull" :disabled="disabled" @change="toggleNull"><span>样本为空值（null）</span></label>
<button type="button" :disabled="busy||disabled" @click="preview">{{ busy?'格式化中…':'预览格式化结果' }}</button>
<p v-if="error" class="inline-error" role="alert">{{ error }}</p>
<pre v-if="result!==''" class="format-result" aria-label="格式化结果">{{ result }}</pre>
</section></template>
<style scoped>
.format-result{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--paper-2);padding:12px;border-radius:8px;margin:12px 0 0}
.legacy-box{border:1px solid var(--warn-line);background:var(--warn-soft);border-radius:8px;padding:12px 14px;margin:14px 0}
.legacy-box p{margin:0 0 6px}
.legacy-box small{display:block;margin-top:8px;color:var(--muted)}
.legacy-code{margin:8px 0;background:var(--paper-2);padding:10px;border-radius:6px;max-height:220px;overflow:auto}
.legacy-note{margin:6px 0 0}
.map-row{display:flex;gap:8px;align-items:center;margin:8px 0}
.map-row input{flex:1;min-width:0;margin-top:0}
.map-row span{color:var(--muted);flex:0 0 auto}
.map-row button{flex:0 0 auto}
.element-section{border-top:1px dashed var(--line);margin-top:16px;padding-top:10px}
.element-section summary{cursor:pointer;font-size:13px;color:var(--muted)}
.sub-block{border:1px solid var(--line);border-radius:8px;padding:2px 14px 12px;margin:14px 0}
.sub-block h4{font-size:13px;color:var(--muted);margin:12px 0 0}
.restore-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:16px}
.restore-row button{margin:0}
</style>

<!-- 导入本体内容（20260917 需求 §2，交互按 交互原型_v1.html 的入口/层级/文案/状态）：
     单面板完成 上传 → 同名策略 → 检查 → 预览 → 确认；不做三步向导。
     保存经 inject('form-save') 一次 mutate 合入、一次提交；检查/取消零写入。
     预览绑定本体 ID＋草稿指纹：草稿变化即失效，不能拿旧清单提交。
     原型的演示按钮（正常/有问题/版本变化等）不进正式页面。 -->
<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import AppError from '../shared/AppError.vue'
import { appConfirm } from '../shared/appConfirm'
import { checkFileMeta, parseWorkbook, MAX_FILE_BYTES, MAX_BUSINESS_ROWS, CHUNK_LOAD_HINT, type ParseIssue } from './excelImport'
import { applyPlan, buildPlan, collectExistingNames, planCounts, verifyImported, type ImportPolicy, type PlanOutcome } from './importPlan'
import type { FormGuardAPI, FormSaveAPI } from '../app/formGuard'

const props = defineProps<{ state: any }>()
const emit = defineEmits<{ close: []; navigate: [view: string] }>()
const formSave = inject<FormSaveAPI>('form-save')!
const guardApi = inject<FormGuardAPI>('form-guard')!

const ontologyId = computed(() => String(props.state?.workspaceId || ''))
// 本体名在 workflow.objective.name（与顶栏面包屑同源）
const ontologyName = computed(() => String(props.state?.workflow?.objective?.name || '当前本体'))

// ── 面板状态：file / policy / checking / plan / result ──
const fileInput = ref<HTMLInputElement | null>(null)
const fileName = ref(''), fileSize = ref(0), fileData = ref<ArrayBuffer | null>(null)
const policy = ref<ImportPolicy>('skip')
const checking = ref(false), saving = ref(false)
const fileError = ref('')
const plan = ref<PlanOutcome | null>(null)
const planFilter = ref<'all' | 'create' | 'skip' | 'rename' | 'error'>('all')
const done = ref(false), doneSummary = ref('')
const panelError = ref('')
// 预览绑定：本体 ID + 草稿指纹（结构摘要；草稿变化即失效）
const planOntologyId = ref(''), planFingerprint = ref('')
// 结果不明核对态
const outcomeUnknown = ref(false), verifyNote = ref('')
const currentFingerprint = () => JSON.stringify(props.state?.ontology?.['@graph'] || []) + '#' + JSON.stringify(props.state?.workflow?.businessRules || []) + '#' + JSON.stringify(props.state?.workflow?.actions || '')
const previewStale = computed(() => !plan.value || planOntologyId.value !== ontologyId.value || (!done.value && planFingerprint.value !== currentFingerprint()))

const dragOver = ref(false)
function pickFile() { if (!busy.value) fileInput.value?.click() }
function onFileChange(e: Event) { const f = (e.target as HTMLInputElement).files?.[0]; if (f) takeFile(f) }
function onDrop(e: DragEvent) { dragOver.value = false; const f = e.dataTransfer?.files?.[0]; if (f) takeFile(f) }
function takeFile(f: File) {
  resetPreview()
  const meta = checkFileMeta(f.name, f.size)
  if (!meta.ok) { fileName.value = ''; fileData.value = null; fileError.value = meta.error || '文件不符合要求。'; return }
  fileError.value = ''
  const reader = new FileReader()
  reader.onload = () => { fileData.value = reader.result as ArrayBuffer }
  reader.onerror = () => { fileError.value = '读取文件失败，请重试。' }
  reader.readAsArrayBuffer(f)
  fileName.value = f.name; fileSize.value = f.size
}
function switchPolicy(v: ImportPolicy) { if (policy.value === v) return; policy.value = v; resetPreview() }
// 移除已选文件（保留同名策略选择，只清文件与预览）
function clearFile() {
  if (busy.value) return
  fileName.value = ''; fileSize.value = 0; fileData.value = null; fileError.value = ''; dragOver.value = false
  if (fileInput.value) fileInput.value.value = ''
  resetPreview('选择文件后检查。')
}
function resetPreview(hint = '内容已变化，请重新检查。') {
  plan.value = null; panelError.value = ''; outcomeUnknown.value = false; verifyNote.value = ''
  checkHint.value = hint
}
const checkHint = ref('选择文件后检查。')
const busy = computed(() => checking.value || saving.value)
const counts = computed(() => plan.value ? planCounts(plan.value.decisions) : { create: 0, skip: 0, rename: 0, error: 0, importable: 0 })
const canConfirm = computed(() => !!plan.value && !previewStale.value && !plan.value.blocked && counts.value.importable > 0 && !done.value)
const confirmLabel = computed(() => saving.value ? '正在导入…' : '导入 ' + counts.value.importable + ' 项')

async function runCheck() {
  if (!fileData.value || busy.value) return
  checking.value = true; panelError.value = ''; outcomeUnknown.value = false; verifyNote.value = ''
  await new Promise(r => setTimeout(r, 30)) // 让「正在检查…」先渲染
  try {
    const { result, error } = await parseWorkbook(fileData.value)
    if (error || !result) { panelError.value = error || '无法读取文件。'; plan.value = null; return }
    const existing = collectExistingNames(props.state)
    const outcome = buildPlan(result.rows, {
      policy: policy.value, existing,
      random: () => Math.random(),
      newObjectId: () => 'mg:object_' + crypto.randomUUID().replaceAll('-', ''),
      newPropertyId: () => 'mg:p_' + crypto.randomUUID().replaceAll('-', ''),
      newRuleId: () => 'rule_' + crypto.randomUUID().replaceAll('-', ''),
      newActionId: () => 'action_' + crypto.randomUUID().replaceAll('-', ''),
    })
    outcome.issues.push(...result.issues)
    plan.value = outcome
    planOntologyId.value = ontologyId.value
    planFingerprint.value = currentFingerprint()
    planFilter.value = 'all'
    if (outcome.emptyFile) checkHint.value = '已检查：模板没有填写内容。'
    else if (outcome.allSkipped) checkHint.value = '已检查：没有可导入的新内容，全部同名项已跳过。'
    else if (outcome.blocked) checkHint.value = '已检查：有填写问题，修改 Excel 后重新上传检查；本次不会导入任何内容。'
    else checkHint.value = '已检查，请确认下方处理结果。'
  } catch (e: any) {
    // 分块加载失败（旧页面持有已失效的分块名）与其他异常都要有可读反馈，不能只留控制台报错
    plan.value = null
    const msg = String(e?.message || e || '')
    panelError.value = msg.includes(CHUNK_LOAD_HINT) || /dynamically imported module|importModule|before initialization/i.test(msg)
      ? CHUNK_LOAD_HINT
      : (msg || '检查失败，请重试。')
  } finally { checking.value = false }
}

// 表单守卫：确认中途离开需确认（检查/预览本身不写数据，dirty 仅在 saving 期间为真）
const guard = { isDirty: () => saving.value, discard: () => {} }
watch(() => saving.value, open => { if (open) guardApi.register(guard); else guardApi.unregister(guard) }, { immediate: true })
onBeforeUnmount(() => guardApi.unregister(guard))

async function confirmImport() {
  if (!canConfirm.value || busy.value) return
  if (previewStale.value) { panelError.value = '当前本体草稿已更新，原预览已失效。请重新检查。'; return }
  if (!(await appConfirm({ message: '确认把 ' + counts.value.importable + ' 项新增内容写入「' + ontologyName.value + '」当前草稿？已保存一次完成，可撤销。' }))) return
  const snapshot = plan.value!
  saving.value = true; panelError.value = ''
  try {
    // mutate 前最后核对：预览仍绑定当前本体与草稿
    if (planOntologyId.value !== ontologyId.value) { panelError.value = '已切换本体，请重新检查后再导入。'; return }
    if (planFingerprint.value !== currentFingerprint()) { panelError.value = '当前本体草稿已更新，原预览已失效。请重新检查。'; return }
    const r = await formSave.submitForm('ontology', () => applyPlan(props.state, snapshot.decisions))
    if (!r.ok) {
      if (r.message.includes('版本冲突')) {
        panelError.value = '草稿版本冲突：服务端已有更新的草稿。请关闭面板重新打开并重新检查，不能覆盖较新内容。'
        plan.value = null
      } else panelError.value = r.message || '保存失败，已回滚；本次导入未写入。'
      return
    }
    const c = planCounts(snapshot.decisions)
    doneSummary.value = ['对象', '属性', '规则', '动作'].map(s => {
      const label = s === '属性' ? '共享属性' : s === '规则' ? '业务规则' : s === '动作' ? '动作' : '对象'
      const n = snapshot.decisions.filter(d => d.sheet === s && (d.disposition === 'create' || d.disposition === 'rename')).length
      return label + '新增 ' + n + ' 项'
    }).join('，') + '；跳过 ' + c.skip + ' 项。'
    done.value = true; plan.value = null
  } catch (e: any) {
    // 网络断开可能发生在服务端已保存之后：结果不明，用固定 ID 核对
    if (e?.failure === 'network' || e?.failure === 'timeout') {
      outcomeUnknown.value = true
      panelError.value = '未收到导入结果，暂不能确认是否成功。请点击「核对导入结果」，不要重复导入。'
    } else panelError.value = (e as Error)?.message || '保存失败，已回滚；本次导入未写入。'
  } finally { saving.value = false }
}

// 结果不明：用预览固定 ID 只读核对最新草稿（App 的 working 即最新已保存内容）
async function verifyOutcome() {
  if (!outcomeUnknown.value || busy.value) return
  checking.value = true
  try {
    const snapshot = plan.value
    if (!snapshot) return
    const outcome = verifyImported(props.state, snapshot.decisions)
    if (outcome === 'all-present') {
      const c = planCounts(snapshot.decisions)
      doneSummary.value = ['对象', '属性', '规则', '动作'].map(s => {
        const label = s === '属性' ? '共享属性' : s === '规则' ? '业务规则' : s === '动作' ? '动作' : '对象'
        const n = snapshot.decisions.filter(d => d.sheet === s && (d.disposition === 'create' || d.disposition === 'rename')).length
        return label + '新增 ' + n + ' 项'
      }).join('，') + '；跳过 ' + c.skip + ' 项。'
      done.value = true; plan.value = null; outcomeUnknown.value = false
      verifyNote.value = ''
    } else if (outcome === 'none-present') {
      outcomeUnknown.value = false
      verifyNote.value = '服务端没有本次内容，导入很可能未写入。请重新检查后再提交。'
      plan.value = null
    } else {
      verifyNote.value = '部分内容存在或已被修改：请到对象建模／共享属性／业务规则／动作定义人工核对，不要再次导入本文件。'
    }
  } finally { checking.value = false }
}

const filterTabs = [
  { key: 'all', label: '全部' }, { key: 'create', label: '新增' }, { key: 'skip', label: '跳过' },
  { key: 'rename', label: '自动重命名' }, { key: 'error', label: '问题' },
] as const
const dispositionLabel: Record<string, string> = { create: '新增', skip: '跳过', rename: '自动重命名', error: '填写问题' }
const visibleRows = computed(() => {
  if (!plan.value) return []
  return plan.value.decisions.filter(d => planFilter.value === 'all' || d.disposition === planFilter.value)
})
const rowIssues = computed<ParseIssue[]>(() => plan.value ? plan.value.issues : [])
// 结构/文件级问题（无对应 decision 行的）：decisions 的 error 行已显示原因，不重复渲染
const extraIssues = computed<ParseIssue[]>(() => {
  if (!plan.value) return []
  const covered = new Set(plan.value.decisions.map(d => d.sheet + '#' + d.row))
  return plan.value.issues.filter(i => i.row === 0 || !covered.has(i.sheet + '#' + i.row))
})

function templateHref() { return (import.meta as any).env?.BASE_URL ? (import.meta as any).env.BASE_URL + 'templates/ontology-import-rule-action-v1.xlsx' : '/templates/ontology-import-rule-action-v1.xlsx' }
function formatSize(n: number) { return n < 1024 * 1024 ? (n / 1024).toFixed(1) + ' KB' : (n / 1024 / 1024).toFixed(2) + ' MB' }

async function requestClose() {
  if (saving.value) return
  if (saving.value || (plan.value && !done.value && counts.value.importable > 0)) {
    if (!(await appConfirm({ message: '关闭将放弃本次检查结果（不会改动已保存的草稿）。继续关闭？' }))) return
  }
  emit('close')
}
function onKeydown(e: KeyboardEvent) { if (e.key === 'Escape') { e.preventDefault(); void requestClose() } }
void nextTick
</script>

<template>
<div class="modal-backdrop imp-backdrop" @click.self="requestClose" @keydown.esc="onKeydown">
  <section class="modal-card imp-modal" role="dialog" aria-modal="true" aria-label="导入本体内容">
    <header class="imp-head">
      <div><h2>导入本体内容</h2><p class="muted">导入到：<strong>{{ ontologyName }} · 当前草稿</strong></p></div>
      <button type="button" aria-label="关闭" :disabled="saving" @click="requestClose">×</button>
    </header>

    <div class="imp-body">
      <!-- 结果页 -->
      <section v-if="done" class="imp-success">
        <h3>导入完成</h3>
        <p>{{ doneSummary }}</p>
        <p class="muted">已保存到当前本体草稿。可在对象建模中关联共享属性、规则和动作。</p>
        <div class="tools imp-golinks">
          <button type="button" @click="emit('navigate', 'objects')">查看对象</button>
          <button type="button" @click="emit('navigate', 'library')">查看共享属性</button>
          <button type="button" @click="emit('navigate', 'rules')">查看规则</button>
          <button type="button" @click="emit('navigate', 'actions')">查看动作</button>
        </div>
      </section>

      <!-- 编辑页 -->
      <template v-else>
        <!-- 文件选择：隐藏原生 file 控件（全局 input 样式会把它渲染成宽输入框，很难看），
             用「选择文件」按钮 + 拖拽区承载；选中后显示文件名/大小/移除。 -->
        <div class="imp-drop" :class="{ drag: dragOver, filled: !!fileName }"
             @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="onDrop">
          <input ref="fileInput" type="file" accept=".xlsx" hidden @change="onFileChange">
          <template v-if="!fileName">
            <span class="imp-drop-ico" aria-hidden="true">⤒</span>
            <strong>选择填写好的 Excel</strong>
            <p class="note">把文件拖到这里，或</p>
            <button type="button" class="primary" :disabled="busy" @click="pickFile">选择文件</button>
            <p class="note imp-limits">仅支持 .xlsx，最大 {{ Math.round(MAX_FILE_BYTES / 1024) }} KB，四个表合计最多 {{ MAX_BUSINESS_ROWS }} 条内容</p>
          </template>
          <div v-else class="imp-file">
            <span class="imp-file-ico" aria-hidden="true">▤</span>
            <span class="imp-file-main">
              <strong>{{ fileName }}</strong>
              <small class="note">{{ formatSize(fileSize) }}<template v-if="checking"> · 正在检查…</template></small>
            </span>
            <span class="imp-file-ops">
              <button type="button" :disabled="busy" @click="pickFile">更换</button>
              <button type="button" :disabled="busy" @click="clearFile">移除</button>
            </span>
          </div>
        </div>
        <p v-if="fileError" class="inline-error" role="alert">{{ fileError }}</p>
        <p class="note imp-tplrow">还没有模板？<a class="imp-dl" :href="templateHref()" download="本体模型填写模板.xlsx">下载空白模板</a></p>

        <h3 class="imp-section">遇到同名内容时</h3>
        <div class="imp-policies">
          <label class="imp-policy" :class="{ selected: policy === 'skip' }">
            <input type="radio" name="imp-policy" value="skip" :checked="policy === 'skip'" :disabled="busy" @change="switchPolicy('skip')">
            <span><strong>跳过同名内容</strong><span class="note">保留已有定义，不导入同类重复项。</span></span>
          </label>
          <label class="imp-policy" :class="{ selected: policy === 'rename' }">
            <input type="radio" name="imp-policy" value="rename" :checked="policy === 'rename'" :disabled="busy" @change="switchPolicy('rename')">
            <span><strong>保留并自动重命名</strong><span class="note">重复项仍导入，名称末尾自动添加随机数字。</span></span>
          </label>
        </div>
        <p class="note imp-hintnote">只比较当前本体内同一类别的名称，文件内部重复也按此策略处理。</p>

        <div class="tools imp-checkrow">
          <button type="button" class="primary" :disabled="!fileData || busy" @click="runCheck">{{ checking ? '正在检查…' : '检查导入内容' }}</button>
          <span class="note">{{ previewStale && plan ? '内容已变化，请重新检查。' : checkHint }}</span>
        </div>

        <AppError v-if="panelError" compact :title="outcomeUnknown ? '导入结果待确认' : '导入未完成'" :reason="panelError" :hint="outcomeUnknown ? '网络中断可能发生在保存已完成之后。请先核对，不要重复导入。' : ''" :retry-label="outcomeUnknown ? '核对导入结果' : undefined" @retry="verifyOutcome"/>

        <!-- 预览 -->
        <section v-if="plan && !previewStale">
          <div class="imp-summary">
            <span><strong>{{ counts.create }}</strong>新增</span>
            <span><strong>{{ counts.skip }}</strong>跳过</span>
            <span><strong>{{ counts.rename }}</strong>自动重命名</span>
            <span><strong>{{ counts.error }}</strong>填写问题</span>
          </div>
          <div v-if="counts.error" class="inline-error" role="alert">有 {{ counts.error }} 项填写问题。请回 Excel 修改并重新上传，本次不会导入任何内容。</div>
          <div v-else-if="!counts.importable" class="inline-warning">{{ plan.emptyFile ? '模板没有填写内容。' : '没有可导入的新内容：全部同名项已跳过。' }}</div>
          <div class="imp-tabs" role="tablist" aria-label="预览筛选">
            <button v-for="t in filterTabs" :key="t.key" type="button" role="tab" :class="{ active: planFilter === t.key }" :aria-selected="planFilter === t.key" @click="planFilter = t.key">{{ t.label }}</button>
          </div>
          <div class="imp-tablewrap">
            <table>
              <thead><tr><th>工作表 / 行</th><th>原名称</th><th>导入后名称</th><th>处理结果</th><th>说明</th></tr></thead>
              <tbody>
                <tr v-for="d in visibleRows" :key="d.sheet + d.row">
                  <td>{{ d.sheet }} · 第 {{ d.row }} 行</td>
                  <td>{{ d.originalName }}</td>
                  <td>{{ d.finalName || '—' }}</td>
                  <td><span class="imp-badge" :class="d.disposition">{{ dispositionLabel[d.disposition] }}</span></td>
                  <td class="imp-reason">{{ d.reason }}</td>
                </tr>
                <tr v-for="(i, idx) in (planFilter === 'all' || planFilter === 'error' ? extraIssues : [])" :key="'i' + idx">
                  <td>{{ i.sheet }}<template v-if="i.row"> · 第 {{ i.row }} 行</template></td>
                  <td>—</td><td>—</td>
                  <td><span class="imp-badge error">填写问题</span></td>
                  <td class="imp-reason">{{ i.message }}</td>
                </tr>
                <tr v-if="!visibleRows.length && !rowIssues.length"><td colspan="5" class="muted">此分类没有内容</td></tr>
              </tbody>
            </table>
          </div>
        </section>
        <p class="note imp-hintnote">属性将进入共享属性库。导入后，可在对象建模中关联属性、规则和动作。</p>
      </template>
    </div>

    <footer class="imp-foot">
      <span class="note">检查和取消不会写入数据；确认导入后一次保存。</span>
      <div class="tools">
        <template v-if="!done">
          <button type="button" :disabled="busy" @click="requestClose">取消</button>
          <button type="button" class="primary" :disabled="!canConfirm || busy" @click="confirmImport">{{ confirmLabel }}</button>
        </template>
        <button v-else type="button" class="primary" @click="emit('close')">完成</button>
      </div>
    </footer>
  </section>
</div>
</template>

<style scoped>
/* ── 导入弹窗（20260918 视觉重做）──────────────────────────────────────────────
   关键修复：全局 `input,select,textarea{display:block;width:100%;...}` 会波及本组件内的
   file / radio 控件——原生「选择文件」被渲染成整行输入框、radio 被拉宽顶开文字，
   这正是原来"很丑"的根因。此处把这两类控件的外观归还给浏览器（重置 display/width/margin/
   padding/border），其余沿用工作台既有令牌与卡片语言。 */
.imp-backdrop{z-index:120}
.imp-modal{width:min(920px,96vw);max-height:94vh;padding:0;display:flex;flex-direction:column}
.imp-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;padding:20px 26px;border-bottom:1px solid var(--line)}
.imp-head h2{margin:0 0 4px;font-size:19px}
.imp-head p{margin:0;font-size:13px}
.imp-head button{border:0;font-size:22px;padding:0 8px;color:var(--muted);background:none;line-height:1}
.imp-head button:hover{color:var(--ink);background:var(--paper-2);border-radius:var(--r-sm)}
.imp-body{padding:22px 26px;overflow:auto}

/* 文件选择区：未选=虚线投放区；已选=实心文件卡（不出现原生 file 控件） */
.imp-drop{border:1px dashed var(--line-2);background:var(--bg);border-radius:var(--r-md);padding:26px 20px;text-align:center;transition:background .12s,border-color .12s}
.imp-drop.drag{background:var(--blue-soft);border-color:var(--blue)}
.imp-drop.filled{padding:14px 16px;background:var(--paper);border-style:solid;border-color:var(--line);text-align:left}
.imp-drop strong{display:block;font-size:15px;margin-bottom:4px}
.imp-drop-ico{display:inline-flex;align-items:center;justify-content:center;width:38px;height:38px;border-radius:50%;background:var(--paper);border:1px solid var(--line);color:var(--blue);font-size:18px;margin-bottom:10px}
.imp-drop .note{margin:0}
.imp-drop .imp-limits{margin-top:12px;font-size:12px;color:var(--faint)}
.imp-drop .primary{margin-top:10px}
.imp-file{display:flex;align-items:center;gap:12px}
.imp-file-ico{flex:none;width:34px;height:34px;border-radius:var(--r-sm);background:var(--blue-soft);color:var(--blue-ink);display:inline-flex;align-items:center;justify-content:center;font-size:15px}
.imp-file-main{flex:1;min-width:0}
.imp-file-main strong{display:block;font-size:14px;margin:0;overflow-wrap:anywhere}
.imp-file-main .note{font-size:12px}
.imp-file-ops{flex:none;display:flex;gap:8px}
.imp-file-ops button{height:30px;padding:0 12px;font-size:12px}

/* 原生控件外观归还（覆盖全局 input 规则；仅本弹窗内） */
.imp-drop input[type=file]{display:none}
.imp-policy input[type=radio]{display:inline-block;width:auto;min-width:0;height:auto;margin:3px 0 0;padding:0;border:0;border-radius:0;background:none;accent-color:var(--blue);flex:none}

.imp-tplrow{margin:10px 0 0;font-size:12px}
.imp-dl{color:var(--blue);text-decoration:underline;text-underline-offset:3px;white-space:nowrap;padding:2px 4px;border-radius:4px}
.imp-dl:hover{background:var(--blue-soft);text-decoration:none}
.imp-section{font-size:14px;margin:22px 0 10px}
.imp-policies{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.imp-policy{display:flex;align-items:flex-start;gap:10px;padding:14px 16px;border:1px solid var(--line);border-radius:var(--r-md);cursor:pointer;background:var(--paper);margin:0}
.imp-policy:hover{border-color:var(--blue-line)}
.imp-policy.selected{border-color:var(--blue);background:var(--blue-soft)}
.imp-policy>span{min-width:0}
.imp-policy strong{display:block;font-size:14px}
.imp-policy .note{display:block;margin-top:3px;font-size:12px;line-height:1.6}
.imp-hintnote{margin:9px 0 16px;font-size:12px}
.imp-checkrow{margin:0 0 14px;align-items:center}
.imp-checkrow button{white-space:nowrap}

/* 检查结果摘要：四个计数卡（替代裸数字行） */
.imp-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;padding:0 0 14px}
.imp-summary>span{display:flex;align-items:baseline;gap:6px;padding:10px 12px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--paper);font-size:12px;color:var(--muted)}
.imp-summary strong{font-size:20px;font-weight:650;color:var(--ink);font-variant-numeric:tabular-nums}
.imp-tabs{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}
.imp-tabs button{padding:5px 11px;font-size:12px;border-radius:var(--r-pill)}
.imp-tabs button.active{border-color:var(--blue-line);color:var(--blue-ink);background:var(--blue-soft);font-weight:600}
.imp-tablewrap{max-height:280px;overflow:auto;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--paper)}
.imp-tablewrap table{font-size:13px}
.imp-tablewrap th{position:sticky;top:0;white-space:nowrap;z-index:1}
.imp-tablewrap td{vertical-align:top}
.imp-reason{overflow-wrap:anywhere;font-size:12px;color:var(--muted)}
.imp-badge{white-space:nowrap;font-size:12px;border-radius:4px;padding:3px 7px;display:inline-block;background:var(--paper-2);color:var(--muted)}
.imp-badge.create{background:var(--ok-soft);color:var(--ok);border:1px solid var(--ok-line)}
.imp-badge.rename{background:var(--warn-soft);color:var(--warn);border:1px solid var(--warn-line)}
.imp-badge.error{background:var(--danger-soft);color:var(--danger);border:1px solid var(--danger-line)}
.imp-success{padding:20px;background:var(--ok-soft);border:1px solid var(--ok-line);border-radius:var(--r-md)}
.imp-success h3{margin:0 0 8px;font-size:18px;color:var(--ok)}
.imp-success p{margin:0 0 6px}
.imp-golinks{margin-top:16px}
.imp-foot{padding:14px 26px;border-top:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:14px;background:var(--paper-2)}
.imp-foot .note{font-size:12px}
@media(max-width:800px){
  .imp-modal{width:100vw;height:100dvh;max-height:100dvh;border-radius:0}
  .imp-policies{grid-template-columns:1fr}
  .imp-summary{grid-template-columns:repeat(2,minmax(0,1fr))}
  .imp-head,.imp-body,.imp-foot{padding:16px}
  .imp-file{flex-wrap:wrap}
  .imp-file-ops{width:100%;justify-content:flex-end}
}
</style>

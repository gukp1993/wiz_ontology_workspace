<!-- 配置迁移（20260919 需求 §3；交互按 交互原型_v1.html）：设置 → 数据管理 → 配置迁移。
     导出页签：选择本体/项目（可搜）→ 查看导出内容（冻结预览）→ 下载 ZIP。
     导入页签：上传 ZIP（分片）→ 预检命名预览（可改顶层名）→ 确认导入（requestId 幂等）
       → 结果页（新 ID 导航/待补清单/再次导入）。
     每次主动导入都新建资产；无覆盖/合并选项（需求红线）。检查与取消零写入。 -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import AppError from '../shared/AppError.vue'
import SearchField from '../shared/SearchField.vue'
import {
  conflictSuggestions, discard, exportDownload, exportPreview, importConfirm, importPreview,
  importResult, stageUpload,
  type ExportPreview, type ImportPreview, type ImportReceipt,
} from './transferApi'

const props = defineProps<{ preselect?: { ontologyId?: string; projectId?: string } }>()
const emit = defineEmits<{ navigate: [view: string]; openOntology: [id: string]; openProject: [id: string] }>()

const tab = ref<'export' | 'import'>('export')
const busy = ref(false)
const message = ref('')
const messageError = ref(false)

// ── 导出状态 ──
const ontologies = ref<{ id: string; name: string }[]>([])
const projects = ref<{ id: string; name: string }[]>([])
const listsLoaded = ref(false)
const listsError = ref('')
const searchText = ref('')
const selectedModels = ref<string[]>([])
const selectedProjects = ref<string[]>([])
const extraFlows = ref<{ id: string; name: string }[]>([])
const selectedExtraFlows = ref<string[]>([])
const showExtra = ref(false)
const exportPreviewData = ref<ExportPreview | null>(null)
const downloading = ref(false)

// ── 导入状态 ──
const fileInput = ref<HTMLInputElement | null>(null)
const fileName = ref(''), fileSize = ref(0)
const uploading = ref(false), uploadDone = ref(0), uploadTotal = ref(0)
const uploadId = ref('')
const importPreviewData = ref<ImportPreview | null>(null)
const nameEdits = ref<Record<string, string>>({})
const requestId = ref('')
const submitting = ref(false)
const receipt = ref<ImportReceipt | null>(null)
const outcomeUnknown = ref(false)
const importError = ref('')
const importErrorCode = ref('')

const filteredOntologies = computed(() => {
  const q = searchText.value.trim().toLowerCase()
  return q ? ontologies.value.filter(o => o.name.toLowerCase().includes(q)) : ontologies.value
})
const filteredProjects = computed(() => {
  const q = searchText.value.trim().toLowerCase()
  return q ? projects.value.filter(o => o.name.toLowerCase().includes(q)) : projects.value
})
const canPreview = computed(() => selectedModels.value.length > 0 || selectedProjects.value.length > 0)
const importable = computed(() => !!importPreviewData.value && !importPreviewData.value.blockers.length)

async function loadLists(force = false) {
  if (listsLoaded.value && !force) return
  listsError.value = ''
  try {
    const { getJson } = await import('../app/http')
    const [ont, prj, flows] = await Promise.all([
      getJson('/api/ontologies'), getJson('/api/projects'), getJson('/api/flows'),
    ])
    ontologies.value = ont.items || []
    projects.value = prj.items || []
    extraFlows.value = (flows.items || []).map((f: any) => ({ id: f.id, name: f.name }))
    if (props.preselect?.ontologyId && ontologies.value.some(o => o.id === props.preselect!.ontologyId)) {
      selectedModels.value = [props.preselect.ontologyId]
      if (props.preselect.projectId && projects.value.some(p => p.id === props.preselect!.projectId)) {
        selectedProjects.value = [props.preselect.projectId]
      }
    }
    listsLoaded.value = true
  } catch (e) {
    listsError.value = (e as Error).message || '清单读取失败'
  }
}
void loadLists()

function toggle(list: string[], id: string) {
  const i = list.indexOf(id)
  if (i >= 0) list.splice(i, 1)
  else list.push(id)
}
function invalidateExportPreview() {
  exportPreviewData.value = null
  if (exportPreviewData.value) void discard({ exportToken: exportPreviewData.value.exportToken || undefined })
}

async function runExportPreview() {
  if (!canPreview.value || busy.value) return
  busy.value = true
  message.value = ''
  try {
    const r = await exportPreview(selectedModels.value, selectedProjects.value, selectedExtraFlows.value)
    exportPreviewData.value = r
  } catch (e) {
    message.value = (e as Error).message || '导出预览失败'
    messageError.value = true
  } finally { busy.value = false }
}

function exportFileName(): string {
  const stamp = (exportPreviewData.value?.snapshotAt || new Date().toISOString()).slice(0, 19).replace(/[:T-]/g, '')
  return `config-package-${stamp}.zip`
}

async function runDownload() {
  const preview = exportPreviewData.value
  if (!preview?.exportToken || downloading.value) return
  downloading.value = true
  message.value = ''
  try {
    await exportDownload(preview.exportToken, exportFileName())
    message.value = '配置包已生成并开始下载'
    messageError.value = false
  } catch (e) {
    message.value = (e as Error).message || '下载失败'
    messageError.value = true
  } finally { downloading.value = false }
}

// ── 导入 ──
function pickFile() { if (!busyImport.value) fileInput.value?.click() }
function onFileChange(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) void takeFile(f)
}
async function takeFile(f: File) {
  resetImport()
  if (!f.name.toLowerCase().endsWith('.zip')) {
    importError.value = '请选择本工作台导出的 .zip 配置包。'
    return
  }
  fileName.value = f.name
  fileSize.value = f.size
  uploading.value = true
  importError.value = ''
  try {
    uploadId.value = await stageUpload(f, (done, total) => { uploadDone.value = done; uploadTotal.value = total })
    uploading.value = false
    await runImportPreview()
  } catch (e) {
    uploading.value = false
    importError.value = (e as Error).message || '上传失败'
  }
}
function resetImport() {
  importPreviewData.value = null
  receipt.value = null
  outcomeUnknown.value = false
  importError.value = ''
  importErrorCode.value = ''
  requestId.value = ''
  nameEdits.value = {}
}
const busyImport = computed(() => uploading.value || submitting.value)

async function runImportPreview() {
  if (!uploadId.value) return
  busy.value = true
  try {
    importPreviewData.value = await importPreview(uploadId.value)
    const edits: Record<string, string> = {}
    for (const a of importPreviewData.value.assets) edits[a.packageKey] = a.suggestedName
    nameEdits.value = edits
  } catch (e) {
    importError.value = (e as Error).message || '预检失败'
  } finally { busy.value = false }
}

async function runConfirm() {
  if (!importable.value || busyImport.value) return
  submitting.value = true
  importError.value = ''
  // 至明确结果前不换 requestId：网络中断后用同 ID 查回执或重试
  if (!requestId.value) requestId.value = 'req-' + crypto.randomUUID()
  const overrides: Record<string, string> = {}
  for (const [k, v] of Object.entries(nameEdits.value)) {
    if (v.trim() && importPreviewData.value?.assets.find(a => a.packageKey === k)?.suggestedName !== v.trim()) {
      overrides[k] = v.trim()
    }
  }
  try {
    const r = await importConfirm(importPreviewData.value!.previewToken, requestId.value, overrides)
    receipt.value = r
    requestId.value = r.requestId || requestId.value
    void discard({ previewToken: importPreviewData.value?.previewToken })
  } catch (e: any) {
    if (e?.failure && e.failure !== 'http') {
      outcomeUnknown.value = true
      importError.value = '未收到导入结果，暂不能确认是否成功。请点击「核对导入结果」，不要重复提交。'
    } else {
      importError.value = e?.message || '导入失败'
      importErrorCode.value = e?.data?.code || ''
      const suggestions = conflictSuggestions(e)
      if (suggestions.length) {
        for (const s of suggestions) nameEdits.value[s.packageKey] = s.suggestedName
      }
    }
  } finally { submitting.value = false }
}

async function verifyOutcome() {
  if (!requestId.value) return
  busy.value = true
  try {
    const r = await importResult(requestId.value)
    if (r.status === 'completed' && r.receipt) {
      receipt.value = r.receipt
      outcomeUnknown.value = false
      importError.value = ''
    } else {
      importError.value = '服务端尚无本次导入的回执。可重新点击「确认导入」继续提交（不会产生重复副本）。'
      outcomeUnknown.value = false
    }
  } catch (e) {
    importError.value = (e as Error).message || '核对失败'
  } finally { busy.value = false }
}

function againImport() {
  // 新的一次主动导入：清空全部状态重新开始（新预检/新命名/新 requestId）
  resetImport()
  uploadId.value = ''
  fileName.value = ''
  fileSize.value = 0
  uploadDone.value = 0
  uploadTotal.value = 0
}

function openAsset(kind: string, id: string) {
  // 结果跳转传新 ID（不能按名称定位）；App 层负责真实切换并离开设置
  if (kind === 'model') emit('openOntology', id)
  else if (kind === 'project') emit('openProject', id)
}

const kindLabel: Record<string, string> = { model: '本体', project: '项目', flow: '编排', modelConfig: '模型配置' }
function formatSize(n: number) { return n < 1024 * 1024 ? (n / 1024).toFixed(1) + ' KB' : (n / 1024 / 1024).toFixed(2) + ' MB' }
</script>

<template>
<div class="ct-page">
  <header class="ct-head">
    <div>
      <h2>配置迁移</h2>
      <p class="muted">把本工作台的本体与项目配置打包交给他人继续编辑。每次导入都创建新的本体和项目，不会覆盖已有内容。</p>
    </div>
  </header>

  <div class="tabs ct-tabs" role="tablist" aria-label="配置迁移页签">
    <button type="button" role="tab" :class="{ active: tab === 'export' }" :aria-selected="tab === 'export'" @click="tab = 'export'">导出配置</button>
    <button type="button" role="tab" :class="{ active: tab === 'import' }" :aria-selected="tab === 'import'" @click="tab = 'import'">导入配置</button>
  </div>

  <p v-if="message" :class="{ error: messageError }" role="status">{{ message }}</p>

  <!-- ── 导出 ── -->
  <section v-if="tab === 'export'" class="card ct-card">
    <p v-if="listsError" class="inline-error" role="alert">{{ listsError }} <button type="button" @click="loadLists(true)">重试</button></p>
    <div class="ct-pickhead">
      <h3>选择要导出的内容</h3>
      <div class="ct-search"><SearchField :value="searchText" placeholder="搜索本体或项目" aria-label="搜索本体或项目" @update:value="searchText = $event"/></div>
    </div>
    <div class="ct-columns">
      <div class="ct-col">
        <h4>本体（可多选）</h4>
        <p v-if="!filteredOntologies.length" class="muted">还没有本体。</p>
        <label v-for="o in filteredOntologies" :key="o.id" class="check-option ct-item">
          <input type="checkbox" :checked="selectedModels.includes(o.id)" @change="invalidateExportPreview(); toggle(selectedModels, o.id)">
          <span class="ct-item-name">{{ o.name }}</span>
        </label>
      </div>
      <div class="ct-col">
        <h4>相关项目（可多选，默认不全选）</h4>
        <p v-if="!filteredProjects.length" class="muted">还没有项目；可只导出本体。</p>
        <label v-for="p in filteredProjects" :key="p.id" class="check-option ct-item">
          <input type="checkbox" :checked="selectedProjects.includes(p.id)" @change="invalidateExportPreview(); toggle(selectedProjects, p.id)">
          <span class="ct-item-name">{{ p.name }}</span>
        </label>
      </div>
    </div>
    <details class="ct-extra" :open="showExtra" @toggle="showExtra = ($event.target as HTMLDetailsElement).open">
      <summary>额外编排（可选，未绑定到项目的编排）</summary>
      <p v-if="!extraFlows.length" class="muted">当前没有编排。</p>
      <label v-for="f in extraFlows" :key="f.id" class="check-option ct-item">
        <input type="checkbox" :checked="selectedExtraFlows.includes(f.id)" @change="invalidateExportPreview(); toggle(selectedExtraFlows, f.id)">
        <span class="ct-item-name">{{ f.name }}</span>
      </label>
    </details>
    <div class="tools ct-actions">
      <button type="button" class="primary" :disabled="!canPreview || busy" @click="runExportPreview">{{ busy ? '正在生成预览…' : '查看导出内容' }}</button>
      <span class="note">预览是冻结快照；之后的新编辑不会混进已生成的包，需要时重新生成预览。</span>
    </div>

    <div v-if="exportPreviewData" class="ct-preview">
      <div v-if="exportPreviewData.blockers.length" class="inline-error" role="alert">
        <strong>存在阻断问题，无法导出：</strong>
        <ul><li v-for="b in exportPreviewData.blockers" :key="b">{{ b }}</li></ul>
      </div>
      <h3>导出内容预览 <small class="muted">快照时间 {{ exportPreviewData.snapshotAt }}</small></h3>
      <div class="ct-preview-grid">
        <section v-if="exportPreviewData.assets.models.length"><h4>本体</h4><ul class="ct-list">
          <li v-for="m in exportPreviewData.assets.models" :key="m.packageKey"><strong>{{ m.name }}</strong>
            <small class="muted"> · {{ m.releaseCount }} 个发布版本 · {{ m.includeReason }}</small></li>
        </ul></section>
        <section v-if="exportPreviewData.assets.projects.length"><h4>项目</h4><ul class="ct-list">
          <li v-for="p in exportPreviewData.assets.projects" :key="p.packageKey"><strong>{{ p.name }}</strong>
            <small class="muted"> · {{ p.releaseCount }} 个发布版本</small></li>
        </ul></section>
        <section v-if="exportPreviewData.assets.flows.length"><h4>函数编排</h4><ul class="ct-list">
          <li v-for="f in exportPreviewData.assets.flows" :key="f.packageKey"><strong>{{ f.name }}</strong>
            <small class="muted"> · {{ f.includeReason }}<template v-if="f.usedBy?.length"> · 使用方：{{ f.usedBy.join('、') }}</template><template v-if="f.shared"> · 多项目共享同一副本</template></small></li>
        </ul></section>
        <section v-if="exportPreviewData.assets.modelConfigs.length"><h4>模型配置</h4><ul class="ct-list">
          <li v-for="c in exportPreviewData.assets.modelConfigs" :key="c.packageKey"><strong>{{ c.name }}</strong>
            <small class="muted"> · 仅元数据<template v-if="c.missingApiKey"> · 未配置 API Key（导入后需补填）</template></small></li>
        </ul></section>
      </div>
      <ul v-if="exportPreviewData.warnings.length" class="ct-warnings">
        <li v-for="w in exportPreviewData.warnings" :key="w">{{ w }}</li>
      </ul>
      <div class="tools ct-actions">
        <button type="button" class="primary" :disabled="!!exportPreviewData.blockers.length || downloading" @click="runDownload">{{ downloading ? '正在生成配置包…' : '下载配置包' }}</button>
        <button type="button" :disabled="busy" @click="runExportPreview">重新生成预览</button>
      </div>
    </div>
  </section>

  <!-- ── 导入 ── -->
  <section v-else class="card ct-card">
    <!-- 结果页 -->
    <div v-if="receipt" class="ct-success">
      <h3>导入完成</h3>
      <p>已创建 {{ receipt.assets.filter(a => a.kind !== 'modelConfig').length }} 项配置<template v-if="receipt.pendingCredentials.length">；{{ receipt.pendingCredentials.length }} 项凭据待补填</template>。</p>
      <p class="muted">本次导入全部是新资产：已有内容不受影响。配置已就绪但未执行任何连接测试或业务查询。</p>
      <table class="ct-result">
        <thead><tr><th>类型</th><th>导入后名称</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="a in receipt.assets" :key="a.packageKey">
            <td>{{ kindLabel[a.kind] || a.kind }}</td>
            <td><strong>{{ a.newName }}</strong><small v-if="a.sourceName && a.sourceName !== a.newName" class="muted">（原：{{ a.sourceName }}）</small></td>
            <td>
              <button v-if="a.kind === 'model'" type="button" @click="openAsset('model', a.newId)">打开本体</button>
              <button v-else-if="a.kind === 'project'" type="button" @click="openAsset('project', a.newId)">打开项目</button>
              <template v-else>—</template>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="receipt.pendingCredentials.length" class="ct-pending">
        <h4>待补配置（重启后仍保留）</h4>
        <ul><li v-for="p in receipt.pendingCredentials" :key="p.declarationId">
          <strong>{{ p.name }}</strong><span class="muted"> — {{ (p.usage || []).join('；') || '凭据缺失' }}；请在项目「数据连接 / 凭据」处重新填写。</span>
        </li></ul>
      </div>
      <ul v-if="receipt.warnings.length" class="ct-warnings"><li v-for="w in receipt.warnings" :key="w">{{ w }}</li></ul>
      <div class="tools ct-actions">
        <button type="button" class="primary" @click="againImport">再次导入此文件</button>
        <button type="button" @click="emit('navigate', 'settings-transfer')">返回导出</button>
      </div>
    </div>

    <!-- 上传 + 预检 -->
    <template v-else>
      <div class="ct-drop">
        <strong>{{ fileName ? '已选择文件' : '选择配置包' }}</strong>
        <button type="button" :disabled="busyImport" @click="pickFile">{{ fileName ? '更换文件' : '选择文件' }}</button>
        <input ref="fileInput" type="file" accept=".zip" class="ct-file-input" @change="onFileChange">
        <p v-if="fileName" class="note">{{ fileName }}（{{ formatSize(fileSize) }}）
          <template v-if="uploading"> · 正在上传 {{ uploadDone }}/{{ uploadTotal }} 分片…</template>
          <template v-else-if="busy && uploadId"> · 正在检查配置包…</template>
        </p>
        <p class="note">仅支持本工作台「导出配置」生成的 ZIP；导入会创建全新的本体和项目，不会覆盖已有内容。</p>
      </div>
      <AppError v-if="importError && !outcomeUnknown" compact title="导入未完成" :reason="importError"
                 :hint="importErrorCode === 'DUPLICATE_NAME' ? '已按服务端建议更新名称，请确认后重新提交。' : ''"
                 :retry-label="importErrorCode === 'DUPLICATE_NAME' ? undefined : '重试'" @retry="runConfirm"/>
      <AppError v-else-if="importError && outcomeUnknown" compact title="导入结果待确认" :reason="importError"
                hint="网络中断可能发生在保存已完成之后。请先核对，不要重复导入。" retry-label="核对导入结果" @retry="verifyOutcome"/>

      <div v-if="importPreviewData" class="ct-preview">
        <div v-if="importPreviewData.blockers.length" class="inline-error" role="alert">
          <strong>存在阻断问题，无法导入：</strong>
          <ul><li v-for="b in importPreviewData.blockers" :key="b">{{ b }}</li></ul>
        </div>
        <div class="ct-confirm-banner">本次将创建新的本体和项目，不会覆盖已有内容。</div>
        <table class="ct-result">
          <thead><tr><th>类型</th><th>原名称</th><th>导入后名称（可修改）</th><th>版本</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="a in importPreviewData.assets" :key="a.packageKey">
              <td>{{ kindLabel[a.kind] }}</td>
              <td>{{ a.sourceName }}</td>
              <td>
                <input v-if="a.kind !== 'flow'" v-model="nameEdits[a.packageKey]" class="ct-name-input" :aria-label="'导入后名称：' + a.sourceName" maxlength="80">
                <template v-else>{{ nameEdits[a.packageKey] }}</template>
              </td>
              <td>{{ a.releaseCount || '—' }}</td>
              <td class="ct-reason">
                <template v-if="a.renameReason">{{ a.renameReason }}</template>
                <template v-if="a.dependencyNote"><div class="muted">{{ a.dependencyNote }}</div></template>
              </td>
            </tr>
          </tbody>
        </table>
        <ul v-if="importPreviewData.warnings.length" class="ct-warnings">
          <li v-for="w in importPreviewData.warnings" :key="w">{{ w }}</li>
        </ul>
        <div class="tools ct-actions">
          <button type="button" class="primary" :disabled="!importable || busyImport" @click="runConfirm">{{ submitting ? '正在导入…' : '确认导入' }}</button>
          <button type="button" :disabled="busyImport" @click="pickFile">取消</button>
          <span class="note">导入一次保存完成；确认后请勿关闭页面，直到看到结果。</span>
        </div>
      </div>
    </template>
  </section>
</div>
</template>

<style scoped>
.ct-page{display:flex;flex-direction:column;gap:14px}
.ct-head h2{margin:0 0 4px}
.ct-tabs{display:flex;gap:8px}
.ct-tabs button{padding:7px 16px}
.ct-tabs button.active{border-color:var(--blue);color:var(--blue);background:var(--blue-soft)}
.ct-card{padding:20px 22px}
.ct-pickhead{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}
.ct-search{max-width:280px}
.ct-search .search-field{width:100%}
.ct-columns{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.ct-col h4{margin:0 0 4px;font-size:13px}
.ct-item{margin:0;padding:9px 10px;border-radius:var(--r-sm);cursor:pointer;color:var(--ink);font-size:14px}
.ct-item:hover{background:var(--paper-2)}
.ct-item input[type="checkbox"]{margin:3px 0 0}
.ct-item-name{color:var(--ink);overflow-wrap:anywhere}
.ct-file-input{display:none!important}
.ct-extra{margin-top:12px}
.ct-extra summary{cursor:pointer;font-size:13px;color:var(--ink-2)}
.ct-actions{margin-top:14px;align-items:center;flex-wrap:wrap}
.ct-preview{margin-top:18px;border-top:1px solid var(--line);padding-top:14px}
.ct-preview h3{margin:0 0 10px}
.ct-preview-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px 24px}
.ct-preview-grid h4{margin:0 0 6px;font-size:13px}
.ct-list{margin:0;padding-left:18px}
.ct-list li{margin:3px 0}
.ct-warnings{margin:12px 0 0;padding-left:18px;color:var(--warn);font-size:13px}
.ct-warnings li{margin:3px 0}
.ct-drop{border:1px dashed var(--line-2);background:var(--bg);border-radius:var(--r-md);padding:18px;text-align:center}
.ct-drop strong{display:block;margin-bottom:10px}
.ct-drop button{margin-right:8px}
.ct-confirm-banner{background:var(--blue-soft);color:var(--blue-ink);border:1px solid var(--blue);border-radius:var(--r-sm);padding:8px 12px;margin-bottom:12px;font-size:13px}
.ct-result{width:100%;border-collapse:collapse}
.ct-result th,.ct-result td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top;font-size:13px}
.ct-name-input{width:100%;max-width:240px;margin:0;min-height:36px;padding:7px 10px}
.ct-reason{color:var(--muted)}
.ct-success{padding:4px 0}
.ct-success h3{margin:0 0 8px;color:var(--ok)}
.ct-pending{margin-top:12px;background:var(--warn-soft);border:1px solid var(--warn);border-radius:var(--r-sm);padding:10px 14px}
.ct-pending h4{margin:0 0 6px;font-size:13px}
.inline-error{color:var(--danger);background:var(--danger-soft);border:1px solid var(--danger);border-radius:var(--r-sm);padding:10px 14px;margin:8px 0}
.inline-error ul{margin:6px 0 0;padding-left:18px}
@media(max-width:900px){.ct-columns,.ct-preview-grid{grid-template-columns:1fr}}
</style>

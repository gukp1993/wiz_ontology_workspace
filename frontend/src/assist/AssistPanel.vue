<!-- ─── 辅助填写面板（T3）────────────────────────────────────────────────────
     挂载点：各表单组件（T5–T10）在本体/项目编辑区一侧渲染本面板，并传入宿主适配
     binding（见 useAssistPanel.ts 的 AssistHostBinding）。
     props：
       binding — 宿主适配接口：draft()/apply()/snapshot()/restore() + 目标标识；
                 宿主切换编辑目标时传新的 binding 对象（本面板 watch 到变化即重新 open），
                 或经 ref 调 open(binding)。
       api     — 可选注入（测试桩）；缺省用 defaultAssistApi()（POST /api/assist-*）。
       locate  — 可选：检查页签 issue 的字段定位回调（由宿主提供滚动/高亮）。
     emits：
       close — 用户点「收起」：面板内部 close()（contextToken 与结果保留，可 reopen），
               同时通知宿主移除挂载点。
     保存边界：本面板只把采纳值写进宿主草稿（binding.apply），「已填入表单，尚未保存」，
     绝不调用表单保存/commit-now/touch——持久化由用户在表单上自行保存。
     安全：所有模型/用户文本一律插值渲染（不用 v-html）；模型输出按不可信数据处理。 -->
<script setup lang="ts">
import { onMounted, watch, computed } from 'vue'
import {
  useAssistPanel, defaultAssistApi, type AssistApi, type AssistHostBinding,
} from './useAssistPanel'
import type { AssistQuestion } from './types'

const props = defineProps<{
  binding: AssistHostBinding
  api?: AssistApi
  locate?: (fieldKey: string) => void
}>()
const emit = defineEmits<{ close: [] }>()

const {
  status, contextInfo, result, error, tab, intent, answers, checked,
  stale, canUndo, justAdopted, notice, closed, contextToken,
  questions, suggestions, issues, explanation, hasContext, selectedCount,
  open, refreshContext, generate, adopt, undo, notifyDraftChanged, syncStaleness,
  cancel, close, reopen, setChecked, answerQuestion, clearNotice,
} = useAssistPanel(props.api ?? defaultAssistApi())

onMounted(() => { void open(props.binding) })
// 宿主切换编辑目标：binding 对象变化即整卡重置并重取上下文（同目标持续编辑请保持同一 binding 对象）
watch(() => props.binding, b => { if (b) void open(b) })

const busy = computed(() => status.value === 'generating' || status.value === 'loading-context')
const canGenerate = computed(() => !closed.value && hasContext.value && !busy.value && status.value !== 'no-model')
const canAdopt = computed(() => !closed.value && !stale.value && status.value === 'done' && selectedCount.value > 0)

const TABS: { key: 'fill' | 'check' | 'explain'; label: string }[] = [
  { key: 'fill', label: '帮我填写' },
  { key: 'check', label: '检查当前内容' },
  { key: 'explain', label: '解释怎么填' },
]

const runLabel = computed(() => {
  if (status.value === 'generating') return '生成中…'
  if (tab.value === 'check') return '开始检查'
  if (tab.value === 'explain') return '解释怎么填'
  return '生成建议'
})

/** 错误分类：决定错误横幅的行动按钮。 */
const errorKind = computed(() => {
  if (status.value !== 'error' || !error.value) return ''
  const c = error.value.code
  if (c === 'CONTEXT_STALE') return 'stale-context'
  if (c === 'MODEL_NOT_CONFIGURED') return 'no-model'
  if (c === 'MODEL_BAD_RESPONSE' || c === 'MODEL_TIMEOUT' || c === '502' || c === '504') return 'model-retry'
  return 'retry'
})

// ── 展示辅助（全部纯文本插值，不用 v-html）────────────────────────────────────
function fmtValue(v: unknown): string {
  if (v === undefined || v === null || v === '') return '（空）'
  if (typeof v === 'string') return v
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
function fieldLabel(key: string): string {
  return contextInfo.value?.editableFields.find(f => f.key === key)?.label ?? key
}
function currentOf(key: string): string {
  return fmtValue(props.binding.draft()[key])
}
function proposedOf(s: { proposed: Record<string, unknown> }, key: string): string {
  return fmtValue(s.proposed[key])
}
function stateTag(state: string): string {
  return state === 'ready' ? '可采纳' : state === 'pending' ? '待补充' : '不可采纳'
}
function reasonOf(s: { state: string; reason?: string; pendingReason?: string; blockedReason?: string }): string {
  if (s.state === 'blocked') return s.blockedReason || s.reason || ''
  if (s.state === 'pending') return s.pendingReason || s.reason || ''
  return s.reason || ''
}
function catalogFields(c: { fields: { name: string }[] }): string {
  return c.fields.slice(0, 8).map(f => f.name).join('、') + (c.fields.length > 8 ? ' 等' : '')
}
const metaLine = computed(() => {
  const m = result.value?.meta
  if (!m) return ''
  return `${m.provider} · ${m.model} · ${m.durationMs}ms`
})

// ── 交互（回答变化 → 自动重算：重发全部 answers 与 intent）─────────────────────
function setAnswerText(q: AssistQuestion, value: string) {
  answerQuestion(q.id, value.trim() === '' ? undefined : { value: value.trim() })
  void generate()
}
function setAnswerChoice(q: AssistQuestion, value: string) {
  const cur = answers.value[q.id]
  answerQuestion(q.id, { value, unsure: cur?.unsure || undefined })
  void generate()
}
function toggleUnsure(q: AssistQuestion, on: boolean) {
  const cur = answers.value[q.id]
  if (on) answerQuestion(q.id, { value: cur?.value, unsure: true })
  else answerQuestion(q.id, cur?.value !== undefined ? { value: cur.value } : undefined)
  void generate()
}
function onCollapse() {
  close()
  emit('close')
}
// 宿主表单可选择不调 notifyDraftChanged，而把表单 change 事件接到 syncStaleness
function hostChanged() { notifyDraftChanged() }

defineExpose({ open, close, reopen, refreshContext, notifyDraftChanged: hostChanged, syncStaleness })
</script>

<template>
<section v-if="!closed" class="card assist-panel">
  <header class="assist-head">
    <div class="assist-head-text">
      <p class="eyebrow">辅助填写</p>
      <h3>{{ contextInfo?.title || binding.contextTitle }}</h3>
    </div>
    <div class="tools">
      <button type="button" class="mini" @click="onCollapse">收起</button>
    </div>
  </header>

  <p class="assist-privacy">已获授权模型将看到以上参考内容与你的输入；连接密码等凭据不会发送。模型建议仅供参考，采纳后仍需你检查并自行保存。</p>

  <details class="assist-ref">
    <summary>本次参考内容</summary>
    <div v-if="contextInfo" class="assist-ref-body">
      <div v-if="(contextInfo.definitions || []).length">
        <h4>相关定义</h4>
        <ul>
          <li v-for="d in contextInfo.definitions" :key="d.kind + ':' + d.id">
            {{ d.label }}<span class="muted">（{{ d.kind }}）</span>
            <small v-if="d.hint" class="muted"> — {{ d.hint }}</small>
          </li>
        </ul>
      </div>
      <div v-if="(contextInfo.catalog || []).length">
        <h4>数据目录</h4>
        <ul>
          <li v-for="c in contextInfo.catalog" :key="c.connection + ':' + c.table">
            {{ c.connection }} · {{ c.table }}（{{ c.fields.length }} 个字段：{{ catalogFields(c) }}）
          </li>
        </ul>
      </div>
      <div v-if="(contextInfo.flows || []).length">
        <h4>函数编排</h4>
        <ul>
          <li v-for="f in contextInfo.flows" :key="f.id">
            {{ f.name }}<span class="muted">（{{ f.id }}）</span>
            <small class="muted"> — 输入：{{ f.inputs.map(i => i.label).join('、') || '无' }}；输出：{{ f.outputs.map(o => o.label).join('、') || '无' }}</small>
          </li>
        </ul>
      </div>
      <p v-if="!(contextInfo.definitions || []).length && !(contextInfo.catalog || []).length && !(contextInfo.flows || []).length" class="muted">本场景没有附加参考内容。</p>
    </div>
    <p v-else class="muted">尚未获取上下文。</p>
  </details>

  <!-- 取上下文中 / 完全失败（没有任何上下文） -->
  <p v-if="status === 'loading-context'" class="muted assist-line">正在获取辅助上下文…</p>
  <div v-else-if="!hasContext && status !== 'idle'" class="assist-banner assist-banner-error">
    <p role="alert">{{ error?.message || '获取辅助上下文失败。' }}</p>
    <button type="button" @click="refreshContext">重新获取上下文</button>
  </div>
  <div v-else-if="status === 'idle'" class="assist-banner">
    <p class="muted">尚未获取上下文。</p>
    <button type="button" @click="refreshContext">获取上下文</button>
  </div>

  <template v-else>
    <!-- 补充说明：三个模式共用，页签切换不清空 -->
    <label class="assist-intent">
      <span class="assist-intent-title">补充说明（可留空）</span>
      <textarea v-model="intent" rows="3" maxlength="4000" :disabled="busy"
                placeholder="想让它怎么填？可粘贴背景资料（不要粘贴密码等敏感信息）。"
                @change="syncStaleness"></textarea>
      <small class="muted">{{ intent.length }}/4000</small>
    </label>

    <!-- 模式页签 -->
    <div class="assist-tabs" role="tablist" aria-label="辅助模式">
      <button v-for="t in TABS" :key="t.key" type="button" role="tab"
              :class="{ active: tab === t.key }" :aria-selected="tab === t.key"
              :disabled="busy" @click="tab = t.key">{{ t.label }}</button>
    </div>

    <div class="assist-run tools">
      <button type="button" class="primary" :disabled="!canGenerate" @click="generate">{{ runLabel }}</button>
      <button v-if="status === 'generating'" type="button" @click="cancel">取消</button>
      <span v-if="notice" class="muted">{{ notice }}</span>
    </div>

    <!-- 未配置模型：保留输入 + 引导（不做路由跳转，由宿主引导） -->
    <div v-if="status === 'no-model'" class="assist-banner assist-banner-warn">
      <p>当前账号还没有配置可用的默认模型。请前往「设置 → 模型设置」配置默认模型后重试；你填写的内容会保留。</p>
    </div>

    <!-- 错误态 -->
    <div v-if="status === 'error' && error" class="assist-banner assist-banner-error">
      <p role="alert">{{ error.message }}</p>
      <button v-if="errorKind === 'stale-context'" type="button" @click="refreshContext">重新获取上下文</button>
      <button v-else type="button" :disabled="busy" @click="generate">重试</button>
    </div>

    <!-- 失效 / 已填入提示条 -->
    <div v-if="stale && (status === 'done' || status === 'empty')" class="assist-banner assist-banner-warn">
      <p>建议已失效：表单已修改，请重新生成。</p>
    </div>
    <div v-if="justAdopted" class="assist-banner assist-banner-ok">
      <p>已填入表单，尚未保存；请检查内容后在表单上自行保存。</p>
    </div>

    <p v-if="status === 'generating'" class="muted assist-line">正在生成，请稍候…</p>

    <template v-else-if="status === 'done' || status === 'empty'">
      <p v-if="metaLine" class="muted assist-meta">{{ metaLine }}</p>

      <!-- 空结果 -->
      <p v-if="status === 'empty'" class="muted assist-line">模型本次没有给出可用建议；可补充说明后重试，或切换页签检查当前内容。</p>

      <!-- fill：补充问题 + 建议 -->
      <template v-if="tab === 'fill'">
        <div v-for="q in questions" :key="q.id" class="assist-question">
          <p class="assist-q-prompt">{{ q.prompt }}</p>
          <input v-if="q.kind === 'text'"
                 :value="answers[q.id]?.value ?? ''" type="text" :maxlength="2000"
                 :placeholder="q.allowUnsure ? '可填写，或勾选「暂不确定」' : '请填写'"
                 @change="setAnswerText(q, ($event.target as HTMLInputElement).value)">
          <div v-else class="assist-choices">
            <label v-for="opt in q.options" :key="opt.value" class="check-option assist-choice">
              <input type="radio" :name="'assist-q-' + q.id" :checked="answers[q.id]?.value === opt.value"
                     @change="setAnswerChoice(q, opt.value)">
              <span>{{ opt.label }}</span>
            </label>
          </div>
          <label v-if="q.allowUnsure" class="check-option assist-unsure">
            <input type="checkbox" :checked="!!answers[q.id]?.unsure"
                   @change="toggleUnsure(q, ($event.target as HTMLInputElement).checked)">
            <span>暂不确定</span>
          </label>
        </div>

        <div v-if="suggestions.length" class="assist-suggestions">
          <div v-for="s in suggestions" :key="s.id" class="assist-suggestion" :class="'is-' + s.state">
            <div class="assist-s-head">
              <label v-if="s.state === 'ready'" class="check-option assist-check">
                <input type="checkbox" :checked="!!checked[s.id] && !stale" :disabled="stale"
                       @change="setChecked(s.id, ($event.target as HTMLInputElement).checked)">
              </label>
              <span v-else class="assist-tag" :class="'tag-' + s.state">{{ stateTag(s.state) }}</span>
              <strong>{{ s.label }}</strong>
            </div>
            <table class="assist-diff">
              <tbody>
                <tr v-for="k in s.fieldKeys" :key="s.id + ':' + k">
                  <th>{{ fieldLabel(k) }}</th>
                  <td class="diff-old">{{ currentOf(k) }}</td>
                  <td class="diff-new">{{ proposedOf(s, k) }}</td>
                </tr>
              </tbody>
            </table>
            <p v-if="reasonOf(s)" class="muted assist-reason">{{ reasonOf(s) }}</p>
            <p v-if="(s.evidenceRefs || []).length" class="muted assist-evidence">依据：{{ (s.evidenceRefs || []).join('；') }}</p>
          </div>
        </div>

        <div class="assist-adopt tools">
          <button type="button" class="primary" :disabled="!canAdopt" @click="adopt">填入所选建议{{ selectedCount ? '（' + selectedCount + ' 项）' : '' }}</button>
          <button type="button" :disabled="!canUndo" @click="undo">撤销填入</button>
        </div>
        <p class="muted assist-note">填入只改动表单草稿，不会自动保存；pending「待补充」与 blocked「不可采纳」的建议无法勾选。</p>
      </template>

      <!-- check：问题清单 -->
      <template v-else-if="tab === 'check'">
        <div v-if="issues.length" class="assist-issues">
          <div v-for="(it, idx) in issues" :key="idx" class="assist-issue">
            <p><strong>{{ fieldLabel(it.fieldKey) }}</strong><span class="muted">（{{ it.fieldKey }}）</span></p>
            <p>{{ it.message }}</p>
            <button v-if="locate" type="button" class="mini" @click="locate(it.fieldKey)">定位字段</button>
          </div>
        </div>
        <p v-else class="muted assist-line">模型没有发现需要处理的问题。</p>
      </template>

      <!-- explain：解释文本 -->
      <template v-else>
        <div v-if="explanation" class="assist-explain">
          <h4>{{ explanation.title }}</h4>
          <p>{{ explanation.body }}</p>
        </div>
        <p v-else class="muted assist-line">模型本次没有返回解释内容。</p>
      </template>
    </template>
  </template>
</section>
</template>

<style scoped>
.assist-panel{font-size:14px}
.assist-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.assist-head-text h3{margin:4px 0 0;font-size:15px;overflow-wrap:anywhere}
.assist-privacy{font-size:12px;color:var(--muted);background:var(--paper-2);border-radius:var(--r-sm);padding:8px 10px;margin:12px 0}
.assist-ref summary{cursor:pointer;font-size:13px;color:var(--muted)}
.assist-ref-body{padding:10px 12px;margin-top:8px;background:var(--paper-2);border:1px solid var(--line);border-radius:var(--r-sm);font-size:13px}
.assist-ref-body h4{margin:8px 0 4px;font-size:12px;color:var(--muted)}
.assist-ref-body ul{margin:0;padding-left:18px}
.assist-ref-body li{margin:3px 0;overflow-wrap:anywhere}
.assist-line{margin:12px 0}
.assist-intent{display:block;margin:12px 0;color:var(--ink)}
.assist-intent-title{display:block;font-size:13px;color:var(--muted)}
.assist-intent textarea{margin-top:6px;min-height:64px}
.assist-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}
.assist-tabs button{border:1px solid var(--line);background:var(--paper);color:var(--ink-2);border-radius:var(--r-pill);padding:5px 12px;font-size:13px}
.assist-tabs button.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink);font-weight:600}
.assist-run{margin:10px 0}
.assist-banner{border:1px solid var(--line);border-radius:var(--r-sm);padding:10px 12px;margin:10px 0;font-size:13px}
.assist-banner p{margin:0 0 6px;overflow-wrap:anywhere}
.assist-banner-error{background:var(--danger-soft);border-color:var(--danger-line)}
.assist-banner-warn{background:var(--blue-soft);border-color:var(--blue-line)}
.assist-banner-ok{background:var(--paper-2)}
.assist-banner button{margin-right:8px}
.assist-meta{font-size:12px;margin:8px 0 0}
.assist-question{border:1px solid var(--line);border-radius:var(--r-sm);padding:12px 14px;margin:10px 0}
.assist-q-prompt{margin:0 0 8px;font-weight:600}
.assist-choices{display:flex;flex-direction:column;gap:4px}
.assist-choice{margin:4px 0}
.assist-unsure{margin:6px 0 0}
.assist-suggestions{margin:12px 0}
.assist-suggestion{border:1px solid var(--line);border-radius:var(--r-sm);padding:12px 14px;margin:8px 0}
.assist-suggestion.is-blocked,.assist-suggestion.is-pending{background:var(--paper-2)}
.assist-s-head{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.assist-check{margin:0}
.assist-tag{font-size:12px;border:1px solid var(--line);border-radius:var(--r-pill);padding:2px 9px;color:var(--muted);white-space:nowrap}
.assist-tag.tag-pending{border-color:var(--blue-line);color:var(--blue-ink);background:var(--blue-soft)}
.assist-tag.tag-blocked{border-color:var(--danger-line);color:var(--danger);background:var(--danger-soft)}
.assist-diff{width:100%;border-collapse:collapse;font-size:13px;margin:4px 0}
.assist-diff th,.assist-diff td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
.assist-diff th{width:32%;color:var(--muted);font-weight:500;background:var(--paper-2)}
.assist-diff .diff-old{color:var(--muted)}
.assist-diff .diff-new{color:var(--blue-ink);font-weight:600}
.assist-reason,.assist-evidence{margin:6px 0 0;font-size:12px}
.assist-adopt{margin:12px 0 6px}
.assist-note{font-size:12px;margin:6px 0 0}
.assist-issues{margin:12px 0}
.assist-issue{border:1px solid var(--line);border-radius:var(--r-sm);padding:10px 12px;margin:8px 0}
.assist-issue p{margin:0 0 4px;overflow-wrap:anywhere}
.assist-explain{border:1px solid var(--line);border-radius:var(--r-sm);padding:12px 14px;margin:12px 0}
.assist-explain h4{margin:0 0 6px;font-size:14px}
.assist-explain p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8}
</style>

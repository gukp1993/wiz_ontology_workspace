<!-- ─── 整表自动填写抽屉（T3，2026-09-22 改版）────────────────────────────────
     交互基准：文档/需求/20260922_整表自动填写交互/交互原型_v1.html；
     行为契约：文档/接口文档/04-编排与LLM接口.md §6.6。
     挂载点：各表单组件在页头放「✦ 自动填写」次要入口（宿主负责入口按钮与 aria-expanded），
     持有本面板并传入宿主适配 binding（见 formAutofill.ts 的 AutofillHostBinding）。
     推荐接线（G2/G3）：面板保持挂载，入口按钮经 ref 调 toggle()；旧接线（v-if + @close 卸载）
     仍兼容——done 自动收起不触发 close 事件，仅用户主动关闭（×/Esc/遮罩）才 emit('close')。
     props：
       binding   — 宿主适配接口：draft()/apply|applyDraft/snapshot()/restore()/codecs + 目标标识；
                   宿主切换编辑目标时传新 binding 对象（watch 到变化即重新 open）。
       api       — 可选注入（测试桩）；缺省用 defaultAssistApi()（POST /api/assist-*）。
       examples  — 可选示例文案 chips（点击填入输入框）。
       triggerId — 可选：宿主入口按钮 id（写进 aria-controls）。
     emits：
       close      — 用户主动关闭（×/Esc/窄屏遮罩）；宿主可据此移除挂载点。
       need-model — 当前账号未配置模型时用户点「前往模型设置」。
     保存边界：本面板只把操作写进宿主草稿（applyDraft/apply），「已填写 N 项，尚未保存」；
     绝不调用表单保存/commit-now/touch——持久化由用户在表单上自行保存。
     表单状态条（撤销/查看修改）由宿主按 statusBarText/roundSummary 渲染，本面板不渲染。
     安全：所有模型/用户文本一律插值渲染（不用 v-html）；模型输出按不可信数据处理。 -->
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  AUTOFILL_EMPTY_TEXT, defaultAssistApi, useAssistPanel,
  type AssistApi, type AutofillHostBinding,
} from './useAssistPanel'

const props = defineProps<{
  binding: AutofillHostBinding
  api?: AssistApi
  examples?: string[]
  triggerId?: string
  /** 宿主受控开合（T10 F2）：宿主常驻挂载面板时以本属性驱动显隐；
   *  不传则维持旧行为（挂载即打开，收起经 @close 由宿主卸载）。 */
  open?: boolean
}>()
const emit = defineEmits<{ close: []; 'need-model': [] }>()

const {
  status, collapsed, loadingContext, contextInfo, contextToken, intent, error, notice,
  questions, answers, unresolved, undoHint, statusBarText,
  helpStatus, helpResult, helpError, helpMode,
  open, close, expand, cancel, generate, answer, refreshContext, runHelp, notifyDraftChanged,
} = useAssistPanel(props.api ?? defaultAssistApi())

const drawerRef = ref<HTMLElement | null>(null)
const intentBox = ref<HTMLTextAreaElement | null>(null)
const narrow = ref(false) // ≤1100px：遮罩 + 焦点圈闭（视觉由 CSS 媒体查询负责，JS 只管行为）
let outsideFocus: HTMLElement | null = null

onMounted(() => {
  outsideFocus = (typeof document !== 'undefined' ? document.activeElement : null) as HTMLElement | null
  // 受控宿主（传入 :open）常驻挂载：只取上下文、保持收起，展开由 :open watch 驱动（T10 F1/F2）；
  // 未传 open 的旧宿主维持「挂载即打开」。
  void open(props.binding, { reveal: props.open === undefined ? true : props.open === true })
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    const mq = window.matchMedia('(max-width: 1100px)')
    narrow.value = !!mq.matches
    const onChange = (e: MediaQueryListEvent) => { narrow.value = !!e.matches }
    if (typeof mq.addEventListener === 'function') mq.addEventListener('change', onChange)
  }
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('keydown', onGlobalKeydown)
  }
})

// 焦点进入输入框（需求 §4.2）：上下文（textarea 挂载前提）就绪后再聚焦。
// 首帧 nextTick 时面板仍处 loading-context 分支、textarea 尚未渲染，直接聚焦是空操作（T10 F1）。
watch([contextToken, collapsed], () => {
  if (collapsed.value || !contextToken.value) return
  void nextTick(() => {
    const box = intentBox.value
    if (!box || typeof document === 'undefined') return
    const active = document.activeElement
    // 不抢用户当下已有的输入焦点（例如用户已点进别的表单控件）
    if (active === box) return
    if (active && active !== document.body && drawerRef.value?.contains(active)) return
    box.focus()
  })
}, { immediate: true })
onBeforeUnmount(() => {
  if (typeof window !== 'undefined' && typeof window.removeEventListener === 'function') {
    window.removeEventListener('keydown', onGlobalKeydown)
  }
})

// 宿主切换编辑目标：binding 对象变化即重新 open（同目标保留输入，切目标整卡重置）
watch(() => props.binding, b => { if (b) void open(b) })

// 宿主受控开合（T10 F2）：关闭只收起（保留输入/会话），重开复用同一引擎实例——
// 同目标输入与已答问题得以保留；切目标仍由上面的 binding watch 整卡重置。
watch(() => props.open, v => {
  if (v === undefined) return
  if (v && collapsed.value) expand()
  else if (!v && !collapsed.value) close()
})

const busy = computed(() => status.value === 'generating' || loadingContext.value)
const contextLine = computed(() => contextInfo.value?.title || props.binding.contextTitle)
const canGenerate = computed(() =>
  !collapsed.value && !!contextToken.value && !busy.value
  && status.value !== 'no-model' && intent.value.trim().length > 0)
const runLabel = computed(() => (status.value === 'generating' ? '正在填写…' : '自动填写'))

function applyExample(text: string): void {
  if (busy.value) return
  intent.value = text
  void nextTick(() => intentBox.value?.focus())
}

function restoreOutsideFocus(): void {
  const el = outsideFocus
  if (el && typeof el.focus === 'function' && typeof document !== 'undefined'
      && typeof document.contains === 'function' && document.contains(el)) el.focus()
}

function doClose(): void {
  close()
  restoreOutsideFocus()
  emit('close')
}

function onGlobalKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && !collapsed.value) {
    e.stopPropagation()
    doClose()
  }
}

/** 遮罩态（窄屏）Tab 焦点圈闭：键盘焦点不落到抽屉背后的表单（需求 §4.2）。 */
function trapTab(e: KeyboardEvent): void {
  if (!narrow.value || collapsed.value) return
  const root = drawerRef.value
  if (!root || typeof root.querySelectorAll !== 'function') return
  const focusables = Array.from(
    root.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'),
  ).filter(el => el.offsetParent !== null || el === document.activeElement)
  if (!focusables.length) return
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  const active = document.activeElement
  if (e.shiftKey && (active === first || !root.contains(active as Node))) {
    e.preventDefault(); last.focus()
  } else if (!e.shiftKey && (active === last || !root.contains(active as Node))) {
    e.preventDefault(); first.focus()
  }
}

defineExpose({
  /** 宿主手改字段后调用：作废在途请求 + 禁用整轮撤销 */
  notifyDraftChanged,
  open, close: doClose, expand,
  /** 入口按钮切换：收起状态展开 / 展开状态关闭（不 emit close 的自动收起路径由内部处理） */
  toggle: () => { if (collapsed.value) expand(); else doClose() },
  refreshContext,
  collapsed,
})
</script>

<template>
<section v-if="!collapsed" ref="drawerRef" class="assist-drawer" role="dialog" aria-label="自动填写"
         :aria-controls="props.triggerId" @keydown.tab="trapTab">
  <header class="assist-drawer-head">
    <h2><span class="assist-ai" aria-hidden="true">✦</span>自动填写</h2>
    <button type="button" class="assist-close" aria-label="关闭自动填写" @click="doClose">×</button>
  </header>

  <p class="assist-drawer-context">{{ contextLine }}</p>
  <p class="assist-drawer-tip">说说你想填写什么，生成后将直接填入当前表单。你仍可以检查和修改。</p>

  <p v-if="loadingContext" class="assist-line assist-muted">正在获取表单上下文…</p>
  <div v-else-if="!contextToken" class="assist-banner assist-banner-error">
    <p role="alert">{{ error?.message || '获取表单上下文失败。' }}</p>
    <button type="button" @click="refreshContext">重新获取上下文</button>
  </div>
  <template v-else>
    <label class="assist-intent">
      <span class="assist-intent-title">填写要求</span>
      <textarea ref="intentBox" v-model="intent" rows="6" maxlength="4000" :disabled="busy"
                aria-label="填写要求"
                placeholder="描述你想填写的内容，也可以粘贴业务说明。"></textarea>
      <small class="assist-muted">{{ intent.length }}/4000</small>
    </label>

    <div v-if="examples && examples.length" class="assist-examples">
      <button v-for="(ex, i) in examples" :key="i" type="button" :disabled="busy"
              :title="ex" @click="applyExample(ex)">试试：{{ ex }}</button>
    </div>

    <div class="assist-actions">
      <span class="assist-muted">填写当前表单；未提及的已有内容保留。</span>
      <button v-if="status === 'generating'" type="button" @click="cancel">取消生成</button>
      <button type="button" class="assist-primary" :disabled="!canGenerate" @click="generate">{{ runLabel }}</button>
    </div>

    <!-- 未配置模型：保留输入 + 引导设置入口，不整页阻断（需求 §4.1） -->
    <div v-if="status === 'no-model'" class="assist-banner assist-banner-warn">
      <p>当前账号还没有配置可用的默认模型。你填写的内容会保留；配置模型后即可自动填写。</p>
      <button type="button" @click="emit('need-model')">前往模型设置</button>
    </div>

    <!-- 错误态：保留表单与输入，提示具体原因，可重试（需求 §4.3） -->
    <div v-if="status === 'error' && error" class="assist-banner assist-banner-error">
      <p role="alert">{{ error.message }}</p>
      <button v-if="error.code === 'CONTEXT_STALE'" type="button" @click="refreshContext">重新获取上下文</button>
      <button v-else type="button" :disabled="busy" @click="generate">重试</button>
    </div>

    <!-- 无有效变更：明确 empty，不假称成功；empty 且带回 unresolved 时（模型明确拒绝并给了
         原因，T10 F3）不说「内容已一致」，改由下方 unresolved 清单如实展示具体原因。 -->
    <p v-if="status === 'empty' && !unresolved.length" class="assist-line assist-muted" role="status">{{ AUTOFILL_EMPTY_TEXT }}</p>
    <p v-else-if="status === 'empty'" class="assist-line assist-muted" role="status">本次没有可直接填写的内容；以下原因待处理。</p>

    <p v-if="notice" class="assist-line assist-muted">{{ notice }}</p>

    <!-- 回填摘要信息行（表单状态条由宿主渲染；此处仅提示待补） -->
    <p v-if="status === 'questions' && statusBarText" class="assist-line assist-filled" role="status">{{ statusBarText }}</p>

    <!-- 补问卡：问题文本 + 选项按钮 + 暂不确定；答案分字段提交（§4.3） -->
    <div v-for="q in questions" :key="q.id" class="assist-question">
      <p class="assist-q-text">{{ q.text }}</p>
      <p v-if="q.fields && q.fields.length" class="assist-muted assist-q-fields">涉及：{{ q.fields.join('、') }}</p>
      <div v-if="q.options && q.options.length" class="assist-q-options">
        <button v-for="opt in q.options" :key="opt" type="button"
                :class="{ 'assist-chosen': answers[q.id]?.value === opt }"
                @click="answer(q.id, opt)">{{ opt }}</button>
      </div>
      <input v-else type="text" :maxlength="2000" :value="answers[q.id]?.value ?? ''"
             class="assist-q-input" :aria-label="q.text"
             @change="answer(q.id, (($event.target as HTMLInputElement).value.trim() || undefined))">
      <button v-if="q.allowUnsure" type="button" class="assist-unsure"
              @click="answer(q.id, undefined, true)">暂不确定</button>
    </div>

    <!-- 未填写项及原因（独立合法组已填，未填写项逐条列出，禁止宣称完成） -->
    <div v-if="unresolved.length" class="assist-unresolved">
      <p class="assist-muted assist-unresolved-title">以下内容未能自动填写：</p>
      <p v-for="(u, i) in unresolved" :key="i" class="assist-unresolved-item">
        <strong>{{ u.field }}</strong>：{{ u.reason }}
      </p>
    </div>

    <p v-if="undoHint" class="assist-muted assist-line">{{ undoHint }}</p>

    <!-- check/explain 降为次要入口：只读展示，不提供任何写入（需求 §4.2） -->
    <details class="assist-help">
      <summary>更多帮助</summary>
      <div class="assist-help-actions">
        <button type="button" :disabled="busy || helpStatus === 'running'"
                @click="runHelp('check')">检查当前内容</button>
        <button type="button" :disabled="busy || helpStatus === 'running'"
                @click="runHelp('explain')">解释怎么填</button>
      </div>
      <p v-if="helpStatus === 'running'" class="assist-muted assist-line">正在{{ helpMode === 'check' ? '检查' : '整理解释' }}，请稍候…</p>
      <template v-else-if="helpStatus === 'done' && helpResult">
        <div v-if="(helpResult.issues || []).length">
          <p v-for="(it, i) in helpResult.issues" :key="i" class="assist-help-issue">
            <strong>{{ it.fieldKey }}</strong>：{{ it.message }}
          </p>
        </div>
        <div v-else-if="helpMode === 'check'" class="assist-muted assist-line">模型没有发现需要处理的问题。</div>
        <div v-if="helpResult.explanation" class="assist-help-explain">
          <h4>{{ helpResult.explanation.title }}</h4>
          <p>{{ helpResult.explanation.body }}</p>
        </div>
        <!-- 旧 suggestions 只读渲染：无勾选、无采纳入口 -->
        <div v-if="(helpResult.suggestions || []).length" class="assist-help-suggestions">
          <p v-for="s in helpResult.suggestions" :key="s.id" class="assist-help-issue">
            <strong>{{ s.label }}</strong>（{{ s.state === 'ready' ? '参考' : s.state === 'pending' ? '待补充' : '不适用' }}）：{{ s.reason || '仅供参考，不自动写入。' }}
          </p>
        </div>
      </template>
      <p v-else-if="helpStatus === 'error' && helpError" class="assist-banner assist-banner-error" role="alert">{{ helpError.message }}</p>
    </details>
  </template>
</section>
<div v-if="!collapsed" class="assist-shade" aria-hidden="true" @click="doClose"></div>
</template>

<style scoped>
.assist-drawer{position:fixed;top:0;right:0;bottom:0;width:min(420px,100vw);z-index:60;background:#fff;
  border-left:1px solid var(--line,#dce4ee);box-shadow:-8px 0 28px rgba(38,57,80,.08);
  overflow:auto;padding:20px;font-size:14px;color:var(--ink,#24384c)}
.assist-shade{position:fixed;inset:0;background:rgba(23,44,70,.33);z-index:55;display:none}
@media (max-width:1100px){.assist-shade{display:block}}
.assist-drawer-head{display:flex;justify-content:space-between;align-items:center;
  border-bottom:1px solid var(--line,#e6ecf3);padding-bottom:12px}
.assist-drawer-head h2{margin:0;font-size:16px}
.assist-ai{color:var(--blue-ink,#285bea);margin-right:6px}
.assist-close{border:none;background:transparent;padding:2px 8px;font-size:20px;color:var(--ink-2,#5b6c80);cursor:pointer}
.assist-close:hover{background:var(--paper-2,#f0f5ff)}
.assist-drawer-context{font-size:13px;color:#73849a;margin:12px 0 0}
.assist-drawer-tip{font-size:13px;color:#75869b;margin:6px 0 14px}
.assist-muted{color:var(--muted,#75869b)}
.assist-line{margin:10px 0;font-size:13px}
.assist-intent{display:block;margin:4px 0 8px}
.assist-intent-title{display:block;font-size:13px;color:var(--muted,#75869b);margin-bottom:6px}
.assist-intent textarea{width:100%;min-height:120px;resize:vertical;padding:10px 12px;
  border:1px solid var(--line,#cbd7e8);border-radius:6px;font:inherit;color:inherit;background:#fff;box-sizing:border-box}
.assist-examples{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 14px}
.assist-examples button{max-width:100%;text-align:left;font-size:12px;padding:4px 10px;
  border-radius:5px;border:1px solid var(--line,#d8e1ed);background:#fff;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.assist-examples button:hover{background:var(--paper-2,#f0f5ff)}
.assist-actions{display:flex;align-items:center;justify-content:flex-end;gap:10px;flex-wrap:wrap;margin:6px 0 4px}
.assist-actions .assist-muted{margin-right:auto;font-size:12px}
.assist-actions button{padding:8px 14px;border:1px solid var(--line,#d8e1ed);border-radius:6px;background:#fff;cursor:pointer;font:inherit}
.assist-actions button:hover:not(:disabled){background:var(--paper-2,#f0f5ff)}
.assist-actions button:disabled{opacity:.5;cursor:not-allowed}
.assist-primary{background:var(--blue-ink,#285bea);color:#fff;border-color:var(--blue-ink,#285bea)}
.assist-primary:hover:not(:disabled){background:#174ad4}
.assist-banner{border:1px solid var(--line,#dce4ee);border-radius:6px;padding:10px 12px;margin:10px 0;font-size:13px}
.assist-banner p{margin:0 0 6px;overflow-wrap:anywhere}
.assist-banner button{margin-right:8px;padding:4px 10px}
.assist-banner-error{background:var(--danger-soft,#fff0ed);border-color:var(--danger-line,#edc4bd);color:var(--danger,#ba3e34)}
.assist-banner-warn{background:var(--blue-soft,#edf3ff);border-color:var(--blue-line,#c4d5f5)}
.assist-question{background:#fff9ed;border:1px solid #f0dfbd;border-radius:6px;padding:14px;margin:12px 0}
.assist-q-text{margin:0 0 4px;font-weight:600;overflow-wrap:anywhere}
.assist-q-fields{margin:0 0 8px;font-size:12px}
.assist-q-options{display:flex;flex-direction:column;gap:6px;align-items:stretch;margin:8px 0}
.assist-q-options button{text-align:left;padding:7px 12px;border:1px solid #e3d5b3;border-radius:6px;background:#fff;cursor:pointer;font:inherit}
.assist-q-options button:hover{background:#fdf4de}
.assist-q-options button.assist-chosen{border-color:var(--blue-ink,#285bea);color:var(--blue-ink,#285bea);font-weight:600}
.assist-q-input{width:100%;padding:8px 11px;border:1px solid #e3d5b3;border-radius:6px;font:inherit;box-sizing:border-box}
.assist-unsure{margin-top:8px;padding:4px 10px;border:1px solid var(--line,#d8e1ed);border-radius:6px;background:transparent;cursor:pointer;font:inherit;font-size:13px}
.assist-filled{color:var(--blue-ink,#285bea);background:var(--blue-soft,#edf3ff);border-radius:6px;padding:8px 10px}
.assist-unresolved{border:1px solid var(--line,#dce4ee);border-radius:6px;padding:10px 12px;margin:10px 0;font-size:13px}
.assist-unresolved-title{margin:0 0 6px}
.assist-unresolved-item{margin:4px 0;overflow-wrap:anywhere}
.assist-help{margin:16px 0 4px;border-top:1px solid var(--line,#edf0f4);padding-top:10px}
.assist-help summary{cursor:pointer;font-size:13px;color:var(--muted,#75869b)}
.assist-help-actions{display:flex;gap:8px;margin:10px 0}
.assist-help-actions button{padding:5px 12px;border:1px solid var(--line,#d8e1ed);border-radius:6px;background:#fff;cursor:pointer;font:inherit;font-size:13px}
.assist-help-actions button:hover:not(:disabled){background:var(--paper-2,#f0f5ff)}
.assist-help-actions button:disabled{opacity:.5;cursor:not-allowed}
.assist-help-issue{margin:6px 0;font-size:13px;overflow-wrap:anywhere}
.assist-help-explain{border:1px solid var(--line,#dce4ee);border-radius:6px;padding:10px 12px;margin:8px 0}
.assist-help-explain h4{margin:0 0 6px;font-size:13px}
.assist-help-explain p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}
.assist-help-suggestions{margin:8px 0 0}
</style>

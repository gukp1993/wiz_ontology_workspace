<script setup lang="ts">
// P01 集成与保存协调（任务板 §1/§2/§5；保存规则见 GLM 指令第三节）。
// 两区各持一个 Saver 自动保存（saveCoordinator.ts）；导航两空间五菜单 + 更多工具；
// 顶栏不再有“保存草稿/发布”按钮：状态条 + 撤销/重做 + 校验快捷按钮；发布移入各区“校验与发布”页（P09）。
// 画布已整体搬到 components/ObjectCanvas.vue（由 ObjectWorkspace/P03 挂载）。
import { ref, computed, provide, onMounted, onBeforeUnmount, watch, shallowRef, nextTick, type Ref } from 'vue'
import OntologyHome from './ontology/OntologyHome.vue'
import ObjectWorkspace from './ontology/ObjectWorkspace.vue'
import SharedLibrary from './ontology/SharedLibrary.vue'
import BusinessRuleLibrary from './ontology/BusinessRuleLibrary.vue'
import OntologyRelease from './ontology/OntologyRelease.vue'
import ToolsPage from './tools/ToolsPage.vue'
import OntologyDiscover from './tools/OntologyDiscover.vue'
import KnowledgeExplorer from './tools/KnowledgeExplorer.vue'
import InstanceExplorer from './tools/InstanceExplorer.vue'
import AppSelect from './shared/AppSelect.vue'
import AppError from './shared/AppError.vue'
import FunctionManager from './ontology/FunctionManager.vue'
import ActionLibrary from './ontology/ActionLibrary.vue'
import DefinitionManager from './tools/DefinitionManager.vue'
import ValueTypeManager from './tools/ValueTypeManager.vue'
import ProjectHome from './project/ProjectHome.vue'
import ProjectBinding from './project/ProjectBinding.vue'
import ProjectValidation from './project/ProjectValidation.vue'
import ConnectionManager from './project/ConnectionManager.vue'
import QueryRuleManager from './project/QueryRuleManager.vue'
import ProjectVersion from './project/ProjectVersion.vue'
import FlowList from './flow/FlowList.vue'
import FlowEditor from './flow/FlowEditor.vue'
import ModelSettings from './tools/LlmProviders.vue'
import { decodeState, requestBody, type WorkbenchState } from './ontology/modelFormat'
import { shortcutAction } from './app/shortcuts'
import { READ_TIMEOUT_MS, SaveRequestError, isOriginRejected, localAccessUrl } from './app/http'
import { createSaver } from './app/saveCoordinator'
import type { FormGuardInstance, FormGuardAPI, FormSaveAPI, FormSaveAction } from './app/formGuard'
import { pages, normalizeView, projectViews, projectSpaceViews, flowViews, menuOntology, menuProjectOf, initialView, initialSpace, isGlobalView, settingsCategories } from './app/navigation'
import { navIcons } from './shared/icons'
import { appConfirm } from './shared/appConfirm'
import { createUndoArea } from './app/workspace'
import * as oapi from './ontology/api'
import * as papi from './project/api'
import * as fapi from './flow/api'
import { stripCatalogs } from './project/api'
import { configSignature } from './flow/flowModel'

const clone = (x: any) => JSON.parse(JSON.stringify(x))
const message = ref(''), error = ref(false), busy = ref(false)
// 初始加载链完成前，不渲染任何依赖数据的内容，否则会先闪一帧“还没有项目/创建第一个本体”这类自相矛盾的错误空态。
// G1（20260917 全局交互评审采纳）：必要读取有明确状态——loading / ready / error。
// 读取失败不能渲染成「没有本体」，也不能引导用户误建重复本体；超过 3 秒给出「仍在加载」提示。
const booting = ref(true)
const ontologyLoad = ref<'loading' | 'ready' | 'error'>('loading')
const ontologyLoadError = ref('')
const ontologyLoadFailure = ref<string>('')
const ontologySlow = ref(false)
let ontologySlowTimer: ReturnType<typeof setTimeout> | null = null
// 慢加载提示阈值：测试可注入更短时间（globalThis.__WIZ_SLOW_HINT_MS）
const ONTOLOGY_SLOW_MS = Number((globalThis as any).__WIZ_SLOW_HINT_MS) || 3000
// G2：Origin 明确被拒时给本机标准地址（按当前端口推导，不写死测试端口，也不放宽服务端校验）
const ontologyOriginBlocked = ref(false)
const localAccess = computed(() => localAccessUrl(location.hash || '#' + view.value))
const ontologyFailureHint = computed(() => ontologyOriginBlocked.value
  ? '当前页面地址未通过服务校验，本次请求已被拒绝。请改用本机标准地址访问后重试；已保存的草稿不会丢失。'
  : ontologyLoadFailure.value === 'timeout'
    ? '本地服务超过 ' + Math.round(READ_TIMEOUT_MS / 1000) + ' 秒仍未返回。请确认本地服务已启动、网络未被拦截，然后重试读取。'
    : '无法读取本体草稿与本体列表。请确认本地服务已启动后重试读取。读取失败不代表本体不存在，请勿重复新建本体。')
const ontologyFailureDetails = computed(() => ontologyLoadError.value || '')
const projectFailureTitle = computed(() => projectOriginBlocked.value ? '当前页面地址未通过服务校验' : '项目数据加载失败')
function startSlowHint() { stopSlowHint(); ontologySlowTimer = setTimeout(() => { if (ontologyLoad.value === 'loading') ontologySlow.value = true }, ONTOLOGY_SLOW_MS) }
function stopSlowHint() { if (ontologySlowTimer) { clearTimeout(ontologySlowTimer); ontologySlowTimer = null } ontologySlow.value = false }
const definitionFocusId = ref('')
function friendlyIssue(text: string) {
  const records = [...(state.value?.ontology?.['@graph'] || []), ...['functions', 'actions', 'interfaces'].flatMap(k => state.value?.workflow?.[k] || [])]
  return records.sort((a: any, b: any) => String(b['@id'] || b.id).length - String(a['@id'] || a.id).length).reduce((out: string, n: any) => { const id = n['@id'] || n.id; if (!id) return out; const kind = ({ 'owl:Class': '对象类型', 'owl:ObjectProperty': '链接类型', 'owl:DatatypeProperty': '属性', 'mg:SharedProperty': '共享属性', 'mg:ValueType': '值类型' } as any)[n['@type']] || '定义'; return out.split(id).join(`${n['rdfs:label'] || n.name || '未命名' + kind}（${id}）`) }, String(text))
}
const templateHref = ((import.meta as any).env?.BASE_URL || '/') + 'templates/ontology-import-v1.xlsx'
function notify(text: string, bad = false) { message.value = bad ? friendlyIssue(text) : text; error.value = bad }

// --- 本体区 Saver：load GET /api/state（decodeState），submit POST /api/save（requestBody 自动 encodeState，附 projectState 沿用旧格式） ---
const ontologyId = new URLSearchParams(location.search).get('ontology') || 'storage'
const ontologyQuery = '?ontology=' + encodeURIComponent(ontologyId)
const ontologyName = ref(''), ontologyList = ref<{ id: string; name: string }[]>([]), newOntologyName = ref(''), creatingOntology = ref(false), latestVersion = ref('')
const versionList = ref<any[]>([]), releases = ref<any[]>([]), preview = ref<any>(null), validationReport = ref<any>(null)
const ontologySaveErrors = ref<string[]>([])
const showOntologyDialog = ref(false)
const hasOntology = computed(() => ontologyList.value.some(o => o.id === ontologyId))

// --- 项目区 Saver：load GET /api/project-state，submit POST /api/project-save（剥 bindings.catalogs） ---
const projects = ref<any[]>([]), projectId = ref(''), refState = ref<any>(null), projectReport = ref<any>(null), migrationTodos = ref<any[]>([])
const projectStoreKey = 'wiz-last-project'
let projectLoadMeta: any = null
const projectSaveErrors = ref<string[]>([])

const ontologySaver = createSaver('本体草稿',
  async () => {
    const d = await oapi.loadStateRaw(ontologyId).catch((e: any) => { throw e instanceof SaveRequestError ? e : new SaveRequestError('读取草稿失败', 0) })
    latestVersion.value = d.latestVersion || ''
    return { state: decodeState(d.state), revision: d.revision }
  },
  async (working, baseRevision) => {
    const d = await oapi.ontologyPost('save', { state: working, revision: baseRevision, projectState: projectState.value ? stripCatalogs(projectState.value) : undefined })
    ontologySaveErrors.value = d.errors || [] // 草稿允许带错误：保存成功，状态条提示“N 项校验问题”
    return d
  })
const projectSaver = createSaver('项目草稿',
  async () => {
    const d = await papi.loadProjectStateRaw(projectId.value).catch((e: any) => { throw e instanceof SaveRequestError ? e : new SaveRequestError('读取项目失败', 0) })
    projectLoadMeta = { migrationTodos: d.migrationTodos || [], warning: d.warning || '' } // load 协议只含 state/revision，扩展字段经闭包带出
    return { state: d.state, revision: d.revision }
  },
  async (working, baseRevision) => {
    const d = await papi.saveProject(working, baseRevision) // catalogs 剥除在 project/api 统一实现
    projectSaveErrors.value = d.errors || []
    return d
  })

// --- 函数编排区 Saver：独立状态线（不携带本体/项目数据）；保存响应附带最新配置检查 ---
const flowId = ref(''), flowSaveErrors = ref<string[]>([]), flowCheck = ref<any>(null), flowCheckSig = ref('')
// 从模型设置返回编排的现场恢复通道：页签 + 节点焦点 + 模型列表刷新信号
const flowInspectorTab = ref('')
const flowFocusNode = ref<{ id: string; token: number } | null>(null)
const flowFocusSeq = ref(0)
const flowProvidersRefresh = ref(0)
const flowStoreKey = 'wiz-last-flow'
const flowSaver = createSaver('编排草稿',
  async () => {
    const d = await fapi.loadFlowStateRaw(flowId.value).catch((e: any) => { throw e instanceof SaveRequestError ? e : new SaveRequestError('读取编排失败', 0) })
    return { state: d.state, revision: d.revision }
  },
  async (working, baseRevision) => {
    const sig = configSignature(working)
    const d = await fapi.saveFlow(working, baseRevision, projectConnections.value)
    flowSaveErrors.value = d.check?.errors || []   // 配置检查失败 ≠ 草稿保存失败：状态条按“待完善”呈现
    if (d.check) { flowCheck.value = d.check; flowCheckSig.value = sig }
    return d
  })
// 顶栏检查结论的新鲜度：配置再变动后旧结论不再展示（与编辑页快照规则一致）
watch(() => [flowState.value && configSignature(flowState.value), flowCheckSig.value], () => {
  if (flowCheck.value && flowCheckSig.value !== (flowState.value ? configSignature(flowState.value) : '')) flowCheck.value = null
})

// working 即旧 state/projectState 的替代（Saver 的同一个 ref，切换/撤销/重载都替换它）。
const state = ontologySaver.working as Ref<WorkbenchState | null>
const revision = ontologySaver.revision
const projectState = projectSaver.working
const projectRevision = projectSaver.revision
const projectDirty = computed(() => projectSaver.status.value !== 'saved')
const flowState = flowSaver.working
const flowDirty = computed(() => flowSaver.status.value !== 'saved')
// 当前项目的数据连接（仅元数据，含 MySQL 与 Redis）：SQL/Redis 节点下拉与编排配置检查的引用上下文
const projectConnections = computed(() => (projectState.value?.connections?.connections || [])
  .map((c: any) => ({ id: c.id, name: c.name, engine: c.engine })))

// --- T00 表单守卫：局部表单注册/离开保护/提交助手（契约见 app/formGuard.ts） ---
// shallowRef + 不可变更新：深响应 ref 会把数组元素包成代理对象，unregister 的恒等
// 比较永远失配，守卫一旦注册就移除不掉（“正在编辑表单”残留）。
const formGuards = shallowRef<FormGuardInstance[]>([])
const leaveDialog = ref(false)
let leaveResolver: ((proceed: boolean) => void) | null = null
const formEditing = computed(() => formGuards.value.length > 0)
function dirtyGuards() { return formGuards.value.filter(g => { try { return g.isDirty() } catch { return false } }) }
// 统一离开保护：未提交表单切换菜单/两区/本体/项目时，给“继续编辑 / 放弃本次修改并离开”。
function requestLeave(): Promise<boolean> {
  if (leaveResolver) return Promise.resolve(false) // 已有一个离开确认在等待：直接拒绝新导航
  const dirty = dirtyGuards()
  if (!dirty.length) return Promise.resolve(true)
  leaveDialog.value = true
  return new Promise<boolean>(resolve => { leaveResolver = (proceed) => {
    if (proceed) {
      // App 侧收口：丢弃草稿后直接把被放弃的守卫移出注册表——组件端卸载注销若因
      // 任何原因未触发（残留 dirty 守卫会持续拦截后续导航与刷新），这里兜底清除。
      dirty.forEach(g => { try { g.discard() } catch { /* 草稿丢弃失败不阻断离开 */ } })
      formGuards.value = formGuards.value.filter(g => !dirty.includes(g))
    }
    leaveDialog.value = false; leaveResolver = null; resolve(proceed)
  } })
}
function keepEditing() { leaveResolver?.(false) }
function discardEditing() { leaveResolver?.(true) }
// 模态（离开确认/新建本体）打开时：topbar/rail/shell 三块根界面 inert，焦点移入弹窗。
const modalOpen = computed(() => leaveDialog.value || showOntologyDialog.value)
watch(modalOpen, open => {
  if (!open) return
  void nextTick(() => {
    const cards = document.querySelectorAll<HTMLElement>('.modal-backdrop .modal-card')
    const card = cards.length ? cards[cards.length - 1] : null // 取 DOM 末尾的卡（最上层弹窗）
    card?.querySelector<HTMLElement>('button, input')?.focus()
  })
})
const formGuardApi: FormGuardAPI = {
  register(guard) { if (!formGuards.value.includes(guard)) formGuards.value = [...formGuards.value, guard] },
  unregister(guard) { formGuards.value = formGuards.value.filter(g => g !== guard) },
  hasDirty: () => dirtyGuards().length > 0,
  editing: () => formEditing.value,
}
provide('form-guard', formGuardApi)
// 表单提交：flush 基线→快照→mutate→commitNow→按 status 判定；失败回滚 working 并清除后台重试隐患。
const formSaveApi: FormSaveAPI = {
  // 候选事务（20260918 撤销优化）：mutate 前开启候选；成功才以 actionLabel 登记一条历史；
  // 失败/异常/无变化取消候选，且不清 redo。actionLabel 可选（第三参），未提供时不入历史
  // （对应"未补具名的调用点"，避免出现无名步骤；逐页补齐）。
  async submitForm(area, mutate, action?: FormSaveAction) {
    const saver = area === 'project' ? projectSaver : ontologySaver
    await saver.flush()
    if (saver.status.value === 'conflict') return { ok: false, message: '草稿版本冲突，请先在顶栏处理后再保存表单' }
    if (saver.status.value === 'error') return { ok: false, message: '工作区有未保存成功的修改（' + saver.error.value + '），请先点顶栏“重试”' }
    if (saver.status.value !== 'saved') return { ok: false, message: '工作区正在保存其他修改，请稍候重试' }
    const undoArea = area === 'project' ? projectUndoArea : ontologyUndoArea
    const scopeBefore = historyScopeKey.value
    const tx = action && area === 'ontology' ? undoArea.begin() : null
    const snapshot = clone(saver.working.value)
    try {
      mutate()
      if (area === 'project') { projectEditGeneration.value++; projectReport.value = null; projectSaveErrors.value = [] }
      else { editGeneration.value++; validationReport.value = null; ontologySaveErrors.value = [] }
      await saver.commitNow()
      if (saver.status.value === 'saved') {
        if (tx && scopeBefore === historyScopeKey.value) tx.commit(action!.actionLabel, saver.working.value, action!.target ? { target: action!.target } : undefined)
        else tx?.cancel() // scope 已变（保存期间切换了页面/资产）：不入历史
        return { ok: true, message: '' }
      }
      const msg = saver.error.value || '保存失败'
      saver.working.value = snapshot
      saver.clearFailed()
      tx?.cancel() // 失败：取消候选，不清 redo
      return { ok: false, message: msg }
    } catch (e) {
      // mutate 同步异常：commitNow 尚未发起，回滚 working、取消候选并向上抛（组件按保存失败处理）
      saver.working.value = snapshot
      tx?.cancel()
      throw e
    }
  },
}
provide('form-save', formSaveApi)

const editGeneration = ref(0), projectEditGeneration = ref(0), flowEditGeneration = ref(0)
// 编辑历史（20260918 撤销范围优化）：仅白名单编辑页收集。项目映射/连接/设置等表单页
// 用保存/取消，无业务撤销——projectUndoArea 保留但已无入口，禁止继续压入项目快照。
const ontologyUndoArea = createUndoArea(state), projectUndoArea = createUndoArea(projectState), flowUndoArea = createUndoArea(flowState)
/** 编辑页白名单（需求 §3）：这些页面展示撤销/重做并接收快捷键；其余页面两者皆无。 */
const EDITABLE_VIEWS = ['objects', 'library', 'rules', 'actions', 'f-editor'] as const
const isEditableView = (v: string) => (EDITABLE_VIEWS as readonly string[]).includes(v)
/** scopeKey：本体 = ontologyId+页；编排 = flowId+f-editor。页面内切换对象/页签不换 scope。 */
const historyScopeKey = computed(() => {
  if (view.value === 'f-editor') return 'flow:' + flowId.value + ':f-editor'
  if (isEditableView(view.value)) return 'ontology:' + ontologyId + ':' + view.value
  return ''
})

// --- 导航（任务板 §2）：常量与别名在 app/navigation；view 状态与定位 ref 在此装配 ---
const view = ref(initialView())
// 空间切换器：本体空间处理本体建模，项目空间处理项目映射与函数编排；
// 编排是项目空间的菜单项，但数据不依赖选中项目（页面白名单经 projectSpaceViews 判定归属）。
const space = ref<('ontology' | 'project')>(initialSpace(location.hash, localStorage.getItem('wiz-space')))
// 侧栏收起为通用能力：由用户手动切换并记住偏好，任何页面不自动收起
const railMini = ref(localStorage.getItem('wiz-rail-mini') === '1')
// ── 全局设置中心（20260918）：左下用户区、用户菜单、来源返回 ──
const userMenuOpen = ref(false)
const userMenuWrap = ref<HTMLElement | null>(null)
const userMenuTrigger = ref<HTMLButtonElement | null>(null)
const userName = '本地用户' // 当前无应用账户 API；数据库连接用户名不能当登录用户
const hasAuthSession = false // 无认证服务：退出登录禁用并说明（接入认证后启用同一菜单项）
function toggleUserMenu() {
  if (userMenuOpen.value) { closeUserMenu(true); return }
  userMenuOpen.value = true
  void nextTick(() => userMenuWrap.value?.querySelector<HTMLElement>('[role="menuitem"]')?.focus())
}
function closeUserMenu(refocus = false) {
  if (!userMenuOpen.value) return
  userMenuOpen.value = false
  if (refocus) userMenuTrigger.value?.focus()
}
function userMenuKeydown(e: KeyboardEvent) {
  const items = [...(e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])')]
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault()
    const i = items.indexOf(document.activeElement as HTMLElement)
    items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus()
  } else if (e.key === 'Home') { e.preventDefault(); items[0]?.focus() }
  else if (e.key === 'End') { e.preventDefault(); items[items.length - 1]?.focus() }
  else if (e.key === 'Tab') closeUserMenu(false)
}
function onDocClickUserMenu(e: MouseEvent) { if (userMenuOpen.value && !userMenuWrap.value?.contains(e.target as Node)) closeUserMenu(false) }
// 进入设置：记录返回来源（进入前的 view）；已在设置内再次进入不覆盖最初来源
const settingsReturn = ref<{ view: string; node?: string; tab?: string } | null>(null)
async function openSettings(fromView?: string) {
  closeUserMenu(false)
  if (!isGlobalView(view.value)) {
    const ret: { view: string; node?: string; tab?: string } = { view: fromView || view.value }
    // 对象建模：记录当前选中对象与页签，返回时恢复（取不到则不写字段，保持协议最小化）
    if (ret.view === 'objects') {
      const node = document.querySelector('.ld-row.active')?.getAttribute('data-row')
      if (node) ret.node = node
      const tabText = document.querySelector('.ld-tabs button.active')?.textContent.trim() || ''
      if (tabText.startsWith('属性')) ret.tab = 'props'
      else if (tabText.startsWith('链接')) ret.tab = 'links'
      else if (tabText.startsWith('动作')) ret.tab = 'actions'
      else if (tabText.startsWith('规则')) ret.tab = 'rules'
    }
    settingsReturn.value = ret
  }
  await navigate('settings-models')
}
async function backToWorkspace() {
  // 返回来源页；无可靠来源（深链直达/刷新）回最近有效业务页
  const ret = settingsReturn.value
  settingsReturn.value = null
  if (ret && ret.view in pages && !isGlobalView(ret.view)) {
    if (projectSpaceViews.includes(ret.view)) { space.value = 'project'; try { localStorage.setItem('wiz-space', 'project') } catch {} }
    else { space.value = 'ontology'; try { localStorage.setItem('wiz-space', 'ontology') } catch {} }
    if (ret.view === 'objects') {
      // 对象建模：缓存选中对象与页签，经 focus 参数恢复（组件卸载丢失内存状态）
      if (ret.node) { propertyFocusType.value = ret.node }
      if (ret.tab) objectDetailTab.value = ret.tab
      await navigate('objects', ret.node ? { type: ret.node, tab: ret.tab } : (ret.tab ? { tab: ret.tab } : undefined))
      return
    }
    await navigate(ret.view)
    if (ret.view === 'f-editor') {
      // 恢复节点与页签（保留原 providerId/“使用默认”），并刷新可用模型列表
      if (ret.tab) flowInspectorTab.value = ret.tab
      if (ret.node) flowFocusNode.value = { id: ret.node, token: ++flowFocusSeq.value }
      flowProvidersRefresh.value++
    }
    return
  }
  await navigate(lastOntologyView.value || 'o-home')
}
async function logoutClick() {
  // 无真实会话：禁用项不可达；兜底提示，不模拟注销、不清业务数据
  if (!hasAuthSession) { notify('当前为本地模式，无登录会话；退出登录不可用。'); return }
}
function toggleRail() {
  railMini.value = !railMini.value
  try { localStorage.setItem('wiz-rail-mini', railMini.value ? '1' : '0') } catch {}
}
const lastOntologyView = ref('objects')
const area = computed(() => space.value)
const knowledgeFocus = ref(''), propertyFocusType = ref(''), propertyFocusId = ref(''), bindingFocusType = ref(''), contractFocusId = ref(''), implFocus = ref('')
const objectDetailTab = ref('props') // 对象建模深链页签（动作库反向引用 → 对象动作页签）
watch(space, s => { try { localStorage.setItem('wiz-space', s) } catch {}
  if (s === 'project') { if (!projectSpaceViews.includes(view.value)) navigate('p-home') }
  else if (projectSpaceViews.includes(view.value)) navigate(lastOntologyView.value) })
async function switchSpace(s: 'ontology' | 'project') { if (s === space.value) return; if (!(await requestLeave())) return; space.value = s
  // 进入项目区：侧栏需要项目列表；项目状态由 navigate 的主路径按需加载（等待中仍可切回本体）
  if (s === 'project') void ensureProjectList() }

async function navigate(v: string, focus?: { type?: string; property?: string; impl?: string; connection?: string; contract?: string; definition?: string; tab?: string }) {
  v = normalizeView(v); if (!(v in pages)) return
  if (v === view.value && !focus) { /* 同页重入不触发离开保护 */ }
  else if (!(await requestLeave())) return
  // R2：依赖项目的页面按需准备项目上下文；菜单、显式 hash 深链、跨区入口都走这一条路径。
  // p-home 无项目也要能进（显示创建/选择入口），且不等挂起的读取——概览页自身会显示等待/失败态，
  // 否则项目读取挂起时用户会停在原页面看不到任何项目区反馈。
  if (v === 'p-home') { void ensureProjectContext() }
  else if (projectViews.includes(v)) {
    const ready = await ensureProjectContext()
    if (!ready && !projectState.value) {
      const reason = projectListState.value === 'error' ? '项目列表加载失败：' + projectListError.value : projectLoadError.value ? '项目加载失败：' + projectLoadError.value : '请先在项目概览中选择或新建项目'
      return notify(reason, true)
    }
  }
  else if (projectSpaceViews.includes(v)) { void ensureProjectList() } // 函数编排等项目空间页面不依赖项目，但侧栏列表需后台加载（否则一直停在“正在加载项目”）
  if (v === 'f-editor' && !flowState.value) return notify('请先在编排列表中选择或新建编排', true)
  if (focus?.type) { propertyFocusType.value = focus.type; bindingFocusType.value = focus.type }
  if (focus?.property) propertyFocusId.value = focus.property
  if (focus?.impl) implFocus.value = focus.impl
  if (focus?.contract) contractFocusId.value = focus.contract
  if (focus?.definition) definitionFocusId.value = focus.definition
  if (focus?.tab) objectDetailTab.value = focus.tab
  // 「前往模型设置」携带编排上下文：记录节点与页签供返回恢复（来源取当前页；不写 lastOntologyView）
  if (isGlobalView(v) && focus) {
    settingsReturn.value = { view: view.value, node: focus.definition || '', tab: typeof focus.tab === 'string' ? focus.tab : '' }
  }
  // 全局设置页：独立于两区——不改写 lastOntologyView、不切 space、不加载/校验业务数据
  if (isGlobalView(v)) {
    if (view.value !== v) { message.value = ''; error.value = false }
    view.value = v; history.replaceState(null, '', '#' + v)
    return
  }
  if (!projectViews.includes(v) && !flowViews.includes(v)) lastOntologyView.value = v
  if (view.value !== v) { message.value = ''; error.value = false }
  view.value = v; history.replaceState(null, '', '#' + v)
  // 跨区入口（如本体概览“进入项目映射”）同步侧栏工作区状态；先定 view 再切 space，避免 watch 递归导航
  if (projectSpaceViews.includes(v) && space.value !== 'project') { space.value = 'project'; try { localStorage.setItem('wiz-space', 'project') } catch {} }
  if (!projectSpaceViews.includes(v) && space.value !== 'ontology') { space.value = 'ontology'; try { localStorage.setItem('wiz-space', 'ontology') } catch {} }
  if (v === 'instances') await runPreview()
  if (v === 'o-release') { await loadVersionList(); await loadReleases(); await checkWorkflow() }
  if (v === 'p-release') await validateProject(false)
}
function hashChanged() { navigate(window.location.hash.slice(1)) }
watch(view, () => closeUserMenu(false))
// scope 生命周期（需求 §4.2）：实际成功离开编辑页 / 切换本体 / 切换编排 / 重载 → 清空该范围历史。
// watch(view) 只在实际切换成功后触发（导航取消不会改 view），符合「实际离开时结束会话」。
watch([view, historyScopeKey], ([nv], [ov]) => {
  if (nv === ov) return
  const wasEditable = isEditableView(String(ov))
  const nowEditable = isEditableView(String(nv))
  // 离开编辑页：清对应区域；编辑页之间互切也各自重置（objects→library 等不同 scope）
  if (wasEditable && ov !== 'f-editor' && (!nowEditable || nv !== ov)) ontologyUndoArea.reset()
  // 编排内部不清（同 flowId 同 scope）；f-home↔f-editor 保留（同一编排的列表/编辑切换）
})
// 侧栏菜单：本体建设 5 项 + 更多工具；项目映射 5 项（未选项目时只显示项目概览）。
const menuProject = computed<Record<string, string>>(() => menuProjectOf(!!projectState.value))
// 项目页眉 crumb 引用的本体名：按项目引用的本体 id 回查列表，回退显示 id。
const refOntologyLabel = computed(() => { const oid = projectState.value?.ontologyId || ''; return ontologyList.value.find(o => o.id === oid)?.name || oid || '—' })
// 项目区加载状态（R2）：「等待/失败」与「确实没有项目」分开反馈，不在懒加载未完成时抢先显示无项目空态。
const projectAreaFailed = computed(() => projectViews.includes(view.value) && !projectState.value && (projectListState.value === 'error' || !!projectLoadError.value))
// 等待态与失败态互斥：失败时显示错误面板与重试，不能继续显示骨架
const projectAreaWaiting = computed(() => projectViews.includes(view.value) && !projectState.value && !projectAreaFailed.value
  && (projectListState.value === 'idle' || projectListState.value === 'loading' || projectLoading.value))
// 顶栏面包屑文案（展示型 computed）：沿用原 header crumb 三分支逻辑，随单层顶栏搬迁，页面标题另见 pages[view]。
const crumbPath = computed(() => onGlobalView.value ? '设置 / ' + (pages[view.value] || '设置') : flowViews.includes(view.value) ? `${flowState.value?.name || '未选择编排'} / 函数编排 · 项目映射` : space.value === 'project' ? `${projectState.value?.name || '未选择项目'} / 项目配置 · 引用 ${refOntologyLabel.value} ${projectState.value?.ontologyVersion || ''}` : `${ontologyName.value || '未创建本体'} / 抽象定义`)
// 旧调用点迁移：openFunction/openProperties/openBindings/showKnowledge/showGraph → 新 view 名 + focus。
function openFunction(id: string) { contractFocusId.value = id; navigate('contracts') }
function openProperties(type: string, id = '') { propertyFocusType.value = type; propertyFocusId.value = id; navigate('objects') }
function openBindings(type: string) { bindingFocusType.value = type; navigate(projectState.value ? 'binding' : 'p-home') }
function showKnowledge(id = '') { knowledgeFocus.value = id; navigate('knowledge') }
function showGraph(id: string) { navigate('objects', { type: id }) }

// --- 变更入口：组件 emit('changed') → touch() 自动保存（900ms 合并）；撤销/重做 → commitNow() 立即产生新修订 ---
function changed() { editGeneration.value++; ontologySaveErrors.value = []; validationReport.value = null; ontologySaver.touch(); if (message.value) { message.value = '内容已修改，之前的操作结果已过期；请重新校验。'; error.value = false } }
function projectChanged() { projectEditGeneration.value++; projectSaveErrors.value = []; projectReport.value = null; projectValidateError.value = ''; projectSaver.touch() }
function flowChanged() { flowEditGeneration.value++; flowSaveErrors.value = []; flowSaver.touch() }
// counts() 读取普通数组不具响应性：依赖 editGeneration（每次 changed 递增）触发重算，沿用既有约定。
// 编排页面位于项目空间：按当前视图（而非 space）路由到对应撤销栈与 Saver。
const onFlowView = computed(() => flowViews.includes(view.value))
// 撤销按钮可用性：仅白名单编辑页展示；保存失败/冲突未处理时禁用（走顶栏既有入口）
const historyUsable = computed(() => isEditableView(view.value) && !formEditing.value && !undoBusy.value)
const undoCount = computed(() => {
  if (!historyUsable.value) return 0
  void (onFlowView.value ? flowEditGeneration.value : editGeneration.value)
  return onFlowView.value ? flowUndoArea.counts.value[0] : ontologyUndoArea.counts.value[0]
})
const redoCount = computed(() => {
  if (!historyUsable.value) return 0
  void (onFlowView.value ? flowEditGeneration.value : editGeneration.value)
  return onFlowView.value ? flowUndoArea.counts.value[1] : ontologyUndoArea.counts.value[1]
})
const canUndo = computed(() => undoCount.value > 0)
const canRedo = computed(() => redoCount.value > 0)
const undoLabel = computed(() => onFlowView.value ? flowUndoArea.peekUndoLabel() : ontologyUndoArea.peekUndoLabel())
const redoLabel = computed(() => onFlowView.value ? flowUndoArea.peekRedoLabel() : ontologyUndoArea.peekRedoLabel())
// pushUndo(actionLabel?, target?, mergeKey?)：具名压栈（编辑器 emit('before-change') 的既有调用点
// 不带参数时仍可用，但建议逐页补动作名）。合并 key 相同的连续输入只保留一步。
function pushUndo(actionLabel?: string | { actionLabel: string; target?: { kind: string; id: string; ownerId?: string }; mergeKey?: string }, target?: { kind: string; id: string; ownerId?: string }, mergeKey?: string) {
  if (typeof actionLabel === 'object' && actionLabel) {
    ontologyUndoArea.push(String(actionLabel.actionLabel), undefined, { target: actionLabel.target, mergeKey: actionLabel.mergeKey })
    return
  }
  const label = typeof actionLabel === 'string' ? actionLabel : '编辑本体草稿'
  ontologyUndoArea.push(label, undefined, { target, mergeKey })
}
function pushProjectUndo() { /* 项目映射/连接等表单页无业务撤销（20260918）：显式保存/取消 */ }
function pushFlowUndo(actionLabel?: string | { actionLabel: string; target?: { kind: string; id: string }; mergeKey?: string }, target?: { kind: string; id: string }, mergeKey?: string) {
  if (typeof actionLabel === 'object' && actionLabel) {
    flowUndoArea.push(String(actionLabel.actionLabel), undefined, { target: actionLabel.target, mergeKey: actionLabel.mergeKey })
    return
  }
  const label = typeof actionLabel === 'string' ? actionLabel : '编辑编排'
  flowUndoArea.push(label, undefined, { target, mergeKey })
}
// 撤销/重做主流程（需求 §7.1）：① 结束未结算字段事务 ② flush 基线（失败不动栈）
// ③ 应用目标快照、指针移动一次、检查/结果标过期 ④ commitNow 串行保存。
// 保存失败：保留恢复内容与历史位置，交由顶栏重试/重载（§7.2）；重试不再移动历史。
async function applyHistory(kind: 'undo' | 'redo') {
  if (undoBusy.value || !isEditableView(view.value) || formEditing.value) return
  const isFlow = view.value === 'f-editor'
  const area = isFlow ? flowUndoArea : ontologyUndoArea
  const scopeBefore = historyScopeKey.value
  const label = kind === 'undo' ? area.peekUndoLabel() : area.peekRedoLabel()
  undoBusy.value = true
  try {
    if (isFlow) { await flowSaver.flush() }
    else { await ontologySaver.flush() }
    const saver = isFlow ? flowSaver : ontologySaver
    if (saver.status.value !== 'saved') return // 基线保存失败/冲突：历史位置不动
    if (historyScopeKey.value !== scopeBefore) return // 等待期间离开了编辑页
    const restored = kind === 'undo' ? area.undo() : area.redo()
    if (!restored) return
    if (isFlow) { flowState.value = restored; flowCheck.value = null }
    else { state.value = restored; validationReport.value = null }
    editGeneration.value++; isFlow ? flowEditGeneration.value++ : 0
    notify(kind === 'undo' ? '已撤销：' + label + '，正在保存…' : '已重做：' + label + '，正在保存…')
    await saver.commitNow()
    if (saver.status.value === 'saved') notify(kind === 'undo' ? '已撤销：' + label + '，已保存' : '已重做：' + label + '，已保存')
    else notify('已在当前页面' + (kind === 'undo' ? '撤销' : '重做') + '，但保存未成功／结果未确认；可用顶栏重试或放弃重载', true)
  } finally { undoBusy.value = false }
}
function undo() { void applyHistory('undo') }
function redo() { void applyHistory('redo') }
function undoProject() { /* 项目映射/连接无业务撤销（20260918） */ }
function redoProject() { /* 同上 */ }
function undoFlow() { undo() }
function redoFlow() { redo() }

// --- 函数编排：打开/复制/删除（编排列表页触发；切换前 flush 由 Saver 状态兜底） ---
async function openFlow(id: string) {
  if (!id || busy.value) return
  busy.value = true
  try {
    if (flowState.value) { await flowSaver.flush(); if (flowSaver.status.value !== 'saved' && !(await appConfirm({ message: '当前编排自动保存未成功，切换将丢弃未保存修改。继续？', danger: true }))) return }
    flowId.value = id
    await flowSaver.reload()
    try { localStorage.setItem(flowStoreKey, id) } catch {}
    flowUndoArea.reset(); flowCheck.value = null; flowSaveErrors.value = []
    await navigate('f-editor')
  } catch (e) { notify((e as Error).message, true) } finally { busy.value = false }
}
async function onFlowCreated(created: { id: string }) { await openFlow(created.id) }
function onFlowDeleted(id: string) {
  if (flowId.value === id) { flowId.value = ''; flowState.value = null; flowUndoArea.reset(); flowCheck.value = null; flowSaveErrors.value = []; if (view.value === 'f-editor') navigate('f-home') }
}

// --- 只读校验/预览不走保存队列（直接带当前 working 与 revision） ---
// 只读校验/预览不走保存队列：api 层统一请求编码与错误解析（含 409 currentRevision）。
const api = (path: string, extra: any = {}) => oapi.ontologyPost(path, { state: state.value, revision: revision.value, projectState: projectState.value ? stripCatalogs(projectState.value) : undefined, ...extra })
const projectApi = (path: string, extra: any = {}) => papi.projectPost(path, { state: projectState.value, revision: projectRevision.value, ...extra })
async function checkWorkflow() { busy.value = true; const generation = editGeneration.value; try { const report = await api('workflow-check'); if (generation === editGeneration.value) validationReport.value = report; else notify('检查期间内容已修改，请重新检查。') } catch (e) { notify((e as Error).message, true) } finally { busy.value = false } }
async function validateModel() { if (busy.value) return; busy.value = true; const generation = editGeneration.value; try { const d = await api('validate'); if (generation !== editGeneration.value) return notify('校验期间内容已修改，请重新校验。'); notify(d.errors.length ? d.errors.join('；') : '校验通过', !!d.errors.length) } catch (e) { notify((e as Error).message, true) } finally { busy.value = false } }
async function runPreview() { busy.value = true; try { preview.value = await api('preview'); if (preview.value.errors.length) notify(preview.value.errors.join('；'), true) } catch (e) { notify((e as Error).message, true) } finally { busy.value = false } }
async function loadVersionList() { try { versionList.value = [...(await oapi.listVersions(ontologyId)).items].reverse() } catch (e) { notify((e as Error).message, true) } }
async function loadReleases() { try { releases.value = await oapi.listReleases(ontologyId) } catch (e) { notify((e as Error).message, true) } }
// 数据函数保留在此（P09 的发布页自行调用/迁移）；App 侧不再挂载发布与版本页面。
async function exportZip() { try { const blob = await oapi.exportDraftBlob({ state: state.value }); const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = ontologyName.value + '-草稿.zip'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) } catch (e) { notify((e as Error).message, true) } }
async function restoreRelease(releaseName: string) { if (busy.value || !(await appConfirm({ message: '将此快照恢复为当前本体的草稿？现有已保存草稿会自动备份，其他本体不受影响。' }))) return; busy.value = true; try { await ontologySaver.flush(); await oapi.restoreRelease({ state: state.value, revision: revision.value, release: releaseName }); location.reload() } catch (e) { notify((e as Error).message, true); busy.value = false } }

// --- 本体切换/新建：先 flush，未落盘且用户不确认则中止（beforeunload 由 Saver 拦截兜底） ---
async function guardUnsaved(saver: typeof ontologySaver) { await saver.flush(); if (saver.status.value !== 'saved' && !(await appConfirm({ message: '有未保存的修改自动保存未成功，继续将丢弃这些修改。确定继续？', danger: true }))) throw new Error('cancelled') }
function enterOntology(id: string) { location.assign('?ontology=' + encodeURIComponent(id) + '#objects') }
async function openReferencedOntology() {
  const id = projectState.value?.ontologyId
  if (!id || busy.value) return
  if (id === ontologyId) { await navigate('o-release'); return }
  if (!(await requestLeave())) return
  try {
    await projectSaver.flush()
    await ontologySaver.flush()
    if (projectSaver.status.value !== 'saved' || ontologySaver.status.value !== 'saved') {
      notify('请先处理保存失败，再进入本体发布页。', true); return
    }
    location.assign('?ontology=' + encodeURIComponent(id) + '#o-release')
  } catch (e) { notify((e as Error).message, true) }
}
async function switchOntology(id: string) { if (id === ontologyId || busy.value) return; if (!(await requestLeave())) return; busy.value = true; try { await guardUnsaved(ontologySaver); enterOntology(id) } catch (e) { if ((e as Error).message !== 'cancelled') notify((e as Error).message, true); busy.value = false } }
async function createOntology() { if (busy.value || !newOntologyName.value.trim()) return; busy.value = true; try { await guardUnsaved(ontologySaver); const d = await oapi.createOntology(newOntologyName.value.trim()); enterOntology(d.id) } catch (e) { if ((e as Error).message !== 'cancelled') notify((e as Error).message, true); busy.value = false } }
async function createOntologyFromDialog() { await createOntology(); showOntologyDialog.value = false }
async function loadOntologies() { ontologyList.value = (await oapi.listOntologies()).items }

// --- 项目加载/切换/引用/升级：切换前 flush（失败 confirm），成功后经 Saver.reload 采纳服务端最新 ---
// R2：项目数据按需加载。本体页面不再等待项目列表/完整项目状态/项目引用版本，只有确实进入
// 项目区（菜单、显式 hash 深链、切区、恢复记忆项目）时才请求；未加载 ≠ 没有项目。
const ontologyOptions = computed(() => ontologyList.value.map(o => ({ value: o.id, label: o.name })))
// 侧栏「当前项目」选项：列表为准，缺失时用当前项目状态兜底（新建后列表刷新竞态下也显示名称而非 id）
const projectOptions = computed(() => {
  const items = projects.value.map(p => ({ value: p.id, label: p.name }))
  const cur = projectState.value
  if (cur?.projectId && !items.some(p => p.value === cur.projectId)) items.push({ value: cur.projectId, label: cur.name || cur.projectId })
  return items
})
const projectValidateError = ref('')   // G2：配置校验请求自身失败（不是业务问题，也不是校验通过）
const projectListState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const projectOriginBlocked = ref(false)
const projectListError = ref('')
const projectLoadError = ref('')
const projectLoading = ref(false)
let projectListJob: Promise<boolean> | null = null
async function ensureProjectList(): Promise<boolean> {
  if (projectListState.value === 'ready') return true
  if (projectListJob) return projectListJob // 并发去重：同一份列表只请求一次
  projectListState.value = 'loading'; projectListError.value = ''
  projectListJob = (async () => {
    try { projects.value = (await papi.listProjects()).items; projectListState.value = 'ready'; projectOriginBlocked.value = false; return true }
    catch (e) { projectListState.value = 'error'; projectListError.value = (e as Error).message; projectOriginBlocked.value = isOriginRejected(e); return false }
    finally { projectListJob = null }
  })()
  return projectListJob
}
// 失败重试：重新取列表，并补一次项目状态加载（首次失败可能发生在任一步）。
// 侧栏「＋ 新建」：先落到项目概览（承担离开保护与项目上下文准备），再转发信号打开新建弹窗
const projectCreateSignal = ref(0)
async function newProjectFromSidebar() { if (view.value !== 'p-home') await navigate('p-home'); projectCreateSignal.value++ }
async function retryProjectContext() {
  if (projectListState.value === 'error' || projectListState.value === 'idle') { projectListState.value = 'idle'; projectListError.value = '' }
  projectLoadError.value = ''
  if (!(await ensureProjectList())) return notify('项目列表加载失败：' + projectListError.value, true)
  if (!projectState.value) {
    const ok = await ensureProjectContext()
    if (!ok) notify(projectLoadError.value ? '项目加载失败：' + projectLoadError.value : '还没有项目，可在项目概览中新建。', !!projectLoadError.value)
  }
}
// 返回项目引用版本的已发布本体定义（只读校验/绑定用）；返回 null 表示读取失败。
async function loadRefState(oid: string, version: string) {
  refState.value = null
  try { return decodeState((await oapi.versionStateRaw(oid, version)).state) }
  catch (e) { notify((e as Error).message, true); return null }
}
// 项目加载：序号保证旧响应不覆盖新项目；同一项目并发加载去重（A7）。
// 不用全局 busy 拦截：正在加载 A 时用户改选 B 必须能真的切过去（A 的迟到响应由序号丢弃）。
let projectLoadSeq = 0
let projectJob: { id: string; promise: Promise<boolean> } | null = null
async function loadProject(id: string, force = false): Promise<boolean> {
  if (!id) return false
  if (projectJob && projectJob.id === id) return projectJob.promise
  if (!force && !(await requestLeave())) return false
  const seq = ++projectLoadSeq, previous = projectId.value
  const promise = (async () => {
    busy.value = true; projectLoading.value = true
    try {
      if (!force && projectState.value) { await projectSaver.flush(); if (projectSaver.status.value !== 'saved' && !(await appConfirm({ message: '项目草稿自动保存未成功，切换将丢弃未保存修改。继续？', danger: true }))) return false }
      if (seq !== projectLoadSeq) return false // 期间已有更新的加载：本次作废
      projectId.value = id
      await projectSaver.reload() // Saver 内部 epoch 丢弃在途旧响应
      if (seq !== projectLoadSeq) return false // 旧响应不得覆盖后发起的新项目
      try { localStorage.setItem(projectStoreKey, id) } catch {}
      const d: any = projectState.value
      if (d.ontologyId && d.ontologyVersion) {
        migrationTodos.value = projectLoadMeta?.migrationTodos || []
        if (projectLoadMeta?.warning) notify(projectLoadMeta.warning, true)
        const nextRef = await loadRefState(d.ontologyId, d.ontologyVersion)
        if (seq !== projectLoadSeq) return false
        refState.value = nextRef
      } else { migrationTodos.value = []; refState.value = null }
      projectUndoArea.reset(); projectReport.value = null; projectLoadError.value = ''
      return true
    } catch (e) {
      if (seq === projectLoadSeq) { projectId.value = previous; projectLoadError.value = (e as Error).message; projectOriginBlocked.value = isOriginRejected(e); notify((e as Error).message, true) }
      return false
    } finally { if (seq === projectLoadSeq) { busy.value = false; projectLoading.value = false } }
  })()
  projectJob = { id, promise }
  void promise.finally(() => { if (projectJob?.promise === promise) projectJob = null })
  return promise
}
// 进入依赖项目的页面时补齐项目上下文：列表 → 有效的记忆项目（否则沿用默认选择）→ 无项目时交给页面显示创建入口。
async function ensureProjectContext(): Promise<boolean> {
  if (!(await ensureProjectList())) return false
  if (projectState.value && projectId.value) return true
  const stored = localStorage.getItem(projectStoreKey)
  const initial = stored && projects.value.some(p => p.id === stored) ? stored : (projects.value[0]?.id || '')
  if (!initial) return false
  return loadProject(initial, true)
}
async function createProjectDone(id: string) {
  // 先刷新列表再加载；加载完成后再刷一次（项目状态本身也带着最新名称，以后端为准）
  await ensureProjectList()
  await loadProject(id, true)
  await ensureProjectList()
  await navigate('binding')
}
async function applyProjectReference(ref: { ontology: string; version: string }) {
  if (!projectState.value) return
  pushProjectUndo(); projectState.value.ontologyId = ref.ontology; projectState.value.ontologyVersion = ref.version; projectEditGeneration.value++
  await projectSaver.commitNow()
  const pid = projectState.value.projectId; await loadProject(pid, true); await navigate('binding'); notify('已绑定本体版本，可开始对象绑定')
}
async function upgradeProject(target: string) {
  if (!projectState.value) return
  pushProjectUndo(); projectState.value.ontologyVersion = target; projectEditGeneration.value++; projectReport.value = null
  refState.value = await loadRefState(projectState.value.ontologyId, target)
  await projectSaver.commitNow()
}
// 引用页等待真实保存结果；复用表单事务，失败恢复原引用而不是提前显示成功。
async function saveProjectReference(target: { ontology: string; version: string }) {
  if (!projectState.value) throw new Error('请先选择项目')
  const nextRef = decodeState((await oapi.versionStateRaw(target.ontology, target.version)).state)
  const result = await formSaveApi.submitForm('project', () => {
    projectState.value.ontologyId = target.ontology
    projectState.value.ontologyVersion = target.version
  })
  if (!result.ok) throw new Error(result.message)
  refState.value = nextRef
  migrationTodos.value = []
}
async function validateProject(showNav = true) {
  if (busy.value || !projectState.value) return
  busy.value = true; projectValidateError.value = ''
  try {
    const d = await projectApi('project-validate')
    projectReport.value = d
    if (showNav && view.value !== 'p-release') await navigate('p-release')
    notify(d.errors.length ? `项目校验发现 ${d.errors.length} 个问题` : (d.warnings.length ? '配置校验通过（未执行验证），另有 ' + d.warnings.length + ' 条提示' : '配置校验通过（未执行验证）'), !!d.errors.length)
  } catch (e) {
    // G2：校验请求失败 ≠ 业务问题，也不保留「本次校验通过」假象；错误在校验页持续可见（不重复弹全局红条）
    projectReport.value = null
    projectValidateError.value = (e as Error).message || '未能完成校验'
    if (view.value !== 'p-release') await navigate('p-release')
  } finally { busy.value = false }
}
// 发布页回调：发布对话框与 API 都在页面组件内完成，App 只刷新相关数据。
async function onOntologyPublished() { await loadVersionList(); await loadReleases() }
async function onProjectPublished() { if (!projectState.value) return; await projectSaver.flush(); await loadProject(projectState.value.projectId, true) }

// --- 顶栏状态条（按当前视图路由：函数编排页面用 flowSaver，其余按空间） ---
const onGlobalView = computed(() => isGlobalView(view.value))
// ── 撤销/重做统一入口（20260918）：按钮与快捷键调用同一组 canUndo/canRedo/undoRedo ──
const undoBusy = ref(false)
const undoDisabledReason = computed(() => {
  if (!isEditableView(view.value)) return '当前页面没有可撤销的操作'
  if (formEditing.value) return '请先保存或取消当前表单'
  if (undoBusy.value) return '正在处理撤销／重做'
  if (space.value === 'project' && projectSaver.status.value === 'saving') return '正在保存，请稍候'
  if (view.value === 'f-editor' && flowSaver.status.value === 'saving') return '正在保存，请稍候'
  if (!onFlowView.value && ontologySaver.status.value === 'saving') return '正在保存，请稍候'
  if (!canUndo.value && !canRedo.value) return '当前页面没有可撤销的操作'
  return ''
})
const activeSaver = computed(() => onGlobalView.value ? null : onFlowView.value ? flowSaver : space.value === 'project' ? projectSaver : ontologySaver)
const activeErrors = computed(() => onGlobalView.value ? [] as string[] : onFlowView.value ? flowSaveErrors.value : space.value === 'project' ? projectSaveErrors.value : ontologySaveErrors.value)
const activeReleaseView = computed(() => onGlobalView.value ? view.value : onFlowView.value ? 'f-editor' : space.value === 'project' ? 'p-release' : 'o-release')
// G3（20260917 全局交互评审采纳）：顶栏文案由真实 guard dirty 与当前区域 Saver 状态派生。
// 打开表单 ≠ 有未保存修改；有未提交输入时不再并列一个无范围的绿色「已保存」；
// 保存失败／冲突优先，不被更轻的表单提示覆盖。
const formDirty = computed(() => dirtyGuards().length > 0)
// 写操作超时不自动中断（G1 §2.2）：只补充「响应较慢，正在等待结果」，不提前认定失败
const saveSlow = ref(false)
let saveSlowTimer: ReturnType<typeof setTimeout> | null = null
const SAVE_SLOW_MS = 15000
const saveState = computed<{ kind: string; text: string; hint: string }>(() => {
  if (onGlobalView.value) {
    if (formEditing.value) return { kind: 'editing', text: '编辑表单', hint: '设置表单已打开；未保存的修改在关闭前确认' }
    return { kind: 'saved', text: '设置', hint: '模型设置为部署级配置，不属于本体/项目草稿' }
  }
  const s = activeSaver.value!.status.value
  if (s === 'saving') return { kind: 'saving', text: saveSlow.value ? '保存中…响应较慢，正在等待结果' : '保存中…请勿重复操作', hint: '正在写入服务端，完成前请勿重复提交；超过 15 秒仍未返回也不代表失败' }
  if (s === 'error') return { kind: 'error', text: activeSaver.value.unknownOutcome.value ? '未收到保存结果，暂不能确认是否成功' : '保存失败', hint: activeSaver.value.error.value }
  if (s === 'conflict') return { kind: 'conflict', text: '版本冲突', hint: activeSaver.value.error.value }
  if (s === 'dirty') return { kind: 'dirty', text: formDirty.value ? '工作区草稿有未保存修改；表单修改尚未提交' : '未保存修改', hint: '修改会自动保存；表单里的输入需要点保存才提交' }
  if (formDirty.value) return { kind: 'form', text: '表单有未保存修改', hint: '表单内容还没提交；保存成功前不会写入草稿' }
  if (formEditing.value) return { kind: 'editing', text: '编辑表单 · 草稿已保存', hint: '表单已打开但没有修改；草稿内容已保存' }
  return { kind: 'saved', text: '已保存', hint: '当前草稿已保存' }
})
watch(() => activeSaver.value.status.value, s => {
  if (saveSlowTimer) { clearTimeout(saveSlowTimer); saveSlowTimer = null }
  saveSlow.value = false
  if (s === 'saving') saveSlowTimer = setTimeout(() => { if (activeSaver.value.status.value === 'saving') saveSlow.value = true }, SAVE_SLOW_MS)
})
async function retrySave() { try { await activeSaver.value.retry() } catch (e) { notify((e as Error).message, true) } }
async function discardReload() { if (!(await appConfirm({ message: '放弃本地修改，重新加载服务端最新草稿？', danger: true, confirmLabel: '放弃重载' }))) return; try { await activeSaver.value.reload(); notify('已重新加载服务端草稿') } catch (e) { notify((e as Error).message, true) } }
async function retryLocal() { if (!(await appConfirm({ message: '以当前屏幕内容重试保存：将覆盖服务端较新的草稿。确定？', danger: true, confirmLabel: '覆盖保存' }))) return; try { await activeSaver.value.retryFromLocal(); notify(activeSaver.value.status.value === 'saved' ? '已按当前内容保存' : '仍未保存，请查看保存状态', activeSaver.value.status.value !== 'saved') } catch (e) { notify((e as Error).message, true) } }
// provide commit-now：表单“保存”按钮在 mutate 之后调用（协议 §7）；按当前视图/空间路由到对应 Saver。
function commitCurrent() { void (onFlowView.value ? flowSaver.commitNow() : area.value === 'project' ? projectSaver.commitNow() : ontologySaver.commitNow()) }
provide('commit-now', () => onFlowView.value ? flowSaver.commitNow() : area.value === 'project' ? projectSaver.commitNow() : ontologySaver.commitNow())

function keydown(e: KeyboardEvent) { const action = shortcutAction(e, { modal: showOntologyDialog.value || leaveDialog.value, graph: false }); if (!action) return
  const target = e.target as HTMLElement | null
  if ((action === 'undo' || action === 'redo') && target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable)) return // 文本撤销归输入控件
  e.preventDefault(); ({ save: commitCurrent, undo: () => undo(), redo: () => redo(), escape: () => { showOntologyDialog.value = false; if (leaveResolver) keepEditing() } } as any)[action]?.() }
const areaLabel = computed(() => area.value === 'project' ? '项目空间' : '本体空间')
// 局部表单未提交时刷新/关闭也保护（工作区草稿由各 Saver 的 beforeunload 保护）
function unloadGuard(e: BeforeUnloadEvent) { if (dirtyGuards().length) { e.preventDefault(); e.returnValue = '' } }

onMounted(async () => {
  bindGlobals()
  document.addEventListener('click', onDocClickUserMenu)
  await loadOntologyData()
  normalizeLanding()
  booting.value = false
  await settleInitialView()
})
// 按记忆的工作空间落位（只用本地状态，不等项目数据）
function normalizeLanding() {
  if (space.value === 'project' && !projectSpaceViews.includes(view.value)) { view.value = 'p-home'; history.replaceState(null, '', '#p-home') }
  else if (space.value === 'ontology' && projectSpaceViews.includes(view.value)) { view.value = hasOntology.value ? lastOntologyView.value : 'o-home'; history.replaceState(null, '', '#' + view.value) }
}
// 全局监听器只注册一次：重试读取不得重复注册（否则 keydown/hashchange 会成倍触发）。
let globalsBound = false
function bindGlobals() {
  if (globalsBound) return
  globalsBound = true
  window.addEventListener('keydown', keydown); window.addEventListener('hashchange', hashChanged); window.addEventListener('beforeunload', unloadGuard)
}
// 本体必要读取（草稿 + 本体列表）：失败进 error 态并可重试；不渲染成「没有本体」。
async function loadOntologyData() {
  ontologyLoad.value = 'loading'; ontologyLoadError.value = ''; ontologyLoadFailure.value = ''
  startSlowHint()
  try {
    // 草稿与本体列表都是必要读取：任一失败都进 error 态，但不能因为一个失败就完全不读另一个
    // （否则会把「读取失败」渲染成「没有本体」）。
    const [draft, list] = await Promise.allSettled([ontologySaver.reload(), loadOntologies()])
    const failed = [draft, list].find(r => r.status === 'rejected') as PromiseRejectedResult | undefined
    const hit = ontologyList.value.find(o => o.id === ontologyId)
    ontologyName.value = hit?.name || ''
    document.title = (ontologyName.value || '本体工作台') + ' · 本体工作台'
    if (failed) {
      const e: any = failed.reason
      ontologyLoad.value = 'error'
      ontologyLoadFailure.value = String(e?.failure || '')
      ontologyOriginBlocked.value = isOriginRejected(e)
      ontologyLoadError.value = (e as Error)?.message || '读取失败'
    } else ontologyLoad.value = 'ready'
  } finally { stopSlowHint() }
}
// 重试：只重新读取本体数据与必要的项目上下文，不刷新浏览器、不丢弃已编辑表单、不重复注册监听器。
async function retryOntologyLoad() {
  if (ontologyLoad.value === 'loading') return
  booting.value = true
  message.value = ''
  await loadOntologyData()
  normalizeLanding()
  booting.value = false
  await settleInitialView()
}
// 启动收尾（R2）：只有当前页面确实需要项目数据时才加载；等待期间用户已离开项目区就不再强行改视图。
async function settleInitialView() {
  if (projectSpaceViews.includes(view.value)) {
    if (projectViews.includes(view.value)) {
      const ready = await ensureProjectContext()
      if (!projectSpaceViews.includes(view.value)) return // 用户已返回本体区
      // 显式项目深链：加载完成后再判断有没有项目，不能因懒加载未完成就提前退回概览
      if (!projectState.value && (projectListState.value === 'error' || projectLoadError.value)) { view.value = 'p-home'; history.replaceState(null, '', '#p-home') }
      else if (!ready && !projectState.value && projectListState.value === 'ready') { view.value = 'p-home'; history.replaceState(null, '', '#p-home') }
    }
    else { void ensureProjectList() } // 编排页等项目空间页面：侧栏项目列表后台加载，不阻塞页面
    // 函数编排：恢复上次打开的编排（编排页不依赖本体/项目状态）
    if (view.value === 'f-editor') {
      const storedFlow = localStorage.getItem(flowStoreKey)
      if (storedFlow) { try { await openFlow(storedFlow) } catch { view.value = 'f-home'; history.replaceState(null, '', '#f-home') } }
      else { view.value = 'f-home'; history.replaceState(null, '', '#f-home') }
    }
  }
  if (view.value === 'instances') await runPreview()
  if (view.value === 'o-release') { await loadVersionList(); await loadReleases(); await checkWorkflow() }
  if (view.value === 'p-release') await validateProject(false)
}
onBeforeUnmount(() => { window.removeEventListener('keydown', keydown); window.removeEventListener('hashchange', hashChanged); window.removeEventListener('beforeunload', unloadGuard); document.removeEventListener('click', onDocClickUserMenu) })
onBeforeUnmount(() => { window.removeEventListener('keydown', keydown); window.removeEventListener('hashchange', hashChanged); window.removeEventListener('beforeunload', unloadGuard) })
</script>

<template>
<div class="topbar" :class="{mini: railMini}" :inert="modalOpen"><div class="topbar-crumb"><span class="crumb-path">{{crumbPath}}</span><h1 class="topbar-title">{{pages[view]}}</h1></div><div class="topbar-status">
<template v-if="booting||ontologyLoad==='loading'"><span class="save-pill">{{ontologySlow?'仍在加载，请稍候…':'加载中…'}}</span></template>
<template v-else-if="(space==='project'&&((projectState||(flowViews.includes(view)&&flowState))))||(space==='ontology'&&hasOntology&&ontologyLoad==='ready')">
<!-- G3：状态由真实 guard dirty + Saver 状态派生；摘要文案，完整错误在页面内持续可见 -->
<span class="save-pill" :class="saveState.kind" :title="saveState.hint"><template v-if="saveState.kind==='form'">✎ </template>{{saveState.text}}<template v-if="saveState.kind==='saved'&&!onGlobalView&&activeSaver!.lastSavedAt.value"> · {{activeSaver!.lastSavedAt.value}}</template></span>
<button v-if="saveState.kind==='saved'&&!onGlobalView&&activeErrors.length" class="save-issues" :title="activeErrors.join('；')" @click="navigate(activeReleaseView)">{{activeErrors.length}} 项待完善</button>
<span v-else-if="saveState.kind==='saved'&&!onGlobalView&&onFlowView&&flowCheck" :class="flowCheck.errors.length||flowCheck.warnings.length?'inline-warning':'inline-success'">{{flowCheck.errors.length?'配置有错误':(flowCheck.warnings.length?'已保存，配置待完善':'已保存，配置检查通过')}}</span>
<button v-if="saveState.kind==='error'&&!onGlobalView" @click="retrySave">{{activeSaver.unknownOutcome.value?'重试保存':'重试'}}</button>
<template v-if="saveState.kind==='conflict'&&!onGlobalView"><button @click="discardReload" title="放弃本地修改，重新加载服务端最新草稿">放弃本地并重新加载</button><button @click="retryLocal" title="以当前屏幕内容覆盖服务端较新草稿">以当前内容重试</button></template>
</template>
<template v-else><span v-if="ontologyLoad==='error'" class="save-pill error" :title="ontologyLoadError">本体读取失败</span><span v-else-if="space==='ontology'">尚未创建本体</span><span v-else-if="flowViews.includes(view)">未选择编排</span><span v-else>未选择项目</span></template>
</div><div class="topbar-actions">
<template v-if="isEditableView(view)&&!onGlobalView"><button :disabled="!canUndo" :title="canUndo?('撤销：'+undoLabel):undoDisabledReason" :aria-label="canUndo?('撤销：'+undoLabel):'撤销（当前页面没有可撤销的操作）'" @click="undo">撤销</button><button :disabled="!canRedo" :title="canRedo?('重做：'+redoLabel):undoDisabledReason" :aria-label="canRedo?('重做：'+redoLabel):'重做（当前页面没有可重做的操作）'" @click="redo">重做</button></template>
</div></div>
<aside class="rail" :class="{mini: railMini && !onGlobalView}" :inert="modalOpen">
<!-- 设置中心形态：独立分类侧栏，替换业务 Tab/选择器/菜单（20260918 需求 §3.2） -->
<template v-if="onGlobalView">
  <div class="brand">◇ <span>本体工作台</span><small>ONTOLOGY WORKSPACE</small></div>
  <button type="button" class="settings-back" @click="backToWorkspace">← 返回工作区</button>
  <div class="settings-group"><label>设置</label></div>
  <nav aria-label="设置导航">
    <template v-for="cat in settingsCategories" :key="cat.id">
      <p class="settings-cat">{{ cat.group }}</p>
      <button type="button" :aria-current="view===cat.id?'page':undefined" :class="{active:view===cat.id}" @click="navigate(cat.id)"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="navIcons[cat.id]||navIcons._default"/></svg>{{ cat.title }}</button>
    </template>
  </nav>
</template>
<!-- 业务形态：原有两区侧栏 -->
<template v-else>
  <div class="brand">◇ <span>本体工作台</span><small>ONTOLOGY WORKSPACE</small></div><div class="ws-tabs" role="tablist" aria-label="切换工作区"><button role="tab" :class="{active:space==='ontology'}" :aria-selected="space==='ontology'" @click="switchSpace('ontology')">本体</button><button role="tab" :class="{active:space==='project'}" :aria-selected="space==='project'" @click="switchSpace('project')">项目</button></div><button type="button" class="rail-toggle" :title="railMini?'展开侧栏':'收起侧栏'" :aria-label="railMini?'展开侧栏':'收起侧栏'" @click="toggleRail">{{railMini?'»':'«'}}<span>{{railMini?'':'收起侧栏'}}</span></button><div class="space"><template v-if="booting"><div class="space-head"><div class="skeleton" style="height:13px;width:56px;margin:0"></div></div><div class="skeleton" style="height:42px"></div></template><template v-else-if="space==='ontology'"><div class="space-head"><label>当前本体</label><button type="button" class="space-new" @click="showOntologyDialog=true">＋ 新建</button></div><AppSelect :model-value="hasOntology?ontologyId:''" placeholder="未选择本体" :options="ontologyList.map(o=>({value:o.id,label:o.name}))" :disabled="busy||!ontologyList.length" aria-label="切换本体" @update:model-value="switchOntology"/></template>
<template v-else><div class="space-head"><label>当前项目</label><button v-if="projectListState==='error'" type="button" class="space-new" @click="retryProjectContext">重试</button><button v-else type="button" class="space-new" :disabled="projectListState!=='ready'" title="新建项目" @click="newProjectFromSidebar">＋ 新建</button></div><p v-if="projectListState==='error'" class="space-error" role="alert">项目列表加载失败</p><template v-else-if="projectListState!=='ready'||(projectLoading&&!projectState)"><div class="skeleton" style="height:42px"></div><p class="space-note">正在加载项目…</p></template><AppSelect v-else :model-value="projectState?.projectId||''" placeholder="未选择项目" :options="projectOptions" :disabled="busy" aria-label="切换项目" @update:model-value="$event&&loadProject($event)"/></template></div><nav aria-label="工作台导航"><template v-if="booting"><div v-for="n in 5" :key="n" class="nav-boot"><div class="skeleton" style="height:13px;width:64%"></div></div></template><template v-else-if="space==='ontology'"><template v-if="hasOntology"><button v-for="(label,key) in menuOntology" :key="key" :title="label" :aria-current="view===key?'page':undefined" :class="{active:view===key}" @click="navigate(key)"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="navIcons[key]||navIcons._default"/></svg>{{label}}</button><button :aria-current="view==='tools'?'page':undefined" :title="'更多工具'" :class="{active:view==='tools'}" @click="navigate('tools')"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="navIcons.tools||navIcons._default"/></svg>更多工具</button></template></template><template v-else><button v-for="(label,key) in menuProject" :aria-current="view===key?'page':undefined" :key="key" :title="label" :class="{active:view===key}" @click="navigate(key)"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="navIcons[key]||navIcons._default"/></svg>{{label}}</button><p v-if="!projectState" class="rail-hint">选择或新建项目后，可进行对象映射与项目校验；函数编排不依赖项目。</p></template></nav>
</template>
<!-- 底部固定用户区：头像+用户名（用户菜单触发）与独立齿轮（设置），两种形态都显示 -->
<div class="rail-user">
  <div class="rail-user-wrap" ref="userMenuWrap">
    <button ref="userMenuTrigger" type="button" class="rail-user-trigger" :aria-expanded="userMenuOpen" aria-controls="user-menu" aria-haspopup="menu" :title="railMini&&!onGlobalView?userName:undefined" @click="toggleUserMenu">
      <span class="user-avatar" aria-hidden="true">{{ userName.slice(0, 1) }}</span>
      <span v-if="!railMini||onGlobalView" class="user-name">{{ userName }}</span>
    </button>
    <div v-if="userMenuOpen" id="user-menu" class="user-menu" role="menu" aria-label="用户菜单" @keydown="userMenuKeydown">
      <div class="user-menu-head">
        <span class="user-avatar" aria-hidden="true">{{ userName.slice(0, 1) }}</span>
        <span><strong>{{ userName }}</strong><small>当前工作台</small></span>
      </div>
      <button type="button" role="menuitem" @click="openSettings()"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="navIcons['user-settings']||navIcons._default"/></svg>设置</button>
      <button type="button" role="menuitem" :disabled="!hasAuthSession" :title="hasAuthSession?undefined:'当前为本地模式，无登录会话'" @click="logoutClick"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="navIcons['user-logout']||navIcons._default"/></svg>退出登录<small v-if="!hasAuthSession" class="user-menu-note">未登录</small></button>
    </div>
  </div>
  <button type="button" class="rail-gear" :title="'设置'" aria-label="设置" @click="openSettings()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="navIcons.settings||navIcons._default"/></svg></button>
</div>
</aside>
<div class="shell" :class="{mini: railMini}" :inert="modalOpen"><div v-if="message" id="feedback" :class="{error}" role="status">{{message}}</div>
<main v-if="onGlobalView">
<!-- 模型设置独立渲染：不依赖本体/项目加载结果（20260918 需求 §3.2），不显示业务校验/发布/撤销 -->
<ModelSettings/>
</main>
<main v-else-if="booting||ontologyLoad==='loading'"><section class="card"><div class="skeleton" style="height:18px;width:200px;margin:0 0 18px"></div><div class="skeleton" style="height:14px;margin:12px 0"></div><div class="skeleton" style="height:14px;margin:12px 0;width:92%"></div><div class="skeleton" style="height:14px;margin:12px 0;width:96%"></div><div class="skeleton" style="height:14px;margin:12px 0;width:78%"></div></section><p v-if="ontologySlow" class="muted" role="status">仍在加载，请稍候…（超过 {{ Math.round(READ_TIMEOUT_MS/1000) }} 秒仍未返回会给出重试入口）</p></main>
<!-- G1/G2：本体必要读取失败——给出状态与恢复入口，不能渲染成「没有本体」 -->
<main v-else-if="!state">
<AppError :title="ontologyLoadFailure==='timeout'?'读取本体数据超时':'读取本体数据失败'" :reason="ontologyLoadError" :hint="ontologyFailureHint" retry-label="重试读取" :details="ontologyFailureDetails" @retry="retryOntologyLoad"/>
</main>
<main v-else>
<!-- G2：本体数据重新读取失败（已展示的是上一次成功读取的内容） -->
<AppError v-if="ontologyLoad==='error'" compact title="本体数据重新读取失败" :reason="ontologyLoadError" hint="当前显示的是上一次成功读取的内容；已保存的草稿不受影响，可重试读取。" retry-label="重试读取" @retry="retryOntologyLoad"/>
<!-- 项目区加载中/失败（R2）：与「还没有项目」区分，等待或失败时都可返回本体 -->
<section v-if="projectAreaWaiting" class="card"><div class="skeleton" style="height:18px;width:200px;margin:0 0 18px"></div><div class="skeleton" style="height:14px;margin:12px 0"></div><div class="skeleton" style="height:14px;margin:12px 0;width:88%"></div></section>
<AppError v-else-if="projectAreaFailed" :title="projectFailureTitle" :reason="projectListError||projectLoadError" :hint="projectOriginBlocked?'':'项目数据加载失败，本体建模可继续使用。修正后重试，或先回到本体区继续工作。'" :fix-href="projectOriginBlocked?localAccess:''" retry-label="重试" secondary-label="返回本体" @retry="retryProjectContext" @secondary="switchSpace('ontology')"/>
<section v-else-if="!hasOntology&&area==='ontology'" class="card"><div class="panelhead"><div><h2>创建第一个本体</h2><p class="muted">本体建模需要先有本体。也可以并行地先创建项目——项目不依赖本体，绑定本体可随时在项目信息中补选。</p></div><a :href="templateHref" download="本体模型填写模板.xlsx">下载 Excel 模板</a></div><form class="sample-panel" @submit.prevent="createOntology"><label>本体名称 *<input v-model="newOntologyName" required maxlength="80" placeholder="例如：储能本体"></label><p class="muted">从空白开始，不复制任何已有内容。导入 Excel 需要先选择或新建本体。</p><div class="tools"><button type="submit" class="primary" :disabled="busy||!newOntologyName.trim()">创建本体</button></div></form><div v-if="ontologyList.length" class="ontology-list"><div v-for="o in ontologyList" :key="o.id" class="panelhead"><strong>{{o.name}}</strong><button :disabled="busy" @click="switchOntology(o.id)">打开</button></div></div></section>
<template v-else>
<OntologyHome v-if="view==='o-home'" :state="state" @navigate="navigate"/>
<ObjectWorkspace v-if="view==='objects'" :state="state" :focus-type="propertyFocusType" :focus-property="propertyFocusId" :initial-tab="objectDetailTab" @before-change="pushUndo" @changed="changed" @navigate="navigate"/>
<FunctionManager v-if="view==='contracts'" :state="state" :focus-id="contractFocusId" @properties="openProperties" @before-change="pushUndo" @changed="changed"/>
<SharedLibrary v-if="view==='library'" :state="state" @before-change="pushUndo" @changed="changed" @navigate="navigate"/>
<BusinessRuleLibrary v-if="view==='rules'" :state="state" :focus-id="definitionFocusId" @before-change="pushUndo" @changed="changed" @navigate="navigate"/>
<OntologyRelease v-if="view==='o-release'" :state="state" @before-change="pushUndo" @changed="changed" @published="onOntologyPublished" @navigate="navigate"/>
<ProjectHome v-if="view==='p-home'" :create-signal="projectCreateSignal" :default-ontology-id="hasOntology?ontologyId:''" :ontology-options="ontologyOptions" :projects="projects" :project-state="projectState" :project-dirty="projectDirty" :migration-todos="migrationTodos" @select="id=>loadProject(id)" @created="createProjectDone" @reference="applyProjectReference" @upgrade="upgradeProject" @open-implementations="navigate('implements')" @open-connections="navigate('connections')" @open-binding="t=>t?openBindings(t):navigate('binding')" @navigate="navigate" @before-change="pushProjectUndo" @changed="projectChanged"/>
<ConnectionManager v-if="view==='connections'&&projectState" :project-state="projectState" @before-change="pushProjectUndo" @changed="projectChanged"/>
<ProjectVersion v-if="view==='p-upgrade'&&projectState" :project-state="projectState" :ontology-options="ontologyOptions" :apply-reference="saveProjectReference" @navigate="navigate"/>
<ProjectBinding v-if="view==='binding'&&projectState" :project-state="projectState" :ref-state="refState" :focus-type="bindingFocusType" :report="projectReport" @navigate="navigate" @open-ontology="openReferencedOntology" @before-change="pushProjectUndo" @changed="projectChanged"/>
<QueryRuleManager v-if="view==='implements'&&projectState" :project-state="projectState" :ref-state="refState" :focus-impl="implFocus" @before-change="pushProjectUndo" @changed="projectChanged"/>
<ProjectValidation :ref-state="refState" v-if="view==='p-release'&&projectState" :report="projectReport" :validate-error="projectValidateError" :busy="busy" :project-state="projectState" @open-ontology="openReferencedOntology" @refresh="validateProject(false)" @navigate="navigate" @published="onProjectPublished"/>
<FlowList v-if="view==='f-home'" @open="openFlow" @created="onFlowCreated" @deleted="onFlowDeleted"/>
<FlowEditor v-if="view==='f-editor'&&flowState" :state="flowState" :project-connections="projectConnections" :project-id="projectId" :project-name="projectState?.name || ''" :revision="flowSaver.revision.value" :save-check="flowCheck" :save-check-sig="flowCheckSig" :restore-tab="flowInspectorTab" :restore-node="flowFocusNode" :providers-refresh="flowProvidersRefresh" @navigate="navigate" @back="navigate('f-home')" @before-change="pushFlowUndo" @changed="flowChanged"/>
<ToolsPage v-if="view==='tools'" @navigate="navigate"/>
<OntologyDiscover v-if="view==='discover'" :state="state" @navigate="navigate" @graph="showKnowledge" @properties="openProperties"/>
<KnowledgeExplorer v-if="view==='knowledge'" :state="state" :focus-id="knowledgeFocus" @navigate="navigate"/>
<InstanceExplorer v-if="view==='explorer'" :state="state" :project-state="projectState" @navigate="navigate"/>
<DefinitionManager v-if="view==='interfaces'" :key="view" kind="interfaces" :state="state" :project-state="projectState" :focus-id="definitionFocusId" @before-change="pushUndo" @changed="changed" />
<ActionLibrary v-if="view==='actions'" :state="state" :focus-id="definitionFocusId" @before-change="pushUndo" @changed="changed" @navigate="navigate"/>
<ValueTypeManager v-if="view==='valuetypes'" :state="state" @properties="openProperties" @before-change="pushUndo" @changed="changed"/>
<section v-if="view==='learning' && !state.learning" class="card empty">此本体暂无导入或教学材料。</section><section v-if="view==='learning' && state.learning"><div class="card"><div class="badge">LEARNING PATH · 储能业务示例</div><h2>{{state.learning.title}}</h2><p>先理解“是什么”，再定义“怎么算”，最后绑定“数据在哪里”。你不需要先参与现场审核。</p><ol><li v-for="step in state.learning.steps" :key="step">{{step}}</li></ol><div class="tools"><button class="primary" @click="navigate('objects')">① 看对象画布</button><button @click="navigate('contracts')">② 看SOC规则</button><button @click="navigate('instances')">③ 看70%结果</button></div></div><div class="card"><h2>旧图42个节点，哪些属于本体？</h2><p class="muted">对象类型才是默认画布节点。属性挂在对象上；观测、指标与算法有各自的定义文件。</p><div class="scroll"><table><thead><tr><th>原节点</th><th>归类</th><th>新表达</th><th>为什么</th></tr></thead><tbody><tr v-for="item in state.learning.classification" :key="item.source_id"><td>{{item.original_name}}</td><td><strong>{{item.category}}</strong></td><td>{{item.name}}</td><td>{{item.reason}}</td></tr></tbody></table></div></div><div class="card"><h2>教学假设</h2><ul><li v-for="a in state.learning.assumptions" :key="a">{{a}}</li></ul><p class="muted">这些假设让例子可理解、可验证；不代表现场数据已经核实。</p></div></section>
<section v-if="view==='instances'"><div v-if="!preview" class="card"><div class="skeleton" style="height:14px;margin:10px 0"></div><div class="skeleton" style="height:14px;margin:10px 0"></div><div class="skeleton" style="height:14px;margin:10px 0"></div><div class="skeleton" style="height:14px;margin:10px 0"></div><div class="skeleton" style="height:14px;margin:10px 0"></div><div class="skeleton" style="height:14px;margin:10px 0"></div></div><div v-else-if="preview.errors.length" class="card">{{preview.errors.join('；')}}</div><template v-else><div class="columns"><div class="card"><h2>南区储能系统 SOC</h2><div class="result">{{preview.result.value??'—'}}<small> %</small></div><p>{{preview.result.reason||'质量有效 · 模拟快照'}}</p><p class="muted">2026-09-08 10:00 +08:00</p></div><div class="card"><h2>计算血缘</h2><p v-for="r in preview.result.lineage">{{r.object_id}}<br>{{r.soc_pct}}% × {{r.capacity_basis_kwh}} kWh<br><small class="muted">{{r.sampled_at}} · {{r.source_table}}</small></p></div></div><div class="card scroll"><h2>项目实例</h2><table><thead><tr><th>名称</th><th>类型</th><th>归属</th></tr></thead><tbody><tr v-for="o in preview.objects"><td>{{o.display_name||o.properties.name}}</td><td>{{o.type}}</td><td>{{Object.values(o.parents).join(', ')||'—'}}</td></tr></tbody></table></div></template></section>
</template>
</main></div>
<div v-if="leaveDialog" class="modal-backdrop" @click.self="keepEditing"><section class="modal-card" role="dialog" aria-modal="true" aria-label="未保存的表单修改"><h2>当前表单还有未保存的修改</h2><p class="field-help">工作区已有草稿不会丢失。离开只会放弃本次表单修改。</p><div class="dialogtools"><button @click="discardEditing">放弃本次修改并离开</button><button class="primary" @click="keepEditing">继续编辑</button></div></section></div>
<div v-if="showOntologyDialog" class="modal-backdrop" @click.self="showOntologyDialog=false"><form class="modal-card" role="dialog" aria-modal="true" aria-label="新建本体" @submit.prevent="createOntologyFromDialog"><h2>新建本体</h2><p class="field-help">从空白开始，不复制任何已有内容。</p><label>本体名称<input v-model="newOntologyName" required maxlength="80" placeholder="例如：储能本体"></label><div class="dialogtools"><button type="button" @click="showOntologyDialog=false">取消</button><button class="primary" :disabled="busy||!newOntologyName.trim()">创建</button></div></form></div>
</template>

<style>
/* 侧栏收起（用户手动切换，偏好记忆）：64px 图标轨，全页面通用 */
.rail-toggle{display:flex;align-items:center;gap:7px;width:100%;background:var(--paper-2);border:1px solid var(--line);border-radius:var(--r-sm);padding:7px 10px;color:var(--ink-2);font-size:12px}
.rail-toggle:hover{background:var(--blue-soft);color:var(--blue-ink)}
.rail.mini{width:64px;padding:14px 6px}
.rail.mini .brand{text-align:center;padding:0}
.rail.mini .brand span,.rail.mini .brand small,.rail.mini .space,.rail.mini .rail-hint{display:none}
.rail.mini .rail-toggle{justify-content:center;padding:7px 0;font-size:14px}
.rail.mini .rail-toggle span{display:none}
.rail.mini .ws-tabs{flex-direction:column}
.rail.mini .ws-tabs button{padding:5px 0;font-size:12px}
.rail.mini nav button{font-size:0;gap:0;justify-content:center;padding:11px 6px}
.rail.mini nav button .nav-ico{width:18px;height:18px}
.rail.mini nav p{display:none}
.shell.mini{margin-left:64px}
.topbar.mini{left:64px}
</style>

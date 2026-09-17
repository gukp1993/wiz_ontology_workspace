// 保存协调器（P01）。本体区/项目区各持一个 Saver 实例：串行保存、revision 合并、
// 失败/冲突状态机、beforeunload 拦截。接口冻结来源：outputs/工作台整体交互迭代_并行任务板.md §1，
// 勿改 Saver/createSaver 的成员签名；行为细则（8 条）见同文件 §1。
import { ref, type Ref } from 'vue'
import { SaveRequestError, requestJson } from './http'

export { SaveRequestError, requestJson } // 兼容再导出：请求基础层已迁 app/http.ts

export type SaveStatus = 'saved' | 'dirty' | 'saving' | 'error' | 'conflict'

export interface Saver {
  status: Ref<SaveStatus>
  error: Ref<string>
  lastSavedAt: Ref<string>
  working: Ref<any>            // 当前工作副本（App 的 state/projectState 即此对象的 .value）
  revision: Ref<string>
  unknownOutcome: Ref<boolean> // G2：失败是否属于「未收到结果、无法确认服务端是否完成」（超时/网络中断）
  commitNow(): Promise<void>   // 立即持久化当前 working（表单保存按钮/撤销重做/图操作结束调用；取消 pending debounce）
  touch(): void                // changed() 入口：dirty + debounce 900ms 自动保存（连续编辑合并）
  flush(): Promise<void>       // 切换本体/项目/版本前 await（保存中则等待完成）
  reload(): Promise<void>      // 放弃本地修改，重新 GET（conflict/用户显式操作）
  retryFromLocal(): Promise<void> // conflict：以当前屏幕内容换最新 revision 重提交（UI 需二次确认，覆盖服务端较新草稿）
  retry(): Promise<void>       // error（网络/5xx）时重试上次未成功快照
  clearFailed(): void          // 表单保存失败并已回滚 working 后调用：丢弃 lastFailed 并复位 error，
                               // 防止后台重试把用户已取消的修改再次提交。conflict 状态保留交由状态栏处理。
}

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
const DEBOUNCE_MS = 900

interface QueueItem { snapshot: any; gen: number }

export function createSaver(label: string, load: () => Promise<{ state: any; revision: string }>,
  submit: (state: any, revision: string) => Promise<{ revision: string }>): Saver {
  const status = ref<SaveStatus>('saved'), error = ref(''), lastSavedAt = ref('')
  const working = ref<any>(null), revision = ref('')
  // G2：区分「服务端明确拒绝」与「未收到结果」——后者不能显示成功，也不能断言未写入。
  const unknownOutcome = ref(false)
  let gen = 0                          // 工作内容代数：touch/撤销/替换 working 都递增；用于“入队后内容又变了”判定
  let queue: QueueItem | null = null   // 待发送快照（连续编辑合并只留最后一项），携带入队瞬间的 working 深快照与当时 revision
  let lastFailed: QueueItem | null = null
  let conflictRevision: string | null = null
  let timer: any = null
  let inflight: Promise<void> | null = null
  let epoch = 0                        // reload 递增：丢弃在途旧响应，避免过期保存覆盖新载入的内容

  function schedule() {
    if (timer || status.value === 'conflict') return // 冲突期间不自动提交，等用户选择放弃/重试
    timer = setTimeout(() => { timer = null; void drain() }, DEBOUNCE_MS)
  }

  // 串行：同一 Saver 同时最多一个在途请求；由 drain 循环驱动，完成一条再取下一条。
  async function pumpOne() {
    if (inflight || !queue || status.value === 'conflict') return
    const item = queue; queue = null
    const myEpoch = epoch
    inflight = (async () => {
      status.value = 'saving'; error.value = ''
      try {
        // 提交基线取「本客户端最近确认的 revision」：串行队列中它就是服务端对这份草稿的最新
        // 确认值。入队时点的旧 revision 在前一次保存成功后即过期，沿用会制造假 409（F3-②）。
        const data = await submit(item.snapshot, revision.value)
        if (myEpoch !== epoch) return // 期间发生了重新载入：丢弃过期响应
        if (typeof data?.revision === 'string') revision.value = data.revision
        lastSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
        unknownOutcome.value = false
        if (gen !== item.gen && working.value) { // 入队后内容又有修改：以最新内容 + 已确认 revision 续存
          queue = { snapshot: clone(working.value), gen }
          status.value = 'dirty'
        } else if (queue) status.value = 'dirty'
        else status.value = 'saved'
      } catch (err: any) {
        if (myEpoch !== epoch) return
        if (err?.status === 409) { conflictRevision = err.currentRevision ?? null; status.value = 'conflict'; error.value = err?.message || '此草稿已有新版本' }
        else {
          lastFailed = item; status.value = 'error'
          // 超时／网络中断：请求可能已到达服务端，不能显示成功也不能断言未写入（G2）
          unknownOutcome.value = !(err instanceof SaveRequestError) || err.failure !== 'http'
          error.value = unknownOutcome.value ? '未收到保存结果，暂不能确认是否成功' : (err?.message || '保存失败')
        }
      } finally { inflight = null }
    })()
  }

  // force 仅由显式动作（retry / commitNow）传入：error 状态不自动续发，避免无界自动重试。
  async function drain(force = false) {
    for (;;) {
      if (!inflight) {
        if (!queue || status.value === 'conflict' || (status.value === 'error' && !force)) break
        void pumpOne()
        force = false
      }
      await inflight
    }
  }

  function touch() {
    if (!working.value) return
    gen++
    queue = { snapshot: clone(working.value), gen }
    if (status.value !== 'conflict' && status.value !== 'saving') status.value = 'dirty' // saving 期间保持 saving，落定后由代数检查回 dirty
    schedule()
  }

  async function commitNow() {
    if (!working.value || status.value === 'conflict') return // 冲突时提交必然 409，走 retryFromLocal/reload
    if (timer) { clearTimeout(timer); timer = null }
    queue = { snapshot: clone(working.value), gen }
    if (status.value === 'error') status.value = 'dirty' // 显式保存是用户动作：可重试上次失败的保存（F3-⑥）
    await drain(true)
  }

  async function flush() {
    if (!working.value) return
    if (timer) { clearTimeout(timer); timer = null }
    await drain() // 冲突/失败时立即返回，由调用方检查 status 决定 confirm
  }

  async function reload() {
    const my = ++epoch
    const data = await load()
    if (my !== epoch) return
    working.value = data.state
    revision.value = data.revision
    gen++
    queue = null; lastFailed = null; conflictRevision = null; unknownOutcome.value = false
    if (timer) { clearTimeout(timer); timer = null }
    status.value = 'saved'; error.value = ''
  }

  async function retry() {
    if (status.value !== 'error' || !lastFailed) return
    const failed = lastFailed; lastFailed = null
    // 失败后又编辑（内容与失败快照不一致）：重试最新确认内容，不静默把新内容换回旧失败快照（F3-⑤）
    const changed = working.value && JSON.stringify(working.value) !== JSON.stringify(failed.snapshot)
    queue = changed ? { snapshot: clone(working.value), gen } : failed
    status.value = 'dirty'; error.value = ''; unknownOutcome.value = false
    if (timer) { clearTimeout(timer); timer = null }
    await drain(true) // error 状态下 drain 默认不续发；显式重试强制发送一次（F3-①）
  }

  // 冲突处理：语义=「以我屏幕上的内容为准重试」。
  // 409 响应带 currentRevision → 直接换基线重提当前内容（无则先 refetch 最新草稿为基线，
  // 会覆盖本地 working，再恢复屏幕内容）。UI 必须写明覆盖语义并二次确认，绝不自动调用。
  async function retryFromLocal() {
    if (status.value !== 'conflict' || !working.value) return
    const local = clone(working.value)
    if (conflictRevision) revision.value = conflictRevision
    else { await reload(); working.value = local; gen++ }
    conflictRevision = null
    status.value = 'dirty'; error.value = ''; unknownOutcome.value = false
    queue = { snapshot: clone(local), gen }
    await drain(true)
  }

  function clearFailed() {
    if (status.value === 'error') { lastFailed = null; queue = null; status.value = 'saved'; error.value = ''; unknownOutcome.value = false }
    // conflict 不在此复位：队列为空不会自动重提，由用户经状态栏显式处理
  }

  // beforeunload：dirty/saving/conflict/error 时拦截（error 也意味着有未落盘内容——F3-③；
  // 每个 Saver 各自注册，任一有未落盘内容都会拦截）。
  window.addEventListener('beforeunload', (e: BeforeUnloadEvent) => {
    if (status.value !== 'saved') { e.preventDefault(); e.returnValue = '' }
  })

  return { status, error, lastSavedAt, working, revision, unknownOutcome, commitNow, touch, flush, reload, retryFromLocal, retry, clearFailed }
}

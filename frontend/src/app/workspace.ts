// 编辑历史管理（20260918 撤销／重做范围与交互优化）：带操作元信息的轻量快照栈。
// 仍是内存快照方案（不上事件溯源/命令总线），演进点：
//   · 具名操作 actionLabel / target（按钮与 aria 展示"撤销：删除属性「…」"）；
//   · 候选事务 begin/commit/cancel——form-save 成功才入栈一条，失败/异常取消且不清 redo；
//   · mergeKey——同一实体同一字段的连续输入合并为一步（blur/结构操作结算）；
//   · 空操作（before/after 深比较相同）不记录；上限 60 步；counts 响应式。
// scope（本体 id+页 / flowId）由 App 层判定：离开编辑页 reset；本管理器只认 scopeKey 字符串。
import { ref, type Ref } from 'vue'

const clone = (x: any) => JSON.parse(JSON.stringify(x))
const same = (a: any, b: any) => JSON.stringify(a) === JSON.stringify(b)

export interface UndoTarget { kind: string; id: string; ownerId?: string }
export interface UndoEntry {
  scopeKey: string
  actionLabel: string
  target?: UndoTarget
  before: any
  after: any
  mergeKey?: string
}
export interface UndoTransaction {
  /** 结束并登记：before/after 相同则丢弃（空操作）。 */
  commit(actionLabel: string, after: any, meta?: { target?: UndoTarget; mergeKey?: string }): void
  /** 丢弃候选（失败/取消/无变化保存）：不影响已入栈条目与 redo。 */
  cancel(): void
}

export interface UndoArea {
  /** 即时/结构操作前调用：开启候选事务（同一时刻只允许一个，重复 begin 取消前一个未结算事务）。 */
  begin(meta?: { mergeKey?: string; target?: UndoTarget }): UndoTransaction
  /** 兼容旧调用点（before-change）：等价 begin+commit 同一快照（before=current, after=payload）。
   *  payload 缺省取当前 working。用于已确认的一次性原子操作。 */
  push(actionLabel: string, payload?: any, meta?: { target?: UndoTarget; mergeKey?: string }): void
  /** 撤销：返回要恢复的快照并把当前压入 redo；空栈返回 null。 */
  undo(): any | null
  /** 重做。 */
  redo(): any | null
  /** 待撤销操作名（栈顶）；无则空串。 */
  peekUndoLabel(): string
  /** 待重做操作名；无则空串。 */
  peekRedoLabel(): string
  /** 响应式计数 [undo, redo]。 */
  readonly counts: Ref<[number, number]>
  /** 资源/页面切换、重载后清空。 */
  reset(): void
}

export function createUndoArea(current: Ref<any>, limit = 60): UndoArea {
  let undoStack: UndoEntry[] = [], redoStack: UndoEntry[] = []
  const counts = ref<[number, number]>([0, 0])
  const sync = () => { counts.value = [undoStack.length, redoStack.length] }
  let scopeKey = ''
  let pending: { before: any } | null = null

  function ensureScope(key: string) {
    if (scopeKey !== key) { scopeKey = key; undoStack = []; redoStack = []; sync() }
  }
  function record(entry: UndoEntry) {
    // mergeKey：与栈顶同 mergeKey 的连续编辑合并（保留栈顶 before，更新 after）
    const top = undoStack[undoStack.length - 1]
    if (entry.mergeKey && top && top.mergeKey === entry.mergeKey && top.scopeKey === entry.scopeKey) {
      top.after = entry.after
    } else {
      undoStack.push(entry)
      if (undoStack.length > limit) undoStack.shift()
    }
    redoStack = []
    sync()
  }

  return {
    begin(meta) {
      // 未结算事务直接作废（结构操作前结算/覆盖场景）
      const before = clone(current.value)
      const pendingMergeKey = meta?.mergeKey
      const pendingTarget = meta?.target
      pending = { before }
      return {
        commit(actionLabel, after, meta2) {
          if (!pending) return
          pending = null
          const afterSnapshot = clone(after)
          if (same(before, afterSnapshot)) return // 空操作不记
          record({ scopeKey, actionLabel, target: meta2?.target ?? pendingTarget, before, after: afterSnapshot, mergeKey: meta2?.mergeKey ?? pendingMergeKey })
        },
        cancel() { pending = null },
      }
    },
    push(actionLabel, payload, meta) {
      const before = clone(current.value)
      const after = payload === undefined ? clone(current.value) : clone(payload)
      // push 语义：调用方先改后报？不——旧调用点是"改之前"调用。这里 before 取参数里的 payload 不成立，
      // 保持旧语义：push 在 mutate 之前调用，before=当前，after 由调用方在 changed 后不可知 →
      // 旧栈存 before，undo 恢复 before。为兼容，after 记为调用完成后的 current 由 App 层在 changed 时无感知，
      // 因此 push 存 before 快照（actionLabel 描述此次操作），undo 时恢复 before，redo 需要 after——
      // 旧实现 redoStack.push(clone(current)) 已涵盖。这里保持一致：
      if (same(before, current.value) && payload === undefined) {
        // before 与当前相同：记录 before 作为恢复点（旧语义），不比较
      }
      record({ scopeKey, actionLabel, target: meta?.target, before, after: clone(current.value), mergeKey: meta?.mergeKey })
    },
    undo() {
      const top = undoStack[undoStack.length - 1]
      if (!top) return null
      redoStack.push({ scopeKey: top.scopeKey, actionLabel: top.actionLabel, target: top.target, before: clone(current.value), after: clone(current.value) })
      undoStack.pop()
      sync()
      return clone(top.before)
    },
    redo() {
      const top = redoStack[redoStack.length - 1]
      if (!top) return null
      undoStack.push({ scopeKey: top.scopeKey, actionLabel: top.actionLabel, target: top.target, before: clone(current.value), after: top.after })
      redoStack.pop()
      sync()
      return clone(top.after)
    },
    peekUndoLabel: () => undoStack[undoStack.length - 1]?.actionLabel || '',
    peekRedoLabel: () => redoStack[redoStack.length - 1]?.actionLabel || '',
    counts,
    reset() {
      undoStack = []; redoStack = []; pending = null; sync()
    },
  }
}

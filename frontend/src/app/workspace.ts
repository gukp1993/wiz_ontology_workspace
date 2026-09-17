// 工作区编辑生命周期辅助（架构方案 §4.2）：撤销/重做快照栈。
// 撤销/重做替换 working 后由调用方触发 commitNow 产生新修订（不修改历史版本）。
import { type Ref } from 'vue'

const clone = (x: any) => JSON.parse(JSON.stringify(x))

export interface UndoArea {
  push(): void          // before-change：压入当前快照，清空 redo
  undo(): any | null    // 返回要恢复的快照（同时把当前压入 redo）；栈空返回 null
  redo(): any | null
  counts(): [number, number]
  reset(): void         // 资源切换/重载后清空
}

export function createUndoArea(current: Ref<any>, limit = 60): UndoArea {
  let undoStack: any[] = [], redoStack: any[] = []
  return {
    push() { undoStack.push(clone(current.value)); if (undoStack.length > limit) undoStack.shift(); redoStack = [] },
    undo() { if (!undoStack.length) return null; redoStack.push(clone(current.value)); return undoStack.pop() },
    redo() { if (!redoStack.length) return null; undoStack.push(clone(current.value)); return redoStack.pop() },
    counts: () => [undoStack.length, redoStack.length],
    reset() { undoStack = []; redoStack = [] },
  }
}

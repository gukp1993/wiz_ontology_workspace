// 快捷键策略（20260918 撤销范围优化更新 undo/redo 判定）：
// 1. IME 组合中：应用层不接管任何键。
// 2. 输入控件（INPUT/TEXTAREA/SELECT/contenteditable 及代码编辑器根 data-code-editor）：
//    Ctrl/Cmd+Z（±Shift）属于控件自身文本撤销，业务层不 preventDefault、不落业务历史；
//    文本栈为空也不能冒泡到业务撤销。
// 3. 撤销/重做快捷键是否生效由调用方传入 allowUndoRedo 判定（编辑页白名单、无表单/弹窗、栈非空），
//    本函数只做输入目标与 IME 的通用判定，保持其他快捷键行为不变。
export function shortcutAction(e,{modal=false,graph=true}={}){
 if(e.isComposing)return null
 const key=e.key.toLowerCase(),typing=['INPUT','TEXTAREA','SELECT'].includes(e.target?.tagName)||e.target?.isContentEditable
 if(key==='escape')return 'escape'
 if(modal)return null
 if((e.metaKey||e.ctrlKey)&&key==='s')return 'save'
 if(typing)return null
 if((e.metaKey||e.ctrlKey)&&key==='z')return e.shiftKey?'redo':'undo'
 if(!graph||e.metaKey||e.ctrlKey||e.altKey)return null
 if(key==='f')return e.shiftKey?'zoomOut':'focus'
 if(key==='delete'||key==='backspace')return 'delete'
 if(key==='?')return 'help'
 return null
}

/** 撤销/重做快捷键是否应交给业务层：输入控件（含 composedPath 深查与编辑器根标记）一律 false。 */
export function undoRedoAllowed(e: KeyboardEvent): boolean {
  if (e.isComposing || e.defaultPrevented) return false
  const path: EventTarget[] = (e as any).composedPath ? (e as any).composedPath() : [e.target]
  for (const node of path) {
    if (!(node instanceof HTMLElement)) continue
    const tag = node.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'OPTION') return false
    if (node.isContentEditable) return false
    if (node.closest?.('[data-code-editor], .code-editor, [role="textbox"]')) return false
  }
  return true
}

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

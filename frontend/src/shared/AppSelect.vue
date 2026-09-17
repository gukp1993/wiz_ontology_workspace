<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,ref,useAttrs,useId,watch} from 'vue'
import type {CSSProperties} from 'vue'

type Option=string|{value:string;label:string;disabled?:boolean}
defineOptions({inheritAttrs:false})
const props=withDefaults(defineProps<{
  modelValue?:string|number|boolean
  options?:Option[]
  placeholder?:string
  disabled?:boolean
  searchable?:boolean
}>(),{options:()=>[],placeholder:'请选择',searchable:undefined})
const emit=defineEmits<{'update:modelValue':[value:string];'before-change':[]}>()
const attrs=useAttrs(),uid='app-select-'+useId()
const trigger=ref<HTMLButtonElement>(),panel=ref<HTMLDivElement>(),list=ref<HTMLDivElement>(),searchInput=ref<HTMLInputElement>()
const opened=ref(false),query=ref(''),active=ref(-1),fieldsetDisabled=ref(false),position=ref<CSSProperties>({})
const disabled=computed(()=>props.disabled||fieldsetDisabled.value)
const value=computed(()=>String(props.modelValue??''))
const options=computed(()=>props.options.map(option=>typeof option==='string'?{value:option,label:option,disabled:false}:{...option,value:String(option.value)}))
const selected=computed(()=>options.value.find(option=>option.value===value.value))
const caption=computed(()=>selected.value?.label||value.value||props.placeholder)
const searchable=computed(()=>props.searchable??options.value.length>8)
const visible=computed(()=>options.value.filter(option=>(option.label+' '+option.value).toLowerCase().includes(query.value.trim().toLowerCase())))
const activeId=computed(()=>active.value>=0?uid+'-option-'+active.value:undefined)
let resizeObserver:ResizeObserver,fieldsetObserver:MutationObserver,typeBuffer='',typeTimer:ReturnType<typeof setTimeout>

function close(restoreFocus=false){
  opened.value=false
  if(restoreFocus&&!disabled.value)trigger.value?.focus({preventScroll:true})
}
function place(){
  if(!opened.value||!trigger.value)return
  const rect=trigger.value.getBoundingClientRect(),viewport=window.visualViewport
  const width=viewport?.width||window.innerWidth,height=viewport?.height||window.innerHeight
  const leftEdge=viewport?.offsetLeft||0,topEdge=viewport?.offsetTop||0,bottomEdge=topEdge+height
  if(!rect.width||rect.bottom<topEdge||rect.top>bottomEdge){close();return}
  const below=bottomEdge-rect.bottom-12,above=rect.top-topEdge-12
  const up=below<Math.min(280,visible.value.length*39+(searchable.value?58:12))&&above>below
  const panelWidth=Math.min(Math.max(rect.width,220),width-16)
  position.value={
    position:'fixed',width:panelWidth+'px',maxHeight:Math.max(40,Math.min(360,up?above:below))+'px',
    left:Math.min(Math.max(leftEdge+8,rect.left),leftEdge+width-panelWidth-8)+'px',
    ...(up?{bottom:window.innerHeight-rect.top+6+'px'}:{top:rect.bottom+6+'px'})
  }
}
function initialIndex(last=false){
  const current=visible.value.findIndex(option=>option.value===value.value&&!option.disabled)
  if(current>=0)return current
  const indexes=visible.value.map((_,i)=>i).filter(i=>!visible.value[i].disabled)
  return (last?indexes.at(-1):indexes[0])??-1
}
function reveal(){nextTick(()=>list.value?.querySelector<HTMLElement>('[data-active="true"]')?.scrollIntoView({block:'nearest'}))}
async function open(last=false,initialQuery=''){
  if(disabled.value||trigger.value?.matches(':disabled'))return
  query.value=initialQuery;opened.value=true;active.value=initialIndex(last)
  await nextTick();if(!opened.value)return
  place();if(!opened.value)return
  await nextTick();(searchable.value?searchInput.value:list.value)?.focus({preventScroll:true});reveal()
}
function choose(index:number){
  const option=visible.value[index]
  if(disabled.value||trigger.value?.matches(':disabled')||!option||option.disabled)return
  if(option.value!==value.value){emit('before-change');emit('update:modelValue',option.value)}
  close(true)
}
function move(step:number){
  const indexes=visible.value.map((_,i)=>i).filter(i=>!visible.value[i].disabled)
  if(!indexes.length){active.value=-1;return}
  const current=indexes.indexOf(active.value)
  active.value=indexes[(current<0?(step>0?0:indexes.length-1):current+step+indexes.length)%indexes.length];reveal()
}
function edge(last:boolean){
  const indexes=visible.value.map((_,i)=>i).filter(i=>!visible.value[i].disabled)
  active.value=(last?indexes.at(-1):indexes[0])??-1;reveal()
}
function typeAhead(key:string){
  clearTimeout(typeTimer);typeBuffer+=key.toLowerCase()
  typeTimer=setTimeout(()=>{typeBuffer=''},700)
  const index=visible.value.findIndex(option=>!option.disabled&&option.label.toLowerCase().startsWith(typeBuffer))
  if(index>=0){active.value=index;reveal()}
}
function tabAway(event:KeyboardEvent){
  // The menu is teleported; continue Tab from the trigger's place in the form.
  const controls=[...document.querySelectorAll<HTMLElement>('a[href],button,input,textarea,select,[tabindex]')]
    .filter(element=>element.tabIndex>=0&&!element.matches(':disabled')&&element.getClientRects().length&&!panel.value?.contains(element))
  const index=controls.indexOf(trigger.value),next=controls[index+(event.shiftKey?-1:1)]
  close()
  if(next){event.preventDefault();next.focus()}
  else trigger.value?.focus({preventScroll:true})
}
function keydown(event:KeyboardEvent){
  if(event.isComposing){event.stopPropagation();return}
  if(event.metaKey||event.ctrlKey){
    // Preserve save shortcuts, but do not send text undo to the graph editor.
    if(event.key.toLowerCase()!=='s')event.stopPropagation()
    return
  }
  if(event.altKey)return
  const key=event.key,isSearch=event.target===searchInput.value
  if(key==='Tab'){if(opened.value){event.stopPropagation();tabAway(event)}return}
  if(key==='Escape'){if(opened.value){event.preventDefault();event.stopPropagation();close(true)}return}
  if(['ArrowDown','ArrowUp','Enter'].includes(key)||(!isSearch&&key===' ')){
    event.preventDefault();event.stopPropagation()
    if(!opened.value){void open(key==='ArrowUp');return}
    if(key==='ArrowDown'||key==='ArrowUp')move(key==='ArrowDown'?1:-1)
    else choose(active.value)
    return
  }
  if(opened.value&&!isSearch&&(key==='Home'||key==='End')){event.preventDefault();event.stopPropagation();edge(key==='End');return}
  if(!isSearch&&key.length===1){
    event.preventDefault();event.stopPropagation()
    if(!opened.value){void open(false,searchable.value?key:'').then(()=>{if(!searchable.value)typeAhead(key)})}
    else typeAhead(key)
  }
}
function outside(event:Event){const target=event.target as Node;if(!trigger.value?.contains(target)&&!panel.value?.contains(target))close()}
function clearSearch(){query.value='';searchInput.value?.focus()}
function listen(add:boolean){
  const method=add?'addEventListener':'removeEventListener'
  document[method]('pointerdown',outside,true);document[method]('focusin',outside,true)
  window[method]('scroll',place,true);window[method]('resize',place)
  window.visualViewport?.[method]('resize',place);window.visualViewport?.[method]('scroll',place)
}
watch(opened,value=>{listen(value);if(!value){typeBuffer='';clearTimeout(typeTimer)}})
watch(disabled,value=>{if(value)close()})
watch([visible,value],()=>{if(opened.value){active.value=initialIndex();place();reveal()}})
onMounted(()=>{
  const updateDisabled=()=>{fieldsetDisabled.value=!!trigger.value?.closest('fieldset[disabled]')}
  updateDisabled();fieldsetObserver=new MutationObserver(updateDisabled)
  for(let parent=trigger.value?.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='FIELDSET')fieldsetObserver.observe(parent,{attributes:true,attributeFilter:['disabled']})
  resizeObserver=new ResizeObserver(place);if(trigger.value)resizeObserver.observe(trigger.value)
})
onBeforeUnmount(()=>{listen(false);resizeObserver?.disconnect();fieldsetObserver?.disconnect();clearTimeout(typeTimer)})
</script>

<template>
  <div class="app-select">
    <button ref="trigger" v-bind="attrs" type="button" role="combobox" class="app-select-trigger"
      :class="{'is-open':opened,'is-placeholder':!selected&&!value}" :disabled="disabled" :aria-disabled="disabled"
      :aria-expanded="opened" aria-haspopup="listbox" :aria-controls="uid+'-list'" :aria-activedescendant="opened?activeId:undefined"
      :title="caption" @click="opened?close():open()" @keydown="keydown">
      <span class="app-select-caption">{{caption}}</span>
      <svg class="app-select-chevron" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
    <Teleport to="body">
      <div v-if="opened" ref="panel" class="app-select-panel" :style="position" @keydown="keydown">
        <div v-if="searchable" class="app-select-search">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="m10.5 10.5 3 3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
          <input ref="searchInput" v-model="query" type="search" aria-label="搜索选项" :aria-controls="uid+'-list'" :aria-activedescendant="activeId" placeholder="搜索选项…" autocomplete="off">
          <button v-if="query" type="button" aria-label="清空搜索" class="app-select-clear" tabindex="-1" @click="clearSearch">×</button>
        </div>
        <div :id="uid+'-list'" ref="list" role="listbox" :aria-label="String(attrs['aria-label']||'可选项')" :aria-activedescendant="activeId" tabindex="-1" class="app-select-options">
          <button v-for="(option,index) in visible" :id="uid+'-option-'+index" :key="option.value+'-'+index"
            type="button" role="option" tabindex="-1" class="app-select-option" :title="option.label"
            :disabled="option.disabled" :aria-disabled="!!option.disabled" :aria-selected="option.value===value"
            :class="{'is-active':active===index,'is-selected':option.value===value}" :data-active="active===index"
            @pointermove="!option.disabled&&(active=index)" @mousedown.prevent @click="choose(index)">
            <span>{{option.label}}</span><span v-if="option.value===value" class="app-select-check" aria-hidden="true">✓</span>
          </button>
          <div v-if="!visible.length" class="app-select-empty">{{query?'没有匹配的选项':'暂无可选项'}}</div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.app-select{display:block;width:100%;min-width:0;margin-top:5px}
.app-select-trigger{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;min-height:41px;padding:9px 11px;border:1px solid var(--line-2);border-radius:7px;background:var(--paper);color:var(--ink);font:inherit;font-size:14px;line-height:1.6;text-align:left;cursor:pointer;transition:border-color .12s}
.app-select-trigger:hover:not(:disabled){border-color:var(--blue-line);color:var(--ink)}
.app-select-trigger.is-open{border-color:var(--blue)}
.app-select-trigger:disabled{opacity:1;background:var(--paper-2);border-color:var(--line);color:var(--muted);cursor:not-allowed}
.app-select-trigger.is-placeholder{color:var(--faint)}.app-select-caption{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.app-select-chevron{flex:none;color:var(--faint);transition:transform .12s}.is-open .app-select-chevron{transform:rotate(180deg)}
.app-select-panel{z-index:1000;display:flex;flex-direction:column;overflow:hidden;box-sizing:border-box;padding:5px;border:1px solid var(--line);border-radius:9px;background:var(--paper);color:var(--ink);box-shadow:var(--shadow-1);font:14px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif}
.app-select-search{display:flex;align-items:center;gap:8px;margin:3px 3px 7px;padding:0 9px;border:1px solid var(--line);border-radius:6px;background:var(--paper-2);color:var(--faint);flex:none}
.app-select-search input{min-width:0;width:100%;padding:8px 0;margin:0;border:0;background:transparent;box-shadow:none;outline:none;color:var(--ink);font:inherit;font-size:13px;line-height:1.6}
.app-select-search input::-webkit-search-cancel-button{display:none}.app-select-search svg{flex:none}.app-select-search:focus-within{border-color:var(--blue-line)}
.app-select-clear{flex:none;border:0;padding:0;width:20px;min-height:24px;background:transparent;color:var(--faint);font-size:18px;line-height:1;cursor:pointer}
.app-select-options{min-height:0;overflow:auto;overscroll-behavior:contain;scrollbar-width:thin;outline:none}
.app-select-option{display:flex;align-items:center;gap:12px;width:100%;min-height:38px;padding:8px 10px;border:0;border-radius:5px;background:transparent;color:var(--ink);font:inherit;text-align:left;cursor:pointer}
.app-select-option>span:first-child{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.app-select-option.is-active{background:var(--paper-2)}.app-select-option.is-selected{background:var(--blue-soft);color:var(--blue-ink)}
.app-select-option:disabled{opacity:.5;color:var(--muted);background:transparent;cursor:not-allowed}.app-select-check{flex:none;color:var(--blue-ink);font-weight:650}.app-select-empty{padding:21px 12px;text-align:center;color:var(--faint);font-size:13px}
</style>

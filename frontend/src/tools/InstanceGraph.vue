<script setup lang="ts">
import {computed,ref,watch,onMounted,onBeforeUnmount} from 'vue'
import cytoscape from 'cytoscape'
import type {Core,CollectionReturnValue} from 'cytoscape'
import type {ExplorerObject,ExplorerLink} from './explorerTypes'
import AppSelect from '../shared/AppSelect.vue'
import {INSTANCE_GRAPH, instanceNodeColor} from '../shared/graphStyle'

const props=defineProps<{objects:ExplorerObject[];links:ExplorerLink[];selectedId?:string}>()
const emit=defineEmits<{select:[id:string]}>()
const canvas=ref<HTMLElement>(),layout=ref('breadthfirst'),selection=ref(''),neighborsOnly=ref(false)
const typeIds=computed(()=>[...new Set(props.objects.map(o=>o.type))].sort((a,b)=>a.localeCompare(b)))
const legend=computed(()=>typeIds.value.map(id=>({id,name:props.objects.find(o=>o.type===id)?.typeName||id,color:color(id)})))
const selected=computed(()=>props.objects.find(o=>o.id===selection.value))
const validLinks=computed(()=>{const ids=new Set(props.objects.map(o=>o.id));return props.links.filter(l=>ids.has(l.source)&&ids.has(l.target))})
const omitted=computed(()=>props.links.length-validLinks.value.length)
let cy:Core|undefined,observer:ResizeObserver|undefined
function color(id:string){return instanceNodeColor(typeIds.value.indexOf(id))}
// Prefix separate identifier namespaces; never interpolate business IDs into selectors.
function nodeId(id:string){return 'object:'+id}
function arrange(){
  if(!cy||!props.objects.length)return
  cy.elements(':visible').layout({name:layout.value,animate:false,fit:false,padding:45,nodeDimensionsIncludeLabels:true,
    ...(layout.value==='breadthfirst'?{directed:true,spacingFactor:1.6}:{}),
    ...(layout.value==='cose'?{randomize:false,nodeRepulsion:()=>40000,idealEdgeLength:()=>240,componentSpacing:150}: {}),
    stop:()=>fit()
  } as any).run()
}
function fitElements(elements:CollectionReturnValue,padding=45){
  if(!cy||!elements.length)return
  cy.fit(elements,padding)
  if(cy.zoom()>1.2)cy.zoom(1.2)
  cy.center(elements)
}
function fit(){if(cy)fitElements(cy.elements(':visible'))}
function zoom(factor:number){if(cy)cy.zoom({level:cy.zoom()*factor,renderedPosition:{x:cy.width()/2,y:cy.height()/2}})}
function applySelection(){
  if(!cy)return
  cy.elements().removeClass('focused faded').style('display','element')
  const node=cy.getElementById(nodeId(selection.value))
  if(node.length){const vicinity=node.closedNeighborhood();node.addClass('focused');cy.elements().difference(vicinity).addClass('faded');if(neighborsOnly.value)cy.elements().difference(vicinity).style('display','none')}
  else neighborsOnly.value=false
}
function select(id:string,notify=true){selection.value=id;applySelection();if(notify)emit('select',id)}
function reset(){neighborsOnly.value=false;select('');arrange()}
function focus(){if(!cy)return;const node=cy.getElementById(nodeId(selection.value));if(node.length)fitElements(node.closedNeighborhood(),65);else fit()}
function update(){
  if(!cy)return
  cy.elements().remove()
  const seen=new Set<string>()
  cy.add(props.objects.filter(o=>{if(seen.has(o.id))return false;seen.add(o.id);return true}).map(o=>({data:{id:nodeId(o.id),objectId:o.id,label:o.name||o.id,color:color(o.type)}})))
  cy.add(validLinks.value.map((l,i)=>({data:{id:'link:'+i+':'+l.id,source:nodeId(l.source),target:nodeId(l.target),label:l.name||l.type}})))
  if(selection.value&&!seen.has(selection.value))select('')
  applySelection();arrange()
}
function exportPng(){if(!cy||!props.objects.length)return;const a=document.createElement('a');a.href=cy.png({full:true,bg:INSTANCE_GRAPH.exportBg,scale:2,maxWidth:4096,maxHeight:4096});a.download='储能对象关系图.png';a.click()}
function keydown(e:KeyboardEvent){if(e.target!==canvas.value||e.ctrlKey||e.metaKey||e.altKey)return;if(e.key.toLowerCase()==='f'){e.preventDefault();e.stopPropagation();focus()}else if(e.key==='Escape'){e.preventDefault();e.stopPropagation();neighborsOnly.value=false;select('')}}
function text(value:unknown){if(value==null)return '—';return typeof value==='object'?JSON.stringify(value,null,2):String(value)}
watch(()=>[props.objects,props.links],update,{deep:true})
watch(()=>props.selectedId,id=>select(id||'',false))
watch(neighborsOnly,()=>{applySelection();fit()})
watch(layout,arrange)
onMounted(()=>{
  cy=cytoscape({container:canvas.value,elements:[],minZoom:.08,maxZoom:3,wheelSensitivity:.2,boxSelectionEnabled:false,autounselectify:true,style:[
    {selector:'node',style:{'background-color':'data(color)',label:'data(label)',width:42,height:42,'font-size':12,'text-valign':'bottom','text-margin-y':9,'text-wrap':'wrap','text-max-width':'145px',color:INSTANCE_GRAPH.nodeText,'border-width':3,'border-color':INSTANCE_GRAPH.nodeBorder}},
    {selector:'edge',style:{width:1.6,'line-color':INSTANCE_GRAPH.edgeLine,'target-arrow-color':INSTANCE_GRAPH.edgeLine,'target-arrow-shape':'triangle','curve-style':'bezier',label:'data(label)','font-size':11,color:INSTANCE_GRAPH.edgeLabel,'text-background-color':INSTANCE_GRAPH.labelBg,'text-background-opacity':.9,'text-background-padding':'3px','text-rotation':'autorotate'}},
    {selector:'.focused',style:{'border-color':INSTANCE_GRAPH.focused,'border-width':5}},
    {selector:'.faded',style:{opacity:.18}}
  ]})
  cy.on('tap','node',e=>{canvas.value?.focus({preventScroll:true});select(e.target.data('objectId'))})
  cy.on('tap',e=>{if(e.target===cy){canvas.value?.focus({preventScroll:true});select('')}})
  selection.value=props.selectedId||'';update()
  observer=new ResizeObserver(()=>{cy?.resize()});if(canvas.value)observer.observe(canvas.value)
})
onBeforeUnmount(()=>{observer?.disconnect();cy?.destroy();cy=undefined})
</script>

<template>
  <section class="instance-graph">
    <div class="graph-toolbar">
      <div class="layout-select"><AppSelect v-model="layout" aria-label="关系图布局" :options="[{value:'cose',label:'力导向布局'},{value:'breadthfirst',label:'层级布局'},{value:'circle',label:'环形布局'},{value:'grid',label:'网格布局'}]" /></div>
      <button type="button" @click="fit">适应全图</button><button type="button" aria-label="放大关系图" @click="zoom(1.25)">＋</button><button type="button" aria-label="缩小关系图" @click="zoom(.8)">−</button>
      <button type="button" :disabled="!selected" @click="focus">聚焦所选</button>
      <label class="neighbor-toggle"><input v-model="neighborsOnly" type="checkbox" :disabled="!selected">仅一跳关联</label>
      <button type="button" @click="reset">重置</button><button type="button" :disabled="!objects.length" @click="exportPng">导出 PNG</button>
    </div>
    <div class="graph-legend"><span v-for="item in legend" :key="item.id"><i :style="{background:item.color}"></i>{{item.name}}</span><span class="graph-count">{{objects.length}} 个对象 · {{validLinks.length}} 条关联</span></div>
    <p v-if="omitted" class="graph-notice">{{omitted}} 条关联的端点不在当前结果内，因此未展示。</p>
    <div class="graph-body">
      <div class="graph-stage"><div ref="canvas" class="graph-canvas" tabindex="0" aria-label="只读对象关系图。点击对象查看详情，F 聚焦，Escape 清除选择" @keydown="keydown"></div><div v-if="!objects.length" class="graph-empty">暂无符合条件的对象，请调整上方筛选条件。</div><div class="canvas-hint">拖动画布平移 · 滚轮缩放 · 聚焦画布后 F 聚焦，Esc 清除选择</div></div>
      <aside v-if="selected" class="graph-details" aria-label="所选对象详情">
        <div class="detail-heading"><div><small>{{selected.typeName}}</small><h3>{{selected.name}}</h3></div><button type="button" aria-label="关闭对象详情" @click="select('')">×</button></div>
        <p class="object-id">{{selected.id}}</p>
        <p v-if="selected.source" class="detail-source">来源：{{selected.source}}</p>
        <dl><template v-for="(value,key) in selected.properties" :key="key"><dt>{{selected.propertyLabels[key]||key}}</dt><dd>{{text(value)}}</dd></template></dl>
        <p v-if="!Object.keys(selected.properties).length" class="detail-source">该对象暂无属性值。</p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.instance-graph{background:var(--paper);border:1px solid var(--line);border-radius:var(--r-md);overflow:hidden;color:var(--ink)}.graph-toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:12px;border-bottom:1px solid var(--line)}.graph-toolbar button{padding:7px 10px;font-size:13px}.detail-heading button{font-size:13px}.layout-select{width:155px}.layout-select :deep(.app-select){margin-top:0}.neighbor-toggle{display:flex;align-items:center;gap:6px;font-size:13px;margin:0 5px}.neighbor-toggle input{width:auto;margin:0}.graph-legend{display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:12px 16px;font-size:12px;color:var(--muted)}.graph-legend span{display:flex;gap:7px;align-items:center}.graph-legend i{width:10px;height:10px;border-radius:50%}.graph-count{margin-left:auto}.graph-notice{margin:0;padding:8px 16px;background:var(--warn-soft);font-size:12px;color:var(--warn)}.graph-body{display:flex;min-height:480px}.graph-stage{position:relative;flex:1;min-width:0}.graph-canvas{height:520px;width:100%;background:radial-gradient(var(--grid) 1px,transparent 1px);background-size:18px 18px}.graph-canvas:focus-visible{outline:2px solid var(--focus);outline-offset:-2px}.canvas-hint{bottom:10px;left:12px;font-size:11px}.graph-empty{position:absolute;inset:0;display:grid;place-items:center;color:var(--faint);pointer-events:none}.graph-details{width:290px;flex-shrink:0;padding:18px;border-left:1px solid var(--line);max-height:520px;overflow:auto;box-sizing:border-box}.detail-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}.detail-heading small{color:var(--muted)}.detail-heading h3{margin:7px 0;font-size:17px;overflow-wrap:anywhere}.detail-heading button{padding:2px 8px;font-size:20px}.object-id,.detail-source{font-size:12px;color:var(--faint);overflow-wrap:anywhere}.graph-details dl{margin:20px 0 0}.graph-details dt{font-size:12px;color:var(--muted);margin-top:14px}.graph-details dd{font-size:13px;margin:5px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}@media(max-width:900px){.graph-body{flex-direction:column}.graph-details{width:auto;max-height:340px;border-left:0;border-top:1px solid var(--line)}.graph-canvas{height:420px}.graph-count{margin-left:0}}
</style>

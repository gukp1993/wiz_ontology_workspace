<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,reactive,ref,watch} from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import InstanceTable from './InstanceTable.vue'
import InstanceGraph from './InstanceGraph.vue'
import InstanceCharts from './InstanceCharts.vue'
import InstanceMap from './InstanceMap.vue'
import { explorerSnapshot } from '../ontology/api'
import {matchesObject,type ExplorerFilter} from './explorerModel'
import type {ExplorerSnapshot} from './explorerTypes'
const props=defineProps<{state:any;projectState?:any}>(),emit=defineEmits<{navigate:[page:string]}>()
const data=ref<ExplorerSnapshot|null>(null),loading=ref(false),error=ref(''),view=ref('objects'),selected=ref(''),saveName=ref(''),notice=ref('')
const filter=reactive<ExplorerFilter>({query:'',type:'',property:'',operator:'contains',value:''})
type Saved={name:string;filter:ExplorerFilter;view:string}
const storageKey='ontology-studio-explorations-v1-'+(props.state.workspaceId||'storage'),saved=ref<Saved[]>([])
let controller:AbortController|null=null
const modes=[{value:'objects',label:'列表与表格'},{value:'graph',label:'关系图'},{value:'charts',label:'图表与时间序列'},{value:'map',label:'地理分布'}]
const types=computed(()=>[...new Map((data.value?.objects||[]).map(o=>[o.type,o.typeName])).entries()].map(([value,label])=>({value,label})))
const properties=computed(()=>[...new Set((data.value?.objects||[]).filter(o=>!filter.type||o.type===filter.type).flatMap(o=>Object.keys(o.properties)))].map(k=>({value:k,label:data.value?.objects.find(o=>o.propertyLabels[k])?.propertyLabels[k]||k})))
const objects=computed(()=>(data.value?.objects||[]).filter(o=>matchesObject(o,filter)))
const links=computed(()=>{const ids=new Set(objects.value.map(o=>o.id));return (data.value?.links||[]).filter(l=>ids.has(l.source)&&ids.has(l.target))})
// 演示执行边界提示：merged() 过滤掉的非字符串属性来源随响应 bindings.unsupportedSources 返回
const unsupportedHint=computed(()=>{
 const list=(data.value as any)?.bindings?.unsupportedSources
 if(!Array.isArray(list)||!list.length)return ''
 const labels=[...new Set(list.filter((s:any)=>s&&typeof s.property==='string').map((s:any)=>String(s.property)+'（'+(s.kind?String(s.kind):'未知')+'）'))]
 if(!labels.length)return ''
 return '以下属性为配置态来源，演示执行未支持：'+labels.join('、')+'；配置校验通过不代表可执行'
})
watch(properties,keys=>{if(!keys.some(k=>k.value===filter.property))filter.property=''})
watch(objects,rows=>{if(!rows.some(o=>o.id===selected.value))selected.value=''})
async function refresh(){controller?.abort();const active=new AbortController();controller=active;loading.value=true;error.value='';try{const body=await explorerSnapshot({state:props.state,projectState:props.projectState||undefined},{signal:active.signal});if(active!==controller)return;data.value=body;if(body.errors?.length)error.value=body.errors.join('；')}catch(e){if(e.name!=='AbortError')error.value=e.message}finally{if(active===controller)loading.value=false}}
function reset(){Object.assign(filter,{query:'',type:'',property:'',operator:'contains',value:''});selected.value=''}
function select(id:string){if(!id){selected.value='';return}if(!objects.value.some(o=>o.id===id))reset();selected.value=id}
function selectFromChart(id:string){select(id);view.value='objects'}
function persist(){try{localStorage.setItem(storageKey,JSON.stringify(saved.value));notice.value='已保存到当前浏览器。'}catch{notice.value='浏览器存储不可用，探索配置仅在当前页面保留。'}}
function saveExploration(){const name=saveName.value.trim();if(!name){notice.value='请先填写探索名称。';return}const entry={name,filter:{...filter},view:view.value};saved.value=[...saved.value.filter(e=>e.name!==name),entry].slice(-20);persist();saveName.value=''}
function restore(entry:Saved){Object.assign(filter,entry.filter);view.value=entry.view;selected.value='';notice.value='已恢复筛选条件；展示当前加载的数据。'}
function removeSaved(name:string){saved.value=saved.value.filter(e=>e.name!==name);persist()}
onMounted(()=>{try{const parsed=JSON.parse(localStorage.getItem(storageKey)||'[]');if(Array.isArray(parsed))saved.value=parsed.filter(e=>typeof e.name==='string'&&e.filter&&['query','type','property','operator','value'].every(k=>typeof e.filter[k]==='string')&&modes.some(m=>m.value===e.view)).slice(-20)}catch{}refresh()})
onBeforeUnmount(()=>controller?.abort())
</script>
<template><section class="instance-explorer"><div class="card"><div class="panelhead"><div><h2>对象数据浏览</h2><p class="muted">当前项目的模拟数据 · 按当前草稿映射读取 · 不修改本体定义</p></div><div class="tools"><button :disabled="loading" @click="refresh">{{loading?'加载中…':'刷新数据'}}</button><button @click="emit('navigate','instances')">SOC计算预览</button></div></div><div class="filters"><label>搜索对象<input v-model="filter.query" type="search" placeholder="名称、标识或属性值" aria-label="搜索实例"></label><label>对象类型<AppSelect v-model="filter.type" aria-label="筛选对象类型" :options="[{value:'',label:'全部类型'},...types]"/></label><button @click="reset">清空筛选</button></div><details class="technical-section"><summary>属性筛选与保存探索</summary><div class="filters"><label>筛选属性<AppSelect v-model="filter.property" aria-label="筛选属性" :options="[{value:'',label:'不限制属性'},...properties]"/></label><label>条件<AppSelect v-model="filter.operator" aria-label="筛选条件" :options="[{value:'contains',label:'包含文本'},{value:'eq',label:'等于'},{value:'gte',label:'数值大于等于'},{value:'lte',label:'数值小于等于'},{value:'present',label:'有值'},{value:'missing',label:'缺值'}]"/></label><label v-if="!['present','missing'].includes(filter.operator)">比较值<input v-model="filter.value" aria-label="筛选比较值" placeholder="输入文本或数值"></label></div><div class="filters"><label>探索名称<input v-model="saveName" aria-label="探索名称" placeholder="例如：额定容量至少100的设备" @keydown.enter="saveExploration"></label><button @click="saveExploration">保存筛选与视图</button></div><p class="muted">仅保存到当前浏览器，不保存数据快照；同名覆盖，最多保留20项。</p><div class="saved" v-for="entry in saved" :key="entry.name"><button @click="restore(entry)">{{entry.name}}</button><button :aria-label="'删除探索 '+entry.name" @click="removeSaved(entry.name)">删除</button></div><p v-if="notice" role="status">{{notice}}</p></details><div class="display-tabs" role="tablist" aria-label="实例展示形式"><button v-for="m in modes" :key="m.value" role="tab" :aria-selected="view===m.value" :class="{active:view===m.value}" @click="view=m.value">{{m.label}}</button></div><p v-if="data" class="muted">{{objects.length}} / {{data.objects.length}} 个对象 · 筛选内 {{links.length}} 条关系。图表与关系图跟随上方筛选。</p></div><div v-if="error" class="card error" role="alert">{{error}} <button @click="refresh">重试</button></div><p v-for="w in data?.warnings||[]" :key="w" class="muted">{{w}}</p><p v-if="unsupportedHint" class="muted" role="note">{{unsupportedHint}}</p><div v-if="loading&&!data" class="card">正在读取项目实例…</div><template v-if="data&&!error"><InstanceTable v-if="view==='objects'" :objects="objects" :all-objects="data.objects" :links="data.links" :selected-id="selected" @select="select"/><InstanceGraph v-else-if="view==='graph'" :objects="objects" :links="links" :selected-id="selected" @select="select"/><InstanceCharts v-else-if="view==='charts'" :objects="objects" :observations="data.observations" @select="selectFromChart"/><InstanceMap v-else :objects="objects" @select="selectFromChart"/></template></section></template>
<style scoped>.filters{display:flex;align-items:end;gap:14px;flex-wrap:wrap}.filters label{flex:1;min-width:180px}.filters input{margin-top:5px}.filters>button{margin-bottom:2px}.display-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-top:22px;border-top:1px solid var(--line);padding-top:16px}.display-tabs button.active{background:var(--blue);color:var(--paper);border-color:var(--blue)}.saved{display:inline-flex;gap:4px;margin:4px 10px 4px 0}.saved button:last-child{font-size:12px}.instance-explorer .panelhead{gap:16px;flex-wrap:wrap}.instance-explorer .error{color:var(--danger)}</style>

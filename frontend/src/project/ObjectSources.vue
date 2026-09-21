<script setup lang="ts">
// 实例识别（T04 · 原型 identityView / identity、source 编辑态）：
// 浏览态默认只读摘要（实例来源方式、连接、身份表、实例主键 + 「配置／修改」），补充来源折叠在
// 「已登记补充来源（可选）」details 里（可为零个或多个数据库表／Redis 来源）。
// 修改进入独立表单：顶部「实例来源方式」（数据库表／视图 | 项目登记），database 沿用连接 → 表 →
// 主键联动下拉；registered 隐藏数据库表单，挂载 RegisteredInstances 编辑实例草稿。
// 局部草稿、保存走 inject('form-save') 直通持久化，取消放弃；打开期间注册 T00 表单守卫。
import {computed,inject,onBeforeUnmount,ref,watch} from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import RegisteredInstances from './RegisteredInstances.vue'
import MappingDescription from './MappingDescription.vue'
import {descTextOf,commitDesc} from './bindingModel'
import {sourcesOf,commitSources,dropSource,propertyView,catalogOf,tableCatalog,tableOptions,fieldOptions,newSourceId,refreshCatalogOf,bindingIdentityOf,registeredInstancesOf,commitRegisteredIdentity,clearRegisteredIdentity,registeredBlockingItemsOf,databaseIdentityDependentsOf,dropRegisteredInstance} from './bindingModel'
import type {RegisteredInstanceView,IdentityRefItem} from './bindingModel'
import {effectiveProperty} from '../ontology/propertyModel'
import type {FormGuardAPI,FormSaveAPI} from '../app/formGuard'
import AssistPanel from '../assist/AssistPanel.vue'
import {identityAssistBinding} from '../assist/identityLinkBindings'
import type {AssistApi} from '../assist/useAssistPanel'
const props=defineProps<{projectState:any;refState:any;b:any}>()
const emit=defineEmits(['before-change','changed','edit-state','go-tab'])
function before(){emit('before-change')}
function changed(){emit('changed')}
function mutate(fn){before();fn();changed()}
// 旧格式一次性迁移：related_sources 存在时立即转为 sources（安全迁移，编辑即产生新草稿修订）
watch(()=>props.b,()=>{if(props.b?.related_sources?.length)mutate(()=>commitSources(props.b,sourcesOf(props.b)))},{immediate:true})
const graph=computed(()=>props.refState?.ontology?.['@graph']||[])
const connections=computed(()=>props.projectState.connections?.connections||[])
const connName=(id:string)=>{const c=connections.value.find((x:any)=>x.id===id);return c?(c.name||c.id):''}
const mysqlOptions=computed(()=>connections.value.filter((c:any)=>c.engine==='mysql').map((c:any)=>({value:c.id,label:c.name||c.id})))
const redisOptions=computed(()=>connections.value.filter((c:any)=>c.engine==='redis').map((c:any)=>({value:c.id,label:c.name||c.id})))
const sources=computed<any[]>(()=>sourcesOf(props.b))
const identityCatalog=computed(()=>tableCatalog(props.projectState,props.b?.connection,props.b?.table))
const objectName=computed(()=>{const n=graph.value.find((x:any)=>x['@id']==='mg:'+props.b?.object_type);return n?.['rdfs:label']||props.b?.object_type||''})
// 显示名称跟随本体引用版本的属性标记，项目层不再单独选择
const titleInfo=computed(()=>{
  const marker=(graph.value||[]).find(n=>n['@type']==='owl:DatatypeProperty'&&n['rdfs:domain']?.['@id']==='mg:'+props.b?.object_type&&(effectiveProperty(n,graph.value)['mg:isDisplayName']?.['@value']===true))
  if(!marker)return null
  const merged=effectiveProperty(marker,graph.value)
  return merged['rdfs:label']||marker['@id'].slice(3)
})
function sourceSummaryText(s:any){
  // 未知 kind（非 db/redis）：当前协议不认识其结构，只原样保留、不提供编辑入口（零丢失，E03）。
  if(s.kind!=='db'&&s.kind!=='redis')return (s.name||s.id||'未命名来源')+' · 当前版本未识别的来源结构，已原样保留'
  if(s.kind==='redis')return (s.name||'未命名 Redis 来源')+' · '+(s.connection?connName(s.connection):'未选连接')
  return (s.name||s.table||'未命名数据库来源')+' · '+(s.connection?connName(s.connection):'未选连接')+' · '+(s.table||'未选表')
    +' · '+(props.b.table||'实例表')+'.'+(s.matchLeft||'字段')+' ＝ '+(s.table||'本表')+'.'+(s.matchRight||'字段')
    +(s.cardinality==='many'?' · 零或多条':' · 零或一条')
}
// 只有当前协议认识的来源形态可进编辑表单；未知形态若被打开将以错误形态回写，故只保留「移除」入口。
function sourceEditable(s:any):boolean{return s.kind==='db'||s.kind==='redis'}
// ---------- 登记身份（无表对象）：读 b.identity，缺省 database ----------
const identityMode=computed(()=>bindingIdentityOf(props.b))
const savedInstances=computed(()=>registeredInstancesOf(props.b))
// 切换影响条目的可读名（属性/链接按本体引用版本解析显示名，解析不到回退键）
function propertyLabelOf(api:string){const n=graph.value.find((x:any)=>x['@id']==='mg:'+api||x['mg:apiName']===api);return String(n?effectiveProperty(n,graph.value)['rdfs:label']||api:api)}
function relationLabelOf(id:string){const n=graph.value.find((x:any)=>x['@type']==='owl:ObjectProperty'&&x['@id']==='mg:'+id);return String(n?.['rdfs:label']||id)}
function refItemLabel(i:IdentityRefItem){return (i.type==='property'?'属性「'+propertyLabelOf(i.key)+'」':i.type==='relation'?'链接「'+relationLabelOf(i.key)+'」':'补充来源「'+i.key+'」')+'（'+i.note+'）'}
// ---------- 编辑态：实例识别 / 补充来源（局部草稿，保存才写回 b） ----------
type EditorKind='identity'|'source'|'note'|null
const editing=ref<EditorKind>(null)
const identityDraft=ref<{mode:'database'|'registered';connection:string;table:string;primary_key:string;instances:RegisteredInstanceView[]}>({mode:'database',connection:'',table:'',primary_key:'',instances:[]})
const sourceDraft=ref<any>(null)
const message=ref(''),tableChangeNote=ref(''),removeMessage=ref('')
const switchNote=ref(''),switchBlock=ref('')
const refreshing=ref(''),refreshMessage=ref('')
const saving=ref(false)
// 说明草稿（对象级）：进入说明编辑态时快照原文，保存经 formSave 直通写入 mappingDescriptions
const noteDraft=ref(''),noteBaseline=ref('')
const savedNote=computed(()=>descTextOf(props.projectState,'objects',props.b?.object_type||''))
const editingOpen=computed(()=>editing.value!==null)
const formGuard=inject<FormGuardAPI|null>('form-guard',null)
const formSave=inject<FormSaveAPI|null>('form-save',null)
// 保存直通兜底：未接入 form-save（独立挂载）时退回自动保存
async function submit(apply:()=>void):Promise<{ok:boolean;message:string}>{
  if(formSave)return formSave.submitForm('project',apply)
  before();apply();changed();return {ok:true,message:''}
}
// ── 辅助填写（2026-09-21 T8，P1 实例识别）：编辑态挂 AssistPanel，采纳只改本地草稿 ──
// 面板 binding 由 assist/identityLinkBindings 的 identityAssistBinding 构造（白名单快照与合并，
// primaryKey↔primary_key、note↔noteDraft 映射；registered 模式快照只含 mode/note）。边界与本体区
// 一致：采纳绝不触发表单保存（含 form-save/commit-now/touch），持久化由用户点「保存」走既有
// submitForm 链路；采纳进来的说明随实例识别保存一起经既有 commitDesc 落盘。手改字段经
// assistTouched 通知面板置过期；编辑器关闭面板收起。登记实例清单不在辅助范围。
const assistApi=inject<AssistApi|null>('assist-api',null) // 测试注入桩；缺省 null → 面板内部用 defaultAssistApi()
const assistOpen=ref(false)
const assistPanelRef=ref<{notifyDraftChanged():void}|null>(null)
const assistTouchTick=ref(0) // 手改字段次数（测试观察点；面板通知经模板 ref，SSR 下为 null 自动跳过）
const assistBinding=computed(()=>{
  if(editing.value!=='identity')return null
  return identityAssistBinding(()=>identityDraft.value,noteDraft,{
    projectId:String(props.projectState?.projectId||''),
    targetId:String(props.b?.object_type||''),
    contextTitle:'配置「'+objectName.value+'」的实例识别',
    applyMode:switchIdentityMode, // 复用既有切换拦截（登记引用未清理时阻止切回数据库表／视图）
  })
})
watch(assistBinding,b=>{if(!b)assistOpen.value=false})
function toggleAssist(){assistOpen.value=!assistOpen.value}
// 手改字段 → 通知面板：旧建议过期、解除撤销保护；采纳/撤销写草稿由面板自管草稿指纹，不经此路径。
function assistTouched(){assistTouchTick.value++;assistPanelRef.value?.notifyDraftChanged()}
let identityBaseline='',sourceBaseline=''
const dirty=computed(()=>{
  if(editing.value==='note')return noteDraft.value!==noteBaseline.value
  if(editing.value==='identity')return JSON.stringify(identityDraft.value)!==identityBaseline||noteDraft.value!==noteBaseline.value
  if(editing.value==='source')return JSON.stringify(sourceDraft.value)!==sourceBaseline
  return false
})
const guard={isDirty:()=>dirty.value,discard:()=>closeEditor()}
watch(editingOpen,open=>{emit('edit-state',open);open?formGuard?.register(guard):formGuard?.unregister(guard)})
onBeforeUnmount(()=>formGuard?.unregister(guard))
function openIdentity(){
  identityDraft.value={mode:bindingIdentityOf(props.b),connection:String(props.b.connection||''),table:String(props.b.table||''),primary_key:String(props.b.primary_key||''),instances:registeredInstancesOf(props.b)}
  identityBaseline=JSON.stringify(identityDraft.value)
  // 说明草稿对齐到已保存说明：辅助面板快照里的 note 反映真实当前值（与说明编辑态打开时同一语义）
  noteDraft.value=savedNote.value;noteBaseline.value=savedNote.value
  message.value='';tableChangeNote.value='';refreshMessage.value='';switchNote.value='';switchBlock.value=''
  editing.value='identity'
}
function openSource(s?:any){
  // 未知形态不进编辑表单：避免以其不理解的形态回写覆盖原结构（零丢失）。
  if(s&&!sourceEditable(s)){removeMessage.value='该来源的结构当前版本无法识别，为保留原内容不提供编辑；如确需更换，请移除后重新登记。';return}
  const base:any=s?JSON.parse(JSON.stringify(s)):{id:newSourceId(),name:'',kind:'db',connection:'',table:'',matchLeft:'',matchRight:'',cardinality:'one'}
  sourceDraft.value=base
  sourceBaseline=JSON.stringify(base)
  message.value='';refreshMessage.value=''
  editing.value='source'
}
function closeEditor(){editing.value=null;identityDraft.value={mode:'database',connection:'',table:'',primary_key:'',instances:[]};sourceDraft.value=null;message.value='';tableChangeNote.value='';refreshMessage.value='';switchNote.value='';switchBlock.value='';saving.value=false;assistOpen.value=false}
function openNote(){noteDraft.value=savedNote.value;noteBaseline.value=savedNote.value;message.value='';editing.value='note'}
async function saveNote(){
  if(saving.value)return
  saving.value=true
  const r=await submit(()=>commitDesc(props.projectState,'objects',props.b.object_type,null,noteDraft.value))
  saving.value=false
  if(r.ok)closeEditor()
  else message.value='保存未完成：'+r.message+'。说明内容已保留，可重试保存或取消。'
}
// --- 实例来源方式切换：只改本地草稿（不落盘）；registered→database 有不兼容引用时阻止 ---
const modeOptions=[{value:'database',label:'数据库表／视图'},{value:'registered',label:'项目登记'}]
function identityModeChanged(v:string){
  const target:'registered'|'database'=v==='registered'?'registered':'database'
  if(target===identityDraft.value.mode)return
  if(!switchIdentityMode(target))return
  assistTouched()
}
// 切换核心逻辑（下拉与辅助采纳共用）：登记引用未清理时阻止切回 database；成功改本地草稿并返回 true。
// 辅助采纳经此复用既有拦截与提示；手改通知由调用方（identityModeChanged）负责，采纳路径不触发。
function switchIdentityMode(target:'registered'|'database'):boolean{
  if(target===identityDraft.value.mode)return true
  if(target==='database'){
    const items=registeredBlockingItemsOf(props.b)
    if(items.length){
      switchBlock.value='不能切回「数据库表／视图」：以下配置依赖项目登记身份，请先到对应页签移除后再切换（不会静默删除）——'+items.map(refItemLabel).join('、')
      return false
    }
  }
  switchBlock.value=''
  identityDraft.value.mode=target
  if(target==='registered'){
    const deps=databaseIdentityDependentsOf(props.b)
    switchNote.value=deps.length
      ?'将改按项目登记识别实例；以下依赖数据库身份的配置会原样保留但暂不生效（切回数据库来源后恢复）——'+deps.map(refItemLabel).join('、')
      :'将改按项目登记识别实例，不依赖数据表；原有数据库连接与表配置保留不用，切回时可恢复。'
  }else switchNote.value=''
  return true
}
// --- 依赖顺序：连接 → 表（目录） → 字段；换连接清表与主键，换表清主键并提示补充来源需重配 ---
const draftIdentityCatalog=computed(()=>tableCatalog(props.projectState,identityDraft.value.connection,identityDraft.value.table))
function identityConnChanged(v:string){identityDraft.value.connection=v;identityDraft.value.table='';identityDraft.value.primary_key='';tableChangeNote.value='';refreshMessage.value='';assistTouched()}
function identityTableChanged(v:string){identityDraft.value.table=v;identityDraft.value.primary_key=''
  tableChangeNote.value=(props.b.table&&props.b.table!==v)||(props.b.connection&&props.b.connection!==identityDraft.value.connection)
    ?'已切换身份表：已登记补充来源的匹配配置会原样保留（不会自动清空，也不会按同名字段自动重绑）；如字段与新表不匹配，保存后由项目校验报告失效并阻断发布。':''
  assistTouched()}
function identityPkChanged(v:string){if(identityDraft.value.primary_key===v)return;identityDraft.value.primary_key=v;assistTouched()}
// --- 补充来源草稿：连接 → 表 → 匹配字段 ---
const draftSourceCatalog=computed(()=>sourceDraft.value&&sourceDraft.value.kind==='db'?tableCatalog(props.projectState,sourceDraft.value.connection,sourceDraft.value.table):null)
function sourceConnChanged(v:string){if(sourceDraft.value.connection===v)return;message.value='';sourceDraft.value.connection=v;sourceDraft.value.table='';sourceDraft.value.matchRight='';refreshMessage.value=''}
function sourceTableChanged(v:string){if(sourceDraft.value.table===v)return;message.value='';sourceDraft.value.table=v;sourceDraft.value.matchRight=''}
// --- 表结构目录刷新：真实读取，失败保留旧目录并显示脱敏原因 ---
async function refresh(connectionId:string){
  if(!connectionId||refreshing.value)return
  refreshing.value=connectionId;refreshMessage.value=''
  const out=await refreshCatalogOf(props.projectState,connectionId,mutate)
  refreshing.value=''
  refreshMessage.value=(out.ok?'目录已刷新：':'目录未刷新（')+out.message+(out.ok?'':'）；已保留旧选择')
}
// 引用检查：属性来源或链接映射引用此来源时禁止移除
function sourceUsed(id:string){
  for(const api of Object.keys(props.b.properties||{})){const v:any=propertyView(props.b,api);if(v&&v.source===id)return true}
  for(const r of props.b.relations||[]){if(r.sourceId===id||r.targetSourceId===id)return true}
  return false
}
async function removeSource(s:any){
  if(sourceUsed(s.id)){removeMessage.value='此来源仍被属性或链接引用，请先调整';return}
  if(!(await appConfirm({ message: '移除补充来源「'+(s.name||s.table||s.id)+'」？引用它的属性来源会被一并清除。', danger: true })))return
  removeMessage.value=''
  const r=await submit(()=>dropSource(props.b,s.id))
  if(!r.ok)removeMessage.value='移除未完成：'+r.message+'。可重试或取消。'
}
// 辅助采纳进来的说明（noteDraft）随本表单保存一起经既有 commitDesc 落盘（与链接映射保存共用
// 说明的机制一致）；未采纳/未修改说明时不产生额外修订。
function commitIdentityNote(){if(noteDraft.value!==noteBaseline.value)commitDesc(props.projectState,'objects',props.b.object_type,null,noteDraft.value)}
async function saveIdentity(){
  const d=identityDraft.value
  if(saving.value)return
  if(d.mode==='registered'){
    const list=d.instances.map(i=>({id:String(i.id||'').trim(),label:String(i.label||'').trim()}))
    if(list.some(i=>!i.id)){message.value='存在未填写编号的实例，请补全或删除后再保存。';return}
    const dup=list.map(i=>i.id).filter((v,i,a)=>a.indexOf(v)!==i)
    if(dup.length){message.value='实例编号重复：'+[...new Set(dup)].join('、')+'；同一对象内编号必须唯一。';return}
    message.value='';saving.value=true
    // registered：写 identity 块（不清空 connection/table/primary_key，后端按 identity 分支处理）；
    // 草稿中已删除的实例同步清掉 membership 规则行（UI 已阻止删除被引用实例，此处兜底）。
    const r=await submit(()=>{
      const kept=new Set(list.map(i=>i.id))
      for(const old of registeredInstancesOf(props.b))if(!kept.has(old.id))dropRegisteredInstance(props.b,old.id)
      commitRegisteredIdentity(props.b,list)
      commitIdentityNote()
    })
    saving.value=false
    if(r.ok)closeEditor()
    else message.value='保存未完成：'+r.message+'。表单内容已保留，可重试保存或取消。'
    return
  }
  if(!d.connection){message.value='请选择数据连接。';return}
  if(!d.table){message.value='请选择来源表。';return}
  if(!d.primary_key){message.value='请选择实例主键。';return}
  message.value='';saving.value=true
  const r=await submit(()=>{
    // 换连接／换表只改身份三键（用户明确选择的目标），已登记补充来源与属性／链接／说明一律原样保留：
    // 失效字段不在此静默清空、不按同名字段自动重绑，交服务端校验报告并阻断发布（P03/F04，20260920 v2）。
    props.b.connection=d.connection;props.b.table=d.table;props.b.primary_key=d.primary_key
    // 切回数据库来源：移除 identity 块；identity.kind 非 registered 的未知值不动（零丢失）
    if(bindingIdentityOf(props.b)==='registered')clearRegisteredIdentity(props.b)
    commitIdentityNote()
  })
  saving.value=false
  if(r.ok)closeEditor()
  else message.value='保存未完成：'+r.message+'。表单内容已保留，可重试保存或取消。'
}
const sourceIsNew=computed(()=>{const d=sourceDraft.value;return !d||!sources.value.some(s=>s.id===d.id)})
async function saveSource(){
  const d:any=sourceDraft.value
  if(saving.value||!d)return
  if(!String(d.name||'').trim()){message.value='请填写来源名称。';return}
  if(d.kind==='db'){
    if(!d.connection){message.value='请选择数据连接。';return}
    if(!d.table){message.value='请选择表／视图。';return}
    if(!d.matchLeft){message.value='请选择实例来源表字段（匹配字段）。';return}
    if(!d.matchRight){message.value='请选择本表字段（匹配字段）。';return}
    if(!fieldOptions(identityCatalog.value).some(f=>f.value===d.matchLeft)){message.value='实例来源表字段已不在当前身份表目录中，请重新选择。';return}
    if(!fieldOptions(draftSourceCatalog.value).some(f=>f.value===d.matchRight)){message.value='本表字段已不在当前补充表目录中，请重新选择。';return}
  }else if(!d.connection){message.value='请选择 Redis 连接。';return}
  message.value='';saving.value=true
  const r=await submit(()=>{
    const views=sourcesOf(props.b).map(v=>({...v}))
    const i=views.findIndex(v=>v.id===d.id)
    const clean:any={...d,legacy:undefined}
    if(i>=0)views[i]=clean;else views.push(clean)
    commitSources(props.b,views)
  })
  saving.value=false
  if(r.ok)closeEditor()
  else message.value='保存未完成：'+r.message+'。表单内容已保留，可重试保存或取消。'
}
defineExpose({dirty:()=>dirty.value,discard:closeEditor,openIdentity})
</script>
<template><div>
<!-- ========== 浏览态：只读摘要 + 折叠的补充来源 ========== -->
<template v-if="!editing">
<div class="os-desc">
<div class="os-desc-head"><h3>来源说明</h3><button @click="openNote">编辑说明</button></div>
<p v-if="savedNote" class="os-note-text">{{savedNote}}</p>
<p v-else class="muted os-note-text">暂无说明。描述{{objectName}}来自哪里、什么标识能唯一确定一个实例。</p>
</div>
<details class="os-config">
<summary><strong>数据来源</strong><small class="muted">{{identityMode==='registered'?'项目登记 · '+(savedInstances.length||0)+' 个实例':(b.connection?connName(b.connection)+' · '+(b.table||'未选表'):'未配置')}}</small></summary>
<div class="os-config-body">
<div class="os-head"><div><h3>如何识别一个{{objectName}}实例</h3><small class="muted">识别有哪些对象实例，不限制属性可以从哪里获取。</small></div><button class="primary" @click="openIdentity">{{identityMode==='registered'||b.table?'修改':'配置'}}</button></div>
<div class="os-summary">
<div><small>实例来源方式</small><div class="os-readonly">{{identityMode==='registered'?'项目登记':'数据库表／视图'}}</div></div>
<template v-if="identityMode==='database'">
<div><small>数据连接</small><div class="os-readonly">{{b.connection?connName(b.connection):'未配置'}}</div></div>
<div><small>身份表</small><div class="os-readonly">{{b.table||'未配置'}}</div></div>
<div><small>实例主键</small><div class="os-readonly">{{b.primary_key||'未配置'}}</div></div>
</template>
<template v-else>
<div><small>已登记实例</small><div class="os-readonly">{{savedInstances.length?savedInstances.length+' 个':'尚未登记'}}</div></div>
<div><small>实例清单</small><div class="os-readonly">{{savedInstances.length?savedInstances.map(i=>i.id+(i.label?'（'+i.label+'）':'')).join('、'):'—'}}</div></div>
</template>
</div>
<p v-if="identityMode==='database'&&titleInfo" class="field-help">显示名称由本体标记属性「{{titleInfo}}」决定；在「属性取值」页签把该属性绑定到数据列后生效。</p>
<p v-else-if="identityMode==='registered'&&titleInfo" class="field-help">显示名称由本体标记属性「{{titleInfo}}」决定；可在「属性取值」页签把该属性绑定到登记信息的显示名称。</p>
<p v-if="identityMode==='database'&&!b.connection" class="field-help">还没有合适的数据连接？请到「数据连接」页新建并测试后再回来配置。</p>
<details class="os-sources">
<summary>已登记补充来源（可选）{{sources.length?' · '+sources.length+' 项':''}}</summary>
<p class="field-help">用于复用关联路径或配置对象链接。属性直接选择其他表时，无需先在这里登记。</p>
<p v-if="identityMode==='registered'" class="inline-warning">当前按项目登记识别实例：补充来源依赖数据库身份表，仅在切回「数据库表／视图」后生效（配置已保留）。</p>
<div v-for="s in sources" :key="s.id" class="os-source-row">
<div class="os-readonly">{{sourceSummaryText(s)}}</div>
<div class="tools"><button v-if="sourceEditable(s)" @click="openSource(s)">修改</button><button @click="removeSource(s)">移除</button></div>
</div>
<p v-if="!sources.length" class="field-help">暂无补充来源（允许为零项）：对象只有实例来源即可完成识别；需要时可登记数据库补充表。</p>
<div v-if="identityMode==='database'" class="tools"><button @click="openSource()">＋ 登记数据库补充来源</button></div>
<p v-if="removeMessage" class="inline-error" role="alert">{{removeMessage}}</p>
</details>
</div>
</details>
<p class="field-help">这里的说明用于当前项目；本体中的对象定义保持独立。</p>
<div class="os-footer"><button class="primary" @click="emit('go-tab','properties')">继续配置属性取值 →</button></div>
</template>
<!-- ========== 编辑态：对象说明 ========== -->
<template v-else-if="editing==='note'">
<div class="os-editor-top"><button class="os-back" @click="closeEditor">← 返回实例识别</button><small class="muted">当前对象 · {{objectName}}</small></div>
<h2>来源说明</h2>
<p class="os-note">描述当前项目中{{objectName}}的实例来源与识别方式；本体中的对象定义不受影响。</p>
<MappingDescription v-model="noteDraft" hint="说明对象来自哪里，什么标识能唯一确定一个实例。" :rows="9"/>
<p v-if="message" class="inline-error" role="alert">{{message}}</p>
<div class="os-actions"><button class="primary" :disabled="saving" @click="saveNote">{{saving?'保存中…':'保存'}}</button><button :disabled="saving" @click="closeEditor">取消</button><small class="field-help">保存写入当前项目草稿；取消放弃本次修改。</small></div>
</template>
<!-- ========== 编辑态：实例识别表单 ========== -->
<template v-else-if="editing==='identity'">
<div class="os-editor-top"><button class="os-back" @click="closeEditor">← 返回实例识别</button><small class="muted">当前对象 · {{objectName}}</small></div>
<div class="os-assist-row"><button type="button" :aria-pressed="assistOpen" @click="toggleAssist">✦ 辅助填写</button></div>
<h2>{{identityMode==='registered'||b.table?'修改':'配置'}}实例识别</h2>
<p class="os-note">识别“有哪些{{objectName}}实例”，不限制其属性从哪里获取。</p>
<div class="row">
<label>实例来源方式 *<AppSelect :model-value="identityDraft.mode" aria-label="实例来源方式" :options="modeOptions" @update:model-value="identityModeChanged"/><small class="field-help">数据库表／视图：一行数据对应一个实例；项目登记：手工登记实例清单，不依赖数据表。</small></label>
</div>
<p v-if="switchBlock" class="inline-error" role="alert">{{switchBlock}}</p>
<p v-if="switchNote" class="inline-warning" role="status">{{switchNote}}</p>
<template v-if="identityDraft.mode==='database'">
<div class="row">
<label>数据连接 *<AppSelect :model-value="identityDraft.connection" aria-label="实例来源数据连接" searchable :options="[{value:'',label:'请选择连接'},...mysqlOptions]" @update:model-value="identityConnChanged"/><small class="field-help">从当前项目已配置的 MySQL 连接中选择；换连接后需重新选择表与主键（已登记补充来源的匹配配置保留，不自动改写）。</small></label>
<label>来源表／视图 *<AppSelect :key="identityDraft.connection" :model-value="identityDraft.table" aria-label="来源表或视图" searchable :disabled="!identityDraft.connection" :options="[{value:'',label:'请选择表／视图'},...tableOptions(projectState,identityDraft.connection)]" @update:model-value="identityTableChanged"/><small v-if="!identityDraft.connection" class="field-help">先选择数据连接。</small></label>
</div>
<div class="row">
<label>实例主键 *<AppSelect :key="identityDraft.connection+':'+identityDraft.table" :model-value="identityDraft.primary_key" aria-label="实例主键" searchable :disabled="!draftIdentityCatalog" :options="[{value:'',label:'请选择字段'},...fieldOptions(draftIdentityCatalog)]" @update:model-value="identityPkChanged"/><small v-if="identityDraft.connection&&!draftIdentityCatalog" class="field-help">该表不在目录中：请先刷新表结构目录后再选主键。</small><small class="field-help">主键来自当前表，一行对应一个对象实例。</small></label>
</div>
<p v-if="tableChangeNote" class="inline-warning" role="alert">{{tableChangeNote}}</p>
<p v-if="identityDraft.connection&&!catalogOf(projectState,identityDraft.connection)" class="inline-warning">该连接尚未读取表结构目录，无法选择表与字段。<button :disabled="refreshing===identityDraft.connection" @click="refresh(identityDraft.connection)">{{refreshing===identityDraft.connection?'读取中…':'刷新表结构'}}</button></p>
<p v-if="refreshMessage" class="field-help" role="status">{{refreshMessage}}</p>
</template>
<template v-else>
<RegisteredInstances v-model:instances="identityDraft.instances" :b="b" :ref-state="refState"/>
<p class="field-help">实例清单随本表单一并保存；编号是实例身份，保存后不可直接修改。切换来源方式或登记实例都只在保存后生效，取消则全部放弃。</p>
</template>
<p v-if="message" class="inline-error" role="alert">{{message}}</p>
<AssistPanel v-if="assistOpen&&assistBinding" ref="assistPanelRef" class="os-assist-panel" :binding="assistBinding" :api="assistApi??undefined" @close="assistOpen=false"/>
<div class="os-actions"><button class="primary" :disabled="saving" @click="saveIdentity">{{saving?'保存中…':'保存'}}</button><button :disabled="saving" @click="closeEditor">取消</button><small class="field-help">保存写入当前项目草稿；取消放弃本次修改。</small></div>
</template>
<!-- ========== 编辑态：补充来源表单 ========== -->
<template v-else>
<div class="os-editor-top"><button class="os-back" @click="closeEditor">← 返回实例识别</button><small class="muted">当前对象 · {{objectName}}</small></div>
<h2>{{sourceIsNew?'登记补充来源':'修改补充来源'}}</h2>
<p class="os-note">补充来源可为零个或多个，用于复用关联路径或配置对象链接；属性直接选择其他表时无需登记。</p>
<template v-if="sourceDraft.kind==='db'">
<div class="row">
<label>来源名称 *<input v-model="sourceDraft.name" placeholder="例如 储能簇扩展信息"></label>
<label>匹配数量<AppSelect :model-value="sourceDraft.cardinality||'one'" aria-label="匹配数量" :options="[{value:'one',label:'零或一条'},{value:'many',label:'零或多条'}]" @update:model-value="sourceDraft.cardinality=$event"/></label>
</div>
<div class="os-match-grid">
<section class="os-match-side">
<h3>实例来源（已配置）</h3>
<label>数据连接<div class="os-readonly">{{connName(b.connection)||'未配置'}}</div></label>
<label>身份表<div class="os-readonly">{{b.table||'未配置'}}</div></label>
<label>实例来源表字段 *<AppSelect :key="b.connection+':'+b.table" :model-value="sourceDraft.matchLeft||''" aria-label="实例来源表字段" searchable :disabled="!identityCatalog" :options="[{value:'',label:'请选择字段'},...fieldOptions(identityCatalog)]" @update:model-value="sourceDraft.matchLeft=$event"/><small class="field-help">来自 {{b.table||'实例身份表'}}，不随右侧补充表切换。</small></label>
<p v-if="b.connection&&!identityCatalog" class="inline-warning">身份表目录不可用。<button :disabled="!!refreshing" @click="refresh(b.connection)">刷新身份表目录</button></p>
</section>
<section class="os-match-side">
<h3>补充来源</h3>
<label>数据连接 *<AppSelect :model-value="sourceDraft.connection||''" aria-label="补充来源数据连接" searchable :options="[{value:'',label:'请选择连接'},...mysqlOptions]" @update:model-value="sourceConnChanged"/></label>
<label>表／视图 *<AppSelect :key="sourceDraft.connection" :model-value="sourceDraft.table||''" aria-label="补充来源表或视图" searchable :disabled="!sourceDraft.connection||!catalogOf(projectState,sourceDraft.connection)" :options="[{value:'',label:sourceDraft.connection?'请选择表／视图':'先选择数据连接'},...tableOptions(projectState,sourceDraft.connection)]" @update:model-value="sourceTableChanged"/></label>
<label>本表字段 *<AppSelect :key="sourceDraft.connection+':'+sourceDraft.table" :model-value="sourceDraft.matchRight||''" aria-label="本表字段" searchable :disabled="!draftSourceCatalog" :options="[{value:'',label:sourceDraft.table?'请选择字段':'先选择补充表'},...fieldOptions(draftSourceCatalog)]" @update:model-value="sourceDraft.matchRight=$event"/><small class="field-help">{{sourceDraft.table?'只显示 '+sourceDraft.table+' 的字段；切换表后重新选择。':'按数据连接 → 表／视图 → 字段的顺序选择。'}}</small></label>
<button v-if="sourceDraft.connection" :disabled="!!refreshing" @click="refresh(sourceDraft.connection)">{{refreshing===sourceDraft.connection?'读取中…':'刷新表结构目录'}}</button>
</section>
</div>
<p class="os-note">匹配关系：{{b.table||'实例来源表'}}.{{sourceDraft.matchLeft||'待选字段'}} ＝ {{sourceDraft.table||'补充来源表'}}.{{sourceDraft.matchRight||'待选字段'}}</p>
<p v-if="sourceDraft.cardinality==='many'" class="field-help">零或多条：普通标量属性必须配置取最新或汇总规则后才能取值，不任选一条。</p>
<p v-if="sourceDraft.connection&&sourceDraft.connection!==b.connection" class="inline-warning">与实例来源不在同一数据连接：跨库联合查询未在本轮实现，仅登记。</p>
</template>
<template v-else>
<div class="row">
<label>来源名称 *<input v-model="sourceDraft.name" placeholder="例如 储能簇实时测点"></label>
<label>Redis 连接 *<AppSelect :model-value="sourceDraft.connection||''" aria-label="Redis 连接" searchable :options="[{value:'',label:'请选择连接'},...redisOptions]" @update:model-value="sourceDraft.connection=$event"/></label>
</div>
<p class="field-help">Redis 来源只登记连接；具体 Key 模板与参数绑定在「属性取值」页签中逐属性配置。</p>
</template>
<p v-if="sourceDraft.kind==='db'&&sourceDraft.connection&&!catalogOf(projectState,sourceDraft.connection)" class="inline-warning">该连接尚未读取表结构目录，无法选择表与字段。<button :disabled="refreshing===sourceDraft.connection" @click="refresh(sourceDraft.connection)">{{refreshing===sourceDraft.connection?'读取中…':'刷新表结构'}}</button></p>
<p v-if="refreshMessage" class="field-help" role="status">{{refreshMessage}}</p>
<p v-if="message" class="inline-error" role="alert">{{message}}</p>
<div class="os-actions"><button class="primary" :disabled="saving" @click="saveSource">{{saving?'保存中…':'保存'}}</button><button :disabled="saving" @click="closeEditor">取消</button><small class="field-help">保存写入当前项目草稿；取消放弃本次修改。</small></div>
</template>
</div></template>
<style scoped>
.os-desc{margin:0 0 14px}
.os-desc-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px}
.os-desc-head h3{margin:0}
.os-note-text{white-space:pre-wrap;line-height:1.9;font-size:13px;margin:0;padding:12px 14px;background:var(--bg);border:1px solid var(--line);border-radius:7px;overflow-wrap:anywhere}
.os-config{border-top:1px solid var(--line);margin-top:6px;padding-top:12px}
.os-config>summary{cursor:pointer;display:flex;align-items:center;gap:10px;list-style:none}
.os-config>summary::-webkit-details-marker{display:none}
.os-config>summary::before{content:'›';color:var(--muted);transition:transform .12s}
.os-config[open]>summary::before{transform:rotate(90deg)}
.os-config-body{padding-top:14px}
.os-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;margin:0 0 16px}
.os-head h3{margin:0 0 4px}
.os-head .muted{display:block}
.os-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:0 0 8px}
.os-summary small{display:block;font-size:12px;color:var(--muted);margin-bottom:5px}
.os-readonly{padding:9px 12px;background:var(--bg);border:1px solid var(--line);border-radius:7px;overflow-wrap:anywhere;font-size:13px;min-height:39px}
.os-sources{border-top:1px solid var(--line);margin-top:18px;padding-top:14px}
.os-sources summary{cursor:pointer;color:var(--muted)}
.os-source-row{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:10px 0;flex-wrap:wrap}
.os-source-row .os-readonly{flex:1;min-width:260px}
.os-source-row .tools{flex:none}
.os-footer{border-top:1px solid var(--line);margin-top:20px;padding-top:16px;display:flex;gap:9px;flex-wrap:wrap}
.os-editor-top{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:10px;flex-wrap:wrap}
.os-back{background:transparent;border-color:transparent;color:var(--blue);padding-left:0}
.os-back:hover{color:var(--blue-deep);border-color:transparent}
.os-note{background:var(--blue-soft);border:1px solid var(--blue-line);border-radius:7px;padding:10px 14px;font-size:13px;color:var(--muted);margin:12px 0}
.os-actions{display:flex;gap:9px;align-items:center;margin-top:20px;flex-wrap:wrap}
/* 辅助填写（T8 P1）：入口按钮紧贴编辑态页头一行；面板为表单内一张卡片，不挤压既有版式 */
.os-assist-row{display:flex;margin:-6px 0 12px}
.os-assist-panel{margin-top:16px}
.os-match-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin:18px 0}
.os-match-side{border:1px solid var(--line);border-radius:8px;padding:16px;background:var(--paper-2);min-width:0}
.os-match-side h3{font-size:14px;margin:0 0 16px}
.os-match-side label{display:block;margin:16px 0}
.os-match-side .os-readonly{margin-top:8px}
@media(max-width:800px){.os-summary,.os-match-grid{grid-template-columns:1fr}}
</style>

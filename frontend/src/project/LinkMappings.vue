<script lang="ts">
// 模块级链接草稿缓存：编辑链接映射时经「去配置」跳到某端对象的实例识别页，
// 返回本对象后自动恢复编辑上下文（不丢草稿）。ProjectBinding 用 lmPendingOf 决定回位页签。
let pendingLink:{objectType:string;draft:any}|null=null
function lmStash(objectType:string,d:any){pendingLink={objectType,draft:JSON.parse(JSON.stringify(d))}}
/** 是否存在该对象待恢复的链接映射草稿（供 ProjectBinding 回位页签用）。 */
export function lmPendingOf(objectType:string):boolean{return !!pendingLink&&pendingLink.objectType===objectType}
function lmConsume(objectType:string):any|null{if(!lmPendingOf(objectType))return null;const d=pendingLink!.draft;pendingLink=null;return d}
</script>
<script setup lang="ts">
// 链接映射（T04 · 原型 linksView 项目态 / map-link 编辑态）：
// 表与字段只能从两端对象已登记的数据库来源（实例来源＋一对一补充表）选择，不手填，
// 与属性取值页「连接目录任意表」的逻辑严格区分；换来源表清空字段并提示。
// 两端缺来源时提供「去配置」入口：草稿先暂存到模块缓存，返回后恢复链接上下文。
// 保存走 inject('form-save') 直通持久化，取消放弃；打开期间注册 T00 表单守卫。
import {computed,inject,onBeforeUnmount,ref,watch} from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import MappingDescription from './MappingDescription.vue'
import {descTextOf,commitDesc,relationView,commitRelation,sourceById,dbSourcesOf,tableCatalog,fieldOptions,refreshCatalogOf,bindingIdentityOf,registeredInstancesOf,MEMBERSHIP_OPERATORS,MEMBERSHIP_SCOPES} from './bindingModel'
import type {MembershipRuleView} from './bindingModel'
import SourcePreview from './SourcePreview.vue'
import type {RelationView,DbSourceView} from './bindingModel'
import type {FormGuardAPI,FormSaveAPI} from '../app/formGuard'
const props=defineProps<{projectState:any;refState:any;b:any}>()
const emit=defineEmits(['before-change','changed','edit-state','setup-end','goto-object'])
function before(){emit('before-change')}
function changed(){emit('changed')}
function mutate(fn){before();fn();changed()}
const graph=computed(()=>props.refState?.ontology?.['@graph']||[])
const types=computed(()=>graph.value.filter(n=>n['@type']==='owl:Class'))
const relations=computed(()=>graph.value.filter(n=>n['@type']==='owl:ObjectProperty'))
const name=(id:string)=>types.value.find(n=>n['@id']==='mg:'+id)?.['rdfs:label']||id
const relationNode=(relationId:string)=>relations.value.find(r=>r['@id']==='mg:'+relationId)
const linkLabel=(relationId:string)=>relationNode(relationId)?.['rdfs:label']||relationId
const cardinalityNames:Record<string,string>={'one-to-one':'一对一','one-to-many':'一对多','many-to-one':'多对一','many-to-many':'多对多'}
const cardinalityOf=(relationId:string)=>String(relationNode(relationId)?.['mg:cardinality']||'')
const cardLabel=(relationId:string)=>cardinalityNames[cardinalityOf(relationId)]||'数量关系未声明'
const bindingOfType=(t:string)=>props.projectState.bindings.object_bindings.find((x:any)=>x.object_type===t)
// 收窄到 db 来源：redis 来源没有表/字段，不能作为链接两端。
const dbSourceOf=(b:any,sourceId:string):DbSourceView|undefined=>{const s=sourceById(b,sourceId);return s&&s.kind==='db'?s:undefined}
const rows=computed(()=>(props.b.relations||[]).map((r:any)=>relationView(r)))
// ---------- 成员模式（方案 §3.2）：起点=项目登记实例、终点=数据库身份对象 ----------
const originIdentity=computed(()=>bindingIdentityOf(props.b))
const registeredInstances=computed(()=>registeredInstancesOf(props.b))
const membershipPreview=ref(false)
function membershipSummary(view:RelationView):string{
  const rules=view.membership||[]
  const withCond=rules.filter(r=>r.scope==='all'||(r.conditions||[]).length).length
  const conds=rules.reduce((n:number,r)=>n+(r.conditions||[]).length,0)
  return `按条件选择成员 · ${rules.length} 个实例规则（${withCond} 个已配置）· 共 ${conds} 条条件`
}
// 集合端按端判定（与后端 member_side_of 同一规则）：出向一对多/多对多（成员=range）
// 或入向多对一/多对多（成员=domain）。返回 {tid,dir}；不成立时 tid 为空串。
function memberSideOfLink(r:any):{tid:string;dir:'out'|'in'|''}{
  const dom=String(r['rdfs:domain']?.['@id']||'').replace(/^mg:/,'')
  const rng=String(r['rdfs:range']?.['@id']||'').replace(/^mg:/,'')
  const card=cardinalityOf(String(r['@id'].slice(3)))
  const me=String(props.b.object_type||'')
  if(dom===me&&['one-to-many','many-to-many'].includes(card)&&rng)return {tid:rng,dir:'out'}
  if(rng===me&&['many-to-one','many-to-many'].includes(card)&&dom)return {tid:dom,dir:'in'}
  return {tid:'',dir:''}
}
function membershipLinkOk(r:any):string|null{
  const {tid}=memberSideOfLink(r)
  if(!tid)return '该链接数量关系不支持成员规则（集合端须出向一对多／多对多，或入向多对一／多对多）'
  const tb=bindingOfType(tid)
  if(!tb)return '成员对象尚未启用对象映射，请先配置成员对象的实例来源'
  if(bindingIdentityOf(tb)!=='database')return '成员对象不是数据库来源，本轮成员规则仅支持数据库身份的成员'
  if(!tb.table)return '成员对象尚未配置实例来源表'
  return null
}
// 可添加的链接：以本对象为起点的未映射链接（任意数量关系，旧列关联可提示），加上
// 以本对象为终点且数量关系满足按端判定（多对一/多对多）的入向链接——成员规则按端维护。
const pendingLinks=computed(()=>relations.value.filter(r=>{
  if((props.b.relations||[]).some((x:any)=>x.relation===r['@id'].slice(3)))return false
  const dom=String(r['rdfs:domain']?.['@id']||'').replace(/^mg:/,'')
  const rng=String(r['rdfs:range']?.['@id']||'').replace(/^mg:/,'')
  const me=String(props.b.object_type||'')
  if(dom===me)return true
  return rng===me&&['many-to-one','many-to-many'].includes(cardinalityOf(String(r['@id'].slice(3))))
}))
// 以本对象为终点的只读入向链接：排除可在此配置成员规则的入向链接（它们已进入上方添加列表）。
const inboundLinks=computed(()=>relations.value.filter(r=>{
  if(r['rdfs:range']?.['@id']!=='mg:'+props.b.object_type)return false
  return !['many-to-one','many-to-many'].includes(cardinalityOf(String(r['@id'].slice(3))))
}))
const inboundState=(r:any)=>{
  const domainId=String(r['rdfs:domain']?.['@id']||'').replace(/^mg:/,'')
  const ob=bindingOfType(domainId)
  const configured=!!(ob?.relations||[]).some((x:any)=>x.relation===r['@id'].slice(3))
  return {domainId,configured,enabled:!!ob}
}
function summaryOf(b:any,sourceId:string,field:string){const s=sourceId?dbSourceOf(b,sourceId):undefined;return (s?.table||b.table||'未配置表')+'.'+(field||'未选字段')}
function summaryEnd(view:RelationView){const tb=bindingOfType(view.targetType);return tb?summaryOf(tb,view.targetSourceId,view.targetField):'终点对象尚未配置数据映射'}
// ---------- 编辑器：局部草稿，保存才写回；新建（addLink）在保存时才写入 b.relations ----------
const draft=ref<RelationView|null>(null),editIndex=ref(-1),editingIsNew=ref(false)
// 项目说明（链接级，20260919 v2.1）：同一链接两端共用一个说明键；反向入口编辑即更新同一份
const noteDraft=ref(''),noteBaseline=ref('')
const message=ref(''),switchMsg=ref(''),refreshing=ref(''),saving=ref(false)
const editingOpen=computed(()=>!!draft.value)
const formGuard=inject<FormGuardAPI|null>('form-guard',null)
const formSave=inject<FormSaveAPI|null>('form-save',null)
async function submit(apply:()=>void):Promise<{ok:boolean;message:string}>{
  if(formSave)return formSave.submitForm('project',apply)
  before();apply();changed();return {ok:true,message:''}
}
let draftBaseline=''
const draftChanged=computed(()=>{if(!draft.value)return false;return JSON.stringify(draft.value)!==draftBaseline||noteDraft.value!==noteBaseline.value})
function dirty(){return draftChanged.value}
const editingMembership=computed(()=>!!draft.value?.membership)
// 当前草稿链接的集合端方向：'out' 出向 / 'in' 入向（成员=另一端）。
const draftDir=computed(()=>{const rid=draft.value?.relation;const n=relations.value.find(x=>x['@id'].slice(3)===rid);return n?memberSideOfLink(n).dir:''})
function pendingLinkLabel(r:any){
  const rng=String(r['rdfs:range']?.['@id']||'').replace(/^mg:/,'')
  const dom=String(r['rdfs:domain']?.['@id']||'').replace(/^mg:/,'')
  const base=String(r['rdfs:label']||r['@id'].slice(3))
  return rng===String(props.b.object_type||'')?base+'（入向 · '+name(dom)+' → 本对象）':base
}
function membershipChainNote(){
  const d=draft.value
  if(!d)return ''
  return draftDir.value==='in'
    ?name(d.targetType)+' — '+linkLabel(d.relation)+' → '+name(props.b.object_type)+'（入向 · 成员='+name(d.targetType)+'）'
    :name(props.b.object_type)+' — '+linkLabel(d.relation)+' → '+name(d.targetType)
}
function openEditor(index:number){
  editIndex.value=index;editingIsNew.value=false
  const v=relationView(props.b.relations[index])
  draft.value={...v,membership:v.membership?JSON.parse(JSON.stringify(v.membership)):undefined}
  normalizeMembershipDraft()
  draftBaseline=JSON.stringify(draft.value);message.value='';switchMsg.value=''
  noteDraft.value=descTextOf(props.projectState,'links',draft.value.relation)
  noteBaseline.value=noteDraft.value
}
function openNew(r:any){
  editIndex.value=-1;editingIsNew.value=true
  noteDraft.value='';noteBaseline.value=''
  // 成员端按端判定：入向链接（多对一/多对多）的成员=domain 侧，出向为 range 侧。
  const {tid}=memberSideOfLink(r)
  const base={relation:r['@id'].slice(3),targetType:tid||String(r['rdfs:range']?.['@id']||'').replace(/^mg:/,''),sourceId:'',field:'',targetSourceId:'',targetField:'',legacy:false}
  if(originIdentity.value==='registered'){
    const err=membershipLinkOk(r)
    if(err){message.value=err;return}
    ;(base as any).membership=registeredInstances.value.map(i=>({sourceInstance:i.id,scope:'filtered',conditions:[]}))
  }
  draft.value=base
  draftBaseline=JSON.stringify(draft.value);message.value='';switchMsg.value=''
}
// membership 草稿补齐：新登记的实例补默认规则；被删实例的规则行丢弃
function normalizeMembershipDraft(){
  const d:any=draft.value
  if(!d?.membership)return
  const ids=registeredInstances.value.map(i=>i.id)
  d.membership=d.membership.filter((r:MembershipRuleView)=>ids.includes(r.sourceInstance))
  for(const id of ids)if(!d.membership.some((r:MembershipRuleView)=>r.sourceInstance===id))d.membership.push({sourceInstance:id,scope:'filtered',conditions:[]})
}
watch(registeredInstances,()=>{if(editingMembership.value)normalizeMembershipDraft()})
function closeEditor(){draft.value=null;editIndex.value=-1;editingIsNew.value=false;message.value='';switchMsg.value='';saving.value=false}
const SWITCH_CONFIRM='当前链接映射有未保存的修改，切换后将放弃这些修改。继续吗？（确定=放弃并切换，取消=继续编辑）'
async function edit(index:number){if(dirty()&&!(await appConfirm({ message: SWITCH_CONFIRM })))return;openEditor(index as number)}
async function addLink(r:any){if(!r)return;if(dirty()&&!(await appConfirm({ message: SWITCH_CONFIRM })))return;openNew(r)}
async function removeLink(i:number){
  if(!(await appConfirm({ message: '移除此链接映射？', danger: true })))return
  const r=await submit(()=>props.b.relations.splice(i as number,1))
  if(!r.ok){message.value='移除未完成：'+r.message;return}
  if(editIndex.value===i)closeEditor();else if(editIndex.value>(i as number))editIndex.value--
}
// 「去配置」：先暂存草稿再静默关闭编辑器（不经离开确认），跳到目标对象的实例识别页签。
function setupEnd(objectType:string){
  if(draft.value)lmStash(props.b.object_type,draft.value)
  closeEditor()
  emit('setup-end',objectType)
}
function gotoObject(t:string){emit('goto-object',t)}
// 返回恢复：挂载时检查模块缓存中是否有本对象的待恢复草稿。
function restorePending(){
  if(!lmPendingOf(props.b.object_type))return
  const d=lmConsume(props.b.object_type)
  if(!d)return
  const i=(props.b.relations||[]).findIndex((x:any)=>x.relation===d.relation)
  editIndex.value=i;editingIsNew.value=i<0
  draft.value=d;draftBaseline=JSON.stringify(d)
  message.value='';switchMsg.value='已恢复未完成的链接映射草稿。'
}
restorePending()
const guard={isDirty:()=>dirty(),discard:()=>closeEditor()}
watch(editingOpen,open=>{emit('edit-state',open);open?formGuard?.register(guard):formGuard?.unregister(guard)},{immediate:true})
onBeforeUnmount(()=>formGuard?.unregister(guard))
// 起点来源：实例来源 + 一对一/零或一条的补充来源；多条匹配来源不能作为链接字段来源。
const hasManyOrigin=computed(()=>dbSourcesOf(props.b).some(s=>s.cardinality==='many'))
const originSourceOptions=computed(()=>[{value:'',label:'实例来源 · '+(props.b.table||'未配置表')},...dbSourcesOf(props.b).filter(s=>s.cardinality!=='many').map(s=>({value:s.id,label:s.name+' · '+s.table}))])
const originSource=computed(()=>draft.value&&draft.value.sourceId?dbSourceOf(props.b,draft.value.sourceId):undefined)
const originConnection=computed(()=>originSource.value?.connection||props.b.connection||'')
const originTable=computed(()=>originSource.value?.table||props.b.table||'')
const originCatalog=computed(()=>originTable.value?tableCatalog(props.projectState,originConnection.value,originTable.value):null)
function originSourceChanged(v:string){if(!draft.value||v===draft.value.sourceId)return;draft.value.sourceId=v;draft.value.field='';switchMsg.value='已切换起点来源表，请重新选择起点字段。'}
// 终点来源完全对称：取终点对象绑定的实例来源 + one 来源；无绑定则提供去配置入口。
const targetBinding=computed(()=>draft.value?bindingOfType(draft.value.targetType):undefined)
const hasManyTarget=computed(()=>!!targetBinding.value&&dbSourcesOf(targetBinding.value).some(s=>s.cardinality==='many'))
const targetSourceOptions=computed(()=>{const tb=targetBinding.value;return tb?[{value:'',label:'实例来源 · '+(tb.table||'未配置表')},...dbSourcesOf(tb).filter(s=>s.cardinality!=='many').map(s=>({value:s.id,label:s.name+' · '+s.table}))]:[]})
const targetSource=computed(()=>{const tb=targetBinding.value;return tb&&draft.value&&draft.value.targetSourceId?dbSourceOf(tb,draft.value.targetSourceId):undefined})
const targetConnection=computed(()=>targetSource.value?.connection||targetBinding.value?.connection||'')
const targetTable=computed(()=>targetSource.value?.table||targetBinding.value?.table||'')
const targetCatalog=computed(()=>targetTable.value?tableCatalog(props.projectState,targetConnection.value,targetTable.value):null)
function targetSourceChanged(v:string){if(!draft.value||v===draft.value.targetSourceId)return;draft.value.targetSourceId=v;draft.value.targetField='';switchMsg.value='已切换终点来源表，请重新选择终点字段。'}
// 刷新表结构：读取失败保留旧选择，不静默清空映射。
async function refreshCatalog(connectionId:string){
  if(refreshing.value)return
  if(!connectionId){message.value='该来源未指定数据连接，无法刷新表结构';return}
  const conn=(props.projectState.connections?.connections||[]).find((c:any)=>c.id===connectionId)
  if(!conn){message.value='未找到该来源对应的数据连接，无法刷新表结构';return}
  refreshing.value=connectionId
  const out=await refreshCatalogOf(props.projectState,connectionId,mutate)
  refreshing.value=''
  message.value=out.ok?'':out.message
}
// 数量关系校验提示：保存前实时计算，不等到校验阶段才暴露冲突。
const cardinalityHint=computed(()=>{
  const d=draft.value
  if(!d)return null
  const card=cardinalityOf(d.relation)
  if(!card)return {cls:'inline-warning',text:'本体未声明数量关系'}
  if(card==='one-to-many'||card==='many-to-many')return {cls:'field-help',text:'可匹配多个目标实例；执行时将按目标实例标识去重，不任选一条'}
  if(!targetCatalog.value)return {cls:'inline-warning',text:'目标匹配字段唯一性未知，待数据接入后验证'}
  const f=targetCatalog.value.fields.find(x=>x.name===d.targetField)
  if(!f)return {cls:'inline-warning',text:'目标匹配字段唯一性未知，待数据接入后验证'}
  if(f.key==='pri'||f.key==='uni')return {cls:'inline-success',text:'✓ 目标字段为唯一键'}
  return {cls:'inline-error',text:'✗ 目标匹配字段不是唯一键，与数量关系冲突（保存后校验会报错）'}
})
// 关系路径预览：补充来源带匹配链提示，说明如何回到实例身份。
function chainNote(b:any,sourceId:string){const s=sourceId?dbSourceOf(b,sourceId):undefined;return s?'（'+(b.table||'实例表')+'.'+(s.matchLeft||b.primary_key||'匹配字段')+' ＝ '+s.table+'.'+(s.matchRight||'匹配字段')+'）':''}
const pathPreview=computed(()=>{
  const d=draft.value
  if(!d)return ''
  const tb=targetBinding.value
  return name(props.b.object_type)+' → '+chainNote(props.b,d.sourceId)+(originTable.value||'待选来源')+'.'+(d.field||'待选字段')+' ＝ '+(tb?chainNote(tb,d.targetSourceId):'')+(targetTable.value||'待选目标')+'.'+(d.targetField||'待选字段')+' ← '+name(d.targetType)
})
async function save(){
  const d=draft.value
  if(!d||saving.value)return
  if(editingMembership.value){await saveMembership();return}
  if(!originConnection.value){message.value='起点对象尚未配置数据连接，请先配置起点实例来源。';return}
  if(!targetConnection.value){message.value='终点对象尚未配置数据连接，请先配置终点实例来源。';return}
  if(!originTable.value){message.value='起点尚未指定数据表，请先「去配置」起点对象的实例来源。';return}
  if(!d.field){message.value=originCatalog.value?'请选择起点来源的链接字段':'起点字段目录未刷新：请先刷新该连接的表结构，再选择起点字段';return}
  if(!targetBinding.value){message.value='终点对象尚未配置数据映射，请先「去配置」终点对象。';return}
  if(!targetTable.value){message.value='终点尚未指定数据表，请先在终点对象的实例识别中配置。';return}
  if(!d.targetField){message.value=targetCatalog.value?'请选择终点来源的匹配字段':'终点字段目录未刷新：请先刷新该连接的表结构，再选择终点字段';return}
  if(originCatalog.value&&!originCatalog.value.fields.some(f=>f.name===d.field)){message.value='起点字段不在当前来源表中，请重新选择。';return}
  if(targetCatalog.value&&!targetCatalog.value.fields.some(f=>f.name===d.targetField)){message.value='终点字段不在当前来源表中，请重新选择。';return}
  message.value=''
  saving.value=true
  const r=await submit(()=>{
    if(editingIsNew.value)(props.b.relations||=[]).push({relation:d.relation,target_type:d.targetType,sourceId:d.sourceId,field:d.field,targetSourceId:d.targetSourceId,targetField:d.targetField})
    else commitRelation(props.b,editIndex.value,d)
    commitDesc(props.projectState,'links',d.relation,null,noteDraft.value)  // 链接两端共用一份说明
  })
  saving.value=false
  if(r.ok)closeEditor()
  else message.value='保存未完成：'+r.message+'。表单内容已保留，可重试保存或取消。'
}
// ---------- membership：终点身份表目录 + 条件编辑 + 保存 ----------
const memberTargetBinding=computed(()=>draft.value?bindingOfType(draft.value.targetType):undefined)
const memberCatalog=computed(()=>{const tb=memberTargetBinding.value;return tb?tableCatalog(props.projectState,String(tb.connection||''),String(tb.table||'')):null})
const memberFieldOptions=computed(()=>memberCatalog.value?fieldOptions(memberCatalog.value):[])
function memberFieldNumeric(f:any){return /double|decimal|numeric|int|float|number/i.test(String(f?.dataType||''))}
function condValueText(c:any):string{if(c.operator==='in')return Array.isArray(c.value)?c.value.map(String).join('，'):'';return c.value===undefined||c.value===null?'':String(c.value)}
function setCondValue(c:any,raw:string){
  const f=memberCatalog.value?.fields?.find((x:any)=>x.name===c.field)
  const numeric=memberFieldNumeric(f)
  if(c.operator==='in'){c.value=raw.split(/[,，]/).map(s=>s.trim()).filter(Boolean).map(s=>numeric?Number(s):s);return}
  const v=raw.trim()
  c.value=v===''?'':(numeric?Number(v):v)
}
function addCond(rule:MembershipRuleView){rule.conditions.push({field:'',operator:'eq',value:''})}
function rmCond(rule:MembershipRuleView,i:number){rule.conditions.splice(i,1)}
function scopeChanged(rule:MembershipRuleView,v:string){rule.scope=v as any}
async function saveMembership(){
  const d:any=draft.value
  if(!d?.membership){message.value='成员规则缺失';return}
  if(!memberTargetBinding.value||!memberTargetBinding.value.table){message.value='终点对象尚未配置实例来源表';return}
  if(!memberCatalog.value){message.value='终点身份表的字段目录未读取，请先刷新表结构再配置条件';return}
  for(const rule of d.membership){
    if(rule.scope==='filtered'&&!rule.conditions.length){message.value='实例「'+rule.sourceInstance+'」选择按条件筛选，但至少需要一条条件；如需整表请在范围中显式选择。';return}
    for(const c of rule.conditions){
      if(!String(c.field||'').trim()){message.value='实例「'+rule.sourceInstance+'」存在未选择字段的条件';return}
      if(['eq','ne','in'].includes(c.operator)){
        const empty=c.operator==='in'?!Array.isArray(c.value)||!c.value.length:String(c.value??'').trim()===''
        if(empty){message.value='条件「'+c.field+'」未填写比较值';return}
      }
    }
  }
  message.value='';saving.value=true
  const r=await submit(()=>{
    if(editingIsNew.value)(props.b.relations||=[]).push({kind:'membership',relation:d.relation,target_type:d.targetType,rules:d.membership.map(rule=>({
      sourceInstance:rule.sourceInstance,scope:rule.scope,
      conditions:rule.conditions.map(c=>c.operator==='in'?{field:c.field,operator:'in',value:[...(c.value as any[])]}
        :c.operator==='eq'||c.operator==='ne'?{field:c.field,operator:c.operator,value:c.value}
        :{field:c.field,operator:c.operator})}))})
    else commitRelation(props.b,editIndex.value,d)
    commitDesc(props.projectState,'links',d.relation,null,noteDraft.value)  // 链接两端共用一份说明
  })
  saving.value=false
  if(r.ok)closeEditor()
  else message.value='保存未完成：'+r.message+'。表单内容已保留，可重试保存或取消。'
}
defineExpose({dirty,discard:closeEditor})
</script>
<template>
<div>
<!-- ========== 浏览态：链接映射清单 ========== -->
<template v-if="!draft">
<p class="fill-hint">链接两端的表与字段只能从两端对象已登记的数据库来源（实例来源＋补充表）中选择，不接受手填；属性取值页的「连接目录任意表」逻辑不适用于此处。换来源表后需重新选择字段。</p>
<section v-for="(view,i) in rows" :key="view.relation+'-'+i" class="sample-panel">
<div class="panelhead"><div><strong>{{linkLabel(view.relation)}} → {{name(view.targetType)}}</strong> <span v-if="view.legacy" class="status-pill">旧格式</span> <span v-if="view.membership" class="status-pill">成员规则</span> <span v-if="view.unknownRaw" class="status-pill">未识别结构</span> <span v-if="cardinalityOf(view.relation)" class="status-pill">{{cardLabel(view.relation)}}</span></div><div class="tools"><button v-if="view.membership" :disabled="!registeredInstances.length" @click="membershipPreview=!membershipPreview">预览成员</button><button @click="edit(i as number)">配置</button><button @click="removeLink(i as number)">移除</button></div></div>
<p class="muted">{{view.membership?membershipSummary(view):(view.unknownRaw?'已按原样保留，请升级后重新配置':('起点 '+summaryOf(b,view.sourceId,view.field)+' → 终点 '+summaryEnd(view)))}}</p>
<p class="lm-desc" :class="{'muted':!descTextOf(projectState,'links',view.relation)}">{{descTextOf(projectState,'links',view.relation)||'暂无说明（两端共用）'}}</p>
<SourcePreview v-if="view.membership&&membershipPreview" :project-state="projectState" :object-type="b.object_type" :instances="registeredInstances" :property="null" title="成员预览 · "+linkLabel(view.relation) @close="membershipPreview=false"/>
</section>
<p v-if="!rows.length" class="empty">尚未配置链接映射；从下方添加以本对象为起点、或终点为本对象（多对一／多对多，按端配置成员规则）的链接。</p>
<div class="row"><label>添加链接<AppSelect :model-value="''" aria-label="添加链接" placeholder="选择未映射的链接" :options="pendingLinks.map(r=>({value:r['@id'],label:pendingLinkLabel(r)}))" @update:model-value="addLink(relations.find(x=>x['@id']===$event))"/></label></div>
<p v-if="!pendingLinks.length" class="field-help">没有可添加的未映射链接（以本对象为起点，或终点为本对象且数量关系为多对一／多对多）。</p>
<section v-if="inboundLinks.length" class="lm-inbound">
<h3>以{{name(b.object_type)}}为终点的链接</h3>
<p class="field-help">同一条链接只在起点对象下维护一次；如需配置，请到起点对象的「链接映射」页签。</p>
<div v-for="r in inboundLinks" :key="r['@id']" class="lm-inbound-row">
<div><strong>{{name(inboundState(r).domainId)}} — {{r['rdfs:label']||r['@id'].slice(3)}} → {{name(b.object_type)}}</strong><small class="muted">{{inboundState(r).enabled?(inboundState(r).configured?'已配置':'未配置（按需）'):'起点对象尚未启用映射'}}</small></div>
<button @click="gotoObject(inboundState(r).domainId)">去起点对象配置</button>
</div>
</section>
</template>
<!-- ========== 编辑态：配置链接映射（替换页签内容） ========== -->
<template v-else>
<div class="lm-top"><button class="lm-back" @click="closeEditor">← 返回链接映射</button><small class="muted">{{membershipChainNote()}}</small></div>
<!-- ===== 成员模式编辑器（起点=登记实例） ===== -->
<template v-if="editingMembership">
<h2>按条件选择成员</h2>
<MappingDescription v-model="noteDraft" hint="说明两个对象如何关联，例如通过哪些字段找到对方。两端对象共用这一份说明。"/>
<p class="lm-note">{{membershipChainNote()}}{{cardinalityOf(draft.relation)?' · '+cardLabel(draft.relation):''}} · 成员身份表 {{memberTargetBinding?.table||'未配置'}}</p>
<p v-if="switchMsg" class="inline-warning" role="status">{{switchMsg}}</p>
<p class="field-help">成员由成员对象<strong>已配置的实例来源表</strong>按下方条件筛选；字段只能从该表字段目录选择，值按字段类型校验后参数化执行。每个起点实例单独一套条件；成员按成员端主键去重。</p>
<template v-if="!memberCatalog">
<p class="inline-warning">成员身份表的字段目录未读取，无法选择条件字段。</p>
<button :disabled="!!refreshing" @click="refreshCatalog(String(memberTargetBinding?.connection||''))">{{refreshing?'刷新中…':'刷新表结构'}}</button>
</template>
<template v-else>
<section v-for="rule in draft.membership" :key="rule.sourceInstance" class="sample-panel">
<div class="panelhead"><div><strong>{{rule.sourceInstance}}</strong> <small class="muted">{{registeredInstances.find(x=>x.id===rule.sourceInstance)?.label}}</small></div><span class="status-pill">{{rule.scope==='all'?'整表':'按条件'}}</span></div>
<div class="row">
<label>成员范围 *<AppSelect :model-value="rule.scope" :aria-label="'范围 '+rule.sourceInstance" :options="MEMBERSHIP_SCOPES" @update:model-value="scopeChanged(rule,$event)"/></label>
<p class="field-help">{{rule.scope==='all'?'将使用该来源表全部记录作为成员；表有逻辑删除等字段时请改用条件表达。':'至少一条条件；条件之间为 AND 组合。'}}</p>
</div>
<template v-if="rule.scope==='filtered'">
<div v-for="(c,ci) in rule.conditions" :key="ci" class="lm-cond">
<AppSelect :model-value="c.field" :aria-label="'条件字段 '+rule.sourceInstance" searchable placeholder="选择字段" :options="memberFieldOptions" @update:model-value="c.field=$event"/>
<AppSelect :model-value="c.operator" :aria-label="'操作符 '+rule.sourceInstance" :options="MEMBERSHIP_OPERATORS" @update:model-value="c.operator=($event as any)"/>
<template v-if="!['isnull','notnull'].includes(c.operator)">
<input :value="condValueText(c)" :aria-label="'条件值 '+rule.sourceInstance" placeholder="比较值（属于列表用逗号分隔）" @input="setCondValue(c,($event.target as HTMLInputElement).value)">
</template>
<span v-else class="muted lm-cond-null">—</span>
<button class="danger-ghost" @click="rmCond(rule,ci)">×</button>
</div>
<button @click="addCond(rule)">＋ 添加条件</button>
</template>
</section>
<p v-if="!draft.membership.length" class="inline-warning">当前对象尚未登记实例；请先在「实例识别」页登记实例。</p>
</template>
<p v-if="message" class="inline-error" role="alert">{{message}}</p>
<div class="lm-actions"><button class="primary" :disabled="saving" @click="save">{{saving?'保存中…':'保存'}}</button><button :disabled="saving" @click="closeEditor">取消</button><small class="field-help">保存写入当前项目草稿；取消放弃本次修改。保存后可在列表行「预览成员」。</small></div>
</template>
<!-- ===== 表字段关联编辑器（原有） ===== -->
<template v-else>
<h2>配置链接映射</h2>
<MappingDescription v-model="noteDraft" hint="说明两个对象如何关联，例如通过哪些字段找到对方。两端对象共用这一份说明。"/>
<p class="lm-note">{{name(b.object_type)}} → {{linkLabel(draft.relation)}} → {{name(draft.targetType)}}<template v-if="cardinalityOf(draft.relation)"> · {{cardLabel(draft.relation)}}</template></p>
<p v-if="switchMsg" class="inline-warning" role="status">{{switchMsg}}</p>
<div class="columns">
<section>
<h3>起点对象 · {{name(b.object_type)}}</h3>
<template v-if="!originTable&&!originSourceOptions.slice(1).length">
<p class="inline-warning">起点对象尚未配置实例来源表，无法选择链接字段。</p>
<button @click="setupEnd(b.object_type)">去配置{{name(b.object_type)}}实例来源</button>
</template>
<template v-else>
<label>起点来源表 *<AppSelect :model-value="draft.sourceId" aria-label="起点来源表" :options="originSourceOptions" @update:model-value="originSourceChanged"/></label>
<p v-if="hasManyOrigin" class="inline-warning">多条匹配来源不能作为链接字段来源</p>
<label>起点关联字段 *<AppSelect :key="originConnection+':'+originTable" :model-value="draft.field" searchable aria-label="起点关联字段" :options="fieldOptions(originCatalog)" :disabled="!originCatalog" @update:model-value="draft.field=$event"/></label>
<template v-if="!originConnection">
<p class="inline-warning">起点对象已选择表，但尚未配置数据连接，无法读取字段。</p>
<button @click="setupEnd(b.object_type)">配置{{name(b.object_type)}}数据连接 →</button>
</template>
<template v-else-if="!originCatalog">
<p class="inline-warning">尚未读取此表的字段，点击下方刷新后即可选择。</p>
<button :disabled="!!refreshing" @click="refreshCatalog(originConnection)">{{refreshing&&refreshing===originConnection?'刷新中…':'刷新表结构'}}</button>
</template>
</template>
</section>
<section>
<h3>终点对象 · {{name(draft.targetType)}}</h3>
<template v-if="!targetBinding">
<p class="inline-error">终点对象尚未启用对象映射。</p>
<button @click="setupEnd(draft.targetType)">去配置{{name(draft.targetType)}}</button>
</template>
<template v-else-if="!targetTable&&!targetSourceOptions.slice(1).length">
<p class="inline-error">终点对象尚未配置实例来源表，无法选择匹配字段。</p>
<button @click="setupEnd(draft.targetType)">去配置{{name(draft.targetType)}}实例来源</button>
</template>
<template v-else>
<label>终点来源表 *<AppSelect :model-value="draft.targetSourceId" aria-label="终点来源表" :options="targetSourceOptions" @update:model-value="targetSourceChanged"/></label>
<p v-if="hasManyTarget" class="inline-warning">多条匹配来源不能作为链接字段来源</p>
<label>终点匹配字段 *<AppSelect :key="targetConnection+':'+targetTable" :model-value="draft.targetField" searchable aria-label="终点匹配字段" :options="fieldOptions(targetCatalog)" :disabled="!targetCatalog" @update:model-value="draft.targetField=$event"/></label>
<template v-if="!targetConnection">
<p class="inline-warning">终点对象已选择表，但尚未配置数据连接，无法读取字段。</p>
<button @click="setupEnd(draft.targetType)">配置{{name(draft.targetType)}}数据连接 →</button>
</template>
<template v-else-if="!targetCatalog">
<p class="inline-warning">尚未读取此表的字段，点击下方刷新后即可选择。</p>
<button :disabled="!!refreshing" @click="refreshCatalog(targetConnection)">{{refreshing&&refreshing===targetConnection?'刷新中…':'刷新表结构'}}</button>
</template>
</template>
</section>
</div>
<p class="relation-sentence">{{pathPreview}}</p>
<p v-if="cardinalityHint" :class="cardinalityHint.cls">{{cardinalityHint.text}}</p>
<p class="field-help">只选两端对象已登记的表，沿实例匹配路径找到对象；不手填表名。配置「去配置」离开时草稿会保留，回到本页签自动恢复。</p>
<p v-if="message" class="inline-error" role="alert">{{message}}</p>
<div class="lm-actions"><button class="primary" :disabled="saving" @click="save">{{saving?'保存中…':'保存'}}</button><button :disabled="saving" @click="closeEditor">取消</button><small class="field-help">保存写入当前项目草稿；取消放弃本次修改。</small></div>
</template>
</template>
</div>
</template>
<style scoped>
.lm-top{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:10px;flex-wrap:wrap}
.lm-back{background:transparent;border-color:transparent;color:var(--blue);padding-left:0}
.lm-back:hover{color:var(--blue-deep);border-color:transparent}
.lm-note{background:var(--blue-soft);border:1px solid var(--blue-line);border-radius:7px;padding:10px 14px;font-size:13px;color:var(--muted);margin:12px 0}
.lm-actions{display:flex;gap:9px;align-items:center;margin-top:20px;flex-wrap:wrap}
.lm-inbound{margin-top:22px;border-top:1px solid var(--line);padding-top:14px}
.lm-inbound h3{margin:0 0 4px}
.lm-inbound-row{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:13px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
.lm-inbound-row:last-of-type{border-bottom:0}
.lm-inbound-row small{display:block;margin-top:3px}
.lm-inbound-row>button{flex:none}
@media(max-width:800px){.columns{grid-template-columns:1fr}}
.lm-desc{white-space:pre-wrap;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:13px;margin:6px 0 0}
</style>
<style scoped>
.lm-cond{display:grid;grid-template-columns:1.4fr 0.9fr 1.2fr auto;gap:8px;align-items:center;margin:8px 0}
.lm-cond .app-select{min-width:0}
.lm-cond input{width:100%}
.lm-cond-null{text-align:center}
@media(max-width:800px){.lm-cond{grid-template-columns:1fr 1fr}}
.lm-desc{white-space:pre-wrap;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:13px;margin:6px 0 0}
</style>

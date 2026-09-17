<script lang="ts">
// 模块级表单草稿缓存：从表单跳「取值规则库」页维护实现时保留配置草稿，
// 返回本对象的「属性取值」页签后自动恢复（不丢已填内容）。
// ProjectBinding 用 psPendingOf 决定回位页签。
let pendingWizard:{objectType:string;api:string;draft:any;baseline:string}|null=null
function psStash(objectType:string,api:string,draft:any,baseline:string){pendingWizard={objectType,api,draft:JSON.parse(JSON.stringify(draft)),baseline}}
/** 是否存在该对象待恢复的属性取值表单草稿（供 ProjectBinding 回位页签用）。 */
export function psPendingOf(objectType:string):boolean{return !!pendingWizard&&pendingWizard.objectType===objectType}
function psConsume(objectType:string){if(!psPendingOf(objectType))return null;const s=pendingWizard!;pendingWizard=null;return s}
</script>
<script setup lang="ts">
// 属性取值单页表单：按连接类型和目标属性类型联动展开，保存一次全量校验。
// 旧映射原样读入；切换来源只修改局部草稿，取消不改变已保存配置。
// 计算实现页返回时恢复未保存的表单；持久化仍走 form-save 和 commitProperty。
import {computed,inject,onBeforeUnmount,ref,watch} from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import {isQueryRule,isReusableRule,ruleInputErrors} from './queryRules'
import {scanSqlParams,inlineSqlErrors,effectiveParams,blankInlineSql} from './inlineSql'
import {isCalcFunction,calcRangeOk,calcConstantOk,CALC_TYPE_NAMES} from './calcFunction'
import {redisSourcesOf,sourceById,commitProperty,propertyView,tableCatalog,tableOptions,fieldOptions,catalogOf,keyTokens,refreshCatalogOf,TIMESTAMP_ENCODINGS,mysqlConnectionsOf,redisConnectionsOf,identityTableOf,databaseSummary,propertyLocalIssues,bindingIdentityOf,registeredInstancesOf} from './bindingModel'
import SourcePreview from './SourcePreview.vue'
import {localProperties,effectiveProperty,valueShapeOf,propertyTypeLabel,signatureDataType,dataTypeLabel} from '../ontology/propertyModel'
import type {FormGuardAPI,FormSaveAPI} from '../app/formGuard'
const props=defineProps<{projectState:any;refState:any;b:any;report?:any}>()
const emit=defineEmits(['before-change','changed','edit-state','go-tab','setup-end','goto-properties','open-ontology'])
function mutate(fn){emit('before-change');fn();emit('changed')}

// ---------- 本体引用版本的属性定义（只读要求） ----------
const graph=computed(()=>props.refState?.ontology?.['@graph']||[])
const properties=computed(()=>localProperties(graph.value,'mg:'+(props.b?.object_type||'')))
const key=(p:any)=>p['mg:apiName']||p['@id'].slice(3)
const label=(p:any)=>effectiveProperty(p,graph.value)['rdfs:label']||key(p)
const typeNames:Record<string,string>={'xsd:string':'文本','xsd:double':'数值','xsd:decimal':'精确小数','xsd:integer':'整数','xsd:boolean':'是／否','xsd:date':'日期','xsd:dateTime':'日期时间','xsd:array':'数组','xsd:struct':'结构体'}
const typeName=(p:any)=>propertyTypeLabel(p,graph.value)
function commentOf(p:any){return String(effectiveProperty(p,graph.value)?.['rdfs:comment']||'')}
function suffixOf(p:any){return String(effectiveProperty(p,graph.value)?.['mg:valueSuffix']||'')}
const connections=computed(()=>props.projectState.connections?.connections||[])
// ---------- 登记信息 / 关联聚合（方案 §3.3）：仅登记身份对象适用 ----------
const identityMode=computed(()=>bindingIdentityOf(props.b))
const registeredInstances=computed(()=>registeredInstancesOf(props.b))
const bindingOfType=(t:string)=>props.projectState.bindings.object_bindings.find((x:any)=>x.object_type===t)
const linkLabelOf=(relationId:string)=>graph.value.find((n:any)=>n['@id']==='mg:'+relationId)?.['rdfs:label']||relationId
const propLabelOf=(tb:any,api:string)=>{const ps=localProperties(graph.value,'mg:'+tb.object_type);const p=ps.find((x:any)=>key(x)===api);return p?String(label(p)):api}
// 集合端按端判定（方向无关，与后端 member_side_of 同一规则）：
// 出向(domain=本对象)一对多/多对多 → 成员=range；入向(range=本对象)多对一/多对多 → 成员=domain。
function memberSideOfNode(n:any):{tid:string;dir:'out'|'in'|''}{
  const dom=String(n['rdfs:domain']?.['@id']||'').replace(/^mg:/,'')
  const rng=String(n['rdfs:range']?.['@id']||'').replace(/^mg:/,'')
  const card=String(n['mg:cardinality']||'')
  const me=String(props.b?.object_type||'')
  if(dom===me&&['one-to-many','many-to-many'].includes(card)&&rng)return {tid:rng,dir:'out'}
  if(rng===me&&['many-to-one','many-to-many'].includes(card)&&dom)return {tid:dom,dir:'in'}
  return {tid:'',dir:''}
}
const memberTidOfRelation=(relationId:string)=>{const n=graph.value.find((x:any)=>x['@id']==='mg:'+relationId);return n?memberSideOfNode(n).tid:''}
// 聚合可用的成员链接：触及本对象的链接全部列出（入口不静默隐藏，方案 §3.3）；
// 数量关系满足按端判定且成员端已启用数据库身份并有表 → 可选；否则附缺失原因与去配置路径。
const aggregateLinkOptions=computed(()=>graph.value.filter((n:any)=>{
  if(n['@type']!=='owl:ObjectProperty')return false
  const dom=String(n['rdfs:domain']?.['@id']||'').replace(/^mg:/,'')
  const rng=String(n['rdfs:range']?.['@id']||'').replace(/^mg:/,'')
  const me=String(props.b?.object_type||'')
  return dom===me||rng===me
}).map((n:any)=>{
  const {tid,dir}=memberSideOfNode(n)
  const tb=bindingOfType(tid)
  const mode=tb?bindingIdentityOf(tb):''
  const ok=!!tid&&!!tb&&mode==='database'&&!!tb.table
  const reason=!tid?'该链接数量关系不支持成员集合（须出向一对多／多对多，或入向多对一／多对多）'
    :!tb?('成员对象「'+typeNameOf(tid)+'」尚未启用映射')
    :mode!=='database'?('成员对象「'+typeNameOf(tid)+'」的实例来源不是数据库表')
    :!tb.table?('成员对象「'+typeNameOf(tid)+'」未选择来源表'):''
  return {value:n['@id'].slice(3),label:String(n['rdfs:label']||n['@id'].slice(3))+(dir==='in'?'（入向）':''),tid,ok,reason,dir}
}))
const aggregateUsable=computed(()=>aggregateLinkOptions.value.filter((o:any)=>o.ok))
function gotoSetupTarget(tid:string){if(draftDirty.value&&!confirm(SWITCH_CONFIRM))return;emit('setup-end',tid)}
function gotoTargetProperties(tid:string){if(draftDirty.value&&!confirm(SWITCH_CONFIRM))return;emit('goto-properties',tid)}
function gotoLinksTab(){emit('go-tab','links')}
// 成员属性：成员端对象数值属性且已映射为身份表直接字段（字符串/field 直连；严格性由后端校验兜底）
function memberPropertyOptions(relationId:string){
  const tid=memberTidOfRelation(relationId);const tb=bindingOfType(tid)
  if(!tb)return []
  return localProperties(graph.value,'mg:'+tid).filter((p:any)=>{
    const range=String(effectiveProperty(p,graph.value)['rdfs:range']?.['@id']||'')
    if(!['xsd:double','xsd:decimal','xsd:integer'].includes(range))return false
    const v:any=propertyView(tb,key(p))
    if(!v)return false
    if(v.kind==='field')return !!String(v.field||'').trim()
    if(v.kind==='database'){const t=identityTableOf(tb);return !!t.connection&&t.connection===String(v.connection||'')&&t.table===String(v.table||'')&&!!String(v.result?.valueField||'').trim()}
    return false
  }).map((p:any)=>({value:key(p),label:label(p)}))
}
const aggregatePreview=ref<string|null>(null)
function connName(id:string){const c=connections.value.find((x:any)=>x.id===id);return c?(c.name||c.id):id}
const objectLabel=computed(()=>{const t=graph.value.find((n:any)=>n['@id']==='mg:'+(props.b?.object_type||''));return t?.['rdfs:label']||props.b?.object_type||''})
function typeNameOf(id:string){const t=graph.value.find((n:any)=>n['@id']==='mg:'+id);return t?.['rdfs:label']||id}

// ---------- 列表态：来源可读摘要 + 状态（本地推导 + 项目校验 report 兜底） ----------
function viewOf(api:string):any{return propertyView(props.b,api)}
// 当前配置沿用来源摘要（不带种类前缀）
function summaryOf(v:any):string{
  if(!v)return ''
  if(v.kind==='unknown')return '当前版本未识别的来源结构，已原样保留'
  if(v.kind==='computed'&&(v.mode==='inline'||v.mode==='inlineSql'))return '直接 SQL · '+connName(String((v.inline||v.inlineSql||{}).connection||''))
  if(v.kind==='computed'&&v.mode==='function'){const fn=implOf(String(v.implementation||''));return '计算函数 · '+(fn?.name||v.implementation)}
  if(v.kind==='database')return connName(String(v.connection||''))+' · '+(v.table||'未选表')+(v.result?.valueField?' · 值字段 '+v.result.valueField:'')
  if(v.kind==='field'){const src:any=v.source?sourceById(props.b,v.source):null;return (src?src.name||src.table||'未命名来源':props.b.table||'实例来源')+'.'+(v.field||'字段')}
  if(v.kind==='redis')return v.command+' '+(v.key||'（未填 Key 模板）')+(v.command==='HGET'&&v.hashField?' '+v.hashField:'')
  if(v.kind==='registered')return '登记信息 · '+(v.field==='id'?'实例编号':'显示名称')
  if(v.kind==='aggregate')return aggregateSummary(v)
  return implLabel(v.implementation)+(v.output?' · '+outputLabel(v.implementation,v.output):'')
}
function aggregateSummary(v:any):string{
  const rl=linkLabelOf(String(v.relation||''))
  const tb=v.relation?bindingOfType(String(memberTidOfRelation(String(v.relation))||'')):null
  const pl=v.relation&&tb?propLabelOf(tb,String(v.property||'')):''
  return '沿【'+rl+'】对成员【'+(pl||'未选属性')+'】求和'
}
// 列表态来源列可读摘要：数据库 <连接名> · <表> · <字段>[+时标]／Redis <命令> <key>／函数 <实现名>[ · <输出>]
function listSummary(v:any):string{
  if(!v)return '尚未配置'
  if(v.kind==='unknown')return '当前版本未识别的来源结构，已原样保留'
  if(v.kind==='computed'&&(v.mode==='inline'||v.mode==='inlineSql'))return '直接 SQL · '+connName(String((v.inline||v.inlineSql||{}).connection||''))
  if(v.kind==='computed'&&v.mode==='function'){const fn=implOf(String(v.implementation||''));return '计算函数 · '+(fn?.name||v.implementation)}
  if(v.kind==='database')return '数据库 '+connName(String(v.connection||''))+' · '+(v.table||'未选表')+(v.result?.valueField?' · '+v.result.valueField:'')+(v.result?.timestampField?' + '+v.result.timestampField:'')
  if(v.kind==='field'){
    const src:any=v.source?sourceById(props.b,v.source):null
    const conn=src?src.connection:props.b.connection,table=src?(src.table||props.b.table):(props.b.table)
    return '数据库 '+(conn?connName(String(conn)):'未选连接')+' · '+(table||'未选表')+' · '+(v.field||'字段')
  }
  if(v.kind==='redis')return 'Redis '+(v.command||'GET')+' '+(v.key||'（未填 Key 模板）')+(v.command==='HGET'&&v.hashField?' '+v.hashField:'')
  if(v.kind==='registered')return '登记信息 · '+(v.field==='id'?'实例编号':'显示名称')
  if(v.kind==='aggregate')return '关联聚合 · '+aggregateSummary(v).replace('沿','')
  return (isQueryRule(implOf(v.implementation))?'取值规则 ':'函数 ')+implLabel(v.implementation)+(v.output?' · '+outputLabel(v.implementation,v.output):'')
}
// 计算实现：契约名取自 guide_version 3 的通用契约；输出形态取实现页声明（缺省单值）
const contracts=computed(()=>(props.refState?.workflow?.functions||[]).filter((f:any)=>f.guide_version===3))
const implOptions=computed(()=>(props.projectState.implementations||[]).filter((i:any)=>!isQueryRule(i)||isReusableRule(i)||i.objectType===props.b.object_type).map((i:any)=>{const c=contracts.value.find((x:any)=>x.id===i.contractId);return {value:i.id,label:(i.name||c?.name||i.contractId)+(i.environment?'（'+i.environment+'）':'')}}))
function implOf(implId:string){return (props.projectState.implementations||[]).find((i:any)=>i.id===implId)}
function declaredTypeOf(implId:string,outputId:string):any{const impl=implOf(implId)
 if(isCalcFunction(impl))return {type:String(impl.output?.type||'string')}
 const c=contracts.value.find((x:any)=>x.id===impl?.contractId),output=c?.outputs?.find((o:any)=>o.id===outputId),d=impl?.outputDeclarations?.find((x:any)=>x.outputId===outputId)
 return isQueryRule(impl)&&outputId==='series'?(impl.result?.type==='scalar'?{type:impl.result.valueType}:{type:'timeSeries',valueType:impl.result?.valueType}):signatureDataType(output?.ref,graph.value,d)}
function declaredShapeOf(implId:string,outputId:string):'scalar'|'timeSeries'{return declaredTypeOf(implId,outputId)?.type==='timeSeries'?'timeSeries':'scalar'}

function implLabel(implId:string){const impl=implOf(implId);if(!impl)return implId||'未选择实现';const c=contracts.value.find((x:any)=>x.id===impl.contractId);return (impl.name||c?.name||impl.contractId)+(impl.environment?'（'+impl.environment+'）':'')}
function outputLabel(implId:string,outputId:string){const impl=implOf(implId);const c=contracts.value.find((x:any)=>x.id===impl?.contractId);return isQueryRule(impl)?(impl.result?.type==='scalar'?'查询单值':'查询时间序列'):(c?.outputs||[]).find((o:any)=>o.id===outputId)?.name||outputId||''}
function outputsOf(implId:string){const impl=implOf(implId);const c=contracts.value.find((x:any)=>x.id===impl?.contractId)
 if(isCalcFunction(impl))return [{value:String(impl.output?.id||''),label:(impl.output?.name||'计算输出')+' · '+(CALC_TYPE_NAMES[String(impl.output?.type||'')]||impl.output?.type)}]
 return isQueryRule(impl)?[{value:'series',label:impl.result?.type==='scalar'?'查询结果 · 单值':'查询结果 · 时间序列'}]:(c?.outputs||[]).map((o:any)=>({value:o.id,label:o.name+' · '+dataTypeLabel(declaredTypeOf(implId,o.id))}))}
// 组件级补充校验：bindingModel.propertyLocalIssues 之外的结构规则（computed 输出形态匹配需读实现声明）
function extraIssuesOf(api:string,v:any,shape:'scalar'|'timeSeries'):string[]{
  const out:string[]=[]
  const ownProp=properties.value.find((x:any)=>key(x)===api)
  // 清单态也会逐属性调用本函数（此时 selectedProp 为空）：一律按 api 查节点并做空值防护
  const rangeOf=(node:any)=>node?String(effectiveProperty(node,graph.value)?.['rdfs:range']?.['@id']||'').replace('xsd:',''):''
  if(v?.kind==='registered'){
    if(rangeOf(ownProp)!=='string')out.push('登记信息仅支持文本属性')
    if(!['id','label'].includes(String(v.field||'')))out.push('请选择登记信息字段')
    return out
  }
  if(v?.kind==='aggregate'){
    if(!['double','decimal','integer'].includes(rangeOf(ownProp)))out.push('聚合目标属性必须为数值类型')
    if(!String(v.relation||'').trim())out.push('请选择关联链接')
    if(!String(v.property||'').trim())out.push('请选择成员属性')
    return out
  }
  if(v?.kind==='computed'&&(v.mode==='inline'||v.mode==='inlineSql')){
    // 内联 SQL：与后端内联分支同一套检查（连接、单条 SELECT、参数绑定、来源合法性）。
    const inline=(v as any).inline||(v as any).inlineSql
    const identityReady=identityMode.value==='registered'||!!String(props.b?.primary_key||'').trim()
    out.push(...inlineSqlErrors(inline,{
      connections:mysqlConnectionsOf(props.projectState).map((c:any)=>({id:c.value,engine:'mysql'})),
      parameters:props.projectState.parameters||{},identityReady}))
    return out
  }
  if(v?.kind==='computed'&&v.mode==='function'){
    const fn=implOf(String(v.implementation||''))
    if(!isCalcFunction(fn)){out.push('所选计算函数不存在');return out}
    if(shape==='timeSeries')out.push('计算函数输出为单值，不能绑定时间序列属性')
    else if(!calcRangeOk(String(fn.output?.type||''),rangeOf(ownProp)))out.push('计算函数输出类型与属性数据类型不匹配')
    const entries=v.inputs&&typeof v.inputs==='object'?v.inputs:{}
    for(const p of fn.inputs||[]){
      const entry=entries[p.id]
      if(!entry||typeof entry!=='object'){out.push(`计算函数输入「${p.name}」未绑定取值`);continue}
      if(entry.from==='property'){
        const ref=String(entry.property||'')
        if(!ref){out.push(`计算函数输入「${p.name}」未选择属性`);continue}
        if(ref===api){out.push('取值配置存在循环依赖');continue}
        const refProp=properties.value.find((x:any)=>key(x)===ref)
        if(!refProp){out.push(`计算函数输入「${p.name}」引用的属性不存在`);continue}
        if(valueShapeOf(refProp,graph.value)==='timeSeries'||!calcRangeOk(p.type,String(effectiveProperty(refProp,graph.value)?.['rdfs:range']?.['@id']||'').replace('xsd:','')))
          out.push(`计算函数输入「${p.name}」引用的属性「${label(refProp)}」数据类型不匹配`)
      }else if(entry.from==='constant'){
        if(!calcConstantOk(p.type,entry.value))out.push(`计算函数输入「${p.name}」的固定值无效`)
      }else out.push(`计算函数输入「${p.name}」的绑定来源无效`)
    }
    for(const inputId of Object.keys(entries))if(!(fn.inputs||[]).some((p:any)=>p.id===inputId))
      out.push(`计算函数不存在输入 ${inputId}，请重新绑定（函数签名可能已修改）`)
    if(calcCycleExists(api,v))out.push('取值配置存在循环依赖')
    return out
  }
  if(v?.kind==='computed'&&v.implementation&&v.output&&declaredShapeOf(v.implementation,v.output)!==shape)out.push('函数输出数据类型与属性要求不匹配')
  if(v?.kind==='computed'&&isQueryRule(implOf(v.implementation))){
    const rule=implOf(v.implementation)
    if(isReusableRule(rule))out.push(...ruleInputErrors(v.inputs,props.b,rule))
    if(!isReusableRule(rule)&&rule.objectType!==props.b.object_type)out.push('取值规则适用对象与当前对象不一致')
    const p=properties.value.find((p:any)=>key(p)===api)
    if([3,4].includes(rule.schemaVersion)){
      // V3/V4 按 result.type/result.valueType 与属性实际数据类型比对（形态由上方 declaredShapeOf 通用比对覆盖）；
      // 数值属性兼容 double，与后端 project_validation 的绑定校验一致。
      const target=rangeOf(p)
      const actual=String(rule.result?.valueType||'')
      if(p&&target&&actual&&target!==actual&&!(['double','decimal','integer'].includes(target)&&actual==='double'))out.push('取值规则值类型与属性数据类型不匹配')
    }else if(p&&!['double','decimal','integer'].includes(rangeOf(p)))out.push('取值规则返回数值时间序列，观测值类型不匹配')
  }
  return out
}
function issuesOf(api:string,p:any,v:any):string[]{
  if(!v)return []
  const shape=valueShapeOf(p,graph.value)
  return [...propertyLocalIssues(props.b,api,v,shape),...extraIssuesOf(api,v,shape)]
}
// 元数据待核对（轻量判断）：引用连接无目录缓存，或目录中查不到表／关键字段
function fieldIn(table:any,field:string){return !!table&&!!(table.fields||[]).some((f:any)=>f.name===field)}
function metaPending(api:string,v:any):boolean{
  if(v.kind==='database'){
    if(!catalogOf(props.projectState,v.connection))return true
    const t=tableCatalog(props.projectState,v.connection,v.table)
    if(!t)return true
    if(!fieldIn(t,String(v.result?.valueField||'').trim()))return true
    if(v.result?.timestampField&&!fieldIn(t,v.result.timestampField))return true
    if(v.result?.duplicateTimestamp==='secondarySort'&&v.result?.secondarySortField&&!fieldIn(t,v.result.secondarySortField))return true
    if(v.lookup?.timeRange?.field&&!fieldIn(t,v.lookup.timeRange.field))return true
    for(const m of (v.lookup?.match||[]))if(String(m?.field||'').trim()&&!fieldIn(t,m.field))return true
    return false
  }
  if(v.kind==='field'){
    const src:any=v.source?sourceById(props.b,v.source):null
    const conn=src?src.connection:props.b.connection,table=src?src.table:props.b.table
    if(!conn)return false
    if(!catalogOf(props.projectState,conn))return true
    const t=tableCatalog(props.projectState,conn,table)
    if(!t)return true
    if(String(v.field||'').trim()&&!fieldIn(t,v.field))return true
    if(v.selection==='latest'&&v.timeField&&!fieldIn(t,v.timeField))return true
    return false
  }
  return false
}
// 列表状态：已配置=绿／待配置（含元数据待核对）=橙／配置有误=红／未知结构=灰；额外说明进 title
function statusOf(api:string,p:any,v:any):{text:string;cls:string;title:string}{
  if(v&&v.kind==='unknown')return {text:'未知结构',cls:'pill-unknown',title:'当前版本未识别的来源结构，已原样保留'}
  if(!v)return {text:'待配置',cls:'pill-pending',title:''}
  if(issuesOf(api,p,v).length)return {text:'配置有误',cls:'pill-error',title:'保存前校验未通过，进入「修改」查看具体问题'}
  const item=(props.report?.items||[]).find((i:any)=>i.kind==='propertySource'&&i.id===props.b.object_type+'.'+api)
  if(item&&item.status==='invalid')return {text:'配置有误',cls:'pill-error',title:'项目级校验未通过，进入「修改」查看具体问题'}
  if(metaPending(api,v))return {text:'元数据待核对',cls:'pill-pending',title:'配置结构正确，但引用的表／字段暂无法在表结构目录中核对，可刷新表结构后复核'}
  return {text:'已配置',cls:'pill-ok',title:'配置校验通过 · 未执行验证'}
}
const rows=computed(()=>properties.value.map((p:any)=>{const api=key(p);const v=viewOf(api);return {p,api,view:v,shape:valueShapeOf(p,graph.value),status:statusOf(api,p,v)}}))
// 属性列小字：类型 · 形态、单位（单位无则省略）
function propSmall(r:any):string{const u=suffixOf(r.p);return typeName(r.p)+(u?' · '+u:'')}

// ---------- 整页两态：列表态 ↔ 配置态（configuring 驱动，互斥整页替换） ----------
const configuring=ref(false)

// ---------- 局部草稿编辑器：切换只改 draft，保存才 commitProperty，取消不动 b ----------
const selectedApi=ref(''),draft=ref<any>(null),editError=ref(''),switchMsg=ref(''),samplePrimary=ref('123')
// 草稿脏判定：与打开时的基线粗比较（整体 JSON.stringify；剔除视图模型临时字段 mode）
const baseline=ref('')
function normalizeDraft(d:any):string{if(!d)return '';const c=JSON.parse(JSON.stringify(d));delete c.mode;return JSON.stringify(c)}
const draftDirty=computed(()=>!!draft.value&&normalizeDraft(draft.value)!==baseline.value)
const selectedProp=computed(()=>properties.value.find((x:any)=>key(x)===selectedApi.value))
const draftShape=computed<'scalar'|'timeSeries'>(()=>selectedProp.value?valueShapeOf(selectedProp.value,graph.value):'scalar')
const selectedMeta=computed(()=>{const p=selectedProp.value;if(!p)return null;return {label:label(p),comment:commentOf(p),type:typeName(p),shape:valueShapeOf(p,graph.value),suffix:suffixOf(p)}})
// 配置态页眉徽标：<类型> · <单值/时间序列> · <单位>（单位无则省略）；表单顶部可见
const headBadge=computed(()=>{const m=selectedMeta.value;if(!m)return '';return m.type+(m.suffix?' · '+m.suffix:'')})
const numericProperty=computed(()=>['double','decimal','integer'].includes(String(effectiveProperty(selectedProp.value,graph.value)?.['rdfs:range']?.['@id']||'').replace('xsd:','')))
function defaultConversion(){const t=String(effectiveProperty(selectedProp.value,graph.value)?.['rdfs:range']?.['@id']||'');return ['xsd:double','xsd:decimal','xsd:integer'].includes(t)?'number':'text'}
function emptyResult(){return {valueField:'',timestampField:'',timestampEncoding:'',timezone:'',order:'ascending',duplicateTimestamp:'error',secondarySortField:'',secondarySortOrder:'ascending',selection:'',missing:'null'}}
function emptyDatabaseDraft(){return {kind:'database',mode:'direct',connection:'',table:'',lookup:{match:[],timeRange:null},result:emptyResult()}}
function freshDbDraft(){return draftShape.value==='timeSeries'?emptyDatabaseDraft():{kind:'field',mode:'identity',source:'',field:'',connection:String(props.b?.connection||''),table:String(props.b?.table||'')}}
function openEditor(api:string){
  selectedApi.value=api;editError.value='';switchMsg.value='';refreshMessage.value='';resetSaveState()
  const v=propertyView(props.b,api)
  if(!v)draft.value=freshDbDraft()
  else{
    const d=JSON.parse(JSON.stringify(v))
    if(v.kind==='field'){
      // 旧来源映射原样进表单：identity 字符串／已登记来源各自的连接与表带入草稿；
      // 未改连接／表就保存时仍按原编码落盘（字符串／field+source），不偷偷重写。
      d.mode=v.source?'registered':'identity'
      const src:any=v.source?sourceById(props.b,v.source):null
      d.connection=src?src.connection:String(props.b.connection||'')
      d.table=src?src.table:String(props.b.table||'')
    }
    if(v.kind==='computed'&&(v as any).mode==='function'){
      draft.value={kind:'computed',mode:'function',inline:blankInlineSql(),implementation:d.implementation,output:d.output,...(d.inputs!==undefined?{inputs:d.inputs}:{})}
    }else if(v.kind==='computed'&&(v as any).mode==='inlineSql'){
      // 内联 SQL 原文与绑定进草稿；规则侧字段置空（两种方式切换互不覆盖，保存只落一种）。
      draft.value={kind:'computed',mode:'inline',inline:{...d.inlineSql},implementation:'',output:''}
    }else if(v.kind==='computed'){
      draft.value={kind:'computed',mode:'rule',inline:blankInlineSql(),implementation:d.implementation,output:d.output,...(d.inputs!==undefined?{inputs:d.inputs}:{})}
    }else draft.value=d
  }
  baseline.value=normalizeDraft(draft.value)
  configuring.value=true
}
function closeEditor(){resetSaveState();configuring.value=false;selectedApi.value='';draft.value=null;editError.value='';switchMsg.value='';refreshMessage.value=''}
// 返回属性清单前的确认保护；底部「取消」是明确的放弃动作，不二次确认。
const SWITCH_CONFIRM='当前属性有未保存的修改，继续编辑或放弃？'
function requestClose(){if(!draftDirty.value||confirm(SWITCH_CONFIRM))closeEditor()}
function openProperty(api:string){if(!draftDirty.value||confirm(SWITCH_CONFIRM))openEditor(api)}
// ---------- T00 表单守卫：表单打开注册、关闭注销；保存走 form-save ----------
const formGuard=inject<FormGuardAPI|null>('form-guard',null)
const formSave=inject<FormSaveAPI|null>('form-save',null)
const guard={isDirty:()=>draftDirty.value,discard:()=>closeEditor()}
watch(configuring,open=>{emit('edit-state',open);open?formGuard?.register(guard):formGuard?.unregister(guard)},{immediate:true})
onBeforeUnmount(()=>formGuard?.unregister(guard))
defineExpose({dirty:()=>draftDirty.value,discard:closeEditor})
// 取值方式与连接引擎分开：读取数据源 / 调用函数。
const sourceCards=computed(()=>{const out=[
  {value:'data',title:'读取数据源',desc:'选择连接，读取表字段或 Redis 值'},
  {value:'computed',title:'调用取值规则 / 函数',desc:'直接编写 SQL，或引用项目取值规则与原有实现'}]
  if(identityMode.value==='registered'){
    out.push({value:'registered',title:'登记信息',desc:'读取本对象登记实例的编号或显示名称'})
  }
  return out})
function pickCardRaw(v:string){
  if(v==='registered'){draft.value={kind:'registered',field:'label'};return}
  pickCard(v)
}
const topKind=computed(()=>{const d=draft.value;if(!d||d.kind==='none')return '';if(d.kind==='computed')return 'computed';if(d.kind==='registered')return 'registered';if(d.kind==='aggregate')return 'aggregate';return 'data'})
function pickCard(v:string){if(v!==topKind.value)switchKind(v==='data'?'db':v)}
const dataConnectionOptions=computed(()=>[
  {value:'',label:'请选择数据连接'},
  ...mysqlConnectionsOf(props.projectState).map(c=>({value:'db:'+c.value,label:c.label+' · MySQL'})),
  ...redisConnectionsOf(props.projectState).map(c=>({value:'redis:'+c.value,label:c.label+' · Redis',disabled:draftShape.value==='timeSeries'})),
  ...redisSourcesOf(props.b).map(s=>({value:'source:'+s.id,label:'已登记 Redis 来源 · '+(s.name||s.id),disabled:draftShape.value==='timeSeries'}))])
const dataConnection=computed(()=>{const d=draft.value;if(!d)return '';if(d.kind==='redis')return d.connection?'redis:'+d.connection:d.source?'source:'+d.source:'';return d.connection?'db:'+d.connection:''})
function dataConnectionChanged(v:string){
  if(v===dataConnection.value)return
  if(v.startsWith('redis:')||v.startsWith('source:')){
    if(draftShape.value==='timeSeries')return
    switchKind('redis');setRedisTarget((v.startsWith('redis:')?'conn:':'src:')+v.slice(v.indexOf(':')+1))
  }else{
    if(draft.value?.kind!=='field'&&draft.value?.kind!=='database')draft.value=emptyDatabaseDraft()
    dbConnChanged(v.startsWith('db:')?v.slice(3):'')
  }
  editError.value=''
}
function switchKind(v:string){
  if(v==='')draft.value={kind:'none'}
  else if(v==='redis')draft.value={kind:'redis',source:'',connection:'',command:'GET',key:'',params:{},hashField:'',conversion:defaultConversion(),missing:'null'}
  else if(v==='computed')draft.value={kind:'computed',mode:'inline',inline:blankInlineSql(),implementation:'',output:''}  // 新配置默认直接 SQL
  else draft.value=freshDbDraft()
}
// ---------- 数据库路径（连接联动选表）：连接 → 目录搜索选任意表 ＋ 快捷使用实例来源表 ----------
// field 草稿（旧字符串／已登记来源）改动连接或表时提升为 database 直选草稿（清空定位与结果，提示重选）。
function draftToDatabase():any{
  const d:any=draft.value
  if(d.kind!=='field')return d
  const nd=emptyDatabaseDraft()
  nd.connection=String(d.connection||'');nd.table=String(d.table||'')
  draft.value=nd
  return nd
}
function dbConnChanged(v:string){
  if(v===draft.value?.connection)return
  const nd=draftToDatabase()
  nd.connection=v;nd.table='';nd.lookup={match:[],timeRange:null};nd.result=emptyResult()
  switchMsg.value='已切换数据连接，请重新选择数据表与字段。'
}
function dbTableChanged(v:string){
  if(v===draft.value?.table)return
  const nd=draftToDatabase()
  nd.table=v;nd.lookup={match:[],timeRange:null};nd.result=emptyResult()
  switchMsg.value='已切换数据表，请重新选择字段与匹配条件。'
}
function useIdentityTable(){
  const t=identityTableOf(props.b)
  if(!t.connection||!t.table){switchMsg.value='实例来源尚未配置来源表，无法使用快捷选择；请先完成「实例识别」。';return}
  if(draft.value?.connection===t.connection&&draft.value?.table===t.table)return
  const nd=emptyDatabaseDraft()
  nd.connection=t.connection;nd.table=t.table
  draft.value=nd
  switchMsg.value='已使用实例来源表：按实例主键自动定位，无需匹配条件。'
}
// field 草稿的来源信息（registered 匹配链展示）与目录
const draftSource=computed(()=>{const d:any=draft.value;if(!d||d.kind!=='field'||!d.source)return null;return sourceById(props.b,d.source) as any})
const draftFieldCatalog=computed(()=>{const d:any=draft.value;if(!d||(d.kind!=='field'&&d.kind!=='database'))return null;return tableCatalog(props.projectState,String(d.connection||''),String(d.table||''))})
const draftCardinality=computed(()=>{const src:any=draftSource.value;return src&&src.kind==='db'?src.cardinality:'one'})
const directCatalog=computed(()=>{const d:any=draft.value;return d&&d.kind==='database'?draftFieldCatalog.value:null})
const directFieldOptions=computed(()=>directCatalog.value?fieldOptions(directCatalog.value):[])
const directIdentityRow=computed(()=>{const d:any=draft.value;if(!d||d.kind!=='database')return false;const t=identityTableOf(props.b);return !!d.connection&&!!d.table&&d.connection===t.connection&&d.table===t.table})
// 匹配条件：来源字段 + 操作符（固定等于）+ 比较值来源 + 按类型的取值控件
const matchKindOptions=computed(()=>[
  {value:'identityKey',label:'当前实例主键（'+(props.b.primary_key||'未配置')+'）'},
  {value:'identityField',label:'当前实例身份表字段'},
  {value:'property',label:'已映射单值属性'},
  {value:'constant',label:'常量'},
  {value:'parameter',label:'项目参数'}])
const identityFieldOptions=computed(()=>{const t=identityTableOf(props.b);return t.connection&&t.table?fieldOptions(tableCatalog(props.projectState,t.connection,t.table)):[]})
const scalarPropertyOptions=computed(()=>properties.value.filter((p:any)=>{
  const api=key(p);if(api===selectedApi.value)return false
  const v:any=propertyView(props.b,api);if(!v)return false
  if(v.kind==='field')return !!String(v.field||'').trim()
  if(v.kind==='computed')return v.mode!=='inlineSql'&&declaredShapeOf(v.implementation,v.output)==='scalar'
  return false
}).map((p:any)=>({value:key(p),label:label(p)})))
const parameterOptions=computed(()=>Object.entries(props.projectState.parameters||{}).map(([k,v])=>({value:k,label:k+(String(v??'').trim()!==''?'（当前值 '+v+'）':'')})))
function addMatch(){const d:any=draft.value;if(!d.lookup)d.lookup={match:[],timeRange:null};d.lookup.match.push({field:'',operator:'eq',value:{kind:'identityKey'}})}
function removeMatch(i:number){draft.value.lookup.match.splice(i,1)}
function setMatchKind(m:any,kind:string){
  m.value=kind==='identityField'?{kind:'identityField',field:''}
    :kind==='property'?{kind:'property',property:''}
    :kind==='constant'?{kind:'constant',value:''}
    :kind==='parameter'?{kind:'parameter',parameter:''}
    :{kind:'identityKey'}
}
function dupMatch(i:number){const ms=draft.value?.lookup?.match||[];const f=String(ms[i]?.field||'').trim();if(!f)return false;return ms.filter((m:any)=>String(m?.field||'').trim()===f).length>1}
function constantPlaceholder(field:string){const f=directCatalog.value?.fields?.find((x:any)=>x.name===field);return f&&/double|decimal|numeric|int|float|number/i.test(String(f.dataType||''))?'例如 100（数值字段请填数值）':'例如 storage_cluster'}
function addTimeRange(){const d:any=draft.value;d.lookup.timeRange={field:'',start:{kind:'context',name:'startTime'},end:{kind:'context',name:'endTime'},bounds:'[start,end)'}}
function setEncoding(v:string){const d:any=draft.value;d.result.timestampEncoding=v;if(v==='datetime'&&!String(d.result.timezone||'').trim())d.result.timezone=String(props.projectState.parameters?.timezone||'')}
function setSelection(v:string){const d:any=draft.value;d.result.selection=v;d.result.timestampField='';d.result.timestampEncoding='';d.result.timezone=''}
// ---------- Redis：来源（项目连接直连／已登记来源）＋ Key 模板参数绑定 ----------
function setRedisTarget(v:string){const d:any=draft.value;if(!d)return;if(v.startsWith('conn:')){d.connection=v.slice(5);d.source=''}else if(v.startsWith('src:')){d.source=v.slice(4);d.connection=''}else{d.connection='';d.source=''}}
const paramOptions=computed(()=>[
  {value:'primary',label:'实例主键（'+(props.b.primary_key||'未配置')+'）'},
  ...identityFieldOptions.value.map(f=>({value:'idfield:'+f.value,label:'身份表字段 · '+f.label})),
  ...properties.value.filter((p:any)=>{const v:any=propertyView(props.b,key(p));return v&&v.kind==='field'&&!!v.field}).map((p:any)=>({value:'prop:'+key(p),label:label(p)+' · 字段'}))])
function paramModel(token:string):string{const v=draft.value?.params?.[token];if(!v)return '';if(v.from==='primary')return 'primary';if(v.from==='identityField')return 'idfield:'+String(v.field||'');return 'prop:'+String(v.property||'')}
function setParam(token:string,value:string){const d:any=draft.value;if(!d)return;if(!d.params)d.params={};if(!value)delete d.params[token];else if(value==='primary')d.params[token]={from:'primary'};else if(value.startsWith('idfield:'))d.params[token]={from:'identityField',field:value.slice(8)};else d.params[token]={from:'property',property:value.slice(5)}}
const previewKey=computed(()=>{const d:any=draft.value;if(!d||d.kind!=='redis')return '';const sample=String(samplePrimary.value||'').trim()||'123';return String(d.key||'').replace(/\{([^{}\s]+)\}/g,(m,t)=>{const v=d.params?.[t];return v?.from==='primary'?sample:v?.from==='identityField'?'身份字段值':v?.from==='property'?'属性值':m})})
// ---------- 计算函数：展示实现输入绑定并选择输出 ----------
const computedShapeMismatch=computed(()=>{const d:any=draft.value;if(!d||d.kind!=='computed'||!d.implementation||!d.output)return false;return declaredShapeOf(d.implementation,d.output)!==draftShape.value})
const saveDisabled=computed(()=>computedShapeMismatch.value||draft.value?.kind==='aggregate')
const needTimestamp=computed(()=>{const d:any=draft.value;if(!d||d.kind!=='database')return false;return draftShape.value==='timeSeries'||d.result?.selection==='latest'})
function bindingRefText(input:any){
  const r=input?.ref
  if(r?.kind==='object')return '对象 · '+(r.id||'').replace(/^mg:/,'')
  if(r?.kind==='property')return '属性 · '+(r.id||'')
  return '基础 · '+(r?.dataType||'待选')
}
function bindingSourceText(s:any){
  if(!s||s.kind==='none'||!s.kind)return '未绑定'
  if(s.kind==='object')return '对象'+(s.objectType?' · '+String(s.objectType).replace(/^mg:/,''):'')
  if(s.kind==='field')return '数据字段 · '+(s.property||'未选属性')
  if(s.kind==='constant')return '常量 '+(s.value??'（空）')
  if(s.kind==='output')return '计算输出'+(s.outputId?' · '+s.outputId:'（未选）')
  return '未知'
}
// V3/V4 规则按 inputs 声明展示输入：binding 参数在属性绑定时填写，runtime 参数由调用方传入、只说明不填写。
function declaredRuleInputs(implId:string):{binding:any[];runtime:any[]}|null{
  const impl=implOf(implId)
  if(!isQueryRule(impl)||![3,4].includes(impl.schemaVersion))return null
  const rows=(Array.isArray(impl.inputs)?impl.inputs:[]).filter((p:any)=>p&&typeof p==='object')
  return {binding:rows.filter((p:any)=>p.source!=='runtime'),runtime:rows.filter((p:any)=>p.source==='runtime')}
}
function selectImplementation(id:string){
 if(isCalcFunction(implOf(id))){
   const fn=implOf(id)
   draft.value.mode='function';draft.value.implementation=id;draft.value.output=String(fn.output?.id||'');draft.value.inputs={}
   return
 }
 draft.value.mode='rule';draft.value.implementation=id;draft.value.output='';delete draft.value.inputs
 if(isReusableRule(implOf(id))){
   const declared=declaredRuleInputs(id)
   if(declared){const o:Record<string,string>={};for(const p of declared.binding)o[p.name]='';draft.value.inputs=o}
   else draft.value.inputs={model_name:props.b.table||'',attr_name:'',model_id:'{id}'}
   draft.value.output='series'
 }
}
const reusableInput=computed(()=>draft.value?.kind==='computed'&&draft.value.mode!=='inline'&&isReusableRule(implOf(draft.value.implementation)))
const declaredInputs=computed(()=>{const d:any=draft.value;return d?.kind==='computed'&&d.implementation?declaredRuleInputs(d.implementation):null})
function setRuleInput(key:string,value:string){draft.value.inputs??={};draft.value.inputs[key]=value}
// ---------- 直接编写 SQL（内联取值）：参数识别、绑定读写、只读输出约定 ----------
const inlineMode=computed(()=>draft.value?.kind==='computed'&&draft.value.mode==='inline')
const inlineParams=computed(()=>inlineMode.value?scanSqlParams(String(draft.value.inline?.sql||'')):[])
const inlineOutputNote=computed(()=>{if(!inlineMode.value)return '';return draftShape.value==='timeSeries'
  ?'约定返回多行：列名 timestamp 与 value，SQL 应写 ORDER BY 时间列升序。输出约定，尚未执行验证。'
  :'约定返回一行：输出列名 value。输出约定，尚未执行验证。'})
const constTypes=[{value:'string',label:'文本'},{value:'double',label:'数值'},{value:'boolean',label:'是／否'},{value:'dateTime',label:'日期时间'}]
const instanceIdLabel=computed(()=>identityMode.value==='registered'?'登记实例编号':'当前实例主键（'+(props.b.primary_key||'未配置')+'）')
const inlineBindingOptions=computed(()=>[
  {value:'',label:'待绑定（请选择来源）'},{value:'constant',label:'固定值'},
  {value:'projectParameter',label:'项目参数'},{value:'instanceId',label:'实例编号 · '+instanceIdLabel.value}])
function inlineBindingOf(name:string){return draft.value?.inline?.params?.[name]}
function inlineBindingKind(name:string){const b=inlineBindingOf(name);return b&&typeof b==='object'?String(b.from||''):''}
function setInlineBinding(name:string,v:string){
  const p=draft.value.inline;if(!p.params)p.params={}
  if(!v)delete p.params[name]
  else if(v==='constant')p.params[name]={from:'constant',dataType:'string',value:''}
  else if(v==='projectParameter')p.params[name]={from:'projectParameter',key:''}
  else if(v==='instanceId')p.params[name]={from:'instanceId'}
}
// V3/V4 规则值类型与目标属性数据类型是否不相容（与 extraIssuesOf 的保存拦截同一比较）
// 计算函数依赖环：以当前对象属性为节点，函数输入的属性引用为边（含本次草稿），DFS 找回到起点的路径
function calcDepsOf(q:string,editingApi:string,editingView:any):string[]{
  if(q===editingApi){
    const entries=editingView?.inputs&&typeof editingView.inputs==='object'?editingView.inputs:{}
    return Object.values(entries).filter((e:any)=>e?.from==='property').map((e:any)=>String(e.property))
  }
  const view:any=propertyView(props.b,q)
  if(view?.kind==='computed'&&view.mode==='function'){
    return Object.values(view.inputs||{}).filter((e:any)=>e?.from==='property').map((e:any)=>String(e.property))
  }
  return []
}
function calcCycleExists(api:string,editingView:any):boolean{
  const visiting=new Set<string>(),done=new Set<string>()
  const walk=(node:string):boolean=>{
    if(visiting.has(node))return node===api||visiting.has(api)
    if(done.has(node))return false
    visiting.add(node)
    for(const child of calcDepsOf(node,api,editingView))if(walk(child))return true
    visiting.delete(node);done.add(node)
    return false
  }
  return walk(api)
}
// 计算函数绑定辅助
const fnOf=(id:string)=>implOf(String(id||''))
const calcFnOptions=computed(()=>implOptions.value.filter((o:any)=>isCalcFunction(implOf(o.value))))
const fnInputs=computed(()=>{const d:any=draft.value;return d?.kind==='computed'&&d.mode==='function'?((fnOf(d.implementation)?.inputs||[]) as any[]):[]})
function fnBindingOf(inputId:string){const d:any=draft.value;return d?.inputs?.[inputId]}
function setFnBinding(inputId:string,kind:string){
  const d:any=draft.value;if(!d.inputs)d.inputs={}
  if(!kind)delete d.inputs[inputId]
  else if(kind==='property')d.inputs[inputId]={from:'property',property:''}
  else d.inputs[inputId]={from:'constant',value:''}
}
function fnPropertyOptions(calcType:string){
  return properties.value.filter((p:any)=>{
    const api=key(p);if(api===selectedApi.value)return false
    if(valueShapeOf(p,graph.value)==='timeSeries')return false
    const range=String(effectiveProperty(p,graph.value)?.['rdfs:range']?.['@id']||'').replace('xsd:','')
    return calcRangeOk(calcType,range)
  }).map((p:any)=>({value:key(p),label:label(p)+' · '+typeName(p)}))
}
const ruleValueTypeMismatch=computed(()=>{
  const d:any=draft.value
  if(!d||d.kind!=='computed'||!d.implementation)return false
  const rule=implOf(d.implementation)
  const target=String(effectiveProperty(selectedProp.value,graph.value)?.['rdfs:range']?.['@id']||'').replace('xsd:','')
  if(isCalcFunction(rule))return !calcRangeOk(String(rule.output?.type||''),target)
  if(!isQueryRule(rule)||![3,4].includes(rule.schemaVersion))return false
  const actual=String(rule.result?.valueType||'')
  return !!target&&!!actual&&target!==actual&&!(['double','decimal','integer'].includes(target)&&actual==='double')
})
const implInputRows=computed(()=>{
  const d:any=draft.value
  if(!d||d.kind!=='computed'||!d.implementation)return []
  const impl=implOf(d.implementation);if(!impl)return []
  if(isQueryRule(impl))return [{name:'实例主键',ref:'当前对象',binding:props.b.table+'.'+props.b.primary_key},{name:'开始时间 / 结束时间',ref:'查询参数',binding:'由调用方传入'}]
  const c=contracts.value.find((x:any)=>x.id===impl.contractId)
  return (c?.inputs||[]).map((input:any)=>{
    const b=(impl.inputBindings||[]).find((x:any)=>x.inputId===input.id)
    return {name:input.name||input.id,ref:bindingRefText(input),binding:bindingSourceText(b?.source)}
  })
})
// Redis 结果转换与目标类型核对（提示性，不替代后端校验）
const conversionLabels:Record<string,string>={number:'数值',integer:'整数',text:'文本'}
const conversionMismatch=computed(()=>{
  const d:any=draft.value
  if(!d||d.kind!=='redis')return false
  const t=String(effectiveProperty(selectedProp.value,graph.value)?.['rdfs:range']?.['@id']||'').replace('xsd:','')
  return ['number','integer'].includes(String(d.conversion||''))&&!['double','decimal','integer'].includes(t)
})
// 字段已删检查：草稿引用的字段在当前表结构目录中不存在时在表单中提示（目录未加载不误报）
const ghostFields=computed(()=>{
  const d:any=draft.value
  if(!d)return [] as string[]
  const out:string[]=[]
  const check=(table:any,field:string,what:string)=>{if(!table||!field)return;if(!fieldIn(table,field))out.push(what+'「'+field+'」不在当前表结构目录中，可能已被删除，请重新选择')}
  if(d.kind==='database'){
    const t=directCatalog.value
    check(t,d.result?.valueField,'值字段')
    if(needTimestamp.value)check(t,d.result?.timestampField,'时间字段')
    if(d.result?.duplicateTimestamp==='secondarySort')check(t,d.result?.secondarySortField,'次级排序字段')
    if(d.lookup?.timeRange)check(t,d.lookup.timeRange.field,'时间范围字段')
    for(const m of (d.lookup?.match||[]))check(t,m.field,'匹配条件字段')
  }else if(d.kind==='field'){
    const t=draftFieldCatalog.value
    check(t,d.field,'取值字段')
    if(d.selection==='latest')check(t,d.timeField,'排序时间字段')
  }
  return out
})
// ---------- 表单字段分组校验：保存时统一检查全部必填 ----------
function formSectionIssues(n:number):string[]{
  const d:any=draft.value
  if(!d||d.kind==='unknown'||d.kind==='none')return []
  const out:string[]=[]
  if(n===1){
    if(d.kind==='field'||d.kind==='database'){
      if(!String(d.connection||'').trim())out.push('请选择数据连接')
      else if(!String(d.table||'').trim())out.push('请选择来源表')
    }else if(d.kind==='redis'){
      if(draftShape.value==='timeSeries')out.push('普通 Redis GET／HGET 标量读取不能返回时间序列，请选择数据库取值或兼容的函数输出')
      if(!d.connection&&!d.source)out.push('请选择 Redis 连接或已登记 Redis 来源')
    }else if(d.kind==='computed'&&d.mode!=='inline'&&!d.implementation)out.push('请选择一个已有项目实现')
    else if(d.kind==='computed'&&d.mode==='inline'&&!String(d.inline?.connection||'').trim())out.push('请选择数据连接')
    return out
  }
  if(n===2){
    if(d.kind==='database'&&d.table){
      const identityRow=directIdentityRow.value
      for(const m of (d.lookup?.match||[])){
        if(!String(m.field||'').trim()){out.push('匹配条件未选择来源字段');continue}
        const v=m.value
        if(!v||typeof v!=='object')out.push('匹配条件「'+m.field+'」的比较值来源无效')
        else if(v.kind==='identityField'&&!String(v.field||'').trim())out.push('匹配条件「'+m.field+'」未选择身份表字段')
        else if(v.kind==='property'&&!String(v.property||'').trim())out.push('匹配条件「'+m.field+'」未选择引用属性')
        else if(v.kind==='constant'&&!String(v.value??'').trim())out.push('匹配条件「'+m.field+'」的常量值不能为空')
        else if(v.kind==='parameter'&&!String(v.parameter||'').trim())out.push('匹配条件「'+m.field+'」未选择项目参数')
      }
      if(!identityRow&&!(d.lookup?.match||[]).some((m:any)=>m?.value&&(m.value.kind==='identityKey'||m.value.kind==='identityField')))
        out.push('匹配条件至少需要一条绑定当前实例（当前实例主键或身份表字段）')
      if(draftShape.value==='timeSeries'&&d.lookup?.timeRange&&!String(d.lookup.timeRange.field||'').trim())out.push('时间范围未选择时间字段')
    }
    if(d.kind==='redis'){
      if(!String(d.key||'').trim())out.push('请填写 Key 模板')
      else for(const t of keyTokens(String(d.key)))if(!d.params?.[t])out.push('Key 模板占位符「{'+t+'}」未绑定参数')
      if(d.command==='HGET'&&!String(d.hashField||'').trim())out.push('请填写 Hash 字段')
    }
    return out
  }
  if(d.kind==='field'){
    if(!String(d.field||'').trim())out.push('请选择取值字段')
    if(draftCardinality.value==='many'&&d.selection==='latest'&&!String(d.timeField||'').trim())out.push('按时间取最新必须指定排序时间字段')
  }else if(d.kind==='database'){
    const r=d.result||{}
    if(!String(r.valueField||'').trim())out.push('请选择值字段')
    if(draftShape.value==='timeSeries'){
      if(!String(r.timestampField||'').trim())out.push('时间序列属性必须选择时间字段')
      if(!r.timestampEncoding)out.push('时间序列属性必须选择时间戳编码')
      if(r.timestampEncoding==='datetime'&&!String(r.timezone||'').trim())out.push('日期时间编码必须填写时区')
      if(r.duplicateTimestamp==='secondarySort'&&!String(r.secondarySortField||'').trim())out.push('重复时刻按次级排序取一必须选择排序字段')
    }else if(r.selection==='latest'){
      if(!String(r.timestampField||'').trim())out.push('按时间取最新必须指定时间字段')
      if(!r.timestampEncoding)out.push('按时间取最新必须选择时间戳编码')
    }
  }else if(d.kind==='computed'&&d.mode!=='inline'&&!String(d.output||'').trim())out.push('请选择该实现的具体输出')
  return out
}
// ---------- 目录刷新：捕获当时连接，await 后连接已变则放弃本次结果 ----------
const refreshing=ref(''),refreshMessage=ref('')
const draftConn=computed(()=>{const d:any=draft.value;if(!d)return '';if(d.kind==='database'||d.kind==='field')return String(d.connection||'');return ''})
async function refresh(){
  const c=draftConn.value;if(!c||refreshing.value)return
  refreshing.value=c;refreshMessage.value=''
  const out=await refreshCatalogOf(props.projectState,c,(fn:()=>void)=>{if(draftConn.value===c)mutate(fn)})
  refreshing.value=''
  if(draftConn.value!==c)return // 刷新期间已切换连接：放弃本次结果，不写入也不提示
  refreshMessage.value=out.ok?'目录已刷新：'+out.message:'目录未刷新（'+out.message+'）；已保留旧配置'
}
// ---------- 摘要与保存直通 ----------
const advancedNote=computed(()=>{
  const d:any=draft.value;if(!d||d.kind==='none'||d.kind==='unknown')return ''
  if(d.kind==='redis')return 'Key 未找到或转换失败时按「未找到或转换失败」策略处理，缺失不静默变为 0。'
  if(d.kind==='computed')return d.mode==='inline'?'内联 SQL 只做配置校验（连接、单条 SELECT、参数绑定）；配置检查通过不代表 SQL 验证成功，模板不会被执行。':'函数输出仅做结构与形态校验，「配置校验通过」不代表已执行验证。'
  return draftShape.value==='timeSeries'
    ?'时间序列：无记录=空列表；观测值缺失保留 null（不补零）；无效时标或转换失败报错。'
    :'单值：无记录=null；多条记录且未配置多行处理规则时报错。'
})
// 保存直通：一次校验全部 → form-save 提交（mutate 内 commitProperty 编码）；
// 成功关闭表单返回清单；失败（校验或持久化）不关表单、保留输入，可重试或取消。
const saving=ref(false)
function resetSaveState(){saving.value=false}
async function saveDraft(){
  const d:any=draft.value
  if(!d||d.kind==='unknown'||saving.value)return
  if(d.kind!=='none'){
    const issues=[...new Set([...formSectionIssues(1),...formSectionIssues(2),...formSectionIssues(3),...issuesOf(selectedApi.value,selectedProp.value,d),...ghostFields.value])]
    if(issues.length){editError.value='保存前请先处理：'+issues.join('；');return}
  }
  editError.value=''
  saving.value=true
  // 计算取值提交视图：内联 SQL 只落 mode=inlineSql 一种生效结构（params 按扫描裁剪，
  // 移除的参数不进生效配置）；规则方式保持原 computed 结构，不写 mode。
  const commitView=(dv:any)=>dv.kind==='computed'&&dv.mode==='inline'
    ?{kind:'computed',mode:'inlineSql',inlineSql:{version:1,connection:String(dv.inline?.connection||''),sql:String(dv.inline?.sql||''),params:effectiveParams(dv.inline?.params||{},String(dv.inline?.sql||''))}}
    :dv
  const apply=()=>{commitProperty(props.b,selectedApi.value,d.kind==='none'?null:commitView(d))}
  let r:{ok:boolean;message:string}
  if(formSave)r=await formSave.submitForm('project',apply)
  else{mutate(apply);r={ok:true,message:''}}
  saving.value=false
  if(r.ok)closeEditor()
  else editError.value='保存未完成：'+r.message+'。表单内容已保留，可重试保存或取消。'
}
// 跳「计算实现」维护实现：草稿入模块缓存后静默关闭（不经离开确认），返回本页签自动恢复。
function goImplements(){
  if(draft.value&&selectedApi.value)psStash(props.b.object_type,selectedApi.value,draft.value,baseline.value)
  closeEditor()
  location.hash='#implements'
}
// 返回恢复：挂载时检查模块缓存中是否有本对象的待恢复表单草稿。
if(psPendingOf(props.b.object_type)){
  const st=psConsume(props.b.object_type)
  if(st&&properties.value.some((p:any)=>key(p)===st.api)){
    selectedApi.value=st.api
    draft.value=st.draft
    baseline.value=st.baseline
    switchMsg.value='已恢复未完成的取值配置草稿。'
    editError.value='';refreshMessage.value=''
    configuring.value=true
  }
}
</script>
<template><div>
<!-- ========== 列表态（默认）：属性清单 ========== -->
<div v-if="!configuring">
<p class="ps-lede">每个属性配置一种生效来源</p>
<p class="fill-hint">支持读取数据源或调用函数。点「配置／修改」在同一页完成填写并保存。列表状态仅代表配置校验，未执行取值验证。</p>
<div class="scroll"><table class="source-table ps-table">
<thead><tr><th style="width:32%">属性</th><th style="width:37%">来源</th><th style="width:19%">状态</th><th style="width:12%">操作</th></tr></thead>
<tbody>
<template v-for="r in rows" :key="r.api">
<tr>
<td class="prop-name">{{label(r.p)}}<small class="muted">{{propSmall(r)}}</small></td>
<td><span :class="{'muted':!r.view}">{{listSummary(r.view)}}</span></td>
<td><span class="status-pill" :class="r.status.cls" :title="r.status.title">{{r.status.text}}</span></td>
<td><button @click="openProperty(r.api)">{{!r.view?'配置':(r.view.kind==='unknown'?'查看':'修改')}}</button></td>
</tr>
</template>
</tbody>
</table></div>
<p class="ps-list-foot">配置将保存在当前项目中，本体中的属性定义保持独立。</p>
</div>
<!-- ========== 配置态：单页表单，主内容整体替换 ========== -->
<div v-else>
<div class="ps-config-top">
<button class="ps-back" @click="requestClose">← 返回属性清单</button>
<span class="ps-object-tag">当前对象 · {{objectLabel}}</span>
</div>
<div v-if="draft&&selectedMeta" class="ps-config-head">
<div class="ps-config-title">
<h2>配置 · {{selectedMeta.label}}</h2>
<p class="ps-def">{{selectedMeta.comment||'（本体中未填写业务定义）'}}</p>
</div>
<span v-if="headBadge" class="ps-badge">{{headBadge}}</span>
</div>
<!-- 未知结构：只读警示分支，原样保留不进入表单 -->
<div v-if="draft&&draft.kind==='unknown'" class="ps-form-panel">
<p class="inline-warning">该属性保存了当前版本未识别的来源结构，已原样保留且不可编辑；如需重新配置请先导出备份。</p>
<div class="tools"><button @click="closeEditor">保留并关闭</button></div>
</div>
<template v-else-if="draft">
<fieldset class="ps-form-fields" :disabled="saving">
<div class="ps-form-panel">
<h3 class="ps-question">从哪里获取{{selectedMeta?.label}}？</h3>
<div class="ps-cards">
<button v-for="c in sourceCards" :key="c.value" type="button" class="ps-card" :class="{selected:topKind===c.value}" @click="pickCardRaw(c.value)">
<strong>{{c.title}}</strong>
<small>{{c.desc}}</small>
</button>
</div>
<p class="field-help">一个属性只配置一种取值策略；切换只影响当前草稿，保存前不改动已保存配置。</p>
<p v-if="topKind" class="ps-clear"><button type="button" class="ps-clear-btn" @click="switchKind('')">清除该属性的来源配置</button><small class="field-help">保存后该属性恢复为未配置。</small></p>
<p v-else class="field-help">尚未选择来源；保存将清除该属性的来源配置。</p>
<div v-if="topKind==='data'" class="row">
<label>数据连接 *<AppSelect :model-value="dataConnection" aria-label="数据连接" searchable :options="dataConnectionOptions" @update:model-value="dataConnectionChanged($event)"/><small class="field-help">根据连接类型显示对应配置；切换连接会清空原取值配置。</small></label>
<label v-if="draft.kind==='field'||draft.kind==='database'">表／视图 *<AppSelect :key="draft.connection" :model-value="draft.table||''" aria-label="来源表或视图" searchable :disabled="!draft.connection" :options="[{value:'',label:'请选择表／视图'},...tableOptions(projectState,draft.connection)]" @update:model-value="dbTableChanged($event)"/><small class="field-help">可选连接目录中的任何表或视图，无需先登记为对象来源，也不改变链接映射的选表范围。</small></label>
</div>
<template v-if="draft.kind==='field'||draft.kind==='database'">
<div v-if="b.connection&&b.table&&(draft.connection!==b.connection||draft.table!==b.table)" class="tools"><button @click="useIdentityTable">使用已配置的对象身份表</button></div>
<p v-if="draftShape==='timeSeries'" class="inline-warning">时间序列属性：Redis 普通读取与实例来源普通字段映射不能作为序列来源，请在数据库表中选值字段＋时标字段，或选择声明序列输出的函数结果。</p>
<p v-if="draft.connection&&!catalogOf(projectState,draft.connection)" class="inline-warning">该连接的表结构目录尚未读取，无法选择表与字段。<button :disabled="!!refreshing" @click="refresh">{{refreshing?'读取中…':'刷新表结构'}}</button></p>
<p v-if="refreshMessage" class="inline-warning" role="status">{{refreshMessage}}</p>
<p v-if="switchMsg" class="inline-warning" role="status">{{switchMsg}}</p>
</template>
<template v-else-if="draft.kind==='redis'">
<div class="row">
<label>读取方式<AppSelect :model-value="draft.command||'GET'" aria-label="读取方式" :options="[{value:'GET',label:'GET'},{value:'HGET',label:'HGET'}]" @update:model-value="draft.command=$event"/></label>
</div>
<p v-if="draftShape==='timeSeries'" class="inline-error">普通 GET／HGET 标量不能作为时间序列，请选择数据库取值或兼容的函数输出。</p>
</template>
<template v-else-if="draft.kind==='computed'">
<div class="row">
<label>取值方式 *<AppSelect :model-value="draft.mode" aria-label="取值方式" :options="[{value:'inline',label:'直接编写 SQL'},{value:'rule',label:'引用已有规则'},{value:'function',label:'计算函数'}]" @update:model-value="draft.mode=$event"/><small class="field-help">直接 SQL 是当前属性的匿名取值配置，不起规则名、不进规则库；复杂、可复用、多步骤查询请引用已有规则。切换只改本地草稿，两边未保存内容各自保留。</small></label>
</div>
<template v-if="draft.mode==='rule'">
<div class="row">
<label>项目取值规则 / 实现 *<AppSelect :model-value="draft.implementation||''" aria-label="项目实现" searchable :options="[{value:'',label:'请选择已有实现'},...implOptions]" @update:model-value="selectImplementation($event)"/><small class="field-help">引用已存在的项目实现；规则在「取值规则库」页维护，在下方选择具体输出。</small></label>
</div>
<div class="tools"><button @click="goImplements">前往取值规则库 →</button></div>
<p v-if="!implOptions.length" class="field-help">当前项目还没有取值规则或计算实现；可先去维护，返回后草稿保留。</p>
<p v-else class="field-help">无可用实现时可先去维护；离开前草稿会保留，返回本页签自动恢复。</p>
</template>
<template v-else-if="draft.mode==='inline'">
<div class="row">
<label>数据连接 *<AppSelect :model-value="draft.inline?.connection||''" aria-label="内联 SQL 数据连接" searchable :options="[{value:'',label:'请选择数据连接'},...mysqlConnectionsOf(projectState).map(c=>({value:c.value,label:c.label}))]" @update:model-value="draft.inline&&(draft.inline.connection=$event)"/></label>
</div>
<label>SQL 模板 *<textarea v-model="draft.inline.sql" class="inline-sql-editor" aria-label="内联 SQL 模板" spellcheck="false" rows="8" placeholder="SELECT SUM(rated_power) AS value&#10;FROM m_storage_phase&#10;WHERE park_id_column = :park_id;"></textarea>
<small class="field-help">单条 SELECT 模板，JOIN、聚合、表达式、子查询可写在 SELECT 内；用 <code>:参数名</code> 占位并在下方绑定。不支持 WITH、多语句、动态表名／字段名与步骤引用——需要时请改用「引用已有规则」。</small></label>
<div v-if="inlineParams.length">
<p class="field-help">识别到 {{inlineParams.length}} 个参数（字符串、注释内的冒号不参与；重复占位只显示一行）。SQL 改动后仍存在的绑定会保留，新增参数显示待绑定。</p>
<div v-for="name in inlineParams" :key="name" class="row">
<label>参数 {{ '{'+name+'}' }} 来源<AppSelect :model-value="inlineBindingKind(name)" :aria-label="'参数 '+name+' 来源'" :options="inlineBindingOptions" @update:model-value="setInlineBinding(name,$event)"/></label>
<template v-if="inlineBindingKind(name)==='constant'">
<label>数据类型<AppSelect :model-value="inlineBindingOf(name)?.dataType||'string'" :aria-label="'参数 '+name+' 常量类型'" :options="constTypes" @update:model-value="inlineBindingOf(name).dataType=$event"/></label>
<label>常量值 *<input :value="inlineBindingOf(name)?.value??''" :aria-label="'参数 '+name+' 常量值'" @input="inlineBindingOf(name).value=($event.target as HTMLInputElement).value"><small class="field-help">明确填写的 0、false、空字符串都是有效值。</small></label>
</template>
<label v-else-if="inlineBindingKind(name)==='projectParameter'">项目参数 *<AppSelect :model-value="inlineBindingOf(name)?.key||''" :aria-label="'参数 '+name+' 项目参数'" searchable :options="[{value:'',label:'请选择项目参数'},...parameterOptions]" @update:model-value="inlineBindingOf(name).key=$event"/></label>
<span v-else-if="inlineBindingKind(name)==='instanceId'" class="muted">绑定{{instanceIdLabel}}，按现有身份机制解析。</span>
</div>
</div>
<p v-else class="field-help">SQL 中没有 :参数 占位，无需参数绑定。</p>
<p class="field-help"><strong>输出约定（只读，继承属性类型）</strong>：{{inlineOutputNote}}</p>
</template>
<template v-else>
<div class="row">
<label>计算函数 *<AppSelect :model-value="isCalcFunction(fnOf(draft.implementation))?draft.implementation:''" aria-label="计算函数选择" searchable :options="[{value:'',label:calcFnOptions.length?'请选择计算函数':'还没有计算函数'},...calcFnOptions]" @update:model-value="selectImplementation($event)"/><small class="field-help">计算函数在「取值规则库」用「＋ 新建计算函数」维护。</small></label>
</div>
<p v-if="draft.implementation&&!isCalcFunction(fnOf(draft.implementation))" class="inline-warning">所选实现不是计算函数；请重新选择。</p>
<p v-else-if="!draft.implementation" class="field-help">还没有可用的计算函数时，点「前往取值规则库」用「＋ 新建计算函数」创建；离开前草稿会保留。</p>
<template v-if="isCalcFunction(fnOf(draft.implementation))">
<p class="ps-info">计算函数「{{fnOf(draft.implementation)?.name}}」：绑定每个输入后保存。输出「{{fnOf(draft.implementation)?.output?.name}}」自动选用；属性、函数、输入与输出引用都使用稳定标识。<button type="button" class="ps-inline-link" @click="goImplements">前往取值规则库 →</button></p>
<div v-for="p in fnInputs" :key="p.id" class="row">
<label>输入 {{p.name}}（{{CALC_TYPE_NAMES[p.type]}}）<AppSelect :model-value="fnBindingOf(p.id)?.from||''" :aria-label="'函数输入 '+p.name+' 来源'" :options="[{value:'',label:'待绑定（请选择来源）'},{value:'property',label:'当前对象属性'},{value:'constant',label:'固定值'}]" @update:model-value="setFnBinding(p.id,$event)"/></label>
<label v-if="fnBindingOf(p.id)?.from==='property'">属性<AppSelect :model-value="fnBindingOf(p.id)?.property||''" :aria-label="'函数输入 '+p.name+' 属性'" searchable :options="[{value:'',label:'请选择当前对象属性'},...fnPropertyOptions(p.type)]" @update:model-value="fnBindingOf(p.id).property=$event"/></label>
<label v-else-if="fnBindingOf(p.id)?.from==='constant'">固定值<input :value="fnBindingOf(p.id)?.value??''" :aria-label="'函数输入 '+p.name+' 固定值'" :placeholder="p.type==='number'?'例如 100（0 是有效值）':(p.type==='boolean'?'true / false':'文本，空字符串也是有效值')" @input="fnBindingOf(p.id).value=($event.target as HTMLInputElement).value"></label>
</div>
<p class="field-help">输入只能引用当前对象的属性（含本函数之外的取值配置）或填写固定值；存在直接或间接循环依赖时无法通过保存校验。0、false、明确的空字符串是有效固定值。</p>
<p v-if="ruleValueTypeMismatch" class="inline-error">计算函数输出类型与属性数据类型不匹配，请选择兼容的函数或属性。</p>
</template>
</template>
</template>
<!-- ===== 登记信息（registered）===== -->
<template v-else-if="draft.kind==='registered'">
<div class="row">
<label>登记信息字段 *<AppSelect :model-value="draft.field" aria-label="登记信息字段" :options="[{value:'id',label:'实例编号'},{value:'label',label:'显示名称'}]" @update:model-value="draft.field=$event"/></label>
</div>
<p class="ps-info">取值来自本对象在「实例识别」页登记的实例信息，不访问数据库。实例编号身份稳定；显示名称为管理标签。目标属性应为文本类型。</p>
<p v-if="!['xsd:string'].includes(String(effectiveProperty(selectedProp,graph)?.['rdfs:range']?.['@id']||''))" class="inline-error">登记信息仅支持文本属性；当前属性类型不匹配。</p>
</template>
<!-- 历史聚合配置只读兼容，不再提供创建和编辑入口。 -->
<template v-else-if="draft.kind==='aggregate'">
<p class="inline-warning">此属性保留了历史聚合配置，当前仅供查看。请切换到「调用取值规则 / 函数」，使用 SQL 求和规则配置新的来源。</p>
<p class="field-help">{{aggregateSummary(draft)}}。切换后点击保存才替换原配置。</p>
</template>
<!-- 按来源展开定位条件；身份表无需额外填写。 -->
<template v-if="(draft.kind==='field'&&draft.table)||(draft.kind==='database'&&draft.table)||draft.kind==='redis'||(draft.kind==='computed'&&draft.mode!=='inline'&&draft.mode!=='function'&&draft.implementation)">
<template v-if="draft.kind==='none'">
<p class="field-help">未选择来源，无需定位记录。</p>
</template>
<template v-else-if="draft.kind==='field'">
<template v-if="draft.mode==='identity'">
<p class="ps-info">已按实例主键 <strong>{{b.primary_key||'未配置'}}</strong> 自动定位身份表当前记录。选择下方取值字段即可。</p>
</template>
<template v-else>
<p class="ps-info">记录定位由已登记来源「{{draftSource?.name||'尚未选择来源'}}」登记时的匹配配置决定（在「实例识别」页维护）：{{b.table||'实例来源表'}}.{{draftSource?.matchLeft||'匹配字段'}} ＝ {{draftSource?.table||draft.table||'本来源表'}}.{{draftSource?.matchRight||'匹配字段'}}。可直接选择下方取值字段。</p>
</template>
</template>
<template v-else-if="draft.kind==='database'">
<p v-if="directIdentityRow" class="ps-info">当前选择的是实例来源表：取值时按实例主键 <strong>{{b.primary_key||'未配置'}}</strong> 自动定位，无需配置匹配条件。</p>
<details v-if="draft.table" :open="!directIdentityRow||!!draft.lookup.match.length" class="ps-matching">
<summary>{{directIdentityRow?'额外筛选条件（选填）':'匹配当前对象的记录 *'}}</summary>
<p class="field-help"><strong>匹配条件</strong>：等值条件 AND 组合；至少一条绑定当前实例（身份表本行除外）。不提供手输表名或 SQL。</p>
<div v-for="(m,i) in (draft.lookup.match as any[])" :key="i">
<div class="row">
<label>来源字段<AppSelect :model-value="m.field" :aria-label="'条件'+(i+1)+'来源字段'" searchable :disabled="!directCatalog" :options="[{value:'',label:'请选择字段'},...directFieldOptions]" @update:model-value="m.field=$event"/></label>
<label>操作符<span class="muted">等于（首期仅支持等值匹配）</span></label>
<label>比较值来源<AppSelect :model-value="m.value.kind" :aria-label="'条件'+(i+1)+'比较值来源'" :options="matchKindOptions" @update:model-value="setMatchKind(m,$event)"/></label>
<button @click="removeMatch(i)">删除</button>
</div>
<div class="row" v-if="m.value.kind==='identityField'">
<label>身份表字段 *<AppSelect :model-value="m.value.field||''" :aria-label="'条件'+(i+1)+'身份表字段'" searchable :options="[{value:'',label:'请选择字段'},...identityFieldOptions]" @update:model-value="m.value.field=$event"/><small v-if="!identityTableOf(b).connection" class="field-help">实例来源尚未配置数据连接。</small></label>
</div>
<div class="row" v-else-if="m.value.kind==='property'">
<label>引用属性 *<AppSelect :model-value="m.value.property||''" :aria-label="'条件'+(i+1)+'引用属性'" :options="[{value:'',label:'请选择已映射的单值属性'},...scalarPropertyOptions]" @update:model-value="m.value.property=$event"/><small v-if="!scalarPropertyOptions.length" class="field-help">本对象还没有可引用的已映射单值属性。</small></label>
</div>
<div class="row" v-else-if="m.value.kind==='constant'">
<label>常量值 *<input :value="m.value.value" :placeholder="constantPlaceholder(m.field)" @input="m.value.value=($event.target as HTMLInputElement).value"><small class="field-help">按目录字段类型填写，不是自由 SQL；例如对象类型或指标代码列的固定取值。</small></label>
</div>
<div class="row" v-else-if="m.value.kind==='parameter'">
<label>项目参数 *<AppSelect :model-value="m.value.parameter||''" :aria-label="'条件'+(i+1)+'项目参数'" :options="[{value:'',label:'请选择参数'},...parameterOptions]" @update:model-value="m.value.parameter=$event"/></label>
</div>
<p v-if="dupMatch(i)" class="inline-error">重复条件：来源字段「{{m.field}}」出现在多个条件中。</p>
</div>
<div class="tools"><button @click="addMatch">＋ 添加筛选条件</button></div>
</details>
<div class="param-card" v-if="draftShape==='timeSeries'">
<div class="panelhead"><strong>查询时间范围（约定）</strong><button v-if="!draft.lookup.timeRange" @click="addTimeRange">添加时间范围</button></div>
<p class="field-help">查询时间范围由调用上下文提供：startTime ≤ t &lt; endTime（左闭右开）。配置只保存参数角色，不写死具体日期；时间字段从目录选择。</p>
<label v-if="draft.lookup.timeRange">时间字段 *<AppSelect :model-value="draft.lookup.timeRange.field" aria-label="时间范围字段" searchable :disabled="!directCatalog" :options="[{value:'',label:'请选择字段'},...directFieldOptions]" @update:model-value="draft.lookup.timeRange.field=$event"/></label>
</div>

</template>
<template v-else-if="draft.kind==='redis'">
<div class="row">
<label>Key 模板 *<input v-model="draft.key" placeholder="例如 m_storage_cluster-{id}-soc"><small class="field-help">全体实例共用的模板，占位符写成 {参数名}。</small></label>
<label v-if="draft.command==='HGET'">Hash 字段 *<input v-model="draft.hashField" placeholder="例如 soc"></label>
</div>
<p class="field-help"><strong>参数绑定</strong>：每个占位符绑定到实例主键、身份表字段或已映射为数据库字段的属性；保存前需要绑定每个占位符。</p>
<div class="row" v-for="t in keyTokens(String(draft.key||''))" :key="t">
<label>参数 {{ '{'+t+'}' }} ←<AppSelect :model-value="paramModel(t)" :aria-label="'参数 '+t+' 绑定'" :options="paramOptions" @update:model-value="setParam(t,$event)"/><p v-if="!paramModel(t)" class="inline-error">此占位符尚未绑定参数。</p></label>
</div>
<p v-if="!keyTokens(String(draft.key||'')).length" class="field-help">Key 模板中没有占位符，无需参数绑定。</p>
<div class="row"><label>示例实例主键<input v-model="samplePrimary" placeholder="例如 123"></label></div>
<p class="field-help">预览 Key（仅展开模板，不读取 Redis）：{{previewKey?draft.command+' '+previewKey:'填写 Key 模板后显示预览'}}</p>
</template>
<template v-else-if="draft.kind==='computed'">
<template v-if="reusableInput">
<h3 class="ps-sub">规则输入</h3><p class="field-help">这些输入只属于当前属性绑定。同一条规则可以供其他属性使用，各自传入不同的表名与属性名。</p>
<template v-if="declaredInputs">
<div class="form-grid"><label v-for="p in declaredInputs.binding" :key="p.name">{{p.name}} · {{p.description||'输入参数'}} *<input :aria-label="'规则输入 '+p.name" :value="draft.inputs?.[p.name]??''" @input="setRuleInput(p.name,($event.target as HTMLInputElement).value)" placeholder="固定值，或 {id} 引用当前实例主键"></label></div>
<p v-if="declaredInputs.runtime.length" class="field-help">查询时传入（无需在此填写）：{{declaredInputs.runtime.map((p:any)=>p.name+(p.description?'（'+p.description+'）':'')).join('、')}}。</p>
</template>
<div v-else class="form-grid"><label>model_name · 表名 *<input aria-label="规则输入 model_name" :value="draft.inputs?.model_name||''" @input="setRuleInput('model_name',($event.target as HTMLInputElement).value)" placeholder="m_storage_cluster_phase"></label>
<label>attr_name · 属性名 *<input aria-label="规则输入 attr_name" :value="draft.inputs?.attr_name||''" @input="setRuleInput('attr_name',($event.target as HTMLInputElement).value)" placeholder="soc"></label>
<label>model_id · 主键模板 *<input aria-label="规则输入 model_id" :value="draft.inputs?.model_id||''" @input="setRuleInput('model_id',($event.target as HTMLInputElement).value)" placeholder="{id}"><small class="field-help">{id} 使用当前实例主键 {{b.primary_key||'（尚未配置）'}}。</small></label></div>
<p v-if="!declaredInputs" class="field-help">开始、结束时间由查询时传入。</p>
</template><template v-else>
<h3 class="ps-sub">输入按项目实现绑定（只读）</h3>
<div v-if="implInputRows.length" class="ps-impl-inputs">
<div v-for="row in implInputRows" :key="row.name" class="ps-impl-row">
<div><strong>{{row.name}}</strong><small class="muted">{{row.ref}}</small></div>
<span class="ps-impl-binding">{{row.binding}}</span>
</div>
</div>
<p v-else class="field-help">所选规则或实现未声明输入。</p>
<p class="field-help">调用时传入对象引用，实现负责解析对应的主键或资产编码；输入绑定需要在「取值规则库」页修改。<button type="button" class="ps-inline-link" @click="goImplements">前往取值规则库 →</button>离开前草稿会保留，返回本页签自动恢复。</p>
</template>
<p v-if="computedShapeMismatch" class="inline-error">函数输出数据类型与属性要求不匹配，请选择兼容的实现与输出。</p>
</template>
</template>
<!-- 按目标类型展开结果字段，和来源在同一页保存。 -->
<template v-if="(draft.kind==='field'&&draft.table)||(draft.kind==='database'&&draft.table)||draft.kind==='redis'||(draft.kind==='computed'&&draft.mode!=='inline'&&draft.mode!=='function'&&draft.implementation)">
<template v-if="draft.kind==='none'"><p class="field-help">未选择来源，无需结果映射；保存将清除该属性的来源配置。</p></template>
<template v-else-if="draft.kind==='field'">
<div class="row">
<label>取值字段 *<AppSelect :model-value="draft.field||''" aria-label="取值字段" searchable :disabled="!draftFieldCatalog" :options="[{value:'',label:'请选择字段'},...fieldOptions(draftFieldCatalog)]" @update:model-value="draft.field=$event"/></label>

</div>
<details v-if="draftCardinality!=='many'" class="ps-advanced"><summary>无匹配记录时（选填）</summary>
<label>处理方式<AppSelect :model-value="draft.missing||'null'" aria-label="无匹配记录时" :options="[{value:'null',label:'返回无数据（默认）'},{value:'error',label:'返回错误'}]" @update:model-value="draft.missing=$event"/></label>
</details>
<p v-if="draft.connection&&!draftFieldCatalog" class="inline-warning">该来源的表结构目录尚未读取，无法选择字段。<button :disabled="!!refreshing" @click="refresh">{{refreshing?'读取中…':'刷新表结构'}}</button></p>
<p v-if="refreshMessage" class="inline-warning" role="status">{{refreshMessage}}</p>
<div class="row" v-if="draftCardinality==='many'">
<label>多条记录如何得到一个值 *<AppSelect :model-value="draft.selection||''" aria-label="多条记录如何得到一个值" :options="[{value:'',label:'预期零或一条，多行报错'},{value:'latest',label:'按时间取最新'},{value:'sum',label:'求和'},{value:'average',label:'求平均'}]" @update:model-value="draft.selection=$event;draft.timeField='';draft.tieBreaker=''"/></label>
<label>无匹配记录时<AppSelect :model-value="draft.missing||'null'" aria-label="无匹配记录时" :options="[{value:'null',label:'返回无数据'},{value:'error',label:'返回错误'}]" @update:model-value="draft.missing=$event"/></label>
</div>
<div class="row" v-if="draftCardinality==='many'&&draft.selection==='latest'">
<label>排序时间字段 *<AppSelect :model-value="draft.timeField||''" aria-label="排序时间字段" searchable :disabled="!draftFieldCatalog" :options="[{value:'',label:'请选择字段'},...fieldOptions(draftFieldCatalog)]" @update:model-value="draft.timeField=$event"/></label>
<label>次级排序字段<small class="field-help">时间相同的并列记录按此字段定序，应业务唯一。</small><AppSelect :model-value="draft.tieBreaker||''" aria-label="次级排序字段" searchable :disabled="!draftFieldCatalog" :options="[{value:'',label:'不设置'},...fieldOptions(draftFieldCatalog)]" @update:model-value="draft.tieBreaker=$event"/></label>
</div>
<p v-if="draftCardinality==='many'&&draft.selection&&draft.selection!=='latest'" class="field-help">汇总只适用于业务口径允许相加或平均的数值属性。</p>
<p class="field-help">取值路径：{{draft.mode==='registered'?(draftSource?.table||draftSource?.name||'未选表'):(b.table||'实例来源')}}.{{draft.field||'字段'}}</p>
</template>
<template v-else-if="draft.kind==='database'">
<div class="row">
<label>值字段 *<AppSelect :model-value="draft.result.valueField||''" aria-label="结果值字段" searchable :disabled="!directCatalog" :options="[{value:'',label:'请选择字段'},...directFieldOptions]" @update:model-value="draft.result.valueField=$event"/></label>
<label v-if="draftShape==='timeSeries'">时间字段 *<AppSelect :model-value="draft.result.timestampField||''" aria-label="结果时间字段" searchable :disabled="!directCatalog" :options="[{value:'',label:'请选择字段'},...directFieldOptions]" @update:model-value="draft.result.timestampField=$event"/></label>
</div>
<template v-if="needTimestamp">
<div class="row">
<label>时间戳编码 *<AppSelect :model-value="draft.result.timestampEncoding||''" aria-label="时间戳编码" :options="[{value:'',label:'请选择'},...TIMESTAMP_ENCODINGS]" @update:model-value="setEncoding($event)"/></label>
<label v-if="draft.result.timestampEncoding==='datetime'">时区 *<input :value="draft.result.timezone" placeholder="例如 Asia/Shanghai" @input="draft.result.timezone=($event.target as HTMLInputElement).value"><small class="field-help">IANA 时区名；默认取项目参数 timezone{{projectState.parameters?.timezone?'（'+projectState.parameters.timezone+'）':''}}。</small></label>
</div>
</template>
<template v-if="draftShape==='timeSeries'">
<div class="row">
<label>排序<AppSelect :model-value="draft.result.order||'ascending'" aria-label="序列排序" :options="[{value:'ascending',label:'按时间升序（默认）'},{value:'descending',label:'按时间降序'}]" @update:model-value="draft.result.order=$event"/></label>
<label>重复时刻处理<AppSelect :model-value="draft.result.duplicateTimestamp||'error'" aria-label="重复时刻处理" :options="[{value:'error',label:'报数据冲突（默认）'},{value:'secondarySort',label:'按次级排序取舍'}]" @update:model-value="draft.result.duplicateTimestamp=$event;draft.result.secondarySortField=''"/></label>
</div>
<div class="row" v-if="draft.result.duplicateTimestamp==='secondarySort'">
<label>次级排序字段 *<AppSelect :model-value="draft.result.secondarySortField||''" aria-label="次级排序字段" searchable :disabled="!directCatalog" :options="[{value:'',label:'请选择字段'},...directFieldOptions]" @update:model-value="draft.result.secondarySortField=$event"/></label>
<label>次级排序方向<AppSelect :model-value="draft.result.secondarySortOrder||'ascending'" aria-label="次级排序方向" :options="[{value:'ascending',label:'升序'},{value:'descending',label:'降序'}]" @update:model-value="draft.result.secondarySortOrder=$event"/></label>
</div>
<p class="field-help">时间序列返回全部匹配观测点，不强制聚合；无记录=空列表，观测值缺失保留 null。</p>
<div class="param-card ps-range-card">
<strong>查询时间范围（只读说明）</strong>
<p class="field-help">调用时传入 startTime、endTime，返回按时间升序的 [时间, 值] 观测点；时间范围条件在上方「查询时间范围」中配置，只保存参数角色，不写死具体日期。</p>
</div>
</template>
<template v-else>
<details class="ps-advanced" :open="!directIdentityRow||!!draft.result.selection||draft.result.missing==='error'"><summary>多行与缺失处理（选填）</summary>
<div class="row">
<label>多行处理<AppSelect :model-value="draft.result.selection||''" aria-label="多行处理" :options="[{value:'',label:'预期零或一条，多行报错（默认）'},{value:'latest',label:'按时间取最新'},{value:'sum',label:'求和',disabled:!numericProperty},{value:'average',label:'求平均',disabled:!numericProperty}]" @update:model-value="setSelection($event)"/></label>
<label>无匹配记录时<AppSelect :model-value="draft.result.missing||'null'" aria-label="无匹配记录时" :options="[{value:'null',label:'结果为空（null，默认）'},{value:'error',label:'返回错误'}]" @update:model-value="draft.result.missing=$event"/></label>
</div>
<p v-if="draft.result.selection==='latest'" class="field-help">按时间取最新：并列且无确定性取舍规则时报冲突。</p>
<p v-if="draft.result.selection==='sum'||draft.result.selection==='average'" class="field-help">求和／求平均只适用于业务口径允许汇总的数值属性；排序时间不作为聚合输出的采样时间。</p>
</details>
</template>
</template>
<template v-else-if="draft.kind==='redis'">
<div class="row">
<label>结果转换<AppSelect :model-value="draft.conversion||'number'" aria-label="结果转换" :options="[{value:'number',label:'数值'},{value:'integer',label:'整数'},{value:'text',label:'文本'}]" @update:model-value="draft.conversion=$event"/><small class="field-help">按属性类型默认：数值型属性默认数值，其余默认文本。</small></label>
<label>未找到或转换失败<AppSelect :model-value="draft.missing||'null'" aria-label="未找到或转换失败" :options="[{value:'null',label:'明确返回无数据'},{value:'error',label:'返回错误'}]" @update:model-value="draft.missing=$event"/><small class="field-help">缺失不静默变为 0。</small></label>
</div>
<p class="field-help">目标类型核对：属性要求「{{selectedMeta?.type}}」，转换结果为「{{conversionLabels[draft.conversion||'number']||draft.conversion}}」<template v-if="conversionMismatch">；<span class="inline-error">数值转换与目标类型不一致，请调整转换或改用其他来源</span></template>。</p>
</template>
<template v-else-if="draft.kind==='computed'">
<div class="row">
<label>实现的输出 *<AppSelect :model-value="draft.output||''" aria-label="实现输出" searchable :options="[{value:'',label:'请选择该实现的具体输出'},...outputsOf(draft.implementation)]" @update:model-value="draft.output=$event"/><small class="field-help">多输出实现不默认取第一个；选项展示契约输出的数据类型。</small></label>
</div>
  <p v-if="computedShapeMismatch" class="inline-error">函数输出数据类型与属性要求不匹配：输出为{{declaredShapeOf(draft.implementation,draft.output)==='timeSeries'?'时间序列':'单值'}}，属性要求{{draftShape==='timeSeries'?'时间序列':'单值'}}，请选择兼容输出。</p>
  <p v-else-if="ruleValueTypeMismatch" class="inline-error">取值规则值类型与属性数据类型不匹配：输出为{{dataTypeLabel(declaredTypeOf(draft.implementation,draft.output))}}，属性要求{{selectedMeta?.type}}，请选择兼容的规则或属性。</p>
  <p v-else-if="draft.output" class="field-help">输出「{{outputLabel(draft.implementation,draft.output)}}」：{{dataTypeLabel(declaredTypeOf(draft.implementation,draft.output))}}，与属性要求一致；数据类型的最终核对以保存校验为准。</p>
</template>
<template v-if="ghostFields.length">
<p v-for="g in ghostFields" :key="g" class="inline-warning">{{g}}</p>
</template>
<details v-if="draft.kind!=='none'" class="ps-advanced">
<summary>缺失与异常处理（默认）</summary>
<p class="field-help">{{advancedNote}}</p>
</details>
<div class="ps-summary">
<strong>配置摘要</strong>
<p v-if="draft.kind==='database'">{{databaseSummary(draft)}}</p>
<p v-else-if="draft.kind==='none'">尚未选择来源；保存将清除该属性的来源配置。</p>
<p v-else>{{summaryOf(draft)}}</p>
</div>
</template>
</div>
</fieldset>
<p v-if="editError" class="inline-error" role="alert">{{editError}}</p>
<div class="tools ps-actions">
<button :disabled="saving" @click="closeEditor">取消</button>
<div class="ps-actions-right">
<button class="primary" :disabled="saveDisabled||saving" @click="saveDraft">{{saving?'保存中…':(draft.kind==='none'?'清除来源配置':'保存')}}</button>
</div>
</div>
<p class="ps-foot-note">保存后写入当前项目草稿；取消放弃本次修改。</p>
</template>
</div>
</div></template>
<style scoped>
/* 列表态 */
.ps-lede{font-size:15px;font-weight:600;margin:0 0 10px}
.ps-table .prop-name{font-weight:650}
.ps-table .prop-name small{display:block;font-weight:400;margin-top:3px}
.ps-list-foot{font-size:12px;color:var(--muted);margin:12px 2px 0}
.pill-pending{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)}
.pill-unknown{background:var(--paper-3);border-color:var(--line);color:var(--muted)}
/* 配置态骨架 */
.ps-config-top{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:14px}
.ps-back{background:transparent;border-color:transparent;color:var(--blue);padding-left:0;font-size:14px}
.ps-back:hover{color:var(--blue-deep);border-color:transparent}
.ps-object-tag{font-size:12px;color:var(--muted);background:var(--paper);border:1px solid var(--line);border-radius:5px;padding:5px 10px;white-space:nowrap}
.ps-config-head{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:16px}
.ps-config-title{min-width:0}
.ps-config-head h2{margin:0 0 6px}
.ps-def{margin:0;font-size:13px;color:var(--muted);max-width:68ch;overflow-wrap:anywhere}
.ps-badge{flex:none;font-size:12px;color:var(--blue);background:var(--blue-soft);border:1px solid var(--blue-line);border-radius:20px;padding:4px 12px;white-space:nowrap}
.ps-question{font-size:15px;margin:0 0 14px}
.ps-sub{font-size:14px;margin:0 0 10px}
/* 两种取值方式 */
.ps-form-panel{border:1px solid var(--line);border-radius:9px;padding:22px}
.ps-form-panel .row{align-items:flex-start}
.ps-form-panel .row>*{min-width:0}
.ps-form-fields{border:0;padding:0;margin:0;min-width:0}
.ps-matching{margin:14px 0}
.ps-matching summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--ink)}
.ps-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:14px 0 6px}
.ps-card{display:block;text-align:left;border:1px solid var(--line);border-radius:9px;background:var(--paper);padding:14px 16px}
.ps-card strong{display:block;font-size:14px}
.ps-card small{display:block;margin-top:4px;color:var(--muted);font-size:12px}
.ps-card small.ps-card-disabled{color:var(--warn)}
.ps-card:hover{border-color:var(--blue);color:var(--ink)}
.ps-card.selected{border-color:var(--blue);box-shadow:0 0 0 1px var(--blue);background:var(--blue-soft)}
.ps-card.selected strong{color:var(--blue)}
.ps-card:disabled{opacity:.55;cursor:not-allowed}
.ps-card:disabled:hover{border-color:var(--line);color:var(--ink)}
.ps-clear{margin:6px 0 0;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.ps-clear-btn{background:transparent;border-color:transparent;color:var(--danger);padding-left:0;font-size:13px}
.ps-clear-btn:hover{color:var(--danger-deep);border-color:transparent}
/* 信息条 / 时间范围说明卡 / 摘要条 / 实现输入只读 */
.ps-info{background:var(--blue-soft);border:1px solid var(--blue-line);border-radius:7px;padding:10px 14px;font-size:13px;color:var(--muted);margin:10px 0}
.ps-missing{background:var(--warn-soft);border:1px solid var(--warn-line);border-radius:7px;padding:10px 14px;margin:10px 0}
.ps-missing p{margin:6px 0}
.ps-range-card strong{font-size:13px}
.ps-advanced{margin:16px 0 0}
.ps-advanced summary{cursor:pointer;font-size:13px;color:var(--muted);padding:5px 0}
.ps-summary{background:var(--blue-soft);border:1px solid var(--blue-line);border-left:3px solid var(--blue);border-radius:6px;padding:12px 16px;margin:16px 0 0}
.ps-summary strong{font-size:13px;color:#2c4a86}
.ps-summary p{margin:6px 0 0;font-size:13px;color:#33517f;overflow-wrap:anywhere}
.ps-impl-inputs{border:1px solid var(--line);border-radius:8px;background:var(--paper-2);margin:10px 0}
.ps-impl-row{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:10px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.ps-impl-row:last-child{border-bottom:0}
.ps-impl-row small{display:block;margin-top:2px}
.ps-impl-binding{font-size:13px;color:#33517f;overflow-wrap:anywhere}
.ps-inline-link{background:transparent;border-color:transparent;color:var(--blue);padding:0 2px;font-size:12px}
.ps-inline-link:hover{color:var(--blue-deep);border-color:transparent}
/* 底部按钮行：取消居左，保存居右 */
.ps-actions{margin-top:20px}
.ps-actions-right{margin-left:auto;display:flex;gap:9px}
.ps-foot-note{font-size:12px;color:var(--muted);margin:8px 2px 0}
@media(max-width:900px){.ps-form-panel .row{flex-direction:column;gap:0}.ps-form-panel .row>*{width:100%}.ps-cards{grid-template-columns:1fr}.ps-config-head{flex-direction:column}}
/* 内联 SQL 编辑框：等宽、保留缩进，与取值规则库的卡片编辑器一致 */
.inline-sql-editor{width:100%;box-sizing:border-box;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;line-height:1.65;tab-size:2;resize:vertical;padding:14px;background:var(--paper-2);color:var(--ink);border:1px solid var(--line);border-radius:8px;min-height:110px}
</style>

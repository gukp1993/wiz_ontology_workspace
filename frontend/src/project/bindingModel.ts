// 项目绑定视图模型：存储格式（bindings.yaml）↔ 来源制 UI 的双向适配层。
// 兼容旧格式：字符串字段映射、related_sources、{kind:'related'} 属性、relations[].column。
// 保存采用安全迁移：编辑过的来源写入 sources 并移除 related_sources；identity 来源的
// 普通字段仍写回字符串（演示引擎与旧读取方只认直接字符串字段映射）。

export interface DbSourceView{id:string;name:string;kind:'db';connection:string;table:string;matchLeft:string;matchRight:string;cardinality:'one'|'many';legacy?:boolean}
export interface RedisSourceView{id:string;name:string;kind:'redis';connection:string}
export type SourceView=DbSourceView|RedisSourceView
export interface FieldView{kind:'field';source:string;field:string;selection?:''|'latest'|'sum'|'average';timeField?:string;tieBreaker?:string;missing?:'null'|'error'}
export type RedisParamView={from:'primary'}|{from:'property';property:string}|{from:'identityField';field:string}
export interface RedisView{kind:'redis';source:string;connection:string;command:'GET'|'HGET';key:string;params:Record<string,RedisParamView>;hashField:string;conversion:'number'|'integer'|'text';missing:'null'|'error'}
export interface ComputedView{kind:'computed';implementation:string;output:string;inputs?:Record<string,any>}
// 属性内 SQL 取值（mode='inlineSql'）：匿名内联模板，与命名规则互斥；params 绑定结构由 inlineSql.ts 校验。
export interface InlineSqlConfig{version:1;connection:string;sql:string;params:Record<string,any>}
export interface InlineSqlView{kind:'computed';mode:'inlineSql';inlineSql:InlineSqlConfig}
// 计算函数绑定（mode='calcFunction'）：inputs 按函数参数稳定 ID 绑定当前对象属性或固定值。
export type CalcBindingEntry={from:'property';property:string}|{from:'constant';value:any}
export interface CalcFunctionView{kind:'computed';mode:'function';implementation:string;output:string;inputs:Record<string,CalcBindingEntry>}
// 数据库直选表（kind:'database'，2026-09 新结构）：存储里 secondarySort:{field,order} 嵌套，
// 视图扁平为 secondarySortField/secondarySortOrder；默认值在视图侧显式呈现
// （timestampEncoding=''、order='ascending'、duplicateTimestamp='error'、selection=''、missing='null'）。
export type MatchValueView={kind:'identityKey'}|{kind:'identityField';field:string}
  |{kind:'property';property:string}|{kind:'constant';value:string}|{kind:'parameter';parameter:string}
export interface MatchConditionView{field:string;operator:'eq';value:MatchValueView}
export interface TimeRangeView{field:string;start:{kind:'context';name:'startTime'};end:{kind:'context';name:'endTime'};bounds:'[start,end)'}
export interface ResultView{valueField:string;timestampField:string;timestampEncoding:''|'datetime'|'epochSeconds'|'epochMillis';timezone:string;order:'ascending'|'descending';duplicateTimestamp:'error'|'secondarySort';secondarySortField:string;secondarySortOrder:'ascending'|'descending';selection:''|'latest'|'sum'|'average';missing:'null'|'error'}
export interface DatabaseView{kind:'database';connection:string;table:string;lookup:{match:MatchConditionView[];timeRange:TimeRangeView|null};result:ResultView}
export interface UnknownView{kind:'unknown'}
// --- 无表对象与关联聚合（2026-09-15 契约冻结，阶段 0） -----------------------------
// 属性来源新增：登记信息（registered，取实例编号/显示名称）与关联聚合（aggregate，本轮仅 SUM）。
// empty 恒为字符串 'null'（YAML 序列化后端存字符串，视图层不得转成 null）。
export interface RegisteredView{kind:'registered';field:'id'|'label'}
export interface AggregateView{kind:'aggregate';relation:string;property:string;operator:'sum';empty:'null';missing:'incomplete';inputUnitConfirmed:boolean}
export type PropertyView=FieldView|RedisView|ComputedView|InlineSqlView|CalcFunctionView|DatabaseView|RegisteredView|AggregateView|UnknownView
// membership 链接（按条件选择成员）：按起点实例保存成员条件；value 仅 eq/ne/in 需要（标量或标量数组）。
export type MembershipOperator='eq'|'ne'|'in'|'isnull'|'notnull'
export type MembershipScalar=string|number|boolean
export interface MembershipConditionView{field:string;operator:MembershipOperator;value?:MembershipScalar|MembershipScalar[]}
export interface MembershipRuleView{sourceInstance:string;scope:'filtered'|'all';conditions:MembershipConditionView[]}
// 登记身份（binding 无 identity 块 = 旧数据库身份，connection/table/primary_key 原样）。
export type BindingIdentityMode='database'|'registered'
export interface RegisteredInstanceView{id:string;label:string}
export interface RegisteredIdentityView{kind:'registered';instances:RegisteredInstanceView[]}
// RelationView 保持扁平以兼容旧消费方（LinkMappings 等直接读列字段）：
// membership＝membership 链接的规则（此时列字段为空串）；unknownRaw＝未识别 kind 或偏离冻结
// 契约结构的原样载荷（commitRelation 原样写回，零丢失）。
export interface RelationView{relation:string;targetType:string;sourceId:string;field:string;targetSourceId:string;targetField:string;legacy:boolean;membership?:MembershipRuleView[];unknownRaw?:any}

export function newSourceId():string{return 'src-'+crypto.randomUUID().replaceAll('-','').slice(0,12)}
export function newConnectionId():string{return 'conn-'+crypto.randomUUID().replaceAll('-','').slice(0,12)}

// 全部来源（identity 除外）：显式 sources + 旧 related_sources 内存适配（cardinality 固定 one）。
export function sourcesOf(b:any):SourceView[]{
  const out:SourceView[]=[...(b.sources||[])]
  for(const r of (b.related_sources||[]))
    out.push({id:String(r.id||''),name:String(r.name||''),kind:'db',connection:String(b.connection||''),table:String(r.table||''),matchLeft:String(r.source_field||''),matchRight:String(r.target_field||''),cardinality:'one',legacy:true})
  return out
}
export function dbSourcesOf(b:any):DbSourceView[]{return sourcesOf(b).filter(s=>s.kind==='db') as DbSourceView[]}
export function redisSourcesOf(b:any):RedisSourceView[]{return sourcesOf(b).filter(s=>s.kind==='redis') as RedisSourceView[]}
export function sourceById(b:any,id:string):SourceView|undefined{return id?sourcesOf(b).find(s=>s.id===id):undefined}

// 来源保存即迁移：related_sources 清空；{kind:'related'} 属性转为 {kind:'field'}。
export function commitSources(b:any,views:SourceView[]):void{
  b.sources=views.map(v=>({...v,legacy:undefined}))
  delete b.related_sources
  for(const api of Object.keys(b.properties||{})){
    const v=b.properties[api]
    if(v&&typeof v==='object'&&v.kind==='related')b.properties[api]={kind:'field',source:v.source,field:v.field}
  }
}
// 移除补充来源时同步清理引用：属性来源与链接映射。
export function dropSource(b:any,sourceId:string):void{
  b.sources=(b.sources||[]).filter((s:any)=>s.id!==sourceId)
  b.related_sources=(b.related_sources||[]).filter((s:any)=>s.id!==sourceId)
  for(const api of Object.keys(b.properties||{})){
    const v=b.properties[api]
    if(v&&typeof v==='object'&&v.source===sourceId)delete b.properties[api]
  }
  for(const r of (b.relations||[])){
    if(r.sourceId===sourceId){r.sourceId='';r.field=''}
    if(r.targetSourceId===sourceId){r.targetSourceId='';r.targetField=''}
  }
}

// {kind:'database'} 解码：缺失子结构补默认值；secondarySort 嵌套拆平。
function decodeMatchValue(raw:any):MatchValueView{
  const k=raw&&typeof raw==='object'?String(raw.kind||''):''
  if(k==='identityField')return {kind:'identityField',field:String(raw.field||'')}
  if(k==='property')return {kind:'property',property:String(raw.property||'')}
  if(k==='constant')return {kind:'constant',value:raw.value===undefined||raw.value===null?'':String(raw.value)}
  if(k==='parameter')return {kind:'parameter',parameter:String(raw.parameter||'')}
  return {kind:'identityKey'}
}
function decodeDatabase(v:any):DatabaseView{
  const lookup=v.lookup&&typeof v.lookup==='object'?v.lookup:{}
  const tr=lookup.timeRange&&typeof lookup.timeRange==='object'?lookup.timeRange:null
  const r=v.result&&typeof v.result==='object'?v.result:{}
  const ss=r.secondarySort&&typeof r.secondarySort==='object'?r.secondarySort:{}
  return {kind:'database',connection:String(v.connection||''),table:String(v.table||''),
    lookup:{match:(Array.isArray(lookup.match)?lookup.match:[]).map((m:any)=>({field:String(m?.field||''),operator:'eq',value:decodeMatchValue(m?.value)})),
      timeRange:tr?{field:String(tr.field||''),start:{kind:'context',name:'startTime'},end:{kind:'context',name:'endTime'},bounds:'[start,end)'}:null},
    result:{valueField:String(r.valueField||''),timestampField:String(r.timestampField||''),
      timestampEncoding:(['datetime','epochSeconds','epochMillis'].includes(r.timestampEncoding)?r.timestampEncoding:'') as ResultView['timestampEncoding'],
      timezone:String(r.timezone||''),order:r.order==='descending'?'descending':'ascending',
      duplicateTimestamp:r.duplicateTimestamp==='secondarySort'?'secondarySort':'error',
      secondarySortField:String(ss.field||''),secondarySortOrder:ss.order==='descending'?'descending':'ascending',
      selection:(['latest','sum','average'].includes(r.selection)?r.selection:'') as ResultView['selection'],
      missing:r.missing==='error'?'error':'null'}}
}

// 属性来源视图：字符串＝identity 字段；旧 related 视作 field；未知 kind 一律 unknown（绝不当 computed）。
export function propertyView(b:any,api:string):PropertyView|null{
  const v=(b.properties||{})[api]
  if(v===undefined||v===null||v==='')return null
  if(typeof v==='string')return {kind:'field',source:'',field:v}
  if(v.kind==='related')return {kind:'field',source:String(v.source||''),field:String(v.field||'')}
  if(v.kind==='redis')return {kind:'redis',source:v.connection?'':String(v.source||''),connection:v.connection?String(v.connection||''):'',command:v.command==='HGET'?'HGET':'GET',key:String(v.key||''),params:{...(v.params||{})},hashField:String(v.hashField||''),conversion:(['number','integer','text'].includes(v.conversion)?v.conversion:'number') as RedisView['conversion'],missing:v.missing==='error'?'error':'null'}
  if(v.kind==='field')return {kind:'field',source:String(v.source||''),field:String(v.field||''),selection:(['latest','sum','average'].includes(v.selection)?v.selection:'') as FieldView['selection'],timeField:String(v.timeField||''),tieBreaker:String(v.tieBreaker||''),missing:v.missing==='error'?'error':'null'}
  if(v.kind==='database')return decodeDatabase(v)
  if(v.kind==='computed'){
    // 先识别 mode=inlineSql（方案 §5）：结构完整才解出内联视图；未知版本／mode／多余字段
    // 一律走 unknown 通道原样保留（零丢失），绝不当空计算规则重存。
    if(v.mode==='inlineSql'){
      const is=v.inlineSql
      const ok=is&&typeof is==='object'&&!Array.isArray(is)&&is.version===1
        &&typeof is.connection==='string'&&typeof is.sql==='string'
        &&is.params!==null&&typeof is.params==='object'&&!Array.isArray(is.params)
        &&Object.keys(is).every(k=>['version','connection','sql','params'].includes(k))
      return ok?{kind:'computed',mode:'inlineSql',inlineSql:{version:1,connection:is.connection,sql:is.sql,params:JSON.parse(JSON.stringify(is.params))}}:{kind:'unknown'}
    }
    if(v.mode==='calcFunction'){
      const entries=v.inputs
      const okEntries=entries!==null&&typeof entries==='object'&&!Array.isArray(entries)
        &&Object.values(entries).every((e:any)=>e&&typeof e==='object'&&!Array.isArray(e)&&(e.from==='property'||e.from==='constant'))
      const okIds=typeof v.implementation==='string'&&typeof v.output==='string'
      return okIds&&okEntries?{kind:'computed',mode:'function',implementation:v.implementation,output:v.output,inputs:JSON.parse(JSON.stringify(entries))}:{kind:'unknown'}
    }
    if(v.mode!==undefined)return {kind:'unknown'}
    return {kind:'computed',implementation:String(v.implementation||''),output:String(v.output||''),...(v.inputs!==undefined?{inputs:JSON.parse(JSON.stringify(v.inputs))}:{})}
  }
  if(v.kind==='registered')return v.field==='label'?{kind:'registered',field:'label'}:v.field==='id'?{kind:'registered',field:'id'}:{kind:'unknown'}
  if(v.kind==='aggregate'){
    // 严格按冻结契约解码：operator 仅 sum；empty 仅字符串 'null'（键缺失视为默认，YAML 真 null
    // 或其他值视为偏离，走 unknown 通道零丢失）；missing 仅 incomplete。
    if(v.operator!=='sum')return {kind:'unknown'}
    if(v.empty!==undefined&&(typeof v.empty!=='string'||v.empty!=='null'))return {kind:'unknown'}
    if(v.missing!==undefined&&v.missing!=='incomplete')return {kind:'unknown'}
    return {kind:'aggregate',relation:String(v.relation||''),property:String(v.property||''),operator:'sum',empty:'null',missing:'incomplete',inputUnitConfirmed:v.inputUnitConfirmed===true}
  }
  return {kind:'unknown'}
}
// 属性保存：整体替换旧来源；identity 普通字段编码回字符串（演示引擎只接收字符串映射）。
// database：等价于"身份表本行直接字段"的极简配置压缩为字符串（契约 §2.4），其余完整落盘；
// unknown：存储中不是本工具认识的结构，完全不动 b.properties[api]（零丢失）。
export function commitProperty(b:any,api:string,view:PropertyView|null):void{
  if(!view){delete b.properties[api];if(b.title_key===api)b.title_key='';return}
  if(view.kind==='unknown')return
  if(view.kind==='field'){
    if(!view.source&&!view.selection){b.properties[api]=view.field;if(b.title_key===api&&!view.field)b.title_key='';return}
    const out:any={kind:'field',source:view.source,field:view.field}
    if(view.source&&view.selection){out.selection=view.selection;out.timeField=String(view.timeField||'');out.tieBreaker=String(view.tieBreaker||'')}
    if(view.missing)out.missing=view.missing
    b.properties[api]=out;return
  }
  if(view.kind==='redis'){
    // source 与 connection 互斥：直连写 connection，登记来源写 source；两者都空则都不写（后端校验兜底）。
    const out:any={kind:'redis',command:view.command,key:view.key,params:{...view.params},hashField:view.command==='HGET'?view.hashField:'',conversion:view.conversion,missing:view.missing}
    if(view.connection)out.connection=view.connection
    else if(view.source)out.source=view.source
    b.properties[api]=out;return
  }
  if(view.kind==='database'){
    const r=view.result
    // 压缩为字符串的条件（全部满足）：身份表本行（connection/table 与绑定一致）+ 无定位条件 +
    // 无时间范围 + 结果仅有值字段（order/duplicateTimestamp/missing 等于默认、无时间/次级排序/取值/缺失设置）。
    const bare=view.connection===b.connection&&view.table===b.table
      &&view.lookup.match.length===0&&!view.lookup.timeRange
      &&!r.timestampField&&!r.timestampEncoding&&!r.timezone
      &&r.order==='ascending'&&r.duplicateTimestamp==='error'&&!r.secondarySortField
      &&r.selection===''&&r.missing==='null'
    if(bare){b.properties[api]=r.valueField;if(b.title_key===api&&!r.valueField)b.title_key='';return}
    const out:any={kind:'database',connection:view.connection,table:view.table,
      lookup:{match:view.lookup.match.map(m=>({field:m.field,operator:m.operator,value:JSON.parse(JSON.stringify(m.value))})),timeRange:null},
      result:{valueField:r.valueField}}
    if(view.lookup.timeRange)out.lookup.timeRange={field:view.lookup.timeRange.field,start:{kind:'context',name:'startTime'},end:{kind:'context',name:'endTime'},bounds:'[start,end)'}
    // 落盘规范化：默认值不写；secondarySort 仅在 duplicateTimestamp='secondarySort' 时写嵌套结构。
    if(r.timestampField)out.result.timestampField=r.timestampField
    if(r.timestampEncoding)out.result.timestampEncoding=r.timestampEncoding
    if(r.timezone)out.result.timezone=r.timezone
    if(r.order!=='ascending')out.result.order=r.order
    if(r.duplicateTimestamp!=='error')out.result.duplicateTimestamp=r.duplicateTimestamp
    if(r.duplicateTimestamp==='secondarySort')out.result.secondarySort={field:r.secondarySortField,order:r.secondarySortOrder}
    if(r.selection)out.result.selection=r.selection
    if(r.missing!=='null')out.result.missing=r.missing
    b.properties[api]=out;return
  }
  if(view.kind==='registered'){b.properties[api]={kind:'registered',field:view.field};return}
  if(view.kind==='aggregate'){
    // 原样对象存取；empty 恒写字符串 'null'（YAML/JSON 序列化后端存字符串，视图层不得转 null）。
    b.properties[api]={kind:'aggregate',relation:String(view.relation||''),property:String(view.property||''),operator:'sum',empty:'null',missing:'incomplete',inputUnitConfirmed:view.inputUnitConfirmed===true}
    return
  }
  if('mode' in view&&view.mode==='function'){
    // 计算函数绑定：inputs 按函数参数稳定 ID；不复制函数、不写本体。
    b.properties[api]={kind:'computed',mode:'calcFunction',implementation:view.implementation,output:view.output,inputs:JSON.parse(JSON.stringify(view.inputs))}
    return
  }
  if('mode' in view&&view.mode==='inlineSql'){
    // 属性内 SQL：params 已由表单按扫描结果裁剪；此处原样深拷贝，不新增 implementations。
    b.properties[api]={kind:'computed',mode:'inlineSql',inlineSql:JSON.parse(JSON.stringify(view.inlineSql))}
    return
  }
  const rule=view as ComputedView
  b.properties[api]={kind:'computed',implementation:rule.implementation,output:rule.output,...(rule.inputs!==undefined?{inputs:JSON.parse(JSON.stringify(rule.inputs))}:{})}
}

// 链接视图：kind='membership' 解出规则（无表字段）；无 kind+column 为旧格式标记 legacy（保存整体写新格式）；
// 未识别 kind 或 membership 结构偏离冻结契约时原样挂在 unknownRaw 上（保存原样写回，零丢失）。
const MEMBERSHIP_KEYS=['kind','relation','target_type','rules']
function isMembershipScalar(x:any):boolean{return typeof x==='string'||typeof x==='number'||typeof x==='boolean'}
function decodeMembershipRules(raw:any):MembershipRuleView[]|null{
  if(!Array.isArray(raw))return null
  const out:MembershipRuleView[]=[]
  for(const rule of raw){
    if(!rule||typeof rule!=='object'||Array.isArray(rule))return null
    const keys=Object.keys(rule)
    if(keys.length!==3||!keys.includes('sourceInstance')||!keys.includes('scope')||!keys.includes('conditions'))return null
    if(typeof rule.sourceInstance!=='string'||!rule.sourceInstance)return null
    if(rule.scope!=='filtered'&&rule.scope!=='all')return null
    if(!Array.isArray(rule.conditions))return null
    const conditions:MembershipConditionView[]=[]
    for(const c of rule.conditions){
      if(!c||typeof c!=='object'||Array.isArray(c))return null
      const ckeys=Object.keys(c)
      if(!ckeys.includes('field')||!ckeys.includes('operator'))return null
      if(!ckeys.every(k=>k==='field'||k==='operator'||k==='value'))return null
      if(typeof c.field!=='string'||!c.field)return null
      if(c.operator==='eq'||c.operator==='ne'){
        if(!ckeys.includes('value')||!isMembershipScalar(c.value))return null
        conditions.push({field:c.field,operator:c.operator,value:c.value})
      }else if(c.operator==='in'){
        if(!Array.isArray(c.value)||!c.value.length||!c.value.every(isMembershipScalar))return null
        conditions.push({field:c.field,operator:'in',value:[...c.value]})
      }else if(c.operator==='isnull'||c.operator==='notnull'){
        if(ckeys.includes('value'))return null
        conditions.push({field:c.field,operator:c.operator})
      }else return null
    }
    out.push({sourceInstance:rule.sourceInstance,scope:rule.scope,conditions})
  }
  return out
}
export function relationView(r:any):RelationView{
  if(r&&typeof r==='object'&&typeof r.kind==='string'){
    if(r.kind==='membership'
      &&Object.keys(r).every(k=>MEMBERSHIP_KEYS.includes(k))
      &&'relation' in r&&'target_type' in r){
      const rules=decodeMembershipRules(r.rules)
      if(rules)return {relation:String(r.relation||''),targetType:String(r.target_type||''),sourceId:'',field:'',targetSourceId:'',targetField:'',legacy:false,membership:rules}
    }
    return {relation:String(r.relation||''),targetType:String(r.target_type||''),sourceId:'',field:'',targetSourceId:'',targetField:'',legacy:false,unknownRaw:JSON.parse(JSON.stringify(r))}
  }
  const isNew=r.field!==undefined||r.targetSourceId!==undefined||r.targetField!==undefined
  return {relation:String(r.relation||''),targetType:String(r.target_type||''),sourceId:String(r.sourceId||''),field:String(r.field||r.column||''),targetSourceId:String(r.targetSourceId||''),targetField:String(r.targetField||''),legacy:!isNew&&!!r.column}
}
export function commitRelation(b:any,index:number,view:RelationView):void{
  if(view.unknownRaw!==undefined){b.relations[index]=JSON.parse(JSON.stringify(view.unknownRaw));return}
  if(view.membership){
    b.relations[index]={kind:'membership',relation:view.relation,target_type:view.targetType,rules:view.membership.map(rule=>({
      sourceInstance:rule.sourceInstance,scope:rule.scope,
      conditions:rule.conditions.map(c=>c.operator==='in'?{field:c.field,operator:'in',value:[...(c.value as MembershipScalar[])]}
        :c.operator==='eq'||c.operator==='ne'?{field:c.field,operator:c.operator,value:c.value as MembershipScalar}
        :{field:c.field,operator:c.operator})}))}
    return
  }
  b.relations[index]={relation:view.relation,target_type:view.targetType,sourceId:view.sourceId,field:view.field,targetSourceId:view.targetSourceId,targetField:view.targetField}
}

// --- 登记身份（identity 块）助手 ---------------------------------------------------
// b.identity.kind 非 'registered' 的未知值按 database 展示，但调用方不得据此删除 identity 块（零丢失）。
export function bindingIdentityOf(b:any):BindingIdentityMode{return b?.identity?.kind==='registered'?'registered':'database'}
// 解码登记实例（返回副本，可直接作表单草稿；结构不完整的条目被丢弃）。
export function registeredInstancesOf(b:any):RegisteredInstanceView[]{
  const raw=b?.identity?.kind==='registered'?b.identity.instances:null
  if(!Array.isArray(raw))return []
  return raw.filter((i:any)=>i&&typeof i==='object').map((i:any)=>({id:String(i.id||''),label:String(i.label||'')}))
}
// 保存为登记身份：不清空 connection/table/primary_key（后端 registered 分支忽略旧三键，保留利于切回）。
export function commitRegisteredIdentity(b:any,instances:RegisteredInstanceView[]):void{
  b.identity={kind:'registered',instances:instances.map(i=>({id:String(i.id||''),label:String(i.label||'')}))}
}
export function clearRegisteredIdentity(b:any):void{delete b.identity}
// 删除登记实例并清除 membership 规则中该 sourceInstance 的行（被引用的删除由 UI 层阻止；这里只做数据清理）。返回清除的规则行数。
export function dropRegisteredInstance(b:any,id:string):number{
  let removed=0
  for(const r of (b.relations||[])){
    if(!r||r.kind!=='membership'||!Array.isArray(r.rules))continue
    const kept=r.rules.filter((rule:any)=>String(rule?.sourceInstance||'')!==id)
    if(kept.length!==r.rules.length){removed+=r.rules.length-kept.length;r.rules=kept}
  }
  if(b?.identity?.kind==='registered'&&Array.isArray(b.identity.instances))
    b.identity.instances=b.identity.instances.filter((i:any)=>String(i?.id||'')!==id)
  return removed
}
// 引用指定登记实例的 membership 链接（返回链接稳定 id 列表，去重）。
export function instanceMembershipReferencesOf(b:any,instanceId:string):string[]{
  const out:string[]=[]
  for(const r of (b.relations||[])){
    if(!r||r.kind!=='membership'||!Array.isArray(r.rules))continue
    if(r.rules.some((rule:any)=>String(rule?.sourceInstance||'')===instanceId)){
      const id=String(r.relation||'')
      if(!out.includes(id))out.push(id)
    }
  }
  return out
}
// 本对象全部 membership 链接（rules=null 表示结构偏离冻结契约，消费方需原样保留）。
export interface MembershipRelationInfo{index:number;relation:string;targetType:string;rules:MembershipRuleView[]|null}
export function membershipRelationsOf(b:any):MembershipRelationInfo[]{
  const out:MembershipRelationInfo[]=[]
  ;(b.relations||[]).forEach((r:any,index:number)=>{
    if(!r||r.kind!=='membership')return
    const view=relationView(r)
    out.push({index,relation:view.relation,targetType:view.targetType,rules:view.membership||null})
  })
  return out
}
export function hasRegisteredPropertySources(b:any):boolean{
  return Object.values(b?.properties||{}).some((v:any)=>v&&typeof v==='object'&&v.kind==='registered')
}

// --- 来源方式切换影响检查 -----------------------------------------------------------
// IdentityRefItem：property→属性 api 键；relation→链接稳定 id；source→补充来源名称。label 由 UI 层按本体解析。
export interface IdentityRefItem{type:'property'|'relation'|'source';key:string;note:string}
// 依赖项目登记身份、阻止切回数据库来源的配置：登记信息属性、关联聚合属性、membership 链接。
export function registeredBlockingItemsOf(b:any):IdentityRefItem[]{
  const out:IdentityRefItem[]=[]
  for(const api of Object.keys(b.properties||{})){
    const v=(b.properties||{})[api]
    if(v&&typeof v==='object'&&v.kind==='registered')out.push({type:'property',key:api,note:'登记信息来源'})
    else if(v&&typeof v==='object'&&v.kind==='aggregate')out.push({type:'property',key:api,note:'关联聚合来源'})
  }
  for(const r of (b.relations||[]))if(r&&r.kind==='membership')out.push({type:'relation',key:String(r.relation||''),note:'按条件选择成员'})
  return out
}
// 依赖数据库身份（连接/表/主键）的配置：切到项目登记后保留但暂不生效（信息展示，不阻止切换）。
export function databaseIdentityDependentsOf(b:any):IdentityRefItem[]{
  const out:IdentityRefItem[]=[]
  for(const api of Object.keys(b.properties||{})){
    const v=propertyView(b,api)
    if(!v)continue
    if(v.kind==='unknown')out.push({type:'property',key:api,note:'未识别来源格式'})
    else if(v.kind==='field'&&!v.source)out.push({type:'property',key:api,note:'身份表字段'})
    else if(v.kind==='database'){
      const identityRow=String(v.connection)===String(b?.connection||'')&&String(v.table)===String(b?.table||'')
      const usesInstance=v.lookup.match.some(m=>m.value.kind==='identityKey'||m.value.kind==='identityField')
      if(identityRow||usesInstance)out.push({type:'property',key:api,note:'数据库直选'})
    }else if(v.kind==='redis'){
      const params:any=v.params&&typeof v.params==='object'?v.params:{}
      if(Object.values(params).some((p:any)=>p&&(p.from==='primary'||p.from==='identityField')))out.push({type:'property',key:api,note:'Redis 参数绑定实例'})
    }
  }
  for(const r of (b.relations||[])){
    if(!r||r.kind==='membership')continue
    out.push({type:'relation',key:String(r.relation||''),note:typeof r.kind==='string'?'未识别链接格式':'表字段关联'})
  }
  for(const s of sourcesOf(b))if(s.kind==='db')out.push({type:'source',key:s.name||s.table||s.id,note:'补充表匹配身份表'})
  return out
}
// membership 条件/范围下拉选项（供链接映射阶段表单使用）。
export const MEMBERSHIP_OPERATORS:{value:MembershipOperator;label:string}[]=[
  {value:'eq',label:'等于'},{value:'ne',label:'不等于'},{value:'in',label:'属于列表'},{value:'isnull',label:'为空'},{value:'notnull',label:'不为空'}]
export const MEMBERSHIP_SCOPES:{value:'filtered'|'all';label:string}[]=[
  {value:'filtered',label:'按条件选择（至少一条）'},{value:'all',label:'该来源表全部有效记录'}]

// --- 表结构目录（catalogs）助手 -----------------------------------------------
export interface CatalogField{name:string;dataType:string;key:''|'pri'|'uni';comment:string}
export interface CatalogTable{name:string;kind:'table'|'view';fields:CatalogField[]}
export function catalogOf(projectState:any,connectionId:string):{database:string;tables:CatalogTable[];refreshedAt:string}|null{
  return projectState?.bindings?.catalogs?.[connectionId]||null
}
export function tableCatalog(projectState:any,connectionId:string,table:string):CatalogTable|null{
  return catalogOf(projectState,connectionId)?.tables?.find(t=>t.name===table)||null
}
export function tableOptions(projectState:any,connectionId:string):{value:string;label:string}[]{
  const c=catalogOf(projectState,connectionId)
  return (c?.tables||[]).map(t=>({value:t.name,label:t.name+(t.kind==='view'?'（视图）':'')}))
}
export function fieldOptions(table:CatalogTable|null):{value:string;label:string}[]{
  return (table?.fields||[]).map(f=>({value:f.name,label:f.name+(f.key==='pri'?'（主键）':f.key==='uni'?'（唯一）':'')+(f.comment?' · '+f.comment:'')}))
}

// Key 模板占位符：{token} 形式，返回去重 token 列表。
export function keyTokens(template:string):string[]{
  const out:string[]=[]
  for(const m of String(template||'').matchAll(/\{([^{}\s]+)\}/g))if(!out.includes(m[1]))out.push(m[1])
  return out
}

// --- 表结构目录刷新（连接探测为真实执行；失败保留旧目录） -----------------------
import { catalogRefresh } from './api'
// 目录存储在服务端（ontology/catalogs/），不随项目草稿传输；成功后同步写入本地
// projectState 以便下拉即时可用。
export async function refreshCatalogOf(projectState:any,connectionId:string,mutate:(fn:()=>void)=>void):Promise<{ok:boolean;message:string}>{
  const conn=(projectState.connections?.connections||[]).find((c:any)=>c.id===connectionId)
  if(!conn)return {ok:false,message:'未找到该连接的配置，请先在“数据连接”页保存连接'}
  try{
    const d=await catalogRefresh(projectState.projectId,connectionId)
    if(d.ok){mutate(()=>{projectState.bindings.catalogs=projectState.bindings.catalogs||{};projectState.bindings.catalogs[connectionId]={database:d.database,tables:d.tables,refreshedAt:d.refreshedAt}});return {ok:true,message:d.message||'已读取表结构目录'}}
    return {ok:false,message:d.message||'目录读取失败'}
  }catch(e){return {ok:false,message:'无法访问工作台服务：'+(e as Error).message}}
}

// --- 数据库直选表（kind:'database'）与本地结构校验助手 ---------------------------------

// 时间戳编码下拉选项（与 ResultView['timestampEncoding'] 非''分支对应）。
export const TIMESTAMP_ENCODINGS:{value:'datetime'|'epochSeconds'|'epochMillis';label:string}[]=[
  {value:'datetime',label:'日期时间（ISO 8601 + 时区）'},
  {value:'epochSeconds',label:'数值时间戳 · 秒'},
  {value:'epochMillis',label:'数值时间戳 · 毫秒'}]

// 项目连接下拉：按引擎过滤，label 用连接名（缺省回退连接 id）。
export function mysqlConnectionsOf(projectState:any):{value:string;label:string}[]{
  return ((projectState?.connections?.connections)||[]).filter((c:any)=>c.engine==='mysql').map((c:any)=>({value:String(c.id||''),label:String(c.name||c.id||'')}))
}
export function redisConnectionsOf(projectState:any):{value:string;label:string}[]{
  return ((projectState?.connections?.connections)||[]).filter((c:any)=>c.engine==='redis').map((c:any)=>({value:String(c.id||''),label:String(c.name||c.id||'')}))
}

// 身份表（实例来源）：绑定对象当前的 connection+table。
export function identityTableOf(b:any):{connection:string;table:string}{
  return {connection:String(b?.connection||''),table:String(b?.table||'')}
}

// database 配置的可读摘要。labels 可选：{connectionNames?:Record<id,名称>, propertyNames?:Record<api,名称>}。
// 形如「连接 xxx · 表 t：object_id=当前实例主键 且 object_type='storage_cluster'；
// 时间范围 sampled_at∈[startTime,end)；结果 value→值、sampled_at→时间（ISO 8601 + 时区 tz，升序，重复时刻报错）」；
// 单值目标附多行取值/缺失策略描述。
function matchValueText(v:MatchValueView,labels?:any):string{
  if(v.kind==='identityKey')return '当前实例主键'
  if(v.kind==='identityField')return `身份表字段 ${v.field||'（未选）'}`
  if(v.kind==='property')return `属性 ${labels?.propertyNames?.[v.property]||v.property||'（未选）'}`
  if(v.kind==='constant')return `'${v.value}'`
  return `参数 ${v.parameter||'（未选）'}`
}
function resultText(r:ResultView):string{
  let s=`结果 ${r.valueField||'（未选字段）'}→值`
  if(r.selection==='latest'||r.selection==='sum'||r.selection==='average'){
    const sel=r.selection==='latest'?`取最新一条（按时间字段 ${r.timestampField||'（未选）'}）`:r.selection==='sum'?'多行求和':'多行平均'
    s+=`（多行处理：${sel}；无记录：${r.missing==='error'?'报错':'结果为空'}）`
  }else if(r.timestampField&&r.timestampEncoding){
    const enc=r.timestampEncoding==='epochSeconds'?'秒时间戳':r.timestampEncoding==='epochMillis'?'毫秒时间戳':'ISO 8601'+(r.timezone?' + 时区 '+r.timezone:'')
    const dup=r.duplicateTimestamp==='secondarySort'?`重复时刻按 ${r.secondarySortField||'（未选）'} ${r.secondarySortOrder==='descending'?'降序':'升序'}序取一`:'重复时刻报错'
    s+=`、${r.timestampField}→时间（${enc}，${r.order==='descending'?'降序':'升序'}，${dup}）`
  }else{
    s+=`（多行处理：预期零或一条记录，多行报错；无记录：${r.missing==='error'?'报错':'结果为空'}）`
  }
  return s
}
export function databaseSummary(view:DatabaseView,labels?:any):string{
  const conn=labels?.connectionNames?.[view.connection]||view.connection||'（未选连接）'
  const segs:string[]=[]
  const conds=view.lookup.match.filter(m=>String(m.field||'').trim()).map(m=>`${m.field}=${matchValueText(m.value,labels)}`)
  if(conds.length)segs.push(conds.join(' 且 '))
  if(view.lookup.timeRange)segs.push(`时间范围 ${view.lookup.timeRange.field||'（未选字段）'}∈[startTime,end)`)
  segs.push(resultText(view.result))
  return `连接 ${conn} · 表 ${view.table||'（未选表）'}：${segs.join('；')}`
}

// 本地结构校验：后端 blocking 规则的轻量前端镜像（只做结构层；目录/本体元数据类检查交给后端 report）。
// shape 为目标属性的结果形态（scalar | timeSeries）。返回中文 issue 列表，空数组＝结构校验通过。
export function propertyLocalIssues(b:any,api:string,view:any,shape:'scalar'|'timeSeries'):string[] {
  if(!view||view.kind==='unknown')return []
  const out:string[]=[]
  // 时间序列目标只允许 database 直选表或声明序列输出的 computed（旧字符串/related 语义已在 propertyView 解码为 field）
  if(shape==='timeSeries'&&(view.kind==='field'||view.kind==='redis'))out.push('时间序列属性需要数据库直选表的时间序列映射，或声明序列输出的函数结果')
  if(view.kind==='database'){
    if(!String(view.connection||'').trim())out.push('未选择数据连接')
    if(!String(view.table||'').trim())out.push('未选择数据表')
    if(!String(view.result?.valueField||'').trim())out.push('未选择结果值字段')
    const r=view.result||{}
    if(shape==='timeSeries'){
      if(!String(r.timestampField||'').trim())out.push('时间序列属性必须选择时间字段')
      if(!r.timestampEncoding)out.push('时间序列属性必须选择时间戳编码')
      if(r.timestampEncoding==='datetime'&&!String(r.timezone||'').trim())out.push('日期时间编码必须填写时区')
      if(r.duplicateTimestamp==='secondarySort'&&!String(r.secondarySortField||'').trim())out.push('重复时刻按次级排序取一必须选择排序字段')
      const tr=view.lookup?.timeRange
      if(tr&&!String(tr.field||'').trim())out.push('时间范围未选择时间字段')
      if(r.selection)out.push('时间序列属性不能设置多行取值规则')
      if(r.missing&&r.missing!=='null')out.push('时间序列属性不能设置缺失处理策略')
    }else{
      if(r.timestampField||r.timestampEncoding||r.timezone||r.order&&r.order!=='ascending'||r.duplicateTimestamp&&r.duplicateTimestamp!=='error'||r.secondarySortField)out.push('单值属性不能映射时间序列结果')
      if(view.lookup?.timeRange)out.push('单值属性不能配置时间范围')
      if(r.selection==='latest'&&(!String(r.timestampField||'').trim()||!r.timestampEncoding))out.push('取最新一条必须指定时间字段与时间戳编码')
    }
    const identityRow=view.connection===b?.connection&&view.table===b?.table
    if(!identityRow){
      const hasInstance=(view.lookup?.match||[]).some((m:any)=>m?.value&&(m.value.kind==='identityKey'||m.value.kind==='identityField'))
      if(!hasInstance)out.push('匹配条件至少需要一条绑定当前实例（当前实例主键或身份表字段）')
    }
    for(const m of (view.lookup?.match||[])){
      if(!String(m?.field||'').trim()){out.push('匹配条件未选择来源字段');continue}
      const mv=m?.value
      if(!mv||typeof mv!=='object'){out.push(`匹配条件「${m.field}」的比较值来源无效`);continue}
      if(mv.kind==='identityField'){if(!String(mv.field||'').trim())out.push(`匹配条件「${m.field}」未选择身份表字段`)}
      else if(mv.kind==='property'){
        if(!String(mv.property||'').trim())out.push(`匹配条件「${m.field}」未选择引用属性`)
        else if(mv.property===api)out.push(`匹配条件「${m.field}」不能引用属性自身`)
        else if(!(mv.property in (b?.properties||{})))out.push(`匹配条件「${m.field}」引用的属性尚未配置来源`)
      }
      else if(mv.kind==='constant'){if(!String(mv.value??'').trim())out.push(`匹配条件「${m.field}」的常量值不能为空`)}
      else if(mv.kind==='parameter'){if(!String(mv.parameter||'').trim())out.push(`匹配条件「${m.field}」未选择项目参数`)}
    }
    return out
  }
  if(view.kind==='redis'){
    const source=String(view.source||'').trim(),connection=String(view.connection||'').trim()
    if(source&&connection)out.push('Redis 已登记来源与直连连接只能选择其一')
    if(!source&&!connection)out.push('未选择 Redis 数据来源或直连连接')
    const key=String(view.key||'')
    if(!key.trim())out.push('未填写 Key 模板')
    if(view.command==='HGET'&&!String(view.hashField||'').trim())out.push('HGET 读取必须填写 Hash 字段')
    const params=view.params&&typeof view.params==='object'?view.params:{}
    for(const token of keyTokens(key))if(!(token in params))out.push(`Key 模板占位符「{${token}}」未绑定参数`)
    for(const token of Object.keys(params)){
      const target=(params as Record<string,any>)[token]
      if(!target||typeof target!=='object'){out.push(`参数「${token}」绑定配置无效`);continue}
      if(target.from==='primary')continue
      if(target.from==='identityField'){if(!String(target.field||'').trim())out.push(`参数「${token}」未选择身份表字段`);continue}
      if(target.from==='property'){
        if(!String(target.property||'').trim()||target.property===api)out.push(`参数「${token}」绑定的属性无效`)
        else if(!(target.property in (b?.properties||{})))out.push(`参数「${token}」绑定的属性尚未配置字段来源`)
        continue
      }
      out.push(`参数「${token}」绑定配置无效`)
    }
    return out
  }
  if(view.kind==='field'){
    if(!String(view.field||'').trim())out.push('未填写字段名')
    if(view.source&&!sourceById(b,view.source))out.push('属性引用的数据来源不存在')
    if(view.selection==='latest'&&!String(view.timeField||'').trim())out.push('取最新记录必须指定排序时间字段')
    return out
  }
  if(view.kind==='computed'){
    // 内联 SQL 的连接/参数校验需要项目级上下文（连接表、项目参数），由 PropertySources 的
    // extraIssuesOf 调 inlineSql.ts 完成；这里只防结构层误报。
    const m=(view as {mode?:string}).mode
    if(m==='inline'||m==='inlineSql'||m==='function')return out
    if(!String(view.implementation||'').trim())out.push('未选择计算实现')
    if(!String(view.output||'').trim())out.push('未选择输出')
    return out
  }
  return out
}

import {sqlTemplateErrors} from './sqlTemplates'
import {sqlStepsRuleErrors} from './sqlSteps'
// 项目多步取值规则。只描述查询，不执行 SQL，不依赖本体函数契约。
export const isQueryRule=(v:any)=>v?.kind==='queryRule'
export function socRule(){return {
 id:crypto.randomUUID(),kind:'queryRule',schemaVersion:1,name:'查询储能簇 SOC 采样序列',objectType:'',connection:'',
 steps:[
  {id:'point',name:'定位测点',table:'s_attr_scada',cardinality:'one',where:[{field:'model_name',op:'eq',value:'m_storage_cluster_phase'},{field:'attr_name',op:'eq',value:'soc'},{field:'model_id',op:'eq',value:'{{inputs.instanceId}}'}],select:[{as:'scadaTable',field:'scada_table'},{as:'scadaId',field:'scada_id'}]},
  {id:'storage',name:'定位采样存储',table:'{{steps.point.scadaTable}}',cardinality:'one',where:[{field:'id',op:'eq',value:'{{steps.point.scadaId}}'}],select:[{as:'sampleTable',field:'sample_table_name'},{as:'sampleField',field:'sample_field_name'}]},
  {id:'samples',name:'读取采样记录',table:'{{steps.storage.sampleTable}}',cardinality:'many',where:[{field:'record_time',op:'gte',value:'{{inputs.startTime}}'},{field:'record_time',op:'lt',value:'{{inputs.endTime}}'}],select:[{as:'timestamp',field:'record_time'},{as:'value',field:'{{steps.storage.sampleField}}'}]}
 ],result:{type:'timeSeries',valueType:'double',step:'samples',timestamp:'timestamp',value:'value',order:'ascending'},
 policies:{lookupMissing:'error',lookupMultiple:'error',emptySeries:'empty',nullValue:'preserve',identifiers:'connectionCatalog',timezone:'project',range:'[startTime,endTime)'}
}}
export function queryRuleErrors(rule:any):string[]{
 if(rule.schemaVersion===4)return sqlStepsRuleErrors(rule)
 if(rule.schemaVersion===3&&rule.mode==='sqlTemplate')return sqlTemplateErrors(rule)
 if(rule.schemaVersion===3)return structuredRuleErrors(rule)
 const errors:string[]=[]
 if(!rule.name?.trim())errors.push('请填写规则名称')
 if(rule.schemaVersion!==2&&!rule.objectType)errors.push('请选择适用对象')
 if(!rule.connection)errors.push('请选择数据连接')
 const steps=rule.steps||[],known=new Map<string,Set<string>>()
 let instance=false,start=false,end=false
 const usedInputs=new Set<string>()
 const check=(v:any,label:string,identifier=false)=>{
  if(typeof v!=='string'||!v.trim()){errors.push(label+'未填写');return}
  const m=v.match(/^\{\{(inputs\.(instanceId|model_id|model_name|attr_name|startTime|endTime)|steps\.([\w]+)\.([\w]+))\}\}$/)
  if(m){if(m[2]){if(identifier)errors.push(label+'不能使用调用参数作为表名或字段名');else{usedInputs.add(m[2]);instance ||= m[2]==='instanceId'||m[2]==='model_id';start ||= m[2]==='startTime';end ||= m[2]==='endTime'}}else if(!known.get(m[3])?.has(m[4]))errors.push(label+'引用了不存在或尚未执行的步骤输出')}
  else if(v.includes('{{')||v.includes('}}'))errors.push(label+'引用格式无效')
  else if(identifier&&!/^[A-Za-z_][A-Za-z0-9_]*$/.test(v))errors.push(label+'请填写合法表名或字段名')
 }
 if(!steps.length)errors.push('至少需要一个查询步骤')
 steps.forEach((s:any,index:number)=>{
  const title=`步骤 ${index+1}：`
  if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(s.id)||known.has(s.id))errors.push(title+'步骤标识无效或重复')
  check(s.table,title+'来源表',true)
  if(!s.name?.trim())errors.push(title+'请填写步骤名称')
  if(s.cardinality!==(index===steps.length-1?'many':'one'))errors.push(title+'中间步骤须唯一记录，末步须多条采样记录')
  if(!s.where?.length)errors.push(title+'至少填写一个查询条件')
  for(const w of s.where||[]){check(w.field,title+'条件字段',true);check(w.value,title+'条件值');if(!['eq','gte','lt'].includes(w.op))errors.push(title+'条件运算符无效')}
  const aliases=new Set<string>()
  if(!s.select?.length)errors.push(title+'至少提取一个字段')
  for(const f of s.select||[]){check(f.field,title+'提取字段',true);if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(f.as)||aliases.has(f.as))errors.push(title+'输出名称无效或重复');aliases.add(f.as)}
  known.set(s.id,aliases)
 })
 if(rule.schemaVersion===2&&!['model_name','attr_name','model_id'].every(k=>usedInputs.has(k)))errors.push('通用规则须引用 model_name、attr_name、model_id 三个入参')
 if(!instance)errors.push('查询条件须绑定当前实例主键')
 if(!start||!end)errors.push('查询条件须使用开始时间和结束时间')
 const r=rule.result||{},last=steps.at(-1)
 if(r.type!=='timeSeries'||r.valueType!=='double')errors.push('当前取值规则输出须为数值时间序列')
 if(!last||r.step!==last.id||!known.get(r.step)?.has(r.timestamp)||!known.get(r.step)?.has(r.value)||r.timestamp===r.value)errors.push('结果须选择最后一步中不同的时间、数值输出字段')
 if(r.order!=='ascending')errors.push('结果须按时间升序返回')
 const timeField=last?.select?.find((f:any)=>f.as===r.timestamp)?.field
 for(const [param,op] of [['startTime','gte'],['endTime','lt']])if(!(last?.where||[]).some((w:any)=>w.field===timeField&&w.op===op&&w.value==='{{inputs.'+param+'}}')){errors.push('最后一步须按输出时间字段配置开始时间包含、结束时间不包含的过滤条件');break}
 return errors
}

// 固定 SCADA 查询流程的简表适配：仅改变业务参数，保留原有步骤及稳定标识。
// 非此模板的历史规则仍使用原编辑器，避免简化时覆盖自定义条件。
export function scadaFields(rule:any):{modelName:string;attrName:string;modelId:string;timeField:string}|null {
 if(!rule?.steps||rule.steps.length!==3)return null
 const [point,storage,samples]=rule.steps
 const modelName=point.where?.find((w:any)=>w.field==='model_name')?.value
 const attrName=point.where?.find((w:any)=>w.field==='attr_name')?.value
 const modelId=point.where?.find((w:any)=>w.field==='model_id')?.value
 const timeField=samples.select?.find((f:any)=>f.as==='timestamp')?.field
 if(![modelName,attrName,modelId,timeField].every(v=>typeof v==='string'))return null
 const expected=socRule()
 expected.steps[0].where[0].value=modelName;expected.steps[0].where[1].value=attrName
 expected.steps[0].where[2].value=modelId
 expected.steps[2].select[0].field=timeField
 expected.steps[2].where.forEach(w=>w.field=timeField)
 expected.steps.forEach((s,i)=>s.name=rule.steps[i].name)
 const stable=(v:any):string=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x)
 if(stable(expected.steps)!==stable(rule.steps)||stable(expected.result)!==stable(rule.result))return null
 return {modelName,attrName,modelId:modelId==='{{inputs.instanceId}}'?'{id}':modelId,timeField}
}
export function updateScadaField(rule:any,field:'modelName'|'attrName'|'modelId'|'timeField',value:string){
 if(field==='timeField'){
  rule.steps[2].select.find((f:any)=>f.as==='timestamp').field=value
  rule.steps[2].where.forEach((w:any)=>{w.field=value})
 }else{
  const key={modelName:'model_name',attrName:'attr_name',modelId:'model_id'}[field]
  rule.steps[0].where.find((w:any)=>w.field===key).value=field==='modelId'&&value==='{id}'?'{{inputs.instanceId}}':value
 }
}

export const isReusableRule=(rule:any)=>isQueryRule(rule)&&[2,3,4].includes(rule.schemaVersion)
export function reusableScadaRule(){
 const rule:any=socRule();rule.schemaVersion=2;rule.name='采样值取值规则';delete rule.objectType
 rule.steps[0].where[0].value='{{inputs.model_name}}'
 rule.steps[0].where[1].value='{{inputs.attr_name}}'
 rule.steps[0].where[2].value='{{inputs.model_id}}'
 return rule
}
export function ruleInputErrors(inputs:any,binding:any,rule?:any):string[]{
 if(rule&&[3,4].includes(rule.schemaVersion))return (rule.inputs||[]).filter((p:any)=>p.source!=='runtime').flatMap((p:any)=>!String(inputs?.[p.name]??'').trim()?[`请填写输入参数 ${p.name}`]:[])
 const out:string[]=[]
 if(!inputs||typeof inputs!=='object'||Array.isArray(inputs))return ['请填写取值规则的输入参数']
 if(typeof inputs.model_name!=='string'||!inputs.model_name.trim())out.push('请填写 model_name（对象表名）')
 if(typeof inputs.attr_name!=='string'||!inputs.attr_name.trim())out.push('请填写 attr_name（属性名）')
 if(inputs.model_id!=='{id}')out.push('model_id 请填写 {id}，使用当前实例主键')
 if(!binding?.primary_key)out.push('请先配置当前对象的实例主键')
 return out
}

export function migrateScadaRule(source:any):{rule:any;inputs:Record<string,string>|null}{
 const rule=JSON.parse(JSON.stringify(source)),fields=scadaFields(rule)
 if(rule.schemaVersion!==1||!fields||fields.modelId!=='{id}')return {rule,inputs:null}
 rule.schemaVersion=2;delete rule.objectType
 for(const w of rule.steps[0].where)w.value='{{inputs.'+w.field+'}}'
 return {rule,inputs:{model_name:fields.modelName,attr_name:fields.attrName,model_id:'{id}'}}
}

export function editableRule(source?:any):any {
 const rule=source?JSON.parse(JSON.stringify(source)):reusableScadaRule()
 if(rule.schemaVersion!==3){rule.inputs=[
 {name:'model_name',type:'string',description:'模型表名',source:'binding'},
 {name:'attr_name',type:'string',description:'属性名',source:'binding'},
 {name:'model_id',type:'double',description:'当前实例主键，绑定时填写 {id}',source:'binding'},
 {name:'startTime',type:'dateTime',description:'查询开始时间',source:'runtime'},
 {name:'endTime',type:'dateTime',description:'查询结束时间',source:'runtime'}];rule.schemaVersion=3}
 if(JSON.stringify(rule.steps||[]).includes('{{inputs.instanceId}}')&&!rule.inputs.some((p:any)=>p.name==='instanceId'))rule.inputs.push({name:'instanceId',type:'string',description:'当前实例主键',source:'binding'})
 return rule
}
export function blankRule(){const r=editableRule();r.name='';r.inputs=[];r.steps=[{id:'query',name:'查询记录',table:'',cardinality:'one',where:[],select:[{as:'value',field:''}]}];r.result={type:'scalar',valueType:'double',step:'query',value:'value'};return r}
export function renameRuleOutput(rule:any,step:any,old:string,next:string){
 if(!next||old===next)return
 const from=`{{steps.${step.id}.${old}}}`,to=`{{steps.${step.id}.${next}}}`
 for(const s of rule.steps){if(s.table===from)s.table=to;for(const w of s.where){if(w.field===from)w.field=to;if(w.value===from)w.value=to}for(const f of s.select)if(f.field===from)f.field=to}
 if(rule.result.step===step.id)for(const k of ['value','timestamp'])if(rule.result[k]===old)rule.result[k]=next
}
export function structuredRuleErrors(rule:any):string[]{
 const out:string[]=[],known=new Map<string,Set<string>>(),names=new Set<string>()
 const ident=(v:any)=>typeof v==='string'&&/^[A-Za-z_][A-Za-z0-9_]*$/.test(v)
 if(!rule.name?.trim())out.push('请填写规则名称')
 if(!rule.connection)out.push('请选择数据连接')
 for(const p of rule.inputs||[]){if(!ident(p.name)||names.has(p.name))out.push('输入参数名称无效或重复');names.add(p.name);if(!['string','double','boolean','dateTime'].includes(p.type))out.push('输入参数数据类型无效');if(!['binding','runtime'].includes(p.source))out.push('输入参数传入方式无效')}
 const check=(v:any,label:string,id=false)=>{if(typeof v!=='string'||!v.trim()){out.push(label+'未填写');return}const m=v.match(/^\{\{(inputs\.(\w+)|steps\.(\w+)\.(\w+))\}\}$/);if(m){if(m[2]){if(id||!names.has(m[2]))out.push(label+'引用了无效输入参数')}else if(!known.get(m[3])?.has(m[4]))out.push(label+'引用了不存在、返回多条或尚未执行的步骤输出')}else if(v.includes('{{')||v.includes('}}')||(id&&!ident(v)))out.push(label+'格式无效')}
 if(!rule.steps?.length)out.push('至少需要一个查询步骤')
 for(const [i,s] of (rule.steps||[]).entries()){const t=`步骤 ${i+1}：`;if(!ident(s.id)||known.has(s.id))out.push(t+'步骤标识无效或重复');if(!s.name?.trim())out.push(t+'请填写步骤名称');check(s.table,t+'来源表',true);if(!['one','many'].includes(s.cardinality))out.push(t+'预期记录数无效');if(!s.where?.length)out.push(t+'至少填写一个查询条件');for(const w of s.where||[]){check(w.field,t+'条件字段',true);check(w.value,t+'条件值');if(!['eq','ne','gt','gte','lt','lte'].includes(w.op))out.push(t+'条件运算符无效')};const aliases=new Set<string>();if(!s.select?.length)out.push(t+'至少提取一个字段');for(const f of s.select||[]){check(f.field,t+'提取字段',true);if(!ident(f.as)||aliases.has(f.as))out.push(t+'输出名称无效或重复');aliases.add(f.as)};known.set(s.id,s.cardinality==='one'?aliases:new Set());for(const sort of s.orderBy||[]){check(sort.field,t+'排序字段',true);if(!['ascending','descending'].includes(sort.direction))out.push(t+'排序方向无效')}}
 const r=rule.result||{},last=rule.steps?.at(-1),aliases=(last?.select||[]).map((f:any)=>f.as)
 if(!['scalar','timeSeries'].includes(r.type)||!['string','double','boolean','dateTime'].includes(r.valueType))out.push('返回类型无效')
 if(!last||r.step!==last.id||!aliases.includes(r.value))out.push('返回值须选择最后一步的输出字段')
 if(r.type==='scalar'&&last?.cardinality!=='one')out.push('单值输出的最后一步须返回一条记录')
 if(r.type==='timeSeries'&&(!aliases.includes(r.timestamp)||r.timestamp===r.value||last?.cardinality!=='many'||r.order!=='ascending'))out.push('时间序列须返回多条记录，选择不同的时间和值字段，并按时间升序')
 return [...new Set(out)]
}

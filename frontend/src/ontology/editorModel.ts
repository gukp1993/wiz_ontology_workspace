// Business-facing categories; preserve existing precise storage types on edit.
export const typeOptions=[{value:'xsd:string',label:'文本'},{value:'xsd:double',label:'数值'},{value:'xsd:boolean',label:'是／否'},{value:'xsd:dateTime',label:'时间'}]
export const businessType=(id:string)=>({'xsd:decimal':'xsd:double','xsd:integer':'xsd:double','xsd:date':'xsd:dateTime'}[id]||id)
export const typeOptionsFor=(id:string)=>[...typeOptions.map(option=>({...option,value:option.value===businessType(id)?id:option.value})),...(['xsd:array','xsd:struct'].includes(id)?[{value:id,label:id==='xsd:array'?'数组':'结构体'}]:[])]
export const dataTypeOptionsFor=(id:string)=>[...typeOptionsFor(id).filter(o=>!['xsd:array','xsd:struct'].includes(o.value)),{value:'xsd:array',label:'数组'},{value:'xsd:struct',label:'结构体'}]
export const shortType=id=>({'xsd:array':'数组','xsd:struct':'结构体'}[id]||typeOptions.find(t=>t.value===businessType(id))?.label||id)
export const textSet=(obj,key,value)=>{obj[key]=value}
export function business(record){return record?.['mg:business']?.['@value']||{}}
export function setBusiness(record,key,value){record['mg:business']??={'@type':'@json','@value':{}};record['mg:business']['@value'][key]=value}
// Inspect only live schema references; provenance text and imported source candidates are not dependencies.
// 20260920 需求 13：结构化返回（名称+原因），供列表/图谱/资产库统一展示；graphReferences 保留字符串形态兼容旧调用。
export interface ReferenceEntry {
  name: string
  reason: string
  /** 依赖来源类别（用于「去处理」定位）：契约/接口/动作/规则/项目映射/图内定义 */
  sourceKind?: 'contract' | 'interface' | 'action' | 'rule' | 'mapping' | 'definition'
  /** 来源稳定 id（契约/接口/动作/规则的工作流 id；映射用对象类型 id） */
  sourceId?: string
  /** 图内定义依赖时：依赖方的定义 id（对象/属性/链接），用于跳对象建模 */
  definitionId?: string
}
export function graphReferenceEntries(state,id): ReferenceEntry[]{
 const graph=state.ontology?.['@graph']||[],refs=new Map<string,ReferenceEntry>()
 const target=graph.find(n=>n['@id']===id),short=id.replace(/^mg:/,'')
 const ref=value=>typeof value==='string'?value:value?.['@id']
 const matches=value=>!!ref(value)&&[id,short].includes(ref(value))
 const list=value=>Array.isArray(value)?value:[]
 const add=(name,reason,tag?)=>{const key=name+'（'+reason+'）';if(!refs.has(key))refs.set(key,{name,reason,...(tag||{})})}
 for(const n of graph){
  if(n['@id']===id)continue
  const inGraph={sourceKind:'definition' as const,definitionId:n['@id']}
  const source=n['mg:valueSource']?.['@value']
  if(source?.kind==='function'&&matches(source.functionId))add(n['rdfs:label']||n['@id'],'属性取值函数',inGraph)
  const constraint=n['mg:constraint']?.['@value']
  if(matches(constraint?.elementValueType)||list(constraint?.fields).some(f=>matches(f.valueType)))add(n['rdfs:label']||n['@id'],'嵌套值类型',inGraph)
  for(const [key,label] of Object.entries({'rdfs:domain':'所属对象／链接起点','rdfs:range':'链接终点／数据类型','mg:sharedProperty':'共享属性','mg:valueType':'值类型'}))
   if(matches(n[key]))add(n['rdfs:label']||n['@id'],label,inGraph)
 }
 const WORKFLOW_KIND={functions:'contract',actions:'action',interfaces:'interface'} as const
 for(const kind of ['functions','actions','interfaces'])for(const n of list(state.workflow?.[kind])){
  if(n.id===id)continue
  const label=n.name||n.id
  const tag={sourceKind:WORKFLOW_KIND[kind],sourceId:n.id}
  if(list(n.object_types).some(matches))add(label,'适用对象类型',tag)
  for(const step of list(n.steps))if(matches(step.function_ref))add(label,'函数调用步骤',tag)
  for(const binding of list(n.property_bindings))if(matches(binding.object_type)||matches(binding.property_ref))add(label,'函数属性绑定',tag)
  for(const [key,reason] of Object.entries({object_type:'目标对象',output_property:'输出属性',relation_ref:'操作链接',function_ref:'调用计算',interface_ref:'接口引用'}))if(matches(n[key]))add(label,reason,tag)
  if(list(n.properties).some(matches))add(label,'接口要求属性',tag)
  if(list(n.implementations).some(matches))add(label,'接口实现对象',tag)
  for(const input of list(n.inputs))for(const key of ['object_type','property_ref','value_type','value_type_ref','interface_ref'])if(matches(input[key]))add(label,`参数 ${input.name||''}`,tag)
  // V3 契约签名（guide_version 3）：inputs/outputs 的 ref:{kind,id} 直接指向对象/属性/值类型——
  // 共享定义被接口/契约引用时同样是当前草稿依赖（20260920 需求 13 / A13）。
  for(const slot of ['inputs','outputs'])for(const item of list(n[slot])){
   const kind=item?.ref?.kind,rid=item?.ref?.id
   if(kind==='property'&&matches(rid))add(label,`${slot==='outputs'?'输出':'输入'} ${item.name||''}（引用属性）`.trim(),tag)
   else if(kind==='object'&&matches(rid))add(label,`${slot==='outputs'?'输出':'输入'} ${item.name||''}（引用对象）`.trim(),tag)
   else if(kind==='base'&&matches(rid))add(label,`${slot==='outputs'?'输出':'输入'} ${item.name||''}（引用值类型）`.trim(),tag)
  }
  // Removing the last local shared-property reference would break an implemented interface.
  if(kind==='interfaces'&&target?.['@type']==='owl:DatatypeProperty'&&list(n.implementations).some(t=>ref(t)?.replace(/^mg:/,'')===ref(target['rdfs:domain'])?.replace(/^mg:/,''))) {
   const shared=ref(target['mg:sharedProperty'])
   if(shared&&list(n.properties).some(p=>ref(p)===shared)&&!graph.some(p=>p['@id']!==id&&ref(p['rdfs:domain'])===ref(target['rdfs:domain'])&&ref(p['mg:sharedProperty'])===shared))add(label,'实现对象必需属性',tag)
  }
 }
 const mapped=(owner,key)=>target?.['@type']==='owl:DatatypeProperty'&&ref(target['rdfs:domain'])?.replace(/^mg:/,'')===ref(owner)?.replace(/^mg:/,'')&&[id,target['mg:apiName']??short].filter(Boolean).includes(key)
 for(const b of list(state.bindings?.object_bindings)){
  const label='项目映射：'+(b.table||b.object_type)
  const tag={sourceKind:'mapping' as const,sourceId:String(b.object_type||'')}
  if(matches(b.object_type))add(label,'对象类型',tag)
  for(const key of Object.keys(b.properties||{}))if(mapped(b.object_type,key))add(label,`属性 ${key}`,tag)
  for(const r of list(b.relations))if(matches(r.target_type)||matches(r.relation))add(label,'关联映射',tag)
 }
 const observation=state.bindings?.observation_binding
 if(observation){
  const tag={sourceKind:'mapping' as const,sourceId:String(observation.subject_type||'')}
  if(matches(observation.subject_type))add('时序观测映射','主体类型',tag)
  if(mapped(observation.subject_type,observation.property_ref))add('时序观测映射','观测属性',tag)
 }
 for(const metric of list(state.metrics?.metrics)){
  const tag={sourceKind:'rule' as const,sourceId:String(metric.rule_ref||'')}
  if(list(metric.applicable_types).some(matches))add(metric.name||metric.id,'指标适用对象',tag)
  if(matches(metric.rule_ref))add(metric.name||metric.id,'指标规则',tag)
 }
 for(const rule of list(state.rules?.rules)){
  const tag={sourceKind:'rule' as const,sourceId:String(rule.id||'')}
  if(matches(rule.member_type))add(rule.name||rule.id,'规则成员类型',tag)
  if(list(rule.membership_relations).some(matches))add(rule.name||rule.id,'规则成员关系',tag)
 }
 return [...refs.values()]
}
export function graphReferences(state,id){return graphReferenceEntries(state,id).map(r=>r.name+'（'+r.reason+'）')}

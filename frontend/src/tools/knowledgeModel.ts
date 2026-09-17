import type {WorkbenchState} from '../ontology/modelFormat'
import type {KnowledgeModel,KnowledgeNode,KnowledgeKind} from './knowledgeTypes'
import {effectiveProperty} from '../ontology/propertyModel'

/** A read-only projection of current definitions. Import provenance is never a dependency. */
export function buildKnowledgeModel(state:WorkbenchState):KnowledgeModel {
 const model:KnowledgeModel={nodes:[],edges:[],warnings:[]}
 const draftPath=state.workspaceId&&state.workspaceId!=='storage'?`ontology/workspaces/${state.workspaceId}/draft.json`:'ontology/drafts/draft.json'
 const graph=state.ontology?.['@graph']||[], workflow=state.workflow||{}
 const byId=new Map<string,KnowledgeNode>(), warn=(message:string)=>{if(!model.warnings.includes(message))model.warnings.push(message)}
 const list=(value:any):any[]=>Array.isArray(value)?value:[]
 const ref=(value:any):string=>typeof value==='string'?value:value?.['@id']||''
 const pages:Record<KnowledgeKind,string>={object:'graph',property:'properties',valueType:'value-types',function:'metrics',action:'actions',interface:'interfaces',mapping:'bindings'}
 function add(kind:KnowledgeKind,id:string,label:string,record:any,source:string,description='') {
  if(!id){warn(`${source} 存在缺少标识的定义，未加入图。`);return undefined}
  const key=`${kind}:${id}`
  if(byId.has(key)){warn(`${source} 存在重复标识 ${id}，仅展示第一项。`);return byId.get(key)}
  const node:KnowledgeNode={id:key,resourceId:id,label:label||id,kind,description,source,record,page:pages[kind]}
  byId.set(key,node);model.nodes.push(node);return node
 }
 function resolve(kind:KnowledgeKind,id:string,context:string,short=false) {
  if(!id)return undefined
  const direct=byId.get(`${kind}:${id}`)
  const found=direct||(short?byId.get(`${kind}:mg:${id}`):undefined)
  if(!found)warn(`${context} 引用的${kind==='object'?'对象类型':'定义'} ${id} 不存在，未生成依赖线。`)
  return found
 }
 function edge(source:KnowledgeNode|undefined,target:KnowledgeNode|undefined,label:string,record?:any) {
  if(!source||!target)return
  const id=JSON.stringify([source.id,target.id,label])
  if(!model.edges.some(e=>e.id===id))model.edges.push({id,source:source.id,target:target.id,label,kind:'dependency',record})
 }
 const typeKinds:Record<string,KnowledgeKind>={'owl:Class':'object','owl:DatatypeProperty':'property','mg:SharedProperty':'property'}
 for(const row of graph){const kind=typeKinds[row['@type']];if(!kind)continue
  const effective=kind==='property'?effectiveProperty(row,graph):row
  add(kind,row['@id'],effective['rdfs:label'],row,draftPath+' · ontology',effective['rdfs:comment']||'')
 }
 for(const [key,kind] of [['functions','function'],['actions','action'],['interfaces','interface']] as const)
  for(const row of list(workflow[key]))add(kind,row.id,row.name,row,`${draftPath} · workflow.${key}`,row.description||'')
 const links=new Map<string,any>()
 for(const row of graph.filter(r=>r['@type']==='owl:ObjectProperty')){
  links.set(row['@id'],row)
  const source=resolve('object',ref(row['rdfs:domain']),`链接 ${row['rdfs:label']}`),target=resolve('object',ref(row['rdfs:range']),`链接 ${row['rdfs:label']}`)
  if(source&&target)model.edges.push({id:`link:${row['@id']}`,source:source.id,target:target.id,label:row['rdfs:label']||row['@id'],kind:'link',record:row})
  else if(!ref(row['rdfs:domain'])||!ref(row['rdfs:range']))warn(`链接 ${row['rdfs:label']||row['@id']} 未配置完整起点与终点。`)
 }
 for(const row of graph){
  const node=byId.get(`property:${row['@id']}`);if(!node)continue
  if(row['@type']==='owl:DatatypeProperty'){
   const owner=resolve('object',ref(row['rdfs:domain']),`属性 ${node.label}`)
   if(owner){const effective=effectiveProperty(row,graph);(owner.properties||=[]).push({id:row['@id'],label:node.label,type:ref(effective['rdfs:range']).replace(/^xsd:/,'')||'未配置'});edge(owner,node,'具有属性')}
   else if(!ref(row['rdfs:domain']))warn(`属性 ${node.label} 未配置所属对象类型。`)
  }
  edge(node,resolve('property',ref(row['mg:sharedProperty']),`属性 ${node.label}`),'引用共享属性')
 }
 function relationDependency(node:KnowledgeNode|undefined,id:string,label:string){
  if(!id)return
  const row=links.get(id)||links.get(`mg:${id}`)
  if(!row){warn(`${node?.label} 引用的链接类型 ${id} 不存在。`);return}
  warn(`动作 ${node?.label} 通过链接 ${row['rdfs:label']} 的起终点展示操作依赖；这不是新增业务链接。`)
  edge(node,resolve('object',ref(row['rdfs:domain']),id),`${label}：${row['rdfs:label']}（起点）`,row)
  edge(node,resolve('object',ref(row['rdfs:range']),id),`${label}：${row['rdfs:label']}（终点）`,row)
 }
 for(const [key,kind] of [['functions','function'],['actions','action']] as const)for(const row of list(workflow[key])){
  const node=byId.get(`${kind}:${row.id}`)
  for(const target of kind==='function'&&Array.isArray(row.object_types)?row.object_types:row.object_type?[row.object_type]:[])edge(node,resolve('object',target,`${row.name} 目标`,true),kind==='action'?'操作对象':'计算对象')
  for(const property of graph)if(property['mg:valueSource']?.['@value']?.kind==='function'&&property['mg:valueSource']['@value'].functionId===row.id)edge(node,resolve('property',property['@id'],`${row.name} 属性来源`,true),'提供属性值')
  for(const step of list(row.steps))if(step.function_ref)edge(node,resolve('function',step.function_ref,`${row.name} 调用`,true),'调用函数')
  for(const binding of list(row.property_bindings))edge(node,resolve('property',binding.property_ref,`${row.name} 属性绑定`,true),'提供属性值')
  for(const input of list(row.inputs)){
   edge(resolve('object',input.object_type,`${row.name} 参数 ${input.name}`,true),node,`输入：${input.name}`)
   edge(resolve('property',input.property_ref,`${row.name} 参数 ${input.name}`),node,`输入属性：${input.name}`)
  }
  edge(node,resolve('property',row.output_property,`${row.name} 输出`),'结果对应属性（不自动写回）')
  edge(node,resolve('function',row.function_ref,`${row.name} 调用`),'调用计算')
  relationDependency(node,row.relation_ref,'操作链接')
 }
 for(const row of list(workflow.interfaces)){
  const node=byId.get(`interface:${row.id}`)
  for(const id of list(row.properties))edge(node,resolve('property',ref(id),`${row.name} 契约属性`),'要求属性')
  for(const id of list(row.implementations))edge(resolve('object',ref(id),`${row.name} 实现`,true),node,'实现接口')
 }
 function mappedProperty(owner:KnowledgeNode|undefined,key:string,context:string){
  if(!owner)return undefined
  const candidates=graph.filter(p=>p['@type']==='owl:DatatypeProperty'&&ref(p['rdfs:domain'])===owner.resourceId&&(p['@id']===key||p['mg:apiName']===key))
  if(candidates.length===1)return byId.get(`property:${candidates[0]['@id']}`)
  warn(`${context} 字段键 ${key} ${candidates.length?'匹配多个属性':'未匹配所属对象的属性 ID / API 名称'}；仅保留映射记录，不新增本体属性。`)
 }
 for(const [index,binding] of list(state.bindings?.object_bindings).entries()){
  const owner=resolve('object',binding.object_type,`对象映射 ${binding.table||index}`,true)
  const node=add('mapping',`object_bindings.${index}`,binding.table||'未配置数据表',binding,'ontology/projects/chuangzhi/bindings.yaml · object_bindings',`${binding.connection||'未配置连接'} · ${owner?.label||binding.object_type||'未配置对象'}`)
  edge(node,owner,'提供对象数据')
  for(const [property,column] of Object.entries(binding.properties||{}))edge(node,mappedProperty(owner,property,`映射 ${binding.table}`),`字段 ${String(column)} → ${property}`,{property,column})
  for(const relation of list(binding.relations)){
   const row=links.get(relation.relation)||links.get(`mg:${relation.relation}`)
   if(!row){warn(`映射 ${binding.table} 引用的链接 ${relation.relation} 不存在。`);continue}
   const target=resolve('object',relation.target_type,`映射 ${binding.table} 关联目标`,true)
   if(owner?.resourceId!==ref(row['rdfs:domain'])||target?.resourceId!==ref(row['rdfs:range']))warn(`映射 ${binding.table} 的 ${relation.relation} 起终点与链接定义不一致。`)
   else edge(node,target,`字段 ${relation.column} → ${row['rdfs:label']}`,relation)
  }
 }
 const observation=state.bindings?.observation_binding
 if(observation&&Object.keys(observation).length){
  const node=add('mapping','observation_binding',observation.table||'未配置观测表',observation,'ontology/projects/chuangzhi/bindings.yaml · observation_binding','时序观测映射')
  const owner=resolve('object',observation.subject_type,'观测映射主体',true)
  edge(node,owner,'提供对象观测')
  if(observation.property_ref)edge(node,mappedProperty(owner,observation.property_ref,'观测映射'),'提供属性观测')
  else warn('观测映射未声明属性 ID / API 名称：展示主体和字段配置，不根据表名或数值字段推断属性连线。')
 }
 return model
}

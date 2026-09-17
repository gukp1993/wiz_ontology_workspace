// Local JSON protocol. This is not the proprietary Foundry ontology export schema.
export type ModelRecord = Record<string, any>
export interface LegacyOntology { '@context': ModelRecord; '@graph': ModelRecord[]; [key:string]: any }
export interface OntologyJson {
  schemaVersion: 1
  namespaces: ModelRecord
  objectTypes: ModelRecord[]
  linkTypes: ModelRecord[]
  properties: ModelRecord[]
  sharedProperties: ModelRecord[]
  valueTypes: ModelRecord[]
  metadata: ModelRecord[]
  definitionOrder: string[]
  extensions?: ModelRecord
}
export interface WorkbenchState {
  ontology: LegacyOntology
  workflow: ModelRecord
  [key:string]: any
}
export interface ProjectState {
  projectId: string
  name: string
  ontologyId: string
  ontologyVersion: string
  connections: ModelRecord
  bindings: ModelRecord
  implementations: ModelRecord[]
  parameters: ModelRecord
  projectMeta?: ModelRecord
  [key:string]: any
}
const groups={objectTypes:'owl:Class',linkTypes:'owl:ObjectProperty',properties:'owl:DatatypeProperty',sharedProperties:'mg:SharedProperty',valueTypes:'mg:ValueType'}
const fields={id:'@id',displayName:'rdfs:label',description:'rdfs:comment',aliases:'mg:aliases',apiName:'mg:apiName',reverseDisplayName:'mg:reverseLabel',cardinality:'mg:cardinality',visibility:'mg:visibility',valueSuffix:'mg:valueSuffix',decimalPlaces:'mg:decimalPlaces',sourceIds:'mg:sourceIds',reviewNote:'mg:reviewNote',valueShape:'mg:valueShape'}
const refs={sharedPropertyId:'mg:sharedProperty',valueTypeId:'mg:valueType'}
const jsonFields={constraint:'mg:constraint',business:'mg:business',formatting:'mg:formatting',valueSource:'mg:valueSource',isDisplayName:'mg:isDisplayName'}
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value))
const topFields=new Set(['schemaVersion','namespaces','metadata','definitionOrder','extensions',...Object.keys(groups)])
const recordFields=new Set(['resourceType','objectTypeId','sourceObjectTypeId','targetObjectTypeId','dataType','extensions',...Object.keys(fields),...Object.keys(refs),...Object.keys(jsonFields)])
const legacyFields=new Set(['@type','rdfs:domain','rdfs:range',...Object.values(fields),...Object.values(refs),...Object.values(jsonFields)])
function object(value:any,path:string){if(!value||typeof value!=='object'||Array.isArray(value))throw Error(path+' 必须是JSON对象')}
function unknown(value:ModelRecord,allowed:Set<string>,path:string){const extra=Object.keys(value).filter(k=>!allowed.has(k));if(extra.length)throw Error(path+' 包含未识别字段 '+extra.sort().join(', ')+'；请放入 extensions，避免保存时丢失')}
export const seriesValueTypes = ['string','double','decimal','integer','boolean','date','dateTime']
export function canonicalRecord(record:ModelRecord):ModelRecord {
  const out=clone(record),shape=out.valueShape;delete out.valueShape
  if(shape==='timeSeries')out.dataType={type:'timeSeries',valueType:out.dataType?.type||'double'}
  return out
}
function comparableLegacy(model:LegacyOntology){const out=clone(model);for(const n of out['@graph'])if(n['mg:valueShape']==='scalar')delete n['mg:valueShape'];return out}
export function validateJson(model:ModelRecord){
  object(model,'ontology');if(model.schemaVersion!==1)throw Error('不支持的本体JSON版本')
  unknown(model,topFields,'ontology');object(model.namespaces,'namespaces');object(model.extensions===undefined?{}:model.extensions,'ontology.extensions')
  if(['@context','@graph'].some(k=>k in (model.extensions||{})))throw Error('ontology.extensions 不能覆盖内部结构字段')
  const identifiers:string[]=[]
  for(const group of [...Object.keys(groups),'metadata']){
    if(!Array.isArray(model[group]))throw Error(group+' 必须是数组')
    model[group].forEach((record:ModelRecord,i:number)=>{
      const path=group+'['+i+']';object(record,path);unknown(record,recordFields,path)
      if(typeof record.id!=='string'||!record.id)throw Error(path+'.id 必须是非空文本')
      if('aliases' in record){if(group!=='objectTypes')throw Error(path+'.aliases 只适用于对象类型');if(!Array.isArray(record.aliases)||record.aliases.some(alias=>typeof alias!=='string'))throw Error(path+'.aliases 必须是字符串数组')}
      identifiers.push(record.id);object(record.extensions===undefined?{}:record.extensions,path+'.extensions')
      if(Object.keys(record.extensions||{}).some(k=>legacyFields.has(k)))throw Error(path+'.extensions 不能覆盖已建模字段')
      if(group==='metadata'&&typeof record.resourceType!=='string')throw Error(path+'.resourceType 必须是文本')
      if(group!=='metadata'&&'resourceType' in record)throw Error(path+'.resourceType 只适用于metadata')
      if('sourceObjectTypeId' in record&&'objectTypeId' in record)throw Error(path+' 不能同时指定两种起点字段')
      if('targetObjectTypeId' in record&&'dataType' in record)throw Error(path+' 不能同时指定目标对象和数据类型')
      for(const key of [...Object.keys(refs),'objectTypeId','sourceObjectTypeId','targetObjectTypeId'])if(key in record&&typeof record[key]!=='string')throw Error(path+'.'+key+' 必须是文本')
      if('dataType' in record){object(record.dataType,path+'.dataType');unknown(record.dataType,new Set(['type','valueType']),path+'.dataType');if(typeof record.dataType.type!=='string'||!record.dataType.type)throw Error(path+'.dataType.type 必须是非空文本')}
      const dtype=record.dataType||{}
      if(dtype.type==='timeSeries'){
        if(!['properties','sharedProperties'].includes(group))throw Error(path+' 时间序列只适用于属性')
        if(!seriesValueTypes.includes(dtype.valueType))throw Error(path+'.dataType.valueType 必须是有效的时间序列观测值类型')
        if('valueShape' in record)throw Error(path+' 时间序列数据类型不能同时包含旧 valueShape')
      }else if('valueType' in dtype)throw Error(path+'.dataType.valueType 只适用于时间序列')
      if('valueShape' in record){if(!['scalar','timeSeries'].includes(record.valueShape))throw Error(path+'.valueShape 必须是 scalar 或 timeSeries');if(record.valueShape==='timeSeries'&&['array','struct'].includes(record.dataType?.type))throw Error(path+'.valueShape 为时间序列时，数据类型不能是数组或结构体')}
    })
  }
  const order=model.definitionOrder
  if(!Array.isArray(order)||order.some(x=>typeof x!=='string'))throw Error('definitionOrder 必须是标识数组')
  if(new Set(identifiers).size!==identifiers.length)throw Error('本体中存在重复标识')
  if(order.length!==identifiers.length||new Set(order).size!==order.length||identifiers.some(x=>!order.includes(x)))throw Error('definitionOrder 必须完整列出每个定义标识，且不得重复')
}
function sorted(value:any):any{return Array.isArray(value)?value.map(sorted):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,sorted(value[k])])):value}
export function encodeOntology(legacy:LegacyOntology):OntologyJson {
  const out:ModelRecord={schemaVersion:1,namespaces:clone(legacy['@context']),...Object.fromEntries(Object.keys(groups).map(k=>[k,[]])),metadata:[],definitionOrder:[]}
  for(const source of legacy['@graph']){
    const node=clone(source),kind=node['@type'];delete node['@type']
    const record:ModelRecord={}
    for(const [k,v] of Object.entries(fields))if(v in node){record[k]=node[v];delete node[v]}
    for(const [k,v] of Object.entries(refs))if(v in node){record[k]=node[v]['@id'];delete node[v]}
    for(const [k,v] of Object.entries(jsonFields))if(v in node){record[k]=node[v]['@value'];delete node[v]}
    if('rdfs:domain' in node){record[kind==='owl:ObjectProperty'?'sourceObjectTypeId':'objectTypeId']=node['rdfs:domain']['@id'];delete node['rdfs:domain']}
    if('rdfs:range' in node){const target=node['rdfs:range']['@id'];if(kind==='owl:ObjectProperty')record.targetObjectTypeId=target;else record.dataType={type:target.replace(/^xsd:/,'')};delete node['rdfs:range']}
    const group=Object.keys(groups).find(k=>groups[k]===kind)||'metadata'
    if(group==='metadata')record.resourceType=kind
    if(Object.keys(node).length)record.extensions=node
    out[group].push(['properties','sharedProperties'].includes(group)?canonicalRecord(record):record);out.definitionOrder.push(record.id)
  }
  const extra=Object.fromEntries(Object.entries(legacy).filter(([k])=>!['@context','@graph'].includes(k)))
  if(Object.keys(extra).length)out.extensions=clone(extra)
  if(JSON.stringify(sorted(comparableLegacy(decodeOntology(out as OntologyJson))))!==JSON.stringify(sorted(comparableLegacy(legacy))))throw Error('内部模型包含不能无损转换的字段结构，请保留原文件并补充格式适配')
  return out as OntologyJson
}
export function decodeOntology(model:OntologyJson|LegacyOntology):LegacyOntology {
  if('@graph' in model){if('schemaVersion' in model)throw Error('不能混用JSON与JSON-LD结构');return clone(model) as LegacyOntology}
  validateJson(model)
  const graph:ModelRecord[]=[]
  for(const [group,defaultType] of Object.entries({...groups,metadata:null}))for(const record of model[group]||[]){
    const node:ModelRecord={...clone(record.extensions||{}),'@type':defaultType||record.resourceType}
    for(const [k,v] of Object.entries(fields))if(k in record)node[v]=clone(record[k])
    for(const [k,v] of Object.entries(refs))if(k in record)node[v]={'@id':record[k]}
    for(const [k,v] of Object.entries(jsonFields))if(k in record)node[v]={'@type':'@json','@value':clone(record[k])}
    const domain=record.sourceObjectTypeId??record.objectTypeId
    if(domain!==undefined)node['rdfs:domain']={'@id':domain}
    if('targetObjectTypeId' in record)node['rdfs:range']={'@id':record.targetObjectTypeId}
    else if('dataType' in record){const d=record.dataType;node['rdfs:range']={'@id':'xsd:'+(d.type==='timeSeries'?d.valueType:d.type)};if(d.type==='timeSeries')node['mg:valueShape']='timeSeries'}
    graph.push(node)
  }
  const order=new Map(model.definitionOrder.map((id,i)=>[id,i]))
  graph.sort((a,b)=>(order.get(a['@id'])??order.size)-(order.get(b['@id'])??order.size))
  return {...clone(model.extensions||{}),'@context':clone(model.namespaces),'@graph':graph}
}
export function decodeState(state:ModelRecord):WorkbenchState{return {...state,ontology:decodeOntology(state.ontology)} as WorkbenchState}
export function encodeState(state:WorkbenchState){return {...state,ontology:encodeOntology(state.ontology)}}
export function requestBody(payload:ModelRecord){return JSON.stringify({...payload,...(payload.state?{state:encodeState(payload.state)}:{})})}

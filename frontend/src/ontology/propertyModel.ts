import {graphReferences} from './editorModel'
export const metadataKeys=['rdfs:label','rdfs:comment','rdfs:range','mg:visibility','mg:valueSuffix','mg:decimalPlaces','mg:valueType','mg:formatting','mg:valueShape']
export const sharedType='mg:SharedProperty'
export const propertyTypes=['string','double','decimal','integer','boolean','date','dateTime','array','struct']
export const newId=()=>`mg:p_${crypto.randomUUID().replaceAll('-','')}`
export function effectiveProperty(p,graph){return graph.find(n=>n['@id']===p['mg:sharedProperty']?.['@id']&&n['@type']===sharedType)||p}
export function isDisplayNameOf(p,graph){const merged=effectiveProperty(p,graph);return merged?.['mg:isDisplayName']?.['@value']===true}
// The old RDF representation is an adapter detail; editors use one type descriptor.
export function propertyDataType(p:any,graph:any=[]):any {
 const m=effectiveProperty(p,graph),base=String(m?.['rdfs:range']?.['@id']||'xsd:string').replace(/^xsd:/,'')
 return m?.['mg:valueShape']==='timeSeries'?{type:'timeSeries',valueType:base}:{type:base}
}
export function propertyTypeLabel(p:any,graph:any=[]):string{return dataTypeLabel(propertyDataType(p,graph))}
export function dataTypeLabel(t:any):string {
 const names:any={string:'文本',double:'数值',decimal:'数值',integer:'数值',boolean:'是／否',date:'日期',dateTime:'时间',timestamp:'时间',array:'数组',struct:'结构体'}
 return t?.type==='timeSeries'?'时间序列（'+(names[t.valueType]||t.valueType||'未指定')+'）':names[t?.type]||t?.type||'未指定类型'
}
export function setPropertyDataType(p:any,t:any){
 p['rdfs:range']={'@id':'xsd:'+(t.type==='timeSeries'?t.valueType:t.type)}
 delete p['mg:valueShape'];if(t.type==='timeSeries')p['mg:valueShape']='timeSeries'
}
export function signatureDataType(ref:any,graph:any[],declaration?:any):any {
 let t:any=null
 if(ref?.kind==='property'){const p=graph.find(n=>n['@id']===ref.id);if(p)t=propertyDataType(p,graph)}
 else if(ref?.kind==='base')t=ref.dataType==='timeSeries'?{type:'timeSeries',valueType:ref.valueType}:{type:ref.dataType}
 if(['scalar','timeSeries'].includes(declaration?.valueShape)){t=t||{type:'unknown'};const base=t.type==='timeSeries'?t.valueType:t.type;t=declaration.valueShape==='timeSeries'?{type:'timeSeries',valueType:base}:{type:base}}
 return t
}
// Compatibility for the existing database/Redis source discriminants, never user-editable.
export function valueShapeOf(p:any,graph:any):'scalar'|'timeSeries'{return propertyDataType(p,graph).type==='timeSeries'?'timeSeries':'scalar'}

export function setTitleFlag(p,graph,set){const domain=p['rdfs:domain']?.['@id'];for(const n of graph){if(n['@type']!=='owl:DatatypeProperty'||n['rdfs:domain']?.['@id']!==domain)continue;if(n['@id']===p['@id']){if(set)n['mg:isDisplayName']={'@type':'@json','@value':true};else delete n['mg:isDisplayName']}else if(set&&n['mg:isDisplayName']?.['@value'])delete n['mg:isDisplayName']}}
export function localProperties(graph,type){return graph.filter(n=>n['@type']==='owl:DatatypeProperty'&&n['rdfs:domain']?.['@id']===type)}
export function attachProperty(p,shared){
 if(p['rdfs:range']&&p['rdfs:range']['@id']!==shared['rdfs:range']['@id'])throw Error('基础类型不同，不能关联这个共享属性')
 for(const k of metadataKeys)delete p[k]
 p['mg:sharedProperty']={'@id':shared['@id']}
}
export function detachProperty(p,graph){const shared=effectiveProperty(p,graph);for(const k of metadataKeys)if(k in shared)p[k]=JSON.parse(JSON.stringify(shared[k]));delete p['mg:sharedProperty']}
export function parsePropertyRows(text){
 const rows=[];let row=[],cell='',quoted=false
 for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'&&(quoted||!cell)){if(quoted&&text[i+1]==='"'){cell+='"';i++}else quoted=!quoted}else if(!quoted&&(c==='\t'||c==='\n')){row.push(cell.trim());cell='';if(c==='\n'){rows.push(row);row=[]}}else if(c!=='\r')cell+=c}
 if(quoted)throw Error('粘贴内容的引号未闭合')
 row.push(cell.trim());rows.push(row)
 const map={'十进制数':'decimal','精确小数':'decimal','是／否':'boolean','数值':'double','文本':'string','整数':'integer','是/否':'boolean','布尔':'boolean','时间':'dateTime','日期':'date','日期时间':'dateTime','数组':'array','结构体':'struct'}
 return rows.filter(r=>r.some(Boolean)).filter((r,i)=>!(i===0&&['属性名称','名称'].includes(r[0])&&['业务描述','描述'].includes(r[1]))).map((r,i)=>{
  if(r.length>4)throw Error(`第${i+1}行超过4列：名称、描述、类型、单位`)
  if(!r[0])throw Error(`第${i+1}行缺少名称`)
  const type=map[r[2]]||r[2]||'string';if(!propertyTypes.includes(type))throw Error(`第${i+1}行类型“${r[2]}”不支持`)
  return {name:r[0],description:r[1]||'',type,suffix:r[3]||''}
 })
}
export function makeProperty(row,objectType){const id=newId();return {'@id':id,'@type':objectType?'owl:DatatypeProperty':sharedType,'rdfs:label':row.name,'rdfs:comment':row.description||'','rdfs:range':{'@id':'xsd:'+(row.type||'string')},'mg:valueSuffix':row.suffix||'','mg:visibility':'normal',...(objectType?{'rdfs:domain':{'@id':objectType},'mg:apiName':id.slice(3)}:{})}}
export function asShared(p,graph){if(p['@type']===sharedType)return p;if(p['mg:sharedProperty'])return effectiveProperty(p,graph);const s={'@id':newId(),'@type':sharedType};for(const k of [...metadataKeys,'mg:sourceIds','mg:reviewNote'])if(k in p)s[k]=JSON.parse(JSON.stringify(p[k]));graph.push(s);attachProperty(p,s);return s}
export function addReference(graph,s,type){const existing=localProperties(graph,type);if(existing.some(p=>p['mg:sharedProperty']?.['@id']===s['@id']))return false;if(existing.some(p=>effectiveProperty(p,graph)['rdfs:label']===s['rdfs:label']))return false;const id=newId();graph.push({'@id':id,'@type':'owl:DatatypeProperty','mg:apiName':id.slice(3),'rdfs:domain':{'@id':type},'mg:sharedProperty':{'@id':s['@id']}});return true}
export function listShared(graph){return graph.filter(n=>n['@type']===sharedType)}
export function referencesOf(graph,id){return graph.filter(n=>n['@type']==='owl:DatatypeProperty'&&n['mg:sharedProperty']?.['@id']===id)}
// 形态一致性：目标对象已有同 label 但 valueShape/range 不同的属性时，不能引用同一份完整共享定义。
export function shapeConflict(graph,s,type){return localProperties(graph,type).find(p=>{const m=effectiveProperty(p,graph);return m['rdfs:label']===s['rdfs:label']&&(valueShapeOf(m,graph)!==valueShapeOf(s,graph)||m['rdfs:range']?.['@id']!==s['rdfs:range']?.['@id'])})}
// 复制为私有：新属性标识 + 完整元数据拷贝，无 mg:sharedProperty，此后独立维护。
export function copyAsPrivate(s,graph,type){const id=newId();const p={'@id':id,'@type':'owl:DatatypeProperty','mg:apiName':id.slice(3),'rdfs:domain':{'@id':type}};for(const k of [...metadataKeys,'mg:sourceIds','mg:reviewNote'])if(k in s)p[k]=JSON.parse(JSON.stringify(s[k]));graph.push(p);return p}
// 契约/接口/项目映射等对共享定义的直接引用（本地属性经 mg:sharedProperty 的引用除外）——删除定义前必须先处理。
export function externalReferencesOf(state,id){return graphReferences(state,id).filter(r=>!r.endsWith('（共享属性）'))}

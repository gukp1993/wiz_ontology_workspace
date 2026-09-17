// 对已保存的 schema 形态做只读比较；稳定标识匹配属性，不受 JSON 键顺序影响。
export function canonical(value:any):string {
  function sorted(v:any):any {
    if(Array.isArray(v))return v.map(sorted)
    if(v&&typeof v==='object')return Object.fromEntries(Object.keys(v).sort().map(k=>[k,sorted(v[k])]))
    return v
  }
  return JSON.stringify(sorted(value))
}
export function definitionSignature(state:any):string {
  const ontology={...(state?.ontology||{})}
  delete ontology.definitionOrder
  for(const k of ['objectTypes','linkTypes','properties','sharedProperties','valueTypes'])
    ontology[k]=[...(ontology[k]||[])].sort((a,b)=>String(a.id).localeCompare(String(b.id)))
  return canonical({ontology,workflow:state?.workflow||{}})
}
export function propertyDifferences(current:any,draft:any,objectType:string) {
  const oid=objectType.startsWith('mg:')?objectType:'mg:'+objectType
  function propsOf(s:any):Map<string,any> {
    const o=s?.ontology||{},shared=new Map<string,any>((o.sharedProperties||[]).map((p:any)=>[p.id,p]))
    return new Map((o.properties||[]).filter((p:any)=>p.objectTypeId===oid).map((p:any)=>[p.id,{...(shared.get(p.sharedPropertyId)||{}),...p}]))
  }
  const before=propsOf(current),after=propsOf(draft),rows:{id:string;name:string;change:string}[]=[]
  for(const [id,p] of after){const old=before.get(id);if(!old||canonical(old)!==canonical(p))rows.push({id,name:p.displayName||id,change:old?'定义已修改':'草稿新增'})}
  for(const [id,p] of before)if(!after.has(id))rows.push({id,name:p.displayName||id,change:'草稿已移除'})
  return rows
}

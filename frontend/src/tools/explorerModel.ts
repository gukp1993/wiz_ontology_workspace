import type {ExplorerObject} from './explorerTypes'
export interface ExplorerFilter { query:string; type:string; property:string; operator:string; value:string }
export function matchesObject(o:ExplorerObject,f:ExplorerFilter){
  if(f.type&&o.type!==f.type)return false
  if(f.query.trim()&&!JSON.stringify([o.id,o.name,o.typeName,o.properties]).toLocaleLowerCase().includes(f.query.trim().toLocaleLowerCase()))return false
  if(!f.property)return true
  const value=o.properties[f.property],missing=value===undefined||value===null||value===''
  if(f.operator==='missing')return missing
  if(f.operator==='present')return !missing
  if(missing)return false
  if(f.operator==='contains')return String(value).toLocaleLowerCase().includes(f.value.toLocaleLowerCase())
  if(f.operator==='eq')return String(value)===f.value
  if(typeof value!=='number'||!f.value.trim()||!Number.isFinite(Number(f.value)))return false
  return f.operator==='gte'?value>=Number(f.value):f.operator==='lte'?value<=Number(f.value):false
}
export function coordinate(value:unknown,min:number,max:number):number|null{
  if(!['number','string'].includes(typeof value)||value===null||value===undefined||value===''||typeof value==='boolean'||(typeof value==='string'&&!value.trim()))return null
  const n=Number(value);return Number.isFinite(n)&&n>=min&&n<=max?n:null
}

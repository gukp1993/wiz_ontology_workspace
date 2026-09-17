// SQL templates are stored instructions, never executed by this workbench.
export function toSqlTemplate(rule:any):string {
 if(typeof rule.sqlTemplate==='string')return rule.sqlTemplate
 const ops:Record<string,string>={eq:'=',ne:'<>',gt:'>',gte:'>=',lt:'<',lte:'<='}
 const identifier=(v:string)=>v.replace(/^\{\{steps\.(\w+)\.(\w+)\}\}$/,'{{$1.$2}}')
 const value=(v:string)=>v.startsWith('{{inputs.')?v.replace(/^\{\{inputs\.(\w+)\}\}$/,':$1'):v.startsWith('{{steps.')?v.replace(/^\{\{steps\.(\w+)\.(\w+)\}\}$/,':$1.$2'):"'"+String(v).replaceAll("'","''")+"'"
 return (rule.steps||[]).map((s:any)=>{
 const cols=s.select.map((f:any)=>`${identifier(f.field)} AS ${f.as}`).join(',\n       ')
 const conditions=s.where.map((w:any)=>`${identifier(w.field)} ${ops[w.op]||w.op} ${value(w.value)}`).join('\n  AND ')
 const sorts=s.orderBy?.length?s.orderBy:rule.result?.type==='timeSeries'&&rule.result.step===s.id?[{field:s.select.find((f:any)=>f.as===rule.result.timestamp)?.field,direction:'ascending'}]:[]
 return `-- @step ${s.id} ${s.cardinality}\n-- ${s.name}\nSELECT ${cols}\nFROM ${identifier(s.table)}${conditions?'\nWHERE '+conditions:''}${sorts.length?'\nORDER BY '+sorts.map((x:any)=>identifier(x.field)+(x.direction==='descending'?' DESC':' ASC')).join(', '):''};`
 }).join('\n\n')
}
export function sqlTemplateErrors(rule:any):string[]{
 const errors:string[]=[],names=new Set<string>(),known=new Set<string>(),seen=new Set<string>()
 if(!rule.name?.trim())errors.push('请填写规则名称')
 if(!rule.connection)errors.push('请选择数据连接')
 for(const p of rule.inputs||[]){if(!/^[A-Za-z_]\w*$/.test(p.name)||names.has(p.name))errors.push('输入参数名称无效或重复');names.add(p.name);if(!['string','double','boolean','dateTime'].includes(p.type)||!['binding','runtime'].includes(p.source))errors.push('输入参数类型或传入方式无效')}
 const sql=String(rule.sqlTemplate||'')
 const parts=sql.split(/^\s*--\s*@step\s+(\w+)\s+(one|many)\s*$/m)
 if(parts.length<4)errors.push('请用 -- @step 步骤名 one 或 many 声明查询段')
 if(parts[0].replace(/--[^\n]*/g,'').trim())errors.push('SQL 必须写在 @step 查询段内')
 let last='',cardinality=''
 for(let i=1;i+2<parts.length;i+=3){const id=parts[i],card=parts[i+1],body=parts[i+2],label=`查询段 ${id}：`
 if(seen.has(id))errors.push(label+'步骤名重复');seen.add(id)
 // Strip strings/comments for template checks only; this is not a SQL security parser.
 const code=body.replace(/'(?:''|[^'])*'|"(?:""|[^"])*"|--[^\n]*|\/\*[\s\S]*?\*\//g,' ')
 if(!/^\s*SELECT\b/i.test(code))errors.push(label+'请填写 SELECT 查询')
 if(code.split(';').filter(s=>s.trim()).length!==1)errors.push(label+'每段只填写一条查询')
 for(const m of code.matchAll(/:(\w+)(?:\.(\w+))?/g)){if(m[2]?!known.has(m[1]):!names.has(m[1]))errors.push(label+'参数或前置步骤不存在：'+m[0])}
 for(const m of code.matchAll(/\{\{([^}]+)\}\}/g))if(!/^\w+\.\w+$/.test(m[1])||!known.has(m[1].split('.')[0]))errors.push(label+'动态标识须引用前面 one 查询段的输出：'+m[0])
 if(card==='one')known.add(id)
 last=id;cardinality=card
 }
 const r=rule.result||{}
 if(!['scalar','timeSeries'].includes(r.type)||!['string','double','boolean','dateTime'].includes(r.valueType))errors.push('请选择返回类型和值类型')
 if(r.type==='scalar'&&cardinality!=='one')errors.push('单值输出的最后一段须为 one')
 if(r.type==='timeSeries'&&cardinality!=='many')errors.push('时间序列输出的最后一段须为 many')
 if(!r.value||r.type==='timeSeries'&&(!r.timestamp||r.timestamp===r.value))errors.push('请填写返回字段；时间与值字段不能相同')
 if(!last)errors.push('至少填写一段 SQL 查询')
 return [...new Set(errors)]
}

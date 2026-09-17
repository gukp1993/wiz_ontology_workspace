// 校验协议保留原始错误；展示层按记录归组，使用业务名称和中文字段提示。
export function validationGroups(errors:string[],state:any) {
  const graph=state?.ontology?.['@graph']||[]
  const workflow=state?.workflow||{}
  const kinds:Record<string,string>={'owl:Class':'对象类型','owl:ObjectProperty':'链接类型','owl:DatatypeProperty':'属性','mg:SharedProperty':'共享属性','mg:ValueType':'值类型'}
  const records:any[]=[...graph.map((n:any,i:number)=>({id:n['@id'],name:n['rdfs:label']||'未命名定义 '+(i+1),kind:kinds[n['@type']]||'本体定义'}))]
  for(const [area,kind] of [['functions','计算契约'],['actions','动作定义'],['interfaces','接口定义'],['businessRules','业务规则']])
    (workflow[area]||[]).forEach((n:any,i:number)=>{const obj=graph.find((g:any)=>g['@id']==='mg:'+String(n.object_type||'').replace(/^mg:/,''));records.push({id:n.id,name:n.name||'未命名'+kind+' '+(i+1),kind,object:obj?.['rdfs:label']})})
  const groups=new Map<string,any>()
  const fields:Record<string,string>={effect:'变更效果：执行后会修改什么，例如更改设备所属系统。',criteria:'提交条件：满足什么条件才允许执行。',permission:'权限与审批要求：谁可以提交、批准和执行。',acceptance:'验收案例：举例说明输入以及预期的变更结果。',logic:'计算口径：说明计算或查询规则。'}
  for(const raw of errors){
    const record=records.filter(r=>r.id&&raw.includes(r.id)).sort((a,b)=>b.id.length-a.id.length)[0]
    const key=record?.id||raw
    let group=groups.get(key)
    if(!group){group={id:key,title:record?record.kind+' · '+record.name:'本体定义问题',object:record?.object||'',kind:record?.kind||'',issues:[],raw:[]};groups.set(key,group)}
    let message=record?raw.split(record.id).join('').trim():raw
    if(message==='缺少名称')message='名称：尚未填写，无法识别这项定义的用途。'
    else if(message==='缺少业务描述')message='业务描述：说明这项定义用来做什么。'
    else {const match=message.match(/(?:动作|函数|计算)定义未完整[：:]\s*(\w+)$/);if(match&&fields[match[1]])message=fields[match[1]]}
    group.issues.push(message);group.raw.push(raw)
  }
  return [...groups.values()]
}

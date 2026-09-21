<script setup lang="ts">
import {computed} from 'vue'
import type {KnowledgeNode,KnowledgeEdge,KnowledgeModel} from './knowledgeTypes'
const props=defineProps<{node:KnowledgeNode|null;edge:KnowledgeEdge|null;model:KnowledgeModel}>()
const emit=defineEmits<{navigate:[page:string];select:[id:string]}>()
const kindNames:Record<string,string>={object:'对象类型',property:'属性',valueType:'值类型',function:'计算定义',action:'动作定义',interface:'接口定义',mapping:'项目数据映射'}
const fieldNames:Record<string,string>={apiName:'API 名称','mg:apiName':'API 名称',object_type:'作用对象类型',objectTypeId:'所属对象类型',dataType:'数据类型','rdfs:range':'数据类型','mg:valueType':'值类型',valueTypeId:'值类型',sharedPropertyId:'共享属性','mg:sharedProperty':'共享属性',baseType:'基础数据类型',base_type:'基础数据类型','mg:constraint':'值约束',kind:'约束方式',minInclusive:'包含下限',maxInclusive:'包含上限',constraints:'值约束',validation:'校验规则','mg:constraints':'值约束',unit:'单位',valueSuffix:'显示后缀','mg:valueSuffix':'显示后缀',cardinality:'数量关系','mg:cardinality':'数量关系',reverseDisplayName:'反向名称','mg:reverseLabel':'反向名称',inputs:'输入参数',member_scope:'成员范围',logic:'计算逻辑',missing_policy:'缺失数据处理',output_type:'输出数据类型',output_property:'输出属性',output_description:'输出说明',implementation_ref:'实现引用',status:'状态',effect:'变更规则',criteria:'提交条件',acceptance:'验收要求',function_ref:'调用计算',properties:'属性／字段映射',implementations:'实现对象类型',connection:'连接',table:'数据表',primary_key:'主键字段',relations:'关系映射',subject_type:'观测对象类型',subject_key:'对象标识字段',value_column:'观测值字段',timestamp_column:'采样时间字段',quality_column:'质量字段',source_unit:'来源单位',review_note:'核验说明',reviewNote:'核验说明','mg:reviewNote':'核验说明',description:'说明',name:'名称',type:'类型',required:'是否必填',min:'最小值',max:'最大值',minimum:'最小值',maximum:'最大值',pattern:'格式表达式',enum:'允许值',values:'允许值',minLength:'最短长度',maxLength:'最长长度',column:'来源字段',relation:'链接类型',target_type:'目标对象类型',return_type:'返回类型',nullable:'允许空值',precision:'精度',scale:'小数位数',format:'格式'}
const raw=computed(()=>props.node?.record||props.edge?.record||{})
const fields=computed(()=>Object.entries(raw.value).filter(([k,v])=>fieldNames[k]&&!['mg:valueType','valueTypeId'].includes(k)&&v!==undefined&&v!==null&&v!==''&&k!=='description'))
const related=computed(()=>props.node?props.model.edges.filter(e=>e.source===props.node!.id||e.target===props.node!.id):[])
function getNode(id:string){return props.model.nodes.find(n=>n.id===id)||props.model.nodes.find(n=>n.kind==='property'&&n.resourceId===id)}
function title(id:string){return getNode(id)?.label||id}
function display(v:any):string{
 if(v===null||v===undefined||v==='')return '未配置'
 if(typeof v==='boolean')return v?'是':'否'
 if(typeof v==='string')return ({'one-to-one':'一对一','one-to-many':'一对多','many-to-one':'多对一','many-to-many':'多对多'} as Record<string,string>)[v]||v
 if(Array.isArray(v))return v.length?v.map(display).join('\n'):'未配置'
 if(typeof v==='object'&&'@value' in v)return display(v['@value'])
 if(typeof v==='object')return v['@id']||Object.entries(v).map(([key,value])=>`${fieldNames[key]||key}：${display(value)}`).join('\n')||'未配置'
 return String(v)
}
const description=computed(()=>props.node?.description||raw.value.description||raw.value['rdfs:comment']||'尚未填写说明。')
</script>
<template>
<aside class="knowledge-details" aria-label="模型资源详情">
 <template v-if="node||edge">
  <span class="kind-tag">{{node?kindNames[node.kind]:edge?.kind==='link'?'链接类型':'模型依赖'}}</span>
  <h3>{{node?.label||edge?.label}}</h3>
  <code class="resource-id">{{node?.resourceId||edge?.id}}</code>
  <p class="definition">{{description}}</p>
  <div v-if="edge" class="edge-path"><button @click="emit('select',edge.source)">{{title(edge.source)}}</button><span>↓ {{edge.label}}</span><button @click="emit('select',edge.target)">{{title(edge.target)}}</button></div>
  <p class="explanation">{{edge?.kind==='dependency'?'这条线由当前定义中的引用生成，表示模型依赖，不是业务链接类型。':edge?'这是已维护的业务链接类型，描述两个对象类型之间的业务关系。':'这里展示当前构建成果中的类型或配置定义，不是具体设备的实例数据。'}}</p>
  <button v-if="node?.page" class="maintain" @click="emit('navigate',node.page)">前往{{kindNames[node.kind]}}维护 →</button>
  <section v-if="node?.source" class="source"><h4>当前定义来源</h4><code>{{node.source}}</code><p>使用工作台当前加载的定义（含未保存编辑）；不会读取原始导入图谱生成连线。</p></section>
  <dl v-if="fields.length" class="fields"><template v-for="[key,value] in fields" :key="key"><dt>{{fieldNames[key]}}</dt><dd>{{display(value)}}</dd></template></dl>
  <section v-if="node?.properties?.length"><h4>对象属性 · {{node.properties.length}}</h4><div v-for="p in node.properties" :key="p.id" class="property-row"><button v-if="getNode(p.id)" @click="emit('select',getNode(p.id)!.id)">{{p.label}}</button><strong v-else>{{p.label}}</strong><span>{{p.type||'未配置类型'}}</span></div></section>
  <section v-if="node"><h4>模型关联 · {{related.length}}</h4><button v-for="r in related" :key="r.id" class="related" @click="emit('select',r.source===node.id?r.target:r.source)"><span class="relation-type">{{r.kind==='link'?'业务链接':'定义依赖'}}</span><span>{{r.source===node.id?'→':'←'}} {{r.label}}</span><strong>{{title(r.source===node.id?r.target:r.source)}}</strong></button><p v-if="!related.length" class="muted">当前没有已配置的关联。缺少引用时不会根据名称猜测连线。</p></section>
  <details :key="node?.id||edge?.id" class="raw"><summary>查看结构化配置</summary><pre>{{JSON.stringify(raw,null,2)}}</pre></details>
 </template>
 <div v-else class="no-selection"><span class="empty-icon">◎</span><h3>查看模型定义</h3><p>点击节点查看业务定义、约束和配置来源；点击连线区分业务链接与模型依赖。</p><p>先从「业务模型」了解对象关系，再切换「完整定义」或「项目映射」追踪建设成果。</p></div>
</aside>
</template>
<style scoped>
.knowledge-details{background:var(--paper);border:1px solid var(--line);border-radius:var(--r-md);padding:20px;min-width:0;color:var(--ink);font-size:13px;overflow:auto;max-height:780px;box-sizing:border-box}.kind-tag{display:inline-block;padding:5px 9px;border-radius:var(--r-sm);background:var(--blue-soft);color:var(--blue-ink);font-size:12px}h3{font-size:20px;margin:12px 0 8px;overflow-wrap:anywhere}.resource-id{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.definition{line-height:1.85;white-space:pre-wrap}.explanation{background:var(--paper-2);border-left:3px solid var(--blue-line);padding:12px;color:var(--muted);font-size:12px;line-height:1.7}.maintain{padding:9px 12px;border:1px solid var(--blue-line);border-radius:var(--r-sm);background:var(--blue-soft);color:var(--blue-ink);cursor:pointer}h4{font-size:13px;margin:22px 0 10px;border-top:1px solid var(--paper-3);padding-top:16px}.source code{display:block;font-size:12px;overflow-wrap:anywhere;color:var(--muted)}.source p,.muted{font-size:12px;color:var(--muted);line-height:1.7}.fields{margin:20px 0}.fields dt{color:var(--muted);margin:16px 0 5px;font-size:12px}.fields dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8}.edge-path{display:flex;flex-direction:column;align-items:stretch;gap:9px;margin:15px 0}.edge-path span{text-align:center;color:var(--faint)}.edge-path button{padding:9px;border:1px solid var(--line);background:var(--paper-2);border-radius:var(--r-sm);color:var(--blue-ink);cursor:pointer}.related{display:flex;flex-direction:column;gap:6px;width:100%;text-align:left;background:var(--paper-2);border:1px solid var(--line);border-radius:var(--r-sm);padding:11px;margin:8px 0;color:var(--muted);font-size:12px;cursor:pointer}.related strong{color:var(--blue-ink);overflow-wrap:anywhere}.relation-type{color:var(--faint);font-size:12px}.property-row{display:flex;justify-content:space-between;align-items:start;gap:10px;border-bottom:1px solid var(--paper-3);padding:9px 0;font-size:12px}.property-row span{color:var(--faint);overflow-wrap:anywhere}.property-row button{border:0;background:none;color:var(--blue-ink);padding:0;text-align:left;cursor:pointer}.raw{margin-top:22px;font-size:12px;color:var(--muted)}.raw summary{cursor:pointer}.raw pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--paper-2);border-radius:var(--r-sm);padding:12px;line-height:1.65;max-height:330px;overflow:auto}.no-selection{padding:40px 0;color:var(--faint);line-height:1.85}.no-selection h3{color:var(--ink-2);font-size:17px}.empty-icon{font-size:32px;color:var(--faint)}button:focus-visible,summary:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
</style>

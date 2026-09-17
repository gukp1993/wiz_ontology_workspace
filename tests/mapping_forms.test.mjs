// 单页属性表单与链接缺连接回归；只使用内存夹具及临时编译目录。
// node --import ./tests/ts_hooks.mjs tests/mapping_forms.test.mjs
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFileSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join,resolve,dirname} from 'node:path'
import {pathToFileURL} from 'node:url'
const require=createRequire(new URL('../frontend/package.json',import.meta.url))
const {parse,compileScript,compileTemplate}=require('@vue/compiler-sfc')
const ts=require('typescript'),{createSSRApp,reactive,h}=require('vue'),{renderToString}=require('@vue/server-renderer')
const root=mkdtempSync(join(tmpdir(),'wiz_mapping_forms_'))
process.env.WIZ_WORKBENCH_ROOT=root;process.env.WIZ_WORKBENCH_PORT='18991'
async function loadComponent(name){
 const filename=resolve('frontend/src/project/'+name+'.vue')
 const {descriptor}=parse(readFileSync(filename,'utf8'),{filename})
 const script=compileScript(descriptor,{id:name})
 const template=compileTemplate({source:descriptor.template.content,filename,id:name,ssr:true,ssrCssVars:[],compilerOptions:{bindingMetadata:script.bindings}})
 assert.deepEqual(template.errors,[])
 let code=script.content.replace(/import AppSelect from ['"].*?['"]/,'const AppSelect = globalThis.__mappingSelect')
 code=code.replace(/import SourcePreview from ['"].*?['"]/,'const SourcePreview = globalThis.__mappingSelect')
 code=code.replace(/import QueryRuleImplementation from ['"].*?['"]/,'const QueryRuleImplementation = globalThis.__mappingSelect')
 code=code.replace(/import (RuleValue|ImplementationManager) from ['"].*?['"]/g,(_,name)=>'const '+name+' = globalThis.__mappingSelect')
 code=code.replace('export default ', 'const Component = ')+ '\n'+template.code+'\nComponent.ssrRender=ssrRender; export default Component;'
 code=code.replace(/from (['"])([^'"]+)\1/g,(_,q,spec)=>'from '+JSON.stringify(pathToFileURL(spec.startsWith('.')?resolve(dirname(filename),spec+'.ts'):require.resolve(spec)).href))
 const file=join(root,name+'.mjs');writeFileSync(file,ts.transpileModule(code,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText)
 return (await import(pathToFileURL(file).href)).default
}
// 下拉仅替换渲染外壳；被测模板、表单状态和保存逻辑全部来自实际组件。
globalThis.__mappingSelect={props:['options','modelValue','disabled'],render(){return h('select',{disabled:this.disabled},(this.options||[]).map(o=>h('option',{value:o.value,selected:o.value===this.modelValue,disabled:o.disabled},o.label)))}}
try{
 const fields=(...names)=>names.map(name=>({name,dataType:'double',key:name==='id'?'pri':'',comment:''}))
 const b=reactive({object_type:'cluster',connection:'db',table:'clusters',primary_key:'id',sources:[],properties:{},relations:[]})
 const target=reactive({object_type:'storage',connection:'',table:'storage',primary_key:'id',sources:[],properties:{},relations:[]})
 const projectState=reactive({connections:{connections:[{id:'db',name:'测试库',engine:'mysql'},{id:'redis',name:'缓存',engine:'redis'}]},bindings:{object_bindings:[b,target],catalogs:{db:{tables:[{name:'clusters',fields:fields('id','storage_id','rated_power')},{name:'samples',fields:fields('cluster_id','soc','sampled_at')},{name:'storage',fields:fields('id','name')}]}}},implementations:[{id:'impl',contractId:'fn',inputBindings:[]}],parameters:{timezone:'Asia/Shanghai',park_id:'P001'}})
 const graph=[{'@id':'mg:cluster','@type':'owl:Class','rdfs:label':'储能簇'},{'@id':'mg:storage','@type':'owl:Class','rdfs:label':'储能设备'},...[['power','xsd:double'],['soc','xsd:double'],['history','xsd:double','ts'],['note','xsd:string'],['tsnote','xsd:string','ts'],['flag','xsd:boolean'],['commissioned','xsd:dateTime']].map(([id,range,shape])=>({'@id':'mg:'+id,'@type':'owl:DatatypeProperty','rdfs:domain':{'@id':'mg:cluster'},'rdfs:range':{'@id':range},'rdfs:label':id,'mg:apiName':id,...(shape==='ts'?{'mg:valueShape':'timeSeries'}:{})})),{'@id':'mg:belongs','@type':'owl:ObjectProperty','rdfs:domain':{'@id':'mg:cluster'},'rdfs:range':{'@id':'mg:storage'},'rdfs:label':'所属设备','mg:cardinality':'many-to-one'}]
 const refState={ontology:{'@graph':graph},workflow:{functions:[{id:'fn',name:'计算功率',guide_version:3,inputs:[],outputs:[{id:'out',name:'功率',ref:{kind:'base',dataType:'double'}}]}]}}
 let saved=0,failSave=false
 async function mount(Component,bb=b){
  let api;const setup=Component.setup;Component.setup=(p,c)=>{api=setup(p,c);return api}
  const app=createSSRApp(Component,{projectState,refState,b:bb})
  app.provide('form-guard',{register(){},unregister(){}})
  app.provide('form-save',{async submitForm(area,apply){if(failSave)return {ok:false,message:'测试保存失败'};apply();saved++;return {ok:true,message:''}}})
  await renderToString(app)
  // 渲染同一份 setup 状态，不重新执行 setup，验证全部字段确实在一个页面。
  const html=()=>renderToString(createSSRApp({render:()=>h({props:Component.props,ssrRender:Component.ssrRender,setup:()=>api},{projectState,refState,b})}))
  return {api,html}
 }
 const {api,html}=await mount(await loadComponent('PropertySources'))
 api.openEditor('power');let page=await html()
 assert.match(page,/数据连接/);assert.match(page,/取值字段/);assert.doesNotMatch(page,/下一步|上一步|steps-bar|<template/)
 await api.saveDraft();assert.equal(saved,0);assert.match(api.editError.value,/字段/)
 api.draft.value.field='rated_power';await api.saveDraft();assert.equal(saved,1);assert.equal(b.properties.power,'rated_power')
 api.openEditor('power');api.dbTableChanged('samples');assert.equal(api.draft.value.result.valueField,'');assert.equal(api.directFieldOptions.value[0].value,'cluster_id')
 api.draft.value.result.valueField='soc';await api.saveDraft();assert.equal(saved,1);assert.match(api.editError.value,/绑定当前实例/)
 api.closeEditor();assert.equal(b.properties.power,'rated_power')
 api.openEditor('soc');api.dataConnectionChanged('redis:redis');api.draft.value.key='soc-{id}';page=await html();assert.match(page,/Key 模板/);assert.match(page,/结果转换/);assert.doesNotMatch(page,/来源表或视图/)
 await api.saveDraft();assert.equal(saved,1);api.setParam('id','primary');await api.saveDraft();assert.equal(saved,2);assert.equal(b.properties.soc.kind,'redis')
 api.openEditor('history');assert.equal(api.draftShape.value,'timeSeries');api.dataConnectionChanged('db:db');api.dbTableChanged('samples');api.addMatch();api.draft.value.lookup.match[0].field='cluster_id';api.draft.value.result.valueField='soc';page=await html();assert.match(page,/结果时间字段/);assert.match(page,/条件1来源字段/)
 await api.saveDraft();assert.equal(saved,2);assert.match(api.editError.value,/时间字段/)
 api.draft.value.result.timestampField='sampled_at';api.setEncoding('datetime');await api.saveDraft();assert.equal(saved,3);assert.equal(b.properties.history.kind,'database')
 api.openEditor('power');api.pickCard('computed');api.draft.value.mode='rule';api.draft.value.implementation='impl';page=await html();assert.match(page,/实现输出/);api.draft.value.output='out';failSave=true;await api.saveDraft();assert.equal(api.configuring.value,true);assert.equal(api.draft.value.output,'out');assert.match(api.editError.value,/测试保存失败/);failSave=false;api.closeEditor()
 const link=await mount(await loadComponent('LinkMappings'));link.api.openNew(graph.at(-1));link.api.draft.value.field='storage_id';page=await link.html();assert.match(page,/终点对象已选择表，但尚未配置数据连接/);assert.match(page,/配置储能设备数据连接/);assert.doesNotMatch(page,/刷新中/)
 await link.api.save();assert.equal(saved,3);assert.match(link.api.message.value,/终点对象尚未配置数据连接/)
 target.connection='db';page=await link.html();assert.doesNotMatch(page,/终点对象已选择表/);assert.equal(link.api.targetCatalog.value.name,'storage');assert.equal(link.api.draft.value.field,'storage_id');link.api.draft.value.targetField='id';await link.api.save();assert.equal(saved,4);assert.equal(b.relations[0].targetField,'id')
 const {socRule}=await import('../frontend/src/project/queryRules.ts');const rule=socRule();rule.objectType='cluster';rule.connection='db';projectState.implementations.push(rule)
 api.openEditor('history');api.pickCard('computed');api.draft.value.mode='rule';api.draft.value.implementation=rule.id;api.draft.value.output='series';assert.equal(api.declaredShapeOf(rule.id,'series'),'timeSeries');assert.equal(api.implInputRows.value.length,2);page=await html();assert.match(page,/查询储能簇 SOC 采样序列/);await api.saveDraft();assert.equal(saved,5);assert.equal(b.properties.history.implementation,rule.id)
 const {reusableScadaRule}=await import('../frontend/src/project/queryRules.ts');const reusable=reusableScadaRule();reusable.connection='db';projectState.implementations.push(reusable)
 api.openEditor('history');api.pickCard('computed');api.selectImplementation(reusable.id);assert.equal(api.draft.value.inputs.model_name,'clusters');assert.equal(api.draft.value.inputs.model_id,'{id}');assert.equal(api.draft.value.output,'series')
 page=await html();assert.match(page,/规则输入 attr_name/);await api.saveDraft();assert.equal(saved,5);assert.match(api.editError.value,/attr_name/)
 api.setRuleInput('attr_name','soc');await api.saveDraft();assert.equal(saved,6);assert.equal(b.properties.history.inputs.attr_name,'soc')
 api.openEditor('history');assert.equal(api.draft.value.inputs.attr_name,'soc');api.setRuleInput('attr_name','temperature');api.closeEditor();assert.equal(b.properties.history.inputs.attr_name,'soc','取消不会改动已保存参数')
 const manager=await mount(await loadComponent('QueryRuleManager'));manager.api.open(rule);page=await manager.html();assert.match(page,/规则名称 \*/);assert.doesNotMatch(page,/保存时绑定属性|规则适用对象|规则名称（选填）/);assert.equal(manager.api.draft.value.schemaVersion,4);assert.equal(manager.api.draft.value.mode,'sqlSteps');assert.equal(rule.schemaVersion,1)
 b.properties.history={kind:'computed',implementation:rule.id,output:'series'};await manager.api.save();assert.equal(projectState.implementations.find(i=>i.id===rule.id).schemaVersion,4);assert.deepEqual(b.properties.history.inputs,{model_name:'m_storage_cluster_phase',attr_name:'soc',model_id:'{id}'})
 page=await manager.html();assert.match(page,/取值规则库/);assert.match(page,/采样值取值规则/);assert.match(page,/详情/);assert.doesNotMatch(page,/SQL 查询步骤|SQL 模板|规则实现|新建采样取值规则/,'规则库默认列表不展开实现详情')
 manager.api.toggleDetail(rule.id);page=await manager.html();assert.match(page,/rule-detail/,'点「详情」后展开实现');manager.api.toggleDetail(rule.id);page=await manager.html();assert.doesNotMatch(page,/rule-detail/,'再点收起')
 const ruleCount=projectState.implementations.length;manager.api.openSampling();const fixedId=manager.api.draft.value.id;manager.api.close();manager.api.openSampling();assert.equal(manager.api.draft.value.id,fixedId);assert.equal(projectState.implementations.length,ruleCount,'再次配置复用规则，不新增记录');manager.api.close()

 // —— V4/V3 声明式规则输入与返回类型相容（审阅缺陷1/缺陷2 表单级回归）——
 const v4Rule=(type,valueType)=>({id:'v4-'+type+'-'+valueType,kind:'queryRule',schemaVersion:4,mode:'sqlSteps',name:'V4 '+valueType+' '+type,connection:'db',
  inputs:[{name:'device_id',type:'string',description:'设备主键',source:'binding'},{name:'startTime',type:'dateTime',description:'查询开始',source:'runtime'}],
  steps:[type==='scalar'
   ?{id:'u1',key:'dev',name:'查设备',cardinality:'one',sql:'SELECT name AS device_name FROM storage WHERE id = :device_id'}
   :{id:'u1',key:'dev',name:'查设备序列',cardinality:'many',sql:'SELECT ts, name AS device_name FROM storage WHERE id = :device_id AND ts >= :startTime'}],
  result:type==='scalar'?{step:'u1',type:'scalar',valueType,value:'device_name'}:{step:'u1',type:'timeSeries',valueType,value:'device_name',timestamp:'ts'}})
 const v4Text=v4Rule('scalar','string'),v4TextSeries=v4Rule('timeSeries','string'),v4Double=v4Rule('scalar','double'),v4Bool=v4Rule('scalar','boolean'),v4Date=v4Rule('scalar','dateTime')
 projectState.implementations.push(v4Text,v4TextSeries,v4Double,v4Bool,v4Date);const implCountAfterV4=projectState.implementations.length // 缺陷1：V4 按 inputs 声明展示 binding 参数、runtime 仅说明；初始化不再统一塞 model_name/attr_name/model_id；保存重开不丢
 api.openEditor('note');api.pickCard('computed');api.selectImplementation(v4Text.id)
 assert.deepEqual(api.draft.value.inputs,{device_id:''},'按声明初始化，不塞旧三参数')
 page=await html();assert.match(page,/device_id · 设备主键/);assert.match(page,/查询时传入（无需在此填写）：startTime/);assert.doesNotMatch(page,/规则输入 model_name|attr_name · 属性名/)
 await api.saveDraft();assert.equal(saved,7);assert.match(api.editError.value,/请填写输入参数 device_id/)
 api.setRuleInput('device_id','{id}');await api.saveDraft();assert.equal(saved,8)
 assert.deepEqual(b.properties.note,{kind:'computed',implementation:v4Text.id,output:'series',inputs:{device_id:'{id}'}})
 api.openEditor('note');assert.equal(api.draft.value.inputs.device_id,'{id}','保存重开后参数不丢失');api.closeEditor()
 // V3 声明式入参同样按 inputs 初始化
 const v3declared={id:'v3decl',kind:'queryRule',schemaVersion:3,name:'V3 声明入参',connection:'db',inputs:[{name:'model_name',type:'string',description:'表名',source:'binding'},{name:'startTime',type:'dateTime',source:'runtime'}],steps:[{id:'q',name:'查询',table:'t',cardinality:'one',where:[{field:'id',op:'eq',value:'{{inputs.model_name}}'}],select:[{as:'value',field:'soc'}]}],result:{type:'scalar',valueType:'double',step:'q',value:'value'}}
 projectState.implementations.push(v3declared)
 api.openEditor('soc');api.pickCard('computed');api.selectImplementation(v3declared.id)
 assert.deepEqual(api.draft.value.inputs,{model_name:''},'V3 按声明初始化')
 page=await html();assert.match(page,/model_name · 表名/);assert.doesNotMatch(page,/规则输入 attr_name/);api.closeEditor()
 // 缺陷2：V4 返回值类型与属性数据类型比对（文本/数值/是/否/日期时间一致逻辑，数值属性兼容 double）
 api.openEditor('power');api.pickCard('computed');api.selectImplementation(v4Text.id);api.setRuleInput('device_id','{id}')
 await api.saveDraft();assert.equal(saved,8);assert.match(api.editError.value,/取值规则值类型与属性数据类型不匹配/,'文本规则绑数值属性必须拦截')
 api.selectImplementation(v4Double.id);api.setRuleInput('device_id','{id}');await api.saveDraft();assert.equal(saved,9,'数值规则绑数值属性可保存')
 api.openEditor('flag');api.pickCard('computed');api.selectImplementation(v4Bool.id);api.setRuleInput('device_id','{id}');await api.saveDraft();assert.equal(saved,10,'是/否规则绑布尔属性可保存')
 api.openEditor('commissioned');api.pickCard('computed');api.selectImplementation(v4Date.id);api.setRuleInput('device_id','{id}');await api.saveDraft();assert.equal(saved,11,'日期时间规则绑日期属性可保存')
 api.openEditor('note');api.pickCard('computed');api.selectImplementation(v4Bool.id);api.setRuleInput('device_id','{id}')
 await api.saveDraft();assert.equal(saved,11);assert.match(api.editError.value,/取值规则值类型与属性数据类型不匹配/,'布尔规则绑文本属性必须拦截');api.closeEditor()
 // 时序：文本序列绑文本序列属性成功；绑数值序列 / 单值绑序列 均拦截
 api.openEditor('tsnote');api.pickCard('computed');api.selectImplementation(v4TextSeries.id);api.setRuleInput('device_id','{id}');await api.saveDraft();assert.equal(saved,12,'文本时间序列规则绑文本序列属性可保存')
 api.openEditor('history');api.pickCard('computed');api.selectImplementation(v4TextSeries.id);api.setRuleInput('device_id','{id}')
 await api.saveDraft();assert.equal(saved,12);assert.match(api.editError.value,/取值规则值类型与属性数据类型不匹配/,'文本序列绑数值序列必须拦截')
 api.selectImplementation(v4Text.id);api.setRuleInput('device_id','{id}')
 await api.saveDraft();assert.equal(saved,12);assert.match(api.editError.value,/函数输出数据类型与属性要求不匹配/,'单值绑序列属性必须拦截');api.closeEditor()

 // —— 属性内 SQL 取值（computed/mode=inlineSql，方案 A1-A10 表单级）——
 delete b.properties.power
 api.openEditor('power');api.pickCard('computed')
 assert.equal(api.draft.value.mode,'inline','新配置默认直接 SQL')
 page=await html();assert.match(page,/直接编写 SQL/);assert.match(page,/引用已有规则/)
 api.draft.value.inline.connection='db';api.draft.value.inline.sql='SELECT SUM(rated_power) AS value FROM clusters'
 page=await html();assert.doesNotMatch(page,/参数 \{/,'无参数不显示绑定行')
 api.draft.value.inline.sql="SELECT SUM(rated_power) AS value FROM clusters WHERE park = :park_id -- :fake\n  AND flag = :zero"
 page=await html();assert.match(page,/参数 \{park_id\} 来源/);assert.match(page,/参数 \{zero\} 来源/);assert.doesNotMatch(page,/\{fake\}/,'注释内伪参数不识别')
 api.setInlineBinding('park_id','projectParameter');api.setInlineBinding('zero','constant')
 page=await html();assert.match(page,/请选择项目参数/);assert.match(page,/常量值/)
 api.draft.value.inline.params.park_id.key='park_id';api.draft.value.inline.params.zero.value='0'
 const implCountBeforeInline=projectState.implementations.length
 await api.saveDraft();assert.equal(saved,13,'A2/A3：保存成功，implementations 不变')
 assert.deepEqual(b.properties.power,{kind:'computed',mode:'inlineSql',inlineSql:{version:1,connection:'db',
  sql:"SELECT SUM(rated_power) AS value FROM clusters WHERE park = :park_id -- :fake\n  AND flag = :zero",
  params:{park_id:{from:'projectParameter',key:'park_id'},zero:{from:'constant',dataType:'string',value:'0'}}}})
 assert.equal(projectState.implementations.length,implCountBeforeInline,'不新增 implementations')
 // 保存重开：原文与绑定完整；列表摘要「直接 SQL · 连接名称」
 api.openEditor('power');assert.equal(api.draft.value.mode,'inline');assert.match(api.draft.value.inline.sql,/SUM\(rated_power\)/)
 assert.equal(api.inlineBindingKind('park_id'),'projectParameter');assert.equal(api.inlineBindingKind('zero'),'constant')
 api.closeEditor();page=await html();assert.match(page,/直接 SQL · 测试库/)
 // A4：改 SQL 移除 :zero → 不再显示，保存后不进生效参数；保留的 park_id 绑定不丢
 api.openEditor('power');api.draft.value.inline.sql='SELECT rated_power AS value FROM clusters WHERE park = :park_id'
 page=await html();assert.match(page,/参数 \{park_id\} 来源/);assert.doesNotMatch(page,/\{zero\}/)
 await api.saveDraft();assert.equal(saved,14)
 assert.deepEqual(b.properties.power.inlineSql.params,{park_id:{from:'projectParameter',key:'park_id'}},'移除的参数不进生效配置')
 // A5：明确空字符串常量有效；0/false 合法；未绑定拦截
 api.openEditor('power');api.draft.value.inline.sql="SELECT :empty, :zero, :flag FROM clusters WHERE id = :park_id"
 api.setInlineBinding('empty','constant');api.setInlineBinding('zero','constant');api.setInlineBinding('flag','constant')
 api.draft.value.inline.params.zero.value='0';api.draft.value.inline.params.flag.value='false'
 page=await html();assert.match(page,/明确填写的 0、false、空字符串都是有效值/)
 await api.saveDraft();assert.equal(saved,15)
 // 新增参数未绑定 → 保存拦截
 api.openEditor('power');api.draft.value.inline.sql+=' AND x = :newp'
 await api.saveDraft();assert.equal(saved,15);assert.match(api.editError.value,/参数 :newp 未绑定取值/)
 // A9：切换方式只改本地草稿，两边内容保留；取消无写入
 api.draft.value.mode='rule';page=await html();assert.match(page,/请选择已有实现/)
 api.draft.value.mode='inline';assert.match(api.draft.value.inline.sql,/AND x = :newp/,'切回后 SQL 草稿保留')
 const storedBefore=JSON.stringify(b.properties.power)
 api.closeEditor()
 assert.equal(JSON.stringify(b.properties.power),storedBefore,'A9：取消无修订写入')
 // A8：原规则绑定（无 mode 的旧 computed）按原方式回显
 api.openEditor('history');assert.equal(api.draft.value.mode,'rule');assert.equal(api.draft.value.implementation,rule.id);api.closeEditor()
 // A10：未知版本内联结构 → 只读保留，不被清空
 b.properties.note={kind:'computed',mode:'inlineSql',inlineSql:{version:2,connection:'db',sql:'SELECT 1',params:{}}}
 api.openEditor('note');page=await html();assert.match(page,/未识别的来源结构/)
 api.closeEditor();assert.deepEqual(b.properties.note.inlineSql.version,2,'未知版本零丢失')
 // A6：登记对象（无来源表）也能配置内联 SQL + 项目参数 + 实例编号
 const regB=reactive({object_type:'cluster',identity:{kind:'registered',instances:[{id:'S1',label:'储能系统一'}]},connection:'',table:'',primary_key:'',title_key:'',sources:[],properties:{},relations:[]})
 const reg=await mount(await loadComponent('PropertySources'),regB)
 reg.api.openEditor('power');reg.api.pickCard('computed')
 reg.api.draft.value.inline.connection='db'
 reg.api.draft.value.inline.sql='SELECT SUM(x) AS value FROM m WHERE park = :park_id AND sid = :sid'
 reg.api.setInlineBinding('park_id','projectParameter');reg.api.setInlineBinding('sid','instanceId')
 reg.api.draft.value.inline.params.park_id.key='park_id'
 page=await reg.html();assert.match(page,/登记实例编号/)
 await reg.api.saveDraft()
 assert.deepEqual(regB.properties.power.inlineSql.params,{park_id:{from:'projectParameter',key:'park_id'},sid:{from:'instanceId'}})
 // —— 计算函数（kind=calculationFunction，A1-A9 表单级）——
 // /api/calc-eval 替身：validateOnly 镜像本地检查；带值时 40/100 → 40
 const realFetch=globalThis.fetch
 globalThis.fetch=async(url,init)=>{
   const body=JSON.parse(init?.body||'{}')
   const reply=(payload)=>({ok:true,json:async()=>payload})
   if(String(url).includes('/api/calc-eval')){
     if(body.validateOnly)return reply({ok:true})
     const vs=(body.inputs||[]).map((i)=>Number(i.value))
     const v=vs[0]/vs[1]*100
     return reply(vs[1]===0?{ok:false,error:'额定容量必须大于零'}:{ok:true,value:v})
   }
   return realFetch(url,init)
 }
 manager.api.newCalc()
 assert.equal(manager.api.draft.value.kind,'calculationFunction','A1：新建计算函数进入函数表单')
 page=await manager.html();assert.match(page,/函数名称/);assert.match(page,/示例试算/);assert.doesNotMatch(page,/数据连接 \*/)
 manager.api.draft.value.name='计算 SOC'
 manager.api.draft.value.inputs[0].name='剩余电量'
 manager.api.draft.value.inputs.push({id:'inp-bbb',name:'额定容量',type:'number'})
 manager.api.displayFormula.value='{剩余电量} / {额定容量} * 100'
 manager.api.draft.value.output.name='SOC'
 await manager.api.saveCalc()
 if(projectState.implementations.filter((i)=>i.kind==='calculationFunction').length!==1)console.log('DEBUG saveCalc error:',manager.api.error.value,'formula:',manager.api.displayFormula.value)
 assert.equal(projectState.implementations.filter((i)=>i.kind==='calculationFunction').length,1,'A1：保存落 implementations')
 const savedFn=projectState.implementations.find((i)=>i.kind==='calculationFunction')
 assert.equal(savedFn.implementation.expression,'{'+savedFn.inputs[0].id+'} / {'+savedFn.inputs[1].id+'} * 100','公式以稳定标识保存')
 // A5：改名不破坏公式；删除被引用参数阻止保存
 manager.api.open(savedFn)
 const exprBefore=savedFn.implementation.expression
 manager.api.draft.value.inputs[0].name='当前剩余电量'
 await manager.api.saveCalc()
 assert.equal(projectState.implementations.find((i)=>i.kind==='calculationFunction').implementation.expression,exprBefore,'A5：改名不影响公式')
 manager.api.open(savedFn)
 manager.api.draft.value.inputs.splice(0,1)
 await manager.api.saveCalc()
 assert.match(manager.api.error.value,/已被删除|不存在/,'A5：删除被引用参数阻止保存')
 // A2/A4：试算 40/100 → 40；b=0 → ERROR 分支消息（惰性）
 manager.api.open(savedFn)
 manager.api.trialValues.value={};manager.api.trialValues.value[savedFn.inputs[0].id]='40';manager.api.trialValues.value[savedFn.inputs[1].id]='100'
 await manager.api.runTrial()
 assert.equal(manager.api.trialResult.value.text,'试算结果：40','A2：40/100*100=40')
 manager.api.trialValues.value[savedFn.inputs[1].id]='0'
 await manager.api.runTrial()
 assert.match(manager.api.trialResult.value.text,/额定容量必须大于零/,'A4：ERROR 分支消息')
 // A9 属性绑定（PropertySources）：函数输出绑 rated_power；直接循环与类型不匹配拦截
 const savedBase=saved
 api.openEditor('power');api.pickCard('computed');api.draft.value.mode='function';api.draft.value.implementation=''
 api.draft.value.mode='function';api.draft.value.implementation=savedFn.id;api.draft.value.output=savedFn.output.id
 api.draft.value.inputs={}
 api.draft.value.inputs[savedFn.inputs[0].id]={from:'property',property:'power'}
 await api.saveDraft();assert.equal(saved,savedBase);assert.match(api.editError.value,/循环依赖/,'A9：直接循环拦截')
 api.draft.value.inputs[savedFn.inputs[0].id]={from:'property',property:'history'}
 await api.saveDraft();assert.match(api.editError.value,/不匹配|时间序列/,'A8：时序属性不能作为数值输入')
 api.draft.value.inputs[savedFn.inputs[0].id]={from:'property',property:'flag'}
 await api.saveDraft();assert.match(api.editError.value,/不匹配/,'A8：布尔属性不能作为数值输入')
 api.draft.value.inputs[savedFn.inputs[0].id]={from:'constant',value:0}
 api.draft.value.inputs[savedFn.inputs[1].id]={from:'property',property:'soc'}
 await api.saveDraft();assert.equal(saved,savedBase+1,'0 是有效固定值，绑定保存成功')
 page=await html();assert.match(page,/额定功率|power/,'清单态渲染函数绑定（回归：此前此处抛 mg:sharedProperty TypeError）')
 assert.equal(b.properties.power.mode,'calcFunction')
 api.openEditor('power');assert.equal(api.draft.value.mode,'function','A1：重开按函数绑定回显');assert.equal(api.draft.value.inputs[savedFn.inputs[0].id].value,0,'0 固定值回显');api.closeEditor()
 globalThis.fetch=realFetch
 console.log('通过：…计算函数（新建编辑保存重开、稳定标识公式、改名不破坏、删参拦截、试算与惰性 IF、绑定类型相容与循环拦截）。')
}finally{delete globalThis.__mappingSelect;rmSync(root,{recursive:true,force:true})}

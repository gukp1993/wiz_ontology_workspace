// 来源/编排配置保留回归（P03/F04 · P05/F06，20260920 v2 冻结语义）。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/source_config_retention.test.mjs
// 方法：与 tests/object_sources.test.mjs 同一套 SSR 编译模式（@vue/compiler-sfc + 受控 stub），
// 不启服务、不连数据库，全部内存夹具：
//   T08 换身份表后 sources[]（matchLeft/matchRight/table）、属性绑定、说明、未知字段逐字段保留；
//       新身份表恰好存在同名字段也不自动重绑/自动确认；除身份三键外保存前后深比较不变。
//   T09 registered ↔ database 切换：原数据库身份三键、补充来源、说明与属性绑定原样保留。
//   E03 bindingModel：旧格式（related_sources／字符串映射／仅 column 链接）与未知 kind 零丢失，
//       commitProperty 只改授权目标。
//   T17 编排详情缓存按 revision 复核：复核必重新取数；5xx → failed（不清引用、文案不是「不存在」）；
//       404 → missing；签名变化（同名新 id）不自动确认。E05：0/false/空串是合法固定值。
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFileSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join,resolve,dirname} from 'node:path'
import {pathToFileURL} from 'node:url'
const require=createRequire(new URL('../frontend/package.json',import.meta.url))
const {parse,compileScript}=require('@vue/compiler-sfc')
const ts=require('typescript'),{createSSRApp,reactive}=require('vue'),{renderToString}=require('@vue/server-renderer')
const root=mkdtempSync(join(tmpdir(),'wiz_source_retention_'))
process.env.WIZ_WORKBENCH_ROOT=root;process.env.WIZ_WORKBENCH_PORT='18991'

let failed=0
function check(name,cond,detail=''){console.log(`${cond?'通过':'失败'}：${name}${cond?'':' — '+(detail||'断言不成立')}`);if(!cond)failed++}
const tick=(ms=5)=>new Promise(r=>setTimeout(r,ms))

// 组件编译：沿用 object_sources.test.mjs 的替换/解析约定（下拉等外壳换成空组件）。
function compileComponent(relPath,replacements){
  const filename=resolve(relPath)
  const {descriptor}=parse(readFileSync(filename,'utf8'),{filename})
  let code=compileScript(descriptor,{id:'retention-'+relPath.replace(/\W+/g,'-')}).content
  code=code.replace(/import type \{[^}]*\} from ['"][^'"]+['"]\s*;?/g,'')
  for(const [re,rep] of replacements)code=code.replace(re,rep)
  code=code.replace(/from (['"])([^'"]+)\1/g,(_,quote,spec)=>{
    const target=spec==='vue'?require.resolve('vue'):resolve(dirname(filename),spec+'.ts')
    return 'from '+JSON.stringify(pathToFileURL(target).href)
  })
  const file=join(root,relPath.split('/').pop().replace('.vue','')+'.mjs')
  writeFileSync(file,ts.transpileModule(code,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText)
  return import(pathToFileURL(file).href)
}
const STUB_IMPORTS=[
  [/import AppSelect from ['"].*?['"]/,'const AppSelect = {}'],
  [/import RegisteredInstances from ['"].*?['"]/,'const RegisteredInstances = {}'],
  [/import MappingDescription from ['"].*?['"]/,'const MappingDescription = {}'],
  [/import SourcePreview from ['"].*?['"]/,'const SourcePreview = {}'],
]

// ---------- 受控 flow stub（不访问网络；脚本化每次取数的结果） ----------
const flowCalls=[]
let flowScript=[]
const flowStub={
  async listFlows(){return {items:[{id:'flow-a',name:'编排 A',status:'active',errorCount:0}]}},
  async loadFlowStateRaw(flowId){
    flowCalls.push(flowId)
    const step=flowScript.shift()
    if(!step)throw Object.assign(new Error('未预期的取数'),{status:500})
    if(step.error)throw Object.assign(new Error(step.error),{status:step.status})
    return {state:step.state,revision:step.revision}
  },
}
function flowPayload(marker,outputId='out_v'){return {flowId:'flow-a',name:'编排 A',status:'active',
  inputs:[{id:'in_x',name:'x',label:'输入 x',type:{type:'number'}}],
  outputs:[{id:outputId,name:'v',label:'输出 '+marker,type:{type:'number'}}],
  nodes:[],connections:[],layout:{}}}
// 组件模块级 `const {listFlows,loadFlowStateRaw}=globalThis.__flowStub` 在 import 时求值，故先挂全局桩。
globalThis.__flowStub=flowStub

async function mount(Component,props,provides){
  let api
  const setup=Component.setup
  Component.setup=(p,c)=>{api=setup(p,c);return api}
  Component.render=()=>null
  const app=createSSRApp(Component,props)
  for(const [k,v] of Object.entries(provides||{}))app.provide(k,v)
  await renderToString(app)
  return api
}

try{
  const ObjectSources=(await compileComponent('frontend/src/project/ObjectSources.vue',STUB_IMPORTS)).default
  const PropertySources=(await compileComponent('frontend/src/project/PropertySources.vue',[...STUB_IMPORTS,
    [/\nimport \{listFlows,loadFlowStateRaw\} from ['"].*?['"]\n/,'\nconst {listFlows,loadFlowStateRaw}=globalThis.__flowStub\n'],
  ])).default
  const bm=await import('../frontend/src/project/bindingModel.ts')

  // ================= T08 / T09：身份来源变更与补充来源保留 =================
  {
    const fields=(...names)=>names.map(name=>({name,dataType:'int',key:'',comment:''}))
    const projectState=reactive({
      connections:{connections:[{id:'a',name:'库A',engine:'mysql'},{id:'b',name:'库B',engine:'mysql'}]},
      bindings:{
        catalogs:{
          // 新身份表 new_identity 里恰好存在同名 company 字段（反例：不得据此自动确认语义相同）
          a:{tables:[{name:'identity',fields:fields('id','company')},{name:'new_identity',fields:fields('id','company','note')}]},
          b:{tables:[{name:'first',fields:fields('external_id','other_value')}]},
        },
        mappingDescriptions:{schemaVersion:1,objects:{'mg:cluster':'簇来自库A身份表'}},
      },
    })
    const b=reactive({object_type:'cluster',connection:'a',table:'identity',primary_key:'id',
      sources:[{id:'src-1',name:'补充一',kind:'db',connection:'b',table:'first',matchLeft:'company',matchRight:'external_id',cardinality:'one',unknownField:{keep:true}}],
      properties:{power:{kind:'field',source:'src-1',field:'other_value'},note:'直接字符串映射'},
      relations:[{relation:'belongs',target_type:'storage',sourceId:'src-1',field:'external_id',targetSourceId:'',targetField:''}],
      identity:{kind:'database'},extraUnknown:{keep:['x']}})
    let saved=0
    const api=await mount(ObjectSources,{projectState,refState:{ontology:{'@graph':[]}},b},{
      'form-guard':{register(){},unregister(){}},
      'form-save':{async submitForm(area,apply){assert.equal(area,'project');apply();saved++;return {ok:true,message:''}}},
    })
    const snapshot=JSON.parse(JSON.stringify(b))
    const descBefore=JSON.stringify(projectState.bindings.mappingDescriptions)
    api.openIdentity()
    api.identityTableChanged('new_identity')
    check('T08 换表提示不再宣称会清空匹配字段',!/将被清空/.test(api.tableChangeNote.value),api.tableChangeNote.value)
    check('T08 换表提示说明原样保留',/原样保留/.test(api.tableChangeNote.value),api.tableChangeNote.value)
    // 未选新表主键：拒绝该次变更并保留输入，且不写入任何配置（不静默清空）
    await api.saveIdentity()
    check('T08 未选主键时原子拒绝保存',saved===0&&/主键/.test(api.message.value),saved+' / '+api.message.value)
    check('T08 拒绝后原配置逐字节不变',JSON.stringify(b)===JSON.stringify(snapshot),JSON.stringify(b))
    check('T08 拒绝后表单输入保留',api.identityDraft.value.table==='new_identity',api.identityDraft.value.table)
    api.identityDraft.value.primary_key='id'
    await api.saveIdentity()
    check('T08 换表保存成功',saved===1,'saved='+saved)
    check('T08 身份三键按用户选择更新',b.connection==='a'&&b.table==='new_identity'&&b.primary_key==='id',
      [b.connection,b.table,b.primary_key].join(','))
    const after=JSON.parse(JSON.stringify(b))
    check('T08 除身份三键外逐字段保留（补充来源/属性/链接/说明/未知字段）',
      JSON.stringify(after)===JSON.stringify({...snapshot,connection:'a',table:'new_identity',primary_key:'id'}),
      JSON.stringify(after))
    check('T08 原 matchLeft/matchRight 未被清空也未被同名字段重绑',
      after.sources[0].matchLeft==='company'&&after.sources[0].matchRight==='external_id',JSON.stringify(after.sources[0]))
    check('T08 未知扩展字段保留',after.sources[0].unknownField.keep===true&&after.extraUnknown.keep[0]==='x')
    check('T08 属性绑定与说明不动',after.properties.power.source==='src-1'&&after.properties.note==='直接字符串映射'
      &&JSON.stringify(projectState.bindings.mappingDescriptions)===descBefore)
    check('T08 链接映射端点不动',after.relations[0].sourceId==='src-1'&&after.relations[0].field==='external_id')
    // 同一补充来源仍可编辑保存（换表没有把它锁死或丢字段）
    api.openSource(after.sources[0])
    check('T08 已登记来源仍进编辑表单（非未知形态）',!!api.sourceDraft.value&&api.sourceDraft.value.matchLeft==='company')
    check('T08 表单带入的补充来源逐字段完整',JSON.stringify(api.sourceDraft.value)===JSON.stringify(after.sources[0]))
    api.closeEditor()

    // T09：database → registered → database
    api.openIdentity()
    api.identityModeChanged('registered')
    api.identityDraft.value.instances=[{id:'C1',label:'一号簇'}]
    await api.saveIdentity()
    check('T09 切登记身份保存成功',saved===2,'saved='+saved)
    check('T09 登记身份保留原数据库三键（切回可恢复）',
      b.connection==='a'&&b.table==='new_identity'&&b.primary_key==='id',[b.connection,b.table,b.primary_key].join(','))
    check('T09 切登记后补充来源/属性/链接逐字段保留',JSON.stringify(b.sources)===JSON.stringify(after.sources)
      &&JSON.stringify(b.properties)===JSON.stringify(after.properties)&&JSON.stringify(b.relations)===JSON.stringify(after.relations))
    check('T09 切登记后说明不动',JSON.stringify(projectState.bindings.mappingDescriptions)===descBefore)
    api.openIdentity()
    api.identityModeChanged('database')
    await api.saveIdentity()
    check('T09 切回数据库来源成功且字段未丢',saved===3&&b.identity===undefined
      &&JSON.stringify(b.sources)===JSON.stringify(after.sources)&&b.properties.power.source==='src-1',JSON.stringify(b.sources))

    // E03（bindingModel）：旧格式与未知 kind 零丢失；commitProperty 只改授权目标
    const legacy={object_type:'cluster',connection:'a',table:'identity',primary_key:'id',
      related_sources:[{id:'src-old',name:'旧来源',table:'first',source_field:'company',target_field:'external_id'}],
      properties:{power:{kind:'related',source:'src-old',field:'other_value'},weird:{kind:'mysteryFromFuture',payload:[1,2]}},
      relations:[{relation:'belongs',target_type:'storage',column:'external_id'}]}
    const weirdView=bm.propertyView(legacy,'weird')
    check('E03 未知 kind 解为 unknown（绝不当 computed）',!!weirdView&&weirdView.kind==='unknown',JSON.stringify(weirdView))
    bm.commitProperty(legacy,'weird',weirdView)
    check('E03 unknown 原样不写（零丢失）',
      JSON.stringify(legacy.properties.weird)===JSON.stringify({kind:'mysteryFromFuture',payload:[1,2]}),JSON.stringify(legacy.properties.weird))
    check('E03 旧 related_sources 内存适配为 sources',bm.sourcesOf(legacy).length===1
      &&bm.sourcesOf(legacy)[0].matchLeft==='company'&&bm.sourcesOf(legacy)[0].legacy===true,JSON.stringify(bm.sourcesOf(legacy)))
    check('E03 旧 column 链接识别为 legacy 且读取回退 field',bm.relationView(legacy.relations[0]).legacy===true
      &&bm.relationView(legacy.relations[0]).field==='external_id',JSON.stringify(bm.relationView(legacy.relations[0])))
    const untouched=()=>JSON.stringify({weird:legacy.properties.weird,relations:legacy.relations,related:legacy.related_sources,other:legacy.object_type})
    const beforeUntouched=untouched()
    bm.commitProperty(legacy,'power',bm.propertyView(legacy,'power'))
    check('E03 commitProperty 只改授权目标（其余逐字节不动）',untouched()===beforeUntouched,untouched())
    check('E03 授权目标按 field 来源重写',legacy.properties.power.kind==='field'&&legacy.properties.power.source==='src-old',JSON.stringify(legacy.properties.power))
    console.log('T08/T09/E03 组完成')
  }

  // ================= T17 / E05：编排详情按 revision 复核 =================
  {
    globalThis.__flowStub=flowStub
    const fields=(...names)=>names.map(name=>({name,dataType:'double',key:'',comment:''}))
    const projectState=reactive({projectId:'p1',connections:{connections:[]},bindings:{object_bindings:[],catalogs:{}},implementations:[],parameters:{}})
    const b=reactive({object_type:'cluster',connection:'',table:'',primary_key:'id',sources:[],properties:{
      power:{kind:'flow',flow:'flow-a',output:'out_v',inputs:{in_x:{from:'instanceId'}}}},relations:[]})
    const graph=[{'@id':'mg:cluster','@type':'owl:Class','rdfs:label':'储能簇'},
      {'@id':'mg:power','@type':'owl:DatatypeProperty','rdfs:domain':{'@id':'mg:cluster'},'rdfs:range':{'@id':'xsd:double'},'rdfs:label':'功率','mg:apiName':'power'}]
    const powerNode=graph[1]
    const binding=()=>JSON.stringify(b.properties.power)
    const bindingBefore=binding()
    // 挂载时 loadFlows() 会为已配置属性补详情：第一次取数返回 r-1
    flowScript=[{state:flowPayload('v1'),revision:'r-1'}]
    const api=await mount(PropertySources,{projectState,refState:{ontology:{'@graph':graph}},b},{
      'form-guard':{register(){},unregister(){}},
      'form-save':{async submitForm(){return {ok:true,message:''}}},
    })
    await tick(10)
    check('T17 列表加载后自动取详情并记录 revision',api.flowEntryOf('flow-a')?.revision==='r-1',
      JSON.stringify(api.flowEntryOf('flow-a')?.revision))
    const firstView={kind:'flow',flow:'flow-a',output:'out_v',inputs:{in_x:{from:'instanceId'}}}
    check('T17 签名匹配时不报失效',!api.issuesOf('power',powerNode,firstView).some(t=>/输出不存在|读取失败|不存在/.test(t)),
      JSON.stringify(api.issuesOf('power',powerNode,firstView)))

    // 复核：编排已改动（新 revision）→ 必须重新取数并使用新声明
    flowCalls.length=0;flowScript=[{state:flowPayload('v2'),revision:'r-2'}]
    await api.ensureFlowState('flow-a')
    check('T17 复核重新取数（不按 flowId 永久复用）',flowCalls.length===1,'calls='+flowCalls.length)
    check('T17 使用新 revision 与新的声明内容',api.flowEntryOf('flow-a')?.revision==='r-2'
      &&api.flowStateOf('flow-a')?.outputs?.[0]?.label==='输出 v2',JSON.stringify(api.flowEntryOf('flow-a')?.revision))
    check('T17 复核不清空、不改写原绑定',binding()===bindingBefore,binding())

    // 签名变化：输出换成同名的其他稳定 id（out_w）→ 不自动确认、报失效
    flowScript=[{state:flowPayload('v3','out_w'),revision:'r-3'}]
    await api.ensureFlowState('flow-a')
    const sigIssues=api.issuesOf('power',powerNode,firstView)
    check('T17 同名新 id 不自动重绑（旧输出 id 报失效）',sigIssues.some(t=>/编排输出不存在/.test(t)),JSON.stringify(sigIssues))
    check('T17 签名变化仍不动原绑定',binding()===bindingBefore,binding())

    // 读取失败（500）：failed ≠ 不存在；不清引用；不下失效结论
    flowCalls.length=0;flowScript=[{error:'服务暂时不可用',status:500}]
    await api.ensureFlowState('flow-a')
    const failedEntry=api.flowEntryOf('flow-a')
    check('T17 读取失败记为 failed 且带原因',failedEntry?.status==='failed'&&/服务暂时不可用/.test(failedEntry?.message||''),
      JSON.stringify(failedEntry))
    const failedIssues=api.issuesOf('power',powerNode,firstView)
    check('T17 读取失败提示读取失败',failedIssues.some(t=>/读取失败/.test(t)),JSON.stringify(failedIssues))
    check('T17 读取失败不出现「不存在/已删除」结论',!failedIssues.some(t=>/不存在或已删除/.test(t)),JSON.stringify(failedIssues))
    check('T17 读取失败不清空原引用',binding()===bindingBefore,binding())
    const failedStatus=api.statusOf('power',powerNode,b.properties.power)
    check('T17 读取失败不显示为「配置有误」',failedStatus.text==='编排待读取',failedStatus.text+' / '+failedStatus.title)

    // 404：真正的「不存在」按既有失效语义（仍不清空原引用）
    flowScript=[{error:'编排不存在',status:404}]
    await api.ensureFlowState('flow-a')
    check('T17 404 记为 missing',api.flowEntryOf('flow-a')?.status==='missing',JSON.stringify(api.flowEntryOf('flow-a')))
    const missingIssues=api.issuesOf('power',powerNode,firstView)
    check('T17 404 按不存在报错',missingIssues.some(t=>/不存在或已删除/.test(t)),JSON.stringify(missingIssues))
    check('T17 404 同样不清空原引用',binding()===bindingBefore,binding())

    // E05：0/false/空串都是合法固定值（时间序列属性照旧只接受 database/序列输出）
    const scalarFlow={flowId:'flow-a',name:'编排 A',status:'active',
      inputs:[{id:'in_n',name:'n',label:'数值',type:{type:'number'}},{id:'in_b',name:'b',label:'布尔',type:{type:'boolean'}},{id:'in_t',name:'t',label:'文本',type:{type:'text'}}],
      outputs:[{id:'out_v',name:'v',label:'标量输出',type:{type:'number'}}],nodes:[],connections:[],layout:{}}
    flowScript=[{state:scalarFlow,revision:'r-4'}]
    await api.ensureFlowState('flow-a')
    const scalarView={kind:'flow',flow:'flow-a',output:'out_v',inputs:{in_n:{from:'constant',value:'0'},in_b:{from:'constant',value:'false'},in_t:{from:'constant',value:''}}}
    const scalarIssues=api.issuesOf('power',powerNode,scalarView)
    check('E05 0/false/空串不算未绑定或无效',!scalarIssues.some(t=>/固定值无效|未绑定/.test(t)),JSON.stringify(scalarIssues))
    const missingInput={kind:'flow',flow:'flow-a',output:'out_v',inputs:{in_n:{from:'constant',value:'0'}}}
    check('E05 缺少的输入仍报未绑定',api.issuesOf('power',powerNode,missingInput).some(t=>/未绑定取值/.test(t)),
      JSON.stringify(api.issuesOf('power',powerNode,missingInput)))
    // 时间序列属性：函数编排标量输出仍被拒（既有本地校验不放松）
    const tsNode={'@id':'mg:series','@type':'owl:DatatypeProperty','rdfs:domain':{'@id':'mg:cluster'},'rdfs:range':{'@id':'xsd:double'},'rdfs:label':'曲线','mg:apiName':'series','mg:valueShape':'timeSeries'}
    check('E05 时间序列属性拒绝标量输出（既有边界保持）',
      api.issuesOf('series',tsNode,scalarView).some(t=>/不能绑定时间序列属性/.test(t)),
      JSON.stringify(api.issuesOf('series',tsNode,scalarView)))
    console.log('T17/E05 组完成')
    check('T17 全程未写入项目状态（项目属性仅其自身绑定）',Object.keys(b.properties).length===1&&b.properties.power.flow==='flow-a')
  }
  console.log(failed?`\n${failed} 项失败`:'\n全部通过')
}finally{rmSync(root,{recursive:true,force:true})}
process.exit(failed?1:0)

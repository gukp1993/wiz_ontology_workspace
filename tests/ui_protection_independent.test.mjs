// 角色 F 独立复核（20260920 v2 / P03 · P04 · P05）：对 E/C/L 已落盘前端行为的交叉验证。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/ui_protection_independent.test.mjs
//
// 取证方式：真实组件（@vue/compiler-sfc 编译 + 真实模板 SSR 渲染）+ 受控 stub（子组件/网络/确认框/form-save），
// 只断言公开交互结果与渲染输出，不镜像实现内部结构；不启服务、不连数据库、不写真实根。
//   1) P03 换连接/换表保存：除身份三键外项目绑定逐字段不变；未知来源不进编辑入口、仍可移除。
//   2) P05 编排详情：复核必重新取数（旧 revision 的声明不被继续使用）；500 → failed（引用保留、不报「不存在」）；
//      404 → missing。
//   3) P04 refreshCatalogOf：{ok:true,stale:true} 不写 catalogs、返回 ok:false。
//   4) P04 删除连接：持久化失败（含 reject）不提示成功、连接行保留；目录刷新 stale 不提示成功。
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFileSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {basename,dirname,join,resolve} from 'node:path'
import {pathToFileURL} from 'node:url'

const require=createRequire(new URL('../frontend/package.json',import.meta.url))
const {parse,compileScript,compileTemplate}=require('@vue/compiler-sfc')
const ts=require('typescript')
const {createSSRApp,reactive,h}=require('vue')
const {renderToString}=require('@vue/server-renderer')

const root=mkdtempSync(join(tmpdir(),'wiz_ui_protection_'))
process.env.WIZ_WORKBENCH_ROOT=root
process.env.WIZ_WORKBENCH_PORT='18997'

let failed=0
function check(name,cond,detail=''){console.log(`${cond?'通过':'失败'}：${name}${cond?'':' — '+(detail||'断言不成立')}`);if(!cond)failed++}
const tick=(ms=10)=>new Promise(r=>setTimeout(r,ms))
const deep=v=>JSON.parse(JSON.stringify(v))
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b)
const guard={register(){},unregister(){}}
function formSaveStub(){
  const st={mode:'ok',calls:0,gate:null}
  st.api={async submitForm(area,apply){
    st.calls++;assert.equal(area,'project','保存入口必须是项目区')
    if(st.mode==='fail')return {ok:false,message:'保存失败：草稿版本冲突'}
    if(st.mode==='throw')throw new Error('网络中断，未收到响应')
    if(st.gate)await st.gate
    apply();return {ok:true,message:''}
  }}
  return st
}

// ---------- 受控 stub：子组件/网络/确认框 ----------
const NullComp={render(){return null}}
// 下拉替换为真实 <select>，渲染输出才能作为「界面看到什么」的证据（与生产组件无关的渲染外壳）。
const SelectStub={props:['options','modelValue','disabled'],render(){return h('select',{disabled:this.disabled},(this.options||[]).map(o=>h('option',{value:o.value,selected:o.value===this.modelValue,disabled:o.disabled},o.label)))}}
function flowPayload(marker,outputId='out_v'){return {flowId:'flow-a',name:'编排 A',status:'active',
  inputs:[{id:'in_x',name:'x',label:'输入 x',type:{type:'text'}}],
  outputs:[{id:outputId,name:'v',label:'输出 '+marker,type:{type:'number'}}],nodes:[],connections:[],layout:{}}}
const stubs={
  confirm:true,
  calls:{flow:[],test:[],refresh:[]},
  flowList:{items:[{id:'flow-a',name:'编排 A',status:'active',errorCount:0}]},
  flowDefault:{state:flowPayload('v1'),revision:'r-1'},
  flowNext:null,
  testResult:{ok:true,message:'连接成功',latencyMs:1},
  catalogResult:{ok:true,stale:false,database:'energy',tables:[],refreshedAt:'2026-09-20T00:00:00+00:00',message:'已读取 0 张表'},
}
globalThis.__uiChain={
  NullComp,SelectStub,
  appConfirm:async()=>stubs.confirm,
  listFlows:async()=>stubs.flowList,
  loadFlowStateRaw:async flowId=>{
    stubs.calls.flow.push(flowId)
    const step=stubs.flowNext||stubs.flowDefault
    stubs.flowNext=null
    if(step.error)throw Object.assign(new Error(step.error),{status:step.status})
    return step
  },
  connectionTest:async p=>{stubs.calls.test.push(p);return stubs.testResult},
  connectionSecret:async()=>({saved:true}),
  catalogRefresh:async(...a)=>{stubs.calls.refresh.push(a);return stubs.catalogResult},
}

// ---------- 编译与挂载 ----------
function resolveSpec(fromFile,spec){
  if(!spec.startsWith('.'))return require.resolve(spec)
  return resolve(dirname(fromFile),spec+'.ts')
}
async function compileSFC(relPath,scriptReplace=[],{render=false}={}){
  const filename=resolve(relPath)
  const {descriptor}=parse(readFileSync(filename,'utf8'),{filename})
  const id='indep-'+basename(relPath).replace(/\W+/g,'-')
  const script=compileScript(descriptor,{id})
  let code=script.content.replace(/import type \{[^}]*\} from ['"][^'"]+['"]\s*;?/g,'')
  code=code.replace(/import AssistPanel from ['"][^'"]+['"]/,'const AssistPanel = {props:[\'binding\',\'api\',\'locate\'],render:() => null}') // T8/T10：面板桩（本文件聚焦表单保护，不测面板）
  for(const [re,rep] of scriptReplace)code=code.replace(re,rep)
  if(render){
    const tpl=compileTemplate({source:descriptor.template.content,filename,id,ssr:true,ssrCssVars:[],compilerOptions:{bindingMetadata:script.bindings}})
    assert.deepEqual(tpl.errors,[])
    code=code.replace('export default ','const Component = ')+'\n'+tpl.code+'\nComponent.ssrRender=ssrRender\nexport default Component\n'
  }
  // 统一把裸导入解析成绝对 URL（含模板编译产物里的 vue 导入）
  code=code.replace(/from (['"])([^'"]+)\1/g,(_,q,spec)=>{
    if(spec==='vue'||spec.startsWith('vue/'))return 'from '+JSON.stringify(pathToFileURL(require.resolve(spec)).href)
    if(spec.startsWith('.'))return 'from '+JSON.stringify(pathToFileURL(resolveSpec(filename,spec)).href)
    return 'from '+JSON.stringify('node:'+spec)
  })
  const file=join(root,basename(relPath).replace(/\.vue$/,'')+'.mjs')
  writeFileSync(file,ts.transpileModule(code,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText)
  return import(pathToFileURL(file).href)
}
async function mount(Component,props,provides){
  let api
  const setup=Component.setup
  Component.setup=(p,c)=>{api=setup(p,c);return api}
  if(!Component.ssrRender)Component.render=()=>null
  const app=createSSRApp(Component,props)
  for(const [k,v] of Object.entries(provides||{}))app.provide(k,v)
  await renderToString(app)
  const html=()=>Component.ssrRender
    ? renderToString(createSSRApp({render:()=>h({props:Component.props,ssrRender:Component.ssrRender,setup:()=>api},props)}))
    : Promise.resolve('')
  return {api,html}
}
const UI_STUBS=[
  [/import\s+AppSelect\s+from\s*['"][^'"]*['"]/,'const AppSelect=globalThis.__uiChain.SelectStub'],
  [/import\s+RegisteredInstances\s+from\s*['"][^'"]*['"]/,'const RegisteredInstances=globalThis.__uiChain.NullComp'],
  [/import\s+MappingDescription\s+from\s*['"][^'"]*['"]/,'const MappingDescription=globalThis.__uiChain.NullComp'],
  [/import\s+SourcePreview\s+from\s*['"][^'"]*['"]/,'const SourcePreview=globalThis.__uiChain.NullComp'],
  [/import\s+RowMenu\s+from\s*['"][^'"]*['"]/,'const RowMenu=globalThis.__uiChain.NullComp'],
  [/import\s*\{[^}]*\}\s*from\s*['"]\.\.\/shared\/appConfirm['"]/,'const {appConfirm}=globalThis.__uiChain'],
  [/import\s*\{[^}]*\}\s*from\s*['"][^'"]*\/flow\/api['"]/,'const {listFlows,loadFlowStateRaw}=globalThis.__uiChain'],
  [/import\s*\{[^}]*\}\s*from\s*['"]\.\/api['"]/,'const {connectionTest,connectionSecret,catalogRefresh}=globalThis.__uiChain'],
]

try{
  // ===================== 1. P03 ObjectSources：来源变更保留 + 未知 kind 只读保留 =====================
  {
    const ObjectSources=(await compileSFC('frontend/src/project/ObjectSources.vue',UI_STUBS,{render:true})).default
    const fields=(...names)=>names.map(name=>({name,dataType:'int',key:'',comment:''}))
    const connections={connections:[{id:'a',name:'库A',engine:'mysql'},{id:'b',name:'库B',engine:'mysql'}]}
    const projectState=reactive({projectId:'p1',connections:deep(connections),bindings:{
      catalogs:{
        a:{database:'energy',refreshedAt:'2026-09-20T00:00:00+00:00',tables:[
          {name:'identity',kind:'table',fields:fields('id','company')},
          {name:'new_identity',kind:'table',fields:fields('id','company','note')}]},
        b:{database:'other',refreshedAt:'2026-09-20T00:00:00+00:00',tables:[{name:'first',kind:'table',fields:fields('external_id','other_value')}]},
      },
      mappingDescriptions:{schemaVersion:1,objects:{'mg:cluster':'簇实例来自库A的身份表，一行一个簇'}},
    }})
    const b=reactive({object_type:'cluster',connection:'a',table:'identity',primary_key:'id',
      sources:[
        {id:'src-1',name:'补充一',kind:'db',connection:'b',table:'first',matchLeft:'company',matchRight:'external_id',cardinality:'one',extraFuture:{keep:true}},
        {id:'src-future',name:'未来来源',kind:'warehouseFromFuture',payload:{columns:['x'],nested:{k:1}},matchLeft:'whatever',matchRight:'anything'},
      ],
      properties:{power:{kind:'field',source:'src-1',field:'other_value'},weird:{kind:'mysteryFromFuture',payload:[1,2]}},
      relations:[{relation:'belongs',target_type:'storage',sourceId:'src-1',field:'external_id',targetSourceId:'',targetField:''}],
      identity:{kind:'database'},extraUnknown:{keep:['x']}})
    const fs1=formSaveStub()
    const {api,html}=await mount(ObjectSources,{projectState,refState:{ontology:{'@graph':[]}},b},{'form-guard':guard,'form-save':fs1.api})
    const descBefore=JSON.stringify(projectState.bindings.mappingDescriptions)
    const snapshot=deep(b)

    // --- 未知 kind：界面只给「移除」、不给「修改」 ---
    const listHtml=await html()
    const rowChunks=listHtml.split('<div class="os-source-row"').slice(1)
    const futureRow=rowChunks.find(c=>c.includes('未来来源'))||''
    const dbRow=rowChunks.find(c=>c.includes('补充一'))||''
    check('未知来源行显示「未识别/原样保留」且没有「修改」入口',!!futureRow&&/移除/.test(futureRow)&&!/>修改</.test(futureRow),futureRow.slice(0,260))
    check('已登记 db 来源行仍有「修改」入口',!!dbRow&&/>修改</.test(dbRow),dbRow.slice(0,260))
    api.openSource(deep(b).sources[1])
    const refuseHtml=await html()
    check('对未知来源执行「修改」被拒绝且不进入编辑表单',/无法识别/.test(refuseHtml)&&!/<h2>登记补充来源<\/h2>/.test(refuseHtml),refuseHtml.slice(0,200))
    check('拒绝打开未知来源不写入任何配置',same(deep(b),snapshot))

    // --- 换表保存：只改身份三键，其余逐字段不变 ---
    api.openIdentity()
    api.identityTableChanged('new_identity')
    check('换表提示改为「原样保留」且不再宣称清空',/原样保留/.test(api.tableChangeNote.value)&&!/将被清空/.test(api.tableChangeNote.value),api.tableChangeNote.value)
    api.identityDraft.value.primary_key='id'
    await api.saveIdentity()
    check('换表保存成功',fs1.calls===1,'calls='+fs1.calls)
    const afterTable=deep(b)
    check('换表后 b 除身份三键外逐字段不变',same(afterTable,{...snapshot,table:'new_identity'}),JSON.stringify(afterTable).slice(0,300))
    check('换表后未知来源对象逐字段不变',same(afterTable.sources[1],snapshot.sources[1]),JSON.stringify(afterTable.sources[1]))
    check('换表后 matchLeft/matchRight 未被清空也未被改名重绑',
      afterTable.sources[0].matchLeft==='company'&&afterTable.sources[0].matchRight==='external_id'&&afterTable.sources[0].table==='first',
      JSON.stringify(afterTable.sources[0]))
    check('换表后属性/链接/说明/未知字段不动',
      same(afterTable.properties,snapshot.properties)&&same(afterTable.relations,snapshot.relations)
      &&same(afterTable.extraUnknown,snapshot.extraUnknown)&&JSON.stringify(projectState.bindings.mappingDescriptions)===descBefore,
      JSON.stringify({p:afterTable.properties,r:afterTable.relations}))

    // --- 换连接保存：同上 ---
    api.openIdentity()
    api.identityConnChanged('b')
    check('换连接清空的只是表单草稿（表与主键待重选）',
      api.identityDraft.value.connection==='b'&&api.identityDraft.value.table===''&&api.identityDraft.value.primary_key==='',
      JSON.stringify(api.identityDraft.value))
    api.identityTableChanged('first')
    api.identityDraft.value.primary_key='external_id'
    await api.saveIdentity()
    check('换连接保存成功',fs1.calls===2,'calls='+fs1.calls)
    const afterConn=deep(b)
    check('换连接后 b 除身份三键外逐字段不变',
      same(afterConn,{...afterTable,connection:'b',table:'first',primary_key:'external_id'}),JSON.stringify(afterConn).slice(0,300))
    check('换连接后补充来源（含未知来源）逐字段不变',same(afterConn.sources,afterTable.sources),JSON.stringify(afterConn.sources))
    check('换连接后连接清单与说明不动',
      same(deep(projectState.connections),connections)&&JSON.stringify(projectState.bindings.mappingDescriptions)===descBefore)

    // --- 失效字段：编辑既有来源时不得自动重绑、不得静默清空（拒绝保存并保留输入） ---
    api.openSource(deep(b).sources[0])
    check('已登记 db 来源进入表单时逐字段带入',same(api.sourceDraft.value,b.sources[0]),JSON.stringify(api.sourceDraft.value))
    api.sourceDraft.value.name='补充一改'
    await api.saveSource()
    check('换表后旧 matchLeft 已不在目录：拒绝保存而不是清空/自动改名',
      fs1.calls===2&&/不在当前身份表目录/.test(api.message.value)&&same(deep(b).sources,afterTable.sources),
      fs1.calls+' / '+api.message.value+' / '+JSON.stringify(b.sources[0]))
    check('拒绝后表单输入与已保存内容都保留',api.sourceDraft.value.name==='补充一改'&&b.sources[0].name==='补充一',
      JSON.stringify(api.sourceDraft.value))

    // --- 保存另一个来源（roundtrip）：未知来源对象不增不减 ---
    api.openSource()
    api.sourceConnChanged('b')
    api.sourceTableChanged('first')
    api.sourceDraft.value.name='新增来源'
    api.sourceDraft.value.matchLeft='external_id'
    api.sourceDraft.value.matchRight='other_value'
    await api.saveSource()
    const afterRoundtrip=deep(b)
    check('新增补充来源保存成功',fs1.calls===3&&afterRoundtrip.sources.length===3,'calls='+fs1.calls+' n='+afterRoundtrip.sources.length)
    check('保存另一来源后未知来源对象逐字段不变',same(afterRoundtrip.sources[1],afterTable.sources[1]),JSON.stringify(afterRoundtrip.sources[1]))
    check('保存另一来源后未知来源键集合不增不减',
      same(Object.keys(afterRoundtrip.sources[1]).sort(),Object.keys(afterTable.sources[1]).sort()),
      JSON.stringify(Object.keys(afterRoundtrip.sources[1])))
    check('保存另一来源不动既有来源与属性/链接',
      same(afterRoundtrip.sources[0],afterTable.sources[0])&&same(afterRoundtrip.properties,afterTable.properties)
      &&same(afterRoundtrip.relations,afterTable.relations),JSON.stringify(afterRoundtrip.sources[0]))

    // --- 未知来源仍可移除，且不牵连其他配置 ---
    fs1.calls=0
    await api.removeSource(b.sources.find(s=>s.id==='src-future'))
    const afterRemove=deep(b)
    check('未知来源可移除（删除入口可用）',fs1.calls===1&&!afterRemove.sources.some(s=>s.id==='src-future'),JSON.stringify(afterRemove.sources))
    check('移除未知来源后属性/链接/说明不动',
      same(afterRemove.properties,afterTable.properties)&&same(afterRemove.relations,afterTable.relations)
      &&JSON.stringify(projectState.bindings.mappingDescriptions)===descBefore,
      JSON.stringify(afterRemove.properties))

    // --- 登记身份块：切回数据库来源只允许动 identity，其余保留 ---
    const beforeRegistered=deep(b)
    b.identity={kind:'registered',instances:[{id:'C1',label:'一号簇'}]}
    api.openIdentity()
    api.identityModeChanged('database')
    check('切回数据库来源未被依赖阻断',!api.switchBlock.value,api.switchBlock.value)
    await api.saveIdentity()
    const afterBack=deep(b)
    check('切回数据库来源只移除登记身份块，其他逐字段不变',
      same(afterBack,Object.fromEntries(Object.entries(beforeRegistered).filter(([k])=>k!=='identity'))),JSON.stringify(afterBack).slice(0,300))
    check('切回数据库来源后身份三键保留（不丢原数据库配置）',
      afterBack.connection==='b'&&afterBack.table==='first'&&afterBack.primary_key==='external_id',
      [afterBack.connection,afterBack.table,afterBack.primary_key].join(','))
    console.log('—— 组 1（P03 来源保留 / 未知 kind）完成')
  }

  // ===================== 2. P05 PropertySources：编排详情缓存与读取失败语义 =====================
  {
    const PropertySources=(await compileSFC('frontend/src/project/PropertySources.vue',UI_STUBS,{render:true})).default
    stubs.calls.flow.length=0
    stubs.flowDefault={state:flowPayload('v1'),revision:'r-1'}
    stubs.flowNext=null
    const graph=[{'@id':'mg:cluster','@type':'owl:Class','rdfs:label':'储能簇'},
      {'@id':'mg:power','@type':'owl:DatatypeProperty','rdfs:domain':{'@id':'mg:cluster'},'rdfs:range':{'@id':'xsd:double'},'rdfs:label':'功率','mg:apiName':'power'}]
    const powerNode=graph[1]
    const projectState=reactive({projectId:'p1',connections:{connections:[]},bindings:{object_bindings:[],catalogs:{}},implementations:[],parameters:{}})
    const b=reactive({object_type:'cluster',connection:'',table:'',primary_key:'id',sources:[],
      properties:{power:{kind:'flow',flow:'flow-a',output:'out_v',inputs:{in_x:{from:'instanceId'}}}},relations:[]})
    const bindingBefore=JSON.stringify(b.properties.power)
    const fs2=formSaveStub()
    const {api,html}=await mount(PropertySources,{projectState,refState:{ontology:{'@graph':graph}},b},{'form-guard':guard,'form-save':fs2.api})
    await tick()
    check('挂载后自动取一次编排详情',stubs.calls.flow.length===1&&stubs.calls.flow[0]==='flow-a',JSON.stringify(stubs.calls.flow))
    check('首次加载后列表状态不是「配置有误」/「编排待读取」',api.statusOf('power',powerNode,b.properties.power).text==='已配置',
      api.statusOf('power',powerNode,b.properties.power).text)

    // 编排实现已改（新 revision + 新声明）：复核必须重新取数，界面要换成新声明
    const callsBefore=stubs.calls.flow.length
    stubs.flowDefault={state:flowPayload('v2'),revision:'r-2'}
    await api.ensureFlowState('flow-a')
    check('复核重新取数（不按 flowId 永久复用旧结果）',stubs.calls.flow.length===callsBefore+1,'calls='+stubs.calls.flow.length)
    check('复核后使用新 revision 的声明',api.flowEntryOf('flow-a')?.revision==='r-2',JSON.stringify(api.flowEntryOf('flow-a')?.revision))
    api.openEditor('power')
    await tick()
    const cfgHtml=await html()
    check('配置页显示新声明的输出、不再显示旧声明',/输出 v2/.test(cfgHtml)&&!/输出 v1/.test(cfgHtml),cfgHtml.replace(/[\s\S]*out_v/,'out_v').slice(0,160))
    check('复核不得改动已保存绑定',JSON.stringify(b.properties.power)===bindingBefore,bindingBefore)
    api.closeEditor()

    // 读取失败（500）：failed ≠ 不存在；不清引用、不显示为配置有误
    stubs.flowDefault={error:'服务暂时不可用',status:500}
    await api.ensureFlowState('flow-a')
    const failedIssues=api.issuesOf('power',powerNode,b.properties.power)
    check('读取失败记为 failed 并带真实原因',api.flowEntryOf('flow-a')?.status==='failed'&&/服务暂时不可用/.test(api.flowEntryOf('flow-a')?.message||''),
      JSON.stringify(api.flowEntryOf('flow-a')))
    check('读取失败提示「读取失败」而非「不存在」',failedIssues.some(t=>/读取失败/.test(t))&&!failedIssues.some(t=>/不存在/.test(t)),JSON.stringify(failedIssues))
    check('读取失败不清空/不改写原绑定',JSON.stringify(b.properties.power)===bindingBefore,bindingBefore)
    check('读取失败列表状态为「编排待读取」而不是配置有误',api.statusOf('power',powerNode,b.properties.power).text==='编排待读取',
      api.statusOf('power',powerNode,b.properties.power).text)
    const failListHtml=await html()
    check('列表渲染同样显示「编排待读取」',/编排待读取/.test(failListHtml)&&!/>已配置</.test(failListHtml),failListHtml.slice(0,240))
    api.openEditor('power')
    await tick()
    const failCfgHtml=await html()
    check('配置页给出「读取失败 + 重试读取」，不宣称已删除',
      /编排定义读取失败/.test(failCfgHtml)&&/重试读取/.test(failCfgHtml)&&!/已被删除/.test(failCfgHtml),
      failCfgHtml.replace(/[\s\S]*编排定义/,'编排定义').slice(0,200))
    check('读取失败后打开表单不改动绑定',JSON.stringify(b.properties.power)===bindingBefore,bindingBefore)

    // 重试读取成功：恢复可用（之前失败没有破坏引用）
    stubs.flowDefault={state:flowPayload('v2'),revision:'r-2'}
    await api.ensureFlowState('flow-a')
    check('重试读取成功后恢复「已配置」且绑定不变',
      api.statusOf('power',powerNode,b.properties.power).text==='已配置'&&JSON.stringify(b.properties.power)===bindingBefore,
      api.statusOf('power',powerNode,b.properties.power).text)
    api.closeEditor()

    // 404：真正的「不存在」按既有失效语义报告，但仍不清空引用
    stubs.flowDefault={error:'编排不存在或已删除',status:404}
    await api.ensureFlowState('flow-a')
    const missingIssues=api.issuesOf('power',powerNode,b.properties.power)
    check('404 记为 missing 并按「不存在」报错',api.flowEntryOf('flow-a')?.status==='missing'&&missingIssues.some(t=>/不存在或已删除/.test(t)),
      JSON.stringify({e:api.flowEntryOf('flow-a'),i:missingIssues}))
    check('404 仍不清空原引用',JSON.stringify(b.properties.power)===bindingBefore,bindingBefore)
    api.openEditor('power')
    await tick()
    const missingCfgHtml=await html()
    check('配置页说明「原绑定保留，不会静默清空」',/不存在或已删除/.test(missingCfgHtml)&&/原绑定保留/.test(missingCfgHtml),missingCfgHtml.replace(/[\s\S]*引用的编排/,'引用的编排').slice(0,200))
    api.closeEditor()
    console.log('—— 组 2（P05 编排详情缓存）完成')
  }

  // ===================== 3. P04 refreshCatalogOf：stale 不得当成功 =====================
  {
    const src=readFileSync(resolve('frontend/src/project/bindingModel.ts'),'utf8')
    let code=src.replace(/import \{ catalogRefresh \} from ['"][^'"]*['"]/,'const {catalogRefresh}=globalThis.__uiChain')
    assert.notEqual(code,src,'bindingModel 的 catalogRefresh 导入未被替换，测试无法注入受控响应')
    const file=join(root,'bindingModel_probe.mjs')
    writeFileSync(file,ts.transpileModule(code,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText)
    const bm=await import(pathToFileURL(file).href)
    const projectState=reactive({projectId:'p1',connections:{connections:[{id:'c1',name:'库A',engine:'mysql'}]},
      bindings:{catalogs:{c1:{database:'old',refreshedAt:'2026-09-19T00:00:00+00:00',tables:[{name:'t_old'}]}}}})
    let mutated=0
    const mutate=fn=>{mutated++;fn()}

    stubs.catalogResult={ok:true,stale:true,database:'energy',tables:[{name:'t_new'}],refreshedAt:'2026-09-20T00:00:00+00:00',message:'探测期间连接配置或凭据已变化'}
    const staleOut=await bm.refreshCatalogOf(projectState,'c1',mutate)
    check('stale 结果返回 ok:false（丢弃≠报成功）',staleOut.ok===false,JSON.stringify(staleOut))
    check('stale 结果不写入本地 catalogs',mutated===0&&projectState.bindings.catalogs.c1.database==='old'
      &&projectState.bindings.catalogs.c1.tables[0].name==='t_old',JSON.stringify(projectState.bindings.catalogs.c1))
    check('stale 结果不谎称目录已刷新',!/已读取|已刷新/.test(staleOut.message),staleOut.message)

    stubs.catalogResult={ok:true,stale:false,database:'energy',tables:[{name:'t_new'}],refreshedAt:'2026-09-20T00:00:00+00:00',message:'已读取 1 张表'}
    const okOut=await bm.refreshCatalogOf(projectState,'c1',mutate)
    check('非 stale 的成功结果照常写入（对照组）',okOut.ok===true&&mutated===1
      &&projectState.bindings.catalogs.c1.tables[0].name==='t_new',JSON.stringify({out:okOut,c:projectState.bindings.catalogs.c1}))

    stubs.catalogResult={ok:false,stale:false,message:'连接失败：拒绝访问'}
    const badOut=await bm.refreshCatalogOf(projectState,'c1',mutate)
    check('目录读取失败保留旧选择且不写本地',badOut.ok===false&&mutated===1&&projectState.bindings.catalogs.c1.tables[0].name==='t_new',JSON.stringify(badOut))
    const missing=await bm.refreshCatalogOf(projectState,'nope',mutate)
    check('未知连接直接拒绝（不发起请求）',missing.ok===false&&mutated===1&&stubs.calls.refresh.length===3,JSON.stringify(missing))
    console.log('—— 组 3（refreshCatalogOf stale）完成')
  }

  // ===================== 4. P04 ConnectionManager：删除先持久化、目录 stale 不报成功 =====================
  {
    const ConnectionManager=(await compileSFC('frontend/src/project/ConnectionManager.vue',UI_STUBS,{render:true})).default
    const projectState=reactive({projectId:'p1',
      connections:{connections:[
        {id:'c1',name:'业务库',engine:'mysql',host:'10.0.0.1',port:3306,database:'energy',username:'reader',tls:'none',credentialRef:'local-vault'},
        {id:'c2',name:'缓存',engine:'redis',host:'10.0.0.9',port:6379,dbIndex:0,credentialRef:''}]},
      bindings:{catalogs:{c1:{database:'energy',tables:[{name:'t_device'}],refreshedAt:'2026-09-20T00:00:00+00:00'}}},
      implementations:[]})
    globalThis.document={querySelector:()=>null}
    const fs4=formSaveStub()
    stubs.confirm=true
    stubs.catalogResult={ok:true,stale:false,database:'energy',tables:[{name:'t_device'}],refreshedAt:'2026-09-20T00:00:00+00:00',message:'已读取 1 张表'}
    const {api,html}=await mount(ConnectionManager,{projectState},{'form-guard':guard,'form-save':fs4.api})

    // 持久化明确失败
    fs4.mode='fail'
    await api.deleteConnection(projectState.connections.connections[0])
    let listHtml=await html()
    check('保存返回 ok:false：连接行仍在',projectState.connections.connections.length===2&&projectState.connections.connections[0].id==='c1')
    check('保存失败提示真实错误、不提示成功',
      /删除失败/.test(api.message.value)&&!/已删除连接/.test(api.message.value)&&api.messageTone.value==='error',api.message.value)
    check('失败提示确实渲染到页面（含「连接已保留」）',/删除失败/.test(listHtml)&&/连接已保留/.test(listHtml)&&!/已删除连接/.test(listHtml),listHtml.slice(-400))

    // 持久化 reject（网络中断）：不得提示成功
    fs4.mode='throw'
    await api.deleteConnection(projectState.connections.connections[0])
    listHtml=await html()
    check('保存 reject：连接行仍在且不提示成功',
      projectState.connections.connections.length===2&&/删除失败/.test(api.message.value)&&!/已删除连接/.test(api.message.value)
      &&!/已删除连接/.test(listHtml),api.message.value)

    // 持久化进行中：不得提前提示成功、不得先清行
    fs4.mode='ok';let release;fs4.gate=new Promise(r=>{release=r})
    const deleting=api.deleteConnection(projectState.connections.connections[0])
    await tick()
    check('持久化进行中不提示成功、行不提前消失',
      !/已删除连接/.test(api.message.value)&&projectState.connections.connections.length===2,api.message.value)
    release();fs4.gate=null
    await deleting
    check('持久化成功后才提示成功并清行',
      projectState.connections.connections.length===1&&projectState.connections.connections[0].id==='c2'
      &&api.messageTone.value==='success'&&/已删除连接/.test(api.message.value),api.message.value)
    check('删除连接同时清掉本地目录副本',projectState.bindings.catalogs.c1===undefined)

    // 目录刷新：stale（服务端丢弃迟到结果）不得提示成功，也不得注入本地目录
    stubs.catalogResult={ok:true,stale:true,database:'energy',tables:[{name:'t_new'}],refreshedAt:'2026-09-20T01:00:00+00:00',message:'探测期间连接配置或凭据已变化'}
    const c2=projectState.connections.connections[0]
    projectState.bindings.catalogs.c2={database:'old',tables:[{name:'t_old'}],refreshedAt:'2026-09-19T00:00:00+00:00'}
    await api.refreshCatalog(c2)
    check('目录 stale：提示按错误处理、不提示成功',
      api.messageTone.value==='error'&&/已变化/.test(api.message.value)&&!/已读取/.test(api.message.value),api.message.value)
    check('目录 stale：不覆盖本地已有目录',projectState.bindings.catalogs.c2.tables[0].name==='t_old',
      JSON.stringify(projectState.bindings.catalogs.c2))
    stubs.catalogResult={ok:true,stale:false,database:'redis',tables:[{name:'k'}],refreshedAt:'2026-09-20T02:00:00+00:00',message:'已读取 1 张表'}
    await api.refreshCatalog(c2)
    check('目录正常结果照常写入（对照组）',
      api.messageTone.value==='success'&&projectState.bindings.catalogs.c2.tables[0].name==='k',
      JSON.stringify({m:api.message.value,c:projectState.bindings.catalogs.c2}))
    console.log('—— 组 4（ConnectionManager 删除/目录 stale）完成')
  }

  console.log(failed?`\n${failed} 项失败`:'\n全部通过')
}finally{rmSync(root,{recursive:true,force:true})}
process.exit(failed?1:0)

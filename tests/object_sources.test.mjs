// 实际 ObjectSources 组件的依赖联动测试；不连接数据库、不写真实用户数据。
// node --import ./tests/ts_hooks.mjs tests/object_sources.test.mjs
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFileSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join,resolve} from 'node:path'
import {pathToFileURL} from 'node:url'
const require=createRequire(new URL('../frontend/package.json',import.meta.url))
const {parse,compileScript}=require('@vue/compiler-sfc')
const ts=require('typescript'),{createSSRApp,reactive}=require('vue'),{renderToString}=require('@vue/server-renderer')
const root=mkdtempSync(join(tmpdir(),'wiz_source_form_'))
process.env.WIZ_WORKBENCH_ROOT=root;process.env.WIZ_WORKBENCH_PORT='18991'
try {
  const filename=resolve('frontend/src/project/ObjectSources.vue')
  const {descriptor}=parse(readFileSync(filename,'utf8'),{filename})
  let code=compileScript(descriptor,{id:'source-test'}).content
  code=code.replace(/import AppSelect from ['"].*?['"]/,'const AppSelect = {}')
  code=code.replace(/import RegisteredInstances from ['"].*?['"]/,'const RegisteredInstances = {}')
  code=code.replace(/import MappingDescription from ['"].*?['"]/,'const MappingDescription = {}')
  code=code.replace(/from (['"])([^'"]+)\1/g,(_,quote,spec)=>{
    const target=spec==='vue'?require.resolve('vue'):resolve('frontend/src/project',spec+'.ts')
    return 'from '+JSON.stringify(pathToFileURL(target).href)
  })
  const file=join(root,'component.mjs')
  writeFileSync(file,ts.transpileModule(code,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText)
  const Component=(await import(pathToFileURL(file).href)).default
  let api,saved=0
  const setup=Component.setup
  Component.setup=(props,ctx)=>{api=setup(props,ctx);return api}
  Component.render=()=>null
  const fields=(...names)=>names.map(name=>({name,dataType:'int',key:'',comment:''}))
  const projectState=reactive({connections:{connections:[{id:'a',engine:'mysql'},{id:'b',engine:'mysql'}]},bindings:{catalogs:{
    a:{tables:[{name:'identity',fields:fields('id','company')},{name:'first',fields:fields('owner_id','first_value')},{name:'second',fields:fields('cluster_id','second_value')}]},
    b:{tables:[{name:'first',fields:fields('external_id','other_value')}]}
  }}})
  const b=reactive({object_type:'cluster',connection:'a',table:'identity',primary_key:'id',sources:[],properties:{},relations:[]})
  const app=createSSRApp(Component,{projectState,refState:{ontology:{'@graph':[]}},b})
  app.provide('form-guard',{register(){},unregister(){}})
  app.provide('form-save',{async submitForm(area,apply){assert.equal(area,'project');apply();saved++;return {ok:true,message:''}}})
  await renderToString(app)
  api.openSource();api.sourceConnChanged('a');api.sourceTableChanged('first')
  api.sourceDraft.value.matchLeft='id';api.sourceDraft.value.matchRight='owner_id'
  api.sourceTableChanged('second')
  assert.equal(api.sourceDraft.value.matchLeft,'id')
  assert.equal(api.sourceDraft.value.matchRight,'')
  assert.deepEqual(api.draftSourceCatalog.value.fields.map(f=>f.name),['cluster_id','second_value'])
  assert.deepEqual(api.identityCatalog.value.fields.map(f=>f.name),['id','company'])
  api.sourceDraft.value.matchRight='cluster_id';api.sourceConnChanged('b')
  assert.equal(api.sourceDraft.value.table,'');assert.equal(api.sourceDraft.value.matchRight,'');assert.equal(api.draftSourceCatalog.value,null)
  api.sourceTableChanged('first')
  assert.deepEqual(api.draftSourceCatalog.value.fields.map(f=>f.name),['external_id','other_value'])
  api.sourceDraft.value.name='补充信息';api.sourceDraft.value.matchRight='owner_id'
  await api.saveSource();assert.equal(saved,0);assert.match(api.message.value,/不在当前补充表目录/)
  api.sourceDraft.value.matchRight='external_id';await api.saveSource();assert.equal(saved,1)
  assert.equal(b.sources[0].kind,'db');assert.equal(b.sources[0].matchLeft,'id');assert.equal(b.sources[0].matchRight,'external_id')
  api.openIdentity();api.identityTableChanged('second')
  assert.equal(api.identityDraft.value.primary_key,'');assert.equal(api.draftIdentityCatalog.value.name,'second')
  api.identityConnChanged('b');assert.equal(api.identityDraft.value.table,'');assert.equal(api.draftIdentityCatalog.value,null)
  console.log('通过：切表更新字段、切连接清空下游、左右字段独立、陈旧字段拒绝保存、有效关联保存、身份来源联动。')
} finally {rmSync(root,{recursive:true,force:true})}

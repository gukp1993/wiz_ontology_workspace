// 规则模板/动态引用验证；只用内存数据。
import assert from 'node:assert/strict'
import {socRule,queryRuleErrors,scadaFields,updateScadaField,reusableScadaRule,ruleInputErrors,migrateScadaRule} from '../frontend/src/project/queryRules.ts'
const fresh=()=>{const r=socRule();r.objectType='cluster';r.connection='db';return r}
let r=fresh();assert.deepEqual(queryRuleErrors(r),[])
assert.equal(r.steps[1].table,'{{steps.point.scadaTable}}')
assert.equal(r.steps[2].select[1].field,'{{steps.storage.sampleField}}')
r.steps[0].where[2].value='1';assert.match(queryRuleErrors(r).join(),/实例主键/)
r=fresh();r.steps[1].table='{{steps.samples.value}}';assert.match(queryRuleErrors(r).join(),/尚未执行/)
r=fresh();r.steps[2].where[1].op='gte';assert.match(queryRuleErrors(r).join(),/结束时间不包含/)
r=fresh();r.steps[0].select[0].as='different';assert.match(queryRuleErrors(r).join(),/输出/)
r=fresh();r.steps[0].table='{{inputs.instanceId}}';assert.match(queryRuleErrors(r).join(),/不能使用调用参数/)
r=fresh();r.steps[0].table='s_attr_scada; DROP TABLE x';assert.match(queryRuleErrors(r).join(),/合法表名/)
console.log('通过：SOC 多步模板、当前实例参数、动态表字段引用、前向引用拒绝、时间边界、失效输出与非法标识校验。')

r=fresh();assert.equal(scadaFields(r).modelId,'{id}')
updateScadaField(r,'modelName','m_other_cluster');updateScadaField(r,'attrName','temperature');updateScadaField(r,'timeField','sample_time')
assert.equal(scadaFields(r).modelName,'m_other_cluster');assert.equal(scadaFields(r).timeField,'sample_time');assert.deepEqual(queryRuleErrors(r),[])
assert.equal(r.steps[2].where[0].field,'sample_time');assert.equal(r.steps[2].where[1].field,'sample_time');assert.equal(r.steps[2].select[1].field,'{{steps.storage.sampleField}}')
const reordered=JSON.parse(JSON.stringify(r,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).reverse()):x));assert.ok(scadaFields(reordered),'服务端重排键仍使用简表')
updateScadaField(r,'modelId','{id}');assert.equal(r.steps[0].where[2].value,'{{inputs.instanceId}}')
r.steps[0].where.push({field:'tenant',op:'eq',value:'42'});assert.equal(scadaFields(r),null,'自定义条件不能被简表覆盖')
console.log('通过：三个输入简表、主键模板转换、时间字段同步过滤、动态采样字段保留、存储往返顺序兼容、自定义规则保留。')

r=reusableScadaRule();r.connection='db';assert.equal(r.objectType,undefined);assert.deepEqual(queryRuleErrors(r),[]);assert.ok(scadaFields(r));assert.equal(r.steps[0].where[0].value,'{{inputs.model_name}}')
assert.deepEqual(ruleInputErrors({model_name:'clusters',attr_name:'soc',model_id:'{id}'},{primary_key:'id'}),[])
assert.match(ruleInputErrors({model_name:'clusters',attr_name:'',model_id:'{id}'},{primary_key:'id'}).join(),/attr_name/)
console.log('通过：通用规则不要求对象，三个参数移至属性绑定校验。')

const original=fresh(),migrated=migrateScadaRule(original);assert.equal(migrated.rule.id,original.id);assert.equal(migrated.rule.schemaVersion,2);assert.equal(original.schemaVersion,1);assert.equal(migrated.rule.objectType,undefined);assert.deepEqual(migrated.inputs,{model_name:'m_storage_cluster_phase',attr_name:'soc',model_id:'{id}'});assert.deepEqual(queryRuleErrors(migrated.rule),[])

const {editableRule,blankRule,renameRuleOutput}=await import('../frontend/src/project/queryRules.ts')
r=editableRule(reusableScadaRule());r.connection='db';assert.deepEqual(queryRuleErrors(r),[])
const sid=r.id;renameRuleOutput(r,r.steps[0],'scadaTable','pointTable');r.steps[0].select[0].as='pointTable';assert.equal(r.steps[1].table,'{{steps.point.pointTable}}');assert.equal(r.id,sid);assert.deepEqual(queryRuleErrors(r),[])
r.steps[0].select.splice(0,1);assert.match(queryRuleErrors(r).join(),/引用了不存在/)
r=blankRule();r.name='读取额定功率';r.connection='db';r.inputs=[{name:'id',type:'double',source:'binding'}];r.steps[0].table='devices';r.steps[0].where=[{field:'id',op:'eq',value:'{{inputs.id}}'}];r.steps[0].select[0].field='rated_power';assert.deepEqual(queryRuleErrors(r),[])
assert.deepEqual(ruleInputErrors({id:'{id}'},{primary_key:'id'},r),[]);assert.match(ruleInputErrors({}, {},r).join(),/id/)
r.inputs=[];assert.match(queryRuleErrors(r).join(),/无效输入参数/)
console.log('通过：V3 自定义参数、单值输出、采样规则转换、输出改名同步引用、删除依赖拦截。')

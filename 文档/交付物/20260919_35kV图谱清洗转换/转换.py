"""一次性、可复跑的图谱转换。仅写本目录；不访问工作台数据库或业务数据源。"""
import sys,json,re,hashlib,uuid,zipfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from workbench.model_format import GROUPS,validate_json,decode_state,encode_state
DEFAULT_NAMESPACES = {'mg': 'https://example.com/microgrid/', 'owl': 'http://www.w3.org/2002/07/owl#', 'rdfs': 'http://www.w3.org/2000/01/rdf-schema#', 'xsd': 'http://www.w3.org/2001/XMLSchema#'}
from workbench.workflow import empty_workflow
from workbench.config_package_format import read_package,parse_manifest
OUT=Path(__file__).resolve().parent
SRC=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/Users/gukepeng/Downloads/35kV_V35kV_v1.jsonId')
raw=SRC.read_bytes(); source=json.loads(raw); graph=source['@graph']; byid={x['@id']:x for x in graph}
assert len(byid)==len(graph),'重复源标识，需要人工检查'
ids_path=OUT/'标识映射.json'
ids=json.loads(ids_path.read_text()) if ids_path.exists() else {}
def uid(key):
 if key not in ids:ids[key]='mg:'+str(uuid.uuid4())
 return ids[key]
def dump(name,value): (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
changes=[]
def clean(value,path=''):
 if not isinstance(value,str):return value
 result='\n'.join(line.rstrip() for line in value.replace('\r\n','\n').replace('\ufeff','').splitlines()).strip()
 if result.endswith('"') and result.count('"')%2:result=result[:-1].rstrip()
 if result!=value:changes.append({'path':path,'change':'清理首尾空白、行尾空白或末尾不配对引号'})
 return result
nodes=[n for n in graph if not n['@type'].endswith('Relationship')]
edges=[n for n in graph if n['@type'].endswith('Relationship')]
for edge in edges:
 assert edge['source']['@id'] in byid and edge['target']['@id'] in byid,edge['@id']
model={'schemaVersion':1,'namespaces':DEFAULT_NAMESPACES.copy(),'metadata':[],'definitionOrder':[],**{k:[] for k in GROUPS}}
workflow=empty_workflow('35kV主变与技改项目本体（清洗待确认）');workflow.update(businessRules=[],businessRuleAssociations=[],actionAssociations=[])
state={'workspaceId':uid('asset:35kv-cleaned').removeprefix('mg:'),'ontology':model,'workflow':workflow,'metrics':{'metrics':[]},'rules':{'rules':[]},'layout':{'positions':{},'zoom':1,'pan':{'x':0,'y':0}}}
notes={
'czy:attribute:01':'枚举码值采用文本保留编码；完整枚举表未提供，不补造。',
'czy:attribute:02':'枚举码值采用文本保留编码；来源字段 professionalSubdivison 原样保留，不擅自纠正拼写。',
'czy:attribute:07':'原文为枚举/数值，规则使用35kV文本；暂用文本，待确认编码和单位后再配置项目转换。',
'czy:attribute:08':'数值年；原公式按天数除365，是否采用周年或闰年口径待确认。',
'czy:attribute:15':'原单位为%，未说明底层存0–1还是0–100；不配置自动乘100。',
'czy:attribute:16':'定义明确为记录集合，暂用数组；元素结构和枚举未提供，不补造约束。',
'czy:attribute:17':'按是/否含义推断布尔值；项目层需把原始码值映射到true/false。',
'czy:attribute:18':'定义明确为事件集合，暂用数组；事件字段和时间范围待项目配置。',
'czy:rule:02':'原文混用语义向量相似度与概率，0.85阈值按原文保留，模型与阈值标定待确认。',
'czy:rule:06':'GB 20052未标版本及具体适用条件；2级或3级的判断边界待业务确认，未核验标准。',
'czy:rule:07':'持续增长、逼近规程临界值没有窗口/阈值定义；原文诊断阈值未核验，不生成可执行控制逻辑。',
'czy:rule:08':'频繁出现、两项严重缺陷的计数范围与去重规则未明确。',
'czy:rule:09':'一票否决或优先改造的结论方向不明确，短路冲击统计口径待确认。',
'czy:rule:10':'容量裕度、短路阻抗未定义为属性，暂不新增。',
'czy:rule:12':'未触发标签不必然代表输入完整；原文缺少缺测/评估失败分支。项目与设备关联路径及多设备合并规则也未明确。'}
outputs={
1:'项目是否符合电网一次老旧项目初筛条件；满足条件时标记为“电网一次老旧项目”。',
2:'项目是否识别为主变老旧更换项目；满足原规则时生成对应标记。',
3:'是否应暂不审查；符合剔除条件时标记为“非纯老旧原因改造”。',
4:'项目是否关联35kV主变；满足条件时标记为“35kV主变关联项目”。',
5:'主变运行年限，单位年，按原规则保留一位小数。',
6:'超期服役、高耗能设备及老旧高耗能主变的判定标签。',
7:'内部放电或过热、固体绝缘老化、铁芯接地风险、绝缘劣化等风险标签与严重内部隐患判定。',
8:'外部缺陷风险标签及运行风险高、需重点治理的判定。',
9:'主体健康度、抗短路能力、可靠性风险标签与重大安全隐患判定；处置方向待确认。',
10:'是否不满足电网发展需求的判定标签。',
11:'是否存在家族缺陷的判定标签。',
12:'项目立项必要性判定及包含关联设备台账ID的反馈描述（按原文模板）。'}
objects={}
for n in nodes:
 if not n['@type'].endswith('EntityNode'):continue
 sid=n['@id'];d=n['data']; desc=clean(d.get('definition',''),sid+'.definition')
 row={'id':uid(sid),'displayName':n['name'],'description':desc,'sourceIds':[sid]}
 if sid in notes:row['reviewNote']=notes[sid]
 model['objectTypes'].append(row);objects[sid]=row
props={}
for n in nodes:
 if not n['@type'].endswith('AttributeNode'):continue
 sid=n['@id'];d=n['data']; num=int(sid.split(':')[-1]);desc=clean(d.get('definition',''),sid+'.definition')
 # 数据类型推断不改变原始业务定义，所有推断可审计。
 dtype={'type':'string' if num<=7 else 'array' if num in (16,18) else 'boolean' if num==17 else 'double'}
 row={'id':uid(sid),'displayName':n['name'],'description':desc,'dataType':dtype,'sourceIds':[sid],
      'reviewNote':notes.get(sid,'数据类型由原定义推断，非来源显式类型声明。')}
 unit=d.get('unit','')
 if unit in ('年','kW','μL/L','mA'):
  row['formatting']={'mode':'natural','instruction':f'显示单位：{unit}。不改变原始数值。'}
 if num==17:row['formatting']={'mode':'natural','instruction':'true显示“是”，false显示“否”；缺失值不当作否。'}
 model['sharedProperties'].append(row);props[sid]=[row]
 changes.append({'path':sid+'.dataType','change':'根据原定义推断类型','target':dtype})
rulemap={}
for n in nodes:
 if not n['@type'].endswith('RuleNode'):continue
 sid=n['@id'];d=n['data']; content=clean(d.get('rule_content',''),sid+'.rule_content');param=clean(d.get('param_intro',''),sid+'.param_intro')
 output=outputs[int(sid.split(':')[-1])]
 description=clean(d.get('definition',''),sid+'.definition')
 if sid=='czy:rule:11' and content.startswith('2）'):
  content='1）'+content[2:];changes.append({'path':sid+'.rule_content','change':'单条规则序号2）规范为1），不改业务条件'})
 fullcontent=content+ ('\n\n参数说明：\n'+param if param else '')
 if d.get('description'):fullcontent+='\n\n来源补充说明：\n'+clean(d['description'],sid+'.description')
 changes.append({'path':sid+'.output','change':'从原有条件和结论归纳输出说明，不新增执行结果或算法','target':output})
 row={'id':uid(sid).removeprefix('mg:'),'name':n['name'],'description':description,'content':fullcontent,'output':output}
 workflow['businessRules'].append(row);rulemap[sid]=row
ledger=[];association_pairs=set();rule_pairs=set()
for e in edges:
 s=e['source']['@id'];t=e['target']['@id']; kind='保留在来源关系附录（当前本体无对应关系槽位）'; targets=[]
 if s in objects and t in props:
  pair=(s,t)
  if pair not in association_pairs:
   for p in props[t]:
    row={'id':uid(e['@id']+':'+p['id']),'objectTypeId':objects[s]['id'],'sharedPropertyId':p['id'],'apiName':'p_'+p['id'][3:].replace('-',''),'sourceIds':[e['@id']]}
    model['properties'].append(row);targets.append(row['id'])
   association_pairs.add(pair);kind='对象属性引用（共享属性）'
  else:kind='同一对象属性去重；读取方式保留在项目映射参考'
 elif s in objects and t in objects:
  row={'id':uid(e['@id']),'displayName':e['relation'],'sourceObjectTypeId':objects[s]['id'],'targetObjectTypeId':objects[t]['id'],'description':clean(e.get('description',''),e['@id']+'.description'),'sourceIds':[e['@id']],'reviewNote':'原数据未声明基数；保持未设置，需确认一对多/多对一等，不默认推断。'}
  model['linkTypes'].append(row);targets=[row['id']];kind='对象链接（基数待确认）'
 elif s in objects and t in rulemap:
  if (s,t) not in rule_pairs:
   workflow['businessRuleAssociations'].append({'objectTypeId':objects[s]['id'],'ruleId':rulemap[t]['id']});rule_pairs.add((s,t))
  targets=[objects[s]['id'],rulemap[t]['id']];kind='对象规则关联'
 ledger.append({'sourceRelationId':e['@id'],'source':s,'relation':e['relation'],'target':t,'disposition':kind,'targetIds':targets})
# 规则“适用对象”与现有对象名称精确匹配，转为可见关联；不推断隐含多对象范围。
association_evidence=[]
for n in nodes:
 if n['@id'] not in rulemap:continue
 target_name=n['data'].get('applicableObject','').strip()
 matched=[(sid,o) for sid,o in objects.items() if o['displayName']==target_name]
 assert len(matched)==1, (n['@id'],target_name)
 sid,obj=matched[0];rid=rulemap[n['@id']]['id']
 if (sid,n['@id']) not in rule_pairs:
  workflow['businessRuleAssociations'].append({'objectTypeId':obj['id'],'ruleId':rid})
  rule_pairs.add((sid,n['@id']))
 association_evidence.append({'sourceRuleId':n['@id'],'sourceField':'data.applicableObject','sourceValue':target_name,'objectTypeId':obj['id'],'ruleId':rid})
changes.append({'path':'workflow.businessRuleAssociations','change':'按明确适用对象字段建立12条规则关联','evidence':association_evidence})
# 保留全部原始资料和未支持的边，避免随跨工作台迁移丢失；扩展不参与执行。
model['extensions']={'conversionProvenance':{'sourceFile':SRC.name,'sha256':hashlib.sha256(raw).hexdigest(),'sourceVersion':source.get('version'),'reviewRequired':True,'originalGraph':source,'relationDisposition':ledger,'reviewNotes':notes,'ruleAssociationEvidence':association_evidence}}
for group in GROUPS:model['definitionOrder'] += [r['id'] for r in model[group]]
validate_json(model)
assert encode_state(decode_state(state))==state,'协议往返发生数据变化'
dump('本体草稿.json',state);dump('ontology.json',model);dump('标识映射.json',ids)
dump('清洗审计.json',{'sourceSha256':hashlib.sha256(raw).hexdigest(),'sourceNodes':len(nodes),'sourceRelationships':len(edges),'changes':changes,'notes':notes,'relations':ledger,'sourceIdToTargetIds':{n['@id']:[v for k,v in ids.items() if k==n['@id'] or k.startswith(n['@id']+':')] for n in nodes}})
dump('项目映射参考.json',{'executable':False,'note':'保留原始取值说明与冲突，不是可直接保存的项目配置。需选择连接并配置身份、关联、属性取值。','objects':[n for n in nodes if n['@type'].endswith('EntityNode')],'attributes':[n for n in nodes if n['@type'].endswith('AttributeNode')],'rules':[n for n in nodes if n['@type'].endswith('RuleNode')],'relationships':edges})
payload=json.dumps(state,ensure_ascii=False,indent=2).encode();path='models/35kv-cleaned/draft.json'
manifest={'format':'wiz-workbench-config-package','formatVersion':1,'packageId':str(uuid.uuid4()),'producerVersion':'1.0','exportedAt':datetime.now(timezone.utc).isoformat(),'requiredCapabilities':[],'rootSelection':{'models':[state['workspaceId']],'projects':[],'extraFlows':[]},'assets':[{'kind':'model','packageKey':'35kv-cleaned','sourceId':state['workspaceId'],'name':workflow['objective']['name'],'payloadFormat':'workbench-state-1','draftPath':path,'releases':[]}],'dependencyEdges':[],'credentialDeclarations':[],'pendingItems':[{'description':'清洗审阅草稿：请先确认清洗说明中的语义歧义，确认规则阈值、数据编码及项目设备关联后再发布。'}],'credentialsExcluded':True,'files':{path:{'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}}}
with zipfile.ZipFile(OUT/'35kV本体_清洗待确认.zip','w',zipfile.ZIP_DEFLATED) as z:
 z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False));z.writestr(path,payload)
parse_manifest(read_package((OUT/'35kV本体_清洗待确认.zip').read_bytes()))
counts={k:len(model[k]) for k in GROUPS};counts.update(businessRules=len(workflow['businessRules']),businessRuleAssociations=len(workflow['businessRuleAssociations']),actions=0)
dump('转换统计.json',counts);print(json.dumps(counts,ensure_ascii=False))

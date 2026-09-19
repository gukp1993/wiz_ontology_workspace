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
SRC=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/Users/gukepeng/Downloads/储能_V20260814_0002 (3).jsonId')
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
workflow=empty_workflow('储能本体（图谱清洗待确认）');workflow.update(businessRules=[],businessRuleAssociations=[],actionAssociations=[])
state={'workspaceId':uid('asset:storage-cleaned').removeprefix('mg:'),'ontology':model,'workflow':workflow,'metrics':{'metrics':[]},'rules':{'rules':[]},'layout':{'positions':{},'zoom':1,'pan':{'x':0,'y':0}}}
notes={
'czy:entity:01':'源定义称簇为设备集合，但关系明确设备包含簇；本次按关系整理为设备的组成单元。',
'czy:entity:04':'待确认：名称为园区，实例表和说明却表示储能电站。本次保留名称，不把电站和园区合并认定。',
'czy:entity:06':'源文件缺少业务定义；本次不补造。',
'czy:attribute:03':'待确认：容量(kWh)减功率(kW)量纲不一致；包含上调/下调多个量，暂用结构体，不定义可执行公式或字段契约。',
'czy:attribute:05':'待确认：原单位kW，但计算示例是百分比；对象适用说明仅系统，连线却包含设备及簇。本次按连线保留关联。',
'czy:attribute:06':'待确认：设备status和簇alarm的编码不一致，尚未定义统一状态枚举。',
'czy:attribute:07':'同时包含充电量、放电量，暂用结构体；最终是否拆属性及统计区间需确认。',
'czy:attribute:15':'SOC定义采用0–100%，剩余电量计算需要 capacity × SOC / 100；原取值说明省略了 /100，本次只标注问题，不生成执行公式。',
'czy:rule:17':'参数说明与按簇聚合的SOC口径有冲突，需确认簇SOC是否参与设备SOC计算。',
'czy:rule:18':'项目层取值规则：仅保留文本参考，不代表本体有可执行SQL。应在项目配置中落实。',
'czy:rule:20':'原文件归为规则，但只有计划结果表说明、缺少生成规则。保留为不完整草稿，建议确认后建计划对象。',
'czy:rule:05':'策略描述15分钟96点，但计划结果表说明5分钟点；需确认生成粒度、落库粒度及转换关系。'}
objects={}
for n in nodes:
 if not n['@type'].endswith('EntityNode'):continue
 sid=n['@id'];d=n['data']; desc=clean(d.get('definition',''),sid+'.definition')
 if sid.endswith('entity:01'):desc='储能设备的组成单元，按簇粒度管理。'
 elif sid.endswith('entity:02'):desc='由一定业务范围内的储能设备组成的逻辑集合，用于描述储能整体。'
 else:desc=re.sub(r'有独立实例表\s+\w+[。.]?','',desc)
 row={'id':uid(sid),'displayName':n['name'],'description':desc,'sourceIds':[sid]}
 if sid in notes:row['reviewNote']=notes[sid]
 model['objectTypes'].append(row);objects[sid]=row
props={}
for n in nodes:
 if not n['@type'].endswith('AttributeNode'):continue
 sid=n['@id'];d=n['data']; num=int(sid.split(':')[-1]);desc=clean(d.get('definition',''),sid+'.definition')
 # 原始图谱无类型字段；以下类型是明确记录的转换推断，非来源既有声明。
 variants=[('realtime','储能SOC实时值',{'type':'double'}),('sampled','储能SOC采样值',{'type':'timeSeries','valueType':'double'})] if num==1 else [('',n['name'], {'type':'timeSeries','valueType':'double'} if num in (12,13,14) else {'type':'string' if num==6 else 'struct' if num in (3,7) else 'double'})]
 props[sid]=[]
 for suffix,name,dtype in variants:
  description=desc
  if num==1:description='电池的剩余电量百分比（SOC），以0–100表示。'+('表示当前最新观测值。' if suffix=='realtime' else '表示随观测时间记录的采样序列，每条包含时标和SOC值。')
  row={'id':uid(sid+(':'+suffix if suffix else '')),'displayName':name,'description':description,'dataType':dtype,'sourceIds':[sid], 'reviewNote':notes.get(sid,'数据类型由原定义推断；源文件未提供的业务定义保持为空。' if not desc else '数据类型由原定义推断。')}
  if d.get('unit') and num not in (3,5,7):row['formatting']={'mode':'natural','instruction':f"显示单位：{d['unit']}。不改变原始数值。"}
  model['sharedProperties'].append(row);props[sid].append(row)
rulemap={}
for n in nodes:
 if not n['@type'].endswith('RuleNode'):continue
 sid=n['@id'];d=n['data']; content=clean(d.get('rule_content',''),sid+'.rule_content');param=clean(d.get('param_intro',''),sid+'.param_intro')
 out=re.search(r'输出[：:]\s*([\s\S]*)',content)
 output=out.group(1) if out else {'czy:rule:18':'采样时间与采样值组成的序列。','czy:rule:19':'所选对象范围内的实时功率、额定功率或采样功率序列。','czy:rule:21':'储能SOC采样时间与SOC值组成的序列。'}.get(sid,'')
 description=clean(d.get('definition',''),sid+'.definition')
 if not description and sid=='czy:rule:19':description='描述不同对象范围内实时功率、额定功率和采样功率的取值口径。'
 fullcontent=content+ ('\n\n参数说明：\n'+param if param else '')
 if d.get('description'):fullcontent+='\n\n来源补充说明：\n'+clean(d['description'],sid+'.description')
 # 不把表说明伪装成计划生成逻辑；该条明确保留空规则以阻止误发布。
 if sid=='czy:rule:20':fullcontent=''
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
# 保留全部原始资料和未支持的边，避免随跨工作台迁移丢失；扩展不参与执行。
model['extensions']={'conversionProvenance':{'sourceFile':SRC.name,'sha256':hashlib.sha256(raw).hexdigest(),'sourceVersion':source.get('version'),'reviewRequired':True,'originalGraph':source,'relationDisposition':ledger,'reviewNotes':notes}}
for group in GROUPS:model['definitionOrder'] += [r['id'] for r in model[group]]
validate_json(model)
assert encode_state(decode_state(state))==state,'协议往返发生数据变化'
dump('本体草稿.json',state);dump('ontology.json',model);dump('标识映射.json',ids)
dump('清洗审计.json',{'sourceSha256':hashlib.sha256(raw).hexdigest(),'sourceNodes':len(nodes),'sourceRelationships':len(edges),'changes':changes,'notes':notes,'relations':ledger,'sourceIdToTargetIds':{n['@id']:[v for k,v in ids.items() if k==n['@id'] or k.startswith(n['@id']+':')] for n in nodes}})
dump('项目映射参考.json',{'executable':False,'note':'保留原始取值说明与冲突，不是可直接保存的项目配置。需选择连接并配置身份、关联、属性取值。','objects':[n for n in nodes if n['@type'].endswith('EntityNode')],'attributes':[n for n in nodes if n['@type'].endswith('AttributeNode')],'rules':[n for n in nodes if n['@type'].endswith('RuleNode')],'relationships':edges})
payload=json.dumps(state,ensure_ascii=False,indent=2).encode();path='models/storage-cleaned/draft.json'
manifest={'format':'wiz-workbench-config-package','formatVersion':1,'packageId':str(uuid.uuid4()),'producerVersion':'1.0','exportedAt':datetime.now(timezone.utc).isoformat(),'requiredCapabilities':[],'rootSelection':{'models':[state['workspaceId']],'projects':[],'extraFlows':[]},'assets':[{'kind':'model','packageKey':'storage-cleaned','sourceId':state['workspaceId'],'name':workflow['objective']['name'],'payloadFormat':'workbench-state-1','draftPath':path,'releases':[]}],'dependencyEdges':[],'credentialDeclarations':[],'pendingItems':[{'description':'清洗审阅草稿：请先确认清洗说明中的语义歧义，补齐定义和链接基数后再发布。'}],'credentialsExcluded':True,'files':{path:{'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}}}
with zipfile.ZipFile(OUT/'储能本体_清洗待确认.zip','w',zipfile.ZIP_DEFLATED) as z:
 z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False));z.writestr(path,payload)
parse_manifest(read_package((OUT/'储能本体_清洗待确认.zip').read_bytes()))
counts={k:len(model[k]) for k in GROUPS};counts.update(businessRules=len(workflow['businessRules']),businessRuleAssociations=len(workflow['businessRuleAssociations']),actions=0)
dump('转换统计.json',counts);print(json.dumps(counts,ensure_ascii=False))

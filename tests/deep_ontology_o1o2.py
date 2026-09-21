# -*- coding: utf-8 -*-
"""Q02 深度测试套件 1：O1 对象/属性 + O2 链接（真实 /api/*，服务已在 18931）。

运行：.runtime/venv/bin/python tests/deep_ontology_o1o2.py
"""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep_ontology_client import Api, Recorder, brief  # noqa: E402
from deep_ontology_common import (obj, private_prop, shared_prop, link,  # noqa: E402
                                  state_with, schema_of, pick_schema, diff_records, find_record)

API = Api()
REC = Recorder('o1o2-graph')


def create_ontology(name):
    name = (name + '-' + uuid.uuid4().hex[:6])[:80]
    r = API.post('/api/ontologies', {'name': name})
    assert r['status'] == 201, f'创建本体失败 {r["status"]} {brief(r["json"])}'
    return r['json']['id']


def get_state(oid):
    r = API.get('/api/state?ontology=' + oid)
    assert r['status'] == 200, brief(r['json'])
    return r['json']


def save(oid, state, revision=None):
    state = json.loads(json.dumps(state, ensure_ascii=False, default=str))
    state['workspaceId'] = oid
    rev = revision if revision is not None else get_state(oid)['revision']
    return API.post('/api/save', {'state': state, 'revision': rev})


def case(cid, title, cond, evidence, kind=''):
    REC.add(cid, 'pass' if cond else 'fail', title, evidence, kind)


# ---------------------------------------------------------------- O1 对象/属性
oid = create_ontology('Q02-S1-O1对象属性')

# O1-01 新建对象（名称+业务定义齐全）保存并读回
st = state_with([obj('DevA', '储能设备A', '站级储能设备对象'),
                 private_prop('socA', '荷电状态', 'DevA', xsd='double')])
resp = save(oid, st)
ok = resp['status'] == 200 and not resp['json'].get('errors')
back = get_state(oid)['state']
got = pick_schema(back)
sent = pick_schema(schema_of(st))
probs = diff_records(sent, got)
case('O1-01', '新建对象+私有属性 保存200且读回逐字段一致', ok and not probs,
     f'save={resp["status"]} errors={brief(resp["json"].get("errors") if resp["json"] else "")} diff={probs} 读回objectTypes={brief(got["objectTypes"])}')

# O1-02 缺名称/缺业务定义：validate 报错、草稿仍可存（200+errors）、发布被 422 阻断
bad = state_with([{'@id': 'mg:DevNoName', '@type': 'owl:Class'}])
resp = save(oid, bad)
save_ok = resp['status'] == 200 and any('缺少名称' in e for e in (resp['json'] or {}).get('errors', []))
v = API.post('/api/validate', {'state': dict(bad, workspaceId=oid)})
val_ok = v['status'] == 200 and any('mg:DevNoName' in e and '缺少名称' in e for e in v['json']['errors'])
rev = get_state(oid)['revision']
pub = API.post('/api/publish', {'state': dict(bad, workspaceId=oid), 'revision': rev})
pub_ok = pub['status'] == 422 and any('缺少名称' in e for e in (pub['json'] or {}).get('errors', []))
case('O1-02', '缺名称对象：草稿允许带错保存(200+errors)，发布被422阻断', save_ok and val_ok and pub_ok,
     f'save={resp["status"]}/{brief((resp["json"] or {}).get("errors"))} validate={v["status"]}/{brief(v["json"]["errors"] if v["json"] else "")} publish={pub["status"]}/{brief((pub["json"] or {}).get("errors"))}')

# O1-03 私有属性 dataType timeSeries（schema 形态直发）保存并读回
st_ts = schema_of(state_with([obj('DevTS', '时间序列载体'),
                              private_prop('seriesP', '功率序列', 'DevTS', xsd='double', series=True)]))
resp = save(oid, st_ts)
back = get_state(oid)['state']
rec = find_record(back, 'properties', 'mg:seriesP')
ok = (resp['status'] == 200 and rec is not None
      and rec.get('dataType') == {'type': 'timeSeries', 'valueType': 'double'})
case('O1-03', 'timeSeries 属性（dataType 描述）保存后读回 dataType 等价', ok,
     f'save={resp["status"]} 读回记录={brief(rec)}')

# O1-04 timeSeries 非法 valueType → 400 且零写入（revision/内容不变）
bad_ts = json.loads(json.dumps(st_ts))
for r in bad_ts['ontology']['properties']:
    if r['id'] == 'mg:seriesP':
        r['dataType'] = {'type': 'timeSeries', 'valueType': 'banana'}
before = get_state(oid)
resp = save(oid, bad_ts, revision=before['revision'])
after = get_state(oid)
ok = (resp['status'] == 400 and 'valueType' in json.dumps(resp['json'], ensure_ascii=False)
      and after['revision'] == before['revision'])
case('O1-04', '非法 timeSeries valueType → 400 报错且零写入', ok,
     f'save={resp["status"]} body={brief(resp["json"])} revision前后一致={after["revision"] == before["revision"]}')

# O1-05 共享属性创建 + 两对象引用
sp_graph = [shared_prop('cap', '容量', 'decimal'),
            obj('DevB', '设备B'), obj('DevC', '设备C'),
            private_prop('capB', '容量(B)', 'DevB', xsd='decimal', shared_ref='mg:cap'),
            private_prop('capC', '容量(C)', 'DevC', xsd='decimal', shared_ref='mg:cap')]
base = get_state(oid)['state']
st_sp = state_with(sp_graph)
# 保留 DevTS/seriesP 等既有定义，避免把读回的 schema 形态和 @graph 混发：直接整体用 schema 合并
merged = schema_of(state_with(sp_graph))
merged['ontology']['objectTypes'] = base['ontology']['objectTypes'] + merged['ontology']['objectTypes']
merged['ontology']['properties'] = base['ontology']['properties'] + merged['ontology']['properties']
merged['ontology']['definitionOrder'] = base['ontology']['definitionOrder'] + merged['ontology']['definitionOrder']
resp = save(oid, merged)
back = get_state(oid)['state']
sp = find_record(back, 'sharedProperties', 'mg:cap')
b = find_record(back, 'properties', 'mg:capB')
c = find_record(back, 'properties', 'mg:capC')
ok = (resp['status'] == 200 and sp and b and c
      and b.get('sharedPropertyId') == 'mg:cap' and c.get('sharedPropertyId') == 'mg:cap')
case('O1-05', '共享属性被两个对象引用，保存200且读回 sharedPropertyId 均指向共享属性', ok,
     f'save={resp["status"]}/{brief((resp["json"] or {}).get("errors"))} capB={brief(b)} capC={brief(c)}')

# O1-06 改共享属性显示名不断链（引用按稳定 id）
back2 = json.loads(json.dumps(back))
for r in back2['ontology']['sharedProperties']:
    if r['id'] == 'mg:cap':
        r['displayName'] = '可用容量(改名)'
resp = save(oid, back2)
after = get_state(oid)['state']
sp = find_record(after, 'sharedProperties', 'mg:cap')
b = find_record(after, 'properties', 'mg:capB')
c = find_record(after, 'properties', 'mg:capC')
ok = (resp['status'] == 200 and sp and sp.get('displayName') == '可用容量(改名)'
      and b.get('sharedPropertyId') == 'mg:cap' and c.get('sharedPropertyId') == 'mg:cap')
case('O1-06', '共享属性改显示名后引用不断链', ok,
     f'save={resp["status"]} sp={brief(sp)} 引用={b.get("sharedPropertyId")}/{c.get("sharedPropertyId")}')

# O1-07 删除仍被引用的共享属性 → 422 BROKEN_REFERENCE + 零写入
back3 = json.loads(json.dumps(after))
back3['ontology']['sharedProperties'] = [r for r in back3['ontology']['sharedProperties'] if r['id'] != 'mg:cap']
back3['ontology']['definitionOrder'] = [x for x in back3['ontology']['definitionOrder'] if x != 'mg:cap']
before = get_state(oid)
resp = save(oid, back3, revision=before['revision'])
after_state = get_state(oid)
still = find_record(after_state['state'], 'sharedProperties', 'mg:cap')
ok = (resp['status'] == 422 and (resp['json'] or {}).get('code') == 'BROKEN_REFERENCE'
      and after_state['revision'] == before['revision'] and still is not None)
case('O1-07', '删除仍被引用的共享属性 → 422 BROKEN_REFERENCE 且草稿零写入', ok,
     f'save={resp["status"]} body={brief(resp["json"])} revision未变={after_state["revision"] == before["revision"]} 共享属性仍在={still is not None}')

# O1-08 先解除两对象引用再删共享属性 → 200 保存成功
back4 = json.loads(json.dumps(after))
back4['ontology']['properties'] = [{k: v for k, v in r.items() if k != 'sharedPropertyId'}
                                   for r in back4['ontology']['properties']]
back4['ontology']['sharedProperties'] = [r for r in back4['ontology']['sharedProperties'] if r['id'] != 'mg:cap']
back4['ontology']['definitionOrder'] = [x for x in back4['ontology']['definitionOrder'] if x != 'mg:cap']
resp = save(oid, back4)
final = get_state(oid)['state']
ok = (resp['status'] == 200 and find_record(final, 'sharedProperties', 'mg:cap') is None)
case('O1-08', '解除关联后删除共享属性 → 保存200，读回已不存在', ok,
     f'save={resp["status"]}/{brief(resp["json"])} errors={brief((resp["json"] or {}).get("errors"))}')

# ---------------------------------------------------------------- O2 链接
oid2 = create_ontology('Q02-S1-O2链接')
st = state_with([obj('Pack', '电池包'), obj('Cell', '电芯'),
                 link('packHasCell', '包含电芯', 'mg:Pack', 'mg:Cell', 'one-to-many', '属于电池包')])
resp = save(oid2, st)
back = get_state(oid2)['state']
lk = find_record(back, 'linkTypes', 'mg:packHasCell')
ok = (resp['status'] == 200 and lk
      and lk.get('sourceObjectTypeId') == 'mg:Pack' and lk.get('targetObjectTypeId') == 'mg:Cell'
      and lk.get('cardinality') == 'one-to-many' and lk.get('reverseDisplayName') == '属于电池包')
case('O2-01', '链接方向/基数/反向名保存并逐字段读回一致', ok,
     f'save={resp["status"]} 读回link={brief(lk)}')

# O2-02 非法基数：validate 报错（发布阻断），草稿可存
st_bad = json.loads(json.dumps(back))
for r in st_bad['ontology']['linkTypes']:
    if r['id'] == 'mg:packHasCell':
        r['cardinality'] = 'many-to-whatever'
v = API.post('/api/validate', {'state': dict(st_bad, workspaceId=oid2)})
val_ok = v['status'] == 200 and any('链接数量关系无效' in e for e in v['json']['errors'])
rev = get_state(oid2)['revision']
pub = API.post('/api/publish', {'state': dict(st_bad, workspaceId=oid2), 'revision': rev})
save_r = save(oid2, st_bad, revision=rev)
ok = val_ok and pub['status'] == 422 and save_r['status'] == 200
case('O2-02', '非法基数 → validate报错、发布422、草稿仍可存(200+errors)', ok,
     f'validate={brief(v["json"]["errors"] if v["json"] else "")} publish={pub["status"]} save={save_r["status"]}/{brief((save_r["json"] or {}).get("errors"))}')

# O2-03 引用不存在对象：validate 报错且保存 422 零写入（schema 形态记录）
st_ghost = json.loads(json.dumps(back))
st_ghost['ontology']['linkTypes'].append(
    {'id': 'mg:ghostLink', 'displayName': '指向幽灵', 'sourceObjectTypeId': 'mg:Pack',
     'targetObjectTypeId': 'mg:GhostType', 'cardinality': 'many-to-one', 'reverseDisplayName': '被指'})
st_ghost['ontology']['definitionOrder'].append('mg:ghostLink')
v = API.post('/api/validate', {'state': dict(st_ghost, workspaceId=oid2)})
val_ok = v['status'] == 200 and any('引用不存在的对象类型' in e and 'mg:GhostType' in e for e in v['json']['errors'])
before = get_state(oid2)
save_r = save(oid2, st_ghost, revision=before['revision'])
after = get_state(oid2)
ok = (val_ok and save_r['status'] == 422 and (save_r['json'] or {}).get('code') == 'BROKEN_REFERENCE'
      and after['revision'] == before['revision'])
case('O2-03', '链接引用不存在对象 → validate 报错 + 保存422 零写入', ok,
     f'validate={brief(v["json"]["errors"] if v["json"] else "")} save={save_r["status"]}/{brief(save_r["json"])} 未写入={after["revision"] == before["revision"]}')

REC.dump()
print('\nO1/O2 套件使用的本体ID：', oid, oid2)

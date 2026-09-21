# -*- coding: utf-8 -*-
"""Q02 深度测试套件 2：O3 规则/动作（含 A01 专项）+ O4 校验/发布/恢复 + O5 协议反例。

运行：.runtime/venv/bin/python tests/deep_ontology_o3o4o5.py
"""
import io
import json
import sys
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep_ontology_client import Api, Recorder, brief  # noqa: E402
from deep_ontology_common import (obj, private_prop, state_with, schema_of,  # noqa: E402
                                  rule, action_v2, find_record)
import deep_verdicts as V  # noqa: E402
from deep_reverify_scenarios import scenario_a01  # noqa: E402

API = Api()
REC = Recorder('o3o4o5-rules-publish')
FAILED_WITH_EXC = []


def create_ontology(name):
    r = API.post('/api/ontologies', {'name': (name + '-' + uuid.uuid4().hex[:6])[:80]})
    assert r['status'] == 201, f'创建本体失败 {r["status"]} {brief(r["json"])}'
    return r['json']['id']


def get_state(oid):
    r = API.get('/api/state?ontology=' + oid)
    assert r['status'] == 200, brief(r['json'])
    return r['json']


def save(oid, state, revision=None):
    st = json.loads(json.dumps(state, ensure_ascii=False, default=str))
    st['workspaceId'] = oid
    rev = revision if revision is not None else get_state(oid)['revision']
    return API.post('/api/save', {'state': st, 'revision': rev})


def case(cid, title, cond, evidence, kind=''):
    REC.add(cid, 'pass' if cond else 'fail', title, evidence, kind)


def known(cid, title, evidence, kind='A01'):
    REC.add(cid, 'known', title, evidence, kind)


# 干净可发布的最小状态：一个对象 + 一个属性
def clean_state(rules=None, actions=None):
    st = state_with([obj('DevQ', '设备Q', '用于规则动作测试的对象'),
                     private_prop('tempQ', '温度', 'DevQ', xsd='double')])
    if rules is not None:
        st['workflow']['businessRules'] = rules
    if actions is not None:
        st['workflow']['actions'] = actions
    return st


# ---------------------------------------------------------------- O3 规则/动作
oid3 = create_ontology('Q02-S2-O3规则动作')

# O3-01 规则缺名称/缺业务定义 → validate 报必填、发布 422；trim 判空
no_name = clean_state(rules=[rule('r-noname', name='  ', description='有定义')])
no_desc = clean_state(rules=[rule('r-nodesc', name='有名称', description='')])
v1 = API.post('/api/validate', {'state': dict(no_name, workspaceId=oid3)})
v2 = API.post('/api/validate', {'state': dict(no_desc, workspaceId=oid3)})
ok = (any('缺少名称' in e for e in v1['json']['errors'])
      and any('缺少业务定义' in e for e in v2['json']['errors']))
rev = get_state(oid3)['revision']
pub = API.post('/api/publish', {'state': dict(no_name, workspaceId=oid3), 'revision': rev})
ok = ok and pub['status'] == 422 and any('缺少名称' in e for e in (pub['json'] or {}).get('errors', []))
case('O3-01', '规则 name/description 必填（trim判空）：validate报错、发布422', ok,
     f'validate(缺名)={brief(v1["json"]["errors"])} validate(缺定义)={brief(v2["json"]["errors"])} publish={pub["status"]}/{brief((pub["json"] or {}).get("errors"))}')

# O3-02 动作 v2 缺名称/业务定义 → 报错；content/effect 选填不阻断
a_bad = clean_state(actions=[{'id': 'a-bad', 'name': '', 'description': 'x', 'status': 'active', 'definitionVersion': 2}])
va = API.post('/api/validate', {'state': dict(a_bad, workspaceId=oid3)})
a_min = clean_state(actions=[action_v2('a-min', '最小动作', '只填必填两项')])
vm = API.post('/api/validate', {'state': dict(a_min, workspaceId=oid3)})
ok = any('缺少名称' in e for e in va['json']['errors']) and not vm['json']['errors']
case('O3-02', '动作 v2：名称/业务定义必填，仅必填两项即通过校验', ok,
     f'validate(缺名)={brief(va["json"]["errors"])} validate(最小)={brief(vm["json"]["errors"])}')

# O3-03 content/effect 空串与缺键：选填语义（文档 §4.9：空值按未填处理，不阻断）
empty_opt = clean_state(rules=[rule('r-empty', content='   ')],
                        actions=[action_v2('a-opt', effect=None)])
ve = API.post('/api/validate', {'state': dict(empty_opt, workspaceId=oid3)})
sr = save(oid3, empty_opt)
rev = get_state(oid3)['revision']
pub = API.post('/api/publish', {'state': dict(empty_opt, workspaceId=oid3), 'revision': rev})
ok = (not ve['json']['errors']) and sr['status'] == 200 and pub['status'] == 200
case('O3-03', 'content/effect 空白或 null 按选填处理，保存发布均不阻断', ok,
     f'validate={brief(ve["json"]["errors"])} save={sr["status"]} publish={pub["status"]}/{brief(pub["json"])}')

# O3-04 A01 专项（修订：前置合法→追加非法→统一判定；未复现分支不再固定 fail）
# 判定与 tests/deep_reverify_scenarios.py::scenario_a01 共用（deep_verdicts R1–R7）：
#  - 前置失败 → blocked；500/无关4xx/空响应 → test_error；
#  - 被接受且非法值进发布快照 → known_defect_reproduced（基线已知，不是产品通过）；
#  - 被正确拒绝（422 + 针对 content/effect 的诊断 + 版本零新增）→ product_pass。
_a01 = scenario_a01(API, REC, oid3, tag='O3-04')

# O3-05 旧规则 output 键零丢失（透传保存与发布快照）
out_state = clean_state(rules=[rule('r-out', output='历史输出说明')])
sr = save(oid3, out_state)
rev = get_state(oid3)['revision']
pub2 = API.post('/api/publish', {'state': dict(out_state, workspaceId=oid3), 'revision': rev,
                                 'requestId': 'q02-out-' + uuid.uuid4().hex})
v2 = (pub2['json'] or {}).get('version', '')
vs2 = API.get(f'/api/version-state?ontology={oid3}&version={v2}') if pub2['status'] == 200 else {'json': None}
kept = any(r['id'] == 'r-out' and r.get('output') == '历史输出说明'
           for r in (vs2['json'] or {}).get('state', {}).get('workflow', {}).get('businessRules', []))
case('O3-05', '旧规则 output 键保存+发布快照零丢失', sr['status'] == 200 and pub2['status'] == 200 and kept,
     f'save={sr["status"]} publish={pub2["status"]}/{brief(pub2["json"])} version={v2} 读回={brief((vs2["json"] or {}).get("state", {}).get("workflow", {}).get("businessRules"))}')

# ---------------------------------------------------------------- O4 校验/发布/恢复
oid4 = create_ontology('Q02-S2-O4发布链')

# O4-01 /api/validate 错误定位：错误串含问题节点 @id（前端按 id 归组跳转）
loc_state = state_with([obj('Good', '正常对象'),
                        {'@id': 'mg:NoLabel', '@type': 'owl:Class'},
                        private_prop('orphan', '孤儿属性', 'NoSuchObj')])
loc_state['workflow']['businessRules'] = [rule('r-401', name='规则缺定义', description='')]
v = API.post('/api/validate', {'state': dict(loc_state, workspaceId=oid4)})
errs = v['json']['errors']
ok = (v['status'] == 200 and any('mg:NoLabel' in e for e in errs)
      and any('mg:orphan' in e for e in errs) and any('r-401' in e or '规则缺定义' in e for e in errs))
case('O4-01', 'validate 错误含稳定 id 定位（NoLabel/orphan/规则标识可归组）', ok, f'errors={brief(errs)}')

# O4-02~05 发布链：initial → compatible(次版本+1) → breaking(主版本+1 且禁改标兼容)
pc0 = API.post('/api/publish-check', {'state': dict(loc_state, workspaceId=oid4)})
st_v1 = clean_state()
sr1 = save(oid4, st_v1)
rev = get_state(oid4)['revision']
pub1 = API.post('/api/publish', {'state': dict(st_v1, workspaceId=oid4), 'revision': rev,
                                 'requestId': 'q02-p1-' + uuid.uuid4().hex})
v1n = (pub1['json'] or {}).get('version', '')
ct1 = (pub1['json'] or {}).get('changeType', '')
ok02 = (pc0['json']['suggested'] == 'initial' and pub1['status'] == 200 and ct1 == 'initial')
case('O4-02', 'publish-check 首发布 suggested=initial，发布版本号 v1 且 changeType=initial', ok02,
     f'precheck={brief(pc0["json"])} publish={pub1["status"]} version={v1n} changeType={ct1}')

st_v2 = schema_of(clean_state())
st_v2['ontology']['objectTypes'].append({'id': 'mg:Extra', 'displayName': '新增对象', 'description': '兼容新增'})
st_v2['ontology']['definitionOrder'].append('mg:Extra')
pc1 = API.post('/api/publish-check', {'state': dict(st_v2, workspaceId=oid4)})
rev = get_state(oid4)['revision']
pub2 = API.post('/api/publish', {'state': dict(st_v2, workspaceId=oid4), 'revision': rev,
                                 'requestId': 'q02-p2-' + uuid.uuid4().hex})
v2n = (pub2['json'] or {}).get('version', '')
ok03 = (pc1['json']['suggested'] == 'compatible' and pub2['status'] == 200
        and v2n != v1n and v2n.split('.')[0] == v1n.split('.')[0])
case('O4-03', '新增对象 suggested=compatible，发布次版本递增', ok03,
     f'precheck={brief(pc1["json"])} {v1n}->{v2n}')

# 删除 mg:Extra → breaking；改标 compatible 被 422 拒绝；按 breaking 发布主版本+1
st_v3 = json.loads(json.dumps(st_v2))
st_v3['ontology']['objectTypes'] = [r for r in st_v3['ontology']['objectTypes'] if r['id'] != 'mg:Extra']
st_v3['ontology']['definitionOrder'] = [x for x in st_v3['ontology']['definitionOrder'] if x != 'mg:Extra']
pc2 = API.post('/api/publish-check', {'state': dict(st_v3, workspaceId=oid4)})
rev = get_state(oid4)['revision']
force = API.post('/api/publish', {'state': dict(st_v3, workspaceId=oid4), 'revision': rev,
                                  'changeType': 'compatible', 'requestId': 'q02-p3f-' + uuid.uuid4().hex})
pub3 = API.post('/api/publish', {'state': dict(st_v3, workspaceId=oid4), 'revision': rev,
                                 'requestId': 'q02-p3-' + uuid.uuid4().hex})
v3n = (pub3['json'] or {}).get('version', '')
ok04 = (pc2['json']['suggested'] == 'breaking' and not pc2['json']['canCompatible']
        and force['status'] == 422 and pub3['status'] == 200
        and v3n.split('.')[0] == str(int(v2n.split('.')[0]) + 1))
case('O4-04', '删除定义 suggested=breaking：强标兼容被422拒，按破坏发布主版本+1', ok04,
     f'precheck={brief(pc2["json"])} forceCompatible={force["status"]}/{brief(force["json"])} 发布={pub3["status"]} {v2n}->{v3n}')

# O4-05 发布后读回：versions 清单 + version-state 内容与发布状态一致 + releases ZIP 清单
vers = API.get('/api/versions?ontology=' + oid4)
vs = API.get(f'/api/version-state?ontology={oid4}&version={v1n}')
rel = API.get('/api/releases?ontology=' + oid4)
v1_back = (vs['json'] or {}).get('state', {}).get('ontology')
expect_v1 = schema_of(clean_state())['ontology']
same = vs['status'] == 200 and vs['json']['version'] == v1n and v1_back == expect_v1
vers_ok = any((item.get('version') == v3n and item.get('changeType') == 'breaking') for item in vers['json']['items'])
ok05 = vs['status'] == 200 and same and vers_ok
case('O4-05', '发布后 versions/version-state 读回一致（v1内容逐字段等于发布状态）', ok05,
     f'versions={brief(vers["json"]["items"])} version-state==v1发布内容={same}')

# O4-05b 新发现候选：在线发布不生成可恢复快照 → /api/releases 恒空、恢复链无入口
ok05b_expected = isinstance(rel['json'], list) and len(rel['json']) == 0
REC.add('O4-05b', 'fail' if ok05b_expected else 'pass',
        '发布后 /api/releases 为空：在线发布未写 release-zip 附件，历史快照恢复对新资产不可达（新发现）',
        f'已发布3个版本 {v1n}/{v2n}/{v3n}；GET /api/releases={brief(rel["json"])}；'
        'release-zip 附件仅由 transfer 迁移写入（workbench/storage/transfer.py），versions.publish 不写；'
        '前端 OntologyRelease.vue 的恢复下拉依赖此端点', 'new')

# O4-06 发布不可变性（修订）：拆成两条，避免把「无入口」写成动态验证通过
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench import server  # noqa: E402
write_like = [p for p in list(server.GET_ROUTES) + list(server.POST_ROUTES)
              if not p.startswith('/api/project') and ('release' in p or 'version' in p)
              and p not in ('/api/versions', '/api/version-state', '/api/releases',
                            '/api/publish-check', '/api/publish')]
_static = V.classify_static_check(not write_like,
                                  '可疑写路径=%s（GET_ROUTES/POST_ROUTES 只读枚举）' % brief(write_like))
REC.add('O4-06', _static['result'],
        'O4-06 静态核对：路由白名单不存在发布版本的改写/删除入口',
        _static['reason'] + '；' + _static['detail'], 'static')
REC.add('O4-06b', V.NOT_TESTED,
        'O4-06b 发布不可变性（破坏路径）未实测：协议无落库写入口可达，不能替代动态验证',
        '发布为不可变快照；本基线只能确认不存在 HTTP 入口，未构造任何绕过或直连存储的破坏尝试', 'unreachable')

# O4-07 /api/restore 真实语义：
#  7a 成功路径——在线发布本体拿不到快照名（见 O4-05b），记阻塞；
#  7b 错误路径独立验证：非法快照名/不存在/他体快照 400；stale revision 409+currentRevision。
zip_name = rel['json'][0] if rel['json'] else ''
st_now = get_state(oid4)
mut = json.loads(json.dumps(st_now['state']))
mut['ontology']['objectTypes'].append({'id': 'mg:MutAfter', 'displayName': '恢复前改动'})
mut['ontology']['definitionOrder'].append('mg:MutAfter')
sr = save(oid4, mut)
rev2 = get_state(oid4)['revision']
if not zip_name:
    REC.add('O4-07a', 'blocked', 'restore 成功路径（恢复快照→内容一致+自动备份）无法执行：无在线可恢复快照',
            f'依赖 O4-05b：GET /api/releases 为空（本体 {oid4} 已发布3版本）。成功路径代码审查：'
            'post_restore 走附件表 PURPOSE_RELEASE_ZIP，仅迁移导入会产生该附件', 'blocked')
else:
    rst = API.post('/api/restore', {'state': dict(mut, workspaceId=oid4), 'revision': rev2, 'release': zip_name})
    body = rst['json'] or {}
    back_state = body.get('state', {}).get('ontology')
    ok07a = (rst['status'] == 200 and 'MutAfter' not in json.dumps(back_state or {}, ensure_ascii=False)
             and body.get('backup') and body.get('note'))
    case('O4-07a', 'restore 成功：恢复快照内容、生成备份、项目配置不回灌', ok07a,
         f'restore={rst["status"]} 快照={zip_name} backup={brief(body.get("backup"))} note={brief(body.get("note"))}')
bad1 = API.post('/api/restore', {'state': dict(mut, workspaceId=oid4), 'revision': get_state(oid4)['revision'],
                                 'release': '../etc/passwd'})
bad2 = API.post('/api/restore', {'state': dict(mut, workspaceId=oid4), 'revision': get_state(oid4)['revision'],
                                 'release': 'no-such-snapshot.zip'})
other = API.get('/api/releases?ontology=' + oid3)['json']
bad3 = API.post('/api/restore', {'state': dict(get_state(oid4)['state'], workspaceId=oid4),
                                 'revision': get_state(oid4)['revision'],
                                 'release': (other or ['ghost.zip'])[0]})
stale = API.post('/api/restore', {'state': dict(mut, workspaceId=oid4), 'revision': 'r-stale-token',
                                  'release': zip_name or 'any.zip'})
ok07b = (bad1['status'] == 400 and bad2['status'] == 400 and bad3['status'] == 400
         and stale['status'] == 409 and 'currentRevision' in (stale['json'] or {}))
case('O4-07b', 'restore 错误路径：非法名/不存在/他体快照400（快照按本体归属隔离），过期revision 409+currentRevision',
     ok07b,
     f'非法路径={bad1["status"]}/{brief(bad1["json"])} 不存在={bad2["status"]}/{brief(bad2["json"])} '
     f'他体={bad3["status"]}/{brief(bad3["json"])} stale={stale["status"]}/{brief(stale["json"])}')

# ---------------------------------------------------------------- O5 协议反例
oid5 = create_ontology('Q02-S2-O5协议反例')
base_schema = schema_of(state_with([obj('P5', '对象5'), private_prop('pr5', '属性5', 'P5')]))
sr0 = save(oid5, base_schema)

# O5-01 definitionOrder 缺项 → 400（非 500），零写入
bad_order = json.loads(json.dumps(base_schema))
bad_order['ontology']['definitionOrder'] = bad_order['ontology']['definitionOrder'][:-1]
before = get_state(oid5)
r = save(oid5, bad_order, revision=before['revision'])
after = get_state(oid5)
ok = r['status'] == 400 and 'definitionOrder' in json.dumps(r['json'], ensure_ascii=False) and after['revision'] == before['revision']
case('O5-01', 'definitionOrder 缺项 → 400 INVALID_ARGUMENT（非500）且零写入', ok,
     f'status={r["status"]} body={brief(r["json"])} revision未变={after["revision"] == before["revision"]}')

# O5-02 revision 重放：同一旧 token 二次保存 → 第二次 409 + currentRevision
rev0 = get_state(oid5)['revision']
s1 = json.loads(json.dumps(base_schema)); s1['workflow']['objective']['question'] = '版本1'
s2 = json.loads(json.dumps(base_schema)); s2['workflow']['objective']['question'] = '版本2'
r1 = save(oid5, s1, revision=rev0)
r2 = save(oid5, s2, revision=rev0)  # 重放旧 token
cur = get_state(oid5)
ok = (r1['status'] == 200 and r2['status'] == 409
      and (r2['json'] or {}).get('code') == 'REVISION_CONFLICT'
      and (r2['json'] or {}).get('currentRevision') == cur['revision'])
case('O5-02', 'revision 重放 → 409 REVISION_CONFLICT 且 currentRevision 为服务端最新', ok,
     f'r1={r1["status"]} r2={r2["status"]}/{brief(r2["json"])} 服务端revision={cur["revision"]}')

# O5-03 A→B→A：内容回到 A 但用旧 token 仍必须被拒（token 与内容 hash 分离）
rev_b = r1['json']['revision']
r3 = save(oid5, s1, revision=rev_b)  # 内容=A，token=B → 应成功（新 token 提交旧内容）
r4 = save(oid5, s1, revision=rev0)   # 内容=A，token=A(旧) → 必须 409
ok = r3['status'] == 200 and r4['status'] == 409
case('O5-03', 'A→B→A：旧内容+最新token可提交；旧内容+旧token必被409拒（不依赖内容hash）', ok,
     f'新token={r3["status"]} 旧token={r4["status"]}/{brief(r4["json"])}')

# O5-04 未知字段：顶层记录带未识别字段 → 明确 400 拒绝（文档允许：拒绝或 extensions 零丢失）
bad_field = json.loads(json.dumps(base_schema))
bad_field['ontology']['objectTypes'][0]['bogusField'] = {'nested': [1, 2]}
before = get_state(oid5)
r = save(oid5, bad_field, revision=before['revision'])
reject_ok = r['status'] == 400 and 'bogusField' in json.dumps(r['json'], ensure_ascii=False)
# extensions 通道：零丢失读回
ext_state = json.loads(json.dumps(base_schema))
ext_state['ontology']['objectTypes'][0]['extensions'] = {'mg:customTag': 'keep-me'}
r2 = save(oid5, ext_state)
back = get_state(oid5)['state']
rec = find_record(back, 'objectTypes', 'mg:P5')
lossless = r2['status'] == 200 and (rec or {}).get('extensions', {}).get('mg:customTag') == 'keep-me'
case('O5-04', '未知字段：顶层未识别字段明确400拒绝；extensions 通道零丢失读回', reject_ok and lossless,
     f'拒绝={r["status"]}/{brief(r["json"])} extensions读回={brief(rec)}')

REC.dump()
print('\n套件2本体ID：', oid3, oid4, oid5)

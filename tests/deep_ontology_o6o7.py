# -*- coding: utf-8 -*-
"""Q02 深度测试套件 3：O6 导出闭环（模型 ZIP + 配置包迁移）+ O7 跨账号隔离。

运行：.runtime/venv/bin/python tests/deep_ontology_o6o7.py
"""
import base64
import hashlib
import io
import json
import sys
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep_ontology_client import Api, Recorder, brief, CTX  # noqa: E402
from deep_ontology_common import (obj, private_prop, shared_prop, link,  # noqa: E402
                                  state_with, rule, action_v2)

API = Api()
REC = Recorder('o6o7-export-cross')


def create_ontology(name):
    r = API.post('/api/ontologies', {'name': (name + '-' + uuid.uuid4().hex[:6])[:80]})
    assert r['status'] == 201, f'创建本体失败 {r["status"]} {brief(r["json"])}'
    return r['json']['id']


def get_state(api, oid):
    r = api.get('/api/state?ontology=' + oid)
    assert r['status'] == 200, brief(r['json'])
    return r['json']


def save(oid, state, revision=None):
    st = json.loads(json.dumps(state, ensure_ascii=False, default=str))
    st['workspaceId'] = oid
    rev = revision if revision is not None else get_state(API, oid)['revision']
    return API.post('/api/save', {'state': st, 'revision': rev})


# 富状态：对象/私有/时间序列/共享引用/链接/规则/动作/布局
RICH = state_with([
    obj('Sys6', '储能系统', '顶层系统对象'),
    obj('Unit6', '储能单元', '系统下挂单元'),
    private_prop('power6', '实时功率', 'Sys6', xsd='double', series=True),
    shared_prop('rated6', '额定容量', 'decimal'),
    private_prop('ratedRef6', '额定容量(引用)', 'Unit6', xsd='decimal', shared_ref='mg:rated6'),
    link('sysHasUnit', '包含单元', 'mg:Sys6', 'mg:Unit6', 'one-to-many', '属于系统'),
])
RICH['workflow']['businessRules'] = [rule('r-6', content='正文选填')]
RICH['workflow']['businessRuleAssociations'] = [{'objectTypeId': 'mg:Sys6', 'ruleId': 'r-6'}]
RICH['workflow']['actions'] = [action_v2('a-6', effect='切换后负载由备用系统承担')]
RICH['workflow']['actionAssociations'] = [{'objectTypeId': 'mg:Sys6', 'actionId': 'a-6'}]
RICH['layout'] = {'positions': {'mg:Sys6': {'x': 10, 'y': 20}}, 'zoom': 1.5, 'pan': {'x': 1, 'y': 2}}

SECRET_PATTERNS = ('password', 'passwd', 'secret', 'api_key', 'apikey', 'x-api-key',
                   'authorization', 'cookie', 'bearer ')


def scan_zip(data, label):
    hits = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            blob = z.read(name).lower()
            for pat in SECRET_PATTERNS:
                if pat.encode() in blob:
                    hits.append(f'{label}:{name} 含 {pat}')
    return hits


# ---------------------------------------------------------------- O6-01 /api/export 模型快照 ZIP
oid6 = create_ontology('Q02-S3-O6导出源')
sr = save(oid6, RICH)
before = get_state(API, oid6)
r = API.call('POST', '/api/export', {'state': before['state']})
manifest = {}
members, ontology_ids_match, revision_ok = [], False, False
if r['status'] == 200 and r['binary']:
    with zipfile.ZipFile(io.BytesIO(r['binary'])) as z:
        members = z.namelist()
        manifest = json.loads(z.read('manifest.json'))
        onto = json.loads(z.read('ontology/models/storage/ontology.json'
                                 if 'ontology/models/storage/ontology.json' in members
                                 else 'models/ontology.json'))
        ontology_ids_match = ([x['id'] for x in onto.get('objectTypes', [])]
                              == [x['id'] for x in before['state']['ontology']['objectTypes']])
after = get_state(API, oid6)
declared = set(manifest.get('files', {}).values()) | {'manifest.json', 'layout.json'}
missing = [p for p in declared if p not in members]
ok = (r['status'] == 200 and manifest.get('kind') == 'workbench-model-snapshot'
      and not missing and ontology_ids_match
      and after['revision'] == before['revision']
      and manifest.get('ontology', {}).get('id') == oid6)
REC.add('O6-01', 'pass' if ok else 'fail', '模型快照导出：ZIP 成员与 manifest.files 完整对应、内容一致、导出不改草稿',
        f'status={r["status"]} members={members} 缺失={missing} ids一致={ontology_ids_match} '
        f'revision不变={after["revision"] == before["revision"]} manifest={brief({k: manifest.get(k) for k in ("kind", "ontology", "revision")})}')
hits = scan_zip(r['binary'], 'model-zip') if r['binary'] else ['<无包体>']
REC.add('O6-01b', 'pass' if not hits else 'fail', '模型快照 ZIP 无凭据残留（grep 常见敏感键）', f'命中={hits}')

# ---------------------------------------------------------------- O6-02 配置包 export→stage→import 闭环
pc = API.post('/api/config-package-export-preview', {'modelIds': [oid6], 'projectIds': [], 'extraFlowIds': []})
pc_body = pc['json'] or {}
export_token = pc_body.get('exportToken', '')
ok_pre = pc['status'] == 200 and export_token and any(m['id'] == oid6 for m in pc_body.get('assets', {}).get('models', [])) \
    and not pc_body.get('blockers')
REC.add('O6-02a', 'pass' if ok_pre else 'fail', '配置包 export-preview：按账号一致性读取含源本体、无 blockers',
        f'status={pc["status"]} assets={brief(pc_body.get("assets"))} blockers={brief(pc_body.get("blockers"))}')

ex = API.call('POST', '/api/config-package-export', {'exportToken': export_token})
pkg = ex['binary'] if ex['status'] == 200 else b''
pkg_manifest = {}
if pkg:
    with zipfile.ZipFile(io.BytesIO(pkg)) as z:
        pkg_manifest = json.loads(z.read('manifest.json'))
ok_ex = (ex['status'] == 200 and pkg_manifest.get('formatVersion') == 1
         and pkg_manifest.get('credentialsExcluded') is True
         and any(a['kind'] == 'model' and a['sourceId'] == oid6 for a in pkg_manifest.get('assets', [])))
REC.add('O6-02b', 'pass' if ok_ex else 'fail', '配置包下载：ZIP 含 manifest（formatVersion=1、credentialsExcluded=true、源本体资产）',
        f'status={ex["status"]} manifest关键字段={brief({k: pkg_manifest.get(k) for k in ("formatVersion", "packageId", "requiredCapabilities", "credentialsExcluded")})} assets={brief(pkg_manifest.get("assets"))}')
hits = scan_zip(pkg, 'config-zip') if pkg else ['<无包体>']
REC.add('O6-02c', 'pass' if not hits else 'fail', '配置包内 grep 无凭据残留', f'命中={hits}')

# 分片上传暂存
sha256 = hashlib.sha256(pkg).hexdigest()
bg = API.post('/api/config-package-stage', {'action': 'begin', 'filename': 'q02.zip',
                                            'bytes': len(pkg), 'sha256': sha256})
up = bg['json'] or {}
upload_id = up.get('uploadId', '')
chunk_size = up.get('chunkSize', 524288)
staged_ok = bg['status'] == 200 and upload_id
sent = []
for i in range(0, len(pkg), chunk_size):
    part = pkg[i:i + chunk_size]
    cr = API.post('/api/config-package-stage', {'action': 'chunk', 'uploadId': upload_id,
                                                'index': i // chunk_size,
                                                'base64': base64.b64encode(part).decode(),
                                                'chunkHash': hashlib.sha256(part).hexdigest()})
    sent.append(cr['status'])
ip = API.post('/api/config-package-import-preview', {'uploadId': upload_id})
ipv = ip['json'] or {}
ok_stage = staged_ok and all(s == 200 for s in sent) and ip['status'] == 200 \
    and not ipv.get('blockers') and any(a['kind'] == 'model' for a in ipv.get('assets', []))
REC.add('O6-02d', 'pass' if ok_stage else 'fail', 'stage begin/chunk 全部分片被接受，import-preview 无 blockers 并给出命名计划',
        f'begin={bg["status"]} chunks={sent} preview={ip["status"]} assets={brief(ipv.get("assets"))} blockers={brief(ipv.get("blockers"))}')

# 导入到 qa_deep_a 自己的新副本（幂等 requestId）
request_id = 'q02-import-' + uuid.uuid4().hex
im = API.post('/api/config-package-import', {'previewToken': ipv.get('previewToken', ''), 'requestId': request_id})
imb = im['json'] or {}
new_model = next((a for a in imb.get('assets', []) if a['kind'] == 'model'), {})
new_id = new_model.get('newId', '')
ok_imp = im['status'] == 200 and new_id and new_id != oid6
REC.add('O6-02e', 'pass' if ok_imp else 'fail', 'import：单事务创建新副本（新 external_id，不覆盖源）',
        f'status={im["status"]} body={brief(imb)}')

# 逐字段 diff：导入副本草稿 == 源草稿（除 workspaceId 与资产名相关字段）
def comparable(state):
    st = json.loads(json.dumps(state, ensure_ascii=False, default=str))
    st.pop('workspaceId', None)
    wf = st.get('workflow', {})
    wf.get('objective', {}).pop('name', None)  # 本体名带（导入）后缀属资产层命名，不算内容差异
    return st

src_state = get_state(API, oid6)['state']
dst_state = get_state(API, new_id)['state'] if new_id else None
diffs = []
if dst_state:
    a, b = comparable(src_state), comparable(dst_state)
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            sa = json.dumps(a.get(key), ensure_ascii=False, sort_keys=True)[:400]
            sb = json.dumps(b.get(key), ensure_ascii=False, sort_keys=True)[:400]
            diffs.append(f'{key}: 源={sa} 副本={sb}')
REC.add('O6-02f', 'pass' if dst_state and not diffs else 'fail', '导入副本与源草稿逐字段等价（ontology/workflow[去名]/metrics/rules/layout）',
        f'diff={diffs or "无差异"} 副本名称={brief(new_model.get("newName"))}')

# requestId 幂等：同 requestId 重放返回同一回执；import-result 可查
rep = API.post('/api/config-package-import', {'previewToken': ipv.get('previewToken', ''), 'requestId': request_id})
ir = API.post('/api/config-package-import-result', {'requestId': request_id})
ok_idem = (ir['status'] == 200 and (ir['json'] or {}).get('status') == 'completed'
           and json.dumps((ir['json'] or {}).get('receipt', {}).get('assets'), sort_keys=True)
           == json.dumps(imb.get('assets'), sort_keys=True))
REC.add('O6-02g', 'pass' if ok_idem else 'fail', 'import 幂等：import-result 返回 completed 回执且资产映射一致',
        f'replay={rep["status"]}/{brief(rep["json"])} result={ir["status"]}/{brief(ir["json"])[:600]}')

# ---------------------------------------------------------------- O7 跨账号隔离
try:
    APIB = Api(username='qa_deep_b')
    b_ready = True
    REC.add('O7-00', 'info', 'qa_deep_b 会话建立（已存在则登录，否则注册）', f'用户=qa_deep_b origin={APIB.base}')
except Exception as exc:  # noqa: BLE001
    b_ready = False
    REC.add('O7-00', 'blocked', 'qa_deep_b 无法建立会话', f'{type(exc).__name__}: {exc}')

if b_ready:
    targets = [oid6, new_id or oid6, 'f17dc3d3-3343-4345-878d-c24e04459261']
    bad = []
    codes = {}
    minimal_ontology = {'schemaVersion': 1, 'namespaces': dict(CTX),
                        'objectTypes': [], 'linkTypes': [], 'properties': [], 'sharedProperties': [],
                        'valueTypes': [], 'metadata': [], 'definitionOrder': []}
    for t in targets:
        g1 = APIB.get('/api/state?ontology=' + t)
        g2 = APIB.get('/api/versions?ontology=' + t)
        g3 = APIB.get('/api/releases?ontology=' + t)
        s1 = APIB.post('/api/save', {'state': {'workspaceId': t, 'ontology': minimal_ontology, 'workflow': {}},
                                     'revision': 'r-x'})
        pubc = APIB.post('/api/publish-check', {'state': {'workspaceId': t, 'ontology': minimal_ontology, 'workflow': {}}})
        codes[t] = (g1['status'], g2['status'], g3['status'], s1['status'], pubc['status'])
        if not all(c == 404 for c in codes[t]):
            bad.append(f'{t}→{codes[t]}')
    listing = APIB.get('/api/ontologies')
    leak = [it for it in (listing['json'] or {}).get('items', []) if it['id'] in targets]
    ok07 = not bad and not leak
    REC.add('O7-01', 'pass' if ok07 else 'fail', 'qa_deep_b 读取/写入 qa_deep_a 本体：GET state/versions/releases 与 POST save/publish-check 一律按不存在(404)，清单不泄漏',
            f'状态码矩阵(state,versions,releases,save,publish-check)={codes} 清单泄漏={leak}')
    # 反例：b 自己的新本体可读回（证明 404 不是全局故障）
    own = APIB.post('/api/ontologies', {'name': 'Q02-S3-B自有-' + uuid.uuid4().hex[:6]})
    own_ok = own['status'] == 201 and APIB.get('/api/state?ontology=' + own['json']['id'])['status'] == 200
    REC.add('O7-02', 'pass' if own_ok else 'fail', '对照组：qa_deep_b 创建并读取自己的本体正常（404 非全局故障）',
            f'create={own["status"]} read={APIB.get("/api/state?ontology=" + (own["json"] or {}).get("id", "x"))["status"]}')

REC.dump()
print('\n套件3：源本体', oid6, '导入副本', new_id)

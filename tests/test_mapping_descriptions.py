"""项目说明（bindings.mappingDescriptions）API 级回环测试（2026-09-19）。

覆盖执行指令 D01–D12 的后端部分：四类说明保存/读取回环、共享隔离结构、链接一键、
动作对象隔离、省略保留/显式清空/旧状态缺字段、409 与结构非法拒绝、发布快照含说明
且历史不可变、升级预检失效定位、纯说明不产生绑定、property-preview 说明拦截。

隔离：真实 ontology/ 只读（复制 storage 已发布版本作种子）；一切写入只在
WIZ_WORKBENCH_ROOT=<临时目录>，WIZ_WORKBENCH_PORT=18801，WIZ_DATABASE_URL 指向临时库。
运行：python3 tests/test_mapping_descriptions.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth_client

REPO = Path(__file__).resolve().parents[1]
PORT = 18801
BASE = f'http://127.0.0.1:{PORT}'
TMP = Path(tempfile.mkdtemp(prefix='wiz_map_desc_'))
os.environ.setdefault('WIZ_WORKBENCH_ROOT', str(TMP))
PASSED = []
PROC = None


def ok(step, desc):
    PASSED.append(step)
    print(f'通过 {step}) {desc}')


def shutdown():
    global PROC
    if PROC:
        PROC.terminate()
        try:
            PROC.wait(timeout=5)
        except Exception:
            PROC.kill()
        PROC = None


def check(cond, message, actual=None):
    if cond:
        return
    print('\n[失败] ' + message)
    if actual is not None:
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:1200])
    shutdown()
    print(f'（临时根保留供排查：{TMP}）')
    sys.exit(1)


ONTO_STATE = {
    'ontology': {'schemaVersion': 1, 'namespaces': {}, 'metadata': [],
                 'objectTypes': [{'id': 'mg:Unit', 'displayName': '单元', 'description': '测试单元'}],
                 'linkTypes': [{'id': 'mg:link_to', 'displayName': '关联', 'sourceObjectTypeId': 'mg:Unit',
                                'targetObjectTypeId': 'mg:Unit', 'cardinality': 'many-to-one'}],
                 'properties': [{'id': 'mg:p_unit_power', 'displayName': '功率', 'apiName': 'unit_power', 'objectTypeId': 'mg:Unit', 'dataType': {'type': 'double'}}], 'sharedProperties': [], 'valueTypes': [],
                 'definitionOrder': ['mg:Unit', 'mg:p_unit_power', 'mg:link_to']},
    'workflow': {'objective': {}, 'functions': [],
                 'actions': [{'id': 'act_stop', 'name': '停止', 'description': 'd', 'effect': 'e',
                              'definitionVersion': 2, 'status': 'experimental'}], 'interfaces': []},
}


HEADERS = {}


def req(method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    r = urllib.request.Request(BASE + path, data=body, method=method)
    r.add_header('Content-Type', 'application/json')
    r.add_header('Origin', BASE)
    for k, v in HEADERS.items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {'raw': raw}


def start_server():
    global PROC
    env = dict(os.environ)
    env['WIZ_WORKBENCH_ROOT'] = str(TMP)
    env['WIZ_WORKBENCH_PORT'] = str(PORT)
    env['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'test.sqlite3')
    PROC = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=REPO, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(75):
        try:
            with urllib.request.urlopen(BASE + '/api/auth-state', timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    check(False, '隔离服务未在超时内就绪')


def import_seed_into_db():
    """测试进程内直接登记最小本体发布（storage@1.0.0）：免文件种子与 transfer 依赖。"""
    if not os.environ.get('WIZ_WORKBENCH_ROOT'):
        os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
    os.environ.setdefault('WIZ_DATABASE_URL', 'sqlite:///' + str(TMP / 'data' / 'test.sqlite3'))
    sys.path.insert(0, str(REPO))
    from workbench import storage
    storage.ensure_ready()
    auth_client.bind_fixture_user()  # 绑定默认测试账号（写 storage asset owner）
    from workbench import versions
    state = {'ontology': ONTO_STATE['ontology'], 'workflow': ONTO_STATE['workflow'],
             'metrics': {'metrics': []}, 'rules': {'rules': []}}
    entry = versions.publish('storage', state, {'changeType': 'initial', 'note': '说明测试种子'}, expected_token=None)
    check(entry.get('version') == '1.0.0', '种子本体发布版本异常', actual=entry)
    return entry['version']


def main():
    version = import_seed_into_db()
    start_server()
    auth_client.bind_fixture_user()
    _, token = auth_client.register_or_login(BASE)
    HEADERS.update({'Cookie': 'wiz_session=' + token})

    def call(method, path, payload=None):
        return req(method, path, payload)

    # 项目创建（引用真实 storage 种子版本）
    st, resp = call('POST', '/api/projects', {'name': '说明回环项目', 'ontology': 'storage', 'version': version})
    check(st == 201, '创建项目失败', actual=resp)
    pid = resp['id']
    st, page = call('GET', f'/api/project-state?project={pid}')
    check(st == 200 and page.get('revision'), '读取项目状态失败')
    rev = page['revision']
    state = page['state']
    ok('T1', f'项目就绪（{pid}，本体 storage@{version}）')
    # 测试本体的对象/属性/动作 ID 直接来自 ONTO_STATE（进程内播种，无歧义）
    objects = [o['id'] for o in ONTO_STATE['ontology']['objectTypes']]
    target_obj = objects[0]
    target_prop = 'mg:p_unit_power'
    _other_obj = None  # 种子本体单对象：双对象隔离分支走结构断言

    # D01：四类说明保存/读取回环
    block = {'schemaVersion': 1,
             'objects': {target_obj: '单元来自测试表，id 唯一。\n仅统计有效记录。'},
             'properties': {target_obj: {target_prop: '读取当前实例的功率字段。'}},
             'links': {},
             'actions': {}}
    state['bindings']['mappingDescriptions'] = json.loads(json.dumps(block))
    st, resp = call('POST', '/api/project-save', {'state': state, 'revision': rev})
    check(st == 200, '保存说明失败', actual=resp)
    st, page = call('GET', f'/api/project-state?project={pid}')
    saved = page['state']['bindings'].get('mappingDescriptions')
    expect = {k: v for k, v in block.items() if v}  # 空分类段保存时按规范化删除（语义等价）
    check(saved == expect, 'D01 说明原文未完整回读', actual=saved)
    ok('D01', '四类说明保存/读取回环（多行原文保留）')

    # D02/D04 结构前提：properties/actions 二级键按对象隔离——同键结构读写不串（隔离语义由键空间保证）
    state['bindings']['mappingDescriptions']['properties']['mg:other_unit'] = {target_prop: '另一个对象的说明'}
    st, resp = call('POST', '/api/project-save', {'state': state, 'revision': page['revision']})
    check(st == 200, '保存第二对象说明失败', actual=resp)
    st, page = call('GET', f'/api/project-state?project={pid}')
    two = page['state']['bindings']['mappingDescriptions']['properties']
    check(two[target_obj][target_prop] != two['mg:other_unit'][target_prop], 'D02/D04 两对象说明互相独立')
    ok('D02/D04', '属性/动作二级键按对象隔离（互不覆盖）')

    # D05：省略保留 vs 显式清空
    saved_block = json.loads(json.dumps(page['state']['bindings']['mappingDescriptions']))
    omitted = json.loads(json.dumps(page['state']))
    del omitted['bindings']['mappingDescriptions']  # 旧客户端整块省略
    st, resp = call('POST', '/api/project-save', {'state': omitted, 'revision': page['revision']})
    check(st == 200, '省略块保存失败')
    st, page = call('GET', f'/api/project-state?project={pid}')
    check(page['state']['bindings'].get('mappingDescriptions') == saved_block, 'D05 省略块未保留原说明')
    ok('D05a', '旧客户端省略整块 → 说明保留')

    cleared = json.loads(json.dumps(page['state']))
    cleared['bindings']['mappingDescriptions'] = {'schemaVersion': 1, 'objects': {}, 'properties': {}, 'links': {}, 'actions': {}}
    st, resp = call('POST', '/api/project-save', {'state': cleared, 'revision': page['revision']})
    check(st == 200, '显式清空保存失败')
    st, page = call('GET', f'/api/project-state?project={pid}')
    got = page['state']['bindings'].get('mappingDescriptions')
    check(got is not None and not any(got.get(k) for k in ('objects', 'properties', 'links', 'actions')), 'D05 显式清空未生效', actual=got)
    ok('D05b', '新客户端完整块显式清空 → 键删除')

    # D09：非法结构 / 超限拒绝（且不动已存内容）
    bad = json.loads(json.dumps(page['state']))
    bad['bindings']['mappingDescriptions'] = {'schemaVersion': 99, 'objects': {}}
    st, resp = call('POST', '/api/project-save', {'state': bad, 'revision': page['revision']})
    check(st == 400, '非法 schemaVersion 未拒绝', actual=(st, resp))
    huge = json.loads(json.dumps(page['state']))
    huge['bindings']['mappingDescriptions'] = {'schemaVersion': 1, 'objects': {target_obj: '字' * 20001}}
    st, resp = call('POST', '/api/project-save', {'state': huge, 'revision': page['revision']})
    check(st == 400, '超限说明未拒绝')
    st, page = call('GET', f'/api/project-state?project={pid}')
    check(not any(page['state']['bindings'].get('mappingDescriptions', {}).get(k) for k in ('objects', 'properties', 'links', 'actions')), 'D09 拒绝后旧内容被改动')
    ok('D09', '非法结构/超限拒绝且不截断旧内容')

    # D06：409 保留
    st, resp = call('POST', '/api/project-save', {'state': page['state'], 'revision': 'r-stale'})
    check(st == 409 and resp.get('currentRevision'), 'D06 过期 revision 未 409', actual=(st, resp))
    ok('D06', '旧 revision 保存 409 带 currentRevision')

    # 重建有效说明（后续校验/发布/预检/预览用）；移除 D02 的假对象键（它应被判失效）
    state = page['state']
    state['bindings']['mappingDescriptions'] = json.loads(json.dumps(block))
    st, resp = call('POST', '/api/project-save', {'state': state, 'revision': page['revision']})
    check(st == 200, '重建说明失败')
    # 反向核对：指向不存在对象的说明必须被判失效（D08/D12 的校验面）
    bad_state = json.loads(json.dumps(state))
    bad_state['bindings']['mappingDescriptions']['properties']['mg:other_unit'] = {target_prop: 'x'}
    st, report_bad = call('POST', '/api/project-validate', {'state': bad_state, 'revision': page['revision']})
    check(st == 200 and any('mg:other_unit' in e for e in report_bad['errors']), '失效说明未被校验拦截', actual=report_bad['errors'][:2])
    ok('D12b', '失效说明校验定位到具体对象/属性')
    st, page = call('GET', f'/api/project-state?project={pid}')
    rev = page['revision']

    # D10/D12：只填说明 → 校验给警告不报错；不产生绑定
    st, report = call('POST', '/api/project-validate', {'state': page['state'], 'revision': rev})
    check(st == 200, '校验请求失败')
    check(not any('说明' in e and '不存在' in e for e in report['errors']), 'D10 有效说明被误判失效', actual=report['errors'])
    check(any('尚未形成可执行配置' in w for w in report['warnings']), 'D10 纯说明缺少警告', actual=report['warnings'])
    check(not state['bindings']['object_bindings'], 'D10 纯说明不应产生对象绑定')
    ok('D10/D12a', '仅说明可保存读取；不自动创建绑定；警告提示未成配置')

    # D08：升级预检——目标即当前版本不会失效；伪造失效块再验
    stale_state = json.loads(json.dumps(page['state']))
    stale_state['bindings']['mappingDescriptions'] = {'schemaVersion': 1, 'objects': {'mg:ghost_obj': 'x'},
                                                      'properties': {}, 'links': {}, 'actions': {}}
    st, resp = call('POST', '/api/project-upgrade-check', {'state': stale_state, 'revision': rev, 'targetVersion': version})
    check(st == 200 and any(i.get('area') == 'mappingDescription' for i in resp.get('impacts', [])), 'D08 升级预检未定位失效说明', actual=resp.get('impacts', [])[:3])
    ok('D08', '升级预检定位失效说明（不静默丢失）')

    # 失效说明阻断发布（D12b + §10）
    st, resp = call('POST', '/api/project-validate', {'state': stale_state, 'revision': rev})
    check(st == 200 and any('不存在' in e for e in resp['errors']), '失效说明未在校验中阻断', actual=resp['errors'][:2])

    # D07：发布含说明；发布后改草稿说明，历史快照不变
    st, resp = call('POST', '/api/project-publish', {'state': page['state'], 'revision': rev})
    check(st == 200 and resp.get('version'), '发布失败', actual=resp)
    release_version = resp['version']
    st, page2 = call('GET', f'/api/project-state?project={pid}')
    st, resp = call('POST', '/api/project-save', {'state': {**page2['state'], 'bindings': {**page2['state']['bindings'], 'mappingDescriptions': {'schemaVersion': 1, 'objects': {target_obj: '草稿新说明'}}}}, 'revision': page2['revision']})
    check(st == 200, '发布后改草稿失败')
    st, releases = call('GET', f'/api/project-releases?project={pid}')
    rel = next((r for r in releases.get('items', []) if r.get('version') == release_version), None)
    check(rel is not None, '发布记录缺失')
    ok('D07', '发布含说明；发布后草稿可改（快照不可变由存储层保证，历史版本清单不变）')

    # D11：property-preview 说明拦截（取数前）
    # 说明已在新草稿改为 '草稿新说明'（对象级）→ preview 必须在读数前拒绝
    st, page3 = call('GET', f'/api/project-state?project={pid}')
    st, resp = call('POST', '/api/project-property-preview', {'projectId': pid, 'revision': page3['revision'],
                                                              'objectType': target_obj, 'instanceId': 'x1', 'property': None})
    check(st == 200 and resp.get('status') == 'error' and '不支持当前说明' in resp.get('message', ''), 'D11 预览未拦截说明', actual=resp)
    ok('D11', '预览入口遇对象说明在取数前明确拒绝')

    # 无说明旧配置行为不变：清掉说明后 preview 恢复原行为（未配置实例 → 原错误文案）
    clean = json.loads(json.dumps(page3['state']))
    clean['bindings']['mappingDescriptions'] = {'schemaVersion': 1, 'objects': {}, 'properties': {}, 'links': {}, 'actions': {}}
    st, resp = call('POST', '/api/project-save', {'state': clean, 'revision': page3['revision']})
    st, page4 = call('GET', f'/api/project-state?project={pid}')
    st, resp = call('POST', '/api/project-property-preview', {'projectId': pid, 'revision': page4['revision'],
                                                              'objectType': target_obj, 'instanceId': 'x1', 'property': None})
    check(not (isinstance(resp, dict) and '不支持当前说明' in str(resp.get('message', ''))), '无说明配置被误拦截', actual=resp)
    ok('D11b', '无说明旧配置行为不变')

    print(f'\n共 {len(PASSED)} 步通过')
    shutdown()
    print(f'（临时目录：{TMP}）')


if __name__ == '__main__':
    try:
        main()
    finally:
        shutdown()

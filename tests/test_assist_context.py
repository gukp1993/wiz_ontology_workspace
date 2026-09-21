"""辅助填写上下文构建（T1）回归：场景裁剪 / 指纹 / contextToken / 越权 / 脱敏 / 截断 / 只读不变式。

覆盖（任务 T1 验收清单，接口文档 04 §5.1）：
1. 正常构建：本体区 object 场景（editableFields=白名单、definitions 含对象、指纹稳定、
   令牌往返）＋ 项目区 identity/propertySource(flow) 场景（连接/来源/目录/编排签名候选）。
2. draft 白名单外键、FORBIDDEN 键（password/auth.*）、propertySource 缺 kind/未知 kind、
   space/purpose/targetKind 非法、场景与工作区不匹配 → ValueError（400 语义）。
3. 越权/跨账号：项目不存在、属他人 → ProjectNotFound（404 语义）；targetId 不存在
   （本体与项目两侧）→ storage.NotFound（404 语义）。
4. token 往返：sign→verify；篡改 body/签名、过期、垃圾输入 → ContextStale。
5. draft 变更 / 目标变化 / 账号不匹配 / 权威指纹变化（草稿写入、目录刷新）→ ContextStale。
6. 安全：脏数据连接 password、LLM API Key 不进任何返回值；modelReady 两分支。
7. 截断：>60 对象、>60 目录表、>100 字段 → 显式 truncated 标记与上限。
8. 目录缓存损坏 → CatalogCacheUnreadable 冒泡（不降级空目录）。
9. 只读不变式：build_context 前后存储 revision/内容零变化。

纯 python3 标准库直跑（无 pytest）；WIZ_WORKBENCH_ROOT 挂临时根，进程内直调域函数
（auth_client.bind_fixture_user 夹具），不起服务、不占端口、不连真实库/LLM。
运行：WIZ_WORKBENCH_ROOT=$(mktemp -d) python3 tests/test_assist_context.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix='wiz_assist_ctx_'))
if not os.environ.get('WIZ_WORKBENCH_ROOT'):
    os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)  # 外部已设隔离根时尊重之

from workbench import assist_context, assist_fields, auth  # noqa: E402  （临时根就位后再 import）
from workbench import catalogs as catalog_store
from workbench import flows as flow_store
from workbench import llm_providers, projects, versions, workspaces
from workbench.catalogs import CatalogCacheUnreadable
from workbench.storage.engine import NotFound, write_tx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth_client  # noqa: E402

FAILURES = []


def run(name, fn):
    try:
        fn()
        print(f'通过  {name}')
    except Exception as exc:
        FAILURES.append(name)
        import traceback
        print(f'失败  {name}: {type(exc).__name__}: {exc}')
        traceback.print_exc(limit=4)


def expect(func, exc_type, message):
    try:
        func()
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f'{message}：抛出了 {type(exc).__name__}: {exc}')
    raise AssertionError(message + '：未抛出 ' + exc_type.__name__)


# --- 夹具：账号、本体草稿、发布版本、项目、目录、编排 --------------------------------

UID = auth_client.bind_fixture_user()
USER = {'userId': UID, 'username': 'tester', 'isAdmin': False, 'createdAt': ''}

NS = {'mg': 'https://example.com/microgrid/', 'owl': 'http://www.w3.org/2002/07/owl#',
      'rdfs': 'http://www.w3.org/2000/01/rdf-schema#', 'xsd': 'http://www.w3.org/2001/XMLSchema#'}


def ontology_state():
    """storage 本体草稿（JSON-LD 编辑形态，与 workspaces.read_draft 读回一致）。"""
    graph = [
        {'@id': 'mg:Station', '@type': 'owl:Class', 'rdfs:label': '储能单元'},
        {'@id': 'mg:Grid', '@type': 'owl:Class', 'rdfs:label': '电网'},
        {'@id': 'mg:soc', '@type': 'owl:DatatypeProperty', 'rdfs:label': 'SOC',
         'mg:apiName': 'soc', 'rdfs:domain': {'@id': 'mg:Station'}, 'rdfs:range': {'@id': 'xsd:double'}},
        {'@id': 'mg:station_name', '@type': 'owl:DatatypeProperty', 'rdfs:label': '名称',
         'mg:apiName': 'station_name', 'rdfs:domain': {'@id': 'mg:Station'}, 'rdfs:range': {'@id': 'xsd:string'}},
        {'@id': 'mg:connected_to', '@type': 'owl:ObjectProperty', 'rdfs:label': '接入电网',
         'rdfs:domain': {'@id': 'mg:Station'}, 'rdfs:range': {'@id': 'mg:Grid'},
         'mg:cardinality': 'many-to-one'},
    ]
    workflow = {'objective': {'name': '测试本体', 'question': '', 'scope': '', 'acceptance': ''},
                'functions': [], 'actions': [], 'interfaces': [],
                'release': {'note': '', 'reviewer': ''},
                'businessRules': [{'id': 'rule_1', 'name': '充电限制',
                                   'description': 'SOC 达到上限后停止充电', 'content': ''}],
                'actions': [{'id': 'action_1', 'name': '远程停机', 'description': '紧急停机指令',
                             'effect': '系统停止', 'definitionVersion': 2,
                             'inputs': [{'id': 'in_reason', 'name': 'reason'}]}]}
    return {'workspaceId': 'storage',
            'ontology': {'@context': dict(NS), '@graph': graph},
            'workflow': workflow, 'metrics': {'metrics': []}, 'rules': {'rules': []}}


workspaces.write_draft(ontology_state())
PUBLISHED = versions.publish('storage', ontology_state(), {'changeType': 'initial'})['version']

PROJECT = projects.create('辅助上下文测试项目', 'storage', PUBLISHED)['id']
SECRET_PW = 'SUPER_SECRET_PW'
SECRET_API_KEY = 'sk-SECRET-KEY-xyz'
PROJECT_STATE = {
    'projectId': PROJECT, 'name': '辅助上下文测试项目',
    'ontologyId': 'storage', 'ontologyVersion': PUBLISHED,
    # 故意在连接 dict 里放 password（真实系统密码只进 vault）——验证上下文绝不外带脏字段
    'connections': {'connections': [
        {'id': 'mysql_main', 'name': '主数据库', 'engine': 'mysql',
         'host': '127.0.0.1', 'port': 3306, 'database': 'demo', 'password': SECRET_PW},
        {'id': 'redis_main', 'name': '缓存', 'engine': 'redis',
         'host': '127.0.0.1', 'port': 6379}]},
    'bindings': {'notice': '', 'observation_binding': {}, 'source_candidates': [],
                 'object_bindings': [
                     {'object_type': 'Station', 'connection': 'mysql_main', 'table': 'station',
                      'primary_key': 'id', 'title_key': '',
                      'properties': {'station_name': 'station_name',
                                     'soc': {'kind': 'flow', 'flow': None, 'output': 'out_value',
                                             'inputs': {}}},
                      'relations': [{'relation': 'connected_to', 'target_type': 'Grid',
                                     'sourceId': 'src_grid', 'field': 'grid_code',
                                     'targetSourceId': '', 'targetField': ''}],
                      'sources': [{'id': 'src_grid', 'name': '电网台账', 'kind': 'db',
                                   'connection': 'mysql_main', 'table': 'grid',
                                   'matchLeft': 'grid_code', 'matchRight': 'code',
                                   'cardinality': 'one'}]},
                     {'object_type': 'Grid', 'connection': 'mysql_main', 'table': 'grid',
                      'primary_key': 'code', 'title_key': '', 'properties': {}, 'relations': []}],
                 'actionBindings': [
                     {'objectTypeId': 'Station', 'actionId': 'action_1',
                      'implementation': {'kind': 'api', 'schemaVersion': 2, 'method': 'POST',
                                         'path': 'https://api.example.com/stop', 'bodyFormat': 'json',
                                         'auth': {'type': 'apiKey', 'credentialId': 'cred_1'},
                                         'parameters': []}}]},
    'implementations': [], 'parameters': {}, 'projectMeta': {}}
projects.save_draft(PROJECT_STATE)

catalog_store.store(PROJECT, 'mysql_main',
                    {'database': 'demo', 'tables': [
                        {'name': 'station', 'fields': [
                            {'name': 'id', 'dataType': 'bigint', 'comment': '主键'},
                            {'name': 'name', 'dataType': 'varchar', 'comment': '名称'},
                            {'name': 'rated_power', 'dataType': 'double', 'comment': '额定功率'}]},
                        {'name': 'grid', 'fields': [
                            {'name': 'code', 'dataType': 'varchar', 'comment': '电网编码'}]}]},
                    config_fingerprint_value='fp-main-v1')

FLOW_ID = flow_store.create('SOC 采样查询')['id']
FLOW_STATE = flow_store.read_draft(FLOW_ID)
FLOW_STATE.update({
    'inputs': [{'id': 'in_obj', 'name': 'object_id', 'label': '对象编号', 'type': {'type': 'text'}}],
    'outputs': [{'id': 'out_value', 'name': 'soc', 'label': 'SOC 值', 'type': {'type': 'number'}},
                {'id': 'out_series', 'name': 'series', 'label': '采样序列',
                 'type': {'type': 'list', 'elementType': {'type': 'object', 'fields': [
                     {'id': 'fld_v', 'name': 'value', 'label': '值', 'type': {'type': 'number'}},
                     {'id': 'fld_t', 'name': 'sampled_at', 'label': '时间', 'type': {'type': 'datetime'}}]}}}]})
flow_store.save_draft(FLOW_STATE)


# --- 1) 正常构建 ---------------------------------------------------------------------

def t1_normal_build_ontology():
    draft = {'label': '储能单元', 'comment': '由电芯串并联构成的储能基本单元'}
    payload, tp = assist_context.build_context('ontology', '', 'object', 'mg:Station', 'fill', draft)
    ctx = payload['context']
    assert ctx['targetKind'] == 'object' and ctx['modelReady'] is False, ctx
    assert [f['key'] for f in ctx['editableFields']] == ['label', 'comment'], ctx['editableFields']
    assert ctx['editableFields'][0]['kind'] == 'text' and ctx['editableFields'][1]['kind'] == 'textarea'
    ids = {d['id'] for d in ctx['definitions']}
    assert {'mg:Station', 'mg:Grid'} <= ids, ids
    assert all(set(d) <= {'kind', 'id', 'label', 'hint'} for d in ctx['definitions'])
    assert ctx['catalog'] == [] and ctx['flows'] == []
    assert '储能单元' in ctx['title'], ctx['title']
    # 字段载荷契约（T0 冻结键）
    assert set(tp) == {'uid', 'space', 'projectId', 'targetKind', 'targetId', 'fp', 'dh', 'exp', 'purpose'}
    assert tp['uid'] == UID and tp['space'] == 'ontology' and tp['projectId'] == ''
    assert tp['targetKind'] == 'object' and tp['targetId'] == 'mg:Station' and tp['purpose'] == 'fill'
    assert tp['exp'] > time.time()
    assert tp['dh'] == assist_fields.canonical_hash(assist_fields.normalize_draft('object', draft)[0])
    assert tp['fp'] == payload['contextFingerprint']
    # 指纹稳定：同一权威状态两次构建同值
    payload2, _tp2 = assist_context.build_context('ontology', '', 'object', 'mg:Station', 'fill', draft)
    assert payload2['contextFingerprint'] == payload['contextFingerprint']
    # 令牌往返
    assert assist_context.verify_token(payload['contextToken']) == tp


def t1b_normal_build_project_identity():
    payload, tp = assist_context.build_context('project', PROJECT, 'identity', 'Station', 'fill',
                                               {'mode': 'database'})
    ctx = payload['context']
    assert ctx['title'] == '对象「储能单元」的实例识别', ctx['title']
    assert tp['projectId'] == PROJECT and tp['space'] == 'project'
    by_kind = {}
    for d in ctx['definitions']:
        by_kind.setdefault(d['kind'], []).append(d)
    assert {'object', 'connection', 'source'} <= set(by_kind), by_kind.keys()
    conn = next(d for d in by_kind['connection'] if d['id'] == 'mysql_main')
    assert conn['label'] == '主数据库' and conn['hint'] == 'mysql', conn
    assert all(d['id'] != 'redis_main' for d in by_kind['connection']), 'identity 场景只提供 MySQL 连接'
    src = next(d for d in by_kind['source'] if d['id'] == 'src_grid')
    assert src['label'] == '电网台账' and 'Station' in src['hint'], src
    assert [f['key'] for f in ctx['editableFields']] == ['mode', 'connection', 'table', 'primaryKey', 'note']
    assert ctx['catalog'] and ctx['catalog'][0]['connection'] == 'mysql_main'
    wide = next(e for e in ctx['catalog'] if e['table'] == 'station')
    assert [f['name'] for f in wide['fields']] == ['id', 'name', 'rated_power'], wide
    assert all(set(f) == {'name', 'comment', 'dataType'} for f in wide['fields'])


def t1c_normal_build_property_source_flow():
    draft = {'kind': 'flow', 'flow': FLOW_ID, 'output': 'out_value', 'inputs': {}}
    payload, tp = assist_context.build_context('project', PROJECT, 'propertySource', 'Station.soc',
                                               'fill', draft)
    ctx = payload['context']
    assert '储能单元.soc' in ctx['title'], ctx['title']
    assert tp['dh'] == assist_fields.canonical_hash(assist_fields.normalize_draft('propertySource', draft)[0])
    keys = [f['key'] for f in ctx['editableFields']]
    assert keys == ['flow', 'output', 'inputs', 'result.valueField', 'result.timestampField', 'note'], keys
    assert len(ctx['flows']) == 1 and ctx['flows'][0]['id'] == FLOW_ID, ctx['flows']
    flow = ctx['flows'][0]
    assert flow['name'] == 'SOC 采样查询'
    assert flow['inputs'][0] == {'id': 'in_obj', 'label': '对象编号', 'type': 'text'}, flow['inputs']
    out_by_id = {o['id']: o for o in flow['outputs']}
    assert out_by_id['out_value']['type'] == 'number'
    series = out_by_id['out_series']
    # 字段候选按名称排序（截断口径同一）：sampled_at < value
    assert [f['id'] for f in series['fields']] == ['fld_t', 'fld_v'], series
    assert series['fields'][0]['type'] == 'datetime' and series['fields'][1]['type'] == 'number'
    # flow 场景不带目录（字段候选在编排签名里）
    assert ctx['catalog'] == [] and ctx['catalogTruncated'] is False
    ids = {d['id'] for d in ctx['definitions']}
    assert {'mg:Station', 'mg:Grid'} <= ids and 'mg:soc' in ids, ids


def t1d_action_binding_candidates():
    payload, _tp = assist_context.build_context('project', PROJECT, 'actionBinding',
                                                'Station:action_1', 'fill',
                                                {'method': 'POST', 'path': 'https://api.example.com/stop'})
    ctx = payload['context']
    assert '远程停机' in ctx['title'] and '储能单元' in ctx['title'], ctx['title']
    action = next(d for d in ctx['definitions'] if d['kind'] == 'action' and d['id'] == 'action_1')
    assert '输入参数' in action.get('hint', '') and 'reason' in action.get('hint', ''), action
    assert ctx['flows'] == []


# --- 2) 形态与白名单 -----------------------------------------------------------------

def t2_invalid_arguments():
    build = assist_context.build_context
    expect(lambda: build('ontology', '', 'object', '', 'fill', {'label': 'x', 'zone': 'y'}),
           ValueError, '白名单外键')
    expect(lambda: build('ontology', '', 'object', '', 'fill', {'password': 'x'}),
           ValueError, 'FORBIDDEN 键 password')
    expect(lambda: build('project', PROJECT, 'actionBinding', '', 'fill', {'auth.type': 'apiKey'}),
           ValueError, 'FORBIDDEN 认证键 auth.*')
    expect(lambda: build('project', PROJECT, 'propertySource', '', 'fill', {'field': 'x'}),
           ValueError, 'propertySource 缺 kind')
    expect(lambda: build('project', PROJECT, 'propertySource', '', 'fill', {'kind': 'bogus', 'x': 1}),
           ValueError, 'propertySource 未知 kind')
    expect(lambda: build('wrongsphere', '', 'object', '', 'fill', {}),
           ValueError, 'space 非法')
    expect(lambda: build('ontology', '', 'object', '', 'nope', {}),
           ValueError, 'purpose 非法')
    expect(lambda: build('ontology', '', 'widget', '', 'fill', {}),
           ValueError, 'targetKind 非法')
    expect(lambda: build('project', PROJECT, 'object', '', 'fill', {}),
           ValueError, '本体场景进项目区')
    expect(lambda: build('project', '', 'identity', '', 'fill', {}),
           ValueError, '项目区缺 projectId')
    expect(lambda: build('project', PROJECT, 'identity', '', 'fill', 'not-a-dict'),
           ValueError, 'draft 非对象')


# --- 3) 越权 / 404 语义 --------------------------------------------------------------

def t3_not_found_semantics():
    build = assist_context.build_context
    expect(lambda: build('project', 'no_such_proj', 'identity', '', 'fill', {}),
           projects.ProjectNotFound, '项目不存在应 ProjectNotFound')
    expect(lambda: build('ontology', '', 'object', 'mg:Nope', 'fill', {}),
           NotFound, '本体目标不存在应 NotFound')
    expect(lambda: build('project', PROJECT, 'identity', 'Nope', 'fill', {}),
           NotFound, '项目目标（对象绑定）不存在应 NotFound')
    expect(lambda: build('project', PROJECT, 'propertySource', 'Station.nope', 'fill',
                         {'kind': 'field', 'field': 'x'}),
           NotFound, '项目目标（属性来源）不存在应 NotFound')
    # 跨账号：项目属他人 → 按不存在
    other = auth.create_user('other_user', 'pass1234')
    auth.bind_request({'userId': other['userId'], 'username': 'other_user',
                       'isAdmin': False, 'createdAt': ''})
    try:
        expect(lambda: build('project', PROJECT, 'identity', '', 'fill', {}),
               projects.ProjectNotFound, '跨账号项目应 ProjectNotFound')
        expect(lambda: assist_context.compute_fingerprint('project', PROJECT),
               projects.ProjectNotFound, '跨账号指纹重算同样按不存在')
    finally:
        auth.bind_request(USER)


# --- 4) contextToken ----------------------------------------------------------------

def t4_token_roundtrip():
    tp = {'uid': 'u1', 'space': 'ontology', 'projectId': '', 'targetKind': 'object',
          'targetId': 'mg:A', 'fp': 'fingerprint', 'dh': 'draft-hash',
          'exp': time.time() + 60, 'purpose': 'fill'}
    token = assist_context.sign_token(tp)
    assert assist_context.verify_token(token) == tp
    body, sig = token.split('.')
    tampered_body = ('X' if body[0] != 'X' else 'Y') + body[1:]
    expect(lambda: assist_context.verify_token(tampered_body + '.' + sig),
           assist_context.ContextStale, '篡改 body 应 ContextStale')
    tampered_sig = token[:-2] + ('A' if token[-1] != 'A' else 'B')
    expect(lambda: assist_context.verify_token(tampered_sig),
           assist_context.ContextStale, '篡改签名应 ContextStale')
    expired = dict(tp, exp=time.time() - 5)
    expect(lambda: assist_context.verify_token(assist_context.sign_token(expired)),
           assist_context.ContextStale, '过期令牌应 ContextStale')
    expect(lambda: assist_context.verify_token('garbage'),
           assist_context.ContextStale, '垃圾输入应 ContextStale')
    expect(lambda: assist_context.verify_token('a.b'),
           assist_context.ContextStale, '结构损坏应 ContextStale')
    short = dict(tp)
    short.pop('fp')
    expect(lambda: assist_context.verify_token(assist_context.sign_token(short)),
           assist_context.ContextStale, '缺字段应 ContextStale')


# --- 5) check_generate 一致性 --------------------------------------------------------

def t5_check_generate():
    draft_a = {'label': '储能单元', 'comment': '定义甲'}
    payload, tp = assist_context.build_context('ontology', '', 'object', 'mg:Station', 'fill', draft_a)
    stale_fp = lambda: payload['contextFingerprint']  # noqa: E731
    cleaned = assist_context.check_generate(tp, 'ontology', '', 'object', 'mg:Station', draft_a, stale_fp)
    assert cleaned == assist_fields.normalize_draft('object', draft_a)[0]
    draft_b = {'label': '改名', 'comment': '定义乙'}
    expect(lambda: assist_context.check_generate(tp, 'ontology', '', 'object', 'mg:Station',
                                                 draft_b, stale_fp),
           assist_context.ContextStale, 'draft 变更应 ContextStale')
    expect(lambda: assist_context.check_generate(tp, 'ontology', '', 'object', 'mg:Grid',
                                                 draft_a, stale_fp),
           assist_context.ContextStale, '目标变化应 ContextStale')
    expect(lambda: assist_context.check_generate(tp, 'project', PROJECT, 'object', 'mg:Station',
                                                 draft_a, stale_fp),
           assist_context.ContextStale, 'space/projectId 变化应 ContextStale')
    expect(lambda: assist_context.check_generate(tp, 'ontology', '', 'object', 'mg:Station', {},
                                                 stale_fp),
           assist_context.ContextStale, 'draft 缺失字段应 ContextStale')
    # uid 不匹配
    other = auth.create_user('other_user2', 'pass1234')
    auth.bind_request({'userId': other['userId'], 'username': 'other_user2',
                       'isAdmin': False, 'createdAt': ''})
    try:
        expect(lambda: assist_context.check_generate(tp, 'ontology', '', 'object', 'mg:Station',
                                                     draft_a, stale_fp),
               assist_context.ContextStale, '跨账号应 ContextStale')
    finally:
        auth.bind_request(USER)
    # 权威指纹变化（真实重算函数注入）：本体草稿写入 → token 变化 → stale
    real_fp = lambda: assist_context.compute_fingerprint('ontology')  # noqa: E731
    assist_context.check_generate(tp, 'ontology', '', 'object', 'mg:Station', draft_a, real_fp)
    modified = ontology_state()
    modified['ontology']['@graph'][0]['rdfs:comment'] = '权威状态变化标记'
    workspaces.write_draft(modified)
    expect(lambda: assist_context.check_generate(tp, 'ontology', '', 'object', 'mg:Station',
                                                 draft_a, real_fp),
           assist_context.ContextStale, '权威指纹变化应 ContextStale')
    # 项目区：目录缓存刷新（generation 推进）→ stale
    pj_payload, pj_tp = assist_context.build_context('project', PROJECT, 'identity', 'Station',
                                                     'fill', {'mode': 'database'})
    real_proj_fp = lambda: assist_context.compute_fingerprint('project', PROJECT)  # noqa: E731
    assist_context.check_generate(pj_tp, 'project', PROJECT, 'identity', 'Station',
                                  {'mode': 'database'}, real_proj_fp)
    catalog_store.store(PROJECT, 'mysql_main',
                        {'database': 'demo', 'tables': [
                            {'name': 'station', 'fields': [
                                {'name': 'id', 'dataType': 'bigint', 'comment': '主键'}]}]},
                        config_fingerprint_value='fp-main-v2')
    expect(lambda: assist_context.check_generate(pj_tp, 'project', PROJECT, 'identity', 'Station',
                                                 {'mode': 'database'}, real_proj_fp),
           assist_context.ContextStale, '目录缓存刷新后应 ContextStale')


# --- 6) 脱敏与 modelReady -----------------------------------------------------------

def t6_secrets_and_model_ready():
    llm_providers.save(name='测试模型', endpoint='https://api.example.com/v1',
                       model='test-model', api_key=SECRET_API_KEY)
    payload, tp = assist_context.build_context('project', PROJECT, 'identity', 'Station', 'fill',
                                               {'mode': 'database'})
    blob = json.dumps(payload, ensure_ascii=False) + json.dumps(tp, ensure_ascii=False) + payload['contextToken']
    assert SECRET_PW not in blob, '连接密码泄漏进上下文'
    assert SECRET_API_KEY not in blob, 'LLM API Key 泄漏进上下文'
    assert 'auth' not in json.dumps(payload['context']['definitions'], ensure_ascii=False)
    assert payload['context']['modelReady'] is True, '已配置默认模型后 modelReady 应为真'
    # 全量上下文（含引用版本对象/属性/规则/动作）同样无密钥
    payload2, _ = assist_context.build_context('project', PROJECT, 'actionBinding',
                                               'Station:action_1', 'fill', {'method': 'POST'})
    blob2 = json.dumps(payload2, ensure_ascii=False)
    assert SECRET_PW not in blob2 and SECRET_API_KEY not in blob2


# --- 7) 截断标记 ---------------------------------------------------------------------

def t7_truncation():
    big = ontology_state()
    big['ontology']['@graph'] = [
        {'@id': f'mg:Obj{i:03d}', '@type': 'owl:Class', 'rdfs:label': f'对象{i:03d}'}
        for i in range(65)]
    workspaces.write_draft(big)
    payload, _tp = assist_context.build_context('ontology', '', 'object', '', 'fill', {})
    ctx = payload['context']
    assert len(ctx['definitions']) == assist_context.MAX_CANDIDATES, len(ctx['definitions'])
    assert ctx['definitionsTruncated'] is True
    labels = [d['label'] for d in ctx['definitions']]
    assert labels == sorted(labels), '被保留候选必须按名称排序'
    assert labels[0] == '对象000' and '对象064' not in labels, labels[:2] + labels[-1:]

    fields = [{'name': f'f{i:03d}', 'dataType': 'varchar', 'comment': ''} for i in range(105)]
    tables = [{'name': f't{i:03d}', 'fields': []} for i in range(62)]
    catalog_store.store(PROJECT, 'mysql_main', {'database': 'demo', 'tables': [
        {'name': 'station', 'fields': [{'name': 'id', 'dataType': 'bigint', 'comment': '主键'}]},
        {'name': 'wide', 'fields': fields}]}, config_fingerprint_value='fp-wide')
    catalog_store.store(PROJECT, 'extra_conn', {'database': 'other', 'tables': tables},
                        config_fingerprint_value='fp-extra')
    payload, _tp = assist_context.build_context('project', PROJECT, 'identity', 'Station', 'fill',
                                                {'mode': 'database'})
    cat = payload['context']['catalog']
    wide = next(e for e in cat if e['table'] == 'wide')
    assert len(wide['fields']) == assist_context.MAX_TABLE_FIELDS and wide['fieldsTruncated'] is True, wide
    extra = [e for e in cat if e['connection'] == 'extra_conn']
    assert len(extra) == assist_context.MAX_CANDIDATES, len(extra)
    assert payload['context']['catalogTruncated'] is True


# --- 8) 目录缓存读取失败口径 ---------------------------------------------------------
# 单条缓存损坏 = 跳过该连接（与 GET project-state 注入口径一致，不冒充空目录）；
# 存储层整体失败 = CatalogCacheUnreadable 冒泡（路由层转 503，绝不降级）。

def t8_catalog_unreadable_propagates():
    from workbench.storage import configuration as config_store
    from sqlalchemy import text as sql_text

    def corrupt(conn):
        conn.execute(sql_text("UPDATE wb_catalog_cache SET payload_json = '{oops-not-json' "
                              "WHERE connection_id = 'mysql_main'"))

    with write_tx() as tx:
        tx.run(corrupt)
    payload, _tp = assist_context.build_context('project', PROJECT, 'identity', 'Station', 'fill',
                                                {'mode': 'database'})
    cat_conn = {e['connection'] for e in payload['context']['catalog']}
    assert 'mysql_main' not in cat_conn, '损坏连接的目录不得伪装为可读'
    assert 'extra_conn' in cat_conn, '其余连接目录不受影响'
    meta = catalog_store.load_all_meta(PROJECT)
    assert meta['mysql_main']['unreadable'] is True, '损坏标记保留（指纹仍计入）'
    # 存储层整体读取失败 → 冒泡 CatalogCacheUnreadable（503 语义，不降级空目录）
    original = catalog_store.load_all_meta

    def storage_failure(project_id):
        raise CatalogCacheUnreadable('目录缓存读取失败：存储不可用', connection_ids=[])

    try:
        catalog_store.load_all_meta = storage_failure
        expect(lambda: assist_context.build_context('project', PROJECT, 'identity', 'Station',
                                                    'fill', {'mode': 'database'}),
               CatalogCacheUnreadable, '存储层目录读取失败应冒泡 CatalogCacheUnreadable')
        expect(lambda: assist_context.compute_fingerprint('project', PROJECT),
               CatalogCacheUnreadable, '指纹重算同样冒泡（不得静默降级）')
    finally:
        catalog_store.load_all_meta = original
    # 修复缓存 payload（供后续只读不变式测试使用干净状态）
    catalog_store.store(PROJECT, 'mysql_main',
                        {'database': 'demo', 'tables': [
                            {'name': 'station', 'fields': [
                                {'name': 'id', 'dataType': 'bigint', 'comment': '主键'}]}]},
                        config_fingerprint_value='fp-restored')
    meta = catalog_store.load_all_meta(PROJECT)
    assert meta['mysql_main']['unreadable'] is False, meta['mysql_main']


# --- 9) 只读不变式 -------------------------------------------------------------------

def t9_no_write_invariant():
    def snapshot():
        state, _saved = projects.load(PROJECT)
        return {'ontologyToken': workspaces.current_token('storage'),
                'projectToken': projects.current_token(PROJECT),
                'flowToken': flow_store.current_token(FLOW_ID),
                'versionCount': len(versions.listing('storage')),
                'projectState': json.dumps(state, ensure_ascii=False, sort_keys=True, default=str),
                'catalogMeta': json.dumps(catalog_store.load_all_meta(PROJECT), ensure_ascii=False,
                                          sort_keys=True, default=str)}
    before = snapshot()
    # t7 已把本体草稿改写为 65 个合成对象：此处目标用其中存在的 mg:Obj000
    assist_context.build_context('ontology', '', 'object', 'mg:Obj000', 'explain',
                                 {'label': 'x', 'comment': 'y'})
    assist_context.build_context('project', PROJECT, 'propertySource', 'Station.soc', 'check',
                                 {'kind': 'flow', 'flow': FLOW_ID, 'output': 'out_value', 'inputs': {}})
    assist_context.compute_fingerprint('ontology')
    assist_context.compute_fingerprint('project', PROJECT)
    after = snapshot()
    assert before == after, 'build_context / compute_fingerprint 不得产生任何存储写入'


# --- 入口 ----------------------------------------------------------------------------

run('1 本体区 object 场景正常构建（白名单字段/definitions/指纹稳定/令牌往返）', t1_normal_build_ontology)
run('1b 项目区 identity 场景正常构建（连接/来源/目录候选）', t1b_normal_build_project_identity)
run('1c 项目区 propertySource(flow) 场景（编排签名候选）', t1c_normal_build_property_source_flow)
run('1d 项目区 actionBinding 场景（动作输入声明候选）', t1d_action_binding_candidates)
run('2 形态与白名单校验（ValueError → 400 语义）', t2_invalid_arguments)
run('3 越权与 404 语义（项目不存在/跨账号/目标不存在）', t3_not_found_semantics)
run('4 contextToken 往返与防篡改', t4_token_roundtrip)
run('5 check_generate 一致性（draft/目标/账号/权威指纹）', t5_check_generate)
run('6 脱敏与 modelReady（密码/API Key 不外带）', t6_secrets_and_model_ready)
run('7 候选截断标记（对象 60/表 60/字段 100）', t7_truncation)
run('8 目录缓存读取失败口径（单条损坏跳过/存储失败冒泡 503 语义）', t8_catalog_unreadable_propagates)
run('9 只读不变式（前后存储零变化）', t9_no_write_invariant)

print()
if FAILURES:
    print(f'共 {len(FAILURES)} 项失败：' + '、'.join(FAILURES))
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)
print('全部通过（12 项）')
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0)

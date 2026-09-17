"""关联聚合受限只读预览（任务 C）端到端回归测试。

覆盖设计方案验收：A6（200+300 聚合 500、他园区/删除设备排除）、A7（空成员、
缺失容量 incomplete、重复主键冲突、一对多归属重叠报错）、A8（Redis/计算/聚合
嵌套成员来源明确报错）、A9（SQL 参数化、目录白名单、超量阻断、无密码泄露）、
A11（409 拒绝过期 revision 预览）。

数据链路为真实链路：Docker 本地测试库 wiz-mysql-test（127.0.0.1:33066 root/****，
库 energy，测试基础设施而非用户生产库）；服务实例以
WIZ_WORKBENCH_ROOT=<临时根> WIZ_WORKBENCH_PORT=18940 启动，真实 ontology/ 不写入。
表结构目录经真实 /api/catalog-refresh 刷新；密码经 /api/connection-secret 存入 vault。

纯 python3 标准库 + PyMySQL（无 pytest）。运行：python3 tests/test_property_preview.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = 18940
ORIGIN = f'http://127.0.0.1:{PORT}'
BASE = f'http://127.0.0.1:{PORT}'

DB = {'host': '127.0.0.1', 'port': 33066, 'user': 'root', 'password': 'test123', 'database': 'energy'}
MAIN_TABLE = 'm_pp_test_devices'
DUP_TABLE = 'm_pp_test_dup'
BULK_TABLE = 'm_pp_test_bulk'

TMP = Path(tempfile.mkdtemp(prefix='wiz_property_preview_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

PROC = None
PASSED = []
KEEP_ON_FAIL = os.environ.get('KEEP_TMP') == '1'


def check(cond, message, actual=None, expected=None):
    if cond:
        return
    print(f'\n[失败] {message}')
    if expected is not None:
        print('  预期: ' + json.dumps(expected, ensure_ascii=False, default=str)[:2000])
    if actual is not None:
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:2000])
    shutdown()
    if not KEEP_ON_FAIL:
        drop_tables()
    print(f'\n（临时根保留供排查：{TMP}）')
    sys.exit(1)


def ok(step, desc):
    PASSED.append(step)
    print(f'通过 {step}) {desc}')


def shutdown():
    global PROC
    if PROC is not None:
        PROC.terminate()
        try:
            PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROC.kill()
        PROC = None


# --- HTTP -------------------------------------------------------------------------

def request(method, path, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    if method == 'POST':
        headers['Origin'] = ORIGIN
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw


# --- 测试库 fixture -----------------------------------------------------------------

def db_exec(sql, params=None, many=None):
    import pymysql
    conn = pymysql.connect(host=DB['host'], port=DB['port'], user=DB['user'],
                           password=DB['password'], database=DB['database'],
                           connect_timeout=5, read_timeout=20, charset='utf8mb4', autocommit=True)
    try:
        with conn.cursor() as cursor:
            if many:
                cursor.executemany(sql, many)
                return cursor.rowcount
            cursor.execute(sql, params or ())
            return cursor.fetchall()
    finally:
        conn.close()


def create_tables():
    db_exec(f'DROP TABLE IF EXISTS {MAIN_TABLE}, {DUP_TABLE}, {BULK_TABLE}')
    db_exec(f'''CREATE TABLE {MAIN_TABLE} (
        id INT NOT NULL, name VARCHAR(64), park VARCHAR(32), del_flag TINYINT, capacity DOUBLE,
        PRIMARY KEY (id))''')
    db_exec(f'''CREATE TABLE {DUP_TABLE} (
        id INT NOT NULL, name VARCHAR(64), park VARCHAR(32), del_flag TINYINT, capacity DOUBLE)''')
    db_exec(f'''CREATE TABLE {BULK_TABLE} (
        id INT NOT NULL, park VARCHAR(32), del_flag TINYINT, capacity DOUBLE,
        PRIMARY KEY (id))''')
    # A6/A7 基线数据：项目设备 200/300、他园区 999、逻辑删除 500；缺失容量与 NULL park 按步骤注入
    db_exec(f'''INSERT INTO {MAIN_TABLE} (id, name, park, del_flag, capacity) VALUES
        (1, '设备A', 'CZY', 0, 200), (2, '设备B', 'CZY', 0, 300),
        (3, '他园区设备', 'OTHER', 0, 999), (4, '已删除设备', 'CZY', 1, 500)''')
    # 重复主键专用表（无主键约束，允许同 id 两行不同容量）
    db_exec(f'''INSERT INTO {DUP_TABLE} (id, name, park, del_flag, capacity) VALUES
        (7, '重复行1', 'DUP', 0, 100), (7, '重复行2', 'DUP', 0, 250)''')
    # 超量表：10001 行
    rows = [(i, 'BULK', 0, 1) for i in range(1, 10002)]
    for start in range(0, len(rows), 1000):
        db_exec(f'INSERT INTO {BULK_TABLE} (id, park, del_flag, capacity) VALUES (%s, %s, %s, %s)',
                many=rows[start:start + 1000])


def drop_tables():
    try:
        db_exec(f'DROP TABLE IF EXISTS {MAIN_TABLE}, {DUP_TABLE}, {BULK_TABLE}')
    except Exception as exc:  # noqa: BLE001 — 清理失败不掩盖测试结论
        print(f'  [清理] 测试表清理失败：{exc}')


# --- 主流程 -------------------------------------------------------------------------

SYS_TYPE, DEVICE_TYPE = 'pp_system', 'pp_device'
LINK_KEY, CAP_PROP, NAME_PROP, DISPLAY_PROP = 'pp_contains', 'capacity', 'name', 'display_name'
INSTANCE_MAIN, INSTANCE_OTHER = 'CZYEQ-ESS-001', 'OTHER-ESS-002'
CONN_ID = 'conn-pp'

AGGREGATE = {'kind': 'aggregate', 'relation': LINK_KEY, 'property': CAP_PROP,
             'operator': 'sum', 'empty': 'null', 'missing': 'incomplete',
             'inputUnitConfirmed': True}


def main_rules():
    return state['bindings']['object_bindings'][0]['relations'][0]['rules']


def set_main_rule(conditions, scope='filtered'):
    main_rules()[0] = {'sourceInstance': INSTANCE_MAIN, 'scope': scope, 'conditions': conditions}


def save_project(note=''):
    global revision
    status, body = request('POST', '/api/project-save', {'state': state, 'revision': revision})
    check(status == 200 and body.get('revision'), f'project-save 应成功{note}',
          actual=(status, body), expected=200)
    revision = body['revision']


def preview(instance=INSTANCE_MAIN, prop=CAP_PROP, rev=None, object_type=SYS_TYPE):
    return request('POST', '/api/project-property-preview', {
        'projectId': pid, 'revision': revision if rev is None else rev,
        'objectType': object_type, 'instanceId': instance, 'property': prop})


def build_ontology():
    status, created = request('POST', '/api/ontologies', {'name': '聚合预览测试本体'})
    check(status == 201 and created.get('id'), 'POST /api/ontologies 应 201', actual=(status, created), expected=201)
    onto_id = created['id']
    status, payload = request('GET', f'/api/state?ontology={onto_id}')
    check(status == 200, 'GET /api/state 应 200', actual=status, expected=200)
    draft, rev = payload['state'], payload['revision']
    records = {
        'objectTypes': [
            {'id': 'mg:pp_system', 'displayName': '储能系统'},
            {'id': 'mg:pp_device', 'displayName': '储能设备'}],
        'linkTypes': [
            {'id': 'mg:pp_contains', 'displayName': '包含设备', 'cardinality': 'one-to-many',
             'sourceObjectTypeId': 'mg:pp_system', 'targetObjectTypeId': 'mg:pp_device'}],
        'properties': [
            {'id': 'mg:pp_system_capacity', 'displayName': '额定容量', 'apiName': CAP_PROP,
             'objectTypeId': 'mg:pp_system', 'dataType': {'type': 'double'}},
            {'id': 'mg:pp_system_display', 'displayName': '系统名称', 'apiName': DISPLAY_PROP,
             'objectTypeId': 'mg:pp_system', 'dataType': {'type': 'string'}},
            {'id': 'mg:pp_device_capacity', 'displayName': '额定容量', 'apiName': CAP_PROP,
             'objectTypeId': 'mg:pp_device', 'dataType': {'type': 'double'}},
            {'id': 'mg:pp_device_name', 'displayName': '设备名称', 'apiName': NAME_PROP,
             'objectTypeId': 'mg:pp_device', 'dataType': {'type': 'string'}}],
    }
    for group, items in records.items():
        draft['ontology'][group] += items
    draft['ontology']['definitionOrder'] += [r['id'] for items in records.values() for r in items]
    status, saved = request('POST', '/api/save', {'state': draft, 'revision': rev})
    check(status == 200 and saved.get('revision'), 'POST /api/save 应 200', actual=(status, saved), expected=200)
    status, published = request('POST', '/api/publish', {'state': draft, 'revision': saved['revision']})
    check(status == 200 and published.get('version'), 'POST /api/publish 应产出版本',
          actual=(status, published), expected=200)
    return onto_id, published['version']


def main():
    global PROC, pid, state, revision
    create_tables()

    probe = socket.socket()
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', PORT))
    except OSError as exc:
        print(f'[失败] 端口 {PORT} 已被占用：{exc}')
        sys.exit(1)
    finally:
        probe.close()
    env = dict(os.environ, WIZ_WORKBENCH_ROOT=str(TMP), WIZ_WORKBENCH_PORT=str(PORT))
    PROC = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.time() + 20
    ready = False
    while time.time() < deadline:
        if PROC.poll() is not None:
            print('[失败] 服务进程提前退出：')
            print(PROC.stdout.read().decode(errors='replace'))
            sys.exit(1)
        try:
            status, _ = request('GET', '/api/ontologies')
            if status == 200:
                ready = True
                break
            time.sleep(0.2)
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    check(ready, '服务未在 20 秒内就绪', actual='未就绪', expected='200')
    ok('0', f'隔离服务就绪（{TMP}，端口 {PORT}）；测试表已建（devices/dup/bulk，bulk 10001 行）')

    # --- 1) 本体：系统 一对多 包含 设备，发布 ------------------------------------------------
    onto_id, version = build_ontology()
    ok('1', f'本体已发布 {onto_id[:8]}… @ {version}（pp_system --pp_contains(1:N)--> pp_device）')

    # --- 2) 项目 + 连接 + 凭据 + 真实目录刷新 -----------------------------------------------
    status, created = request('POST', '/api/projects',
                               {'name': '关联聚合预览测试项目', 'ontology': onto_id, 'version': version})
    check(status == 201 and created.get('id'), 'POST /api/projects 应 201', actual=(status, created), expected=201)
    pid = created['id']
    status, payload = request('GET', f'/api/project-state?project={pid}')
    check(status == 200, 'GET /api/project-state 应 200', actual=status, expected=200)
    state, revision = payload['state'], payload['revision']
    state['connections'] = {'connections': [
        {'id': CONN_ID, 'name': '聚合预览测试库', 'engine': 'mysql', 'host': DB['host'],
         'port': DB['port'], 'username': DB['user'], 'database': DB['database'], 'tls': 'none'}]}
    save_project('（连接）')
    status, secret_saved = request('POST', '/api/connection-secret',
                                   {'projectId': pid, 'connectionId': CONN_ID, 'action': 'set',
                                    'secret': DB['password']})
    check(status == 200 and secret_saved.get('saved'), 'connection-secret 应保存成功',
          actual=(status, secret_saved), expected={'saved': True})
    status, catalog = request('POST', '/api/catalog-refresh', {'projectId': pid, 'connectionId': CONN_ID})
    table_names = {t.get('name') for t in catalog.get('tables', [])}
    check(status == 200 and catalog.get('ok') and {MAIN_TABLE, DUP_TABLE, BULK_TABLE} <= table_names,
          'catalog-refresh 应真实读取到三张测试表', actual=table_names,
          expected=f'包含 {MAIN_TABLE}/{DUP_TABLE}/{BULK_TABLE}')
    ok('2', f'项目 {pid} 已建；连接凭据入 vault；目录真实刷新（{len(table_names)} 表）')

    # --- 3) 绑定：registered 系统 + membership + aggregate；设备表映射 ----------------------
    state['bindings']['object_bindings'] = [
        {'object_type': SYS_TYPE,
         'identity': {'kind': 'registered', 'instances': [
             {'id': INSTANCE_MAIN, 'label': '创智园二期储能系统'},
             {'id': INSTANCE_OTHER, 'label': '对照系统'}]},
         'properties': {CAP_PROP: dict(AGGREGATE),
                        DISPLAY_PROP: {'kind': 'registered', 'field': 'label'}},
         'relations': [{'kind': 'membership', 'relation': LINK_KEY, 'target_type': DEVICE_TYPE,
                        'rules': [
                            {'sourceInstance': INSTANCE_MAIN, 'scope': 'filtered', 'conditions': [
                                {'field': 'park', 'operator': 'eq', 'value': 'CZY'},
                                {'field': 'del_flag', 'operator': 'eq', 'value': 0}]},
                            {'sourceInstance': INSTANCE_OTHER, 'scope': 'filtered', 'conditions': [
                                {'field': 'park', 'operator': 'eq', 'value': 'OTHER2'}]}]}]},
        {'object_type': DEVICE_TYPE, 'connection': CONN_ID, 'table': MAIN_TABLE,
         'primary_key': 'id', 'title_key': NAME_PROP,
         'properties': {CAP_PROP: 'capacity', NAME_PROP: 'name'}, 'relations': []},
    ]
    save_project('（绑定）')
    ok('3', '项目草稿保存：registered 系统×2 + membership(park/del_flag) + aggregate capacity + 设备表直映射')

    # --- 4) A6 主链路：200+300=500，成员恰为两台项目设备 ------------------------------------
    status, body = preview()
    check(status == 200, '预览应 200', actual=(status, body), expected=200)
    check(body.get('status') == 'ok' and body.get('value') == 500,
          'A6 聚合结果应为 500（200+300）', actual={'status': body.get('status'), 'value': body.get('value')},
          expected={'status': 'ok', 'value': 500})
    check(body.get('memberCount') == 2 and body.get('missingCount') == 0,
          '成员数应为 2、缺失 0', actual=(body.get('memberCount'), body.get('missingCount')), expected=(2, 0))
    member_ids = sorted(m.get('id') for m in body.get('membersPreview', []))
    check(member_ids == [1, 2], '成员应为设备 1/2（他园区 3 与已删除 4 被条件排除）',
          actual=member_ids, expected=[1, 2])
    member = body['membersPreview'][0]
    check(member.get('label') == '设备A' and member.get('fields', {}).get('park') == 'CZY'
          and member.get('fields', {}).get('del_flag') == 0,
          '成员明细应含 label 与参与过滤的字段值', actual=member,
          expected={'label': '设备A', 'fields': {'park': 'CZY', 'del_flag': 0}})
    check(body.get('truncated') is False, '2 条成员不应截断', actual=body.get('truncated'), expected=False)
    check(bool(body.get('evaluatedAt')) and isinstance(datetime.fromisoformat(body['evaluatedAt']), datetime),
          'evaluatedAt 应为合法 ISO 时间', actual=body.get('evaluatedAt'), expected='ISO 时间串')
    check('求和' in body.get('message', ''), 'message 应包含计算说明', actual=body.get('message'), expected='包含「求和」')
    ok('4', f'A6 通过：value=500 memberCount=2，成员={member_ids}，message="{body["message"]}"')

    # --- 5) registered 属性预览（不查库） ----------------------------------------------------
    status, body = preview(prop=DISPLAY_PROP)
    check(status == 200 and body.get('status') == 'ok' and body.get('value') == '创智园二期储能系统',
          'registered 属性预览应返回登记 label', actual={'status': body.get('status'), 'value': body.get('value')},
          expected={'status': 'ok', 'value': '创智园二期储能系统'})
    check(body.get('membersPreview') == [] and body.get('memberCount') == 0,
          'registered 预览不应有成员明细', actual=body.get('membersPreview'), expected=[])
    ok('5', 'registered 属性预览：value=登记 label，未查库')

    # --- 5b) 仅成员预览（property 为空/null）--------------------------------------------------
    for empty_prop in ('', None):
        status, body = request('POST', '/api/project-property-preview', {
            'projectId': pid, 'revision': revision, 'objectType': SYS_TYPE,
            'instanceId': INSTANCE_MAIN, 'property': empty_prop})
        check(status == 200 and body.get('status') == 'ok' and body.get('value') is None,
              '仅成员预览应 status=ok 且 value=null', actual={'status': body.get('status'), 'value': body.get('value')},
              expected={'status': 'ok', 'value': None})
        check(body.get('memberCount') == 2 and body.get('missingCount') == 0
              and sorted(m.get('id') for m in body.get('membersPreview', [])) == [1, 2]
              and body.get('message') == '仅成员预览' and body.get('truncated') is False,
              '仅成员预览应返回成员明细（不取值字段）',
              actual={'memberCount': body.get('memberCount'), 'message': body.get('message')},
              expected={'memberCount': 2, 'message': '仅成员预览'})
    status, body = preview(instance=INSTANCE_OTHER, prop='')
    check(status == 200 and body.get('status') == 'ok' and body.get('memberCount') == 0
          and body.get('membersPreview') == [],
          '对照系统仅成员预览应为 0 成员', actual=body.get('memberCount'), expected=0)
    ok('5b', '仅成员预览（property 空/null）：主系统 2 成员；对照系统 0 成员；value=null message="仅成员预览"')

    # --- 6) 条件操作符：ne / in / notnull / isnull -------------------------------------------
    set_main_rule([{'field': 'park', 'operator': 'ne', 'value': 'OTHER'},
                   {'field': 'del_flag', 'operator': 'eq', 'value': 0}])
    save_project('（ne 条件）')
    status, body = preview()
    check(status == 200 and body.get('value') == 500 and body.get('memberCount') == 2,
          'ne 条件应排除他园区设备，结果仍 500', actual=(body.get('value'), body.get('memberCount')), expected=(500, 2))
    db_exec(f"INSERT INTO {MAIN_TABLE} (id, name, park, del_flag, capacity) VALUES "
            f"(8, '空标记设备', 'NULLTEST', NULL, 77), (9, '正常标记设备', 'NULLTEST', 0, 11)")
    set_main_rule([{'field': 'park', 'operator': 'in', 'value': ['NULLTEST']},
                   {'field': 'del_flag', 'operator': 'notnull'}])
    save_project('（in/notnull 条件）')
    status, body = preview()
    check(status == 200 and body.get('value') == 11 and body.get('memberCount') == 1
          and body['membersPreview'][0]['id'] == 9,
          'in + notnull 应仅命中 del_flag 非空的设备 9（值 11；del_flag 为 NULL 的 8 被排除）',
          actual={'value': body.get('value'), 'memberCount': body.get('memberCount')}, expected={'value': 11, 'memberCount': 1})
    db_exec(f'DELETE FROM {MAIN_TABLE} WHERE id IN (8, 9)')
    db_exec(f'INSERT INTO {MAIN_TABLE} (id, name, park, del_flag, capacity) VALUES (6, "无园区设备", NULL, 0, 42)')
    set_main_rule([{'field': 'park', 'operator': 'isnull'}])
    save_project('（isnull 条件）')
    status, body = preview()
    check(status == 200 and body.get('value') == 42 and body.get('memberCount') == 1
          and body['membersPreview'][0]['id'] == 6,
          'isnull 条件应命中 park 为空的设备 6（值 42）', actual=body, expected='value=42 memberCount=1')
    db_exec(f'DELETE FROM {MAIN_TABLE} WHERE id = 6')
    set_main_rule([{'field': 'park', 'operator': 'eq', 'value': 'CZY'},
                   {'field': 'del_flag', 'operator': 'eq', 'value': 0}])
    save_project('（恢复主条件）')
    ok('6', '操作符覆盖：eq/ne/in/isnull/notnull 均参数化生效（isnull 命中 NULL park 设备=42）')

    # --- 7) A11：409 拒绝过期 revision 预览 --------------------------------------------------
    stale_revision = revision
    set_main_rule([{'field': 'park', 'operator': 'eq', 'value': 'CZY'},
                   {'field': 'del_flag', 'operator': 'eq', 'value': 0},
                   {'field': 'park', 'operator': 'notnull'}])
    save_project('（为 409 制造新 revision）')
    check(revision != stale_revision, '保存后 revision 应更新', actual=revision[:12], expected='不同于旧值')
    status, body = preview(rev=stale_revision)
    check(status == 409, '旧 revision 预览应 409', actual=(status, body), expected=409)
    check(body.get('error') == '项目配置已更新，请刷新后重试' and body.get('currentRevision') == revision,
          '409 应携带规范文案与 currentRevision', actual=body,
          expected={'error': '项目配置已更新，请刷新后重试', 'currentRevision': revision})
    status, body = preview()
    check(status == 200 and body.get('value') == 500, '新 revision 预览应恢复可用',
          actual=(status, body.get('value')), expected=(200, 500))
    ok('7', f'409 生效（旧 rev={stale_revision[:12]}… 被拒，currentRevision 随响应返回）；保存后 revision 已更新')

    # --- 8) A7 缺失容量 → incomplete ---------------------------------------------------------
    db_exec(f'INSERT INTO {MAIN_TABLE} (id, name, park, del_flag, capacity) VALUES (5, "缺容量设备", "CZY", 0, NULL)')
    status, body = preview()
    check(status == 200 and body.get('status') == 'incomplete' and body.get('value') is None,
          '缺失容量应 incomplete 且 value=null（不把缺失当 0）',
          actual={'status': body.get('status'), 'value': body.get('value')},
          expected={'status': 'incomplete', 'value': None})
    check(body.get('memberCount') == 3 and body.get('missingCount') == 1,
          '成员 3 台、缺失 1 台', actual=(body.get('memberCount'), body.get('missingCount')), expected=(3, 1))
    db_exec(f'DELETE FROM {MAIN_TABLE} WHERE id = 5')
    ok('8', 'A7 incomplete：NULL 容量成员 → value=null + missingCount=1（0 仍是合法值）')

    # --- 9) A7 空成员 → empty ------------------------------------------------------------------
    status, body = preview(instance=INSTANCE_OTHER)
    check(status == 200 and body.get('status') == 'empty' and body.get('value') is None
          and body.get('memberCount') == 0,
          '对照系统（park=OTHER2 无匹配）应 empty', actual=body.get('status'), expected='empty')
    check('未匹配到成员' in body.get('message', ''), 'empty 应有明确文案', actual=body.get('message'),
          expected='包含「未匹配到成员」')
    ok('9', 'A7 empty：条件无匹配 → status=empty value=null message="未匹配到成员设备"')

    # --- 10) 显式 scope all 才全表（含他园区与已删除设备） --------------------------------------
    set_main_rule([], scope='all')
    save_project('（scope all）')
    status, body = preview()
    check(status == 200 and body.get('value') == 1999 and body.get('memberCount') == 4,
          '显式全表应包含 4 行（200+300+999+500=1999）', actual=(body.get('value'), body.get('memberCount')),
          expected=(1999, 4))
    set_main_rule([], scope='filtered')
    save_project('（filtered 0 条件）')
    status, body = preview()
    check(status == 200 and body.get('status') == 'error'
          and '至少' in body.get('message', '') and '条件' in body.get('message', ''),
          'filtered 且 0 条件应被执行器拦截（不得静默全表）',
          actual={'status': body.get('status'), 'message': body.get('message')}, expected='error + 至少…条件')
    set_main_rule([{'field': 'park', 'operator': 'eq', 'value': 'CZY'},
                   {'field': 'del_flag', 'operator': 'eq', 'value': 0}])
    save_project('（恢复主条件）')
    ok('10', '范围语义：scope=all 显式全表（1999/4 行）；filtered 0 条件被拦（"至少一条"）')

    # --- 11) 字段白名单：不在目录 → error -------------------------------------------------------
    set_main_rule([{'field': 'ghost_field', 'operator': 'eq', 'value': 'x'},
                   {'field': 'del_flag', 'operator': 'eq', 'value': 0}])
    save_project('（目录外字段）')
    status, body = preview()
    check(status == 200 and body.get('status') == 'error' and '不在目录中' in body.get('message', ''),
          '目录外条件字段应报错', actual={'status': body.get('status'), 'message': body.get('message')},
          expected='error + 不在目录中')
    set_main_rule([{'field': 'park', 'operator': 'eq', 'value': 'CZY'},
                   {'field': 'del_flag', 'operator': 'eq', 'value': 0}])
    save_project('（恢复主条件）')
    ok('11', 'A9 白名单：条件字段不在目录 → error「字段不在目录中，请刷新表结构」')

    # --- 12) A7 一对多归属重叠 → error ----------------------------------------------------------
    main_rules()[1] = {'sourceInstance': INSTANCE_OTHER, 'scope': 'filtered', 'conditions': [
        {'field': 'park', 'operator': 'eq', 'value': 'CZY'}]}
    save_project('（制造归属重叠）')
    status, body = preview()
    check(status == 200 and body.get('status') == 'error'
          and '成员同时属于多个系统实例' in body.get('message', ''),
          '一对多链接下成员落入多个实例应报归属冲突',
          actual={'status': body.get('status'), 'message': body.get('message')},
          expected='error + 成员同时属于多个系统实例')
    main_rules()[1] = {'sourceInstance': INSTANCE_OTHER, 'scope': 'filtered', 'conditions': [
        {'field': 'park', 'operator': 'eq', 'value': 'OTHER2'}]}
    save_project('（恢复对照规则）')
    status, body = preview()
    check(status == 200 and body.get('value') == 500, '恢复不重叠后预览应回到 500',
          actual=(status, body.get('value')), expected=(200, 500))
    ok('12', 'A7 归属重叠：对照系统条件改为 CZY → 主系统预览报「成员同时属于多个系统实例」')

    # --- 13) 重复主键取值冲突 → error ------------------------------------------------------------
    device = state['bindings']['object_bindings'][1]
    device['table'] = DUP_TABLE
    set_main_rule([{'field': 'park', 'operator': 'eq', 'value': 'DUP'}])
    save_project('（切到重复主键表）')
    status, body = preview()
    check(status == 200 and body.get('status') == 'error' and '重复主键' in body.get('message', '')
          and '冲突' in body.get('message', ''),
          '同主键不同容量应报取值冲突（不静默取一条）',
          actual={'status': body.get('status'), 'message': body.get('message')},
          expected='error + 重复主键 + 冲突')
    device['table'] = MAIN_TABLE
    set_main_rule([{'field': 'park', 'operator': 'eq', 'value': 'CZY'},
                   {'field': 'del_flag', 'operator': 'eq', 'value': 0}])
    save_project('（恢复主表）')
    ok('13', 'A7 冲突：专用表两行同 id 不同容量 → error「重复主键…取值冲突」')

    # --- 14) A9 超量阻断：10001 行 → error，不求和 -----------------------------------------------
    device['table'] = BULK_TABLE
    set_main_rule([{'field': 'park', 'operator': 'eq', 'value': 'BULK'}])
    save_project('（切到超量表）')
    status, body = preview()
    check(status == 200 and body.get('status') == 'error' and '超过 10000' in body.get('message', '')
          and body.get('value') is None,
          '候选 10001 行应直接报错且不求和',
          actual={'status': body.get('status'), 'message': body.get('message'), 'value': body.get('value')},
          expected='error + 超过 10000 + value=null')
    device['table'] = MAIN_TABLE
    set_main_rule([{'field': 'park', 'operator': 'eq', 'value': 'CZY'},
                   {'field': 'del_flag', 'operator': 'eq', 'value': 0}])
    save_project('（恢复主表）')
    ok('14', 'A9 超量：10001 行 → error「候选记录超过 10000，请缩小成员范围」，未对截断记录求和')

    # --- 15) membersPreview 上限 20 与 truncated -------------------------------------------------
    db_exec(f'INSERT INTO {MAIN_TABLE} (id, name, park, del_flag, capacity) VALUES '
            + ', '.join(f'({i}, "批量设备{i}", "MANY", 0, 1)' for i in range(100, 121)))
    set_main_rule([{'field': 'park', 'operator': 'eq', 'value': 'MANY'}])
    save_project('（21 台成员）')
    status, body = preview()
    check(status == 200 and body.get('value') == 21 and body.get('memberCount') == 21
          and len(body.get('membersPreview', [])) == 20 and body.get('truncated') is True,
          '21 台成员应 value=21、明细 20 条、truncated=true',
          actual={'value': body.get('value'), 'memberCount': body.get('memberCount'),
                  'preview': len(body.get('membersPreview', [])), 'truncated': body.get('truncated')},
          expected={'value': 21, 'memberCount': 21, 'preview': 20, 'truncated': True})
    db_exec(f'DELETE FROM {MAIN_TABLE} WHERE id >= 100')
    set_main_rule([{'field': 'park', 'operator': 'eq', 'value': 'CZY'},
                   {'field': 'del_flag', 'operator': 'eq', 'value': 0}])
    save_project('（恢复主条件）')
    ok('15', 'membersPreview ≤20：21 台成员 → 明细 20 条 + truncated=true（value=21）')

    # --- 16) A8 不支持的成员来源 → 明确 error ----------------------------------------------------
    device['properties'][CAP_PROP] = {'kind': 'redis', 'connection': 'conn-x', 'command': 'GET',
                                      'key': 'dev-{id}', 'params': {'id': {'from': 'primary'}}}
    save_project('（Redis 成员来源）')
    status, body = preview()
    check(status == 200 and body.get('status') == 'error' and 'Redis' in body.get('message', ''),
          'Redis 成员来源应明确报错', actual=body.get('message'), expected='error + Redis')
    device['properties'][CAP_PROP] = dict(AGGREGATE)
    save_project('（聚合嵌套成员来源）')
    status, body = preview()
    check(status == 200 and body.get('status') == 'error' and '聚合' in body.get('message', ''),
          '聚合依赖聚合应明确报错（防递归）', actual=body.get('message'), expected='error + 聚合')
    device['properties'][CAP_PROP] = {'kind': 'computed', 'implementation': 'impl-x', 'output': 'series'}
    save_project('（计算成员来源）')
    status, body = preview()
    check(status == 200 and body.get('status') == 'error' and '计算' in body.get('message', ''),
          '计算来源成员属性应明确报错', actual=body.get('message'), expected='error + 计算')
    device['properties'][CAP_PROP] = 'capacity'
    save_project('（恢复直取映射）')
    status, body = preview()
    check(status == 200 and body.get('value') == 500, '恢复直取映射后预览回到 500',
          actual=(status, body.get('value')), expected=(200, 500))
    ok('16', 'A8：Redis/聚合嵌套/计算 成员来源均明确报错，不假装执行成功')

    # --- 17) A9 密码不泄漏（认证失败走脱敏分类） --------------------------------------------------
    request('POST', '/api/connection-secret',
            {'projectId': pid, 'connectionId': CONN_ID, 'action': 'set', 'secret': 'wrong-secret-XYZ'})
    status, body = preview()
    check(status == 200 and body.get('status') == 'error',
          '错误凭据下预览应 status=error（脱敏消息）', actual={'status': body.get('status')}, expected='error')
    leaked = [token for token in (DB['password'], 'wrong-secret-XYZ')
              if token in json.dumps(body, ensure_ascii=False)]
    check(not leaked, '错误消息与响应体不得包含任何密码', actual=body.get('message'), expected='不含任何凭据')
    error_message = body.get('message', '')
    request('POST', '/api/connection-secret',
            {'projectId': pid, 'connectionId': CONN_ID, 'action': 'set', 'secret': DB['password']})
    status, body = preview()
    check(status == 200 and body.get('value') == 500, '恢复正确凭据后预览应回到 500',
          actual=(status, body.get('value')), expected=(200, 500))
    ok('17', f'A9 脱敏：错误凭据 → error「{error_message}」，响应不含 {DB["password"]}/wrong-secret-XYZ')

    # --- 18) 协议层错误：4xx ------------------------------------------------------------------------
    status, body = request('POST', '/api/project-property-preview',
                           {'projectId': pid, 'revision': revision, 'objectType': SYS_TYPE,
                            'instanceId': INSTANCE_MAIN, 'property': CAP_PROP})
    check(status == 200, '正常预览应 200（基线）', actual=(status, body.get('status')), expected=200)
    status, body = request('POST', '/api/project-property-preview',
                           {'projectId': pid, 'revision': revision, 'objectType': SYS_TYPE,
                            'property':CAP_PROP})
    check(status == 400, '缺 instanceId 参数应 400', actual=(status, body), expected=400)
    status, body = request('POST', '/api/project-property-preview',
                           {'projectId': pid, 'objectType': SYS_TYPE,
                            'instanceId': INSTANCE_MAIN, 'property': CAP_PROP})
    check(status == 400, '缺 revision 应 400（property 之外的必填项）', actual=(status, body), expected=400)
    status, body = request('POST', '/api/project-property-preview',
                           {'projectId': 'no-such-project', 'revision': revision,
                            'objectType': SYS_TYPE, 'instanceId': INSTANCE_MAIN, 'property': CAP_PROP})
    check(status == 404, '项目不存在应 404', actual=(status, body), expected=404)
    status, body = preview(instance='NOT-REGISTERED')
    check(status == 200 and body.get('status') == 'error' and '实例未登记' in body.get('message', ''),
          '未登记实例应 status=error', actual=body.get('message'), expected='error + 实例未登记')
    ok('18', '协议层：缺 objectType/instanceId 400；项目不存在 404；未登记实例 200+status=error')

    # --- 19) SQL 构造单元验证：标识与参数分离（直接调用执行器模块） ---------------------------------
    sys.path.insert(0, str(REPO))
    from workbench import project_property_reader as reader
    field_meta = {name: {'name': name, 'dataType': dtype, 'key': '', 'comment': ''}
                  for name, dtype in (('id', 'int'), ('name', 'varchar'), ('park', 'varchar'),
                                      ('del_flag', 'tinyint'), ('capacity', 'double'))}
    rule = {'sourceInstance': INSTANCE_MAIN, 'scope': 'filtered', 'conditions': [
        {'field': 'park', 'operator': 'eq', 'value': "CZY' OR 1=1 --"},
        {'field': 'del_flag', 'operator': 'eq', 'value': '0'}]}
    columns, sql, params, used = reader.compile_rule_query(rule, MAIN_TABLE, 'id', 'name', 'capacity', field_meta)
    check(sql == 'SELECT `id`, `name`, `park`, `del_flag`, `capacity` FROM `m_pp_test_devices` '
                 'WHERE `park` = %s AND `del_flag` = %s LIMIT 10001',
          'SQL 应为白名单反引号标识 + %s 占位', actual=sql, expected='SELECT `id`, `name`, `park`, `del_flag`, `capacity` FROM `m_pp_test_devices` WHERE `park` = %s AND `del_flag` = %s LIMIT 10001')
    check(params == ["CZY' OR 1=1 --", 0] and isinstance(params[1], int),
          '比较值应全部走参数（数值目录字段完成类型转换）', actual=params, expected=["CZY' OR 1=1 --", 0])
    for evil in ('id` OR 1=1 --', 'park; DROP TABLE x', 'a b', ''):
        try:
            reader.compile_rule_query({'scope': 'filtered', 'conditions': [
                {'field': evil, 'operator': 'eq', 'value': 1}]}, MAIN_TABLE, 'id', '', 'capacity', field_meta)
            check(False, f'注入字段「{evil}」应被拒绝', actual=evil, expected='PreviewFailed')
        except reader.PreviewFailed:
            pass
    ok('19', f'SQL 分离证明：{sql} ｜ params={params}；注入式字段名一律拒绝')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — 意外异常停服并留现场
        import traceback
        traceback.print_exc()
        shutdown()
        print(f'\n（临时根保留供排查：{TMP}）')
        sys.exit(1)
    finally:
        shutdown()
    drop_tables()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'\n统计：{len(PASSED)} 个步骤全部通过（0–19，含 5b）')
    print('临时根与测试表已清理')

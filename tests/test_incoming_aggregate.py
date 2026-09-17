"""入向多对一链接的成员规则与关联聚合端到端（设备—属于系统(many-to-one)→系统）。

验证「按端判定」扩展：链接方向为 设备→系统，系统在 range 侧仍可作为集合端：
membership 挂在系统绑定（target_type=设备），聚合沿该链接对设备容量求和。
隔离运行：临时 WIZ_WORKBENCH_ROOT + 端口 18950 + Docker MySQL（127.0.0.1:33066）。
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

import pymysql

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
PORT = 18950
BASE = f'http://127.0.0.1:{PORT}'
DB = {'host': '127.0.0.1', 'port': 33066, 'user': 'root', 'password': 'test123', 'database': 'energy'}
TABLE = 'm_in_agg_devices'
CONN = 'conn_in_agg'
SYS, DEV, LINK, CAP, NAME = 'in_system', 'in_device', 'in_belongsTo', 'capacity', 'name'

TMP = Path(tempfile.mkdtemp(prefix='wiz_incoming_agg_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
PASSED = []


def check(cond, message, actual=None, expected=None):
    if not cond:
        print(f'[失败] {message}')
        if actual is not None:
            print(f'  实际: {actual}')
        if expected is not None:
            print(f'  预期: {expected}')
        sys.exit(1)


def ok(step, desc):
    PASSED.append(step)
    print(f'通过 {step}) {desc}')


def request(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json', 'Origin': BASE})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode())


def db_exec(sql, params=None):
    conn = pymysql.connect(host=DB['host'], port=DB['port'], user=DB['user'],
                           password=DB['password'], database=DB['database'])
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.commit()
    finally:
        conn.close()


def main():
    db_exec(f'DROP TABLE IF EXISTS `{TABLE}`')
    db_exec(f'''CREATE TABLE `{TABLE}` (
        `id` INT PRIMARY KEY,
        `name` VARCHAR(64) NOT NULL,
        `park` VARCHAR(32) NOT NULL,
        `capacity` DOUBLE NULL)''')
    conn = pymysql.connect(host=DB['host'], port=DB['port'], user=DB['user'],
                           password=DB['password'], database=DB['database'])
    with conn.cursor() as cur:
        cur.executemany(f'INSERT INTO `{TABLE}` VALUES (%s,%s,%s,%s)',
                        [(1, '设备A', 'CZY', 200), (2, '设备B', 'CZY', 300),
                         (3, '他园设备', 'OTHER', 400)])
    conn.commit()
    conn.close()

    env = dict(os.environ, WIZ_WORKBENCH_ROOT=str(TMP), WIZ_WORKBENCH_PORT=str(PORT))
    proc = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                if request('GET', '/api/ontologies')[0] == 200:
                    break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.2)
        ok('0', f'隔离服务就绪（{TMP}，端口 {PORT}）')

        # 本体：设备 —属于系统(多对一)→ 系统（入向）
        _, created = request('POST', '/api/ontologies', {'name': '入向聚合测试本体'})
        onto = created['id']
        _, payload = request('GET', f'/api/state?ontology={onto}')
        draft, rev = payload['state'], payload['revision']
        draft['ontology']['objectTypes'] += [
            {'id': 'mg:' + SYS, 'displayName': '储能系统'},
            {'id': 'mg:' + DEV, 'displayName': '储能设备'}]
        draft['ontology']['linkTypes'] += [
            {'id': 'mg:' + LINK, 'displayName': '属于系统', 'cardinality': 'many-to-one',
             'sourceObjectTypeId': 'mg:' + DEV, 'targetObjectTypeId': 'mg:' + SYS}]
        draft['ontology']['properties'] += [
            {'id': f'mg:{SYS}_cap', 'displayName': '额定容量', 'apiName': CAP,
             'objectTypeId': 'mg:' + SYS, 'dataType': {'type': 'double'}},
            {'id': f'mg:{DEV}_cap', 'displayName': '额定容量', 'apiName': CAP,
             'objectTypeId': 'mg:' + DEV, 'dataType': {'type': 'double'}},
            {'id': f'mg:{DEV}_name', 'displayName': '设备名称', 'apiName': NAME,
             'objectTypeId': 'mg:' + DEV, 'dataType': {'type': 'string'}}]
        draft['ontology']['definitionOrder'] += [r['id'] for g in ('objectTypes', 'linkTypes', 'properties')
                                                 for r in draft['ontology'][g]]
        _, saved = request('POST', '/api/save', {'state': draft, 'revision': rev})
        _, published = request('POST', '/api/publish', {'state': draft, 'revision': saved['revision']})
        ok('1', f'本体已发布（{DEV} —{LINK}(多对一)→ {SYS}，链接方向为设备→系统）')

        _, created = request('POST', '/api/projects', {'name': '入向聚合测试项目', 'ontology': onto,
                                                       'version': published['version']})
        pid = created['id']
        _, payload = request('GET', f'/api/project-state?project={pid}')
        state, rev = payload['state'], payload['revision']
        state['connections'] = {'connections': [
            {'id': CONN, 'name': '入向测试库', 'engine': 'mysql', 'host': DB['host'],
             'port': DB['port'], 'username': DB['user'], 'database': DB['database'], 'tls': 'none'}]}
        _, body = request('POST', '/api/project-save', {'state': state, 'revision': rev})
        rev = body['revision']
        request('POST', '/api/connection-secret', {'projectId': pid, 'connectionId': CONN,
                                                   'action': 'set', 'secret': DB['password']})
        request('POST', '/api/catalog-refresh', {'projectId': pid, 'connectionId': CONN})
        _, payload = request('GET', f'/api/project-state?project={pid}')
        state, rev = payload['state'], payload['revision']
        # membership 挂在系统（range 侧）绑定上；target_type = 成员端（domain 侧设备）
        state['bindings']['object_bindings'] = [
            {'object_type': SYS,
             'identity': {'kind': 'registered', 'instances': [{'id': 'XT001', 'label': '入向测试系统'}]},
             'properties': {CAP: {'kind': 'aggregate', 'relation': LINK, 'property': CAP,
                                  'operator': 'sum', 'empty': 'null', 'missing': 'incomplete',
                                  'inputUnitConfirmed': True}},
             'relations': [{'kind': 'membership', 'relation': LINK, 'target_type': DEV,
                            'rules': [{'sourceInstance': 'XT001', 'scope': 'filtered', 'conditions': [
                                {'field': 'park', 'operator': 'eq', 'value': 'CZY'}]}]}]},
            {'object_type': DEV, 'connection': CONN, 'table': TABLE,
             'primary_key': 'id', 'title_key': NAME,
             'properties': {CAP: 'capacity', NAME: 'name'}, 'relations': []}]
        _, body = request('POST', '/api/project-save', {'state': state, 'revision': rev})
        rev = body['revision']
        ok('2', '项目保存：membership 在系统绑定（target_type=设备）+ 聚合沿入向链接')

        status, report = request('POST', '/api/project-validate', {'state': state})
        errors = report.get('errors', []) if isinstance(report, dict) else []
        check(not errors, '入向配置应无校验错误', actual=errors, expected=[])
        ok('3', '项目校验通过（按端判定：系统=range 侧集合端，成员=设备）')

        status, body = request('POST', '/api/project-property-preview', {
            'projectId': pid, 'revision': rev, 'objectType': SYS,
            'instanceId': 'XT001', 'property': CAP})
        check(status == 200 and body.get('status') == 'ok' and body.get('value') == 500,
              '入向聚合结果应为 500（200+300，他园 400 排除）',
              actual={'status': body.get('status'), 'value': body.get('value')},
              expected={'status': 'ok', 'value': 500})
        member_ids = sorted(m.get('id') for m in body.get('membersPreview', []))
        check(member_ids == [1, 2], '成员应为设备 1/2', actual=member_ids, expected=[1, 2])
        ok('4', f'预览 500，成员 {member_ids}（message="{body["message"]}"）')

        # 仅成员预览（property=null）也应工作
        status, body = request('POST', '/api/project-property-preview', {
            'projectId': pid, 'revision': rev, 'objectType': SYS, 'instanceId': 'XT001', 'property': None})
        check(status == 200 and body.get('memberCount') == 2,
              '入向成员预览应返回 2 个成员', actual=body.get('memberCount'), expected=2)
        ok('5', '仅成员预览（property=null）通过')

        # 反例与方向切换：同一链接（设备→系统）改为一对多后，系统（“多”端）不能作集合端；
        # 设备侧（domain+一对多）则成为集合端——判定随端与数量关系切换，与方向解耦。
        from workbench.project_mapping import member_side_of
        simple_graph = [
            {'@id': 'mg:' + LINK, '@type': 'owl:ObjectProperty',
             'rdfs:domain': {'@id': 'mg:' + DEV}, 'rdfs:range': {'@id': 'mg:' + SYS},
             'mg:cardinality': 'one-to-many'}]
        member, _c, o = member_side_of(simple_graph, SYS, LINK)
        check(member == '' and o == '', '设备→系统 one-to-many 时系统不能作为集合端',
              actual=(member, o), expected=('', ''))
        member, _c, o = member_side_of(simple_graph, DEV, LINK)
        check(member == SYS and o == 'out', '设备侧作为集合端（domain+one-to-many）成立',
              actual=(member, o), expected=(SYS, 'out'))
        ok('6', '反例通过：数量关系不满足时集合端判定为空；方向反转后判定随端切换')
        print(f'\n统计：{len(PASSED) + 1} 个步骤全部通过（0–6）')
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        db_exec(f'DROP TABLE IF EXISTS `{TABLE}`')
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
        print('临时根与测试表已清理')


if __name__ == '__main__':
    main()

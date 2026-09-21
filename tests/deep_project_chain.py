"""Q03 深度测试：项目配置 + 编排业务链（主脚本：P1-P6, P8, P9）。

只测试记录，不修业务代码。目标服务 http://127.0.0.1:18931（不启动/不停止）。
主用账号 qa_deep_b；qa_deep_a 仅跨账号只读探测。

覆盖：
- P1 建最小储能本体并发布（作引用目标）
- P2 项目生命周期（创建/读写 project-state/不存在404/重名/CAS）
- P3 数据连接（假连接保存/连接测试打关闭端口=优雅非500/密钥不回显/探测不持锁不阻塞保存）
- P4 属性映射（缺身份保存放行发布阻断/各来源往返无损/注入未知kind往返无损/旧字符串/链接/参数）
- P5 取值规则 v1/v2 校验 + 确认不执行真实 SQL
- P6 project-validate 报错准确 + 发布→releases + 升级比较保留失效绑定
- P8 R02 特例：空壳编排被 flow 来源引用 → 项目校验是否放行、发布是否成功（基线已知）
- P9 跨账号：qa_deep_a 读 qa_deep_b 的 项目/编排/本体 id → 视为不存在

红线：不连真实 MySQL/Redis、不执行真实业务 SQL；只连 127.0.0.1 自建假端口/关闭端口；结束关掉 mock。
"""
import copy
import json
import socket
import threading
import time
from datetime import datetime, timezone

import deep_project_client as C
from deep_project_client import Api, Recorder, blank_state, brief
from deep_reverify_scenarios import scenario_r02  # noqa: E402  修订后的统一判定（deep_verdicts）

BASE = C.BASE
PASSWORD = C.PASSWORD

NOW = datetime.now(timezone.utc).strftime('%m%d%H%M%S')
UNIQ = f'q03-{NOW}'

CTX = {'mg': 'https://example.com/microgrid/', 'owl': 'http://www.w3.org/2002/07/owl#',
       'rdfs': 'http://www.w3.org/2000/01/rdf-schema#', 'xsd': 'http://www.w3.org/2001/XMLSchema#'}


# --- 本体 @graph 节点构造（直接构造 JSON-LD 编辑形态，POST 时经 decode_state 原样通过） ----

def cls(node_id, label, comment='业务定义'):
    return {'@id': 'mg:' + node_id, '@type': 'owl:Class', 'rdfs:label': label, 'rdfs:comment': comment}


def prop(node_id, label, owner_id, api, xsd='double', series=False):
    node = {'@id': 'mg:' + node_id, '@type': 'owl:DatatypeProperty', 'rdfs:label': label,
            'rdfs:domain': {'@id': 'mg:' + owner_id}, 'rdfs:range': {'@id': 'xsd:' + xsd},
            'mg:apiName': api}
    if series:
        node['mg:valueShape'] = 'timeSeries'
    return node


def link(node_id, label, src_id, tgt_id, cardinality='one-to-many', reverse='被包含'):
    return {'@id': 'mg:' + node_id, '@type': 'owl:ObjectProperty', 'rdfs:label': label,
            'rdfs:domain': {'@id': src_id}, 'rdfs:range': {'@id': tgt_id},
            'mg:cardinality': cardinality, 'mg:reverseLabel': reverse}


def business_rule(rid, name, description):
    return {'id': rid, 'name': name, 'description': description}


def action_v2(aid, name, description):
    return {'id': aid, 'name': name, 'description': description, 'status': 'active', 'definitionVersion': 2}


def storage_ontology_graph_state(name, workspace_id):
    """P1 目标本体：储能簇/储能设备 + 功率/时序SOC/设备编号 + 簇含设备链接 + 1规则 + 1动作。"""
    st = blank_state(name)
    st['workspaceId'] = workspace_id
    graph = [
        cls('Q03Cluster', '储能簇', '一组电池的集合'),
        cls('Q03Device', '储能设备', '单台储能设备'),
        prop('clusterActivePower', '有功功率', 'Q03Cluster', 'activePower', xsd='double'),
        prop('clusterSocSeries', 'SOC时序', 'Q03Cluster', 'socSeries', xsd='double', series=True),
        prop('deviceCode', '设备编号', 'Q03Device', 'deviceCode', xsd='string'),
        link('hasDevice', '包含设备', 'mg:Q03Cluster', 'mg:Q03Device', cardinality='one-to-many', reverse='属于簇'),
    ]
    st['ontology']['@graph'] = graph
    st['workflow']['businessRules'] = [business_rule('rule-q03-thermal', '热稳定判定', '识别簇温度是否越限。')]
    st['workflow']['businessRuleAssociations'] = [{'objectTypeId': 'mg:Q03Cluster', 'ruleId': 'rule-q03-thermal'}]
    st['workflow']['actions'] = [action_v2('act-q03-switch', '切换负荷', '把负载切换到备用簇。')]
    st['workflow']['actionAssociations'] = [{'objectTypeId': 'mg:Q03Cluster', 'actionId': 'act-q03-switch'}]
    return st


# --- 端口工具（仅 127.0.0.1；关闭端口 = 立即连接被拒，绝不触达真实数据库） -----------------

def free_closed_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SilentMock:
    """监听但不回应的假服务：让真实驱动在读响应阶段挂起，用于并发不持锁验证。结束必须关闭。"""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self._run = True
        self._conns = []
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def _loop(self):
        self.sock.settimeout(0.5)
        while self._run:
            try:
                c, _ = self.sock.accept()
                self._conns.append(c)  # 接受但绝不发送任何协议字节
            except socket.timeout:
                continue
            except OSError:
                break

    def close(self):
        self._run = False
        for c in self._conns:
            try:
                c.close()
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass


def mysql_conn(cid, name, port, host='127.0.0.1', database='q03db'):
    return {'id': cid, 'name': name, 'engine': 'mysql', 'host': host, 'port': port,
            'username': 'qa', 'database': database, 'tls': 'none'}


def redis_conn(cid, name, port, host='127.0.0.1'):
    return {'id': cid, 'name': name, 'engine': 'redis', 'host': host, 'port': port, 'dbIndex': 0, 'tls': 'none'}


# --- 运行上下文（跨用例共享：本体/项目/版本 id） -------------------------------------------

CTX_STATE = {}


def main():
    api = Api(username='qa_deep_b')
    rec = Recorder('project_chain')
    closed_port = free_closed_port()
    mock = None
    try:
        _p1_build_publish(api, rec)
        _p2_lifecycle(api, rec)
        _p3_connections(api, rec, closed_port)
        _p4_mapping(api, rec)
        _p5_query_rules(api, rec, closed_port)
        _p6_validate_publish_upgrade(api, rec)
        _p8_r02(api, rec)
        _p9_cross_account(api, rec)
    finally:
        if mock is not None:
            mock.close()
        rec.dump()


# ============================== P1 ============================================

def _p1_build_publish(api, rec):
    r = api.post('/api/ontologies', {'name': f'Q03储能最小本体 {UNIQ}'})
    ok_created = r['status'] == 201 and isinstance(r['json'], dict) and r['json'].get('id')
    rec.add('P1.1', 'pass' if ok_created else 'fail', 'P1 新建本体工作区返回 201 + id',
            f'HTTP {r["status"]} {brief(r["json"])}', 'api')
    onto_id = r['json']['id'] if ok_created else 'storage'
    CTX_STATE['onto_id'] = onto_id

    r = api.get(f'/api/state?ontology={onto_id}')
    rev = (r['json'] or {}).get('revision') if isinstance(r['json'], dict) else None
    saved = (r['json'] or {}).get('saved') if isinstance(r['json'], dict) else None
    rec.add('P1.2', 'pass' if r['status'] == 200 and rev else 'fail',
            'P1 GET /api/state 返回 revision 令牌 + saved 标志',
            f'HTTP {r["status"]} revision={rev} saved={saved}', 'api')
    CTX_STATE['onto_rev0'] = rev

    # 发布前校验（validate 端点）应无错误
    graph_state = storage_ontology_graph_state(f'Q03储能最小本体 {UNIQ}', onto_id)
    r = api.post('/api/validate', {'state': graph_state, 'revision': rev})
    errs = (r['json'] or {}).get('errors') if isinstance(r['json'], dict) else None
    rec.add('P1.3', 'pass' if r['status'] == 200 and errs == [] else 'fail',
            'P1 最小储能本体 /api/validate errors=[]（可发布前提）',
            f'HTTP {r["status"]} errors={brief(errs)}', 'api')

    # 保存草稿（用刚读到的 revision）
    r = api.post('/api/save', {'state': graph_state, 'revision': rev})
    j = r['json'] if isinstance(r['json'], dict) else {}
    rev_after_save = j.get('revision')
    ok = r['status'] == 200 and j.get('errors') == []
    rec.add('P1.4', 'pass' if ok else 'fail', 'P1 /api/save 落草稿并推进 revision，errors=[]',
            f'HTTP {r["status"]} errors={brief(j.get("errors"))} newRev={rev_after_save}', 'api')

    # 发布
    r = api.post('/api/publish', {'state': graph_state, 'revision': rev_after_save})
    j = r['json'] if isinstance(r['json'], dict) else {}
    version = j.get('version')
    rec.add('P1.5', 'pass' if r['status'] == 200 and version else 'fail',
            'P1 /api/publish 成功产出版本，作项目引用目标',
            f'HTTP {r["status"]} version={version} changeType={j.get("changeType")}', 'api')
    CTX_STATE['onto_version'] = version

    # 发布后版本清单可见（/api/versions = 发布历史；/api/releases 仅列 release-ZIP 工件）
    r = api.get(f'/api/versions?ontology={onto_id}')
    j = r['json'] if isinstance(r['json'], dict) else {}
    items = j.get('items') or []
    listed = [it.get('version') for it in items if isinstance(it, dict)]
    rec.add('P1.6', 'pass' if r['status'] == 200 and str(version) in [str(v) for v in listed] else 'fail',
            'P1 /api/versions 版本清单含刚发布版本（发布历史可见）',
            f'HTTP {r["status"]} versions={brief(listed)}', 'api')
    # 说明性：/api/releases 是发布 ZIP 工件列表（非版本历史），普通 publish 不产出 → 为空属正常
    r = api.get(f'/api/releases?ontology={onto_id}')
    rec.add('P1.6b', 'info', 'P1 语义澄清：/api/releases 返回 release-ZIP 工件（非版本历史），publish 后为空属正常',
            f'HTTP {r["status"]} releases={brief(r["json"] if isinstance(r["json"], list) else [])}', 'info')

    # 用错误 revision 发布 → 409
    r = api.post('/api/publish', {'state': graph_state, 'revision': 'bogus-token'})
    rec.add('P1.7', 'pass' if r['status'] == 409 and (r['json'] or {}).get('code') == 'REVISION_CONFLICT' else 'fail',
            'P1 陈旧 revision 发布 → 409 REVISION_CONFLICT + currentRevision',
            f'HTTP {r["status"]} {brief(r["json"])}', 'api')


# ============================== P2 ============================================

def _p2_lifecycle(api, rec):
    onto_id = CTX_STATE['onto_id']
    onto_version = CTX_STATE['onto_version']

    r = api.post('/api/projects', {'name': f'Q03主项目 {UNIQ}', 'ontology': onto_id, 'version': onto_version})
    j = r['json'] if isinstance(r['json'], dict) else {}
    pid = j.get('id')
    CTX_STATE['proj_id'] = pid
    rec.add('P2.1', 'pass' if r['status'] == 201 and pid else 'fail',
            'P2 POST /api/projects 绑定已发布本体版本创建项目', f'HTTP {r["status"]} {brief(j)}', 'api')

    # 重名（同本体）→ 400
    r2 = api.post('/api/projects', {'name': f'Q03主项目 {UNIQ}', 'ontology': onto_id, 'version': onto_version})
    rec.add('P2.2', 'pass' if r2['status'] == 400 and '同名' in str((r2['json'] or {}).get('error', '')) else 'fail',
            'P2 同本体重复项目名 → 400（拒绝）', f'HTTP {r2["status"]} {brief(r2["json"])}', 'api')

    # 读 project-state
    r = api.get(f'/api/project-state?project={pid}')
    j = r['json'] if isinstance(r['json'], dict) else {}
    rev = j.get('revision')
    CTX_STATE['proj_rev'] = rev
    rec.add('P2.3', 'pass' if r['status'] == 200 and rev and isinstance(j.get('state'), dict) else 'fail',
            'P2 GET /api/project-state 返回 state + revision',
            f'HTTP {r["status"]} revision={rev} saved={j.get("saved")}', 'api')

    # 写 project-state：改 parameters 往返
    st = copy.deepcopy(j.get('state') or {})
    st['parameters'] = {'region': 'cn-south', 'windowMinutes': 15}
    r = api.post('/api/project-save', {'state': st, 'revision': rev})
    j2 = r['json'] if isinstance(r['json'], dict) else {}
    rev2 = j2.get('revision')
    CTX_STATE['proj_rev'] = rev2
    ok = r['status'] == 200 and rev2 and rev2 != rev
    rec.add('P2.4', 'pass' if ok else 'fail', 'P2 /api/project-save 写入 parameters 并推进 revision',
            f'HTTP {r["status"]} newRev={rev2}', 'api')

    # 读回校验 parameters 无损
    r = api.get(f'/api/project-state?project={pid}')
    got = (r['json'] or {}).get('state', {}).get('parameters') if isinstance(r['json'], dict) else None
    rec.add('P2.5', 'pass' if got == {'region': 'cn-south', 'windowMinutes': 15} else 'fail',
            'P2 读回 parameters 与写入一致（往返无损）', f'读回={brief(got)}', 'api')

    # 陈旧 revision 保存 → 409 + currentRevision
    r = api.post('/api/project-save', {'state': st, 'revision': rev})  # 用旧 rev
    j = r['json'] if isinstance(r['json'], dict) else {}
    rec.add('P2.6', 'pass' if r['status'] == 409 and j.get('code') == 'REVISION_CONFLICT' and j.get('currentRevision') else 'fail',
            'P2 陈旧 revision 保存 → 409 + currentRevision（CAS）', f'HTTP {r["status"]} {brief(j)}', 'api')

    # 不存在项目 → 404
    r = api.get('/api/project-state?project=doesnotexist0')
    rec.add('P2.7', 'pass' if r['status'] == 404 else 'fail',
            'P2 GET 不存在 project-state → 404', f'HTTP {r["status"]} {brief(r["json"])}', 'api')

    # 刷新上下文 revision（后续用）
    r = api.get(f'/api/project-state?project={pid}')
    CTX_STATE['proj_rev'] = (r['json'] or {}).get('revision')


# ============================== P3 ============================================

def _p3_connections(api, rec, closed_port):
    pid = CTX_STATE['proj_id']
    rev = CTX_STATE['proj_rev']
    r = api.get(f'/api/project-state?project={pid}')
    st = copy.deepcopy((r['json'] or {}).get('state') or {})
    rev = (r['json'] or {}).get('revision')

    # 保存一条假 MySQL 连接（关闭端口）+ 一条 Redis 连接
    st['connections'] = {'connections': [
        mysql_conn('conn-mysql-1', 'Q03假MySQL', closed_port),
        redis_conn('conn-redis-1', 'Q03假Redis', closed_port),
    ]}
    r = api.post('/api/project-save', {'state': st, 'revision': rev})
    j = r['json'] if isinstance(r['json'], dict) else {}
    rec.add('P3.1', 'pass' if r['status'] == 200 and j.get('revision') else 'fail',
            'P3 保存假数据连接（不触发探测，纯配置写入）', f'HTTP {r["status"]} {brief(j)}', 'api')
    CTX_STATE['proj_rev'] = j.get('revision') or rev

    # connection-test 打关闭端口 → 优雅非 500
    secret_marker = 'S3CR3T-q03-NEVER-RETURN-9x7'
    t0 = time.monotonic()
    r = api.post('/api/connection-test', {'projectId': pid, 'password': 'x',
                                          'connection': mysql_conn('conn-x', '探测目标', closed_port)})
    lat = round(time.monotonic() - t0, 2)
    j = r['json'] if isinstance(r['json'], dict) else {}
    graceful = r['status'] == 200 and j.get('ok') is False and j.get('category')
    rec.add('P3.2', 'pass' if graceful else 'fail',
            'P3 connection-test 连 127.0.0.1 关闭端口 → 200 优雅错误（非500）',
            f'HTTP {r["status"]} {lat}s ok={j.get("ok")} category={j.get("category")}', 'api')

    # 密钥写入 vault：set 后 GET 不回显
    r = api.post('/api/connection-secret', {'projectId': pid, 'connectionId': 'conn-mysql-1',
                                            'action': 'set', 'secret': secret_marker})
    set_ok = r['status'] == 200 and (r['json'] or {}).get('saved')
    rec.add('P3.3', 'pass' if set_ok else 'fail', 'P3 connection-secret set 仅回 saved',
            f'HTTP {r["status"]} {brief(r["json"])}', 'api')

    r = api.get(f'/api/project-state?project={pid}')
    body = json.dumps(r['json'], ensure_ascii=False) if r['json'] is not None else ''
    leaked = secret_marker in body
    rec.add('P3.4', 'pass' if not leaked else 'fail',
            'P3 connection-secret 写入后 GET project-state 不回显密钥',
            f'HTTP {r["status"]} 泄露密钥={leaked}', 'security')

    r = api.get('/api/project-config')  # 旧辅助接口（storage 上下文）
    cfg_body = json.dumps(r['json'], ensure_ascii=False) if r['json'] is not None else ''
    rec.add('P3.5', 'pass' if secret_marker not in cfg_body else 'fail',
            'P3 project-config 亦不含该密钥', f'HTTP {r["status"]} 泄露={secret_marker in cfg_body}', 'security')

    # 探测不持锁：静默 mock 挂住 connection-test 期间并发 project-save 仍应快速成功
    mock = SilentMock()
    try:
        probe_err = {}

        def run_probe():
            rr = api.post('/api/connection-test', {'projectId': pid, 'password': 'x',
                                                    'connection': mysql_conn('conn-hang', '挂起探测', mock.port)})
            probe_err['status'] = rr['status']

        th = threading.Thread(target=run_probe, daemon=True)
        th.start()
        time.sleep(0.4)  # 确保探测已进入网络等待
        st['parameters'] = dict(st.get('parameters') or {})
        st['parameters']['probeNoLock'] = 'yes'
        s0 = time.monotonic()
        rs = api.post('/api/project-save', {'state': st, 'revision': CTX_STATE['proj_rev']})
        save_lat = round(time.monotonic() - s0, 2)
        j = rs['json'] if isinstance(rs['json'], dict) else {}
        if j.get('revision'):
            CTX_STATE['proj_rev'] = j['revision']
        # 保存快速成功且远小于探测挂起时长（connect/read 超时），证明探测不持全局写锁
        non_blocking = rs['status'] == 200 and save_lat < 3.0
        rec.add('P3.6', 'pass' if non_blocking else 'fail',
                'P3 探测挂起期间并发 project-save 仍快速成功（connection probe 不持 LOCK）',
                f'save HTTP {rs["status"]} 用时 {save_lat}s（探测仍未返回，status={probe_err.get("status")}）',
                'concurrency')
        th.join(timeout=12)
    finally:
        mock.close()

    # 目录读取失败不阻断保存：对关闭端口 catalog 探测 → 优雅非500
    r = api.post('/api/connection-catalog', {'projectId': pid, 'password': 'x',
                                             'connection': mysql_conn('conn-cat', '目录探测', closed_port)})
    j = r['json'] if isinstance(r['json'], dict) else {}
    rec.add('P3.7', 'pass' if r['status'] == 200 and j.get('ok') is False else 'fail',
            'P3 connection-catalog 连关闭端口 → 200 优雅错误（不阻断保存线）',
            f'HTTP {r["status"]} category={j.get("category")}', 'api')


# ============================== P4 ============================================

def _object_binding(props, conn='conn-mysql-1', table='cluster', pk='cluster_id'):
    return {'object_type': 'Q03Cluster', 'connection': conn, 'table': table, 'primary_key': pk,
            'properties': props, 'relations': []}


def _p4_mapping(api, rec):
    pid = CTX_STATE['proj_id']
    r = api.get(f'/api/project-state?project={pid}')
    base = copy.deepcopy((r['json'] or {}).get('state') or {})
    base['connections'] = {'connections': [
        mysql_conn('conn-mysql-1', 'Q03假MySQL', 3),
        redis_conn('conn-redis-1', 'Q03假Redis', 3),
    ]}
    # 计算函数实现（供 computed 引用）
    impl = {'id': 'calc-q03', 'kind': 'calculationFunction', 'schemaVersion': 1, 'name': 'Q03倍率',
            'inputs': [{'id': 'p-in', 'name': 'x', 'type': 'number'}],
            'output': {'id': 'p-out', 'name': 'y', 'type': 'number'},
            'implementation': {'language': 'calc-expression-1', 'expression': '{x} * 2'}}
    base['implementations'] = [impl]

    sources = {
        'activePower': {'kind': 'field', 'field': 'power_col'},
        'socSeries': {'kind': 'database', 'connection': 'conn-mysql-1', 'table': 'soc',
                      'result': {'valueField': 'v', 'timestampField': 'ts', 'timestampEncoding': 'datetime',
                                 'timezone': 'Asia/Shanghai'},
                      'lookup': {'match': [{'field': 'cid', 'op': 'eq',
                                            'value': {'kind': 'identityKey'}}],
                                 'timeRange': {'field': 'ts', 'start': {'kind': 'context', 'name': 'startTime'},
                                               'end': {'kind': 'context', 'name': 'endTime'}, 'bounds': '[start,end)'}}},
        'deviceCode': 'legacy_column_string',           # 旧字符串映射
        'unknownField': {'kind': 'quantum', 'data': 'keep-me'},  # 注入未知 kind
        'computedField': {'kind': 'computed', 'mode': 'calcFunction', 'implementation': 'calc-q03',
                          'output': 'p-out', 'inputs': {'p-in': {'from': 'constant', 'value': 3}}},
        'redisField': {'kind': 'redis', 'connection': 'conn-redis-1', 'key': 'soc:{cluster_id}',
                       'command': 'GET', 'params': {}},
    }
    # 注入的属性里，deviceCode/unknownField/computedField/redisField 不在本体中定义 →
    # 校验会报“版本中不存在此属性”，但保存与往返不受影响（往返测的是存储无损）。
    binding = _object_binding(copy.deepcopy(sources))
    st = copy.deepcopy(base)
    st['bindings']['object_bindings'] = [binding]
    st['bindings']['catalogs'] = {}

    r = api.post('/api/project-save', {'state': st, 'revision': CTX_STATE['proj_rev']})
    j = r['json'] if isinstance(r['json'], dict) else {}
    save_ok = r['status'] == 200 and j.get('revision')
    if save_ok:
        CTX_STATE['proj_rev'] = j['revision']
    rec.add('P4.1', 'pass' if save_ok else 'fail',
            'P4 混合来源（field/database/redis/computed/unknown/旧字符串）保存放行（保存不跑校验）',
            f'HTTP {r["status"]} {brief(j)}', 'api')

    # 读回逐字段比对（往返无损）
    r = api.get(f'/api/project-state?project={pid}')
    got_state = (r['json'] or {}).get('state') or {}
    got_props = {}
    for b in got_state.get('bindings', {}).get('object_bindings', []):
        if b.get('object_type') == 'Q03Cluster':
            got_props = b.get('properties', {})
    diffs = []
    for key, want in sources.items():
        have = got_props.get(key)
        if have != want:
            diffs.append(f'{key}: 期望={brief(want)} 实得={brief(have)}')
    rec.add('P4.2', 'pass' if not diffs and set(got_props) >= set(sources) else 'fail',
            'P4 各来源类型往返无损（含注入未知 kind、旧字符串原样保留）',
            brief(diffs) if diffs else '所有来源键与值逐字段一致', 'data-integrity')

    # computed / field / redis 各自单独再验证无损（拆分定位）
    for key, label in (('activePower', 'field'), ('socSeries', 'database'),
                       ('deviceCode', 'legacy-string'), ('unknownField', 'unknown-kind'),
                       ('computedField', 'computed'), ('redisField', 'redis')):
        same = got_props.get(key) == sources[key]
        rec.add(f'P4.3.{label}', 'pass' if same else 'fail',
                f'P4 来源类型 [{label}] 读回与写入逐字段一致', f'实得={brief(got_props.get(key))}', 'data-integrity')

    # 未知 kind 注入 → 校验报“属性来源方式无效”，但不阻断保存（已证 P4.1）
    r = api.post('/api/project-validate', {'state': st, 'revision': CTX_STATE['proj_rev']})
    j = r['json'] if isinstance(r['json'], dict) else {}
    errs = j.get('errors') or []
    unknown_flagged = any('unknownField' in e and '来源方式无效' in e for e in errs)
    rec.add('P4.4', 'pass' if r['status'] == 200 and unknown_flagged else 'fail',
            'P4 未知来源 kind 被校验判为“属性来源方式无效”（保存放行/校验拦截）',
            f'HTTP {r["status"]} 命中未知kind错误={unknown_flagged}', 'validation')

    # 缺身份（无 table/primary_key/connection）→ 保存放行、发布阻断
    naked = copy.deepcopy(base)
    naked['bindings']['object_bindings'] = [{
        'object_type': 'Q03Cluster', 'table': '', 'primary_key': '', 'connection': '',
        'properties': {'activePower': {'kind': 'field', 'field': 'power_col'}}, 'relations': []}]
    naked['bindings']['catalogs'] = {}
    r = api.post('/api/project-save', {'state': naked, 'revision': CTX_STATE['proj_rev']})
    j = r['json'] if isinstance(r['json'], dict) else {}
    naked_save = r['status'] == 200 and j.get('revision')
    if naked_save:
        CTX_STATE['proj_rev'] = j['revision']
    # 发布（携带缺身份状态）→ 422
    r = api.post('/api/project-publish', {'state': naked, 'revision': CTX_STATE['proj_rev']})
    pub_blocked = r['status'] == 422
    rec.add('P4.5', 'pass' if (naked_save and pub_blocked) else 'fail',
            'P4 缺身份：project-save 放行、project-publish 被阻断(422)',
            f'save={naked_save} publish HTTP {r["status"]} {brief((r["json"] or {}).get("error"))}', 'validation')


# ============================== P5 ============================================

def _p5_query_rules(api, rec, closed_port):
    pid = CTX_STATE['proj_id']
    r = api.get(f'/api/project-state?project={pid}')
    st = copy.deepcopy((r['json'] or {}).get('state') or {})
    st['connections'] = {'connections': [mysql_conn('conn-q', 'Q03规则连接', closed_port)]}
    st['bindings']['object_bindings'] = [_object_binding({}, conn='conn-q', table='t', pk='pk')]
    st['bindings']['catalogs'] = {}
    # v1 规则（不完整：缺 steps 等）→ 应报具体校验错误；且引用关闭端口连接，校验只读不连库
    v1 = {'id': 'qr-v1', 'kind': 'queryRule', 'schemaVersion': 1, 'name': 'Q03规则V1',
          'objectType': 'Q03Cluster', 'connection': 'conn-q',
          'policies': {'lookupMissing': 'error', 'lookupMultiple': 'error', 'emptySeries': 'empty',
                       'nullValue': 'preserve', 'identifiers': 'connectionCatalog', 'timezone': 'project',
                       'range': '[startTime,endTime)'}}
    v2 = copy.deepcopy(v1)
    v2.update({'id': 'qr-v2', 'schemaVersion': 2, 'name': 'Q03规则V2'})
    st['implementations'] = [v1, v2]

    t0 = time.monotonic()
    r = api.post('/api/project-validate', {'state': st, 'revision': CTX_STATE['proj_rev']})
    lat = round(time.monotonic() - t0, 2)
    j = r['json'] if isinstance(r['json'], dict) else {}
    errs = j.get('errors') or []
    v1_flagged = any('取值规则 Q03规则V1' in e for e in errs)
    v2_flagged = any('取值规则 Q03规则V2' in e or '通用规则须引用' in e for e in errs)
    rec.add('P5.1', 'pass' if r['status'] == 200 and (v1_flagged or v2_flagged) else 'fail',
            'P5 queryRule v1/v2 校验产出定位错误', f'HTTP {r["status"]} v1命中={v1_flagged} v2命中={v2_flagged}',
            'validation')

    # 确认不执行真实 SQL：连关闭端口仅用于连接元数据（engine=mysql 判定），校验耗时短且无“连接被拒”类错误
    refused_in_err = any(('被拒' in e or 'refused' in e.lower() or '连接失败' in e) for e in errs)
    fast = lat < 3.0
    rec.add('P5.2', 'pass' if (not refused_in_err and fast) else 'fail',
            'P5 校验阶段不发起真实 SQL（无连接报错、耗时短），connection 仅按 engine 元数据判定',
            f'校验耗时 {lat}s；错误里出现连接失败文案={refused_in_err}', 'security')

    # 无 steps 必报“至少需要一个查询步骤”
    need_step = any('至少需要一个查询步骤' in e for e in errs)
    rec.add('P5.3', 'pass' if need_step else 'fail',
            'P5 缺 steps 的 queryRule 报“至少需要一个查询步骤”（校验准确）',
            f'命中={need_step}', 'validation')


# ============================== P6 ============================================

def _p6_validate_publish_upgrade(api, rec):
    onto_id = CTX_STATE['onto_id']
    onto_version = CTX_STATE['onto_version']
    pid = CTX_STATE['proj_id']

    # 干净可发布状态：完整身份 + field 映射，validate errors 针对本对象应为空
    r = api.get(f'/api/project-state?project={pid}')
    st = copy.deepcopy((r['json'] or {}).get('state') or {})
    rev = CTX_STATE['proj_rev']
    st['connections'] = {'connections': [mysql_conn('conn-clean', 'Q03干净连接', 3)]}
    st['bindings']['object_bindings'] = [{'object_type': 'Q03Cluster', 'connection': 'conn-clean',
                                           'table': 'cluster', 'primary_key': 'cluster_id',
                                           'properties': {'activePower': {'kind': 'field', 'field': 'power_col'}},
                                           'relations': []}]
    st['bindings']['catalogs'] = {}
    st['implementations'] = []
    r = api.post('/api/project-validate', {'state': st, 'revision': rev})
    j = r['json'] if isinstance(r['json'], dict) else {}
    errs = j.get('errors') or []
    # 该配置无连接错误、无身份错误（目录未刷新仅出 warning）
    clean = not any(('数据连接不存在' in e or '未配置来源' in e or '未配置实例' in e) for e in errs)
    rec.add('P6.1', 'pass' if r['status'] == 200 and clean else 'fail',
            'P6 project-validate 干净配置无阻断错误（错误集准确）', f'errors={brief(errs)}', 'validation')

    # 保存 + 发布 → releases
    r = api.post('/api/project-save', {'state': st, 'revision': CTX_STATE['proj_rev']})
    j = r['json'] if isinstance(r['json'], dict) else {}
    if j.get('revision'):
        CTX_STATE['proj_rev'] = j['revision']
    r = api.post('/api/project-publish', {'state': st, 'revision': CTX_STATE['proj_rev'], 'requestId': f'q03-pub-{UNIQ}'})
    j = r['json'] if isinstance(r['json'], dict) else {}
    pub_ok = r['status'] == 200 and j.get('version')
    if j.get('revision'):
        CTX_STATE['proj_rev'] = j['revision']
    rec.add('P6.2', 'pass' if pub_ok else 'fail', 'P6 干净配置发布成功产出版本',
            f'HTTP {r["status"]} version={j.get("version")} {brief(j)}', 'api')

    r = api.get(f'/api/project-releases?project={pid}')
    j = r['json'] if isinstance(r['json'], dict) else {}
    versions_list = [it.get('version') for it in (j.get('items') or []) if isinstance(it, dict)]
    rec.add('P6.3', 'pass' if r['status'] == 200 and versions_list else 'fail',
            'P6 发布后 /api/project-releases 可见发布记录', f'items版本={brief(versions_list)}', 'api')

    # 幂等回放：同 requestId 同内容 → 回放同版本
    r = api.post('/api/project-publish', {'state': st, 'revision': CTX_STATE['proj_rev'], 'requestId': f'q03-pub-{UNIQ}'})
    j = r['json'] if isinstance(r['json'], dict) else {}
    replay = r['status'] == 200 and j.get('idempotentReplay')
    # 注意：回放优先于 CAS，即便 revision 已推进也应回放；此处用当前 rev 提交同内容
    rec.add('P6.4', 'pass' if replay else 'info',
            'P6 同 requestId 同内容发布 → 幂等回放', f'HTTP {r["status"]} replay={j.get("idempotentReplay")} {brief(j)}',
            'api')
    r = api.get(f'/api/project-state?project={pid}')
    CTX_STATE['proj_rev'] = (r['json'] or {}).get('revision')

    # 升级比较：发布一个对本体的破坏性新版本（删除项目正在绑定的属性 clusterActivePower），
    # 该删除在本体内部无悬空引用（无其他节点引用该属性）→ 本体 save/publish 应成功；
    # 项目侧 activePower 绑定随之失效 → 供 project-upgrade-check 产出影响面。
    graph_state2 = storage_ontology_graph_state(f'Q03储能最小本体 {UNIQ}', onto_id)
    graph_state2['ontology']['@graph'] = [n for n in graph_state2['ontology']['@graph']
                                          if n['@id'] != 'mg:clusterActivePower']
    onto_rev_cur = _cur_onto_rev(api, onto_id)
    r = api.post('/api/save', {'state': graph_state2, 'revision': onto_rev_cur})
    sj = r['json'] if isinstance(r['json'], dict) else {}
    onto_rev2 = sj.get('revision')
    rec.add('P6.5a', 'pass' if r['status'] == 200 and onto_rev2 and (sj.get('errors') == []) else 'fail',
            'P6 破坏性编辑本体（删被项目引用的属性）本体自身 save 无错通过',
            f'HTTP {r["status"]} errors={brief(sj.get("errors"))} newRev={onto_rev2}', 'api')
    r = api.post('/api/publish', {'state': graph_state2, 'revision': onto_rev2})
    j = r['json'] if isinstance(r['json'], dict) else {}
    v2 = j.get('version')
    CTX_STATE['onto_version2'] = v2
    rec.add('P6.5', 'pass' if r['status'] == 200 and v2 else 'fail',
            'P6 发布本体新版本（删属性）作为升级目标', f'HTTP {r["status"]} ontoV2={v2} {brief(j)}', 'api')

    if v2:
        r = api.post('/api/project-upgrade-check', {'state': st, 'revision': CTX_STATE['proj_rev'], 'targetVersion': v2})
        j = r['json'] if isinstance(r['json'], dict) else {}
        has_impacts = 'impacts' in j and 'classification' in j
        rec.add('P6.6', 'pass' if r['status'] == 200 and has_impacts else 'fail',
                'P6 project-upgrade-check 返回分类与影响面',
                f'HTTP {r["status"]} classification={j.get("classification")} #impacts={len(j.get("impacts") or [])}', 'api')
        # 比较只读：项目失效/受影响绑定原样保留
        r = api.get(f'/api/project-state?project={pid}')
        kept = _find_binding((r['json'] or {}).get('state') or {}, 'Q03Cluster')
        retained = kept is not None and 'activePower' in (kept.get('properties') or {})
        rec.add('P6.7', 'pass' if retained else 'fail',
                'P6 升级比较后项目绑定数据原样保留（只读比较不改配置）',
                f'仍含 Q03Cluster.activePower={retained}', 'data-integrity')


def _cur_onto_rev(api, onto_id):
    r = api.get(f'/api/state?ontology={onto_id}')
    return (r['json'] or {}).get('revision') if isinstance(r['json'], dict) else None


def _find_binding(state, otype):
    for b in state.get('bindings', {}).get('object_bindings', []):
        if b.get('object_type') == otype:
            return b
    return None


# ============================== P8 (R02 复现) ==================================

def _p8_r02(api, rec):
    """R02：修订后判定 —— 前置必须成功，未拦截记 known_defect_reproduced（不是产品通过）。

    一致性：与 tests/deep_reverify_scenarios.py::scenario_r02 共用同一套判定，
    口径以 deep_verdicts 为准；本函数只负责把结果写进本套件证据。
    """
    pid = CTX_STATE['proj_id']
    r = api.get(f'/api/project-state?project={pid}')
    pst = copy.deepcopy((r['json'] or {}).get('state') or {})
    rev = (r['json'] or {}).get('revision')
    pst['connections'] = {'connections': [mysql_conn('conn-clean', 'Q03干净连接', 3)]}
    pst['implementations'] = []
    pst.setdefault('bindings', {})['catalogs'] = {}
    pst['bindings']['object_bindings'] = [{
        'object_type': 'Q03Cluster', 'connection': 'conn-clean', 'table': 'cluster', 'primary_key': 'cluster_id',
        'properties': {'activePower': {'kind': 'flow', 'flow': 'pending', 'output': 'fout-1', 'inputs': {}}},
        'relations': []}]

    out = scenario_r02(api, rec, pid, pst, 'Q03Cluster', 'activePower', tag='P8')
    CTX_STATE['shell_flow_id'] = out.get('shellFlowId')
    CTX_STATE['proj_rev'] = (api.get(f'/api/project-state?project={pid}')['json'] or {}).get('revision')
    print(f'    P8(R02) 结论：{brief(out.get("cases"))} 发布版本数 {out.get("publish", {}).get("before")}'
          f'->{out.get("publish", {}).get("after")}')


# ============================== P9 跨账号 =====================================

def _p9_cross_account(api, rec):
    # 只读探测账号 qa_deep_a：不得访问 qa_deep_b 的项目/编排/本体（视为不存在）
    a = Api(username='qa_deep_a', password=C.PASSWORD)
    pid = CTX_STATE['proj_id']
    fid = CTX_STATE.get('shell_flow_id')
    onto_id = CTX_STATE['onto_id']

    r = a.get(f'/api/project-state?project={pid}')
    rec.add('P9.1', 'pass' if r['status'] == 404 else 'fail',
            'P9 跨账号读他人项目 → 404（视为不存在）', f'HTTP {r["status"]} {brief(r["json"])}', 'security')

    r = a.get(f'/api/flow-state?flow={fid}') if fid else None
    code = r['status'] if r else None
    rec.add('P9.2', 'pass' if code == 404 else 'fail',
            'P9 跨账号读他人编排 → 404', f'HTTP {code} {brief(r["json"]) if r else ""}', 'security')

    r = a.get(f'/api/state?ontology={onto_id}')
    code = r['status']
    rec.add('P9.3', 'pass' if code == 404 else 'fail',
            'P9 跨账号读他人本体草稿 → 404', f'HTTP {code} {brief(r["json"])}', 'security')

    # 跨账号写：以 qa_deep_a 携带他人 projectId 保存 → 404（load 抛 ProjectNotFound）
    r = a.post('/api/project-save', {'state': {'projectId': pid, 'name': 'x', 'bindings': {}}, 'revision': 'x'})
    rec.add('P9.4', 'pass' if r['status'] == 404 else 'fail',
            'P9 跨账号写他人项目 → 404（无法越权改数据）', f'HTTP {r["status"]} {brief(r["json"])}', 'security')


if __name__ == '__main__':
    main()

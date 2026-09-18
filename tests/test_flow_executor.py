"""flow_executor 回归：拓扑序 / 链校验 / 输入取值 / 失败传播 / 五类执行器（全部 mock 驱动）。

纯 python3 标准库直跑；pymysql/redis 以桩模块注入 sys.modules，LLM 与 HTTP 用
monkeypatch，绝不连接任何真实服务。WIZ_WORKBENCH_ROOT 挂临时数据根。
运行：python3 tests/test_flow_executor.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix='wiz_fexec_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

from workbench import flow_executor, flow_routes, flows, llm_client, llm_providers  # noqa: E402

PASSED = []


def check(cond, message, actual=None):
    if cond:
        PASSED.append(message)
        print(f'通过) {message}')
        return
    print(f'[失败] {message}')
    if actual is not None:
        print('  实际:', json.dumps(actual, ensure_ascii=False, default=str)[:2000])
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)


CTX = {'project_id': '', 'connections': {}, 'credential_ids': None}


def node(kind, name, inputs=(), outputs=(), impl=None, extra=None):
    row = {'id': f'nd_{name}', 'kind': kind, 'name': name,
           'inputs': [dict(i, id='in_' + i['name']) for i in inputs],
           'outputs': [dict(o, id='out_' + o['name']) for o in outputs],
           'implementation': impl or {}}
    if extra:
        row.update(extra)
    return row


def fixed(value_type, value):
    return {'kind': 'fixed', 'valueType': value_type, 'value': value}


# 1) 拓扑序与链校验 ---------------------------------------------------------------
state = {'schemaVersion': 1, 'flowId': 'x1', 'name': 't', 'inputs': [], 'outputs': [], 'connections': [],
         'nodes': [
             node('calc', '甲', inputs=[{'name': 'a', 'type': {'type': 'number'}, 'source': fixed('number', 6)}],
                  outputs=[{'name': 'v', 'type': {'type': 'number'}}],
                  impl={'mode': 'formula', 'formulas': {'v': '{a} * 7'}}),
             node('calc', '乙', inputs=[{'name': 'v', 'type': {'type': 'number'}, 'source': {'kind': 'node', 'nodeId': 'nd_甲', 'outputId': 'out_v'}}],
                  outputs=[{'name': 'w', 'type': {'type': 'number'}}],
                  impl={'mode': 'formula', 'formulas': {'w': '{v} + 1'}}),
         ]}
order = [n['name'] for n in flow_executor.topo_order(state)]
check(order == ['甲', '乙'], '拓扑序按依赖排列', order)
flow_executor.validate_chain(state, ['nd_甲', 'nd_乙'])
flow_executor.validate_chain(state, ['nd_乙'])  # 单节点测试放行：上游值由表单提供
result = flow_executor.run(state, ctx=CTX)
check([r['status'] for r in result['nodeResults']] == ['success', 'success'], '全图执行成功', result)
check(result['nodeResults'][1]['outputs'] == {'w': 43}, '6*7+1=43 逐节点传值', result['nodeResults'][1])
state['nodes'].append(node('calc', '丙', inputs=[{'name': 'w', 'type': {'type': 'number'}, 'source': {'kind': 'node', 'nodeId': 'nd_乙', 'outputId': 'out_w'}}],
                           outputs=[{'name': 'z', 'type': {'type': 'number'}}],
                           impl={'mode': 'formula', 'formulas': {'z': '{w} + 1'}}))
try:
    flow_executor.validate_chain(state, ['nd_甲', 'nd_丙'])
    check(False, '多节点断链应被拦截')
except ValueError:
    check(True, '多节点断链被拦截并给出缺失上游')


# 2) 失败传播：上游失败 → 下游 skipped ------------------------------------------------
bad = state['nodes'][0]
bad['implementation']['formulas']['v'] = '{missing_param} * 2'
result = flow_executor.run(state)
check(result['nodeResults'][0]['status'] == 'failed' and result['nodeResults'][1]['status'] == 'skipped',
      '失败传播：下游标记跳过', result['nodeResults'])
bad['implementation']['formulas']['v'] = '{a} * 7'

# 3) 输入取值：nodeField / flowInput 缺值 ------------------------------------------------
state['nodes'][1]['inputs'][0] = {'id': 'in_v', 'name': 'v', 'type': {'type': 'number'},
                                  'source': {'kind': 'flowInput', 'inputId': 'fin_1'}}
result = flow_executor.run(state, targets=['nd_乙'])
check(result['nodeResults'][0]['status'] == 'failed' and '入口参数取值' in result['nodeResults'][0]['error'],
      '链测试缺入口取值给可读错误', result['nodeResults'][0])
result = flow_executor.run(state, targets=['nd_乙'], inputs={'fin_1': 10})
check(result['nodeResults'][0]['status'] == 'success' and result['nodeResults'][0]['outputs'] == {'w': 11},
      '入口取值按 inputId 提供后成功', result['nodeResults'][0])

# 4) Python 节点：LLM 代执行契约（mock evaluate_json） ---------------------------------
py_node = node('python', '清洗', inputs=[{'name': 'raw', 'type': {'type': 'text'}, 'source': fixed('text', 'x')}],
               outputs=[{'name': 'len', 'type': {'type': 'number'}}, {'name': 'tag', 'type': {'type': 'text'}}],
               impl={'language': 'python', 'code': 'def main(raw):\n    return {"len": 3, "tag": "z", "extra": 1}\n',
                     'providerId': ''})
state['nodes'].append(py_node)
real_eval = llm_client.evaluate_json
real_resolve = llm_providers.resolve
try:
    llm_providers.resolve = lambda pid='': {'name': 'P', 'endpoint': 'https://x', 'model': 'm',
                                            'api_key': 'k', 'temperature': 0}
    llm_client.evaluate_json = lambda payload, provider, timeout=None, **kw: {
        'ok': True, 'result': {'len': 3, 'tag': 'z', 'extra': 1},
        'trace': {'provider': 'P', 'model': 'm', 'durationMs': 5, 'request': 'q', 'response': 'r'}}
    result = flow_executor.run(state, targets=['nd_清洗'])
    entry = result['nodeResults'][0]
    check(entry['status'] == 'success' and entry['outputs'] == {'len': 3, 'tag': 'z'},
          'LLM 代执行按输出声明承接（多余键丢弃）', entry)
    check(any('未声明的键' in log for log in entry['logs']), '多余键有提示日志', entry['logs'])
    check(any('非确定性' in log for log in entry['logs']), 'LLM 非确定性提示在日志')

    llm_client.evaluate_json = lambda payload, provider, timeout=None, **kw: {
        'ok': True, 'result': {'len': 3}, 'trace': {'provider': 'P', 'model': 'm', 'durationMs': 5}}
    result = flow_executor.run(state, targets=['nd_清洗'])
    check(result['nodeResults'][0]['status'] == 'failed' and 'tag' in result['nodeResults'][0]['error'],
          '缺少声明输出判失败', result['nodeResults'][0])
finally:
    llm_client.evaluate_json = real_eval
    llm_providers.resolve = real_resolve

# 5) SQL 节点：桩 pymysql（只读会话 / 参数 / 行数上限 / DML 勾选） -------------------------
captured = {}


class _FakeCursor:
    def __init__(self):
        self.description = None
        self.rowcount = -1

    def execute(self, sql, args=None):
        captured.setdefault('statements', []).append((sql, args))
        if sql.upper().startswith('SELECT'):
            self.description = [('id',), ('name',)]
            self._rows = [(1, '甲'), (2, '乙'), (3, '丙')]
        else:
            self.description = None
            self.rowcount = 3

    def fetchmany(self, size):
        return self._rows[:size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    committed = 0

    def cursor(self):
        return _FakeCursor()

    def commit(self):
        _FakeConn.committed += 1

    def close(self):
        pass


fake_pymysql = type(sys)('pymysql')
fake_pymysql.connect = lambda **kw: captured.update(conn=kw) or _FakeConn()
fake_pymysql.cursors = type(sys)('pymysql.cursors')
sys.modules['pymysql'] = fake_pymysql
sys.modules['pymysql.cursors'] = fake_pymysql.cursors

sql_node = node('sql', '查询', inputs=[{'name': 'status', 'type': {'type': 'text'}, 'source': fixed('text', 'ok')},
                                      {'name': 'lim', 'type': {'type': 'number'}, 'source': fixed('number', 5)}],
                outputs=[{'name': 'rows', 'type': {'type': 'list', 'elementType': {'type': 'object'}}}],
                impl={'language': 'sql', 'connectionId': 'c1',
                      'sql': 'SELECT id, name FROM t WHERE s = #{status} LIMIT #{lim}'},
                extra={'execution': {'maxRows': 2}})
ctx = {'project_id': 'p1', 'connections': {'c1': {'id': 'c1', 'engine': 'mysql', 'name': '库', 'host': 'db.local',
                                                  'port': 3306, 'username': 'u', 'database': 'app'}}, 'credential_ids': set()}
os.environ['WIZ_SECRET_FOR_TEST'] = ''
from workbench import secrets as secrets_store  # noqa: E402
secrets_store.save('p1', 'c1', 'pass-123')

result = flow_executor.run({'flowId': 'x', 'name': 'f', 'nodes': [sql_node], 'inputs': [], 'outputs': [],
                            'connections': [], 'schemaVersion': 1}, targets=['nd_查询'], ctx=ctx)
entry = result['nodeResults'][0]
stmts = captured['statements']
check(entry['status'] == 'success' and len(entry['outputs']['rows']) == 2, '行数上限生效', entry)
check(stmts[0] == ('SET SESSION TRANSACTION READ ONLY', None), '默认只读会话', stmts[0])
check(stmts[1] == ('SELECT id, name FROM t WHERE s = %s LIMIT %s', ['ok', 5]), '预编译参数顺序正确', stmts[1])
check(entry['outputs']['rows'][0] == {'id': 1, 'name': '甲'}, '行映射为对象（列名=值）', entry['outputs']['rows'][0])

sql_node['execution'] = {'allowWrite': True, 'maxRows': 1000}
sql_node['outputs'] = [{'id': 'out_affected', 'name': 'affected', 'type': {'type': 'number'}}]
sql_node['implementation']['sql'] = 'UPDATE t SET s = #{status} WHERE id = 1'
result = flow_executor.run({'flowId': 'x', 'name': 'f', 'nodes': [sql_node], 'inputs': [], 'outputs': [],
                            'connections': [], 'schemaVersion': 1}, targets=['nd_查询'], ctx=ctx)
entry = result['nodeResults'][0]
check(entry['status'] == 'success' and entry['outputs']['affected'] == 3, 'DML 影响行数', entry)
check(all(sql.upper() != 'SET SESSION TRANSACTION READ ONLY' for sql, _ in captured['statements'][-2:]),
      '允许写时关闭只读会话')
check(_FakeConn.committed >= 1, 'DML 提交事务')


# 4.5) nodeField 绑定：字段稳定 ID → 按声明技术名映射到输出值（SQL 列名场景） ------------
sql_src = node('sql', '取容量', inputs=[{'name': 'device_id', 'type': {'type': 'text'}, 'source': fixed('text', 'd1')}],
               outputs=[{'id': 'out_info', 'name': 'info',
                         'type': {'type': 'object', 'fields': [{'id': 'fld-uuid-id', 'name': 'id', 'label': '标识', 'type': {'type': 'number'}}]}}],
               impl={'language': 'sql', 'connectionId': 'c1', 'sql': 'SELECT id FROM m WHERE d = #{device_id}'})
calc_user = node('calc', '用容量', inputs=[{'name': 'cap', 'type': {'type': 'number'},
                                           'source': {'kind': 'nodeField', 'nodeId': 'nd_取容量', 'outputId': 'out_info',
                                                      'fieldPath': ['fld-uuid-id']}}],
                 outputs=[{'name': 'double', 'type': {'type': 'number'}}],
                 impl={'mode': 'formula', 'formulas': {'double': '{cap} * 2'}})
result = flow_executor.run({'flowId': 'x', 'name': 'f', 'nodes': [sql_src, calc_user], 'inputs': [], 'outputs': [],
                            'connections': [], 'schemaVersion': 1}, ctx=ctx)
statuses = [r['status'] for r in result['nodeResults']]
values = {r['nodeId']: r.get('outputs') for r in result['nodeResults']}
check(statuses == ['success', 'success'] and values['nd_用容量'] == {'double': 2},
      'nodeField 按声明映射列名取值（id=1 × 2）', result['nodeResults'])
calc_user['inputs'][0]['source']['fieldPath'] = ['fld-uuid-missing']
result = flow_executor.run({'flowId': 'x', 'name': 'f', 'nodes': [sql_src, calc_user], 'inputs': [], 'outputs': [],
                            'connections': [], 'schemaVersion': 1}, ctx=ctx)
check(result['nodeResults'][1]['status'] == 'failed' and '字段路径在输出声明中不存在' in result['nodeResults'][1].get('error', ''),
      '声明中不存在的字段路径给可读错误', result['nodeResults'][1])
calc_user['inputs'][0]['source']['fieldPath'] = ['fld-uuid-id']
sql_src['outputs'][0]['type']['fields'][0]['name'] = 'capacity'
result = flow_executor.run({'flowId': 'x', 'name': 'f', 'nodes': [sql_src, calc_user], 'inputs': [], 'outputs': [],
                            'connections': [], 'schemaVersion': 1}, ctx=ctx)
check(result['nodeResults'][1]['status'] == 'failed' and '不存在于上游输出值' in result['nodeResults'][1].get('error', ''),
      '值里缺少声明字段（列名不匹配）给可读错误', result['nodeResults'][1])

# 5) SQL 节点：桩 pymysql（只读会话 / 参数 / 行数上限 / DML 勾选） -------------------------
captured = {}


class _FakeCursor:
    def __init__(self):
        self.description = None
        self.rowcount = -1

    def execute(self, sql, args=None):
        captured.setdefault('statements', []).append((sql, args))
        if sql.upper().startswith('SELECT'):
            self.description = [('id',), ('name',)]
            self._rows = [(1, '甲'), (2, '乙'), (3, '丙')]
        else:
            self.description = None
            self.rowcount = 3

    def fetchmany(self, size):
        return self._rows[:size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    committed = 0

    def cursor(self):
        return _FakeCursor()

    def commit(self):
        _FakeConn.committed += 1

    def close(self):
        pass


# 6) Redis 节点：桩 redis（单键 / 行模式 / 返回映射） --------------------------------------
class _FakeRedis:
    calls = []

    def get(self, key):
        _FakeRedis.calls.append(('get', key))
        return b'cache-hit'

    def setex(self, key, seconds, value):
        _FakeRedis.calls.append(('setex', key, seconds, value))
        return True

    def incr(self, key):
        _FakeRedis.calls.append(('incr', key))
        return 7


fake_redis = type(sys)('redis')
fake_redis.Redis = lambda **kw: _FakeRedis()
sys.modules['redis'] = fake_redis

redis_node = node('redis', '读缓存', inputs=[{'name': 'k', 'type': {'type': 'text'}, 'source': fixed('text', 'a-1')}],
                  outputs=[{'name': 'flag', 'type': {'type': 'text'}}],
                  impl={'language': 'redis', 'connectionId': 'r1', 'command': 'GET', 'keyTemplate': 'cache:${k}', 'args': []})
ctx_r = {'project_id': 'p1', 'connections': {'r1': {'id': 'r1', 'engine': 'redis', 'name': '缓存', 'host': 'r.local',
                                                    'port': 6379, 'dbIndex': 0}}, 'credential_ids': set()}
result = flow_executor.run({'flowId': 'x', 'name': 'f', 'nodes': [redis_node], 'inputs': [], 'outputs': [],
                            'connections': [], 'schemaVersion': 1}, targets=['nd_读缓存'], ctx=ctx_r)
entry = result['nodeResults'][0]
check(entry['status'] == 'success' and entry['outputs']['flag'] == 'cache-hit', 'GET 返回 bulk → 文本', entry)
check(_FakeRedis.calls[-1] == ('get', 'cache:a-1'), 'key 模板 ${参数} 替换', _FakeRedis.calls[-1])

redis_row = node('redis', '写缓存', inputs=[{'name': 'items', 'type': {'type': 'list'}, 'source': fixed('text', 'x')},
                                           {'name': 'ttl', 'type': {'type': 'number'}, 'source': fixed('number', 60)}],
                 outputs=[{'name': 'oks', 'type': {'type': 'list'}}],
                 impl={'language': 'redis', 'connectionId': 'r1', 'command': 'SETEX', 'keyTemplate': 'row:{{name}}',
                       'args': ['ttl', '900'], 'rowMode': True})
# fixed 来源造不出真实列表：直接走 run 的 inputs 传参（绑定 flowInput 的等价路径）
redis_row['inputs'][0] = {'id': 'in_items', 'name': 'items', 'type': {'type': 'list'},
                          'source': {'kind': 'flowInput', 'inputId': 'fin_items'}}
result = flow_executor.run({'flowId': 'x', 'name': 'f', 'nodes': [redis_row], 'inputs': [], 'outputs': [],
                            'connections': [], 'schemaVersion': 1},
                           targets=['nd_写缓存'], inputs={'fin_items': [{'name': 'r1'}, {'name': 'r2'}]}, ctx=ctx_r)
entry = result['nodeResults'][0]
check(entry['status'] == 'success' and entry['outputs']['oks'] == [True, True], '行模式逐行 SETEX', entry)
check(_FakeRedis.calls[-2:] == [('setex', 'row:r1', 60, 900), ('setex', 'row:r2', 60, 900)],
      '行模式 key 取元素字段、参数取输入与字面量', _FakeRedis.calls[-2:])

# 7) HTTP 节点：桩 urlopen（2xx JSON / responsePath / 非 2xx） ----------------------------
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

http_node = node('http', '推送', inputs=[{'name': 'score', 'type': {'type': 'number'}, 'source': fixed('number', 16)}],
                 outputs=[{'name': 'reply', 'type': {'type': 'object'}}],
                 impl={'language': 'http', 'method': 'POST', 'url': 'https://hooks.local/score/{score}',
                       'headers': {'X-A': 'b'}, 'bodyMode': 'json', 'body': '{"s": {score}}',
                       'credentialId': '', 'responsePath': 'data'})
real_urlopen = urllib.request.urlopen
captured_http = {}


class _HttpResp:
    status = 200

    def __init__(self, body):
        self._body = body.encode()

    def read(self, limit=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(request, timeout=None):
    url = request.full_url
    captured_http['url'] = url
    captured_http['body'] = (request.data or b'').decode()
    captured_http['headers'] = dict(request.header_items())
    if 'bad' in url:
        raise urllib.error.HTTPError(url, 502, 'Bad Gateway', None, None)
    if 'text' in url:
        return _HttpResp('not-json')
    return _HttpResp('{"code": 0, "data": {"accepted": true}}')


try:
    urllib.request.urlopen = _fake_urlopen
    result = flow_executor.run({'flowId': 'x', 'name': 'f', 'nodes': [http_node], 'inputs': [], 'outputs': [],
                                'connections': [], 'schemaVersion': 1}, targets=['nd_推送'])
    entry = result['nodeResults'][0]
    check(entry['status'] == 'success' and entry['outputs']['reply'] == {'accepted': True},
          'HTTP 响应按 responsePath 提取', entry)
    check(captured_http['url'] == 'https://hooks.local/score/16' and captured_http['body'] == '{"s": 16}',
          'URL 编码占位与 body 数值替换', captured_http)
    http_node['implementation']['url'] = 'https://hooks.local/bad'
    result = flow_executor.run({'flowId': 'x', 'name': 'f', 'nodes': [http_node], 'inputs': [], 'outputs': [],
                                'connections': [], 'schemaVersion': 1}, targets=['nd_推送'])
    check(result['nodeResults'][0]['status'] == 'failed' and '502' in result['nodeResults'][0]['error'],
          '非 2xx 给可读失败', result['nodeResults'][0])
    captured_http['url'] = 'https://hooks.local/text'
    http_node['implementation']['url'] = 'https://hooks.local/text/{score}'
    result = flow_executor.run({'flowId': 'x', 'name': 'f', 'nodes': [http_node], 'inputs': [], 'outputs': [],
                                'connections': [], 'schemaVersion': 1}, targets=['nd_推送'])
    check(result['nodeResults'][0]['status'] == 'failed' and 'JSON' in result['nodeResults'][0]['error'],
          '非 JSON 响应给可读失败', result['nodeResults'][0])
finally:
    urllib.request.urlopen = real_urlopen

# 8) 路由 /api/flow-run：测试形态 + 全图 revision gate --------------------------------------
created = flows.create('路由冒烟')
state = flows.read_draft(created['id'])
state.pop('_draft')
state['nodes'].append(node('calc', '路由', inputs=[{'name': 'a', 'type': {'type': 'number'}, 'source': fixed('number', 1)}],
                           outputs=[{'name': 'v', 'type': {'type': 'number'}}],
                           impl={'mode': 'formula', 'formulas': {'v': '{a} + 1'}}))
flows.save_draft(state)
revision = flows.current_token(created['id'])
result = flow_routes.post_flow_run({'state': state, 'targets': ['nd_路由'], 'inputs': {}})
check(result[1] == 200 and result[0]['status'] == 'success', '测试形态通过路由执行', result)
result = flow_routes.post_flow_run({'state': state, 'inputs': {}, 'revision': 'stale'})
check(result[1] == 409 and 'currentRevision' in result[0], '全图运行 revision gate（409）', result)
result = flow_routes.post_flow_run({'state': state, 'inputs': {}, 'revision': revision, 'projectId': ''})
check(result[1] == 200 and result[0]['status'] == 'success', '全图运行成功（无项目上下文）', result)
broken = json.loads(json.dumps(state))
broken['nodes'][0]['implementation']['formulas'] = {'v': '{nope}'}
result = flow_routes.post_flow_run({'state': broken, 'inputs': {}, 'revision': revision})
check(result[1] == 422 and '配置检查未通过' in result[0]['error'], '全图运行 errors gate（422）', result)

print(f'\n全部通过：{len(PASSED)} 项')
shutil.rmtree(TMP, ignore_errors=True)

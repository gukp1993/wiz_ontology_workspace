"""函数编排执行器：五类节点（SQL/计算/Python/Redis/HTTP）的拓扑序执行。

硬边界（见 文档/需求/20260918_函数编排_节点函数化与执行/）：
* 本机不执行任何用户代码：Python 节点由 LLM 代执行（llm_client，本机仅 ast 静态
  解析）；计算节点公式模式走 calc_functions 受限表达式引擎（禁 eval）。
* 真实外呼（SQL/Redis/HTTP）仅由用户显式触发；连接配置与密钥全部服务端读取
  （项目数据连接 + secrets vault / api_credentials），密钥不进日志与响应。
* 不持 workbench.locking.LOCK（长网络操作）；同编排全图运行在进程内互斥。
* 无状态：结果仅随响应返回，不落盘、不写修订流。
"""
import ast
import json
import re
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal

from workbench import api_credentials, calc_functions, dbdrivers, flow_http, flow_sql, flows
from workbench import llm_client, llm_providers, projects, secrets as secrets_store

MUTEXES = {}
_MUTEX_GUARD = threading.Lock()
_KEY_TEMPLATE_ROW = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}')
_KEY_TEMPLATE_PARAM = re.compile(r'\$\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}')
_DEFAULT_TIMEOUT_MS = {'python': 60000, 'sql': 30000, 'redis': 30000, 'http': 15000, 'calc': 30000}


class NodeFailure(Exception):
    def __init__(self, message, logs=None):
        super().__init__(message)
        self.logs = logs or []


def flow_mutex(flow_id):
    with _MUTEX_GUARD:
        return MUTEXES.setdefault(flow_id, threading.Lock())


# ── 值序列化（Decimal/datetime/bytes → JSON 可表达） ──────────────────────────
def jsonify(value, depth=0):
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=' ')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode('utf-8', 'replace')
    if isinstance(value, dict):
        return {str(k): jsonify(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonify(v, depth + 1) for v in value]
    return str(value)


def _preview(text, limit=600):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit] + '…（截断）'


# ── 依赖图与顺序 ─────────────────────────────────────────────────────────────
def _node_dependencies(node):
    deps = []
    for inp in node.get('inputs', []) or []:
        src = inp.get('source') if isinstance(inp, dict) else None
        if isinstance(src, dict) and src.get('kind') in ('node', 'nodeField') and src.get('nodeId'):
            deps.append(src['nodeId'])
    return deps


def topo_order(state, targets=None):
    """Kahn 拓扑序；targets 给定时只保留被测节点（仍按全局依赖排序）。
    检测不到的依赖（引用已删节点）忽略——配置检查负责报错。"""
    nodes = [n for n in state.get('nodes', []) if isinstance(n, dict) and n.get('id')]
    index = {n['id']: n for n in nodes}
    keep = {n['id'] for n in nodes} if targets is None else {t for t in targets if t in index}
    deps = {nid: [d for d in _node_dependencies(node) if d in keep] for nid, node in index.items() if nid in keep}
    order, ready = [], sorted(nid for nid in keep if not deps[nid])
    pending = {nid: set(deps[nid]) for nid in keep}
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for other, waiting in pending.items():
            if nid in waiting:
                waiting.discard(nid)
                if not waiting and other not in order and other not in ready:
                    ready.append(other)
                    ready.sort()
    if len(order) != len(keep):  # 环（配置检查已拦截，这里兜底）
        raise ValueError('编排存在循环依赖，无法执行；请先通过配置检查')
    return [index[nid] for nid in order]


def validate_chain(state, target_ids):
    """链测试校验：被测集合内每个节点的节点依赖都在被测集合内（存在拓扑序）。"""
    index = {n['id']: n for n in state.get('nodes', []) if isinstance(n, dict)}
    missing = []
    for nid in target_ids:
        node = index.get(nid)
        if node is None:
            raise ValueError(f'被测节点 {nid} 不存在于当前编排')
        for dep in _node_dependencies(node):
            if dep not in target_ids:
                missing.append(f'「{node.get("name") or nid}」依赖未选中的「{index[dep].get("name") or dep}」')
    if missing:
        raise ValueError('选中节点不构成连续链：' + '；'.join(dict.fromkeys(missing)) +
                         '。请按执行顺序把依赖节点一并选中。')


# ── 输入取值 ─────────────────────────────────────────────────────────────────
def _resolve_inputs(node, state, results, inputs):
    """解析节点全部输入值（技术名 → 值）。缺来源/缺值抛 NodeFailure。"""
    flow_inputs = {i.get('id'): i for i in state.get('inputs', []) if isinstance(i, dict)}
    values, logs = {}, []
    for inp in node.get('inputs', []) or []:
        if not isinstance(inp, dict) or not inp.get('name'):
            continue
        name, label = inp['name'], str(inp.get('label') or inp.get('name') or inp['name'])
        src = inp.get('source')
        if src is None:
            raise NodeFailure(f'输入「{label}」尚未绑定来源')
        if not isinstance(src, dict):
            raise NodeFailure(f'输入「{label}」来源声明无效')
        kind = src.get('kind')
        if kind == 'fixed':
            values[name] = src.get('value')
            continue
        if kind == 'flowInput':
            entry = flow_inputs.get(src.get('inputId')) or {}
            for key in (src.get('inputId'), entry.get('name'), f'{node["id"]}.{name}'):
                if key is not None and key in inputs:
                    values[name] = inputs[key]
                    break
            else:
                raise NodeFailure(f'输入「{label}」缺少入口参数取值（请在运行/测试表单中提供）')
            continue
        if kind in ('node', 'nodeField'):
            upstream = results.get(src.get('nodeId'))
            if not upstream or upstream.get('status') != 'success':
                raise NodeFailure(f'输入「{label}」的上游节点尚未成功执行')
            outputs = upstream.get('outputs', {})
            output_name = upstream.get('outputNames', {}).get(src.get('outputId'))
            if output_name is None or output_name not in outputs:
                raise NodeFailure(f'输入「{label}」引用了上游不存在的输出')
            value = outputs[output_name]
            if kind == 'nodeField':
                for fid in src.get('fieldPath') or []:
                    if isinstance(value, dict) and fid in value:
                        value = value[fid]
                    else:
                        raise NodeFailure(f'输入「{label}」引用的对象字段不存在于上游输出')
            values[name] = value
            continue
        raise NodeFailure(f'输入「{label}」来源类型未知')
    return values, logs


def _node_timeout(node):
    execution = node.get('execution') if isinstance(node.get('execution'), dict) else {}
    timeout = execution.get('timeoutMs')
    if isinstance(timeout, int) and not isinstance(timeout, bool) and 1000 <= timeout <= flows.EXEC_MAX_TIMEOUT_MS:
        return timeout
    return _DEFAULT_TIMEOUT_MS.get(node.get('kind'), 30000)


def _output_slots(node):
    """输出名 → 声明映射；返回 (names_by_id, decls_by_name)。"""
    names_by_id, decls_by_name = {}, {}
    for out in node.get('outputs', []) or []:
        if isinstance(out, dict) and out.get('id') and out.get('name'):
            names_by_id[out['id']] = out['name']
            decls_by_name[out['name']] = out
    return names_by_id, decls_by_name


def _single_output(value, node):
    """单输出节点（sql/redis/http）的执行结果包装成 输出名 → 值。"""
    _names, decls = _output_slots(node)
    name = next(iter(decls), 'result')
    return {name: value}


def _contract_dict(result, node, logs):
    """dict 返回值按声明输出承接：缺 → 失败；多 → 丢弃并提示。"""
    names_by_id, decls_by_name = _output_slots(node)
    if result is None:
        return {name: None for name in decls_by_name}
    if not isinstance(result, dict):
        raise NodeFailure('返回值不是对象（dict），无法承接声明的输出')
    values = {}
    for name in decls_by_name:
        if name not in result:
            raise NodeFailure(f'返回值缺少声明的输出「{name}」')
        values[name] = jsonify(result[name])
    for key in result:
        if key not in decls_by_name:
            logs.append(f'返回值包含未声明的键「{key}」，已丢弃（输出声明即契约）')
    return values


# ── 五类执行器 ───────────────────────────────────────────────────────────────
def _exec_python(node, values, ctx):
    impl = node.get('implementation') or {}
    code = impl.get('code')
    logs = []
    try:
        ast.parse(str(code or ''))
    except SyntaxError as exc:
        raise NodeFailure(f'Python 代码语法无法解析（第 {exc.lineno} 行）', logs)
    try:
        provider = llm_providers.resolve(str(impl.get('providerId') or ''))
    except ValueError as exc:
        raise NodeFailure(str(exc), logs)
    started = time.monotonic()
    verdict = llm_client.evaluate_json({'code': code, 'inputs': values}, provider,
                                       timeout=max(1, _node_timeout(node) // 1000))
    trace = verdict.get('trace') or {}
    if trace:
        logs.append(f"[LLM] provider={trace.get('provider')} model={trace.get('model')} 耗时 {trace.get('durationMs')}ms"
                    f"（总耗时 {int((time.monotonic() - started) * 1000)}ms）")
        logs.append('[LLM 请求摘要] ' + _preview(str(trace.get('request'))))
        logs.append('[LLM 响应摘要] ' + _preview(str(trace.get('response'))))
    if not verdict.get('ok'):
        logs.append(f'[LLM] 调用失败：{verdict.get("error")}')
        raise NodeFailure(verdict.get('error') or 'LLM 代执行失败', logs)
    logs.append('注意：LLM 求值具有非确定性，请结合业务判断结果。')
    return _contract_dict(jsonify(verdict['result']), node, logs), logs


def _exec_calc(node, values, ctx):
    impl = node.get('implementation') or {}
    logs = []
    if (impl.get('mode') or 'formula') == 'llm':
        try:
            provider = llm_providers.resolve(str(impl.get('providerId') or ''))
        except ValueError as exc:
            raise NodeFailure(str(exc), logs)
        verdict = llm_client.evaluate_json({'instruction': impl.get('llmInstruction'), 'inputs': values},
                                           provider, timeout=max(1, _node_timeout(node) // 1000))
        trace = verdict.get('trace') or {}
        if trace:
            logs.append(f"[LLM] provider={trace.get('provider')} model={trace.get('model')} 耗时 {trace.get('durationMs')}ms")
        if not verdict.get('ok'):
            logs.append(f'[LLM] 调用失败：{verdict.get("error")}')
            raise NodeFailure(verdict.get('error') or 'LLM 计算失败', logs)
        return _contract_dict(jsonify(verdict['result']), node, logs), logs
    formulas = impl.get('formulas') or {}
    param_types = {}
    values_out = {}
    for inp in node.get('inputs', []) or []:
        if not isinstance(inp, dict) or not inp.get('name'):
            continue
        calc_type = flows.CALC_TYPE_OF_FLOW.get((inp.get('type') or {}).get('type'))
        if calc_type is not None:
            param_types[inp['name']] = calc_type
    values_out = {}
    for out in node.get('outputs', []) or []:
        if not isinstance(out, dict) or not out.get('name'):
            continue
        name = out['name']
        calc_type = flows.CALC_TYPE_OF_FLOW.get((out.get('type') or {}).get('type'))
        if calc_type is None:
            continue
        expr = formulas.get(name)
        verdict = calc_functions.evaluate(expr, param_types, values, calc_type)
        if not verdict.get('ok'):
            raise NodeFailure(f'公式（{name}）计算失败：{verdict.get("error")}', logs)
        values_out[name] = verdict['value']
    logs.append(f'[calc-expression-1] {len(values_out)} 个公式确定性求值完成')
    return values_out, logs


def _connection(ctx, impl, label):
    connection_id = str(impl.get('connectionId') or '')
    if not connection_id or not ctx.get('project_id'):
        raise NodeFailure(f'{label}节点需要数据连接与所属项目上下文（请在项目空间打开编排后运行）')
    raw = ctx['connections'].get(connection_id)
    if raw is None:
        raise NodeFailure('数据连接不存在或已被删除，请重新选择')
    try:
        cfg = dbdrivers.normalize_config(raw)
    except ValueError as exc:
        raise NodeFailure(f'数据连接配置无效：{exc}') from None
    try:
        secret = secrets_store.read(ctx['project_id'], connection_id)
    except Exception:
        secret = ''
    return cfg, secret


def _exec_sql(node, values, ctx):
    impl = node.get('implementation') or {}
    logs = []
    sql_text, args = flow_sql.render(str(impl.get('sql') or ''), values)
    logs.append('[SQL 渲染] ' + _preview(sql_text) + f'（{len(args)} 个预编译参数）')
    cfg, secret = _connection(ctx, impl, 'SQL')
    if not secret:
        raise NodeFailure('该数据连接尚未保存密码，请到项目「数据连接」页配置', logs)
    execution = node.get('execution') if isinstance(node.get('execution'), dict) else {}
    max_rows = execution.get('maxRows') if isinstance(execution.get('maxRows'), int) else 1000
    allow_write = bool(execution.get('allowWrite'))
    timeout_s = max(1, _node_timeout(node) // 1000)
    try:
        import pymysql
    except ImportError:
        raise NodeFailure('服务端未安装 PyMySQL（pip install PyMySQL），无法执行 SQL 节点', logs)
    try:
        conn = pymysql.connect(host=cfg['host'], port=int(cfg['port']), user=cfg.get('username') or '',
                               password=secret, database=cfg.get('database') or None, charset='utf8mb4',
                               connect_timeout=min(5, timeout_s), read_timeout=timeout_s,
                               write_timeout=timeout_s)
    except Exception as exc:
        raise NodeFailure('数据库连接失败：' + _preview(str(exc), 200), logs) from None
    try:
        with conn.cursor() as cur:
            if not allow_write:
                cur.execute('SET SESSION TRANSACTION READ ONLY')
            cur.execute(sql_text, args)
            if cur.description:  # SELECT
                rows = cur.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                rows = rows[:max_rows]
                columns = [c[0] for c in cur.description]
                records = [jsonify(dict(zip(columns, row))) for row in rows]
                if truncated:
                    logs.append(f'[SQL] 结果超过行数上限 {max_rows}，已截断')
                logs.append(f'[SQL] 查询返回 {len(records)} 行')
                value = _sql_scalar_or_rows(node, records, logs)
            else:
                value = cur.rowcount
                if allow_write:
                    conn.commit()
                logs.append(f'[SQL] 影响 {value} 行')
        return _single_output(value, node), logs
    except NodeFailure:
        raise
    except Exception as exc:
        detail = str(exc)
        if 'READ ONLY' in detail.upper():
            detail = '连接处于只读会话，DML 被拒绝；如需写操作请在节点勾选「允许写」'
        raise NodeFailure('SQL 执行失败：' + _preview(detail, 300), logs) from None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _sql_scalar_or_rows(node, records, logs):
    outputs = [o for o in node.get('outputs', []) or [] if isinstance(o, dict)]
    if len(outputs) > 1:
        logs.append('SQL 节点声明了多个输出，仅第一个参与执行')
    decl = (outputs[0].get('type') if outputs else None) or {}
    kind = decl.get('type')
    if kind == 'list':
        return records
    first = records[0] if records else None
    if kind == 'object':
        return first
    if first is None:
        return None
    scalar = next(iter(first.values()))
    if kind in ('number', 'text', 'boolean', 'datetime') and len(first) > 1:
        logs.append(f'按首行首列承接为标量输出（列 {next(iter(first))}）')
    return scalar


def _exec_redis(node, values, ctx):
    impl = node.get('implementation') or {}
    logs = []
    command = str(impl.get('command') or 'GET').upper()
    if command not in flows.REDIS_COMMANDS:
        raise NodeFailure(f'不支持的 Redis 命令 {command}', logs)
    key_template = str(impl.get('keyTemplate') or '')
    args_decl = impl.get('args') if isinstance(impl.get('args'), list) else []
    cfg, secret = _connection(ctx, impl, 'Redis')
    timeout_s = max(1, _node_timeout(node) // 1000)
    try:
        import redis
    except ImportError:
        raise NodeFailure('服务端未安装 redis 驱动（pip install redis），无法执行 Redis 节点', logs)
    try:
        client = redis.Redis(host=cfg['host'], port=int(cfg['port']), password=secret or None,
                             db=int(cfg.get('dbIndex') or 0), socket_connect_timeout=min(5, timeout_s),
                             socket_timeout=timeout_s)
    except Exception as exc:
        raise NodeFailure('Redis 连接创建失败：' + _preview(str(exc), 200), logs) from None

    def literal(entry, element):
        row = _KEY_TEMPLATE_ROW.fullmatch(entry.strip())
        if row:
            if element is None:
                raise NodeFailure(f'命令参数 {{{{{row.group(1)}}}}} 仅可在行模式引用元素字段', logs)
            return jsonify((element or {}).get(row.group(1)))
        if entry in values:
            return jsonify(values[entry])
        if re.fullmatch(r'-?\d+(\.\d+)?', entry):
            number = float(entry)
            return int(number) if number.is_integer() else number
        return entry

    method = getattr(client, command.lower(), None)
    if method is None:
        raise NodeFailure(f'Redis 驱动不支持命令 {command}', logs)

    def run_once(element):
        key = key_template
        for match in _KEY_TEMPLATE_ROW.finditer(key_template):
            field = match.group(1)
            if not isinstance(element, dict) or field not in element:
                raise NodeFailure(f'key 模板引用了元素不存在的字段 {{{{{field}}}}}', logs)
            key = key.replace(match.group(0), str(jsonify(element[field])))
        for match in _KEY_TEMPLATE_PARAM.finditer(key):
            name = match.group(1)
            if name not in values:
                raise NodeFailure(f'key 模板引用了未提供的输入参数 {name}', logs)
            key = key.replace(match.group(0), str(jsonify(values[name])))
        cmd_args = [literal(entry, element) for entry in args_decl]
        try:
            return jsonify(method(key, *cmd_args))
        except NodeFailure:
            raise
        except Exception as exc:
            raise NodeFailure(f'Redis {command} 执行失败：' + _preview(str(exc), 200), logs) from None

    # 行模式：恰好一个列表输入（与配置检查一致）
    list_input = next((i for i in node.get('inputs', []) or []
                       if isinstance(i, dict) and isinstance(i.get('type'), dict) and i['type'].get('type') == 'list'
                       and isinstance(values.get(i.get('name')), list)), None)
    if list_input is not None and _KEY_TEMPLATE_ROW.search(key_template):
        rows = values[list_input['name']]
        value = [run_once(element if isinstance(element, dict) else {'value': element}) for element in rows]
        logs.append(f'[Redis] {command} 行模式执行 {len(value)} 次')
    else:
        value = run_once(None)
        logs.append(f'[Redis] {command} 执行完成')
    return _single_output(value, node), logs


def _exec_http(node, values, ctx):
    impl = node.get('implementation') or {}
    logs = []
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
    url = flow_http.render_url(str(impl.get('url') or ''), values)
    method = str(impl.get('method') or 'GET').upper()
    headers = {str(k): str(v) for k, v in (impl.get('headers') or {}).items()}
    body = None
    if (impl.get('bodyMode') or 'none') == 'json':
        body_text = flow_http.render_body(str(impl.get('body') or ''), values)
        body = body_text.encode()
        headers.setdefault('Content-Type', 'application/json')
    credential_id = str(impl.get('credentialId') or '')
    if credential_id:
        if not ctx.get('project_id'):
            raise NodeFailure('HTTP 节点使用认证凭据需要所属项目上下文', logs)
        try:
            credential = api_credentials.read(ctx['project_id'], credential_id)
        except Exception:
            credential = None
        if not credential:
            raise NodeFailure('认证凭据不存在或已被删除，请重新选择', logs)
        headers.setdefault('Authorization', 'Bearer ' + str(credential.get('secret') or ''))
        logs.append(f'[HTTP] 使用凭据「{credential.get("name")}」注入 Authorization 头')
    started = time.monotonic()
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=max(1, _node_timeout(node) // 1000)) as response:
            status = response.status
            raw = response.read(2_000_001)
    except HTTPError as exc:
        raise NodeFailure(f'HTTP 调用失败（状态码 {exc.code}）', logs) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise NodeFailure('HTTP 调用失败：' + _preview(str(exc.reason) if hasattr(exc, 'reason') else str(exc), 200), logs) from None
    duration = int((time.monotonic() - started) * 1000)
    if len(raw) > 2_000_000:
        raise NodeFailure('响应体超过 2MB 上限', logs)
    logs.append(f'[HTTP] {method} {url} → {status}（{duration}ms，{len(raw)}B）')
    if not 200 <= status < 300:
        raise NodeFailure(f'HTTP 状态码非 2xx（{status}）：' + _preview(raw.decode("utf-8", "replace"), 200), logs)
    try:
        payload = json.loads(raw.decode('utf-8', 'replace'))
    except ValueError:
        raise NodeFailure('响应体不是合法 JSON：' + _preview(raw.decode('utf-8', 'replace'), 200), logs) from None
    value = flow_http.pick_path(payload, impl.get('responsePath'))
    if value is None and str(impl.get('responsePath') or '').strip():
        raise NodeFailure(f'响应提取路径 {impl.get("responsePath")} 不存在；响应结构：' +
                          _preview(json.dumps(jsonify(payload), ensure_ascii=False), 200), logs)
    logs.append('[HTTP 响应] ' + _preview(json.dumps(jsonify(payload), ensure_ascii=False)))
    return _single_output(jsonify(value), node), logs


_EXECUTORS = {'python': _exec_python, 'calc': _exec_calc, 'sql': _exec_sql,
              'redis': _exec_redis, 'http': _exec_http}


# ── 主入口 ───────────────────────────────────────────────────────────────────
def load_context(project_id):
    """服务端加载项目数据连接全量配置与 API 凭据 id（密钥不在此读取）。"""
    ctx = {'project_id': '', 'connections': {}, 'credential_ids': None}
    if not project_id:
        return ctx
    ctx['project_id'] = projects.clean_id(project_id)
    state, _is_draft = projects.load(ctx['project_id'])
    for conn in (state.get('connections') or {}).get('connections', []) or []:
        if isinstance(conn, dict) and conn.get('id'):
            ctx['connections'][conn['id']] = conn
    try:
        ctx['credential_ids'] = api_credentials.ids(ctx['project_id'])
    except Exception:
        ctx['credential_ids'] = None
    return ctx


def run(state, targets=None, inputs=None, ctx=None, started_at=None):
    """执行编排（全图或 targets 链）。返回响应结构；不落盘。"""
    start = time.monotonic()
    if started_at is None:
        started_at = datetime.now(timezone.utc).isoformat()
    inputs = inputs if isinstance(inputs, dict) else {}
    order = topo_order(state, targets)
    results, ordered = {}, []
    failed = set()
    for node in order:
        nid = node.get('id')
        deps = [d for d in _node_dependencies(node) if d in {n['id'] for n in order}]
        if any(dep in failed for dep in deps):
            results[nid] = {'status': 'skipped'}
            ordered.append({'nodeId': nid, 'status': 'skipped'})
            continue
        entry = {'nodeId': nid, 'status': 'running'}
        results[nid] = entry
        started = time.monotonic()
        logs = []
        try:
            values, input_logs = _resolve_inputs(node, state, results, inputs)
            logs.extend(input_logs)
            executor = _EXECUTORS.get(node.get('kind'))
            if executor is None:
                raise NodeFailure(f'节点类型 {node.get("kind")} 不可执行')
            outputs, exec_logs = executor(node, values, ctx or {})
            logs.extend(exec_logs)
            names_by_id, _decls = _output_slots(node)
            entry.update({'status': 'success', 'outputs': outputs, 'outputNames': names_by_id,
                          'durationMs': int((time.monotonic() - started) * 1000), 'logs': logs})
            primary = next((outputs[n] for n in _output_slots(node)[1] if outputs.get(n) is not None), None)
            entry['rowCount'] = len(primary) if isinstance(primary, list) else (1 if primary is not None else 0)
        except NodeFailure as exc:
            failed.add(nid)
            entry.update({'status': 'failed', 'error': str(exc),
                          'durationMs': int((time.monotonic() - started) * 1000),
                          'logs': logs + list(exc.logs or [])})
        except Exception as exc:  # 执行器兜底：任何意外都成为可读的节点失败，而不是 500
            failed.add(nid)
            entry.update({'status': 'failed', 'error': '节点执行异常：' + _preview(str(exc), 300),
                          'durationMs': int((time.monotonic() - started) * 1000), 'logs': logs})
        ordered.append(entry)
    status = 'success' if not failed else 'error'
    return {'status': status, 'nodeResults': ordered, 'startedAt': started_at,
            'durationMs': int((time.monotonic() - start) * 1000)}

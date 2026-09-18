"""函数编排（一期）独立存储与纯配置校验。

第三工作区的数据线，与本体/项目两条保存线完全独立：
  ontology/drafts/flows/<flowId>/current.json              原子指针
  ontology/drafts/flows/<flowId>/revisions/<seq>-<hash>/flow.json  整修订目录

一期边界：只有草稿（无发布）、软删除（status=deleted，修订历史永不物理清除）、
复制（服务端重生成全部稳定 ID 并重映射引用）。配置检查是纯结构校验：
绝不执行 Python/SQL、绝不访问数据库、绝不联网；检查失败不影响草稿保存。
未知字段整体保留（零丢失）：本模块只读取认识的键，从不重建整个状态。
"""
import ast
import copy
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from workbench import calc_functions, flow_http, flow_sql
from workbench.paths import DATA_ROOT
from workbench.query_rules import IDENTIFIER, SQL_PARAM, strip_sql_noise_mysql

DRAFTS = DATA_ROOT / 'ontology/drafts/flows'
FLOW_FILE = 'flow.json'
INPUT_NODE = 'flow-input'
OUTPUT_NODE = 'flow-output'
RESERVED_NODE_IDS = (INPUT_NODE, OUTPUT_NODE)
SCALAR_TYPES = ('text', 'number', 'boolean', 'datetime')
ALL_TYPES = SCALAR_TYPES + ('object', 'list')
TYPE_LABELS = {'text': '文本', 'number': '数值', 'boolean': '是/否', 'datetime': '日期时间',
               'object': '对象', 'list': '列表'}
ENGINES = ('mysql',)
MAX_TYPE_DEPTH = 3
NODE_KINDS = ('python', 'sql', 'redis', 'http', 'calc')
NODE_KIND_LABEL = {'python': 'Python', 'sql': 'SQL', 'redis': 'Redis', 'http': 'HTTP', 'calc': '计算'}
# Redis 白名单命令表：命令 → (最少参数, 最多参数(None=不限), 返回类型)；表外命令一律拒绝
REDIS_COMMANDS = {
    'GET': (1, 1, 'text'), 'MGET': (1, None, 'list'), 'EXISTS': (1, None, 'number'),
    'TTL': (1, 1, 'number'), 'TYPE': (1, 1, 'text'), 'STRLEN': (1, 1, 'number'),
    'HGET': (2, 2, 'text'), 'HGETALL': (1, 1, 'object'), 'HMGET': (2, None, 'list'),
    'HKEYS': (1, 1, 'list'), 'HVALS': (1, 1, 'list'), 'HLEN': (1, 1, 'number'),
    'LRANGE': (3, 3, 'list'), 'LLEN': (1, 1, 'number'), 'SISMEMBER': (2, 2, 'boolean'),
    'SMEMBERS': (1, 1, 'list'), 'SCARD': (1, 1, 'number'), 'ZSCORE': (2, 2, 'number'),
    'ZRANGE': (3, 3, 'list'), 'ZCARD': (1, 1, 'number'),
    'SET': (2, 3, 'text'), 'SETEX': (3, 3, 'text'), 'SETNX': (2, 2, 'number'),
    'DEL': (1, None, 'number'), 'INCR': (1, 1, 'number'), 'DECR': (1, 1, 'number'),
    'HSET': (3, None, 'number'), 'HMSET': (2, None, 'text'), 'LPUSH': (2, None, 'number'),
    'RPUSH': (2, None, 'number'), 'SADD': (2, None, 'number'), 'ZADD': (3, None, 'number'),
    'EXPIRE': (2, 2, 'number'),
}
HTTP_METHODS = flow_http.METHODS
EXEC_MAX_TIMEOUT_MS = 300000
SQL_MAX_ROWS_MAX = 10000
# 公式模式的流类型 → calc-expression 类型映射（datetime/object/list 不可进公式）
CALC_TYPE_OF_FLOW = {'number': 'number', 'text': 'string', 'boolean': 'boolean'}


class FlowNotFound(ValueError):
    pass


def clean_id(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', identifier):
        raise ValueError('编排标识无效')
    return identifier


def draft_dir(identifier):
    path = DRAFTS / clean_id(identifier)
    if path.resolve().parent != DRAFTS.resolve():
        raise ValueError('编排目录无效')
    return path


# --- draft store ---------------------------------------------------------------

def _pointer(identifier):
    path = draft_dir(identifier) / 'current.json'
    return json.loads(path.read_text()) if path.is_file() else None


def revision_of(state):
    payload = {k: v for k, v in state.items() if not str(k).startswith('_')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def ensure_structure(state):
    """保存前的最低结构门槛：损坏请求在此拒绝（ValueError → 400）。
    配置层面的未完成项由 check_flow 报告，绝不在这里拦截。"""
    if not isinstance(state, dict):
        raise ValueError('编排状态必须是对象')
    if not isinstance(state.get('flowId'), str) or not state.get('flowId'):
        raise ValueError('编排缺少 flowId')
    clean_id(state['flowId'])
    for key in ('inputs', 'outputs', 'connections', 'nodes'):
        if not isinstance(state.get(key), list):
            raise ValueError(f'编排 {key} 必须是列表')
    if state.get('layout') is not None and not isinstance(state.get('layout'), dict):
        raise ValueError('编排 layout 无效')
    for node in state['nodes']:
        if not isinstance(node, dict):
            raise ValueError('节点必须是对象')
        if node.get('id') in RESERVED_NODE_IDS:
            raise ValueError('编排输入/输出是边界节点，不能出现在处理节点列表中')


def read_draft(identifier):
    pointer = _pointer(identifier)
    if not pointer:
        return None
    directory = draft_dir(identifier) / 'revisions' / str(pointer.get('revisionDir', ''))
    if not directory.is_dir():
        raise ValueError('编排草稿修订不完整，请检查 ontology/drafts/flows 目录')
    state = json.loads((directory / FLOW_FILE).read_text())
    state['_draft'] = {'seq': pointer.get('seq', 0), 'updatedAt': pointer.get('updatedAt', '')}
    return state


def save_draft(state):
    """整修订写入 + 原子换指针；失败清理修订目录，绝不留半写状态。"""
    ensure_structure(state)
    identifier = clean_id(state['flowId'])
    directory = draft_dir(identifier)
    pointer = _pointer(identifier)
    seq = (pointer.get('seq', 0) if pointer else 0) + 1
    rev = revision_of(state)
    revision_dir = directory / 'revisions' / f'{seq:04d}-{rev[:8]}'
    if revision_dir.exists():
        revision_dir = directory / 'revisions' / f'{seq:04d}-{rev[:8]}-{uuid4().hex[:4]}'
    revision_dir.mkdir(parents=True, exist_ok=False)
    try:
        (revision_dir / FLOW_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        temp = directory / '.current.tmp'
        temp.write_text(json.dumps({'seq': seq, 'revision': rev, 'revisionDir': revision_dir.name,
                                    'updatedAt': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False))
        temp.replace(directory / 'current.json')
    except Exception:
        shutil.rmtree(revision_dir, ignore_errors=True)
        raise
    return {'revision': rev, 'seq': seq}


def blank_flow(identifier, name, description=''):
    return {'schemaVersion': 1, 'flowId': identifier, 'name': name,
            'description': str(description or ''), 'status': 'active',
            'inputs': [], 'outputs': [], 'connections': [], 'nodes': [],
            'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}}}


def create(name, description=''):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('编排名称需要填写 1～80 个字符')
    name = name.strip()
    if any(item['name'].casefold() == name.casefold() for item in listing()):
        raise ValueError('已存在同名编排，请换一个名称')
    identifier = uuid4().hex[:12]
    while _pointer(identifier):
        identifier = uuid4().hex[:12]
    state = blank_flow(identifier, name, description)
    directory = draft_dir(identifier)
    directory.mkdir(parents=True, exist_ok=False)
    try:
        save_draft(state)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return {'id': identifier, 'name': name}


def listing(include_deleted=False):
    items = []
    if not DRAFTS.is_dir():
        return items
    for pointer_path in sorted(DRAFTS.glob('*/current.json')):
        identifier = pointer_path.parent.name
        try:
            state = read_draft(identifier)
            pointer = json.loads(pointer_path.read_text())
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        status = state.get('status') or 'active'
        if status == 'deleted' and not include_deleted:
            continue
        check = check_flow(state)
        items.append({'id': identifier,
                      'name': str(state.get('name') or identifier),
                      'description': str(state.get('description') or ''),
                      'status': status,
                      'nodeCount': sum(1 for n in state.get('nodes', []) if isinstance(n, dict)),
                      'errorCount': len(check['errors']),
                      'updatedAt': str(pointer.get('updatedAt', '')),
                      'configStatus': check['status']})
    return items


def copy_flow(identifier, name=None):
    """复制编排：全部稳定 ID 重新生成并重映射引用；原编排任何字节不动。"""
    source = read_draft(clean_id(identifier))
    if source is None:
        raise FlowNotFound('编排不存在')
    state = copy.deepcopy({k: v for k, v in source.items() if not str(k).startswith('_')})
    clone_id = uuid4().hex[:12]
    while _pointer(clone_id):
        clone_id = uuid4().hex[:12]
    state['flowId'] = clone_id
    state['status'] = 'active'
    # 项目数据连接引用（不在编排自带 connections 里的 connectionId）复制时保持不变
    legacy_conn_ids = {c.get('id') for c in (state.get('connections') or [])
                       if isinstance(c, dict) and isinstance(c.get('id'), str) and c.get('id')}

    id_map = {}

    def new_id(old, prefix='x'):
        if old not in id_map:
            id_map[old] = f'{prefix}-{uuid4().hex[:12]}'
        return id_map[old]

    def remap_type(decl):
        if not isinstance(decl, dict):
            return decl
        if decl.get('type') == 'object':
            for field in decl.get('fields') or []:
                if isinstance(field, dict):
                    if field.get('id'):
                        field['id'] = new_id(field['id'], 'fld')
                    remap_type(field.get('type'))
        elif decl.get('type') == 'list':
            remap_type(decl.get('elementType'))
        return decl

    def remap_source(src):
        if not isinstance(src, dict):
            return src
        kind = src.get('kind')
        if kind == 'flowInput' and src.get('inputId'):
            src['inputId'] = new_id(src['inputId'], 'fin')
        elif kind in ('node', 'nodeField'):
            if src.get('nodeId'):
                src['nodeId'] = new_id(src['nodeId'], 'nd')
            if src.get('outputId'):
                src['outputId'] = new_id(src['outputId'], 'out')
            if isinstance(src.get('fieldPath'), list):
                src['fieldPath'] = [new_id(f, 'fld') for f in src['fieldPath']]
        return src

    for conn in state.get('connections', []):
        if isinstance(conn, dict) and conn.get('id'):
            conn['id'] = new_id(conn['id'], 'cn')
    for inp in state.get('inputs', []):
        if isinstance(inp, dict):
            if inp.get('id'):
                inp['id'] = new_id(inp['id'], 'fin')
            remap_type(inp.get('type'))
    for out in state.get('outputs', []):
        if isinstance(out, dict):
            if out.get('id'):
                out['id'] = new_id(out['id'], 'fout')
            remap_type(out.get('type'))
            if isinstance(out.get('binding'), dict):
                remap_source(out['binding'])
    for node in state.get('nodes', []):
        if not isinstance(node, dict):
            continue
        if node.get('id'):
            node['id'] = new_id(node['id'], 'nd')
        for inp in node.get('inputs', []) or []:
            if isinstance(inp, dict):
                if inp.get('id'):
                    inp['id'] = new_id(inp['id'], 'in')
                remap_type(inp.get('type'))
                if inp.get('source') is not None:
                    remap_source(inp['source'])
        for out in node.get('outputs', []) or []:
            if isinstance(out, dict):
                if out.get('id'):
                    out['id'] = new_id(out['id'], 'out')
                remap_type(out.get('type'))
        impl = node.get('implementation')
        if isinstance(impl, dict) and impl.get('connectionId'):
            # 仅重映射编排自带连接的引用；项目数据连接引用保持原 id
            if impl['connectionId'] in legacy_conn_ids:
                impl['connectionId'] = new_id(impl['connectionId'], 'cn')
    positions = (state.get('layout') or {}).get('positions')
    if isinstance(positions, dict):
        state.setdefault('layout', {})['positions'] = {new_id(k, 'nd'): v for k, v in positions.items()
                                                       if isinstance(v, dict)}

    base = str(name or '').strip() or f"{state.get('name') or '编排'}-副本"
    final, counter = base, 2
    taken = {item['name'].casefold() for item in listing()}
    while final.casefold() in taken:
        final = f'{base}{counter}'
        counter += 1
    state['name'] = final
    save_draft(state)
    return {'id': clone_id, 'name': final}


def soft_delete(identifier):
    """软删除：只追加一条 status=deleted 的修订，历史修订全部保留。"""
    state = read_draft(clean_id(identifier))
    if state is None:
        raise FlowNotFound('编排不存在')
    if state.get('status') != 'deleted':
        state['status'] = 'deleted'
        save_draft(state)
    return {'ok': True}


# --- configuration check（纯结构校验，无执行语义） -------------------------------

def _field_of(decl, field_path):
    current = decl
    for field_id in field_path:
        if not isinstance(current, dict) or current.get('type') != 'object':
            return None
        match = next((f for f in (current.get('fields') or [])
                      if isinstance(f, dict) and f.get('id') == field_id), None)
        if match is None:
            return None
        current = match.get('type')
    return current


def _type_decl_errors(decl, label, object_depth=0):
    """object_depth = 当前声明外层已嵌套的对象层数；列表不占层，字段内的对象再 +1。"""
    errors = []
    if not isinstance(decl, dict):
        return [f'{label}：类型声明无效']
    kind = decl.get('type')
    if kind not in ALL_TYPES:
        return [f'{label}：数据类型无效']
    if kind == 'object':
        if object_depth >= MAX_TYPE_DEPTH:
            return [f'{label}：对象嵌套超过 {MAX_TYPE_DEPTH} 层']
        fields = decl.get('fields')
        if fields is not None and not isinstance(fields, list):
            return [f'{label}：对象字段声明无效']
        seen_names, seen_ids = set(), set()
        for field in fields or []:
            if not isinstance(field, dict):
                errors.append(f'{label}：对象字段声明无效')
                continue
            fname = field.get('name')
            if not isinstance(fname, str) or not IDENTIFIER.fullmatch(fname or ''):
                errors.append(f'{label}：对象字段技术名无效（须为字母/下划线开头）')
            elif fname in seen_names:
                errors.append(f'{label}：对象字段技术名 {fname} 重复')
            else:
                seen_names.add(fname)
            fid = field.get('id')
            if not isinstance(fid, str) or not fid:
                errors.append(f'{label}：字段「{field.get("label") or fname}」缺少稳定 ID')
            elif fid in seen_ids:
                errors.append(f'{label}：字段稳定 ID 重复')
            else:
                seen_ids.add(fid)
            errors.extend(_type_decl_errors(field.get('type'), f'{label}.{fname or "?"}', object_depth + 1))
    elif kind == 'list':
        errors.extend(_type_decl_errors(decl.get('elementType'), label + ' 的列表元素', object_depth))
    return errors


def _structure_warnings(decl, label, object_depth=0):
    """结构未声明的对象/对象列表允许保存，但按待完善提示，绝不假装检查通过。"""
    warnings = []
    if object_depth > MAX_TYPE_DEPTH or not isinstance(decl, dict):
        return warnings
    if decl.get('type') == 'object':
        if not (decl.get('fields') or []):
            warnings.append(f'{label}：对象结构尚未声明字段，待完善')
        for field in decl.get('fields') or []:
            if isinstance(field, dict):
                warnings.extend(_structure_warnings(field.get('type'),
                                                    f'{label}.{field.get("name") or "?"}', object_depth + 1))
    elif decl.get('type') == 'list':
        element = decl.get('elementType')
        if isinstance(element, dict) and element.get('type') == 'object' and not (element.get('fields') or []):
            warnings.append(f'{label}：列表元素对象尚未声明字段，待完善')
        else:
            warnings.extend(_structure_warnings(element, label + ' 的列表元素', object_depth))
    return warnings


def types_compatible(target, source):
    """保守相容：类型名一致；对象按字段技术名逐一同名匹配且嵌套相容。
    稳定 ID 用于绑定路径与引用；类型相容性按技术名判定——独立声明的同结构对象
    （字段 ID 不同但技术名/类型一致）视为相容，改显示名不影响任何判断。"""
    if not isinstance(target, dict) or not isinstance(source, dict):
        return False, '类型声明无效'
    if target.get('type') != source.get('type'):
        return False, f'来源 {TYPE_LABELS.get(source.get("type"), "?")} 与目标 {TYPE_LABELS.get(target.get("type"), "?")} 不一致'
    if target.get('type') == 'object':
        source_fields = {f.get('name'): f for f in (source.get('fields') or []) if isinstance(f, dict) and f.get('name')}
        for field in target.get('fields') or []:
            if not isinstance(field, dict) or not field.get('name'):
                continue
            match = source_fields.get(field['name'])
            if match is None:
                return False, f'来源缺少字段「{field.get("label") or field.get("name")}」'
            ok, reason = types_compatible(field.get('type'), match.get('type'))
            if not ok:
                return False, f'字段「{field.get("label") or field.get("name")}」' + reason
    elif target.get('type') == 'list':
        return types_compatible(target.get('elementType'), source.get('elementType'))
    return True, ''


def check_flow(state, project_connections=None, llm_meta=None, credential_ids=None):
    """纯配置检查。返回 {errors, warnings, items, status}；items 携带 kind/id 供前端定位。
    project_connections：SQL/Redis 节点可引用的项目数据连接（仅元数据）。
    llm_meta：LLM 提供方元数据列表（{'id','name'}）；None 表示无法获取（跳过存在性检查），
    空列表 = 尚未配置任何提供方（Python/LLM 计算节点给指引 warning）。
    credential_ids：项目 API 凭据 id 集合（HTTP 节点认证引用校验）；None 跳过。
    返回在既有 errors/warnings/items/status 之外附 diagnostics[]（仅 API 定位信息，
    不存草稿）：{code, level, message, kind, id, section, field, parameterId}。
    section ∈ inputs/implementation/outputs/advanced；无精确 field 的问题退到 section/节点。
    不做语法/业务正确性判断之外的执行语义检查，也不尝试执行任何代码。"""
    errors, warnings, items, diagnostics = [], [], [], []
    index, diag_index = {}, set()

    def report(level, kind, item_id, name, text, code='CONFIG_ISSUE', section=None,
               field=None, parameter_id=None):
        (errors if level == 'error' else warnings).append(text)
        key = (kind, str(item_id))
        if key not in index:
            entry = {'kind': kind, 'id': str(item_id), 'name': name, 'level': level, 'issues': [text]}
            index[key] = entry
            items.append(entry)
        else:
            entry = index[key]
            if text not in entry['issues']:
                entry['issues'].append(text)
            if level == 'error':
                entry['level'] = 'error'
        diag_key = (code, level, kind, str(item_id), str(parameter_id or ''), str(field or ''))
        if diag_key not in diag_index:
            diag_index.add(diag_key)
            diagnostics.append({'code': code, 'level': level, 'message': text, 'kind': kind,
                                'id': str(item_id), 'section': section, 'field': field,
                                'parameterId': parameter_id})

    try:
        _check_body(state, report, project_connections, llm_meta, credential_ids)
    except Exception:  # 结构怪异的数据也要给出可定位的失败，而不是 500
        report('error', 'flow', str(state.get('flowId') if isinstance(state, dict) else '') or 'flow',
               '编排', '编排结构无法解析，请修复或重新创建编排', code='FLOW_STATE_UNPARSEABLE')

    return {'errors': list(dict.fromkeys(errors)), 'warnings': list(dict.fromkeys(warnings)),
            'items': items, 'status': 'passed' if not errors and not warnings else 'pending',
            'diagnostics': diagnostics}


def _execution_issues(node):
    """节点执行参数（v2 additive）：返回 (text, code, field) 三元组列表。"""
    execution = node.get('execution')
    if execution is None:
        return []
    if not isinstance(execution, dict):
        return [('execution 执行参数必须是对象', 'EXEC_CONFIG_INVALID', 'execution')]
    issues = []
    timeout = execution.get('timeoutMs')
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, int)
                                or not 1000 <= timeout <= EXEC_MAX_TIMEOUT_MS):
        issues.append((f'节点超时无效（1000–{EXEC_MAX_TIMEOUT_MS} 毫秒）', 'EXEC_TIMEOUT_INVALID', 'timeoutMs'))
    max_rows = execution.get('maxRows')
    if max_rows is not None and (isinstance(max_rows, bool) or not isinstance(max_rows, int)
                                 or not 1 <= max_rows <= SQL_MAX_ROWS_MAX):
        issues.append((f'行数上限无效（1–{SQL_MAX_ROWS_MAX}）', 'EXEC_MAXROWS_INVALID', 'maxRows'))
    if 'allowWrite' in execution and not isinstance(execution.get('allowWrite'), bool):
        issues.append(('允许写必须是是/否', 'EXEC_ALLOWWRITE_INVALID', 'allowWrite'))
    return issues


def _check_llm_provider(impl, llm_meta, report, kind, item_id, label):
    provider = str(impl.get('providerId') or '')
    if provider and llm_meta is not None and provider not in {m.get('id') for m in llm_meta}:
        report('error', kind, item_id, label, 'LLM 提供方不存在或已被删除，请重新选择',
               code='LLM_PROVIDER_NOT_FOUND', section='implementation', field='providerId')
    if llm_meta is not None and not llm_meta:
        report('error', kind, item_id, label, '尚未配置 LLM 提供方：请到「更多工具 → LLM 配置」添加后重试',
               code='LLM_PROVIDER_NOT_CONFIGURED', section='implementation', field='providerId')


def _check_python_impl(node, impl, llm_meta, report, nid, nlabel):
    code = impl.get('code')
    if not isinstance(code, str) or not code.strip():
        report('error', 'node', nid, nlabel, 'Python 代码为空',
               code='IMPLEMENTATION_EMPTY', section='implementation', field='code')
        return
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        report('error', 'node', nid, nlabel, f'Python 代码语法无法解析（第 {exc.lineno} 行）',
               code='PYTHON_SYNTAX_INVALID', section='implementation', field='code')
        return
    if not any(isinstance(item, ast.FunctionDef) and item.name == 'main' for item in tree.body):
        report('error', 'node', nid, nlabel, 'Python 代码需在顶层定义 main 函数（def main(...)）',
               code='PYTHON_MAIN_MISSING', section='implementation', field='code')
    _check_llm_provider(impl, llm_meta, report, 'node', nid, nlabel)


def _check_calc_impl(node, impl, input_map, llm_meta, report, nid, nlabel):
    mode = impl.get('mode') or 'formula'
    if mode not in ('formula', 'llm'):
        report('error', 'node', nid, nlabel, '计算模式无效（formula/llm）',
               code='CALC_MODE_INVALID', section='implementation', field='mode')
        return
    outputs = [o for o in node.get('outputs', []) if isinstance(o, dict) and o.get('name')]
    if mode == 'llm':
        if not str(impl.get('llmInstruction') or '').strip():
            report('error', 'node', nid, nlabel, 'LLM 计算模式需要填写自然语言计算规则',
                   code='LLM_INSTRUCTION_MISSING', section='implementation', field='llmInstruction')
        _check_llm_provider(impl, llm_meta, report, 'node', nid, nlabel)
        return
    formulas = impl.get('formulas')
    if formulas is None:
        formulas = {}
    if not isinstance(formulas, dict):
        report('error', 'node', nid, nlabel, '公式声明无效（应为 输出技术名 → 公式 的对象）',
               code='CALC_FORMULAS_INVALID', section='implementation', field='formulas')
        return
    output_names = {o['name'] for o in outputs}
    for name in formulas:
        if name not in output_names:
            report('error', 'node', nid, nlabel, f'公式 {name} 没有对应的输出',
                   code='CALC_OUTPUT_UNKNOWN', section='implementation', field='formulas')
    param_types = {}
    unmappable = {}
    for info in input_map.values():
        decl = info['decl'] if isinstance(info['decl'], dict) else {}
        calc_type = CALC_TYPE_OF_FLOW.get(decl.get('type'))
        name = info['input'].get('name')
        if calc_type is not None:
            param_types[name] = calc_type
        else:
            unmappable[name] = decl.get('type') or '?'
    for out in outputs:
        name = out['name']
        expr = formulas.get(name)
        calc_type = CALC_TYPE_OF_FLOW.get(out.get('type', {}).get('type') if isinstance(out.get('type'), dict) else None)
        if calc_type is None:
            report('error', 'node', nid, nlabel,
                   f'公式模式仅支持数值/文本/是否类型的输出「{out.get("label") or name}」',
                   code='CALC_OUTPUT_TYPE_UNSUPPORTED', section='outputs', field='type', parameter_id=out.get('id'))
            continue
        if not isinstance(expr, str) or not expr.strip():
            report('error', 'node', nid, nlabel, f'输出「{out.get("label") or name}」缺少公式',
                   code='CALC_FORMULA_MISSING', section='implementation', field='formulas', parameter_id=out.get('id'))
            continue
        for ref in re.findall(r'\{([A-Za-z_][A-Za-z0-9_]*)\}', expr):
            if ref not in input_map:
                report('error', 'node', nid, nlabel, f'公式引用了不存在的输入 {{{ref}}}',
                       code='CALC_PARAM_UNKNOWN', section='implementation', field='formulas', parameter_id=out.get('id'))
            elif ref in unmappable:
                report('error', 'node', nid, nlabel,
                       f'公式不支持引用 {TYPE_LABELS.get(unmappable[ref], unmappable[ref])} 类型的输入「{ref}」',
                       code='CALC_PARAM_TYPE_UNSUPPORTED', section='implementation', field='formulas', parameter_id=out.get('id'))
        issue = calc_functions.validate_expression(expr, param_types, calc_type)
        if issue:
            report('error', 'node', nid, nlabel, f'公式（{name}）：{issue}',
                   code='CALC_FORMULA_INVALID', section='implementation', field='formulas', parameter_id=out.get('id'))


def _check_sql_impl(node, impl, input_names, input_map, connections, project_connections, report, nid, nlabel):
    sql = impl.get('sql')
    if not isinstance(sql, str) or not sql.strip():
        report('error', 'node', nid, nlabel, 'SQL 模板为空',
               code='IMPLEMENTATION_EMPTY', section='implementation', field='sql')
    conn = connections.get(impl.get('connectionId'))
    if not impl.get('connectionId'):
        report('error', 'node', nid, nlabel, '请选择数据连接（来自数据连接菜单）',
               code='CONNECTION_MISSING', section='implementation', field='connectionId')
    elif conn is None:
        if project_connections is None:
            report('warning', 'node', nid, nlabel, '数据连接引用待在编辑器中选择项目数据连接后校验',
                   code='CONNECTION_UNVERIFIED', section='implementation', field='connectionId')
        else:
            report('error', 'node', nid, nlabel, '数据连接不存在或已被删除，请重新选择',
                   code='CONNECTION_NOT_FOUND', section='implementation', field='connectionId')
    elif conn.get('engine') != 'mysql':
        report('error', 'node', nid, nlabel, '数据连接类型须为 MySQL',
               code='CONNECTION_ENGINE_INVALID', section='implementation', field='connectionId')
    if isinstance(sql, str) and sql.strip():
        try:
            scanned = flow_sql.scan_template(sql)
        except flow_sql.FlowSqlError as exc:
            report('error', 'node', nid, nlabel, f'SQL 模板无法解析：{exc}',
                   code='SQL_PARSE_INVALID', section='implementation', field='sql')
            scanned = {'named': [], 'collections': []}
        referenced = set(SQL_PARAM_SCAN(sql)) | set(scanned['named'])
        for param in sorted(referenced):
            if param not in input_names:
                report('error', 'node', nid, nlabel, f'SQL 引用了未声明的输入参数 :{param}',
                       code='SQL_PARAM_UNDECLARED', section='implementation', field='sql')
        for name in sorted(n for n in input_names if n not in referenced):
            report('warning', 'node', nid, nlabel, f'输入参数 {name} 未在 SQL 中引用',
                   code='SQL_PARAM_UNUSED', section='implementation', field='sql')
        for collection in scanned['collections']:
            info = input_map.get(collection)
            if info is None:
                report('error', 'node', nid, nlabel, f'foreach 引用了未声明的集合 {collection}',
                       code='SQL_FOREACH_COLLECTION_INVALID', section='implementation', field='sql')
            elif not isinstance(info['decl'], dict) or info['decl'].get('type') != 'list':
                report('error', 'node', nid, nlabel, f'foreach 的集合 {collection} 须为列表类型的输入',
                       code='SQL_FOREACH_NOT_LIST', section='implementation', field='sql')
        if re.search(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}', sql):
            report('warning', 'node', nid, nlabel, '${} 原文拼接存在 SQL 注入风险，请确认参数来源可信',
                   code='SQL_RAW_INTERPOLATION', section='implementation', field='sql')


def _check_redis_impl(node, impl, input_names, connections, project_connections, report, nid, nlabel):
    inputs = node.get('inputs') if isinstance(node.get('inputs'), list) else []
    key_template = impl.get('keyTemplate')
    if not isinstance(key_template, str) or not key_template.strip():
        report('error', 'node', nid, nlabel, 'Redis key 模板为空',
               code='KEY_TEMPLATE_EMPTY', section='implementation', field='keyTemplate')
    conn = connections.get(impl.get('connectionId'))
    if not impl.get('connectionId'):
        report('error', 'node', nid, nlabel, '请选择数据连接（来自数据连接菜单）',
               code='CONNECTION_MISSING', section='implementation', field='connectionId')
    elif conn is None:
        if project_connections is None:
            report('warning', 'node', nid, nlabel, '数据连接引用待在编辑器中选择项目数据连接后校验',
                   code='CONNECTION_UNVERIFIED', section='implementation', field='connectionId')
        else:
            report('error', 'node', nid, nlabel, '数据连接不存在或已被删除，请重新选择',
                   code='CONNECTION_NOT_FOUND', section='implementation', field='connectionId')
    elif conn.get('engine') != 'redis':
        report('error', 'node', nid, nlabel, '数据连接类型须为 Redis',
               code='CONNECTION_ENGINE_INVALID', section='implementation', field='connectionId')
    command = impl.get('command')
    if command:
        if command not in REDIS_COMMANDS:
            report('error', 'node', nid, nlabel,
                   f'不支持的 Redis 命令 {command}（仅允许白名单内的读写命令，管理命令一律禁止）',
                   code='REDIS_COMMAND_FORBIDDEN', section='implementation', field='command')
        args = impl.get('args')
        if args is not None:
            if not isinstance(args, list) or not all(isinstance(a, str) and a.strip() for a in args):
                report('error', 'node', nid, nlabel, '命令参数无效（应为非空文本列表：输入参数名或字面量）',
                       code='REDIS_ARGS_INVALID', section='implementation', field='args')
                args = []
            low, high = REDIS_COMMANDS.get(command, (0, None))[0], REDIS_COMMANDS.get(command, (0, None))[1]
            if isinstance(args, list) and (len(args) < low or (high is not None and len(args) > high)):
                report('error', 'node', nid, nlabel,
                       f'{command} 需要 {low}' + (f'–{high}' if high else ' 个及以上') + '个参数',
                       code='REDIS_ARGS_COUNT_INVALID', section='implementation', field='args')
            for entry in args if isinstance(args, list) else []:
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', entry) and not re.fullmatch(r'-?\d+(\.\d+)?', entry) \
                        and not re.fullmatch(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}", entry.strip()):
                    report('error', 'node', nid, nlabel,
                           f'命令参数「{entry}」无效：应为输入参数名、数字字面量或行模式 {{字段}}',
                           code='REDIS_ARGS_ENTRY_INVALID', section='implementation', field='args')
    if isinstance(key_template, str) and key_template.strip():
        row_refs = re.findall(r'\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}', key_template)
        param_refs = re.findall(r'\$\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}', key_template)
        if row_refs and param_refs:
            report('error', 'node', nid, nlabel, 'key 模板不能混用 {{行字段}} 与 ${参数}；行模式只引用列表元素字段，单键模式只引用输入参数',
                   code='KEY_TEMPLATE_MIXED', section='implementation', field='keyTemplate')
        elif row_refs:
            list_inputs = [i for i in inputs if isinstance(i, dict) and isinstance(i.get('type'), dict) and i['type'].get('type') == 'list']
            if len(inputs) != 1 or not list_inputs:
                report('error', 'node', nid, nlabel, '行模式（{{行字段}}）要求恰好声明一个列表类型的输入参数',
                       code='KEY_TEMPLATE_ROW_INPUT_INVALID', section='inputs')
            else:
                element = inputs[0]['type'].get('elementType')
                fields = {f.get('name') for f in (element or {}).get('fields', [])
                          if isinstance(f, dict) and f.get('name')} if isinstance(element, dict) else set()
                for ref in row_refs:
                    if ref not in fields:
                        report('error', 'node', nid, nlabel, f'key 模板引用了列表元素不存在的字段 {{{{{ref}}}}}',
                               code='KEY_TEMPLATE_FIELD_UNKNOWN', section='implementation', field='keyTemplate')
                src = inputs[0].get('source')
                if not src or not isinstance(src, dict) or src.get('kind') not in ('node', 'nodeField', 'flowInput'):
                    report('error', 'node', nid, nlabel, '行模式的列表输入须绑定来源（编排入口/其他节点输出）',
                           code='INPUT_BINDING_MISSING', section='inputs',
                           parameter_id=inputs[0].get('id') if isinstance(inputs[0], dict) else None)
            outputs = node.get('outputs') if isinstance(node.get('outputs'), list) else []
            if not command and outputs and not any(isinstance(o.get('type'), dict) and o['type'].get('type') == 'list' for o in outputs):
                report('warning', 'node', nid, nlabel, '行模式输出建议声明为列表（与输入列表逐行对应）')
        else:
            for p in param_refs:
                if p not in input_names:
                    report('error', 'node', nid, nlabel, f'key 模板引用了未声明的输入参数 ${{{p}}}',
                           code='KEY_TEMPLATE_PARAM_UNDECLARED', section='implementation', field='keyTemplate')


def _check_body(state, report, project_connections=None, llm_meta=None, credential_ids=None):
    if not isinstance(state, dict):
        raise ValueError('state must be a dict')
    flow_id = state.get('flowId') or 'flow'
    flow_label = str(state.get('name') or '编排')
    if not str(state.get('name') or '').strip():
        report('error', 'flow', flow_id, '编排', '请填写编排名称')

    # 可引用的数据连接：项目数据连接（数据连接菜单）为主；编排自带声明仅作旧草稿引用回退，
    # 不再校验（界面已无维护入口，报错无法修复）。
    connections = {}
    if isinstance(project_connections, list):
        for conn in project_connections:
            if isinstance(conn, dict) and isinstance(conn.get('id'), str) and conn.get('id'):
                connections[conn['id']] = conn
    for conn in (state.get('connections') or []):
        if isinstance(conn, dict):
            cid = conn.get('id')
            if isinstance(cid, str) and cid and cid not in connections:
                connections[cid] = conn

    # 编排入口参数
    flow_inputs = {}
    for inp in state.get('inputs', []):
        if not isinstance(inp, dict):
            report('error', 'flow', flow_id, flow_label, '编排入口参数声明无效',
                   code='PARAM_DECL_INVALID', section='inputs')
            continue
        iid = inp.get('id')
        label = str(inp.get('label') or inp.get('name') or '未命名参数')
        if not isinstance(iid, str) or not iid:
            report('error', 'flow-input', iid or '?', label, '编排入口参数缺少稳定 ID',
                   code='PARAM_ID_MISSING', section='inputs')
            continue
        if iid in flow_inputs:
            report('error', 'flow-input', iid, label, '编排入口参数稳定 ID 重复',
                   code='PARAM_ID_DUPLICATE', section='inputs', parameter_id=iid)
            continue
        flow_inputs[iid] = inp
        name = inp.get('name')
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name or ''):
            report('error', 'flow-input', iid, label, '编排入口参数技术名无效（须为字母/下划线开头）',
                   code='PARAM_NAME_INVALID', section='inputs', field='name', parameter_id=iid)
        for issue in _type_decl_errors(inp.get('type'), label):
            report('error', 'flow-input', iid, label, issue,
                   code='PARAM_TYPE_INVALID', section='inputs', field='type', parameter_id=iid)
        for issue in _structure_warnings(inp.get('type'), label):
            report('warning', 'flow-input', iid, label, issue,
                   code='PARAM_TYPE_INCOMPLETE', section='inputs', field='type', parameter_id=iid)

    node_index = {}
    for node in state.get('nodes', []):
        if isinstance(node, dict) and isinstance(node.get('id'), str) and node.get('id'):
            node_index[node['id']] = node

    def resolve_source(src, target_label):
        """返回 (类型声明|None, 错误|None, 来源节点ID|None)。"""
        if src is None:
            return None, None, None
        if not isinstance(src, dict):
            return None, '输入来源声明无效', None
        kind = src.get('kind')
        if kind == 'flowInput':
            inp = flow_inputs.get(src.get('inputId'))
            if inp is None:
                return None, f'{target_label}引用了不存在的编排入口参数', None
            return inp.get('type'), None, None
        if kind == 'fixed':
            value_type = src.get('valueType')
            if value_type not in SCALAR_TYPES:
                return None, f'{target_label}固定值数据类型无效', None
            value = src.get('value')
            valid = ((value_type == 'number' and isinstance(value, (int, float)) and not isinstance(value, bool)) or
                     (value_type == 'boolean' and isinstance(value, bool)) or
                     (value_type in ('text', 'datetime') and isinstance(value, str)))
            if not valid:
                return None, f'{target_label}固定值与所选类型不匹配', None
            return {'type': value_type}, None, None
        if kind in ('node', 'nodeField'):
            node = node_index.get(src.get('nodeId'))
            if node is None:
                return None, f'{target_label}引用了不存在的节点', None
            output = next((o for o in node.get('outputs', []) if isinstance(o, dict) and o.get('id') == src.get('outputId')), None)
            if output is None:
                return None, f'{target_label}引用了该节点不存在的输出', None
            if kind == 'node':
                return output.get('type'), None, node.get('id')
            path = src.get('fieldPath')
            if not isinstance(path, list) or not path or not all(isinstance(p, str) and p for p in path):
                return None, f'{target_label}字段引用路径无效', node.get('id')
            decl = _field_of(output.get('type'), path)
            if decl is None:
                return None, f'{target_label}引用了输出中不存在的对象字段', node.get('id')
            return decl, None, node.get('id')
        return None, f'{target_label}输入来源类型未知', None

    edges = []  # (sourceNodeId, targetNodeId)
    for node in state.get('nodes', []):
        if not isinstance(node, dict):
            report('error', 'flow', flow_id, flow_label, '存在结构无效的处理节点')
            continue
        nid = node.get('id')
        nlabel = str(node.get('name') or '').strip() or '未命名节点'
        if not isinstance(nid, str) or not nid:
            report('error', 'flow', flow_id, flow_label, '存在缺少稳定 ID 的处理节点')
            continue
        if node.get('kind') not in NODE_KINDS:
            report('error', 'node', nid, nlabel, '节点类型未知（支持 SQL/计算/Python/Redis/HTTP）',
                   code='NODE_KIND_UNKNOWN', section='implementation')
        if not str(node.get('name') or '').strip():
            report('error', 'node', nid, nlabel, '请填写节点名称',
                   code='NODE_NAME_MISSING', field='name')

        input_names = set()
        inputs = node.get('inputs') if isinstance(node.get('inputs'), list) else []
        for inp in inputs:
            if not isinstance(inp, dict):
                report('error', 'node', nid, nlabel, '输入参数声明无效',
                       code='PARAM_DECL_INVALID', section='inputs')
                continue
            iid = inp.get('id')
            ilabel = str(inp.get('label') or inp.get('name') or '未命名输入')
            if not isinstance(iid, str) or not iid:
                report('error', 'node', nid, nlabel, f'输入「{ilabel}」缺少稳定 ID',
                       code='PARAM_ID_MISSING', section='inputs')
            name = inp.get('name')
            if not isinstance(name, str) or not IDENTIFIER.fullmatch(name or ''):
                report('error', 'node', nid, nlabel, f'输入「{ilabel}」技术名无效（须为字母/下划线开头）',
                       code='PARAM_NAME_INVALID', section='inputs', field='name', parameter_id=iid)
            elif name in input_names:
                report('error', 'node', nid, nlabel, f'输入技术名 {name} 重复',
                       code='PARAM_NAME_DUPLICATE', section='inputs', field='name', parameter_id=iid)
            else:
                input_names.add(name)
            for issue in _type_decl_errors(inp.get('type'), f'输入「{ilabel}」'):
                report('error', 'node', nid, nlabel, issue,
                       code='PARAM_TYPE_INVALID', section='inputs', field='type', parameter_id=iid)
            for issue in _structure_warnings(inp.get('type'), f'输入「{ilabel}」'):
                report('warning', 'node', nid, nlabel, issue,
                       code='PARAM_TYPE_INCOMPLETE', section='inputs', field='type', parameter_id=iid)
            src = inp.get('source')
            decl, error, source_node = resolve_source(src, f'输入「{ilabel}」')
            if error:
                report('error', 'node', nid, nlabel, error,
                       code='INPUT_REFERENCE_INVALID', section='inputs', field='binding', parameter_id=iid)
            if src is None:
                report('warning', 'node', nid, nlabel, f'输入「{ilabel}」尚未绑定来源，待完善',
                       code='INPUT_BINDING_MISSING', section='inputs', field='binding', parameter_id=iid)
            else:
                if source_node == nid:
                    report('error', 'node', nid, nlabel, f'输入「{ilabel}」不能引用本节点自身的输出',
                           code='INPUT_SELF_REFERENCE', section='inputs', field='binding', parameter_id=iid)
                elif source_node:
                    edges.append((source_node, nid))
                if decl is not None and isinstance(inp.get('type'), dict):
                    ok, reason = types_compatible(inp['type'], decl)
                    if not ok:
                        report('error', 'node', nid, nlabel, f'输入「{ilabel}」类型不相容：{reason}',
                               code='INPUT_TYPE_MISMATCH', section='inputs', field='binding', parameter_id=iid)

        output_names = set()
        outputs = node.get('outputs') if isinstance(node.get('outputs'), list) else []
        for out in outputs:
            if not isinstance(out, dict):
                report('error', 'node', nid, nlabel, '输出声明无效',
                       code='PARAM_DECL_INVALID', section='outputs')
                continue
            oid = out.get('id')
            olabel = str(out.get('label') or out.get('name') or '未命名输出')
            if not isinstance(oid, str) or not oid:
                report('error', 'node', nid, nlabel, f'输出「{olabel}」缺少稳定 ID',
                       code='PARAM_ID_MISSING', section='outputs')
            name = out.get('name')
            if not isinstance(name, str) or not IDENTIFIER.fullmatch(name or ''):
                report('error', 'node', nid, nlabel, f'输出「{olabel}」技术名无效（须为字母/下划线开头）',
                       code='PARAM_NAME_INVALID', section='outputs', field='name', parameter_id=oid)
            elif name in output_names:
                report('error', 'node', nid, nlabel, f'输出技术名 {name} 重复',
                       code='PARAM_NAME_DUPLICATE', section='outputs', field='name', parameter_id=oid)
            else:
                output_names.add(name)
            for issue in _type_decl_errors(out.get('type'), f'输出「{olabel}」'):
                report('error', 'node', nid, nlabel, issue,
                       code='PARAM_TYPE_INVALID', section='outputs', field='type', parameter_id=oid)
            for issue in _structure_warnings(out.get('type'), f'输出「{olabel}」'):
                report('warning', 'node', nid, nlabel, issue,
                       code='PARAM_TYPE_INCOMPLETE', section='outputs', field='type', parameter_id=oid)

        input_map = {}
        for inp in inputs:
            if isinstance(inp, dict) and isinstance(inp.get('name'), str) and inp.get('name'):
                input_map[inp['name']] = {'decl': inp.get('type'), 'input': inp}

        impl = node.get('implementation')
        for issue, code, field in _execution_issues(node):
            report('error', 'node', nid, nlabel, issue,
                   code=code, section='advanced', field=field)
        if not isinstance(impl, dict):
            report('error', 'node', nid, nlabel, '缺少实现正文',
                   code='IMPLEMENTATION_MISSING', section='implementation')
            continue
        kind = node.get('kind') or impl.get('language')
        if kind == 'python':
            _check_python_impl(node, impl, llm_meta, report, nid, nlabel)
        elif kind == 'sql':
            _check_sql_impl(node, impl, input_names, input_map, connections, project_connections, report, nid, nlabel)
        elif kind == 'redis':
            _check_redis_impl(node, impl, input_names, connections, project_connections, report, nid, nlabel)
        elif kind == 'http':
            for issue, code, field in flow_http.implementation_issues(impl, input_names, credential_ids):
                report('error', 'node', nid, nlabel, issue,
                       code=code, section='implementation', field=field)
        elif kind == 'calc':
            _check_calc_impl(node, impl, input_map, llm_meta, report, nid, nlabel)
        else:
            report('error', 'node', nid, nlabel, '节点类型未知（支持 SQL/计算/Python/Redis/HTTP）',
                   code='NODE_KIND_UNKNOWN', section='implementation')

    # 循环依赖（自引用已在节点内报告；这里查多节点环）
    deps = {}
    for source_node, target_node in edges:
        deps.setdefault(target_node, set()).add(source_node)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_index}

    def walk(nid, stack):
        color[nid] = GRAY
        stack.append(nid)
        for source_node in deps.get(nid, ()):
            if source_node not in color:
                continue
            if color[source_node] == GRAY:
                cycle = stack[stack.index(source_node):] + [source_node]
                names = [str(node_index[n].get('name') or n) for n in cycle if n in node_index]
                report('error', 'flow', flow_id, flow_label,
                       '存在循环依赖：' + ' → '.join(names), code='FLOW_CYCLE')
            elif color[source_node] == WHITE:
                walk(source_node, stack)
        stack.pop()
        color[nid] = BLACK

    for nid in node_index:
        if color.get(nid) == WHITE:
            walk(nid, [])

    # 编排输出
    output_names = set()
    for out in state.get('outputs', []):
        if not isinstance(out, dict):
            report('error', 'flow-output', '?', '编排输出', '编排输出声明无效',
                   code='PARAM_DECL_INVALID', section='outputs')
            continue
        oid = out.get('id')
        olabel = str(out.get('label') or out.get('name') or '未命名输出')
        if not isinstance(oid, str) or not oid:
            report('error', 'flow-output', oid or '?', olabel, '编排输出缺少稳定 ID',
                   code='PARAM_ID_MISSING', section='outputs')
            continue
        name = out.get('name')
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name or ''):
            report('error', 'flow-output', oid, olabel, '编排输出技术名无效（须为字母/下划线开头）',
                   code='PARAM_NAME_INVALID', section='outputs', field='name', parameter_id=oid)
        elif name in output_names:
            report('error', 'flow-output', oid, olabel, f'编排输出技术名 {name} 重复',
                   code='PARAM_NAME_DUPLICATE', section='outputs', field='name', parameter_id=oid)
        else:
            output_names.add(name)
        for issue in _type_decl_errors(out.get('type'), f'编排输出「{olabel}」'):
            report('error', 'flow-output', oid, olabel, issue,
                   code='PARAM_TYPE_INVALID', section='outputs', field='type', parameter_id=oid)
        binding = out.get('binding')
        if binding is None:
            report('error', 'flow-output', oid, olabel, f'编排输出「{olabel}」尚未绑定来源节点输出',
                   code='OUTPUT_BINDING_MISSING', section='outputs', field='binding', parameter_id=oid)
            continue
        decl, error, _ = resolve_source(binding, f'编排输出「{olabel}」')
        if error:
            report('error', 'flow-output', oid, olabel, error,
                   code='OUTPUT_REFERENCE_INVALID', section='outputs', field='binding', parameter_id=oid)
        elif isinstance(out.get('type'), dict):
            ok, reason = types_compatible(out['type'], decl)
            if not ok:
                report('error', 'flow-output', oid, olabel, f'编排输出「{olabel}」声明类型与来源不相容：{reason}',
                       code='OUTPUT_TYPE_MISMATCH', section='outputs', field='binding', parameter_id=oid)


def SQL_PARAM_SCAN(sql):
    """扫描 SQL 模板中的 :参数（字符串/反引号/注释之外），去重保序。"""
    code = strip_sql_noise_mysql(str(sql or ''))
    seen, out = set(), []
    for match in SQL_PARAM.finditer(code):
        if match[1] not in seen:
            seen.add(match[1])
            out.append(match[1])
    return out

"""辅助填写上下文构建与 contextToken（T1，2026-09-21，接口文档 04 §5.1）。

职责（只读，绝不写任何修订、绝不持全局写锁）：
* build_context：按场景（assist_fields 白名单）装载**权威候选**并裁剪——本体区读当前
  草稿（workspaces），项目区读项目草稿＋固定引用版本（projects/versions）＋目录缓存
  元数据（catalogs）＋编排签名（flows）；只取元数据（id/名称/类型等），任何连接密码、
  API Key、认证头、凭据明文、业务记录不进入返回值。
* 权威指纹 fingerprint：对当前权威状态的 revision/token 组合做 canonical_hash。
  同一状态产出同一指纹；任一组成部分变化即产生新指纹（check_generate 据此判 stale）。
* contextToken：HMAC-SHA256 签名短令牌（进程级随机密钥，不落盘、不进日志、不落库），
  绑定 用户＋space＋projectId＋targetKind＋targetId＋指纹＋draft 摘要，TTL 见
  assist_fields.TOKEN_TTL（600 秒）。verify_token / check_generate 任何失败抛
  ContextStale（路由层转 409 CONTEXT_STALE，客户端重取 §5.1）。

错误语义（与 server.py 既有映射对齐，T2 接线时无需新增分支）：
* ValueError                    → 400（space/purpose 非法、draft 白名单外、缺 projectId）；
* auth.AuthRequired             → 401；
* projects.ProjectNotFound /
  versions.VersionNotFound /
  workspaces.WorkspaceNotFound /
  storage.engine.NotFound       → 404（项目不可见按不存在；targetId 不可见/不存在）；
* CatalogCacheUnreadable        → 503（目录缓存读取失败，绝不降级为空目录）。

协议补充（§5.1 示例之外的增量键，前端镜像 T3 需同步）：
* context.definitionsTruncated / context.flowsTruncated / context.catalogTruncated：
  对应候选类超过 MAX_CANDIDATES（60）时为 true（条目按名称排序保留前 60）；
* context.catalog[i].fieldsTruncated：单表字段超过 MAX_TABLE_FIELDS（100）时为 true。
  被裁候选必须可见「已截断」，不得让被裁条目看起来不存在。
"""
import base64
import hmac
import json
import secrets
import time

from workbench import assist_fields, auth
from workbench import catalogs as catalog_store
from workbench import flows as flow_store
from workbench import llm_providers
from workbench import projects, versions, workspaces
from workbench.project_mapping import bare, sources_of
from workbench.storage.engine import NotFound

# ---- 候选裁剪上限（T1 冻结；截断必须显式标记） ----
MAX_CANDIDATES = 60      # 每类候选（definitions 分类 / flows / catalog 表条目）
MAX_TABLE_FIELDS = 100   # 每表字段
MAX_HINT = 160           # 单条 hint 文本上限（prompt 经济性；超长截断加省略号）

_PURPOSES = ('fill', 'check', 'explain')

# 本体节点类型 →（@type, 中文目标名）
_ONTOLOGY_NODE_KINDS = {'object': ('owl:Class', '对象'),
                        'property': ('owl:DatatypeProperty', '属性'),
                        'sharedProperty': ('mg:SharedProperty', '共享属性'),
                        'link': ('owl:ObjectProperty', '链接')}


class ContextStale(ValueError):
    """contextToken 验签失败/过期/目标或权威状态/draft 已变化：HTTP 409 CONTEXT_STALE。"""


# --- contextToken（进程级 HMAC 密钥：懒生成，不落盘不进日志） -----------------------

_KEY = None
_TOKEN_KEYS = frozenset(('uid', 'space', 'projectId', 'targetKind', 'targetId',
                         'fp', 'dh', 'exp', 'purpose'))


def _signing_key():
    global _KEY
    if _KEY is None:
        _KEY = secrets.token_bytes(32)
    return _KEY


def _b64url(data):
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _b64url_decode(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def sign_token(payload):
    """b64url(JSON payload) + '.' + b64url(HMAC-SHA256)。token 内不含任何敏感信息。"""
    body = _b64url(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':')).encode('utf-8'))
    signature = _b64url(hmac.new(_signing_key(), body.encode('ascii'), 'sha256').digest())
    return body + '.' + signature


def verify_token(token):
    """验签/结构/有效期全部通过 → 返回 payload dict；任何失败抛 ContextStale。

    只做令牌自身校验；uid 归属、目标一致性与指纹一致性在 check_generate 校验。
    """
    try:
        body, signature = str(token).split('.')
        expected = _b64url(hmac.new(_signing_key(), body.encode('ascii'), 'sha256').digest())
        if not hmac.compare_digest(str(signature), expected):
            raise ContextStale('contextToken 无效')
        payload = json.loads(_b64url_decode(body))
    except ContextStale:
        raise
    except Exception as exc:  # 形态损坏/编码非法一律按令牌无效处理
        raise ContextStale('contextToken 无效或已损坏') from exc
    if not isinstance(payload, dict) or not _TOKEN_KEYS.issubset(payload):
        raise ContextStale('contextToken 结构无效')
    exp = payload.get('exp')
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp <= time.time():
        raise ContextStale('contextToken 已过期，请重新获取上下文')
    return payload


# --- 权威指纹 -----------------------------------------------------------------------

def compute_fingerprint(space, project_id='', ontology_id='storage'):
    """当前权威状态的稳定摘要（canonical_hash 归一；同一状态同值）。

    本体区：草稿 head 不透明 token（无草稿为 ''，首次保存必然变化）。
    项目区：项目 head token ＋ 引用版本坐标 ＋ 目录缓存 fingerprint:generation
    （含损坏条目的代际）＋ 全部编排 head token。任一变化即产生新指纹。
    check_generate 前注入本函数重算：权威状态任何变化 → 与令牌 fp 不一致 → ContextStale。
    """
    if space == 'ontology':
        return assist_fields.canonical_hash({'kind': 'ontology',
                                             'id': workspaces.clean_id(ontology_id),
                                             'rev': workspaces.current_token(ontology_id) or ''})
    if space != 'project':
        raise ValueError('space 必须为 ontology 或 project')
    return _project_fingerprint(_project_authority(project_id))


def _project_fingerprint(authority):
    """项目区指纹（与候选装载同一份读取，见 _project_authority）。"""
    state = authority['state']
    reference = (str(state.get('ontologyId') or '') + '@' + str(state.get('ontologyVersion') or ''))
    return assist_fields.canonical_hash({'kind': 'project',
                                         'projectId': projects.clean_id(authority['projectId']),
                                         'rev': authority['revision'],
                                         'ref': reference,
                                         'catalogs': authority['catalogTokens'],
                                         'flows': authority['flowTokens']})


# --- 权威状态装载（只读） -----------------------------------------------------------

def _ontology_bundle(ontology_id):
    info = workspaces.describe(ontology_id)
    identifier = info['id']
    # 先取 head token 再读草稿：两次读取间若发生写入，指纹落后于候选 → check 端
    # fail-closed（宁拒旧上下文，不让候选与指纹错位通过）。
    revision = workspaces.current_token(identifier) or ''
    state = workspaces.read_draft(identifier)
    return {'id': identifier, 'name': info['name'],
            'state': state if state is not None else {},
            'revision': revision}


def _project_authority(project_id):
    """项目区权威状态一次读取（指纹与候选共用同一份，杜绝「候选与指纹不同基线」）。

    返回 {'projectId', 'state', 'revision', 'catalogs', 'catalogTokens',
    'flows', 'flowTokens', 'reference'}。项目不存在/跨账号 → ProjectNotFound（404）；
    目录缓存存储失败 → CatalogCacheUnreadable（503）。
    """
    identifier = projects.clean_id(project_id)
    state, _saved = projects.load(identifier)
    revision = projects.current_token(identifier) or ''
    meta = catalog_store.load_all_meta(identifier)  # 存储失败 → 503，绝不降级空目录
    catalogs, catalog_tokens = {}, {}
    for cid, entry in sorted((meta or {}).items()):
        if not isinstance(entry, dict):
            continue
        catalog_tokens[str(cid)] = (str(entry.get('fingerprint') or '') + ':' +
                                    str(int(entry.get('generation') or 0)))
        payload = entry.get('payload')
        # 单条缓存损坏只跳过该连接（与 GET project-state 注入口径一致）；
        # 存储层整体失败已在 load_all_meta 冒泡为 CatalogCacheUnreadable。
        if not entry.get('unreadable') and isinstance(payload, dict):
            catalogs[str(cid)] = payload
    flows, flow_tokens = {}, {}
    for item in flow_store.listing():
        fid = str(item.get('id') or '')
        if not fid:
            continue
        flow_state = flow_store.read_draft(fid)
        if isinstance(flow_state, dict):
            flows[fid] = flow_state
            flow_tokens[fid] = flow_store.current_token(fid) or ''
    reference = None
    if state.get('ontologyId') and state.get('ontologyVersion'):
        try:
            reference = versions.read_state(workspaces.clean_id(state.get('ontologyId')),
                                            str(state.get('ontologyVersion')))
        except (ValueError, OSError):
            reference = None  # 引用版本缺失：与 GET project-state 同口径降级（warning 由项目页承载）
    return {'projectId': identifier, 'state': state, 'revision': revision,
            'catalogs': catalogs, 'catalogTokens': catalog_tokens,
            'flows': flows, 'flowTokens': flow_tokens, 'reference': reference}


# --- 摘要辅助（只取元数据；任何凭据/业务记录不进入） --------------------------------

def _text(value):
    """JSON-LD 字面量容错读取（plain 字符串或 {'@value': ...}）。"""
    if isinstance(value, dict):
        value = value.get('@value')
    return str(value or '').strip()


def _hint(text, limit=MAX_HINT):
    text = str(text or '').strip()
    if len(text) <= limit:
        return text
    return text[:limit] + '…'


def _node_label(node):
    return _text(node.get('rdfs:label')) or bare(node.get('@id', ''))


def _type_name(decl):
    return str(decl.get('type') or '') if isinstance(decl, dict) else ''


def _definition(kind, identifier, label, hint=''):
    entry = {'kind': kind, 'id': str(identifier or ''), 'label': str(label or '')}
    if hint:
        entry['hint'] = _hint(hint)
    return entry


def _cap(items, limit=MAX_CANDIDATES):
    """按名称排序保留前 limit 条；返回 (items, truncated)。"""
    ordered = sorted(items, key=lambda d: (str(d.get('label') or ''), str(d.get('id') or '')))
    return ordered[:limit], len(ordered) > limit


def _merge_groups(groups):
    """逐类裁剪（每类 ≤60）后合并；任一类被裁即置 truncated（不得隐匿被裁候选）。"""
    out, truncated = [], False
    for group in groups:
        capped, group_truncated = _cap(group)
        out.extend(capped)
        truncated = truncated or group_truncated
    return out, truncated


def _ontology_definitions(graph):
    """本体草稿图 → 各类候选摘要（objects/properties/shared/links）。"""
    objects, properties, shared, links = [], [], [], []
    for node in graph:
        if not isinstance(node, dict) or not node.get('@id'):
            continue
        kind = node.get('@type')
        if kind == 'owl:Class':
            objects.append(_definition('object', node['@id'], _node_label(node)))
        elif kind == 'owl:DatatypeProperty':
            domain = bare(_text((node.get('rdfs:domain') or {}).get('@id')
                                if isinstance(node.get('rdfs:domain'), dict) else ''))
            data_type = bare(_text((node.get('rdfs:range') or {}).get('@id')
                                   if isinstance(node.get('rdfs:range'), dict) else ''))
            hint = domain
            if data_type:
                hint += ' · ' + data_type + ('/时间序列' if node.get('mg:valueShape') == 'timeSeries' else '')
            properties.append(_definition('property', node['@id'], _node_label(node), hint))
        elif kind == 'mg:SharedProperty':
            shared.append(_definition('sharedProperty', node['@id'], _node_label(node)))
        elif kind == 'owl:ObjectProperty':
            start = bare(_text((node.get('rdfs:domain') or {}).get('@id')
                               if isinstance(node.get('rdfs:domain'), dict) else ''))
            end = bare(_text((node.get('rdfs:range') or {}).get('@id')
                             if isinstance(node.get('rdfs:range'), dict) else ''))
            cardinality = str(node.get('mg:cardinality') or '')
            links.append(_definition('link', node['@id'], _node_label(node),
                                     ' → '.join([x for x in (start, end) if x]) +
                                     ((' · ' + cardinality) if cardinality else '')))
    return objects, properties, shared, links


def _workflow_definitions(workflow):
    """业务规则/动作摘要；动作含输入参数声明（actionBinding 场景用）。"""
    rules, actions = [], []
    workflow = workflow if isinstance(workflow, dict) else {}
    for row in workflow.get('businessRules') or []:
        if isinstance(row, dict) and row.get('id'):
            rules.append(_definition('rule', row['id'], row.get('name') or row['id'],
                                     row.get('description') or ''))
    for row in workflow.get('actions') or []:
        if not isinstance(row, dict) or not row.get('id'):
            continue
        inputs = []
        for item in row.get('inputs') or []:
            if isinstance(item, dict) and _text(item.get('name')):
                inputs.append(_text(item.get('id')) or _text(item.get('name')))
        hint = row.get('description') or row.get('effect') or ''
        if inputs:
            hint = (hint + '\n' if hint else '') + '输入参数：' + '、'.join(inputs[:MAX_CANDIDATES])
        actions.append(_definition('action', row['id'], row.get('name') or row['id'], hint))
    return rules, actions


def _connection_definitions(connections, engines=None):
    """项目连接清单 → 连接候选（仅 id/名称/engine；host/端口/库名等不进上下文）。"""
    rows = []
    for conn in connections or []:
        if not isinstance(conn, dict) or not conn.get('id'):
            continue
        engine = str(conn.get('engine') or '')
        if engines is not None and engine not in engines:
            continue
        rows.append(_definition('connection', conn['id'], conn.get('name') or conn['id'], engine))
    return rows


def _source_definitions(state):
    """实例来源与补充来源（REF_SOURCE 候选；identity 之外的显式/旧格式来源统一视图）。"""
    rows, seen = [], {}
    for b in state.get('bindings', {}).get('object_bindings') or []:
        if not isinstance(b, dict):
            continue
        ot = str(b.get('object_type') or '')
        for sid, source in sorted(sources_of(b).items()):
            hint = ' · '.join([x for x in (ot, str(source.get('kind') or ''),
                                           str(source.get('connection') or ''),
                                           str(source.get('table') or '')) if x])
            if sid in seen:
                existing = seen[sid]
                if hint and hint not in existing.get('hint', ''):
                    existing['hint'] = _hint(existing.get('hint', '') + '；' + hint)
                continue
            entry = _definition('source', sid,
                                str(source.get('name') or '') or sid, hint)
            seen[sid] = entry
            rows.append(entry)
    return rows


def _catalog_entries(catalogs):
    """目录缓存 → 表/字段元数据候选（仅 name/comment/dataType；每表字段 ≤100）。"""
    entries, tables_truncated = [], False
    for cid in sorted(catalogs):
        payload = catalogs.get(cid)
        tables = payload.get('tables') if isinstance(payload, dict) else None
        if not isinstance(tables, list):
            continue
        ordered = sorted([t for t in tables if isinstance(t, dict) and t.get('name')],
                         key=lambda t: str(t.get('name') or ''))
        if len(ordered) > MAX_CANDIDATES:
            tables_truncated = True
            ordered = ordered[:MAX_CANDIDATES]
        for table in ordered:
            fields = [f for f in table.get('fields') or [] if isinstance(f, dict) and f.get('name')]
            fields = sorted(fields, key=lambda f: str(f.get('name') or ''))
            fields_truncated = len(fields) > MAX_TABLE_FIELDS
            entries.append({'connection': cid, 'table': str(table.get('name') or ''),
                            'fields': [{'name': str(f.get('name') or ''),
                                        'comment': _hint(_text(f.get('comment')), 200),
                                        'dataType': str(f.get('dataType') or '')}
                                       for f in fields[:MAX_TABLE_FIELDS]],
                            'fieldsTruncated': fields_truncated})
    return entries, tables_truncated


def _flow_payload(flows):
    """编排签名（id/名称/输入/输出类型；列表输出附元素字段，字段 ≤100）。"""
    rows, truncated = [], False
    for fid in sorted(flows):
        state = flows[fid]
        inputs = []
        for item in (state.get('inputs') or []):
            if isinstance(item, dict) and item.get('id'):
                inputs.append({'id': str(item.get('id')),
                               'label': str(item.get('label') or item.get('name') or item.get('id')),
                               'type': _type_name(item.get('type'))})
        inputs, inputs_truncated = _cap(inputs, MAX_CANDIDATES)
        outputs = []
        outputs_truncated = False
        for item in (state.get('outputs') or []):
            if not isinstance(item, dict) or not item.get('id'):
                continue
            out = {'id': str(item.get('id')),
                   'label': str(item.get('label') or item.get('name') or item.get('id')),
                   'type': _type_name(item.get('type'))}
            decl = item.get('type') if isinstance(item.get('type'), dict) else {}
            if decl.get('type') == 'list':
                element = decl.get('elementType') if isinstance(decl.get('elementType'), dict) else {}
                fields = [f for f in (element.get('fields') or [])
                          if isinstance(f, dict) and f.get('id')] if element.get('type') == 'object' else []
                fields = sorted(fields, key=lambda f: str(f.get('name') or ''))
                if len(fields) > MAX_TABLE_FIELDS:
                    out['fieldsTruncated'] = True
                    fields = fields[:MAX_TABLE_FIELDS]
                out['fields'] = [{'id': str(f.get('id')),
                                  'name': str(f.get('name') or ''),
                                  'label': str(f.get('label') or ''),
                                  'type': _type_name(f.get('type'))} for f in fields]
            outputs.append(out)
        if len(outputs) > MAX_CANDIDATES:
            outputs = sorted(outputs, key=lambda o: (o['label'], o['id']))[:MAX_CANDIDATES]
            outputs_truncated = True
        rows.append({'id': fid,
                     'name': str(state.get('name') or fid),
                     'inputs': inputs, 'outputs': outputs,
                     'inputsTruncated': inputs_truncated, 'outputsTruncated': outputs_truncated})
    rows.sort(key=lambda r: (r['name'], r['id']))
    if len(rows) > MAX_CANDIDATES:
        rows = rows[:MAX_CANDIDATES]
        truncated = True
    return rows, truncated


def _model_ready():
    """当前账号是否配置了默认模型；任何配置异常一律 False，绝不阻断上下文获取。"""
    try:
        return llm_providers.default_provider() is not None
    except Exception:
        return False


# --- 目标定位与标题（targetId 不可见/不存在 → 404 语义） ----------------------------

def _ontology_title(target_kind, target_id, state):
    spec = assist_fields.resolve(target_kind)
    if not target_id:
        return '新建' + spec['label']
    graph = (state.get('ontology') or {}).get('@graph') or []
    if target_kind in _ONTOLOGY_NODE_KINDS:
        want, label_zh = _ONTOLOGY_NODE_KINDS[target_kind]
        node = next((n for n in graph if isinstance(n, dict) and n.get('@id') == target_id
                     and n.get('@type') == want), None)
        if node is None:
            raise NotFound('编辑目标不存在或不可见：' + target_id)
        return label_zh + '「' + _node_label(node) + '」'
    workflow = state.get('workflow') or {}
    key, label_zh = ('businessRules', '业务规则') if target_kind == 'rule' else ('actions', '动作')
    row = next((r for r in (workflow.get(key) or [])
                if isinstance(r, dict) and r.get('id') == target_id), None)
    if row is None:
        raise NotFound('编辑目标不存在或不可见：' + target_id)
    return label_zh + '「' + str(row.get('name') or target_id) + '」'


def _reference_labels(reference):
    """引用版本图的 {bare id: 显示名}（缺失时退回空表，标题退用裸 id）。"""
    labels = {}
    graph = ((reference or {}).get('ontology') or {}).get('@graph') or []
    for node in graph:
        if isinstance(node, dict) and node.get('@id'):
            labels[bare(node['@id'])] = _node_label(node)
    return labels


def _binding_of(bindings, object_type):
    return next((b for b in bindings if isinstance(b, dict)
                 and str(b.get('object_type') or '') == object_type), None)


def _ref_def_exists(reference, bare_id, want_types, domain_bare=None):
    """引用版本图中是否存在指定裸 id 的定义（可选校验 domain）。用于放宽「未配置目标」：
    定义存在而项目侧尚无配置 → 允许辅助（标题标「未配置」）；定义本身不存在 → 404。"""
    for node in ((reference or {}).get('ontology') or {}).get('@graph') or []:
        if not isinstance(node, dict):
            continue
        if bare(node.get('@id') or '') != bare_id:
            continue
        if want_types and not any(t in str(node.get('@type') or '') for t in want_types):
            continue
        if domain_bare is not None:
            domain = node.get('rdfs:domain')
            domain_id = domain.get('@id') if isinstance(domain, dict) else domain
            if domain_id and bare(domain_id) != domain_bare:
                continue
        return True
    return False


def _project_title(target_kind, target_id, state, reference):
    spec = assist_fields.resolve(target_kind)
    if not target_id:
        return '新建' + spec['label']
    bindings = state.get('bindings', {}).get('object_bindings') or []
    labels = _reference_labels(reference)
    workflow = (reference or {}).get('workflow') or {}
    if target_kind == 'identity':
        binding = _binding_of(bindings, target_id)
        if binding is not None:
            ot = str(binding.get('object_type') or target_id)
            return '对象「' + labels.get(ot, ot) + '」的实例识别'
        if _ref_def_exists(reference, target_id, ('owl:Class',)):
            return '对象「' + labels.get(target_id, target_id) + '」的实例识别（未配置）'
        raise NotFound('编辑目标不存在或不可见：' + target_id)
    if target_kind == 'propertySource':
        ot, _, prop = target_id.partition('.')
        binding = _binding_of(bindings, ot)
        if binding is not None and prop in (binding.get('properties') or {}):
            return '属性「' + labels.get(ot, ot) + '.' + prop + '」的取值来源'
        if _ref_def_exists(reference, prop, ('Property',), domain_bare=ot):
            return '属性「' + labels.get(ot, ot) + '.' + prop + '」的取值来源（未配置）'
        raise NotFound('编辑目标不存在或不可见：' + target_id)
    if target_kind == 'linkMapping':
        ot, _, relation = target_id.partition('.')
        binding = _binding_of(bindings, ot)
        rows = (binding.get('relations') or []) if isinstance(binding, dict) else []
        if any(isinstance(r, dict) and str(r.get('relation') or '') == relation for r in rows):
            return '链接「' + relation + '」的映射（' + labels.get(ot, ot) + '）'
        if _ref_def_exists(reference, relation, ('owl:ObjectProperty',), domain_bare=ot):
            return '链接「' + relation + '」的映射（' + labels.get(ot, ot) + '）（未配置）'
        raise NotFound('编辑目标不存在或不可见：' + target_id)
    if target_kind == 'actionBinding':
        if target_id == 'actionBindings':
            return '动作接口映射'
        oid, _, aid = target_id.partition(':')
        rows = state.get('bindings', {}).get('actionBindings') or []
        if isinstance(rows, dict):
            rows = [dict(v or {}, objectTypeId=k.split(':', 1)[0], actionId=k.split(':', 1)[-1])
                    for k, v in rows.items()]  # 兼容历史上按组合键存 dict 的形态
        hit = next((r for r in rows if isinstance(r, dict)
                    and str(r.get('objectTypeId') or '') == oid
                    and str(r.get('actionId') or '') == aid), None)
        action_name = next((str(a.get('name') or aid) for a in (workflow.get('actions') or [])
                            if isinstance(a, dict) and a.get('id') == aid), aid)
        if hit is not None:
            return '动作「' + action_name + '」的接口映射（' + labels.get(oid, oid) + '）'
        if (any(isinstance(a, dict) and a.get('id') == aid for a in (workflow.get('actions') or []))
                and _ref_def_exists(reference, oid, ('owl:Class',))):
            return '动作「' + action_name + '」的接口映射（' + labels.get(oid, oid) + '）（未配置）'
        raise NotFound('编辑目标不存在或不可见：' + target_id)
    raise ValueError('未知的辅助填写目标类型：' + str(target_kind))


# --- 场景候选装配（返回同种类分组列表；每类独立裁剪 ≤60） ---------------------------

def _ontology_candidates(target_kind, bundle):
    state = bundle['state']
    graph = (state.get('ontology') or {}).get('@graph') or []
    objects, properties, shared, links = _ontology_definitions(graph)
    rules, actions = _workflow_definitions(state.get('workflow'))
    if target_kind == 'object':
        return [objects]
    if target_kind in ('property', 'sharedProperty'):
        return [objects, properties, shared]
    if target_kind == 'link':
        return [objects, links]
    if target_kind == 'rule':
        return [objects, rules]
    if target_kind == 'action':
        return [objects, actions]
    raise ValueError('未知的辅助填写目标类型：' + str(target_kind))


def _project_candidates(target_kind, draft_kind, authority):
    state = authority['state']
    connections = (state.get('connections') or {}).get('connections') or []
    reference = authority['reference']
    ref_graph = ((reference or {}).get('ontology') or {}).get('@graph') or []
    objects, properties, shared, links = _ontology_definitions(ref_graph)
    _rules, actions = _workflow_definitions((reference or {}).get('workflow'))
    catalog, catalog_truncated = _catalog_entries(authority['catalogs'])
    flows, flows_truncated = _flow_payload(authority['flows'])

    if target_kind == 'identity':
        groups = [objects, _connection_definitions(connections, engines=('mysql',)),
                  _source_definitions(state)]
    elif target_kind == 'propertySource':
        if draft_kind == 'field':
            groups = [objects, properties, _source_definitions(state)]
        elif draft_kind == 'database':
            groups = [objects, properties, _connection_definitions(connections)]
        elif draft_kind == 'redis':
            groups = [_connection_definitions(connections)]  # params 绑定 identity 字段 → 仍附目录
        elif draft_kind == 'flow':
            groups = [objects, properties]
            catalog, catalog_truncated = [], False  # 流来源字段候选在 flows 签名里
        else:
            raise ValueError('未知的属性取值来源类型：' + str(draft_kind))
    elif target_kind == 'linkMapping':
        groups = [objects, links, _source_definitions(state)]
    elif target_kind == 'actionBinding':
        groups = [actions, objects, properties, shared]
        flows, flows_truncated = [], False
    else:
        raise ValueError('未知的辅助填写目标类型：' + str(target_kind))
    return groups, catalog, flows, catalog_truncated, flows_truncated


# --- 主入口 -------------------------------------------------------------------------

def build_context(space, project_id, target_kind, target_id, purpose, draft,
                  ontology_id='storage'):
    """构建 §5.1 响应与令牌载荷；返回 (payload, token_payload)。

    payload：{'contextToken', 'contextFingerprint', 'context'}（contextToken 已签名，
    T2 路由直接返回）；token_payload：签入令牌的字段 dict（uid/space/projectId/
    targetKind/targetId/fp/dh/exp/purpose），供测试与 check_generate 使用。
    只读：不写任何修订、不持全局写锁、不产生存储变更。
    """
    if space not in ('ontology', 'project'):
        raise ValueError('space 必须为 ontology 或 project')
    if purpose not in _PURPOSES:
        raise ValueError('purpose 必须为 fill、check 或 explain')
    spec = assist_fields.resolve(target_kind)
    if spec['space'] != space:
        raise ValueError('目标类型与工作区不匹配：' + str(target_kind))
    cleaned, draft_kind = assist_fields.normalize_draft(target_kind, draft)
    target_id = str(target_id or '').strip()
    uid = auth.require_user_id()

    if space == 'ontology':
        bundle = _ontology_bundle(ontology_id)
        fingerprint = assist_fields.canonical_hash({'kind': 'ontology', 'id': bundle['id'],
                                                    'rev': bundle['revision']})
        title = _ontology_title(target_kind, target_id, bundle['state'])
        groups = _ontology_candidates(target_kind, bundle)
        catalog, flows = [], []
        catalog_truncated = flows_truncated = False
    else:
        if not str(project_id or '').strip():
            raise ValueError('项目区必须提供 projectId')
        authority = _project_authority(project_id)
        fingerprint = _project_fingerprint(authority)
        title = _project_title(target_kind, target_id, authority['state'], authority['reference'])
        (groups, catalog, flows,
         catalog_truncated, flows_truncated) = _project_candidates(target_kind, draft_kind, authority)

    definitions, definitions_truncated = _merge_groups(groups)

    context = {'targetKind': target_kind,
               'title': title,
               'editableFields': assist_fields.editable_fields_payload(target_kind, draft_kind),
               'definitions': definitions,
               'catalog': catalog,
               'flows': flows,
               'modelReady': _model_ready(),
               'definitionsTruncated': definitions_truncated,
               'catalogTruncated': catalog_truncated,
               'flowsTruncated': flows_truncated}

    token_payload = {'uid': uid,
                     'space': space,
                     'projectId': str(project_id or ''),
                     'targetKind': target_kind,
                     'targetId': target_id,
                     'fp': fingerprint,
                     'dh': assist_fields.canonical_hash(cleaned),
                     'exp': time.time() + assist_fields.TOKEN_TTL,
                     'purpose': purpose, 'ontologyId': str(ontology_id or 'storage')}
    return {'contextToken': sign_token(token_payload),
            'contextFingerprint': fingerprint,
            'context': context}, token_payload


def check_generate(token_payload, space, project_id, target_kind, target_id, draft,
                   expected_fingerprint_fn, ontology_id='storage'):
    """assist-generate 前置校验（§5.2）：uid/目标/draft 摘要/权威指纹，任一不符 ContextStale。

    `expected_fingerprint_fn`：注入的当前权威指纹重算函数（测试可替换；生产传
    `lambda: compute_fingerprint(space, project_id)`）。返回规范化后的 draft，
    供后续模式处理直接使用。draft 白名单违规仍抛 ValueError（400 语义）。
    """
    if not isinstance(token_payload, dict):
        raise ContextStale('contextToken 无效')
    uid = auth.require_user_id()
    if str(token_payload.get('uid') or '') != str(uid):
        raise ContextStale('contextToken 属于其他账号，请重新获取上下文')
    if (str(token_payload.get('space') or '') != space
            or str(token_payload.get('projectId') or '') != str(project_id or '')
            or str(token_payload.get('targetKind') or '') != target_kind
            or str(token_payload.get('targetId') or '') != str(target_id or '').strip()
            or str(token_payload.get('ontologyId') or 'storage') != str(ontology_id or 'storage')):
        raise ContextStale('编辑目标已变化，请重新获取上下文')
    cleaned, _draft_kind = assist_fields.normalize_draft(target_kind, draft)
    if assist_fields.canonical_hash(cleaned) != str(token_payload.get('dh') or ''):
        raise ContextStale('编辑内容已变化，请重新获取上下文')
    if str(token_payload.get('fp') or '') != str(expected_fingerprint_fn()):
        raise ContextStale('权威状态已变化，请重新获取上下文')
    return cleaned

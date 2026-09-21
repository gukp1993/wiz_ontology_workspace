"""从物料构建本体：候选评审域 —— 编辑、决定、合并/撤销与交付阻断检查。

边界（与 tasks.py / materials.py 一致）：
* 所有函数在调用方的写事务里执行（传 conn），自身不开事务、不提交、不持有全局写锁；
* 查询一律带 owner() = auth.require_user_id()；跨账号与不存在同样处理 → store 返回 None
  → 抛 sto.NotFound，不泄露他账号数据；
* 结构校验是确定性的（validate_candidate），不调用模型、不猜单位、不默认数量关系；
* 字段差异只展示与提示，绝不静默采用被合并项的取值；弱证据转拟纳入必须留人工理由；
* 合并与撤销都会追加 review_op（kind='merge'，payload 带操作前后的可逆状态）；
  op 与候选写共用同一时间戳，撤销用 candidate.updated_at > op.created_at 判定
  「合并之后又有编辑」，绝不静默覆盖。

返回结构与接口文档 08 §7 对齐：单条候选操作返回 Candidate 视图（路由包一层
{"candidate": …}）；merge_apply / undo_review_op 返回响应体；merge_preview 返回 preview 体。
"""
import json

from workbench import auth
from workbench.ontology_build import protocol
from workbench.storage import engine as sto
from workbench.storage import ontology_build as store

# update_candidate 允许编辑的字段：name/definition/ownerKey 是列，其余进 fields_json。
_COLUMN_FIELDS = ('name', 'definition', 'ownerKey')
_TYPE_FIELDS = ('dataType', 'valueType', 'content', 'effect', 'cardinality')
EDITABLE_FIELDS = _COLUMN_FIELDS + _TYPE_FIELDS

# 合并后的候选是否已有后续人工编辑由 updated_at 判定，字段展示顺序固定便于前端对比。
_FIELD_ORDER = ('name', 'definition', 'ownerKey', 'dataType', 'valueType', 'sourceRef',
                'targetRef', 'cardinality', 'content', 'effect')

_DEPENDENCY_BLOCKED = 'DEPENDENCY_NOT_INCLUDED'
# 宿主/端点解析不到的可定位阻断码（R3-03）：复用 08 分册既有阻断码集合，
# 语义为「属性/规则/动作所属对象存在且未被排除」——交付侧不新增未登记码。
_OWNER_UNRESOLVED = 'PROPERTY_OWNER_UNRESOLVED'


class InvalidStateError(ValueError):
    """评审语义阻断（D17）：HTTP 422 `INVALID_STATE` + `issues`（server.py 按属性映射）。

    与形态错误（ValueError → 400）区分：证据不足转纳入缺理由、合并类型/数据类型
    不兼容、已合并候选再操作等都是**状态**问题，不是请求字段格式问题。
    消息保持中文可读，issues 里带同一条便于前端逐条定位。
    """

    code = 'INVALID_STATE'
    status = 422

    def __init__(self, message, field='', issue_code=''):
        self.issues = [{'code': issue_code or self.code, 'field': field, 'message': message}]
        super().__init__(message)


def owner() -> str:
    return auth.require_user_id()


# --- 读取 -------------------------------------------------------------------------

def candidate_detail(conn, candidate_id) -> dict:
    """候选详情：候选 + 按字段分组的证据（事实已不存在的位置不展示，绝不虚构）。"""
    owner_id = owner()
    row = store.get_candidate(conn, candidate_id, owner_id)
    if row is None:
        raise sto.NotFound('候选定义不存在')
    view = store.candidate_view(row)
    evidence = view.get('evidence') if isinstance(view.get('evidence'), dict) else {}
    fact_ids = []
    for field_ids in evidence.values():
        for fact_id in _ids_of(field_ids):
            if fact_id not in fact_ids:
                fact_ids.append(fact_id)
    facts = store.facts_by_ids(conn, view['taskId'], owner_id, fact_ids)
    materials = {item['id']: item['relPath']
                 for item in store.list_materials(conn, view['taskId'], owner_id)}
    groups = []
    for field, field_ids in evidence.items():
        items = []
        for fact_id in _ids_of(field_ids):
            fact = facts.get(fact_id)
            if fact is None:
                continue  # 证据位置已删除：宁缺勿造
            items.append({'factId': fact['id'], 'locator': fact.get('locator') or {},
                          'snippet': fact.get('snippet') or '', 'quality': fact.get('quality') or '',
                          'materialRelPath': materials.get(fact.get('materialId'), '')})
        groups.append({'field': field, 'items': items})
    return {'candidate': view, 'evidence': groups}


# --- 编辑与决定 -------------------------------------------------------------------

def update_candidate(conn, candidate_id, fields, revision) -> dict:
    """人工编辑候选字段（只允许契约内字段），写回前重算结构 issues。

    fields 允许 name/definition/dataType/valueType/content/effect/cardinality/ownerKey；
    未知键抛 ValueError；dataType 限 protocol.PROPERTY_DATA_TYPES（**平铺字符串**，
    不接受嵌套对象）；valueType 必须 ∈ protocol.VALUE_TYPES（= model_format 的
    SERIES_VALUE_TYPES，与交付侧同一枚举，非法值在这里就拒绝，不等交付才报）；
    cardinality 必须是 {source, target} 且取值 ∈ {one, many}。CAS 用 revision。
    """
    owner_id = owner()
    if not isinstance(fields, dict):
        raise ValueError('缺少要修改的字段')
    for key in fields:
        if key not in EDITABLE_FIELDS:
            raise ValueError('不支持的字段：%s' % key)
    row = store.get_candidate(conn, candidate_id, owner_id)
    if row is None:
        raise sto.NotFound('候选定义不存在')
    view = store.candidate_view(row)
    updated_fields = dict(view.get('fields') or {})
    columns = {}
    for key, value in fields.items():
        if key in _COLUMN_FIELDS:
            columns[key] = str(value or '').strip()
        elif key == 'dataType':
            if isinstance(value, dict):
                raise ValueError('dataType 需要平铺字符串（如 "timeSeries"）；'
                                 '观测值类型请用平铺的 valueType 字段，可选：%s'
                                 % '/'.join(protocol.VALUE_TYPES))
            if isinstance(value, (list, tuple)):
                raise ValueError('dataType 需要平铺字符串（如 "timeSeries"）')
            text = str(value or '').strip()
            if text not in protocol.PROPERTY_DATA_TYPES:
                raise ValueError('不支持的数据类型：%s（可选：%s）'
                                 % (text or '空', '/'.join(protocol.PROPERTY_DATA_TYPES)))
            updated_fields['dataType'] = text
        elif key == 'valueType':
            if isinstance(value, dict):
                raise ValueError('valueType 需要平铺字符串，可选：%s'
                                 % '/'.join(protocol.VALUE_TYPES))
            text = str(value or '').strip()
            if not text:
                updated_fields.pop('valueType', None)
            elif text not in protocol.VALUE_TYPES:
                raise ValueError('时间序列观测值类型「%s」不合法；可选：%s'
                                 % (text, '/'.join(protocol.VALUE_TYPES)))
            else:
                updated_fields['valueType'] = text
        elif key == 'cardinality':
            if value is None or value == '':
                updated_fields.pop('cardinality', None)
            else:
                updated_fields['cardinality'] = _clean_cardinality(value)
        else:  # content / effect：选填文本，允许清空
            updated_fields[key] = str('' if value is None else value).strip()
    prospective = dict(view)
    prospective.update(columns)
    prospective['fields'] = updated_fields
    issues = validate_candidate(conn, view['taskId'], prospective)
    fresh = store.update_candidate(conn, candidate_id, owner_id, expected_revision=revision,
                                   name=prospective.get('name'), definition=prospective.get('definition'),
                                   fields=updated_fields, issues=issues,
                                   owner_key=prospective.get('ownerKey'))
    return store.candidate_view(fresh)


def decide(conn, candidate_id, decision, reason, revision) -> dict:
    """人工决定：include/defer/exclude；弱证据（非 supported）转 include 必须给理由。"""
    owner_id = owner()
    value = str(decision or '').strip()
    if value not in protocol.DECISIONS:
        raise ValueError('不支持的决定：%s' % (value or '空'))
    row = store.get_candidate(conn, candidate_id, owner_id)
    if row is None:
        raise sto.NotFound('候选定义不存在')
    view = store.candidate_view(row)
    text = str(reason or '').strip()
    status = view.get('evidenceStatus') or ''
    if value == 'include' and status != 'supported' and not text:
        label = protocol.EVIDENCE_STATUS_LABELS.get(status, status or '未确认')
        raise InvalidStateError('证据状态为「%s」，转为拟纳入前请填写人工确认理由' % label,
                                field='reason', issue_code='REASON_REQUIRED')
    fresh = store.update_candidate(conn, candidate_id, owner_id, expected_revision=revision,
                                   decision=value, reason=text, reviewed=True)
    return store.candidate_view(fresh)


# --- 合并与撤销 -------------------------------------------------------------------

def merge_preview(conn, task_id, primary_id, merge_ids) -> dict:
    """合并预览：字段差异、合并后证据数、关联变化提示；不写库。

    仅同类型可合并；property 的 dataType 不同、link 的基数角色冲突一律 ValueError。
    """
    _owner_id, primary, merges, pool = _merge_plan(conn, task_id, primary_id, merge_ids)
    fields = _diff_fields(primary, merges)
    evidence_ids = []
    for node in [primary] + merges:
        for fact_id in _evidence_ids(node):
            if fact_id not in evidence_ids:
                evidence_ids.append(fact_id)
    warnings = []
    for item in fields:
        if item['diff']:
            merged = ' / '.join(_text(value) for value in item['mergeValues'])
            warnings.append('字段「%s」存在差异：保留项为「%s」，被合并项为「%s」；合并后仍采用保留项的值'
                            % (item['name'], _text(item['primaryValue']), merged or '（空）'))
    for node in merges:
        if primary['type'] == 'link' and _cardinality(node.get('fields') or {}) and \
                not _cardinality(primary.get('fields') or {}):
            warnings.append('保留项「%s」的数量关系尚未确认，被合并项为「%s」；请人工确认'
                            % (primary['name'] or '未命名',
                               _text(_cardinality(node['fields']))))
    warnings.extend(_reference_warnings(pool, primary, merges))
    return {'fields': fields, 'evidenceCount': len(evidence_ids), 'warnings': warnings}


def merge_apply(conn, task_id, primary_id, merge_ids, revision) -> dict:
    """执行合并：保留项取证据并集、记录 mergedFrom，被合并项 origin.mergedInto 指向保留项。

    字段差异不自动采用（保留项字段不变）；写 review_op(kind='merge') 供撤销。CAS 用 revision。
    """
    owner_id, primary, merges, _pool = _merge_plan(conn, task_id, primary_id, merge_ids)
    merged_ids = [node['id'] for node in merges]
    evidence = dict(primary.get('evidence') or {})
    for node in merges:
        for field, field_ids in (node.get('evidence') or {}).items():
            bucket = list(evidence.get(field) or [])
            for fact_id in _ids_of(field_ids):
                if fact_id not in bucket:
                    bucket.append(fact_id)
            evidence[field] = bucket
    origin = dict(primary.get('origin') or {})
    merged_from = list(origin.get('mergedFrom') or [])
    for merged_id in merged_ids:
        if merged_id not in merged_from:
            merged_from.append(merged_id)
    origin['mergedFrom'] = merged_from
    prospective = dict(primary)
    prospective['evidence'] = evidence
    prospective['origin'] = origin
    issues = validate_candidate(conn, task_id, prospective)
    before = _snapshot(primary)
    after = dict(before)
    after.update({'evidence': evidence, 'origin': origin, 'issues': issues})
    now = sto.utcnow()
    store.update_candidate(conn, primary_id, owner_id, expected_revision=revision,
                           origin=origin, issues=issues, now=now)
    _write_evidence(conn, primary_id, owner_id, evidence, now)
    for node in merges:
        node_origin = dict(node.get('origin') or {})
        node_origin['mergedInto'] = primary_id
        store.update_candidate(conn, node['id'], owner_id, origin=node_origin, now=now)
    op_id = store.append_review_op(conn, task_id, owner_id, 'merge',
                                   {'primaryId': primary_id, 'mergeIds': merged_ids,
                                    'before': before, 'after': after}, now=now)
    fresh = store.get_candidate(conn, primary_id, owner_id)
    return {'candidate': store.candidate_view(fresh), 'opId': op_id}


def undo_review_op(conn, task_id, op_id, revision) -> dict:
    """撤销合并：恢复保留项的先前字段与证据，清空被合并项的 mergedInto。

    已撤销 → ValueError；保留项在合并之后有新的编辑（updated_at 晚于 op.created_at）
    → RevisionConflict（说明影响，不静默丢弃后写入）。
    """
    owner_id = owner()
    op = store.get_review_op(conn, op_id, owner_id)
    if op is None or op['task_id'] != task_id:
        raise sto.NotFound('评审操作不存在')
    if op['kind'] != 'merge':
        raise ValueError('该操作不支持撤销')
    if op['reverted']:
        raise ValueError('该操作已撤销，不能重复撤销')
    payload = op.get('payload') if isinstance(op.get('payload'), dict) else {}
    primary_id = str(payload.get('primaryId') or '')
    before = payload.get('before')
    if not primary_id or not isinstance(before, dict):
        raise ValueError('该操作的撤销信息不完整，无法撤销')
    row = store.get_candidate(conn, primary_id, owner_id)
    if row is None:
        raise sto.NotFound('候选定义不存在')
    current = row['revision']
    if revision not in (None, '') and str(revision) != str(current):
        raise sto.RevisionConflict(current_revision=current,
                                   message='候选定义已被其他操作更新，请刷新后重试')
    if str(row['updated_at']) > str(op['created_at']):
        raise sto.RevisionConflict(current_revision=current,
                                   message='保留项在合并之后有新的编辑，撤销会覆盖这些修改；请先核对再撤销')
    now = sto.utcnow()
    store.update_candidate(conn, primary_id, owner_id, expected_revision=current,
                           name=before.get('name'), definition=before.get('definition'),
                           fields=before.get('fields') or {}, origin=before.get('origin') or {},
                           decision=before.get('decision'), reviewed=bool(before.get('reviewed')),
                           reason=before.get('reason'), issues=before.get('issues') or [],
                           owner_key=before.get('ownerKey'), now=now)
    _write_evidence(conn, primary_id, owner_id, before.get('evidence') or {}, now)
    for merged_id in payload.get('mergeIds') or []:
        merged_id = str(merged_id or '')
        if not merged_id:
            continue
        node = store.get_candidate(conn, merged_id, owner_id)
        if node is None:
            continue
        node_origin = dict(store.candidate_view(node).get('origin') or {})
        if str(node_origin.get('mergedInto') or '') != primary_id:
            continue
        node_origin['mergedInto'] = None
        store.update_candidate(conn, merged_id, owner_id, origin=node_origin, now=now)
    store.mark_review_op_reverted(conn, op_id, owner_id)
    fresh = store.get_candidate(conn, primary_id, owner_id)
    return {'ok': True, 'candidate': store.candidate_view(fresh)}


# --- 结构校验与交付阻断 -----------------------------------------------------------

def validate_candidate(conn, task_id, candidate) -> list:
    """候选结构校验（确定性）：返回 issues [{code, message, field}]。

    检查名称/定义非空、property 数据类型合法、timeSeries 有观测值类型、
    property 所属对象可解析、link 两端可解析（端点 key/ID 在任务内找不到 → 未解析）。
    """
    owner_id = owner()
    if not isinstance(candidate, dict):
        raise ValueError('候选结构无效')
    issues = []
    name = str(candidate.get('name') or '').strip()
    if not name:
        issues.append(_issue('NAME_REQUIRED', 'name', '名称不能为空'))
    if not str(candidate.get('definition') or '').strip():
        issues.append(_issue('DEFINITION_REQUIRED', 'definition', '业务定义不能为空'))
    fields = candidate.get('fields') if isinstance(candidate.get('fields'), dict) else {}
    by_id, by_key = _index(store.all_candidates(conn, task_id, owner_id, include_merged=True))
    ctype = str(candidate.get('type') or '')
    label = name or '未命名候选'
    if ctype == 'property':
        data_type = str(fields.get('dataType') or '').strip()
        if data_type not in protocol.PROPERTY_DATA_TYPES:
            issues.append(_issue('DATA_TYPE_INVALID', 'dataType',
                                 '属性「%s」的数据类型不受支持：%s' % (label, data_type or '空')))
        elif data_type == 'timeSeries':
            value_type = str(fields.get('valueType') or '').strip()
            if not value_type:
                issues.append(_issue('OBSERVATION_VALUE_TYPE_MISSING', 'valueType',
                                     '时间序列属性「%s」缺少观测值类型' % label))
            elif value_type not in protocol.VALUE_TYPES:
                issues.append(_issue('OBSERVATION_VALUE_TYPE_INVALID', 'valueType',
                                     '时间序列属性「%s」的观测值类型「%s」不在枚举内（可选：%s）'
                                     % (label, value_type, '/'.join(protocol.VALUE_TYPES))))
        owner_ref = str(candidate.get('ownerKey') or '').strip()
        node = _resolve(owner_ref, by_id, by_key)
        if node is None or node.get('type') != 'object':
            issues.append(_issue('PROPERTY_OWNER_UNRESOLVED', 'ownerKey',
                                 '属性「%s」的所属对象「%s」在本次任务中不存在'
                                 % (label, owner_ref or '未指定')))
    elif ctype == 'link':
        for slot, role in (('sourceRef', '源端'), ('targetRef', '目标端')):
            ref = str(fields.get(slot) or '').strip()
            node = _resolve(ref, by_id, by_key)
            if node is None or node.get('type') != 'object':
                issues.append(_issue('LINK_ENDPOINT_UNRESOLVED', slot,
                                     '链接「%s」的%s对象「%s」无法解析到本次任务中的对象'
                                     % (label, role, ref or '未指定')))
        cardinality = fields.get('cardinality')
        if cardinality not in (None, '') and not _is_cardinality(cardinality):
            issues.append(_issue('CARDINALITY_INVALID', 'cardinality',
                                 '链接「%s」的数量关系必须是 {source, target} 且取值为 one/many' % label))
    return issues


def deliver_blockers(conn, task_id, batch_id=None) -> dict:
    """交付前的依赖阻断：拟纳入的属性/链接/规则/动作，其宿主或端点未纳入即逐条报错。

    返回 {'selected': {type: 数量}, 'issues': [{code, message, candidateId}],
    'excludedItems': [{candidateId, type, name, decision}]}；已合并候选不参与选定集，
    但**引用被合并候选的候选**沿 origin.mergedInto 解析到保留项后再判断依赖
    （合并链一并跟随；撤销合并后 mergedInto 清空，引用回到被合并候选本身）。

    R3-03：非空引用解析不到（对象已合并后撤销、被删除或引用不存在）同样产出
    **可定位** issue（candidateId），绝不静默放过；属性/链接的同类问题由
    validate_candidate 报告（delivery._blocking_issues 按码并入，不在这里重复），
    规则/动作没有那条校验路径，在这里补齐，避免「预检通过、交付 422」的分裂。
    """
    owner_id = owner()
    if store.require_task(conn, task_id, owner_id) is None:
        raise sto.NotFound('生成任务不存在')
    pool = store.all_candidates(conn, task_id, owner_id, include_merged=True)
    scoped = [node for node in pool if not batch_id or node['batchId'] == batch_id]
    selected = [node for node in scoped
                if node['decision'] == 'include' and not node['origin'].get('mergedInto')]
    by_id, by_key = _index(pool, batch_id or '')
    counts = {ctype: 0 for ctype in protocol.CANDIDATE_TYPES}
    issues = []
    for node in selected:
        counts[node['type']] = counts.get(node['type'], 0) + 1
        for slot, ref, role in _relation_refs(node):
            target = _resolve(ref, by_id, by_key)
            # 合并链走到头仍停在「已被合并」的候选上（悬空/成环）时同样不可交付：
            # 解析方返回的是被合并候选本身，validate_candidate 看不出这种情况。
            chain_broken = bool(target is not None
                                and str((target.get('origin') or {}).get('mergedInto') or ''))
            if target is None or target.get('type') != 'object' or chain_broken:
                # 端点缺失：属性/链接由 validate_candidate 报（并入交付阻断清单），
                # 规则/动作没有那条校验路径，在此补报可定位 issue
                # （非空引用才报；空字符串 = 未声明宿主，允许不挂关联）。
                if str(ref or '').strip() and (node['type'] in ('rule', 'action') or chain_broken):
                    issues.append({'code': _OWNER_UNRESOLVED, 'candidateId': node['id'],
                                   'message': '%s「%s」的%s「%s」无法解析到本次交付的对象'
                                              '（未纳入、合并已撤销或合并链缺失），请先纳入该对象'
                                              '或清除该引用'
                                              % (protocol.TYPE_LABELS.get(node['type'], node['type']),
                                                 node['name'] or '未命名', role, ref)})
                continue
            if target['decision'] not in ('exclude', 'defer'):
                continue
            issues.append({'code': _DEPENDENCY_BLOCKED, 'candidateId': node['id'],
                           'message': '%s「%s」的%s「%s」当前为「%s」，请先纳入该对象，或暂缓/排除此项'
                                      % (protocol.TYPE_LABELS.get(node['type'], node['type']),
                                         node['name'] or '未命名', role,
                                         target['name'] or '未命名',
                                         protocol.DECISION_LABELS.get(target['decision'],
                                                                      target['decision']))})
    excluded = [{'candidateId': node['id'], 'type': node['type'], 'name': node['name'],
                 'decision': node['decision']} for node in scoped
                if node['decision'] != 'include' and not node['origin'].get('mergedInto')]
    return {'selected': counts, 'issues': issues, 'excludedItems': excluded}


# --- 内部辅助 ---------------------------------------------------------------------

def _issue(code, field, message):
    return {'code': code, 'field': field, 'message': message}


def _ids_of(value):
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or '').strip()]


def _evidence_ids(node):
    evidence = node.get('evidence') if isinstance(node.get('evidence'), dict) else {}
    out = []
    for field_ids in evidence.values():
        for fact_id in _ids_of(field_ids):
            if fact_id not in out:
                out.append(fact_id)
    return out


def _is_cardinality(value):
    return isinstance(value, dict) and set(value) == {'source', 'target'} and \
        all(str(value.get(role) or '') in ('one', 'many') for role in ('source', 'target'))


def _clean_cardinality(value):
    if not isinstance(value, dict):
        raise ValueError('数量关系必须是 {source, target} 对象')
    if set(value) != {'source', 'target'}:
        raise ValueError('数量关系必须同时给出 source 与 target')
    out = {}
    for role in ('source', 'target'):
        text = str(value.get(role) or '').strip()
        if text not in ('one', 'many'):
            raise ValueError('数量关系取值必须是 one 或 many')
        out[role] = text
    return out


def _cardinality(fields):
    value = (fields or {}).get('cardinality')
    return value if _is_cardinality(value) else None


def _relation_refs(node):
    """候选的对外引用：(槽位, 引用值, 角色说明)。

    规则/动作同样通过 ownerKey 关联宿主对象（D14，契约 08 §8）：排除宿主必须
    阻断其规则/动作交付，不能只盯属性与链接。
    """
    fields = node.get('fields') if isinstance(node.get('fields'), dict) else {}
    if node.get('type') == 'property':
        return [('ownerKey', str(node.get('ownerKey') or ''), '所属对象')]
    if node.get('type') == 'link':
        return [('sourceRef', str(fields.get('sourceRef') or ''), '源端对象'),
                ('targetRef', str(fields.get('targetRef') or ''), '目标端对象')]
    if node.get('type') in ('rule', 'action'):
        return [('ownerKey', str(node.get('ownerKey') or ''), '宿主对象')]
    return []


def _index(candidates, batch_id=''):
    """候选索引：同批次、未合并的候选优先占用 key；ID 恒唯一。"""
    by_id, by_key = {}, {}
    ordered = sorted(candidates or [], key=lambda node: (
        0 if (not batch_id or node.get('batchId') == batch_id) else 1,
        1 if (node.get('origin') or {}).get('mergedInto') else 0,
        str(node.get('id') or '')))
    for node in ordered:
        by_id.setdefault(node['id'], node)
        key = str(node.get('key') or '').strip()
        if key:
            by_key.setdefault(key, node)
    return by_id, by_key


def _resolve(reference, by_id, by_key):
    """解析候选引用（稳定 ID 或批内 key），并沿 mergedInto 跟随到保留项。"""
    ref = str(reference or '').strip()
    if not ref:
        return None
    node = by_id.get(ref) or by_key.get(ref)
    for _ in range(8):
        if node is None:
            return None
        target = str((node.get('origin') or {}).get('mergedInto') or '')
        if not target:
            return node
        following = by_id.get(target) or by_key.get(target)
        if following is None or following['id'] == node['id']:
            return node
        node = following
    return node


def _field_value(node, key):
    if key in _COLUMN_FIELDS:
        return node.get(key) or ''
    fields = node.get('fields') if isinstance(node.get('fields'), dict) else {}
    return fields.get(key)


def _diff_fields(primary, merges):
    """字段差异：固定顺序列出双方取值与差异标记（合并后采用保留项取值）。"""
    keys = {'name', 'definition'}
    for node in [primary] + merges:
        for key in ('name', 'definition', 'ownerKey'):
            if str(node.get(key) or '').strip():
                keys.add(key)
        fields = node.get('fields') if isinstance(node.get('fields'), dict) else {}
        for key, value in fields.items():
            if str(value or '').strip() or key == 'cardinality':
                keys.add(key)
    rows = []
    for key in sorted(keys, key=lambda item: (_FIELD_ORDER.index(item)
                                              if item in _FIELD_ORDER else len(_FIELD_ORDER), item)):
        primary_value = _field_value(primary, key)
        merge_values = [_field_value(node, key) for node in merges]
        rows.append({'name': key, 'primaryValue': primary_value, 'mergeValues': merge_values,
                     'diff': any(value != primary_value for value in merge_values)})
    return rows


def _merge_plan(conn, task_id, primary_id, merge_ids):
    """合并前置校验：同任务、同类型、数据类型/基数角色兼容；返回 (owner, 保留项, 被合并项, 全部候选)。

    语义不兼容一律 InvalidStateError（D17：422 INVALID_STATE + issues，不是 400）。
    """
    owner_id = owner()
    pool = store.all_candidates(conn, task_id, owner_id, include_merged=True)
    by_id = {node['id']: node for node in pool}
    primary = by_id.get(str(primary_id or ''))
    if primary is None:
        raise sto.NotFound('候选定义不存在')
    if primary['origin'].get('mergedInto'):
        raise InvalidStateError('候选定义「%s」已被合并，不能作为保留项'
                                % (primary['name'] or '未命名'),
                                field='primaryId', issue_code='ALREADY_MERGED')
    ordered = []
    for raw in merge_ids or []:
        merged_id = str(raw or '').strip()
        if not merged_id:
            continue
        if merged_id == primary['id']:
            raise InvalidStateError('保留项不能同时作为被合并项', field='mergeIds',
                                    issue_code='MERGE_TARGET_INVALID')
        if merged_id in ordered:
            continue
        ordered.append(merged_id)
    if not ordered:
        raise ValueError('请选择要合并的候选')
    merges = []
    for merged_id in ordered:
        node = by_id.get(merged_id)
        if node is None:
            raise sto.NotFound('候选定义不存在')
        if node['type'] != primary['type']:
            raise InvalidStateError('仅同类型候选可合并：「%s」与「%s」类型不同'
                                    % (primary['name'] or '未命名', node['name'] or '未命名'),
                                    field='mergeIds', issue_code='MERGE_TYPE_MISMATCH')
        if node['origin'].get('mergedInto'):
            raise InvalidStateError('候选「%s」已被合并，不能重复合并'
                                    % (node['name'] or '未命名'),
                                    field='mergeIds', issue_code='ALREADY_MERGED')
        if primary['type'] == 'property':
            left = str((primary.get('fields') or {}).get('dataType') or '')
            right = str((node.get('fields') or {}).get('dataType') or '')
            if left != right:
                raise InvalidStateError('数据类型不一致，不能合并', field='dataType',
                                        issue_code='MERGE_DATA_TYPE_MISMATCH')
        elif primary['type'] == 'link':
            left = _cardinality(primary.get('fields') or {})
            right = _cardinality(node.get('fields') or {})
            if left and right and left != right:
                raise InvalidStateError('链接基数角色不一致，不能合并', field='cardinality',
                                        issue_code='MERGE_CARDINALITY_MISMATCH')
        merges.append(node)
    return owner_id, primary, merges, pool


def _reference_warnings(pool, primary, merges):
    """关联变化提示：其它候选把被合并项当作链接/属性端点，且与保留项不同源时提示确认。"""
    merged_ids = {node['id'] for node in merges}
    merged_keys = {str(node.get('key') or '') for node in merges if node.get('key')}
    after = {}
    for node in pool:
        after[node['id']] = node
    lines = []
    for node in pool:
        if node['id'] in merged_ids or node['id'] == primary['id']:
            continue
        for _slot, ref, role in _relation_refs(node):
            if ref not in merged_ids and ref not in merged_keys:
                continue
            target = _resolve(ref, after, {str(item.get('key') or ''): item
                                           for item in pool if item.get('key')})
            lines.append('候选「%s」的%s引用了被合并的候选「%s」，合并后关联到「%s」，请确认仍正确'
                         % (node['name'] or '未命名', role, ref,
                            (target or {}).get('name') or primary['name'] or '未命名'))
    return lines


def _snapshot(node):
    """操作前状态（撤销所需）：可编辑列 + 证据 + origin。"""
    return {'name': node.get('name') or '', 'definition': node.get('definition') or '',
            'fields': dict(node.get('fields') or {}), 'ownerKey': node.get('ownerKey') or '',
            'evidence': dict(node.get('evidence') or {}), 'origin': dict(node.get('origin') or {}),
            'decision': node.get('decision') or 'defer', 'reviewed': bool(node.get('reviewed')),
            'reason': node.get('reason') or '', 'issues': list(node.get('issues') or [])}


def _write_evidence(conn, candidate_id, owner_id, evidence, now):
    """evidence_json 不在 store.update_candidate 的可写列内：同一事务内补写。

    不轮换 revision（与同事务的 CAS 写共用一次修订与 updated_at），中间态对外不可见。
    """
    conn.execute(sto.text('UPDATE wb_build_candidates SET evidence_json = :ev, updated_at = :now '
                          'WHERE candidate_id = :c AND owner_user_id = :o'),
                 {'ev': json.dumps(evidence or {}, ensure_ascii=False), 'now': now,
                  'c': candidate_id, 'o': owner_id or ''})


def _text(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value if value is not None else '').strip()
    return text[:60] + '…' if len(text) > 60 else text


# --- 再生成差异（跨批次对齐、人工修改保护；契约见接口文档 08 §7） -------------------

def build_diff(conn, task_id, owner_user_id, old_batch_id, new_batch_id):
    """比较新旧批次的候选：新增 / 变动 / 消失 / 保留 / 已排除保护。

    对齐键用候选的 alignedKey（同名同类型；属性还要求 dataType 与宿主对象一致），
    由生成管线在 alignment 阶段写入。**旧批次完全不改动**（只读回看）。

    本函数可能在 GET 只读连接上执行（D04）：**绝不写库**——排除保护（旧批次
    exclude → 新批次强制 defer + revived 标记）由生成管线在写事务内完成，
    这里只如实报告 `excludedProtected` 桶。
    """
    old_items = store.all_candidates(conn, task_id, owner_user_id, batch_id=old_batch_id) \
        if old_batch_id else []
    new_items = store.all_candidates(conn, task_id, owner_user_id, batch_id=new_batch_id)
    old_by_key = {str(c.get('alignedKey') or c['id']): c for c in old_items}
    new_by_key = {str(c.get('alignedKey') or c['id']): c for c in new_items}

    added, changed, removed, kept, protected = [], [], [], [], []
    for key, candidate in new_by_key.items():
        previous = old_by_key.get(key)
        if previous is None:
            added.append(candidate['id'])
            continue
        fields_changed = _changed_fields(previous, candidate)
        if fields_changed:
            manual_conflict = bool(previous['reviewed']) and _manually_edited(previous)
            changed.append({'id': candidate['id'], 'fieldsChanged': fields_changed,
                            'manualConflict': manual_conflict})
        else:
            kept.append(candidate['id'])
        if previous['decision'] == 'exclude':
            # 已排除候选不自动复活：生成写事务已把新批次同键候选强制 defer + 标记
            # revived（pipeline.inherit_manual_exclusions），此处只读汇报，绝不写库。
            protected.append(candidate['id'])
    for key, candidate in old_by_key.items():
        if key not in new_by_key:
            removed.append(candidate['id'])
    return {'against': old_batch_id or None, 'newBatch': new_batch_id,
            'buckets': {'added': added, 'changed': changed, 'removed': removed,
                        'kept': kept, 'excludedProtected': protected},
            'items': new_items}


def _changed_fields(previous, candidate):
    """人工可编辑字段的差异（名称/定义/类型相关字段/所属对象）。"""
    fields = []
    if (previous.get('name') or '') != (candidate.get('name') or ''):
        fields.append('name')
    if (previous.get('definition') or '') != (candidate.get('definition') or ''):
        fields.append('definition')
    if (previous.get('ownerKey') or '') != (candidate.get('ownerKey') or ''):
        fields.append('ownerKey')
    prev_fields = previous.get('fields') or {}
    new_fields = candidate.get('fields') or {}
    for key in sorted(set(prev_fields) | set(new_fields)):
        if prev_fields.get(key) != new_fields.get(key):
            fields.append(key)
    return fields


def _manually_edited(candidate):
    """该候选是否被人工编辑过：审阅过且有非自动来源的改动痕迹（reason 或非默认决定）。"""
    if candidate.get('reason'):
        return True
    origin = candidate.get('origin') or {}
    return bool(origin.get('mergedFrom'))


def resolve_diff(conn, task_id, candidate_id, choice, revision):
    """处理人工修改冲突：keepManual=保留旧批次的人工字段；acceptNew=采用新生成结果。

    keepManual（D11）：对齐键取候选行的 `aligned_key` 列（origin_json 里没有
    alignedKey，旧实现读错字段导致恒 400/错配）；对照批与 /api/build-diff 同口径
    （最近一个非本候选批次的批次）。**只覆盖旧批次中确实存在的取值**：旧候选
    未命名/未填的字段不得清空新候选，其余字段一律保留新批次内容。
    """
    if choice not in ('keepManual', 'acceptNew'):
        raise ValueError('choice 只能是 keepManual 或 acceptNew')
    owner_id = owner()
    candidate = store.get_candidate(conn, candidate_id, owner_user_id=owner_id)
    if candidate is None:
        raise sto.NotFound('候选定义不存在')
    if candidate['task_id'] != task_id:
        raise sto.NotFound('候选定义不存在')
    if choice == 'acceptNew':
        # 只清冲突标记，字段保持新批次内容（人工可继续编辑）
        origin = dict(_loads(candidate['origin_json'], {}))
        origin.pop('manualConflict', None)
        updated = store.update_candidate(conn, candidate_id, owner_id,
                                        expected_revision=revision or None, origin=origin)
    else:
        # keepManual：把旧批次同对齐键候选的**人工取值字段**复制过来（不张冠李戴）
        aligned = str(candidate['aligned_key'] or candidate['candidate_id'])
        old_batch = next((b['id'] for b in store.list_batches(conn, task_id, owner_id, limit=10)
                          if b['id'] != candidate['batch_id']), None)
        if not old_batch:
            raise ValueError('没有可对齐的旧批次')
        previous = next((c for c in store.all_candidates(conn, task_id, owner_id,
                                                          batch_id=old_batch)
                         if str(c.get('alignedKey') or c['id']) == aligned), None)
        if previous is None:
            raise ValueError('旧批次中没有可对齐的候选')
        merged_fields = dict(_loads(candidate['fields_json'], {}))
        for key, value in (previous.get('fields') or {}).items():
            if value not in (None, '', [], {}):
                merged_fields[key] = value
        columns = {}
        if str(previous.get('name') or '').strip():
            columns['name'] = previous['name']
        if str(previous.get('definition') or '').strip():
            columns['definition'] = previous['definition']
        if str(previous.get('ownerKey') or '').strip():
            columns['owner_key'] = previous['ownerKey']
        updated = store.update_candidate(conn, candidate_id, owner_id,
                                         expected_revision=revision or None,
                                         fields=merged_fields, reviewed=True,
                                         reason=previous.get('reason') or '', **columns)
    return store.candidate_view(updated)


def _loads(raw, fallback):
    try:
        value = json.loads(raw or '')
    except (ValueError, TypeError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback

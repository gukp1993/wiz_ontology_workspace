"""交付域：预检（结构性校验 + 依赖阻断）与原子创建新本体草稿。

原子性契约（开发计划 §4.4 / 接口文档 08 §8）：
同一写事务内完成「确认 owner 与未交付 → 幂等回执检查 → 重验基线与选定集合 →
名称查重 → 分配本体与定义 ID → 写完整新草稿 → 记录候选 ID 映射与任务来源 →
写回执 + 任务已交付」。任一步失败全部回滚：绝不出现空本体、也不出现
「任务已交付但没有草稿」。
不调用会自行提交的 workspaces.create；这里直接走 storage.ontology_build 的
事务内建资产函数（create_ontology_asset）。
"""
import hashlib

from workbench import auth, storage
from workbench.ontology_build import ontology_adapter as adapter
from workbench.ontology_build import protocol
from workbench.ontology_build import tasks as task_domain
from workbench.storage import engine as sto
from workbench.storage import ontology_build as store


class DeliveryBlocked(ValueError):
    """交付被确定性校验阻断（HTTP 422，带 issues）。"""

    def __init__(self, issues, message='交付前检查未通过'):
        self.issues = issues
        super().__init__(message)


class DuplicateOntologyName(ValueError):
    """新本体名称与当前账号已有资产重名（HTTP 409，不覆盖）。"""


def owner():
    return auth.require_user_id()


def _selected_candidates(conn, task_id, owner_id, batch_id):
    """选定集合：decision='include' 且未被合并掉的候选（跨账号/他任务不可见）。"""
    items = store.all_candidates(conn, task_id, owner_id, batch_id=batch_id)
    selected = [c for c in items if c['decision'] == 'include']
    return items, selected


def _blocking_issues(conn, task_id, owner_id, batch_id):
    """依赖与结构阻断（与评审页 deliver_blockers 同口径，交付时服务端重验）。"""
    from workbench.ontology_build import review
    blockers = review.deliver_blockers(conn, task_id, batch_id)
    issues = list(blockers.get('issues') or [])
    items, selected = _selected_candidates(conn, task_id, owner_id, batch_id)
    ids = {c['id'] for c in items}
    for candidate in selected:
        for issue in review.validate_candidate(conn, task_id, candidate):
            if issue['code'] in ('MISSING_NAME', 'MISSING_DEFINITION', 'INVALID_DATA_TYPE',
                                 'LINK_ENDPOINT_UNRESOLVED', 'PROPERTY_OWNER_UNRESOLVED',
                                 'LINK_CARDINALITY_MISSING', 'OBSERVATION_VALUE_TYPE_MISSING'):
                issues.append({'code': issue['code'], 'candidateId': candidate['id'],
                               'message': '%s：%s' % (candidate['name'], issue['message'])})
    # 端点引用必须指向本批候选（合并后可能只剩主候选，端点使用已合并键也算合法）
    for candidate in selected:
        if candidate['type'] != 'link':
            continue
        fields = candidate.get('fields') or {}
        for endpoint in ('sourceRef', 'targetRef'):
            token = str(fields.get(endpoint) or '')
            if token and token not in ids and not any(c['key'] == token for c in items):
                issues.append({'code': 'LINK_ENDPOINT_UNRESOLVED', 'candidateId': candidate['id'],
                               'message': '链接「%s」的%s无法解析到本批候选' % (candidate['name'], endpoint)})
    return issues


def precheck(conn, task_id, batch_id=None):
    """交付前检查：计数、阻断项、未纳入项、覆盖缺口与 checkToken。"""
    owner_id = owner()
    row = store.require_task(conn, task_id, owner_id)
    if row is None:
        raise sto.NotFound('生成任务不存在')
    delivery = store.get_delivery(conn, task_id, owner_id)
    if delivery:
        raise DeliveryBlocked([{'code': 'ALREADY_DELIVERED',
                                'message': '该任务已交付本体（%s），一个任务只能创建一次' % delivery['ontologyId']}],
                              '该任务已交付')
    batch = store.get_batch(conn, batch_id, owner_id) if batch_id else store.latest_batch(conn, task_id, owner_id)
    if batch is None:
        raise DeliveryBlocked([{'code': 'NO_BATCH', 'message': '尚未生成候选，请先确认范围并生成'}], '尚未生成候选')
    if batch['stale']:
        raise DeliveryBlocked([{'code': 'STALE_RESULT',
                                'message': '材料或范围已变化，当前结果已过期；请重新确认范围并生成后再创建'}],
                              '结果已过期')
    batch_id = batch['batch_id']
    items, selected = _selected_candidates(conn, task_id, owner_id, batch_id)
    counts = {}
    for candidate in selected:
        counts[candidate['type']] = counts.get(candidate['type'], 0) + 1
    issues = _blocking_issues(conn, task_id, owner_id, batch_id)
    deferred = [c for c in items if c['decision'] == 'defer']
    excluded = [c for c in items if c['decision'] == 'exclude']
    scope = store.get_scope(conn, task_id, owner_id)
    coverage = {'materials': len([m for m in store.list_materials(conn, task_id, owner_id)
                                  if not m['excluded']]),
                'failedSegments': sum(len((m['coverage'] or {}).get('failedSegments') or [])
                                      for m in store.list_materials(conn, task_id, owner_id)),
                'notes': [n for m in store.list_materials(conn, task_id, owner_id)
                          for n in ((m['coverage'] or {}).get('notes') or [])][:20]}
    token = adapter.make_check_token(batch_id, [c['id'] for c in selected], scope['revision'],
                                     store.require_task(conn, task_id, owner_id)['material_revision'])
    return {
        'ok': not issues and bool(selected),
        'counts': counts,
        'issues': issues,
        'excluded': len(excluded),
        'deferred': len(deferred),
        'coverage': coverage,
        'checkToken': token,
        'batchId': batch_id,
        'notIncluded': [{'id': c['id'], 'name': c['name'], 'type': c['type'],
                         'decision': c['decision']} for c in items if c['decision'] != 'include'],
        'selectedIds': [c['id'] for c in selected],
    }


def prepare_payload(conn, task_id, batch_id=None):
    """构造编辑器态 ontology（JSON-LD @graph）与 ID 映射（事务内纯计算；不写库）。"""
    owner_id = owner()
    batch = store.get_batch(conn, batch_id, owner_id) if batch_id else store.latest_batch(conn, task_id, owner_id)
    if batch is None:
        raise DeliveryBlocked([{'code': 'NO_BATCH', 'message': '尚未生成候选'}], '尚未生成候选')
    items, selected = _selected_candidates(conn, task_id, owner_id, batch['batch_id'])
    ontology, id_map, warnings = adapter.assemble(selected)
    issues = adapter.verify_structure(ontology)
    if issues:
        raise DeliveryBlocked([{'code': 'STRUCTURE_INVALID', 'message': text} for text in issues])
    return ontology, id_map, warnings, batch['batch_id']


def _check_name(conn, owner_id, name):
    """同名保护：当前账号下已有本体（草稿或发布）即拒绝，绝不覆盖/合并（沿用 name_key 口径）。"""
    from workbench.storage import assets as asset_store
    if asset_store.name_taken(conn, 'model', name, owner_user_id=owner_id):
        raise DuplicateOntologyName('已存在同名本体「%s」，请修改名称后重试（不会覆盖已有本体）' % name)


def _new_ontology_id():
    """新本体 ID：必须是与现有本体同形态的规范 UUID（workspaces.clean_id 校验）。"""
    import uuid
    return str(uuid.uuid4())


def deliver(conn, task_id, name, check_token, request_id):
    """在调用方事务内原子创建新本体草稿 + 回执。

    幂等：同 requestId 且同 payload digest → 返回原结果（网络重试安全）；
    同 requestId 不同 payload → 409；已交付任务不同 requestId → 409（只交付一次）。
    """
    owner_id = owner()
    display = str(name or '').strip()
    if not display:
        raise ValueError('本体名称不能为空')
    if len(display) > 160:
        raise ValueError('本体名称过长')
    request_id = str(request_id or '').strip()
    if not request_id or len(request_id) > 80:
        raise ValueError('缺少有效的 requestId')

    row = store.require_task(conn, task_id, owner_id)
    if row is None:
        raise sto.NotFound('生成任务不存在')

    ontology, id_map, warnings, batch_id = prepare_payload(conn, task_id)
    # 幂等指纹只取**选定集合内容**：定义 ID 每次装配都由服务端重新随机分配，
    # 以图内容作指纹会让同一请求的重放被判成不同 payload（重放必须命中）。
    digest = _selection_digest(conn, task_id, owner_id, batch_id)
    if check_token and str(check_token) != _expected_token(conn, task_id, owner_id, batch_id):
        raise DeliveryBlocked([{'code': 'CHECK_TOKEN_STALE',
                                'message': '选定集合或材料/范围已变化，请重新运行交付前检查'}],
                              '交付前检查已失效')
    issues = _blocking_issues(conn, task_id, owner_id, batch_id)
    if issues:
        raise DeliveryBlocked(issues)

    # 幂等回执：先按 requestId 识别成功回执（网络重试安全），不能因 revision/ID 变化误报失败
    record = store.get_delivery_by_request(conn, owner_id, request_id)
    if record:
        if record['taskId'] != task_id or record.get('payloadDigest') != digest:
            raise sto.RevisionConflict(message='同一 requestId 提交了不同内容，请刷新后重试')
        return {'ontologyId': record['ontologyId'], 'taskId': record['taskId'],
                'deliveredAt': record['createdAt'], 'replayed': True}
    existing = store.get_delivery(conn, task_id, owner_id)
    if existing:
        raise DeliveryBlocked([{'code': 'ALREADY_DELIVERED',
                                'message': '该任务已交付本体（%s），不能重复创建；'
                                           '如需再建请复制为新任务' % existing['ontologyId']}],
                              '该任务已交付')

    from workbench.storage import assets as asset_store
    asset_store.bump_guard(conn, 'asset-name:model')
    _check_name(conn, owner_id, display)
    ontology_id = _new_ontology_id()
    from workbench.storage import assets as asset_store
    from workbench import workspaces
    state = adapter.build_state(ontology, display)
    state['workspaceId'] = ontology_id
    created = store.create_ontology_asset(conn, owner_id, ontology_id, display,
                                         workspaces._payload_of(state),
                                         asset_store.PAYLOAD_FORMAT_ONTOLOGY,
                                         summary={'source': 'ontology-build', 'taskId': task_id,
                                                  'batchId': batch_id, 'candidateMap': id_map,
                                                  'warnings': warnings,
                                                  'definitionCount': len(ontology.get('@graph') or [])})
    store.insert_delivery(conn, task_id, owner_id, request_id, digest, ontology_id)
    store.touch_task(conn, task_id, owner_id, status='delivered',
                     stage_label=protocol.TASK_STAGE_LABELS['delivered'],
                     delivery_ontology_id=ontology_id)
    return {'ontologyId': ontology_id, 'taskId': task_id, 'deliveredAt': sto.utcnow(),
            'revision': created['revision'], 'warnings': warnings, 'replayed': False}


def _selection_digest(conn, task_id, owner_id, batch_id):
    """选定集合的**内容**指纹（不含服务端分配的 ID 与时间）：同一评审状态下稳定，
    因此能作为 requestId 幂等比对基准，同时集合/字段变化会使其失效。"""
    items, selected = _selected_candidates(conn, task_id, owner_id, batch_id)
    material = sorted(
        sto.json_dumps({
            'id': c['id'], 'type': c['type'], 'key': c['key'], 'name': c['name'],
            'definition': c['definition'], 'fields': c['fields'],
            'ownerKey': c['ownerKey'], 'decision': c['decision'],
        }) for c in selected)
    return sto.content_hash(sto.json_dumps(material).encode('utf-8'))


def _expected_token(conn, task_id, owner_id, batch_id):
    """按当前选定集合重算 checkToken（用于与客户端令牌比对）。"""
    items, selected = _selected_candidates(conn, task_id, owner_id, batch_id)
    scope = store.get_scope(conn, task_id, owner_id)
    row = store.require_task(conn, task_id, owner_id)
    return adapter.make_check_token(batch_id, [c['id'] for c in selected], scope['revision'],
                                    row['material_revision'])


def delivery_view(conn, task_id):
    owner_id = owner()
    record = store.get_delivery(conn, task_id, owner_id)
    if record is None:
        return {'delivery': None}
    return {'delivery': record}

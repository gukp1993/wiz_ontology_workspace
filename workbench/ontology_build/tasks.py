"""生成任务域：任务 CRUD、状态机、范围基线冻结、批次失效与再生成。

所有函数在调用方的写事务里执行（传 conn），由路由层开一次短事务；
材料修订/范围修订推进时，旧批次标 stale（旧结果只读，不可直接交付）。
"""
import json

from workbench import auth
from workbench.ontology_build import protocol
from workbench.storage import engine as sto
from workbench.storage import ontology_build as store


class TaskError(ValueError):
    """业务校验失败（HTTP 400/422，取决于调用点）。"""


def owner():
    return auth.require_user_id()


DEFAULT_NAME_PREFIX = '未命名任务'


def create_task(conn, name):
    """新建任务；空名自动编号（原型要求：任务可无名开始）。"""
    owner_id = owner()
    display = str(name or '').strip()
    if not display:
        display = '%s-%s' % (DEFAULT_NAME_PREFIX, sto.utcnow()[:10])
    if len(display) > 160:
        raise TaskError('任务名称过长')
    task_id = store.create_task(conn, owner_id, display)
    return store.get_task(conn, task_id, owner_id)


def rename_task(conn, task_id, name, revision):
    owner_id = owner()
    display = str(name or '').strip()
    if not display:
        raise TaskError('任务名称不能为空')
    store.touch_task(conn, task_id, owner_id, expected_revision=revision, status=None)
    conn.execute(sto.text('UPDATE wb_build_tasks SET name = :n, name_key = :nk '
                          'WHERE task_id = :t AND owner_user_id = :o'),
                 {'n': display, 'nk': display.casefold(), 't': task_id, 'o': owner_id})
    return store.get_task(conn, task_id, owner_id)


def get_task_detail(conn, task_id):
    """任务详情：任务 + 材料 + 范围 + 最近运行 + 回执 + 能力（A01/A02 首屏一次取全）。"""
    owner_id = owner()
    row = store.require_task(conn, task_id, owner_id)
    if row is None:
        raise sto.NotFound('生成任务不存在')
    task = store.task_view(row)
    scope = store.get_scope(conn, task_id, owner_id)
    run_row = store.latest_run(conn, task_id, owner_id)
    return {
        'task': task,
        'materials': store.list_materials(conn, task_id, owner_id),
        'scope': scope,
        'run': store.run_view(run_row) if run_row else None,
        'batch': store.batch_view(store.latest_batch(conn, task_id, owner_id))
                 if store.latest_batch(conn, task_id, owner_id) else None,
        'delivery': store.get_delivery(conn, task_id, owner_id),
    }


def require_owned_task(conn, task_id):
    owner_id = owner()
    row = store.require_task(conn, task_id, owner_id)
    if row is None:
        raise sto.NotFound('生成任务不存在')
    return owner_id, row


def mark_materials_changed(conn, task_id, owner_id, expect_revision=None):
    """材料清单变化：推进 materialRevision、使全部批次失效、回退状态到 materials。"""
    row = store.require_task(conn, task_id, owner_id)
    if row is None:
        raise sto.NotFound('生成任务不存在')
    next_rev = int(row['material_revision']) + 1
    store.invalidate_batches(conn, task_id, owner_id)
    status = 'materials' if row['status'] in ('draft', 'materials') else 'materials'
    store.touch_task(conn, task_id, owner_id, expected_revision=expect_revision,
                     status=status, stage_label=protocol.TASK_STAGE_LABELS[status],
                     material_revision=next_rev)
    return next_rev


def maybe_advance_to_scope(conn, task_id, owner_id):
    """扫描成功后：若仍是 materials 阶段且存在可用材料，推进到确定范围。"""
    row = store.require_task(conn, task_id, owner_id)
    if row is None or row['status'] not in ('draft', 'materials'):
        return
    usable = [m for m in store.list_materials(conn, task_id, owner_id)
              if not m['excluded'] and m['parseState'] in ('success', 'partial')]
    if usable:
        store.touch_task(conn, task_id, owner_id, status='scope',
                         stage_label=protocol.TASK_STAGE_LABELS['scope'])


def task_baseline(conn, task_id, owner_id, provider=None):
    """冻结输入基线：材料清单与哈希、范围修订、解析/提示词版本、模型指纹（不含密钥）。"""
    row = store.require_task(conn, task_id, owner_id)
    if row is None:
        raise sto.NotFound('生成任务不存在')
    materials = store.list_materials(conn, task_id, owner_id)
    usable = [m for m in materials if not m['excluded']]
    if not usable:
        raise TaskError('至少需要一份未排除的材料')
    if not any(m['parseState'] in ('success', 'partial') for m in usable):
        raise TaskError('材料尚未成功解析，请先扫描物料')
    scope = store.get_scope(conn, task_id, owner_id)
    fingerprint = protocol.provider_fingerprint(provider) if provider else (scope.get('providerFingerprint') or {})
    return {
        'materialRevision': int(row['material_revision']),
        'materialHashes': sorted('%s:%s' % (m['id'], m['contentHash']) for m in usable),
        'scopeRevision': int(scope['revision']),
        'parserVersion': protocol.PARSER_VERSION,
        'promptVersion': protocol.PROMPT_VERSION,
        'providerFingerprint': fingerprint,
    }


def scope_has_blocking_issues(scope):
    """重大矛盾（同一词同时出现在纳入与排除范围且无解释）→ 阻断范围确认。

    D19 收紧后的保守规则：
    * 只有 **完全相等**（casefold 后）才算冲突——「储能」是「储能簇」的子串
      并不代表同一模块，旧的字串包含规则会大面积误报；
    * 覆盖说明**逐冲突对**豁免：coverage 文本明确提到该词才免报这一对，
      一句无关的话不再把全部冲突整体放行；
    * 长词（>12 字）不再被分词窗口静默丢弃（窗口放宽到 2–64）。
    """
    include = str(scope.get('include') or '')
    exclude = str(scope.get('exclude') or '')
    coverage = str(scope.get('coverage') or '').strip()
    if not include or not exclude:
        return []
    issues = []
    include_words, exclude_words = _tokens(include), _tokens(exclude)
    exclude_folded = {word.casefold() for word in exclude_words}
    conflicts, seen = [], set()
    for word in include_words:
        folded = word.casefold()
        if folded in exclude_folded and folded not in seen:
            seen.add(folded)
            conflicts.append(word)
    coverage_folded = coverage.casefold()
    for word in conflicts:
        if coverage_folded and word.casefold() in coverage_folded:
            continue  # 该冲突对已被覆盖说明点名
        issues.append({'code': 'SCOPE_CONFLICT', 'field': 'exclude',
                       'message': '「%s」同时出现在纳入与排除范围，'
                                  '请在覆盖说明中给出取舍口径' % word})
    blocking_open = [q for q in (scope.get('openQuestions') or []) if isinstance(q, dict) and q.get('blocking')]
    for question in blocking_open:
        issues.append({'code': 'SCOPE_OPEN_QUESTION', 'field': 'openQuestions',
                       'message': '阻断性未决问题：%s' % str(question.get('text') or '')})
    return issues


def _tokens(text):
    """粗分块：按常见分隔符切出 2–64 字的业务词（不做语义处理，只用于一致性检查）。"""
    out = []
    buf = ''
    for ch in str(text):
        if ch.isalnum() or ch in ('_', '-'):
            buf += ch
        else:
            if 2 <= len(buf) <= 64:
                out.append(buf)
            buf = ''
    if 2 <= len(buf) <= 64:
        out.append(buf)
    return out


def confirm_scope(conn, task_id, revision, provider):
    """确认范围并冻结基线：建 batch + generate run，推进任务状态。

    返回 (batch_id, run_id, baseline)。调用方随后把作业提交给 runner。
    """
    owner_id, row = require_owned_task(conn, task_id)
    scope = store.get_scope(conn, task_id, owner_id)
    if int(revision) != int(scope['revision']):
        raise sto.RevisionConflict(current_revision=str(scope['revision']),
                                   message='范围摘要有新的修订，请刷新后重试')
    if not str(scope.get('goal') or '').strip() or not str(scope.get('include') or '').strip():
        raise TaskError('请先填写建模目标与纳入范围')
    issues = scope_has_blocking_issues(scope)
    if issues:
        raise ScopeBlocked(issues)
    materials = [m for m in store.list_materials(conn, task_id, owner_id)
                 if not m['excluded'] and m['parseState'] in ('success', 'partial')]
    if not materials:
        raise TaskError('至少需要一份成功解析的材料才能开始生成')
    if provider is None:
        raise TaskError('尚未配置可用的 LLM 提供方，请先到「设置 → 模型设置」添加')
    # 顺序要紧：先把「确认」写回范围（revision 会 +1），再冻结基线。
    # 反过来的话批次基线记录的是确认前的 revision，任何批次一建出来就被判 stale。
    store.put_scope(conn, task_id, owner_id, _scope_payload(scope), expected_revision=None,
                    confirmed=True, provider_fingerprint=baseline_fingerprint(provider))
    baseline = task_baseline(conn, task_id, owner_id, provider)
    batch_id = store.create_batch(conn, task_id, owner_id, '', baseline)
    run_id, lease = store.create_run(conn, task_id, owner_id, 'generate', baseline, batch_id)
    conn.execute(sto.text('UPDATE wb_build_batches SET run_id = :r WHERE batch_id = :b'),
                 {'r': run_id, 'b': batch_id})
    store.touch_task(conn, task_id, owner_id, status='generating',
                     stage_label=protocol.TASK_STAGE_LABELS['generating'],
                     current_batch=batch_id)
    return batch_id, run_id, baseline, lease


class ScopeBlocked(ValueError):
    """范围存在阻断问题（HTTP 422 + issues）。"""

    def __init__(self, issues):
        self.issues = issues
        super().__init__('范围存在需要先解决的矛盾')


def baseline_fingerprint(provider):
    """范围确认时写入的模型指纹（不含密钥）；基线冻结时复用同一算法。"""
    return protocol.provider_fingerprint(provider) if provider else {}


def _scope_payload(scope):
    return {'goal': scope.get('goal', ''), 'include': scope.get('include', ''),
            'exclude': scope.get('exclude', ''), 'relations': scope.get('relations', ''),
            'coverage': scope.get('coverage', ''),
            'openQuestions': scope.get('openQuestions') or []}


def current_scope_payload(scope):
    return _scope_payload(scope)


def stale_for_task(conn, task_id, owner_id):
    """当前结果是否过期：最近批次被标记 stale，或材料/范围修订晚于批次基线。"""
    batch = store.latest_batch(conn, task_id, owner_id)
    if batch is None:
        return False
    if batch['stale']:
        return True
    row = store.require_task(conn, task_id, owner_id)
    baseline = store._loads(batch['baseline_json'], {})
    if int(baseline.get('materialRevision', -1)) != int(row['material_revision']):
        return True
    scope = store.get_scope(conn, task_id, owner_id)
    if int(baseline.get('scopeRevision', -1)) != int(scope['revision']):
        return True
    return False


def dump(value):
    return json.dumps(value, ensure_ascii=False)

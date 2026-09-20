"""「从物料自动构建本体」HTTP 路由层：输入校验 + 域服务分派 + 统一响应。

本层只做三件事：参数形态与类型校验（缺参/类型不符 → ValueError，HTTP 400）、
开一次短写事务调用域服务、把域结果整理成契约响应。
业务规则在 tasks / materials / review / delivery / pipeline，本层不重复实现。
长任务（解析、LLM）通过 runner 后台执行，路由立即返回 runId（不阻塞、不持锁）。

契约：文档/接口文档/08-从物料自动构建本体接口.md。
"""
import json

from workbench import storage
from workbench.ontology_build import delivery as delivery_domain
from workbench.ontology_build import materials as material_domain
from workbench.ontology_build import protocol
from workbench.ontology_build import review as review_domain
from workbench.ontology_build import runner
from workbench.ontology_build import tasks as task_domain
from workbench.storage import engine as sto
from workbench.storage import ontology_build as store

_OPS = ('generate', 'scan', 'dialog')


# ── 参数工具 ───────────────────────────────────────────────────────────────

def _text(payload, key, required=True, limit=512):
    value = payload.get(key)
    if value is None:
        if required:
            raise ValueError('缺少参数：%s' % key)
        return ''
    if not isinstance(value, str):
        raise ValueError('参数 %s 必须是字符串' % key)
    if required and not value.strip():
        raise ValueError('参数 %s 不能为空' % key)
    if len(value) > limit:
        raise ValueError('参数 %s 过长' % key)
    return value


def _flag(payload, key, default=False):
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value in ('true', 'false'):
        return value == 'true'
    raise ValueError('参数 %s 必须是布尔值' % key)


def _int_arg(payload, key, required=True, default=None, low=None, high=None):
    value = payload.get(key, default)
    if value is None or value == '':
        if required:
            raise ValueError('缺少参数：%s' % key)
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError('参数 %s 必须是整数' % key) from None
    if low is not None and number < low:
        raise ValueError('参数 %s 不能小于 %d' % (key, low))
    if high is not None and number > high:
        raise ValueError('参数 %s 不能大于 %d' % (key, high))
    return number


def _json_arg(payload, key, required=True):
    value = payload.get(key)
    if value is None:
        if required:
            raise ValueError('缺少参数：%s' % key)
        return {}
    if not isinstance(value, (dict, list)):
        raise ValueError('参数 %s 必须是对象或数组' % key)
    return value


def _query(query, key, required=True, default=''):
    values = query.get(key) or []
    if not values:
        if required:
            raise ValueError('缺少参数：%s' % key)
        return default
    return values[0]


def _query_int(query, key, required=False, default=0):
    raw = _query(query, key, required=required, default='')
    if raw in ('', None):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError('参数 %s 必须是整数' % key) from None


def _run_task(owner_user_id, run_id, job):
    """把作业交给后台池（不阻塞请求线程）。"""
    runner.submit(owner_user_id, run_id, job)


# ── GET ────────────────────────────────────────────────────────────────────

def get_capabilities(query):
    from workbench import auth
    from workbench import llm_providers
    provider = None
    try:
        provider = llm_providers.default_provider()
    except Exception:
        provider = None
    ref = None
    if provider:
        ref = {'id': provider.get('id', ''), 'name': provider.get('name', ''),
               'model': provider.get('model', '')}
    return {
        'limits': protocol.LIMITS,
        'ocr': {'available': False,
                'reason': '未配置 OCR 服务；扫描页 PDF 将逐页报告失败，不假称解析成功'},
        'provider': ref,
        'parserVersion': protocol.PARSER_VERSION,
        'promptVersion': protocol.PROMPT_VERSION,
        'candidateTypes': list(protocol.CANDIDATE_TYPES),
        'propertyDataTypes': list(protocol.PROPERTY_DATA_TYPES),
        'valueTypes': list(protocol.VALUE_TYPES),
    }, 200


def get_tasks(query):
    limit = _query_int(query, 'limit', default=20)
    offset = _query_int(query, 'offset', default=0)
    with sto.read_connection() as conn:
        from workbench import auth
        items, total = store.list_tasks(conn, auth.require_user_id(), limit=limit, offset=offset)
    return {'items': items, 'total': total}, 200


def get_task(query):
    task_id = _query(query, 'taskId')
    with sto.read_connection() as conn:
        detail = task_domain.get_task_detail(conn, task_id)
        detail['stale'] = task_domain.stale_for_task(conn, task_id, _owner())
        detail['capabilities'] = {'limits': protocol.LIMITS,
                                  'ocrAvailable': False}
    return detail, 200


def get_materials(query):
    task_id = _query(query, 'taskId')
    owner_id = _owner()
    with sto.read_connection() as conn:
        row = store.require_task(conn, task_id, owner_id)
        if row is None:
            raise sto.NotFound('生成任务不存在')
        items = store.list_materials(conn, task_id, owner_id)
    return {'items': items, 'revision': int(row['material_revision'])}, 200


def get_run(query):
    task_id = _query(query, 'taskId')
    run_id = _query(query, 'runId', required=False)
    owner_id = _owner()
    with sto.read_connection() as conn:
        row = store.require_task(conn, task_id, owner_id)
        if row is None:
            raise sto.NotFound('生成任务不存在')
        run = store.get_run(conn, run_id, owner_id) if run_id else store.latest_run(conn, task_id, owner_id)
        if run is None:
            raise sto.NotFound('没有可用的运行记录')
        return {'run': store.run_view(run), 'taskStatus': row['status']}, 200


def get_messages(query):
    task_id = _query(query, 'taskId')
    after = _query_int(query, 'after', default=0)
    owner_id = _owner()
    with sto.read_connection() as conn:
        row = store.require_task(conn, task_id, owner_id)
        if row is None:
            raise sto.NotFound('生成任务不存在')
        messages = store.list_messages(conn, task_id, owner_id, after=after)
        scope = store.get_scope(conn, task_id, owner_id)
        stale = task_domain.stale_for_task(conn, task_id, owner_id)
    return {'messages': messages, 'scope': scope, 'revision': scope['revision'], 'stale': stale}, 200


def get_candidates(query):
    task_id = _query(query, 'taskId')
    owner_id = _owner()
    with sto.read_connection() as conn:
        row = store.require_task(conn, task_id, owner_id)
        if row is None:
            raise sto.NotFound('生成任务不存在')
        batch_id = _query(query, 'batch', required=False) or (row['current_batch'] or '')
        if not batch_id:
            latest = store.latest_batch(conn, task_id, owner_id)
            batch_id = latest['batch_id'] if latest else ''
        items, total = store.list_candidates(
            conn, task_id, owner_id, batch_id=batch_id or None,
            ctype=_query(query, 'type', required=False),
            decision=_query(query, 'decision', required=False),
            evidence_status=_query(query, 'evidenceStatus', required=False),
            query=_query(query, 'query', required=False),
            offset=_query_int(query, 'offset', default=0),
            limit=_query_int(query, 'limit', default=100))
        counts = store.candidate_counts(conn, task_id, owner_id, batch_id=batch_id or None)
        batch = store.get_batch(conn, batch_id, owner_id) if batch_id else None
        stale = task_domain.stale_for_task(conn, task_id, owner_id)
    return {'items': items, 'total': total, 'counts': counts,
            'batch': store.batch_view(batch) if batch else None, 'stale': stale}, 200


def get_candidate(query):
    candidate_id = _query(query, 'candidateId')
    with sto.read_connection() as conn:
        return review_domain.candidate_detail(conn, candidate_id), 200


def get_diff(query):
    task_id = _query(query, 'taskId')
    batch_id = _query(query, 'batch', required=False)
    owner_id = _owner()
    with sto.read_connection() as conn:
        row = store.require_task(conn, task_id, owner_id)
        if row is None:
            raise sto.NotFound('生成任务不存在')
        new_batch = store.get_batch(conn, batch_id, owner_id) if batch_id else store.latest_batch(conn, task_id, owner_id)
        if new_batch is None:
            raise sto.NotFound('尚未生成批次')
        batches = store.list_batches(conn, task_id, owner_id, limit=10)
        previous = next((b for b in batches if b['id'] != new_batch['batch_id']), None)
        result = review_domain.build_diff(conn, task_id, owner_id,
                                          previous['id'] if previous else None, new_batch['batch_id'])
    return result, 200


def get_delivery(query):
    task_id = _query(query, 'taskId')
    with sto.read_connection() as conn:
        return delivery_domain.delivery_view(conn, task_id), 200


# ── POST：任务 ──────────────────────────────────────────────────────────────

def post_task_create(payload):
    name = _text(payload, 'name', required=False, limit=160)
    with sto.write_tx() as tx:
        return {'task': tx.run(lambda conn: task_domain.create_task(conn, name))}, 200


def post_task_rename(payload):
    task_id = _text(payload, 'taskId')
    name = _text(payload, 'name', limit=160)
    revision = _text(payload, 'revision', limit=80)
    with sto.write_tx() as tx:
        return {'task': tx.run(lambda conn: task_domain.rename_task(conn, task_id, name, revision))}, 200


def post_task_delete(payload):
    task_id = _text(payload, 'taskId')
    confirm = _text(payload, 'confirmName', limit=160)
    owner_id = _owner()
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            if confirm.strip() != str(row['name']).strip():
                raise ValueError('确认名称与任务名不一致，未删除')
            store.soft_delete_task(conn, task_id, owner_id)
            delivery = store.get_delivery(conn, task_id, owner_id)
            note = ''
            if delivery:
                note = '任务已删除，但已创建的本体草稿（%s）保留，不随任务删除；原始证据将不可用' % delivery['ontologyId']
            return {'ok': True, 'note': note}
        return tx.run(body), 200


# ── POST：上传与物料 ────────────────────────────────────────────────────────

def post_upload_init(payload):
    task_id = _text(payload, 'taskId')
    rel_path = _text(payload, 'relPath', limit=512)
    size = _int_arg(payload, 'size', low=1)
    owner_id = _owner()
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            return material_domain.upload_init(conn, owner_id, task_id, rel_path, size)
        return tx.run(body), 200


def post_upload_chunk(payload):
    upload_id = _text(payload, 'uploadId')
    index = _int_arg(payload, 'index', low=0)
    chunk_hash = _text(payload, 'hash', limit=128)
    data_b64 = _text(payload, 'dataBase64')
    owner_id = _owner()
    with sto.write_tx() as tx:
        return tx.run(lambda conn: material_domain.upload_chunk(
            conn, owner_id, upload_id, index, chunk_hash, data_b64)), 200


def post_upload_complete(payload):
    upload_id = _text(payload, 'uploadId')
    final_hash = _text(payload, 'finalHash', limit=128)
    owner_id = _owner()
    with sto.write_tx() as tx:
        def body(conn):
            result = material_domain.upload_complete(conn, owner_id, upload_id, final_hash)
            upload = store.get_upload(conn, upload_id, owner_id)
            if upload is not None:
                materials = result.get('materials') or []
                if materials:
                    task_domain.mark_materials_changed(conn, upload['task_id'], owner_id)
            return result
        return tx.run(body), 200


def post_upload_abort(payload):
    upload_id = _text(payload, 'uploadId')
    owner_id = _owner()
    with sto.write_tx() as tx:
        return tx.run(lambda conn: material_domain.upload_abort(conn, owner_id, upload_id)), 200


def post_material_exclude(payload):
    task_id = _text(payload, 'taskId')
    material_id = _text(payload, 'materialId')
    excluded = _flag(payload, 'excluded')
    revision = _text(payload, 'revision', limit=80)
    owner_id = _owner()
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            material = store.get_material(conn, material_id, owner_id)
            if material is None or material['task_id'] != task_id:
                raise sto.NotFound('材料不存在')
            current = str(row['material_revision'])
            if revision not in ('', current):
                raise sto.RevisionConflict(current_revision=current,
                                           message='材料清单已变化，请刷新后重试')
            store.update_material(conn, material_id, owner_id, excluded=excluded,
                                  parse_state='excluded' if excluded else 'pending')
            task_domain.mark_materials_changed(conn, task_id, owner_id)
            updated = store.get_material(conn, material_id, owner_id)
            task_row = store.require_task(conn, task_id, owner_id)
            return {'material': store.material_view(updated),
                    'task': store.task_view(task_row)}
        return tx.run(body), 200


def post_material_retry(payload):
    """重试单份材料：等价于对该材料重新扫描（复用同一扫描管线）。"""
    task_id = _text(payload, 'taskId')
    material_id = _text(payload, 'materialId')
    return _start_scan(task_id, material_ids=[material_id])


def post_scan(payload):
    task_id = _text(payload, 'taskId')
    return _start_scan(task_id)


def _start_scan(task_id, material_ids=None):
    owner_id = _owner()
    from workbench.ontology_build import pipeline
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            materials = [m for m in store.list_materials(conn, task_id, owner_id)
                         if not m['excluded'] and (not material_ids or m['id'] in material_ids)]
            if not materials:
                raise ValueError('没有可扫描的材料（至少需要一份未排除材料）')
            for material in materials:
                store.update_material(conn, material['id'], owner_id, parse_state='pending', error='')
            run_id, _lease = store.create_run(conn, task_id, owner_id, 'scan',
                                              {'materialRevision': int(row['material_revision'])})
            store.touch_task(conn, task_id, owner_id, status='materials' if row['status'] == 'draft' else None,
                             stage_label='解析材料')
            return run_id, [m['id'] for m in materials]
        run_id, ids = tx.run(body)
    _run_task(owner_id, run_id, lambda user, run: pipeline.run_scan(user, task_id, run))
    return {'runId': run_id}, 200


# ── POST：运行 ──────────────────────────────────────────────────────────────

def post_run_cancel(payload):
    task_id = _text(payload, 'taskId')
    run_id = _text(payload, 'runId')
    owner_id = _owner()
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            run = store.get_run(conn, run_id, owner_id)
            if run is None or run['task_id'] != task_id:
                raise sto.NotFound('运行不存在')
            return None
        tx.run(body)
    runner.request_cancel(owner_id, run_id)
    with sto.read_connection() as conn:
        run = store.get_run(conn, run_id, owner_id)
        return {'run': store.run_view(run)}, 200


def post_run_resume(payload):
    task_id = _text(payload, 'taskId')
    run_id = _text(payload, 'runId')
    owner_id = _owner()
    from workbench.ontology_build import pipeline
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            run = store.get_run(conn, run_id, owner_id)
            if run is None or run['task_id'] != task_id:
                raise sto.NotFound('运行不存在')
            if run['state'] not in ('failed', 'cancelled', 'interrupted'):
                raise ValueError('仅失败、已取消或已中断的运行可以重试')
            store.update_run(conn, run_id, owner_id, state='queued', error='',
                             retryable=False, cancel_requested=False,
                             attempt=int(run['attempt']) + 1)
            return {'runId': run_id, 'kind': run['kind'],
                    'batchId': run['batch_id'] or ''}
        result = tx.run(body)
    kind = result['kind']
    if kind == 'generate':
        return _resume_generate(owner_id, task_id, run_id, result['batchId']), 200
    job = (lambda user, run: pipeline.run_scan(user, task_id, run)) if kind == 'scan' \
        else (lambda user, run: pipeline.run_dialog(user, task_id, run, _provider_or_raise()))
    _run_task(owner_id, run_id, job)
    return {'runId': run_id}, 200


def _resume_generate(owner_id, task_id, run_id, batch_id):
    provider = _provider_or_raise()
    from workbench.ontology_build import pipeline
    _run_task(owner_id, run_id,
              lambda user, run: pipeline.run_generate(user, task_id, run, batch_id, provider))
    return {'runId': run_id}


def _provider_or_raise():
    from workbench import llm_providers
    provider = llm_providers.default_provider()
    if not provider:
        raise ValueError('尚未配置可用的 LLM 提供方：请到「更多工具 → LLM 配置」添加后再重试')
    return provider


# ── POST：范围对话 ──────────────────────────────────────────────────────────

def post_message(payload):
    task_id = _text(payload, 'taskId')
    content = _text(payload, 'content', limit=8000)
    revision = _int_arg(payload, 'revision', required=False, default=0)
    owner_id = _owner()
    from workbench.ontology_build import pipeline
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            scope = store.get_scope(conn, task_id, owner_id)
            if revision and int(revision) != int(scope['revision']):
                raise sto.RevisionConflict(current_revision=str(scope['revision']),
                                           message='范围摘要有新的修订，请刷新后重试')
            message_id, _seq = store.append_message(conn, task_id, owner_id, 'user', content,
                                                    scope_revision=scope['revision'])
            run_id, _lease = store.create_run(conn, task_id, owner_id, 'dialog',
                                              {'scopeRevision': scope['revision']})
            return message_id, run_id
        message_id, run_id = tx.run(body)
    provider = None
    try:
        provider = _provider_or_raise()
    except ValueError as exc:
        with sto.write_tx() as tx:
            tx.run(lambda conn: store.append_message(
                conn, task_id, owner_id, 'assistant', '', error=str(exc)))
        return {'messageId': message_id, 'assistantPending': False, 'assistantError': str(exc)}, 200
    _run_task(owner_id, run_id,
              lambda user, run: pipeline.run_dialog(user, task_id, run, provider))
    return {'messageId': message_id, 'assistantPending': True}, 200


def post_scope_save(payload):
    task_id = _text(payload, 'taskId')
    scope = _json_arg(payload, 'scope')
    revision = _int_arg(payload, 'revision', required=False, default=0)
    owner_id = _owner()
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            payload_scope = {'goal': str(scope.get('goal') or ''),
                             'include': str(scope.get('include') or ''),
                             'exclude': str(scope.get('exclude') or ''),
                             'relations': str(scope.get('relations') or ''),
                             'coverage': str(scope.get('coverage') or ''),
                             'openQuestions': _open_questions(scope.get('openQuestions'))}
            new_revision = store.put_scope(conn, task_id, owner_id, payload_scope,
                                           expected_revision=revision)
            store.touch_task(conn, task_id, owner_id, scope_revision=new_revision)
            return store.get_scope(conn, task_id, owner_id)
        return {'scope': tx.run(body)}, 200


def _open_questions(value):
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:50]:
        if isinstance(item, dict):
            out.append({'text': str(item.get('text') or '')[:500],
                        'blocking': bool(item.get('blocking'))})
        elif isinstance(item, str):
            out.append({'text': item[:500], 'blocking': False})
    return out


def post_scope_confirm(payload):
    task_id = _text(payload, 'taskId')
    revision = _int_arg(payload, 'revision', required=False, default=0)
    provider_id = _text(payload, 'providerId', required=False, limit=64)
    owner_id = _owner()
    from workbench import llm_providers
    from workbench.ontology_build import pipeline
    provider = llm_providers.resolve(provider_id) if provider_id else llm_providers.default_provider()
    if not provider:
        raise ValueError('尚未配置可用的 LLM 提供方：请到「更多工具 → LLM 配置」添加')
    with sto.write_tx() as tx:
        try:
            batch_id, run_id, baseline, _lease = tx.run(
                lambda conn: task_domain.confirm_scope(conn, task_id, revision, provider))
        except task_domain.ScopeBlocked as exc:
            raise _blocked(exc.issues) from None
    _run_task(owner_id, run_id,
              lambda user, run: pipeline.run_generate(user, task_id, run, batch_id, provider))
    return {'runId': run_id, 'batchId': batch_id}, 200


def _blocked(issues):
    """把域层 issues 转成路由层可识别的异常（server.py 映射 422 + issues）。"""
    error = ValueError('范围或选定集合存在需要先解决的问题')
    error.issues = issues
    error.code = 'INVALID_STATE'
    return error


# ── POST：候选评审 ──────────────────────────────────────────────────────────

def post_candidate_update(payload):
    candidate_id = _text(payload, 'candidateId')
    fields = _json_arg(payload, 'fields')
    revision = _text(payload, 'revision', limit=80)
    with sto.write_tx() as tx:
        candidate = tx.run(lambda conn: review_domain.update_candidate(
            conn, candidate_id, fields, revision))
    return {'candidate': candidate}, 200


def post_candidate_decide(payload):
    candidate_id = _text(payload, 'candidateId')
    decision = _text(payload, 'decision', limit=16)
    reason = _text(payload, 'reason', required=False, limit=2000)
    revision = _text(payload, 'revision', limit=80)
    with sto.write_tx() as tx:
        candidate = tx.run(lambda conn: review_domain.decide(conn, candidate_id, decision, reason, revision))
    return {'candidate': candidate}, 200


def post_candidates_merge(payload):
    task_id = _text(payload, 'taskId')
    primary_id = _text(payload, 'primaryId')
    merge_ids = _json_arg(payload, 'mergeIds')
    if not isinstance(merge_ids, list) or not merge_ids:
        raise ValueError('mergeIds 必须是非空数组')
    merge_ids = [str(item) for item in merge_ids][:50]
    confirmed = _flag(payload, 'confirmed', False)
    revision = _text(payload, 'revision', required=False, limit=80)
    if confirmed:
        with sto.write_tx() as tx:
            result = tx.run(lambda conn: review_domain.merge_apply(conn, task_id, primary_id, merge_ids, revision))
        return result, 200
    with sto.read_connection() as conn:
        return review_domain.merge_preview(conn, task_id, primary_id, merge_ids), 200


def post_review_undo(payload):
    task_id = _text(payload, 'taskId')
    op_id = _text(payload, 'opId')
    revision = _text(payload, 'revision', required=False, limit=80)
    with sto.write_tx() as tx:
        result = tx.run(lambda conn: review_domain.undo_review_op(conn, task_id, op_id, revision))
    return result, 200


def post_regenerate(payload):
    task_id = _text(payload, 'taskId')
    revision = _int_arg(payload, 'revision', required=False, default=0)
    owner_id = _owner()
    from workbench.ontology_build import pipeline
    provider = _provider_or_raise()
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            scope = store.get_scope(conn, task_id, owner_id)
            if revision and int(revision) != int(scope['revision']):
                raise sto.RevisionConflict(current_revision=str(scope['revision']),
                                           message='范围摘要有新的修订，请刷新后重试')
            baseline = task_domain.task_baseline(conn, task_id, owner_id, provider)
            batch_id = store.create_batch(conn, task_id, owner_id, '', baseline)
            run_id, _lease = store.create_run(conn, task_id, owner_id, 'generate', baseline, batch_id)
            conn.execute(sto.text('UPDATE wb_build_batches SET run_id = :r WHERE batch_id = :b'),
                         {'r': run_id, 'b': batch_id})
            store.touch_task(conn, task_id, owner_id, status='generating',
                             stage_label=protocol.TASK_STAGE_LABELS['generating'],
                             current_batch=batch_id)
            return batch_id, run_id
        batch_id, run_id = tx.run(body)
    _run_task(owner_id, run_id,
              lambda user, run: pipeline.run_generate(user, task_id, run, batch_id, provider))
    return {'runId': run_id, 'batchId': batch_id}, 200


def post_diff_resolve(payload):
    task_id = _text(payload, 'taskId')
    candidate_id = _text(payload, 'candidateId')
    choice = _text(payload, 'choice', limit=16)
    revision = _text(payload, 'revision', required=False, limit=80)
    with sto.write_tx() as tx:
        candidate = tx.run(lambda conn: review_domain.resolve_diff(
            conn, task_id, candidate_id, choice, revision))
    return {'candidate': candidate}, 200


# ── POST：交付 ──────────────────────────────────────────────────────────────

def post_deliver_precheck(payload):
    task_id = _text(payload, 'taskId')
    with sto.read_connection() as conn:
        return delivery_domain.precheck(conn, task_id), 200


def post_deliver(payload):
    task_id = _text(payload, 'taskId')
    name = _text(payload, 'name', limit=160)
    check_token = _text(payload, 'checkToken', required=False, limit=64)
    request_id = _text(payload, 'requestId', limit=80)
    from workbench.ontology_build import delivery
    try:
        with sto.write_tx() as tx:
            return tx.run(lambda conn: delivery.deliver(conn, task_id, name, check_token, request_id)), 200
    except delivery.DuplicateOntologyName as exc:
        error = ValueError(str(exc))
        error.code = 'DUPLICATE_NAME'
        error.status = 409
        raise error from None
    except delivery.DeliveryBlocked as exc:
        raise _blocked(exc.issues) from None


def _owner():
    from workbench import auth
    return auth.require_user_id()

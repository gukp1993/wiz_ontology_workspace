"""「从物料自动构建本体」HTTP 路由层：输入校验 + 域服务分派 + 统一响应。

本层只做三件事：参数形态与类型校验（缺参/类型不符 → ValueError，HTTP 400）、
开一次短写事务调用域服务、把域结果整理成契约响应。
业务规则在 tasks / materials / review / delivery / pipeline，本层不重复实现。
长任务（解析、LLM）通过 runner 后台执行，路由立即返回 runId（不阻塞、不持锁）。

契约：文档/接口文档/08-从物料自动构建本体接口.md。
"""
import os

from workbench.ontology_build import batch_contracts
from workbench.ontology_build import budget_profile
from workbench.ontology_build import delivery as delivery_domain
from workbench.ontology_build import blacklist as blacklist_domain
from workbench.ontology_build import materials as material_domain
from workbench.ontology_build import protocol
from workbench.ontology_build import review as review_domain
from workbench.ontology_build import runner
from workbench.ontology_build import tasks as task_domain
from workbench.storage import engine as sto
from workbench.storage import ontology_build as store

_OPS = ('generate', 'scan', 'dialog')


def _v2_codec():
    raw = str(os.environ.get(batch_contracts.OUTPUT_CODEC_ENV) or '').strip()
    return raw if raw in batch_contracts.CODEC_VERSIONS else batch_contracts.CODEC_LEGACY


def _budget_issue(code, message):
    """构造带 code/status 的 ValueError（server.py 映射为 422 + code）。"""
    error = ValueError(message)
    error.code = code
    error.status = 422
    return error


def _v2_precheck_generate(provider):
    """generate 类入口的同步预算预检（08 §14.2）：任何状态变更前调用；错误即 422。"""
    profile = budget_profile.build_profile()
    if not profile.get('enabled'):
        return None
    errors = budget_profile.request_budget_errors(profile, str(provider.get('id') or ''),
                                                  str(provider.get('model') or ''))
    if errors:
        code, field = errors[0]
        if code == batch_contracts.BUDGET_PROFILE_REQUIRED:
            raise _budget_issue(code, '自适应分批配置不完整（%s）：请设置对应的 WIZ_BUILD_* '
                                      '环境变量后重试。' % field)
        if code == batch_contracts.BUDGET_PROFILE_MISMATCH:
            raise _budget_issue(code, '请求的模型与自适应分批 profile 绑定不一致：请在 LLM 配置'
                                      '选择绑定的提供方/模型，或更新 WIZ_BUILD_PROFILE_* 配置。')
        raise _budget_issue(code, '自适应分批配置非法（%s）：请修正后重试。' % field)
    return profile


def _v2_count_guard(conn, owner_id, task_id, profile):
    """有界轻量数量检查：入模事实超计划上限 → 422 BUDGET_PLAN_TOO_LARGE（零状态变更）。"""
    count = store.count_facts(conn, task_id, owner_id)
    if count > batch_contracts.MAX_PLAN_TARGETS:
        raise _budget_issue(batch_contracts.BUDGET_PLAN_TOO_LARGE,
                            '任务事实 %d 条超过计划目标上限 %d：请缩小范围或拆分任务后再生成。'
                            % (count, batch_contracts.MAX_PLAN_TARGETS))
    return count
# provider 缺失时的统一可读文案（422 INVALID_STATE 的 message；对话端点写进 assistantError）
NO_PROVIDER_MESSAGE = '尚未配置可用的 LLM 提供方：请到「设置 → 模型设置」添加后再重试'

# 解析器支持矩阵（08 §2.1/§12.7；需求《结构化格式解析支持_v1》§5）：静态支持矩阵，不查库。
# 前 7 项 = 三级分派第①层专用解析器（kind → 后缀）；后 2 项 = 排除说明（硬/软黑名单），
# locator 用 '—' 表示「不产出定位事实」。exts 一律含点小写，供能力页原样展示。
# 调整解析支持时必须同步本表、protocol.detect_kind 与 parsers/__init__.py 的登记。
PARSER_MATRIX = (
    {'exts': ('.json', '.jsonld', '.jsonid'), 'label': '专用（json）',
     'locator': 'JSON 路径 / 节点 @id', 'note': 'JSON-LD 语义模式（@graph 节点高质量事实）'},
    {'exts': ('.jsonl', '.ndjson'), 'label': '专用（json 逐行）',
     'locator': '行号+键路径', 'note': ''},
    {'exts': ('.yaml', '.yml'), 'label': '专用（yaml）',
     'locator': '键路径', 'note': 'PyYAML safe_load，与 JSON 同一遍历口径'},
    {'exts': ('.properties',), 'label': '专用（properties）',
     'locator': '行号', 'note': '\\uXXXX 按 Java 规范解码；编码复用既有探测链'},
    {'exts': ('.csv', '.tsv'), 'label': '专用（csv）',
     'locator': '行号+列统计', 'note': '>100 行采样并注记截断范围'},
    {'exts': ('.ini', '.cfg', '.conf'), 'label': '专用（ini）',
     'locator': '节+键路径', 'note': '重复节/解析错误进 failedSegments'},
    {'exts': ('.toml',), 'label': '专用（toml 子集）',
     'locator': '键路径', 'note': '多行字符串/日期时间/[[数组表]]/虚键不支持，逐条诚实降级'},
    {'exts': ('.env*',), 'label': '硬黑名单排除',
     'locator': '—', 'note': '凭据边界（G20），不可配置；不产出事实'},
    {'exts': ('.parquet', '.proto', '.avro', '.msgpack'), 'label': '软黑名单/魔数排除',
     'locator': '—', 'note': '二进制格式不可解析，如实报告，不进 LLM 兜底'},
)


def _parser_matrix():
    """能力接口用的解析器支持矩阵（静态数据；列表化以免调用方改动元组）。"""
    return [{'exts': list(item['exts']), 'label': item['label'],
             'locator': item['locator'], 'note': item['note']} for item in PARSER_MATRIX]


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

def _ocr_capability():
    """V2-2（G18）：OCR 可用性动态探测（pytesseract + Pillow + tesseract）。"""
    from workbench.ontology_build.parsers import ocr_support
    available, reason = ocr_support.ocr_status()
    return {'available': available, 'reason': reason or 'OCR 已配置（本地 tesseract）'}


def get_capabilities(query):
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
        'ocr': _ocr_capability(),
        # V2-4（08 §13）：黑名单三层枚举（硬层完整公开；软层为默认值，任务可覆盖）
        'blacklist': {
            'hard': {'exts': sorted(blacklist_domain.HARD_EXTS),
                     'dirs': sorted(blacklist_domain.HARD_DIRS),
                     'note': '安全边界，不可配置；任何白名单不能越过'},
            'softDefaults': blacklist_domain.effective_soft_exts(None),
        },
        # V2-3（G19）：LLM 兜底解析限额与可用性（enabled=当前有默认提供方）
        'llmFallback': {
            'maxFiles': protocol.LLM_FALLBACK_MAX_FILES,
            'maxBytes': protocol.LLM_FALLBACK_MAX_BYTES,
            'sliceChars': protocol.LLM_FALLBACK_SLICE_CHARS,
            'enabled': bool(ref),
        },
        # 结构化格式（08 §2.1/§12.7）：解析器支持矩阵，7 支持项 + 2 排除说明，静态数据
        'parserMatrix': _parser_matrix(),
        # 生成控输出 v3（08 §14.4）：预算配置生效值展示（默认关闭；不给密钥/fact 清单）
        'generationBudget': budget_profile.budget_view(
            budget_profile.build_profile(), _v2_codec()),
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
    owner_id = _owner()
    with sto.read_connection() as conn:
        detail = task_domain.get_task_detail(conn, task_id)
        detail['stale'] = task_domain.stale_for_task(conn, task_id, owner_id)
        # schema2 运行：详情里的 run 同样输出冻结轮询摘要（08 §14.3）
        latest = store.latest_run(conn, task_id, owner_id)
        if latest is not None and detail.get('run'):
            detail['run'] = _v2_run_view(conn, latest, detail['run'])
        detail['capabilities'] = {'limits': protocol.LIMITS,
                                  'ocrAvailable': _ocr_capability()['available']}
    return detail, 200


def get_materials(query):
    task_id = _query(query, 'taskId')
    owner_id = _owner()
    with sto.read_connection() as conn:
        row = store.require_task(conn, task_id, owner_id)
        if row is None:
            raise sto.NotFound('生成任务不存在')
        if 'view' in query and query['view'][0] == 'groups':
            groups, total = store.list_material_groups(conn, task_id, owner_id)
            return {'groups': groups, 'total': total,
                    'revision': int(row['material_revision'])}, 200
        if 'view' in query and query['view'][0] == 'filter':
            # V2-4（08 §13）：被过滤文件报告——计数 + 清单 + 逐项命中规则（G20 可见性）。
            return {'filter': store.filter_spec_view(row),
                    'softDefaults': blacklist_domain.effective_soft_exts(None),
                    'report': store.filter_report_view(row),
                    'revision': int(row['material_revision'])}, 200
        # folder 显式出现（含空串=根目录组）才过滤；不出现保持旧行为全量兼容
        if 'folder' in query:
            items, total = store.list_materials_page(
                conn, task_id, owner_id, folder=query['folder'][0],
                offset=_query_int(query, 'offset', default=0),
                limit=_query_int(query, 'limit', default=100))
            return {'items': items, 'total': total,
                    'revision': int(row['material_revision'])}, 200
        items = store.list_materials(conn, task_id, owner_id)
    return {'items': items, 'revision': int(row['material_revision'])}, 200


def _v2_run_view(conn, run_row, view):
    """schema2 运行的轮询摘要增强（08 §14.3）：

    checkpoint.generate 换成 batch_contracts 冻结视图（jobs/coverage/budget/blocking，
    不回全量 jobs/attempts/factIds/候选正文）；usage 并入可空 token 统计
    （knownCompletionTokens/unknownUsageCalls，reasoning 不双加）。schema1 原样返回。
    """
    raw = store.run_checkpoint_doc(conn, str(run_row['run_id']), str(run_row['owner_user_id']))
    if isinstance(raw, dict) and isinstance(raw.get('generate'), dict):
        gen_doc = raw['generate']
    else:
        gen_doc = raw if isinstance(raw, dict) else {}
    # 损坏版本安全解析（P2-3 同源修复）：非整数/未知值按 schema1 原样返回，
    # 绝不裸抛（否则轮询 GET /api/build-run 会对损坏 checkpoint 报 400/500）。
    version = _safe_schema_version(gen_doc.get('schemaVersion')) if gen_doc else None
    if gen_doc and isinstance(version, int) and version >= 2:
        view['checkpoint'] = {'generate':
                              batch_contracts.generate_checkpoint_view(gen_doc)}
        aggregate = gen_doc.get('usageAggregate') if isinstance(gen_doc.get('usageAggregate'),
                                                                dict) else {}
        usage = dict(view.get('usage') or {})
        usage.update(batch_contracts.usage_view(aggregate, gen_doc.get('planEpoch')))
        view['usage'] = usage
    return view


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
        view = store.run_view(run)
        view = _v2_run_view(conn, run, view)
        return {'run': view, 'taskStatus': row['status']}, 200


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


def post_task_filter(payload):
    """任务级过滤设置（V2-4，08 §13）：软名单覆盖 + 自定义追加排除 + 后缀白名单。

    * `revision` 是任务 token（同 build-task-rename，§0）：必填，缺失/空串 400，不匹配 409；
    * `filter.softExts` 缺省/None = 用默认软名单；显式数组（含空数组）= 整体覆盖默认软名单；
    * 后缀一律规范化为 '.ext' 小写形态；硬黑名单不受本设置影响（安全边界）。
    """
    task_id = _text(payload, 'taskId')
    revision = _text(payload, 'revision', limit=80)
    raw_filter = payload.get('filter')
    if raw_filter is None:
        raw_filter = {}
    if not isinstance(raw_filter, dict):
        raise ValueError('参数 filter 必须是对象')
    spec = blacklist_domain.normalize_spec(raw_filter)
    owner_id = _owner()
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            store.touch_task(conn, task_id, owner_id, expected_revision=revision, status=None)
            store.set_task_filter(conn, task_id, owner_id, spec)
            return store.get_task(conn, task_id, owner_id)
        task = tx.run(body)
    return {'task': task, 'filter': spec}, 200


def post_task_delete(payload):
    # 08 §12.2：物理删除任务及全部关联行与 blob 文件；已创建的本体草稿不删除。
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
            delivery = store.get_delivery(conn, task_id, owner_id)
            counts, blob_paths = store.purge_task(conn, task_id, owner_id)
            return counts, blob_paths, delivery
        counts, blob_paths, delivery = tx.run(body)
    cleaned = material_domain.delete_task_blob_files(blob_paths)
    note = '已创建的本体草稿不随任务删除'
    if delivery:
        note += '；该任务此前交付的本体草稿（%s）保留' % delivery['ontologyId']
    if cleaned['errors']:
        note += '；%d 个物料文件删除失败（详见服务日志）' % len(cleaned['errors'])
    return {'ok': True, 'deleted': counts, 'note': note}, 200


# ── POST：上传与物料 ────────────────────────────────────────────────────────

def post_upload_init(payload):
    task_id = _text(payload, 'taskId')
    rel_path = _text(payload, 'relPath', limit=512)
    size = _int_arg(payload, 'size', low=1)
    owner_id = _owner()
    try:
        with sto.write_tx() as tx:
            def body(conn):
                row = store.require_task(conn, task_id, owner_id)
                if row is None:
                    raise sto.NotFound('生成任务不存在')
                return material_domain.upload_init(conn, owner_id, task_id, rel_path, size)
            return tx.run(body), 200
    except material_domain.Blacklisted as exc:
        # V2-4（08 §13）：命中黑名单的上传在独立短事务里登记过滤事件（G20 可见性）——
        # 主事务已回滚，事件不能写在被回滚的事务里；登记失败不改变 422 结论。
        event = exc.event if isinstance(exc.event, dict) else {
            'path': rel_path, 'layer': 'hard', 'rule': str(exc), 'size': 0}
        try:
            with sto.write_tx() as tx2:
                tx2.run(lambda conn: store.append_filter_events(conn, task_id, owner_id, [event]))
        except Exception:
            pass
        raise


def post_upload_chunk(payload):
    upload_id = _text(payload, 'uploadId')
    index = _int_arg(payload, 'index', low=0)
    chunk_hash = _text(payload, 'hash', limit=128)
    # dataBase64 的上限只由 chunkBytes 决定（D01）：这里按 2 MB 请求体上限放行，
    # 真正的「分片超量」判定在域层（LimitExceeded → 422 LIMIT_EXCEEDED）。
    # 不能沿用通用 _text 的 512 字符默认上限，否则任何 >384 字节的分片都会被 400 拒收。
    data_b64 = _text(payload, 'dataBase64', limit=2_000_000)
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
    # D05：材料清单的 CAS 基线是 materialRevision 的**字符串形态**（前端 String(revision)），
    # 不是任务 token，也不是数字。必填：缺失/空串 → 400，不匹配 → 409 + currentRevision。
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
            if revision != current:
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
    # V2-3：扫描可带 LLM 兜底解析——provider 缺失不阻断扫描（本地解析照常），仅跳过兜底。
    provider = _current_provider()
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            materials = [m for m in store.list_materials(conn, task_id, owner_id)
                         if not m['excluded'] and (not material_ids or m['id'] in material_ids)]
            if not materials:
                # 08 §4：无可用材料 → 422 INVALID_STATE（不是 400 形态错误）
                raise _blocked([{'code': 'NO_MATERIAL', 'field': 'materials',
                                 'message': '没有可扫描的材料（至少需要一份未排除材料）'}])
            for material in materials:
                if not material_ids and material['parseState'] == 'success' \
                        and int((material.get('coverage') or {}).get('factCount') or 0) > 0:
                    # 任务级扫描：已成功且内容未变的材料保留 success——run 内按内容哈希
                    # 复用跳过（08 §4 / G25d「重新扫描仅解析未完成文件」；此前的整体
                    # pending 重置使契约承诺的复用从未生效，属缺陷修复）。
                    continue
                store.update_material(conn, material['id'], owner_id, parse_state='pending', error='')
            run_id, _lease = store.create_run(conn, task_id, owner_id, 'scan',
                                              {'materialRevision': int(row['material_revision'])})
            store.touch_task(conn, task_id, owner_id, status='materials' if row['status'] == 'draft' else None,
                             stage_label='解析材料')
            return run_id, [m['id'] for m in materials]
        run_id, ids = tx.run(body)
    _run_task(owner_id, run_id,
              lambda user, run: pipeline.run_scan(user, task_id, run, provider=provider))
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
        return {'run': _v2_run_view(conn, run, store.run_view(run))}, 200


def _safe_schema_version(raw):
    """resume 预检的 checkpoint.generate.schemaVersion 安全解析（非整数绝不裸抛）。

    判据与 runner._plan_schema_version 一致（未知版本一律拒绝，绝不猜 0/1 放行）：
    缺失/空串 → None（按缺省走 legacy 路径）；bool 与不可解析值（'x'、'2.5'、仅空白、
    对象/数组等）→ -1（未知版本，fail-closed）；其余整数（含 '2' 这类整数字符串）→ 原值。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        if raw == '':
            return None
        try:
            return int(raw)
        except ValueError:
            return -1
    if isinstance(raw, bool):
        return -1
    if isinstance(raw, int):
        return raw
    return -1


def post_run_resume(payload):
    task_id = _text(payload, 'taskId')
    run_id = _text(payload, 'runId')
    # V2-8（08 §5）：resumeMode 控制 generate 断点续跑粒度——
    #   auto（默认）：按批次检查点只补跑失败批次，成功批次候选保留；
    #   abstract：复用已持久化的确定性阶段产物（筛选/对齐），但重跑全部抽象批次。
    # 非 generate 运行忽略该字段。
    resume_mode = payload.get('resumeMode', 'auto')
    if resume_mode not in ('auto', 'abstract'):
        raise ValueError('参数 resumeMode 只能是 auto 或 abstract')
    owner_id = _owner()
    from workbench.ontology_build import pipeline
    # ── v2 预检（08 §14.2）：任何状态变更前完成——版本守卫/受阻/轻量指纹/配置校验。──
    with sto.read_connection() as conn:
        pre_row = store.require_task(conn, task_id, owner_id)
        if pre_row is not None:
            pre_run = store.get_run(conn, run_id, owner_id) if run_id else None
            if pre_run is not None and pre_run['task_id'] == task_id                     and pre_run['kind'] == 'generate'                     and pre_run['state'] in ('failed', 'cancelled', 'interrupted'):
                raw_doc = store.run_checkpoint_doc(conn, run_id, owner_id)
                gen_doc = raw_doc.get('generate') if isinstance(raw_doc, dict) \
                    and isinstance(raw_doc.get('generate'), dict) else raw_doc
                if not isinstance(gen_doc, dict) or not gen_doc:
                    gen_doc = None
                # 版本解析走安全辅助：损坏值（'x'/'2.5'/对象等）按未知版本 → 422，
                # 绝不裸抛 int() 的 ValueError（曾把校验失败降级为 400 参数错误并泄露异常
                # 文本）；缺失/空串仍走缺省 legacy 路径。
                version = _safe_schema_version(
                    gen_doc.get('schemaVersion') if gen_doc is not None else None)
                if version is not None and version not in (1, 2):
                    raise _budget_issue(batch_contracts.UNKNOWN_CHECKPOINT_SCHEMA,
                                        '生成计划版本无法识别，拒绝恢复；原候选已保留。')
                if version == 2:
                    if gen_doc.get('blocking'):
                        raise _budget_issue('INVALID_STATE',
                                            '上次运行已受阻（%s）：%s 受阻需新建生成计划或补充'
                                            '材料，直接重试无法继续。'
                                            % (gen_doc['blocking'].get('code'),
                                               gen_doc['blocking'].get('message')))
                    if resume_mode == 'auto':
                        pre_scope = store.get_scope(conn, task_id, owner_id)
                        if int(gen_doc.get('scopeRevision') or 0) != int(pre_scope.get('revision') or 0):
                            raise _budget_issue(batch_contracts.BUDGET_PLAN_MISMATCH,
                                                '自适应计划与当前范围不一致（指纹已变化）：'
                                                '请重新生成建立新计划。')
                        if int(gen_doc.get('materialRevision') or 0) != int(pre_row['material_revision']):
                            raise _budget_issue(batch_contracts.BUDGET_PLAN_MISMATCH,
                                                '自适应计划与当前材料不一致（指纹已变化）：'
                                                '请重新生成建立新计划。')
                # 仅 schema2 计划才做 v2 预算预检：legacy 运行沿用既有语义（provider 缺失在
                    # 执行侧按 §2.1 处理，不得被前置拦截——否则破坏 legacy resume 行为）。
                if version == 2:
                    provider = _provider_or_raise()
                    _v2_precheck_generate(provider)
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
        return _resume_generate(owner_id, task_id, run_id, result['batchId'], resume_mode), 200
    if kind == 'scan':
        scan_provider = _current_provider()
        _run_task(owner_id, run_id,
                  lambda user, run: pipeline.run_scan(user, task_id, run, provider=scan_provider))
    else:
        _run_task(owner_id, run_id,
                  lambda user, run: pipeline.run_dialog(user, task_id, run, _provider_or_raise()))
    return {'runId': run_id}, 200


def _resume_generate(owner_id, task_id, run_id, batch_id, resume_mode='auto'):
    provider = _provider_or_raise()
    from workbench.ontology_build import pipeline
    _run_task(owner_id, run_id,
              lambda user, run: pipeline.run_generate(user, task_id, run, batch_id, provider,
                                                      resume_mode=resume_mode))
    return {'runId': run_id}


def _provider_or_raise():
    from workbench import llm_providers
    provider = llm_providers.default_provider()
    if not provider:
        # 08 §2.1：provider 为 null 时范围确认/生成/恢复等启动类操作 → 422 INVALID_STATE（不是 400）
        raise _no_provider()
    return provider


def _current_provider():
    """当前默认可用 provider；没有则 None（对话端点据此决定是否落助手错误）。"""
    from workbench import llm_providers
    try:
        return llm_providers.default_provider()
    except Exception:
        return None


def _no_provider():
    error = ValueError(NO_PROVIDER_MESSAGE)
    error.code = 'INVALID_STATE'
    error.status = 422
    return error


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
    # 08 §6：对话是**唯一例外**——provider 缺失时不返回 422，而是保留用户消息、
    # 以 200 + assistantError 上报（丢输入比错一个状态码更糟）；启动类操作仍 422。
    provider = _current_provider()
    if not provider:
        with sto.write_tx() as tx:
            tx.run(lambda conn: store.append_message(
                conn, task_id, owner_id, 'assistant', '', error=NO_PROVIDER_MESSAGE))
        return {'messageId': message_id, 'assistantPending': False,
                'assistantError': NO_PROVIDER_MESSAGE}, 200
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
        raise _no_provider()
    # 生成控输出 v3（08 §14.2）：预算预检在状态变更事务之前；422 拒绝时零状态变更。
    _v2_precheck_generate(provider)
    with sto.write_tx() as tx:
        def _preflight(conn):
            _v2_count_guard(conn, owner_id, task_id,
                            budget_profile.build_profile())
            return None
        tx.run(_preflight)
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
    if not confirmed:
        # 预览不改状态：不读 revision（08 §7）
        with sto.read_connection() as conn:
            return review_domain.merge_preview(conn, task_id, primary_id, merge_ids), 200
    # D05：执行合并是**保留项候选级 CAS**——revision 收候选的 r-uuid token，
    # 必填（缺失/空串 → 400），不匹配 → 409 REVISION_CONFLICT + currentRevision。
    revision = _text(payload, 'revision', limit=80)
    with sto.write_tx() as tx:
        result = tx.run(lambda conn: review_domain.merge_apply(conn, task_id, primary_id, merge_ids, revision))
    return result, 200


def post_review_undo(payload):
    task_id = _text(payload, 'taskId')
    op_id = _text(payload, 'opId')
    # D05：撤销同样按**保留项候选 token** 做 CAS（必填，缺失/空串 400，不匹配 409）
    revision = _text(payload, 'revision', limit=80)
    with sto.write_tx() as tx:
        result = tx.run(lambda conn: review_domain.undo_review_op(conn, task_id, op_id, revision))
    return result, 200


def post_regenerate(payload):
    task_id = _text(payload, 'taskId')
    # D05：再生成的 revision 是**可选整数**（scopeRevision），不是任务 token。
    # 省略（或空串）= 跳过范围比对，按当前范围重跑；传值必须是整数且等于当前
    # scopeRevision，否则 409 REVISION_CONFLICT + currentRevision。
    revision = _int_arg(payload, 'revision', required=False, default=None)
    owner_id = _owner()
    from workbench.ontology_build import pipeline
    provider = _provider_or_raise()
    # 生成控输出 v3（08 §14.2）：预算预检在状态变更事务之前；422 拒绝时零状态变更。
    _v2_precheck_generate(provider)
    with sto.write_tx() as tx:
        def body(conn):
            row = store.require_task(conn, task_id, owner_id)
            if row is None:
                raise sto.NotFound('生成任务不存在')
            _v2_count_guard(conn, owner_id, task_id, budget_profile.build_profile())
            scope = store.get_scope(conn, task_id, owner_id)
            if revision is not None and int(revision) != int(scope['revision']):
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
    # D05：差异裁决是**该候选**的候选级 CAS（必填 token；缺失/空串 400，不匹配 409）
    revision = _text(payload, 'revision', limit=80)
    with sto.write_tx() as tx:
        candidate = tx.run(lambda conn: review_domain.resolve_diff(
            conn, task_id, candidate_id, choice, revision))
    return {'candidate': candidate}, 200


# ── POST：交付 ──────────────────────────────────────────────────────────────

def post_deliver_precheck(payload):
    """交付前检查：只读，总是针对最近批次（不接受 batch 参数）。

    域层通过 DeliveryBlocked（422 + issues）表达无法检查的情形（已交付、无批次、
    结果已过期）；其余情形 200 且 `ok=false` + issues 可定位。
    """
    task_id = _text(payload, 'taskId')
    with sto.read_connection() as conn:
        return delivery_domain.precheck(conn, task_id), 200


def post_deliver(payload):
    task_id = _text(payload, 'taskId')
    name = _text(payload, 'name', limit=160)
    # D10：checkToken 必填——缺失/空串一律 400，杜绝「跳过预检直接提交」。
    # 令牌本身失效（选定集合或材料/范围已变）由域层复核并报 422 CHECK_TOKEN_STALE。
    check_token = _text(payload, 'checkToken', limit=64)
    request_id = _text(payload, 'requestId', limit=80)
    from workbench.ontology_build import delivery
    with sto.write_tx() as tx:
        try:
            return tx.run(lambda conn: delivery.deliver(
                conn, task_id, name, check_token, request_id)), 200
        except delivery.DuplicateOntologyName as exc:
            # 域层该类未挂 code/status（不改域层）：此处只补映射，不吞异常语义。
            # 其余域异常（DeliveryBlocked / AlreadyDelivered / RevisionConflict /
            # NotFound / InvalidStateError）一律原样上抛，由 server.py 按
            # code/status/issues 映射，禁止再包一层压成 400。
            error = ValueError(str(exc))
            error.code = 'DUPLICATE_NAME'
            error.status = 409
            raise error from None


def _owner():
    from workbench import auth
    return auth.require_user_id()

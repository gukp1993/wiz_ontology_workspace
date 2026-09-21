"""从物料构建本体：任务族 Repository（全部显式 owner + 短写事务）。

约定（与 assets.py 同构）：
* 只做数据裁决（归属、CAS、幂等、原子性），不懂业务校验；payload 由域层组装。
* 每个对外函数都带 owner_user_id；归属不匹配与不存在同样处理（NotFound / 空），
  绝不泄露他账号数据。任务级联表（材料/事实/消息/运行/候选/回执）一律先校验任务归属。
* 长任务（解析、LLM）不在这里持锁；调用方按「短事务检查 → 外部工作 → 短事务提交」
  推进状态，fencing token（lease_token）保证晚结果不写入新基线。
* 任务修订与内容 hash 分离：revision 是不透明 token 'r-<hex>'。

本模块是 ontology_build 包与 HTTP 路由层之间唯一的数据访问面；
解析器/生成管线不得直接拼 SQL。
"""
import json

from workbench.storage import engine as sto
from workbench.storage import assets as asset_store

TASK_KIND = 'build-task'


def _dumps(value):
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _loads(raw, fallback):
    try:
        value = json.loads(raw or '')
    except (ValueError, TypeError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def new_token():
    return 'r-' + sto.new_id().replace('-', '')


# --- 任务 -------------------------------------------------------------------------

def create_task(conn, owner_user_id, name, now=None):
    now = now or sto.utcnow()
    task_id = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_build_tasks (task_id, owner_user_id, name, name_key, '
                          'status, stage_label, revision, material_revision, scope_revision, '
                          'current_batch, delivery_ontology_id, created_at, updated_at) '
                          'VALUES (:t, :o, :n, :nk, :s, :sl, :r, 0, 0, \'\', \'\', :c, :c2)'),
                 {'t': task_id, 'o': owner_user_id or '', 'n': name, 'nk': name.strip().casefold(),
                  's': 'draft', 'sl': '新建', 'r': new_token(), 'c': now, 'c2': now})
    return task_id


def _task_row(conn, task_id, owner_user_id, include_deleted=False):
    sql = ('SELECT * FROM wb_build_tasks WHERE task_id = :t AND owner_user_id = :o')
    if not include_deleted:
        sql += ' AND deleted_at IS NULL'
    row = conn.execute(sto.text(sql), {'t': task_id, 'o': owner_user_id or ''}).mappings().first()
    return row


def task_view(row):
    """任务行 → 对外结构（HTTP 响应与前端一致；不含 owner）。"""
    return {
        'id': row['task_id'],
        'name': row['name'],
        'status': row['status'],
        'stageLabel': row['stage_label'],
        'revision': row['revision'],
        'materialRevision': int(row['material_revision']),
        'scopeRevision': int(row['scope_revision']),
        'currentBatch': row['current_batch'] or '',
        'deliveryOntologyId': row['delivery_ontology_id'] or '',
        # V2-4（08 §13）：任务级过滤设置（'.' 前缀小写后缀；softExts=None 表示用默认软名单）
        'filter': filter_spec_view(row),
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
    }


def filter_spec_view(row):
    """filter_json 列 → 任务过滤设置视图；缺列/坏 JSON 按未配置处理（迁移前旧行兼容）。"""
    try:
        raw = row['filter_json']
    except (KeyError, IndexError):
        return {'allowExts': [], 'softExts': None, 'excludeExts': []}
    value = _loads(raw if raw is not None else '{}', {})
    soft = value.get('softExts')
    return {
        'allowExts': [str(item) for item in value.get('allowExts') or []],
        'softExts': [str(item) for item in soft] if isinstance(soft, list) else None,
        'excludeExts': [str(item) for item in value.get('excludeExts') or []],
    }


def filter_report_view(row):
    """filter_report_json 列 → 被过滤文件报告视图（G20：计数 + 清单 + 命中规则）。"""
    try:
        raw = row['filter_report_json']
    except (KeyError, IndexError):
        return {'items': [], 'counts': {'hard': 0, 'soft': 0, 'custom': 0, 'total': 0},
                'truncated': False}
    value = _loads(raw if raw is not None else '{}', {})
    items = [item for item in value.get('items') or [] if isinstance(item, dict)]
    counts = value.get('counts') if isinstance(value.get('counts'), dict) else {}
    return {
        'items': items,
        'counts': {
            'hard': int(counts.get('hard') or 0),
            'soft': int(counts.get('soft') or 0),
            'custom': int(counts.get('custom') or 0),
            'total': int(counts.get('total') or 0),
        },
        'truncated': bool(value.get('truncated')),
    }


def set_task_filter(conn, task_id, owner_user_id, spec, now=None):
    """写入任务过滤设置（不推进任务 revision；调用方先用 touch_task 做 CAS）。"""
    conn.execute(sto.text('UPDATE wb_build_tasks SET filter_json = :f, updated_at = :n '
                          'WHERE task_id = :t AND owner_user_id = :o'),
                 {'f': _dumps(spec), 'n': now or sto.utcnow(), 't': task_id,
                  'o': owner_user_id or ''})


def append_filter_events(conn, task_id, owner_user_id, events, max_items=500, now=None):
    """登记被过滤文件事件（G20）：计数累加、清单保留最近 max_items 条、超限置 truncated。

    事件形状：{'path': str, 'layer': 'hard'|'soft'|'custom', 'rule': str, 'size': int}；
    同一任务累计（跨多次上传/展开），最近事件排在前面。失败不抛——过滤报告是辅助可见性，
    不应让上传/展开主流程回滚（与 blob 文件删除失败不回滚同一原则）。
    """
    try:
        row = _task_row(conn, task_id, owner_user_id)
        if row is None:
            return
        report = filter_report_view(row)
        merged = list(events or []) + report['items']
        counts = {'hard': 0, 'soft': 0, 'custom': 0, 'total': 0}
        for item in merged:
            layer = str(item.get('layer') or '')
            key = layer if layer in ('hard', 'soft', 'custom') else 'hard'
            counts[key] = counts.get(key, 0) + 1
            counts['total'] += 1
        payload = {
            'items': merged[:max_items],
            'counts': counts,
            'truncated': bool(report.get('truncated') or len(merged) > max_items),
        }
        conn.execute(sto.text('UPDATE wb_build_tasks SET filter_report_json = :r, updated_at = :n '
                              'WHERE task_id = :t AND owner_user_id = :o'),
                     {'r': _dumps(payload), 'n': now or sto.utcnow(), 't': task_id,
                      'o': owner_user_id or ''})
    except Exception:
        return


def get_task(conn, task_id, owner_user_id):
    row = _task_row(conn, task_id, owner_user_id)
    return task_view(row) if row else None


def require_task(conn, task_id, owner_user_id):
    """取任务行；不存在/跨账号返回 None（调用方按 404）。"""
    return _task_row(conn, task_id, owner_user_id)


def list_tasks(conn, owner_user_id, limit=20, offset=0):
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    rows = conn.execute(sto.text(
        'SELECT * FROM wb_build_tasks WHERE owner_user_id = :o AND deleted_at IS NULL '
        'ORDER BY updated_at DESC, task_id LIMIT :l OFFSET :f'),
        {'o': owner_user_id or '', 'l': limit, 'f': offset}).mappings().all()
    total = conn.execute(sto.text('SELECT COUNT(*) FROM wb_build_tasks '
                                  'WHERE owner_user_id = :o AND deleted_at IS NULL'),
                         {'o': owner_user_id or ''}).scalar()
    return [task_view(r) for r in rows], int(total or 0)


def touch_task(conn, task_id, owner_user_id, expected_revision=None, status=None,
               stage_label=None, material_revision=None, scope_revision=None,
               current_batch=None, delivery_ontology_id=None, now=None):
    """推进任务修订（CAS）并更新给定字段；返回新 revision，冲突抛 RevisionConflict。

    expected_revision 为 None 时以当前 revision 为基线（内部推进，例如状态机自己推进）。
    """
    row = _task_row(conn, task_id, owner_user_id)
    if row is None:
        raise sto.NotFound('生成任务不存在')
    base = row['revision'] if expected_revision is None else expected_revision
    new_rev = new_token()
    sets = ['revision = :r', 'updated_at = :now']
    params = {'r': new_rev, 'now': now or sto.utcnow(), 't': task_id, 'o': owner_user_id or '',
              'base': base}
    if status is not None:
        sets.append('status = :s'); params['s'] = status
    if stage_label is not None:
        sets.append('stage_label = :sl'); params['sl'] = stage_label
    if material_revision is not None:
        sets.append('material_revision = :mr'); params['mr'] = int(material_revision)
    if scope_revision is not None:
        sets.append('scope_revision = :sr'); params['sr'] = int(scope_revision)
    if current_batch is not None:
        sets.append('current_batch = :cb'); params['cb'] = current_batch or ''
    if delivery_ontology_id is not None:
        sets.append('delivery_ontology_id = :do'); params['do'] = delivery_ontology_id or ''
    result = conn.execute(sto.text('UPDATE wb_build_tasks SET ' + ', '.join(sets) +
                                   ' WHERE task_id = :t AND owner_user_id = :o AND revision = :base'),
                          params)
    if result.rowcount != 1:
        fresh = _task_row(conn, task_id, owner_user_id)
        raise sto.RevisionConflict(current_revision=(fresh or {}).get('revision', ''),
                                   message='任务已被其他操作更新，请刷新后重试')
    return new_rev


def soft_delete_task(conn, task_id, owner_user_id, now=None):
    """软删除任务（材料/候选/回执保留，便于审计与误删恢复的二次确认）。"""
    now = now or sto.utcnow()
    result = conn.execute(sto.text('UPDATE wb_build_tasks SET deleted_at = :n, updated_at = :n2 '
                                   'WHERE task_id = :t AND owner_user_id = :o AND deleted_at IS NULL'),
                          {'n': now, 'n2': now, 't': task_id, 'o': owner_user_id or ''})
    return result.rowcount == 1


def invalidate_batches(conn, task_id, owner_user_id, now=None):
    """材料/范围变化后把既有批次标 stale（旧结果只读，不可直接交付）。"""
    conn.execute(sto.text('UPDATE wb_build_batches SET stale = 1 WHERE task_id = :t '
                          'AND owner_user_id = :o'),
                 {'t': task_id, 'o': owner_user_id or ''})


# --- blob 与上传 ----------------------------------------------------------------

def create_blob(conn, owner_user_id, task_id, rel_path, size, content_hash, blob_path, now=None):
    now = now or sto.utcnow()
    blob_id = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_build_blobs (blob_id, owner_user_id, task_id, rel_path, '
                          'size, content_hash, blob_path, created_at) '
                          'VALUES (:b, :o, :t, :p, :s, :h, :bp, :c)'),
                 {'b': blob_id, 'o': owner_user_id or '', 't': task_id, 'p': rel_path,
                  's': int(size), 'h': content_hash, 'bp': blob_path, 'c': now})
    return blob_id


def get_blob(conn, blob_id, owner_user_id, task_id=None):
    sql = 'SELECT * FROM wb_build_blobs WHERE blob_id = :b AND owner_user_id = :o'
    params = {'b': blob_id, 'o': owner_user_id or ''}
    if task_id is not None:
        sql += ' AND task_id = :t'; params['t'] = task_id
    row = conn.execute(sto.text(sql), params).mappings().first()
    return dict(row) if row else None


def find_blob_by_hash(conn, task_id, owner_user_id, content_hash):
    """同任务内同内容 blob 复用（重复副本不重复占空间；归属仍按任务校验）。"""
    row = conn.execute(sto.text('SELECT * FROM wb_build_blobs WHERE task_id = :t '
                                'AND owner_user_id = :o AND content_hash = :h LIMIT 1'),
                       {'t': task_id, 'o': owner_user_id or '', 'h': content_hash}).mappings().first()
    return dict(row) if row else None


def task_storage_bytes(conn, task_id, owner_user_id):
    """任务已登记 blob 总字节（上传限额用；含上传中临时不可见）。"""
    value = conn.execute(sto.text('SELECT COALESCE(SUM(size), 0) FROM wb_build_blobs '
                                  'WHERE task_id = :t AND owner_user_id = :o'),
                         {'t': task_id, 'o': owner_user_id or ''}).scalar()
    return int(value or 0)


def create_upload(conn, owner_user_id, task_id, rel_path, size, chunk_bytes, temp_path, now=None):
    now = now or sto.utcnow()
    upload_id = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_build_uploads (upload_id, owner_user_id, task_id, rel_path, '
                          'size, chunk_bytes, chunks_json, received, state, temp_path, created_at, updated_at) '
                          "VALUES (:u, :o, :t, :p, :s, :cb, '{}', 0, 'open', :tp, :c, :c2)"),
                 {'u': upload_id, 'o': owner_user_id or '', 't': task_id, 'p': rel_path,
                  's': int(size), 'cb': int(chunk_bytes), 'tp': temp_path, 'c': now, 'c2': now})
    return upload_id


def get_upload(conn, upload_id, owner_user_id):
    row = conn.execute(sto.text('SELECT * FROM wb_build_uploads WHERE upload_id = :u '
                                'AND owner_user_id = :o'),
                       {'u': upload_id, 'o': owner_user_id or ''}).mappings().first()
    if not row:
        return None
    item = dict(row)
    item['chunks'] = _loads(item.pop('chunks_json'), {})
    return item


def upload_chunks(conn, upload_id, owner_user_id, chunks, received, now=None):
    conn.execute(sto.text('UPDATE wb_build_uploads SET chunks_json = :c, received = :r, '
                          'updated_at = :n WHERE upload_id = :u AND owner_user_id = :o'),
                 {'c': _dumps(chunks), 'r': int(received), 'n': now or sto.utcnow(),
                  'u': upload_id, 'o': owner_user_id or ''})


def close_upload(conn, upload_id, owner_user_id, state, now=None):
    conn.execute(sto.text('UPDATE wb_build_uploads SET state = :s, updated_at = :n '
                          'WHERE upload_id = :u AND owner_user_id = :o'),
                 {'s': state, 'n': now or sto.utcnow(), 'u': upload_id, 'o': owner_user_id or ''})


def list_uploads(conn, owner_user_id, task_id=None, states=('open',)):
    placeholders = ', '.join(':s%d' % i for i in range(len(states)))
    params = {'o': owner_user_id or ''}
    sql = 'SELECT * FROM wb_build_uploads WHERE owner_user_id = :o AND state IN (%s)' % placeholders
    for i, value in enumerate(states):
        params['s%d' % i] = value
    if task_id is not None:
        sql += ' AND task_id = :t'; params['t'] = task_id
    return [dict(r) for r in conn.execute(sto.text(sql), params).mappings().all()]


# --- 材料 -------------------------------------------------------------------------

def create_material(conn, owner_user_id, task_id, blob_id, rel_path, kind, size, content_hash,
                    source_group='', now=None):
    now = now or sto.utcnow()
    material_id = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_build_materials (material_id, task_id, owner_user_id, '
                          'blob_id, rel_path, kind, size, content_hash, parse_state, coverage_json, '
                          'source_group, excluded, error, revision, created_at, updated_at) '
                          "VALUES (:m, :t, :o, :b, :p, :k, :s, :h, 'pending', '{}', :sg, 0, '', :r, :c, :c2)"),
                 {'m': material_id, 't': task_id, 'o': owner_user_id or '', 'b': blob_id,
                  'p': rel_path, 'k': kind, 's': int(size), 'h': content_hash, 'sg': source_group,
                  'r': new_token(), 'c': now, 'c2': now})
    return material_id


def material_view(row):
    coverage = _loads(row['coverage_json'], {})
    return {
        'id': row['material_id'],
        'taskId': row['task_id'],
        'relPath': row['rel_path'],
        'kind': row['kind'],
        'size': int(row['size']),
        'contentHash': row['content_hash'],
        'parseState': row['parse_state'],
        'coverage': coverage,
        'sourceGroup': row['source_group'],
        'excluded': bool(row['excluded']),
        'error': row['error'],
        'revision': row['revision'],
    }


def get_material(conn, material_id, owner_user_id):
    row = conn.execute(sto.text('SELECT * FROM wb_build_materials WHERE material_id = :m '
                                'AND owner_user_id = :o'),
                       {'m': material_id, 'o': owner_user_id or ''}).mappings().first()
    return row


def list_materials(conn, task_id, owner_user_id):
    rows = conn.execute(sto.text('SELECT * FROM wb_build_materials WHERE task_id = :t '
                                 'AND owner_user_id = :o ORDER BY created_at, material_id'),
                        {'t': task_id, 'o': owner_user_id or ''}).mappings().all()
    return [material_view(r) for r in rows]


def list_material_groups(conn, task_id, owner_user_id):
    """按 relPath 顶层目录分组统计（08 §12.1）：folder='' 表示根目录文件。"""
    rows = conn.execute(sto.text('SELECT rel_path, parse_state, size FROM wb_build_materials '
                                 'WHERE task_id = :t AND owner_user_id = :o'),
                        {'t': task_id, 'o': owner_user_id or ''}).mappings().all()
    groups = {}
    total = 0
    for row in rows:
        rel = row['rel_path'] or ''
        folder = rel.split('/', 1)[0] if '/' in rel else ''
        group = groups.setdefault(folder, {'folder': folder, 'total': 0, 'byParseState': {}, 'bytes': 0})
        group['total'] += 1
        group['byParseState'][row['parse_state']] = group['byParseState'].get(row['parse_state'], 0) + 1
        group['bytes'] += int(row['size'] or 0)
        total += 1
    return sorted(groups.values(), key=lambda item: -item['total']), total


def list_materials_page(conn, task_id, owner_user_id, folder=None, offset=0, limit=100):
    """分页取物料；folder=None 全量（兼容），folder='src' 等为顶层组精确过滤（''=根目录组）。"""
    where = 'task_id = :t AND owner_user_id = :o'
    params = {'t': task_id, 'o': owner_user_id or ''}
    if folder is not None:
        where += (" AND (CASE WHEN instr(rel_path, '/') > 0 "
                  "THEN substr(rel_path, 1, instr(rel_path, '/') - 1) ELSE '' END) = :folder")
        params['folder'] = folder
    total = int(conn.execute(sto.text('SELECT COUNT(*) FROM wb_build_materials WHERE ' + where),
                             params).scalar() or 0)
    rows = conn.execute(sto.text('SELECT * FROM wb_build_materials WHERE ' + where +
                                 ' ORDER BY rel_path, material_id LIMIT :l OFFSET :f'),
                        dict(params, l=max(1, min(int(limit or 100), 500)),
                             f=max(0, int(offset or 0)))).mappings().all()
    return [material_view(r) for r in rows], total


def update_material(conn, material_id, owner_user_id, parse_state=None, coverage=None,
                    excluded=None, error=None, now=None):
    sets, params = ['updated_at = :n'], {'n': now or sto.utcnow(), 'm': material_id,
                                         'o': owner_user_id or ''}
    if parse_state is not None:
        sets.append('parse_state = :ps'); params['ps'] = parse_state
    if coverage is not None:
        sets.append('coverage_json = :cv'); params['cv'] = _dumps(coverage)
    if excluded is not None:
        sets.append('excluded = :ex'); params['ex'] = 1 if excluded else 0
    if error is not None:
        sets.append('error = :er'); params['er'] = error
    conn.execute(sto.text('UPDATE wb_build_materials SET ' + ', '.join(sets) +
                          ' WHERE material_id = :m AND owner_user_id = :o'), params)


def material_ids_by_hash(conn, task_id, owner_user_id, content_hash):
    row = conn.execute(sto.text('SELECT material_id FROM wb_build_materials WHERE task_id = :t '
                                'AND owner_user_id = :o AND content_hash = :h LIMIT 1'),
                       {'t': task_id, 'o': owner_user_id or '', 'h': content_hash}).first()
    return row[0] if row else None


# --- 事实 -------------------------------------------------------------------------

def replace_material_facts(conn, task_id, owner_user_id, material_id, facts, now=None):
    """按材料整体替换事实（重试/重解析幂等）；facts 为已定结构 dict 列表。"""
    now = now or sto.utcnow()
    conn.execute(sto.text('DELETE FROM wb_build_facts WHERE material_id = :m AND task_id = :t '
                          'AND owner_user_id = :o'),
                 {'m': material_id, 't': task_id, 'o': owner_user_id or ''})
    for fact in facts:
        conn.execute(sto.text('INSERT INTO wb_build_facts (fact_id, task_id, owner_user_id, '
                              'material_id, module, locator_json, snippet, kind, data_json, quality, '
                              'created_at) VALUES (:f, :t, :o, :m, :md, :l, :sn, :k, :d, :q, :c)'),
                     {'f': fact['id'], 't': task_id, 'o': owner_user_id or '', 'm': material_id,
                      'md': fact.get('module', ''), 'l': _dumps(fact.get('locator', {})),
                      'sn': fact.get('snippet', ''), 'k': fact.get('kind', ''),
                      'd': _dumps(fact.get('data', {})), 'q': fact.get('quality', 'high'), 'c': now})


def fact_view(row):
    return {
        'id': row['fact_id'],
        'taskId': row['task_id'],
        'materialId': row['material_id'],
        'module': row['module'],
        'locator': _loads(row['locator_json'], {}),
        'snippet': row['snippet'],
        'kind': row['kind'],
        'data': _loads(row['data_json'], {}),
        'quality': row['quality'],
    }


def facts_by_ids(conn, task_id, owner_user_id, fact_ids):
    """按 ID 批量取事实（证据解析用）；只返回属于本任务的记录。"""
    ids = [str(i) for i in (fact_ids or []) if i]
    if not ids:
        return {}
    out = {}
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        placeholders = ', '.join(':i%d' % i for i in range(len(chunk)))
        params = {'t': task_id, 'o': owner_user_id or ''}
        for i, value in enumerate(chunk):
            params['i%d' % i] = value
        rows = conn.execute(sto.text('SELECT * FROM wb_build_facts WHERE task_id = :t '
                                     'AND owner_user_id = :o AND fact_id IN (%s)' % placeholders),
                            params).mappings().all()
        for row in rows:
            out[row['fact_id']] = fact_view(row)
    return out


def list_facts(conn, task_id, owner_user_id, material_id=None):
    params = {'t': task_id, 'o': owner_user_id or ''}
    sql = 'SELECT * FROM wb_build_facts WHERE task_id = :t AND owner_user_id = :o'
    if material_id:
        sql += ' AND material_id = :m'; params['m'] = material_id
    rows = conn.execute(sto.text(sql + ' ORDER BY material_id, fact_id'), params).mappings().all()
    return [fact_view(r) for r in rows]


def count_facts(conn, task_id, owner_user_id):
    return int(conn.execute(sto.text('SELECT COUNT(*) FROM wb_build_facts WHERE task_id = :t '
                                     'AND owner_user_id = :o'),
                            {'t': task_id, 'o': owner_user_id or ''}).scalar() or 0)


# --- 会话与范围 -------------------------------------------------------------------

def append_message(conn, task_id, owner_user_id, role, content, patch=None, error='',
                   scope_revision=0, now=None):
    now = now or sto.utcnow()
    message_id = sto.new_id()
    seq = int(conn.execute(sto.text('SELECT COALESCE(MAX(seq), 0) + 1 FROM wb_build_messages '
                                    'WHERE task_id = :t AND owner_user_id = :o'),
                           {'t': task_id, 'o': owner_user_id or ''}).scalar() or 1)
    conn.execute(sto.text('INSERT INTO wb_build_messages (message_id, task_id, owner_user_id, seq, '
                          'role, content, patch_json, error, scope_revision, created_at) '
                          "VALUES (:m, :t, :o, :q, :r, :c, :p, :e, :sr, :n)"),
                 {'m': message_id, 't': task_id, 'o': owner_user_id or '', 'q': seq, 'r': role,
                  'c': content, 'p': _dumps(patch or {}), 'e': error or '',
                  'sr': int(scope_revision or 0), 'n': now})
    return message_id, seq


def message_view(row):
    return {
        'id': row['message_id'],
        'seq': int(row['seq']),
        'role': row['role'],
        'content': row['content'],
        'patch': _loads(row['patch_json'], {}),
        'error': row['error'],
        'scopeRevision': int(row['scope_revision']),
        'createdAt': row['created_at'],
    }


def list_messages(conn, task_id, owner_user_id, after=0):
    rows = conn.execute(sto.text('SELECT * FROM wb_build_messages WHERE task_id = :t '
                                 'AND owner_user_id = :o AND seq > :a ORDER BY seq'),
                        {'t': task_id, 'o': owner_user_id or '', 'a': int(after or 0)}).mappings().all()
    return [message_view(r) for r in rows]


def get_scope(conn, task_id, owner_user_id):
    row = conn.execute(sto.text('SELECT * FROM wb_build_scopes WHERE task_id = :t '
                                'AND owner_user_id = :o'),
                       {'t': task_id, 'o': owner_user_id or ''}).mappings().first()
    if not row:
        return {'taskId': task_id, 'goal': '', 'include': '', 'exclude': '', 'relations': '',
                'coverage': '', 'openQuestions': [], 'confirmed': False, 'revision': 0,
                'confirmedAt': '', 'providerFingerprint': {}}
    payload = _loads(row['payload_json'], {})
    payload.setdefault('openQuestions', [])
    return {
        'taskId': task_id,
        'goal': str(payload.get('goal') or ''),
        'include': str(payload.get('include') or ''),
        'exclude': str(payload.get('exclude') or ''),
        'relations': str(payload.get('relations') or ''),
        'coverage': str(payload.get('coverage') or ''),
        'openQuestions': payload.get('openQuestions') if isinstance(payload.get('openQuestions'), list) else [],
        'confirmed': bool(row['confirmed_at']),
        'revision': int(row['revision']),
        'confirmedAt': row['confirmed_at'] or '',
        'providerFingerprint': _loads(row['provider_fingerprint'], {}),
    }


def put_scope(conn, task_id, owner_user_id, payload, expected_revision=None, confirmed=False,
              provider_fingerprint=None, now=None):
    """写入范围；expected_revision 不为 None 时做 CAS（人工编辑保护）。返回新 revision。"""
    now = now or sto.utcnow()
    row = conn.execute(sto.text('SELECT * FROM wb_build_scopes WHERE task_id = :t '
                                'AND owner_user_id = :o'),
                       {'t': task_id, 'o': owner_user_id or ''}).mappings().first()
    current = int(row['revision']) if row else 0
    if expected_revision is not None and int(expected_revision) != current:
        raise sto.RevisionConflict(current_revision=str(current),
                                   message='范围摘要有新的修订，请刷新后重试')
    revision = current + 1
    confirmed_at = (row['confirmed_at'] if row and not confirmed else (now if confirmed else ''))
    fingerprint = _dumps(provider_fingerprint if provider_fingerprint is not None
                         else (_loads((row or {}).get('provider_fingerprint', '{}'), {})))
    if row:
        conn.execute(sto.text('UPDATE wb_build_scopes SET payload_json = :p, revision = :r, '
                              'confirmed_at = :ca, provider_fingerprint = :fp, updated_at = :n '
                              'WHERE task_id = :t AND owner_user_id = :o'),
                     {'p': _dumps(payload), 'r': revision, 'ca': confirmed_at or '',
                      'fp': fingerprint, 'n': now, 't': task_id, 'o': owner_user_id or ''})
    else:
        conn.execute(sto.text('INSERT INTO wb_build_scopes (task_id, owner_user_id, payload_json, '
                              'revision, confirmed_at, provider_fingerprint, updated_at) '
                              'VALUES (:t, :o, :p, :r, :ca, :fp, :n)'),
                     {'t': task_id, 'o': owner_user_id or '', 'p': _dumps(payload), 'r': revision,
                      'ca': confirmed_at or '', 'fp': fingerprint, 'n': now})
    return revision


# --- 运行与批次 -------------------------------------------------------------------

def create_run(conn, task_id, owner_user_id, kind, baseline=None, batch_id='', now=None):
    now = now or sto.utcnow()
    run_id = sto.new_id()
    lease = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_build_runs (run_id, task_id, owner_user_id, kind, state, '
                          'stage, stage_label, progress_json, baseline_json, attempt, error, '
                          'retryable, usage_json, batch_id, lease_token, cancel_requested, '
                          "checkpoint_json, created_at, updated_at) "
                          "VALUES (:r, :t, :o, :k, 'queued', '', '', '{}', :b, 1, '', 0, '{}', "
                          ":bd, :l, 0, '{}', :c, :c2)"),
                 {'r': run_id, 't': task_id, 'o': owner_user_id or '', 'k': kind,
                  'b': _dumps(baseline or {}), 'bd': batch_id or '', 'l': lease,
                  'c': now, 'c2': now})
    return run_id, lease


def run_view(row):
    return {
        'id': row['run_id'],
        'taskId': row['task_id'],
        'kind': row['kind'],
        'state': row['state'],
        'stage': row['stage'],
        'stageLabel': row['stage_label'],
        'progress': _loads(row['progress_json'], {}),
        'baseline': _loads(row['baseline_json'], {}),
        'attempt': int(row['attempt']),
        'error': row['error'],
        'retryable': bool(row['retryable']),
        'usage': _loads(row['usage_json'], {}),
        'batchId': row['batch_id'] or '',
        'cancelRequested': bool(row['cancel_requested']),
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
    }


def get_run(conn, run_id, owner_user_id):
    return conn.execute(sto.text('SELECT * FROM wb_build_runs WHERE run_id = :r '
                                 'AND owner_user_id = :o'),
                        {'r': run_id, 'o': owner_user_id or ''}).mappings().first()


def latest_run(conn, task_id, owner_user_id, kind=None):
    params = {'t': task_id, 'o': owner_user_id or ''}
    sql = 'SELECT * FROM wb_build_runs WHERE task_id = :t AND owner_user_id = :o'
    if kind:
        sql += ' AND kind = :k'; params['k'] = kind
    return conn.execute(sto.text(sql + ' ORDER BY created_at DESC, run_id DESC LIMIT 1'),
                        params).mappings().first()


def update_run(conn, run_id, owner_user_id, state=None, stage=None, stage_label=None,
               progress=None, error=None, retryable=None, usage=None, cancel_requested=None,
               checkpoint=None, attempt=None, lease=None, now=None):
    """更新运行行；返回是否命中（rowcount==1）。

    fencing（D13）：
    * 传入 `attempt`（重试计数推进）即视为新一次接管，**同步轮换 lease_token**，
      旧 worker 手里的一切写入从此失效；
    * 传入 `lease` 时 UPDATE 带 `lease_token = :lease` 条件，不匹配返回 False，
      由调用方（runner）丢弃晚结果，绝不静默改写新接管运行的状态。
    """
    sets, params = ['updated_at = :n'], {'n': now or sto.utcnow(), 'r': run_id,
                                         'o': owner_user_id or ''}
    if state is not None:
        sets.append('state = :s'); params['s'] = state
    if stage is not None:
        sets.append('stage = :st'); params['st'] = stage
    if stage_label is not None:
        sets.append('stage_label = :sl'); params['sl'] = stage_label
    if progress is not None:
        sets.append('progress_json = :p'); params['p'] = _dumps(progress)
    if error is not None:
        sets.append('error = :e'); params['e'] = error
    if retryable is not None:
        sets.append('retryable = :rt'); params['rt'] = 1 if retryable else 0
    if usage is not None:
        sets.append('usage_json = :u'); params['u'] = _dumps(usage)
    if cancel_requested is not None:
        sets.append('cancel_requested = :cr'); params['cr'] = 1 if cancel_requested else 0
    if checkpoint is not None:
        sets.append('checkpoint_json = :cp'); params['cp'] = _dumps(checkpoint)
    if attempt is not None:
        sets.append('attempt = :a'); params['a'] = int(attempt)
        new_lease = sto.new_id()
        sets.append('lease_token = :lease_new'); params['lease_new'] = new_lease
    where = ' WHERE run_id = :r AND owner_user_id = :o'
    if lease:
        params['lease'] = lease
        where += ' AND lease_token = :lease'
    result = conn.execute(sto.text('UPDATE wb_build_runs SET ' + ', '.join(sets) + where),
                          params)
    return result.rowcount == 1


def rotate_run_lease(conn, run_id, owner_user_id):
    """显式轮换 fencing token（重试接管时调用）；返回新 lease，运行不存在返回 ''。"""
    new_lease = sto.new_id()
    result = conn.execute(sto.text('UPDATE wb_build_runs SET lease_token = :l, updated_at = :n '
                                   'WHERE run_id = :r AND owner_user_id = :o'),
                          {'l': new_lease, 'n': sto.utcnow(), 'r': run_id,
                           'o': owner_user_id or ''})
    return new_lease if result.rowcount == 1 else ''


def run_state(conn, run_id, owner_user_id):
    """轻量读：外部工作期间检查取消/中断（不返回详情）。"""
    row = conn.execute(sto.text('SELECT state, cancel_requested, lease_token, baseline_json '
                                'FROM wb_build_runs WHERE run_id = :r AND owner_user_id = :o'),
                       {'r': run_id, 'o': owner_user_id or ''}).mappings().first()
    return dict(row) if row else None


def mark_stale_runs_interrupted(conn, now=None):
    """服务启动时把所有 queued/running 标为 interrupted（绝不假装仍在运行）。"""
    now = now or sto.utcnow()
    result = conn.execute(sto.text("UPDATE wb_build_runs SET state = 'interrupted', "
                                   "error = '服务重启，执行已中断；可重试', retryable = 1, "
                                   'updated_at = :n WHERE state IN (\'queued\', \'running\')'),
                          {'n': now})
    return int(result.rowcount or 0)


def create_batch(conn, task_id, owner_user_id, run_id, baseline, now=None):
    now = now or sto.utcnow()
    batch_id = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_build_batches (batch_id, task_id, owner_user_id, run_id, '
                          'baseline_json, stale, created_at) VALUES (:b, :t, :o, :r, :bl, 0, :c)'),
                 {'b': batch_id, 't': task_id, 'o': owner_user_id or '', 'r': run_id,
                  'bl': _dumps(baseline or {}), 'c': now})
    return batch_id


def batch_view(row):
    return {'id': row['batch_id'], 'taskId': row['task_id'], 'runId': row['run_id'],
            'baseline': _loads(row['baseline_json'], {}), 'stale': bool(row['stale']),
            'createdAt': row['created_at']}


def get_batch(conn, batch_id, owner_user_id):
    return conn.execute(sto.text('SELECT * FROM wb_build_batches WHERE batch_id = :b '
                                 'AND owner_user_id = :o'),
                        {'b': batch_id, 'o': owner_user_id or ''}).mappings().first()


def latest_batch(conn, task_id, owner_user_id):
    return conn.execute(sto.text('SELECT * FROM wb_build_batches WHERE task_id = :t '
                                 'AND owner_user_id = :o ORDER BY created_at DESC, batch_id DESC LIMIT 1'),
                        {'t': task_id, 'o': owner_user_id or ''}).mappings().first()


def list_batches(conn, task_id, owner_user_id, limit=20):
    rows = conn.execute(sto.text('SELECT * FROM wb_build_batches WHERE task_id = :t '
                                 'AND owner_user_id = :o ORDER BY created_at DESC LIMIT :l'),
                        {'t': task_id, 'o': owner_user_id or '', 'l': int(limit)}).mappings().all()
    return [batch_view(r) for r in rows]


# --- 候选 -------------------------------------------------------------------------

def create_candidate(conn, task_id, owner_user_id, batch_id, payload, now=None):
    now = now or sto.utcnow()
    candidate_id = payload.get('id') or sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_build_candidates (candidate_id, task_id, batch_id, '
                          'owner_user_id, ctype, ckey, name, definition, fields_json, owner_key, '
                          'evidence_json, evidence_status, conflicts_json, decision, reviewed, '
                          'reason, issues_json, origin_json, aligned_key, revision, created_at, updated_at) '
                          "VALUES (:c, :t, :b, :o, :ty, :k, :n, :d, :f, :ok, :ev, :es, :cf, :dc, 0, "
                          "'' , :is_, :og, :ak, :r, :now, :now2)"),
                 {'c': candidate_id, 't': task_id, 'o': owner_user_id or '', 'b': batch_id,
                  'ty': payload.get('type', 'object'), 'k': payload.get('key', ''),
                  'n': payload.get('name', ''), 'd': payload.get('definition', ''),
                  'f': _dumps(payload.get('fields', {})), 'ok': payload.get('ownerKey', ''),
                  'ev': _dumps(payload.get('evidence', {})),
                  'es': payload.get('evidenceStatus', 'inferred'),
                  'cf': _dumps(payload.get('conflicts', [])),
                  'dc': payload.get('decision', 'defer'),
                  'is_': _dumps(payload.get('issues', [])), 'og': _dumps(payload.get('origin', {})),
                  'ak': payload.get('alignedKey', ''), 'r': new_token(), 'now': now, 'now2': now})
    return candidate_id


def candidate_view(row):
    return {
        'id': row['candidate_id'],
        'taskId': row['task_id'],
        'batchId': row['batch_id'],
        'type': row['ctype'],
        'key': row['ckey'],
        'name': row['name'],
        'definition': row['definition'],
        'fields': _loads(row['fields_json'], {}),
        'ownerKey': row['owner_key'],
        'evidence': _loads(row['evidence_json'], {}),
        'evidenceStatus': row['evidence_status'],
        'conflicts': _loads(row['conflicts_json'], []),
        'decision': row['decision'],
        'reviewed': bool(row['reviewed']),
        'reason': row['reason'],
        'issues': _loads(row['issues_json'], []),
        'origin': _loads(row['origin_json'], {}),
        'alignedKey': row['aligned_key'],
        'revision': row['revision'],
    }


def get_candidate(conn, candidate_id, owner_user_id):
    return conn.execute(sto.text('SELECT * FROM wb_build_candidates WHERE candidate_id = :c '
                                 'AND owner_user_id = :o'),
                        {'c': candidate_id, 'o': owner_user_id or ''}).mappings().first()


def _unmerged_clause():
    """「未被合并掉」的 SQL 判定（D03）：origin_json 里 mergedInto 为空/缺失/null。

    候选 ID 是裸 uuid（无 'bc-' 前缀），旧的 `NOT LIKE '%"mergedInto": "bc%'` 永不
    匹配；必须按 JSON 字段实际取值判断。origin_json 为 NULL/''（历史行）同样保留。
    MySQL 8 的 JSON_EXTRACT 与 SQLite json_extract 同名同路径语法（预留兼容）。
    """
    return ("(origin_json IS NULL OR origin_json = '' OR "
            "json_extract(origin_json, '$.mergedInto') IS NULL)")


def list_candidates(conn, task_id, owner_user_id, batch_id=None, ctype=None, decision=None,
                    evidence_status=None, query=None, offset=0, limit=100, include_merged=False):
    """候选列表 + 计数；默认过滤已合并候选（origin.mergedInto 非空）。"""
    params = {'t': task_id, 'o': owner_user_id or ''}
    where = ['task_id = :t', 'owner_user_id = :o']
    if batch_id:
        where.append('batch_id = :b'); params['b'] = batch_id
    if ctype:
        where.append('ctype = :ty'); params['ty'] = ctype
    if decision:
        where.append('decision = :dc'); params['dc'] = decision
    if evidence_status:
        where.append('evidence_status = :es'); params['es'] = evidence_status
    if query:
        where.append('(name LIKE :q OR definition LIKE :q)'); params['q'] = '%' + str(query) + '%'
    if not include_merged:
        where.append(_unmerged_clause())
    clause = ' AND '.join(where)
    total = int(conn.execute(sto.text('SELECT COUNT(*) FROM wb_build_candidates WHERE ' + clause),
                             params).scalar() or 0)
    params['l'] = max(1, min(int(limit or 100), 500))
    params['f'] = max(0, int(offset or 0))
    rows = conn.execute(sto.text('SELECT * FROM wb_build_candidates WHERE ' + clause +
                                 ' ORDER BY ctype, name, candidate_id LIMIT :l OFFSET :f'),
                        params).mappings().all()
    return [candidate_view(r) for r in rows], total


def all_candidates(conn, task_id, owner_user_id, batch_id=None, include_merged=False):
    return list_candidates(conn, task_id, owner_user_id, batch_id=batch_id, limit=500,
                           include_merged=include_merged)[0]


def candidate_counts(conn, task_id, owner_user_id, batch_id=None):
    params = {'t': task_id, 'o': owner_user_id or ''}
    where = ['task_id = :t', 'owner_user_id = :o', _unmerged_clause()]
    if batch_id:
        where.append('batch_id = :b'); params['b'] = batch_id
    clause = ' AND '.join(where)
    rows = conn.execute(sto.text('SELECT ctype, decision, evidence_status, COUNT(*) AS n '
                                 'FROM wb_build_candidates WHERE ' + clause +
                                 ' GROUP BY ctype, decision, evidence_status'), params).mappings().all()
    by_type, by_decision, by_status = {}, {}, {}
    for row in rows:
        n = int(row['n'])
        by_type[row['ctype']] = by_type.get(row['ctype'], 0) + n
        by_decision[row['decision']] = by_decision.get(row['decision'], 0) + n
        by_status[row['evidence_status']] = by_status.get(row['evidence_status'], 0) + n
    return {'byType': by_type, 'byDecision': by_decision, 'byStatus': by_status}


def update_candidate(conn, candidate_id, owner_user_id, expected_revision=None, name=None,
                     definition=None, fields=None, decision=None, reviewed=None, reason=None,
                     issues=None, origin=None, owner_key=None, evidence_status=None, now=None):
    row = get_candidate(conn, candidate_id, owner_user_id)
    if row is None:
        return None
    base = row['revision'] if expected_revision is None else expected_revision
    sets, params = ['revision = :r', 'updated_at = :n'], {
        'r': new_token(), 'n': now or sto.utcnow(), 'c': candidate_id, 'o': owner_user_id or '',
        'base': base}
    if name is not None:
        sets.append('name = :nm'); params['nm'] = name
    if definition is not None:
        sets.append('definition = :df'); params['df'] = definition
    if fields is not None:
        sets.append('fields_json = :fj'); params['fj'] = _dumps(fields)
    if decision is not None:
        sets.append('decision = :dc'); params['dc'] = decision
    if reviewed is not None:
        sets.append('reviewed = :rv'); params['rv'] = 1 if reviewed else 0
    if reason is not None:
        sets.append('reason = :rs'); params['rs'] = reason
    if issues is not None:
        sets.append('issues_json = :ij'); params['ij'] = _dumps(issues)
    if origin is not None:
        sets.append('origin_json = :oj'); params['oj'] = _dumps(origin)
    if owner_key is not None:
        sets.append('owner_key = :ok'); params['ok'] = owner_key
    if evidence_status is not None:
        sets.append('evidence_status = :es'); params['es'] = evidence_status
    result = conn.execute(sto.text('UPDATE wb_build_candidates SET ' + ', '.join(sets) +
                                   ' WHERE candidate_id = :c AND owner_user_id = :o '
                                   'AND revision = :base'), params)
    if result.rowcount != 1:
        fresh = get_candidate(conn, candidate_id, owner_user_id)
        raise sto.RevisionConflict(current_revision=(fresh or {}).get('revision', ''),
                                   message='候选定义已被其他操作更新，请刷新后重试')
    return get_candidate(conn, candidate_id, owner_user_id)


def delete_candidates_of_batch(conn, task_id, owner_user_id, batch_id):
    conn.execute(sto.text('DELETE FROM wb_build_candidates WHERE task_id = :t '
                          'AND owner_user_id = :o AND batch_id = :b'),
                 {'t': task_id, 'o': owner_user_id or '', 'b': batch_id})


# --- 评审操作与回执 ---------------------------------------------------------------

def append_review_op(conn, task_id, owner_user_id, kind, payload, now=None):
    now = now or sto.utcnow()
    op_id = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_build_review_ops (op_id, task_id, owner_user_id, kind, '
                          'payload_json, reverted, created_at) VALUES (:o, :t, :u, :k, :p, 0, :n)'),
                 {'o': op_id, 't': task_id, 'u': owner_user_id or '', 'k': kind,
                  'p': _dumps(payload), 'n': now})
    return op_id


def get_review_op(conn, op_id, owner_user_id):
    row = conn.execute(sto.text('SELECT * FROM wb_build_review_ops WHERE op_id = :o '
                                'AND owner_user_id = :u'),
                       {'o': op_id, 'u': owner_user_id or ''}).mappings().first()
    if not row:
        return None
    item = dict(row)
    item['payload'] = _loads(item.pop('payload_json'), {})
    return item


def mark_review_op_reverted(conn, op_id, owner_user_id):
    result = conn.execute(sto.text('UPDATE wb_build_review_ops SET reverted = 1 WHERE op_id = :o '
                                   'AND owner_user_id = :u AND reverted = 0'),
                          {'o': op_id, 'u': owner_user_id or ''})
    return result.rowcount == 1


def get_delivery(conn, task_id, owner_user_id):
    row = conn.execute(sto.text('SELECT * FROM wb_build_deliveries WHERE task_id = :t '
                                'AND owner_user_id = :o'),
                       {'t': task_id, 'o': owner_user_id or ''}).mappings().first()
    if not row:
        return None
    return {'id': row['delivery_id'], 'taskId': row['task_id'], 'requestId': row['request_id'],
            'ontologyId': row['ontology_id'], 'createdAt': row['created_at']}


def get_delivery_by_request(conn, owner_user_id, request_id):
    row = conn.execute(sto.text('SELECT * FROM wb_build_deliveries WHERE owner_user_id = :o '
                                'AND request_id = :r'),
                       {'o': owner_user_id or '', 'r': request_id}).mappings().first()
    if not row:
        return None
    return {'id': row['delivery_id'], 'taskId': row['task_id'], 'requestId': row['request_id'],
            'ontologyId': row['ontology_id'], 'payloadDigest': row['payload_digest'],
            'createdAt': row['created_at']}


def insert_delivery(conn, task_id, owner_user_id, request_id, payload_digest, ontology_id, now=None):
    now = now or sto.utcnow()
    delivery_id = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_build_deliveries (delivery_id, task_id, owner_user_id, '
                          'request_id, payload_digest, ontology_id, created_at) '
                          'VALUES (:d, :t, :o, :r, :p, :nid, :n)'),
                 {'d': delivery_id, 't': task_id, 'o': owner_user_id or '', 'r': request_id,
                  'p': payload_digest, 'nid': ontology_id, 'n': now})
    return delivery_id


# --- 任务级联物理清理（08 §12.2）------------------------------------------------

def purge_task(conn, task_id, owner_user_id):
    """物理删除任务及全部关联行；返回 (各类删除计数, blob 相对路径列表)。

    只删行不删文件：blob 文件由调用方在事务提交后经 materials.delete_task_blob_files
    删除（文件删除失败不回滚数据库）。已交付的本体草稿在 wb_assets 侧，不受影响。
    """
    row = _task_row(conn, task_id, owner_user_id)
    if row is None:
        raise sto.NotFound('生成任务不存在')
    blob_paths = [r['blob_path'] for r in conn.execute(
        sto.text('SELECT DISTINCT blob_path FROM wb_build_blobs '
                 'WHERE task_id = :t AND owner_user_id = :o'),
        {'t': task_id, 'o': owner_user_id or ''}).mappings().all()]
    counts = {}
    for table in ('wb_build_materials', 'wb_build_facts', 'wb_build_messages',
                  'wb_build_runs', 'wb_build_batches', 'wb_build_candidates',
                  'wb_build_review_ops', 'wb_build_uploads', 'wb_build_deliveries',
                  'wb_build_blobs'):
        result = conn.execute(sto.text('DELETE FROM ' + table +
                                       ' WHERE task_id = :t AND owner_user_id = :o'),
                              {'t': task_id, 'o': owner_user_id or ''})
        counts[table] = int(result.rowcount or 0)
    result = conn.execute(sto.text('DELETE FROM wb_build_scopes '
                                   'WHERE task_id = :t AND owner_user_id = :o'),
                          {'t': task_id, 'o': owner_user_id or ''})
    counts['wb_build_scopes'] = int(result.rowcount or 0)
    result = conn.execute(sto.text('DELETE FROM wb_build_tasks '
                                   'WHERE task_id = :t AND owner_user_id = :o'),
                          {'t': task_id, 'o': owner_user_id or ''})
    counts['wb_build_tasks'] = int(result.rowcount or 0)
    return counts, blob_paths


# --- 交付复用：新建本体资产（与 workspaces 同事务） -------------------------------

def create_ontology_asset(conn, owner_user_id, external_id, name, payload, payload_format,
                          summary=None, purpose='draft', now=None):
    """在当前写事务内创建一份新本体资产 + 首个快照 + head。

    与 workspaces.create 的差异：不自行提交事务，可参与交付的原子事务
    （候选映射、任务回执、任务状态与本体创建同生共死）。
    """
    now = now or sto.utcnow()
    asset_uid = asset_store.ensure_asset(conn, 'model', external_id, name, summary, now,
                                         owner_user_id=owner_user_id)
    snapshot = asset_store.append_snapshot(conn, asset_uid, payload, payload_format, purpose,
                                           '', now)
    token = asset_store.new_token()
    conn.execute(sto.text('INSERT INTO wb_asset_heads (asset_uid, snapshot_id, revision_token, '
                          'generation, snapshot_seq, release_seq, updated_at) '
                          'VALUES (:a, :s, :t, 1, :q, 0, :now)'),
                 {'a': asset_uid, 's': snapshot['snapshot_id'], 't': token,
                  'q': snapshot['seq'], 'now': now})
    return {'assetUid': asset_uid, 'snapshotId': snapshot['snapshot_id'], 'revision': token,
            'seq': snapshot['seq'], 'contentHash': snapshot['content_hash']}

"""资产/快照/head/发布/项目引用 Repository（全部走短写事务 + CAS）。

约定：
* 本模块只做数据裁决（唯一性、CAS、不可变），不懂业务校验；payload 是业务层
  已定的 JSON 可序列化对象，content_hash 对 canonical JSON 计算。
* revision_token 不透明且不复用：新保存一律 'r-<uuid>'；迁移/导入允许把旧内容
  hash 初始化为首个 token（一次兼容），此后永不生成内容 hash 形式的 token。
* 快照与发布记录不可变：本模块不提供 update/delete 快照、发布的接口。
"""
import json

from workbench.storage import engine as sto

PAYLOAD_FORMAT_ONTOLOGY = 'workbench-state-1'
PAYLOAD_FORMAT_PROJECT = 'project-state-1'
PAYLOAD_FORMAT_FLOW = 'flow-state-1'
PAYLOAD_FORMAT_LEGACY_FILES = 'legacy-files-v3'       # 迁移的历史修订（原组件形态）
PAYLOAD_FORMAT_LEGACY_RELEASE = 'legacy-release-v1'   # 迁移的发布目录组件


def name_key(name):
    return str(name or '').strip().casefold()


def ensure_asset(conn, kind, external_id, name, summary=None, now=None):
    """取或建资产行（不建 head）。返回 asset_uid。"""
    now = now or sto.utcnow()
    row = conn.execute(sto.text('SELECT asset_uid FROM wb_assets WHERE kind = :k AND external_id = :e'),
                       {'k': kind, 'e': external_id}).first()
    if row:
        return row[0]
    asset_uid = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_assets (asset_uid, kind, external_id, name, name_key, '
                          'summary_json, created_at, updated_at) '
                          'VALUES (:u, :k, :e, :n, :nk, :s, :c, :u2)'),
                 {'u': asset_uid, 'k': kind, 'e': external_id, 'n': name or '',
                  'nk': name_key(name), 's': json.dumps(summary or {}, ensure_ascii=False),
                  'c': now, 'u2': now})
    return asset_uid


def get_asset(conn, kind, external_id):
    row = conn.execute(sto.text('SELECT asset_uid, kind, external_id, name, summary_json, '
                                'created_at, updated_at, deleted_at FROM wb_assets '
                                'WHERE kind = :k AND external_id = :e'),
                       {'k': kind, 'e': external_id}).mappings().first()
    if not row:
        return None
    out = dict(row)
    out['summary'] = json.loads(out.pop('summary_json') or '{}')
    return out


def bump_guard(conn, key):
    """条件更新护栏行（命名/默认项竞态收口）；行缺失说明库未初始化。"""
    result = conn.execute(sto.text('UPDATE wb_write_guards SET generation = generation + 1 '
                                   'WHERE guard_key = :k'), {'k': key})
    if result.rowcount != 1:
        raise sto.StorageUnavailable('存储未初始化：请先运行 python3 -m workbench.storage.transfer init')


def name_taken(conn, kind, name, exclude_uid=None):
    """同名（strip().casefold()）检查；范围性规则由业务层自行加条件。"""
    params = {'k': kind, 'nk': name_key(name)}
    sql = 'SELECT a.external_id FROM wb_assets a WHERE a.kind = :k AND a.name_key = :nk ' \
          'AND a.deleted_at IS NULL'
    if exclude_uid:
        sql += ' AND a.asset_uid <> :x'
        params['x'] = exclude_uid
    return conn.execute(sto.text(sql), params).first() is not None


def next_seq(conn, asset_uid):
    row = conn.execute(sto.text('SELECT COALESCE(MAX(seq), 0) FROM wb_snapshots WHERE asset_uid = :a'),
                       {'a': asset_uid}).first()
    return int(row[0]) + 1


def append_snapshot(conn, asset_uid, payload, payload_format, purpose, legacy_revision='', now=None):
    """插入不可变快照；返回 snapshot 行信息（含 content_hash）。seq 在事务内分配。"""
    now = now or sto.utcnow()
    snapshot_id = sto.new_id()
    seq = next_seq(conn, asset_uid)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    conn.execute(sto.text('INSERT INTO wb_snapshots (snapshot_id, asset_uid, seq, purpose, '
                          'payload_format, payload_json, content_hash, legacy_revision, created_at) '
                          'VALUES (:s, :a, :q, :p, :f, :j, :h, :lr, :c)'),
                 {'s': snapshot_id, 'a': asset_uid, 'q': seq, 'p': purpose, 'f': payload_format,
                  'j': raw, 'h': sto.content_hash(raw.encode('utf-8')), 'lr': legacy_revision or '',
                  'c': now})
    return {'snapshot_id': snapshot_id, 'seq': seq, 'content_hash': sto.content_hash(raw.encode('utf-8'))}


def new_token():
    return 'r-' + sto.new_id().replace('-', '')


def _current_head(kind, external_id, url=None):
    """CAS 失败后的补偿读取：在新的读连接上取最新 token（避免旧事务快照）。"""
    with sto.read_connection(url) as conn:
        return sto.head_by_external(conn, kind, external_id)


def _save_draft_in_conn(conn, kind, external_id, payload, payload_format, expected_token,
                        name, summary, project_ref, purpose, legacy_revision,
                        allow_create, allow_advance, now, conflict):
    """在既有写事务内保存草稿；CAS 失败时置 conflict['head'] 并抛 RevisionConflict。"""
    now = now or sto.utcnow()
    conflict.setdefault('kind', kind)
    conflict.setdefault('external_id', external_id)
    asset = get_asset(conn, kind, external_id)
    if asset is None:
        if not allow_create:
            raise sto.NotFound(f'{kind} 资产不存在：{external_id}')
        asset_uid = ensure_asset(conn, kind, external_id, name or '', summary, now)
    else:
        asset_uid = asset['asset_uid']
    head = sto.read_head(conn, asset_uid)
    if head is None:
        if expected_token not in (None, ''):
            raise sto.RevisionConflict(message='资产不存在或尚未初始化')
        new_snapshot = append_snapshot(conn, asset_uid, payload, payload_format, purpose,
                                       legacy_revision, now)
        token = legacy_revision or new_token()  # 迁移初始化沿用旧 token，在线保存生成新 token
        conn.execute(sto.text('INSERT INTO wb_asset_heads (asset_uid, snapshot_id, '
                              'revision_token, generation, snapshot_seq, release_seq, updated_at) '
                              'VALUES (:a, :s, :t, 1, :q, 0, :now)'),
                     {'a': asset_uid, 's': new_snapshot['snapshot_id'], 't': token,
                      'q': new_snapshot['seq'], 'now': now})
    else:
        if expected_token is None:
            if not allow_advance:
                raise sto.StorageError('缺少保存基线（revision）：拒绝无基线覆盖')
            expected = head['revision_token']
        else:
            expected = expected_token
        new_snapshot = append_snapshot(conn, asset_uid, payload, payload_format, purpose,
                                       legacy_revision, now)
        token = new_token()  # 保存后永不复用内容 hash 形式 token
        if not sto.cas_head(conn, asset_uid, expected, head['generation'],
                            new_snapshot['snapshot_id'], token, new_snapshot['seq'], now):
            conflict['head'] = True
            raise sto.RevisionConflict(message='并发保存冲突')
        if project_ref is not None:
            upsert_project_ref(conn, new_snapshot['snapshot_id'], project_ref)
    if name is not None or summary is not None:
        _refresh_asset_meta(conn, asset_uid, name, summary, now)
    return {'revision': token, 'seq': new_snapshot['seq'],
            'snapshotId': new_snapshot['snapshot_id'],
            'contentHash': new_snapshot['content_hash'], 'assetUid': asset_uid}


def save_draft(kind, external_id, payload, payload_format, expected_token=None,
               name=None, summary=None, project_ref=None, purpose='draft',
               legacy_revision='', allow_create=True, allow_advance=False, now=None):
    """一次草稿保存事务：追加快照 → CAS 推进 head（→ 项目引用 → 摘要）。

    expected_token 指定客户端基线（CAS 校验）；None 仅允许两种情形：
    资产尚无 head（首次建立，allow_create）或内部初始化路径（allow_advance=True，
    以当前 head 为基线推进）。在线保存路径必须显式传基线，防止绕过并发控制。
    返回 {'revision': token, 'seq', 'snapshotId', 'contentHash'}；冲突抛 RevisionConflict。
    """
    now = now or sto.utcnow()
    conflict = {}

    def body(conn):
        return _save_draft_in_conn(conn, kind, external_id, payload, payload_format,
                                   expected_token, name, summary, project_ref, purpose,
                                   legacy_revision, allow_create, allow_advance, now, conflict)

    try:
        with sto.write_tx() as tx:
            return tx.run(body)
    except sto.RevisionConflict:
        if conflict.get('head'):
            fresh = _current_head(kind, external_id)
            raise sto.RevisionConflict(current_revision=(fresh or {}).get('revision_token'),
                                       message='此内容已有新版本，请刷新后重试')
        raise


def run_in_write_tx(body):
    """把草稿保存与其他写入放进同一事务的通用入口（发布/复制等复合操作）。

    body(conn, save_fn, conflict)：save_fn 是绑定本事务的 _save_draft_in_conn。
    冲突时同样做补偿读取填充 current_revision。
    """
    conflict = {}

    def save_fn(conn, **kwargs):
        merged = {'kind': None, 'external_id': None, 'payload': None, 'payload_format': None,
                  'expected_token': None, 'name': None, 'summary': None, 'project_ref': None,
                  'purpose': 'draft', 'legacy_revision': '', 'allow_create': False,
                  'allow_advance': False, 'now': None}
        merged.update(kwargs)
        return _save_draft_in_conn(conn, merged['kind'], merged['external_id'], merged['payload'],
                                   merged['payload_format'], merged['expected_token'],
                                   merged['name'], merged['summary'], merged['project_ref'],
                                   merged['purpose'], merged['legacy_revision'],
                                   merged['allow_create'], merged['allow_advance'],
                                   merged['now'], conflict)

    try:
        with sto.write_tx() as tx:
            return tx.run(lambda conn: body(conn, save_fn, conflict))
    except sto.RevisionConflict:
        if conflict.get('head'):
            fresh = _current_head(conflict.get('kind'), conflict.get('external_id'))
            raise sto.RevisionConflict(current_revision=(fresh or {}).get('revision_token'),
                                       message='此内容已有新版本，请刷新后重试')
        raise


def _refresh_asset_meta(conn, asset_uid, name, summary, now):
    sets, params = ['updated_at = :now'], {'u': asset_uid, 'now': now}
    if name is not None:
        sets += ['name = :n', 'name_key = :nk']
        params.update({'n': name, 'nk': name_key(name)})
    if summary is not None:
        sets += ['summary_json = :s']
        params['s'] = json.dumps(summary, ensure_ascii=False)
    conn.execute(sto.text('UPDATE wb_assets SET ' + ', '.join(sets) + ' WHERE asset_uid = :u'), params)


def _ref_target_release(conn, ontology_id, version):
    row = conn.execute(sto.text("SELECT r.release_id FROM wb_releases r JOIN wb_assets a "
                                "ON a.asset_uid = r.asset_uid WHERE a.kind = 'model' "
                                'AND a.external_id = :o AND r.version_label = :v'),
                       {'o': ontology_id, 'v': str(version)}).first()
    return row[0] if row else None


def upsert_project_ref(conn, project_snapshot_id, ref, conn_=None):
    """项目快照引用（至多一条）：能解析到本体发布版本则挂 release FK，否则标 missing。"""
    target_release = None
    resolution = 'missing'
    if ref.get('target_ontology_id') and ref.get('target_version'):
        target_release = _ref_target_release(conn, ref['target_ontology_id'], ref['target_version'])
        resolution = 'ok' if target_release else 'missing'
    conn.execute(sto.text('DELETE FROM wb_project_refs WHERE project_snapshot_id = :p'),
                 {'p': project_snapshot_id})
    conn.execute(sto.text('INSERT INTO wb_project_refs (project_snapshot_id, target_ontology_id, '
                          'target_version, target_release_id, resolution) '
                          'VALUES (:p, :o, :v, :r, :res)'),
                 {'p': project_snapshot_id, 'o': ref.get('target_ontology_id', ''),
                  'v': str(ref.get('target_version', '')), 'r': target_release, 'res': resolution})
    return resolution


def get_project_ref(conn, snapshot_id):
    row = conn.execute(sto.text('SELECT project_snapshot_id, target_ontology_id, target_version, '
                                'target_release_id, resolution FROM wb_project_refs '
                                'WHERE project_snapshot_id = :p'), {'p': snapshot_id}).mappings().first()
    return dict(row) if row else None


def read_current(kind, external_id, url=None):
    """当前 head + 快照完整行（payload 已解析为 dict）；不存在返回 None。"""
    with sto.read_connection(url) as conn:
        head = sto.head_by_external(conn, kind, external_id)
        if head is None:
            return None
        snapshot = sto.read_snapshot(conn, head['snapshot_id'])
        if snapshot is None:
            raise sto.StorageError('当前快照缺失：数据库一致性需要检查（integrity_check）')
        snapshot['payload'] = json.loads(snapshot.pop('payload_json'))
        return {'head': head, 'snapshot': snapshot}


def read_snapshot_parsed(snapshot_id, url=None):
    with sto.read_connection(url) as conn:
        snapshot = sto.read_snapshot(conn, snapshot_id)
        if snapshot is not None:
            snapshot['payload'] = json.loads(snapshot.pop('payload_json'))
        return snapshot


def read_head(kind, external_id, url=None):
    with sto.read_connection(url) as conn:
        return sto.head_by_external(conn, kind, external_id)


def current_token(kind, external_id):
    head = read_head(kind, external_id)
    return head['revision_token'] if head else None


def list_assets(conn, kind):
    rows = conn.execute(sto.text('SELECT a.asset_uid, a.external_id, a.name, a.name_key, '
                                 'a.summary_json, a.created_at, h.revision_token, h.snapshot_id, '
                                 'h.updated_at, h.snapshot_seq, h.release_seq, s.purpose '
                                 'FROM wb_assets a JOIN wb_asset_heads h ON h.asset_uid = a.asset_uid '
                                 'JOIN wb_snapshots s ON s.snapshot_id = h.snapshot_id '
                                 'WHERE a.kind = :k AND a.deleted_at IS NULL '
                                 'ORDER BY a.name_key, a.external_id'), {'k': kind}).mappings().all()
    out = []
    for row in rows:
        item = dict(row)
        item['summary'] = json.loads(item.pop('summary_json') or '{}')
        out.append(item)
    return out


def delete_asset_soft(kind, external_id, now=None):
    """首期不做物理清理；当前仅编排软删除走快照，不删资产行。保留接口以防误用。"""
    raise NotImplementedError('首期不提供资产删除')


# --- 发布 -----------------------------------------------------------------------

def release_rows(conn, asset_uid):
    rows = conn.execute(sto.text('SELECT release_id, version_label, release_order, snapshot_id, '
                                 'source_draft_id, manifest_json, created_at FROM wb_releases '
                                 'WHERE asset_uid = :a ORDER BY release_order'), {'a': asset_uid}).mappings().all()
    out = []
    for row in rows:
        item = dict(row)
        item['manifest'] = json.loads(item.pop('manifest_json') or '{}')
        out.append(item)
    return out


def get_release_row(conn, asset_uid, version_label):
    row = conn.execute(sto.text('SELECT release_id, version_label, release_order, snapshot_id, '
                                'source_draft_id, manifest_json, created_at FROM wb_releases '
                                'WHERE asset_uid = :a AND version_label = :v'),
                       {'a': asset_uid, 'v': version_label}).mappings().first()
    if not row:
        return None
    item = dict(row)
    item['manifest'] = json.loads(item.pop('manifest_json') or '{}')
    return item


def append_release(conn, asset_uid, version_label, snapshot_id, manifest, source_draft_id=None, now=None):
    """插入发布记录；版本号/顺序唯一约束兜底。返回 release_id。"""
    now = now or sto.utcnow()
    row = conn.execute(sto.text('SELECT COALESCE(MAX(release_order), 0) FROM wb_releases '
                                'WHERE asset_uid = :a'), {'a': asset_uid}).first()
    release_id = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_releases (release_id, asset_uid, version_label, '
                          'release_order, snapshot_id, source_draft_id, manifest_json, created_at) '
                          'VALUES (:r, :a, :v, :o, :s, :d, :m, :c)'),
                 {'r': release_id, 'a': asset_uid, 'v': version_label, 'o': int(row[0]) + 1,
                  's': snapshot_id, 'd': source_draft_id,
                  'm': json.dumps(manifest, ensure_ascii=False), 'c': now})
    head = sto.read_head(conn, asset_uid)
    if head is not None:
        conn.execute(sto.text('UPDATE wb_asset_heads SET release_seq = :o, updated_at = :now '
                              'WHERE asset_uid = :a'),
                     {'o': int(row[0]) + 1, 'now': now, 'a': asset_uid})
    return release_id

"""附件（历史 ZIP、恢复前备份、被引用导入材料、导出原始字节）Repository。

历史恢复 ZIP 与发布原始材料在库化后归档于此；恢复产生新草稿，不覆盖旧快照。
同 (owner, purpose, legacy_name) 的重复写入须核对内容一致才允许（迁移幂等）。
"""
import json

from workbench.storage import engine as sto

PURPOSE_RELEASE_ZIP = 'release-zip'
PURPOSE_RESTORE_BACKUP = 'restore-backup'
PURPOSE_SOURCE_REFERENCE = 'source-reference'


def put_artifact(conn, data, purpose, legacy_name='', owner_asset_uid=None,
                 media_type='', snapshot_id=None, release_id=None, now=None,
                 allow_duplicate=False):
    now = now or sto.utcnow()
    content_hash = sto.content_hash(data)
    if not allow_duplicate:
        if owner_asset_uid is None:
            row = conn.execute(sto.text('SELECT artifact_id, content_hash FROM wb_artifacts '
                                        'WHERE owner_asset_uid IS NULL AND purpose = :p AND legacy_name = :n'),
                               {'p': purpose, 'n': legacy_name}).first()
        else:
            row = conn.execute(sto.text('SELECT artifact_id, content_hash FROM wb_artifacts '
                                        'WHERE owner_asset_uid = :o AND purpose = :p AND legacy_name = :n'),
                               {'o': owner_asset_uid, 'p': purpose, 'n': legacy_name}).first()
        if row:
            if row[1] != content_hash:
                raise sto.StorageError(f'同名附件内容不一致，拒绝覆盖：{legacy_name}')
            return row[0]
    artifact_id = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_artifacts (artifact_id, owner_asset_uid, snapshot_id, '
                          'release_id, purpose, legacy_name, media_type, content_hash, bytes_data, '
                          'created_at) VALUES (:a, :o, :s, :r, :p, :n, :m, :h, :b, :c)'),
                 {'a': artifact_id, 'o': owner_asset_uid, 's': snapshot_id, 'r': release_id,
                  'p': purpose, 'n': legacy_name, 'm': media_type, 'h': content_hash,
                  'b': data, 'c': now})
    return artifact_id


def get_artifact(conn, artifact_id):
    row = conn.execute(sto.text('SELECT artifact_id, owner_asset_uid, snapshot_id, release_id, purpose, '
                                'legacy_name, media_type, content_hash, bytes_data, created_at '
                                'FROM wb_artifacts WHERE artifact_id = :a'), {'a': artifact_id}).mappings().first()
    return dict(row) if row else None


def find_artifacts(conn, owner_asset_uid=None, purpose=None):
    sql = 'SELECT artifact_id, owner_asset_uid, purpose, legacy_name, media_type, content_hash, ' \
          'created_at FROM wb_artifacts WHERE 1=1'
    params = {}
    if owner_asset_uid is not None:
        sql += ' AND owner_asset_uid = :o'
        params['o'] = owner_asset_uid
    elif owner_asset_uid is None and purpose is not None:
        sql += ' AND owner_asset_uid IS NULL'
    if purpose:
        sql += ' AND purpose = :p'
        params['p'] = purpose
    sql += ' ORDER BY legacy_name DESC, created_at DESC'
    return [dict(r) for r in conn.execute(sto.text(sql), params).mappings().all()]


def read_by_name(conn, owner_asset_uid, purpose, legacy_name):
    """按历史文件名取附件内容；缺失返回 None。"""
    row = conn.execute(sto.text('SELECT bytes_data FROM wb_artifacts WHERE owner_asset_uid = :o '
                                'AND purpose = :p AND legacy_name = :n'),
                       {'o': owner_asset_uid, 'p': purpose, 'n': legacy_name}).first()
    return bytes(row[0]) if row else None


def artifact_meta_list(conn, owner_asset_uid, purpose):
    return find_artifacts(conn, owner_asset_uid=owner_asset_uid, purpose=purpose)


def import_items_record(conn, batch_id, source_key, source_hash, entity_kind, target_id,
                        status='imported', summary=None, now=None):
    """迁移清单登记；同 source_key 已存在（任意批次）时返回 ('exists', same_hash)。

    查重按 source_key **全局**，不限定 batch_id：迁移 key 是内容寻址的
    （如 `model-release:{id}:{version}`），可重入与「源变化中止」语义必须跨批次
    成立。原实现 `WHERE batch_id = :b AND source_key_hash = :h` 依赖两次 import
    恰好落在同一秒（batch id 为秒级时间戳）才不重复导入——跨秒重入会绕过查重
    直接触发唯一约束崩溃（2026-09-18 批次 C 回归中发现）。
    """
    now = now or sto.utcnow()
    from workbench.storage.engine import stable_hash
    key_hash = stable_hash(source_key)
    row = conn.execute(sto.text('SELECT item_id, source_hash, target_id FROM wb_import_items '
                                'WHERE source_key_hash = :h ORDER BY updated_at DESC'),
                       {'h': key_hash}).first()
    if row:
        same = row[1] == source_hash
        return ('exists', same)
    conn.execute(sto.text('INSERT INTO wb_import_items (item_id, batch_id, source_key, source_key_hash, '
                          'source_hash, entity_kind, target_id, status, summary_json, updated_at) '
                          'VALUES (:i, :b, :k, :h, :sh, :e, :t, :st, :s, :now)'),
                 {'i': sto.new_id(), 'b': batch_id, 'k': source_key, 'h': key_hash, 'sh': source_hash,
                  'e': entity_kind, 't': target_id, 'st': status,
                  's': json.dumps(summary or {}, ensure_ascii=False), 'now': now})
    return ('inserted', True)

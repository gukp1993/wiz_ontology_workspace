"""定义/引用索引：可重建的查询投影（不是第二事实源）。

只为当前草稿与发布快照生成；同一资产换 head 时先清该资产的草稿投影再写新值，
全部在保存事务内完成。stable_id 可能是长 IRI：完整值存 TEXT，检索用定长 hash，
唯一键 (snapshot_id, definition_kind, stable_id_hash, owner_hash)，hash 命中后
由调用方核对原字符串。索引缺失时调用方回源完整文档重建，绝不返回假空列表。
"""
import json

from workbench.storage import engine as sto


def _hash(value):
    return sto.stable_hash(str(value or ''))


GROUP_KIND = {'objectTypes': 'object', 'linkTypes': 'link', 'properties': 'property',
              'sharedProperties': 'sharedProperty', 'valueTypes': 'valueType'}


def extract_definition_rows(payload):
    """从本体快照 payload 提取定义索引行（可重建投影的统一提取器）。"""
    ontology = (payload or {}).get('ontology') or {}
    if 'schemaVersion' not in ontology:
        try:
            from workbench.model_format import encode_ontology
            ontology = encode_ontology(ontology)
        except Exception:
            return []
    rows = []
    for group, kind in GROUP_KIND.items():
        for record in ontology.get(group) or []:
            rows.append({'kind': kind, 'id': record.get('id', ''),
                         'name': str(record.get('displayName') or record.get('apiName') or record.get('id', '')),
                         'owner': record.get('objectTypeId') or record.get('sourceObjectTypeId') or ''})
    return rows


def replace_definition_index(conn, asset_uid, snapshot_id, rows):
    conn.execute(sto.text('DELETE FROM wb_definition_index WHERE asset_uid = :a AND snapshot_id = :s'),
                 {'a': asset_uid, 's': snapshot_id})
    for ordinal, row in enumerate(rows or []):
        conn.execute(sto.text('INSERT INTO wb_definition_index (row_id, asset_uid, snapshot_id, '
                              'definition_kind, stable_id, stable_id_hash, owner_id, owner_hash, '
                              'name, name_key, ordinal, document_locator) '
                              'VALUES (:r, :a, :s, :k, :i, :ih, :o, :oh, :n, :nk, :ord, :loc)'),
                     {'r': sto.new_id(), 'a': asset_uid, 's': snapshot_id,
                      'k': str(row.get('kind') or ''),
                      'i': str(row.get('id') or ''), 'ih': _hash(row.get('id')),
                      'o': str(row.get('owner') or ''), 'oh': _hash(row.get('owner')),
                      'n': str(row.get('name') or '')[:255],
                      'nk': str(row.get('name') or '').strip().casefold()[:255],
                      'ord': row.get('ordinal', ordinal), 'loc': str(row.get('locator') or '')})


def replace_reference_index(conn, snapshot_id, rows):
    conn.execute(sto.text('DELETE FROM wb_reference_index WHERE snapshot_id = :s'), {'s': snapshot_id})
    for row in rows or []:
        conn.execute(sto.text('INSERT INTO wb_reference_index (row_id, snapshot_id, source_kind, '
                              'source_id, target_kind, target_id, target_id_hash, relation_kind, '
                              'document_locator) VALUES (:r, :s, :sk, :si, :tk, :ti, :tih, :rk, :loc)'),
                     {'r': sto.new_id(), 's': snapshot_id, 'sk': str(row.get('sourceKind') or ''),
                      'si': str(row.get('sourceId') or ''), 'tk': str(row.get('targetKind') or ''),
                      'ti': str(row.get('targetId') or ''), 'tih': _hash(row.get('targetId')),
                      'rk': str(row.get('relation') or ''), 'loc': str(row.get('locator') or '')})


def clear_draft_index(conn, asset_uid):
    """同资产旧草稿投影可清理（快照不删）；发布快照投影保留。"""
    conn.execute(sto.text("DELETE FROM wb_definition_index WHERE asset_uid = :a AND snapshot_id IN "
                          "(SELECT snapshot_id FROM wb_snapshots WHERE asset_uid = :a AND purpose IN ('draft','imported-base')) "
                          'AND snapshot_id NOT IN (SELECT snapshot_id FROM wb_asset_heads WHERE asset_uid = :a)'),
                 {'a': asset_uid})
    conn.execute(sto.text("DELETE FROM wb_reference_index WHERE snapshot_id IN "
                          "(SELECT snapshot_id FROM wb_snapshots WHERE asset_uid = :a AND purpose IN ('draft','imported-base')) "
                          'AND snapshot_id NOT IN (SELECT snapshot_id FROM wb_asset_heads WHERE asset_uid = :a)'),
                 {'a': asset_uid})


def query_definitions(conn, snapshot_id, kind=None, owner=None, q=None, limit=50, offset=0):
    sql = "SELECT definition_kind, stable_id, owner_id, name, ordinal FROM wb_definition_index " \
          'WHERE snapshot_id = :s'
    params = {'s': snapshot_id, 'lim': max(1, min(int(limit), 100)), 'off': max(0, int(offset))}
    if kind:
        sql += ' AND definition_kind = :k'
        params['k'] = kind
    if owner:
        sql += ' AND owner_hash = :oh'
        params['oh'] = _hash(owner)
    if q:
        sql += ' AND name_key LIKE :q'
        params['q'] = '%' + str(q).strip().casefold() + '%'
    sql += ' ORDER BY definition_kind, ordinal, stable_id LIMIT :lim OFFSET :off'
    return [dict(r) for r in conn.execute(sto.text(sql), params).mappings().all()]


def count_definitions(conn, snapshot_id):
    return conn.execute(sto.text('SELECT COUNT(*) FROM wb_definition_index WHERE snapshot_id = :s'),
                        {'s': snapshot_id}).scalar() or 0

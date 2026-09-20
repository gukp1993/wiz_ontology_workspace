"""从物料构建本体：跨来源对齐（**纯函数**，不访问数据库、不调用模型）。

两块职责（开发计划 §5.2、接口文档 08 §1.6）：
1. `group_evidence(facts, relevant_ids)` —— 把检索到的事实按“像候选的同一主体”
   （DDL 表 / 代码符号 / 文档章节）分组，并对同一 snippet 哈希去重：重复副本不算独立
   佐证，只在 duplicates 里指向首个副本。供复核页与管线抽取前查看证据分布。
2. `align(candidates)` —— 同一批候选内合并。规则是保守的：
   * 只有 **同名（casefold 后一致）+ 同类型** 才合并；仅名称相同、类型不同绝不合并；
   * 属性额外要求 dataType 一致（不同数据类型的属性不自动合并）；
   * 证据取并集（去重）；
   * 两侧取值不同（含一侧缺失）的字段一律写入 conflicts 保留两侧，绝不自动取舍；
   * 出现冲突 → evidenceStatus='conflict'；无冲突时：全部来源 supported 且证据非空 →
     supported，否则有证据 → inferred，无证据 → insufficient（只降不升）。
   未命中的候选也带 alignedKey（自身分组键），便于后续批次对齐与差异比较。
"""

from workbench.ontology_build import retrieval

STATUS_PRIORITY = ('insufficient', 'inferred', 'conflict', 'supported')
_FIELD_ORDER = ('definition', 'dataType', 'valueType', 'sourceRef', 'targetRef', 'cardinality',
                'content', 'effect')
MAX_CONFLICTS = 20


def _text(value):
    return str(value if value is not None else '').strip()


def _normal_name(value):
    return ' '.join(_text(value).casefold().split())


def _jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return _text(value)


def _same(left, right):
    return _jsonable(left) == _jsonable(right)


def align_key(candidate):
    """候选的分组键（同名同类型；属性还要求 dataType 一致）。无名称返回 ''。"""
    candidate = candidate if isinstance(candidate, dict) else {}
    name = _normal_name(candidate.get('name'))
    if not name:
        return ''
    ctype = _text(candidate.get('type')).casefold() or 'unknown'
    key = '%s:%s' % (ctype, name)
    if ctype == 'property':
        fields = candidate.get('fields') if isinstance(candidate.get('fields'), dict) else {}
        data_type = _text(fields.get('dataType')).casefold()
        key += '#%s' % (data_type or 'unknown')
    return key


def _evidence_ids(candidate, field=None):
    """候选证据里的 factId 列表；field 省略时汇总所有字段（保持出现顺序、去重）。"""
    evidence = candidate.get('evidence') if isinstance(candidate.get('evidence'), dict) else {}
    buckets = [evidence.get(field)] if field is not None else list(evidence.values())
    out = []
    for items in buckets:
        for item in (items if isinstance(items, list) else []):
            text = _text(item)
            if text and text not in out:
                out.append(text)
    return out


def _first_ref(candidate, field):
    ids = _evidence_ids(candidate, field) or _evidence_ids(candidate, '_record') or _evidence_ids(candidate)
    return ids[0] if ids else ''


def merge_group(members):
    """同一分组内的候选合并；返回 (合并后的候选, conflicts, 说明列表)。"""
    primary = dict(members[0])
    primary['evidence'] = {key: list(value) for key, value in
                           (primary.get('evidence') or {}).items()
                           if isinstance(value, list)}
    conflicts = [item for item in (primary.get('conflicts') or []) if isinstance(item, dict)]
    notes = []
    sources = [_text(item.get('origin', {}).get('key') if isinstance(item.get('origin'), dict)
                     else '') or _text(item.get('key')) or item.get('id', '') for item in members]
    for member in members[1:]:
        member_evidence = member.get('evidence') if isinstance(member.get('evidence'), dict) else {}
        for field, ids in member_evidence.items():
            bucket = primary['evidence'].setdefault(_text(field), [])
            for fact_id in ids if isinstance(ids, list) else []:
                text = _text(fact_id)
                if text and text not in bucket:
                    bucket.append(text)
        for item in member.get('conflicts') or []:
            if isinstance(item, dict) and len(conflicts) < MAX_CONFLICTS:
                conflicts.append(item)
        # 字段差异：保留两侧，绝不自动取舍
        for field in _FIELD_ORDER:
            left = (primary.get('fields') or {}).get(field)
            right = (member.get('fields') or {}).get(field)
            if right in (None, '') and left in (None, ''):
                continue
            if _same(left, right):
                continue
            sides = [{'factId': _first_ref(primary, field), 'value': left},
                     {'factId': _first_ref(member, field), 'value': right}]
            conflicts.append({'field': field, 'sides': sides,
                              'note': '来源「%s」与「%s」的该字段取值不同（含一侧缺失），'
                                      '未自动取舍，请人工确认' % (sources[0] or '主候选',
                                                             _text(member.get('key')) or '另一来源')})
        if _text(primary.get('definition')) and _text(member.get('definition')) and \
                _text(primary.get('definition')) != _text(member.get('definition')):
            conflicts.append({'field': 'definition',
                              'sides': [{'factId': _first_ref(primary, 'definition'),
                                         'value': _text(primary.get('definition'))},
                                        {'factId': _first_ref(member, 'definition'),
                                         'value': _text(member.get('definition'))}],
                              'note': '同名同类型但业务定义表述不同，两侧均保留，请人工确认'})
        notes.append('候选「%s」（%s）合并来源：%s'
                     % (_text(primary.get('name')) or '未命名', _text(primary.get('type')) or '未知',
                        '、'.join(name for name in sources if name) or '（无来源键）'))
    return primary, conflicts, notes


def align(candidates):
    """候选列表（同一批）→ 对齐后的候选列表 + 说明。

    返回 {'candidates': [...], 'notes': [...], 'stats': {...}}；
    合并后的候选带 alignedKey / conflicts / mergedFromKeys，evidenceStatus 按上述规则重算。
    """
    items = [candidate for candidate in (candidates or []) if isinstance(candidate, dict)]
    groups, order = {}, []
    for index, candidate in enumerate(items):
        key = align_key(candidate)
        bucket_key = key or '#unnamed-%d' % index  # 无名称候选绝不合并
        if bucket_key not in groups:
            groups[bucket_key] = []
            order.append(bucket_key)
        groups[bucket_key].append(candidate)

    out, notes = [], []
    merged_total = conflicts_total = 0
    for bucket_key in order:
        members = groups[bucket_key]
        key = align_key(members[0])
        if len(members) == 1:
            candidate = dict(members[0])
            conflicts = [item for item in (candidate.get('conflicts') or []) if isinstance(item, dict)]
            candidate['conflicts'] = conflicts
            candidate['alignedKey'] = key
            candidate['evidenceStatus'] = _status(members, conflicts)
            candidate['mergedFromKeys'] = []
            out.append(candidate)
            continue
        merged, conflicts, group_notes = merge_group(members)
        merged['conflicts'] = conflicts[:MAX_CONFLICTS]
        merged['alignedKey'] = key
        merged['evidenceStatus'] = _status(members, merged['conflicts'])
        merged['mergedFromKeys'] = [(_text(item.get('key')) or _text(item.get('id')))
                                    for item in members[1:]]
        out.append(merged)
        notes.extend(group_notes)
        merged_total += len(members)
        conflicts_total += len(merged['conflicts'])
    return {'candidates': out, 'notes': notes,
            'stats': {'input': len(items), 'groups': len(order), 'merged': merged_total,
                      'conflicts': conflicts_total}}


def _status(members, conflicts):
    """合并后的证据状态（只降不升；模型给出的 conflict 信号保留）。"""
    if conflicts:
        return 'conflict'
    statuses = [_text(member.get('evidenceStatus')) or 'inferred' for member in members]
    if 'conflict' in statuses:
        return 'conflict'
    evidence = {}
    for member in members:
        for field, ids in (member.get('evidence') or {}).items():
            if isinstance(ids, list) and [text for text in ids if _text(text)]:
                evidence[_text(field)] = True
    if not evidence:
        return 'insufficient'
    if all(status == 'supported' for status in statuses):
        return 'supported'
    if any(status in ('supported', 'inferred') for status in statuses):
        return 'inferred'
    return 'insufficient'


# --- 证据分组（供复核与抽取前查看） -----------------------------------------------

def subject_key(fact):
    """事实的“像哪个候选”的粗主体键（DDL 表 / 代码符号 / 文档章节）。"""
    fact = fact if isinstance(fact, dict) else {}
    locator = fact.get('locator') if isinstance(fact.get('locator'), dict) else {}
    data = fact.get('data') if isinstance(fact.get('data'), dict) else {}
    kind = _text(locator.get('kind') or fact.get('kind'))
    if kind in ('ddl', 'ddlComment'):
        table = _text(locator.get('table') or data.get('table'))
        return 'ddl:%s' % (table.casefold() or _text(locator.get('file')).casefold())
    if kind == 'code':
        symbol = _text(data.get('qualified') or data.get('name') or locator.get('symbol'))
        if symbol:
            return 'code:%s' % symbol.casefold()
        return 'code:%s' % _text(locator.get('file')).casefold()
    if kind in ('md', 'docx', 'pdf', 'xlsx'):
        section = _text(locator.get('section') or locator.get('sheet') or data.get('text'))
        return '%s:%s#%s' % (kind, _text(locator.get('file')).casefold(), section.casefold()[:80])
    if kind == 'other':
        return 'other:%s' % _text(locator.get('file')).casefold()
    return '%s:%s' % (kind or 'fact', _text(locator.get('file')).casefold())


def group_evidence(facts, relevant_ids=None):
    """事实 → 证据分组（同主体聚合 + 同片段哈希去重）。

    返回 {'groups': [{'key','kind','factIds','items','duplicates','modules','materialIds'}],
          'duplicates': {factId: 首个副本 factId}, 'notes': [...], 'stats': {...}}
    事实先按 id 去重；重复（同 snippet 哈希）只在 duplicates 里指向首个副本，不计入 items。
    """
    allowed = None
    if relevant_ids is not None:
        allowed = {_text(item) for item in relevant_ids if _text(item)}
    groups, order = {}, []
    duplicates = {}
    seen_fact, skipped = set(), 0
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        fact_id = _text(fact.get('id'))
        if not fact_id or fact_id in seen_fact:
            continue
        if allowed is not None and fact_id not in allowed:
            continue
        seen_fact.add(fact_id)
        key = subject_key(fact)
        group = groups.get(key)
        if group is None:
            group = {'key': key, 'kind': _text(fact.get('kind')), 'factIds': [], 'items': [],
                     'duplicates': [], 'modules': [], 'materialIds': []}
            groups[key] = group
            order.append(key)
        module = _text(fact.get('module'))
        if module and module not in group['modules']:
            group['modules'].append(module)
        material_id = _text(fact.get('materialId'))
        if material_id and material_id not in group['materialIds']:
            group['materialIds'].append(material_id)
        digest = retrieval.snippet_digest(fact)
        keeper = group.get('_hashes', {}).get(digest)
        if keeper:
            duplicates[fact_id] = keeper
            group['duplicates'].append({'factId': fact_id, 'duplicateOf': keeper})
            skipped += 1
            continue
        group.setdefault('_hashes', {})[digest] = fact_id
        group['factIds'].append(fact_id)
        group['items'].append({'factId': fact_id, 'locator': fact.get('locator') or {},
                               'snippet': _text(fact.get('snippet')), 'kind': _text(fact.get('kind')),
                               'quality': _text(fact.get('quality')),
                               'materialId': material_id, 'module': module})
    result = []
    for key in order:
        group = groups[key]
        group.pop('_hashes', None)
        result.append(group)
    notes = []
    if skipped:
        notes.append('同片段重复副本 %d 条只计一次佐证（duplicates 指向首个副本）。' % skipped)
    return {'groups': result, 'duplicates': duplicates, 'notes': notes,
            'stats': {'facts': len(seen_fact), 'groups': len(result), 'duplicates': skipped}}

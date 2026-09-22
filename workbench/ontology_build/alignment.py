"""从物料构建本体：跨来源对齐（**纯函数**，不访问数据库、不调用模型）。

三块职责（开发计划 §5.2、接口文档 08 §1.6）：
1. `group_evidence(facts, relevant_ids)` —— 把检索到的事实按“像候选的同一主体”
   （DDL 表 / 代码符号 / 文档章节）分组，并对同一**事实身份**（retrieval.snippet_digest：
   snippet + kind + locator + data 语义键，非仅 snippet）去重：重复副本不算独立
   佐证，只在 duplicates 里指向首个副本。供复核页与管线抽取前查看证据分布。
2. `namespace_batch(candidates, position, existing=None)` —— 批次级临时键命名空间化（R1）：
   模型只被要求 key「批内唯一」，不同批次可能重用同名键（obj/p）；候选跨批累积后
   引用索引对同名键「首到者胜」，第二批判定的属性会解析到第一批判定的宿主。
   在候选入跨批累积集合（与同事务落库）前调用：与累积集合**实际冲突**的键加
   `b{position}:` 前缀（无冲突的键保留原键，入库行为与历史一致），并按
   「先建映射、再替换」同步重写批内 ownerKey 与链接 sourceRef/targetRef——
   跨批键绝不冲突，批内引用关系完整保留（截断拆批的子批同属一个 position）。
3. `align(candidates)` —— 同一批候选内合并。规则是保守的：
   * 只有 **同名（casefold 后一致）+ 同类型** 才合并；仅名称相同、类型不同绝不合并；
   * 属性额外要求 dataType 一致且宿主对象（ownerKey 解析到的规范名）一致
     （D08：不同数据类型的属性不自动合并，宿主不同的同名属性也不自动合并）；
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


def align_key(candidate, owner_name=None):
    """候选的分组键（同名同类型；属性还要求 dataType 与宿主对象一致）。无名称返回 ''。

    D08：属性候选的键附加规范化的宿主对象名（`align(candidates)` 里由
    ownerKey 解析得到；解析不到时用 'unknown'）。同名同 dataType 但宿主不同
    的属性因此不再进入同一分组，绝不自动合并。宿主用**规范名**而不是批次内
    key，保证跨批次 alignedKey 稳定（差异比较/决策继承依赖这一点）。
    """
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
        host = _normal_name(owner_name)
        key += '@%s' % (host or 'unknown')
    return key


def namespace_batch(candidates, position, existing=None):
    """一批候选的临时键命名空间化：与累积集合冲突的键加批次前缀并重写批内引用（R1）。

    R1：SYSTEM_EXTRACT 只要求 key「批内唯一」，不同批次可能重用同名键（obj/p）。
    候选跨批累积后 `_ref_index` 对同名键首到者胜，第二批判定的属性 ownerKey 会
    解析到第一批判定的宿主对象（alignedKey 错记 @宿主，同名同 dataType 的属性
    被误并为一项）。在入累积集合（与同事务落库）前调用本函数：

    * `existing`（跨批累积集合的当前候选列表）提供时（**含空列表**：首批无冲突，
      不加前缀），只为与已有键**实际冲突**的键加 `b{position}:` 前缀（position
      为 1 起的批次序号）——键跨批唯一即无冲突，不加前缀，入库键与历史行为完全
      一致（兼容旧候选行/既有断言）；冲突键加前缀后跨批绝不冲突（目标键若仍被
      占用则追加 `_` 兜底），批内引用（属性 ownerKey、链接 sourceRef/targetRef）
      按「先收集本批 key→新 key 映射，再替换引用值」同步重写，引用关系完整保留；
    * `existing` 省略（None）时全部非空键加前缀（纯批语义，供直调与测试）；
    * 引用指向批外键（跨批引用本就不合法）或为空时原样保留；已带本批前缀的键
      视为已处理，原样保留（幂等，防重复前缀）；截断拆批的子批同属一个
      position，前缀一致。

    返回新候选列表（浅拷贝逐项，不修改入参）；同名键批内重复时映射取首到者
    （与 `_dedupe_keys` 保留首个的口径一致）。
    """
    items = [candidate for candidate in (candidates or []) if isinstance(candidate, dict)]
    prefix = 'b%d:' % max(1, int(position or 0))
    prefixing_all = existing is None   # 未提供累积集合 = 纯批语义：全部加前缀
    taken = {_text(node.get('key')) for node in (existing or []) if isinstance(node, dict)} \
        if existing is not None else set()
    mapping, used = {}, set(taken)
    for candidate in items:
        key = _text(candidate.get('key'))
        if not key or key.startswith(prefix) or key in mapping:
            continue
        if not prefixing_all and key not in taken:
            continue   # 跨批无冲突：保留原键（兼容路径），无需命名空间化
        target = prefix + key
        while target in used:   # 极小概率目标键仍被占用：追加下划线兜底，绝不制造新冲突
            target += '_'
        used.add(target)
        mapping[key] = target
    if not mapping:
        return items
    out = []
    for candidate in items:
        node = dict(candidate)
        key = _text(node.get('key'))
        if key in mapping:
            node['key'] = mapping[key]
        owner = _text(node.get('ownerKey'))
        if owner in mapping:
            node['ownerKey'] = mapping[owner]
        fields = node.get('fields') if isinstance(node.get('fields'), dict) else None
        if fields:
            fields = dict(fields)
            for slot in ('sourceRef', 'targetRef'):
                ref = _text(fields.get(slot))
                if ref in mapping:
                    fields[slot] = mapping[ref]
            node['fields'] = fields
        out.append(node)
    return out


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


def _ref_index(items):
    """候选引用索引：临时键与 ID 都能解析（与 ontology_adapter._resolve 口径一致）。

    R1 兜底：同名临时键跨批出现时（未经 `namespace_batch` 的旧存量数据、或直调
    `align` 的场景），旧实现「首到者胜」会把后续批次的同名对象吞掉，属性宿主必然
    解析错。这里保留**全部**同名命中（按出现顺序），交 `_owner_name_for` 按
    「最近 precedent」消歧；键唯一时命中唯一，行为与旧实现完全一致。
    """
    index = {}
    for order, candidate in enumerate(items):
        for field in ('key', 'id'):
            ref = _text(candidate.get(field))
            if ref:
                index.setdefault(ref, []).append((order, candidate))
    return index


def _resolve_owner(index, owner_key, own_order):
    """ownerKey → 宿主对象候选；解析不到返回 None。

    同名命中多个对象时取**属性之前最近的宿主**：抽取与累积都按批连续落位、
    批内对象先于其属性输出，最近 precedent 与「批内解析」一致（跨批同名时这比
    全局首到者胜可靠）；之前没有命中（属性先于对象输出的少数布局）则取之后最近
    的一个。仅作未命名空间化数据的兜底，管线主路径的键经 `namespace_batch`
    已批间唯一，不会进入多命中分支。
    """
    matches = index.get(owner_key) or []
    objects = [(order, node) for order, node in matches
               if _text(node.get('type')).casefold() == 'object']
    if not objects:
        return None
    if len(objects) == 1:
        return objects[0][1]
    preceding = [item for item in objects if item[0] < own_order]
    return preceding[-1][1] if preceding else objects[0][1]


def _owner_name_for(candidate, index, own_order=0):
    """属性/规则/动作的 ownerKey → 宿主对象候选的名称；解析不到返回 None（键里记 unknown）。"""
    if _text(candidate.get('type')).casefold() not in ('property',):
        return None
    owner = _resolve_owner(index, _text(candidate.get('ownerKey')), own_order)
    if isinstance(owner, dict) and _text(owner.get('type')).casefold() == 'object':
        return _text(owner.get('name'))
    return None


def align(candidates):
    """候选列表（同一批）→ 对齐后的候选列表 + 说明。

    返回 {'candidates': [...], 'notes': [...], 'stats': {...}}；
    合并后的候选带 alignedKey / conflicts / mergedFromKeys，evidenceStatus 按上述规则重算。
    属性的对齐键含宿主对象名（D08）：宿主先经本批引用索引解析（临时键或 ID；
    同名键多命中时按 `_resolve_owner` 的最近 precedent 消歧），解析不到按
    'unknown' 参与分组。
    """
    items = [candidate for candidate in (candidates or []) if isinstance(candidate, dict)]
    index = _ref_index(items)
    owner_names = [_owner_name_for(candidate, index, order)
                   for order, candidate in enumerate(items)]
    groups, order, first_order = {}, [], {}
    for cand_index, candidate in enumerate(items):
        key = align_key(candidate, owner_names[cand_index])
        bucket_key = key or '#unnamed-%d' % cand_index  # 无名称候选绝不合并
        if bucket_key not in groups:
            groups[bucket_key] = []
            first_order[bucket_key] = cand_index
            order.append(bucket_key)
        groups[bucket_key].append(candidate)

    out, notes = [], []
    merged_total = conflicts_total = 0
    for bucket_key in order:
        members = groups[bucket_key]
        key = align_key(members[0], owner_names[first_order[bucket_key]])
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
    事实先按 id 去重；重复（同事实身份，见 retrieval.snippet_digest）只在 duplicates 里
    指向首个副本，不计入 items。
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

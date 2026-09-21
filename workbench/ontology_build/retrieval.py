"""从物料构建本体：本地检索与范围筛选（确定性、纯标准库、**不调用模型**）。

职责边界（接口文档 08 §1.3/§1.6、开发计划 §5.2）：
* 只对已解析的 **事实（Fact）** 做关键词命中与有限依赖扩展，产出「哪些事实属于本次
  范围」的裁决与理由；不做语义理解、不做候选抽象、不猜单位/关系。
* 绝不因文件名或扩展名过滤材料：命中不到关键词的事实仍保守保留在 `related`
  （不丢材料，交由模型与人工判断）。
* 同一 snippet 哈希出现多次只算一条证据（重复副本不构成独立佐证）；重复项仍保留在
  `related` 并在 reasons 里说明与哪条事实重复。
* 完全确定性：同一输入永远同一输出（不依赖时间、随机数、字典序以外的顺序）。

范围词分级（保守口径）：
* 强词 = 范围文本里整段英文/数字标识或整段中文（2–12 字）→ 命中 `include` 判 relevant。
* 弱词 = 超长中文串切出的 2–3 字片段 → 只用于把事实标为 related 并给出更具体的理由，
  绝不用弱词判 relevant，也不用弱词判 excluded（避免误排除）。
* `goal` / `relations` 只作为 related 的理由来源（不改变 include/exclude 裁决）。
"""

import hashlib
import re

# 中文（含扩展区）与英文/数字标识的粗切分：不做分词，保持确定性
_TERM_RE = re.compile(r'[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+')
_ASCII_ONLY = re.compile(r'^[A-Za-z0-9_]+$')

MIN_ASCII_TERM = 3
MIN_CJK_TERM = 2
MAX_TERM_LEN = 12
WEAK_GRAM_MIN = 2
WEAK_GRAM_MAX = 3
DEPENDENT_PER_HIT = 5          # 每个 relevant 最多带出的同模块/同文件事实数
_MAX_FIELD_CHARS = 400
_MAX_DATA_ITEMS = 40


def _id_of(fact):
    return str((fact or {}).get('id') or '')


def _snippet_hash(text):
    return hashlib.sha1(str(text or '').encode('utf-8')).hexdigest()


def _flatten(value, depth=0, out=None):
    """data 值 → 参与检索的字符串列表（限深限量，防止超大嵌套拖慢检索）。"""
    out = [] if out is None else out
    if len(out) >= _MAX_DATA_ITEMS or depth > 2:
        return out
    if isinstance(value, dict):
        for key, item in value.items():
            if len(out) >= _MAX_DATA_ITEMS:
                break
            out.append(str(key))
            _flatten(item, depth + 1, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            if len(out) >= _MAX_DATA_ITEMS:
                break
            _flatten(item, depth + 1, out)
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value)
        if text:
            out.append(text[:_MAX_FIELD_CHARS])
    return out


def searchable_text(fact):
    """事实的可检索文本（snippet + 定位 + 结构化字段，统一 casefold）。"""
    fact = fact if isinstance(fact, dict) else {}
    parts = [str(fact.get('snippet') or ''), str(fact.get('kind') or ''),
             str(fact.get('module') or '')]
    locator = fact.get('locator') if isinstance(fact.get('locator'), dict) else {}
    for value in locator.values():
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            parts.append(str(value))
    parts.extend(_flatten(fact.get('data')))
    return ' '.join(part for part in parts if part).casefold()


def index_tokens(text):
    """文本 → 索引 token（英文/数字标识原样小写；中文整段 2–12 字）。"""
    tokens = []
    seen = set()
    for chunk in _TERM_RE.findall(str(text or '')):
        token = chunk.casefold()
        if _ASCII_ONLY.match(chunk):
            if len(chunk) < 2:
                continue
        elif not (MIN_CJK_TERM <= len(chunk) <= MAX_TERM_LEN):
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def build_index(facts):
    """事实列表 → 本地检索索引。

    返回 {'modules': [...], 'tokens': {token: [factId]}, 'byKind': {kind: [factId]}, ...}；
    额外键（byModule/byFile/text/snippetHash/material/order）供 select_scope 与复核使用，
    全部按输入顺序稳定生成。
    """
    modules = []
    tokens = {}
    by_kind = {}
    by_module = {}
    by_file = {}
    texts = {}
    hashes = {}
    materials = {}
    order = []
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        fact_id = _id_of(fact)
        if not fact_id:
            continue
        order.append(fact_id)
        module = str(fact.get('module') or '')
        if module and module not in modules:
            modules.append(module)
        kind = str(fact.get('kind') or '')
        by_kind.setdefault(kind, []).append(fact_id)
        by_module.setdefault(module, []).append(fact_id)
        locator = fact.get('locator') if isinstance(fact.get('locator'), dict) else {}
        file_name = str(locator.get('file') or '')
        by_file.setdefault(file_name, []).append(fact_id)
        text = searchable_text(fact)
        texts[fact_id] = text
        hashes[fact_id] = _snippet_hash(fact.get('snippet'))
        materials[fact_id] = str(fact.get('materialId') or '')
        for token in index_tokens(' '.join([text, module, kind, file_name])):
            bucket = tokens.setdefault(token, [])
            if fact_id not in bucket:
                bucket.append(fact_id)
    return {'modules': modules, 'tokens': tokens, 'byKind': by_kind, 'byModule': by_module,
            'byFile': by_file, 'text': texts, 'snippetHash': hashes, 'material': materials,
            'order': order}


def scope_terms(text):
    """范围文本 → (强词, 弱词)，均为去重后的有序列表。"""
    strong, weak = [], []
    strong_seen, weak_seen = set(), set()
    for chunk in _TERM_RE.findall(str(text or '')):
        folded = chunk.casefold()
        if _ASCII_ONLY.match(chunk):
            if len(chunk) < MIN_ASCII_TERM:
                continue
            if folded not in strong_seen:
                strong_seen.add(folded)
                strong.append(folded)
            continue
        if MIN_CJK_TERM <= len(chunk) <= MAX_TERM_LEN:
            if folded not in strong_seen:
                strong_seen.add(folded)
                strong.append(folded)
            continue
        # 超长中文串：整段仍是强词（长命中更精确），同时切出 2–3 字弱片段
        if len(chunk) > MAX_TERM_LEN and folded not in strong_seen:
            strong_seen.add(folded)
            strong.append(folded)
        for size in range(WEAK_GRAM_MIN, WEAK_GRAM_MAX + 1):
            for start in range(0, max(0, len(chunk) - size + 1)):
                gram = chunk[start:start + size].casefold()
                if gram not in weak_seen and gram not in strong_seen:
                    weak_seen.add(gram)
                    weak.append(gram)
    return strong, weak


def _hits(terms, text):
    return [term for term in terms if term and term in text]


def select_scope(facts, scope, index=None):
    """按范围文本裁决事实归属。

    返回 {'relevant': [factId], 'related': [factId], 'excluded': [factId],
          'reasons': {factId: 中文说明}, 'counts': {...}}
    规则（顺序即优先级）：
      1. 命中 include 强词 → relevant；同 snippet 哈希已在前面作为证据出现 → related（重复副本）。
      2. 否则命中 exclude 强词 → excluded（记录原因）。
      3. 其余 → 先做依赖扩展（与 relevant 同 module 或同 file，每个 relevant 最多 5 条）→ related。
      4. 仍未归类的 → related（保守保留，不丢材料）。
    排除项绝不因依赖扩展复活；文件名/扩展名不参与过滤。
    """
    scope = scope if isinstance(scope, dict) else {}
    facts = [fact for fact in (facts or []) if isinstance(fact, dict) and _id_of(fact)]
    index = index or build_index(facts)
    include_strong, include_weak = scope_terms(scope.get('include'))
    exclude_strong, _exclude_weak = scope_terms(scope.get('exclude'))
    hint_strong, _hint_weak = scope_terms(scope.get('goal'))
    relation_strong, _relation_weak = scope_terms(scope.get('relations'))
    hints = list(hint_strong) + list(relation_strong)

    contexts = {_id_of(fact): fact for fact in facts}
    texts = index.get('text') or {}
    hashes = index.get('snippetHash') or {}
    by_module = index.get('byModule') or {}
    by_file = index.get('byFile') or {}
    order = [fact_id for fact_id in (index.get('order') or []) if fact_id in contexts] or list(contexts)

    relevant, related, excluded = [], [], []
    reasons = {}
    claimed = {}
    used_hash = {}
    for fact_id in order:
        text = texts.get(fact_id, '')
        hit_include = _hits(include_strong, text)
        hit_exclude = _hits(exclude_strong, text)
        if hit_include:
            digest = hashes.get(fact_id, '')
            if digest and digest in used_hash:
                related.append(fact_id)
                reasons[fact_id] = ('与 %s 内容重复（同一片段副本，不作为独立佐证）'
                                    % used_hash[digest])
                claimed[fact_id] = 'related'
                continue
            if digest:
                used_hash[digest] = fact_id
            note = '命中纳入范围：%s' % '、'.join(hit_include[:5])
            if hit_exclude:
                note += '；同时命中排除范围词（纳入优先，请人工确认）：%s' % '、'.join(hit_exclude[:5])
            relevant.append(fact_id)
            claimed[fact_id] = 'relevant'
            reasons[fact_id] = note
        elif hit_exclude:
            excluded.append(fact_id)
            claimed[fact_id] = 'excluded'
            reasons[fact_id] = '命中排除范围：%s' % '、'.join(hit_exclude[:5])

    # 依赖有限扩展：与 relevant 同模块或同文件，每个 relevant 最多 DEPENDENT_PER_HIT 条
    for fact_id in relevant:
        module = str(contexts[fact_id].get('module') or '')
        locator = contexts[fact_id].get('locator')
        file_name = str((locator or {}).get('file') or '') if isinstance(locator, dict) else ''
        neighbours, seen = [], set()
        for neighbour in (by_module.get(module) or []) + (by_file.get(file_name) or []):
            if neighbour == fact_id or neighbour in seen:
                continue
            seen.add(neighbour)
            if claimed.get(neighbour) == 'excluded':
                continue  # 排除项绝不因依赖扩展复活
            neighbours.append(neighbour)
        taken = 0
        for neighbour in neighbours:
            if taken >= DEPENDENT_PER_HIT:
                break
            if neighbour in claimed:
                continue
            related.append(neighbour)
            claimed[neighbour] = 'related'
            same_file = bool(file_name) and neighbour in (by_file.get(file_name) or [])
            reasons[neighbour] = ('与纳入事实 %s 同一%s（依赖扩展，每个纳入项最多 %d 条）'
                                  % (fact_id, '模块' if not same_file else '文件',
                                     DEPENDENT_PER_HIT))
            taken += 1

    # 其余事实：保守保留（不丢材料），理由区分是否与目标/关联描述弱相关
    for fact_id in order:
        if fact_id in claimed:
            continue
        text = texts.get(fact_id, '')
        weak_hits = _hits(include_weak, text)
        hint_hits = _hits(hints, text)
        if weak_hits:
            reasons[fact_id] = ('范围描述的片段命中（保守保留，待人工确认）：%s'
                                % '、'.join(weak_hits[:5]))
        elif hint_hits:
            reasons[fact_id] = ('命中建模目标/关联说明关键词（保守保留）：%s'
                                % '、'.join(hint_hits[:5]))
        else:
            reasons[fact_id] = '未命中范围关键词，保守保留（不丢材料）'
        related.append(fact_id)
        claimed[fact_id] = 'related'

    return {'relevant': relevant, 'related': related, 'excluded': excluded, 'reasons': reasons,
            'counts': {'relevant': len(relevant), 'related': len(related),
                       'excluded': len(excluded), 'total': len(order)}}


def select_scope_ids(selection, bucket='relevant'):
    """便捷读取（供管线按序取事实 ID；缺省返回空列表）。"""
    if not isinstance(selection, dict):
        return []
    value = selection.get(bucket)
    return [str(item) for item in value] if isinstance(value, list) else []


def snippet_digest(fact):
    """供 alignment / 复核共用的片段哈希（同一实现的唯一入口）。"""
    return _snippet_hash((fact or {}).get('snippet'))

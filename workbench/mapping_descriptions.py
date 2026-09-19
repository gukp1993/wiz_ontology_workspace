"""四类项目说明（bindings.mappingDescriptions）——结构与引用校验、兼容合并、消费检查。

协议（接口文档 01 §3.4，2026-09-19）：schemaVersion=1；四类键 objects/properties/
links/actions；一律稳定 ID（对象/链接含 mg: 前缀；properties 二级键 = 对象实际属性
节点稳定 ID，非 apiName、非共享库根定义 ID；actions 二级键 = 对象ID → 动作ID）。
单项上限 20,000 Unicode 码点（Python len 即码点数）。纯空白文本按清除处理。

语义边界：
- 缺省（旧数据无该键）= 无说明，不迁移、不生成空文本。
- 写路径「旧客户端省略整块保留」在路由层 CAS/写锁边界内经 merge_omitted 完成。
- 校验只查结构与引用（失效 → 阻断发布），不做自然语言语义判断。
- 尚不支持说明语义的执行入口用 preview_block 判断目标/依赖说明，取数前拒绝。
"""
from workbench.project_mapping import bare

SCHEMA_VERSION = 1
MAX_TEXT_POINTS = 20000
KINDS = ('objects', 'properties', 'links', 'actions')
KIND_LABELS = {'objects': '对象说明', 'properties': '属性说明', 'links': '链接说明', 'actions': '动作说明'}
UNSUPPORTED_MESSAGE = '不支持当前说明，请使用支持项目说明的执行器'


def block_of(state):
    """读取说明块；缺失或非 dict 返回 None（= 无说明，不当空内容清理）。"""
    block = (state.get('bindings') or {}).get('mappingDescriptions')
    return block if isinstance(block, dict) else None


def _clean_text(value):
    """合法文本 → 规范化字符串（换行统一 LF，纯空白按清除=返回 ''）；非法 → None。"""
    if not isinstance(value, str):
        return None
    text = value.replace('\r\n', '\n').replace('\r', '\n')
    return '' if not text.strip() else text


def normalize_block(raw):
    """规范化副本：仅保留 schemaVersion=1 的合法结构；纯空白文本删键。

    结构非法时抛 ValueError（消息可直接给用户）；不认识的更高版本原样抛错拒绝写入。
    返回 (block, changed)——changed 表示与传入相比有规范化差异（如清除空白项）。
    """
    if raw is None:
        return None, False
    if not isinstance(raw, dict):
        raise ValueError('mappingDescriptions 必须是对象')
    version = raw.get('schemaVersion')
    if version != SCHEMA_VERSION:
        raise ValueError(f'mappingDescriptions.schemaVersion 仅支持 {SCHEMA_VERSION}（收到：{version!r}）；更高版本请升级工作台后再编辑')
    out: dict = {'schemaVersion': SCHEMA_VERSION}
    changed = False
    for kind in KINDS:
        section = raw.get(kind)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise ValueError(f'mappingDescriptions.{kind} 必须是对象')
        clean_section: dict = {}
        for key, value in section.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f'mappingDescriptions.{kind} 存在无效键')
            if kind in ('properties', 'actions'):
                if not isinstance(value, dict):
                    raise ValueError(f'mappingDescriptions.{kind}.{key} 必须是对象')
                clean_inner: dict = {}
                for inner_key, inner_value in value.items():
                    if not isinstance(inner_key, str) or not inner_key.strip():
                        raise ValueError(f'mappingDescriptions.{kind}.{key} 存在无效键')
                    text = _clean_text(inner_value)
                    if text is None:
                        raise ValueError(f'mappingDescriptions.{kind}.{key}.{inner_key} 必须是文本')
                    if len(text) > MAX_TEXT_POINTS:
                        raise ValueError(f'{KIND_LABELS[kind]}「{inner_key}」超过 {MAX_TEXT_POINTS} 字上限')
                    if not text:
                        changed = True
                        continue
                    clean_inner[inner_key] = text
                if clean_inner:
                    clean_section[key] = clean_inner
                if clean_inner != value:
                    changed = True
                continue
            text = _clean_text(value)
            if text is None:
                raise ValueError(f'mappingDescriptions.{kind}.{key} 必须是文本')
            if len(text) > MAX_TEXT_POINTS:
                raise ValueError(f'{KIND_LABELS[kind]}「{key}」超过 {MAX_TEXT_POINTS} 字上限')
            if not text:
                changed = True
                continue
            clean_section[key] = text
        if clean_section != section:
            changed = True
        if clean_section:
            out[kind] = clean_section
    if set(out) != set(raw):
        changed = True
    return out, changed


def merge_omitted(incoming_state, saved_state):
    """旧客户端省略整块保留：incoming bindings 无 mappingDescriptions **键**时，
    沿用 saved 的块（原样，含服务端不认识的更高版本——只读保留语义）。

    返回 True 表示 incoming 被补全（调用方需以返回后的 state 为准）。键存在（含
    空对象）= 完整块提交，显式清空按删键表达，绝不在此补回。
    """
    incoming_bindings = incoming_state.get('bindings')
    saved_block = block_of(saved_state)
    if not isinstance(incoming_bindings, dict):
        return False
    if 'mappingDescriptions' in incoming_bindings:
        return False
    if saved_block is None:
        return False
    import copy
    incoming_bindings['mappingDescriptions'] = copy.deepcopy(saved_block)
    return True


def iter_descriptions(block):
    """展开为 (kind, object_id, item_id|None, text)；item_id 仅 properties/actions 有。"""
    if not isinstance(block, dict):
        return
    for kind in KINDS:
        section = block.get(kind)
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if kind in ('properties', 'actions') and isinstance(value, dict):
                for inner_key, inner_value in value.items():
                    if isinstance(inner_value, str) and inner_value.strip():
                        yield kind, key, inner_key, inner_value
            elif isinstance(value, str) and value.strip():
                yield kind, key, None, value


def _objects_of(ontology_state):
    return {n['@id'] for n in ontology_state.get('ontology', {}).get('@graph', [])
            if n.get('@type') == 'owl:Class'}


def _links_of(ontology_state):
    return {n['@id'] for n in ontology_state.get('ontology', {}).get('@graph', [])
            if n.get('@type') == 'owl:ObjectProperty'}


def _property_ids_by_object(ontology_state):
    """对象稳定 ID → 该对象实际属性节点稳定 ID 集合（直接属性 + 共享引用节点）。"""
    out: dict = {}
    for n in ontology_state.get('ontology', {}).get('@graph', []):
        if n.get('@type') != 'owl:DatatypeProperty':
            continue
        domain = (n.get('rdfs:domain') or {}).get('@id', '') if isinstance(n.get('rdfs:domain'), dict) else ''
        if domain:
            out.setdefault(domain, set()).add(n['@id'])
    return out


def _actions_of(ontology_state):
    return {str(a.get('id', '')) for a in (ontology_state.get('workflow', {}).get('actions') or [])
            if isinstance(a, dict) and a.get('id')}


def stale_references(state, ontology_state):
    """失效引用清单：[{kind, id, item_id, text}]——键指向的元素在引用本体版本中不存在。

    语义与 project_validation 的容忍口径一致：对象/链接按 mg: 前缀 bare 比对；属性
    内层键必须属于该对象的实际属性节点；动作内层键必须存在于本体 workflow.actions。
    """
    block = block_of(state)
    if not isinstance(block, dict) or block.get('schemaVersion') != SCHEMA_VERSION:
        return []
    classes = _objects_of(ontology_state)
    bare_classes = {bare(c) for c in classes}
    links = _links_of(ontology_state)
    bare_links = {bare(c) for c in links}
    props_by_obj = _property_ids_by_object(ontology_state)
    actions = _actions_of(ontology_state)
    out = []
    for kind, obj_id, item_id, _text in iter_descriptions(block):
        if kind == 'objects':
            if obj_id not in classes and bare(obj_id) not in bare_classes:
                out.append({'kind': kind, 'id': obj_id, 'item_id': None, 'text': '说明指向的对象在本体中不存在'})
        elif kind == 'links':
            if obj_id not in links and bare(obj_id) not in bare_links:
                out.append({'kind': kind, 'id': obj_id, 'item_id': None, 'text': '说明指向的链接在本体中不存在'})
        elif kind == 'properties':
            known = props_by_obj.get(obj_id if obj_id in props_by_obj else 'mg:' + obj_id.removeprefix('mg:'), set())
            if obj_id not in props_by_obj and bare(obj_id) not in {bare(c) for c in props_by_obj}:
                out.append({'kind': kind, 'id': obj_id, 'item_id': item_id, 'text': '说明指向的对象在本体中不存在'})
            elif item_id not in known:
                out.append({'kind': kind, 'id': obj_id, 'item_id': item_id, 'text': '说明指向的属性不属于该对象（属性已删除或移到其他对象）'})
        elif kind == 'actions':
            if obj_id not in classes and bare(obj_id) not in bare_classes:
                out.append({'kind': kind, 'id': obj_id, 'item_id': item_id, 'text': '说明指向的对象在本体中不存在'})
            elif item_id not in actions:
                out.append({'kind': kind, 'id': obj_id, 'item_id': item_id, 'text': '说明指向的动作在本体中不存在'})
    return out


def configured_of(state):
    """当前项目的可执行配置指纹（判定「只有说明」用）：对象绑定数 / 属性来源数 /
    链接映射数 / 动作实现数。"""
    bindings = state.get('bindings') or {}
    object_bindings = [b for b in (bindings.get('object_bindings') or []) if isinstance(b, dict)]
    property_sources = sum(1 for b in object_bindings for k, v in (b.get('properties') or {}).items() if v)
    link_maps = sum(len(b.get('relations') or []) for b in object_bindings)
    action_impls = len([a for a in (bindings.get('actionBindings') or []) if isinstance(a, dict)])
    return {'objectBindings': len(object_bindings), 'propertySources': property_sources,
            'linkMappings': link_maps, 'actionBindings': action_impls}


def plain_description_only(state):
    """「只有说明，未启用具体映射」：存在非空说明且四类具体配置全为零。"""
    block = block_of(state)
    has_text = any(True for _ in iter_descriptions(block)) if isinstance(block, dict) else False
    if not has_text:
        return False
    counts = configured_of(state)
    return not any(counts.values())


def has_description(state, object_type, item_id=None, kind='objects'):
    """消费者能力检查：目标（或其属性/动作项）是否存在非空说明。

    object_type / item_id 兼容 mg: 前缀与 bare 两种形态；kind 取
    'objects' | 'properties' | 'links' | 'actions'。
    """
    block = block_of(state)
    if not isinstance(block, dict):
        return False
    section = block.get(kind)
    if not isinstance(section, dict):
        return False
    want_bare = bare(str(object_type or ''))
    hit_key = None
    for key in section:
        if key == object_type or bare(key) == want_bare:
            hit_key = key
            break
    if hit_key is None:
        return False
    value = section[hit_key]
    if item_id is None:
        return isinstance(value, str) and bool(value.strip())
    if not isinstance(value, dict):
        return False
    text = value.get(item_id)
    if isinstance(text, str) and text.strip():
        return True
    # item_id 兼容：调用方可能传 apiName 或缺 mg: 前缀的节点 id
    for key2, text2 in value.items():
        if isinstance(text2, str) and text2.strip() and (key2 == item_id or key2.endswith(item_id) or key2.removeprefix('mg:') == item_id):
            return True
    return False


def preview_block(state, object_type, item_id=None, kind='objects'):
    """尚不支持说明语义的执行入口调用：命中说明 → 返回 (True, 提示消息)；否则 (False, '')。

    properties/actions 同时受对象级说明约束（对象说明描述实例身份与取值范围）。
    """
    checks = [(kind, object_type, item_id)] if kind in ('properties', 'actions') else [(kind, object_type, None)]
    if kind in ('properties', 'actions'):
        checks.append(('objects', object_type, None))
    for kind_, obj, item in checks:
        if has_description(state, obj, item, kind_):
            label = KIND_LABELS.get(kind_, kind_)
            return True, UNSUPPORTED_MESSAGE + f'（{label}：{obj}' + (f'/{item}' if item else '') + '）'
    return False, ''


def context_of(state):
    """10.1 映射上下文（纯函数）：项目与引用版本 + 元素稳定 ID + 说明原文 + 具体配置指纹。

    供后续消费者复用；本期只保证「取得到」，不做任何解释或执行。
    """
    block = block_of(state) or {}
    return {'schemaVersion': block.get('schemaVersion'),
            'projectId': state.get('projectId', ''),
            'ontologyId': state.get('ontologyId', ''),
            'ontologyVersion': str(state.get('ontologyVersion', '')),
            'mappingDescriptions': block,
            'configured': configured_of(state)}

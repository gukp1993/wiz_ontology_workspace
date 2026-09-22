"""autofill/1 表单契约装载器 —— T1（2026-09-22 整表自动填写改版）。

契约文件是字段定义的单一来源（接口文档 04 §6.1）：`contracts/forms/<formId>.json`，
10 个 formId 与 §5.0 targetKind 一一对应；propertySource 按 draft.kind 分派子契约变体
（单文件内 variants，loader 按 (formId, draftKind) 取）。

职责与边界：
* load(form_id, draft_kind)：读文件 → 严格语法校验（未知键/未知类型/条件表达式非法
  一律 ValueError）→ canonical JSON（键排序、去 schemaDigest）重算 SHA-256 digest。
  运行时不接受客户端提交 schema，本模块只读仓库内契约文件。
* schema_version / schema_digest：响应回传用；digest 变化即 CONTEXT_STALE 语义。
* field_def / atomic_groups / list_def / list_defs / ref_provider：按点路径查字段、
  原子组集合、list 定义查询（T2 校验与 T3 生成共用）。
* check_consistency：全部契约装载检查（构建期/测试用），返回问题列表（空列表=通过）。

assist_fields.py（旧 check/explain 协议注册表）继续独立使用；两边的一致性由
tests/test_autofill_contracts.py 守护，本模块不 import 它，避免互相牵连。
"""
import copy
import hashlib
import json
from pathlib import Path

from workbench.paths import CODE_ROOT

# ---- 冻结常量（接口文档 04 §6.0/§6.1） ----

FORM_IDS = ('object', 'property', 'sharedProperty', 'link', 'rule', 'action',
            'identity', 'propertySource', 'linkMapping', 'actionBinding')

PROPERTY_SOURCE_FORM = 'propertySource'
PROPERTY_SOURCE_KINDS = ('field', 'database', 'redis', 'flow')

FIELD_TYPES = ('text', 'textarea', 'enum', 'boolean', 'ref', 'group', 'list')
WHEN_OPS = ('eq', 'ne', 'in', 'notEmpty')
WHEN_OPS_WITH_VALUE = ('eq', 'ne', 'in')

TOP_KEYS = {'formId', 'schemaVersion', 'title', 'space', 'fields', 'variants',
            'lists', 'codecs', 'refProviders',
            # schemaDigest：允许文件自带（如历史工具落盘），loader 忽略并重算（canonical 去 digest）
            'schemaDigest'}
VARIANT_KEYS = {'fields', 'lists'}
FIELD_KEYS = {'id', 'label', 'type', 'required', 'maxLength', 'enum', 'nullable',
              'visibleWhen', 'editableWhen', 'atomicGroup', 'requires', 'ai',
              'codec', 'refProvider', 'fields', 'list'}
WHEN_KEYS = {'field', 'op', 'value'}
AI_KEYS = {'fillable', 'clearable', 'sensitive'}
LIST_KEYS = {'id', 'rowIdScope', 'item'}
SPACES = ('ontology', 'project')

# 认证/凭据与分派键永远不成为契约字段（与 assist_fields.FORBIDDEN_KEYS 同一边界）
FORBIDDEN_IDS = {'auth', 'apiKey', 'api_key', 'credentialId', 'secret', 'password', 'kind'}

DEFAULT_FORMS_DIR = CODE_ROOT / 'contracts' / 'forms'


class ContractError(ValueError):
    """契约缺失/语法/语义非法。继承 ValueError 便于调用方统一按 400 语义处理。"""


# ---- digest ------------------------------------------------------------------

def _forms_dir(forms_dir=None):
    return Path(forms_dir) if forms_dir else DEFAULT_FORMS_DIR


def _contract_path(form_id, forms_dir=None):
    if form_id not in FORM_IDS:
        raise ContractError('未知的表单契约 formId：' + str(form_id))
    return _forms_dir(forms_dir) / (form_id + '.json')


def canonical_bytes(doc):
    """canonical JSON 字节（键排序、紧凑分隔、递归去 schemaDigest）——digest 唯一口径。"""
    def strip_digest(obj):
        if isinstance(obj, dict):
            return {k: strip_digest(v) for k, v in obj.items() if k != 'schemaDigest'}
        if isinstance(obj, list):
            return [strip_digest(v) for v in obj]
        return obj

    payload = json.dumps(strip_digest(doc), ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'))
    return payload.encode('utf-8')


def digest_of(doc):
    return hashlib.sha256(canonical_bytes(doc)).hexdigest()


# ---- 装载与校验 ----------------------------------------------------------------

def load_raw(form_id, forms_dir=None):
    """读取并校验整份契约文件。返回 (doc, schema_version, schema_digest)。

    variants 结构原样保留（生成器取全量；运行时单变体走 load）。
    """
    path = _contract_path(form_id, forms_dir)
    if not path.is_file():
        raise ContractError('表单契约文件缺失：%s（formId=%s）' % (path, form_id))
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            doc = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ContractError('表单契约不是合法 JSON：%s：%s' % (path, exc)) from exc
    if not isinstance(doc, dict):
        raise ContractError('表单契约顶层必须是 JSON 对象：%s' % path)

    unknown_top = sorted(set(doc) - TOP_KEYS)
    if unknown_top:
        raise ContractError('契约 %s 含未冻结的顶层键：%s' % (form_id, ','.join(unknown_top)))
    if doc.get('formId') != form_id:
        raise ContractError('契约 formId 与文件名不一致：%s ≠ %s' % (doc.get('formId'), form_id))
    version = doc.get('schemaVersion')
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ContractError('契约 %s 的 schemaVersion 必须是正整数' % form_id)
    if not isinstance(doc.get('title'), str) or not doc.get('title'):
        raise ContractError('契约 %s 缺少 title' % form_id)
    if doc.get('space') not in SPACES:
        raise ContractError('契约 %s 的 space 必须是 ontology|project' % form_id)
    if 'codecs' in doc and not _string_list(doc.get('codecs')):
        raise ContractError('契约 %s 的 codecs 必须是非空字符串数组' % form_id)
    ref_providers = doc.get('refProviders', {})
    if not isinstance(ref_providers, dict) or not all(
            isinstance(k, str) and k and isinstance(v, str) and v
            for k, v in ref_providers.items()):
        raise ContractError('契约 %s 的 refProviders 必须是 角色→提供方名称 的字符串映射' % form_id)

    if form_id == PROPERTY_SOURCE_FORM:
        if 'variants' not in doc:
            raise ContractError('契约 propertySource 必须按 draft.kind 提供 variants')
        if 'fields' in doc:
            raise ContractError('契约 propertySource 不允许顶层 fields（用 variants 分派）')
        variants = doc['variants']
        if not isinstance(variants, dict) or not variants:
            raise ContractError('契约 propertySource 的 variants 必须是非空对象')
        bad = sorted(set(variants) - set(PROPERTY_SOURCE_KINDS))
        if bad:
            raise ContractError('契约 propertySource 含未冻结的 draft.kind：%s' % ','.join(bad))
        missing = [k for k in PROPERTY_SOURCE_KINDS if k not in variants]
        if missing:
            raise ContractError('契约 propertySource 缺少 draft.kind 变体：%s' % ','.join(missing))
        for kind in PROPERTY_SOURCE_KINDS:
            _validate_variant(form_id, kind, variants[kind], ref_providers)
    else:
        if 'variants' in doc:
            raise ContractError('契约 %s 不允许 variants（仅 propertySource 分派）' % form_id)
        if not isinstance(doc.get('fields'), list) or not doc['fields']:
            raise ContractError('契约 %s 缺少非空 fields' % form_id)
        scope_lists = _collect_lists(form_id, '顶层', doc.get('lists'), ref_providers)
        _validate_scope(form_id, '顶层', doc['fields'], ref_providers, scope_lists, False)

    for problem in _sensitive_ref_violations(doc):
        raise ContractError('契约 %s：%s' % (form_id, problem))

    return doc, version, digest_of(doc)


def _string_list(value):
    return isinstance(value, list) and all(isinstance(v, str) and v for v in value)


def _validate_variant(form_id, kind, variant, ref_providers):
    where = 'variants.%s' % kind
    if not isinstance(variant, dict):
        raise ContractError('契约 %s 的 %s 必须是对象' % (form_id, where))
    unknown = sorted(set(variant) - VARIANT_KEYS)
    if unknown:
        raise ContractError('契约 %s 的 %s 含未冻结键：%s' % (form_id, where, ','.join(unknown)))
    scope_lists = _collect_lists(form_id, where, variant.get('lists'), ref_providers)
    if not isinstance(variant.get('fields'), list) or not variant['fields']:
        raise ContractError('契约 %s 的 %s.fields 必须是非空数组' % (form_id, where))
    _validate_scope(form_id, where, variant['fields'], ref_providers, scope_lists, False)


def _collect_lists(form_id, where, lists, ref_providers):
    """list 声明校验；返回 {listId: item}。item 支持两种冻结写法（由 loader 归一）：
    单字段定义（如 {"id":"left","type":"text"}）或 group 行结构（多字段行）；
    行内可见条件相对行根解析（row-local）。"""
    out = {}
    if lists is None:
        return out
    if not isinstance(lists, list):
        raise ContractError('契约 %s 的 %s.lists 必须是数组' % (form_id, where))
    for decl in lists:
        if not isinstance(decl, dict):
            raise ContractError('契约 %s 的 %s.lists 元素必须是对象' % (form_id, where))
        unknown = sorted(set(decl) - LIST_KEYS)
        if unknown:
            raise ContractError('契约 %s 的 list 声明含未冻结键：%s' % (form_id, ','.join(unknown)))
        list_id = decl.get('id')
        if not isinstance(list_id, str) or not list_id:
            raise ContractError('契约 %s 的 %s.lists 元素缺少 id' % (form_id, where))
        if list_id in out:
            raise ContractError('契约 %s 的 %s 重复声明 list：%s' % (form_id, where, list_id))
        if decl.get('rowIdScope') != 'local':
            raise ContractError('契约 %s 的 list %s 的 rowIdScope 冻结为 local' % (form_id, list_id))
        item = decl.get('item')
        if not isinstance(item, dict):
            raise ContractError('契约 %s 的 list %s 的 item 必须是字段定义' % (form_id, list_id))
        if item.get('type') == 'group':
            if not isinstance(item.get('fields'), list) or not item['fields']:
                raise ContractError('契约 %s 的 list %s 的 item 组行结构必须非空'
                                    % (form_id, list_id))
            _validate_scope(form_id, '%s(item of %s)' % (where, list_id), item['fields'],
                            ref_providers, set(), True, require_label=False)
        else:
            if not item.get('id') or not item.get('type'):
                raise ContractError('契约 %s 的 list %s 的 item 缺少 id/type' % (form_id, list_id))
            _validate_scope(form_id, '%s(item of %s)' % (where, list_id), [item],
                            ref_providers, set(), True, require_label=False)
        out[list_id] = item
    return out


def _validate_scope(form_id, where, fields, ref_providers, scope_lists, row_local,
                    root_paths=None, is_root=True, require_label=True):
    """校验同层字段数组。可见/依赖引用相对最近的行/表单根解析（root_paths）；
    嵌套组沿用其所在根的作用域。仅根层禁止把分派键 kind 声明为字段；
    list 行字段（item 作用域）不要求 label（04 §6.1 示例即无 label）。"""
    scope_paths = root_paths if root_paths is not None else _scope_paths(fields)
    seen = set()
    for field in fields:
        _validate_field(form_id, where, field, ref_providers, scope_lists,
                        row_local, scope_paths, seen, is_root, require_label)


def _scope_paths(fields):
    """收集作用域内可解析路径 → 节点。顶层扁平 id（含点，如 result.valueField）
    与结构化嵌套路径（组子字段）都可解析；扁平 id 优先。"""
    out = {}

    def walk(nodes, base):
        for f in nodes:
            if not isinstance(f, dict) or not isinstance(f.get('id'), str):
                continue
            path = (base + '.' + f['id']) if base else f['id']
            if path not in out:
                out[path] = f
            if f.get('type') == 'group' and isinstance(f.get('fields'), list):
                walk(f['fields'], path)

    walk(fields, '')
    return out


def _validate_field(form_id, where, field, ref_providers, scope_lists,
                    row_local, scope_paths, seen, is_root=True, require_label=True):
    if not isinstance(field, dict):
        raise ContractError('契约 %s 的 %s.fields 元素必须是对象' % (form_id, where))
    unknown = sorted(set(field) - FIELD_KEYS)
    if unknown:
        raise ContractError('契约 %s 的 %s 含未冻结的字段键：%s' % (form_id, where, ','.join(unknown)))
    fid = field.get('id')
    if not isinstance(fid, str) or not fid or fid.startswith('.') or fid.endswith('.') \
            or '..' in fid:
        raise ContractError('契约 %s 的 %s 含非法字段 id：%r' % (form_id, where, fid))
    root_of_fid = fid.split('.')[0]
    if root_of_fid == 'auth' or (is_root and fid in FORBIDDEN_IDS):
        raise ContractError('契约 %s 的 %s 含禁止成为契约字段的 id：%s' % (form_id, where, fid))
    if fid in seen:
        raise ContractError('契约 %s 的 %s 重复字段 id：%s' % (form_id, where, fid))
    seen.add(fid)
    if require_label and (not isinstance(field.get('label'), str) or not field.get('label')):
        raise ContractError('契约 %s 的字段 %s 缺少 label' % (form_id, fid))
    ftype = field.get('type')
    if ftype not in FIELD_TYPES:
        raise ContractError('契约 %s 的字段 %s 类型未冻结：%r' % (form_id, fid, ftype))

    for key in ('required', 'nullable'):
        if key in field and not isinstance(field[key], bool):
            raise ContractError('契约 %s 的字段 %s 的 %s 必须是布尔' % (form_id, fid, key))
    if 'maxLength' in field:
        limit = field['maxLength']
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ContractError('契约 %s 的字段 %s 的 maxLength 必须是正整数' % (form_id, fid))
        if ftype not in ('text', 'textarea'):
            raise ContractError('契约 %s 的字段 %s 只有 text/textarea 可声明 maxLength'
                                % (form_id, fid))

    if ftype == 'enum':
        options = field.get('enum')
        if not _string_list(options) or not options:
            raise ContractError('契约 %s 的枚举字段 %s 必须声明非空 enum 数组' % (form_id, fid))
        if len(set(options)) != len(options):
            raise ContractError('契约 %s 的枚举字段 %s 的 enum 含重复值' % (form_id, fid))
    elif 'enum' in field:
        raise ContractError('契约 %s 的字段 %s 只有 enum 类型可声明 enum' % (form_id, fid))

    if ftype == 'ref':
        role = field.get('refProvider')
        if not isinstance(role, str) or role not in ref_providers:
            raise ContractError('契约 %s 的引用字段 %s 的 refProvider 未登记：%r'
                                % (form_id, fid, role))
    elif 'refProvider' in field:
        raise ContractError('契约 %s 的字段 %s 只有 ref 类型可声明 refProvider' % (form_id, fid))

    if ftype == 'group':
        if 'codec' in field:
            # codec 托管的整组值（如 formatting）：载荷结构由已注册 codec 负责，不内嵌 fields
            if field.get('fields') is not None:
                raise ContractError('契约 %s 的 codec 组 %s 不允许再内嵌 fields' % (form_id, fid))
            if not isinstance(field['codec'], str) or not field['codec']:
                raise ContractError('契约 %s 的组 %s 的 codec 必须是非空字符串' % (form_id, fid))
        elif isinstance(field.get('fields'), list) and field['fields']:
            child_where = '%s.%s' % (where, fid)
            _validate_scope(form_id, child_where, field['fields'], ref_providers,
                            scope_lists, row_local, root_paths=scope_paths, is_root=False)
        else:
            raise ContractError('契约 %s 的组字段 %s 必须声明非空 fields（或声明 codec）'
                                % (form_id, fid))
    else:
        if 'fields' in field:
            raise ContractError('契约 %s 的字段 %s 只有 group 类型可声明 fields' % (form_id, fid))

    if ftype == 'list':
        list_id = field.get('list')
        if not isinstance(list_id, str) or list_id not in scope_lists:
            raise ContractError('契约 %s 的 list 字段 %s 未指向本层已声明 list：%r'
                                % (form_id, fid, list_id))
    elif 'list' in field:
        raise ContractError('契约 %s 的字段 %s 只有 list 类型可声明 list' % (form_id, fid))
    if 'codec' in field and ftype not in ('group', 'list'):
        # codec：已注册业务 setter 标识；group=整组值转换，list=参数行/行 ID 适配
        raise ContractError('契约 %s 的字段 %s 只有 group/list 类型可声明 codec' % (form_id, fid))

    for key in ('visibleWhen', 'editableWhen'):
        if key in field:
            _validate_when(form_id, fid, key, field[key], scope_paths)
    if 'requires' in field:
        dep = field['requires']
        if not isinstance(dep, str) or not dep:
            raise ContractError('契约 %s 的字段 %s 的 requires 必须是非空字段路径' % (form_id, fid))
        if dep not in scope_paths:
            raise ContractError('契约 %s 的字段 %s 的 requires 指向不存在字段：%s'
                                % (form_id, fid, dep))

    ai = field.get('ai', {'fillable': True})
    if not isinstance(ai, dict) or set(ai) - AI_KEYS \
            or not all(isinstance(v, bool) for v in ai.values()):
        raise ContractError('契约 %s 的字段 %s 的 ai 必须是 fillable/clearable/sensitive 布尔映射'
                            % (form_id, fid))
    if ai.get('sensitive') and ai.get('fillable', True):
        raise ContractError('契约 %s 的字段 %s 声明 sensitive 即不可写（fillable 必须为 false）'
                            % (form_id, fid))


def _validate_when(form_id, fid, key, when, scope_paths):
    if not isinstance(when, dict) or set(when) - WHEN_KEYS:
        raise ContractError('契约 %s 的字段 %s 的 %s 必须是 {field,op,value} 受限表达'
                            % (form_id, fid, key))
    op = when.get('op')
    if op not in WHEN_OPS:
        raise ContractError('契约 %s 的字段 %s 的 %s.op 未冻结：%r' % (form_id, fid, key, op))
    target = when.get('field')
    if not isinstance(target, str) or not target:
        raise ContractError('契约 %s 的字段 %s 的 %s.field 必须是非空字段路径' % (form_id, fid, key))
    if op in WHEN_OPS_WITH_VALUE:
        if 'value' not in when:
            raise ContractError('契约 %s 的字段 %s 的 %s（op=%s）缺少 value' % (form_id, fid, key, op))
    elif 'value' in when:
        raise ContractError('契约 %s 的字段 %s 的 %s（op=notEmpty）不接受 value' % (form_id, fid, key))
    if target not in scope_paths:
        raise ContractError('契约 %s 的字段 %s 的 %s.field 指向不存在字段：%s'
                            % (form_id, fid, key, target))


def _sensitive_ref_violations(doc):
    """ai.sensitive 字段不得充当引用候选：角色名/提供方名/引用字段的候选集不得指向它。"""
    problems = []
    sensitive_ids = set()

    def collect_sensitive(fields):
        for f in fields or []:
            if not isinstance(f, dict):
                continue
            if (f.get('ai') or {}).get('sensitive'):
                sensitive_ids.add(f.get('id'))
            collect_sensitive(f.get('fields'))

    def check_refs(fields):
        for f in fields or []:
            if not isinstance(f, dict):
                continue
            if f.get('type') == 'ref' and f.get('refProvider') in sensitive_ids:
                problems.append('引用字段 %s 的候选集指向 ai.sensitive 字段 %s'
                                % (f.get('id'), f.get('refProvider')))
            check_refs(f.get('fields'))

    if 'variants' in doc:
        scopes = [v.get('fields') for v in doc['variants'].values()]
    else:
        scopes = [doc.get('fields')]
    for fields in scopes:
        collect_sensitive(fields)
    if sensitive_ids:
        for role, provider in (doc.get('refProviders') or {}).items():
            for sid in sensitive_ids:
                if sid in (role, provider):
                    problems.append('ai.sensitive 字段 %s 不得充当引用候选（refProviders.%s）'
                                    % (sid, role))
        for fields in scopes:
            check_refs(fields)
    return problems


# ---- 查询（点路径 / 原子组 / list） ---------------------------------------------

def _form_scope(form_id, draft_kind=None, forms_dir=None):
    """装载并返回 (doc, version, digest, 顶层字段索引, {listId: item}, draft_kind)。"""
    doc, version, digest = load_raw(form_id, forms_dir)
    if form_id == PROPERTY_SOURCE_FORM:
        if draft_kind is None:
            raise ContractError('表单 propertySource 按 draft.kind 分派子契约，必须提供 draft_kind')
        if draft_kind not in doc['variants']:
            raise ContractError('未知的属性取值来源 draft.kind：%s' % draft_kind)
        variant = doc['variants'][draft_kind]
        index = _scope_paths(variant['fields'])
        lists = {decl['id']: decl['item'] for decl in (variant.get('lists') or [])}
        return doc, version, digest, index, lists, draft_kind
    if draft_kind is not None:
        raise ContractError('表单 %s 不按 draft.kind 分派子契约' % form_id)
    return doc, version, digest, _scope_paths(doc['fields']), \
        {decl['id']: decl['item'] for decl in (doc.get('lists') or [])}, None


def _variant_lists(form_id, kind, forms_dir=None):
    doc = load_raw(form_id, forms_dir)[0]
    if kind is None:
        return doc.get('lists') or []
    return doc['variants'][kind].get('lists') or []


def _resolve_path(form_id, path, index, lists, draft_kind):
    """点路径解析：先按扁平 id 精确命中（result.valueField），再按段下行
    （组子字段；list 进入 item 行结构）。"""
    if path in index:
        return index[path]
    segments = path.split('.')
    for i in range(len(segments), 0, -1):
        head = '.'.join(segments[:i])
        if head not in index:
            continue
        node = index[head]
        for seg in segments[i:]:
            children = None
            if node.get('type') == 'group':
                children = node.get('fields')
            elif node.get('type') == 'list':
                item = lists.get(node.get('list'))
                if isinstance(item, dict):
                    children = item.get('fields')
            node = None
            for child in children or []:
                if isinstance(child, dict) and child.get('id') == seg:
                    node = child
                    break
            if node is None:
                break
        if node is not None:
            return node
        break
    raise ContractError('契约 %s（kind=%s）不存在字段：%s' % (form_id, draft_kind, path))


def load(form_id, draft_kind=None, forms_dir=None):
    """装载单（变体）契约为规范化 dict；运行时 fill 校验与生成器共用。

    返回：formId/schemaVersion/schemaDigest/title/space/fields/lists/codecs/
    refProviders/draftKind（仅 propertySource 变体非 None）。
    """
    doc, version, digest, _index, _lists, kind = _form_scope(form_id, draft_kind, forms_dir)
    if kind is None:
        fields = doc['fields']
        list_decls = doc.get('lists') or []
    else:
        fields = doc['variants'][kind]['fields']
        list_decls = doc['variants'][kind].get('lists') or []
    return {
        'formId': form_id,
        'schemaVersion': version,
        'schemaDigest': digest,
        'title': doc['title'],
        'space': doc['space'],
        'fields': copy.deepcopy(fields),
        'lists': copy.deepcopy(list_decls),
        'codecs': list(doc.get('codecs') or []),
        'refProviders': dict(doc.get('refProviders') or {}),
        'draftKind': kind,
    }


def schema_version(form_id, draft_kind=None, forms_dir=None):
    return load_raw(form_id, forms_dir)[1]


def schema_digest(form_id, draft_kind=None, forms_dir=None):
    return load_raw(form_id, forms_dir)[2]


def field_def(form_id, path, draft_kind=None, forms_dir=None):
    """按点路径查字段定义。扁平 id（result.valueField）与结构化嵌套路径
    （lookup.match.value.kind）都可解析；不存在抛 ContractError。"""
    if not isinstance(path, str) or not path:
        raise ContractError('字段路径必须是非空字符串')
    _, _, _, index, lists, kind = _form_scope(form_id, draft_kind, forms_dir)
    return copy.deepcopy(_resolve_path(form_id, path, index, lists, kind))


def atomic_groups(form_id, draft_kind=None, forms_dir=None):
    """原子组集合：{组名: [字段路径]}（收集声明 atomicGroup 的全部节点）。"""
    _, _, _, index, _lists, _kind = _form_scope(form_id, draft_kind, forms_dir)
    groups = {}
    for path, node in index.items():
        name = node.get('atomicGroup')
        if name:
            groups.setdefault(name, []).append(path)
    return groups


def list_def(form_id, list_id, draft_kind=None, forms_dir=None):
    """按 id 查 list 声明（含 item 行结构）；不存在抛 ContractError。"""
    for decl in _variant_lists(form_id, _form_scope(form_id, draft_kind, forms_dir)[5],
                               forms_dir):
        if decl.get('id') == list_id:
            return copy.deepcopy(decl)
    raise ContractError('契约 %s（kind=%s）未声明 list：%s'
                        % (form_id, draft_kind, list_id))


def list_defs(form_id, draft_kind=None, forms_dir=None):
    kind = _form_scope(form_id, draft_kind, forms_dir)[5]
    return copy.deepcopy(_variant_lists(form_id, kind, forms_dir))


def ref_provider(form_id, role, draft_kind=None, forms_dir=None):
    """引用角色 → 候选提供方名称；未登记抛 ContractError。"""
    providers = load_raw(form_id, forms_dir)[0].get('refProviders') or {}
    if role not in providers:
        raise ContractError('契约 %s 未登记引用角色：%s' % (form_id, role))
    return providers[role]


# ---- FormContract 适配接口（T2 assist_ops 冻结约定） -----------------------------
# workbench/assist_ops.py 模块头冻结的接口：field_def 返回规范化叶子定义（type 仅五种
# 叶类型、ref 解析为候选提供方名、ai 补默认值；组节点/列表 id 不可寻址 → KeyError）、
# list_def 归一为 fields 映射（单字段/多字段 item 两种写法）、行字段可按
# 「列表id.行字段id」寻址。本类是 workbench.assist_forms 对该约定的实现。


def _normalize_leaf(node, ref_providers):
    """规范化叶子字段定义（assist_ops FormContract 接口口径）。"""
    ai = node.get('ai') or {}
    out = {
        'type': node['type'],
        'nullable': bool(node.get('nullable')),
        'required': bool(node.get('required')),
        'maxLength': node.get('maxLength'),
        'ai': {
            'fillable': ai.get('fillable', True),
            'clearable': ai.get('clearable', False),
            'sensitive': ai.get('sensitive', False),
        },
    }
    if node['type'] == 'enum':
        out['enum'] = list(node['enum'])
    if node['type'] == 'ref':
        out['ref'] = ref_providers[node['refProvider']]
    return out


def _flatten_row_cells(item):
    """list item 归一为 {行字段路径: 节点}：单字段写法即自身；组行结构对嵌套组用点路径展平。"""
    if item.get('type') != 'group':
        return {item['id']: item}
    out = {}

    def walk(nodes, base):
        for f in nodes:
            path = (base + '.' + f['id']) if base else f['id']
            if f.get('type') == 'group' and f.get('fields'):
                walk(f['fields'], path)
            else:
                out[path] = f

    walk(item.get('fields') or [], '')
    return out


class FormContract:
    """按目标解析完毕的只读契约视图（T2 operations 校验消费；不执行业务校验）。"""

    def __init__(self, form_id, draft_kind=None, forms_dir=None):
        doc, version, digest = load_raw(form_id, forms_dir)
        if form_id == PROPERTY_SOURCE_FORM:
            if draft_kind is None:
                raise ContractError('表单 propertySource 按 draft.kind 分派子契约，必须提供 draft_kind')
            if draft_kind not in doc['variants']:
                raise ContractError('未知的属性取值来源 draft.kind：%s' % draft_kind)
            fields = doc['variants'][draft_kind]['fields']
            list_decls = doc['variants'][draft_kind].get('lists') or []
        else:
            if draft_kind is not None:
                raise ContractError('表单 %s 不按 draft.kind 分派子契约' % form_id)
            fields = doc['fields']
            list_decls = doc.get('lists') or []
        self.form_id = form_id
        self.draft_kind = draft_kind
        self.title = doc['title']
        self.space = doc['space']
        self._ref_providers = doc.get('refProviders') or {}
        self._index = _scope_paths(fields)
        self._lists = {decl['id']: decl for decl in list_decls}
        self._digest = digest
        self._version = version

    @property
    def schema_version(self):
        return self._version

    @property
    def digest(self):
        return self._digest

    def _leaf_node(self, path):
        """叶子寻址：表单级扁平/结构化路径，或「列表id.行字段路径」；组节点/列表本身不可寻址。"""
        node = None
        if path in self._index:
            node = self._index[path]
        else:
            head, _, rest = path.partition('.')
            if head in self._lists and rest:
                cells = _flatten_row_cells(self._lists[head]['item'])
                node = cells.get(rest)
            if node is None:
                try:
                    candidate = _resolve_path(self.form_id, path, self._index, self._lists,
                                              self.draft_kind)
                except ContractError:
                    candidate = None
                node = candidate
        if node is None or node.get('type') in ('group', 'list'):
            raise KeyError(path)
        return node

    def field_def(self, path, draft_kind=None):
        """规范化叶子字段定义；path 不在契约内（或指向组/列表）→ KeyError(path)。"""
        if not isinstance(path, str) or not path:
            raise KeyError(path)
        return _normalize_leaf(self._leaf_node(path), self._ref_providers)

    def atomic_groups(self):
        """原子组名 → 该组完整成员点路径列表（缺一即整组无效）。"""
        groups = {}
        for path, node in self._index.items():
            name = node.get('atomicGroup')
            if name:
                groups.setdefault(name, []).append(path)
        return groups

    def list_def(self, list_id):
        """{'id','rowIdScope','fields': {行字段路径: 规范化定义}}；未声明 → KeyError(list_id)。"""
        decl = self._lists.get(list_id)
        if decl is None:
            raise KeyError(list_id)
        fields = {path: _normalize_leaf(node, self._ref_providers)
                  for path, node in _flatten_row_cells(decl['item']).items()}
        return {'id': decl['id'], 'rowIdScope': decl['rowIdScope'], 'fields': fields}




# ---- 一致性检查（构建期/测试守护） ----------------------------------------------

def check_consistency(forms_dir=None):
    """装载全部契约并返回问题列表（空列表 = 全部通过）。逐项报告，不抛异常。"""
    problems = []
    for form_id in FORM_IDS:
        try:
            load_raw(form_id, forms_dir)
        except ContractError as exc:
            problems.append(str(exc))
            continue
        if form_id == PROPERTY_SOURCE_FORM:
            for kind in PROPERTY_SOURCE_KINDS:
                try:
                    load(form_id, kind, forms_dir)
                except ContractError as exc:
                    problems.append(str(exc))
    return problems

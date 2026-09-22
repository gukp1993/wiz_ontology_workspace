"""受限字段操作（operations）校验器——整表自动填写 autofill/1（表单辅助填写 T2，2026-09-22）。

协议冻结来源：`文档/接口文档/04-编排与LLM接口.md` §6（operations 语法、分层校验、
basis 规则、限额）、`文档/需求/20260922_整表自动填写交互/需求说明.md` §4.4/§6、
`文档/需求/20260922_整表自动填写交互/开发计划.md` §8.2（模块归属：T2）。

职责边界：
* validate_operations 把模型输出的 operations 数组按**表单契约白名单**逐条校验：
  - 结构级违规（operations 非数组/元素非对象/未知 op/op 携带未知键/条数超限）
    → 抛 assist_schema.ModelBadResponse（路由层转 502 MODEL_BAD_RESPONSE）；
  - 单操作违规（字段不在契约、值类型不符、clear 未授权、basis 无效、行标识非法等）
    → 一律不抛异常，进 invalid 列表，由上层组装 unresolved（04 §6.3 分层）。
* basis 程序核验防模型自报授权：kind=intent 的 quote 必须是调用方传入 intent 的子串
  （两侧做空白归一后比对）；kind=question 的 questionId 必须属于调用方传入的
  valid_question_ids 集合。核验失败该操作无效。
* 不核引用存在性（ref 候选核对在 service 层）、不命中草稿现有行（rowId 是否存在由
  service/前端核对，本层只查格式）、不执行 SQL、不访问网络、不写存储、不持锁。

═════════════════════════════════════════════════════════════════════════════════
FormContract 接口约定（冻结；T1 `workbench.assist_forms.FormContract` 按此实现，
本模块只依赖接口、不依赖其实现。`tests/fixtures/autofill_contract_fixture.json`
同步是本接口的兼容样例；T1 未合入时测试用按同一接口的最小 stub，
见 tests/test_autofill_patch.py 的 FixtureContract）：
═════════════════════════════════════════════════════════════════════════════════

* contract.field_def(path, draft_kind=None) -> dict
  path 是契约内叶子字段的点路径：顶层字段 id、组子字段「组id.子字段id」、列表行字段
  「列表id.行字段id」（组节点与列表 id 本身不可寻址，同样抛 KeyError）。
  返回规范化字段定义 dict，必含键：
    type: 'text'｜'textarea'｜'enum'｜'boolean'｜'ref'
    enum: list[str]        （仅 type=enum：可选值）
    ref: str               （仅 type=ref：候选 provider 名，存在性由 service 层核对）
    nullable: bool         （缺省 False；仅显式 true 时 clear 合法）
    required: bool         （缺省 False）
    maxLength: int｜None   （text/textarea 长度上限；None=不限制）
    ai: {'fillable': bool, 'clearable': bool, 'sensitive': bool}
                           （缺省 fillable=True、clearable=False、sensitive=False；
                             fillable=False 或 sensitive=True 的字段模型不可写）
  path 不在契约内 → 抛 KeyError(path)。
  draft_kind：propertySource 等「按 draft.kind 分派子契约」的场景由调用方传入；
  本模块原样透传、不解释（传给本模块的 contract 通常已按目标解析完毕）。
* contract.atomic_groups() -> dict[str, list[str]]
  原子组名 → 该组**完整**成员点路径列表（组内字段须同时出现，缺一即整组无效）。
* contract.list_def(list_id) -> dict
  返回 {'id': str, 'rowIdScope': str, 'fields': {行字段id: 字段定义}}；
  契约文件 lists[].item 的单字段/多字段写法由 loader 归一为 fields 映射。
  list_id 不在契约内 → 抛 KeyError(list_id)。
* contract.schema_version -> int
* contract.digest -> str
  schemaVersion 与 schemaDigest（canonical JSON SHA-256，loader 重算；本模块不消费，
  供 service/前端指纹核对）。

实现口径说明（两处对冻结文档的解释性落位，如验收结论不同以文档为准回改）：
* 「同字段多条写操作 → 整组无效」按**叶子写操作（set/clear）**判定；row.append 允许
  同列表多条（多条 append 是行协议的基本用法，§6.3 规定 localId 客户端生成、服务端
  按内容去重）。行级冲突按**行标识**判定：同一 localId 的多条 row.append、同一 rowId
  的多条 row.update/row.remove（含 update+remove 混合）视为冲突，涉及操作整组无效。
* set 的 value 不接受 null：置空必须走独立 clear 操作（需求 §4.4「空字符串不自动等于
  删除」、清空须明确授权；set null 不得绕过 clear 的 nullable+clearable+basis 三重约束）。
"""
import re

from workbench.assist_schema import ModelBadResponse

MAX_OPERATIONS = 12  # 一次响应 operations 上限（04 §6.5 冻结）；超出 → 结构违规 502

_ROW_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}\Z')  # 客户端本地行标识（localId/rowId 同格式）

_OP_KEYS = {
    'set': frozenset(('op', 'field', 'value', 'basis')),
    'clear': frozenset(('op', 'field', 'basis')),
    'row.append': frozenset(('op', 'field', 'row')),
    'row.update': frozenset(('op', 'field', 'rowId', 'fields')),
    'row.remove': frozenset(('op', 'field', 'rowId', 'basis')),
}
_SCALAR_OPS = ('set', 'clear')
_NEEDS_BASIS = ('set', 'clear', 'row.remove')
_BASIS_KEYS = {'intent': frozenset(('kind', 'quote')),
               'question': frozenset(('kind', 'questionId'))}
_LEAF_TYPES = ('text', 'textarea', 'enum', 'boolean', 'ref')


def _preview(value):
    """异常信息里的模型值预览（截断，防超长内容进日志/响应）。"""
    text = value if isinstance(value, str) else str(value)
    return text[:40]


def _list_def_or_none(contract, list_id):
    try:
        return contract.list_def(list_id)
    except KeyError:
        return None


def _quote_in_text(text, quote):
    """引文子串核验：两侧把连续空白折叠为单个空格后做子串匹配（容忍换行/多空格）。"""
    return ' '.join(quote.split()) in ' '.join(text.split())


def _check_basis(basis, intent, valid_question_ids):
    """basis 核验（set/clear/row.remove 必带）。返回 None=通过，否则失败原因。"""
    if not isinstance(basis, dict):
        return '缺少有效 basis 依据（须引用用户意图引文或本会话已答问题）'
    kind = basis.get('kind')
    allowed = _BASIS_KEYS.get(kind)
    if allowed is None:
        return 'basis.kind 无效（仅支持 intent/question）'
    extra = set(str(k) for k in basis) - allowed
    if extra:
        return 'basis 携带未知键「%s」' % '、'.join(sorted(extra)[:3])
    if kind == 'intent':
        quote = basis.get('quote')
        if not isinstance(quote, str) or not quote.strip():
            return 'basis.kind=intent 缺少有效 quote 引文'
        if not _quote_in_text(intent, quote):
            return 'basis 引文未出现在本次用户意图中（不能自报授权）'
        return None
    question_id = basis.get('questionId')
    if not isinstance(question_id, str) or not question_id.strip():
        return 'basis.kind=question 缺少有效 questionId'
    if question_id not in valid_question_ids:
        return 'basis questionId「%s」不属于本会话已签发问题' % question_id
    return None


def _check_scalar_value(spec, value, label):
    """叶子字段写入值按类型校验（set 的 value 与列表行字段值共用）。返回 None=通过。"""
    field_type = spec.get('type')
    if value is None:
        return '字段「%s」的值为 null：置空必须使用 clear 操作' % label
    if field_type in ('text', 'textarea'):
        if not isinstance(value, str):
            return '字段「%s」必须填文本' % label
        max_length = spec.get('maxLength')
        if isinstance(max_length, int) and max_length > 0 and len(value) > max_length:
            return '字段「%s」超过长度上限（%d 字符）' % (label, max_length)
        return None
    if field_type == 'enum':
        allowed = spec.get('enum')
        if not isinstance(value, str) or not isinstance(allowed, (list, tuple)) or value not in allowed:
            return '字段「%s」的值不在可选枚举内' % label
        return None
    if field_type == 'boolean':
        if not isinstance(value, bool):
            return '字段「%s」必须是布尔值' % label
        return None
    if field_type == 'ref':
        if not isinstance(value, str) or not value.strip():
            return '字段「%s」必须引用非空候选 id' % label
        return None
    return '字段「%s」类型不在可写范围' % label


def _check_row_fields(row_fields, ldef, list_id):
    """行字段字典校验：键必须属于该列表 item 的合法字段、值按叶子类型校验。
    返回 (normalized_fields, reason)。"""
    item_fields = ldef.get('fields') if isinstance(ldef.get('fields'), dict) else {}
    if not isinstance(row_fields, dict) or not row_fields:
        return None, '缺少要写入的行字段（fields 必须是非空对象）'
    normalized = {}
    for key, value in row_fields.items():
        key_text = key if isinstance(key, str) else str(key)
        spec = item_fields.get(key_text)
        if spec is None:
            return None, '行字段「%s」不在列表「%s」的 item 字段范围' % (key_text, list_id)
        reason = _check_scalar_value(spec, value, '列表「%s」行字段「%s」' % (list_id, key_text))
        if reason:
            return None, reason
        normalized[key_text] = value
    return normalized, None


def _validate_one(item, op, contract, draft_kind):
    """单操作校验（不含 basis）。返回 (field, record, reason)：reason=None 表示自身合法。"""
    raw_field = item.get('field')
    field = raw_field.strip() if isinstance(raw_field, str) else ''
    if not field:
        return '', None, '缺少有效 field 路径'
    first_segment = field.split('.', 1)[0]
    ldef = _list_def_or_none(contract, first_segment)
    if op in ('set', 'clear'):
        if ldef is not None:
            return field, None, ('列表「%s」的行内容只能通过 row.append/row.update/row.remove 修改'
                                 % first_segment)
        try:
            spec = contract.field_def(field, draft_kind)
        except KeyError:
            return field, None, '字段「%s」不在表单契约内' % field
        if spec.get('type') not in _LEAF_TYPES:
            return field, None, '字段「%s」不是可整体写入的叶子字段' % field
        ai = spec.get('ai') if isinstance(spec.get('ai'), dict) else {}
        if ai.get('fillable') is False or ai.get('sensitive') is True:
            return field, None, '字段「%s」不可由 AI 写入（契约 ai 权限）' % field
        if op == 'set':
            reason = _check_scalar_value(spec, item.get('value'), field)
            if reason:
                return field, None, reason
            return field, {'op': 'set', 'field': field, 'value': item.get('value')}, None
        if spec.get('nullable') is not True:
            return field, None, '字段「%s」不可空（契约 nullable 未显式开启），不能 clear' % field
        if ai.get('clearable') is not True:
            return field, None, '字段「%s」未开启 ai.clearable，不能 clear' % field
        return field, {'op': 'clear', 'field': field}, None
    # 行操作：field 必须恰为契约内列表 id
    if ldef is None:
        return field, None, '字段「%s」不是契约内列表' % field
    if field != first_segment:
        return field, None, '行操作的 field 必须是列表「%s」本身，不能指向行内字段' % first_segment
    if op == 'row.append':
        row = item.get('row')
        if not isinstance(row, dict):
            return field, None, 'row.append 缺少有效 row 对象'
        local_id = row.get('localId')
        if not isinstance(local_id, str) or not _ROW_ID_RE.match(local_id):
            return field, None, 'row.localId 格式非法（限 1–64 位字母/数字/下划线/连字符）'
        fields, reason = _check_row_fields(row.get('fields'), ldef, field)
        if reason:
            return field, None, reason
        return field, {'op': 'row.append', 'field': field,
                       'row': {'localId': local_id, 'fields': fields}}, None
    row_id = item.get('rowId')
    if not isinstance(row_id, str) or not _ROW_ID_RE.match(row_id):
        return field, None, '%s 的 rowId 格式非法（限 1–64 位字母/数字/下划线/连字符）' % op
    if op == 'row.remove':
        return field, {'op': 'row.remove', 'field': field, 'rowId': row_id}, None
    fields, reason = _check_row_fields(item.get('fields'), ldef, field)
    if reason:
        return field, None, reason
    return field, {'op': 'row.update', 'field': field, 'rowId': row_id, 'fields': fields}, None


def _row_identity(item, op, field):
    """行操作的冲突键：append 用 localId，update/remove 用 rowId；非行操作返回 None。"""
    if op == 'row.append':
        row = item.get('row')
        local_id = row.get('localId') if isinstance(row, dict) else None
        return ('local', field, local_id if isinstance(local_id, str) else '')
    if op in ('row.update', 'row.remove'):
        row_id = item.get('rowId')
        return ('id', field, row_id if isinstance(row_id, str) else '')
    return None


def _reject_conflicts(entries):
    """同字段多条写操作 / 同一行标识多处操作 → 整组无效（不后项覆盖，04 §6.3）。"""
    scalar_counts = {}
    for entry in entries:
        if entry['op'] in ('set', 'clear'):
            scalar_counts[entry['field']] = scalar_counts.get(entry['field'], 0) + 1
    row_groups = {}
    for entry in entries:
        if entry['row_key'] is not None:
            row_groups.setdefault(entry['row_key'], []).append(entry)
    for entry in entries:
        if not entry['valid']:
            continue
        if entry['op'] in ('set', 'clear') and scalar_counts.get(entry['field'], 0) > 1:
            entry['valid'] = False
            entry['reason'] = '同字段存在多条写操作，整组拒绝（不后项覆盖）'
        elif entry['row_key'] is not None and len(row_groups[entry['row_key']]) > 1:
            entry['valid'] = False
            entry['reason'] = '同一行存在多条行操作，整组拒绝（不后项覆盖）'


def _reject_incomplete_atomic_groups(entries, contract):
    """原子组成员不完整出现 → 组内已通过的操作整组无效（成员各自违规的保持原原因）。"""
    groups = contract.atomic_groups() or {}
    if not isinstance(groups, dict):
        return
    for name, members in groups.items():
        member_set = {str(m) for m in (members or ())}
        if not member_set:
            continue
        covered = {entry['field'] for entry in entries
                   if entry['valid'] and entry['op'] in ('set', 'clear')
                   and entry['field'] in member_set}
        if covered and covered != member_set:
            reason = '原子组「%s」不完整（需同时给出：%s）' % (name, '、'.join(sorted(member_set)))
            for entry in entries:
                if entry['valid'] and entry['field'] in member_set:
                    entry['valid'] = False
                    entry['reason'] = reason


def validate_operations(raw, contract, intent, valid_question_ids, draft_kind=None):
    """校验模型输出 operations（autofill/1，04 §6.3）。

    返回 {'valid': [规范化操作…], 'invalid': [{'op','field','reason'}…]}；
    结构级违规抛 assist_schema.ModelBadResponse（502 分层），单操作违规不抛异常。

    raw：模型输出的 operations 数组（不可信数据）；contract：FormContract（接口约定见
    模块 docstring）；intent：本次发给模型的用户意图原文（quote 子串核验依据）；
    valid_question_ids：本会话仍可引用的 questionId 集合（question 依据核验依据）；
    draft_kind：子契约分派参数，透传 contract.field_def。
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ModelBadResponse('模型输出的 operations 必须是数组')
    if len(raw) > MAX_OPERATIONS:
        raise ModelBadResponse('模型输出的 operations 超过上限（%d 条）' % MAX_OPERATIONS)
    intent_text = intent if isinstance(intent, str) else ''
    valid_qids = {str(q) for q in valid_question_ids} if valid_question_ids else set()

    entries = []
    for item in raw:
        if not isinstance(item, dict):
            raise ModelBadResponse('模型输出的 operations 元素必须是对象')
        op = item.get('op')
        if op not in _OP_KEYS:
            raise ModelBadResponse('模型输出包含未知操作类型：%s' % _preview(op))
        extra = set(str(k) for k in item) - _OP_KEYS[op]
        if extra:
            raise ModelBadResponse('操作 %s 携带未知键：%s' % (op, '、'.join(sorted(extra)[:5])))
        field, record, reason = _validate_one(item, op, contract, draft_kind)
        if reason is None and op in _NEEDS_BASIS:
            if 'basis' not in item:
                reason = '缺少 basis 依据（须引用用户意图引文或本会话已答问题）'
            else:
                reason = _check_basis(item.get('basis'), intent_text, valid_qids)
                if reason is None:
                    record['basis'] = item['basis']
        entries.append({'op': op, 'field': field, 'record': record,
                        'valid': reason is None, 'reason': reason or '',
                        'row_key': _row_identity(item, op, field)})
    _reject_conflicts(entries)
    _reject_incomplete_atomic_groups(entries, contract)
    valid, invalid = [], []
    for entry in entries:
        if entry['valid']:
            valid.append(entry['record'])
        else:
            invalid.append({'op': entry['op'], 'field': entry['field'], 'reason': entry['reason']})
    return {'valid': valid, 'invalid': invalid}

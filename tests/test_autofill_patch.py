"""assist_ops / parse_fill_output（整表自动填写 autofill/1 T2：受限操作校验）测试。

纯 python3 直跑（无 pytest），全部用合成契约与合成模型输出，不连真实 LLM、不启动服务、
不读写真实 ontology/。运行：python3 tests/test_autofill_patch.py
（纯逻辑测试无需 WIZ_WORKBENCH_ROOT，防御性设置临时目录与其他测试保持一致。）

T1 loader（workbench.assist_forms）合入前，本文件用按冻结接口实现的 FixtureContract
stub（接口约定见 workbench/assist_ops.py 模块 docstring；
tests/fixtures/autofill_contract_fixture.json 同步是该接口的兼容样例）。
T9 集成时把 FixtureContract 替换为 assist_forms 装载的真实契约即可，用例不变。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.environ.get('WIZ_WORKBENCH_ROOT'):
    os.environ['WIZ_WORKBENCH_ROOT'] = tempfile.mkdtemp(prefix='wiz_autofill_patch_')

from workbench import assist_ops
from workbench import assist_schema

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'fixtures', 'autofill_contract_fixture.json')

FAILURES = []


def run(name, fn):
    try:
        fn()
        print(f'通过  {name}')
    except Exception as exc:
        FAILURES.append(name)
        print(f'失败  {name}: {type(exc).__name__}: {exc}')


def expect_bad(fn, contains=None):
    """断言抛出 ModelBadResponse。"""
    try:
        fn()
    except assist_schema.ModelBadResponse as exc:
        if contains:
            assert contains in str(exc), f'异常信息不含「{contains}」：{exc}'
        return
    raise AssertionError('未抛出 ModelBadResponse')


# --- FormContract stub（接口冻结在 workbench/assist_ops.py 模块 docstring；T1 合入后替换） --

class FixtureContract:
    """按冻结接口装载 fixture 契约的最小 loader stub。"""

    def __init__(self, path):
        with open(path, 'r', encoding='utf-8') as fh:
            raw = json.load(fh)
        self.form_id = raw['formId']
        self.schema_version = raw['schemaVersion']
        self.digest = 'fixture-stub-digest'  # loader 应重算 canonical SHA-256；校验器不消费内容
        self._fields = {}
        self._lists = {}
        self._groups = {}
        self._walk_fields(raw.get('fields') or [], '')
        self._collect_groups(raw.get('fields') or [], '')
        for lst in raw.get('lists') or []:
            item = lst.get('item') or {}
            entries = item.get('fields') if isinstance(item.get('fields'), list) else [item]
            row_fields = {}
            for field in entries:
                row_fields[field['id']] = self._norm(field)
            self._lists[lst['id']] = {'id': lst['id'],
                                      'rowIdScope': lst.get('rowIdScope') or 'local',
                                      'fields': row_fields}
            for field_id, spec in row_fields.items():  # 行字段以「列表id.字段id」可寻址
                self._fields['%s.%s' % (lst['id'], field_id)] = spec

    @staticmethod
    def _norm(field):
        ai = field.get('ai') if isinstance(field.get('ai'), dict) else {}
        spec = {'type': field.get('type'),
                'nullable': field.get('nullable') is True,
                'required': field.get('required') is True,
                'maxLength': field.get('maxLength'),
                'ai': {'fillable': ai.get('fillable') is not False,
                       'clearable': ai.get('clearable') is True,
                       'sensitive': ai.get('sensitive') is True}}
        if isinstance(field.get('enum'), list):
            spec['enum'] = list(field['enum'])
        if isinstance(field.get('ref'), str):
            spec['ref'] = field['ref']
        return spec

    def _walk_fields(self, fields, prefix):
        for field in fields:
            path = prefix + field['id']
            if field.get('type') == 'group':  # 组节点不可经 field_def 寻址（接口冻结），只展开子字段
                self._walk_fields(field.get('fields') or [], path + '.')
                continue
            self._fields[path] = self._norm(field)

    def _collect_groups(self, fields, prefix):
        for field in fields:
            path = prefix + field['id']
            group = field.get('atomicGroup')
            if isinstance(group, str) and group:
                if field.get('type') == 'group':  # 组节点携带 atomicGroup：成员是全部子字段
                    for child in field.get('fields') or []:
                        self._groups.setdefault(group, []).append(path + '.' + child['id'])
                else:
                    self._groups.setdefault(group, []).append(path)

    def field_def(self, path, draft_kind=None):
        if path not in self._fields:
            raise KeyError(path)
        return dict(self._fields[path])

    def atomic_groups(self):
        return {name: list(members) for name, members in self._groups.items()}

    def list_def(self, list_id):
        if list_id not in self._lists:
            raise KeyError(list_id)
        info = self._lists[list_id]
        return {'id': info['id'], 'rowIdScope': info['rowIdScope'],
                'fields': {key: dict(value) for key, value in info['fields'].items()}}


CONTRACT = FixtureContract(FIXTURE_PATH)

# --- 公共夹具 ----------------------------------------------------------------------

INTENT = '帮我把名称改为创智园储能电站，并把备注清空，单位用 MWh，然后启用，匹配左侧用 id 字段'
VALID_QIDS = ['q_1', 'q_7']


def validate(items, intent=INTENT, valid_qids=None):
    return assist_ops.validate_operations(items, CONTRACT, intent,
                                          VALID_QIDS if valid_qids is None else valid_qids)


def parse_fill(payload, intent=INTENT, valid_qids=None):
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return assist_schema.parse_fill_output(content, intent,
                                           VALID_QIDS if valid_qids is None else valid_qids,
                                           CONTRACT)


def b_intent(quote):
    return {'kind': 'intent', 'quote': quote}


def op_set(field, value, quote='名称改为创智园储能电站', basis=None):
    return {'op': 'set', 'field': field, 'value': value,
            'basis': b_intent(quote) if basis is None else basis}


def op_append(list_id, local_id, fields=None):
    return {'op': 'row.append', 'field': list_id,
            'row': {'localId': local_id, 'fields': {'left': 'id'} if fields is None else fields}}


# --- a) stub 接口一致性与合法操作 ---------------------------------------------------

def t_fixture_contract_interface():
    assert isinstance(CONTRACT.schema_version, int) and CONTRACT.schema_version >= 1
    assert isinstance(CONTRACT.digest, str) and CONTRACT.digest
    spec = CONTRACT.field_def('dataType.type')
    assert spec['type'] == 'enum' and spec['enum'] == ['string', 'double', 'timeSeries']
    spec = CONTRACT.field_def('note')
    assert spec['nullable'] is True and spec['ai']['clearable'] is True
    spec = CONTRACT.field_def('lookupMatch.left')
    assert spec['type'] == 'text' and spec['maxLength'] == 64
    assert CONTRACT.atomic_groups() == {'typeCore': ['dataType.type', 'dataType.valueType']}
    ldef = CONTRACT.list_def('lookupMatch')
    assert ldef['rowIdScope'] == 'local' and ldef['fields']['left']['type'] == 'text'
    for fn, arg in ((CONTRACT.field_def, 'nope'), (CONTRACT.field_def, 'dataType'),
                    (CONTRACT.list_def, 'nope')):
        try:
            fn(arg)
        except KeyError:
            continue
        raise AssertionError('未知寻址未抛 KeyError：%s' % arg)


def t_valid_ops_passthrough():
    ops = [
        op_set('name', '创智园储能电站'),
        op_set('unit', 'MWh', quote='单位用 MWh'),
        op_set('enabled', True, quote='然后启用'),
        op_set('connection', 'conn-01', quote='然后启用'),  # ref：此层只查非空字符串
        op_set('summary', '长摘要', quote='名称改为创智园储能电站'),
        {'op': 'clear', 'field': 'note', 'basis': b_intent('把备注清空')},
        op_append('lookupMatch', 'r1'),
        {'op': 'row.update', 'field': 'tags', 'rowId': 't_1', 'fields': {'value': 'peak'}},
        {'op': 'row.remove', 'field': 'tags', 'rowId': 't_2', 'basis': b_intent('把备注清空')},
    ]
    result = validate(ops)
    assert result['invalid'] == [], f'合法操作不应产生 invalid：{result["invalid"]}'
    assert len(result['valid']) == 9
    by_op = {}
    for record in result['valid']:
        by_op.setdefault(record['op'], []).append(record)
    assert by_op['set'][0] == {'op': 'set', 'field': 'name', 'value': '创智园储能电站',
                               'basis': {'kind': 'intent', 'quote': '名称改为创智园储能电站'}}
    append = by_op['row.append'][0]
    assert append['row'] == {'localId': 'r1', 'fields': {'left': 'id'}}
    assert set(append) == {'op', 'field', 'row'}
    assert by_op['row.update'][0] == {'op': 'row.update', 'field': 'tags', 'rowId': 't_1',
                                      'fields': {'value': 'peak'}}
    assert by_op['clear'][0] == {'op': 'clear', 'field': 'note',
                                 'basis': {'kind': 'intent', 'quote': '把备注清空'}}
    assert by_op['row.remove'][0]['rowId'] == 't_2'


def t_multiple_row_append_same_list():
    """同列表多条 row.append 合法（行协议基本用法；服务端按内容去重是 service 层职责）。"""
    ops = [op_append('lookupMatch', 'r%d' % i) for i in range(3)]
    result = validate(ops)
    assert result['invalid'] == [] and len(result['valid']) == 3


# --- b) 冲突与原子组 ---------------------------------------------------------------

def t_same_field_two_sets_conflict():
    result = validate([op_set('name', 'A'), op_set('name', 'B')])
    assert result['valid'] == [] and len(result['invalid']) == 2
    assert all('同字段' in item['reason'] for item in result['invalid'])
    assert all(item['op'] == 'set' and item['field'] == 'name' for item in result['invalid'])


def t_set_plus_clear_conflict():
    ops = [op_set('note', 'x'),
           {'op': 'clear', 'field': 'note', 'basis': b_intent('把备注清空')}]
    result = validate(ops)
    assert result['valid'] == [] and len(result['invalid']) == 2
    assert all('同字段' in item['reason'] for item in result['invalid'])


def t_conflict_does_not_leak_to_other_fields():
    ops = [op_set('name', 'A'), op_set('name', 'B'), op_set('unit', 'MWh', quote='单位用 MWh')]
    result = validate(ops)
    assert [record['field'] for record in result['valid']] == ['unit']
    assert len(result['invalid']) == 2


def t_duplicate_localid_conflict():
    ops = [op_append('lookupMatch', 'r1'), op_append('lookupMatch', 'r1')]
    result = validate(ops)
    assert result['valid'] == [] and len(result['invalid']) == 2
    assert all('同一行' in item['reason'] for item in result['invalid'])


def t_rowid_update_remove_conflict():
    ops = [{'op': 'row.update', 'field': 'tags', 'rowId': 't_9', 'fields': {'value': 'a'}},
           {'op': 'row.remove', 'field': 'tags', 'rowId': 't_9', 'basis': b_intent('把备注清空')}]
    result = validate(ops)
    assert result['valid'] == [] and len(result['invalid']) == 2


def t_atomic_group_incomplete():
    result = validate([op_set('dataType.type', 'timeSeries', quote='单位用 MWh')])
    assert result['valid'] == [] and len(result['invalid']) == 1
    reason = result['invalid'][0]['reason']
    assert '原子组' in reason and 'typeCore' in reason and 'valueType' in reason


def t_atomic_group_complete():
    ops = [op_set('dataType.type', 'timeSeries', quote='单位用 MWh'),
           op_set('dataType.valueType', 'double', quote='单位用 MWh')]
    result = validate(ops)
    assert result['invalid'] == [] and len(result['valid']) == 2


def t_atomic_group_member_invalid_still_group_invalid():
    """一个成员自身违规（枚举越界）时，组内其余合法成员同样整组无效。"""
    ops = [op_set('dataType.type', 'bogus', quote='单位用 MWh'),
           op_set('dataType.valueType', 'double', quote='单位用 MWh')]
    result = validate(ops)
    assert result['valid'] == [] and len(result['invalid']) == 2
    reasons = {item['field']: item['reason'] for item in result['invalid']}
    assert '枚举' in reasons['dataType.type'] and '原子组' in reasons['dataType.valueType']


# --- c) clear 授权与 basis 核验 ------------------------------------------------------

def t_clear_non_nullable_rejected():
    result = validate([{'op': 'clear', 'field': 'name', 'basis': b_intent('把备注清空')}])
    assert result['valid'] == []
    assert 'nullable' in result['invalid'][0]['reason']


def t_clear_not_clearable_rejected():
    """nullable 但未显式开启 ai.clearable → clear 拒。"""
    result = validate([{'op': 'clear', 'field': 'owner', 'basis': b_intent('把备注清空')}])
    assert result['valid'] == []
    assert 'clearable' in result['invalid'][0]['reason']


def t_basis_quote_not_in_intent():
    result = validate([op_set('name', 'A', quote='清空整个数据库')],
                      intent='帮我把名称改为创智园储能电站')
    assert result['valid'] == []
    assert '不能自报授权' in result['invalid'][0]['reason']


def t_basis_quote_empty_intent():
    result = validate([op_set('name', 'A')], intent='')
    assert result['valid'] == [] and len(result['invalid']) == 1


def t_basis_question_not_in_valid_set():
    ops = [{'op': 'clear', 'field': 'note', 'basis': {'kind': 'question', 'questionId': 'q_99'}}]
    result = validate(ops)
    assert result['valid'] == []
    assert 'questionId' in result['invalid'][0]['reason']


def t_basis_question_in_valid_set_ok():
    ops = [{'op': 'clear', 'field': 'note', 'basis': {'kind': 'question', 'questionId': 'q_7'}}]
    result = validate(ops)
    assert result['invalid'] == [] and len(result['valid']) == 1


def t_missing_basis_rejected_only_where_required():
    ops = [{'op': 'set', 'field': 'name', 'value': 'A'}, op_append('lookupMatch', 'r1')]
    result = validate(ops)
    assert len(result['valid']) == 1 and result['valid'][0]['op'] == 'row.append'
    assert len(result['invalid']) == 1 and 'basis' in result['invalid'][0]['reason']


def t_basis_malformed_rejected():
    cases = [
        {'kind': 'user', 'quote': 'x'},                              # 未知 kind
        {'kind': 'intent'},                                          # 缺 quote
        {'kind': 'intent', 'quote': '   '},                          # quote 全空白
        {'kind': 'intent', 'quote': '单位用 MWh', 'note': 'extra'},   # 多余键
        {'kind': 'question'},                                        # 缺 questionId
        '单位用 MWh',                                                # 非 dict
    ]
    for basis in cases:
        result = validate([op_set('unit', 'MWh', basis=basis)])
        assert result['valid'] == [], f'basis={basis!r} 应判无效：{result}'
        assert len(result['invalid']) == 1, f'basis={basis!r} 应判无效：{result}'


# --- d) 字段路径与值类型 -------------------------------------------------------------

def t_unknown_field_rejected():
    result = validate([op_set('ghost', 'x')])
    assert result['valid'] == []
    assert '不在表单契约内' in result['invalid'][0]['reason']


def t_arbitrary_json_path_rejected():
    for path in ('a.b.c', 'name.extra.deep', 'metadata.tags'):
        result = validate([op_set(path, 'x')])
        assert result['valid'] == [], path
        assert '不在表单契约内' in result['invalid'][0]['reason'], path


def t_set_on_list_paths_rejected():
    for path in ('lookupMatch', 'lookupMatch.left'):
        result = validate([op_set(path, 'x')])
        assert result['valid'] == [], path
        assert 'row.append' in result['invalid'][0]['reason'], path


def t_rowop_non_list_rejected():
    ops = [{'op': 'row.append', 'field': 'name', 'row': {'localId': 'r1', 'fields': {'left': 'x'}}},
           {'op': 'row.update', 'field': 'lookupMatch.left', 'rowId': 't_1', 'fields': {'left': 'x'}}]
    result = validate(ops)
    assert result['valid'] == [] and len(result['invalid']) == 2
    assert '不是契约内列表' in result['invalid'][0]['reason']
    assert '列表「lookupMatch」本身' in result['invalid'][1]['reason']


def t_set_value_type_rules():
    cases = [
        (op_set('unit', 'MW'), '枚举'),          # enum 越界
        (op_set('unit', 1), '枚举'),
        (op_set('enabled', 'yes'), '布尔'),      # boolean
        (op_set('enabled', 1), '布尔'),
        (op_set('name', 'x' * 121), '长度上限'),  # maxLength 120
        (op_set('summary', 123), '文本'),        # textarea 非文本
        (op_set('connection', ''), '非空'),      # ref 空串
        (op_set('connection', '   '), '非空'),
        (op_set('connection', 42), '非空'),
        (op_set('note', None), 'clear'),         # null → 必须走 clear
    ]
    for op, keyword in cases:
        result = validate([op])
        assert len(result['invalid']) == 1, f'{op} 应判无效：{result}'
        assert keyword in result['invalid'][0]['reason'], \
            f'{op} 原因不含「{keyword}」：{result["invalid"]}'


def t_set_non_fillable_and_sensitive_rejected():
    ops = [op_set('derivedCode', 'AUTO-1'), op_set('secretToken', 'x')]
    result = validate(ops)
    assert result['valid'] == [] and len(result['invalid']) == 2
    assert all('不可由 AI 写入' in item['reason'] for item in result['invalid'])


def t_row_localid_bad_format():
    for bad in ('', '有中文', 'bad id', 'x' * 65, 'a$b'):
        result = validate([op_append('lookupMatch', bad)])
        assert result['valid'] == [], repr(bad)
        assert 'localId' in result['invalid'][0]['reason'], repr(bad)


def t_row_update_remove_shapes():
    ops = [
        {'op': 'row.update', 'field': 'tags', 'rowId': 'bad row', 'fields': {'value': 'x'}},
        {'op': 'row.update', 'field': 'tags', 'rowId': 't_1', 'fields': {}},
        {'op': 'row.update', 'field': 'tags', 'rowId': 't_1', 'fields': {'ghost': 'x'}},
        {'op': 'row.update', 'field': 'tags', 'rowId': 't_1', 'fields': {'value': 17}},
        {'op': 'row.remove', 'field': 'tags', 'rowId': '', 'basis': b_intent('把备注清空')},
        op_append('tags', 'ok1', fields=None),
    ]
    result = validate(ops)
    assert result['valid'] == [] and len(result['invalid']) == 6
    assert 'rowId' in result['invalid'][0]['reason']
    assert '行字段' in result['invalid'][1]['reason']
    assert '行字段' in result['invalid'][2]['reason']
    assert '文本' in result['invalid'][3]['reason']
    assert 'rowId' in result['invalid'][4]['reason']
    assert '行字段' in result['invalid'][5]['reason']


# --- e) 结构级违规（502 分层） -------------------------------------------------------

def t_operations_over_limit():
    ops = [op_append('lookupMatch', 'r%d' % i) for i in range(13)]
    expect_bad(lambda: validate(ops), contains='上限')


def t_operations_twelve_ok():
    ops = [op_append('lookupMatch', 'r%d' % i) for i in range(12)]
    result = validate(ops)
    assert len(result['valid']) == 12 and result['invalid'] == []


def t_operations_non_array_and_bad_element():
    expect_bad(lambda: validate({'op': 'set'}), contains='数组')
    expect_bad(lambda: validate(['set name=A']), contains='对象')
    expect_bad(lambda: validate([None]), contains='对象')


def t_unknown_op_structural():
    expect_bad(lambda: validate([{'op': 'replace', 'field': 'name', 'value': 'x'}]),
               contains='未知操作')
    expect_bad(lambda: validate([{'field': 'name', 'value': 'x'}]), contains='未知操作')


def t_op_extra_key_structural():
    expect_bad(lambda: validate([dict(op_set('name', 'A'), row={'localId': 'r1'})]),
               contains='未知键')
    expect_bad(lambda: validate([{'op': 'clear', 'field': 'note',
                                  'basis': b_intent('把备注清空'), 'value': None}]),
               contains='未知键')


# --- f) parse_fill_output（结构、问题换发、unresolved 过滤、限额） --------------------

def t_top_level_unknown_key():
    expect_bad(lambda: parse_fill({'operations': [], 'summary': '多余'}), contains='未知顶层键')


def t_questions_over_limit():
    questions = [{'text': 'q%d？' % i, 'fields': ['unit']} for i in range(4)]
    expect_bad(lambda: parse_fill({'questions': questions}), contains='上限')


def t_model_unresolved_over_limit():
    unresolved = [{'field': 'unit', 'reason': 'r%d' % i} for i in range(13)]
    expect_bad(lambda: parse_fill({'unresolved': unresolved}), contains='上限')


def t_questions_reissued_ids():
    payload = {'questions': [
        {'id': 'model_a', 'text': '取值字段用哪个？', 'fields': ['unit'],
         'options': ['kW', '', 5, 'MWh'], 'allowUnsure': False},
        {'id': 'q_1', 'text': '忽略模型自报的已有 id', 'fields': ['connection']},
    ]}
    result = parse_fill(payload, valid_qids=['q_1', 'q_2'])
    assert [q['id'] for q in result['questions']] == ['q_3', 'q_4'], \
        f'新 id 应避让已占用编号：{result["questions"]}'
    first = result['questions'][0]
    assert first == {'id': 'q_3', 'text': '取值字段用哪个？', 'fields': ['unit'],
                     'options': ['kW', 'MWh'], 'allowUnsure': False}
    second = result['questions'][1]
    assert second['options'] is None and second['allowUnsure'] is True


def t_question_unknown_field_dropped():
    # 原始问题 >3 条会先触发 502 上限（f2 覆盖），此处保持 3 条以内验证逐条丢弃
    payload = {'questions': [
        {'text': '坏问题', 'fields': ['ghost.path']},
        {'text': '缺 fields'},
        {'text': '好问题', 'fields': ['dataType.valueType', 'dataType.type', 'dataType.valueType']},
    ]}
    result = parse_fill(payload)
    assert len(result['questions']) == 1
    assert result['questions'][0]['fields'] == ['dataType.valueType', 'dataType.type']


def t_model_unresolved_filtered():
    payload = {'unresolved': [
        {'field': 'connection', 'reason': '候选目录中没有该表；请刷新目录或人工选择'},
        {'field': 'ghost', 'reason': 'x'},            # 契约外字段 → 丢弃
        {'field': 'unit', 'reason': ''},              # 空 reason → 丢弃
        {'field': 'unit', 'reason': 'r', 'extra': 1},  # 多余键 → 丢弃
        'not-a-dict',                                 # 非对象 → 丢弃
    ]}
    result = parse_fill(payload)
    assert result['unresolved'] == [{'field': 'connection',
                                     'reason': '候选目录中没有该表；请刷新目录或人工选择'}]


def t_parse_fill_output_e2e_layering():
    """端到端分层：合法操作照常返回、单操作违规转 invalid、围栏剥离、问题换发、自报待补过滤。"""
    payload = {
        'operations': [
            op_set('name', '创智园储能电站'),
            op_set('unit', 'MW', quote='单位用 MWh'),  # 枚举越界 → invalid
            {'op': 'clear', 'field': 'note', 'basis': b_intent('把备注清空')},
            op_append('lookupMatch', 'r1'),
        ],
        'questions': [
            {'id': 'm1', 'text': '连接用哪个实例？', 'fields': ['connection'], 'allowUnsure': True},
        ],
        'unresolved': [{'field': 'owner', 'reason': '目录缺少负责人候选'}],
    }
    content = '```json\n' + json.dumps(payload, ensure_ascii=False) + '\n```'
    result = parse_fill(content)
    assert [op['field'] for op in result['operations']] == ['name', 'note', 'lookupMatch']
    assert len(result['invalid_operations']) == 1
    assert result['invalid_operations'][0]['field'] == 'unit'
    assert '枚举' in result['invalid_operations'][0]['reason']
    assert len(result['questions']) == 1 and result['questions'][0]['id'] == 'q_2'  # 避让 q_1/q_7
    assert result['unresolved'] == [{'field': 'owner', 'reason': '目录缺少负责人候选'}]


def t_unresolved_accumulation_cap():
    """限额不变式：operations ≤12（超出 502）⇒ invalid（转 unresolved 的来源之一）≤12；
    模型自报 unresolved ≤12（超出 502）。两者合并后的响应级 ≤12 截断由上层 service
    组装时执行（见 parse_fill_output docstring）。"""
    ops = [op_set('name', 'v%d' % i) for i in range(12)]
    result = validate(ops)  # 同字段 12 条 → 全部冲突无效，invalid 恰 12 条不超限
    assert len(result['invalid']) == 12 and result['valid'] == []
    parsed = parse_fill({'operations': ops,
                         'unresolved': [{'field': 'unit', 'reason': 'r'} for _ in range(12)]})
    assert len(parsed['invalid_operations']) == 12
    assert len(parsed['unresolved']) == 12


ALL_TESTS = [
    ('a0) FixtureContract 与冻结 loader 接口一致', t_fixture_contract_interface),
    ('a1) 合法 set/clear/row 操作全部通过并规范化', t_valid_ops_passthrough),
    ('a2) 同列表多条 row.append 合法', t_multiple_row_append_same_list),
    ('b1) 同字段两条 set 冲突整组无效', t_same_field_two_sets_conflict),
    ('b2) set+clear 同字段冲突整组无效', t_set_plus_clear_conflict),
    ('b3) 同字段冲突不波及其他字段', t_conflict_does_not_leak_to_other_fields),
    ('b4) 重复 localId 行操作冲突', t_duplicate_localid_conflict),
    ('b5) 同 rowId update+remove 冲突', t_rowid_update_remove_conflict),
    ('b6) 原子组不完整整组无效', t_atomic_group_incomplete),
    ('b7) 原子组完整通过', t_atomic_group_complete),
    ('b8) 原子组成员自身违规仍整组无效', t_atomic_group_member_invalid_still_group_invalid),
    ('c1) clear 非 nullable 拒', t_clear_non_nullable_rejected),
    ('c2) clear 未开 ai.clearable 拒', t_clear_not_clearable_rejected),
    ('c3) basis quote 不在 intent 中拒', t_basis_quote_not_in_intent),
    ('c4) intent 为空时 quote 一律拒', t_basis_quote_empty_intent),
    ('c5) basis questionId 不在集合拒', t_basis_question_not_in_valid_set),
    ('c6) basis questionId 在集合内通过', t_basis_question_in_valid_set_ok),
    ('c7) 缺 basis 拒且行增改无需 basis', t_missing_basis_rejected_only_where_required),
    ('c8) basis 形态非法逐例拒', t_basis_malformed_rejected),
    ('d1) 未知 field 路径拒', t_unknown_field_rejected),
    ('d2) 任意 JSON 路径（超契约）拒', t_arbitrary_json_path_rejected),
    ('d3) set 指向列表/行内字段拒', t_set_on_list_paths_rejected),
    ('d4) 行操作指向非列表/行内字段拒', t_rowop_non_list_rejected),
    ('d5) set 值按字段类型逐例拒', t_set_value_type_rules),
    ('d6) 非 fillable 与 sensitive 字段拒写', t_set_non_fillable_and_sensitive_rejected),
    ('d7) row.localId 非法格式拒', t_row_localid_bad_format),
    ('d8) row.update/remove 形态逐例拒', t_row_update_remove_shapes),
    ('e1) operations 13 条结构违规', t_operations_over_limit),
    ('e2) operations 12 条恰好通过', t_operations_twelve_ok),
    ('e3) operations 非数组/元素非对象违规', t_operations_non_array_and_bad_element),
    ('e4) 未知 op 结构违规', t_unknown_op_structural),
    ('e5) op 携带多余键结构违规', t_op_extra_key_structural),
    ('f1) 顶层未知键结构违规', t_top_level_unknown_key),
    ('f2) questions 超过 3 条违规', t_questions_over_limit),
    ('f3) 模型 unresolved 超过 12 条违规', t_model_unresolved_over_limit),
    ('f4) questions id 由服务端换发并避让', t_questions_reissued_ids),
    ('f5) 问题 fields 含契约外路径丢弃该问题', t_question_unknown_field_dropped),
    ('f6) 模型 unresolved 逐条契约过滤', t_model_unresolved_filtered),
    ('g1) parse_fill_output 端到端分层（围栏剥离）', t_parse_fill_output_e2e_layering),
    ('g2) unresolved 累计限额不变式', t_unresolved_accumulation_cap),
]


if __name__ == '__main__':
    for name, fn in ALL_TESTS:
        run(name, fn)
    if FAILURES:
        print(f'\n{len(FAILURES)} 项失败：{", ".join(FAILURES)}')
        sys.exit(1)
    print(f'\n全部通过（{len(ALL_TESTS)} 项）')

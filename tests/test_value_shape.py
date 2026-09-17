"""本体侧结果形态 valueShape 后端测试：编码往返、schema 校验、变更分类、共享属性继承。

纯 python3 脚本（无 pytest），全部用内存 dict 构造，不读写任何数据目录。
运行：WIZ_WORKBENCH_ROOT=$(mktemp -d) python3 tests/test_value_shape.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workbench import contracts, model_format, properties

FAILURES = []
CONTEXT = {'mg': 'https://example.org/mg#', 'xsd': 'http://www.w3.org/2001/XMLSchema#'}


def run(name, fn):
    try:
        fn()
        print(f'通过  {name}')
    except Exception as exc:
        FAILURES.append(name)
        print(f'失败  {name}: {type(exc).__name__}: {exc}')


def expect_error(fn, fragment):
    """断言 fn 抛出 ValueError 且报错包含 fragment。"""
    try:
        fn()
    except ValueError as exc:
        assert fragment in str(exc), f'报错信息缺少「{fragment}」：{exc}'
        return
    raise AssertionError(f'未触发报错（期望包含「{fragment}」）')


def soc_record(**kw):
    """schema 形态的最小属性记录。"""
    record = {'id': 'mg:soc', 'objectTypeId': 'mg:Station', 'apiName': 'soc', 'dataType': {'type': 'double'}}
    record.update(kw)
    return record


def schema_state(prop_records):
    return {'ontology': {'schemaVersion': 1, 'namespaces': {}, 'objectTypes': [], 'linkTypes': [],
                         'properties': list(prop_records), 'sharedProperties': [], 'valueTypes': [], 'metadata': [],
                         'definitionOrder': [r['id'] for r in prop_records]},
            'workflow': {'functions': [], 'actions': [], 'interfaces': []}}


def ontology_model(prop_records):
    return schema_state(prop_records)['ontology']


def legacy_state(nodes):
    return {'ontology': {'@context': dict(CONTEXT), '@graph': nodes}, 'workflow': {}}


def station_and_soc(**soc_kw):
    soc = {'@id': 'mg:soc', '@type': 'owl:DatatypeProperty', 'rdfs:label': 'SOC', 'mg:apiName': 'soc',
           'rdfs:domain': {'@id': 'mg:Station'}, 'rdfs:range': {'@id': 'xsd:double'}}
    soc.update(soc_kw)
    return [{'@id': 'mg:Station', '@type': 'owl:Class', 'rdfs:label': '电站'}, soc]


# --- a) encode/decode 无损往返 ------------------------------------------------

def test_round_trip():
    legacy = {'@context': dict(CONTEXT), '@graph': station_and_soc(
        **{'mg:valueShape': 'timeSeries', 'mg:valueSuffix': '%'})}
    model = model_format.encode_ontology(legacy)
    prop = next(r for r in model['properties'] if r['id'] == 'mg:soc')
    assert prop['dataType'] == {'type': 'timeSeries', 'valueType': 'double'} and 'valueShape' not in prop, prop
    assert 'valueShape' in model_format.RECORD_FIELDS
    assert 'mg:valueShape' in model_format.LEGACY_FIELDS
    assert model_format.decode_ontology(model) == legacy, 'timeSeries 往返不等价'

    explicit = copy.deepcopy(legacy)
    explicit['@graph'][1]['mg:valueShape'] = 'scalar'
    model2 = model_format.encode_ontology(explicit)
    prop2 = next(r for r in model2['properties'] if r['id'] == 'mg:soc')
    assert 'valueShape' not in prop2, '新格式不再写 scalar 标志'
    explicit['@graph'][1].pop('mg:valueShape')
    assert model_format.decode_ontology(model2) == explicit, '显式 scalar 应规范化为普通类型'

    shared = {'@id': 'mg:shared_cap', '@type': 'mg:SharedProperty', 'rdfs:label': '容量', 'mg:apiName': 'cap',
              'rdfs:range': {'@id': 'xsd:double'}, 'mg:valueShape': 'timeSeries'}
    legacy3 = {'@context': dict(CONTEXT), '@graph': station_and_soc() + [shared]}
    model3 = model_format.encode_ontology(legacy3)
    assert next(r for r in model3['sharedProperties'] if r['id'] == 'mg:shared_cap')['dataType'] == {'type':'timeSeries','valueType':'double'}
    assert model_format.decode_ontology(model3) == legacy3, '共享属性往返不等价'


# --- b) validate_json ---------------------------------------------------------

def test_validate_json():
    def check(*records):
        model_format.validate_json(ontology_model(list(records)))

    check(soc_record())                                            # 缺省通过
    check(soc_record(valueShape='scalar'))                         # 显式 scalar 通过
    check(soc_record(valueShape='timeSeries'))                     # timeSeries + double 通过
    expect_error(lambda: check(soc_record(valueShape='vector')), 'valueShape 必须是 scalar 或 timeSeries')
    expect_error(lambda: check(soc_record(valueShape='')), 'valueShape 必须是 scalar 或 timeSeries')
    for bad in ('array', 'struct'):
        expect_error(lambda bad=bad: check(soc_record(valueShape='timeSeries', dataType={'type': bad})),
                     '时间序列时，数据类型不能是数组或结构体')


# --- c) contracts.classify ----------------------------------------------------

def shape_reasons(result):
    return [r for r in result['reasons'] if r.get('id') == 'mg:soc' and '数据类型' in r.get('text', '')]


def test_classify_defaults_equivalent():
    old, new = schema_state([soc_record()]), schema_state([soc_record()])
    result = contracts.classify(old, new)
    assert result['reasons'] == [], f'两侧均缺省不应产生任何变更：{result["reasons"]}'

    new = schema_state([soc_record(valueShape='scalar')])
    result = contracts.classify(old, new)
    assert not shape_reasons(result), f'缺省单值 == 显式单值，不应产生变更：{result["reasons"]}'
    assert result['reasons'] == [], f'规范化后应零差异：{result["reasons"]}'

    old = schema_state([soc_record(valueShape='')])
    result = contracts.classify(old, new)
    assert result['reasons'] == [], f"'' 与 'scalar' 等价：{result['reasons']}"

    old, new = schema_state([soc_record(valueShape='scalar')]), schema_state([soc_record()])
    result = contracts.classify(old, new)
    assert result['reasons'] == [], f'显式单值改回缺省也应零差异：{result["reasons"]}'


def test_classify_breaking():
    old, new = schema_state([soc_record()]), schema_state([soc_record(valueShape='timeSeries')])
    result = contracts.classify(old, new)
    assert result['type'] == 'breaking', f'scalar→timeSeries 应为破坏性：{result}'
    assert shape_reasons(result), '缺少结果形态变更说明'

    old = schema_state([soc_record(valueShape='scalar')])
    result = contracts.classify(old, new)
    assert result['type'] == 'breaking', '显式 scalar→timeSeries 应为破坏性'

    old, new = schema_state([soc_record(valueShape='timeSeries')]), schema_state([soc_record()])
    result = contracts.classify(old, new)
    assert result['type'] == 'breaking', 'timeSeries→缺省(单值) 应为破坏性'

    old, new = schema_state([soc_record(valueShape='timeSeries')]), schema_state([soc_record(valueShape='scalar')])
    result = contracts.classify(old, new)
    assert result['type'] == 'breaking', 'timeSeries→显式 scalar 应为破坏性'

    # 共享属性同样受控
    old_shared = {'id': 'mg:sh', 'apiName': 'sh', 'dataType': {'type': 'double'}}
    old, new = schema_state([]), schema_state([])
    old['ontology']['sharedProperties'] = [dict(old_shared)]
    old['ontology']['definitionOrder'] = ['mg:sh']
    new['ontology']['sharedProperties'] = [dict(old_shared, valueShape='timeSeries')]
    result = contracts.classify(old, new)
    assert result['type'] == 'breaking', f'共享属性 scalar→timeSeries 应为破坏性：{result}'


def test_classify_no_false_positive():
    # valueShape 不变（两侧 timeSeries）时不误报
    old, new = schema_state([soc_record(valueShape='timeSeries')]), schema_state([soc_record(valueShape='timeSeries')])
    result = contracts.classify(old, new)
    assert result['reasons'] == [], f'valueShape 未变不应报变更：{result["reasons"]}'

    # valueShape 不变、仅改名称：只有兼容条目，无 breaking
    new = schema_state([soc_record(valueShape='timeSeries', displayName='荷电状态')])
    result = contracts.classify(old, new)
    assert result['type'] == 'compatible', f'仅改名称应为兼容：{result}'
    assert all(r['severity'] == 'compatible' for r in result['reasons'])

    # 旧记录带其他字段、新记录补显式 scalar：唯一差异被规范化抵消
    old = schema_state([soc_record(description='功率')])
    new = schema_state([soc_record(description='功率', valueShape='scalar')])
    result = contracts.classify(old, new)
    assert result['reasons'] == [], f'补写显式 scalar 不应产生变更：{result["reasons"]}'


def test_classify_via_legacy_state():
    """classify 直接接收 JSON-LD 形态（走 _as_schema/encode_ontology）。"""
    old = legacy_state(station_and_soc())
    new = legacy_state(station_and_soc(**{'mg:valueShape': 'timeSeries'}))
    result = contracts.classify(old, new)
    assert result['type'] == 'breaking', f'JSON-LD 形态输入也应识别破坏性：{result}'
    assert shape_reasons(result)


# --- d) properties.effective() 共享属性继承 -----------------------------------

def shared_and_ref(shared_kw=None, ref_kw=None):
    shared = {'@id': 'mg:shared_soc', '@type': properties.SHARED, 'rdfs:label': 'SOC', 'mg:apiName': 'soc',
              'rdfs:range': {'@id': 'xsd:double'}}
    shared.update(shared_kw or {})
    ref = {'@id': 'mg:b_soc', '@type': 'owl:DatatypeProperty', 'rdfs:label': 'SOC', 'mg:apiName': 'soc',
           'rdfs:domain': {'@id': 'mg:Station'}, 'mg:sharedProperty': {'@id': 'mg:shared_soc'}}
    ref.update(ref_kw or {})
    return shared, ref


def test_effective_inheritance():
    shared, ref = shared_and_ref({'mg:valueShape': 'timeSeries'})
    eff = properties.effective(ref, [shared, ref])
    assert eff.get('mg:valueShape') == 'timeSeries', f'引用属性应继承共享定义的结果形态：{eff.get("mg:valueShape")}'

    shared, ref = shared_and_ref()
    eff = properties.effective(ref, [shared, ref])
    assert 'mg:valueShape' not in eff, '共享定义缺省时引用属性不应出现该字段'


def test_validate_properties_shape():
    station = {'@id': 'mg:Station', '@type': 'owl:Class', 'rdfs:label': '电站'}

    shared, ref = shared_and_ref({'mg:valueShape': 'timeSeries'})
    assert properties.validate_properties([station, shared, ref]) == [], 'timeSeries + double 应通过校验'

    shared, ref = shared_and_ref({'mg:valueShape': 'timeSeries', 'rdfs:range': {'@id': 'xsd:array'}})
    errors = properties.validate_properties([station, shared, ref])
    assert any('时间序列' in e for e in errors), f'timeSeries + array 应报错：{errors}'

    shared, ref = shared_and_ref({'mg:valueShape': 'sequence'})
    errors = properties.validate_properties([station, shared, ref])
    assert any('结果形态无效' in e for e in errors), f'非法 valueShape 应报错：{errors}'

    # 引用方不得覆盖继承内容（mg:valueShape 已进入 FIELDS）
    shared, ref = shared_and_ref({'mg:valueShape': 'timeSeries'}, {'mg:valueShape': 'scalar'})
    errors = properties.validate_properties([station, shared, ref])
    assert any('覆盖' in e for e in errors), f'引用方覆盖 valueShape 应报错：{errors}'


if __name__ == '__main__':
    run('a) encode/decode 含 mg:valueShape 无损往返', test_round_trip)
    run('b) validate_json：非法值/时间序列+array 报错，缺省通过', test_validate_json)
    run('c1) classify：缺省与显式 scalar 等价（零差异）', test_classify_defaults_equivalent)
    run('c2) classify：scalar↔timeSeries 互改均为 breaking', test_classify_breaking)
    run('c3) classify：valueShape 不变时不误报', test_classify_no_false_positive)
    run('c4) classify：JSON-LD 形态输入走 encode 路径', test_classify_via_legacy_state)
    run('d1) effective()：共享定义 valueShape 继承', test_effective_inheritance)
    run('d2) validate_properties：valueShape 合法性与覆盖校验', test_validate_properties_shape)
    if FAILURES:
        print(f'\n{len(FAILURES)} 项失败：{", ".join(FAILURES)}')
        sys.exit(1)
    print('\n全部通过')

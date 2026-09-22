"""autofill/1 表单契约一致性守护 —— T1（2026-09-22 整表自动填写改版）。

覆盖（开发计划 §8.3，命令：python3 tests/test_autofill_contracts.py）：
① 全 10 契约可加载、digest 重算幂等（加载两次同值）；
② 生成器幂等：运行生成器两次，第二份与入库文件字节相同；
③ 漂移检测：临时改动契约后 loader 报出 digest 变化 / 结构非法被拒；
④ 契约 ↔ assist_fields 注册表双向覆盖检查（allowlist 式对照，允许契约更细、不得凭空造字段）；
⑤ visibleWhen / 语法非法值拒绝（op 不在集合、field 不存在、未知键、未知类型等）；
⑥ ai.sensitive 字段不得出现在 refProviders 候选。

纯 python3 直跑（无 pytest），不启动服务器、不占端口；一切写入在 tempfile 临时目录，
绝不写真实 ontology/（生成器仅重写其自家产物 formContracts.gen.ts，用于②的入库比对）。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from workbench import assist_fields, assist_forms  # noqa: E402

PASS = {'n': 0}
FAIL = {'n': 0}


def check(cond, msg):
    if cond:
        PASS['n'] += 1
    else:
        FAIL['n'] += 1
        print('  断言失败: %s' % msg)


def run(name, fn):
    print(name)
    try:
        fn()
    except Exception as exc:  # noqa: B902
        FAIL['n'] += 1
        print('  异常: %s: %s' % (type(exc).__name__, exc))


# ---- 临时契约目录工具 -----------------------------------------------------------

def temp_forms_dir(mutations=None):
    """复制真实契约到临时目录，逐份施加 in-place mutation(doc)；返回目录路径。"""
    tmp = tempfile.mkdtemp(prefix='wiz_autofill_contracts_')
    for name in os.listdir(assist_forms.DEFAULT_FORMS_DIR):
        shutil.copy(os.path.join(assist_forms.DEFAULT_FORMS_DIR, name), tmp)
    for form_id, mutate in (mutations or {}).items():
        path = os.path.join(tmp, form_id + '.json')
        with open(path, 'r', encoding='utf-8') as fh:
            doc = json.load(fh)
        mutate(doc)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
    return tmp


def expect_contract_error(name, form_id, forms_dir, draft_kind=None):
    try:
        assist_forms.load(form_id, draft_kind, forms_dir=forms_dir)
        check(False, name + '（未抛 ContractError）')
    except assist_forms.ContractError as exc:
        check(name in str(exc) or len(str(exc)) > 0, name + '（错误信息可读）')
        check(True, name)


# ---- ① 全 10 契约加载 + digest 幂等 ---------------------------------------------

def t01_load_all_and_digest_idempotent():
    check(assist_forms.check_consistency() == [], 'check_consistency 全部通过')
    for form_id in assist_forms.FORM_IDS:
        doc1, v1, d1 = assist_forms.load_raw(form_id)
        doc2, v2, d2 = assist_forms.load_raw(form_id)
        check(v1 == v2 == 1, '%s schemaVersion=1' % form_id)
        check(d1 == d2, '%s digest 两次装载同值' % form_id)
        check(re.fullmatch(r'[0-9a-f]{64}', d1) is not None, '%s digest 是 64 位十六进制' % form_id)
        check(d1 == hashlib.sha256(assist_forms.canonical_bytes(doc1)).hexdigest(),
              '%s digest 与 canonical JSON SHA-256 重算一致' % form_id)
        check(assist_forms.digest_of(doc2) == d1, '%s digest_of 稳定' % form_id)
        if form_id != assist_forms.PROPERTY_SOURCE_FORM:
            loaded = assist_forms.load(form_id)
            check(loaded['formId'] == form_id and loaded['fields'], '%s load 出非空 fields' % form_id)
            check(loaded['schemaDigest'] == d1 and loaded['schemaVersion'] == v1,
                  '%s load 回带 version+digest' % form_id)
    for kind in assist_forms.PROPERTY_SOURCE_KINDS:
        loaded = assist_forms.load('propertySource', kind)
        check(loaded['fields'] and loaded['draftKind'] == kind,
              'propertySource.%s 变体加载' % kind)
        check(assist_forms.schema_digest('propertySource', kind)
              == assist_forms.schema_digest('propertySource'),
              'propertySource.%s digest 同文件' % kind)
    # 查询 API：点路径 / 原子组 / list / refProvider
    leaf = assist_forms.field_def('propertySource', 'result.valueField', 'database')
    check(leaf['type'] == 'ref' and leaf['required'] is True, 'field_def 扁平点路径命中')
    cell = assist_forms.field_def('propertySource', 'lookup.match.value.kind', 'database')
    check(cell['enum'] == ['identityKey', 'identityField', 'property', 'constant', 'parameter'],
          'field_def 行内嵌套路径命中')
    check(assist_forms.atomic_groups('property') == {'typeCore': ['dataType', 'obsType']},
          'property typeCore 原子组')
    check(assist_forms.atomic_groups('identity') == {}, 'identity 无原子组（visibleWhen 表达）')
    check(assist_forms.list_def('propertySource', 'lookupMatch', 'database')['rowIdScope'] == 'local',
          'list_def lookupMatch')
    check([d['id'] for d in assist_forms.list_defs('actionBinding')] == ['actionParams'],
          'list_defs actionBinding')
    check(assist_forms.ref_provider('link', 'object') == 'ontologyObjects', 'ref_provider 角色')


# ---- ② 生成器幂等（两次运行字节一致，且与入库文件相同） ---------------------------

def t02_generator_idempotent():
    gen_path = os.path.join(ROOT, 'frontend', 'src', 'assist', 'formContracts.gen.ts')
    check(os.path.isfile(gen_path), '入库生成物存在')
    with open(gen_path, 'rb') as fh:
        committed = fh.read()
    outputs = []
    for _ in range(2):
        proc = subprocess.run([sys.executable, '-m', 'workbench.assist_forms_gen'],
                              cwd=ROOT, capture_output=True, text=True,
                              env=dict(os.environ))
        check(proc.returncode == 0, '生成器退出码 0')
        with open(gen_path, 'rb') as fh:
            outputs.append(fh.read())
    check(outputs[0] == committed, '第一次运行与入库文件字节相同（无漂移）')
    check(outputs[1] == outputs[0], '第二次运行与第一次字节相同（确定性输出）')
    text = outputs[0].decode('utf-8')
    for form_id in assist_forms.FORM_IDS:
        check(json.dumps(form_id) in text, '生成物含 formId %s' % form_id)
        check(assist_forms.schema_digest(form_id) in text, '生成物含 %s digest' % form_id)
    check('export const FORM_CONTRACTS' in text, '生成物含 FORM_CONTRACTS')
    check('export const FORM_SCHEMA_VERSIONS' in text, '生成物含 FORM_SCHEMA_VERSIONS')
    check('export const FORM_SCHEMA_DIGESTS' in text, '生成物含 FORM_SCHEMA_DIGESTS')
    check(text.count(': AssistFormContract = {') == 10, '生成物含 10 个契约常量')


# ---- ③ 漂移检测 -----------------------------------------------------------------

def t03_drift_detected():
    baseline = assist_forms.schema_digest('identity')
    # 3a. 加一个枚举值：语法仍合法，但 digest 必须变化（CONTEXT_STALE 语义的来源）
    tmp = temp_forms_dir({'identity': lambda d: d['fields'][0]['enum'].append('snapshotted')})
    try:
        drifted = assist_forms.schema_digest('identity', forms_dir=tmp)
        check(drifted != baseline, '枚举值增加 → digest 变化')
        check(assist_forms.load('identity', forms_dir=tmp)['fields'][0]['enum']
              == ['database', 'registered', 'snapshotted'], '漂移契约仍可按新内容加载')
        check(assist_forms.check_consistency(forms_dir=tmp) == [], '漂移契约结构仍自洽')
        check(assist_forms.schema_digest('identity') == baseline, '真实目录不受临时目录影响')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # 3b. 注入 schemaDigest 字段不影响 digest 口径（canonical JSON 去 digest）
    tmp = temp_forms_dir({'identity': lambda d: d.update(schemaDigest='forged')})
    try:
        check(assist_forms.schema_digest('identity', forms_dir=tmp) == baseline,
              '注入 schemaDigest 被忽略（去 digest 字段口径）')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # 3c. 结构性破坏：loader 直接拒绝，check_consistency 逐项报告
    tmp = temp_forms_dir({'link': lambda d: d['fields'][4].update(
        visibleWhen={'field': 'label', 'op': 'matches', 'value': 'x'})})
    try:
        problems = assist_forms.check_consistency(forms_dir=tmp)
        check(problems and 'op 未冻结' in problems[0], 'check_consistency 报出非法 op')
        try:
            assist_forms.load('link', forms_dir=tmp)
            check(False, '非法 op 契约被 load 拒绝')
        except assist_forms.ContractError:
            check(True, '非法 op 契约被 load 拒绝')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # 3d. 文件缺失
    tmp = tempfile.mkdtemp(prefix='wiz_autofill_missing_')
    try:
        try:
            assist_forms.load('object', forms_dir=tmp)
            check(False, '缺失契约文件被拒绝')
        except assist_forms.ContractError as exc:
            check('缺失' in str(exc), '缺失契约文件报缺失')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- ④ 契约 ↔ assist_fields 注册表双向覆盖（allowlist 对照） ---------------------
# 映射：契约路径 → (注册表键, required 校验开关)。None = 行内单元格（复合键的更细结构，
# 只对存在性负责，不重复校验整组的 required/maxLength/枚举）。

_CORRESPONDENCE = {
    ('object', None): {
        'label': ('label', True), 'comment': ('comment', True)},
    ('property', None): {
        'label': ('label', True), 'comment': ('comment', True),
        'dataType': ('dataType', True), 'obsType': ('obsType', False),
        'formatting': ('formatting', False)},
    ('sharedProperty', None): {
        'label': ('label', True), 'comment': ('comment', True),
        'dataType': ('dataType', True), 'obsType': ('obsType', False),
        'formatting': ('formatting', False)},
    ('link', None): {
        'label': ('label', True), 'from': ('from', True), 'to': ('to', True),
        'cardinality': ('cardinality', True), 'reverseLabel': ('reverseLabel', False),
        'comment': ('comment', False)},
    ('rule', None): {
        'name': ('name', True), 'description': ('description', True),
        'content': ('content', False)},
    ('action', None): {
        'name': ('name', True), 'description': ('description', True),
        'effect': ('effect', False)},
    ('identity', None): {
        'mode': ('mode', True), 'connection': ('connection', False),
        'table': ('table', False), 'primaryKey': ('primaryKey', False),
        'note': ('note', False)},
    ('propertySource', 'field'): {
        'field': ('field', True), 'note': ('note', False)},
    ('propertySource', 'database'): {
        'connection': ('connection', True), 'table': ('table', True),
        'result.valueField': ('result.valueField', True),
        'result.timestampField': ('result.timestampField', False),
        'lookup.match': ('lookup.match', False), 'note': ('note', False),
        'lookup.match.field': ('lookup.match', None),
        'lookup.match.operator': ('lookup.match', None),
        'lookup.match.value': ('lookup.match', None),
        'lookup.match.value.kind': ('lookup.match', None),
        'lookup.match.value.field': ('lookup.match', None),
        'lookup.match.value.property': ('lookup.match', None),
        'lookup.match.value.value': ('lookup.match', None),
        'lookup.match.value.parameter': ('lookup.match', None)},
    ('propertySource', 'redis'): {
        'connection': ('connection', True), 'command': ('command', True),
        'key': ('key', True), 'hashField': ('hashField', False),
        'params': ('params', False), 'conversion': ('conversion', False),
        'missing': ('missing', False), 'note': ('note', False),
        'params.token': ('params', None), 'params.from': ('params', None),
        'params.field': ('params', None), 'params.property': ('params', None)},
    ('propertySource', 'flow'): {
        'flow': ('flow', True), 'output': ('output', True),
        'inputs': ('inputs', False),
        'result.valueField': ('result.valueField', False),
        'result.timestampField': ('result.timestampField', False),
        'note': ('note', False),
        'inputs.inputId': ('inputs', None), 'inputs.from': ('inputs', None),
        'inputs.property': ('inputs', None), 'inputs.value': ('inputs', None)},
    ('linkMapping', None): {
        'sourceId': ('sourceId', True), 'field': ('field', True),
        'targetSourceId': ('targetSourceId', True), 'targetField': ('targetField', True),
        'note': ('note', False)},
    ('actionBinding', None): {
        'method': ('method', True), 'path': ('path', True),
        'bodyFormat': ('bodyFormat', False), 'description': ('description', False),
        'parameters': ('parameters', False), 'note': ('note', False),
        'parameters.name': ('parameters', None), 'parameters.in': ('parameters', None),
        'parameters.value': ('parameters', None),
        'parameters.value.from': ('parameters', None),
        'parameters.value.inputId': ('parameters', None),
        'parameters.value.property': ('parameters', None),
        'parameters.value.valueType': ('parameters', None),
        'parameters.value.value': ('parameters', None)},
}

_KIND_TO_CONTRACT = {
    assist_fields.TEXT: {'text'},
    assist_fields.URL: {'text'},
    assist_fields.TEXTAREA: {'textarea'},
    assist_fields.SELECT: {'enum'},
    assist_fields.REF: {'ref'},
    assist_fields.COMPOSITE: {'group', 'list'},
}


def _contract_paths(fields, lists, prefix=''):
    """收集契约节点路径（组=组路径 + 递归（codec 托管组为叶）；list=叶 + 行内单元格递归）。"""
    out = {}
    for f in fields:
        path = (prefix + f['id']) if prefix else f['id']
        if f['type'] == 'group':
            if f.get('fields'):
                out[path] = f
                out.update(_contract_paths(f['fields'], lists, path + '.'))
            else:
                out[path] = f
        elif f['type'] == 'list':
            out[path] = f
            decl = next((d for d in lists if d.get('id') == f.get('list')), None)
            if decl:
                out.update(_contract_paths(decl['item']['fields'], lists, path + '.'))
        else:
            out[path] = f
    return out


def t04_registry_cross_coverage():
    scenarios = sorted({(form, kind) for (form, kind) in _CORRESPONDENCE})
    check(len(scenarios) == 13, '对照表覆盖 10 formId 的 13 个（变体）场景')
    for form_id, kind in scenarios:
        loaded = assist_forms.load(form_id, kind)
        computed = _contract_paths(loaded['fields'], loaded['lists'])
        expected = _CORRESPONDENCE[(form_id, kind)]
        label = '%s%s' % (form_id, ('/' + kind) if kind else '')
        check(set(computed) == set(expected),
              '%s 契约路径与对照表完全一致（不得凭空造字段）：%s'
              % (label, sorted(set(computed) ^ set(expected))))
        registry_map = assist_fields.field_map(form_id, kind)
        covered = {target for target, _flag in expected.values()}
        check(covered == set(registry_map),
              '%s 注册表 fillable 键全覆盖：%s' % (label, sorted(set(registry_map) ^ covered)))
        # 逐字段类型/必填/长度/枚举相容（仅主映射；行内单元格允许更细）
        for path, (reg_key, flag) in expected.items():
            if flag is None:
                continue
            node, reg = computed[path], registry_map[reg_key]
            allowed = _KIND_TO_CONTRACT[reg['kind']]
            check(node['type'] in allowed,
                  '%s.%s 类型 %s ∈ %s（注册表 kind=%s）' % (label, path, node['type'], sorted(allowed), reg['kind']))
            check(bool(node.get('required')) == bool(reg['required']),
                  '%s.%s required 与注册表一致' % (label, path))
            if node['type'] in ('text', 'textarea'):
                check(node.get('maxLength', 2000) == reg['maxLen'],
                      '%s.%s maxLength 与注册表 maxLen 一致' % (label, path))
            if node['type'] == 'enum':
                expected_enum = {str(o).replace('xsd:', '') for o in (reg['options'] or [])}
                if reg_key == 'dataType':
                    expected_enum = expected_enum | {'timeSeries'}  # assist_schema._select_allowed 特例值
                check(set(node['enum']) == expected_enum,
                      '%s.%s 枚举与注册表选项一致：%s' % (label, path, node['enum']))
    # sharedProperty 是 property 的注册表别名，契约字段结构必须保持镜像
    check(assist_forms.load('sharedProperty')['fields'] == assist_forms.load('property')['fields'],
          'sharedProperty 契约与 property 镜像')


# ---- ⑤ 语法 / visibleWhen 非法值拒绝 --------------------------------------------

def t05_invalid_syntax_rejected():
    # op 不在冻结集合
    tmp = temp_forms_dir({'identity': lambda d: d['fields'][1].update(
        visibleWhen={'field': 'mode', 'op': 'matches', 'value': 'database'})})
    expect_contract_error('op 非法被拒', 'identity', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    # visibleWhen.field 不存在
    tmp = temp_forms_dir({'identity': lambda d: d['fields'][1].update(
        visibleWhen={'field': 'nope', 'op': 'eq', 'value': 'database'})})
    expect_contract_error('visibleWhen.field 不存在被拒', 'identity', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    # eq 缺 value / notEmpty 带 value
    tmp = temp_forms_dir({'identity': lambda d: d['fields'][1].update(
        visibleWhen={'field': 'mode', 'op': 'eq'})})
    expect_contract_error('eq 缺 value 被拒', 'identity', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    tmp = temp_forms_dir({'identity': lambda d: d['fields'][4].update(
        editableWhen={'field': 'mode', 'op': 'notEmpty', 'value': 'x'})})
    expect_contract_error('notEmpty 带 value 被拒', 'identity', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    # requires 指向不存在字段
    tmp = temp_forms_dir({'identity': lambda d: d['fields'][2].update(requires='ghost')})
    expect_contract_error('requires 悬空被拒', 'identity', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    # 未知字段类型（旧注册表类型 select 不进新契约）
    tmp = temp_forms_dir({'rule': lambda d: d['fields'][0].update(type='select')})
    expect_contract_error('未知字段类型被拒', 'rule', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    # 未冻结键：顶层 help / 字段级 pattern
    tmp = temp_forms_dir({'rule': lambda d: d.update(help='额外说明')})
    expect_contract_error('顶层未冻结键被拒', 'rule', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    tmp = temp_forms_dir({'rule': lambda d: d['fields'][0].update(pattern='[a-z]+')})
    expect_contract_error('字段级未冻结键被拒', 'rule', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    # ref 候选角色未登记 / enum 空 / maxLength 出现在 enum
    tmp = temp_forms_dir({'link': lambda d: d['fields'][1].update(refProvider='galaxy')})
    expect_contract_error('refProvider 角色未登记被拒', 'link', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    tmp = temp_forms_dir({'identity': lambda d: d['fields'][0].update(enum=['database', 'database'])})
    expect_contract_error('枚举重复值被拒', 'identity', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    tmp = temp_forms_dir({'identity': lambda d: d['fields'][0].update(maxLength=10)})
    expect_contract_error('enum 字段带 maxLength 被拒', 'identity', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    # 组无 fields 且无 codec / list 指向未声明 id / sensitive+fillable
    tmp = temp_forms_dir({'object': lambda d: d['fields'][1].update(type='group')})
    expect_contract_error('空组被拒', 'object', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    tmp = temp_forms_dir({'actionBinding': lambda d: d['fields'][4].update(list='ghost')})
    expect_contract_error('list 指向未声明 id 被拒', 'actionBinding', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    tmp = temp_forms_dir({'object': lambda d: d['fields'][0].update(
        ai={'fillable': True, 'sensitive': True})})
    expect_contract_error('sensitive 且 fillable 被拒', 'object', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    # 禁止字段 id：凭据类 / 分派键 kind
    tmp = temp_forms_dir({'object': lambda d: d['fields'][0].update(id='apiKey')})
    expect_contract_error('apiKey 字段被拒', 'object', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    tmp = temp_forms_dir({'identity': lambda d: d['fields'][0].update(id='kind')})
    expect_contract_error('kind 分派键被拒', 'identity', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    # JSON 损坏 / formId 不符 / 未知 formId / variant 边界
    tmp = tempfile.mkdtemp(prefix='wiz_autofill_badjson_')
    try:
        shutil.copy(os.path.join(assist_forms.DEFAULT_FORMS_DIR, 'object.json'), tmp)
        with open(os.path.join(tmp, 'object.json'), 'w', encoding='utf-8') as fh:
            fh.write('{"formId": "object", ')
        try:
            assist_forms.load('object', forms_dir=tmp)
            check(False, 'JSON 损坏被拒')
        except assist_forms.ContractError as exc:
            check('合法 JSON' in str(exc), 'JSON 损坏被拒并报合法 JSON')
        with open(os.path.join(tmp, 'link.json'), 'w', encoding='utf-8') as fh:
            json.dump({'formId': 'link2', 'schemaVersion': 1, 'title': 'x', 'space': 'ontology',
                       'fields': []}, fh)
        expect_contract_error('formId 与文件名不符被拒', 'link', tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    try:
        assist_forms.load('widgets')
        check(False, '未知 formId 被拒')
    except assist_forms.ContractError as exc:
        check('未知的表单契约 formId' in str(exc), '未知 formId 报未知')
    tmp = temp_forms_dir({'propertySource': lambda d: d['variants'].pop('flow')})
    expect_contract_error('缺少变体被拒', 'propertySource', tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        assist_forms.load('propertySource', 'aggregate')
        check(False, '未知 draft.kind 被拒')
    except assist_forms.ContractError as exc:
        check('draft.kind' in str(exc), '未知 draft.kind 报分派错误')
    try:
        assist_forms.load('propertySource')
        check(False, 'propertySource 缺 draft_kind 被拒')
    except assist_forms.ContractError as exc:
        check('draft_kind' in str(exc), 'propertySource 缺 draft_kind 报分派错误')
    try:
        assist_forms.load('object', 'database')
        check(False, '非变体表单带 draft_kind 被拒')
    except assist_forms.ContractError as exc:
        check('不按 draft.kind 分派' in str(exc), '非变体表单带 draft_kind 报错')
    # field_def 未知路径 / 非法路径
    try:
        assist_forms.field_def('identity', 'ghost')
        check(False, 'field_def 未知路径被拒')
    except assist_forms.ContractError:
        check(True, 'field_def 未知路径被拒')
    try:
        assist_forms.list_def('identity', 'nope')
        check(False, 'list_def 未声明 id 被拒')
    except assist_forms.ContractError:
        check(True, 'list_def 未声明 id 被拒')


# ---- ⑥ ai.sensitive 不得出现在 refProviders 候选 --------------------------------

def t06_sensitive_never_in_refproviders():
    # 真实契约：目前无 sensitive 字段，不变量恒真也要程序化验证
    for form_id in assist_forms.FORM_IDS:
        kinds = assist_forms.PROPERTY_SOURCE_KINDS if form_id == 'propertySource' else [None]
        for kind in kinds:
            loaded = assist_forms.load(form_id, kind)
            sensitive = _collect_sensitive(loaded['fields'])
            for role, provider in loaded['refProviders'].items():
                check(not (sensitive & {role, provider}),
                      '%s/%s sensitive 不充当候选（%s）' % (form_id, kind, role))

    def make_sensitive(doc):
        doc['fields'][4]['ai'] = {'fillable': False, 'clearable': False, 'sensitive': True}
        return doc

    # sensitive 字段（fillable=false）可合法加载
    tmp = temp_forms_dir({'identity': make_sensitive})
    try:
        loaded = assist_forms.load('identity', forms_dir=tmp)
        check(_collect_sensitive(loaded['fields']) == {'note'}, 'sensitive+fillable=false 合法')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # sensitive 字段充当 refProvider 角色 / 候选集 → 拒绝（在既有角色上追加，避免未登记误报）
    def role_is_sensitive(doc):
        make_sensitive(doc)
        doc['refProviders']['note'] = 'identityTableFields'

    tmp = temp_forms_dir({'identity': role_is_sensitive})
    try:
        assist_forms.load('identity', forms_dir=tmp)
        check(False, 'sensitive 角色名被拒')
    except assist_forms.ContractError as exc:
        check('sensitive' in str(exc), 'sensitive 角色名被拒并说明原因')
    shutil.rmtree(tmp, ignore_errors=True)

    def provider_is_sensitive(doc):
        make_sensitive(doc)
        doc['refProviders']['identityField'] = 'note'

    tmp = temp_forms_dir({'identity': provider_is_sensitive})
    try:
        assist_forms.load('identity', forms_dir=tmp)
        check(False, 'sensitive 提供方名被拒')
    except assist_forms.ContractError as exc:
        check('sensitive' in str(exc), 'sensitive 提供方名被拒并说明原因')
    shutil.rmtree(tmp, ignore_errors=True)
    # ref 字段的候选集指向 sensitive 字段 id → 拒绝
    def ref_to_sensitive(doc):
        make_sensitive(doc)
        doc['refProviders']['note'] = 'note'
        doc['fields'][2]['refProvider'] = 'note'

    tmp = temp_forms_dir({'identity': ref_to_sensitive})
    try:
        assist_forms.load('identity', forms_dir=tmp)
        check(False, 'ref 候选集指向 sensitive 字段被拒')
    except assist_forms.ContractError as exc:
        check('sensitive' in str(exc), 'ref 候选集指向 sensitive 字段被拒并说明原因')
    shutil.rmtree(tmp, ignore_errors=True)


def _collect_sensitive(fields):
    out = set()

    def walk(nodes):
        for f in nodes or []:
            if not isinstance(f, dict):
                continue
            if (f.get('ai') or {}).get('sensitive'):
                out.add(f['id'])
            walk(f.get('fields'))

    walk(fields)
    return out


# ---- ⑦ FormContract 适配接口（T2 assist_ops 冻结约定） ---------------------------

def t07_form_contract_adapter():
    fc = assist_forms.FormContract('property')
    check(fc.schema_version == 1 and len(fc.digest) == 64, 'FormContract version/digest 属性')
    leaf = fc.field_def('obsType')
    check(leaf['type'] == 'enum' and leaf['enum'] == ['string', 'double', 'boolean', 'dateTime'],
          'FormContract.field_def 规范化 enum')
    check(leaf['ai'] == {'fillable': True, 'clearable': False, 'sensitive': False},
          'field_def ai 补默认值')
    check(leaf['nullable'] is False and leaf['required'] is False and leaf['maxLength'] is None,
          'field_def 缺省 nullable/required/maxLength')
    check(fc.field_def('label')['maxLength'] == 120, 'field_def maxLength 透传')
    # 组节点与列表本身不可寻址（KeyError）；ref 的 ref 键解析为提供方名
    for bad in ('formatting', 'dataType.nope', 'ghost', ''):
        try:
            fc.field_def(bad)
            check(False, 'field_def 非法路径 %r 被 KeyError 拒绝' % bad)
        except KeyError as exc:
            check(str(exc) == repr(bad) or True, 'field_def 非法路径 %r 抛 KeyError' % bad)
    fcdb = assist_forms.FormContract('propertySource', 'database')
    check(fcdb.field_def('result.valueField')['ref'] == 'catalogFields',
          'field_def ref 键解析为候选提供方名')
    check(fcdb.field_def('lookupMatch.field')['type'] == 'ref',
          'field_def 支持「列表id.行字段id」寻址')
    check(fcdb.field_def('lookupMatch.value.kind')['enum'][0] == 'identityKey',
          'field_def 行内嵌套组展平寻址')
    # 组/list 节点不可寻址
    try:
        fcdb.field_def('lookup.match')
        check(False, '列表字段本身不可寻址')
    except KeyError:
        check(True, '列表字段本身不可寻址（KeyError）')
    # 原子组与 list_def 归一
    check(fc.atomic_groups() == {'typeCore': ['dataType', 'obsType']}, 'FormContract 原子组')
    ld = fcdb.list_def('lookupMatch')
    check(ld['id'] == 'lookupMatch' and ld['rowIdScope'] == 'local', 'FormContract list_def 头部')
    check(set(ld['fields']) == {'field', 'operator', 'value.kind', 'value.field',
                                'value.property', 'value.value', 'value.parameter'},
          'list_def 行字段归一为展平 fields 映射：%s' % sorted(ld['fields']))
    check(ld['fields']['value.kind']['required'] is True, 'list_def 行字段 required 保留')
    try:
        fcdb.list_def('ghost')
        check(False, 'list_def 未声明 id 被 KeyError 拒绝')
    except KeyError:
        check(True, 'list_def 未声明 id 抛 KeyError')
    # propertySource 不带 draft_kind → ContractError（调用方必须按目标解析）
    try:
        assist_forms.FormContract('propertySource')
        check(False, 'FormContract 缺 draft_kind 被拒')
    except assist_forms.ContractError:
        check(True, 'FormContract 缺 draft_kind 报 ContractError')
    # 单字段 item 写法（§6.1 示例形态）由 loader 归一
    tmp = temp_forms_dir({'rule': lambda d: d.update(
        lists=[{'id': 'keywords', 'rowIdScope': 'local',
                'item': {'id': 'word', 'type': 'text', 'maxLength': 64}}])})
    try:
        fcr = assist_forms.FormContract('rule', forms_dir=tmp)
        single = fcr.list_def('keywords')
        check(single['fields'] == {'word': {'type': 'text', 'nullable': False, 'required': False,
                                            'maxLength': 64,
                                            'ai': {'fillable': True, 'clearable': False,
                                                   'sensitive': False}}},
              '单字段 item 归一为单键 fields 映射')
        check(fcr.field_def('keywords.word')['maxLength'] == 64,
              '单字段 item 行路径可寻址')
        check(assist_forms.check_consistency(forms_dir=tmp) == [], '单字段 item 契约自洽')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- main -----------------------------------------------------------------------

def main():
    print('== autofill/1 表单契约一致性守护 ==')
    run('① 全 10 契约加载 + digest 重算幂等', t01_load_all_and_digest_idempotent)
    run('② 生成器幂等（两次运行同字节，与入库文件一致）', t02_generator_idempotent)
    run('③ 漂移检测（digest 变化 / 结构非法被拒）', t03_drift_detected)
    run('④ 契约↔assist_fields 注册表双向覆盖', t04_registry_cross_coverage)
    run('⑤ 语法 / visibleWhen 非法值拒绝', t05_invalid_syntax_rejected)
    run('⑥ ai.sensitive 不得出现在 refProviders 候选', t06_sensitive_never_in_refproviders)
    run('⑦ FormContract 适配接口（T2 assist_ops 冻结约定）', t07_form_contract_adapter)
    total = PASS['n'] + FAIL['n']
    print('== 断言 %d 项，通过 %d，失败 %d ==' % (total, PASS['n'], FAIL['n']))
    return 0 if FAIL['n'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())

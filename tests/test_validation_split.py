"""B2 拆分验收：金样回放比对。

读取 tests/fixtures/validation_golden.json（由 tests/make_validation_golden.py 用
拆分前的 projects.validate_project 生成），逐样例调用拆分后的
workbench.project_validation.validate_project，深比较 errors/warnings/items
三列表完全相等（含顺序与文案）；并确认兼容入口 projects.validate_project
与新入口结果一致（server.py 无需改动）。

纯内存回放，不启动服务。未设 WIZ_WORKBENCH_ROOT 时自动落到临时目录。
运行：python3 tests/test_validation_split.py
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.environ.get('WIZ_WORKBENCH_ROOT'):
    os.environ['WIZ_WORKBENCH_ROOT'] = tempfile.mkdtemp(prefix='wiz_validation_split_')

from workbench import projects  # noqa: E402
from workbench.project_validation import validate_project  # noqa: E402

# 账号体系（20260918）：validate_project 读取 flow/凭据按当前账号过滤，生成与回放必须同一账号
from pathlib import Path as _P
import sys as _sys
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import auth_client as _auth_client
_auth_client.bind_fixture_user()

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'validation_golden.json'
FAILURES = []


def diff_list(label, expected, actual):
    """返回两个列表的首个差异描述（顺序敏感）；相等返回 None。"""
    if expected == actual:
        return None
    for idx in range(max(len(expected), len(actual))):
        e = expected[idx] if idx < len(expected) else '<缺失>'
        a = actual[idx] if idx < len(actual) else '<缺失>'
        if e != a:
            return f'{label}[{idx}]\n    预期: {json.dumps(e, ensure_ascii=False)}\n    实际: {json.dumps(a, ensure_ascii=False)}'
    return f'{label} 长度不同: 预期 {len(expected)} 实际 {len(actual)}'


def main():
    data = json.loads(FIXTURE.read_text(encoding='utf-8'))
    samples = data['samples']
    assert samples, '金样为空：请先运行 tests/make_validation_golden.py'
    checks = 0
    for s in samples:
        # validate_project 现存行为会就地改写 state 的 title_key，回放必须用深拷贝输入
        state = copy.deepcopy(s['state'])
        ontology_state = copy.deepcopy(s['ontology_state'])
        report = validate_project(state, ontology_state)
        for key in ('errors', 'warnings', 'items'):
            checks += 1
            problem = diff_list(f"{s['name']} {key}", s['report'][key], report.get(key))
            if problem:
                FAILURES.append(f"{s['name']} {key} 不一致：\n{problem}")
        # 兼容入口（server.py / 既有测试经 projects.* 调用）与新入口结果一致
        checks += 1
        legacy_report = projects.validate_project(copy.deepcopy(s['state']),
                                                  copy.deepcopy(s['ontology_state']))
        if legacy_report != report:
            FAILURES.append(f"{s['name']} 兼容入口 projects.validate_project 与新入口结果不一致")
        # 副作用仍在：validate 的净副作用应恰好等于 derive_display_names（现存行为，B2 不改）
        checks += 1
        expected_state = projects.derive_display_names(copy.deepcopy(s['state']),
                                                       copy.deepcopy(s['ontology_state']))
        if state != expected_state:
            FAILURES.append(f"{s['name']} 调用后 state 与 derive_display_names 的改写结果不一致")
    # 任务 B 新增金样（registered / aggregate / membership）的结构性断言：
    # 防止误删样例或行为漂移后“重新生成即通过”。
    checks += check_registered_samples(samples)
    print(f'金样 {len(samples)} 个样例，{checks} 项断言全部执行')
    if FAILURES:
        print('\n'.join(FAILURES))
        print(f'\n{len(FAILURES)} 项失败')
        sys.exit(1)
    print('全部通过：errors/warnings/items 逐字节等价（含顺序），兼容入口一致')


def check_registered_samples(samples):
    by_name = {s['name']: s for s in samples}
    sub = 0

    def expect(condition, message):
        nonlocal sub
        sub += 1
        if not condition:
            FAILURES.append(f'新金样结构断言失败：{message}')

    s45 = by_name.get('45_registered_system_aggregate')
    expect(s45 is not None, '缺少样例 45_registered_system_aggregate')
    if s45:
        r = s45['report']
        expect(r['errors'] == [] and r['warnings'] == [],
               '45 应为合法配置（0 错误 0 警告），实际 '
               f"{len(r['errors'])}e/{len(r['warnings'])}w")
        kinds = [(i['kind'], i['id']) for i in r['items']]
        expect(('registeredIdentity', 'System.identity') in kinds, '45 缺少 registeredIdentity 条目')
        expect(('aggregateSource', 'System.rated_capacity') in kinds, '45 缺少 aggregateSource 条目')
        expect(('membership', 'System.containsBattery') in kinds, '45 缺少 membership 条目')
        expect(all(i['status'] == 'valid' for i in r['items']), '45 全部条目应为 valid')
    s46 = by_name.get('46_registered_membership_variants')
    expect(s46 is not None, '缺少样例 46_registered_membership_variants')
    if s46:
        r = s46['report']
        expect((len(r['errors']), len(r['warnings']), len(r['items'])) == (25, 8, 15),
               '46 计数应为 25e/8w/15i，实际 '
               f"{len(r['errors'])}e/{len(r['warnings'])}w/{len(r['items'])}i")
        for text in ('实例登记 System：实例编号重复：SYS-001',
                     '对象映射 Station：未知的实例来源方式',
                     '属性来源 System.rated_capacity：空值策略必须为字符串 "null"',
                     '属性来源 System.system_note：聚合结果属性必须是数值属性',
                     '成员规则 containsBattery：按条件选择必须至少一条条件；如需整表请在范围中显式选择',
                     '成员规则 containsBattery：条件字段「ghost_col」不在终点对象身份表目录中',
                     '成员规则 containsBattery：条件取值与字段类型不兼容',
                     '成员规则 systemAlias：该数量关系不支持成员规则（集合端须出向一对多／多对多，或入向多对一／多对多）',
                     '成员规则 belongsToStation：成员规则链接的两端都不是当前对象',
                     '成员规则 ghostLink：引用的版本中不存在此链接类型'):
            expect(text in r['errors'], f'46 缺少错误文案：{text}')
        for text in ('属性来源 System.rated_capacity：尚未确认成员数值单位口径一致',
                     '属性来源 System.system_note：聚合引用的链接尚无成员规则',
                     '成员规则 containsBattery：该来源表全部有效记录将作为成员'):
            expect(text in r['warnings'], f'46 缺少警告文案：{text}')
        expect(any(i['kind'] == 'linkMapping' and i['status'] == 'unconfigured' for i in r['items']),
               '46 应保留无 kind 旧 relation 的原样校验（linkMapping unconfigured）')
    return sub


if __name__ == '__main__':
    main()

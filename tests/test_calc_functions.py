"""计算函数（kind=calculationFunction）：表达式求值器、函数校验、属性绑定校验回归。

隔离根 + 独立端口；试算为纯计算，不执行 SQL、不写草稿、不访问网络。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_tmp = tempfile.TemporaryDirectory(prefix='wiz_calc_fn_')
os.environ['WIZ_WORKBENCH_ROOT'] = _tmp.name
os.environ['WIZ_WORKBENCH_PORT'] = '18963'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from make_validation_golden import project, binding, QUERY_ONTOLOGY  # noqa: E402
from workbench import projects  # noqa: E402
from workbench.calc_functions import evaluate, check_function, validate_expression  # noqa: E402
from workbench.project_routes import post_calc_eval  # noqa: E402

# 账号体系（20260918）：域级测试需绑定测试账号作为当前用户
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent))
import auth_client as _auth_client
_auth_client.bind_fixture_user()

PT = {'inp_a': 'number', 'inp_b': 'number'}


def soc_fn():
    return {'kind': 'calculationFunction', 'schemaVersion': 1, 'id': 'cf-1', 'name': '计算 SOC',
            'description': '剩余电量与额定容量之比',
            'inputs': [{'id': 'inp_a', 'name': '剩余电量', 'type': 'number'},
                       {'id': 'inp_b', 'name': '额定容量', 'type': 'number'}],
            'implementation': {'language': 'calc-expression-1', 'expression': '{inp_a} / {inp_b} * 100'},
            'output': {'id': 'out_v', 'name': 'SOC', 'type': 'number'}}


def eval_ok(expr, values, out='number', pt=None):
    r = evaluate(expr, pt or PT, values, out)
    assert r['ok'], r
    return r['value']


def eval_err(expr, values, out='number', pt=None):
    r = evaluate(expr, pt or PT, values, out)
    assert not r['ok'], r
    return r['error']


class Evaluator(unittest.TestCase):
    def test_soc_example(self):
        self.assertEqual(eval_ok('{inp_a} / {inp_b} * 100', {'inp_a': 40, 'inp_b': 100}), 40)

    def test_lazy_if(self):
        expr = 'IF({inp_b} > 0, {inp_a} / {inp_b} * 100, ERROR("额定容量必须大于零"))'
        self.assertEqual(eval_ok(expr, {'inp_a': 40, 'inp_b': 100}), 40)
        err = eval_err(expr, {'inp_a': 40, 'inp_b': 0})
        self.assertIn('额定容量必须大于零', err)
        self.assertNotIn('除', err, '未选中的除法分支不得执行')

    def test_division_by_zero(self):
        self.assertIn('除', eval_err('{inp_a} / 0', {'inp_a': 1}))

    def test_missing_input(self):
        r = evaluate('{inp_a} + {inp_b}', PT, {'inp_a': 1}, 'number')
        self.assertFalse(r['ok'])
        self.assertIn('inp_b', r['error'])

    def test_static_type_errors(self):
        self.assertTrue(validate_expression('{inp_a} + {inp_b}', {'inp_a': 'string', 'inp_b': 'number'}, 'string'))
        self.assertTrue(validate_expression('ABS("x")', {}, 'number'))
        self.assertTrue(validate_expression('IF(1, 2, 3)', {}, 'number'), 'IF 条件必须为是/否')

    def test_min_max_abs_round(self):
        self.assertEqual(eval_ok('MIN({inp_a}, {inp_b})', {'inp_a': 3, 'inp_b': 5}), 3)
        self.assertEqual(eval_ok('MAX({inp_a}, {inp_b}, 10)', {'inp_a': 3, 'inp_b': 5}), 10)
        self.assertEqual(eval_ok('ABS(0 - {inp_a})', {'inp_a': 3}), 3)
        self.assertEqual(eval_ok('ROUND({inp_a} / {inp_b}, 2)', {'inp_a': 2, 'inp_b': 3}), 0.67)

    def test_rejects(self):
        self.assertTrue(validate_expression('1 +', PT, 'number'), '非法表达式')
        self.assertTrue(validate_expression('FOO({inp_a})', PT, 'number'), '非白名单函数')
        self.assertTrue(validate_expression('{inp_a}.x', PT, 'number'), '属性访问')
        self.assertTrue(validate_expression('x = 1', PT, 'number'), '赋值')
        self.assertTrue(validate_expression('a' * 501, PT, 'number'), '表达式过长')
        deep = '(-' * 25 + '1' + ')' * 25
        self.assertTrue(validate_expression(deep, PT, 'number'), '嵌套过深')
        self.assertIn('除', eval_err('0 / 0', {}), 'NaN 必须报错而非返回')
        self.assertIn('超出范围', evaluate('1e999', {}, {}, 'number')['error'], '字面量超界必须拒绝')
        self.assertFalse(evaluate('1e200 * 1e200', {}, {}, 'number')['ok'], 'Infinity 必须拒绝')

    def test_types_text_boolean(self):
        r = evaluate('IF({s} = "", "空", "非空")', {'s': 'string'}, {'s': ''}, 'string')
        self.assertEqual(r['value'], '空')
        r = evaluate('{a} = {b}', {'a': 'boolean', 'b': 'boolean'}, {'a': True, 'b': 1}, 'boolean')
        self.assertTrue(r['ok'])


class FunctionCheck(unittest.TestCase):
    def test_valid_function(self):
        self.assertEqual(check_function(soc_fn()), [])

    def test_invalid_function(self):
        bad = soc_fn()
        bad['inputs'][1]['name'] = '剩余电量'  # 参数名称重复
        errs = check_function(bad)
        self.assertTrue(any('重复' in e for e in errs), errs)
        bad = soc_fn(); bad['implementation']['expression'] = '{inp_ghost} + 1'
        errs = check_function(bad)
        self.assertTrue(any('inp_ghost' in e for e in errs), errs)
        bad = soc_fn(); bad['schemaVersion'] = 2
        self.assertTrue(any('版本' in e for e in check_function(bad)))


class TrialRoute(unittest.TestCase):
    def test_route_pure_eval(self):
        payload = {'expression': '{inp_a} / {inp_b} * 100',
                   'inputs': [{'id': 'inp_a', 'type': 'number', 'value': 40},
                              {'id': 'inp_b', 'type': 'number', 'value': 100}],
                   'outputType': 'number'}
        out, _ = post_calc_eval(payload)
        self.assertTrue(out['ok'])
        self.assertEqual(out['value'], 40)
        out, _ = post_calc_eval({'expression': '{inp_a}', 'inputs': [], 'outputType': 'number'})
        self.assertFalse(out['ok'])
        self.assertIn('inp_a', out['error'])


class BindingValidation(unittest.TestCase):
    def validate(self, bindings, implementations):
        return projects.validate_project(project(bindings=bindings, implementations=implementations), QUERY_ONTOLOGY)

    def prop_errors(self, report, prop='rated_power'):
        return [e for e in report['errors'] if e.startswith(f'属性来源 Station.{prop}')]

    def test_valid_binding(self):
        inputs = {'inp_a': {'from': 'property', 'property': 'temp_avg'},
                  'inp_b': {'from': 'constant', 'value': 100}}
        rep = self.validate([binding(properties={'rated_power': {'kind': 'computed', 'mode': 'calcFunction',
                                                                 'implementation': 'cf-1', 'output': 'out_v', 'inputs': inputs}})],
                            [soc_fn()])
        self.assertEqual(self.prop_errors(rep), [], rep['errors'])

    def test_missing_and_constant_values(self):
        base = lambda inputs: {'kind': 'computed', 'mode': 'calcFunction', 'implementation': 'cf-1',
                               'output': 'out_v', 'inputs': inputs}
        errs = self.prop_errors(self.validate([binding(properties={'rated_power': base({})})], [soc_fn()]))
        self.assertTrue(any('未绑定' in e and '剩余电量' in e for e in errs), errs)
        # A7：0 是明确填写的数值常量（有效）；false/'' 与数值参数不相容才是错误
        inputs = {'inp_a': {'from': 'constant', 'value': 0}, 'inp_b': {'from': 'constant', 'value': 100}}
        self.assertEqual(self.prop_errors(self.validate([binding(properties={'rated_power': base(inputs)})], [soc_fn()])), [])
        for bad in (False, ''):
            inputs = {'inp_a': {'from': 'constant', 'value': bad}, 'inp_b': {'from': 'constant', 'value': 100}}
            errs = self.prop_errors(self.validate([binding(properties={'rated_power': base(inputs)})], [soc_fn()]))
            self.assertTrue(any('固定值无效' in e for e in errs), (bad, errs))
        inputs = {'inp_a': {'from': 'constant', 'value': None}, 'inp_b': {'from': 'constant', 'value': 100}}
        errs = self.prop_errors(self.validate([binding(properties={'rated_power': base(inputs)})], [soc_fn()]))
        self.assertTrue(any('固定值' in e for e in errs), errs)

    def test_output_and_type_mismatch(self):
        inputs = {'inp_a': {'from': 'property', 'property': 'temp_avg'}, 'inp_b': {'from': 'constant', 'value': 100}}
        bad = {'kind': 'computed', 'mode': 'calcFunction', 'implementation': 'cf-1', 'output': 'ghost', 'inputs': inputs}
        errs = self.prop_errors(self.validate([binding(properties={'rated_power': bad})], [soc_fn()]))
        self.assertTrue(any('输出' in e for e in errs), errs)
        # 时序属性不能绑定单值函数输出
        bad2 = {'kind': 'computed', 'mode': 'calcFunction', 'implementation': 'cf-1', 'output': 'out_v', 'inputs': inputs}
        errs = self.prop_errors(self.validate([binding(properties={'soc': bad2})], [soc_fn()]), prop='soc')
        self.assertTrue(any('时间序列' in e or '不匹配' in e for e in errs), errs)

    def test_input_property_type_mismatch(self):
        inputs = {'inp_a': {'from': 'property', 'property': 'station_name'}, 'inp_b': {'from': 'constant', 'value': 100}}
        base = {'kind': 'computed', 'mode': 'calcFunction', 'implementation': 'cf-1', 'output': 'out_v', 'inputs': inputs}
        errs = self.prop_errors(self.validate([binding(properties={'rated_power': base})], [soc_fn()]))
        self.assertTrue(any('station_name' in e or '类型' in e for e in errs), errs)

    def test_cycles(self):
        # 直接：SOC → SOC
        inputs = {'inp_a': {'from': 'property', 'property': 'rated_power'}, 'inp_b': {'from': 'constant', 'value': 100}}
        base = {'kind': 'computed', 'mode': 'calcFunction', 'implementation': 'cf-1', 'output': 'out_v', 'inputs': inputs}
        errs = self.prop_errors(self.validate([binding(properties={'rated_power': base})], [soc_fn()]))
        self.assertTrue(any('循环' in e for e in errs), errs)
        # 间接：rated_power → temp_avg → rated_power
        fn2 = soc_fn(); fn2['id'] = 'cf-2'; fn2['inputs'] = [{'id': 'inp_a', 'name': 'a', 'type': 'number'}]
        fn2['implementation'] = {'language': 'calc-expression-1', 'expression': '{inp_a} * 2'}
        b1 = {'kind': 'computed', 'mode': 'calcFunction', 'implementation': 'cf-1', 'output': 'out_v',
              'inputs': {'inp_a': {'from': 'property', 'property': 'temp_avg'}, 'inp_b': {'from': 'constant', 'value': 100}}}
        b2 = {'kind': 'computed', 'mode': 'calcFunction', 'implementation': 'cf-2', 'output': 'out_v',
              'inputs': {'inp_a': {'from': 'property', 'property': 'rated_power'}}}
        rep = self.validate([binding(properties={'rated_power': b1, 'temp_avg': b2})], [soc_fn(), fn2])
        errs = [e for e in rep['errors'] if '循环' in e]
        self.assertTrue(len(errs) >= 1, rep['errors'])

    def test_stale_references(self):
        # A10：函数删除输入 / 改输出 id / 整个函数删除
        trimmed = soc_fn(); trimmed['inputs'] = [trimmed['inputs'][0]]  # 删除 inp_b（仍被公式引用→函数本身无效）
        inputs = {'inp_a': {'from': 'constant', 'value': 1}, 'inp_b': {'from': 'constant', 'value': 100}}
        base = {'kind': 'computed', 'mode': 'calcFunction', 'implementation': 'cf-1', 'output': 'out_v', 'inputs': inputs}
        rep = self.validate([binding(properties={'rated_power': base})], [trimmed])
        errs = self.prop_errors(rep)
        self.assertTrue(any('inp_b' in e or '不存在' in e for e in errs), rep['errors'])
        rep = self.validate([binding(properties={'rated_power': base})], [])
        self.assertTrue(any('不存在' in e for e in self.prop_errors(rep)), rep['errors'])

    def test_roundtrip_preserved(self):
        fn = soc_fn()
        inputs = {'inp_a': {'from': 'property', 'property': 'temp_avg'}, 'inp_b': {'from': 'constant', 'value': 100}}
        prop = {'kind': 'computed', 'mode': 'calcFunction', 'implementation': 'cf-1', 'output': 'out_v', 'inputs': inputs}
        state = project(bindings=[binding(properties={'rated_power': prop})], implementations=[fn])
        projects.save_draft(state)
        loaded, _ = projects.load(state['projectId'])
        self.assertEqual(loaded['implementations'][0], fn)
        self.assertEqual(loaded['bindings']['object_bindings'][0]['properties']['rated_power'], prop)

    def test_demo_excluded(self):
        from workbench.model_routes import merged
        fn = soc_fn()
        inputs = {'inp_a': {'from': 'property', 'property': 'temp_avg'}, 'inp_b': {'from': 'constant', 'value': 100}}
        prop = {'kind': 'computed', 'mode': 'calcFunction', 'implementation': 'cf-1', 'output': 'out_v', 'inputs': inputs}
        out = merged(QUERY_ONTOLOGY, project(bindings=[binding(properties={'rated_power': prop})], implementations=[fn]))
        self.assertNotIn('rated_power', out['bindings']['object_bindings'][0]['properties'])


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        _tmp.cleanup()

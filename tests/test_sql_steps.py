"""V4 sqlSteps 取值规则校验与保存往返回归（隔离根 + 独立端口，不执行 SQL）。"""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

_tmp = tempfile.TemporaryDirectory(prefix='wiz_sql_steps_')
os.environ['WIZ_WORKBENCH_ROOT'] = _tmp.name
os.environ['WIZ_WORKBENCH_PORT'] = '18961'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from make_validation_golden import project, binding, QUERY_ONTOLOGY  # noqa: E402
from workbench import projects  # noqa: E402
from workbench.query_rules import query_rule_errors, rule_input_errors, sql_steps_errors  # noqa: E402


def soc_v4(rule_id='rule-v4'):
    return {
        'id': rule_id, 'kind': 'queryRule', 'schemaVersion': 4, 'mode': 'sqlSteps',
        'name': '采样值查询', 'connection': 'db',
        'inputs': [
            {'name': 'model_name', 'type': 'string', 'source': 'binding'},
            {'name': 'attr_name', 'type': 'string', 'source': 'binding'},
            {'name': 'model_id', 'type': 'string', 'source': 'binding'},
            {'name': 'startTime', 'type': 'dateTime', 'source': 'runtime'},
            {'name': 'endTime', 'type': 'dateTime', 'source': 'runtime'}],
        'steps': [
            {'id': 's1', 'key': 'point', 'name': '定位测点', 'cardinality': 'one',
             'sql': 'SELECT scada_table, scada_id FROM s_attr_scada WHERE model_name = :model_name AND attr_name = :attr_name AND model_id = :model_id'},
            {'id': 's2', 'key': 'storage', 'name': '定位采样存储', 'cardinality': 'one',
             'sql': 'SELECT sample_table_name, sample_field_name FROM {{point.scada_table}} WHERE id = :point.scada_id'},
            {'id': 's3', 'key': 'samples', 'name': '读取采样记录', 'cardinality': 'many',
             'sql': 'SELECT record_time AS timestamp, {{storage.sample_field_name}} AS value FROM {{storage.sample_table_name}} WHERE record_time >= :startTime AND record_time < :endTime ORDER BY record_time ASC'}],
        'result': {'step': 's3', 'type': 'timeSeries', 'valueType': 'double', 'value': 'value', 'timestamp': 'timestamp'},
        'extensions': {'vendor': 'kept'}}


CTX = {'conn_by_id': {'db': {'engine': 'mysql'}}}


class SqlSteps(unittest.TestCase):
    def test_valid_soc_rule(self):
        self.assertEqual(sql_steps_errors(soc_v4(), CTX), [])

    def test_save_roundtrip_keeps_fields(self):
        rule = soc_v4()
        state = project(bindings=[binding(properties={'soc': {'kind': 'computed', 'implementation': rule['id'],
                                                            'output': 'series',
                                                            'inputs': {'model_name': 'x', 'attr_name': 'soc', 'model_id': '{id}'}}})],
                        implementations=[rule])
        projects.save_draft(state)
        loaded, _ = projects.load(state['projectId'])
        self.assertEqual(loaded['implementations'], [rule])
        self.assertEqual(sql_steps_errors(loaded['implementations'][0], CTX), [])

    def test_forward_reference(self):
        rule = soc_v4()
        rule['steps'][1]['sql'] = 'SELECT x FROM {{samples.sample_table_name}}'
        errs = sql_steps_errors(rule, CTX)
        self.assertTrue(any('samples.sample_table_name' in e and '定位采样存储' in e for e in errs), errs)

    def test_many_step_cannot_be_value_source(self):
        rule = soc_v4()
        rule['steps'][1]['sql'] = 'SELECT x FROM t WHERE id = :samples.value'
        errs = sql_steps_errors(rule, CTX)
        self.assertTrue(any('samples.value' in e for e in errs), errs)

    def test_scalar_result_requires_one_step(self):
        rule = soc_v4()
        rule['result'] = {'step': 's3', 'type': 'scalar', 'valueType': 'double', 'value': 'value'}
        errs = sql_steps_errors(rule, CTX)
        self.assertTrue(any('单值返回' in e for e in errs), errs)

    def test_duplicate_key(self):
        rule = soc_v4()
        rule['steps'][2]['key'] = 'storage'
        errs = sql_steps_errors(rule, CTX)
        self.assertTrue(any('步骤技术名无效或重复' in e for e in errs), errs)

    def test_with_and_multi_statement(self):
        rule = soc_v4()
        rule['steps'][0]['sql'] = 'WITH a AS (SELECT 1) SELECT * FROM a'
        self.assertTrue(any('WITH' in e for e in sql_steps_errors(rule, CTX)))
        rule = soc_v4()
        rule['steps'][0]['sql'] = 'SELECT 1 AS a; SELECT 2 AS b'
        self.assertTrue(any('一条 SELECT' in e for e in sql_steps_errors(rule, CTX)))

    def test_string_and_comment_noise_not_flagged(self):
        rule = soc_v4()
        rule['steps'][0]['sql'] = ("SELECT scada_table, scada_id FROM s_attr_scada "
                                   "WHERE note = '时间 12:30 分号;伪引用 :fake -- @step fake one' "
                                   "AND remark = \"双引号:xx\" "
                                   "-- 注释里的 :missing 与 {{ghost.col}}\n"
                                   "/* 块注释 ; :nope */ AND model_id = :model_id")
        self.assertEqual(sql_steps_errors(rule, CTX), [])

    def test_result_step_missing(self):
        rule = soc_v4()
        rule['result']['step'] = 'ghost'
        self.assertTrue(any('返回步骤不存在' in e for e in sql_steps_errors(rule, CTX)))

    def test_output_not_last_hint(self):
        rule = soc_v4()
        rule['steps'].append({'id': 's4', 'key': 'extra', 'name': '多余步骤', 'cardinality': 'one', 'sql': 'SELECT 1 AS v'})
        rule['result'] = {'step': 's3', 'type': 'timeSeries', 'valueType': 'double', 'value': 'value', 'timestamp': 'timestamp'}
        errs = sql_steps_errors(rule, CTX)
        self.assertEqual(errs, [], '输出步骤非最后仅为界面提示，不构成校验错误')

    def test_rule_input_errors_v4(self):
        rule = soc_v4()
        self.assertEqual(rule_input_errors({'model_name': 'x', 'attr_name': 'soc', 'model_id': '{id}'}, {}, rule), [])
        self.assertEqual(rule_input_errors({'model_name': 'x'}, {}, rule), ['请填写输入参数 attr_name', '请填写输入参数 model_id'])

    def test_dispatch_and_mode(self):
        rule = soc_v4()
        self.assertEqual(query_rule_errors(rule, CTX), [])
        rule['mode'] = 'wrong'
        self.assertTrue(any('版本不受支持' in e for e in query_rule_errors(rule, CTX)))

    def test_many_step_duplicate_key(self):
        rule = soc_v4()
        rule['steps'].append({'id': 's5', 'key': 'samples', 'name': '再采样', 'cardinality': 'many',
                              'sql': 'SELECT 2 AS v FROM t'})
        errs = sql_steps_errors(rule, CTX)
        self.assertTrue(any('步骤「再采样」：步骤技术名无效或重复' in e for e in errs), errs)

    def test_duplicate_step_id(self):
        rule = soc_v4()
        rule['steps'][1]['id'] = rule['steps'][0]['id']
        errs = sql_steps_errors(rule, CTX)
        self.assertTrue(any('步骤「定位采样存储」：步骤标识重复' in e for e in errs), errs)

    def test_missing_step_id(self):
        rule = soc_v4()
        rule['steps'][1]['id'] = ''
        errs = sql_steps_errors(rule, CTX)
        self.assertTrue(any('步骤「定位采样存储」：步骤标识缺失' in e for e in errs), errs)

    def test_invalid_cardinality(self):
        rule = soc_v4()
        rule['steps'][1]['cardinality'] = 'single'
        errs = sql_steps_errors(rule, CTX)
        self.assertTrue(any('步骤「定位采样存储」：预期记录数无效' in e for e in errs), errs)

    def test_uuid_step_id_accepted(self):
        rule = soc_v4()
        rule['steps'][1]['id'] = '5f8f9c2e-6c6e-4bd9-9d2a-1b2a3c4d5e6f'
        self.assertEqual(sql_steps_errors(rule, CTX), [])


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        _tmp.cleanup()

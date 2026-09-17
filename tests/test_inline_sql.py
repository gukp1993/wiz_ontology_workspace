"""属性内 SQL 取值（computed/mode=inlineSql）：协议往返、校验、demo 隔离回归。

隔离根 + 独立端口；只存模板不执行 SQL。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_tmp = tempfile.TemporaryDirectory(prefix='wiz_inline_sql_')
os.environ['WIZ_WORKBENCH_ROOT'] = _tmp.name
os.environ['WIZ_WORKBENCH_PORT'] = '18962'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from make_validation_golden import project, binding, QUERY_ONTOLOGY  # noqa: E402
from workbench import projects  # noqa: E402
from workbench.model_routes import merged  # noqa: E402

PARAMS = {'park_id': 'P001'}


def inline_config(params=None, sql="SELECT SUM(rated_power) AS value FROM m_storage_phase WHERE park_id_column = :park_id",
                  connection='mysql_main', **extra):
    value = {'kind': 'computed', 'mode': 'inlineSql',
             'inlineSql': {'version': 1, 'connection': connection, 'sql': sql, 'params': params or {}}}
    value.update(extra)
    return value


def state_of(properties, parameters=PARAMS):
    return project(bindings=[binding(properties=properties)], parameters=parameters)


def prop_errors(state, obj='Station', prop='rated_power'):
    return [e for e in validate(state)['errors'] if e.startswith(f'属性来源 {obj}.{prop}')]


def validate(state):
    return projects.validate_project(state, QUERY_ONTOLOGY)


class InlineSqlProtocol(unittest.TestCase):
    def test_roundtrip_preserves_inline(self):
        value = inline_config(params={'park_id': {'from': 'projectParameter', 'key': 'park_id'}})
        state = state_of({'rated_power': value})
        projects.save_draft(state)
        loaded, _ = projects.load(state['projectId'])
        self.assertEqual(loaded['bindings']['object_bindings'][0]['properties']['rated_power'], value)

    def test_unknown_version_preserved_with_error(self):
        value = inline_config()
        value['inlineSql']['version'] = 2
        state = state_of({'rated_power': value})
        projects.save_draft(state)
        loaded, _ = projects.load(state['projectId'])
        self.assertEqual(loaded['bindings']['object_bindings'][0]['properties']['rated_power'], value, '未知版本零丢失')
        errs = prop_errors(state)
        self.assertTrue(any('版本不受支持' in e for e in errs), errs)

    def test_malformed_inline_preserved_with_error(self):
        value = {'kind': 'computed', 'mode': 'inlineSql', 'inlineSql': {'version': 1, 'sql': 'SELECT 1'}}
        errs = prop_errors(state_of({'rated_power': value}))
        self.assertTrue(any('结构无效' in e or '数据连接不存在' in e for e in errs), errs)

    def test_mutual_exclusion(self):
        value = inline_config(implementation='impl-1', output='series')
        errs = prop_errors(state_of({'rated_power': value}))
        self.assertTrue(any('不能同时配置' in e for e in errs), errs)

    def test_valid_no_param_sum(self):
        value = inline_config(params=None, sql='SELECT SUM(rated_power) AS value FROM m_storage_phase')
        self.assertEqual(prop_errors(state_of({'rated_power': value})), [])

    def test_valid_project_parameter(self):
        value = inline_config(params={'park_id': {'from': 'projectParameter', 'key': 'park_id'}},
                              sql='SELECT SUM(rated_power) AS value FROM m WHERE park = :park_id')
        self.assertEqual(prop_errors(state_of({'rated_power': value})), [])

    def test_missing_connection_and_engine(self):
        errs = prop_errors(state_of({'rated_power': inline_config(connection='ghost')}))
        self.assertTrue(any('数据连接不存在' in e for e in errs), errs)
        errs = prop_errors(state_of({'rated_power': inline_config(connection='redis_main')}))
        self.assertTrue(any('MySQL' in e for e in errs), errs)

    def test_sql_empty_is_pending(self):
        report = validate(state_of({'rated_power': inline_config(sql='   ')}))
        items = [i for i in report['items'] if i['id'] == 'Station.rated_power']
        self.assertTrue(items and any('未填写 SQL 模板' in i for i in items[0]['issues']), report['items'])
        self.assertEqual(items[0]['status'], 'unconfigured')

    def test_with_multi_and_dynamic(self):
        for fragment, expect in [('WITH a AS (SELECT 1) SELECT * FROM a', 'WITH'),
                                 ('SELECT 1; SELECT 2', '一条 SELECT'),
                                 ('SELECT x FROM {{steps.a.b}}', '动态表名')]:
            value = inline_config(sql=fragment)
            errs = prop_errors(state_of({'rated_power': value}))
            self.assertTrue(any(expect in e for e in errs), (expect, errs))

    def test_param_bindings(self):
        base = 'SELECT :p FROM t'
        errs = prop_errors(state_of({'rated_power': inline_config(sql=base, params={})}))
        self.assertTrue(any('参数 :p 未绑定取值' in e for e in errs), errs)
        errs = prop_errors(state_of({'rated_power': inline_config(sql=base, params={'p': {'from': 'magic'}})}))
        self.assertTrue(any('绑定来源无效' in e for e in errs), errs)
        errs = prop_errors(state_of({'rated_power': inline_config(sql=base, params={'p': {'from': 'projectParameter', 'key': 'ghost'}})}))
        self.assertTrue(any('ghost' in e and '不存在或已失效' in e for e in errs), errs)
        # 常量 0/false/明确空字符串有效；空数值、非数数值、未知类型无效
        for ok in [{'from': 'constant', 'dataType': 'double', 'value': '0'},
                   {'from': 'constant', 'dataType': 'boolean', 'value': 'false'},
                   {'from': 'constant', 'dataType': 'string', 'value': ''}]:
            self.assertEqual(prop_errors(state_of({'rated_power': inline_config(sql=base, params={'p': ok})})), [], ok)
        for bad in [{'from': 'constant', 'dataType': 'double', 'value': ''},
                    {'from': 'constant', 'dataType': 'double', 'value': 'abc'},
                    {'from': 'constant', 'dataType': 'color', 'value': 'x'}]:
            errs = prop_errors(state_of({'rated_power': inline_config(sql=base, params={'p': bad})}))
            self.assertTrue(errs, bad)

    def test_instance_id_on_database_identity(self):
        base = 'SELECT :p FROM t'
        errs = prop_errors(state_of({'rated_power': inline_config(sql=base, params={'p': {'from': 'instanceId'}})}))
        self.assertEqual(errs, [], 'Station 有实例主键，实例编号可用')

    def test_registered_object_can_use_inline(self):
        """A6：无来源表的登记对象也能配置内联 SQL 与项目参数。"""
        reg = binding(object_type='Battery', connection='', table='', primary_key='',
                      identity={'kind': 'registered', 'instances': [{'id': 'B1', 'label': '电池一'}]},
                      properties={'capacity': inline_config(sql='SELECT :p AS value FROM dual',
                                                            params={'p': {'from': 'projectParameter', 'key': 'park_id'}})})
        state = project(bindings=[reg], parameters=PARAMS)
        errs = [e for e in validate(state)['errors'] if e.startswith('属性来源 Battery.capacity')]
        self.assertEqual(errs, [], errs)

    def test_merged_keeps_inline_out_of_demo(self):
        value = inline_config(params={'park_id': {'from': 'projectParameter', 'key': 'park_id'}})
        state = state_of({'rated_power': value})
        out = merged(QUERY_ONTOLOGY, state)
        props = out['bindings']['object_bindings'][0]['properties']
        self.assertNotIn('rated_power', props, '内联配置不得进入演示引擎输入')
        unsupported = out['bindings']['unsupportedSources']
        self.assertTrue(any(u['property'] == 'rated_power' and u['kind'] == 'computed' for u in unsupported), unsupported)

    def test_time_series_property_accepted(self):
        value = inline_config(sql='SELECT ts, soc AS value FROM s WHERE ts >= :startTime ORDER BY ts ASC',
                              params={'startTime': {'from': 'instanceId'}})
        self.assertEqual(prop_errors(state_of({'soc': value}), prop='soc'), [])


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        _tmp.cleanup()

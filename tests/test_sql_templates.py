"""Isolated SQL template storage and validation regression."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
_tmp = tempfile.TemporaryDirectory(prefix='wiz_sql_')
os.environ['WIZ_WORKBENCH_ROOT'] = _tmp.name
os.environ['WIZ_WORKBENCH_PORT'] = '18993'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from make_validation_golden import project, V3_SCALAR_RULE
from workbench import projects
from workbench.query_rules import query_rule_errors
import copy

class SQLTemplates(unittest.TestCase):
    def test_roundtrip_and_invalid_reference(self):
        rule = copy.deepcopy(V3_SCALAR_RULE)
        rule.update(mode='sqlTemplate', sqlTemplate='-- @step query one\nSELECT rated_power AS value FROM station WHERE id = :model_id;')
        del rule['steps']
        ctx={'conn_by_id':{rule['connection']:{'engine':'mysql'}}}
        self.assertEqual(query_rule_errors(rule,ctx), [])
        state=project(implementations=[rule])
        projects.save_draft(state)
        loaded,_=projects.load(state['projectId'])
        self.assertEqual(loaded['implementations'],[rule])
        rule['sqlTemplate']=rule['sqlTemplate'].replace(':model_id', ':missing')
        self.assertTrue(any('missing' in e for e in query_rule_errors(rule,ctx)))

if __name__ == '__main__':
    try: unittest.main()
    finally: _tmp.cleanup()

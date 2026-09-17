"""独立规则无需契约，保存往返不丢字段，错误规则阻止发布。"""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TEMP = tempfile.TemporaryDirectory(prefix='wiz_query_rules_')
os.environ['WIZ_WORKBENCH_ROOT'] = _TEMP.name
os.environ['WIZ_WORKBENCH_PORT'] = '18994'
from make_validation_golden import project, binding, QUERY_RULE, QUERY_ONTOLOGY, REUSABLE_QUERY_RULE, V3_QUERY_RULE, V3_SCALAR_RULE
from workbench import projects, project_routes

class QueryRules(unittest.TestCase):
    def state(self):
        rule=copy.deepcopy(QUERY_RULE)
        return project(bindings=[binding(properties={'soc':{'kind':'computed','implementation':rule['id'],'output':'series'}})],implementations=[rule])
    def test_valid_without_contract(self):
        r=projects.validate_project(self.state(),QUERY_ONTOLOGY)
        self.assertFalse(r['errors'],r)
        self.assertEqual(next(i for i in r['items'] if i['kind']=='implementation')['status'],'valid')
    def test_invalid_definitions_block_publish(self):
        for mutate in [lambda r:r['steps'][0]['where'][2].update(value='1'),lambda r:r['steps'][1].update(table='{{steps.samples.value}}'),lambda r:r['result'].update(timestamp='missing'),lambda r:r.update(connection='missing')]:
            state=self.state();mutate(state['implementations'][0]);state=projects._normalize(state)
            with patch.object(projects,'load',return_value=(state,None)),patch.object(project_routes,'referenced_ontology',return_value=QUERY_ONTOLOGY),patch.object(project_routes.catalog_store,'load_all',return_value={}),patch.object(projects,'publish') as publish:
                out,status=project_routes.post_project_write({'state':state,'revision':projects.revision(state)},'/api/project-publish')
                self.assertEqual(status,422,out);publish.assert_not_called()
    def test_reusable_roundtrip_and_missing_inputs(self):
        rule=copy.deepcopy(REUSABLE_QUERY_RULE)
        state=project(bindings=[binding(properties={'soc':{'kind':'computed','implementation':rule['id'],'output':'series','inputs':{'model_name':'station','attr_name':'soc','model_id':'{id}'}}})],implementations=[rule])
        self.assertFalse(projects.validate_project(copy.deepcopy(state),QUERY_ONTOLOGY)['errors'])
        result=projects.save_draft(state);self.assertIn('revision',result)
        loaded,_=projects.load(state['projectId'])
        self.assertEqual(loaded['bindings']['object_bindings'][0]['properties']['soc']['inputs'],state['bindings']['object_bindings'][0]['properties']['soc']['inputs'])
        state['bindings']['object_bindings'][0]['properties']['soc']['inputs'].pop('attr_name')
        self.assertTrue(any('attr_name' in e for e in projects.validate_project(state,QUERY_ONTOLOGY)['errors']))
    def test_v3_roundtrip_and_dependencies(self):
        for fixture in (V3_QUERY_RULE, V3_SCALAR_RULE):
            state=project(implementations=[copy.deepcopy(fixture)])
            result=projects.save_draft(state)
            loaded,_=projects.load(state['projectId'])
            self.assertEqual(loaded['implementations'],state['implementations'])
            self.assertIn('revision',result)
        from workbench.query_rules import structured_rule_errors
        rule=copy.deepcopy(V3_QUERY_RULE)
        ctx={'conn_by_id':{rule['connection']:{'engine':'mysql'}}}
        self.assertEqual(structured_rule_errors(rule,ctx),[])
        rule['steps'][0]['select'].pop(0)
        self.assertTrue(any('引用' in e for e in structured_rule_errors(rule,ctx)))

    def test_roundtrip(self):
        state=projects._normalize(self.state())
        state['implementations'][0]['extensions']={'vendor':'keep-me'}
        result=projects.save_draft(state)
        self.assertIn('revision',result)
        loaded,_=projects.load(state['projectId'])
        self.assertEqual(loaded['implementations'],state['implementations'])
        self.assertEqual(loaded['bindings']['object_bindings'][0]['properties'],state['bindings']['object_bindings'][0]['properties'])

if __name__=='__main__':
    try:unittest.main()
    finally:_TEMP.cleanup()

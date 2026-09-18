"""身份来源必填项阻止发布，但允许保存未完成草稿（隔离内存路由测试）。"""
import copy
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TEMP = tempfile.TemporaryDirectory(prefix='wiz_identity_required_')
os.environ['WIZ_WORKBENCH_ROOT'] = _TEMP.name
os.environ['WIZ_WORKBENCH_PORT'] = '18994'
from make_validation_golden import project, binding, ontology_state
from workbench import projects, project_routes

class IdentityRequired(unittest.TestCase):
    def test_each_required_field_blocks_publish_but_can_save(self):
        for key in ('connection', 'table', 'primary_key'):
            for value in ('', '   '):
                with self.subTest(key=key, value=value):
                    state = projects._normalize(project(bindings=[binding(**{key:value})]))
                    report = projects.validate_project(copy.deepcopy(state), ontology_state())
                    self.assertTrue(report['errors'])
                    item = next(x for x in report['items'] if x['kind']=='objectBinding')
                    self.assertNotEqual(item['status'], 'valid')
                    with patch.object(projects, 'load', return_value=(state, None)), \
                         patch.object(project_routes, 'referenced_ontology', return_value=ontology_state()), \
                         patch.object(project_routes.catalog_store, 'load_all', return_value={}), \
                         patch.object(projects, 'current_token', return_value='tok-test'), \
                         patch.object(projects, 'save_draft', return_value={'revision':'saved'}) as save, \
                         patch.object(projects, 'publish') as publish:
                        payload={'state':copy.deepcopy(state),'revision':'tok-test'}
                        out, status=project_routes.post_project_write(payload,'/api/project-publish')
                        self.assertEqual(status,422,out)
                        save.assert_not_called();publish.assert_not_called()
                        _, status=project_routes.post_project_write(payload,'/api/project-save')
                        self.assertEqual(status,200)
                        save.assert_called_once()
    def test_complete_identity_passes(self):
        report=projects.validate_project(project(bindings=[binding()]),ontology_state())
        self.assertFalse(report['errors'],report)
        self.assertEqual(next(i for i in report['items'] if i['kind']=='objectBinding')['status'],'valid')

if __name__=='__main__':
    try: unittest.main()
    finally: _TEMP.cleanup()

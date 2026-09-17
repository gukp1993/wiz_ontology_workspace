"""Unified time-series type: protocol parity, migration, shared refs and contract outputs."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workbench import model_format, contracts, properties


def fixture():
    return {'ontology': {'schemaVersion': 1, 'namespaces': {'mg': 'https://example.com/microgrid/'},
        'objectTypes': [{'id': 'mg:Cluster', 'displayName': '储能簇'}], 'linkTypes': [],
        'sharedProperties': [{'id': 'mg:shared', 'displayName': 'SOC序列', 'description': '电池的剩余电量百分比及采样时间',
                              'dataType': {'type': 'timeSeries', 'valueType': 'double'},
                              'extensions': {'mg:customNote': 'keep'}}],
        'properties': [{'id': 'mg:soc', 'apiName': 'soc', 'objectTypeId': 'mg:Cluster', 'sharedPropertyId': 'mg:shared'}],
        'valueTypes': [], 'metadata': [], 'definitionOrder': ['mg:Cluster', 'mg:shared', 'mg:soc']},
        'workflow': {'functions': []}}


class TimeSeriesTypeTest(unittest.TestCase):
    def test_schema_and_legacy_semantically_equivalent(self):
        new = fixture()
        old = copy.deepcopy(new)
        old['ontology']['sharedProperties'][0].update(dataType={'type': 'double'}, valueShape='timeSeries')
        self.assertEqual(model_format.decode_state(old), model_format.decode_state(new))
        self.assertEqual(model_format.encode_state(model_format.decode_state(old)), new)
        self.assertEqual(contracts.classify(old, new)['reasons'], [])
        changed = copy.deepcopy(new)
        changed['ontology']['sharedProperties'][0]['dataType']['valueType'] = 'string'
        self.assertEqual(contracts.classify(new, changed)['type'], 'breaking')
        changed['ontology']['sharedProperties'][0]['dataType'] = {'type': 'double'}
        self.assertEqual(contracts.classify(new, changed)['type'], 'breaking')

    def test_shared_type_and_signature_are_inherited(self):
        state = model_format.decode_state(fixture())
        graph = state['ontology']['@graph']
        p = next(p for p in graph if p['@id'] == 'mg:soc')
        self.assertEqual(properties.data_type(p, graph), {'type': 'timeSeries', 'valueType': 'double'})
        self.assertEqual(properties.validate_properties(graph), [])
        ref = {'kind': 'property', 'id': 'mg:soc'}
        self.assertEqual(properties.signature_data_type(ref, graph), {'type': 'timeSeries', 'valueType': 'double'})
        self.assertEqual(properties.signature_data_type({'kind':'base','dataType':'timeSeries','valueType':'string'}, graph),
                         {'type': 'timeSeries', 'valueType': 'string'})

    def test_invalid_types_rejected_without_loss(self):
        for dtype in ({'type':'timeSeries'}, {'type':'timeSeries','valueType':'array'},
                      {'type':'timeSeries','valueType':{'type':'double'}},
                      {'type':'double','valueType':'double'}):
            with self.subTest(dtype=dtype):
                s = fixture()
                s['ontology']['sharedProperties'][0]['dataType'] = dtype
                with self.assertRaises(ValueError):
                    model_format.validate_json(s['ontology'])
        s = fixture()
        s['ontology']['sharedProperties'][0]['valueShape'] = 'timeSeries'
        with self.assertRaises(ValueError):model_format.validate_json(s['ontology'])

    def test_typescript_python_protocol_and_editor_parity(self):
        script = """
import fs from 'node:fs';
import {decodeState,encodeState} from './frontend/src/ontology/modelFormat.ts';
import {propertyDataType,setPropertyDataType,propertyTypeLabel,signatureDataType} from './frontend/src/ontology/propertyModel.ts';
const original=JSON.parse(fs.readFileSync(0,'utf8')),state=decodeState(original),g=state.ontology['@graph'];
const p=g.find(p=>p['@id']==='mg:soc'),shared=g.find(p=>p['@id']==='mg:shared');
const before=encodeState(state),type=propertyDataType(p,g),label=propertyTypeLabel(p,g);
setPropertyDataType(shared,{type:'timeSeries',valueType:'string'});
const inherited=signatureDataType({kind:'property',id:p['@id']},g);
const changed=encodeState(state);
setPropertyDataType(shared,{type:'double'});
console.log(JSON.stringify({before,type,label,inherited,changed,scalar:encodeState(state)}));
"""
        r = subprocess.run(['node','--import','./tests/ts_hooks.mjs','--input-type=module','-e',script],
                           cwd=ROOT,input=json.dumps(fixture()),text=True,capture_output=True,check=True)
        d = json.loads(r.stdout)
        self.assertEqual(d['before'], fixture())
        self.assertEqual(d['type'], {'type':'timeSeries','valueType':'double'})
        self.assertEqual(d['label'], '时间序列（数值）')
        self.assertEqual(d['inherited'], {'type':'timeSeries','valueType':'string'})
        self.assertEqual(model_format.encode_state(model_format.decode_state(d['changed'])),d['changed'])
        self.assertEqual(d['scalar']['ontology']['sharedProperties'][0]['dataType'], {'type':'double'})
        self.assertNotIn('valueShape', d['scalar']['ontology']['sharedProperties'][0])

    def test_computed_source_uses_contract_type_without_shape_declaration(self):
        # Reuse the established isolated source-validation harness.
        from test_property_sources import base_graph, validate, contract_and_impl
        graph = base_graph(soc_series=True)
        c, impl = contract_and_impl()
        c['outputs'][0]['ref'] = {'kind':'base','dataType':'timeSeries','valueType':'double'}
        source = {'kind':'computed','implementation':'impl1','output':'series'}
        result = validate({'soc': source}, graph=graph, functions=[c], implementations=[impl])
        self.assertEqual(result['errors'], [])
        c['outputs'][0]['ref'] = {'kind':'base','dataType':'double'}
        result = validate({'soc': source}, graph=graph, functions=[c], implementations=[impl])
        self.assertTrue(any('函数输出数据类型' in e for e in result['errors']))


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='wiz_type_tests_') as root:
        os.environ['WIZ_WORKBENCH_ROOT'] = root
        unittest.main()

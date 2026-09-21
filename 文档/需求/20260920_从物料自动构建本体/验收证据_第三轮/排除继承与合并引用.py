"""Read-only pure-domain regression probes; mocked store, no service or user data."""
import sys
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from workbench.ontology_build import pipeline, review, ontology_adapter as adapter

def candidate(identifier, key, kind, **kwargs):
    return dict(id=identifier, key=key, type=kind, name=key, definition='definition',
                decision='include', origin={}, batchId='b', fields={}, **kwargs)

# D04: automatic deferred exclusion must remain protected after another regeneration.
b1 = {'alignedKey': 'property:soc#number@device', 'decision': 'exclude'}
b2 = {'alignedKey': b1['alignedKey'], 'decision': 'include'}
with patch.object(pipeline.store, 'list_batches', return_value=[{'id': 'b2'}, {'id': 'b1'}]), \
     patch.object(pipeline.store, 'all_candidates', return_value=[b1]):
    print('b2 protection:', pipeline.inherit_manual_exclusions(None, 'u', 't', 'b2', [b2]), b2)
b3 = {'alignedKey': b1['alignedKey'], 'decision': 'include'}
with patch.object(pipeline.store, 'list_batches', return_value=[{'id': 'b3'}, {'id': 'b2'}, {'id': 'b1'}]), \
     patch.object(pipeline.store, 'all_candidates', return_value=[b2]):
    print('b3 protection:', pipeline.inherit_manual_exclusions(None, 'u', 't', 'b3', [b3]), b3)
print('BUG exclusion silently revived:', b3['decision'] == 'include')

# Persisted state produced by merge_apply: merged object is omitted from selection;
# references remain its old key; review follows mergedInto but adapter does not.
o1 = candidate('o1', 'obj-device-main', 'object')
o2 = candidate('o2', 'obj-device-alias', 'object')
o2['origin'] = {'mergedInto': 'o1'}
p = candidate('p', 'capacity', 'property', ownerKey='obj-device-alias')
p['fields'] = {'dataType': 'number'}
act = candidate('a', 'stop-device', 'action', ownerKey='obj-device-alias')
with patch.object(review, 'owner', return_value='u'), \
     patch.object(review.store, 'require_task', return_value={}), \
     patch.object(review.store, 'all_candidates', return_value=[o1, o2, p, act]):
    print('merge dependency blockers:', review.deliver_blockers(None, 't', 'b')['issues'])
try:
    adapter.assemble([o1, p])
    print('property assembly passed')
except adapter.AdapterError as exc:
    print('BUG property assembly after successful merge:', str(exc))
graph, workflow, _, _ = adapter.assemble([o1, act])
print('action count:', len(workflow['actions']))
print('BUG missing action association:', workflow['actionAssociations'])
print('final structure issues:', adapter.verify_structure(graph, workflow))

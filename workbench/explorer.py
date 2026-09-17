"""Read-only discovery of the currently mapped local demonstration data."""
import math
from datetime import datetime
from workbench.demo import Demo
from workbench.properties import effective, api_name


def snapshot(state):
    out = dict(objects=[], links=[], observations=[], warnings=[], errors=[], demo=True, project='')
    if state.get('workspaceId','storage')!='storage':
        out.update(demo=False,available=False,warnings=['当前本体尚未接入实例数据，请先配置项目数据接入。'])
        return out
    try:
        demo = Demo()
        demo.bindings = state['bindings']
        out['project'] = demo.project['project_id']
        graph = state['ontology']['@graph']
        definitions = {n['@id'].split(':')[-1]: n for n in graph if n.get('@type') == 'owl:Class'}
        links = {n['@id'].split(':')[-1]: n for n in graph if n.get('@type') == 'owl:ObjectProperty'}
        bindings = {b['object_type']: b for b in demo.bindings['object_bindings']}
        objects = demo.objects()
        for obj in objects.values():
            definition = definitions.get(obj['type'], {})
            labels = {}
            for prop in graph:
                if prop.get('@type') == 'owl:DatatypeProperty' and prop.get('rdfs:domain', {}).get('@id') == definition.get('@id'):
                    labels[api_name(prop)] = effective(prop, graph).get('rdfs:label', api_name(prop))
            out['objects'].append(dict(id=obj['id'], type=obj['type'], typeName=definition.get('rdfs:label', obj['type']),
                                       name=obj['display_name'], properties=obj['properties'], propertyLabels=labels,
                                       source=bindings[obj['type']]['table']))
            for relation, parent in obj['parents'].items():
                out['links'].append(dict(id=f"{obj['id']}::{relation}::{parent}", type=relation,
                                         name=links.get(relation, {}).get('rdfs:label', relation), source=obj['id'], target=parent))
        binding = demo.bindings.get('observation_binding')
        if binding:
            for row in demo.data[binding['table']]:
                oid = f"{out['project']}:{binding['subject_type']}:{row[binding['subject_key']]}"
                value = row[binding['value_column']]
                stamp = row[binding['timestamp_column']]
                try:
                    parsed = datetime.fromisoformat(stamp)
                    valid = parsed.tzinfo is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                except (TypeError, ValueError):
                    valid = False
                if oid not in objects or not valid:
                    out['warnings'].append('已跳过无法关联对象、缺少时区或数值无效的观测记录。')
                    continue
                out['observations'].append(dict(objectId=oid, property='SOC', value=value, timestamp=parsed.isoformat(),
                                                quality=str(row[binding['quality_column']]), unit=binding['source_unit'], source=binding['table']))
        out['warnings'] = list(dict.fromkeys(out['warnings']))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        out.update(objects=[], links=[], observations=[], errors=['实例数据加载失败，请检查项目映射：' + str(exc)])
    return out

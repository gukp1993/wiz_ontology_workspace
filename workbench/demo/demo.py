"""演示用只读执行链；不访问生产数据库，不执行配置内的任意代码。"""
import argparse
from datetime import datetime
import json
import yaml
from rdflib import Graph
from workbench.model_format import decode_ontology
from .calculations.soc import calculate_weighted_soc

# 数据根统一由 workbench.paths 提供（演示属于核心模块的下游消费者）。
from workbench.paths import DATA_ROOT as ROOT

def read_yaml(path):
    return yaml.safe_load((ROOT / path).read_text(encoding='utf-8'))

def timestamp(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError('时间必须包含时区')
    return dt

class Demo:
    def __init__(self):
        self.project = read_yaml('ontology/projects/chuangzhi/project.yaml')
        self.manifest = read_yaml('ontology/models/storage/manifest.yaml')
        self.bindings = read_yaml('ontology/projects/chuangzhi/bindings.yaml')
        self.parameters = read_yaml('ontology/projects/chuangzhi/parameters.yaml')
        self.metric = read_yaml('ontology/models/storage/metrics.yaml')['metrics'][0]
        self.rule = read_yaml('ontology/models/storage/rules.yaml')['rules'][0]
        connection = read_yaml('ontology/projects/chuangzhi/connections.yaml')['connections'][0]
        if connection['adapter'] != 'json_fixture':
            raise ValueError('示例只支持json_fixture适配器')
        self.data = json.loads((ROOT / connection['path']).read_text())
        self.validate()

    def validate(self):
        model = json.loads((ROOT / 'ontology/models/storage/ontology.json').read_text())
        Graph().parse(data=json.dumps(decode_ontology(model)), format='json-ld')
        if self.project['model_version'] != self.manifest['version']:
            raise ValueError('模型版本不匹配')
        if self.metric['rule_ref'] != self.rule['id']:
            raise ValueError('指标引用规则不存在')
        if self.rule['implementation_ref'] != 'storage.calculate_weighted_soc.v1':
            raise ValueError('未注册的计算函数')
        expected = {'member_type':'BatteryCluster', 'capacity_basis_policy':'same_basis_code',
                    'time_policy':'latest_at_or_before', 'missing_policy':'return_unavailable', 'output_unit':'%'}
        if any(self.rule.get(k) != v for k, v in expected.items()):
            raise ValueError('示例不支持此规则契约')
        if self.rule['membership_relations'] != ['belongsToSystem', 'belongsToDevice'] or self.rule['inputs'] != ['soc_pct','capacity_basis_kwh']:
            raise ValueError('示例不支持此成员或输入契约')
        for value in self.parameters.values():
            if isinstance(value, bool) or not isinstance(value, (int,float)) or value < 0:
                raise ValueError('时间容差必须是非负数')

    def objects(self):
        objects = {}
        prefix = self.project['project_id']
        for binding in self.bindings['object_bindings']:
            for row in self.data[binding['table']]:
                key = f"{prefix}:{binding['object_type']}:{row[binding['primary_key']]}"
                if key in objects:
                    raise ValueError('对象主键重复')
                objects[key] = {'id':key, 'type':binding['object_type'],
                    'source_id':str(row[binding['primary_key']]),
                    'properties':{p:row[c] for p,c in binding['properties'].items()},
                    'parents':{r['relation']:f"{prefix}:{r['target_type']}:{row[r['column']]}" for r in binding['relations']}}
        for binding in self.bindings['object_bindings']:
            for obj in objects.values():
                if obj['type']==binding['object_type']:
                    obj['display_name']=str(obj['properties'].get(binding.get('title_key') or 'name',obj['id']))
        for obj in objects.values():
            for parent in obj['parents'].values():
                if parent not in objects:
                    raise ValueError('关系指向不存在的对象')
        return objects

    def execute(self, object_id, at):
        result = {'metric_id':self.metric['id'], 'object_id':object_id, 'requested_at':at,
                  'unit':'%', 'model_version':self.manifest['version'],
                  'project_config_version':self.project['config_version'],
                  'implementation_ref':self.rule['implementation_ref'], 'demo_only':True}
        try:
            query_time = timestamp(at)
            objects = self.objects()
            if object_id not in objects:
                raise ValueError('对象不存在')
            if objects[object_id]['type'] not in self.metric['applicable_types']:
                raise ValueError('指标不适用于该对象类型')
            scope = {object_id}
            while True:
                expanded = scope | {k for k,v in objects.items() if any(p in scope for p in v['parents'].values())}
                if expanded == scope:
                    break
                scope = expanded
            clusters = [objects[k] for k in sorted(scope) if objects[k]['type'] == self.rule['member_type']]
            if not clusters:
                raise ValueError('查询范围没有电池簇')
            bases = {c['properties']['capacity_basis_code'] for c in clusters}
            if len(bases) != 1 or not next(iter(bases)):
                raise ValueError('容量基准不一致或未定义')
            binding = self.bindings['observation_binding']
            if binding['subject_type'] != self.rule['member_type']:
                raise ValueError('SOC观测主体类型与成员类型不一致')
            if binding['source_unit'] != '%':
                raise ValueError('示例只接受百分数SOC')
            rows = []
            for cluster in clusters:
                candidates = [r for r in self.data[binding['table']]
                    if str(r[binding['subject_key']]) == cluster['source_id']
                    and timestamp(r[binding['timestamp_column']]) <= query_time]
                if not candidates:
                    raise ValueError(f"{cluster['id']} 缺少SOC观测")
                latest = max(timestamp(r[binding['timestamp_column']]) for r in candidates)
                matches = [r for r in candidates if timestamp(r[binding['timestamp_column']]) == latest]
                if len(matches) != 1:
                    raise ValueError('同一时刻存在重复观测')
                source = matches[0]
                if source[binding['quality_column']] != 'good':
                    raise ValueError('最新观测质量无效')
                if (query_time - latest).total_seconds() > self.parameters['soc_max_age_seconds']:
                    raise ValueError('SOC观测过期')
                rows.append({'object_id':cluster['id'], 'soc_pct':source[binding['value_column']],
                    'capacity_basis_kwh':cluster['properties']['socCapacityBasisKWh'],
                    'sampled_at':latest.isoformat(), 'source_table':binding['table']})
            times = [timestamp(r['sampled_at']) for r in rows]
            if (max(times)-min(times)).total_seconds() > self.parameters['soc_alignment_tolerance_seconds']:
                raise ValueError('成员采样时间不满足对齐容差')
            result.update(value=calculate_weighted_soc(rows), quality='valid', member_count=len(rows), lineage=rows)
        except (ValueError, KeyError, TypeError) as exc:
            result.update(value=None, quality='unavailable', reason=str(exc))
        return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--object', default='chuangzhi:StorageSystem:south')
    parser.add_argument('--at', default='2026-09-08T10:00:00+08:00')
    args = parser.parse_args()
    print(json.dumps(Demo().execute(args.object, args.at), ensure_ascii=False, indent=2))

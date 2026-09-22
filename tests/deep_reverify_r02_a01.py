# -*- coding: utf-8 -*-
"""继续验证定向复跑（20260921）：只跑受判定修订影响的 R02 / A01 场景 + 负对照。

为什么单独一个入口：原 deep_project_chain / deep_ontology_o3o4o5 会重建大量历史资产，
本轮的验证目标是「修订后的判定在真实 HTTP 上给出正确分类」，不需要全面重测。
两个入口共用 tests/deep_reverify_scenarios.py，判定口径完全一致。

运行（服务须已在 18951 隔离实例运行）：
    DEEP_RUN_ID=reverify-<ts> .runtime/venv/bin/python tests/deep_reverify_r02_a01.py
环境变量：Q03_BASE / Q03_USER（默认 http://127.0.0.1:18951, qa_reverify_b）。

红线：不连真实 MySQL/Redis、不执行真实 SQL、不重启不停止共享实例（18931）与主工作台。
"""
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep_project_client import Api, Recorder, blank_state, brief  # noqa: E402
import deep_verdicts as V  # noqa: E402
from deep_reverify_scenarios import scenario_a01, scenario_r02  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get('Q03_BASE', 'http://127.0.0.1:18951')
USER = os.environ.get('Q03_USER', 'qa_reverify_b')
PASSWORD = os.environ.get('Q03_PASSWORD', 'DeepTest!2026#abc')
EVIDENCE_DIR = REPO_ROOT / '.runtime/reverify-evidence'
RUN_ID = os.environ.get('DEEP_RUN_ID') or 'reverify-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')

NOW = datetime.now(timezone.utc).strftime('%m%d%H%M%S')
UNIQ = 'rv-' + NOW

CTX = {'mg': 'https://example.com/microgrid/', 'owl': 'http://www.w3.org/2002/07/owl#',
       'rdfs': 'http://www.w3.org/2000/01/rdf-schema#', 'xsd': 'http://www.w3.org/2001/XMLSchema#'}


def cls(node_id, label, comment='业务定义'):
    return {'@id': 'mg:' + node_id, '@type': 'owl:Class', 'rdfs:label': label, 'rdfs:comment': comment}


def prop(node_id, label, owner_id, api_name, xsd='double'):
    return {'@id': 'mg:' + node_id, '@type': 'owl:DatatypeProperty', 'rdfs:label': label,
            'rdfs:domain': {'@id': 'mg:' + owner_id}, 'rdfs:range': {'@id': 'xsd:' + xsd},
            'mg:apiName': api_name}


def minimal_ontology_graph(name, workspace_id):
    """R02 前置用的最小可发布本体：1 对象 + 1 属性（属性是 flow 引用的落点）。"""
    st = blank_state(name)
    st['workspaceId'] = workspace_id
    st['ontology']['@graph'] = [cls('RVCluster', '验证储能簇', 'R02 前置对象'),
                                prop('rvActivePower', '有功功率', 'RVCluster', 'activePower')]
    return st


def mysql_conn(cid, name, generation):
    """连接为纯配置（不触发探测）；格式与 deep_project_chain.mysql_conn 一致，engine 必填。"""
    return {'id': cid, 'name': name, 'engine': 'mysql', 'host': '127.0.0.1', 'port': 3306,
            'username': 'rv', 'database': 'rv', 'tls': 'none'}


def main():
    api = Api(base=BASE, username=USER, password=PASSWORD)
    rec = Recorder('reverify_r02_a01', run_id=RUN_ID, evidence_dir=EVIDENCE_DIR)
    rec.add('V00-env', V.INFO,
            'V00 环境登记（实际 cwd/端口/数据根/业务SHA 见本文件 meta 行）',
            'base=%s user=%s runId=%s cwd=%s' % (BASE, USER, RUN_ID, os.getcwd()), 'meta')
    summary = {'runId': RUN_ID, 'base': BASE, 'user': USER, 'ts': datetime.now(timezone.utc).isoformat(),
               'businessSha': rec.run['businessSha'], 'repoHead': rec.run['repoHead'],
               'scriptSha256': rec.run['scriptSha256'], 'scenarios': {}}
    try:
        # ---------- 前置：新建本体 → 保存 → 发布（R02 的项目要绑定已发布本体版本） ----------
        r = api.post('/api/ontologies', {'name': '继续验证最小本体 ' + UNIQ})
        j = r['json'] if isinstance(r['json'], dict) else {}
        onto_id = j.get('id')
        precondition = V.classify_prereq('新建合成本体 201+id', r['status'] == 201 and bool(onto_id),
                                         'HTTP %s %s' % (r['status'], brief(j)))
        rec.add('V-pre-1', precondition['result'], 'V 前置：新建合成本体', precondition['detail'], 'pre')
        summary['scenarios']['pre.ontology'] = precondition['result']

        graph = minimal_ontology_graph('继续验证最小本体 ' + UNIQ, onto_id)
        rev = (api.get('/api/state?ontology=' + onto_id)['json'] or {}).get('revision')
        r = api.post('/api/save', {'state': graph, 'revision': rev})
        sj = r['json'] if isinstance(r['json'], dict) else {}
        r2 = api.post('/api/publish', {'state': graph, 'revision': sj.get('revision') or rev})
        pj = r2['json'] if isinstance(r2['json'], dict) else {}
        onto_version = pj.get('version')
        precondition = V.classify_prereq('本体保存+发布成功', r['status'] == 200 and r2['status'] == 200
                                         and bool(onto_version),
                                         'save HTTP %s publish HTTP %s version=%s'
                                         % (r['status'], r2['status'], onto_version))
        rec.add('V-pre-2', precondition['result'], 'V 前置：本体保存并发布出可引用版本',
                precondition['detail'], 'pre')
        summary['scenarios']['pre.publish'] = precondition['result']
        summary['ontologyId'] = onto_id
        summary['ontologyVersion'] = onto_version

        # ---------- A01：非法规则/动作选填字段 ----------
        a01 = scenario_a01(api, rec, onto_id, tag='V-A01')
        summary['scenarios']['A01'] = a01

        # ---------- R02：项目引用空壳编排 ----------
        if precondition['result'] == V.PRODUCT_PASS:
            r = api.post('/api/projects', {'name': '继续验证项目 ' + UNIQ, 'ontology': onto_id,
                                           'version': onto_version})
            j = r['json'] if isinstance(r['json'], dict) else {}
            pid = j.get('id')
            proj_pre = V.classify_prereq('创建项目 201+id', r['status'] == 201 and bool(pid),
                                         'HTTP %s %s' % (r['status'], brief(j)))
            rec.add('V-pre-3', proj_pre['result'], 'V 前置：创建绑定已发布本体版本的项目',
                    proj_pre['detail'], 'pre')
            summary['scenarios']['pre.project'] = proj_pre['result']
            summary['projectId'] = pid
            if pid:
                pst = (api.get('/api/project-state?project=' + pid)['json'] or {}).get('state') or {}
                pst = copy.deepcopy(pst)
                pst['connections'] = {'connections': [mysql_conn('conn-rv', '继续验证连接', 1)]}
                pst['implementations'] = []
                pst.setdefault('bindings', {})['catalogs'] = {}
                pst['bindings']['object_bindings'] = [{
                    'object_type': 'RVCluster', 'connection': 'conn-rv', 'table': 'cluster',
                    'primary_key': 'cluster_id',
                    'properties': {'activePower': {'kind': 'flow', 'flow': 'pending', 'output': 'fout-1',
                                                   'inputs': {}}},
                    'relations': []}]
                r02 = scenario_r02(api, rec, pid, pst, 'RVCluster', 'activePower', tag='V-R02')
                summary['scenarios']['R02'] = r02
        else:
            rec.add('V-pre-3', V.BLOCKED, 'V 前置：创建项目（本体发布未成功，跳过 R02）',
                    '依赖 V-pre-2 未通过；不拿旧状态继续', 'pre')
            summary['scenarios']['pre.project'] = V.BLOCKED
    finally:
        path = rec.dump()
        summary['evidence'] = str(path)
        out = EVIDENCE_DIR / 'runs' / RUN_ID / 'SUMMARY.json'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print('\n本次运行摘要：%s' % out)
    return summary


if __name__ == '__main__':
    main()

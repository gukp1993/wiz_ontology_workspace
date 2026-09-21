# -*- coding: utf-8 -*-
"""继续验证修订后的 R02 / A01 场景（真实 HTTP，判定走 deep_verdicts）。

被两个入口共用，避免「大脚本」与「定向复跑」判定漂移：
- tests/deep_project_chain.py 的 P8 段（R02）
- tests/deep_ontology_o3o4o5.py 的 O3-04 段（A01）
- tests/deep_reverify_r02_a01.py 定向复跑（本轮新增，只跑这两个场景）

修订要点（独立验收 S2）：
1. 前置（创建/保存/引用/项目保存）必须显式成功，失败即 blocked，不拿旧状态继续；
2. 「未拦截」分支记 known_defect_reproduced（R02/A01 均为基线已知），不是产品通过；
3. 「正确拒绝」分支必须命中针对目标的协议诊断且发布版本零新增才记 product_pass；
4. 500、无关 4xx、401、网络失败、空响应体一律不能记 pass（见 deep_verdicts R1–R7）；
5. R02 的「未拦截」与「仍发布成功」是同一根因的两个观察，统计按缺陷根因计 1 条。

红线：只读业务代码；不连真实 MySQL/Redis；不执行真实 SQL；只打本任务隔离实例。
"""
import copy
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep_ontology_common import obj, private_prop, state_with, rule, action_v2  # noqa: E402
import deep_verdicts as V  # noqa: E402


def _brief(value, limit=900):
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + '…'


def _blob(response):
    parts = []
    if response.get('json') is not None:
        parts.append(json.dumps(response['json'], ensure_ascii=False, default=str))
    if response.get('text'):
        parts.append(str(response['text']))
    return ' '.join(parts)


def _versions_of(api, ontology_id):
    r = api.get('/api/versions?ontology=' + ontology_id)
    items = (r['json'] or {}).get('items') if isinstance(r['json'], dict) else None
    return len(items or []), r


def _releases_of(api, project_id):
    r = api.get('/api/project-releases?project=' + project_id)
    items = (r['json'] or {}).get('items') if isinstance(r['json'], dict) else None
    return len(items or []), r


# ============================== A01：非法规则/动作选填字段 ==============================

def a01_legal_state(obj_id='DevA01', prop_id='tempA01'):
    """合法前置：1 对象 + 1 属性 + 1 合法规则 + 1 合法动作 + 两条关联（避免悬空引用）。"""
    st = state_with([obj(obj_id, '设备A01', 'A01 前置合法对象'),
                     private_prop(prop_id, '温度', obj_id, xsd='double')])
    st['workflow']['businessRules'] = [rule('r-a01-ok', name='合法规则', description='前置合法定义，用于关联不悬空')]
    st['workflow']['businessRuleAssociations'] = [{'objectTypeId': 'mg:' + obj_id, 'ruleId': 'r-a01-ok'}]
    st['workflow']['actions'] = [action_v2('a-a01-ok', '合法动作', '前置合法动作定义')]
    st['workflow']['actionAssociations'] = [{'objectTypeId': 'mg:' + obj_id, 'actionId': 'a-a01-ok'}]
    return st


def a01_illegal_state(base_state):
    """在合法定义之上「追加」非法记录（不替换，避免制造悬空关联）。"""
    st = copy.deepcopy(base_state)
    st['workflow']['businessRules'].append(
        rule('r-a01-bad', name='A01非法选填规则', description='复现 A01：content 非文本', content={'x': 1}))
    st['workflow']['actions'].append(
        action_v2('a-a01-bad', 'A01非法选填动作', '复现 A01：effect 非文本', effect=['a']))
    return st


def scenario_a01(api, rec, ontology_id, tag='O3-04'):
    """A01：非文本 content/effect 是否被拦截；报告口径 = 发布快照是否保留非法原值。"""
    out = {'ontologyId': ontology_id, 'cases': {}}
    legal = a01_legal_state()
    legal['workspaceId'] = ontology_id

    # 前置 1：合法定义 validate 无错误
    r = api.post('/api/validate', {'state': dict(legal)})
    errs = (r['json'] or {}).get('errors') if isinstance(r['json'], dict) else None
    res = V.classify_prereq('合法规则/动作定义 validate errors=[]',
                            r['status'] == 200 and errs == [], 'HTTP %s errors=%s' % (r['status'], _brief(errs)))
    rec.add(tag + '-1', res['result'], tag + ' A01 前置：合法对象+合法规则/动作+关联 validate 无错误',
            'HTTP %s errors=%s' % (r['status'], _brief(errs)), 'A01-pre')
    out['cases'][tag + '-1'] = res['result']
    if res['result'] != V.PRODUCT_PASS:
        return out

    # 前置 2：合法定义保存成功且关联保留（读回校验无悬空）
    rev = (api.get('/api/state?ontology=' + ontology_id)['json'] or {}).get('revision')
    r = api.post('/api/save', {'state': dict(legal), 'revision': rev})
    j = r['json'] if isinstance(r['json'], dict) else {}
    back = (api.get('/api/state?ontology=' + ontology_id)['json'] or {}).get('state') or {}
    back_rules = [x.get('id') for x in (back.get('workflow') or {}).get('businessRules', [])]
    back_assoc = (back.get('workflow') or {}).get('businessRuleAssociations') or []
    legal_kept = 'r-a01-ok' in back_rules and any(a.get('ruleId') == 'r-a01-ok' for a in back_assoc)
    res = V.classify_prereq('合法定义保存 200 且关联读回不悬空',
                            r['status'] == 200 and (j.get('errors') in ([], None)) and legal_kept,
                            'HTTP %s errors=%s 读回规则=%s 关联=%s' % (r['status'], _brief(j.get('errors')),
                                                                   _brief(back_rules), _brief(back_assoc)))
    rec.add(tag + '-2', res['result'], tag + ' A01 前置：合法定义保存成功，关联读回不悬空',
            res['detail'], 'A01-pre')
    out['cases'][tag + '-2'] = res['result']
    if res['result'] != V.PRODUCT_PASS:
        return out

    # 追加非法记录 → validate / save / publish
    illegal = a01_illegal_state(legal)
    illegal['workspaceId'] = ontology_id
    v = api.post('/api/validate', {'state': dict(illegal)})
    v_errs = (v['json'] or {}).get('errors') if isinstance(v['json'], dict) else None
    rev = (api.get('/api/state?ontology=' + ontology_id)['json'] or {}).get('revision')
    s = api.post('/api/save', {'state': dict(illegal), 'revision': rev})
    sj = s['json'] if isinstance(s['json'], dict) else {}
    rev2 = sj.get('revision') or rev
    before, _ = _versions_of(api, ontology_id)
    p = api.post('/api/publish', {'state': dict(illegal), 'revision': rev2,
                                  'requestId': 'reverify-a01-' + uuid.uuid4().hex})
    pj = p['json'] if isinstance(p['json'], dict) else {}
    after, rel_r = _versions_of(api, ontology_id)

    diag_terms = ['content', 'effect', '非文本', '文本', '类型', '字符串']
    detail = ('validate HTTP %s errors=%s；save HTTP %s errors=%s；publish HTTP %s body=%s；'
              '版本 %s→%s' % (v['status'], _brief(v_errs), s['status'], _brief(sj.get('errors')),
                              p['status'], _brief(pj), before, after))
    res = V.classify_guard_attempt(True, p,
                                   {'block_status': 422, 'diagnostic_terms': diag_terms,
                                    'known_defect': True, 'require_no_version_increase': True,
                                    'version_before': before, 'version_after': after},
                                   precondition_detail=detail)

    # 未拦截分支：核对发布快照是否原样保留非法值（A01 的实锤证据）
    snapshot = {}
    if res['result'] == V.KNOWN_DEFECT and pj.get('version'):
        vs = api.get('/api/version-state?ontology=%s&version=%s' % (ontology_id, pj['version']))
        snap = ((vs['json'] or {}).get('state') or {})
        wf = snap.get('workflow') or {}
        snapshot = {
            'contentKept': any(x.get('id') == 'r-a01-bad' and x.get('content') == {'x': 1}
                               for x in wf.get('businessRules', [])),
            'effectKept': any(x.get('id') == 'a-a01-bad' and x.get('effect') == ['a']
                              for x in wf.get('actions', [])),
            'legalRuleKept': any(x.get('id') == 'r-a01-ok' for x in wf.get('businessRules', [])),
            'version': pj.get('version'),
        }
        if not (snapshot['contentKept'] and snapshot['effectKept']):
            res = {'result': V.NEW_DEFECT,
                   'reason': '发布未被拦截，但快照未原样保留非法值（行为与既有记录不同，需人工确认）',
                   'detail': _brief(snapshot)}
    rec.add(tag, res['result'],
            tag + ' A01：非文本 content/effect 经 validate→save→publish（基线已知缺陷）',
            res['reason'] + '；' + detail + '；发布快照=' + _brief(snapshot), 'A01-known')
    out['cases'][tag] = res['result']
    out['snapshot'] = snapshot
    out['reason'] = res['reason']

    # 关联完整性：非法追加不得把合法定义挤掉
    back2 = (api.get('/api/state?ontology=' + ontology_id)['json'] or {}).get('state') or {}
    wf2 = back2.get('workflow') or {}
    ok_assoc = (any(a.get('ruleId') == 'r-a01-ok' for a in (wf2.get('businessRuleAssociations') or []))
                and any(a.get('actionId') == 'a-a01-ok' for a in (wf2.get('actionAssociations') or [])))
    rec.add(tag + '-3', V.PRODUCT_PASS if ok_assoc else V.NEW_DEFECT,
            tag + ' A01 追加非法记录后合法规则/动作关联仍在（无悬空）',
            'rules=%s actions=%s' % (_brief([x.get('id') for x in wf2.get('businessRules', [])]),
                                     _brief([x.get('id') for x in wf2.get('actions', [])])), 'A01')
    out['cases'][tag + '-3'] = V.PRODUCT_PASS if ok_assoc else V.NEW_DEFECT
    return out


# ============================== R02：项目引用空壳编排 ==============================

def shell_flow_state(flow_id, name):
    """空壳编排：声明一个输出但 binding=null（自身 check 报 OUTPUT_BINDING_MISSING）。"""
    return {'schemaVersion': 1, 'flowId': flow_id, 'name': name, 'description': '', 'status': 'active',
            'inputs': [], 'outputs': [{'id': 'fout-1', 'name': 'value', 'label': '取值',
                                       'type': {'type': 'number'}, 'binding': None}],
            'connections': [], 'nodes': [],
            'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}}}


def _patch_binding(state, object_type, prop, source):
    st = copy.deepcopy(state)
    found = False
    for b in (st.get('bindings') or {}).get('object_bindings', []):
        if b.get('object_type') == object_type:
            b.setdefault('properties', {})[prop] = source
            found = True
    return st, found


def _flow_diag_ok(chk):
    codes = [d.get('code') for d in (chk.get('diagnostics') or []) if isinstance(d, dict)]
    text = json.dumps(chk, ensure_ascii=False)
    return ('OUTPUT_BINDING_MISSING' in codes or '尚未绑定' in text or '未绑定' in text), codes


def scenario_r02(api, rec, project_id, project_state, object_type, prop, tag='R02'):
    """R02：被项目 kind='flow' 引用的空壳编排（自身 check 报输出未绑定）是否被门禁拦截。

    期望依据（不凭自创错误码判失败）：03 §2.2 规定校验需读取被引用编排并做依赖检查，
    编排配置检查的权威语义在 flows.check_flow（diagnostics.code）；协议未明确到
    「被引用编排自身 check 失败」的具体错误字段，因此拒绝分支按 422 + 含编排/输出
    诊断词 + 版本零新增判定，未拦截分支按既有 R02 登记记已知缺陷复现。
    """
    out = {'projectId': project_id, 'cases': {}, 'shellFlowId': None}
    diag_terms = ['编排', '输出', '未绑定', 'OUTPUT_BINDING_MISSING', 'flow']
    rev = (api.get('/api/project-state?project=' + project_id)['json'] or {}).get('revision')

    # 1) 创建空壳编排
    r = api.post('/api/flows', {'name': '继续验证空壳编排 ' + uuid.uuid4().hex[:6], 'description': 'R02：输出未绑定'})
    j = r['json'] if isinstance(r['json'], dict) else {}
    fid = j.get('id')
    out['shellFlowId'] = fid
    res = V.classify_prereq('创建空壳编排 201+id', r['status'] == 201 and bool(fid),
                            'HTTP %s %s' % (r['status'], _brief(j)))
    rec.add(tag + '-1', res['result'], tag + ' 前置：创建空壳编排', res['detail'], 'R02-pre')
    out['cases'][tag + '-1'] = res['result']
    if res['result'] != V.PRODUCT_PASS:
        return out

    # 2) 保存空壳（输出声明存在、binding 为空）→ 编排自身 check 报输出未绑定
    st = (api.get('/api/flow-state?flow=' + fid)['json'] or {}).get('state') or {}
    flow_rev = (api.get('/api/flow-state?flow=' + fid)['json'] or {}).get('revision')
    shell = shell_flow_state(fid, st.get('name') or '继续验证空壳编排')
    r = api.post('/api/flow-save', {'state': shell, 'revision': flow_rev})
    j = r['json'] if isinstance(r['json'], dict) else {}
    chk = j.get('check') or {}
    diag_ok, codes = _flow_diag_ok(chk)
    saved_ok = r['status'] == 200 and j.get('revision') and diag_ok and chk.get('status') == 'pending'
    res = V.classify_prereq('空壳编排保存成功且自身 check 报输出未绑定',
                            saved_ok, 'HTTP %s revision=%s status=%s codes=%s errors=%s'
                            % (r['status'], j.get('revision'), chk.get('status'), _brief(codes),
                               _brief(chk.get('errors'))))
    rec.add(tag + '-2', res['result'], tag + ' 前置：flow-save 成功且编排自身诊断含输出未绑定',
            res['detail'], 'R02-pre')
    out['cases'][tag + '-2'] = res['result']
    if res['result'] != V.PRODUCT_PASS:
        return out

    # 3) 独立 flow-check（只读）同样是 pending —— 编排侧结论不依赖保存响应
    r = api.post('/api/flow-check', {'state': shell})
    cj = r['json'] if isinstance(r['json'], dict) else {}
    diag2, codes2 = _flow_diag_ok(cj)
    res = V.classify_prereq('独立 flow-check 报编排存在配置错误',
                            r['status'] == 200 and cj.get('status') == 'pending' and diag2,
                            'HTTP %s status=%s codes=%s' % (r['status'], cj.get('status'), _brief(codes2)))
    rec.add(tag + '-3', res['result'], tag + ' 前置：独立 flow-check 判定该编排配置错误（status=pending）',
            res['detail'], 'R02-pre')
    out['cases'][tag + '-3'] = res['result']
    if res['result'] != V.PRODUCT_PASS:
        return out

    # 4) 项目属性引用该空壳编排的输出 → 项目保存必须成功（否则用例不成立）
    source = {'kind': 'flow', 'flow': fid, 'output': 'fout-1', 'inputs': {}}
    pst, found = _patch_binding(project_state, object_type, prop, source)
    reference_ok = found and source['flow'] == fid and source['output'] == 'fout-1'
    r = api.post('/api/project-save', {'state': pst, 'revision': rev})
    j = r['json'] if isinstance(r['json'], dict) else {}
    rev2 = j.get('revision') or rev
    res = V.classify_prereq('项目保存成功（引用正确 flowId/outputId）',
                            reference_ok and r['status'] == 200 and bool(j.get('revision')),
                            '引用=%s HTTP %s %s' % (reference_ok, r['status'], _brief(j)))
    rec.add(tag + '-4', res['result'], tag + ' 前置：项目保存成功且引用正确 flowId/outputId',
            res['detail'], 'R02-pre')
    out['cases'][tag + '-4'] = res['result']
    if res['result'] != V.PRODUCT_PASS:
        return out

    # 5) project-validate：观察是否拦截（同一根因观察一）
    r = api.post('/api/project-validate', {'state': pst, 'revision': rev2})
    vj = r['json'] if isinstance(r['json'], dict) else {}
    errs = vj.get('errors') or []
    blocked_here = any(('编排' in e or '输出' in e) and (prop in e or 'flow' in e.lower()) for e in errs)
    if blocked_here:
        res = {'result': V.PRODUCT_PASS, 'reason': '项目校验已拦截该依赖错误', 'detail': _brief(errs)}
    else:
        res = {'result': V.KNOWN_DEFECT,
               'reason': 'R02 复现：项目校验未拦截「被引用编排自身输出未绑定」',
               'detail': _brief(errs)}
    rec.add(tag + '-5', res['result'],
            tag + ' project-validate 对空壳编排引用是否拦截（同一根因观察一，不单独计根因）',
            res['reason'] + '；errors=%s' % res['detail'], 'R02-known',
            analysis_unit='observation', root_cause='R02:project_validation._check_flow_binding 不调用 flows.check_flow')
    out['cases'][tag + '-5'] = res['result']

    # 6) project-publish：期望被拦截；版本零新增才算正确阻断
    before, _ = _releases_of(api, project_id)
    r = api.post('/api/project-publish', {'state': pst, 'revision': rev2,
                                          'requestId': 'reverify-r02-' + uuid.uuid4().hex})
    j = r['json'] if isinstance(r['json'], dict) else {}
    after, _ = _releases_of(api, project_id)
    detail = ('publish HTTP %s body=%s；发布版本数 %s→%s' % (r['status'], _brief(j), before, after))
    res = V.classify_guard_attempt(
        True, r, {'block_status': 422, 'diagnostic_terms': diag_terms, 'known_defect': True,
                  'require_no_version_increase': True, 'version_before': before, 'version_after': after},
        precondition_detail=detail)
    rec.add(tag + '-6', res['result'],
            tag + ' project-publish 携带坏编排引用是否被拦截（同一根因观察二，按根因计 1 条）',
            res['reason'] + '；' + detail + '；根因：project_validation._check_flow_binding 不调用 flows.check_flow',
            'R02-known', analysis_unit='defect-reproduction',
            root_cause='R02:project_validation._check_flow_binding 不调用 flows.check_flow')
    out['cases'][tag + '-6'] = res['result']
    out['publish'] = {'before': before, 'after': after, 'status': r['status'],
                      'version': j.get('version'), 'body': _brief(j)}

    # 7) 反例（负对照）：引用「不存在」的编排必须被拦截 —— 证明门禁与判定链路本身有效
    ghost, found = _patch_binding(project_state, object_type, prop,
                                  {'kind': 'flow', 'flow': 'flow-does-not-exist-' + uuid.uuid4().hex[:8],
                                   'output': 'fout-1', 'inputs': {}})
    rev3 = (api.get('/api/project-state?project=' + project_id)['json'] or {}).get('revision') or rev2
    r = api.post('/api/project-validate', {'state': ghost, 'revision': rev3})
    gj = r['json'] if isinstance(r['json'], dict) else {}
    gerrs = gj.get('errors') or []
    ghost_validate = r['status'] == 200 and any('不存在' in e for e in gerrs)
    before2, _ = _releases_of(api, project_id)
    r2 = api.post('/api/project-publish', {'state': ghost, 'revision': rev3,
                                           'requestId': 'reverify-r02neg-' + uuid.uuid4().hex})
    after2, _ = _releases_of(api, project_id)
    res = V.classify_guard_attempt(
        found, r2, {'block_status': 422, 'diagnostic_terms': ['编排不存在', '不存在'],
                    'require_no_version_increase': True, 'version_before': before2, 'version_after': after2},
        precondition_detail='负对照：引用不存在编排；validate 拦截=%s errors=%s' % (ghost_validate, _brief(gerrs)))
    if res['result'] == V.TEST_ERROR and r2['status'] is None:
        pass
    rec.add(tag + '-7', res['result'],
            tag + ' 负对照：引用不存在编排应被拦截（证明门禁与判定链路本身有效）',
            'validate 拦截=%s；%s；%s' % (ghost_validate, res['reason'], res['detail']), 'R02-control',
            analysis_unit='negative-control', root_cause='')
    out['cases'][tag + '-7'] = res['result']
    out['negativeControl'] = {'validateBlocked': ghost_validate, 'publishStatus': r2['status'],
                              'versions': [before2, after2]}
    return out

"""Q03 深度测试：函数编排业务链（P7）。只测试记录，不修业务代码。

目标服务 http://127.0.0.1:18931（不启动/不停止）。主用账号 qa_deep_b；qa_deep_a 仅跨账号只读探测。

红线：flow-run 只跑纯本地计算节点（calc formula，走 calc_functions 受限表达式，无 IO/网络/eval）；
不连真实 MySQL/Redis、不执行 SQL、不触达 Python 用户代码执行（Python 节点本机只静态解析、由 LLM 代执行）。

覆盖 P7：
- 创建/编辑/复制/删除编排
- 保存 CAS（陈旧 revision → 409）、revision 令牌推进
- flow-check 拒绝：输出未绑定 / 循环依赖 / 非法公式；且结构错误 → 400（非 500）
- 证明 check 绝不执行：除零编排在 check 阶段 errors=[]（仅静态），run 阶段才定位失败节点
- flow-run 纯本地计算：链式 calc 结果确定正确；错误节点定位 + 下游 skipped
- 重复运行不脏数据（run 无状态：结果不变、revision 不变）
"""
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deep_project_client as C
from deep_project_client import Api, Recorder, brief

NOW = datetime.now(timezone.utc).strftime('%m%d%H%M%S')
UNIQ = f'q03f-{NOW}'


# --- 编排状态构造（纯 calc：全部本地确定性计算） --------------------------------

def blank_flow_state(flow_id, name):
    return {'schemaVersion': 1, 'flowId': flow_id, 'name': name, 'description': '', 'status': 'active',
            'inputs': [], 'outputs': [], 'connections': [], 'nodes': [],
            'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}}}


def fixed_input(iid, name, value):
    return {'id': iid, 'name': name, 'label': name, 'type': {'type': 'number'},
            'source': {'kind': 'fixed', 'valueType': 'number', 'value': value}}


def node_input_from(iid, name, node_id, output_id):
    return {'id': iid, 'name': name, 'label': name, 'type': {'type': 'number'},
            'source': {'kind': 'node', 'nodeId': node_id, 'outputId': output_id}}


def happy_state(flow_id, name, mult=2):
    st = blank_flow_state(flow_id, name)
    a = {'id': 'nd-a', 'kind': 'calc', 'name': '乘倍', 'inputs': [fixed_input('in-a1', 'x', 6)],
         'outputs': [{'id': 'out-a1', 'name': 'dbl', 'label': '倍', 'type': {'type': 'number'}}],
         'implementation': {'mode': 'formula', 'formulas': {'dbl': '{x} * %d' % mult}}}
    b = {'id': 'nd-b', 'kind': 'calc', 'name': '加一', 'inputs': [node_input_from('in-b1', 'y', 'nd-a', 'out-a1')],
         'outputs': [{'id': 'out-b1', 'name': 'plus', 'label': '加', 'type': {'type': 'number'}}],
         'implementation': {'mode': 'formula', 'formulas': {'plus': '{y} + 1'}}}
    st['nodes'] = [a, b]
    st['outputs'] = [{'id': 'fout-1', 'name': 'result', 'label': '结果', 'type': {'type': 'number'},
                      'binding': {'kind': 'node', 'nodeId': 'nd-b', 'outputId': 'out-b1'}}]
    return st


def divzero_state(flow_id, name):
    """check 静态通过（除零只在运行时暴露）；run 时 nd-d 失败、下游 skipped。"""
    st = blank_flow_state(flow_id, name)
    a = {'id': 'nd-d', 'kind': 'calc', 'name': '除零', 'inputs': [fixed_input('in-d1', 'z', 0)],
         'outputs': [{'id': 'out-d1', 'name': 'q', 'label': '商', 'type': {'type': 'number'}}],
         'implementation': {'mode': 'formula', 'formulas': {'q': '10 / {z}'}}}
    b = {'id': 'nd-after', 'kind': 'calc', 'name': '下游', 'inputs': [node_input_from('in-af', 'w', 'nd-d', 'out-d1')],
         'outputs': [{'id': 'out-af', 'name': 'r', 'label': '继', 'type': {'type': 'number'}}],
         'implementation': {'mode': 'formula', 'formulas': {'r': '{w} + 1'}}}
    st['nodes'] = [a, b]
    st['outputs'] = [{'id': 'fout-2', 'name': 'result', 'label': '结果', 'type': {'type': 'number'},
                      'binding': {'kind': 'node', 'nodeId': 'nd-after', 'outputId': 'out-af'}}]
    return st


def cycle_state(flow_id, name):
    st = blank_flow_state(flow_id, name)
    a = {'id': 'nd-c1', 'kind': 'calc', 'name': '环A', 'inputs': [node_input_from('in-c1', 'u', 'nd-c2', 'out-c2')],
         'outputs': [{'id': 'out-c1', 'name': 'v', 'label': 'v', 'type': {'type': 'number'}}],
         'implementation': {'mode': 'formula', 'formulas': {'v': '{u} + 1'}}}
    b = {'id': 'nd-c2', 'kind': 'calc', 'name': '环B', 'inputs': [node_input_from('in-c2', 'p', 'nd-c1', 'out-c1')],
         'outputs': [{'id': 'out-c2', 'name': 's', 'label': 's', 'type': {'type': 'number'}}],
         'implementation': {'mode': 'formula', 'formulas': {'s': '{p} + 1'}}}
    st['nodes'] = [a, b]
    st['outputs'] = [{'id': 'fout-3', 'name': 'result', 'label': '结果', 'type': {'type': 'number'},
                      'binding': {'kind': 'node', 'nodeId': 'nd-c2', 'outputId': 'out-c2'}}]
    return st


def illegal_state(flow_id, name):
    st = blank_flow_state(flow_id, name)
    a = {'id': 'nd-il', 'kind': 'calc', 'name': '非法公式', 'inputs': [fixed_input('in-il', 'x', 2)],
         'outputs': [{'id': 'out-il', 'name': 'o', 'label': 'o', 'type': {'type': 'number'}}],
         'implementation': {'mode': 'formula', 'formulas': {'o': '{x} + FOO({ghost})'}}}
    st['nodes'] = [a]
    st['outputs'] = [{'id': 'fout-4', 'name': 'result', 'label': '结果', 'type': {'type': 'number'},
                      'binding': {'kind': 'node', 'nodeId': 'nd-il', 'outputId': 'out-il'}}]
    return st


def unbound_state(flow_id, name):
    st = blank_flow_state(flow_id, name)
    a = {'id': 'nd-u', 'kind': 'calc', 'name': '有效节点', 'inputs': [fixed_input('in-u', 'x', 3)],
         'outputs': [{'id': 'out-u', 'name': 'o', 'label': 'o', 'type': {'type': 'number'}}],
         'implementation': {'mode': 'formula', 'formulas': {'o': '{x} * 2'}}}
    st['nodes'] = [a]
    st['outputs'] = [{'id': 'fout-5', 'name': 'result', 'label': '结果', 'type': {'type': 'number'},
                      'binding': None}]
    return st


def create_flow(api, name):
    r = api.post('/api/flows', {'name': name, 'description': 'Q03 P7'})
    j = r['json'] if isinstance(r['json'], dict) else {}
    return (j.get('id') if r['status'] == 201 else None), r, j


def save_flow(api, state, revision):
    return api.post('/api/flow-save', {'state': state, 'revision': revision})


def node_out(run_json, node_id):
    for entry in (run_json or {}).get('nodeResults', []) or []:
        if entry.get('nodeId') == node_id:
            return entry
    return None


# --- 主体 ---------------------------------------------------------------------

def main():
    api = Api(username='qa_deep_b')
    rec = Recorder('flow_chain')

    _p7_create_edit_run(api, rec)
    _p7_check_rejects_and_gate(api, rec)
    _p7_error_location_no_dirty(api, rec)
    _p7_copy_delete(api, rec)
    _p7_cross_account(rec)

    rec.dump()


def _p7_create_edit_run(api, rec):
    fid, r, j = create_flow(api, f'Q03编排-计算 {UNIQ}')
    rec.add('P7.1', 'pass' if fid else 'fail', 'P7 创建编排 → 201 + id', f'HTTP {r["status"]} {brief(j)}', 'api')

    r = api.get(f'/api/flow-state?flow={fid}')
    rev = (r['json'] or {}).get('revision')
    rec.add('P7.2', 'pass' if r['status'] == 200 and rev else 'fail',
            'P7 GET flow-state 返回 state + revision 令牌', f'HTTP {r["status"]} revision={rev}', 'api')

    # 保存链式 calc 编排（x=6 → *2=12 → +1=13）
    state = happy_state(fid, f'Q03编排-计算 {UNIQ}', mult=2)
    r = save_flow(api, state, rev)
    sj = r['json'] if isinstance(r['json'], dict) else {}
    rev = sj.get('revision') or rev
    chk = sj.get('check') or {}
    rec.add('P7.3', 'pass' if r['status'] == 200 and rev and chk.get('errors') == [] else 'fail',
            'P7 flow-save 纯 calc 编排 revision 推进 + check.errors=[]',
            f'HTTP {r["status"]} errors={brief(chk.get("errors"))} newRev={rev}', 'api')

    # 独立 flow-check（无副作用）→ status passed
    r = api.post('/api/flow-check', {'state': state})
    cj = r['json'] if isinstance(r['json'], dict) else {}
    rec.add('P7.4', 'pass' if r['status'] == 200 and cj.get('errors') == [] and cj.get('status') == 'passed' else 'fail',
            'P7 独立 flow-check 纯配置校验通过（不落盘/不执行）', f'HTTP {r["status"]} status={cj.get("status")}', 'api')

    # 全图运行：期望确定值 nd-a.dbl=12, nd-b.plus=13
    r = api.post('/api/flow-run', {'state': state, 'revision': rev})
    rj = r['json'] if isinstance(r['json'], dict) else {}
    ea, eb = node_out(rj, 'nd-a'), node_out(rj, 'nd-b')
    dbl = (ea or {}).get('outputs', {}).get('dbl')
    plus = (eb or {}).get('outputs', {}).get('plus')
    ok_run = r['status'] == 200 and rj.get('status') == 'success' and ea and ea.get('status') == 'success' \
        and eb and eb.get('status') == 'success' and dbl == 12 and plus == 13
    rec.add('P7.5', 'pass' if ok_run else 'fail',
            'P7 flow-run 纯本地 calc 结果确定正确（6*2=12→12+1=13）',
            f'HTTP {r["status"]} runStatus={rj.get("status")} dbl={dbl} plus={plus}', 'exec')

    # 编辑：改倍率 *2 → *5，再运行 6*5=30 → 31
    state_edit = happy_state(fid, f'Q03编排-计算 {UNIQ}', mult=5)
    r = save_flow(api, state_edit, rev)
    sj = r['json'] if isinstance(r['json'], dict) else {}
    rev2 = sj.get('revision') or rev
    rec.add('P7.6', 'pass' if r['status'] == 200 and rev2 and rev2 != rev else 'fail',
            'P7 编辑编排（改公式）保存并推进 revision', f'HTTP {r["status"]} newRev={rev2}', 'api')
    r = api.post('/api/flow-run', {'state': state_edit, 'revision': rev2})
    rj = r['json'] if isinstance(r['json'], dict) else {}
    dbl = (node_out(rj, 'nd-a') or {}).get('outputs', {}).get('dbl')
    plus = (node_out(rj, 'nd-b') or {}).get('outputs', {}).get('plus')
    rec.add('P7.7', 'pass' if dbl == 30 and plus == 31 else 'fail',
            'P7 编辑后重算结果随配置更新（6*5=30→31）', f'dbl={dbl} plus={plus}', 'exec')

    # CAS：陈旧 revision 保存 → 409 REVISION_CONFLICT + currentRevision
    r = save_flow(api, state_edit, rev)  # 用旧 rev
    cj = r['json'] if isinstance(r['json'], dict) else {}
    rec.add('P7.8', 'pass' if r['status'] == 409 and cj.get('code') == 'REVISION_CONFLICT' and cj.get('currentRevision') else 'fail',
            'P7 陈旧 revision flow-save → 409 + currentRevision（CAS）', f'HTTP {r["status"]} {brief(cj)}', 'api')


def _p7_check_rejects_and_gate(api, rec):
    # 输出未绑定：save 放行但 check 报 OUTPUT_BINDING_MISSING；全图 run 被 errors 门 422 拦截（绝不执行）
    fid, r, _ = create_flow(api, f'Q03编排-未绑定 {UNIQ}')
    rev = (api.get(f'/api/flow-state?flow={fid}')['json'] or {}).get('revision')
    st = unbound_state(fid, f'Q03编排-未绑定 {UNIQ}')
    rs = save_flow(api, st, rev)
    chk = (rs['json'] or {}).get('check') if isinstance(rs['json'], dict) else {}
    unbound_err = 'OUTPUT_BINDING_MISSING' in json.dumps(chk or {}, ensure_ascii=False)
    rec.add('P7.9', 'pass' if rs['status'] == 200 and unbound_err else 'fail',
            'P7 输出未绑定：flow-save 放行但 flow-check 报 OUTPUT_BINDING_MISSING',
            f'HTTP {rs["status"]} errors={brief((chk or {}).get("errors"))}', 'validation')
    rr = api.post('/api/flow-run', {'state': st, 'revision': (rs['json'] or {}).get('revision')})
    rec.add('P7.10', 'pass' if rr['status'] == 422 and 'nodeResults' not in (rr['json'] or {}) else 'fail',
            'P7 check 有错时 flow-run 被拦截(422)，绝不进入执行（无 nodeResults）',
            f'HTTP {rr["status"]} {brief(rr["json"])}', 'safety')

    # 循环依赖
    fid2, _, _ = create_flow(api, f'Q03编排-环 {UNIQ}')
    rev2 = (api.get(f'/api/flow-state?flow={fid2}')['json'] or {}).get('revision')
    st2 = cycle_state(fid2, f'Q03编排-环 {UNIQ}')
    r = api.post('/api/flow-check', {'state': st2})
    cj = r['json'] if isinstance(r['json'], dict) else {}
    has_cycle = 'FLOW_CYCLE' in json.dumps(cj, ensure_ascii=False) or any('循环' in e for e in (cj.get('errors') or []))
    rec.add('P7.11', 'pass' if r['status'] == 200 and has_cycle and cj.get('errors') else 'fail',
            'P7 flow-check 拒绝循环依赖（FLOW_CYCLE）', f'errors={brief(cj.get("errors"))}', 'validation')

    # 非法公式（未知函数 + 未声明输入）
    fid3, _, _ = create_flow(api, f'Q03编排-非法 {UNIQ}')
    st3 = illegal_state(fid3, f'Q03编排-非法 {UNIQ}')
    r = api.post('/api/flow-check', {'state': st3})
    cj = r['json'] if isinstance(r['json'], dict) else {}
    blob = json.dumps(cj, ensure_ascii=False)
    illegal_flagged = any(c in blob for c in ('CALC_FORMULA_INVALID', '不支持的函数', 'CALC_PARAM_UNKNOWN', '不存在的输入'))
    rec.add('P7.12', 'pass' if r['status'] == 200 and illegal_flagged and cj.get('errors') else 'fail',
            'P7 flow-check 拒绝非法公式（不支持函数/未声明输入）→ 定位错误', f'errors={brief(cj.get("errors"))}', 'validation')

    # 结构错误（nodes 非列表）→ 400，不是 500
    bad = copy.deepcopy(st3)
    bad['nodes'] = 'not-a-list'
    r = api.post('/api/flow-check', {'state': bad})
    rec.add('P7.13', 'pass' if r['status'] == 400 else 'fail',
            'P7 结构非法（nodes 非列表）flow-check → 400（优雅拒绝，非500）', f'HTTP {r["status"]} {brief(r["json"])}', 'api')


def _p7_error_location_no_dirty(api, rec):
    fid, _, _ = create_flow(api, f'Q03编排-除零 {UNIQ}')
    rev = (api.get(f'/api/flow-state?flow={fid}')['json'] or {}).get('revision')
    st = divzero_state(fid, f'Q03编排-除零 {UNIQ}')
    # check 静态阶段 errors=[]（除零只在运行时暴露）→ 证明 check 绝不执行
    r = api.post('/api/flow-check', {'state': st})
    cj = r['json'] if isinstance(r['json'], dict) else {}
    rec.add('P7.14', 'pass' if r['status'] == 200 and cj.get('errors') == [] else 'fail',
            'P7 check 绝不执行：除零编排静态 errors=[]（仅 run 才暴露）', f'errors={brief(cj.get("errors"))}', 'safety')

    rs = save_flow(api, st, rev)
    rev = (rs['json'] or {}).get('revision') or rev
    rr = api.post('/api/flow-run', {'state': st, 'revision': rev})
    rj = rr['json'] if isinstance(rr['json'], dict) else {}
    enode = node_out(rj, 'nd-d')
    after = node_out(rj, 'nd-after')
    located = (rr['status'] == 200 and rj.get('status') == 'error'
               and enode and enode.get('status') == 'failed' and '除数不能为零' in str(enode.get('error'))
               and after and after.get('status') == 'skipped')
    rec.add('P7.15', 'pass' if located else 'fail',
            'P7 run 运行时失败定位到具体节点(nd-d)+下游 skipped',
            f'runStatus={rj.get("status")} nd-d={ (enode or {}).get("status") }:"{ (enode or {}).get("error") }" nd-after={ (after or {}).get("status") }',
            'exec')

    # 重复运行不脏数据：结果一致 + flow-state revision 不变（run 无状态、不写盘）
    before_rev = (api.get(f'/api/flow-state?flow={fid}')['json'] or {}).get('revision')
    runs = []
    for _ in range(2):
        r = api.post('/api/flow-run', {'state': st, 'revision': rev})
        rj = r['json'] if isinstance(r['json'], dict) else {}
        runs.append([{'nodeId': e.get('nodeId'), 'status': e.get('status'), 'outputs': e.get('outputs')}
                     for e in (rj.get('nodeResults') or [])])
    after_rev = (api.get(f'/api/flow-state?flow={fid}')['json'] or {}).get('revision')
    identical = runs[0] == runs[1]
    no_dirty = before_rev == after_rev == rev
    rec.add('P7.16', 'pass' if identical and no_dirty else 'fail',
            'P7 重复运行幂等无脏数据（两次结果逐节点一致 + revision 未变）',
            f'结果一致={identical} run前rev={before_rev==rev} run后rev={after_rev==rev}', 'data-integrity')


def _p7_copy_delete(api, rec):
    fid, _, _ = create_flow(api, f'Q03编排-复制 {UNIQ}')
    rev = (api.get(f'/api/flow-state?flow={fid}')['json'] or {}).get('revision')
    st = happy_state(fid, f'Q03编排-复制 {UNIQ}')
    save_flow(api, st, rev)
    orig_state = (api.get(f'/api/flow-state?flow={fid}')['json'] or {}).get('state') or {}
    orig_rev = (api.get(f'/api/flow-state?flow={fid}')['json'] or {}).get('revision')
    orig_ids = [n['id'] for n in orig_state.get('nodes', [])]

    r = api.post('/api/flow-copy', {'flowId': fid, 'name': f'Q03编排-副本 {UNIQ}'})
    j = r['json'] if isinstance(r['json'], dict) else {}
    new_id = j.get('id')
    rec.add('P7.17', 'pass' if r['status'] == 201 and new_id and new_id != fid else 'fail',
            'P7 复制编排 → 201 + 新 id', f'HTTP {r["status"]} {brief(j)}', 'api')

    copy_state = (api.get(f'/api/flow-state?flow={new_id}')['json'] or {}).get('state') or {}
    copy_ids = [n['id'] for n in copy_state.get('nodes', [])]
    remapped = copy_state.get('flowId') == new_id and copy_ids and set(copy_ids).isdisjoint(set(orig_ids))
    rec.add('P7.18', 'pass' if remapped else 'fail',
            'P7 副本重生成全部稳定 ID（节点 ID 与原编排不相交）',
            f'原节点={brief(orig_ids)} 副本节点={brief(copy_ids)}', 'data-integrity')

    # 原编排字节不动：复制后原 flow-state 的 revision 与节点 ID 均不变
    after_orig = api.get(f'/api/flow-state?flow={fid}')['json'] or {}
    untouched = after_orig.get('revision') == orig_rev and \
        [n['id'] for n in (after_orig.get('state') or {}).get('nodes', [])] == orig_ids
    rec.add('P7.19', 'pass' if untouched else 'fail',
            'P7 复制不影响原编排（revision 与节点 ID 均未变）', f'未变={untouched}', 'data-integrity')

    # 删除：软删除后默认列表消失、includeDeleted 可见；再删不存在 → 404
    r = api.post('/api/flow-delete', {'flowId': fid})
    rec.add('P7.20', 'pass' if r['status'] == 200 and (r['json'] or {}).get('ok') else 'fail',
            'P7 软删除编排 → ok', f'HTTP {r["status"]} {brief(r["json"])}', 'api')
    items = (api.get('/api/flows')['json'] or {}).get('items') or []
    ids_default = {it.get('id') for it in items}
    items_del = (api.get('/api/flows?includeDeleted=true')['json'] or {}).get('items') or []
    ids_with_del = {it.get('id') for it in items_del}
    gone = fid not in ids_default and fid in ids_with_del
    rec.add('P7.21', 'pass' if gone else 'fail',
            'P7 删除后默认列表不含该编排、includeDeleted 仍可见（软删除，历史保留）',
            f'默认含={fid in ids_default} 含已删={fid in ids_with_del}', 'data-integrity')
    r = api.post('/api/flow-delete', {'flowId': 'zzz-not-exist-0'})
    rec.add('P7.22', 'pass' if r['status'] == 404 else 'fail',
            'P7 删除不存在编排 → 404', f'HTTP {r["status"]} {brief(r["json"])}', 'api')


def _p7_cross_account(rec):
    # qa_deep_a 只读探测 qa_deep_b 的编排 → 视为不存在（覆盖 P9.2 的编排线，独立留证）
    a = Api(username='qa_deep_a', password=C.PASSWORD)
    # 借用本脚本刚创建的副本 id（qa_deep_b 名下）
    items = (Api(username='qa_deep_b').get('/api/flows')['json'] or {}).get('items') or []
    mine = next((it.get('id') for it in items if it.get('name', '').startswith('Q03编排-副本')), None)
    code = a.get(f'/api/flow-state?flow={mine}')['status'] if mine else None
    rec.add('P7.23', 'pass' if code == 404 else 'fail',
            'P7 跨账号读他人编排 → 404（视为不存在）', f'flow={mine} HTTP {code}', 'security')


if __name__ == '__main__':
    main()

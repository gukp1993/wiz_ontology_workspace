# -*- coding: utf-8 -*-
"""结果索引生成器（20260921 继续验证 S1）：把历史证据按「有效执行批次」重新汇总。

问题（独立验收 S1）：报告里的数字与原始 JSONL 没有形成可核算的一次执行批次——
Q03 把 info 算进通过、Q02 用「34 断言」对不上任何一次批次、Q05 有 125 条历史追加
（含 8 条 crash）却只写「约75条」。

做法：
1. 在这里显式登记每个证据文件的执行批次（起止行、是否有效、被谁替代、原因）；
2. 只按「有效批次」重算，输出 runId/时间/业务SHA/脚本SHA/行号与逐条分类；
3. 统计单位写清楚（断言 / 场景 / 缺陷根因），不同单位不相加。

运行（只读 .runtime/test-evidence；产出索引到 .runtime/reverify-evidence 与本需求目录）：
    .runtime/venv/bin/python tests/deep_results_index.py [--write-doc]
"""
import argparse
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import deep_verdicts as V  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
RAW = REPO / '.runtime/test-evidence'
OUT = REPO / '.runtime/reverify-evidence'
DOC = REPO / '文档/需求/20260921_系统全方位深度测试/结果索引.md'

BUSINESS_SHA = 'd6c73c29a19d97ad8da22ad3d8a8f3c0615e3b40'

# 有效批次口径：同一次脚本运行连续写入的行区间（1-based，闭区间）。
# status: valid=该批次结论有效；superseded=被后续完整批次替代（保留原始行，不计入统计）。
BATCHES = [
    # ---- Q02 本体链 ----
    {'suite': 'Q02', 'file': 'q02/o1o2-graph.jsonl', 'rows': (1, 11), 'status': 'valid',
     'script': 'tests/deep_ontology_o1o2.py', 'scriptCommit': '4d4e497', 'runLabel': 'q02-o1o2-1',
     'note': 'O1/O2 唯一一次完整批次'},
    {'suite': 'Q02', 'file': 'q02/o3o4o5-rules-publish.jsonl', 'rows': (1, 16), 'status': 'superseded',
     'script': 'tests/deep_ontology_o3o4o5.py', 'scriptCommit': '4d4e497', 'runLabel': 'q02-o3o4o5-1',
     'replacedBy': 'q02/o3o4o5-rules-publish.jsonl#17-34',
     'reason': '首跑含测试脚本自身缺陷：O4-01 把规则塞进 @graph 触发泛化文案、O4-05 未按 releases 语义取值、'
               'O4-07 在无快照时仍走成功路径 —— 3 条 fail 均为工具构造问题，第 2 跑修正后重算'},
    {'suite': 'Q02', 'file': 'q02/o3o4o5-rules-publish.jsonl', 'rows': (17, 34), 'status': 'valid',
     'script': 'tests/deep_ontology_o3o4o5.py', 'scriptCommit': '4d4e497', 'runLabel': 'q02-o3o4o5-2',
     'note': '修正脚本缺陷后的完整批次；O4-05b 为新发现缺陷，A01 为基线已知，O4-07a 阻塞'},
    {'suite': 'Q02', 'file': 'q02/o6o7-export-cross.jsonl', 'rows': (1, 12), 'status': 'superseded',
     'script': 'tests/deep_ontology_o6o7.py', 'scriptCommit': '4d4e497', 'runLabel': 'q02-o6o7-1',
     'replacedBy': 'q02/o6o7-export-cross.jsonl#13-24',
     'reason': '首跑 O7-01 探针载荷缺陷导致 400 误判，第 2 跑修正后重算'},
    {'suite': 'Q02', 'file': 'q02/o6o7-export-cross.jsonl', 'rows': (13, 24), 'status': 'valid',
     'script': 'tests/deep_ontology_o6o7.py', 'scriptCommit': '4d4e497', 'runLabel': 'q02-o6o7-2',
     'note': '配置包导出导入 + 跨账号探测完整批次'},
    # ---- Q03 项目 + 编排 ----
    {'suite': 'Q03', 'file': 'q03/project_chain.jsonl', 'rows': (1, 51), 'status': 'valid',
     'script': 'tests/deep_project_chain.py', 'scriptCommit': '4d4e497', 'runLabel': 'q03-project-1',
     'note': 'P1–P6/P8/P9 单次完整批次；P1.6b 与 P6.4 为 info 说明行，不计通过'},
    {'suite': 'Q03', 'file': 'q03/flow_chain.jsonl', 'rows': (1, 23), 'status': 'valid',
     'script': 'tests/deep_flow_chain.py', 'scriptCommit': '4d4e497', 'runLabel': 'q03-flow-1',
     'note': 'P7 编排链单次完整批次'},
    # ---- Q05 可靠性/边界 ----
    {'suite': 'Q05', 'file': 'q05/http-main.jsonl', 'rows': (1, 66), 'status': 'superseded',
     'script': 'tests/deep_integrity_http.py', 'scriptCommit': '4d4e497', 'runLabel': 'q05-http-1',
     'replacedBy': 'q05/http-main.jsonl#67-125',
     'reason': '首跑 8 条 crash（I1/I2/I5/I6/I7/I3b/I8/I9 套件异常中断）后脚本修正；'
               '后续完整重跑覆盖同批场景，本批断言（含 I5-getAll/I7-bigBody/I9-saveWithErrors 的失败）'
               '不作为最终结论，原始行保留'},
    {'suite': 'Q05', 'file': 'q05/http-main.jsonl', 'rows': (67, 125), 'status': 'valid',
     'script': 'tests/deep_integrity_http.py', 'scriptCommit': '4d4e497', 'runLabel': 'q05-http-2',
     'note': 'I1/I2/I3b/I5/I6/I7/I8/I9 完整批次（59 条）；I7-originNoOrigin 的 fail 即 D1'},
    {'suite': 'Q05', 'file': 'q05/restart-write.jsonl', 'rows': (1, 1), 'status': 'valid',
     'script': 'tests/deep_integrity_restart.py write', 'scriptCommit': '4d4e497', 'runLabel': 'q05-restart-write'},
    {'suite': 'Q05', 'file': 'q05/restart-verify.jsonl', 'rows': (1, 5), 'status': 'valid',
     'script': 'tests/deep_integrity_restart.py verify', 'scriptCommit': '4d4e497', 'runLabel': 'q05-restart-verify'},
    {'suite': 'Q05', 'file': 'q05/restart-freshroots.jsonl', 'rows': (1, 2), 'status': 'valid',
     'script': 'tests/deep_integrity_restart.py freshroots', 'scriptCommit': '4d4e497',
     'runLabel': 'q05-restart-freshroots'},
    {'suite': 'Q05', 'file': 'q05/fault-prep.jsonl', 'rows': (1, 1), 'status': 'valid',
     'script': 'tests/deep_integrity_fault.py prep', 'scriptCommit': '4d4e497', 'runLabel': 'q05-fault-prep'},
    {'suite': 'Q05', 'file': 'q05/fault-checkFinal.jsonl', 'rows': (1, 1), 'status': 'valid',
     'script': 'tests/deep_integrity_fault.py check', 'scriptCommit': '4d4e497', 'runLabel': 'q05-fault-check'},
    {'suite': 'Q05', 'file': 'q05/fault-corrupt-setup.jsonl', 'rows': (1, 1), 'status': 'valid',
     'script': 'tests/deep_integrity_fault.py corrupt-setup', 'scriptCommit': '4d4e497',
     'runLabel': 'q05-corrupt-setup'},
    {'suite': 'Q05', 'file': 'q05/fault-corrupt-probe.jsonl', 'rows': (1, 1), 'status': 'valid',
     'script': 'tests/deep_integrity_fault.py corrupt-probe', 'scriptCommit': '4d4e497',
     'runLabel': 'q05-corrupt-probe', 'note': '该行 pass 内部含 D2 观察（损坏回传内部异常文本）'},
    {'suite': 'Q05', 'file': 'q05/fault-corrupt-cleanup.jsonl', 'rows': (1, 1), 'status': 'valid',
     'script': 'tests/deep_integrity_fault.py corrupt-cleanup', 'scriptCommit': '4d4e497',
     'runLabel': 'q05-corrupt-cleanup'},
]

# 缺陷根因口径：同一根因的多条观察合并计 1 条（S2 要求 R02 的 P8.3/P8.4 不重复计根因）。
DEFECT_ROOTS = [
    {'id': 'D-Q02-01', 'severity': 'P1', 'classification': 'new_defect_reproduced',
     'observations': ['q02/o3o4o5-rules-publish.jsonl#27(O4-05b)',
                      'q02/o3o4o5-rules-publish.jsonl#29(O4-07a)'],
     'root': 'versions.publish 不写 release-zip 附件 → 前端「恢复快照」入口对在线新资产恒空、'
             '/api/restore 成功路径不可达（发布与版本读回正常）'},
    {'id': 'A01', 'severity': 'P1', 'classification': 'known_defect_reproduced',
     'observations': ['q02/o3o4o5-rules-publish.jsonl#20(O3-04)'],
     'root': '规则 content / 动作 effect 未做类型校验，非文本值可进入不可变发布快照'},
    {'id': 'R02', 'severity': 'P1（原登记 P2，本轮按验收意见保持 P1）', 'classification': 'known_defect_reproduced',
     'observations': ['q03/project_chain.jsonl#46(P8.3)', 'q03/project_chain.jsonl#47(P8.4)'],
     'root': 'workbench/project_validation.py _check_flow_binding 不调用 flows.check_flow；'
             '两条观察同根因，合并计 1 条'},
    {'id': 'D1(Q05)', 'severity': 'P2 待裁定', 'classification': 'new_defect_reproduced',
     'observations': ['q05/http-main.jsonl#107(I7-originNoOrigin)'],
     'root': 'POST 缺省 Origin 被放行，与 README「缺失或不符→403」口径不一致'},
    {'id': 'D2(Q05)', 'severity': 'P2', 'classification': 'new_defect_reproduced',
     'observations': ['q05/fault-corrupt-probe.jsonl#1(I3c-probe)'],
     'root': '存储损坏时 GET /api/state 回传 400 + 原始 Python 解析错误文本'},
    {'id': 'D3(Q05)', 'severity': 'P3', 'classification': 'new_defect_reproduced',
     'observations': ['q05/http-main.jsonl#96(I6-secretCross)', 'q05/http-main.jsonl#97(I6-llmDelNoop)'],
     'root': '跨账号 connection-secret 返回 400、llm-provider-delete 幂等 200，与「按不存在 404/空」口径不一致；'
             '实测无越权效果'},
    {'id': 'R01', 'severity': 'P1', 'classification': 'known_defect_reproduced',
     'observations': ['doc:.runtime/test-evidence/q04/REPORT.md（真实浏览器 console 复现）',
                      'doc:.runtime/accept-evidence/http.txt（独立复现）'],
     'root': 'App.vue watch(flowState) TDZ：启动期 console 抛一次 ReferenceError，随后页面可用（非整页瘫痪）'},
    {'id': 'Q04-01', 'severity': 'P3', 'classification': 'new_defect_reproduced',
     'observations': ['doc:.runtime/test-evidence/q04/REPORT.md（撤销/重做 label 陈旧，两次实测）'],
     'root': '撤销/重做按钮 label 读取普通栈，不随栈顶变化刷新'},
    {'id': 'mapping_forms', 'severity': '基线已知', 'classification': 'known_defect_reproduced',
     'observations': ['doc:.runtime/accept-evidence/mapping.txt（真实失败堆栈）'],
     'root': '前端断言与实现语义漂移（期望「数据连接」，实际渲染未配置分支），committed main 即失败'},
    {'id': 'A02', 'severity': '重新定性', 'classification': 'not_tested',
     'observations': ['doc:.runtime/test-evidence/q04/REPORT.md（旧 NodeEditModal 生产入口不可达）'],
     'root': '旧 NodeEditModal 在生产入口不可达（死代码）；不等于全部图谱必填校验已验证'},
]


def validate_defect_observations(cache=None):
    """核对缺陷根因的 JSONL 观察引用（`文件#行(用例)`）真实存在；`doc:` 前缀为文档类引用，只登记不校验行号。"""
    cache = {} if cache is None else cache
    checked = []
    unknown = []
    for item in DEFECT_ROOTS:
        if not item['observations']:
            unknown.append(item['id'])
        for ref in item['observations']:
            if ref.startswith('doc:'):
                checked.append({'defect': item['id'], 'ref': ref, 'kind': 'doc'})
                continue
            row = _resolve_ref(cache, ref)
            checked.append({'defect': item['id'], 'ref': ref, 'kind': 'jsonl',
                            'legacyVerdict': row.get('verdict')})
    return checked, unknown


def _git(*args):
    out = subprocess.run(['git', '-C', str(REPO)] + list(args), stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    return out.stdout.decode('utf-8', 'replace').strip()


def _blob_sha(commit, path):
    return _git('rev-parse', '%s:%s' % (commit, path))[:12] or ''


# 逐用例替代关系（被替代批次 → 有效批次）：验证要求「不能只取最后一条或按 case 去重，
# 必须说明哪个完整批次有效、哪些记录被替代及原因」。
#
# 引用格式 `文件#行号(用例)`：生成索引时**逐条解析并核对**该行的 caseId，不匹配即报错退出，
# 避免人工枚举行号出错。
CASE_REPLACEMENTS = [
    {'superseded': 'q02/o3o4o5-rules-publish.jsonl#6(O4-01)', 'replacedBy': 'q02/o3o4o5-rules-publish.jsonl#22(O4-01)',
     'reason': '首跑把规则记录塞进 @graph 触发泛化文案「配置不完整或不受当前执行器支持」；第 2 跑按 schema 形态构造后错误含稳定 id'},
    {'superseded': 'q02/o3o4o5-rules-publish.jsonl#10(O4-05)', 'replacedBy': 'q02/o3o4o5-rules-publish.jsonl#26(O4-05)',
     'reason': '首跑把 /api/releases 当版本历史取值导致断言不成立；第 2 跑改用 /api/versions 与 /api/version-state'},
    {'superseded': 'q02/o3o4o5-rules-publish.jsonl#12(O4-07)', 'replacedBy': 'q02/o3o4o5-rules-publish.jsonl#29(O4-07a)+q02/o3o4o5-rules-publish.jsonl#30(O4-07b)',
     'reason': '首跑在无快照时仍按「成功路径」调用 restore；第 2 跑拆成 7a（无在线快照→阻塞，归因 D-Q02-01）与 7b（错误路径 400/409 独立验证）'},
    {'superseded': 'q02/o3o4o5-rules-publish.jsonl#11(O4-06)', 'replacedBy': 'q02/o3o4o5-rules-publish.jsonl#28(O4-06)',
     'reason': '同一静态核对；统计口径改为 static_check_pass，破坏路径另立 O4-06b 记 not_tested'},
    {'superseded': 'q02/o6o7-export-cross.jsonl#11(O7-01)', 'replacedBy': 'q02/o6o7-export-cross.jsonl#23(O7-01)',
     'reason': '首跑跨账号探针载荷构造错误导致 400 误判；第 2 跑修正后 3 目标×5 端点全 404'},
    {'superseded': 'q05/http-main.jsonl#1(I1-crash)', 'replacedBy': 'q05/http-main.jsonl#67(I1-save1)',
     'reason': '首跑 8 个套件（I1/I2/I5/I6/I7/I3b/I8/I9）异常中断（工具错误，非产品缺陷）；修正脚本后同一套件完整重跑覆盖全部同编号场景，逐项对应见同批 #67–#125'},
    {'superseded': 'q05/http-main.jsonl#24(I5-getAll)', 'replacedBy': 'q05/http-main.jsonl#82(I5-getAll)',
     'reason': '首跑夹具把免登录端点 /api/auth-state 计入「17 条白名单全 401」期望，实测该端点返回 200（count401=16）→ 断言不成立；第 2 跑排除免登录端点后 16/16 通过'},
    {'superseded': 'q05/http-main.jsonl#41(I7-bigBody)', 'replacedBy': 'q05/http-main.jsonl#100(I7-bigBody)',
     'reason': '首跑用 urllib 发 >2MB，服务端返回 413 后立即关闭连接，客户端拿不到状态码（status=null）；第 2 跑改用 curl 实测 413 PAYLOAD_TOO_LARGE'},
    {'superseded': 'q05/http-main.jsonl#64(I9-saveWithErrors)', 'replacedBy': 'q05/http-main.jsonl#123(I9-saveWithErrors)',
     'reason': '首跑构造的候选非法定义全部触发保存期硬拒绝 422（20260920 保存边界，属设计内拦截），无法验证「save 200 且 errors 如实回传」；第 2 跑改为 422 情形记 known、另取非引用类校验错误得到 200+errors'},
    {'superseded': 'q05/http-main.jsonl#38(I6-secretCross)', 'replacedBy': 'q05/http-main.jsonl#96(I6-secretCross)+q05/http-main.jsonl#97(I6-llmDelNoop)',
     'reason': '首跑把「跨账号删 LLM 提供方返回 200」并入同一断言故整体 fail；第 2 跑拆成两行：拒写断言 pass（#96），幂等删除语义单列为 D3 观察（#97）'},
]

# 工具错误识别：中断豁免必须**由记录自身声明**（kind 或 title 或 evidence 记载异常中断/堆栈），
# 不能用 case 名字含 crash 作为依据 —— 业务场景名里出现 crash 不得被豁免。
TOOL_ERROR_KINDS = __import__('re').compile(r'中断|异常|traceback|crash', __import__('re').I)


def is_tool_error_row(row):
    """该行是否由记录自身声明为测试工具错误/套件异常中断。"""
    kind = str(row.get('kind') or '')
    title = str(row.get('title') or '')
    evidence = str(row.get('evidence') or '')
    return bool(TOOL_ERROR_KINDS.search(kind) or TOOL_ERROR_KINDS.search(title)
                or 'Traceback (most recent call last)' in evidence)

# 旧编号被拆成多个子用例：**全部子用例**都必须出现在有效批次（一拆多，F04）。
CASE_ID_SPLITS = {
    'O4-07': {'O4-07a', 'O4-07b'},
}

# 旧编号对应多个**等价**编号：命中任意一个即可（任选其一）。
CASE_ID_ALTERNATIVES = {}

_REF_RE = __import__('re').compile(r'^(.+?)#(\d+)\((\S+?)\)$')


def _resolve_ref(cache, ref):
    """解析 `文件#行(用例)` → 行记录；caseId 不匹配立即报错。"""
    match = _REF_RE.match(ref)
    assert match, '引用格式应为 文件#行(用例)：%s' % ref
    rel, line, expect_case = match.group(1), int(match.group(2)), match.group(3)
    rows = cache.get(rel)
    if rows is None:
        rows = cache[rel] = read_rows(rel)
    assert 1 <= line <= len(rows), '%s 行号越界（文件共 %d 行）' % (ref, len(rows))
    row = rows[line - 1]
    assert str(row.get('case')) == expect_case,         '替代关系引用与证据不符：%s 实际为 %s（请核对行号）' % (ref, row.get('case'))
    return row


def validate_replacements(cache=None):
    """核对每条替代关系两侧引用都真实存在且 caseId 一致；返回核对后的清单。"""
    import re as _re
    cache = {} if cache is None else cache
    checked = []
    for item in CASE_REPLACEMENTS:
        row_s = _resolve_ref(cache, item['superseded'])
        for ref in _re.findall(r'[\w./-]+#\d+\([^()]+\)', item['replacedBy']):
            _resolve_ref(cache, ref)
        checked.append({'superseded': item['superseded'], 'supersededVerdict': row_s.get('verdict'),
                        'replacedBy': item['replacedBy'], 'reason': item['reason']})
    return checked

RECLASSIFY = {
    'q03/project_chain.jsonl#46': {
        'result': V.KNOWN_DEFECT, 'severity': 'P1', 'defect': 'R02',
        'reason': '旧判定把「未拦截到坏编排」记 pass（S2 指出的问题）；该行本质是缺陷复现，改为已知缺陷复现，'
                  '与同批次 #47（P8.4 发布成功）合并计 1 个根因'},
    'q02/o3o4o5-rules-publish.jsonl#28': {
        'result': V.STATIC_PASS, 'severity': '-', 'defect': '',
        'reason': '旧判定把「白名单无写入口」记 pass；它是静态核对，改为 static_check_pass，'
                  '不可变性的动态验证单列 not_tested（见缺陷清单 O4-06b）'},
}

# 需在文档里说明的口径细节（不改分类，只标注观察与分类的关系）。
ROW_NOTES = {
    'q05/http-main.jsonl#96': '该行断言（拒写 + A 资产完好）成立 → product_pass；行内同时记录 D3 观察①（400 而非 404）',
    'q05/http-main.jsonl#97': '该行记录 D3 观察②（200 cleared=true 幂等 no-op）；跨账号无越权效果，故断言成立',
    'q05/fault-corrupt-probe.jsonl#1': '该行记录 D2 观察（400 + 原始解析文本）；服务未崩溃、其余接口正常',
    'q02/o3o4o5-rules-publish.jsonl#12': '',
}


def read_rows(rel):
    path = RAW / rel
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def classify_row(row, rel, line_no):
    """按新分类判定单行；旧 kind 里记载的工具错误/异常中断归 test_error。

    安全取向（对抗验证加固）：
    - 工具中断只按 kind 判定（含「中断/异常/crash/error/traceback」），**不按 case 名**：
      业务场景名里出现 crash 不能豁免；
    - 旧枚举只在 LEGACY_MAP 内映射；**枚举外的 verdict 一律 test_error**，
      不再用「默认 product_pass」兜底（拼写错误/新枚举不得悄悄算成通过）。
    """
    override = RECLASSIFY.get('%s#%d' % (rel, line_no))
    if override:
        return override['result']
    kind = str(row.get('kind') or '')
    verdict = row.get('verdict')
    if 'kind' not in row:
        raise AssertionError('证据行缺少 kind 字段（%s#%d），无法判定' % (rel, line_no))
    if verdict == 'fail' and is_tool_error_row(row):
        return V.TEST_ERROR
    if verdict == 'blocked':
        return V.BLOCKED
    if verdict == 'known':
        return V.KNOWN_DEFECT
    if verdict == 'info' or kind == 'info':
        return V.INFO
    if verdict == 'fail':
        # 有效批次内的 fail：Q05 首跑的断言 fail 已在有效批次 pass，这里只剩 D1 缺陷复现
        return V.NEW_DEFECT
    if verdict == 'pass':
        return V.PRODUCT_PASS
    raise AssertionError('证据行 verdict 不在已知枚举内（%s#%d: %r），'
                         '请先确认该记录含义再登记批次' % (rel, line_no, verdict))


def validate_superseded_coverage(batches):
    """核对「被替代批次里的业务场景都被有效批次覆盖」，防止用替代之名丢记录。

    规则（F04 修订：区分「一拆多」与「任选其一」）：
    - 套件异常中断记录（caseId 含 `crash`）没有对应业务场景，允许无覆盖，但必须列出来；
    - `CASE_ID_SPLITS`（一拆多）：旧编号被拆成若干**子用例**时，**全部子用例**都必须出现在
      有效批次里 —— 只覆盖其中一个不能算替代完成（独立复验 F04 指出的漏检）；
    - `CASE_ID_ALTERNATIVES`（任选其一）：旧编号对应多个**等价**编号，命中任意一个即可；
    - 其余 caseId 必须原样出现在同文件的某个有效批次里；
    - 出现无法归属的场景即报错退出（对应「无法归属项标未确认」的强制口径）。
    """
    checked = []
    by_file = {}
    for entry in batches:
        by_file.setdefault(entry['file'], []).append(entry)
    for entry in batches:
        if entry['status'] == 'valid':
            continue
        valid_cases = set()
        valid_rows = {}
        for other in by_file[entry['file']]:
            if other['status'] == 'valid':
                for c in other['cases']:
                    valid_cases.add(c['caseId'])
                    valid_rows.setdefault(c['caseId'], []).append(c)
        crash, info_only, uncovered, split_detail = [], [], [], []
        for case in entry['cases']:
            cid = str(case['caseId'])
            # 被替代批次里本身只是说明行（如会话建立）的不是业务场景，无需业务覆盖，但须列出
            if case.get('legacyVerdict') == 'info' or str(case.get('kind') or '').lower() == 'info':
                info_only.append(cid)
                continue
            # 工具错误行（套件异常中断/堆栈）没有对应业务场景，允许无覆盖但必须列出；
            # 判定完全依据**记录自身的声明**（kind/title/evidence），与 case 名字无关 ——
            # 既不会因名字含 crash 放过业务场景，也不会因名字平常而漏认工具错误。
            if is_tool_error_row(case):
                crash.append(cid)
                continue
            if cid in valid_cases:
                # 有效批次里命中同一 caseId 的行还必须是**业务判定行**，不能被 info 说明行顶替
                rows = valid_rows.get(cid) or []
                if any(not str(r.get('kind') or '').lower().startswith('info') and r.get('result') != V.INFO
                       for r in rows):
                    continue
                uncovered.append('%s（有效批次仅有 info 说明行，不能作为业务覆盖）' % cid)
                continue
            required = CASE_ID_SPLITS.get(cid)
            if required is not None:
                missing = sorted(required - valid_cases)
                split_detail.append({'superseded': cid, 'required': sorted(required),
                                     'missing': missing})
                if missing:
                    uncovered.append('%s（拆分后缺：%s）' % (cid, '、'.join(missing)))
                continue
            alternatives = CASE_ID_ALTERNATIVES.get(cid)
            if alternatives is not None:
                if alternatives & valid_cases:
                    continue
                uncovered.append('%s（等价编号均缺失：%s）' % (cid, '、'.join(sorted(alternatives))))
                continue
            uncovered.append(cid)
        assert not uncovered, \
            '被替代批次 %s 有未被有效批次覆盖的场景：%s（请补替代关系或修正批次登记）' % (entry['runLabel'], uncovered)
        checked.append({'runLabel': entry['runLabel'], 'file': entry['file'],
                        'supersededCases': len(entry['cases']),
                        'toolErrorCrashRows': crash, 'infoOnlyRows': info_only,
                        'uncovered': uncovered, 'splitCoverage': split_detail})
    return checked


def build():
    index = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'businessSha': BUSINESS_SHA,
        'unitPolicy': {
            'assertion': '一行 JSONL = 一条断言/场景记录（含 info）',
            'scenario': '同一批次的唯一 caseId 数',
            'defectRootCause': '同一根因的多条观察合并计 1 条（见 defectRoots）',
            'rule': '三种单位不相加；报告分别给出',
        },
        'batches': [],
        'supersededRawRows': 0,
        'results': {},
    }
    counts = {V.PRODUCT_PASS: 0, V.KNOWN_DEFECT: 0, V.NEW_DEFECT: 0, V.STATIC_PASS: 0,
              V.TEST_ERROR: 0, V.BLOCKED: 0, V.NOT_TESTED: 0, V.INFO: 0}
    per_suite = {}
    for batch in BATCHES:
        rows = read_rows(batch['file'])
        lo, hi = batch['rows']
        chunk = rows[lo - 1:hi]
        assert len(chunk) == hi - lo + 1, '批次 %s 行区间越界（文件 %d 行）' % (batch['runLabel'], len(rows))
        entry = {k: v for k, v in batch.items() if k != 'rows'}
        entry['rows'] = list(batch['rows'])
        entry['rowCount'] = len(chunk)
        entry['scriptBlobSha'] = _blob_sha(batch['scriptCommit'], batch['script'])
        if batch['status'] != 'valid':
            entry['verdictCounts'] = {'superseded': len(chunk)}
            # 被替代批次同样登记逐行 caseId，供覆盖核对（不参与统计，不分类）
            entry['cases'] = [{'caseId': row.get('case'), 'line': lo + offset,
                               'legacyVerdict': row.get('verdict'), 'kind': row.get('kind') or '',
                               'title': row.get('title') or '', 'evidence': row.get('evidence') or ''}
                              for offset, row in enumerate(chunk)]
            entry['uniqueCases'] = len({c['caseId'] for c in entry['cases']})
            index['supersededRawRows'] += len(chunk)
            index['batches'].append(entry)
            continue
        tally = {}
        cases = []
        for offset, row in enumerate(chunk):
            line_no = lo + offset
            result = classify_row(row, batch['file'], line_no)
            tally[result] = tally.get(result, 0) + 1
            counts[result] = counts.get(result, 0) + 1
            note = ROW_NOTES.get('%s#%d' % (batch['file'], line_no), '')
            override = RECLASSIFY.get('%s#%d' % (batch['file'], line_no))
            cases.append({'caseId': row.get('case'), 'result': result,
                          'line': line_no, 'kind': row.get('kind') or '',
                          'legacyVerdict': row.get('verdict'),
                          'reclassified': bool(override), 'reclassifyReason': (override or {}).get('reason', ''),
                          'note': note})
        entry['verdictCounts'] = tally
        entry['uniqueCases'] = len({c['caseId'] for c in cases})
        entry['cases'] = cases
        per_suite.setdefault(batch['suite'], {}).setdefault('records', 0)
        per_suite[batch['suite']]['records'] += len(chunk)
        for key, value in tally.items():
            per_suite[batch['suite']][key] = per_suite[batch['suite']].get(key, 0) + value
        index['batches'].append(entry)
    index['results'] = counts
    index['perSuite'] = per_suite
    refs, unresolved = validate_defect_observations()
    assert not unresolved, '缺陷根因缺少观察来源：%s' % unresolved
    index['defectRoots'] = DEFECT_ROOTS
    index['defectObservationRefs'] = refs
    index['reclassifications'] = RECLASSIFY
    index['caseReplacements'] = validate_replacements()
    index['rowNotes'] = ROW_NOTES
    index['supersededCoverage'] = validate_superseded_coverage(index['batches'])
    index['totals'] = {
        'assertionRecords': sum(counts.values()),
        'assertionByClass': ' '.join('%s=%d' % (k, v) for k, v in counts.items() if v),
        'scenarioUniqueCases': sum(entry.get('uniqueCases', 0) for entry in index['batches']
                                   if entry['status'] == 'valid'),
        'defectRootCauseCount': len([d for d in DEFECT_ROOTS if d['classification'] != V.NOT_TESTED]),
    }
    return index


def render_markdown(index):
    lines = []
    lines.append('# 系统全方位深度测试 · 结果索引（可核算批次）')
    lines.append('')
    lines.append('生成时间：%s；业务 SHA：`%s`；生成脚本：`tests/deep_results_index.py`（只读原始日志重算）。' %
                 (index['generatedAt'], index['businessSha']))
    lines.append('')
    lines.append('本索引替代报告里旧的「约 75 条 / 74 断言 / 31 通过」等写法：所有数字由下面登记的')
    lines.append('有效批次重新计算，原始 JSONL 一律保留在 `.runtime/test-evidence/`（不入库）。')
    lines.append('')
    lines.append('## 1. 单位口径（不同单位不相加）')
    lines.append('')
    lines.append('| 单位 | 定义 | 合计 |')
    lines.append('|---|---|---|')
    lines.append('| 断言/场景记录 | 一行 JSONL = 一条记录（含 info 说明行） | %d |' %
                 index['totals']['assertionRecords'])
    lines.append('| 唯一场景 | 有效批次内唯一 caseId 数 | %d |' % index['totals']['scenarioUniqueCases'])
    lines.append('| 缺陷根因 | 同一根因多条观察合并计 1（见 §4） | %d |' % index['totals']['defectRootCauseCount'])
    lines.append('')
    lines.append('## 2. 有效批次与替代关系')
    lines.append('')
    lines.append('| runLabel | 文件 | 行区间 | 状态 | 记录 | 分类统计 | 替代原因 |')
    lines.append('|---|---|---|---|---|---|---|')
    for entry in index['batches']:
        counts = entry['verdictCounts']
        stat = ' '.join('%s=%d' % (k, v) for k, v in counts.items())
        reason = entry.get('reason') or entry.get('note', '')
        lines.append('| %s | `%s` | %d–%d | %s | %d | %s | %s |' %
                     (entry['runLabel'], entry['file'], entry['rows'][0], entry['rows'][1],
                      entry['status'], entry['rowCount'], stat, reason))
    lines.append('')
    lines.append('被替代批次共 %d 行原始记录仍保留，不计入任何统计。' % index['supersededRawRows'])
    lines.append('')
    lines.append('### 2.1 逐用例替代关系（被替代 → 有效）')
    lines.append('')
    lines.append('| 被替代记录 | 替代为 | 原因 |')
    lines.append('|---|---|---|')
    for item in index['caseReplacements']:
        lines.append('| `%s` | `%s` | %s |' % (item['superseded'], item['replacedBy'], item['reason']))
    lines.append('')
    lines.append('### 2.2 被替代批次的覆盖核对（机器强制）')
    lines.append('')
    lines.append('| 被替代批次 | 被替代记录 | 无对应业务的工具错误行 | 本身即说明行 | 未覆盖场景 |')
    lines.append('|---|---|---|---|---|')
    for item in index.get('supersededCoverage', []):
        lines.append('| %s | %d | %s | %s | %s |' % (item['runLabel'], item['supersededCases'],
                                                     '、'.join(item['toolErrorCrashRows']) or '—',
                                                     '、'.join(item.get('infoOnlyRows') or []) or '—',
                                                     '、'.join(item['uncovered']) or '无（全部已覆盖）'))
    lines.append('')
    lines.append('覆盖核对失败时生成器直接报错退出，不允许用「被替代」掩盖记录。')
    lines.append('业务场景不能由 info 说明行顶替；`*-crash` 行的中断豁免要求 kind 确为工具错误。')
    lines.append('')
    lines.append('## 3. 有效批次的分类统计（按新分类）')
    lines.append('')
    lines.append('| 套件 | 记录 | ' + ' | '.join(V.ALL_RESULTS) + ' |')
    lines.append('|---|---|' + '---|' * len(V.ALL_RESULTS))
    for suite, data in sorted(index['perSuite'].items()):
        cells = [str(data.get(key, 0)) for key in V.ALL_RESULTS]
        lines.append('| %s | %d | %s |' % (suite, data['records'], ' | '.join(cells)))
    lines.append('')
    lines.append('总计数：**%s**（未列出的分类为 0）。' % V.verdict_line(index['results']))
    lines.append('')
    lines.append('## 3.1 逐行重分类（旧枚举含义不唯一，只改统计口径，不改原始行）')
    lines.append('')
    lines.append('| 文件#行 | 旧 verdict | 新分类 | 理由 |')
    lines.append('|---|---|---|---|')
    for entry in index['batches']:
        if entry['status'] != 'valid':
            continue
        for case in entry['cases']:
            if case.get('reclassified'):
                lines.append('| `%s#%d` | %s | %s | %s |' % (entry['file'], case['line'],
                                                             case.get('legacyVerdict'),
                                                             case['result'], case.get('reclassifyReason', '')))
    lines.append('')
    lines.append('## 4. 缺陷根因（同一根因合并计 1 条）')
    lines.append('')
    lines.append('| 根因 | 严重性 | 分类 | 观察来源 | 说明 |')
    lines.append('|---|---|---|---|---|')
    for item in index['defectRoots']:
        lines.append('| %s | %s | %s | %s | %s |' %
                     (item['id'], item['severity'], item['classification'],
                      '、'.join(item['observations']), item['root']))
    lines.append('')
    lines.append('## 5. 本轮继续验证的新批次（2026-09-21 修订后）')
    lines.append('')
    lines.append('本次端口 18951、数据根 `.runtime/reverify-data`（全新合成根，未写 18931 / 真实根）。')
    lines.append('修订后的判定函数：`tests/deep_verdicts.py`；自测 `tests/deep_verdicts_test.py`（41 项）与')
    lines.append('`tests/deep_results_index_test.py`（12 项，覆盖替代/覆盖/拆分核对器）。')
    lines.append('第二轮（按 S1–S3 独立复验 F01–F05 整改）：拒绝分支同样要求版本零新增且版本可读，')
    lines.append('诊断只从约定诊断字段取文本（含诊断键下的字符串列表），校验接口须先过状态码/响应结构，')
    lines.append('一拆多的子用例必须全部覆盖。详见 缺陷清单.md 的「复验整改」段。')
    lines.append('')
    lines.append('### 5.1 本轮运行日志（工具错误一并登记）')
    lines.append('')
    lines.append('| runId | 状态 | 说明 | 证据 |')
    lines.append('|---|---|---|---|')
    for item in RUN_JOURNAL:
        lines.append('| %s | %s | %s | `%s` |' % (item['runId'], item['status'], item['reason'],
                                                   item['evidenceKept']))
    lines.append('')
    lines.append('### 5.2 有效批次逐条分类（runId=%s）' %
                 (next((i['runId'] for i in RUN_JOURNAL if i['status'] == 'valid'), '-')))
    lines.append('')
    index_block = index.get('reverifyRun')
    if index_block:
        lines.append('| caseId | 分类 | 说明 |')
        lines.append('|---|---|---|')
        for item in index_block:
            lines.append('| %s | %s | %s |' % (item['caseId'], item['result'], item.get('title', '')))
        lines.append('')
    return '\n'.join(lines) + '\n'


def load_reverify_index():
    """把本轮 18951 复跑的 runId 证据并入索引文档（只取登记为 valid 的运行）。"""
    rows = []
    runs = OUT / 'runs'
    if not runs.exists():
        return rows
    valid = {item['runId'] for item in RUN_JOURNAL if item['status'] == 'valid'}
    for path in sorted(runs.glob('*/reverify_r02_a01.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get('meta') or row.get('runId') not in valid:
                continue
            rows.append({'runId': row.get('runId'), 'caseId': row.get('case'),
                         'result': row.get('result'), 'title': row.get('title', '')})
    return rows


# 本轮（2026-09-21 继续验证）运行日志：中间运行同样登记，注明替代原因，不隐藏工具错误。
RUN_JOURNAL = [
    {'runId': 'reverify-20260921T0947Z', 'status': 'superseded',
     'reason': '工具构造错误：连接载荷缺 engine 字段导致项目校验报“连接引擎仅支持 MySQL 与 Redis”，'
               'R02 发布被无关 422 拦截；同时记录器序列化 PosixPath 失败。修正 deep_verdicts.run_metadata 与'
               'deep_reverify_r02_a01.mysql_conn 后重跑',
     'evidenceKept': '.runtime/reverify-evidence/runs/reverify-20260921T0947Z/'},
    {'runId': 'reverify-20260921T0952Z', 'status': 'discarded',
     'reason': '工具构造错误：负对照发布复用了已被上一步发布推进的 revision → 409 无法归因（判为 test_error）；'
               '中间运行，证据目录未保留，结论未引用',
     'evidenceKept': '(未保留)'},
    {'runId': 'reverify-20260921T0958Z', 'status': 'valid',
     'reason': '本轮唯一有效批次：前置全成功，A01/R02 均判为已知缺陷复现，负对照通过',
     'evidenceKept': '.runtime/reverify-evidence/runs/reverify-20260921T0958Z/'},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--write-doc', action='store_true', help='同时写出 结果索引.md（入库）')
    args = parser.parse_args()
    index = build()
    index['reverifyRun'] = load_reverify_index()
    OUT.mkdir(parents=True, exist_ok=True)
    full = OUT / 'results-index.json'
    full.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    print('有效批次 %d 个，被替代记录 %d 行' %
          (len([b for b in index['batches'] if b['status'] == 'valid']), index['supersededRawRows']))
    print('分类统计：%s' % V.verdict_line(index['results']))
    print('断言记录=%d 唯一场景=%d 缺陷根因=%d' %
          (index['totals']['assertionRecords'], index['totals']['scenarioUniqueCases'],
           index['totals']['defectRootCauseCount']))
    print('完整索引（含逐行分类）：%s' % full)
    if args.write_doc:
        DOC.write_text(render_markdown(index), encoding='utf-8')
        print('脱敏索引文档：%s' % DOC)
    return 0


if __name__ == '__main__':
    sys.exit(main())

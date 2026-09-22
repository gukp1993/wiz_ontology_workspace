#!/usr/bin/env python3
"""retrieve 算法优化的剖析与基准（G25 后续 · 需求《retrieve 筛选算法优化》R1/R3/R5/R7）。

* 合成 facts 集（默认 20 万条，确定性 seed），形态对齐生产：
  snippet 200–500 字（CJK + ASCII 标识混排，约 10% 含范围关键词）、data 小字典、
  locator 含 file/line、约 150 条事实同属一份材料。
* scope 为生产形态：include/exclude 多业务词、goal 长句（弱词 gram 数百级）。
* 模式：
    --mode bench    计时 + 内存（默认）：build_index / select_scope 分项 wall 秒、
                    tracemalloc 峰值与 ru_maxrss
    --mode profile  cProfile 热点（build_index+select_scope 合并剖析，
                    按 tottime / cumtime 各列前 N）
* 内存：resource.ru_maxrss（进程峰值 RSS，含解释器基线）与 tracemalloc（Python 分配
  峰值，tracemalloc 会拖慢运行，默认关闭，--mem 开启）。

运行（隔离，不连真实库、不访问网络）：
    .venv/bin/python tests/benchmark_retrieve.py --facts 200000
    .venv/bin/python tests/benchmark_retrieve.py --facts 200000 --mode profile
输出末行 RETRIEVE_BENCH_RESULT 为机器可读汇总（JSON）。
"""
import argparse
import cProfile
import io
import json
import pstats
import random
import resource
import sys
import time
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build import retrieval  # noqa: E402

DEFAULT_FACTS = 200000

_MODULES = ('device', 'cluster', 'pcs', 'bms', 'alarm', 'meter', 'order', 'auth')
_KINDS = ('code', 'ddl', 'docx', 'pdf', 'xlsx', 'md', 'image')

# 通用句域刻意避开范围词（储能电站/电池簇/PCS/BMS/告警处置/设备台账/功率变换/电池管理/
# 收益结算/财务分成/权限管理）——否则绝大多数事实会命中 include，失去「未命中事实 ×
# 弱词扫描」的主导成本场景（P2）。
_GENERIC_SENTENCES = (
    '泵站机组的振动数据按巡检周期归档，口径以现场作业指导书为准，跨班组交接存在分钟级延迟，',
    '风机叶片在低风速工况下的效率曲线出现抖动，需要结合历史工单记录复核参数设定的合理性，',
    '光伏组件的清扫周期与辐照采样窗口并不严格对齐，平台侧的存储批次按站点分别维护，',
    '供热管网的补水流程分为申请、审批与执行三个阶段，执行依赖现场回单的结构化录入，',
    '冷链仓储的温区划分在月度盘点与日常巡检之间存在差异，历史版本的台账仍在部分仓库运行，',
    '交通信号的配时方案经由区域控制器下发，链路抖动会造成短暂的状态缺口，重传策略按路口配置，',
    '水务调度的调度令与执行回执在多个子系统间各自留存，统一目录的改造尚在规划阶段，',
    '票据流转的审核规则由财务共享中心独立维护，本期数据接口只做只读对接，',
)
_KEYWORD_SENTENCES = (
    '电池簇 {n} 的额定容量与实时 SOC 采样写入监测表，簇内电芯温度越限触发告警处置流程，',
    'PCS 功率变换系统 {n} 的有功功率设定与运行模式由能量管理平台下发，告警联动需要台账关联，',
    '储能电站 {n} 的设备台账记录电池簇与 PCS 的从属关系，监测数据按设备维度归档，',
    'BMS 电池管理 {n} 的告警处置记录包含确认人与复归时间，台账字段与运行报表保持一致，',
)
_ASCII_TOKENS = ('capacity', 'soc', 'voltage', 'temperature', 'alarmCode', 'stationId',
                 'clusterNo', 'pcsMode', 'bmsVersion', 'settleFlag')


def _snippet(rng, index, keyword):
    parts = []
    total = 0
    target = rng.randint(200, 500)
    if keyword is not None:
        parts.append(keyword.format(n=index % 97))
        total += len(parts[0])
    while total < target:
        if rng.random() < 0.18:
            token = rng.choice(_ASCII_TOKENS)
            piece = '%s=%d；' % (token, rng.randint(0, 99999))
        else:
            piece = rng.choice(_GENERIC_SENTENCES)
        parts.append(piece)
        total += len(piece)
    return ''.join(parts)


def build_facts(count, seed=20260922):
    """合成 facts（形态对齐 store.list_facts 的 fact_view dict），确定性。"""
    rng = random.Random(seed)
    facts = []
    for index in range(count):
        keyword = None
        roll = rng.random()
        if roll < 0.02:
            keyword = _KEYWORD_SENTENCES[0]
        elif roll < 0.04:
            keyword = _KEYWORD_SENTENCES[1 + index % 3]
        locator_kind = _KINDS[index % len(_KINDS)]
        material_id = 'bm-%06d' % (index // 150)
        facts.append({
            'id': 'bf-%08d' % index,
            'taskId': 'task-bench',
            'materialId': material_id,
            'module': _MODULES[index % len(_MODULES)],
            'locator': {'kind': locator_kind,
                        'file': 'src/pkg%02d/Module%03d.%s' % (index % 40, index % 250,
                                                               locator_kind),
                        'line': index % 4000 + 1,
                        'symbol': 'sym_%d' % (index % 1000)},
            'snippet': _snippet(rng, index, keyword),
            'kind': 'segment%d' % (index % 6),
            'data': ({'page': index % 900, 'keywords': [rng.choice(_ASCII_TOKENS)]}
                     if index % 7 else {}),
            'quality': ('high', 'medium', 'low')[index % 3],
        })
    return facts


SCOPE = {
    'goal': ('梳理储能电站运行管理的核心业务模型，覆盖设备台账、实时监测、告警处置与运行报表，'
             '理解功率变换与电池管理链路，澄清计量与结算的数据边界'),
    'include': '储能电站、电池簇、PCS 功率变换、BMS 电池管理、告警处置、设备台账',
    'exclude': '收益结算、财务分成、权限管理',
    'relations': '园区与电表只保留必要关联，聚焦设备与簇的组成关系',
}


def _rss_mb():
    """进程峰值 RSS（MB）。macOS 的 ru_maxrss 单位是 bytes，Linux 是 KB。"""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == 'darwin':
        return raw / (1024.0 * 1024.0)
    return raw / 1024.0


def run_bench(count, use_tracemalloc):
    facts = build_facts(count)
    scope = dict(SCOPE)
    rss_before = _rss_mb()
    if use_tracemalloc:
        tracemalloc.start()

    t0 = time.monotonic()
    index = retrieval.build_index(facts)
    t_index = time.monotonic() - t0

    t1 = time.monotonic()
    selection = retrieval.select_scope(facts, scope, index)
    t_select = time.monotonic() - t1

    peak_current = peak_peak = 0
    if use_tracemalloc:
        peak_current, peak_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    counts = selection['counts']
    result = {
        'mode': 'bench', 'facts': count,
        'indexSeconds': round(t_index, 2), 'selectSeconds': round(t_select, 2),
        'totalSeconds': round(t_index + t_select, 2),
        'relevant': counts['relevant'], 'related': counts['related'],
        'excluded': counts['excluded'],
        'rssPeakMb': round(_rss_mb(), 1), 'rssBeforeMb': round(rss_before, 1),
        'tracemallocPeakMb': round(peak_peak / (1024 * 1024), 1) if use_tracemalloc else None,
        'withTokens': 'tokens' in index and bool(index.get('tokens')),
    }
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ('mode',)}, ensure_ascii=False))
    print('RETRIEVE_BENCH_RESULT ' + json.dumps(result, ensure_ascii=False))
    return result


def run_profile(count, top):
    facts = build_facts(count)
    scope = dict(SCOPE)
    profiler = cProfile.Profile()
    profiler.enable()
    index = retrieval.build_index(facts)
    retrieval.select_scope(facts, scope, index)
    profiler.disable()
    for sort_key in ('tottime', 'cumtime'):
        out = io.StringIO()
        stats = pstats.Stats(profiler, stream=out)
        stats.sort_stats(sort_key).print_stats(top)
        print('===== cProfile top %d by %s =====' % (top, sort_key))
        # 去掉 pstats 头部的路径噪声，只保留函数表
        lines = out.getvalue().splitlines()
        table_started = False
        for line in lines:
            if line.startswith('ncalls'):
                table_started = True
            if table_started:
                print(line[:160])
    print('RETRIEVE_PROFILE_RESULT facts=%d done' % count)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--facts', type=int, default=DEFAULT_FACTS)
    parser.add_argument('--mode', choices=('bench', 'profile', 'all'), default='bench')
    parser.add_argument('--mem', action='store_true', help='开启 tracemalloc（拖慢运行）')
    parser.add_argument('--top', type=int, default=18)
    args = parser.parse_args()
    if args.mode in ('bench', 'all'):
        run_bench(args.facts, args.mem)
    if args.mode in ('profile', 'all'):
        run_profile(args.facts, args.top)
    return 0


if __name__ == '__main__':
    sys.exit(main())

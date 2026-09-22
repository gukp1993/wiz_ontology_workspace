#!/usr/bin/env python3
"""retrieve 剖析/基准共用合成事实夹具（R1/R7 基建，确定性、纯标准库、不依赖仓库模块）。

为《retrieve 筛选算法优化》剖析报告与基准脚本提供贴近创智源实测分布的合成 facts：
* 默认 276,000 条（对齐创智源 27.6 万事实实测规模，见需求 R1/R3），可调条数与 seed；
  约 26 条事实同属一份材料（对齐创智源 276,256 事实 / 10,819 物料）；
* kind 按 java / xml / markdown / llm-fallback 混合（llm-fallback 同时 module=llm-fallback、
  quality=low，对齐生产兜底产物形态）；
* snippet 为 CJK 长句 + ASCII 标识符混排，长度分五档（<200B 短条至 >4KB 长条）；
* data 嵌套 dict/list 深度 ≤2；locator 带 file/line/symbol；
  （materialId/file/module 同组，供依赖扩展产生真实邻域）；
* 命中分布刻意保住 P2 主导成本场景：约 2.5% 强命中 include、约 0.5% 命中 exclude、
  约 7% 弱词/hints 部分命中，其余为纯「未命中事实 × 弱词逐词扫描」；
* 纯函数、确定性：同一 (count, seed) 永远生成同一集合（random.Random，不依赖系统状态）。

本模块刻意 **不 import workbench.***：可同时服务基线快照（git show 导出的 retrieval.py）
与当前实现的前后对比，加载哪一版实现由调用方决定。
tests/benchmark_retrieve.py（另一提交，d6ed223）自带一套独立夹具；本模块是其生产规模
对齐版（276k 默认、四类 kind、五档长度），两者不互相引用。

运行自检（生成 3000 条并打印分布摘要）：
    .venv/bin/python tests/retrieve_fixture.py
"""
import random
import sys

DEFAULT_FACT_COUNT = 276000
DEFAULT_SEED = 20260920
# 创智源实测：276,256 事实 / 10,819 份解析物料 ≈ 26 条/份，此处对齐。
FACTS_PER_MATERIAL = 26

# kind 组成（java 代码主导对齐创智源 8190 .java/724 .xml 的占比口径；markdown 文档、
# llm-fallback 兜底弱证据为分布假设）。
_KIND_WEIGHTS = (('java', 0.55), ('xml', 0.10), ('markdown', 0.25), ('llm-fallback', 0.10))

# snippet 长度五档（字符目标；UTF-8 下 CJK 约 3B/字，档位对应 <200B / 200–1000B /
# 1–2KB / 2–4KB / >4KB 的量级）。(累计概率, 目标字符数下限, 上限)
_LENGTH_TIERS = ((0.25, 30, 62), (0.70, 80, 330), (0.88, 380, 700),
                 (0.97, 700, 1300), (1.01, 1400, 2600))

# 模块名刻意避开 include/exclude 强词的子串（module 参与可检索文本，module 名含
# pcs/bms 等会让 1/6 的事实「顺带」强命中，破坏 2.5% 强命中分布设计）。
_MODULES = ('station-devices', 'battery-cluster', 'converter-ctrl', 'cell-monitor',
            'alarm-center', 'metering', 'energy-dispatch', 'report-export',
            'order-mgmt', 'auth', 'archive', 'gateway-sync')

# 通用句域：刻意不含 include/exclude 的完整强词（储能电站/电池簇/PCS/BMS/功率变换/
# 电池管理/告警处置/设备台账/收益结算/财务分成/权限管理），否则强命中泛滥、
# 退化成「强词命中」场景而非 P2 的「未命中 × 弱词扫描」主导场景。
_GENERIC_SENTENCES = (
    '泵站机组的振动数据按巡检周期归档，口径以现场作业指导书为准，跨班组交接存在分钟级延迟，',
    '风机叶片在低风速工况下的效率曲线出现抖动，需要结合历史工单记录复核参数设定，',
    '光伏组件的清扫周期与辐照采样窗口并不严格对齐，平台侧的存储批次按站点分别维护，',
    '供热管网的补水流程分为申请、审批与执行三个阶段，执行依赖现场回单的结构化录入，',
    '冷链仓储的温区划分在月度盘点与日常巡检之间存在差异，历史版本的清单仍在部分仓库运行，',
    '交通信号的配时方案经由区域控制器下发，链路抖动会造成短暂的状态缺口，重传策略按路口配置，',
    '水务调度的调度令与执行回执在多个子系统间各自留存，统一目录的改造尚在规划阶段，',
    '票据流转的审核规则由财务共享中心独立维护，本期数据接口只做只读对接，',
    '园区的门禁与访客登记按楼栋分别配置，通行记录的保存期限依合同约定逐条核对，',
    '锅炉给水的电导率采样值每五分钟上报一次，异常波动需要值班员在交接班记录中注明原因，',
    '烟气在线监测数据的小时均值与手工比对样之间的偏差按季度评审，超标时段另行留痕，',
    '计量器具的检定证书到期前三十天由系统提醒，逾期未检的器具自动转入封存清单，',
)

# 弱命中句域：包含 include 强词的 2 字片段（储能/电池/台账/设备/管理/处置/告警等），
# 但不含任何完整强词 → 只触发弱词命中分支（P2 第三循环的真实工作量来源）。
_WEAK_HIT_SENTENCES = (
    '站控层的储能配置与电池巡检记录相互独立，管理口径以现场作业指导书为准，',
    '设备巡视与台账整理分属两个班组，处置流程的回执存在分钟级延迟，',
    '告警确认的值班安排按月度轮换，备品备件的领用记录另行归档，',
    '储能舱的通风联动与消防复核共用一套控制回路，管理权限按班组划分，',
    '巡检仪采集的电池温度明细以周为单位导出，台账编号与现场标签逐条核对，',
    '处置工单的关闭需要双重确认，设备停复役流程与调度指令互为前置，',
)

# 强命中句：完整包含 include 强词（约 2.5% 事实）， 决定 relevant 规模。
_KEYWORD_SENTENCES = (
    '储能电站 S{n} 的电池簇 {c} 额定容量与 SOC 采样写入监测表，簇内电芯温度越限触发告警处置，',
    'PCS 功率变换单元 {p} 的有功功率设定由能量管理平台下发，告警处置联动设备台账，',
    'BMS 电池管理单元 {b} 的告警处置记录包含确认人与复归时间，台账字段与运行报表一致，',
    '设备台账导出含电池簇与 PCS 的从属关系，储能电站 {n} 的运行日报按簇汇总，',
)
# 排除命中句：完整包含 exclude 强词（约 0.5% 事实）。
_EXCLUDE_SENTENCE = '电费收益结算与财务分成口径由经营侧另行维护，权限管理流程不在本期对接范围，'

# kind 风味行：拼进 snippet 增加形态真实性（代码/标签/标题/兜底声明），同时提供
# 额外 ASCII 标识符参与 tokenize/子串扫描成本。
_JAVA_LINES = (
    'public class ClusterService{n} implements StatusListener {{ void onSample(Sample s); }}',
    'private static final int RATED_CAPACITY_{n} = 280;',
    '// TODO: retry window for cellDelta report',
)
_XML_LINES = (
    '<cluster id="c{n}"><field name="voltage" value="3.42"/><field name="soc" value="0.87"/></cluster>',
    '<alarm code="{n}" level="2" station="S{n}"/>',
)
_MD_LINES = (
    '## 运行日志第{n}章',
    '- 巡检口径与移交清单见附录 {n}',
    '**注意**：本节字段以 DDL 为准，示例值仅作说明。',
)
_FALLBACK_LINES = (
    '（模型兜底解析，弱证据）片段提到运行值班安排，未给出结构化字段。',
    '（模型兜底解析，弱证据）原文为扫描件转写，字段完整性未经校验。',
)

_ASCII_TOKENS = ('capacity', 'voltage', 'temp', 'alarmCode', 'stationId', 'clusterNo',
                 'cellDelta', 'gridFreq', 'settleFlag', 'reportSeq')

_DATA_POOLS = (
    None,
    {'page': 0},
    {'sheet': 'Sheet1', 'rows': 0},
    {'keywords': ['capacity', 'voltage']},
    {'meta': {'sheet': '运行日报', 'row': 0}, 'keywords': ['stationId']},
    [{'col': 'voltage', 'unit': 'V'}, {'col': 'temp', 'unit': 'C'}],
)


def _pick_kind(rng):
    roll = rng.random()
    for kind, weight in _KIND_WEIGHTS:
        roll -= weight
        if roll < 0:
            return kind
    return 'llm-fallback'


def _pick_target(rng):
    """按总体占比 25%/45%/18%/9%/3% 选长度档，返回目标字符数。"""
    roll = rng.random()
    for threshold, low, high in _LENGTH_TIERS:
        if roll < threshold:
            return rng.randint(low, high)
    return rng.randint(_LENGTH_TIERS[-1][1], _LENGTH_TIERS[-1][2])


def _snippet(rng, index, kind, force):
    """组装一条 snippet：长度分档 + kind 风味行 + 命中句注入。force ∈
    ('include', 'exclude', 'weak', None) 控制命中句注入类型。"""
    target = _pick_target(rng)
    parts = []
    total = 0
    if force == 'include':
        piece = _KEYWORD_SENTENCES[index % 4].format(n=index % 97, c=index % 12,
                                                     p=index % 31, b=index % 17)
        parts.append(piece)
        total += len(piece)
    elif force == 'exclude':
        parts.append(_EXCLUDE_SENTENCE)
        total += len(_EXCLUDE_SENTENCE)
    elif force == 'weak':
        piece = _WEAK_HIT_SENTENCES[index % len(_WEAK_HIT_SENTENCES)]
        parts.append(piece)
        total += len(piece)
    flavor = {'java': _JAVA_LINES, 'xml': _XML_LINES, 'markdown': _MD_LINES,
              'llm-fallback': _FALLBACK_LINES}[kind]
    parts.append(flavor[index % len(flavor)].format(n=index % 997))
    total += len(parts[-1])
    while total < target:
        roll = rng.random()
        if roll < 0.16:
            piece = '%s=%d；' % (rng.choice(_ASCII_TOKENS), rng.randint(0, 99999))
        else:
            piece = rng.choice(_GENERIC_SENTENCES)
        parts.append(piece)
        total += len(piece)
    return ''.join(parts)


def iter_facts(count=DEFAULT_FACT_COUNT, seed=DEFAULT_SEED):
    """逐条产出合成 fact（生成器；同一 (count, seed) 产出序列恒定）。"""
    rng = random.Random(seed)
    for index in range(count):
        kind = _pick_kind(rng)
        is_fallback = kind == 'llm-fallback'
        roll = rng.random()
        if roll < 0.025:
            force = 'include'
        elif roll < 0.030:
            force = 'exclude'
        elif roll < 0.100:
            force = 'weak'
        else:
            force = None
        material_seq = index // FACTS_PER_MATERIAL
        module = 'llm-fallback' if is_fallback else _MODULES[material_seq % len(_MODULES)]
        ext = {'java': 'java', 'xml': 'xml', 'markdown': 'md', 'llm-fallback': 'txt'}[kind]
        data = _DATA_POOLS[index % len(_DATA_POOLS)]
        if isinstance(data, dict) and 'page' in data:
            data = dict(data, page=index % 900)
        if isinstance(data, dict) and 'rows' in data:
            data = dict(data, rows=index % 4000)
        yield {
            'id': 'bf-%08d' % index,
            'taskId': 'task-bench',
            'materialId': 'bm-%06d' % material_seq,
            'module': module,
            'locator': {
                'kind': kind,
                'file': 'src/pkg%02d/mat%05d.%s' % (material_seq % 40, material_seq, ext),
                'line': index % 4000 + 1,
                'symbol': 'sym_%d' % (index % 1000),
            },
            'snippet': _snippet(rng, index, kind, force),
            'kind': kind,
            'data': data if data is not None else {},
            'quality': 'low' if is_fallback else ('high', 'medium')[index % 2],
        }


def iter_fact_batches(count=DEFAULT_FACT_COUNT, batch_size=5000, seed=DEFAULT_SEED):
    """分批产出合成 facts（内存友好：每批 batch_size 条的列表）。"""
    batch = []
    for fact in iter_facts(count, seed):
        batch.append(fact)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def build_facts(count=DEFAULT_FACT_COUNT, seed=DEFAULT_SEED):
    """一次性构建全部合成 facts（select_scope/build_index 需要完整列表时使用）。"""
    return list(iter_facts(count, seed))


# 范围样例：四字段（include/exclude/goal/relations），中英混合，贴近真实用户输入。
# include 为生产常见的「短语 + 长尾句」口吻：前半是可命中的短词表（供强词命中），
# 尾部长中文串（>12 字）切出上百个 2–3 字弱 gram——对齐 P2「四段合计弱词可达上百、
# 未命中事实 × 上百次子串扫描」的主导成本场景（select_scope 第三循环只扫
# include 弱词 + hints，goal/relations 的弱词会被丢弃，故弱 gram 必须落在 include）。
PRIMARY_SCOPE = {
    'include': ('储能电站、电池簇、PCS 功率变换、BMS 电池管理、告警处置、设备台账相关片段均需纳入，'
                '储能配置电池采样设备台账告警处置与运行管理记录均纳入本次建模范围并对疑似关联片段作出标注'),
    'exclude': '收益结算、财务分成、权限管理',
    'goal': ('梳理储能电站运行管理的核心业务模型，覆盖设备台账、实时监测与告警处置链路，'
             '澄清电池簇、PCS 与 BMS 的采样数据边界，理解功率变换设备的运行模式与告警联动机制，'
             '为后续对象建模与属性映射提供依据'),
    'relations': ('园区电表与储能电站只保留必要关联，聚焦电池簇与 PCS 的组成关系，'
                  '忽略运维工单与巡检记录'),
}
SCOPE_SAMPLES = {
    'storage-primary': PRIMARY_SCOPE,
    'billing-trim': {
        'include': '结算单元、电价方案、分成规则',
        'exclude': '储能电站、电池簇、设备台账',
        'goal': '梳理收益结算与财务分成的口径，覆盖电价方案版本与结算周期，澄清计量点与结算单元的归属',
        'relations': '结算单元与电表按计量点关联，忽略告警与巡检',
    },
    'mixed-en': {
        'include': 'PCS、BMS、EMS 调度指令、组串逆变配置',
        'exclude': '报表导出、权限管理',
        'goal': ('理解 PCS 功率变换与 BMS 电池管理的采样链路，澄清 EMS 调度指令与机群 '
                 '运行参数的边界，为容量配置建模提供依据'),
        'relations': '机群与 PCS 按从属关系关联，电表只保留必要关联',
    },
}


def _self_test(count=3000):
    """自检：确定性、分布摘要（供人工核对，不依赖仓库模块）。"""
    first = build_facts(count)
    second = build_facts(count)
    assert first == second, '确定性被破坏：同 seed 两次生成不一致'
    streamed = []
    for batch in iter_fact_batches(count, 700):
        streamed.extend(batch)
    assert streamed == first, '分批与整建生成不一致'
    kinds = {}
    tiers = {'<200B': 0, '200-1000B': 0, '1-2KB': 0, '2-4KB': 0, '>4KB': 0}
    total_bytes = 0
    for fact in first:
        kinds[fact['kind']] = kinds.get(fact['kind'], 0) + 1
        size = len(fact['snippet'].encode('utf-8'))
        total_bytes += size
        if size < 200:
            tiers['<200B'] += 1
        elif size < 1000:
            tiers['200-1000B'] += 1
        elif size < 2048:
            tiers['1-2KB'] += 1
        elif size < 4096:
            tiers['2-4KB'] += 1
        else:
            tiers['>4KB'] += 1
    modules = {fact['module'] for fact in first}
    print('self-test OK: %d facts, deterministic, batch==bulk' % count)
    print('kinds: %s' % kinds)
    print('modules: %d distinct (llm-fallback included)' % len(modules))
    print('snippet bytes tiers: %s' % tiers)
    print('avg snippet bytes: %d' % (total_bytes // max(1, count)))
    return 0


if __name__ == '__main__':
    sys.exit(_self_test())

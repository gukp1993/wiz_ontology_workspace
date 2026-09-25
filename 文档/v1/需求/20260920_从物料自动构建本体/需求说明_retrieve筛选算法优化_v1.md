# 从物料自动构建本体 · retrieve 筛选算法优化 · 需求说明 v1

| 项 | 内容 |
|---|---|
| 日期 | 2026-09-22 |
| 状态 | **待评审**。本文的"应"描述验收目标；评审通过前不提交、不开发。实施交执行方（用户指定 harness 或本侧开发/测试循环）。 |
| 关联 | [需求说明_v2.md](需求说明_v2.md)（§7 生成逻辑、G23–G25）· [开发计划](开发计划.md) §11（27.6 万事实筛选约 40 分钟实测数据点）· 实现基准 `workbench/ontology_build/retrieval.py`（295 行，worktree/build-governance@3dc51dc 线） |

## 1. 背景与实测问题

创智源任务实测：27.6 万事实的生成 run 中，**retrieve（筛选材料片段）阶段约 40 分钟、单核满载**（开发计划 §11 数据点），是大规模物料下生成阶段的第一长杆。以下归因基于 `retrieval.py` 现源码（295 行）逐段核对：

| # | 归因 | 代码事实 |
|---|---|---|
| P1 | **死工作：token 倒排索引零消费**。`build_index` 对每条事实全文做正则切分（`index_tokens`）并构建 `tokens: {token: [factId]}` 倒排桶，但生成路径（`pipeline.run_generate`）只消费 `text/byModule/byFile/snippetHash/order`——**`tokens` 桶零读取**（全仓 grep 无消费方）。正则 tokenize 是 build_index 的主要成本之一，属纯浪费 | retrieval.py:98-141（构建）、全仓 grep 无 `'tokens'` 读取 |
| P2 | **逐词子串扫描主导**。`select_scope` 第三循环对每条**未命中**事实做 include_weak（2–3 字 gram）+ hints 的逐词 `term in text` 子串扫描。一条普通 goal 长句即切出 35+ 个弱 gram（实测），include/exclude/goal/relations 四段合计弱词可达上百；27.6 万条未命中事实 × 上百次子串扫描 × KB 级文本 = 数千万次扫描——主导成本 | retrieval.py:178-278、实测弱词计数 |
| P3 | **双遍构建 + 全量文本驻留**。`searchable_text`（casefold 拼接）在 build_index 内对每条事实执行并存入 `texts` 驻留内存（27.6 万 × KB 级 ≈ 数百 MB 量级），select_scope 再按 id 取用——两遍遍历 + 高内存水位 | retrieval.py:98-141、pipeline.py 调用序 |
| P4 | **逐词收集无早停**。`_hits` 对词表全量收集（即使第一个词已命中可定级），命中判定与理由展示词收集未分离 | retrieval.py:174-175 |

## 2. 优化方案

四项优化，全部**行为保持**（§3 等价性约束）：不改任何事实的 relevant/related/excluded 归属、不改 reasons 文本、不改 counts。

### O0 消除死工作（对应 P1）

生成路径的索引构建不再产出 token 倒排桶：
- `build_index` 增加 `with_tokens=False` 参数（默认 False），仅显式传入时才构建 tokens 桶；
- 全仓 grep 确认当前无消费方；若后续出现消费方（如向量召回挂点），参数化恢复。
- 验收：生成路径索引构建耗时显著下降（以剖析报告为证，R1/R4）。

### O1 单遍融合（对应 P3）

generate 路径将 `build_index` + `select_scope` 融合为**单遍流水**（新内部函数或重排，不改对外语义）：每条事实计算一次 `searchable_text`，同步完成 byModule/byFile 桶登记、digest 计算、include/exclude 强词命中裁决与归类、**弱词/hints 命中探测与理由文本生成（同一遍内完成，按 factId 暂存理由串或命中词表——命中极少数，暂存成本可忽略），文本随后即弃**；未归类事实的归属在依赖扩展后由第三循环按序赋已暂存理由（文本已弃不影响）。依赖扩展阶段（同 module/file 带出）在第二小节基于桶进行——桶保留；文本已弃不影响该阶段（其只需桶与归类结果）。

### O2 命中检测单趟化（对应 P2）

将 include/exclude/weak/hint 各词表分别编译为**合并正则**（`re.compile('|'.join(map(re.escape, terms)))`，标准库，无新依赖），每条事实文本做一次正则扫描替代逐词 `in` 循环。**合并正则仅作早停探测**（任一命中即定级）；命中事实的 reasons 展示词必须按**原词表顺序、原 `term in text` 成员判定**复算补齐（合并正则 findall 为非重叠匹配，与逐词成员判定在重叠词/顺序上存在差异，不得直接以 findall 结果拼接 reasons）——保持 reasons 文本与现版逐字一致。

### O3 命中判定早停（对应 P4）

定级判定找到**第一个**命中即定级（include 命中 → relevant；否则 exclude 命中 → excluded）；reasons 展示词在已定级事实范围内按原词表顺序补齐（命中事实是少数，成本可控）。保守保留循环的弱词/hints 扫描同理：先合并正则探测任一命中，再按需收集展示词。

## 3. 确定性等价性约束（硬性）

同一 facts + scope 输入，优化前后的 `select_scope` 返回**完全一致**：

- relevant / related / excluded 三个列表（含元素顺序）；
- reasons 全文（逐 fact 逐字）；
- counts。

裁决语义不变：弱词绝不判 relevant/excluded；排除项绝不因依赖扩展复活；重复副本只计一次佐证；文件名/扩展名不参与过滤。以**金样对比测试**断言（同一夹具分别跑旧实现与新实现，输出逐字节对比；旧实现快照进测试夹具）。

## 4. 明确不做

- 不引入向量化/语义检索（另行评估，见需求说明_v2 §14.1 与 [自动化构建本体方法论_20260921.md](自动化构建本体方法论_20260921.md) §7.2）；
- 不引入第三方库（Aho-Corasick 等合匹配库；标准库 re 已足够，O2 即可达成目标）；
- 不对 retrieve 做进程池并行（先算法后并行；进程池涉及 facts 序列化开销，另行评估）；
- 不改裁决语义与对外 API 签名（`select_scope`/`build_index` 的输入输出形态不变）；
- 不动 LLM 兜底与结构化格式解析线在途文件（`parsers/`、`blacklist.py` 等——该线已提交，实施时以最新 main/分支状态为准核对文件归属）。

## 5. 验收条

| 编号 | 条件 |
|---|---|
| R1 | **剖析报告先行**：以 ≥20 万事实合成集（或真实创智源数据集）对现状做剖析（cProfile 等价手段），报告 tokenize / 子串扫描 / 内存驻留的耗时占比；各项优化须对应实测热点，不对应热点的优化项删除或降级为可选项 |
| R2 | **功能等价金样**：同一夹具（小样手构造 + 大样合成集）分别运行优化前/后实现，selection 三列表与 reasons 逐字节一致；金样测试入库可重复运行 |
| R3 | **性能目标**：同规模合成集（≥20 万事实）retrieve+index 总耗时 ≤ 5 分钟（基线约 40 分钟；工程预算目标，非倍数承诺），实测数字如实记入开发计划 |
| R4 | **死工作消除**：生成路径不再构建 tokens 桶（或懒构建），有断言覆盖 |
| R5 | **内存水位**：峰值 RSS 较基线下降，实测数字记录（不做硬阈值承诺） |
| R6 | **回归**：tests/test_ontology_build.py 全绿（现有 retrieve 相关用例不得回归）+ O2 新增正则编译的边界用例（空词表/单词条/含正则元字符的词条经 re.escape 处理） |
| R7 | **基准脚本入库**：可重复运行的性能对比脚本随代码提交 |

## 6. 待定项（不作为承诺）

- retrieve 进程池并行化（算法优化后视剩余耗时再评估）；
- 向量召回（需求说明_v2 §14 已列）；
- 检索语义升级（语义匹配/改写召回）。

## 7. 实施约束与交付

- Python 3.9 标准库实现；纯函数、不调用模型、确定性不变；
- 预期改动文件：`workbench/ontology_build/retrieval.py`（主）+ `pipeline.py`（调用点适配，如 O1 融合需要）+ 新增测试文件；与在途任务线的文件交集：`pipeline.py` 存在结构化解析线（c55d94a）与抽取批次并发（573e51a，2026-09-22）两拨改动、`tests/test_ontology_build.py` 亦被其修改——**实施前按最新分支状态核对文件归属**，基线 SHA 以实施时 HEAD 为准；
- 实施约束补充：既有 V2-8 复用探针桩（tests/test_ontology_build.py:1377-1398，以模块属性补丁拦截 `retrieval.build_index/select_scope` 证明确定性阶段未重算）在 O1 引入融合入口后**须同步覆盖新融合入口**，否则探针静默失效；O0 的 `with_tokens=False` 使返回 dict 缺省 tokens 键（内部函数、已证实零消费方，docstring 注明即可）。
- 契约影响：无对外 API 变化（`build_index`/`select_scope` 为内部函数），08 分册无需变更；若实施中需要新增对外展示（如检索耗时），另行提请；
- 交付物：实现 + 金样等价测试 + 剖析报告 + 基准脚本 + 开发计划实施记录；完成报告须含逐项 SHA 与 R1–R7 对照。

# Codex / zcode 共享上下文

上下文版本：`d7d7ac3f5c16b29b`

> 此文件由 `.collaboration/context.py` 生成，请勿手工覆盖。
> 记录是各执行者的交接声明；“已实施”不等于“已验收”。同任务双方结论分开展示。
> 最近 12 个“执行者 + 任务”状态见下；更早记录在 `.collaboration/entries/`，未完成项可能在历史中。

## 当前有效基线（2026-09-18 核对）

- 分工：Codex 负责讨论、需求、原型与验收；zcode 负责按已确认版本实施。需求变动须显式交接，不在实施中自动替换范围。
- 项目是 Git 仓库；工作台常用地址为 http://127.0.0.1:18765。技术栈仍是 Vue 3 + TypeScript 与 Python HTTP。
- 在线权威存储已切到 SQLite，SQLAlchemy Core + Alembic；旧 ontology 文件是迁移/备份材料。MySQL 仅预留，未完成运行验证。以 AGENTS.md 最新存储约定及源码为准。
- 本体定义与具体项目映射分开；项目固定引用已发布本体版本。草稿保存与发布分开，不能自动升级引用或覆盖不可变发布。
- 本体包含对象、属性、链接及后续规则/动作能力；具体 SQL、Redis、函数与编排实现在项目/执行层。精确字段以当前接口文档和需求版本为准。
- 需求归档在 文档/需求/；通常包含原型、需求说明、开发计划、执行指令。共享上下文只记有效决定和状态，不重复完整方案。
- 不覆盖其他工具的未提交修改；不改真实用户数据、密钥或数据库。测试使用隔离目录，注意 WIZ_DATABASE_URL 也必须隔离。
- 2026-09-18 审查修改意见已交付：文档/代码审查修改意见_20260918.md。设置交接机制时看到后续代码已有导出/恢复及错误处理修改，不能继续把旧缺陷全部标为待修；本轮未重新验收这些业务修复。
- 9 月 15 日旧共享上下文已完整归档到 文档/需求/20260918_共享上下文自动交接/历史共享上下文_截至20260915.md；仅供历史追溯，不作为当前事实。

- 2026-09-20 最新分支约定：用户明确发出创建worktree指令后由zcode创建独立分支/目录/环境；开发与修复复用该环境，Codex独立验收。验收通过停在“待用户授权集成”；只有用户明确要求集成并合并，Codex才串行集成重验并更新main。可一次明确授权多个阶段，不重复请示；临时集成worktree包含在合并授权内。集成验证和合并成功后自动停止本人服务，清理该任务开发/临时集成worktree、已合并分支及登记可丢弃的隔离数据，无需另发清理指令；异常或需保留内容明确报告，不强删。主工作台更新另行授权。当前main未提交开发不自动搬移/stash。后续计划与指令自包含AGENTS标准提示词；这是协作规则，不是自动化服务。

## 最近交接（新 → 旧）

### auto_build-output-budget-v1 方案交付 · codex · 需求已交付

时间：2026-09-22T07:18:24.312855+00:00；记录：`.collaboration/entries/000188-daf784c7d17e.json`

交付输出预算分批方案与执行指令，供其他harness实施。仅文档，未改业务代码、未做模型实测或业务验收。依据Semantica固定源码与Palantir官方资料，明确局部抽取与程序组装机制，未发现可保证首次输出不截断的精确预测机制。

- 决定：新计划采用语义单元、输入输出双预算、超限细分、成功叶持久化、失败续跑和完整性守卫；不静默截前N条。；保留v1任务兼容，v2有容量/调用上限；不声称100MB原文件已全量解析。；开发前核对在途owner与实际SHA，契约先行、独立任务并行；待用户下达实施指令，不合并main。
- 验证：只读核对代码与公开资料；文档核对授权停止点、任务表、执行顺序、验证矩阵。业务测试尚未实施。
- 下一步：执行harness落实热点文件交接，按方案开发自测提交，再交Codex独立验收。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/输出预算分批_方案与开发计划_v1.md；文档/需求/20260920_从物料自动构建本体/输出预算分批_执行指令_v1.md

### auto_build-输出预算分批落地方案v1 · codex · 需求已交付

时间：2026-09-22T07:13:14.720325+00:00；记录：`.collaboration/entries/000187-d02f3b764c8e.json`

按用户要求交付其他harness可执行的方案与开发计划、执行指令两份文档。范围为语义单元、双预算估算、反馈调批、有界v2检查点、单attempt记账、叶任务独立提交、超限拆分与覆盖守恒；不承诺零截断或100MB解析全覆盖。文档包含并行任务/唯一文件owner、接口变更、验收矩阵和自包含分支集成授权边界。本轮未实施业务或运行模型。

- 决定：第一版复用现有checkpoint_json并有界保护，不新增表或真实库迁移；默认开关关，隔离验证开启。；预算绑定providerId/model；同步422在任何状态写入前；为在途结果和受阻终态预留checkpoint空间。；源码仍有其他owner在途六文件与三测试，接手须先交接实际已提交基线，不覆盖或代为提交。
- 验证：协调者与两条只读agent核对规划/调用/恢复/存储/API现状；未以旧审核缺陷假称当前未修。；规格复审修正profile绑定、422前置与检查点预留；两文档链接/代码围栏/授权流程检查通过。；纯文档交付；未运行开发测试、前端构建、浏览器验收或真实模型调用。
- 下一步：用户将执行指令交给指定harness后，在已登记auto_build按文件归属实施自测提交，停在待Codex验收，不合并main。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/输出预算驱动分批_落地方案与开发计划_v1.md；文档/需求/20260920_从物料自动构建本体/输出预算驱动分批_执行指令_v1.md

### auto_build-输出Token膨胀专项分析 · codex · 已确认决定

时间：2026-09-22T06:45:10.590567+00:00；记录：`.collaboration/entries/000186-6c1aa459d9f9.json`

仅分析输出Token膨胀，未展开此前五阶段整改。原文推理/格式/元数据方向有价值，但字符占比不是token占比，输入形态与批大小仍影响输出。建议先补安全usage观测并对照low推理档，再验证保留语义节点/受支持本体确定性映射与模型专用紧凑协议；不承诺节省比例。已交专项分析文档，未改业务代码、未读取真实数据或调用模型。

- 验证：代码805e1d9：llm_client未设置reasoning_effort且未返回provider usage；llm usage仅bytes。；JSON通用数组只留结构摘要；模型响应已有标准化边界，内部压缩不必变更前后端候选协议。；2026-09-22查Z.ai官方：GLM5.3Flash不支持禁用思考，effort支持low/high/max，默认max；实际endpoint未读取、参数未实测。
- 下一步：实验建议尚未执行；先固定质量与用量基线，单因素比较推理档、协议、输入表示，再调批次。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/输出Token膨胀_专项分析意见_Codex_20260922.md；https://docs.z.ai/guides/capabilities/thinking

### Codex 五阶段方案审核意见分析（R1-R5 逐条独立复现） · zcode · 已确认决定

时间：2026-09-22T06:27:02.893418+00:00；记录：`.collaboration/entries/000185-f5adf7220be7.json`

对 Codex 审核意见（五阶段方案审核意见_Codex_20260922.md）逐条独立复现。**5 条 P1 全部复现成功**：R1 跨批临时键冲突致属性宿主错误（电池/逆变器各带同 key 属性，对齐后 2 条并为 1 条，alignedKey 错记为 @电池）；R4 拆批半失败被判父批成功（左成右败仍返回 ok=True，调用方据 ok 记 done → 该半批永久缺失）；R5 非连续失败批续跑漏落库（done={2}/pending=[1,3] 时仅批1回调落库、批3 静默丢弃；终局验证 accumulated 非空且 failed_batches 为空 → 运行继续并报成功 = 静默数据丢失且重试不补）；R2 冲突项默认纳入（跨批降级 conflict 但 decision 保持 include、reviewed/reason 空，交付端仅按 include 选取）；R3 去重误剔（battery.voltage=220 与 motor.power=220 因 snippet 哈希相同被剔 1 条，对外 reporting duplicates=0）。另核实第4节批评：429「降挡重排」表述与代码不符（只减并发+冷却，未放回 queued）；JSON 修复确会追加第2次调用故 390 秒非全局上限。

- 决定：R1-R5 均为代码级正确性缺陷且已独立复现，应作为修复生成可靠性前的阻断项，优先级高于进度展示与 429 体验类问题。；R5 性质最严重：静默数据丢失且谎报成功（缺整批候选但运行 succeeded），重试不会补。；审核意见第4.1条质疑成立：原瓶颈论证是同一数据回算且未计 reasoning；后续实测已发现 reasoning_content 占输出约 50%（8676 vs 3007 字符），该发现尚未写入方案文档（reasoning 命中 0 次），需补充。
- 验证：R1 /tmp/repro_r1.py：4 候选→对齐后 3，属性 2→1，alignedKey=property:额定功率#number@电池。；R4 /tmp/repro_r4.py：调用序列 [全批,左半,右半,右半重试]，返回 ok=True、split={'from':4,'halves':[2,2]}。；R5 /tmp/repro_r5.py 与 repro_r5_final.py：flush=[1]、残留 results=[3]；终局 done=[1,2]、failed=[] → 报成功。；R2 /tmp/repro_r2.py：二次 verify 后 evidenceStatus=conflict、decision=include、reviewed=None、reason=None。；代码与第三例：pipeline.py:1191-1197/236-249/157-176/906-907/1314；retrieval.py:344-346；delivery.py:48-52（R3 复现见 /tmp/repro_r3.py，哈希同为 f37062d9a65543a4…，duplicates=0）。
- 下一步：待用户裁决是否按 R1-R5 出整改需求；本轮仅分析，未改任何业务代码。；方案文档待补：reasoning_content 占输出 50% 的实测、第4.1条论证修正、429/390秒口径修正。；复现脚本在 /tmp，未入库；如需留证应移入 tests/ 或证据目录。
- 依据/文档：审核意见：文档/需求/20260920_从物料自动构建本体/五阶段方案审核意见_Codex_20260922.md；被审文档：同目录 生成五阶段完整方案_20260922.md（869 行）；代码基线：codex/auto_build @ 578acd7

### auto_build-五阶段方案审核 · codex · 已确认决定

时间：2026-09-22T06:13:40.153468+00:00；记录：`.collaboration/entries/000184-3abd0f203dc9.json`

五阶段职责可保留，建议修改后复审。确认跨批引用误合并、冲突默认纳入、同值事实去重丢失、拆批半失败判成功、非连续续跑漏回调。仅交付审核文档；未改原方案和业务代码，未合并或更新服务。

- 验证：源码基线578acd7d47e2e457757e088110f2582ae261cbb0。；纯函数合成数据复现引用误合并、冲突include、同值去重丢失；Future替身复现非连续续跑漏回调及429不重排。；拆批部分失败由控制流核实。未访问真实数据或外部模型，未做完整回归或浏览器验收。
- 下一步：先修订方案与正确性复验场景；开发集成按后续授权。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/五阶段方案审核意见_Codex_20260922.md

### retrieve筛选优化-剖析与基准基建（codex/build-progress-log） · zcode · 已实施，待验收

时间：2026-09-22T04:35:04.133748+00:00；记录：`.collaboration/entries/000183-29b8b3b3c3ef.json`

R1/R3/R5/R7 剖析与基建交付：①新增 tests/retrieve_fixture.py（生产对齐合成夹具，默认 276k 条≈26 条/物料、四类 kind、五档长度、PRIMARY_SCOPE 弱词 85+hints 14 对齐 P2 扫描口径；纯标准库确定性，ruff 通过，不 import workbench）；②剖析报告 retrieve优化剖析报告_v1.md：基线=3dc51dc 快照（git show 导出仓库外加载，未改任何仓库文件），实测+外推——基线 build_index 占总耗时 96.7-98.8% 且呈幂指数≈2.0-2.4 膨胀（token 桶 fact_id not in bucket 线性查重随桶长线性增长），276k 外推 40-60 分钟与生产实测 40 分钟同量级吻合；优化后(retrieval.py@964493a) 276k 实测 45.29s（R3≤5min 达成）、峰值 RSS 919.6MB、texts 282.6MB 驻留未变（O1 未实施）。select 基线与优化版持平（合并正则≈逐词 in）；意外热点=依赖扩展全桶扫描二次项（select 13%→42% 随规模升）。③基准脚本 tests/benchmark_retrieve.py 已由实施线先期提交（d6ed223），本轮按写入归属未覆盖；发现其 --mode profile 空表缺陷（pstats 行带缩进致 startswith('ncalls') 恒假）与 --concurrency 占位缺口，留给脚本 owner。

- 决定：不覆盖在途线已提交的 tests/benchmark_retrieve.py 与等价金样（owner 归属）；剖析基线按需求锁定 3dc51dc 快照而非测量时 HEAD（HEAD 已前移但 retrieval.py 未再变，git diff 核实）；夹具 scope include 采用生产口吻长尾句（弱 gram 必须落在 include——select 第三循环只扫 include 弱词+hints，goal/relations 弱词被基线丢弃）；初轮矩阵部分 wall 点受并发残留干扰已识别，引用数字均经空机复测；RSS/profile 占比对争用不敏感沿用原矩阵
- 验证：ruff check tests/retrieve_fixture.py 通过（含 E4/E7/E9/F/B）；夹具自检：同 seed 确定性、分批==整建、分布符合设计（3000 条）；三方等价哨兵：inst==base 逐字节一致（计时副本可信）、head==base selection 逐字节一致（3 组 scope×2000 条）、head tokens 桶为空；关键 wall 点空机复测：base 20k index 9.1s（原 22.5s 判为干扰剔除）、base 40k 47.0s 双跑、head 276k 45.29s；幂律拟合 6 点：p≈2.01（残差包络 28-60 分钟区间）
- 下一步：脚本 owner：benchmark_retrieve.py profile 空表一行修复 + --concurrency 占位（V2-10 复用）；O1 单遍融合未实施——报告 §3.4/§6.4 已留对照基线（texts 282.6MB 驻留、双遍）；依赖扩展二次项（select 42.7s 中约半）为 O0-O3 未覆盖的后续算法轮次候选；剖析脚本现居 /tmp/prof（仓库外），是否入库由集成负责人/用户决定
- 依据/文档：worktree/build-governance/tests/retrieve_fixture.py；文档/需求/20260920_从物料自动构建本体/retrieve优化剖析报告_v1.md；worktree/build-governance/tests/benchmark_retrieve.py（在途线 d6ed223，未改动）
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### ontology-build-retrieve-optimize（retrieve 算法优化，worktree/build-governance） · zcode · 已实施，待验收

时间：2026-09-22T03:32:02.816815+00:00；记录：`.collaboration/entries/000182-ba6ddcc2eb3a.json`

retrieve 优化 O0/O2/O3 实现完成（协调者并行拆分后我的范围）：O0 build_index 增 with_tokens=False（默认跳过 tokens 死工作，键恒存在空 dict）；O2 四词表合并正则（re.escape+交替）单趟命中探测，展示词按原词表顺序原 term-in-text 判定经 _hits 复算（S1 边界：不用 findall 拼 reasons）；O3 定级早停。调用点零改动（pipeline.run_generate 不变即生效）。等价金样 15/15 逐字节一致（内嵌 3dc51dc 基线快照），全量回归 250/250×2 + 全部相关套件绿。O1 单遍融合按协调指示挂起，待并行剖析 agent 的归因报告（文本驻留 vs 子串扫描占比）决策；texts 共享无重复构建已由金样断言 12 确认。范围说明：R1 剖析/R3 性能/R5 内存数字归并行专项 agent；原派发的 200k 基线跑批已按协调意图中途停止（避免同机双份跑批互扰）。

- 决定：合并正则仅作早停探测，展示词一律 _hits 原序复算（S1：findall 非重叠匹配与逐词成员判定在重叠词上不等价）；O1 挂起而非实施：等并行剖析归因数据；texts 共享无重复构建的核对部分已完成（金样断言）；全部实现收在 retrieval.py 内部 + 新增测试/基准文件，调用点与 pipeline.py/protocol.py 零改动
- 验证：tests/test_retrieve_equivalence.py 15/15：手构造 18 条×8 组 scope + 合成 8000×3 组 scope，selection（三列表含顺序+reasons 逐字+counts）json sort_keys 逐字节一致；O0 断言（默认无 tokens/显式恢复/桶结构与 searchable_text 同基线）；tests/test_ontology_build.py 250/250（连续 2 次）；parsers 95/95、struct_parsers 143/143、parser_wiring 97/97、struct_e2e 87/87、late_write 23/23、finish_guard 27/27、runner_isolation 34/34、materials_views 10/10、task_purge 17/17、exclusion_inheritance 17/17、merge_refs 42/42、storage_contract 60/60、storage_transfer 27/27；ruff 全过；V2-8 复用探针桩（test_ontology_build:1377-1398）无需变更：未新增检索入口（S3 满足）；基准脚本 tests/benchmark_retrieve.py 入库（bench/profile 两模式，RETRIEVE_BENCH_RESULT 可读输出）；R1/R3/R5 数字归并行 agent
- 下一步：待并行剖析 agent 交付 R1 归因报告后决策 O1 实施或降级（协调者转发）；R3 性能目标（≤5 分钟）与 R5 内存水位由并行 agent 数字补录开发计划；分支归属说明供协调者知晓：retrieve 实现本体经由共享检出被并行线捆包提交为 964493a（retrieval.py 与我工作区实现逐字节一致，已核对）；开发计划记录原落 build-progress-log（cb64855），已以 plumbing 快进补录 build-governance（78cf7df，不影响共享检出）；金样/基准脚本 d6ed223 两分支皆有
- 依据/文档：codex/build-governance：78cf7df（开发计划记录）/964493a（实现本体，retrieval.py 含我的实现）/d6ed223（金样+基准脚本）；codex/build-progress-log：cb64855（同一开发计划变更，与 78cf7df 内容一致）；workbench/ontology_build/retrieval.py、tests/test_retrieve_equivalence.py、tests/benchmark_retrieve.py

### build-governance 生成进度实时可观测·前端（F1/F2/F3） · zcode · 已实施，待验收

时间：2026-09-22T03:27:44.998585+00:00；记录：`.collaboration/entries/000181-c16847392deb.json`

进度页新增批次状态行（✓批N/✗批N：原因）与可折叠生成日志区（checkpoint.generate.log+notes，默认展开、折叠显最新一条、nextTick 自动滚底、上滚暂停吸附、空数据不渲染）；types.ts RunCheckpoint.generate 增可选 log/notes。仅改 BuildProgressPage.vue 与 types.ts 两文件，未动任何 .py 与其它 .vue，未提交 git。

- 验证：cd frontend && npx vue-tsc --noEmit -p tsconfig.json → 0 错误；cd frontend && npm run build → 成功（19.1s，仅既有 chunk 警告）；node --import ./tests/ts_hooks.mjs tests/ontology_build_frontend.test.mjs → 通过 exit 0；node --import ./tests/ts_hooks.mjs tests/ontology_build_review_edit.test.mjs → 19/19 通过 exit 0
- 下一步：等后端 checkpoint.generate.log/notes 接线后浏览器联调真实数据；Codex 独立验收
- 依据/文档：文档/需求/20260920_从物料自动构建本体/需求说明_生成进度实时可观测_v1.md（主仓库未入本分支）

### build-governance V2-10 解析并发（G25）测试agent复测 第二轮 · zcode · 已验证

时间：2026-09-22T02:06:43.784899+00:00；记录：`.collaboration/entries/000180-7984298bd4b5.json`

V2-10 整改复测通过：三项整改全部闭合，无新问题。方式=/tmp 新导出树（3dc51dc），未触碰工作区他人改动。D：3dc51dc 恰 3 文件无夹带、README 仅 +1 行；修复=future.result() except 链补 except Exception → _scan_write_pool_failure 与超时同口径；注释已如实化。R1 独立复测 13/13：路径A RuntimeError、路径B base.parse 层返回 None 触发 AttributeError、路径C 我新增超长消息边界——均 run succeeded、崩溃文件 failed+原因、事实清空、其余 success。R2 白盒 12/12（上轮唯一失败的异常隔离已 PASS；G25b 峰值4/6.23→2.42s、env 7 形态；G25c 迟到时序 timeout@1.01 worker_done@3.01 后 facts 不变；G25d cancelled 保留+重扫 reused=2；G25e write_tx 全来自 build-run-*、解析线程 0 写）；G25a 补测内容+rowid 物理序一致。R3 回归：test_ontology_build 前两次 47/48 遇偶发 RST、后两次 250/250；parsers 95/95、purge 17/17、storage 60/60、runner 34/34、finish_guard 27/27、vue-tsc 0。R5 支持预存定性：双树同率探针（1200 请求×2）旧 21.5% vs 新 20.2%；机制=未登录 POST 401 早返回 57.3% RST vs 已登录 POST 0% vs 未登录 GET 0%。报告 /tmp/v2_test_report_v2_10.md 第二轮章节。

- 验证：R1 独立复测 13/13：路径A RuntimeError / 路径B base.parse 返回 None 触发 AttributeError / 路径C 超长消息(800字含换行) —— run 均 succeeded、崩溃文件 failed+原因含类名、factCount=0、其余 success；R2 白盒 12/12：G25b 峰值并发4 且 6.23→2.42s、env 7 形态；G25c 超时+迟到丢弃时序证据；G25d 取消保留+重扫 reused=2；G25e write_tx 25 次全来自 build-run-*、build-parse-* 0 次；G25a 补测：并发 1 vs 4，17 条事实内容 + rowid 物理插入序逐元素一致；回归：test_ontology_build 250/250（第3、4次；前两次遇偶发 RST 47/48）、parsers 95/95、purge 17/17、storage_contract 60/60、runner_isolation 34/34、finish_guard 27/27、vue-tsc exit 0；R5 双树探针（1200 请求×2 树）：旧树 RST 21.5% vs 新树 20.2%；机制：未登录 POST 401 早返回 57.3% RST、已登录 POST 0%、未登录 GET 0%
- 下一步：测试通过，待用户明确授权后由集成负责人集成合并 main（本轮验收基线 3dc51dc；区间含另一任务线 c55d94a/361df10 的已提交内容，集成时按其独立验收结论处理）；遗留建议（不阻塞、非 V2-10 范围）：未登录 POST 早返回分支的 ConnectionResetError（server.py do_POST 401 先于读 body），建议单独排期修复——早返回分支可 close=True 或先丢弃 body
- 依据/文档：/tmp/v2_test_report_v2_10.md（第二轮章节）；worktree build-governance@3dc51dc（pipeline.py +51/−4、tests +73、README +1）；workbench/ontology_build/pipeline.py:499-505（except Exception 隔离）、:521-547（_scan_write_pool_failure/_pool_failure_message）；workbench/server.py do_POST 鉴权段（RST 机制证据）

### 方法论 v1.1 修订：并入 Palantir 指导 + §7 重写为代码事实对照 · zcode · 已实施，待验收

时间：2026-09-21T14:38:00.560904+00:00；记录：`.collaboration/entries/000179-0003a988dab9.json`

按用户「更新方法论融合palantir可用内容」指令修订 自动化构建本体方法论_20260921.md 至 v1.1：①头部补 Palantir 输入源与修订记录；②§0 立场表加 Palantir 行（建模真实世界/设计判据/任务化验收）；③S8 增任务化验收裁定（unseen 业务问题测可答性、答不了记为缺口产出、人与AI分开测）；④新增 Palantir 采纳清单表（四优先级原则/身份与观测分离/precedence 权威源/反模式/Validation/分支治理，各标注落点与不采纳理由）；⑤§6 拆 6.1 工程事故类 + 6.2 Palantir 四条可确定性化反模式检查（System Silos/Kitchen Sink/God Object/Misnomer，各拟确定性检查与落点；Golden Hammer/Time Machine/Action Sprawl 说明不采纳理由）；⑥§7 整体重写：基线改为 main 已合并实现代码事实（protocol/llm/alignment/retrieval/delivery 只读核对），9 行逐阶段对照表（6段✅对齐、S4/S6/S7+执行模型🟡四缺口①接地②分批③频率门④批次降级），候选增量按真实差距重排 8 项（P0=接地/逐类分批/批次降级，P1=诱导层/反模式警示/相似建议+缓存，P2=任务化验收/覆盖率报告），7.3 限制令边界保留；⑦附录补官方四页链接与 9-15 能力级对照表。纯文档交付未改代码。

- 验证：grep 校验文档标题结构完整（0-7章+附录，无断节）；4.7 编号瑕疵已修正；§7.1 九行对照全部依据上轮已核实代码事实（含 llm.py:43-45/414-423、alignment.py:12-13 行号）；候选增量均标待拍板；7.3 边界声明与限制令核对一致，未新增白名单议题
- 下一步：候选增量八项待用户逐项拍板后才进需求线排期；P0 三项与生成实测失败根因直接相关，拍板后可与三项管理增量同分支排期
- 依据/文档：文档/需求/20260920_从物料自动构建本体/自动化构建本体方法论_20260921.md；https://www.palantir.com/docs/foundry/ontology/ontology-best-practices/

### ontology-build-v2-10-r2（V2-10 测试第一轮整改，worktree/build-governance） · zcode · 已实施，待验收

时间：2026-09-22T01:58:40.631818+00:00；记录：`.collaboration/entries/000179-c93d4abcce14.json`

测试第一轮 3 项（1 严重+2 一般）全部修复，单一提交 3dc51dc：严重#1 _collect_pool_result.future.result() 超时分支之外补 except Exception（不含 BaseException）——worker 内异常（注入失败、适配器契约外返回值在后处理段触发的 AttributeError）转该文件 failed+异常摘要+清空事实（与超时同口径），继续收集下一文件、不再杀死整个 run（新增 _scan_write_pool_failure/_pool_failure_message）；同提交把「0.5s 短片边界取消即时生效」注释改为如实描述（0.5s 轮询只保证主线程及时回到取消检查点，实际取消生效时间由当前文件解析速度决定，最坏 ≤120s）。一般#2 README 变更记录补 V2-10 一行（git diff --cached 复核仅 1 行新增）。测试新增 6 项断言覆盖两条复现路径（RuntimeError 注入 + 猴补 REGISTRY 适配器返回 None 触发 parse_material 后处理 AttributeError，未改 parsers 源文件），均断言 run succeeded、崩溃文件 failed 且原因含异常类名、事实清空、其余文件 success。

- 决定：异常隔离在 pipeline 侧实现（parsers/__init__.py 属另一任务线，未触碰）；落库口径与超时路径一致（failed + 清空既有事实 + failedSegments 记异常类名）；未夹带其他改动：偶发 ECONNRESET 属基线既有缺陷（探针量化见 verification），本轮仅报告不修改以守「不夹带」纪律
- 验证：tests/test_ontology_build.py 250/250（连续 2 次全绿 v210_r1/r2 + cr_2/cr_3 共 4 次；本轮共 7 次运行）；parsers 95/95、late_write 23/23、finish_guard 27/27、runner_isolation 34/34、materials_views 10/10、task_purge 17/17、exclusion_inheritance 17/17、merge_refs 42/42、storage_contract 60/60、storage_transfer 27/27；偶发失败定性（预存缺陷，非本轮引入）：约 1/3 运行在「未登录 POST」分支（main() 第 2262 行）报 ConnectionResetError。机制：server.py:313 鉴权门在未登录时不读取请求体即返回 401 并关连接，客户端读响应体时撞 OS 级 RST。定向探针（200 轮未登录 POST，两树同机对比）：当前树 60/200 ECONNRESET、基线树 c482547 66/200（同为 ~30%）——同量级；另有 V2-10 之前 R6 轮日志 /tmp/obt.log 同址复现佐证；vue-tsc 0 错误、npm run build 通过（本轮前端零改动）；ruff 改动文件全过
- 下一步：待测试 agent 第二轮复验（G25c 两条复现路径 + README 行）；建请协调者裁定：偶发 ECONNRESET 是否安排单独修复（建议方案：测试客户端对「未登录 401」响应体读取失败容忍为重试一次，或服务端在 401 前 drain 请求体——影响面小但均属本轮范围外，未擅自实施）
- 依据/文档：worktree/build-governance 分支 codex/build-governance：3dc51dc（本轮单一提交）；workbench/ontology_build/pipeline.py _collect_pool_result/_scan_write_pool_failure；探针脚本 /tmp/rst_probe.py（临时，未入库）

### 创建 ui_fix 开发 worktree 并登记隔离环境 · codex · 已确认决定

时间：2026-09-21T13:42:31.672975+00:00；记录：`.collaboration/entries/000178-614ba95041a5.json`

按用户明确创建指令，从已提交 main（4714e8a）派生 codex/ui_fix 分支与 worktree/ui_fix 独立工作树，按 2026-09-21 规范把数据根放在工作树内并完成 main 库快照+根密钥+物料blob 随迁，端口登记 18882。本轮只建环境与登记，未安装依赖、未启动服务、未写业务代码、未改 main。

- 决定：目录名沿用用户给定标识 ui_fix，分支按约定命名 codex/ui_fix；从本地已提交 main 4714e8a 创建，仓库无 remote 故不执行 pull。；数据根=工作树根（WIZ_WORKBENCH_ROOT=worktree/ui_fix），库用 transfer backup --output 生成 WAL 一致快照落 data/workbench.sqlite3，禁复制正在写入的库文件；随迁 keys/wb-root.key（否则副本内加密凭据全不可解）与 data/ontology-build-blobs 57 个物料 blob（1.6MB），使副本自含。；端口实测 18882/18892/18902 空闲、18912 被占，登记 18882；未触碰 18765(main)、18881(build-governance) 等他人实例，未启动任何进程。；登记写入公共目录 .git/workbench-tasks/ui_fix.json（fcntl 串行锁），标注 status=worktree_ready_not_started 与『含真实数据与根密钥副本，不可自动丢弃，删除须用户确认』；账号口令与 main 相同未重置。；依赖与构建按创建模板默认不做：worktree 无 node_modules 与 frontend/dist，后续启动前需 npm ci && npm run build（或由后端托管自己构建的 dist）；npm run dev 的 /api 代理默认 18765，验收本分支前必须核对为 18882。
- 验证：git worktree list 确认 worktree/ui_fix @ codex/ui_fix @ 4714e8a；git -C worktree/ui_fix status 干净；主仓库 git status 无 worktree 泄漏（.gitignore 第24行 /worktree/ 生效）；快照只读校验：alembic_version=20260920_0003，pragma integrity_check=ok，30 张 wb_ 表有数据（wb_users 2、wb_build_materials 58、wb_build_facts 4024、wb_snapshots 365）；落位文件权限：data/ keys/ 0700，workbench.sqlite3 与 wb-root.key 0600，.runtime/task.env 0600；本轮未运行 npm build、未跑测试、未启动服务（仅环境创建与登记）；context.py record 需 read 返回的 ticket 且各列表字段≤5 项、单条≤400 字，超限报『交接列表格式无效』
- 下一步：等用户下达 ui_fix 的具体修复范围与任务；开发前在 worktree/ui_fix 内准备依赖并启动 18882 实例。；若修复涉及前端，需先 ./start.sh setup 或建分支 venv，并 npm ci && npm run build 后由分支后端托管自身 dist。；集成合并与清理仍需用户明确指令；本环境含真实数据副本，删除前必须经用户确认。
- 依据/文档：/Users/gukepeng/Desktop/ZHDL/code/wiz_ai/wiz_kq_builder_v2/worktree/ui_fix；.git/workbench-tasks/ui_fix.json；worktree/ui_fix/.runtime/task.env

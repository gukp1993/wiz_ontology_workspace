# Codex / zcode 共享上下文

上下文版本：`9810609b6a947c1e`

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

### auto-build-output-v3 最终合并需求与并行执行指令 · codex · 需求已交付

时间：2026-09-22T07:55:11.397868+00:00；记录：`.collaboration/entries/000194-fa15f2751f11.json`

按用户最新要求停止由Codex直接开发，合并指定v1在线预算计划与v2试点，交付v3唯一技术基线及最终执行指令。任务D00-D19共20项，明确依赖、文件唯一owner、4槽滚动并行、共享核心/两存储适配器、阶段门与隔离验收。所有开发子agent确认未落盘代码。

- 决定：旧两组v1及v2加历史入口提示；统一ratio0.50、首批2、动态输入预留、只缩不扩及共同3次请求额度。；范围包含试验和默认关闭的分支在线接线；核心fake/恢复是接线前置，HTTP浏览器归最终验收；实验B/D可用compact测G2，在线启用受效果门约束。；接手参考更新为8d38f63及修复22a60e5，修复尚待独立验收；不合并main、不启用主服务、不迁移真实库。
- 验证：检查D00-D19恰好20项、文档相对链接与代码围栏、git diff --check通过。；子agent只读审核发现阶段门循环，已修正；只交付文档，未跑业务测试或真实模型。
- 下一步：用户将最终执行指令交实施harness，核对实际SHA与owner后按DAG开发自测提交，交Codex独立验收。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/本体生成控输出_整合方案与并行开发计划_v3.md；文档/需求/20260920_从物料自动构建本体/本体生成控输出_最终执行指令_v3.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### auto_build-token-pilot-v2 整合试点方案 · codex · 需求已交付

时间：2026-09-22T07:31:42.069282+00:00；记录：`.collaboration/entries/000192-1be3034f3e5d.json`

交付控输出与质量对比试点v2，整合稀疏协议、语义分批、超限恢复、字段级证据及程序组装。先独立实验，再决定在线接入。仅文档，未实施实验或业务代码，未运行模型。

- 决定：本文为唯一试点执行入口，不混用两组v1参数；输出估算比例0.5，基线按当前已交接实现冻结。；原8KB、重复实例、大Schema三类样本；先A/D冒烟再A/B/C/D，分别评估协议与调度；含全部失败重试用量。；质量门槛优先；总token下降与单次超限恢复分开评价；用户看真实结果后决定在线接入。
- 验证：只读核对当前候选prompt、协议与在途修改；独立子agent复核试验及完整性门槛。；试验入口和测试明确待实现，无实测收益声明。
- 下一步：实施harness核对owner与基线后按P0-P4开发独立实验，自测提交并交Codex验收，不自动在线接入或合并。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/控输出与质量对比试点_v2.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### auto_build-R4R5批次记账修复（codex/auto_build worktree） · zcode · 已实施，待验收

时间：2026-09-22T07:31:09.401557+00:00；记录：`.collaboration/entries/000191-a8f61c516ffc.json`

修复R5/R4两条管线正确性缺陷并全绿：R5（pipeline._run_batches_adaptive）废弃连续前缀刷写游标，改为每批完成即独立 on_result 落库（候选表/definitionOrder 按类型+对齐键排序，不依赖落库顺序）；删除死代码 _batch_wait_timeout 与 _BATCH_WAIT_SLACK_SECONDS。R4（pipeline._extract_batch_with_split）拆批返回三态（全成功/全失败/部分成功）：部分成功 ok=False 父批不进 done（重试整批重跑），成功半候选保留，error 注明「拆批后部分失败：已保留 X 条候选…可重试」，failedFactIds 随 error 上抛（嵌套精确到失败子批）。调用方 run_generate._flush_batch：部分成功候选校验+namespace 后同事务落库；content_tx 失败不静默（回滚内存 done、该批计 failed 错误注明「落库失败」、accumulated 只在事务成功后并入）；_flush_tx 写入前按同批已存在 alignedKey/原始 key 去重（部分失败整批重跑幂等）；调度器返回后兜底核对 done∪failed 覆盖 pending，遗漏显式计 failed 并落检查点。

- 决定：落库顺序不再强求等于串行版：候选展示与 definitionOrder 预检按类型+对齐键排序，与落库先后无关；部分成功批的候选也走 namespace_batch 与成功批同一键空间；/tmp 复现脚本修正判定逻辑以表达修复后的不变量（R5 判定改为 pending 全覆盖检查；R4 补丁目标从 _extract_batch_with_split 改为 llm.extract_candidates 才能真正执行拆批分支）
- 验证：WIZ_WORKBENCH_ROOT 隔离下 /tmp/repro_r5_final.py 与 /tmp/repro_r4.py 均输出 ✅（修复前均复现缺陷）；新增 tests/test_ontology_build_batch_accounting.py：7 场景 36 断言全过（非连续续跑空洞、完成序乱序、落库写事务失败计 failed、调度漏报收尾兜底、拆批左成右败/右成左败/全失败/嵌套/全成功、部分失败整批重试批内去重幂等、429 单次调用不重试），tests/run.py --test 子进程亦过；python3 tests/test_ontology_build.py 250/250 全过（无既有断言改动）；python3 tests/run.py all 全绿（60 套件，含并行线新增套件，exit 0）；ruff check workbench/ontology_build/pipeline.py tests/test_ontology_build_batch_accounting.py 全过
- 下一步：待 Codex 独立验收（SHA 以协调者统一提交为准）；未 git commit（协调者统一提交）；与并行线 R1（namespace_batch existing=参数）/R2（AUTO_INCLUDE_REVOKED）/R3（全池 duplicateOf）改动已同文件合流，验收时一并核对
- 依据/文档：workbench/ontology_build/pipeline.py；tests/test_ontology_build_batch_accounting.py

### auto_build-R1跨批临时键冲突（codex/auto_build worktree） · zcode · 已实施，待验收

时间：2026-09-22T07:29:43.493040+00:00；记录：`.collaboration/entries/000190-4413a2704276.json`

修复R1：跨批同名临时键（obj/p）导致属性宿主解析错误、同名同dataType属性被误并。①alignment 新增 namespace_batch(candidates, position, existing=None)：与累积集合实际冲突的键加 b{position}: 前缀并按「先建映射再替换」重写批内 ownerKey/链接 sourceRef/targetRef；无冲突键保留原键（入库行为与历史一致，existing=None 为全量前缀纯批模式）；目标键被占用追加下划线兜底，跨批键绝不冲突。②alignment._ref_index 保留同名键全部命中（按出现顺序），_owner_name_for/_resolve_owner 对多命中按「属性之前最近宿主」消歧——旧存量无前缀候选行与直调 align 场景的兜底，键唯一时行为与原实现一致。③pipeline 批内 verify 后、入 accumulated 与同事务落库前调用 namespace_batch(verified, position, existing=accumulated)（成功分支与拆批部分成功分支两处，均为本任务指定改动点）。④llm.SYSTEM_EXTRACT 规则7要求对象限定见名知义 key（battery_rated_power 而非 p）；_sanitize_candidates 新增 _normalize_key_ref 对 key/ownerKey/sourceRef/targetRef 用同一函数规范化（去空白/内部空白折叠下划线/小写/截断60），修复大小写不一致断链。新增 tests/test_ontology_build_key_namespace.py 30 项断言（纯函数 8 组 + 管线端到端）。

- 决定：命名空间化采用冲突条件模式而非无条件全批前缀：无条件前缀会使库内候选键全部变为 b1:* 形态，test_ontology_build.py flow_bad_candidates 按原始 ckey 直接 UPDATE/查找候选的 4 处断言失配（实测 108/110）；冲突条件模式保持「跨批键绝不冲突」不变量同时非冲突批入库键与历史完全一致，既有测试无需修改；决策继承按 alignedKey（名称型）不受键命名空间化影响，未改 inherit_manual_exclusions
- 验证：WIZ_WORKBENCH_ROOT=/tmp/r1probe python3 /tmp/repro_r1.py：修复前 ❌（属性 2→1、alignedKey 错记 @电池）；修复后 ✅（2 条属性，宿主分别为电池/逆变器）；python3 tests/test_ontology_build_key_namespace.py：30/30（含管线端到端：批1无冲突保留原始键、批2冲突键 b2:obj/b2:p，两条额定功率宿主正确）；python3 tests/test_ontology_build.py：250/250；tests/test_ontology_build_merge_refs.py：42/42；tests/test_ontology_build_exclusion_inheritance.py：17/17；python3 tests/run.py all：60/60 全绿（其中 test_ontology_build_batch_accounting.py 首轮失败系并行 R4/R5 重构中间态，复跑通过）；ruff check alignment.py llm.py pipeline.py 新测试：All checks passed
- 下一步：未修相邻问题1：拆批两个子批之间的同名 key 冲突仍在——子批边界信息在 _extract_batch_with_split（调度器区，本轮禁改区），namespace_batch 收到的是合并后候选无法区分子批；子批冲突键会命中冲突条件被前缀化，但两个子批各自冲突键共享同一 position 前缀时仍同名（回落 _dedupe_keys 首个保留，后续引用悬空为既有行为）；未修相邻问题2：_ref_index 邻近消歧是位置启发式（属性先于宿主对象输出且宿主在同批更早位置时选最近 precedent），仅兜底旧数据；主路径已由命名空间化保证；llm key 统一小写化理论上可能把批内大小写不同的两个键折叠为同名（模型违约场景），由 _dedupe_keys 首个保留兜住，属既有失效等级
- 依据/文档：workbench/ontology_build/alignment.py；workbench/ontology_build/pipeline.py；workbench/ontology_build/llm.py；tests/test_ontology_build_key_namespace.py；/tmp/repro_r1.py

### auto_build-R2冲突候选交付门禁（codex/auto_build worktree） · zcode · 已实施，待验收

时间：2026-09-22T07:22:19.071773+00:00；记录：`.collaboration/entries/000189-595b708843ed.json`

修复R2：跨批发现冲突后候选仍被默认纳入交付。①pipeline.verify_candidates 决定赋值区：带 alignedKey 且状态降级（conflict/inferred/insufficient）、decision=include 且 reviewed 非真的候选重置为 defer，附 AUTO_INCLUDE_REVOKED issue 与报告 note；逐批首次赋值与 reviewed=true/exclude 不变。②delivery 新增 _delivery_allowed 硬门禁：选定集合=include ∧（reviewed=true ∨（无 conflicts ∧ supported））；precheck 新增 conflictExcluded/conflictExcludedItems/notes 排除报告。③protocol.default_decision 文档字符串补充重算时机。新增 tests/test_ontology_build_conflict_gate.py（29 断言，5 场景）全过；repro /tmp/repro_r2.py 转绿；exclusion_inheritance 17/17、finish_guard 27/27、merge_refs 42/42；ruff 全过；tests/run.py all 58/59。

- 决定：撤销与门禁按「准确说」口径实现：无 conflicts 但 evidenceStatus 非 supported 且未确认的 include 候选同样不进交付选定集合（比「仅冲突」口径更严，语义不弱化）；重算逻辑以 alignedKey 为合并标记放在 verify_candidates 决定赋值区（首次赋值时逐批候选尚无 alignedKey，天然不影响），未改动 1373-1380 调用点；被门禁拦截的候选不删行、进 precheck.conflictExcludedItems 显式列出，不静默消失
- 验证：WIZ_WORKBENCH_ROOT=/tmp/r2probe python3 /tmp/repro_r2.py：修复前 ❌ 复现（conflict+include+未确认），修复后 ✅（decision=defer）；python3 tests/test_ontology_build_conflict_gate.py：29/29（含真实临时库播种 delivery.precheck 计数断言）；python3 tests/test_ontology_build_exclusion_inheritance.py 17/17；tests/test_ontology_build_finish_guard.py 27/27；tests/test_ontology_build_merge_refs.py 42/42；ruff check（pipeline/delivery/protocol/新测试）：All checks passed；tests/run.py all：58/59，唯一失败 test_ontology_build.py 经对照实验（禁用本次两处修复后失败完全相同：108/110+同一中断）归因为并行 R1 命名空间改动（候选 key 变 b1:obj-device）与该测试裸 key 播种/断言的冲突，非本修复引起；unit 组 48/49，另一失败 batch_accounting.py 为并行 agent 15:21 新增的调度器 WIP 自测（乱序回调/拆批部分失败），同样与本修复无关
- 下一步：接口文档 08 分册需协调者统一登记：§1.6 issues 新增码 AUTO_INCLUDE_REVOKED（field=decision）与默认决定重算时机；§8.1 预检响应新增 conflictExcluded/conflictExcludedItems/notes，且 selectedIds/counts/checkToken 口径收窄为「门禁后选定集合」；test_ontology_build.py 的 R1 冲突（mutate/查找按裸 key）与 batch_accounting.py WIP 失败由对应并行 agent/协调者处理；未 git commit（按指令由协调者统一提交）；未改动接口文档、alignment/llm/retrieval/runner/storage/frontend
- 依据/文档：workbench/ontology_build/pipeline.py:929-951；workbench/ontology_build/delivery.py:48-73,175-181,196-206；workbench/ontology_build/protocol.py:257-266；tests/test_ontology_build_conflict_gate.py；文档/需求/20260920_从物料自动构建本体/
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

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

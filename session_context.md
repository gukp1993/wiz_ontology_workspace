# Codex / zcode 共享上下文

上下文版本：`689906c359b987cb`

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

### system-deep-test：按 S1S3 复验 F01-F05 整改（判定器与证据核对） · zcode · 已实施，待验收

时间：2026-09-21T02:34:20.442863+00:00；记录：`.collaboration/entries/000141-f8a6848da567.json`

按 S1S3独立复验记录_20260921.md 的 F01-F05 完成整改并要求提交 53a7ab2，业务代码零修改（基线 d6c73c2）。F01：拒绝分支同样要求版本零新增——版本不可读记 test_error、拒绝却新增记独立缺陷；_count_versions 读取失败（500/非200/items 非数组）一律返回 None 不再当 0。F02：新增 classify_validate_observation，校验接口先过传输/状态码/结构（errors 须为数组）再判诊断，500 或结构不符一律 test_error；R02 负对照的 validate 与 publish 两段都须满足期望。F03：诊断只从约定诊断字段提取，收集字符串列表与嵌套 report/items/issues，排除回显载荷与内层判别值键（kind/type/status 等），移除「取不到诊断字段就序列化整份响应」的兜底；支持分组诊断（定位+原因同时命中）；未提供诊断依据不再自动放行。F04：CASE_ID_SPLITS 要求全部子用例覆盖（缺项报错并写明缺哪个），CASE_ID_ALTERNATIVES 才允许任选其一。F05：测试计划历史执行记录加显著失效提示并链接最新索引，历史内容保留。另按四个并行只读验证者交叉核验发现的残余通道追加加固（错误码与接受码重叠、版本计数传字符串的用例配置自检；中断豁免要求 kind 确为工具错误；info 行不得顶替业务覆盖；被替代批次自身说明行单列）。独立复验指令更新到第二轮，新增 §4A F01-F05 核验项与反例清单。

- 决定：拒绝分支的版本门与接受分支同等严格：证据不足（版本不可读）记 test_error 而非放行，拒绝却新增版本按独立缺陷计，避免「返回错误码就判通过」。；校验接口观察独立成 classify_validate_observation，先过状态码/响应结构再判诊断；500 带正确文案也不得记通过。；诊断匹配只取约定诊断字段并显式忽略内层判别值键（kind/type/status/id/version 等）；不提供诊断依据时不自动放行，确需跳过须显式 diagnostic_not_required=True。；F04 区分「一拆多」（全部子用例必须覆盖）与「任选其一」（等价编号），当前真实日志本身不缺项，修的是机器保证可被错误配置绕过。；判定器对用例配置做自检（block_status 与 allow_status 重叠、版本计数非整数）直接报 test_error，避免配置错误静默产出错误结论。
- 验证：tests/deep_verdicts_test.py 49/49 通过（F01 4 项 / F02 7 项 / F03 13 项 / 配置自检 3 项 + 原有对照）；tests/deep_results_index_test.py 16/16 通过（F04 拆分 3 项 + 残余豁免通道 4 项 + 引用核对 3 项）。；18971 全新隔离实例真实 HTTP：fix-20260921-final 15 条 (pass11/known3/info1，负对照两段均通过)、final-onto 22 条 (17/1/1/1/1/1)、final-proj 54 条 (51/2/1)；分类与整改前一致，业务缺陷未变。；结果索引重算数字不变：14 有效批次、被替代 94 行、187 记录、唯一场景 187、根因 9、pass178/known3/new2/static1/blocked1/info2；§2.2 覆盖核对新增「本身即说明行」列（O7-00 如实列出）。；四个并行只读验证者独立构造反例核验（各自不改文件、不启停服务）：F01/F02/F04 声称全部成立；F03 声称部分成立，其发现的 3 处残余通道（整份响应兜底、kind 误收、name 过度排除）已修复并锁定用例；另发现 3 处配置陷阱与豁免通道已加固。；范围核对：git diff d6c73c2..HEAD -- workbench frontend 为空；本轮仅 tests/deep_* 与本需求文档；git diff --check 通过。；清理：18971 按 PID+cwd 确认后停止、端口释放，删除本轮 .runtime/fix-reverify-data（4.5M 合成）；保留 .runtime/reverify-evidence 及 runId 证据；18931(PID56914)、test-data*、test-evidence、accept-evidence、s1s3-accept-evidence 未触碰。
- 下一步：Codex 按 独立复验指令_20260921_S1S3.md（第二版，含 §4A F01-F05 核验与反例清单）复验提交 53a7ab2；重点自造反例验证判定器不再被错误响应骗过。；复验重跑须另起新合成数据根与空闲端口（勿用 18765/8765），脚本支持 Q02_BASE/Q03_BASE/DEEP_RUN_ID 覆盖，勿指向 18931 与真实根。；验收通过后停在「待用户授权集成」；业务缺陷（D-Q02-01/A01/R02/D1/R01/D2/D3 等）修复另行安排。
- 依据/文档：文档/需求/20260921_系统全方位深度测试/S1S3独立复验记录_20260921.md（F01-F05 来源）；文档/需求/20260921_系统全方位深度测试/独立复验指令_20260921_S1S3.md（第二版，含 §4A）；文档/需求/20260921_系统全方位深度测试/缺陷清单.md（「复验整改记录」与「独立验证发现的追加加固」）；提交 53a7ab2；tests/deep_verdicts.py、deep_verdicts_test.py、deep_reverify_scenarios.py、deep_results_index.py、deep_results_index_test.py

### codex/test S1-S3独立复验47d5a8f · codex · 已验证

时间：2026-09-21T02:12:27.386625+00:00；记录：`.collaboration/entries/000140-a31aa0cfa8c0.json`

复验不通过：统计索引及实际HTTP复跑成立，但独立反例证明422版本增加仍pass、R02 validate500仍pass、诊断整响应误匹配、拆分覆盖漏半仍过；交付独立复验记录，未修改原测试/业务。

- 验证：判定测试23/23；索引14批次187记录94替代9根因、文档去生成时间可重现；5行抽验一致。；定向15条、完整本体22条、完整项目54条均完成；A01/R02仍known，未全面重测。；独立内存反例F01-F04实锤；project-save失败正确blocked，错行号正确拒绝。；18961/PID36857已停止，独立s1s3-accept-data清理；其余环境未操作。
- 下一步：执行者修F01-F04及F05标记后定向复验，不需先修业务或重做历史全套测试。
- 依据/文档：文档/需求/20260921_系统全方位深度测试/S1S3独立复验记录_20260921.md

### system-deep-test：S1-S3 独立复验指令交付（交 Codex） · zcode · 已实施，待验收

时间：2026-09-21T02:06:06.737162+00:00；记录：`.collaboration/entries/000139-5a05ccc10835.json`

按用户要求交付可直接复制给 Codex 的复验指令：文档/需求/20260921_系统全方位深度测试/独立复验指令_20260921_S1S3.md（提交 1157e87）。指令自包含被验对象与 SHA（复验 HEAD 1157e87、业务基线 d6c73c2）、只读边界与资源约束、本轮改动清单，以及必做复验 M01-M10：判定函数反例（5xx/无关4xx/前置失败/网络失败不得记通过）、索引可核算、替代关系与覆盖可证伪（要求自行制造反例验证核对有效）、A01/R02 前置与负对照、O4-06 静态与动态不可互相替代、真实 HTTP 定向复跑、修订脚本可运行、文档数字一致与无旧口径残留、未测边界如实。要求逐项给命令/退出码/行号证据，不得把工具跑通当业务通过，并给出不通过判据与停止点。另补强结果索引：新增被替代批次覆盖核对（validate_superseded_coverage，未覆盖场景即报错；crash 行允许无对应业务但须列出；O4-07 按 a/b 拆分别名），已用 3 组反例自测检查器本身有效；索引可复现性实测通过（除生成时间外逐字节一致）。本轮仅测试与文档，未改业务代码。

- 决定：复验指令写成仓库内可持久文件，用户可直接复制全文交 Codex；同时把复验入口链接接入测试计划。；要求复验方自行制造反例验证两项机器核对（引用行号核对、被替代覆盖核对）确实有效，避免只复跑既有用例。；复验沿用前轮已交付的 S1-S3 判定口径；本轮只补强可证伪性，不新增结论、不改统计数字。；复验不得修改 验收记录.md 与实施者交接条目；浏览器复验如需补记另存自己的报告文件。
- 验证：覆盖核对检查器反例自测 3/3：含未覆盖场景正确报错、crash 行正确放行、O4-07 拆分编号别名正确放行。；结果索引可复现：连续两次 --write-doc 除生成时间外逐字节一致；数字不变（14 有效批次、被替代 94 行、187 条记录、根因 9）。；指令内引用的文件与符号均已核对存在：验收记录/报告/清单/计划/结果索引/继续验证指令；scenario_a01、scenario_r02、三处 validate_*、O4-06b。；范围核对：git diff d6c73c2..HEAD -- workbench frontend 为空；本轮提交仅 tests/deep_* 与本需求文档；git status 干净、diff --check 通过。
- 下一步：用户将 独立复验指令_20260921_S1S3.md 全文交 Codex 执行；Codex 按 M01-M10 复验并输出逐项结论与不通过判据。；复验通过后停在「待用户授权集成」；集成合并需用户明确授权，由 Codex 串行组合重验。；业务缺陷（D-Q02-01/A01/R02/D1/R01/D2 等）的修复另行安排，本轮不修。
- 依据/文档：文档/需求/20260921_系统全方位深度测试/独立复验指令_20260921_S1S3.md；提交 1157e87（复验指令）、10d565b（覆盖核对）、6b4665e（S1-S3 修订主体）；被验业务 SHA d6c73c2；文档/需求/20260921_系统全方位深度测试/结果索引.md；测试报告.md；缺陷清单.md；测试计划.md；tests/deep_verdicts.py；tests/deep_verdicts_test.py；tests/deep_results_index.py；tests/deep_reverify_scenarios.py；tests/deep_reverify_r02_a01.py

### system-deep-test 继续验证（S1-S3 修订与针对性复验） · zcode · 已实施，待验收

时间：2026-09-21T01:56:39.517979+00:00；记录：`.collaboration/entries/000138-d7240eb67808.json`

按《继续验证指令》在 worktree/test 完成 S1-S3 修订与必要复验，业务代码零修改（基线 d6c73c2），提交 6b4665e。新增 tests/deep_verdicts.py 统一判定（八类结果，5xx/无关4xx/网络失败/空响应/前置失败一律不记通过）与 23 项判定自测（全通过）；P8/O3-04/O4-06 改为共用 tests/deep_reverify_scenarios.py；新增 tests/deep_results_index.py 与 结果索引.md（14 个有效批次、行区间、替代原因、逐行分类、根因合并，引用按 文件#行(用例) 机器核对）。有效批次 187 条：pass 178、known 3、new 2、static 1、blocked 1、info 2；被替代 94 行保留不计；根因 9 条。定向复跑 reverify-20260921T0958Z（18951 新隔离实例）A01/R02 判已知缺陷复现、负对照被拦截；回归 T1005Z(22)/T1008Z(54)。未全面重测、未恢复压测、未合并 main。

- 决定：统计单位固定三种且不相加：断言/场景记录、唯一场景、缺陷根因；info 与缺陷复现不计入产品通过。；R02 的 validate 未拦截与 publish 仍成功是同一根因两条观察，合并计 1 条；保持 P1（无产品口径允许发布不可用取值配置，已写明降级条件）。；D-Q02-01 收窄为「新发布本体无法通过现有历史快照入口恢复」：发布/版本列表/版本内容读取正常、旧迁移 ZIP 仍可能恢复，不称发布整体失效或数据丢失。；O4-06 拆为静态核对通过（static_check_pass）与不可变性破坏路径未测（not_tested），二者不可互相替代。；判定修订只改 tests/deep_* 与文档、不降既有断言；工具构造错误的运行登记为 superseded/discarded，不隐藏不计入结论。
- 验证：tests/deep_verdicts_test.py 23/23 通过（正确阻断/实际缺陷/500/无关4xx/前置失败/空响应/网络失败各有反例），退出码 0。；tests/deep_results_index.py 只读原始日志重算：14 有效批次、被替代 94 行、断言 187、唯一场景 187、根因 9；替代关系与根因引用逐条机器核对（曾捕获 3 处人工行号偏差并修正）。；reverify-20260921T0958Z（18951）：15 条 pass 11/known 3/info 1；A01 前置合法→追加非法→发布快照保留原值；R02 前置全成功、validate 未拦截、publish 200 版本 0→1，负对照 422 被拦截。；regress-20260921T1005Z(22 条)与 T1008Z(54 条)：修订后 deep_ontology_o3o4o5/deep_project_chain 全链路可运行。；隔离与清理：env -u WIZ_DATABASE_URL、库落 .runtime/reverify-data（已删）；18951 按 PID+cwd 停止并释放端口，保留 .runtime/reverify-evidence；18931 未触碰仍未停止。
- 下一步：Codex 独立复验：核对 deep_verdicts.py/deep_results_index.py 可重算，reverify-20260921T0958Z 的 A01/R02 分类与负对照，三份文档与 结果索引.md 数字一致。；复验重跑须另起新合成数据根与空闲端口（勿用 18765/8765），脚本支持 Q02_BASE/Q03_BASE 覆盖，勿指向 18931 与真实根。；验收通过后停在「待用户授权集成」；集成合并需用户明确授权由 Codex 串行组合重验，产品缺陷修复另行安排。
- 依据/文档：提交 6b4665ec603166b728558d0c6caa1f5cbfbe11c3（codex/test）；被验业务 SHA d6c73c2；文档/需求/20260921_系统全方位深度测试/结果索引.md；文档/需求/20260921_系统全方位深度测试/测试报告.md；缺陷清单.md；测试计划.md（继续验证区）；tests/deep_verdicts.py；deep_verdicts_test.py；deep_reverify_scenarios.py；deep_reverify_r02_a01.py；deep_results_index.py

### codex/test 深度测试继续验证指令 · codex · 需求已交付

时间：2026-09-21T01:41:12.218469+00:00；记录：`.collaboration/entries/000137-87389e169b27.json`

交付继续验证指令，供其他harness按S1-S3修订报告/新增deep测试判定并针对性复跑；本轮仅文档，不执行复验或修业务。

- 决定：复用codex/test，不合并main；报告验收与产品缺陷修复分开。；明确运行批次/分类统计、A01/R02判定反例、延期边界与临时环境清理。
- 依据/文档：文档/需求/20260921_系统全方位深度测试/继续验证指令.md

### codex/test 系统全方位深度测试独立验收 · codex · 已验证

时间：2026-09-21T01:35:57.842518+00:00；记录：`.collaboration/entries/000136-6312c36e211e.json`

独立验收测试交付暂不通过：主要业务缺陷真实，但报告数字/批次归属不一致、部分deep脚本判定可能误报通过，需要修订后复核；非要求先修业务。已交付验收记录。

- 决定：仅审验测试成果，不改业务/原测试，不合并main。；D-Q02-01限定新发布资产恢复断层，发布与版本读回正常；R02分级需解释，A02旧UI不可达不等于全图谱已验。
- 验证：18941新合成环境HTTP复现A01/R02/D1/D2/D3，抽验CAS409/currentRevision、413、未登录401。；生产浏览器复现R01错误但仍能操作；甲乙对象复现撤销提示陈旧且实际撤销正确。；差异只含测试文档/脚本/交接；mapping_forms基线失败复现。；本轮PID13537停止、18941释放、accept-data清理；原18931与其他环境未操作。
- 下一步：执行者按验收记录S1-S3修订统计、脚本判定与缺陷范围后交复核，无需全面重测或先修业务。
- 依据/文档：文档/需求/20260921_系统全方位深度测试/验收记录.md；被验交付3d58d20；业务基线d6c73c2

### system-deep-test 全方位深度测试执行与交付（Q00-Q08） · codex · 已验证

时间：2026-09-21T01:16:28.247683+00:00；记录：`.collaboration/entries/000135-58501560ea5f.json`

在 worktree/test（codex/test，业务SHA d6c73c2零修改）完成独立深度测试：Q02本体34断言、Q03项目+编排74断言、Q05可靠性/边界~75断言、Q04浏览器生产入口实测；Q06性能按用户指令中途取消（仅留有限样本，无统计结论）。总体结论：不通过（存在P1新缺陷），主干CAS/幂等/隔离/脱敏全部符合契约。新发现D-Q02-01(P1在线发布不写release-zip致快照恢复断链)、D1(P2 POST缺Origin放行与README403口径矛盾)、D2(P2损坏库回传内部异常文本)、D3(P3跨账号错误码口径)、Q04-01(P3撤销按钮label陈旧)；基线复现并升级证据A01/R01/R02(根因project_validation.py:512)，A02重定性为本树UI生产入口不可达(死代码)。已提交4d4e497（测试脚本+两份报告+执行记录+子任务entries），证据JSONL留.runtime不入Git。未合并main、未清理；18931(PID56914)保留运行，18932/18933/18939已按PID+cwd确认后停止释放。

- 决定：Q06以用户指令取消为准，已采样本标注n=1仅供参照，不预写性能结论；删除→撤销疑点经受控复测排除（有确认框、撤销正确恢复并落库），原判为协调者误读；/api/releases语义为release-ZIP工件列表而非版本历史（Q03澄清），但D-Q02-01定性不变：在线发布永不写工件→恢复链路对新资产不可达且文档标可用；D1/D3按'文档bug或实现bug'流程交owner裁定，测试侧不改文档；视觉/宽度/缩放/画布/键盘类因应用内浏览器隐藏记环境阻塞，不判缺陷也不判通过
- 验证：本体链34断言31通过+A01复现+D-Q02-01（tests/deep_ontology_o1o2/o3o4o5/o6o7.py）；项目+编排74断言73通过+R02复现（tests/deep_project_chain/deep_flow_chain.py）；I1-I9全过：kill -9注入integrity_check=ok、63端点未登录401、密钥字节扫描0明文（tests/deep_integrity_*）；UI删除/撤销/重做/登录退出受控复测+API交叉核对（q04/REPORT.md）；server-18931.log全程0命中500；mapping_forms.test.mjs基线失败单列未修
- 下一步：交回用户安排修复：优先D-Q02-01→A01→D1裁定→R01→D2（缺陷清单前五项）；集成/合并需用户明确授权后由Codex串行组合重验；18931与test-data*保留待处置；如需补全Q06：tests/deep_perf.py已就绪，须独占实例运行
- 依据/文档：文档/需求/20260921_系统全方位深度测试/测试报告.md；文档/需求/20260921_系统全方位深度测试/缺陷清单.md；文档/需求/20260921_系统全方位深度测试/测试计划.md（实际执行记录）；提交4d4e497；被验业务SHA d6c73c2
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### Q03 深度测试：项目配置+编排业务链（P1-P9） · codex · 已实施，待验收

时间：2026-09-21T01:09:14.900246+00:00；记录：`.collaboration/entries/000134-3f1de0ed1d0e.json`

专属实例18931完成P1-P9，74条断言：73通过+1基线已知(R02)，未发现新增业务缺陷。三条数据线职责符合设计：本体save/publish/versions、项目CAS/往返无损/缺身份发阻断、编排check纯配置绝不执行+纯本地calc确定结果+错误定位+重复run不脏数据。安全：connection-secret不回显、跨账号读/写他人id一律404、探测不持LOCK。红线遵守：未连真实MySQL/Redis、未执行真实SQL/用户Python、flow-run仅calc、连接探测只打127.0.0.1关闭端口/静默mock并结束关闭。P8.4复现R02(known)：空壳编排(输出未绑定)被项目flow来源引用，project-validate不拦、publish仍成功。

- 决定：两处测试侧纠偏(非业务缺陷)：P1.6误用/api/releases(实为release-ZIP工件列表)改查/api/versions；P6.5破坏性编辑造成deviceCode悬空被/api/save正确422拦截，改删无悬空属性clusterActivePower
- 验证：tests/deep_project_chain.py 51条(50 pass/1 known=R02)；tests/deep_flow_chain.py 23条全pass；证据jsonl+REPORT.md在 .runtime/test-evidence/q03/
- 下一步：R02(中)按既有缺口交owner裁定：project_validation._check_flow_binding 不调用 flows.check_flow；Q03只测试记录，未改业务代码，未git commit
- 依据/文档：.runtime/test-evidence/q03/REPORT.md；tests/deep_project_chain.py；tests/deep_flow_chain.py；workbench/project_validation.py:512
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### Q05 深度测试：数据可靠性+账号/接口边界 · zcode · 已实施，待验收

时间：2026-09-21T00:59:10.151882+00:00；记录：`.collaboration/entries/000133-1c391f575ca0.json`

专属实例18932完成I1-I9：约75条断言通过。无跨账号数据泄露、无密码明文回读；kill -9两轮+损坏库副本探测均无半写。缺陷：D1 POST缺Origin被放行与README「缺失或不符403」不符；D2 存储损坏时GET /api/state返回400携带原始Python解析文本（应503/通用500）；D3 跨账号connection-secret返回400、llm-provider-delete对他人id返回200 cleared=true，与06「按不存在404/空」口径不符（实测均无越权效果）。

- 验证：tests/deep_integrity_http.py 59条(58 pass/1 fail=D1)；tests/deep_integrity_restart.py write/verify/freshroots 全过；tests/deep_integrity_fault.py prep/hammer/check/corrupt-* 全过；报告与jsonl证据在 .runtime/test-evidence/q05/
- 下一步：D1-D3 按「文档bug或实现bug」流程交owner裁定；Q05结束已停止18932并释放端口
- 依据/文档：.runtime/test-evidence/q05/REPORT.md；tests/deep_integrity_http.py；tests/deep_integrity_restart.py；tests/deep_integrity_fault.py

### system-deep-test环境与测试指令 · codex · 需求已交付

时间：2026-09-20T16:05:14.587942+00:00；记录：`.collaboration/entries/000132-a740c1659977.json`

按用户最新命名创建并登记worktree/test和codex/test，从main d6c73c2派生；交付测试计划和完整执行指令，未安装启动或执行测试。

- 决定：仅独立测试，不修业务代码、不合并main；覆盖UI、交互、功能、可靠性、性能。；端口18931创建时核实空闲，隔离数据根.runtime/test-data；未包含其他未合并修复。
- 验证：目录和分支已由测试重命名为test，登记与文档同步。
- 依据/文档：文档/需求/20260921_系统全方位深度测试/执行指令.md；文档/需求/20260921_系统全方位深度测试/测试计划.md

### 本体与项目辅助填写原型委托需求 · codex · 需求已交付

时间：2026-09-20T12:04:28.558493+00:00；记录：`.collaboration/entries/000131-082bbd8f1bdb.json`

按用户改为由其他harness制作原型的要求，交付完整需求与独立执行指令；本轮未生成HTML或实施业务功能。

- 决定：本轮两份文档，不生成开发计划；其他harness生成同目录交互原型_v1.html，后续按清单验收。；覆盖本体5类及项目6类辅助填写场景，7条演示链路、17项验收；使用现有风格及离线假数据。；采纳只进入表单，旧建议失效保护、共享影响确认、缺信息追问必须演示；复杂编排生成与真实模型调用不在本次原型必做范围。
- 验证：文档场景/验收编号完整性与两份文件范围检查通过，git diff --check通过；未声称原型或浏览器已验收。
- 下一步：其他harness按执行指令制作独立原型并自检，用户交回后按A01至A17验收。
- 依据/文档：文档/需求/20260920_本体与项目辅助填写/需求说明.md；文档/需求/20260920_本体与项目辅助填写/执行指令.md

### acceptance-fixes环境与修复指令 · codex · 需求已交付

时间：2026-09-20T10:07:25.055603+00:00；记录：`.collaboration/entries/000130-c41ccb18eab1.json`

按用户要求从main b0fb7c0创建worktree/acceptance-fixes，分支codex/acceptance-fixes；修复计划和完整指令已在该分支提交，未开始业务开发。

- 决定：只覆盖合并验收R01-R03/A01-A02；实施后交独立验收，不自动合并main。；端口18921创建时检查可用并登记，尚未启动；隔离数据与Python环境均在该树.runtime。
- 验证：Git工作树创建成功，任务登记加锁写入公共Git目录；文档diff检查通过。
- 依据/文档：worktree/acceptance-fixes/文档/需求/20260920_本体与项目统一维护体验改版/验收修复执行指令.md；worktree/acceptance-fixes/文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md

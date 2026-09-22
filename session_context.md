# Codex / zcode 共享上下文

上下文版本：`25a87dee87971a86`

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

### 模型设置思考强度选项（codex/auto_build 独立功能提交） · zcode · 已实施，待验收

时间：2026-09-22T15:19:56.334008+00:00；记录：`.collaboration/entries/000215-a4d1aec8e9f0.json`

用户指令「模型设置，编辑可设置思考强度」，在 auto_build worktree 实施并提交 c4ba68a（基于 HEAD 0057e9f，与修复提交 1df156f 无文件重叠）。范围：LLM 提供方配置新增 thinking 字段（default|off）——接口文档 04 §4.1/4.2/4.4 + README 变更记录先行；存储列 wb_model_configs.thinking + Alembic 迁移 20260922_0005（server_default 与迁移 DDL 一致）；llm_providers 校验/保存/回显（非法 400、读取宽容）；flow_routes save/test 透传；llm_client.apply_provider_extras 统一 chat/chat_once_result/test_connect 的家族微调（bigmodel.cn+off → thinking:{type:disabled}；minimax 既有行为不变；其他家族不追加）；前端模型设置高级设置加选择器与列表标记。实测依据：GLM-5.3-Flash 关思考单批 150-260s→15.2s（今晚 18891 诊断系列实测）。

- 验证：tests/test_llm_providers.py 53/53（新增 12 项：枚举校验/存取回显/路由 400/请求体四组合/chat 桩捕获）；全量回归 tests/run.py all 78/78（exit 0，含 1df156f 修复后的组合状态）；前端 vue-tsc typecheck + vite build 通过；ruff 无告警；迁移演练：transfer init 新库 schema=20260922_0005 含列、裸 SQL 插入回落 default、revision 链 0004→0005 完整
- 下一步：复验对象更新为 1df156f（验收修复）+ c4ba68a（本功能）；thinking off 的抽取质量 A/B（金样质量门）仍属建议项未做；待 Codex 复验通过后停在待用户授权集成；18891 隔离实例需重启才加载新代码（未动运行中服务）
- 依据/文档：提交 c4ba68a；接口文档 04 分册 §4；迁移 20260922_0005；实测数据链：本会话 18890/18891 诊断（GLM 默认/关思考/minimax 对照、整文件一发截断实验）

### auto-build-output-v3 · zcode · 已实施，待验收

时间：2026-09-22T13:52:30.658299+00:00；记录：`.collaboration/entries/000214-c17dee5bf734.json`

验收修复 5 项全部完成并提交 1df156f（基线 e365141，验收结论 entry 000212 有条件通过）。P1-1 实验 commit_failure 重复回调幂等返回 False（state 场景11 + 脚本B 契约3.5 双适配器，漏网原因防复发）；P1-2 容量三通道冻结口径（dispatch=soft(在途+1)−2KiB / write=无在途 soft(0)+16KiB / >1MiB 硬拒；在线/实验写入通道一致；claim 保险丝转 CHECKPOINT_BUDGET_EXCEEDED 受阻收口）——验收反例 O-cap3/O-cap4 复现修复；P2-1 在线 amend 拒初建 + facade 兜底路径首次真实覆盖；P2-2 陈旧 docstring 清零（grep=0）；P2-3 resume 预检 _safe_schema_version 安全解析→422（'x'/'2.5'/99/'' 四态 + 同源修复 _v2_run_view 轮询不裸抛）。验证：定向 6/6 全绿（state 90/acceptance 7契约/storage 85/execution 15场景/budget_http/budget_resume）+ 全量回归 77/77 + ruff 全过。停在待复验，未合并 main。

- 决定：容量三通道为最终冻结口径（整合计划§13.6 记录）：dispatch(在途+1) 守卫、write 允许终态预留区（1MiB−8KiB 内）、硬上限 1MiB 不放松；跨终态回调整合差异（succeeded 后收 failure：实验抛/在线 False）作为已知差异如实登记未对齐（两者都不改写终态）；P2-3 属实现向文档对齐（400→422），不构成协议变更，未改接口文档
- 验证：python3 tests/run.py all → 77/77 全绿（/tmp/all_reg5.log）；定向：state 90 / acceptance 7契约双适配器 / storage 85 / execution 15场景 / budget_http / budget_resume 全过；验收反例实证：dispatch(0)=1013760 < soft(1)=1015808（余量2048覆盖claim）；write(0)=1040384=1MiB−8KiB；>1MiB 仍拒；ruff check workbench/ experiments/ tests/ → All checks passed
- 下一步：交验收方复验（对象 1df156f）；复验通过后待用户授权集成合并 main
- 依据/文档：文档/需求/20260920_从物料自动构建本体/修复指令_控输出v3验收问题_20260922.md；文档/需求/20260920_从物料自动构建本体/本体生成控输出_整合方案与并行开发计划_v3.md §13.6

### 本体生成控输出 v3 验收问题修复指令交付 · zcode · 需求已交付

时间：2026-09-22T13:35:15.897976+00:00；记录：`.collaboration/entries/000213-b0fea050f0c3.json`

用户要求交付修复指令。已写入 worktree 未跟踪文件 文档/需求/20260920_从物料自动构建本体/修复指令_控输出v3验收问题_20260922.md（自包含：总指令/环境边界/5 项冻结要求与建议实现/新增测试映射/验证清单/交付要求/复验口径），待实施者随修复提交入库。P1-1 实验 commit_failure 幂等返 False；P1-2 容量在途口径统一+16KiB 终态预留落地+执行器兜底转 blocked（四条冻结要求）；P2 三项（amend 拒初建+facade 兜底集成测试、docstring 清零、路由预检安全解析 422）。授权边界=原 worktree 修复+自测+提交，停在待复验；不含集成/合并。

- 下一步：实施者按指令修复并自测提交，交复验；复验口径=反例重放+脚本B/预算存储/执行器矩阵+全量回归；P1-2 具体实现路线为建议（guard 收紧 soft(1)、终态宽限 soft(0)+16KiB、claim 兜底），允许等价替代但四条冻结要求缺一不可
- 依据/文档：文档/需求/20260920_从物料自动构建本体/修复指令_控输出v3验收问题_20260922.md；验收结论 entry 000212-16f646c003a2

### 本体生成控输出 v3（codex/auto_build）独立验收 · zcode · 已验证

时间：2026-09-22T13:30:05.421049+00:00；记录：`.collaboration/entries/000212-16f646c003a2.json`

用户直接指令 zcode 执行独立验收（角色按用户指定优先）。验收 SHA=e365141（代码面 f72fb98，HEAD 未前进；较指令文档的 41efcc1 仅多两个文档/交接提交，无代码差异）。结论：有条件通过——主体与声明相符，但 2×P1+3×P2 需原分支修复后复验。P1-1：ExperimentState.commit_failure 重复回调抛 ValueError 而非冻结语义 False（在线侧返回 False；2ee24cd 只改在线侧；D17 脚本B幂等契约只测 commit_success）。P1-2：容量软阈值边界——在线 _plan_guard 按 inflight=0 放行、claim_job 按新文档在途=1 抛 CheckpointCapacityError 且执行器无捕获，未按冻结契约转 blocked/CHECKPOINT_BUDGET_EXCEEDED（约 8KiB 窗口，resume 不可自愈）；ExperimentState claim 用 inflight=0 与在线口径不一致。P2：在线 amend_plan 无既有计划检查可初建（实验侧拒绝，双实现不一致，无现行生产路径）；docstring 陈旧仍写幂等返回 True；post_run_resume 内联预检非整数 schemaVersion 裸抛 int() 得 500 而非 422。

- 验证：§3-B 全量回归 all 独立复跑 77/77（exit 0）；§3-C 前端 typecheck+build 复跑通过（/tmp/acc_v3_frontend.log）；§3-E 脚本A budget_e2e 直跑内部 28/28；脚本B 6 契约×online/experiment 双适配器全过；§3-A+§4 反例脚本 47 项（/tmp/acc_v3_counterexamples.py）：契约抽查四项全过；45 过 2 不符即 P1-1 与 amend P2；取消语义 5 项、路由守卫 6 例全过；§3-D 5 截图复核通过（合成种子 G3 展示验收，页面自带标注）；§3-F D18 抽查数字全部回溯一致，G2 三结论为未通过/未测/不可证未夸大；§3-G 红线全过：默认关闭+legacy；D18 证据无密钥命中；e365141 不在 main；18765/18890 PID 未动；工作树真实库 mtime 未触碰
- 下一步：原分支修复 P1-1、P1-2 后交复验（建议顺带三项 P2）；复验范围可限定反例重放 E-bool3/O-cap3/O-cap4/O-amend1 + 脚本B/预算存储矩阵重跑；验收通过后停在待用户授权集成；本轮未改业务代码、未合并 main、未 push、未启停 18765/18890
- 依据/文档：文档/需求/20260920_从物料自动构建本体/验收指令_控输出v3_20260922.md；证据：/tmp/acc_v3_regression_all.log、/tmp/acc_v3_frontend.log、/tmp/acc_v3_matrices.log、/tmp/acc_v3_counterexamples.py

### auto-build-output-v3/D18_报告 · zcode · 已实施，待验收

时间：2026-09-22T13:03:18.243804+00:00；记录：`.collaboration/entries/000209-bf762b23af56.json`

D18 真实试验效果报告已交付：/tmp/wiz_pilot_d18/D18_真实试验报告.md（249 行：实验设置/每臂结果表/G2 三项判定/机制结论/未完成项/codec 上线建议/脱敏声明）。本子任务只写报告，未跑试验、未改仓库代码（git status 无本人改动）。全部数字自 results.json 与状态库 SQLite 实读，26 项交叉断言 0 不一致。G2：质量门未过（D object 缺 6/6、property 缺 41/41、link 缺 2/4，命中 2/51、extra 134；34 条结构问题全 duplicateIdentity=17×2金样；precision A 0.0357→D 0.0147 降 2.10pp 超阈，recall A 0.0196→D 0.0392 升 1.96pp 未触阈）；compact 收益门未测（B/C 未跑，不编造收益数字）；成本取舍不可证（A 5/6 unknown、D 1/12 unknown）。A 臂首轮 3×TIMEOUT 已重跑完成且仍 failed（6 调用、knownCompletionTokens 8344、unknown 5、8 条 danglingRef、P 0.0357/R 0.0196），报告 A 列采信重跑、首轮仅作故障记录。

- 决定：codec 上线建议：机制可用，但 compact 收益未实测前不启用——保持在线默认 legacy-v1、compact-v1 仅实验能力默认关闭（对齐整合计划 v3 §12）；A 列采信 A 臂重跑（out-armA）；不用首轮 precision=1.0（空集默认值）作基线，否则得出下降 98.53pp 的假结论；拒绝编造：未给任何 compact 收益百分比与 D-vs-A 成本增幅；A/D 已知部分 7.2×/4.4×/6.6× 仅标非正式参考、不作判定依据；机制结论如实标注：截断拆分链路由 fake 回归覆盖、真实运行未触发（三个 run 的 jobSplit 均 0、无 finishReason=length）
- 验证：自读 results/.../simple-A-r1.json（首轮）、simple-D-r1.json、out-armA/results/.../simple-A-r1.json、两份 report.json/report.md、config.json/config-A.json、状态库 campaign.sqlite3 与 simple-{A,D}-r1.sqlite3（只读 uri）；26 项数值交叉断言（calls/known/unknown/finishReasons/errorCodes/states/durationMs/promptBytes/events/campaignCounters/qualitySummary/missingByType/coverage/budgetProfile）全部一致，0 不一致；脱敏核对：无 key/api_key/password/secret/bearer 样式串，无 prompt/response 正文键；候选 key/name 均来自仓库内合成 fixture；报告无遗留占位/待补文字（grep 确认）
- 下一步：待 Codex 验收报告（独立核对数字与 G2 口径）；补齐项（报告 §6）：原 8KB 真实样本并登记 hash；补跑 B/C 臂分离 codec 与装箱效应；消除跨作业重复身份与同义多命名；provider 返回 reasoning/completionBytes；排查 TIMEOUT 频发（三轮共 9 次）；报告在 /tmp（仓库外），如需入库归档请指定仓库内路径与提交范围
- 依据/文档：/tmp/wiz_pilot_d18/D18_真实试验报告.md；/tmp/wiz_pilot_d18/out-real3/report.json；/tmp/wiz_pilot_d18/out-armA/report.json；/tmp/wiz_pilot_d18/out-real3/results/d18-smoke-20260922/simple-D-r1.json；/tmp/wiz_pilot_d18/out-armA/results/d18-armA-20260922/simple-A-r1.json

### auto-build-output-v3/D14 · zcode · 已实施，待验收

时间：2026-09-22T11:45:59.977606+00:00；记录：`.collaboration/entries/000208-97e8b52fd85c.json`

D14 CLI/模型适配/四臂运行已实施（worktree auto_build，分支 codex/auto_build）：新建 experiments/ontology_token_pilot/adapter.py（FakeModel 脚本事件 length/429/bad_json/crash/unknown_usage、RealModel 懒加载 llm_client、load_config、load_sample_facts 确定性样本事实、run_arm、campaign 预算与暖缓存）、__main__.py（冻结用法 argparse CLI，退出码 0/2/1，report.json+report.md 含 G2 三项判定）、README.md 与 tests/test_ontology_token_pilot_cli.py（7 场景）。四臂全走 batch_execution.run_plan 唯一共享核心：C/D=plan_initial+缺省 pack_next，A/B 仅注入固定20批 planner 且差异登记 meta.adapterDiffs；AST 断言无第二套调度器。

- 决定：A/B 对照臂用 batch_execution._plan_more 既有 planner 注入点实现固定 20 事实批，不复制任何调度逻辑；A/B 无 plan_initial 初始作业；campaign 预算与模型响应暖缓存同放 <output-dir>/states/<campaign>/campaign.sqlite3（跨臂/样本/重复/续跑共享）；物理请求计数逐次即时落库（保守），已知 completion 按本次 runAttempt 收口补记不双计；终态（succeeded/failed/blocked）结果续跑跳过不重做，历史受阻结论保留进续跑报告 notes；重跑须显式删该 run 的结果与状态文件；compact encode_request 实现把目标单元放 payload['facts']（带 alias/unit），FakeModel 按实际实现协议响应（output_codec docstring 的 'units' 键与实现不一致，未改 D06 文件）；结果文件/报告统一过 D13 redact_report；real 模式仅显式 --mode real 时 import workbench 存储层，key 只留内存 provider dict
- 验证：python3 tests/run.py --test tests/test_ontology_token_pilot_cli.py → 退出码 0（7/7 场景：四臂 CLI 子进程、共享核心+AST 无第二调度器、截断 split 父+子入账、campaign maxAttempts=2 第二臂受阻退出2+续跑累计不重置、repeats=2 暖缓存零物理调用、compact coverage 全命中 decode+金样评价、参数错误退出1）；回归：tests/test_ontology_build_batch_execution.py、test_ontology_token_pilot_state.py、test_ontology_token_pilot_metrics.py、test_ontology_token_pilot_fixtures.py 全部通过；ruff check（新三文件）通过；--mode real 无 provider 时优雅受阻退出 2 并出报告（未派发任何真实调用）
- 下一步：real 模式实测与 G2 真实收益归 D18；已知限制：campaign 预算库随 output-dir 走，换目录=新预算库（v3 要求跨目录共享，未实现，待 C 裁决）；默认预算 128 物理请求跑不全三样本×四臂矩阵（约需 114 次，C/D 软目标装箱批较小）；扩额须配置显式给出；blocked/failed run 不自动重试（重跑须删该 run 文件）；如需协议登记可将 skip/预算受阻语义并入接口文档 08
- 依据/文档：experiments/ontology_token_pilot/adapter.py；experiments/ontology_token_pilot/__main__.py；experiments/ontology_token_pilot/README.md；tests/test_ontology_token_pilot_cli.py；文档/需求/20260920_从物料自动构建本体/本体生成控输出_整合方案与并行开发计划_v3.md §8/§11/§12

### auto-build-output-v3/D04 · zcode · 已实施，待验收

时间：2026-09-22T10:39:42.954515+00:00；记录：`.collaboration/entries/000207-82a59abec002.json`

D04 装箱/拆分/稳定ID/覆盖守恒已实施（worktree auto_build，分支 codex/auto_build）：新建 batch_plan.py（纯函数，仅 stdlib+contracts+budget）与 93 断言回归；plan_initial/pack_next/split_or_block/verify_split_coverage/job_definition/estimate_batch_messages 全按冻结规则实现，stable_job_id 复用 contracts。协议变更请求：field selector 拆分子目标为新派生 id，batch_state.job_split 现协议无登记事件，D09 前只能到映射层，待 C 裁决。

- 决定：pack_next/plan_initial/split_or_block 在冻结签名外仅追加 keyword 默认参数（plan_epoch/parent_job_id/parent_split_path/root_job_id/scope_payload），不影响冻结调用；软目标 V 用契约 cold_output_estimate(整批slots,整批I)；硬输出 ΣE 用 expected_output（校准取 max）；反馈收缩以候选单元 E 调 shrink_for_feedback 只缩不扩；field 子目标 targetId 按冻结公式本地派生（t-+sha256(factId+selector)[:16]），不 import semantic_units；field 拆分子作业 contextFactIds 置空（同事实不得既当主目标又当背景）；单元/目标二分子作业 context 取对侧事实 ≤8
- 验证：python3 tests/run.py --test tests/test_ontology_build_batch_plan.py → 退出码 0（93/93）；ruff check 两新文件 → All checks passed；相邻回归 budget/semantic_units/batch_state 测试各 1/1 通过；batch_state 联调：5 个事件全部受理、validate_plan_doc 零错误、pending 清空、coverage_check ok
- 下一步：待 Codex 独立验收 D04（不合并 main）；selector 子目标入状态机的协议扩层待 C 裁决；D09 接线时复核 pack_next 整批重估 O(n²) 开销
- 依据/文档：workbench/ontology_build/batch_plan.py；tests/test_ontology_build_batch_plan.py；workbench/ontology_build/batch_contracts.py；文档/需求/20260920_从物料自动构建本体/本体生成控输出_整合方案与并行开发计划_v3.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### auto-build-output-v3/D10 · zcode · 已实施，待验收

时间：2026-09-22T10:32:03.794423+00:00；记录：`.collaboration/entries/000206-bdcb2618e958.json`

D10 终态/lease/取消/旧版本守卫已实施：runner.py 纯追加 5 个守卫（finish_success_guarded 先跑 final_state_check，不过返回 (False, violations) 不改 run；resume_plan_guard 只读分 legacy_*/v2_*，未知版本 UNKNOWN_CHECKPOINT_SCHEMA 绝不删候选；plan_fingerprint_matches；resumable_jobs；claim_for_resume 进入即轮换 lease）。既有函数零改动（183 行全追加）。新测试 10 场景 93 断言全过（退出码 0），既有 8 套相关回归全绿。未提交 git（按指令，待协调者）。

- 决定：finish_success_guarded 冻结 (ok, violations) 元组：拒绝时不写 failed+blocking、不动 run（任务书冻结口径），由 D16 决定后续；resumable_jobs 可续叶=queued/running/failed（§9 场景5 右子返回）；blocked 不返回（§6 须显式新计划）、superseded 不返回（§4.3）、成功叶不重做；begin_worker_scope(lease=None) 只读不轮换、不构成接管，故另立 claim_for_resume（复用 submit 的轮换语义，context manager，退出作废）；resume_plan_guard 防御码 RESUME_MODE_INVALID/RUN_NOT_FOUND/RESUME_GUARD_ERROR（fail-closed），在冻结返回形状内；runner.py 顶部追加 import json、batch_contracts（后者仅标准库依赖，无环）
- 验证：python3 tests/run.py --test tests/test_ontology_build_budget_resume.py → 退出码 0（93/93）；python3 tests/run.py --test tests/test_ontology_build_finish_guard.py → 退出码 0；python3 tests/run.py --test tests/test_ontology_build_runner_isolation.py → 退出码 0（34/34）；附加回归退出码均 0：late_write、progress_log、batch_state、batch_accounting、budget_storage、test_ontology_build(HTTP e2e)；ruff check 两文件 All checks passed
- 下一步：D16：resume 路由先调 resume_plan_guard；auto 用 plan_fingerprint_matches（期望指纹按冻结 parts 组装）不一致→422 BUDGET_PLAN_MISMATCH；abstract 按 v2_abstract 建新 epoch（候选清理+计划替换同事务）；D16：generate 收尾改调 finish_success_guarded（plan_doc 传 checkpoint 形态 {'generate': doc}），拒绝时不标 succeeded；协调者安排 git 提交（本轮未操作 git）
- 依据/文档：workbench/ontology_build/runner.py；tests/test_ontology_build_budget_resume.py；workbench/ontology_build/batch_contracts.py；文档/需求/20260920_从物料自动构建本体/本体生成控输出_整合方案与并行开发计划_v3.md；文档/接口文档/08-从物料自动构建本体接口.md §14.5

### auto-build-output-v3/D12 · zcode · 已实施，待验收

时间：2026-09-22T10:02:33.780153+00:00；记录：`.collaboration/entries/000205-a6d21e4f29e5.json`

D12 试验 SQLite 适配与隔离缓存已实施：新建 experiments/ontology_token_pilot/state.py（ExperimentState/open_state/candidate_id_for/CheckpointCapacityError；标准库 sqlite3，WAL+busy_timeout=5000，显式 BEGIN IMMEDIATE…COMMIT 异常回滚；复用 batch_state.apply_event/validate_plan_doc 推进与校验，不复制状态机；计划 doc 整体 JSON 列，候选结构化列+payload_json 全量列，candidate_id='c-'+jobId[:12]+'-'+局部键 幂等；每次落库前 contracts.checkpoint_fits 守卫超限抛本地 CheckpointCapacityError；冷缓存独立 KV 表 cache_get/cache_put 支持 ttl、跟 db_path 跨重跑共用）与 tests/test_ontology_token_pilot_state.py（10 场景 82 断言：往返/claim 先落库/commit_success 原子与注入候选表 drop 回滚/幂等/split 父子同事务且子可 claim/interrupted_unknown 聚合 unknownUsageCalls/501 候选分页无重无漏/超 1MiB 容量与 commit 路径注入上限回滚/冷缓存 ttl/双库隔离+零 workbench.storage 依赖）。

- 决定：commit_success 返回值口径：首次提交 True；重复回调（job/attempt 均已 succeeded）返回 False 跳过不写候选；attempt 非 started / job 非 running 一律 ValueError（晚结果拒绝、取消不复活）——已写入模块 docstring 供 D17 双适配器参数化对照；claim_job：queued→claimed+started；running（崩溃恢复续尝试）→直接追加 started；其余状态 ValueError；requestedMaxTokens 按 job.estimate（requestedMaxTokens/requestOutputTokens/expectedOutputTokens）→budgetProfile.requestOutputTokens→contracts 默认值派生；commit_split：在途 started 尝试按 attempt_succeeded+finishReason='length' 收口（usage 取 event_usage）；queued 父作业无在途直接拆分不记账；子作业合法性（覆盖守恒/深度/id 冲突）全部由 batch_state.job_split 校验；save_plan 守卫顺序：validate_plan_doc → checkpoint_fits（超限 CheckpointCapacityError 库内不变）→ 事务；同指纹重存幂等覆盖、异指纹 ValueError 拒绝串写；mark_interrupted_unknown：不存在/非 started id 跳过不抛，返回实际收口数；同一事务批量收口
- 验证：python3 tests/run.py --test tests/test_ontology_token_pilot_state.py → 退出码 0（82/82 断言通过）；python3 tests/test_ontology_token_pilot_state.py 直跑退出码 0；~/Library/Python/3.9/bin/ruff check experiments tests/test_ontology_token_pilot_state.py → All checks passed；回归复跑：test_ontology_build_batch_state.py、test_ontology_build_batch_accounting.py、test_ontology_token_pilot_fixtures.py 均 0；场景 10 断言导入后 sys.modules 无 workbench.storage、state.py 源码无该 import 语句
- 下一步：D17 组合时统一 CheckpointCapacityError 与双适配器参数化用例（含 commit_success 返回值口径核对）；D09/D14 使用本模块时按 docstring 的方法语义要点接线；Persistence 方法签名清单已在交付报告列出
- 依据/文档：experiments/ontology_token_pilot/state.py；tests/test_ontology_token_pilot_state.py；workbench/ontology_build/batch_contracts.py（持久化接口节，D00 冻结）；workbench/ontology_build/batch_state.py（D07）；文档/需求/20260920_从物料自动构建本体/本体生成控输出_整合方案与并行开发计划_v3.md §8 D12 行、§11

### auto-build-output-v3/D13 · zcode · 已实施，待验收

时间：2026-09-22T09:58:32.990690+00:00；记录：`.collaboration/entries/000204-7c61d8e7a6ee.json`

D13 用量/质量评价与脱敏报告已实施：新建 experiments/ontology_token_pilot/evaluate.py（UsageLedger/evaluate_candidates/quality_gate/cost_compare/redact_report/scan_report/build_report，纯函数，仅依赖 batch_contracts）与 tests/test_ontology_token_pilot_metrics.py（11 场景 88 断言）+ experiments 两级空包标记。验证命令退出码 0（88/88），ruff 通过，D11 回归复跑仍 0。

- 决定：Ledger 只吃 attempt 列表不去重不丢弃：父截断+子调用+格式修复+网络重试全入账（一次一记责任在执行器 content_tx）；token 口径逐字复用 contracts.usage_aggregate；reasoningTokens 只单列（已知求和，无已知为 None）绝不并入 completion；候选↔金样=机械归一化后精确身份匹配（camel→snake/小写/分隔符折叠；property 去 owner 前缀；ownerKey 参与身份），一对一消耗，不做模糊匹配；dataType/单位/枚举差异进 quality.fieldsMismatched 明细不改分母；同名异主体：一对一匹配天然不误判 matched，未匹配组进 mergeGroups，多余候选键=组 rawName 记 mergeSuspects；重复身份计 duplicateIdentity；link 悬空引用/空键/非法类型/rejectedRefs 进 structuralIssues；ownerKey 悬空仅调用方提供 allowed_owner_keys 时核验；cost_compare 中位数只用已知样本；任一臂有 unknown → savingsProvable=False 且 compactBenefitGate=None（无法判定≠False 通过）；-20% 为 G2 compact 收益门。quality_gate：三类金样缺失=0+冲突保留 100%+无结构问题+基线降≤2pp；'端点'以链接端点代替，HTTP 端点门归 D16/D17；redact：删 prompt/response 类键、掩密钥形键值/sk-/Bearer/长随机串，纯 hex≥40 按 hash 保留（goldenSha256 不误伤），脱敏幂等；build_report 输出全可 json.dumps 并防御性再脱敏
- 验证：python3 tests/run.py --test tests/test_ontology_token_pilot_metrics.py → 退出码 0，通过 88/88（直跑同 0）；ruff check experiments/ tests/test_ontology_token_pilot_metrics.py → All checks passed；python3 tests/run.py --test tests/test_ontology_token_pilot_fixtures.py → 退出码 0（D11 不受影响）；4 份真实 D11 金样镜像候选全命中（precision=recall=1.0、冲突 covered==expected、质量门全过）——G2 关键金样 100% 路径可达成
- 下一步：D14 CLI 接 evaluate：四臂跑分后用 quality_gate(arm_D, arm_A) 与 cost_compare(arm_A, arm_B) 出报告；D17 引用 scan_report 做脱敏证据；G2 真实样本精确率/召回率与 compact 收益门需 D18 真实试验数据，本轮只交付机制
- 依据/文档：文档/需求/20260920_从物料自动构建本体/本体生成控输出_整合方案与并行开发计划_v3.md §8 D13/§12 G2；workbench/ontology_build/batch_contracts.py（HEAD c5eadc5：normalize_usage 幂等/attempts errorCode errorMessage）；tests/fixtures/ontology_token_pilot/golden/（f391679，D11）；experiments/ontology_token_pilot/evaluate.py

### auto-build-output-v3 D02 完整请求估算与反馈收缩 · zcode · 已实施，待验收

时间：2026-09-22T09:50:48.323039+00:00；记录：`.collaboration/entries/000203-092763936f2a.json`

在 worktree/auto_build（分支 codex/auto_build，基线含 3995586 D01 与契约 3 处注释级修订，HEAD c5eadc5）交付 D02：新建 workbench/ontology_build/budget.py 与 tests/test_ontology_build_budget.py。estimate_request/cold_output_estimate 从 batch_contracts 再导出（函数身份等同，未复制逻辑）；新增 unit_slots/target_slots（孤立1、adjacent_window或data无结构4、同身份去重）、bucket_key/update_bucket/calibrated_estimate 校准桶族（<20 max×1.25、≥20 P90×1.25 线性插值、截断拒绝、unknown usage 不记 0、滑窗有界 20）、expected_output（E=max(冷启动,校准) 永不低于冷启动、整 token 向上取整）、shrink_for_feedback（只缩不扩、无反馈保持原值、单单元放不下→0）、check_input_budget（I+L+reserve≤C 边界相等通过、超限中文可读原因）、snapshot_bounds（CALIBRATION_MAX_SAMPLES=20 有界化、保留 count/max、不改传入）。import 仅 stdlib+batch_contracts（sys.modules 验证零其他 workbench 导入）。

- 决定：update_bucket 增加 truncated=False 可选关键字（任务给定四参调用形式不变）：截断样本双保险拒绝，执行器对 finish_reason=length 必须传 True 或不调用；桶 samples 为最近 20 个比率的滑窗（裁最旧），count 为历史累计接受数——≥20 判定用 count，不受窗口裁剪影响；snapshot_bounds 对手工/遗留无界桶再做一次有界化；expected_output 返回整 token（向上取整，宁高估不低估）；calibration 参数兼容 None/数值/(buckets,key) 元组三种形式；显式 coldOutput 仍受契约 1024 下限收口；unit_slots 身份口径：优先 targetId，缺失回退 contracts.target_digest（factId+selector，与覆盖比对同口径）；未发现契约缺陷，无协议变更请求
- 验证：python3 tests/run.py --test tests/test_ontology_build_budget.py → 退出码 0（93/93 通过）；python3 tests/test_ontology_build_budget.py 直跑 → 退出码 0；ruff check 两个新文件 → 退出码 0；导入隔离：import budget 后 sys.modules 无 batch_contracts/budget 之外的 workbench 模块；9 场景：utf8_proxy 逐字一致（中/英/多消息/空）/slots 口径与去重/冷启动边界（slots=0→1、下限1024、ceil 逐点）/校准桶 19→1250 与 ≥20→P90 插值1500（非max 3750）与截断/unknown拒绝与维度隔离与滑窗/E 永不低于冷启动/shrink 只缩不扩/输入预算边界156000相等通过超1拒绝/快照有界化保留count/max/零副作用（os.environ浅拷贝+传入对象不变）
- 下一步：待 Codex 独立验收（P(D02) 命令）；不合并 main；D04 batch_plan.py 可按契约基于本模块开工
- 依据/文档：workbench/ontology_build/batch_contracts.py（估算节，含 normalize_usage 幂等化等 3 处修订）；workbench/ontology_build/budget.py；tests/test_ontology_build_budget.py；文档/需求/20260920_从物料自动构建本体/本体生成控输出_整合方案与并行开发计划_v3.md §4.2/§4.3/§8 D02

### auto-build-output-v3/D06_output_codec · zcode · 已实施，待验收

时间：2026-09-22T08:52:26.710472+00:00；记录：`.collaboration/entries/000202-4bc4775e8b17.json`

D06 交付：新建 workbench/ontology_build/output_codec.py（legacy-v1/compact-v1 编解码纯函数，冻结签名 encode_request/decode_response，净化与 llm._sanitize_candidates 同语义的独立实现，不 import llm/pipeline/storage/llm_client）与 tests/test_ontology_build_output_codec.py（11 场景 126 断言）。验证命令退出码 0，ruff 通过。按任务约束未操作 git，提交留协调者。

- 决定：证据范围口径：两种 codec 的 evidence 只认 aliasMap 值=本次主目标事实；背景事实载荷不带 id、不可被引用（legacy 主目标给真实 factId，compact 只给 f0/f1 别名，aliasMap 两种 codec 都返回）；compact coverage 按 expected_unit_ids（=encode 的 unitIds=targetId）比对，缺任一 ok=False+COVERAGE_INCOMPLETE 且 candidates 恒空；多余未知单元只记 note；evidence 未知别名→整条丢候选+DANGLING_REFERENCE；conflicts 某侧别名未知→只丢该侧（不足两侧整条冲突丢弃，候选保留）；decode 对非 length 的其他非 stop finish_reason 返回 FORMAT_INVALID；未知 codec 编解码两侧抛 ValueError；compact 顶层 codecVersion 必填校验，legacy 不要求（现行结构兼容）；缺 definition/fields/ownerKey 输出空串/空 dict；evidenceStatus 缺失/未识别落 inferred，绝不默认 supported；selector 切片由调用方完成（内容经 target[fact] 或 entry snippet/data 传入）
- 验证：python3 tests/run.py --test tests/test_ontology_build_output_codec.py → 退出码 0（通过 126/126）；ruff check 两文件 → All checks passed；冒烟：import 不加载任何 llm/pipeline/storage 模块；整合计划 §10 compact 示例形状解码 ok=True 且别名还原真实 factId
- 下一步：待 Codex 独立验收（alias还原/缺字段不补造/截断拒绝/legacy兼容）；协调者留意：batch_contracts.py §编解码 注释的 encode_request 形参（plan_doc/job/targets_by_id/facts_by_id）与本次按任务指令实现的扁平纯函数签名不一致，建议协调者统一契约注释（该文件归 C，本任务未改动）
- 依据/文档：workbench/ontology_build/output_codec.py；tests/test_ontology_build_output_codec.py；workbench/ontology_build/batch_contracts.py；文档/需求/20260920_从物料自动构建本体/本体生成控输出_整合方案与并行开发计划_v3.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

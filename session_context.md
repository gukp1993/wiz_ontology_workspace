# Codex / zcode 共享上下文

上下文版本：`ed5ea88d4afe7339`

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

### model_setting · zcode · 需求已交付

时间：2026-09-25T04:30:56.898730+00:00；记录：`.collaboration/entries/000315-6011f4a0a13d.json`

【zcode】用户对 model_setting worktree 提出需求变更：①列表只保留模型列表（字段=模型名称/供应商/操作=测试连通·编辑·删除）；②添加模型改单弹窗（供应商预设目录下拉含搜索与「其他→自定义」、API Key+测试连接、模型名称预设供应商=目录下拉/自定义=手填接口地址+参数名），不含「高级配置」。本轮仅交付交互原型 v1（纯前端静态演示）到 文档/v2/需求/20260925_模型设置简化改版/，待用户审核；需求说明/开发计划/执行指令待原型确认后补齐。

- 决定：v4 两级交互废弃：可用模型/供应商双 Tab、供应商模板选择页、供应商编辑弹窗不再保留，收敛为单层模型列表；明确去除项：截图中的高级配置（工具调用/图片输入/思考模式/自定义协议）、输入/输出计价字段、Auto 模型选项均不纳入；默认模型机制建议保留（运行时语义沿用主干：未指定配置的节点使用默认），交互建议=编辑弹窗「设为默认」开关+列表徽标，已在原型标注「建议方案·待确认」；同供应商再次添加模型时 API Key 可留空沿用已保存密钥（密钥只写不读回不变），原型按此演示
- 验证：原型为纯前端静态演示，全部数据内置示意，不发真实网络请求、不落库；内联脚本已抽取做 node --check 语法检查通过；文件：文档/v2/需求/20260925_模型设置简化改版/交互原型_v1.html
- 下一步：用户审核原型（重点：三列字段与操作、弹窗字段构成、去除项、默认模型建议方案、预设供应商目录收录范围与命名）；原型确认后补需求说明/开发计划/执行指令三份文档，再在已登记 model_setting worktree（分支 model_setting，端口 18971，含真实数据副本不可自动丢弃）实施；不新建 worktree、不合并 main
- 依据/文档：文档/v2/需求/20260925_模型设置简化改版/交互原型_v1.html；文档/v1/需求/20260922_模型供应商与模型管理改版/（被变更的旧需求）；worktree/model_setting（分支 model_setting@cdb5939，http://127.0.0.1:18971）
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### gpt_fix · zcode · 已实施，待验收

时间：2026-09-25T03:16:53.913232+00:00；记录：`.collaboration/entries/000312-47b83f0d2912.json`

【Codex G1 验收整改完成停待复验】按 entry 000311 指令在原 worktree（基线 8b50cc9）单提交 4e24bc1 修复 health.record_failure 泄漏：degradedReasons.message 与 RECOVERY_FAILURE 日志不再含 str(exc) 原文，删除完整 traceback 输出，诊断能力改为固定 component/action 摘要+异常类别名+安全堆栈位置（basename/函数/行号，最近 3 帧）；核验 5 处调用方 message 均为固定摘要。test_recovery_health 反转原「原文+堆栈进日志」断言，注入合成凭据标记/带口令 DSN/多行异常，断言 /api/health、6 个准入端点 503 响应体、stderr 三处零泄漏；维护失败零原文；新增存储临时不可读双重语义（health degraded(runtime)+buildRunsAllowed 不翻转，请求仍 503 STORAGE_UNAVAILABLE）。01 分册 §8.2/§8.3：buildRunsAllowed 明确为恢复失败准入状态，删除「完整堆栈只走服务端日志」表述，字段与状态码不变。六条门禁命令退出码均 0，5 个既有 blocked 保持 blocked。AutoGLM 浏览器未登录属验收环境阻塞，未归入业务整改。

- 决定：异常原文在任何输出通道零保留，按指令用类别+安全位置替代，不做正则脱敏；安全堆栈位置用文件 basename 防本机用户名泄露；存储不可读 runtime 原因不改变 buildRunsAllowed 判定（准入门单一来源=恢复失败状态）
- 验证：实际退出码：--test test_recovery_health.py=0；run.py all=0（136/141+5 blocked 不计失败）；run.py frontend=0（44 过+5 blocked）；typecheck=0/lint=0/build=0；5 个既有 blocked（assist×2/flow/legacyGraph/ontology_import）保持 blocked 未写成通过；单跑日志确认新链路生效：health 200、build-task-create 503（存储不可用对照）；未合并 main、未重启 18765、未动库快照/密钥/真实 ontology/，工作树干净
- 下一步：交 Codex 独立复验新 SHA 4e24bc1（对比 8b50cc9 仅 1 个修复提交）；复验通过停在待用户授权集成；AutoGLM 扩展登录后补浏览器页面验收；其余 G1 验收项无新修复指令
- 依据/文档：.collaboration/entries/000311（Codex 整改指令）；文档/接口文档/01-通用约定与数据模型.md §8.2/§8.3；workbench-tasks/gpt_fix.json（headSha=4e24bc1）

### gpt_fix-remediation-instruction · codex · 需求已交付

时间：2026-09-25T02:59:28.548873+00:00；记录：`.collaboration/entries/000311-88a56a6825b4.json`

已针对 Codex 独立验收的 gpt_fix G1 T08 阻断项整理原 worktree 整改开发指令：修复 health.record_failure 对外/日志泄漏原始异常文本、更新安全回归及 01 健康契约字段口径；维持现有 503 准入与存储不可用语义。浏览器 AutoGLM 扩展未登录是验收环境阻塞，不归入业务代码整改。

- 决定：原 gpt_fix worktree 修复并提交新 SHA，不合并 main、不重启主服务、不写真实数据；Codex 对新 SHA 独立复验。
- 验证：整改输入基线仍为 gpt_fix HEAD 8b50cc9，工作树干净；health.py:40-73 原始异常文本进入 degradedReasons 和 stderr；tests/test_recovery_health.py:231 旧断言要求原始异常入日志，需反转。
- 下一步：交 zcode 按本轮指令实施、自测、提交；AutoGLM 扩展登录后由 Codex 补浏览器页面验收。
- 依据/文档：gpt_fix Codex 验收 entry 000310-9739df113713.json；workbench/health.py:40-73；文档/接口文档/01-通用约定与数据模型.md:341-357

### gpt_fix · codex · 受阻

时间：2026-09-25T02:54:05.114055+00:00；记录：`.collaboration/entries/000310-9739df113713.json`

Codex 独立验收 gpt_fix G1：实际 HEAD 8b50cc9 仅比业务验收 SHA c9a0001 多验收指令文档。全量门禁及隔离实机扫描通过，但 T08 健康错误摘要把原始异常文本写入已登录可读的 degradedReasons 与服务日志，违反无凭据契约；浏览器自动化因 AutoGLM 扩展未登录未完成。暂不签收，不合并 main。

- 验证：python3 tests/run.py all：136/141 通过，5 blocked，退出码 0；frontend：44/49，5 blocked，退出码 0；5 项 blocked 在 3f29a22 基线复跑均失败。；frontend npm run typecheck、lint、build 均退出 0；G1 容量/解析/Finalizer/门禁/取消晚写入用例通过。；隔离 18961 实机：health 未登录 401、登录后 ok/schema 0007；parser v3；7 成员 Fact 路径及 manifest v3；scan-only unknown/false；预检 422；验收服务已停。；合成哨兵验证 workbench/health.py record_failure：异常文本同时出现在 health.reasons().message 和 stderr；接口文档 01 §8.2 禁止凭据进入响应/日志。
- 下一步：zcode 在原 gpt_fix worktree 修复 health.py:40-73：对外和结构化日志只用稳定错误类别/脱敏摘要，补含敏感文本的回归；不合并 main。；AutoGLM 扩展登录后补浏览器页面验收；修复提交后 Codex 复验新 SHA。
- 依据/文档：c9a0001；8b50cc9；文档/v2/需求/2026_0924gpt优化/04_验收指令_gpt_fix.md

### auto_test-治理底座 · zcode · 已实施，待验收

时间：2026-09-24T20:39:44.654136+00:00；记录：`.collaboration/entries/000309-c8540b61df1d.json`

【auto_test Wave2 + 测试-验收-开发循环收敛完成】按用户指令（开发后开启测试-验收-开发循环直至无问题）：Wave2a 并行 T04 本体版本服务（30 用例/151 断言：不可变版本/去重/兼容分类器 T08 预埋/archive 引用守卫）+ T05 映射版本服务（86 断言：排序语义哈希顺序无关/函数版本入哈希/静态引用校验/主键指纹）→ Wave2b T06 Release Bundle 服务（36 用例：创建/冻结/派生/哈希复算/兼容门禁五处一致）。验收循环 R1 双对抗审查 FAIL（契约域 P0×1 freeze 挪用终态 + P1×2；服务域 P1×3）→ 修复批次 17 文件（frozen_at 标记裁定/门禁复算入写事务体/唯一约束防并发重复行/canonical 深度上限+稳定错误码包装/P2 批 10 项+回归锁定）→ R2 双代理复验 PASS（无 P0/P1；探针 107+ 断言；冻结包可派生裁定经双方独立判定与 FR-03/AC-BUNDLE-004 自洽）→ 文档死句清理。分支六提交至 6badd95；登记 cbb121c(main)。未合并 main，待 Codex 独立验收。

- 决定：freeze 语义=frozen_at 标记列（修未合并 at06 迁移），state 保持 DRAFT；BundleState.FROZEN 保持规格终态，states.py 迁移表零改动；derive 源包守卫=仅拒无出边状态（5 终态+SUPERSEDED），冻结包可派生——R1 修复轮从严执行的「冻结不可派生」与 FR-03/AC-BUNDLE-004 矛盾，R2 裁定纠正；FULL 激活必经 CANARY（APPROVED→ACTIVE 不补边）；TESTED→IN_REVIEW 门禁证据属 Wave3、ROLLED_BACK 证据属 Wave4（开发计划 §3.2 裁定记录）；新增 (bundle_id,resource_type) 唯一约束 + store 层 kind 白名单/mark 单向校验/find_request_id_global 三态信号
- 验证：R2 复验：契约域+服务域双 PASS（无 P0/P1）；R1 全部发现探针实证修复；全量回归 91/91（独立复跑）、vue-tsc 0 错误、临时根迁移演练 0005→at06 升级含新列新约束且 downgrade 干净；分支六提交：15e6e2d/84878a3/81ebd42/e268fec/a672edc/6badd95；测试套件 states 27/contracts 35/store 60 项/ontology 37/mapping 100+/bundles 41+；真实库未动；工作树 session_context.md（auto-generated）未提交属协作摘要噪声，已在登记注明
- 下一步：交 Codex 独立验收（建议验收 SHA 6badd95）；验收通过停在待用户授权集成；集成注意：e268fec 旧形态库升级不自动补 frozen_at/唯一约束（迁移 docstring 已登记，集成负责人处理）；Wave3 门禁/测试证据绑定须按 bundle_id 而非仅 hash（整包复制派生同 hash）；Wave3（T09/T10/T14/T15 并行→T16~T19 差分与快照双构建）待用户排期
- 依据/文档：workbench-tasks/auto_test.json；提交 6badd95；worktree/auto_test 文档/v2/需求/2026_0924自动化测试/开发计划.md §3.1/§3.2；workbench/release_governance/{bundles,ontology_versions,mapping_versions,states,contracts,canonical}.py
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### gpt_fix-T07-真实阶段进度页 · zcode · 已实施，待验收

时间：2026-09-24T19:02:16.523096+00:00；记录：`.collaboration/entries/000306-fdd120480b5b.json`

gpt_fix T07 真实阶段/部分结果/进度页实施完成（修 F07，未提交待协调者收口）：BuildProgressPage.vue 终态阶段状态改读 run.completion.stageRecords（running/completed/failed/skipped/notApplicable 如实显示；缺记录=未知+解释，绝不从总 state 推断 done；skipped 带报告内原因文案；v2/报告登记了 plan 时插 plan 行，登记外记录如失败报告 merge 追加显示）；排队/进行中仍按 Run.stage 实时显示（08 §15.3 实时态口径）。执行状态与覆盖完整度分开：succeeded+partial 徽标「执行结束（部分覆盖）」pill-warn 而非绿色已完成；终态新增「结果与完整性（完成报告）」块（执行状态/完整度/覆盖账本五桶+分母/剩余未完成逐项可追踪/losses/blockers 可读文案+码/评审状态/交付状态/生成收尾）。partial/unknown 语义：评审入口开放（按钮 title+提示行解释）、本页无默认交付按钮且如实说明服务端门禁会拒绝、不提供忽略缺口入口。轮询失败保留已展示结果（既有行为保留+测试锁定）；runId/taskId 上下文恢复沿用既有 watcher 机制+测试锁定；状态不只靠颜色（徽标文字/圆圈字形 ✓✗？–/口径说明行）。分母稳定：账本由服务端给出页面不改写，v2 注明 split 父作业不重复计入。

- 决定：终态一律记录驱动：stageRecords 存在按记录显示、无报告的终态（旧 run/收尾前失败/取消）全部阶段显示未知并解释「没有可读的完成报告」，不做实时位置推断——按 08 §15.3「stageRecords 是终态快照、缺记录按未知呈现」与 G1 冻结政策「不回填 complete」；排队/进行中保留 Run.stage 实时推进显示，两者用 stageSourceNote 口径说明行区分；succeeded+partial/unknown 的评审入口保持开放（reviewable 语义=执行终态即可评审），交付入口本页本来就没有——按指令「按页面现有交付入口实际接线处理」：不新造交付按钮，用交付状态行+部分结果提示行如实说明「创建本体会被服务端门禁拒绝（客户端自报完整无效）」，评审→保存页链路不动（T05 域）；capacity 块前端不可用：核实 storage.run_checkpoint_view 与 batch_contracts.generate_checkpoint_view 均剥除 capacity（run 响应无此块），剩余/未处理追踪按 completion.coverage+losses(INPUT_NOT_PROCESSED) 呈现（§17.1 明确账本是 partial 判定机器可读数据源）；如需同区展示 modelFacts 计数需后端视图层补透传（非 T07 前端权限，留给协调者裁决）；阶段展示超集=固定五阶段+finalize（v2 或记录含 plan 时插 plan），报告登记外阶段键（失败报告 merge）追加显示，label 优先报告自带字段；测试走 @vue/compiler-sfc+空渲染器挂载真实组件（project_binding_guard 同款），./types 用真实转译模块、./api 与 AppError 桩化，断言=setup 绑定+渲染文本树+源码静态三层
- 验证：node tests/build_progress_page.test.mjs = 8/8 通过（C0 全记录 complete 分开呈现；C1 UI-03 succeeded+partial+缺 finalize 不显示完整完成/交付不开放/评审开放/blockers 可见；C2 UI-04 缺记录阶段未知+解释不推断 done；C3 coverage 500/520 剩余 17 可追踪+blocker 文案；C4 无 completion 字段 completionOf 兜底 unknown 不显示完成；C5 轮询失败保留已展示结果+AppError 如实；C6 runId 变化按新 run 拉取+无旧推断静态断言；C6b 本页无交付请求）；cd frontend && npm run typecheck = 0 错误（修掉一处 TS2367 恒真比较）；npx eslint src/ontology/build/BuildProgressPage.vue 无告警；回归：node --import ./tests/ts_hooks.mjs tests/ontology_build_frontend.test.mjs 通过（api.ts 未动）；ProjectBinding.vue/App.vue/types.ts/api.ts 未改，后端文件未碰（工作树中 pipeline.py/review.py 的未提交改动属 T05 并行工作，已核对归属、未触碰）；隔离纪律：未 git commit、未启动服务、未跑 build（协调者统一构建）、data/ keys/ ontology/ 主树 auto_build 树未动；已知限制：浏览器像素级验收（徽标/账本排版）与真实轮询节拍留协调者组合冒烟
- 下一步：协调者：与 T05 的 review.py/pipeline.py 改动组合回归后串行提交（建议 commit：feat(ontology-build): T07 真实阶段进度页——stageRecords 驱动+四概念分开展示）；npm run build 统一执行；后端待办（非 T07 权限）：若需要页面同区展示 capacity modelFacts 计数，需 storage.run_checkpoint_view/batch_contracts.generate_checkpoint_view 透传 capacity 块（§17.1 契约已写，当前 run 响应未带）；T05：交付门禁接线后，页面「交付状态」文案无需改动（deliverable/blockers 已按报告如实显示）；评审→保存页链路的门禁 UI 归 T05/协调者；浏览器验收建议：succeeded+partial 与旧 run（无报告）两场景各看一次徽标/阶段未知解释/结果与完整性块排版
- 依据/文档：worktree/gpt_fix/frontend/src/ontology/build/BuildProgressPage.vue；worktree/gpt_fix/tests/build_progress_page.test.mjs；文档/接口文档/08-从物料自动构建本体接口.md §15/§15.3/§17；文档/v2/需求/2026_0924gpt优化/03_gpt_fix_G1基线矩阵与任务派发.md（§3 冻结政策、§4 T07 归属）

### gpt_fix-T02容量语义移植+扩展 · zcode · 已实施，待验收

时间：2026-09-24T17:44:41.979777+00:00；记录：`.collaboration/entries/000305-66bacbf6c69f.json`

【gpt_fix T02 输入/输出容量不静默丢失实施完成（未提交，等协调者收口）】F02 核对：auto_build 01026de 只移除了 pipeline 收口 500 裁切并加 capacity 块，llm._sanitize_candidates 与 output_codec 两个净化函数的 break 截断仍在——本轮在 gpt_fix 修复：净化不再按数量上限截断，超限登记 CANDIDATE_LIMIT_EXCEEDED+原始计数、候选完整保留；唯一接受上限为候选字节预算 8MiB，耗尽=RESPONSE_TOO_LARGE 显式失败终态（固定路径拆批深度≤2 调用有界）。净化返回 candidateStats 十键（三计数+rejectedRefs+cap/budget+events），净化拒收/超限丢弃/合并去重三事件分离。移植 01026de：run summary/checkpoint capacity 块（六键对齐+T02 五键只增）；计划复用路径「报 truncated 却不真截断」缺陷一并修复；收口与 v2 路径 capacity 块；移除最终候选裁切。RESUME-01 未改 runner，以测试断言 content_tx fencing 两形态。tests/test_capacity_semantics.py 62 断言全过（T18a 夹具），相邻回归 22 个测试文件全绿。接口文档 08 新增 §17+变更记录一行；output_codec 测试冻结键断言同步。

- 决定：F02 修复取「完整保存+显式事件」：单响应候选超 500 不拆批不失败（全保留+事件），字节预算才是硬上限——与移除收口裁切后的语义自洽；CANDIDATE_LIMIT_EXCEEDED 落 batch_contracts（追加 3 行）；预算耗尽复用既有 RESPONSE_TOO_LARGE，不造第二套码；拆批触发扩为 截断 or candidateStats.budgetExhausted；split.cause=output-truncated|candidate-byte-budget 两条独立判定不混同；接口文档容量节编 §17（§16 被并行 T03 占用）；capacity 块即 CompletionReport 判 partial 的机器可读数据源，报告产出接线归 T04
- 验证：tests/run.py --test tests/test_capacity_semantics.py = 62/62（CAP-01 520→500+excludedIds 可追踪；CAP-02 三层 501 条不裁切+旧断言反转；CAP-03 length 三样本+程序侧上限独立；RESUME-01 两形态；预算耗尽 7 次调用有界终态 failed）；相邻回归全绿：test_ontology_build(HTTP e2e)、output_codec（冻结键更新后过）、key_namespace/batch_*/budget_*/call_result/conflict_gate/exclusion_inheritance/finish_guard/late_write/merge_refs/progress_log/fact_identity/runner_isolation/semantic_units/task_purge/token_pilot_*/build_counterexamples/recovery_health/completion_contract；隔离：每场景独立 WIZ_WORKBENCH_ROOT+sto.reset_engine，模型入口函数替换，白盒不起服务；真实 data/、ontology/、keys/ 未动；未 git commit
- 下一步：T04：CompletionReport 产出接线消费 capacity 块（modelFactsTruncated>0 → partial/COVERAGE_INCOMPLETE 数据源就绪）；T05：交付门禁按 §17+§15 复算；v2 单响应 candidateStats 未接入 batch_execution 作业级记账，聚合接线留 T04/后续；协调者：串行提交（容量语义+测试+文档 §17 建议一 commit）；预算场景会输出一次预期 PipelineError traceback（runner.print_exc 生产行为非测试失败）；output_codec 测试冻结键断言本轮顺带同步，请核对归属
- 依据/文档：移植来源（只读）：auto_build 01026de（capacity/收口裁切）、de8f7cd（fencing 测试手法）；worktree/gpt_fix：tests/test_capacity_semantics.py；workbench/ontology_build/{llm,output_codec,pipeline,batch_contracts}.py；文档/接口文档/08-从物料自动构建本体接口.md §17；夹具 tests/counterexamples.py（T18a）

### gpt_fix-T03-解析器v3移植接入 · zcode · 已实施，待验收

时间：2026-09-24T17:41:09.647881+00:00；记录：`.collaboration/entries/000303-3658960b19af.json`

gpt_fix 工作树（分支 gpt_fix，未提交，归协调者收口）：T03 解析器 v3 自 auto_build 0a97fd5/dcd26ff 移植并接入。structwalk/json_parser 整文件移植（yaml/toml/__init__ 同款 hunk，PARSER_VERSION v2→v3 + ARRAY_EXPAND_MAX_MEMBERS/BYTES 常量）；新表 wb_build_scan_manifest（迁移 20260925_0007 接 0006）+ storage upsert/delete/get/scan_manifest_totals + purge_task 级联补行；pipeline 仅扫描接线 3 hunk（_scan_write 同事务 upsert、worker 异常/超时失败路径 delete），容量/候选/收尾段未碰。接口文档 08 新增 §16 + §10 变更记录行。tests/test_parse_v3.py 新增 133 断言全过；相邻回归 10 项全过。

- 决定：Fact 定位消费零 schema 变更：locator_json.path 已含 [n]，data_json 带 expanded/sampled 标记；auto_build 的 fact_set_digest/source_ordinal 属 fact-quality-v1 不移植；诊断 diag-v2/manifestTotals HTTP 消费端不在 gpt_fix 范围，仅落 storage 层函数备用；purge_task 级联补 wb_build_scan_manifest（auto_build 上游同样遗漏此行，已在其工作树外建议回补）；T01 test_completion_contract 迁移链头断言联动 0006→0007（按 03 派发文档 §1 迁移链冻结编号，非业务改动，已注释说明）；tests/test_ontology_build_struct_parsers.py v2 数组断言按 0a97fd5 同款适配为 v3（明细+摘要共存）
- 验证：tests/run.py --test tests/test_parse_v3.py：133/133 通过（PARSE-01 七成员定位+F03 反转、PARSE-02 边界语义、PARSE-03 48 层显式限制、采样/重复键/事实上限、manifest 往返越权、_scan_write 接线、迁移演练头=0007+downgrade 往返、稳定哈希+同文不同源不去重）；tests/run.py --test tests/test_ontology_build_struct_parsers.py：144/144；tests/run.py --test tests/test_ontology_build.py：通过（HTTP e2e）；struct_e2e/parser_wiring/task_purge/fact_identity/storage_contract/storage_transfer/counterexamples/completion_contract 全过
- 下一步：协调者串行收口：与 T02 的 pipeline.py（容量/候选段）/llm.py/output_codec.py 并行改动组合回归后统一提交；T04 收尾接线时注意 structwalk factLimit gaps 与容量块语义衔接；fact-quality-v1 诊断若后续移植，get_scan_manifest/scan_manifest_totals 已就位可直接接 manifestTotals
- 依据/文档：worktree/gpt_fix/workbench/ontology_build/parsers/{structwalk,json_parser,yaml_parser,toml_parser,__init__}.py；worktree/gpt_fix/workbench/migrations/versions/20260925_0007_scan_manifest.py；worktree/gpt_fix/tests/test_parse_v3.py；文档/接口文档/08-从物料自动构建本体接口.md §16

### gpt_fix-T01-完成报告契约 · zcode · 已实施，待验收

时间：2026-09-24T16:40:19.625025+00:00；记录：`.collaboration/entries/000302-13acb1b3de07.json`

T01 CompletionReport 契约落地（gpt_fix 工作树，未提交待协调者收口）：新增 workbench/ontology_build/completion.py 纯契约层（四概念正交/覆盖互斥账本/reportHash/unknown 构造器）；wb_build_runs 加列 completion_json（迁移 20260925_0006 接 0005，只加不改）；storage 增 set_run_completion/get_run_completion/run_completion_view；routes._v2_run_view 统一挂 run.completion（无报告/坏 JSON/校验不过一律读为 unknown，不回填不伪造）；前端 types.ts 增 CompletionReport 与 completionOf() 兜底（不改页面）；接口文档 08 新增 §15 并登记 §10 变更行。schema.py 加列是任务必需的最小越界（归属表未列、无他人认领）。

- 决定：completeness 派生：pending/blocked 归零且分母>0=complete，否则 partial；inScope=0 不得 complete；deliverable 派生：complete ∧ succeeded ∧ finalizer completed ∧ 无 blocker；unknown 恒 false；reviewable=执行终态；新增 blocker 码 EXECUTION_NOT_SUCCEEDED（§5.3 建议码外必需）；ISSUE 校验码与 blocker 码分两层；报告含 candidateRevision/decisionRevision 可选字段，为 T05 交付门禁绑定点；报告损坏按 unknown 读取不原样下发；持久化选 runs 表加列（前向兼容 server_default）；主树接口文档 08 误编辑已精确还原，改动在工作树副本
- 验证：tests/run.py --test tests/test_completion_contract.py 通过（14 用例：520=500+20 partial 不可交付/双计拒绝/父子双计拒绝/派生复算/哈希敏感键序稳定/unknown/存储往返/0005 形态升级演练/链头 0006）；回归通过：test_storage_contract、test_ontology_build_finish_guard、test_ontology_build(HTTP)、test_ontology_build_budget_http、test_ontology_build_task_purge；前端 vue-tsc typecheck 通过、eslint types.ts 无告警；未改 server.py/pipeline.py；未 commit；data/ keys/ ontology/ 未触碰
- 下一步：T02/T04 在管线收尾事务内调 build_report + set_run_completion 产出真实报告；T05 门禁绑定 reportHash/scope/plan/candidate/decision 五基线；T07 进度页消费 run.completion 与 stageRecords；协调者：README 变更记录、全量回归、提交（本任务文件全部在工作树未提交）
- 依据/文档：文档/v2/需求/2026_0924gpt优化/03_gpt_fix_G1基线矩阵与任务派发.md；文档/接口文档/08-从物料自动构建本体接口.md §15；workbench/ontology_build/completion.py；tests/test_completion_contract.py

### auto_test-worktree · zcode · 需求已交付

时间：2026-09-24T16:36:27.247725+00:00；记录：`.collaboration/entries/000301-129835e35840.json`

按用户指令新建 auto_test 分支与独立 worktree（仅创建环境，未开发）：分支 auto_test 自最新已提交 main@3f29a22，工作树 worktree/auto_test/，端口 18981（复用 fix_ui 清理后释放端口），WIZ_WORKBENCH_ROOT=工作树根。数据按 2026-09-22 规则全量随迁：transfer backup 生成 main 库 WAL 一致性快照落位 data/workbench.sqlite3 + keys/wb-root.key 副本(0600) + data/ontology-build-blobs。未安装依赖、未启动服务、未开发；scope 待用户下发。

- 决定：端口取 18981（18952/18961/18971/18991 已被 auto_build/gpt_fix/model_setting/mcp 登记）；数据布局沿用 gpt_fix 同基线模式：库快照+根密钥+blobs，未复制 ontology/ 播种树（如后续测试需要再按需只读复制）；verify 报「连接凭据数量/模型配置数量 0 vs 2」判定为旧文件 vault 迁移时代核对项的预期现象（新 worktree 无 ontology/ 树），非快照缺陷
- 验证：git worktree list 含 worktree/auto_test @3f29a22 [auto_test]；transfer verify：integrity ok、外键 ok、凭据解密探针 ok=4 fail=0、迁移 623 条；快照与源库关键表行数逐一相等：wb_credentials=4/wb_model_configs=2/wb_users=2/wb_assets=11/wb_releases=7；keys/wb-root.key 0600、data 目录 0700；登记提交 5db39f6 仅含 workbench-tasks/auto_test.json（路径限定，未卷入 Codex 375 暂存重命名）
- 下一步：等用户下达 auto_test 开发任务（开发时在工作树内装依赖、以 WIZ_WORKBENCH_PORT=18981 启动）；发现主树 workbench-tasks/gpt_fix.json 为未跟踪状态（他人登记未提交），未代提交
- 依据/文档：workbench-tasks/auto_test.json；提交 5db39f6；AGENTS.md 独立分支开发与串行集成 §2/§3

### rename-wiz-ontology-workspace · zcode · 已实施，待验收

时间：2026-09-24T15:02:35.048944+00:00；记录：`.collaboration/entries/000300-d5ef0ce29996.json`

按用户指令把项目名 wiz_kq_builder_v2 全部替换为 wiz_ontology_workspace：活动区 12 文件替换（README 标题、接口文档总纲适用行、AGENTS.md 标题+§2 路径（吸收此前未提交的改名 hunk）、frontend package.json/package-lock 包名、formatting.py 配置路径 ~/.config/wiz-ontology-workspace/（旧 format-llm.json 已复制到新目录、原件保留）、design.qa.yaml 标识 wiz-ontology-workbench、tests/deep_integrity_srv.sh 路径、workbench-tasks 三份登记路径），提交 57f8de7 并推送 origin/main=3f29a22；另 3f29a22 补登 model_setting 交互v4 状态（9-23 遗留 hunk）。AGENTS.md 标题保留「由 wiz_kq_builder_v2 改名」记录、legacyGraph 4 文件注释保留（wiz_kq_builder_view 是移植来源外部项目名，非本项目）。文档/v1 封存区 105 文件因处于 Codex 375 个暂存重命名待提交状态未替换，待其提交后另行 sed+commit；worktree/(494)/.collaboration/(14)/.idea iml 不在仓库内容范围。临时索引提交后已用 git reset 同步真实索引条目，消除 plain commit 回退风险。

- 决定：改名记录与外部来源注释（wiz_kq_builder_view）保留不改；model_setting.json 拆 hunk：改名行入改名 commit，状态登记单独 chore commit；~/.config 旧配置复制到新路径不删原件
- 验证：活动区 grep 零残留（仅存 AGENTS.md 改名记录与 legacyGraph 来源注释）；python3 tests/run.py quick 3/3 通过（formatting.py 有代码改动）；git log -1 --stat 确认 57f8de7 仅 11 文件、无 mode change；status 确认暂存区仅剩 Codex 375 renames；git ls-remote origin refs/heads/main=3f29a22
- 下一步：Codex 提交 375 个暂存重命名后，对 文档/v1 做 sed 替换 + 单独 commit + 推送；worktree/ 内 494 处旧名随各任务分支合并时自然消亡，未单独处理
- 依据/文档：57f8de7；3f29a22；workbench/formatting.py:17

### repo-push-github · zcode · 已实施，待验收

时间：2026-09-24T14:46:44.604904+00:00；记录：`.collaboration/entries/000299-68cf75f83b4b.json`

添加远程 origin（git@github.com:gukp1993/wiz_ontology_workspace.git，SSH 认证），已推送 main 至 7eed787 并建立跟踪；远程此前为空仓库，推送后仅含 main。本地未提交改动（文档/v1 迁移的重命名暂存、AGENTS.md、session_context.md、workbench-tasks/*.json 修改）未包含在推送中。本地其余分支 auto_build/fill_by_llm/mcp/model_setting（worktree 任务分支）未推送。

- 验证：git ls-remote origin 确认 refs/heads/main=7eed7879664d8178b9258baf041eba11bc1b4add
- 下一步：用户决定是否提交当前工作区未提交改动；用户决定是否推送其余 worktree 任务分支

# Codex / zcode 共享上下文

上下文版本：`e0dd7e344d21c522`

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

### assist-fill 正式实现外部标准验收循环：R2 通过 · zcode · 已验证

时间：2026-09-22T03:58:23.102916+00:00；记录：`.collaboration/entries/000173-086f1d561ede.json`

按用户指令开子 agent 执行《独立验收执行指令_正式实现_20260922.md》全量验收。R1 判定不通过（唯一 P1=DEF-EXT-1：O3 链接编辑器采纳含 label 建议后 contextTitle 读响应式 draft.label 致 binding 重建、面板整卡重置；其余 V1–V8 全过：后端 14+57+10、前端 46 套件、11 场景 revision 零保存取证、凭据零出网、真实模型 minimax 抽查、双视口、账号隔离）。修复 d7d4c20（标题在编辑器打开时冻结为 assistTitle，computed 改读冻结值；补 2 组回归断言）。R2 复验通过：18/18 浏览器断言闭环（横幅/不重置/撤销可用/intent 保留/标题冻结/零保存/保存前进）+ 单测 9/9 + 前端 46/46 + typecheck 0 + build 过 + V5.3/V6.2 抽查过，console 正式窗口 0 error/0 异常。交付 HEAD 09c3c61（含验收记录落款）。停在待用户授权集成。

- 决定：DEF-EXT-1 修法=辅助标题在编辑器打开时冻结（editor.assistTitle 普通字符串），computed 不读响应式草稿——与 ActionLibrary 取行记录名同源思路；不采用收缩场景或去响应式开关等更大改动；外部验收全程只读仓库（.collaboration record 除外），修复由主 agent 执行后再派全新子 agent 复验；真实模型生成在限额内使用（R1 4 次）
- 验证：R1：V1–V8 全量执行（后端三套件复跑、前端 46 套件、11 场景浏览器链路含 revision 零保存取证、凭据零出网、真实模型抽查、双视口、账号隔离），唯一 P1=DEF-EXT-1；修复后：assist_object_workspace 9/9（含 2 组新回归断言）、前端 46/46、typecheck 0、build 过、18951 重启加载；R2：变更核对仅 d7d4c20 两文件；18/18 浏览器断言闭环；V5.3/V6.2 抽查过；console 0 error/0 异常；证据 /tmp/kg_ext_accept/ 与 /tmp/kg_ext_accept_r2/
- 下一步：外部验收通过，停在待用户授权集成；合并 main 与主工作台更新等待用户明确指令；登记待办不阻断：DEF-04 检查页签字段定位（P2）、登录过渡态 TypeError（疑存量）
- 依据/文档：交付 HEAD 09c3c61（分支 codex/assist-fill-production）；修复提交 d7d4c20；指令：文档/需求/20260920_本体与项目辅助填写/独立验收执行指令_正式实现_20260922.md；证据 /tmp/kg_ext_accept/report.md 与 /tmp/kg_ext_accept_r2/；环境 http://127.0.0.1:18951

### assist-fill-production 第二轮复验（DEF-EXT-1 闭环） · zcode · 已验证

时间：2026-09-22T03:55:54.563356+00:00；记录：`.collaboration/entries/000172-25d11081518c.json`

第二轮独立复验通过。变更核对：84eb6be..HEAD 仅 d7d4c20 一个提交、仅 ObjectWorkspace.vue+assist_object_workspace.test.mjs 两文件，与修复声明一致（assistTitle 打开时冻结、computed 改读冻结值）。DEF-EXT-1 浏览器直证 18/18 断言通过（assist_dev@18951，全程 CDP Input.* 真实事件，模型桩 18913）：采纳含 label 建议后「已填入表单，尚未保存」出现、面板保持结果态、撤销可用、intent 保留、标题稳定、label 写入表单；采纳零保存（revision 三测不变）；手改后撤销失效、标题不变；表单保存 revision 前进。回归抽查与上轮通过项抽查全过。console 探针自证后清零。收尾已恢复种子基线，仓库源码区零写入。

- 决定：DEF-EXT-1 判定已闭环：P1 修复有效且无回归，总判定通过；验收写库仅限 assist_dev 合成验收本体（复验链路内保存），结束已恢复种子
- 验证：git log/diff 84eb6be..HEAD：1 提交 2 文件（d7d4c20）；单测 assist_object_workspace.test.mjs 9/9（含新增 2 组断言）；前端 46/46 套件、vue-tsc typecheck、vite build 全过；浏览器断言 18/18：A1-A7 采纳后 banner/结果态/撤销/intent/标题/label 写入/零保存；B1-B4 手改后撤销失效/标题冻结/失效提示/值精确；C1 保存 revision 前进 r-b2ce036ff00e→r-2d729da4cdf1；V5.3 撤销保护抽查过（B1）；V6.2 账号隔离抽查过（admin GET assist_dev 本体 404、列表不含辅助验收本体）；console 探针自证后：console/exception 0、仅 2 条 network 404（V6.2 负例预期，URL 已核对）
- 下一步：待用户明确授权集成/合并；种子基线中部分 description 为上轮验收留存内容（AI建议-业务定义-*），不影响功能
- 依据/文档：/tmp/kg_ext_accept_r2/results/r2_defext1.json；/tmp/kg_ext_accept_r2/shots/；d7d4c20

### assist-fill-production 独立验收（外部 harness） · zcode · 受阻

时间：2026-09-22T03:09:47.952324+00:00；记录：`.collaboration/entries/000171-3612d2859567.json`

独立验收完成，总判定不通过（1 项 P1）。V1 范围/三方登记（总数 99=95+4 认证行，准确）/计划落款一致；V2 后端 14+57+10 全过+契约抽查（409/400/404/默认工作区/只读不变式）；V3 前端 46/46+typecheck+build 过；V4 十二场景真实点击链（10 过+S4 不通过+S10 按指令判过带记录），S11 targetId=cluster.l_c2d 实测、S12 凭据 8 词 0 命中；V5 全 7 项过；V6 全过；V7 真实模型 4 次限额内（2 截断 502 合规+2 成功，meta=MiniMax-M3）；V8 双视口过；console 全零（探针自证）。报告 /tmp/kg_ext_accept/report.md

- 决定：DEF-EXT-1（P1 新发现）：O3 链接编辑器采纳含 label 建议后，assistBinding computed 因 contextTitle 读 draft.label 而重建 binding，面板被当切换目标整卡重置——横幅不出/卡片清空/撤销不可用/intent 清空；草稿写入正确且无自动保存。两次独立复现；修复点 ObjectWorkspace.vue L266+AssistPanel binding watch；P2×2：项目区面板标题用稳定 id 与本体区显示名口径不一；桩对 P2/P4 valueField/flow 卡与非空 issues 不产出（200-empty 合规，物料缺口非缺陷）；环境变更：assist_dev 默认桩按指令删除后重建（字段一致，Key 为占位值，桩不校验）；admin 新增 2 合成对象；assist_dev 本体/项目草稿已恢复种子基线
- 验证：git log fe1179f..HEAD 无代码变更；diff --stat 039a4f0..HEAD 58 文件符合声明；test_assist_context 14 / test_assist_schema 57 / test_assist_api 10（端口空闲实测）；前端 46/46+typecheck 0 错+build 过；12 场景 revision 取证：采纳后≥3s 零变化、保存后前进；截图 30+ 与全量日志在 /tmp/kg_ext_accept/
- 下一步：开发方修复 DEF-EXT-1 后再次送验（复验面：O3 统一链路+V5.3/V5.4+assist_object_workspace 套件）；其余结论可沿用，无需全量重跑
- 依据/文档：文档/需求/20260920_本体与项目辅助填写/独立验收执行指令_正式实现_20260922.md；/tmp/kg_ext_accept/report.md；被验 HEAD 84eb6be（代码交付 fe1179f）

### 交付 assist-fill 正式实现独立验收执行指令 · zcode · 需求已交付

时间：2026-09-22T00:44:11.869478+00:00；记录：`.collaboration/entries/000170-e2017238069c.json`

按用户指令交付可直接交给其他 harness 的独立验收执行指令（纯文档）：文档/需求/20260920_本体与项目辅助填写/独立验收执行指令_正式实现_20260922.md（提交 3e3ae89）。指令自包含：被验代码 SHA fe1179f（其后仅文档/协作提交，HEAD 可前进）、隔离环境 18951 双账号（assist_dev=合成数据+模型桩 18913；admin=真实模型 minimax 限次≤4 次生成）、V1–V8 必做验证（交付完整性与三方文档一致/后端三套件复跑/前端 46 套件+typecheck+build/11 场景浏览器链路含 revision 零保存取证/生命周期与保护/安全红线凭据零出网/真实模型抽查/双视口）、只读边界与禁止项、既有登记项、三态判定与交付格式、通过停在待用户授权集成。

- 决定：代码交付锚定 fe1179f（最后一处代码变更），其后文档/协作提交不算被验内容变更；验收方只读仓库产物进 /tmp；真实模型生成全程限 ≤4 次并要求如实区分桩与真 LLM 验证；P4 场景允许「assist 链路与零保存断言通过、表单保存被既有业务校验正确拦截」判通过；验收方可在 18951 自建合成数据补齐场景；test_assist_api 端口 18911/18912 被占则报告不强杀
- 验证：指令中引用的 SHA/环境/数据 id 均按当前实况核对：HEAD d99178a、18951 HTTP 200、桩 18913 在线、种子数据 id 与账号齐全；指令提交前 git status 核对仅新增该文档，无他人内容带入
- 下一步：用户将指令全文交给其他 harness 执行独立验收；验收通过停在待用户授权集成，合并需用户明确指令
- 依据/文档：文档/需求/20260920_本体与项目辅助填写/独立验收执行指令_正式实现_20260922.md（提交 3e3ae89）；被验分支 codex/assist-fill-production 代码交付 fe1179f；HEAD d99178a；环境 http://127.0.0.1:18951（admin/admin；assist_dev/AssistDev#2026）

### assist-fill-production 子代理验收循环：三轮后通过 · zcode · 已验证

时间：2026-09-21T19:08:17.878347+00:00；记录：`.collaboration/entries/000169-17e91f19a46f.json`

按用户指令开独立子agent验收 assist-fill-production（子agent为未参与开发的全新上下文，只读仓库+真实点击+全量测试）。R1 判定不通过：3 项 P1（DEF-01 本体区上下文固定读默认工作区/DEF-02 新建动作辅助404/DEF-03 替换真实旧值默认勾选违背需求§3.5）。主 agent 修复（fe1179f：协议补 ontologyId 端到端、新建动作 targetId 传空、默认勾选门控改「ready 且宿主旧值为空」，9 套件适配+各增反向断言）。R2 判定不通过：唯一 P1=ontologyId 未登记接口文档。补登记（53ef04a：04§5.1+README）。R3 聚焦复验判定通过（4/4 项：变更范围/文档-实现三方一致/套件复跑 14+10/工作区零写入）。交付 HEAD de60a74。环境 18951 保留。

- 决定：验收循环机制：验收子agent只读仓库零写入不出修复清单，修复由主agent执行后再派全新子agent复验，直至通过；DEF-01 修法=协议补 ontologyId 可选字段（省略回落默认 storage）而非收缩为仅默认工作区；跨账号/不可见工作区一律 404；DEF-03 门控判据=建议 fieldKeys 对应宿主旧值为空才默认勾选；测试同步语义并各套件至少增 1 条反向断言
- 验证：R1：后端 14+57+10 全过、前端 46 套件、9/11 场景浏览器全链路（采纳后≥2.5s revision 冻结→保存前进）、真实模型 minimax 抽查通过、XSS/账号隔离/双视口过；发现 3 P1；修复后：46/46 套件、assist_context 14/14、assist_api 10/10、typecheck 0、build 过、真实模型端到端冒烟（admin/minimax ready 建议）；R2：三项 P1 三层直证全过、全量回归无回归、唯一 P1=文档未登记；R3：4/4 通过（fe1179f..HEAD 仅 2 文件纯追加、文档-实现三方一致、context 14+api 10 复跑、porcelain=0）；证据 /tmp/kg_accept_final/ 与 kg_accept_r2/ r3/
- 下一步：验收通过停在待用户授权集成；合并 main、更新主工作台均等待用户明确指令；登记待办（不阻断）：DEF-04 检查页签字段定位接线（P2）；登录过渡态一次性 TypeError（疑存量，建议另立治理项）
- 依据/文档：交付 HEAD de60a74（分支 codex/assist-fill-production）；验收轮次记录 c9b34de；证据 /tmp/kg_accept_final/ /tmp/kg_accept_r2/ /tmp/kg_accept_r3/；环境 http://127.0.0.1:18951（admin/admin 真实模型；assist_dev/AssistDev#2026 模型桩 18913）

### assist-fill-production · zcode · 已验证

时间：2026-09-21T19:06:44.918388+00:00；记录：`.collaboration/entries/000168-c67aec98bfd7.json`

第三轮独立复验通过：上轮唯一 P1（assist-context 新增请求字段 ontologyId 未登记接口文档）由 53ef04a 闭环。fe1179f..HEAD 仅该 1 提交，改动仅 04-编排与LLM接口.md 与 README.md 各 1 行、纯追加（2 insertions/0 deletions）。三方一致核对：04 §5.1 请求表 ontologyId 行（可选/省略空=默认 storage/不可见或属他人 404 NOT_FOUND/签入 contextToken 并参与一致性校验）＝README 变更记录行（含 409 CONTEXT_STALE 口径、provide('ontology-id') 透传、指向 04 §5.1）＝实现（assist_service.py:96-101 required=False 省略→storage 并透传 build_context、:134 generate 从 token 取 ontologyId；assist_context.py:648 令牌签入、:669-672 check_generate 比较不等则 ContextStale；404 经 workspaces.describe 按 owner 过滤 asset None→WorkspaceNotFound→404）。抽查上轮通过项确认纯文档提交无行为影响：tests/test_assist_context.py 14/14、tests/test_assist_api.py 10/10（18912 空闲自起自清，测试后端口释放、EXIT 均 0）。HEAD=53ef04a，git status --porcelain=0，验收全程未修改文件。

- 验证：git log fe1179f..HEAD 仅 53ef04a；diff --stat 仅 04/README 各 +1 行纯追加；04 §5.1/README/assist_service.py/assist_context.py 四点语义比对一致；python3 tests/test_assist_context.py 14/14 EXIT=0；python3 tests/test_assist_api.py 10/10 EXIT=0，18912 自清释放；git status --porcelain | wc -l = 0
- 下一步：验收通过，状态为待用户授权集成；是否合并 main 由用户明确指令决定
- 依据/文档：文档/接口文档/04-编排与LLM接口.md §5.1；文档/接口文档/README.md 变更记录 2026-09-22 行；workbench/assist_service.py；workbench/assist_context.py；/tmp/kg_accept_r3/

### assist-fill-production：建议默认勾选门控测试适配（3 套件） · zcode · 已实施，待验收

时间：2026-09-21T18:31:20.814006+00:00；记录：`.collaboration/entries/000167-fb306b4721ea.json`

按新门控（ready 且宿主旧值非空默认不勾，需求 §3.5）适配 3 个套件并提交 1fb0bba：assist_property_manager 58/58、assist_property_sources 12/12、assist_workflow 11/11，退出码均 0；frontend typecheck 退出码 0。替换型采纳（④⑤⑦/③⑧⑩/⑥ database）保留非空初值并显式 setChecked；每套件新增旧值非空默认 checked=false 断言（property_sources 另加空值默认勾选反向断言）。workflow ⑦⑨ 失败非门控所致，系工作树未提交的 ActionLibrary DEF-02 改动（新建动作 targetId 传空串），已按新约定修正断言。仅改 3 个测试文件，未动产品代码；依赖同工作树未提交产品改动，随产品提交一并集成。

- 决定：替换已有真实旧值的用例保留非空初值并显式 panel.setChecked 后采纳，不改夹具初值；assist_workflow ⑦⑨ 按 DEF-02 新约定断言新建动作 targetId 为空串（该产品改动在工作树未提交，非本任务修改）
- 验证：node --import ./tests/ts_hooks.mjs tests/assist_property_manager.test.mjs → 58/58 退出 0；node --import ./tests/ts_hooks.mjs tests/assist_property_sources.test.mjs → 12/12 退出 0；node --import ./tests/ts_hooks.mjs tests/assist_workflow.test.mjs → 11/11 退出 0；cd frontend && npm run typecheck → 退出 0
- 下一步：待产品改动（门控+DEF-02+ontologyId）由负责方提交后，本测试提交 1fb0bba 与之配套进入验收
- 依据/文档：tests/assist_property_manager.test.mjs；tests/assist_property_sources.test.mjs；tests/assist_workflow.test.mjs；frontend/src/assist/useAssistPanel.ts

### assist-fill-production 默认勾选门控测试适配（worktree/assist-fill-production） · zcode · 已实施，待验收

时间：2026-09-21T18:28:42.606843+00:00；记录：`.collaboration/entries/000166-515ef10cae66.json`

按新门控语义（ready 且宿主旧值为空才默认勾选）适配 3 个辅助填写套件：assist_action_bindings/assist_identity_link/assist_object_workspace。替换已有值的用例保留非空初值并显式 setChecked 后采纳；每套件新增「旧值非空→ready 默认 checked=false」断言，并在 identity_link ②e、action_bindings ⑥d2 补正向断言（旧值为空→默认勾选）。未改任何产品代码。

- 验证：node --import ./tests/ts_hooks.mjs tests/assist_action_bindings.test.mjs → 57/57，退出码 0；node --import ./tests/ts_hooks.mjs tests/assist_identity_link.test.mjs → 16/16，退出码 0；node --import ./tests/ts_hooks.mjs tests/assist_object_workspace.test.mjs → 9/9，退出码 0；cd frontend && npm run typecheck → 退出码 0；git status 核对：本轮仅改动上述 3 个测试文件，产品侧改动（useAssistPanel.ts 等）为此前已有未提交内容
- 下一步：待 Codex 独立验收本轮测试适配与产品门控实现
- 依据/文档：tests/assist_action_bindings.test.mjs；tests/assist_identity_link.test.mjs；tests/assist_object_workspace.test.mjs；frontend/src/assist/useAssistPanel.ts

### assist-fill-production：本体与项目辅助填写正式实现（T0～T12 全量交付） · zcode · 已实施，待验收

时间：2026-09-21T16:02:07.039015+00:00；记录：`.collaboration/entries/000165-e0b75b18902f.json`

按开发计划 T0～T12 完成正式实现并自测，交付 SHA 8a27163（分支 codex/assist-fill-production，base 039a4f0，worktree/assist-fill-production）。后端新增 assist_fields/assist_context/assist_schema/assist_service/assist_routes 五模块＋server 白名单 2 路由（只读语义、不持写锁、trace 不出服务端）；前端新增 assist/（types/状态机/面板＋6 表单 binding 适配器），11 场景 O1～O5、P1～P6 全部接入：采纳只改本地草稿零自动保存、手改失效、一次性撤销、CONTEXT_STALE 重取、凭据永不出网。接口文档 04§5/05/README 已登记。环境：http://127.0.0.1:18951（隔离数据根 .runtime/assist-data，账号 assist_dev，本地模型桩 18912 已注册默认）。停在被 Codex 独立验收，环境保留。

- 决定：协议 T0 冻结：contextToken HMAC 签名 TTL600s 绑定用户/目标/权威指纹/draft 摘要；错误码 409 CONTEXT_STALE/422 MODEL_NOT_CONFIGURED/502 MODEL_BAD_RESPONSE/504 MODEL_TIMEOUT；上限 intent≤4000/answers≤8×2000/questions≤3/suggestions≤12/issues≤30/draft≤200KB；模型输出分层校验：整体结构违规 502；单条违规丢弃留痕；ref 幻觉/复合组行级越界/formatting 历史样式转 blocked 可见禁选；dataType+obsType 原子组；ifQuestion 未答转 pending；等值丢弃；id 服务端重编；模型自带 state 不采信；浏览器验收两轮 6+1 项 P1 全部修复复验：identityLinkBindings 补 projectId；未配置目标容忍（定义在引用版本即放行标未配置，幻觉目标仍 404）；LinkMappings targetId 对齐「对象类型.关系id」；种子数据形状错误（relations 归属/identity 结构/动作关联）不属产品缺陷已改种子；默认模型走 llm_providers.default_provider 按账号解析；自动化验证全部用本地模型桩（合成建议），真实模型联调未做须用户授权配置，交付报告如实区分
- 验证：后端：test_assist_context 14/14（越权/令牌/指纹/脱敏/截断/未配置容忍/幻觉仍404/只读不变式）、test_assist_schema 57/57、test_assist_api 10/10（本地模型桩走真实网络：401/400/404/409/422/502/504/200empty＋采纳前后草稿零变化）；python3 tests/run.py all 51/52→唯一失败为环境缺 pypdf（物料构建模块，安装后 parsers 95/95），与本功能无关；前端：46/46 套件全绿（新增 assist 九套 274 项断言；mapping_forms 修复基线既有失败，覆盖点保留 147→133 断言，死链路展示断言随旧表单移除并在文件头登记）；vue-tsc 0 错误；npm run build 通过；浏览器（隔离实例 18951 真实点击两轮，CDP Input.* 无 el.click，证据 /tmp/kg_t11_browser/agent{,2}/）：15 条矩阵＋6+1 项 P1 修复复验全过；revision 取证：各场景采纳后等 ≥2.5s revision 不变、表单保存才前进（O1/P1/P2/P4/P6 取证）；凭据零出网断言过；XSS 纯文本；三档视口面板可达；第二轮正式窗口 console error/warning/未捕获全 0；环境隔离：全部命令 cwd=worktree；端口 18951（禁 18765/8765）；独立数据根合成数据可丢弃；测试账号 assist_dev 仅存于隔离根；未连真实 MySQL/Redis/LLM；未合并 main、未推送、未重启主工作台
- 下一步：交 Codex 独立验收：验收环境可直接复用 http://127.0.0.1:18951（assist_dev/AssistDev#2026，模型桩在跑）；重点复核 11 场景采纳零自动保存（revision 冻结）、CONTEXT_STALE 链路、凭据不出网、未配置目标容忍边界；真实模型联调需用户授权配置提供方后另行执行；集成合并等待用户明确授权，本环境保留供验收
- 依据/文档：交付 SHA 8a27163（docs 落款）＋ f355ca0/60c65c0（功能与测试对齐）；分支 codex/assist-fill-production；文档/需求/20260920_本体与项目辅助填写/开发计划_正式实现.md §8/§9（环境登记/字段映射/交付记录）；文档/接口文档/04-编排与LLM接口.md §5、05 速查表 53a/53b、README 变更记录；浏览器证据：/tmp/kg_t11_browser/agent/ 与 agent2/；共享任务登记 .git/workbench-tasks/assist-fill-production.json

### assist-fill-production T11 浏览器真实点击验收（隔离实例18951） · zcode · 已验证

时间：2026-09-21T14:55:15.817114+00:00；记录：`.collaboration/entries/000164-599de2450040.json`

T11完成：O1/O2/O2b/O3/O4/O5/P2/S14/S15/三视口通过；P1/P5辅助上下文400(projectId缺失,identityLinkBindings未传)、P3 404(cluster.biz_id目标不存在)、P5既有映射不显示、储能设备登记实例(DEV-001)UI显示未配置、S13缺信息无问题卡(后端userIntent vs 桩读intent键名错位)；P6入口不可达(引用1.0.0无动作关联)。采纳均零保存(revision冻结)、保存才前进、无凭据泄漏、XSS安全。证据 /tmp/kg_t11_browser/agent/

- 验证：revision链:r-02c2→r-4c36(O1保存)→r-9710(O4保存)；project r-c356→r-0862(P2保存)；console:6 error均为assist-context 400/404(与P1/P3断裂对应),0 warning 0 uncaught；视口1440/1280/768面板可达按钮可见
- 下一步：修复identityLinkBindings/linkAssistBinding缺projectId；排查未配置属性(biz_id)的assist目标404与既有映射/登记实例不显示；裁定userIntent(intent)键名错位归属(assist_schema vs stub_llm)；P6需升级项目引用或另建含动作关联的引用版本后补验
- 依据/文档：/tmp/kg_t11_browser/agent/REPORT_SUMMARY.json；frontend/src/assist/identityLinkBindings.ts；workbench/assist_schema.py:147；/tmp/kg_t11_browser/stub_llm.py:37

### assist-fill-production T9 收尾：mapping_forms 回归修复至全绿 · zcode · 已实施，待验收

时间：2026-09-21T08:58:52.154589+00:00；记录：`.collaboration/entries/000163-80b4e1ea8e72.json`

在已登记 worktree（codex/assist-fill-production）仅修改 tests/mapping_forms.test.mjs 一个文件，把 node --import ./tests/ts_hooks.mjs tests/mapping_forms.test.mjs 修到退出 0（此前 main 基线即失败：PropertySources「未配置先进 none 态」改版后旧断言未同步，且前一 agent 的部分修复留下 openEditor('legacy') 空指针与已删 rule 变量 ReferenceError）。修复口径：保留前一段已修的 none 态/freshDbDraft/redis 保存失败保草稿/LinkMappings 断言；遗留学段按当前存活行为重写——旧 computed 家族（规则绑定/inlineSql/calcFunction）已无新建与编辑入口（switchKind 无 computed 分支、模板只剩只读分支），改用「已存绑定夹具 + openEditor/selectImplementation/setRuleInput/saveDraft（存活 api）」驱动同一套 issuesOf 校验与 commitProperty 回写；scanSqlParams/effectiveParams 纯函数直测锁定；QueryRuleManager（V1→V4 迁移保存/详情/采样复用/计算函数全链）与 LinkMappings 原样保留。死链路删除并注释说明：「实现输出」编辑表单、V3/V4 声明输入页面展示行、内联 SQL 新建默认 mode=inline 视图模型与页面参数行、A9 方式切换保草稿。

- 决定：规则/内联/计算函数各段以已存 computed 绑定夹具为起点（propertyView 解码→saveDraft 校验→commitProperty 回写），替代旧手工 mode='rule' 草稿；只改断言不改组件源码；b.properties.legacy（图外属性）改为复用图内属性 note/power/history/tsnote 等夹具，消除 valueShapeOf 空指针；恢复 socRule/reusableScadaRule 夹具供 QueryRuleManager 段引用（前一 agent 误删导致 ReferenceError）；断言计数 147→133：差额为死链路页面展示断言（约 14 处）+ 新增行内注释；存活覆盖点（声明式输入初始化、值类型相容、未绑定参数拦截、取消无写入、稳定标识公式、循环依赖等）全部保留
- 验证：node --import ./tests/ts_hooks.mjs tests/mapping_forms.test.mjs → 退出 0（通过：四类来源+链接缺连接+遗留规则绑定+内联 SQL+登记对象+计算函数全链）；回归冒烟全过：assist_property_sources 12/12、assist_identity_link 16/16、assist_object_workspace 9/9、assist_property_manager 54/54；git diff 确认本轮仅 tests/mapping_forms.test.mjs 一个文件变化（组件改动为并行 T7/T8/T9 既有工作区内容）
- 下一步：待 Codex 独立验收；未覆盖项已在文件头注释与交付报告登记：V3/V4 声明输入/runtime 参数的页面展示行、内联 SQL 页面参数绑定行、A9 方式切换保草稿（均随旧表单移除，无存活载体）
- 依据/文档：tests/mapping_forms.test.mjs；frontend/src/project/PropertySources.vue；frontend/src/project/QueryRuleManager.vue；frontend/src/project/inlineSql.ts

### assist-fill-production T7 业务规则与动作定义接入辅助填写（O4/O5） · zcode · 已实施，待验收

时间：2026-09-21T08:11:20.173636+00:00；记录：`.collaboration/entries/000162-348c9a308b36.json`

在已登记 worktree（codex/assist-fill-production）交付 T7 四文件：新建 frontend/src/assist/workflowBindings.ts（ruleAssistBinding/actionAssistBinding 工厂：白名单快照 rule={name,description,content}、action={name,description,effect}，与 workbench/assist_fields.py 一致；undefined 不覆盖、白名单外忽略、JSON 克隆快照、restore 就地恢复），修改 BusinessRuleLibrary.vue 与 ActionLibrary.vue（编辑表单 EditorHead 下加「✦ 辅助填写」入口行、AssistPanel 挂表单尾部、字段改动经 onField/setField→assistTouched→模板 ref notifyDraftChanged、assistBinding computed 按 mode/draft/editId 构造、closeEditor 收起+watch 兜底、新增 lib-assist-row/lib-assist-panel 局部样式），新建 tests/assist_workflow.test.mjs（SFC 编译模式，11 项全过）。目标 id 口径：规则新建 targetId=''（未入库）、编辑用规则 id；动作 openNew 预生成 id 直接作 targetId。采纳只改本地草稿（formSave/changed emit 间谍零调用断言），规则历史 output 只读区不在白名单、辅助不触及。

- 决定：规则新建 targetId 传空串（O4 要求），与组件内已预生成 editId 无关；动作按 O5 要求用 openNew 预生成 id 作 targetId；contextTitle 取自 rows 中既有记录名（新建用固定标题），不依赖 draft.name，避免每次击键重建 binding 触发面板重开；AssistPanel 挂编辑表单尾部（message 之前），入口行贴 EditorHead，与 T5 ObjectWorkspace 同构；样式类 lib-assist-row/lib-assist-panel 两组件各自 scoped
- 验证：node --import ./tests/ts_hooks.mjs tests/assist_workflow.test.mjs → 11/11 通过，退出 0；cd frontend && npm run typecheck → 退出 0；回归冒烟：assist_object_workspace 9/9、assist_property_manager 54/54、assist_panel 51/51、dependency_guard 22/22、ont_list_unified 4/4、editor_head_consistency 19 项全过；未 git 提交（按任务边界），改动仅限 4 个授权文件；SSR 不覆盖 onMounted 与模板 ref 实链路
- 下一步：待 Codex 独立验收；浏览器验收时确认面板生成/采纳/手改过期交互；与 T8+（项目区各表单）无文件交集；集成时与 T5/T6 同分支组合验证
- 依据/文档：frontend/src/assist/workflowBindings.ts；frontend/src/ontology/BusinessRuleLibrary.vue；frontend/src/ontology/ActionLibrary.vue；tests/assist_workflow.test.mjs；workbench/assist_fields.py

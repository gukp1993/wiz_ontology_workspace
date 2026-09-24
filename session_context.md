# Codex / zcode 共享上下文

上下文版本：`a91f2b80fbfc59e0`

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

### mcp · zcode · 已实施，待验收

时间：2026-09-24T01:18:44.496394+00:00；记录：`.collaboration/entries/000284-e16fe6eeee59.json`

【侧栏常驻智能问数菜单】按用户指令为 18991 的智能问数页补侧栏入口（原仅 #sq 深链不可发现）：sq 保留全局页属性，新增 onSettingsView 使设置中心专用侧栏仅限两个设置页，sq 落回业务侧栏；业务 nav 末尾追加常驻「智能问数」按钮（本体/项目两空间可见，active 高亮），面包屑「智能问数 · 语义查询」，状态条独立文案。mcp 分支提交 59e14b5+0234a73，浏览器实测过。

- 决定：sq 用业务侧栏而非设置侧栏（非设置语义）；菜单按钮在 nav 内无条件渲染，不随空间菜单显隐
- 验证：浏览器实测 18991：业务侧栏「智能问数」渲染、active 跟随、点击跳转 #sq 挂载正常；typecheck/build 过；过程失误已纠正：一次 shell cwd 漂移致编辑误落主仓 App.vue，已 checkout 还原并重建主仓 dist（复现 index-CcrXiB9h.js 原哈希，主仓零残留）；顺手修 test_sq_service 溯源断言 camelCase 跟随（0234a73）
- 下一步：智能问数仍停在待 Codex 独立验收（分支 mcp）；本轮菜单改动纳入同分支一并验收
- 依据/文档：分支 mcp 59e14b5/0234a73；worktree/mcp/frontend/src/App.vue

### hide-autofill-entry-main · zcode · 已实施，待验收

时间：2026-09-24T00:57:11.426582+00:00；记录：`.collaboration/entries/000282-edff19203e70.json`

【主干直接实施】按用户明确指令「主干的自动填写功能入口先给我隐藏」在 main 工作树直接实施（用户指名主干，作为 worktree 流程的例外记录在案）：新增 useAssistPanel.ASSIST_ENTRY_ENABLED=false 总开关，8 个宿主 9 处「✦ 自动填写」入口按钮 v-if 跟随开关（对象/链接编辑 O1/O3、属性与共享属性 O2、业务规则 O4、动作定义 O5、实例识别 P1、链接映射 P5、属性取值来源 P2-P4 经 assistAvailable、动作接口映射 P6）。后端 /api/assist-* 与面板/引擎实现全部保留，恢复入口改回 true 一处即可。提交 d4aebd1（main）。

- 决定：用户指名主干直接改，作为独立分支开发约定的用户明确例外执行，不建 worktree；开关放 assist 门面 useAssistPanel.ts（各宿主既有 import 点），入口隐藏仅前端渲染层，后端接口不动；设置页「自动填写」模型用途配置不在本次范围（该页归 model_setting 工作树在改）；assist 四个 SSR 测试的入口存在类断言改为跟随开关分支，面板链路仍经 toggleAssist/openAssist 程序化驱动，引擎覆盖不丢
- 验证：vue-tsc 0 错误；vite build 通过（index-CcrXiB9h.js）；node assist_workflow 12/12、assist_object_workspace 14/14、assist_property_manager 74/74、assist_property_sources 18/18、assist_panel 25/25；python3 tests/run.py all 84/85：唯一失败 test_autofill_integration=18941 端口被 fill_by_llm 服务占用的已知环境冲突，与本次无关（失败输出：端口 18941 已被占用）；18765 主工作台服务按请求读 dist：curl 首页引用 assets/index-CcrXiB9h.js 与新构建一致，入口隐藏已生效，服务未重启（PID 81869 未动）
- 下一步：恢复入口：frontend/src/assist/useAssistPanel.ts 把 ASSIST_ENTRY_ENABLED 改回 true 并重新 build；如需隐藏设置页自动填写模型用途配置，建议待 model_setting 工作树合入后统一处理；未做浏览器实机走查（SSR 断言+dist 替换已覆盖），如需可另行验证
- 依据/文档：commit d4aebd1；frontend/src/assist/useAssistPanel.ts；tests/assist_object_workspace.test.mjs 等 4 个测试

### fix_ui · zcode · 已验证

时间：2026-09-23T14:22:46.349991+00:00；记录：`.collaboration/entries/000280-ca839a8f38af.json`

【集成完成+清理完成】用户明确指令「合并到主干，worktree删除」。集成流程：fix_ui 工作树内 git merge main（无冲突，main 侧仅协作/登记提交，文件零重叠）→ 合并提交 a266601 上组合验证（前端 typecheck/build 过、tests/run.py all 84/85 唯一失败=18941 被 fill_by_llm 占用的环境冲突）→ main ff-only 快进至 a266601。清理：停止 18981 实例（PID 37192，端口已释放）、git worktree remove --force worktree/fix_ui（含真实数据副本/根密钥副本/ontology 播种树/node_modules/dist/.runtime，用户指令授权删除）、删除已合并分支 fix_ui（was a266601）。main 现含 fix_ui 全部十四项 UI 整理。

- 决定：用户直接下达合并指令即为集成授权；合并前核对 main 侧提交均为此前各轮协作登记、与 fix_ui 改动文件零重叠；main 工作树内他人未提交改动（model_setting.json、两份未跟踪文档）不在本次范围，原样保留
- 验证：合并提交 a266601 组合验证：typecheck/build 过、tests/run.py all 84/85（唯一失败=18941 环境冲突）；main ff-only 后 git log 确认 a266601 为 main HEAD；git worktree list 无 fix_ui；worktree/ 目录仅存 auto_build/fill_by_llm/model_setting；端口 18981 lsof 无监听；18765 主工作台未更新：main 仓库代码已新，但服务进程与 frontend/dist 仍为旧版，需另行授权构建重启
- 下一步：18765 主工作台更新到 a266601 需用户另行授权（构建+重启）；fill_by_llm/model_setting/auto_build 各自工作树不在本次范围
- 依据/文档：workbench-tasks/fix_ui.json；main 合并提交 a266601；data/workbench-before-migration-0004-0005-20260923-193553.sqlite3

### fix/model-setting-500-db-migration · zcode · 已验证

时间：2026-09-23T11:37:18.985092+00:00；记录：`.collaboration/entries/000269-0a6735d9f0cf.json`

【线上500修复已验证】主工作台18765模型设置页500（requestId=a7a84bccb1cf，sqlite3.OperationalError: no such column: thinking）根因为主库schema落后代码：data/workbench.sqlite3 alembic_version停在20260920_0003，缺0004/0005两迁移。按正规CLI路径 python3 -m workbench.storage.transfer init 显式升级（engine.initialize→migrations.upgrade→alembic heads，幂等，服务不重启），0003→20260922_0005成功。升级前备份：data/workbench-before-migration-0004-0005-20260923-193553.sqlite3（11382784B，WAL一致快照）。

- 决定：迁移走 transfer init 官方入口，未手写ALTER TABLE/裸SQL；服务进程未重启（SQLite动态schema，改列后现有进程即可读）
- 验证：PRAGMA integrity_check=ok；PRAGMA table_info(wb_model_configs) 含 thinking 列=True；HTTP实测：POST /api/auth-login(admin) 200 → GET /api/llm-providers 200，items 为数组含2项，字段含 thinking；备份文件与前后 alembic_version（20260920_0003→20260922_0005）均有命令输出记录
- 下一步：主服务未更新代码版本（本轮只修库），若后续还有代码落后问题另行处理；本轮按任务约束未做git操作，交接记录未提交，后续提交时可包含
- 依据/文档：workbench/storage/transfer.py cmd_init；workbench/storage/migrations.py upgrade；workbench/migrations/versions/20260921_0004_build_task_filter.py；workbench/migrations/versions/20260922_0005_llm_thinking.py；.runtime/server.log#L168附近

### auto_build/fact-quality-v1 · zcode · 已实施，待验收

时间：2026-09-23T07:51:52.375534+00:00；记录：`.collaboration/entries/000267-240b766e7036.json`

【实施完成待验收】按用户指令在 auto_build 工作树多子代理并行实施 Fact 查看与质量评测（fact-quality-v1）：T0 契约冻结（08 分册 §15 全新契约+05 速查 33→48+README 变更记录）→ W1 并行 T1 存储/迁移 0006+T2 诊断引擎 diag-v1+T3 计分引擎 eval-v1+T4 原文回看+T7 前端协议层 → W2 T5 分页基线+T8 浏览视图+T9 评测视图 → T6 十五端点接线（冒烟 47/47）→ W4 T10 物料页集成+T11 黑盒 A1–A10（85 断言）→ T12 收口：全量回归 90/91（唯一失败 test_autofill_integration=18941 端口被 fill_by_llm 服务占用，环境冲突）、typecheck/build/lint 过、隔离浏览器实测 8123B 样本 110 Fact 全场景通过（诊断 ARRAY_SUMMARY_ONLY 4 成员、4 金样 recall 0/4、摘要计 FP、无阈值不显示达标）。实测发现并修复 2 缺陷：范围 path 边界不含数组下标成员（后端+前端+测试）、报告卡信封错读。分支 auto_build 4e5b281→949b03f；工作树库显式迁移 0003→0006；为种子类测试复制主仓库 ontology/ 只读播种树（真实数据，登记已注明）。未合并 main、未更新 18765、工作树保留。

- 验证：python3 tests/run.py all → 90/91（唯一失败为 18941 端口环境冲突）；新增测试：fact_storage 110 / fact_diagnostics 101 / fact_evaluation 60 / fact_preview 16 / fact_query 91 / fact_http 85（A1–A10）断言全过；cd frontend && npm run typecheck / build / lint 通过；git diff --check 干净；浏览器实测（18952 隔离会话）：统计条/分页/详情/原文回看命中/诊断/封闭范围二次确认/4 金样/审核/报告 TP0 FN4 FP1 recall 0(0/4) 逐项通过
- 下一步：交 Codex 独立验收（验收 SHA 949b03f）；验收通过后停在待用户授权集成；工作树含真实数据副本不可自动丢弃
- 依据/文档：文档/需求/20260920_从物料自动构建本体/Fact查看与质量评测_开发计划及执行指令_20260923.md §8；workbench-tasks/auto_build.json；文档/接口文档/08-从物料自动构建本体接口.md §15

### 自动填写 v3：输入区模型选择与思考强度 · codex · 需求已交付

时间：2026-09-23T06:23:50.877899+00:00；记录：`.collaboration/entries/000266-35a113476cb7.json`

用户要求在自动填写输入框内选择当前账号可用模型与思考强度，支持模型默认低。已在原需求目录交付需求说明_v3、开发计划_v3、执行指令_v3；v3 替代 v2 侧栏只读/仅设置选模型的交互决定。约定按具体模型能力展示强度，GLM-5.3-Flash 强制思考支持 low/high/max，DeepSeek deepseek-flash 支持 off/low/high/max；未知能力跟随模型且不假称低。本次仅文档交付，未改业务代码或接口文档，未验收或合并。

- 决定：输入区选择只影响当前自动填写面板会话，不改账号默认或用途配置；切目标重置到用途模型。；请求级 providerId/reasoningEffort 须经服务端账号权限与能力校验；旧客户端省略字段继续旧行为；实现前先更新接口文档。；现有 fill_by_llm 工作树有对象轻量填写的未提交修改，执行者须先核对 owner 并串行处理共享文件，不覆盖。
- 验证：只读核对现有 AssistPanel、formAutofill、llm_purpose、llm_client 和接口文档；v3 三文档内容自检；git diff --check 通过。
- 下一步：harness 在已登记 fill_by_llm 工作树按执行指令_v3 实施、自测并提交，交 Codex 独立验收；不合并 main。
- 依据/文档：文档/需求/20260922_整表自动填写交互/需求说明_v3.md；文档/需求/20260922_整表自动填写交互/开发计划_v3.md；文档/需求/20260922_整表自动填写交互/执行指令_v3.md

### fill_by_llm GLM-5.3-Flash 与豆包速度差诊断及指令纠错 · codex · 已确认决定

时间：2026-09-23T05:57:59.546241+00:00；记录：`.collaboration/entries/000265-1729ed21dd3d.json`

核对智谱官方 GLM-5.3-Flash 文档后纠正先前判断：该模型强制思考，thinking.type 只支持 enabled，reasoning_effort 支持 low/high/max 且默认 max。fill_by_llm 当前模型为 open.bigmodel.cn 的 glm-5.3-flash；代码仅按域名把 thinking off 标记为支持并发送 disabled，UI 的‘思考已关闭’不能证明模型实际关闭。客户端非流式等待完整 JSON，豆包聊天体感可能是首字流式呈现且模型/任务不同。已补充对象轻量填写计划和 harness 指令，要求先核实模型能力/实际请求参数，禁止预设 800 tokens 安全，分开测短提示与推理强度。未改业务代码或数据。

- 决定：把 GLM-5.3-Flash 的思考能力冲突作为现有配置缺陷/待核项，不能以 UI off 文案作为关闭思考证据。；短提示性能 A/B 与 reasoning_effort 参数实验分开，避免混淆因果。
- 验证：智谱官方 docs.z.ai/guides/vlm/glm-5.3-flash 与 docs.bigmodel.cn/cn/guide/start/concept-param；只读核对工作树模型 metadata 与 llm_client.py、llm_purpose.py；git diff --check 通过。
- 下一步：harness 在原工作树实施前应读取更新后的主仓库指令；若已开工需同步此纠错，独立报告 UI 思考状态缺陷和真实模型耗时。
- 依据/文档：文档/需求/20260922_整表自动填写交互/对象轻量填写_开发计划_v1.md；文档/需求/20260922_整表自动填写交互/对象轻量填写_执行指令_v1.md

### fill_by_llm 对象轻量填写 harness 开发指令 · codex · 需求已交付

时间：2026-09-23T05:41:03.908680+00:00；记录：`.collaboration/entries/000264-5e9001fe2567.json`

在主仓库需求目录交付对象轻量填写开发计划与 harness 执行指令 v1。范围限定已登记 fill_by_llm 工作树的新建空白对象首轮生成，按契约能力裁剪通用提示/出网候选，沿用 autofill/1 operations 和校验；要求合成 A/B 耗时与质量证据、分支自测提交、Codex 独立验收，未授权合并 main。当前仅文档交付，未实施业务代码。

- 决定：遵循 v2 的通用提示词＋字段策略，不为对象页另复制整套长期提示词。；第一轮保持现有模型输出协议，不采用 label/comment 直接 JSON 转操作，减少同时改动的变量。；实际提速需对比 modelDurationMs、服务端和 HTTP 总耗时，不承诺固定秒数。
- 验证：核对 fill_by_llm 工作树 fd478ad 干净、main 131d4a7、登记端口 18941；对照 v2 需求与当前对象契约/后端链路；文档路径与内容存在。
- 下一步：harness 按执行指令在原 fill_by_llm 工作树实施、自测并提交，停在 Codex 独立验收；不得合并 main。
- 依据/文档：文档/需求/20260922_整表自动填写交互/对象轻量填写_开发计划_v1.md；文档/需求/20260922_整表自动填写交互/对象轻量填写_执行指令_v1.md

### 自动填写v2简洁需求与harness指令 · codex · 需求已交付

时间：2026-09-23T01:41:16.697646+00:00；记录：`.collaboration/entries/000263-fc1f22bac9bb.json`

在既有整表自动填写需求目录交付需求说明_v2.md与执行指令_v2.md，基于局部原型明确短提示生成对象名称和业务定义、按字段生成策略解耦不同表单、设置中的自动填写专用模型/思考覆盖，以及草稿/保存/撤销/安全与验收边界。当前仅文档交付，未修改业务代码、真实数据或合并分支。

- 决定：每个页面不维护独立整套提示词；用通用生成引擎和字段策略定义差异。；自动填写用途在设置中单独选模型及思考模式，表单侧栏只读显示生效配置。；输入储能系统可生成业务定义草稿，不要求用户先提供完整定义。
- 验证：核对既有v1需求、局部原型v2、fill_by_llm与model_setting现有工作树及接口文档；新文档限定已登记工作树和停止点。
- 依据/文档：文档/需求/20260922_整表自动填写交互/需求说明_v2.md；文档/需求/20260922_整表自动填写交互/执行指令_v2.md；文档/需求/20260922_整表自动填写交互/交互原型_v2.html
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### auto_build · zcode · 实施中

时间：2026-09-23T01:39:15.067419+00:00；记录：`.collaboration/entries/000262-f61fd42ba7f4.json`

【环境启动】按用户指令启动 auto_build 工作树服务（worktree/auto_build，分支 auto_build@2d27584）：工作树内 ./start.sh start，WIZ_WORKBENCH_PORT=18952 + WIZ_WORKBENCH_ROOT=工作树根。首次启动自动 npm install + 前端构建（vue-tsc+vite 通过）并起服务，PID 91512，URL http://127.0.0.1:18952。验证：auth-state 正常、进程 cwd=工作树、lsof 证实读写工作树内 data/workbench.sqlite3（wal/shm 在工作树内），未触碰 main 真实库与其他任务（fill_by_llm 18941/model_setting 18971）资源。备注：本轮交接 entry 曾在工作树 cwd 误记（000259-c692）并已从分支退回，现从主协调树正式补记；登记 json 的 running 状态更新走 main 提交。

- 验证：./start.sh status：✔ 运行中 http://127.0.0.1:18952（PID 91512）；curl /api/auth-state → {"user": null}；lsof -p 91512：sqlite 打开路径=worktree/auto_build/data/workbench.sqlite3；前端构建 vue-tsc --noEmit + vite build 通过
- 下一步：等用户在 18952 登录并下达开发任务；同 host 不同端口共享 Cookie：浏览器登录 18952 可能覆盖 18765 登录态，验收用隔离上下文；main 已前进（88404ef），auto_build 分支仍在 2d27584 基线，后续集成时按协议处理
- 依据/文档：workbench-tasks/auto_build.json；.collaboration/entries/000259-01c4f8ea9810.json

### 新建对象自动填写局部交互原型_v2 · codex · 需求已交付

时间：2026-09-23T01:31:04.416516+00:00；记录：`.collaboration/entries/000261-b130e839b725.json`

新增局部可点击 HTML 原型，仅覆盖新建对象表单与自动填写侧栏：默认手填、输入储能系统后模拟生成名称和业务定义、草稿回填与未保存提示、查看修改、撤销、手改、保存/取消、等待状态。模型在侧栏只读显示，由设置中的自动填写用途配置统一选择，本原型不扩展设置页或真实模型调用。

- 决定：自动填写功能单独选择模型配置，入口放在设置中；表单页面不放模型选择，不打断填写流程。；业务定义允许由短提示生成可编辑草稿，不反问用户提供完整定义。
- 验证：node --check 检查原型内联 JavaScript 通过；HTMLParser 检查无重复 ID、无远程资源。；浏览器工具安全策略拒绝打开本地 file:// 原型，因此未完成浏览器交互实测，交付时如实说明。
- 依据/文档：文档/需求/20260922_整表自动填写交互/交互原型_v2.html

### fill_by_llm · zcode · 实施中

时间：2026-09-22T16:31:14.506067+00:00；记录：`.collaboration/entries/000260-60d5ab674c95.json`

按用户明确指令为 fill_by_llm 创建开发环境（仅建分支/工作树/登记，未开发未装依赖未启动）：分支 fill_by_llm 从最新已提交 main 2d27584 建立，工作树 /Users/gukepeng/Desktop/ZHDL/code/wiz_ai/wiz_kq_builder_v2/worktree/fill_by_llm，端口 18941（创建时实测空闲），WIZ_WORKBENCH_ROOT=工作树根。按 2026-09-22 规则全量随迁 main 数据：transfer backup 快照（31 表与 main 逐表一致，wb_credentials 4=4、wb_model_configs 2=2，解密探针 4/4 通过）+ keys/wb-root.key 副本（shasum 一致）+ ontology-build-blobs/tmp 随迁。登记在 .git/workbench-tasks/fill_by_llm.json，标注含真实数据不可自动丢弃。

- 决定：分支名用用户指定的 fill_by_llm（用户明确命名优先于 codex/<短名> 默认）；端口选 18941：不在既有登记（18871/18872/18881/18882/18890/18921/18931/18951）中且实测无监听；快照校验以逐表行数对比+解密探针为准；transfer verify 仅传 --source 的数量对比项是参数语义误用，不作为缺陷记录
- 验证：git worktree add 成功，工作树 HEAD=2d27584 与 main 一致；快照库 31 张表与 main 逐表行数零差异；凭据解密探针 4/4 通过；integrity_check/foreign_key_check 通过；端口 18941 创建时与登记后两次检查均无监听
- 下一步：等待用户下达开发指令后在 fill_by_llm 工作树实施（首开发轮需先安装前端依赖/构建）；未开发未验收；不合并 main
- 依据/文档：.git/workbench-tasks/fill_by_llm.json；worktree/fill_by_llm
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

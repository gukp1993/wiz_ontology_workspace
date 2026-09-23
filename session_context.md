# Codex / zcode 共享上下文

上下文版本：`074144a474f8f2d7`

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

### auto-build-output-v3 · zcode · 已验证

时间：2026-09-22T16:14:00.625488+00:00；记录：`.collaboration/entries/000258-3a96d0fb49a2.json`

【集成完成】用户明确授权合并 main，已完成：在 auto_build 工作树把 main（已前进 80 提交，含 assist-fill/model_setting 等）合并进来（冲突仅 2 个追加型文档：README 变更记录保留双方、session_context 用 render 脚本重建），组合回归 85/85 全绿 + 前端 typecheck/build 通过，随后 main ff-only 快进到合并提交 2bb5e06。本任务全部交付：控输出 v3 D00–D19（含验收修复 5 项 1df156f、accept 修复 0057e9f）+ 分支上他人任务 c4ba68a（模型思考强度）随合并一并入 main。按用户指令删除 auto_build 工作树与分支（数据副本经核对为 main 库快照、无独有数据）。主工作台 18765 与真实副本服务 18890 未更新（另需授权）。

- 决定：合并以 ff-only 方式落在 main，组合重验基线=合并提交 2bb5e06；冲突处理：仅追加型文档冲突，保留双方内容；session_context.md 按 AGENTS 规则由脚本 render 重建，不手工拼接；worktree 删除前置核对：分支已入 main、无未提交内容、数据副本为 main 快照无独有数据（main 库 11 份资产完整）
- 验证：组合回归 tests/run.py all → 85/85 全绿（/tmp/merge_reg.log，含 main 侧新测试）；前端 npm run typecheck（0 错误）+ npm run build 成功；main 快进前复核：工作树干净、无他人新提交（3e68b89 未动）；git branch --merged main 确认 codex/auto_build 已入
- 下一步：如需更新主工作台 18765 或真实副本 18890 到新 main，需用户明确授权
- 依据/文档：merge commit 2bb5e06（main HEAD）；文档/需求/20260920_从物料自动构建本体/本体生成控输出_整合方案与并行开发计划_v3.md §13

### model_setting-环境创建 · zcode · 需求已交付

时间：2026-09-22T16:00:40.560421+00:00；记录：`.collaboration/entries/000257-658879a5b43a.json`

按用户指令从 main(5bfb605) 创建 model_setting 分支与 worktree/model_setting，仅建环境：端口 18971 实测空闲；数据根工作树自含（transfer backup WAL 快照 + 根密钥副本 0600 + ontology-build-blobs 随迁，登记含真实数据不可自动丢弃）。快照体检 integrity ok、副本根密钥推导 key_id 与库内凭据记录匹配、wb_model_configs 现存 2 行（GLM-5.3-Flash/minimax）。依赖未装、服务未启、未开发。登记 workbench-tasks/model_setting.json 与开发计划 §7 环境表。

- 决定：分支名用用户指定的 model_setting（非 codex/ 前缀模板）；端口取 18971：18961/18913 被占，18951 有旧任务关联，选无关联空闲口
- 验证：git worktree list 确认新树与 auto_build 并存、分支从 5bfb605 派生；transfer backup 输出 11382784B 与 main 库一致；sqlite immutable 只读体检 integrity=ok；key_id 匹配验证：副本根密钥 SHA256 前 16hex == wb_credentials.key_id（c26b32a0c13462a5），副本内加密凭据可解密
- 下一步：待用户开发指令（执行指令.md §1 模板二）；T0 三决策点（目录范围//v1/models/anthropic-messages）未拍板前不冻结契约
- 依据/文档：workbench-tasks/model_setting.json；文档/需求/20260922_模型供应商与模型管理改版/开发计划.md（§7 环境登记）

### 模型供应商与模型管理改版-需求四件套交付 · zcode · 已确认决定

时间：2026-09-22T15:53:41.606973+00:00；记录：`.collaboration/entries/000256-0332c7e4714b.json`

按用户指令参照 ZCode 开源配置模型（本机 ~/.zcode/v2/provider_config.json 与 kingsword09/zcode-cli 文档、应用内置目录 zcode-builtin.json 已核实）交付模型配置改版四件套：三层配置模型（内置目录→供应商模板继承→模型规则目录覆盖/手动）、默认模型三元组 {providerId,modelId,reasoningLevel}、wb_llm_providers/wb_llm_models 两表+Alembic 迁移（providerId 稳定使密钥零重加密）、9 个 API 端点契约、T1-T10 并行任务表。未实施业务代码。

- 决定：模型配置从扁平单表改为 ZCode 式三层结构：仓库内置目录 JSON + 供应商(模板继承/覆盖) + 模型规则(catalog 增量覆盖 | manual 全量)；默认项升级为 {providerId,modelId,reasoningLevel}，替代 models.default_provider_id；isDefault→defaultSelection 属破坏性接口变更需登记；api_type 支持 openai-chat-completions(P0) 与 anthropic-messages(P1)；不抄 OAuth 账号体系/openai-responses/map 表达式引擎，参数注入用按协议的固定映射枚举；迁移保持 provider_id 原值不变，密钥 AAD 不变零重加密；旧编排节点仅 providerId 的绑定回退该供应商默认模型；开放决策点待用户拍板：内置目录首版收录范围、/v1/models 拉取按钮、anthropic-messages 是否首版做
- 验证：现状调研：Explore 子代理只读核实 llm_providers/flow_routes/llm_client/schema/前端设置页/接口文档04 全链路；ZCode 侧核实：本机 provider_config.json 实际结构、zcode-builtin.json(rev30) 模板与模型规则计数、开源仓库 provider.example.json 与 PROVIDER_CONFIG 文档；原型 HTML 标签配对与 JS 语法检查通过；文档不含任何真实密钥（provider_config.json 中的 Key 未复制）
- 下一步：用户拍板 3 个开放决策点后冻结契约（开发计划 T0）；实施需用户按执行指令 §1 下达 worktree 创建指令；接口文档先行（T3）
- 依据/文档：文档/需求/20260922_模型供应商与模型管理改版/需求说明.md；文档/需求/20260922_模型供应商与模型管理改版/开发计划.md；文档/需求/20260922_模型供应商与模型管理改版/执行指令.md；文档/需求/20260922_模型供应商与模型管理改版/交互原型_v1.html
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### assist-fill-production · zcode · 已实施，待验收

时间：2026-09-22T15:21:05.739883+00:00；记录：`.collaboration/entries/000255-7512133e418a.json`

用户截图反馈：自动填写抽屉在生成中（「正在填写…」态）主按钮渲染成空框。定位为 CSS 特异性反吃——.assist-actions button(0,1,1) 只覆盖 background/border 声明，而裸 .assist-primary(0,1,0) 的 color:#fff 仍生效，按钮变白字白底（disabled 态 opacity:.5 时更明显）。修复：规则抬到 .assist-actions button.assist-primary（hover 同步 .assist-actions button.assist-primary:hover:not(:disabled) 压过 (0,3,1)），并在 DESIGN.md 共享语义类表登记该不变量与失败症状。加 3 项回归锁（tests/assist_panel.test.mjs ⑥a/⑥b/⑥c：必须用抬特异性的写法、hover 同款、不得再出现裸单类声明）。全仓同类反吃扫描（裸变体类 + 同父后代选择器覆盖）结果 0 处。前端 45/47（剩 2 个 main 既有债务）、构建过、18951 已重启。提交 eec8314。

- 验证：修复后源码核对：.assist-actions button.assist-primary + hover 变体，无裸 .assist-primary 声明；tests/assist_panel.test.mjs 25/25（含新增 ⑥a/⑥b/⑥c 三项样式回归锁）；全仓扫描裸变体 + 同父按钮覆盖的潜在反吃：0 处；前端 47 套件 45 过（flow_test_workspace/legacy_graph_bridge 为 main 既有债务）；npm run build 过；18951 重启（PID 81880）
- 下一步：待独立验收（交付 SHA 更新为 eec8314）；用户可在 18951 刷新页面确认按钮恢复蓝底白字
- 依据/文档：DESIGN.md（共享语义类表新增 .assist-actions button.assist-primary 行）；frontend/src/assist/AssistPanel.vue:309-313；开发计划 §9

### 整表自动填写 T9 集成与对抗测试（tests/test_autofill_integration.py） · zcode · 已实施，待验收

时间：2026-09-22T12:53:54.209685+00:00；记录：`.collaboration/entries/000253-68f101d5d5e7.json`

交付 tests/test_autofill_integration.py（隔离临时根+自管端口 18941/18942 子进程服务与模型桩，python3 直跑 12 组断言块/130 处 check，实测 5.4s，退出码 0，测后自起进程全部停止）+ tests/fixtures/autofill_integration_seed.json（订单/供应商非储能种子）+ tests/run.py 一行登记 unit 组。覆盖 A09/A14 生命周期（迟到响应零写入、同 token 连轮、无模型 422、超时 504、空 operations→200 empty、截断 502）、A15 安全（伪造/篡改/畸形 token、跨用户、契约外字段与任意 JSON 路径→unresolved、XSS 原样、密钥与 trace 不泄漏）、A16 契约漂移、D1~D7、非储能全链路。生产缺陷 1 项按能力探测+阻塞登记（缺陷修好后自动改跑完整断言）。

- 决定：工作目录已有同名未提交半成品（上一位 agent 遗留）：在其上续接修正，未推倒重写。；修正该半成品两处测试自身缺陷（非生产缺陷）：identity 场景传空草稿，把 P1 分级候选的正确 fail-closed 行为误判为失败；已按 P1 语义改写并补「已选表则主键候选核验通过」正例。；零写入不变式改分段基线：原文把测试自身的管理写（发新版本、登提供方、升级项目引用）也算入，改为 D7 后重取基线，只断言纯生成段零写入。；连接密码改按接口文档 03 §3.4 经 /api/connection-secret 写 vault 播种；原半成品把明文 password 塞进项目 connections 草稿导致其随 /api/project-state 回显，属测试播种方式错误，非生产漏洞。；D1 等值回显前后端口径差异按「记录不裁定」处理，交协调者/独立验收决定。
- 验证：python3 tests/test_autofill_integration.py → EXIT=0，全部通过（12 组断言块），5.44s。；python3 tests/run.py --test test_autofill_integration.py → 通过 1/1（5.4s）；--list 复核 unit 52 项、all 63 项各含本文件 1 次，组内无重复。；ruff check tests/test_autofill_integration.py tests/run.py → All checks passed。；未触碰 workbench/、contracts/、frontend/（含 dist）、.runtime/、data/、ontology/ 及 tests/ 下他人文件；find -mmin 复核零修改。18951 未停止未连接；18941/18942 已释放。；开场仅只读 git log/status 核对基线，无 git 写操作。
- 下一步：生产缺陷待修：workbench/assist_schema.py:1042 `_fill_cell_summary` 读 cell['path']/cell['type']，而 workbench/assist_forms.py:703 `FormContract.list_def()` 返回 _normalize_leaf 归一结果（无 path/label）→ KeyError → server.py 兜底 400。复现：任取声明 lists 的契约（actionBinding、propertySource 的 database/redis/flow 变体）发 mode=fill 即 100% 400，4 变体全不可用。；D1 等值口径差异交独立验收：服务端 fill 不做等值过滤（原样下发 ok），等值不落盘靠前端 changed/topLevelChanges；模型只回显旧值时前端走 done+收起、appliedCount=0、状态条为空，不命中 AssistPanel.vue:182 的 empty 提示分支，与需求 §4.3/A14 有落差。；A01/A02/A18 与 A03~A08/A10~A13/A17 的浏览器呈现分支留 T10 独立验收；本文件头已列不覆盖清单。
- 依据/文档：tests/test_autofill_integration.py；tests/fixtures/autofill_integration_seed.json；tests/run.py；workbench/assist_schema.py 与 workbench/assist_forms.py（缺陷位点）；文档/接口文档/04-编排与LLM接口.md 与 03-项目区接口.md §3.4

### 整表自动填写 T8（P1/P5/P6 三页接入，分支 codex/assist-fill-production） · zcode · 已实施，待验收

时间：2026-09-22T12:17:21.547743+00:00；记录：`.collaboration/entries/000252-f2e2b095ed47.json`

在上一位 agent 半成品上续接完成 T8：identityLinkBindings.ts / actionBindingAdapter.ts 补齐 restore 回写（统一 restoreSnap + cloneJson，宿主 undoRound 与引擎 restore 共用），核对 formId/contractInfo/codecs/applyDraft/snapshot/手改通知全覆盖；ObjectSources/LinkMappings/ActionBindings 三页改 T5/T6 范式——页头次要按钮「✦ 自动填写」（aria-expanded/aria-haspopup + trigger-id 反向接线）、默认无 AI 区、状态条+撤销+查看修改+手改通知，回填只改本地草稿（零 touch/changed/form-save/commit-now），旧建议卡/勾选/采纳 UI 全部移除。

- 决定：P1：registered 模式快照只含 {mode,note}（说明类可填，连接/表/主键 visibleWhen=false 不可见即不可填）；instances 登记实例清单永不出网、不批量生成、不凭 id 名称推断唯一性；mode 切换走组件既有 applyMode 守卫，被拒时同批数据库键一并丢弃。；P5：binding 以 targetId='<对象类型>.<关系id>' 绑定当前编辑的那一条映射行，applyDraft/restore 只写该行契约白名单键；relation/targetType/membership/legacy 等白名单外结构不进快照、不被回填、撤销不触碰（A13 零丢失）；两端字段值照建议原样提交，前端不做「字段同名＝业务等价」判断（服务端核验，不过即转 unresolved）。；P6：auth.* 按契约 ai.sensitive 前端 binding 直接拒绝写入并记入 refusals（快照绝无 auth 故永不出网；点路径 auth.credentialId 同样拒绝），状态条逐条显示原因；适配层与宿主无任何网络调用（测试用 networkCalls 计数断言），参数行只在本地草稿落位。；rowId 裁决：identity/linkMapping 契约 lists=[] 无行结构，仅组件键映射 primaryKey↔primary_key、note↔noteDraft；actionBinding actionParams（rowIdScope=local）由 actionParamRows codec 精确落行——row.update/remove 必须命中现有 rowId（未命中抛错→引擎记 failures 不中断其余操作），row.append 沿用服务端 localId（仅冲突时由 newParamId 补齐，保续轮定位稳定），未涉及行原样保留。；修复半成品实际缺陷：parametersCodec 原来按 v.op 判定入参三类，但引擎 row.append 传入 {localId,fields}（无 op 键）会被误判为「显式整组」抛错——改为先判 row.update/remove、再判 {localId|fields} 为 append、最后才是数组整组；valueIn 兼容契约键 property/valueType 与组件键 propertyId/type 双形态。另：SSR 下 setup 阶段 watch 不触发（实测 Vue 3.5.41），ActionBindings 另导出显式 assistTouched() 手改入口。
- 验证：node --import ./tests/ts_hooks.mjs tests/assist_identity_link.test.mjs → 16/16 通过（重写为新交互：binding 工厂/登记模式说明回填且不批量生成实例/来源模式 connection·table·primaryKey 链路/上下文请求/直接回填无勾选/整轮撤销/手改禁撤销/续轮累计与一次撤销/显式保存 commitDesc 落盘/入口 aria 与抽屉）。；node --import ./tests/ts_hooks.mjs tests/assist_action_bindings.test.mjs → 16/16 通过（URL/方法/参数行回填、row.update·remove·append 精确落行、未命中行失败不中断、auth.* 拒绝且草稿 auth 不变、零网络调用、form-save/commit-now 零调用、撤销含 auth 与行 id 保真、显式保存含历史字段零丢失）。；cd frontend && npx vue-tsc --noEmit → 退出码 0（全仓 0 错，含 T7 并行文件）；npx eslint 我的 5 个文件 → 0 违规（顺带清掉 ActionBindings.vue 既有 no-unused-expressions）。；相邻套件回归：assist_object_workspace 14/14、assist_panel 22/22、assist_property_manager 74/74、assist_workflow 12/12，mapping_forms/object_sources/action_model/source_config_retention/ui_protection_independent 通过；python3 tests/run.py --test tests/test_autofill_contracts.py → 423 断言通过。
- 下一步：停在待 Codex 独立验收：本轮未跑 npm run build（按任务边界只做 vue-tsc），也未起服务做浏览器实链路验收；页头入口/抽屉焦点/aria-expanded 回落/手改 watch 仅由组件级测试与源码断言覆盖。；浏览器验收建议确认：三页入口在窄屏(≤1100px)遮罩态、Esc 关闭焦点回落、done 自动收起后 aria-expanded 回落；ActionBindings 弹窗内状态条与参数表共存布局。；T7（propertySourceBinding/PropertySources）为并行改动，本轮未触碰；未做 git 提交（任务指令禁止），提交由协调者安排。
- 依据/文档：文档/需求/20260922_整表自动填写交互/需求说明.md §3（P1/P5/P6）、§4.4、§4.5、A13；文档/接口文档/04-编排与LLM接口.md §6.6 前端行为契约；contracts/forms/identity.json、linkMapping.json、actionBinding.json；frontend/src/assist/ontologyBindings.ts（T5 createRoundMirror/undoRound 范式）；tests/assist_identity_link.test.mjs、tests/assist_action_bindings.test.mjs

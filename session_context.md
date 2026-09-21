# Codex / zcode 共享上下文

上下文版本：`bc09c7301a448dcb`

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

### assist-fill-production T5 本体对象与链接编辑接入辅助填写（O1/O3） · zcode · 已实施，待验收

时间：2026-09-21T07:47:37.963008+00:00；记录：`.collaboration/entries/000161-ddf7325ef208.json`

在已登记 worktree（codex/assist-fill-production）交付 T5 三文件：修改 frontend/src/ontology/ObjectWorkspace.vue（对象/链接表单各加「✦ 辅助填写」入口行、assistBinding computed 按 editor 目标构造、字段事件经 assistTouched→模板 ref 通知面板、AssistPanel 挂表单内、closeEditor 同步收起+watch 兜底），新建 frontend/src/assist/ontologyBindings.ts（objectAssistBinding/linkAssistBinding 工厂：白名单快照 {label,comment}/{label,from,to,cardinality,reverseLabel,comment}、undefined 不覆盖、白名单外忽略、快照就地恢复保持草稿引用稳定），新建 tests/assist_object_workspace.test.mjs（真实 SFC 编译 AssistPanel+ObjectWorkspace、provide('assist-api') 注入合成桩，9 组检查全过）。采纳只改本地草稿零保存调用（form-save spy=0）；手改后草稿指纹拦截采纳并解除撤销。未 git 提交、未跑 build、未启服务。

- 决定：binding 工厂签名：(draft, options:{targetId?,contextTitle?})，draft 传编辑器实时草稿对象（引用须稳定，restore 就地恢复）；手改通知走字段输入事件+模板 ref（AssistPanel defineExpose 的 notifyDraftChanged），SSR 下 ref 为 null 自动跳过；面板草稿指纹自检作为兜底（手改后 adopt 必被拦）；目标切换：assistOpen 保持、assistBinding computed 随 editor 引用变化产生新对象，由 AssistPanel 内部 watch(binding) 重开；编辑器关闭/切属性表单置 null 收起；api 注入点：inject<AssistApi>('assist-api')（可选，缺省面板用 defaultAssistApi()），测试经 provide 注入桩
- 验证：node --import ./tests/ts_hooks.mjs tests/assist_object_workspace.test.mjs → 9/9 退出 0；object_workspace 7/7；assist_panel 51/51；ont_list_unified 4/4；dependency_guard 22/22；list_controls 4/4；editor_head_consistency 19 项全过；cd frontend && npm run typecheck → 退出 0；mapping_forms.test.mjs 在本分支 HEAD 即失败（PropertySources 改版所致，与本轮文件无引用关系，未处理）；未做浏览器实测/build/真实联调
- 下一步：浏览器验收需覆盖：面板 onMounted 自动取上下文、assistPanelRef 通道即时置过期横幅（SSR 无法覆盖，测试已注明）；mapping_forms 失败需归属负责人处理；集成注意：assist/ 下 propertyBinding.ts（T6）与 ontologyBindings.ts（T5）为并行新增，无文件冲突
- 依据/文档：frontend/src/ontology/ObjectWorkspace.vue；frontend/src/assist/ontologyBindings.ts；tests/assist_object_workspace.test.mjs

### assist-fill-production T6 属性与共享属性编辑接入辅助填写（O2） · zcode · 已实施，待验收

时间：2026-09-21T07:44:14.160264+00:00；记录：`.collaboration/entries/000160-3e203be8dd69.json`

在已登记 worktree（codex/assist-fill-production）交付 T6 三文件：修改 frontend/src/ontology/PropertyManager.vue（辅助入口+面板挂载+手改通知），新建 frontend/src/assist/propertyBinding.ts（propertyAssistBinding 宿主适配工厂）与 tests/assist_property_manager.test.mjs（54 断言）。未 git 提交、未跑 build、未启服务；未触碰 SharedLibrary/ObjectWorkspace/App.vue/AssistPanel/useAssistPanel 及并行 T5 代理的文件。

- 决定：binding 工厂：propertyAssistBinding(opts{draft,targetKind:'property'|'sharedProperty',targetId,isTimeSeries,setType,setObsType})，setter 注入=组件 setRange/setObservation 本体（唯一写路径）；apply 内 dataType/obsType 原子组先类型后观测值，formatting 整组放最后（类型联动会清 formatting，先写会被误删）；快照白名单 {label,comment,dataType,obsType?,formatting?}：obsType 仅时间序列出现；formatting 取 mg:formatting @value；dataType 与 selectedType 同值；空 obsType（离开时序补位）不进 setter；formatting 经 effectiveConfig 判空，同 PropertyFormatting.sync 写 @json 或删键；snapshot/restore 为草稿节点整体 JSON 克隆，restore 原位替换键值保持 draft 对象身份（reactive 与 PropertyFormatting JSON watch 兼容）；组件侧 assistBaseline 在打开/采纳/撤销重对齐，深度 watch 只把面板写入以外的草稿变化通知 notifyDraftChanged；目标映射：私有属性编辑/新建 targetKind=property（新建 targetId 空串，后端出「新建属性定义」标题）；就地维护共享定义与 kind=shared → sharedProperty；共享引用只读态无入口；toShared 转只读时 watch(readonly) 自动收起面板；采纳绝不调用 formSave/commit-now/touch/emit；共享高影响确认仍只由显式 save() 触发（需求 11 指纹机制不变），采纳后的显式保存照常弹确认并落盘
- 验证：node --import ./tests/ts_hooks.mjs tests/assist_property_manager.test.mjs → 54/54 通过退出 0：白名单快照映射/克隆、apply 与手动 UI 最终草稿逐字节等价、离开时序联动不残留、formatting 写换清与同组采纳顺序、采纳后 formSave 间谍零调用且 impactOpen=false、显式保存走桩合入 MANAGED 键、共享定义采纳后显式保存弹确认→确认落盘、只读态无入口、真实 AssistPanel 挂载/收起、手改后 stale 与撤销保护解除；cd frontend && npm run typecheck（vue-tsc --noEmit）→ 退出 0；复跑相邻回归无相互影响：tests/assist_panel.test.mjs 51/51、tests/editor_head_consistency.test.mjs 19/19、tests/dependency_guard.test.mjs 22/22，均退出 0；git status 核对：本任务仅改上述三文件；ObjectWorkspace.vue/ontologyBindings.ts/assist_object_workspace.test.mjs 为并行 T5 代理文件未触碰
- 下一步：协调者串行执行 npm run build 与提交（按指令边界本任务未做）；集成注意：T2 建议的 formatting 复合组含 kind 判别键，binding 按 effectiveConfig 原样写入 @value（零丢失约定容忍未知键）；若裁定不落盘仅需在 propertyBinding.ts formatting 分支删键；测试注意：SSR 输出模板注释，断言辅助入口须匹配按钮文案「✦ 辅助填写」而非裸文本；tests/assist_object_workspace.test.mjs ⑦ 当前失败，属并行 T5 未提交的 ObjectWorkspace.vue WIP（不涉及本任务文件），请传导给 T5 owner
- 依据/文档：frontend/src/assist/propertyBinding.ts；frontend/src/ontology/PropertyManager.vue；tests/assist_property_manager.test.mjs；文档/需求/20260920_本体与项目辅助填写/开发计划_正式实现.md §8.2；workbench/assist_fields.py property/sharedProperty 白名单

### assist-fill-production T2 模型输出schema与建议验证 · zcode · 已实施，待验收

时间：2026-09-21T07:10:54.155870+00:00；记录：`.collaboration/entries/000159-a3228b2580b6.json`

在已登记 worktree（codex/assist-fill-production）完成 T2：新建 workbench/assist_schema.py（ASSIST_SYSTEM_PROMPT、build_user_payload、parse_model_output、5 个复合组校验器）与 tests/test_assist_schema.py（57 项全过 exit 0）。只创建这两个文件，未改其他文件、无 git 写操作、未启服务、未连真实 LLM。

- 决定：失败分层按 04 §5.2『502 或丢弃该条』：整体非 JSON/顶层形态非法/条数超限 → ModelBadResponse；单条结构违规 → dropped；引用/行内/原子组 → blocked 可见禁选；依赖未答问题 → pending；等值 → 丢弃。任务书测试项2 与流水线 4a 对『未知 fieldKeys/键不齐/枚举错/超长』矛盾，取 4a 分层丢弃（文档允许）；formatting『未知参数键』复合组节写结构违规、测试节要求 blocked，取 blocked 与行级策略一致；parse_model_output 增可选第 7 参 mode='fill'（任务书要求 explanation 仅 explain 非 null 但签名无 mode）；dataType 放行 'timeSeries'（同 PropertyManager typeOptions）、obsType 放行 ''；离开 timeSeries 自动补 obsType:'' 并并入 fieldKeys；服务端重编 questionId 为 q_<n>、suggestion id 为 s_<n>，模型 state 忽略，blocked/pending 原因写 blockedReason/pendingReason；候选提取对齐 T1 实际形状：definitions 编码 connection（engine 在 hint）/source/property/flow；catalog {connection,table,fields}；flows outputs[].fields[].id；保留 sources/identity/parameters/actionInputs 扩展键与 candidates 覆盖钩子；候选缺失 fail-closed
- 验证：python3 tests/test_assist_schema.py → 57 项全过 exit 0（合法输出/重编、4 类整体超限、ref 幻觉、5 类复合组、原子组、ifQuestion 四态、等值、evidenceRefs、HTML 保留、注入仅数据、payload 裁剪、mode 语义、T1 形状对齐）；env -u WIZ_WORKBENCH_ROOT 直接 import 成功（无数据目录依赖）；py_compile 通过（Python 3.9.6）；git status 核对：仅新增本任务两文件；T1/T3 并行文件与 session_context.md 改动非本任务所改
- 下一步：T4 路由调用 parse_model_output 时传 mode 与 normalize_draft 的 draft_kind；行级身份表字段核对（params/lookup 的 identityField）需 T4 在 context 注入 identity:{table,fields} 或用 candidates 覆盖；交 Codex 独立验收；HTTP 层与真实模型联调属 T4/T11 范围未做
- 依据/文档：workbench/assist_schema.py；tests/test_assist_schema.py；workbench/assist_fields.py；文档/接口文档/04-编排与LLM接口.md §5；文档/需求/20260920_本体与项目辅助填写/开发计划_正式实现.md §8.2
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### assist-fill-production T1 辅助填写上下文构建及脱敏 · zcode · 已实施，待验收

时间：2026-09-21T07:02:09.603542+00:00；记录：`.collaboration/entries/000158-2698549e7868.json`

在已登记 worktree（assist-fill-production，基线 b231179=T0 冻结提交）交付 T1 两个新文件：workbench/assist_context.py（build_context/sign_token/verify_token/check_generate/compute_fingerprint/ContextStale）与 tests/test_assist_context.py（12 组用例）。只读语义：不写修订、不持 LOCK、不起服务、不连真实库/LLM、未执行 git 操作；未改任何既有文件（assist_schema.py/AssistPanel.vue 等为并行 T2/T3 代理文件，未触碰）。

- 决定：build_context(space, project_id, target_kind, target_id, purpose, draft, ontology_id='storage') 返回 (payload, token_payload)：payload 为 §5.1 完整响应体（contextToken 已签名/contextFingerprint/context），T2 可直接返回；exp=now+TOKEN_TTL(600s)，dh=canonical_hash(白名单裁剪后 draft)。；错误语义对齐 server.py 既有映射：ValueError→400；AuthRequired→401；ProjectNotFound/VersionNotFound/WorkspaceNotFound/storage.NotFound→404（targetId 不可见按不存在抛 NotFound）；CatalogCacheUnreadable→503 冒泡；令牌问题一律 ContextStale(ValueError 子类)→409 CONTEXT_STALE。verify_token 只管验签/结构/过期，uid 归属在 check_generate 用 auth.require_user_id 比对。；项目区指纹与候选共用 _project_authority 单次读取（项目 head token＋引用版本坐标＋目录 fingerprint:generation 含损坏条目＋全部编排 head token），杜绝候选与指纹不同基线；本体区先取 head token 再读草稿（fail-closed 方向）。compute_fingerprint 可独立调用供 check_generate 注入。；候选裁剪按类独立（每类 60、每表字段 100、hint 160 字），截断显式标记为 §5.1 增量键：context.definitionsTruncated/catalogTruncated/flowsTruncated＋catalog[i].fieldsTruncated＋flows[i].inputsTruncated/outputsTruncated/fieldsTruncated。目录口径同 GET project-state：单条损坏跳过该连接（指纹仍计入），存储层失败 503 冒泡不降级。；HMAC 密钥为进程级 secrets.token_bytes(32) 懒生成（不落盘不进日志）；令牌只含 uid/space/projectId/targetKind/targetId/fp/dh/exp/purpose。场景候选：本体区对象/属性/共享属性/链接/规则/动作摘要；项目区按 draft.kind 分派（identity/field/database/redis/flow/linkMapping/actionBinding），连接只取 id/名称/engine，目录字段只取 name/comment/dataType。
- 验证：WIZ_WORKBENCH_ROOT=$(mktemp -d) python3 tests/test_assist_context.py → 12/12 通过、退出码 0：正常构建×4（本体 object/项目 identity/propertySource(flow)/actionBinding）、白名单与形态 9 类 ValueError、越权 404 含跨账号、令牌篡改/过期/缺字段、check_generate 四类 stale、脱敏（脏连接 password 与 LLM API Key 不进返回值）+modelReady 两分支、截断（65→60/105→100/62→60 均带 truncated）、目录损坏跳过+存储失败冒泡、只读不变式零写入。；不预设 WIZ_WORKBENCH_ROOT 直跑同样 12/12 退出 0；python3 -m py_compile 通过（本机 3.9.6，无 3.10+ 语法）；tests/run.py --list 已收录（unit 组自动发现）。未跑前端/服务/全量回归（纯新增两文件，git status 确认零既有文件改动）。
- 下一步：T2 接线：/api/assist-context 可直接返回 build_context 的 payload；generate 侧调 check_generate(tp, space, project_id, target_kind, target_id, draft, lambda: assist_context.compute_fingerprint(space, project_id))；按 docstring 的异常映射转状态码。；T3 前端镜像消费截断标记键；接口文档 04 §5.1 需登记截断增量键与 title 规则（本轮无文档改权）。；T4 复用 token_payload 的 dh/fp 与 canonical_hash(normalize_draft(...))；propertySource 候选已按 draft.kind 在服务端分派。
- 依据/文档：workbench/assist_context.py；tests/test_assist_context.py；workbench/assist_fields.py（T0 冻结，只读）；文档/接口文档/04-编排与LLM接口.md §5.1/§5.2；文档/需求/20260920_本体与项目辅助填写/开发计划_正式实现.md

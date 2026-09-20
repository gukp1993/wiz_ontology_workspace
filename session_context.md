# Codex / zcode 共享上下文

上下文版本：`fcdf435bb1aa0b43`

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

## 最近交接（新 → 旧）

### 本体与项目完整性评审文档归档 · codex · 需求已交付

时间：2026-09-20T01:38:36.849137+00:00；记录：`.collaboration/entries/000070-d17015a36c7f.json`

将已讨论的产品完整性评审输出为Markdown，保留现状、问题表、储能场景、优先级、暂缓项和待确认选择，不生成开发计划或改业务代码。

- 验证：文档内源码与需求相对链接校验通过；未重新执行运行验收。
- 依据/文档：文档/交付物/20260920_本体建模与项目映射完整性评审/产品需求与功能完整性评审.md

### 迁入编辑器 6 轮打磨修复（白屏/布局/保存/撤销/视口）+ 浏览器逐项复验 + 提交 df16d77 · zcode · 已实施，待验收

时间：2026-09-20T01:26:57.295800+00:00；记录：`.collaboration/entries/000069-46b998c990a1.json`

用户首测报点击本体图谱白屏（TDZ），以修复—复验循环完成 6 轮实质修复：R1 白屏 TDZ（ALL_TYPES 上移）；R2 画布高度 0（补迁旧全局样式 legacy.css，弹窗遮罩 display:none）；R3 领域变更未进保存协调器（LegacyGraphHost 补绑 before-change/changed）；R4 撤销栈不含领域操作（domainCmd 包装 8 处领域命令）；R5 坐标导入被旧 state.graphId 拦截；R6 0 尺寸画布写退化视口。浏览器逐项复验 R03–R17 并对 C01–C05 复验；500 节点/1200 边实测首屏 923ms、搜索热态 50ms/次、整理 1985ms。

- 决定：旧仓库全局 style.css 按编辑器依赖提取为 legacy.css 并限定 .legacy-editor-root 容器内，不污染工作台全局样式（需求 P1）；领域命令统一经 domainCmd() 包装：执行前记录领域快照入撤销栈，失败不入栈不污染 redo；Ctrl+Z 经桥 applyUndo 换回真实领域模型并落库；视口持久化防护：容器尺寸≤0 不写；恢复时过滤 minZoom/pan=0 退化值；无有效记忆回退首次适应可见节点（与旧编辑器同 minZoom=0.1 行为）；子代理独立验收因账号配额（2026-09-21 11:19 重置）未执行，改由实施者按同一清单自查并逐项浏览器复验，配额限制如实登记
- 验证：浏览器（隔离实例 18840，用户 legacytester）：编辑器挂载 0 控制台错误、画布 484×961；新建动作落库 POST /api/save；批量连线 1 条落库 + 非法组合（共享属性为源）零写入拦截；删除被引用对象被拒；撤销/重做 9→10→9→10 且落库一致；C01 热路径改链接终点后 edge.target() 真实重连；C03 图谱→对象定义→返回选中保留；C05 选择模式下按钮全部响应；画布操作 0 次 /api 调用；测试与构建：ontology_graph 12/12、legacy_graph_bridge 12/12、npm run build 通过；四档宽度截图 4 张在需求目录/截图/；R17 性能（500 节点/1200 边，macOS arm64+IAB 1440×900）：首屏 923ms、搜索首次 1002ms/热态 50ms、整理 1985ms；与旧编辑器同代码路径无退化
- 下一步：用户验收 18765：本体→对象建模→本体图谱；本轮修复已 restart；配额恢复后补做子代理独立验收（R12 预览截图、R11 文件级往返、1440 工具栏五类 chips 拥挤优化）
- 依据/文档：frontend/src/ontology/legacyGraph/legacy.css（新增）；EditorView.vue；legacyBridge.js；LegacyGraphHost.vue；frontend/src/ontology/ObjectWorkspace.vue；文档/需求/20260919_图谱编辑器源码整体复用/开发计划.md §5.2；修改内容清单.md；git df16d77
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 修复：编排编辑页刷新后项目变空（用户反馈） · zcode · 已实施，待验收

时间：2026-09-20T01:21:16.610592+00:00；记录：`.collaboration/entries/000068-85f86f0ca46b.json`

用户反馈：选编排→编辑→刷新 #f-editor 后当前项目为空。根因：项目选择只存内存（prefSet 记忆了 id 但没恢复状态），刷新与导航对编排页只加载项目列表、不恢复项目状态；项目区三页走 ensureProjectContext 因而无此问题。已实施 a22bc32：新增 restoreStoredProject()——编排页加载侧栏列表后，若有上次项目记忆则后台恢复（force 跳过离开保护、不阻塞渲染、项目失效静默跳过）；刻意不自动选第一个项目（编排不依赖项目）。18765 已重启。

- 决定：恢复策略='仅恢复上次用户选择过的项目'而非'自动选第一个'：编排不依赖项目，自动绑定会引入非用户意图的项目上下文（可能触发多余的保存/刷新链路）；编排页恢复走后台（void），不阻塞编排内容渲染；恢复失败不影响编排编辑；失效记忆静默跳过保持未选择态，不弹错（列表已刷新，下次用户选择会覆盖记忆）
- 验证：build（vue-tsc）通过；global_interaction 7/7、flow_model、ontology_home 27 项回归全过；隔离实例 18905（已清理）浏览器实测完整复现路径：p-home 选择项目→编排列表→编辑→直接刷新 #f-editor→项目选择器仍显示「创智园二期演示」、面包屑与编排内容正常；修复前该场景刷新即空；18765 restart 200；真实数据零写入
- 下一步：用户在 18765 复验：选编排→编辑→刷新，项目应保持
- 依据/文档：frontend/src/App.vue（restoreStoredProject/ensureProjectContext/settleInitialView）；git a22bc32

### 本体建模与项目映射产品完整性评审 · codex · 已确认决定

时间：2026-09-20T01:17:35.170134+00:00；记录：`.collaboration/entries/000067-d34fa7e2a6ce.json`

需求与源码层评审完成：概念骨架已具备，缺口集中在属性取值闭环、确定性集合计算、编排版本固定、说明执行语义、动作结果闭环。仅建议讨论，未实施。

- 决定：先确认配置交付工具与业务运行时的边界；不重复建设已有实体与虚拟对象、时序、编排测试。；建议固定编排修订、区分配置与执行验证状态；不增加独立聚合菜单，不擅自恢复此前暂缓的动作输入与提交条件。
- 验证：只读核对共享上下文、需求与路由、属性预览、编排执行、项目发布升级、动作配置和说明消费检查。；当前预览只支持登记/旧聚合；编排绑定读当前草稿；Python为LLM求值且公式不支持集合；动作仅配置。；未做本轮浏览器或端到端验收，未改业务代码及真实数据；其他执行者在途改动未触碰。
- 下一步：讨论运行边界、说明执行关系、单位/时间口径、动作执行范围，再确定需求。
- 依据/文档：workbench/project_property_reader.py；workbench/flow_executor.py；workbench/projects.py；workbench/project_validation.py；workbench/mapping_descriptions.py

### 建立绑定支持编排输入/输出边界节点（用户反馈） · zcode · 已实施，待验收

时间：2026-09-20T01:15:53.927974+00:00；记录：`.collaboration/entries/000066-47b43115c2c1.json`

用户反馈建立绑定不能选编排输入节点。已实施 38b426d：编排输入可作来源（点边界节点→点目标节点，绑定弹窗来源预选「编排入口参数」）；编排输出可作目标（点来源节点→点边界节点，弹「绑定到编排输出」选输出声明与来源输出端口，预选未占用端口、类型不相容禁用确认）；语义守卫保留（输出不能作来源/输入不能作目标，明确提示）；模式栏文案更新。顺带修复测试自查发现的真实缺陷：来源为边界节点时在 nodes 列表找 source 失败导致入口绑定被静默丢弃。新增 tests/flow_editor_bind.test.mjs 18 项组件行为测试。

- 决定：边界绑定不新增数据契约：仍写 目标输入 source={kind:'flowInput'} 与 编排输出 binding={kind:'node'}，与既有存储/校验完全一致；确认前做类型相容预检（复用 flowModel.typesCompatible）：不相容不写入并保留弹窗让用户改；等于把后端校验前置为可读提示；编排输出弹窗预选「未被占用的输出端口」，多输出节点减少选择成本
- 验证：tests/flow_editor_bind.test.mjs 18 项全过（E01-E04）；flow_model、flow_test_workspace 回归全过；build（vue-tsc）通过；隔离实例 18904 浏览器实测（已清理）：建立绑定→点「编排输入」→点目标节点→弹窗预选入口参数→确认后连线派生+自动保存+问题计数更新；再点来源节点→点「编排输出」→绑定弹窗→确认后第二条连线+「检查通过」；18765 已 restart 200；真实数据零写入
- 下一步：用户在 18765 用「设备SOC计算（Redis版）-副本」试：建立绑定→点「编排输入」→点新 HTTP 节点；若还想要画布直接拖线，可再提
- 依据/文档：frontend/src/flow/FlowEditor.vue；frontend/src/flow/FlowCanvas.vue；tests/flow_editor_bind.test.mjs；git 38b426d

### 实施 图谱编辑器源码整体复用（P0–P4 收尾提交 573a586） · zcode · 已实施，待验收

时间：2026-09-20T00:55:46.124716+00:00；记录：`.collaboration/entries/000065-e463a5bccee6.json`

整体复制旧编辑器（HEAD b2f10d1，8 文件 SHA256 与计划一致）到 frontend/src/ontology/legacyGraph/：EditorView/Preview/MultiPreview+组件/composables/core/shared，交互逐行保留，类型扩为当前五类；新增 legacyBridge 适配层（lg: 稳定 ID 投影、领域命令走 before-change/changed/保存协调器、撤销=领域快照、坐标=视图偏好、外部变化检测）；保存接 commit-now，版本管理→校验与发布页，发布版本只读预览（单图/多本体多选），坐标导入导出+离线图谱包客户端生成，jsonId 有损默认阻止；旧 MCP/问数/徽标/另存版本/jsonId 导入移除并登记；删除上一版只读控制器消除双控制器。

- 决定：节点/边身份=lg:<类型前缀>:<领域稳定ID>；边身份=定义 ID（链接/共享引用/归属）或类型+双方 ID（规则/动作关联），平行链接与自关联如实上图；删除保护与当前建模一致（graphReferences/规则与动作引用/共享引用计数）；批量连线先全量校验整批拒绝；引用边删除=属性转私有继承字段，归属边删除=删私有属性；jsonId 导出含动作/私有属性/共享引用/时间序列等不可表达内容时默认阻止并列损失清单；完整迁移走配置迁移；图谱包=store-only ZIP（坐标+离线预览 HTML，无凭据断网可看）；旧 3 秒自动保存/版本快照/第二套存储切断；本体切换/新建经 App 既有守卫链路（switchOntology/createOntologyNamed）
- 验证：build（vue-tsc+vite）通过；tests/ontology_graph.test.mjs 12/12（模型断言保留）+ tests/legacy_graph_bridge.test.mjs 12/12（新增领域断言）；既有前端回归全过：object_workspace 7/ontology_home 27/global_interaction 7/save_queue 22/undo_history 7；修改内容对照表（逐文件适配+旧功能→当前入口+C01–C05 口径+已知限制）见 文档/需求/20260919_图谱编辑器源码整体复用/修改内容清单.md；开发计划 §5.1 已回填
- 下一步：子代理验收与打磨循环补齐：R01–R18 隔离浏览器逐项走查、四档宽度截图、500/1200 性能对比、失败与 409/账号切换实测（§5.1 如实登记未测项）；用户验收 18765：本体→对象建模→本体图谱（新编辑器）；重点核对新建/连线/删除/撤销走当前保存链路
- 依据/文档：frontend/src/ontology/legacyGraph/（全部迁入文件+legacyBridge.js+offlineBundle.js+LegacyGraphHost.vue）；frontend/src/ontology/ObjectWorkspace.vue；frontend/src/App.vue；OntologyGraph.vue 已删除；文档/需求/20260919_图谱编辑器源码整体复用/修改内容清单.md；开发计划.md §5.1；git 573a586

### 函数编排持续打磨（开发+子代理验收交替，≥50轮交互） · zcode · 已实施，待验收

时间：2026-09-19T21:55:00.055434+00:00；记录：`.collaboration/entries/000064-b579e2ce0a15.json`

按用户要求对函数编排做持续打磨：三路代码审查+两轮独立验收代理交替，命名轮 R01–R20，子代理有效交互 7 个（另 2 个大范围代理停滞失败后拆小重发），审查发现 45 条、修复 38 条（含画布白屏 P1、测试视图可选输入阻断 P1、后端校验漏洞 3 条 P2、LLM 异常路径 3 条 P2），不采纳 1 条（SSR F 黑名单，localhost 单机工具且会破坏本地模型场景，理由记日志）。共 11 个提交 38806d5→a5401a2。18765 已重启。

- 决定：列表配置状态区分「N 个问题/ N 项提示」（error/warning 分计）并按更新时间降序；空编排给 FLOW_EMPTY 提示不再假「检查通过」；列表 check 携带 llm_meta（每账号一次）；后端加固：链测试 422 门拦截流程级错误（成环不再退化 400）；重复节点 ID、Redis 缺命令、args 未声明名、HTTP 凭据与自填 Authorization 冲突均校验阻断；密钥库故障如实报错不伪装；画布 P1：悬挂输出绑定致 cy.add 抛错整块白屏——坏边按节点存在性过滤；拓扑签名剔 classes（勾选/运行态不再全量重建）；TypeEditor 按目标类型清理残留键；LLM 四条异常路径（非 dict JSON/HTTPException/超大响应/引号内括号提取）+endpoint 控制字符拒绝
- 验证：最终回归：run.py all 33/33、flow_model 46 项、flow_test_workspace 36 项全过；test_flows 85→94、flow_test_workspace 21→36 增量用例；build（vue-tsc）通过；两轮验收代理分别对 6 提交与 4 提交独立复验全过（含隔离实例 API 实证 NODE_ID_DUPLICATE/REDIS_COMMAND_MISSING/成环 422/endpoint 控制字符 400）；浏览器实测（隔离实例 18903，已清理）：列表四态文案、重命名弹窗脏守卫、画布加删节点同步、SQL 模板呈现、单节点测试错误路径、LLM 弹窗示例占位、HTTP/SQL 编排真实执行与失败传播；18765 与真实 ontology/ 零写入；18765 restart 200；临时实例/目录/种子脚本全部清理；在途他人文件（legacyGraph/tmp）未触碰
- 下一步：用户验收 18765：编排列表/编辑器/测试视图/LLM 配置页；未尽事项见打磨日志（flow 取值预览属执行能力未做、SSR F 黑名单留多租户评估、mapping_forms 既有基线失败属他人）
- 依据/文档：文档/需求/20260919_函数编排持续打磨/打磨日志.md；frontend/src/flow/（全部组件）；workbench/flows.py、flow_executor.py、flow_routes.py、flow_http.py、llm_client.py、llm_providers.py；git 38806d5..a5401a2（11 提交）
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 四提交独立验收（4799bcb/b7bc31b/581b80c/c039746） · zcode · 已验证

时间：2026-09-19T21:51:48.334963+00:00；记录：`.collaboration/entries/000063-601f9faadeee.json`

独立验收通过：四提交回归全绿（flows 94/llm 40/executor 33/test_plan 70/flow_model 46/flow_test_workspace 36 全过，build vue-tsc+vite exit0）；五项重点 diff 审查全部通过；隔离实例 18933 API 断言 endpoint 带换行/制表符均 400「接口地址不能包含换行或控制字符」且未落库；无 P1/P2 新问题。

- 决定：FlowCanvas nodeIds 过滤=渲染节点集精确闭合：derivedEdges 自滤悬挂引用、flowInput 边 source=INPUT_NODE 在集合内、output 悬挂 nodeId 正确丢弃；旧过滤因 el.group 恒真（普通对象无 group 字段），commit 描述准确；topologyOf 剔除 classes 自洽：run-failed/run-waiting/test-checked/bind-src 四动态类 refreshData 全量重算，boundary 静态，无仅在 sync 加类的路径；TypeEditor list→list/object→object 保留用户已配 elementType/fields（仅缺失才初始化默认）；llm_client read(_MAX_RESPONSE+1) 对恰好 1MB 无误判；非 dict 分支在解析成功后判，顺序正确；4799bcb parameterId 前后端同源（flows.py iid/oid ↔ row.input.id/out.id）；b7bc31b watchEffect setup 即时初始化收敛无循环；581b80c list_metadata 每调用读一次，快照语义不变
- 验证：python3 tests/test_flows.py=94、test_llm_providers.py=40、test_flow_executor.py=33、test_flow_test_plan.py=70 全过；node ts_hooks 两套件 flow_model 46/flow_test_workspace 36 全过（后者计数大于任务书 29，为后续提交新增用例，非异常）；npm run build（vue-tsc）通过，仅存量 chunk>500kB 警告；隔离实例 18933（WIZ_WORKBENCH_ROOT+WIZ_DATABASE_URL 临时 sqlite+临时账号）llm-provider-save 断言过，llm-providers 列表空；实例/临时目录已清理，18765 与真实数据零写入
- 下一步：P3：_extract_json 首个 opener 落在字符串内提取失败后不回退找后续块，极边缘不影响既有用例
- 依据/文档：git 4799bcb b7bc31b 581b80c c039746；frontend/src/flow/FlowCanvas.vue、TypeEditor.vue、NodeConfig.vue；workbench/llm_client.py、llm_providers.py、flows.py

### 函数编排打磨六提交独立验收（38806d5/8e5b7d2/75bc160/4341a7a/1e6aecc/5c3235b） · zcode · 已验证

时间：2026-09-19T21:29:29.463026+00:00；记录：`.collaboration/entries/000062-f08da4796606.json`

独立验收通过：六提交回归全绿（flows 94/executor 33/test_plan 70/sql_dialect 25/llm 40/project_flow_source 29/两个 node 套件/5c3235b 归档 build exit0）；隔离实例 18847 API 断言四项全过（NODE_ID_DUPLICATE、REDIS_COMMAND_MISSING、成环 flow-run 422、范围外节点错误不拦片段 200）；无 P1/P2 新问题。

- 决定：75bc160 的 422 门只额外拦流程级全局错误，非 target 节点级错误不拦，API 实证 200；4341a7a 代次守卫自洽：cacheContext 先 runGen++ 再释放，晚回包不落新上下文；declSig 含 type，隐藏空文本勾选无残留死锁；1e6aecc 公式键随迁在 before/changed 之间符合快照机制，撤销整体一步，仅缺 actionLabel 回退通用文案（原有模式）；范围内 listing 逐编排查 LLM 元数据为 P3 性能瑕疵，已在此后提交 581b80c 修复
- 验证：python3 tests/test_flows.py 等 6 套件全过；node --import tests/ts_hooks.mjs 两套件全过；git archive 5c3235b 到临时目录 npm run build exit 0（vue-tsc+vite）；隔离实例 18847（WIZ_WORKBENCH_ROOT/WIZ_DATABASE_URL 临时）flow-check/flow-run 四断言过，服务与临时目录已清理，18765 与真实数据零写入
- 下一步：P3 建议：renameTechnical 补 actionLabel；calc 输出改名撞已有名时 formulas 同名键覆盖边缘
- 依据/文档：git 38806d5..5c3235b；workbench/flow_routes.py；workbench/flows.py；frontend/src/flow/FlowTestWorkspace.vue；frontend/src/flow/NodeConfig.vue

### 图谱编辑器源码整体复用需求 · codex · 需求已交付

时间：2026-09-19T14:55:54.439502+00:00；记录：`.collaboration/entries/000061-2bb89a17b152.json`

按用户要求交付完整需求、开发计划、执行指令三份文件。明确整体复制旧编辑器及依赖，当前工作台提供数据/领域操作与必要适配，不再重新实现画布。未实施业务代码。

- 决定：新需求替代上一版只读画布范围，纳入新建编辑删除、批量连线、撤销重做及坐标/预览能力。；保存发布、鉴权和数据库仍使用当前工作台；旧MCP与问数外围服务不迁入。；以旧真实源码为交互基准，不另写模拟原型；源文件hash与逐功能对照作为验收要求。
- 验证：核对旧EditorView导入依赖、功能入口、useGraph保存机制与Vue/Cytoscape依赖。；三份文档内部链接检查通过，源关键文件SHA256已记录；无业务实现或运行验收声明。
- 下一步：执行者按P0–P5整体复制与适配，逐项验收R01–R18并复验旧C01–C05。
- 依据/文档：文档/需求/20260919_图谱编辑器源码整体复用/需求说明.md；文档/需求/20260919_图谱编辑器源码整体复用/开发计划.md；文档/需求/20260919_图谱编辑器源码整体复用/执行指令.md

### 本体画布独立验收与源码复用复核 · codex · 已确认决定

时间：2026-09-19T14:48:26.494516+00:00；记录：`.collaboration/entries/000060-0ffcec657e26.json`

独立验收发现五项缺陷，暂不通过；用户质疑重写后复核旧源码，建议优先复用成熟交互代码并做当前数据与导航适配。本轮未修改业务代码。

- 决定：两项目同用Vue与Cytoscape，数据模型差异不要求重写交互层。；旧整页依赖旧路由、状态与保存接口，不直接覆盖；源码复用路径尚未实施。
- 验证：build及原两套图谱测试通过；隔离补测复现端点未更新、邻域重挂载恢复失败、链接返回定位错误。；隔离浏览器复现重复快捷键处理及选择模式下工具栏点击失效。隔离服务与临时测试数据已清理。
- 下一步：按验收报告保留五项问题，先对照旧源码确定复用范围，再实施修复并复验。
- 依据/文档：文档/需求/20260919_本体图谱画布能力优化/验收报告.md；文档/需求/20260919_本体图谱画布能力优化/开发计划.md §6.4–6.5

### 工作台整体只读体验与优化建议 · codex · 已确认决定

时间：2026-09-19T14:27:19.852233+00:00；记录：`.collaboration/entries/000059-c9697f82c2d5.json`

实际体验18765的本体对象/共享属性、项目概览/连接/映射、函数编排编辑与测试工作区、模型设置/配置迁移，交付分优先级体验评审报告。未修改业务代码或真实配置，未运行业务测试。

- 决定：保留现有对象列表详情、说明textarea与折叠配置、版本引用和独立测试布局，不建议全站重写。；优先修正旧计算实现入口、删除与移除引用文案、状态与账号范围；下一阶段重点补齐属性绑定后的取值预览，再增加命名测试用例。；Python节点当前为模型求值，公式计算已有确定性引擎；建议清晰区分，不顺带引入沙箱。；仅产品建议，未授权实施；在途画布改动不纳入本轮验收。
- 验证：Chrome真实导航检查了35kV本体、创智园二期储能簇采样SOC绑定和采样取值编排测试页，无保存、发布、连接探测或业务调用。；源码核对现有属性预览仅支持登记/旧聚合且拒绝flow；check_flow把警告与错误均置pending；模型配置实际按账号隔离。
- 下一步：用户选择优先需求后，再为该项输出四件套；本报告不自动扩大实施范围。
- 依据/文档：文档/交付物/20260919_工作台体验评审/体验评审.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

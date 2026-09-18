# Codex / zcode 共享上下文

上下文版本：`8c8cebb686f18adb`

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

### 本体列表统一设计-v1 · zcode · 已实施，待验收

时间：2026-09-18T11:09:59.761351+00:00；记录：`.collaboration/entries/000008-e57bd03223c8.json`

按四件套实施八处列表统一：新增 T1 公共层（ontList.ts 全量过滤后分页、OntologyList 标准表格、OntDrawer 只读抽屉、RowMenu 改 Teleport+fixed 浮层与 compact ⋯）；对象导航行高 58px 与计数文案；四页签全部表格化（列/操作/分页按 §6）；三资产库改全宽表格（规则库无更多菜单、动作库取消左列并保留历史格式转换与删除保护）；跨菜单维护/返回来源对象闭环。修复中发现的两个缺陷：动作/规则行缺统一 id 致返回不定位、行内菜单被滚动容器裁切。仅改前端本体区，无接口/后端/数据变更。

- 决定：视图状态统一走 useOntTable：搜索/排序在全量记录上执行再分页，页码随结果收缩合法化；编辑一律复用既有表单；危险操作只收进 RowMenu 不改语义；共享删除仍走私有化+外部引用拦截；规则库不新增删除；动作删除受引用保护；历史动作只读保留可显式转换；RowMenu 浮层 Teleport 到 body 避免表格滚动容器裁切
- 验证：typecheck 与 build 通过；新增 ont_list_unified 4 项全过；object_workspace 8/8；run.py quick 3/3；既有前端套件 20/21：唯一败 mapping_forms 经 stash 基线对照为本批之前已存在，未修如实保留；隔离实例 18890 浏览器 1440/1024/768：四页签表格与分页、跨页搜索、浮层完整可见、抽屉焦点归还、三库表格、跨菜单返回定位高亮、768 单栏无横向溢出；浏览期间 0 次 /api/save；全部夹具仅写隔离库；未测 100x200 规模浏览器压测（以分页逻辑用例+26 条夹具覆盖）
- 下一步：用户/Codex 验收原型一致性；mapping_forms 既有失败属属性取值来源任务，需其负责人处理；真实 18765 服务未重启加载本次构建，验收通过后按发布流程处理
- 依据/文档：frontend/src/ontology/ontList.ts；frontend/src/shared/OntologyList.vue；frontend/src/shared/OntDrawer.vue；frontend/src/shared/RowMenu.vue；文档/需求/20260918_本体列表统一设计/开发计划.md

### 本体列表统一设计-共享属性库 SharedLibrary.vue 单文件改造 · zcode · 已实施，待验收

时间：2026-09-18T10:32:38.145252+00:00；记录：`.collaboration/entries/000007-4a41782de8cd.json`

按需求 §6.5/§5/§7/§8 重写 frontend/src/ontology/SharedLibrary.vue（仅此一文件）：条目卡片改 OntologyList 全宽表格（名称42%/数据类型20%/引用情况20%/操作18%，每页20，名称 zh-CN 排序，名称+业务定义搜索）；页头 ont-lib-head 右侧 RowMenu（粘贴多行/批量复用）+「＋ 新建共享属性」，删底部 details.library-more；#filter 数据类型分段筛选（9 项，propertyDataType 判定，时间序列不命中数值）；名称点击 OntDrawer 只读详情（定义/类型/单位/引用+编辑定义）；引用徽标 OntDrawer 引用位置列表（ont-ref-row 跳回对象属性，保留 externalReferencesOf 提示），usages 弹窗分支移除。编辑态/四弹窗/removeShared 私有化/feedback/locate 全保留。

- 决定：引用计数口径不变：usageCount=referencesOf 记录数，非去重对象数；原「共 N 项 · M 处引用」信息移到表格下 ont-context 行；totalText 用组件默认；locate 适配分页：目标行不在筛选/当前页时清类型筛选、reset 并翻到所在页再高亮（data-lib-row 在 tr 上）；.ont-filters 样式写本文件 scoped（全局无此类）；timestamp 归「时间」筛选项；删除本文件未用的 valueShapeOf/shortType/typeLabel/rangeOf 死代码，业务逻辑零删减
- 验证：npm run typecheck：SharedLibrary.vue 0 错误（唯一初错 aria-label kebab 未解析为 ariaLabel prop，已改 ariaLabel= 传参）；残留 8 错全在未触碰文件：ObjectWorkspace.vue 757/795/824/853 TS2345（同 aria-label 问题）；ontList.ts 29 行 source.value 应为 source()（运行时会崩）与 47 行 3 处接口类型不匹配；与并行交接 000006 所见一致，属公共层既有问题；按铁律未跑 git/build/服务，浏览器验收未做
- 下一步：ontList.ts 与 ObjectWorkspace.vue 的公共层错误由各自负责人修复，否则 build 失败；整包构建后做隔离浏览器验收（保存定位、筛选组合、抽屉焦点归还）
- 依据/文档：frontend/src/ontology/SharedLibrary.vue；frontend/src/shared/OntologyList.vue；frontend/src/ontology/ontList.ts；文档/需求/20260918_本体列表统一设计/需求说明.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 本体列表统一设计-动作定义库改造（单文件） · zcode · 已实施，待验收

时间：2026-09-18T10:26:21.773328+00:00；记录：`.collaboration/entries/000006-f30026a04add.json`

按 20260918 需求 §6.7/§5/§7/§8 重写 frontend/src/ontology/ActionLibrary.vue（仅此一文件）：取消 EditorLayout 左列表/右详情布局，改为 ont-lib-head 页头 + OntologyList 全宽表格（名称32%/业务效果33%/关联对象18%/操作17%，名称 zh-CN 排序、名称+业务定义搜索、引用情况分段筛选、分页）；名称点击打开 OntDrawer 只读详情（业务定义/业务效果/关联对象 ont-ref-row 可跳对象动作页签；历史动作保留历史字段只读区；footer V2=编辑、历史=转为新格式编辑，focusOrigin?.type 存在时加返回来源对象）；行内关联对象徽标打开关联对象抽屉；openEdit/openConvert/remove 改为按 id 参数调用，行内编辑对历史动作走既有转换确认；新增可选 prop focusOrigin，watch(focusId) 改为清筛选并打开详情抽屉；watch(rows) 等价改写为编辑目标一致性检查，guard 注册/注销与 onBeforeUnmount 保留；空态区分无数据/无匹配。

- 决定：删除保护语义不变：V2 被对象引用禁止删除、graphReferences 检查、danger 确认，仅收入 RowMenu 更多菜单；编辑表单（mode edit card）整体保留独占整页宽，converting 替换旧字段逻辑与保存文案不动；ont-filters 分段按钮样式按需求规格写在本文件 scoped style（未改 style.css，未动任何其他文件）
- 验证：cd frontend && npm run typecheck：ActionLibrary.vue 0 错误；typecheck 全量剩 8 个错误全部来自其他文件（非本任务范围，未动）：ObjectWorkspace.vue 757/795/824/853 四处 aria-label 未解析为 ariaLabel prop；ontList.ts 29 行 source.value 应为 source()、47 行 OntTable 接口返回类型不匹配（ref<string> 实例化表达式/ComputedRef vs WritableComputedRef）；按铁律未运行 git/npm build/服务，浏览器验收未做
- 下一步：OntologyList/OntDrawer/RowMenu/ontList.ts 的公共契约属 T1 文件：ontList.ts 现存 4 个类型错误会让 npm run build 失败，需该文件负责人修复；ObjectWorkspace.vue 四处 aria-label 需同样改为 ariaLabel 传 prop；App.vue 后续接线 focusOrigin prop（本组件已支持）
- 依据/文档：frontend/src/ontology/ActionLibrary.vue；frontend/src/shared/OntologyList.vue；frontend/src/ontology/ontList.ts；文档/需求/20260918_本体列表统一设计/需求说明.md

### 本体列表统一设计-v1 · codex · 需求已交付

时间：2026-09-18T08:36:10.448107+00:00；记录：`.collaboration/entries/000005-2ef756cc8ff9.json`

已交付本体区八处列表统一设计四件套，仅需求与独立原型，未实施工作台源码。对象选择使用紧凑导航列表，其余七处清单统一标准表格。

- 决定：统一列字段、工具栏、分页、只读详情、行操作与窄屏；编辑继续复用既有表单。；保留共享属性删除时私有化及引用保护；动作/规则对象端仅移除关联，规则库不新增删除。；不变更本体协议、项目映射、真实数据及保存发布流程。
- 验证：独立 HTML 的 24 项源码级 DOM 检查通过，包含搜索分页、详情引用定位、关联移除保留原定义、空/异常状态。；浏览器 URL 策略拒绝本地 HTML 预览，未完成视觉验收；未改产品代码，未运行产品构建。
- 下一步：用户评审原型；执行者按四份文件实施，完成正式构建与隔离浏览器验收。
- 依据/文档：文档/需求/20260918_本体列表统一设计/交互原型_v1.html；文档/需求/20260918_本体列表统一设计/需求说明.md；文档/需求/20260918_本体列表统一设计/开发计划.md；文档/需求/20260918_本体列表统一设计/执行指令.md

### 模型录入区分套餐类型 · zcode · 已确认决定

时间：2026-09-18T08:07:09.406417+00:00；记录：`.collaboration/entries/000004-c657bede3352.json`

用户先提出模型录入优化（区分 GLM coding plan 与非 coding plan 端点），随即决定本期不改：录入表单保持现状，端点地址由用户自行手工录入。未做任何代码改动。

- 决定：本期模型录入不增加 coding plan/标准端点区分；用户手工填写接口地址（编码套餐 key 配 /api/coding/paas/v4/，普通充值 key 配 /api/paas/v4/）
- 验证：本轮无代码改动，无需验证
- 下一步：若后续版本再做此优化，可参考 000003 号记录中的确诊依据（1113 错误码与双端点实测）
- 依据/文档：.collaboration/entries/000003-ef5d34cac0b8.json；frontend/src/tools/LlmProviders.vue

### GLM模型端点配置修复 · zcode · 已验证

时间：2026-09-18T08:05:04.092918+00:00；记录：`.collaboration/entries/000003-ef5d34cac0b8.json`

用户 GLM 提供方连通失败根因确诊并修复：该 key 为编码套餐（GLM Coding Plan）密钥，标准 API 路径无余额（直连实测 HTTP 429 错误码 1113「余额不足或无可用资源包」），同一 key 走 coding 专用路径返回 200。已把 llm-5021bee500 的端点改为 https://open.bigmodel.cn/api/coding/paas/v4/chat/completions（其余字段未动、密钥沿用已存值），工作台连通测试转为通过。附带证实 temperature=0 在该模型可用（此前怀疑不成立）。

- 决定：编码套餐 key 必须配 coding 端点（/api/coding/paas/v4/），标准 paas/v4 仅适用于充值了标准 API 的密钥
- 验证：直连标准端点复刻工作台请求：429 + {"code":"1113","message":"余额不足或无可用资源包"}；直连 coding 端点同 key 同请求：HTTP 200 模型响应（temperature=0 与 0.1 均 200）；经工作台 /api/llm-provider-save 改端点（未带 apiKey，沿用已存值，keyConfigured=true）后 /api/llm-provider-test：ok=true 连通正常 1527ms；诊断调用共 5 次，密钥未写入任何文件、git 或交接记录
- 下一步：提示用户：该 key 已在聊天明文出现过，建议之后在 bigmodel 控制台轮换；编码套餐按套餐限速，若编排高并发调用再遇 429（1302/1304）属套餐限流而非配置问题
- 依据/文档：workbench/llm_client.py；frontend/src/tools/LlmProviders.vue

### 共享上下文接入 · zcode · 已实施，待验收

时间：2026-09-18T07:43:19.905634+00:00；记录：`.collaboration/entries/000002-5c8fe20346b4.json`

zcode 已接入项目 Hook：新建 .zcode/config.json（hooks.enabled=true，SessionStart/UserPromptSubmit/Stop 三个事件调用 context.py hook --actor zcode，timeout 10s）。经 zcode 官方配置指南核实：事件恰为七种、含所需三种；工作区配置式 Hook 无信任门（区别于 Codex 需 /hooks 审查），enabled 即生效。临时根六项协议模拟全过：additionalContext 注入含 ticket、Stop 未交接首块二提（防循环）、交接后空输出放行、缺 session/turn 降级仍可注入、仓库外 cwd 空转。当前会话按规则主动交接；真实客户端事件触发留待下一轮验证。

- 决定：zcode 采用工作区 .zcode/config.json 配置式 Hook，actor=zcode 与 Codex 配置互不影响；未改共用脚本与存储格式
- 验证：python3 tests/test_context_sync.py：7 项通过；zcode 官方 zcode-configuration-guide/diagnosing-hooks skill 核实事件与输出协议（additionalContext 注入、Stop 可请求续跑、输出 JSON 严格校验）；临时根模拟 6 项：UserPromptSubmit 注入、Stop 首块/次提/交接后放行、降级、仓库外空转；本会话真实 read→record 完成（本轮 ticket）；手动模拟均用 --root 临时目录，未污染真实事件计数；未验证：真实客户端事件触发（配置为本轮新建，热重载未知），下一轮观察注入上下文与 .runtime/context-hooks.json 计数
- 下一步：下一轮核对真实触发：AI 可读到注入上下文、Stop 核对生效；若 zcode payload 字段名与 snake_case（session_id/turn_id/hook_event_name）不符，仅写薄字段适配器，不改共用存储；若本会话未热加载，重启会话后生效
- 依据/文档：.zcode/config.json；.collaboration/context.py；文档/需求/20260918_共享上下文自动交接/zcode接入指令.md；文档/需求/20260918_共享上下文自动交接/使用说明.md

### 共享上下文自动交接-v1 · codex · 已实施，待验收

时间：2026-09-18T07:30:34.427985+00:00；记录：`.collaboration/entries/000001-9b2570bcc1d3.json`

共用交接脚本、Codex 项目级 Hooks、AGENTS 规则及 zcode 接入指令已落地。当前会话主动交接可用；Codex 新 Hook 尚需用户信任并验证真实事件，zcode 尚未接入，不能声称两端已经全自动。

- 决定：以每轮和阶段交付为同步时机，不等待关闭会话；Codex 与 zcode 分别追加交接，由共用脚本加锁汇总，不直接覆盖 session_context.md；不调用外部模型，不扫描原始聊天或写入业务数据库
- 验证：python3 tests/test_context_sync.py：7 项通过；含隔离并发、去重、重建、防循环、常见密钥拒绝；.codex/hooks.json JSON 解析通过；本机 CLI hooks feature 开启，项目受信任；未完成真实客户端 Hook 信任与自动触发验证；未进行工作台业务回归
- 下一步：用户在 Codex /hooks 审查并信任本项目 Hook，然后在下一轮验证实际触发；zcode 按接入指令核实自身事件能力并配置；无 Hook 则先使用规则驱动的主动交接；当前其他工具仍在改项目映射，后续验收需重新核对代码和其交接
- 依据/文档：文档/需求/20260918_共享上下文自动交接/使用说明.md；文档/需求/20260918_共享上下文自动交接/zcode接入指令.md；.codex/hooks.json；.collaboration/context.py；tests/test_context_sync.py

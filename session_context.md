# Codex / zcode 共享上下文

上下文版本：`8a59d3fa935dec28`

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

### 执行 20260920_本体与项目统一维护体验改版 执行指令（v2 功能保护，多Agent并行） · zcode · 实施中

时间：2026-09-20T07:07:50.953248+00:00；记录：`.collaboration/entries/000117-35aec319ef1d.json`

W3 阶段：A 三项最小修复完成（B1 前端补 applicable_objects 三入口一致、B2 references 补裸名前缀容错、B3 槽位身份改目标出现序号不放松保护；test_references 21 步、dependency_guard 22/22、金样 97/507 全过）；F 前端保护独立复核 60 项通过并发现两缺陷，协调者已修：① ConnectionManager 引用检测补 inlineSql（原漏报导致先见「保存失败」）、② PropertySources 编排读取失败且配置未改时不再阻断「只改说明」的保存；C/E 产物经 F 独立复核通过；L 补 stale 前端分支与目录/凭据条件写接线、保存后清理已删连接派生数据；12 步发布守卫测试（a–l）全过。B（领域校验）与 D（前端基线）仍在实施，D 的幂等 key 签名含 revision 的修正待其应用。

- 决定：F 发现①处理：UI 引用检测必须覆盖 inlineSql/inline.connection，与服务端保存边界同形态，避免用户先见持久化失败。；F 发现②处理：编排读取失败仅阻断发布（G1 契约），配置未改、只改说明的草稿保存不被阻断（过滤该条提示）。；A 的 B1 采用「同步前端」而非登记差异：三入口（图谱/对象页/批量）统一预告阻断。；A 的 B3 槽位身份=引用类别+目标 id+同目标出现序号（不用所在序号）；新增同目标槽位仍产生新键。
- 验证：test_references 21 步（含新增单元⑩B2/⑪B1/⑫B3）、dependency_guard 22/22、legacy_graph_bridge、object_workspace 7/7、ont_list_unified 4/4、金样 97/507 全过。；F 复核 ui_protection_independent.test.mjs 60 项全过（换表逐字段保留、未知 kind 只读、flow failed/missing 区分、stale 不覆盖、删除先落盘）。；我修后复跑：connection_manager.test.mjs、ui_protection_independent、source_config_retention 全过；typecheck exit=0；test_publish_guards 12 步、quick 3/3、unit 27/27、test_project_api_roundtrip 13 步、test_save_iteration、test_references、test_mapping_descriptions、test_flows、test_storage_contract 全过。；接口文档 02 §2.2 已登记 B2 前缀容错与 B3 槽位身份口径（文档先行的同 commit 差异）。
- 下一步：等 B（project_validation/project_mapping/影响匹配）与 D（key 签名修正等）回收；随后金样预期差异生成与回放。；W4：npm run build（cs统一一次）、隔离实例浏览器验收（F）、§11.4 逐项 F01–F10、按工作包提交。
- 依据/文档：文档/接口文档/02-本体区接口.md §2.2、03-项目区接口.md §2.1-§2.4/§3.3、README 变更记录；workbench/{references,projects,project_routes,flows,catalogs,secrets,server}.py；tests/{test_publish_guards,test_references,ui_protection_independent,connection_manager,source_config_retention}.py|mjs
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 角色F 独立QA：复核L发布守卫与C目录/凭据语义（只新增测试） · zcode · 已实施，待验收

时间：2026-09-20T07:07:44.269945+00:00；记录：`.collaboration/entries/000116-25597eede82e.json`

新增两个独立测试文件（动态端口+独立临时根+假账号），未改任何生产文件与他人测试。tests/test_publish_guards_adversarial.py 135项断言全过：4路并发同requestId同内容（Barrier真并发，3轮全200）只多一个版本v1、wb_requests一行、各响应version/revision完全一致、无5xx与裸约束异常；带旧revision重试回放优先CAS；同key异内容409零写入；dict键序不同仍回放，数组顺序不同则409不回放（观察项）；catalogs/_draft漂移不影响回放；保存边界422+state逐字节零写入，覆盖object_bindings/implementations/inlineSql/sources四种引用形态，同批移除放行；注入窗口改编排head→409 DEPENDENCY_CHANGED、releases不增、revision不推进、无回执，恢复同key重试成功；CAS与422不写回执；跨账号及同账号他项目均不串号。tests/test_catalog_independent.py 硬断言47项全过+6项规格观察：fingerprint仅改name/id不变、host/port/database/username/tls/caPath/dbIndex逐个变化；store_if_current正向可写、改地址/换凭据/删连接/代际过期四种注入全False且payload+generation逐字节不变；损坏payload抛CatalogCacheUnreadable带connection_ids、strict=False跳过、GET不5xx；secrets.save幽灵项目拒绝且不建资产、不可读回、代际0。

- 决定：只新增 tests/test_publish_guards_adversarial.py 与 tests/test_catalog_independent.py；未修改 workbench/ 生产代码，未改 L 的 test_publish_guards.py 与 C 的 test_connection_catalog.py。；幂等指纹实测为 canonical（sort_keys）：dict 键序不同仍回放；数组顺序敏感，同 key 数组逆序→409 REVISION_CONFLICT。属冻结口径固有结果，登记为观察项与客户端 key 轮换风险，不判缺陷。；冻结契约未落地项以「规格观察」分区记录（默认不阻断，WIZ_QA_STRICT=1 时按失败计），避免把未接线记为通过。
- 验证：python3 tests/test_publish_guards_adversarial.py → 通过135项/不符0，退出码0，约8s；A1并发复跑3轮稳定（[200,200,200,200]，version/revision集合唯一）。；python3 tests/test_catalog_independent.py → 硬断言47项通过/不符0/规格观察6项，退出码0；WIZ_QA_STRICT=1 退出码1（规格观察按失败计）。；python3 tests/run.py --test <两文件> 均通过；两测试均断言 DATA_ROOT 与 engine.resolve_url() 落在各自临时根内，可并行；未访问真实 ontology/18765/MySQL/Redis/外网。
- 下一步：建议将 test_publish_guards_adversarial.py 归入 HTTP 组登记，但其动态端口与独立临时根可与 L 的 18841 并行，不需要串行。；P0 待修：损坏目录未阻断校验/发布（errors=[] 且实际发布出 v1）——需 B 落地 degraded_catalogs 参数并移除 project_routes 的 except TypeError 回退。；P0 待修：storage 级目录读取失败时 POST /api/project-validate 正确 503，但 GET /api/project-state 返回 500 INTERNAL_ERROR（server.do_GET 未登记 CatalogCacheUnreadable）。；P1 建议：编排 unreadable 的校验文案与 missing 区分；数组顺序敏感的 key 轮换约定补入接口文档。；未覆盖：多进程/多实例目录落库窗口竞争、浏览器点击级闭环、真实 MySQL/Redis 探测。
- 依据/文档：tests/test_publish_guards_adversarial.py（新增，135项断言）；tests/test_catalog_independent.py（新增，硬断言47项+规格观察6项）；workbench/project_routes.py:169-190（degraded_catalogs 的 except TypeError 回退）；workbench/server.py:331-334 与 do_GET 异常分支（GET 未映射 CatalogCacheUnreadable→503）；文档/接口文档/03-项目区接口.md:189-190、219-228（冻结契约）；开发计划.md §11.2（G1）

### 从物料自动构建本体需求与原型v1 · codex · 需求已交付

时间：2026-09-20T07:04:55.447768+00:00；记录：`.collaboration/entries/000115-bbd9b7893015.json`

交付工作台内从物料生成新本体的需求说明与交互原型v1，供用户评审；无正式代码或真实数据修改。

- 决定：本轮严格只交付需求说明.md和交互原型_v1.html；开发计划、执行指令等用户确认后再生成。；沿用已确认范围：混合前后端/DDL/Word/PDF/Excel/MD，多轮澄清，证据充分默认拟纳入、弱证据暂缓，仅生成新本体。；新提议交互覆盖任务、物料、范围、生成、证据评审、新建保存；技术栈/OCR/规模上限等明确待评审。；适配当前本体自有协议及最新规则动作字段精简方向，不恢复动作参数、项目映射或MCP口径生成。
- 验证：原型JS语法通过；Node VM最小DOM的19项交互检查通过，涵盖失效、依赖、冲突与失败保留。；相对链接、两文件交付、HTML解析及无网络调用检查通过。未做浏览器视觉验收，未运行正式应用构建或业务测试。
- 下一步：用户评审原型和需求范围，确认后再生成开发计划与执行指令。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/需求说明.md；文档/需求/20260920_从物料自动构建本体/交互原型_v1.html
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 角色A W3：本体保护三项最小修复（B1 applicable_objects 一致 / B2 domain-range 前缀容错 / B3 槽位序号回退误报） · zcode · 已实施，待验收

时间：2026-09-20T07:02:30.679046+00:00；记录：`.collaboration/entries/000114-7ec34699bb8d.json`

按开发计划 §11.2 三项低危缺陷最小修复并实测。B1：editorModel.graphReferenceEntries 同步 applicable_objects，对象删除检查与图谱保留命令现预告阻断，与后端 new_broken_references 同口径（dependencyModel/legacyBridge 生产码经核查无需改）。B2：references.py 新增 _canon_graph_ref，domain/range 接受 mg: 前缀与裸名（裸名按 mg: 核对，节点 id 为裸名的历史数据精确命中不误报，xsd:/http: 行为不变）。B3：无稳定 id 的签名槽位与 mg:constraint.fields 无名称槽位改用「同槽位内同一目标引用的出现序号」做身份——删除前置槽位不再误报历史悬空，新增同目标槽位/删除目标仍阻断。仅改自己独占文件：editorModel.ts、references.py、test_references.py、dependency_guard.test.mjs、legacy_graph_bridge.test.mjs。未 git add/commit，未改接口文档。

- 决定：B1 选「前端同步」而非登记差异：后端 applicable_objects 拦截源于编辑器统一删除判定，非有意更严；前端补判后删除即预告阻断。；B2 与前端 matches 同口径：精确命中优先、其次补 mg:；裸名仍不命中时按 mg: 身份核对，不放松也不臆造其他前缀身份。；B3 槽位身份 = 引用类别+目标 id+同目标出现序号（有稳定 id 仍优先用 id）；比较键仍只由稳定 ID 组成，新增悬空必产生新键，保护不放松。；生产改动限定 2 个文件、共十余行；未新增样式/布局/菜单/弹窗/抽屉；未触碰他人 owner 文件、金样、真实数据。
- 验证：python3 tests/run.py --test test_references.py → 全部 21 步通过（原 18 步 + 单元⑩B2/⑪B1/⑫B3；HTTP①～⑧ 原样通过）。；node --import ./tests/ts_hooks.mjs tests/dependency_guard.test.mjs → 22/22 项通过（新增 B1 前端预告阻断断言）。；node --import ./tests/ts_hooks.mjs tests/legacy_graph_bridge.test.mjs → 全部通过（新增 B1 图谱 domainDeleteNode 阻断+状态不变）；object_workspace 7/7；ont_list_unified 4/4。；python3 tests/run.py --test test_validation_split.py → 金样 97 样例 / 507 项断言全部通过，errors/warnings/items 逐字节等价（未改金样）。；cd frontend && npm run typecheck → exit=0（按要求未跑 npm run build）。
- 下一步：建议协调者（接口文档 owner）在 02 §2.2 补登记 B2 裸名/mg: 容错与 B3 槽位身份口径两句。；浏览器点击级复验、HTTP 组、build、提交按指令留给协调者。
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §11.2（B1/B2/B3）；文档/接口文档/02-本体区接口.md §2.2；workbench/references.py / frontend/src/ontology/editorModel.ts
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 角色F 复核记录更正（断言计数） · zcode · 已验证

时间：2026-09-20T07:00:14.126561+00:00；记录：`.collaboration/entries/000113-86ba9c34bbe2.json`

更正上一条交接（记录 000112）中的断言计数：tests/ui_protection_independent.test.mjs 实际为 60 项 check、全部通过（原文写 58，系笔误）；运行命令与结论不变，exit=0。其余结论（P03/P04/P05 行为、两项缺陷、E 既有测试回归）不受影响。

- 验证：grep -c '^通过：' 输出 60；node --import ./tests/ts_hooks.mjs tests/ui_protection_independent.test.mjs → exit=0

### 角色F：E/C/L 前端产物独立复核（P03/P04/P05） · zcode · 已验证

时间：2026-09-20T06:59:31.974362+00:00；记录：`.collaboration/entries/000112-4073fea0c300.json`

新增 tests/ui_protection_independent.test.mjs（真实 SFC 编译 + 真实模板 SSR + 受控 stub，58 项断言全过）：P03 换连接/换表保存后除身份三键外项目绑定逐字段不变（sources[].matchLeft/matchRight/table、属性、relations、说明、未知字段、未知 kind 来源对象）；未知 kind 只保留「移除」、无「修改」入口；P05 编排详情复核必重新取数（r-1→r-2 界面不再使用旧声明）、500→failed（「编排待读取」，引用保留、文案不含「不存在」）、404→missing、重试成功后恢复；refreshCatalogOf stale 返回 ok:false 且不写 catalogs；ConnectionManager 删除连接在 ok:false/reject/进行中都不提示成功且行保留。E 既有测试 object_sources/source_config_retention 全过（未修改）。仅新增本测试文件，未改生产代码，未跑 build、未访问真实数据/18765/外网。

- 验证：node --import ./tests/ts_hooks.mjs tests/ui_protection_independent.test.mjs → 全部通过（58 项）；node --import ./tests/ts_hooks.mjs tests/object_sources.test.mjs → 通过；source_config_retention.test.mjs → 全部通过；node --import ./tests/ts_hooks.mjs tests/connection_manager.test.mjs → 通过（C 自测，仅运行未修改）；隔离根临时目录 + WIZ_WORKBENCH_PORT=18997；未访问真实数据/18765/外网
- 下一步：缺陷①（低危·UI 提示）：ConnectionManager.referencesOf 未识别 inlineSql 属性来源连接（只查 kind=database/redis 与实现 connection），UI 判无引用并提示「已删除连接」，而服务端 projects.referenced_connection_ids 覆盖 inlineSql，project-save 会 422 REFERENCE_IN_USE 拒绝（数据仍受保护，但用户看到的是保存失败）。；缺陷②（中低·过严）：PropertySources.saveDraft 在编排读取失败时经 issuesOf 直接阻止保存，包括只改项目说明未改结构的情形；G1 只要求读取失败 fail-closed 阻断发布，未要求阻断草稿保存。；两项均未改生产代码，交 L 决定是否最小修复并补用例；最终验收仍归 Codex。
- 依据/文档：tests/ui_protection_independent.test.mjs；frontend/src/project/{ObjectSources,PropertySources,ConnectionManager}.vue；bindingModel.ts；workbench/projects.py:54 referenced_connection_ids / :157 save_boundary_issues；文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §11.2 G1

### 本体自动化构建管线范围确认 · codex · 已确认决定

时间：2026-09-20T06:40:44.084764+00:00；记录：`.collaboration/entries/000111-5e9d66e9fbf8.json`

用户进一步确定通过LLM多轮交互澄清生成范围，第一版仅支持生成新本体；仍为产品讨论，未授权实施。

- 决定：延续仅生成本体、集成工作台、多种混合业务物料和弱证据候选清单的既定边界。；范围不限制为一句话输入，采用LLM多轮对话逐步澄清。；第一版仅生成新本体，不追加或覆盖已有本体。
- 下一步：讨论物料辅助澄清、范围确认摘要及生成后人工裁剪的具体交互。

### 规则动作字段精简与Excel模板同步 · codex · 需求已交付

时间：2026-09-20T06:39:26.873462+00:00；记录：`.collaboration/entries/000110-0ca4a15cc80d.json`

交付需求说明、开发计划、执行指令及简洁表头新版空Excel模板；未修改正式业务代码和下载入口。

- 决定：规则名称/业务定义必填，规则内容选填；动作名称/业务定义必填，预期效果选填且沿用effect键。；不新增范围/例外/边界字段；旧规则output保留且只读展示，旧Excel业务效果别名兼容，双列冲突阻断。；本轮按用户指定交付三份文档和模板，无额外原型；新下载模板需与解析器同步上线，不恢复v2延期UI改造。
- 验证：四页模板逐页渲染检查通过；XLSX表头、空白区、冻结首行、隐藏列、数据验证与条件格式保留检查通过。；文档链接检查通过；未验证Excel原生下拉或正式工作台导入，未改业务代码。
- 下一步：其他harness按执行指令实施前后端校验、页面、导入兼容和模板入口，完成R01-R11验收。
- 依据/文档：文档/需求/20260920_规则动作字段精简与Excel模板同步/需求说明.md；文档/需求/20260920_规则动作字段精简与Excel模板同步/开发计划.md；文档/需求/20260920_规则动作字段精简与Excel模板同步/执行指令.md；文档/需求/20260920_规则动作字段精简与Excel模板同步/本体模型填写模板_规则动作精简.xlsx

### 角色A 本体保护验收（只读审计）：O01/O02 行为矩阵 + S1-S4 复验 · zcode · 已验证

时间：2026-09-20T06:16:09.594589+00:00；记录：`.collaboration/entries/000107-131f6db44a41.json`

只读审计：复验 S1-S4 通过（test_references 18 步、dependency_guard 21/21、legacy_graph_bridge 全过、object_workspace 7/7、ont_list_unified 4/4）；O01/O02 行为矩阵逐项取证，多数已满足。发现两处前后端一致性偏差（均低危 fail-closed、无真实数据写入）：① applicable_objects 后端拦、前端不预告；② 无 mg: 前缀的 domain/range 前端拦、后端放行。未改任何代码/测试，未做浏览器点击闭环。最终验收仍归 Codex。

- 验证：范围：只读；改动文件 0（git status 仅他人未跟踪报告文件）；未跑 http 组、未访问真实数据/外网。；S1 三类全量遍历基本成立（functions/actions/interfaces、签名槽位、properties/implementations、steps/property_bindings/历史 inputs）；S2 稳定键成立（改名放行、改目标阻断）；S3 旧 rules id 集成立；S4 returnTo 链路成立（App 分发 + 目标页返回入口 + openUsages 抽屉恢复）。；矩阵要点：确认指纹=编辑内容+原定义+引用集合（dependencyModel.ts:241-261）、取消零写（PropertyManager.vue:130）、409/失败后旧确认失效（:211-216）；转私有保留对象属性 ID 与内容（propertyModel.ts:39 实跑 @id 不变）；规则/动作移除关联不删定义；五类删除均阻断式无强制删除；图谱批量预检+快照回滚（legacyBridge.js:515-556）；后端 POST /api/save 只拦新引入悬空引用（model_routes.py:575-594）。；两项偏差复现：① applicable_objects 引用对象时前端 graphReferenceEntries=[]、objectDeleteCheck.blocked=false，后端 new_broken_references 阻断；② rdfs:domain 省前缀 o1 时前端命中引用，后端 ([] , True) 放行。
- 下一步：建议最小改动（未实施）：① editorModel.ts:44 或 dependencyModel 同步 applicable_objects（或明确后端更严为有意设计并登记接口文档）；② references.py 的 domain/range 判定加 mg: 前缀容错。；浏览器点击级闭环（确认弹窗取消/失效、批量删除、图谱删除）本轮未做，需隔离实例复核。；HEAD 已推进到 a72f4c3（仅新增 docs 一文件），代码与基线 82923e3 相同。
- 依据/文档：workbench/references.py:110-311；workbench/model_routes.py:575-594；frontend/src/ontology/dependencyModel.ts:100-261；editorModel.ts:22-91；frontend/src/ontology/PropertyManager.vue:89-218；frontend/src/ontology/legacyGraph/legacyBridge.js:317-600；tests/test_references.py；tests/dependency_guard.test.mjs

### 第二轮验收 S1–S4 修正（多 agent 并行） · zcode · 已实施，待验收

时间：2026-09-20T05:57:20.471047+00:00；记录：`.collaboration/entries/000106-25585c164560.json`

按验收意见第二轮复验修正 S1–S4（提交 d99367f）。S1 保存边界由仅契约签名扩为与前端对齐的三类全量遍历（含接口 properties/implementations、签名槽位 base、steps/property_bindings/历史 inputs），实测接口引用属性被删现 422 零写入；S2 比较键改稳定身份（来源 id+槽位 id+目标 id），既有失效改名放行、同文案不同目标仍阻断；S3 旧规则 id 集纠正，指标 rule_ref 有效旧引用不再误报；S4 新增 returnTo 来源上下文贯穿 navigate→App→六个目标页，共享库两类依赖行均带返回，回库打开原定义引用位置抽屉继续操作，规则/动作库补外部依赖「去处理」。接口文档 02 §2.2 与 README 先行登记。测试：test_references 18 步、run.py all 34/34、validation_golden 97 样例零误报、dependency_guard 21/21、前端回归全绿、typecheck+build 通过。按用户指示跳过自测验收阶段（未做浏览器点击级闭环）。

- 决定：保存边界检查范围以「前端会拦的后端也拦」为口径收口在 references.py，并在 02 §2.2 明确写出两项不在范围（接口实现完整性、项目映射 bindings）；悬空引用比较键只用稳定身份（来源记录 id + 引用字段/槽位 id + 目标 id）；业务名称仅用于展示文案；S4 返回上下文不新增页面/菜单：navigate focus 携带 returnTo，App 统一分发与清理，目标页可选 prop 渲染返回入口；共享库用 openUsages 标志恢复引用位置抽屉（editFocus 优先不破坏图谱编辑跳转）；规则/动作库去处理入口就地扩展（规则库在既有引用对象抽屉内、动作库在阻断提示下方），不自动替用户删除
- 验证：test_references.py 18 步全过：含 S1 原始复现（接口 properties 引用属性 → 删除 → 422 + 零写入 + revision 不变）与接口 implementations 同类覆盖、S2（改名放行 + 同文案不同目标阻断）、S3 三情形、动作/接口签名槽位与 base 引用 6 条精确断言；tests/run.py http 8/8、all 34/34；validation_golden 97 样例经新检查零误报；grep 确认生产调用方仅 model_routes.post_save；dependency_guard 21/21（新增 S4 源码级断言：五目标页返回带 openUsages、共享库 edit 优先、无入口依赖 disabled、三个库去处理主体无删除调用）；ont_list_unified 4/4、object_workspace 7/7、ontology_home 27、global_interaction 7/7、save_queue 22/22、editor_head_consistency 19、graph_toolbar_layout 8、business_rule_model、action_model 全过；typecheck+build 通过
- 下一步：Codex 复验 S1–S4（重点：S1 接口引用 HTTP 复现、S2 改名/同文案、S3 旧规则、S4 返回闭环）；未验证项（开发计划 §9.4）：浏览器点击级闭环按用户指示跳过本轮自测验收；_canon 仅容错 mg: 前缀；steps/property_bindings/历史 inputs 无稳定槽位 id；接口实现完整性与项目映射 bindings 不在保存边界内
- 依据/文档：文档/需求/20260920_本体建设维护与版本改版/验收意见_20260920.md；文档/需求/20260920_本体建设维护与版本改版/开发计划.md §9；workbench/references.py；tests/test_references.py；文档/接口文档/02-本体区接口.md §2.2

### v2详细开发计划与多Agent执行指令 · codex · 需求已交付

时间：2026-09-20T05:46:50.673446+00:00；记录：`.collaboration/entries/000105-0d3408a421ac.json`

已补齐开发计划和可独立交给其他harness的执行指令，含L协调者+A至F角色、单写者文件归属、G0/G1闸门、分波次并行、逐项任务和26项必测矩阵。只交付文档，未实施业务代码。

- 决定：范围仍为v2功能保护，所有新增样式/交互及其他历史延期项不恢复。；统一基线、接口、幂等和失效中间态先冻结；project_validation、App和项目写入热点分别单owner。；修正项目发布幂等现状措辞：请求receipt设施不等于项目发布已接入，实施先核验，必要时补齐。；用户要求并行规划，使用两个只读agent核对后端和前端/测试；复审补入App内formSave归D、客户端幂等key、损坏目录和实现失效边界。
- 验证：三份Markdown相对链接、计划列出现有测试路径、T01至T26/F01至F10覆盖检查通过。；两个只读agent复审完成；未运行正式业务测试或构建，不将计划记作实施。
- 下一步：执行harness按指令先核对最新基线与他人未提交修改，冻结契约后按文件owner并行，实施证据追加开发计划§11。
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md；文档/需求/20260920_本体与项目统一维护体验改版/执行指令.md；文档/需求/20260920_本体与项目统一维护体验改版/需求说明.md

### 三修正独立子代理验收结论（提交 806a63c 归档开发计划 §5.10） · zcode · 已验证

时间：2026-09-20T05:36:05.193904+00:00；记录：`.collaboration/entries/000104-bd4846ece477.json`

本会话三项反馈修复（eab7545）经独立子代理并行复验，三项全部通过：① 规则编辑表单「← 返回图谱」@(271,99)+「关闭」@(382,99)，返回后原节点仍选中且 zoom/pan 不变；② 对象/私有属性/共享属性/规则/动作五类编辑页头部坐标逐像素一致、计算样式相同，非图谱来源为单按钮「返回规则列表/动作列表」无回归；③ 1440 与 1280 下选中节点（1跳/2跳 出现）后 inspector-max.right − tool-row.right = 0、scrollWidth == clientWidth、无祖先裁剪，激活邻域与最大化切换后复测仍为 0。验收方式：子代理环境无 IAB 控制权，改用本机 Chrome headless + CDP，以节点真实屏幕坐标派发等价 MouseEvent（已核对 cytoscape 无 isTrusted 门槛、命中双重确认）；固定 bundle 测量、前后哈希一致；全程只读零写入。

- 决定：接受子代理结论：三项修正独立复验通过，不再迭代；窄屏 ≤1100 单行横向滚动为既定取舍（换行会把画布挤到 0 高），登记为已知行为而非缺陷；App.vue TDZ（第 171 行 watch 引用第 181 行声明的 flowState）确属既有缺陷但与本轮三修复无因果（自 eab7545^ 起存在），按用户指示由其他 agent 修复，本轮不处理
- 验证：① 双击 lg:rule:rule_soc → .editor-head 为表单卡首元素，点返回后 hash 回 #objects、cy.$(':selected')=[lg:rule:rule_soc]、zoom=0.693/pan={584.59,308} 与进入前一致；清本地偏好后全新画布复做结果相同；② 五页头部实测：(271,99) 101×36 与 (382,99) 57×36，计算样式 display:flex/gap:10px/margin-bottom:14px、按钮 font-size:14px/padding:6px 13px/border-radius:6px 五页一致；非图谱来源 hasBackToGraph=false；③ 1440: max-right 1403 = row-right 1403（差 0），scrollWidth/clientWidth = 1050/1050；1280: 1243/1243、890/890；clippedByAncestor=null；1跳/2跳 激活后半透明节点 15→7 且复测仍 0；最大化态与还原态同样 0 裁切；隔离实例夹具 revision_token=r-b31308b051254255bdf65d13c95d99bb / generation=4 / snapshot_seq=4 与验证前一致（零写入）；真实 ontology/ 未触碰；18765 仅 GET 探活
- 下一步：用户在 18765 强刷（Cmd+Shift+R）后按三项验收：规则/动作编辑表单左上角返回图谱、五类编辑页头部一致、选中节点后工具栏最大化不被裁切；子代理建议的加强项（可选）：在带真实鼠标输入的浏览器里对双击跳转做一次人手点按复核，彻底排除合成事件的歧义；工作区有 Codex 在途改动（App.vue/SharedLibrary/FunctionManager/ProjectBinding/DefinitionManager + 后端 references.py，即 S4 返回来源与保存边界修正）：本轮修改已提交且未被其覆盖（EditorHead 引用完好），互不冲突；若其落盘后重新构建，建议按 §5.10 判据（同五类坐标 (271,99)/(382,99)、max-right 差值 0）重跑一次
- 依据/文档：文档/需求/20260919_图谱编辑器源码整体复用/开发计划.md §5.9（实施）与 §5.10（独立验收结论）；git eab7545（三项修复）、806a63c（验收结论归档）；tests/editor_head_consistency.test.mjs（19 项）、tests/graph_toolbar_layout.test.mjs（8 项）

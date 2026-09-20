# Codex / zcode 共享上下文

上下文版本：`81a874dab7a9ff69`

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

### 执行 20260920_本体与项目统一维护体验改版 执行指令（v2 功能保护，多Agent并行）——实施完成交接 · zcode · 已实施，待验收

时间：2026-09-20T07:47:40.655262+00:00；记录：`.collaboration/entries/000120-b48bbedf7bad.json`

v2 功能保护七个工作包全部完成并提交：L 集成（d2cc6b9）、C 目录凭据（53f46f9）、A 本体三项复验修正（cc1b833）、E 映射保留+两处 P0（9d57276）、D 前端基线（8e2f7c1）、B 领域校验（4a1dcb5）。测试：run.py all 39/39、金样重生成回放 97/507、前端 node 全套通过、F 独立 QA 三套（135+47+60 项）全过、npm run build 通过；隔离实例浏览器验收 5 项通过（UI 发布 v2 与幂等回放、删被引用连接阻断、损坏目录发布阻断等）。开发计划 §11.2 冻结记录、§11.3 进度、§11.4 F01–F10 逐项结论与未验证项已终稿。

- 决定：幂等指纹只含内容（不含 revision，回放优先于 CAS）；保存边界 422 REFERENCE_IN_USE 覆盖四类引用形态、同批移除放行；依赖重验快照=编排 head+目录指纹代际。；F 独立 QA 两个 P0 已修：损坏目录阻断校验与发布（路由兜底+B 形参双路径）、目录存储失败 GET/POST 同 503。；F 两个 P2 观察项：数组顺序视为内容差异（已登记 03 §2.3）；动作绑定 unreadable 文案仍沿用「不存在」（行为正确 fail-closed，记为待跟进）。；B 的 P1-5 后半（编排自身 check_flow 阻断）因会打挂对抗夹具而回滚，列为待办；金样按 B 预期条目重生成并回放通过。；既有失败 mapping_forms.test.mjs（四提交复现、范围外，P02 延期）不复写；现行编排绑定路径由 flow_editor_bind 覆盖。
- 验证：后端：python3 tests/run.py all → 39/39（quick 3/3、http 10/10、unit 28/28）；金样 97 样例 507 断言；test_publish_guards 14 步；test_references 21 步；test_project_flow_source 38 步；test_upgrade_impact 7 项。；前端：22 个 node 测试文件全部通过；typecheck 0；npm run build 通过。；F 独立 QA：对抗性 135 项、目录语义 47 项、UI 保护 60 项全过；P0 修复后加回归步骤 m/n 锁定。；浏览器验收（隔离 18871，验收后实例与数据已清理）：概览「1 项待处理」；删被内联 SQL 引用连接阻断且提示精确；校验阻断→发布禁用；修正后 UI 发布 v2 且同 requestId 回放；真实重载与服务端一致。
- 下一步：Codex 复验 F01–F10 证据，并决定 P1-5/B-4 两个待办是否本期补齐。；待办（非本期阻塞）：编排自身 check_flow 阻断启用+F 夹具同步；动作绑定 unreadable 文案统一；App.vue TDZ 由他人修复。
- 依据/文档：文档/接口文档/02 §2.2、03 §2.1–§2.4/§3.3、01 §5.1、05 §1.2、README 变更记录；文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §11.2–§11.4；提交：d2cc6b9、53f46f9、cc1b833、9d57276、8e2f7c1、4a1dcb5

### 角色B领域校验（重派）：依赖三态/目录降级形参/属性键失效/影响匹配纯函数 · zcode · 已实施，待验收

时间：2026-09-20T07:39:53.878228+00:00；记录：`.collaboration/entries/000119-facb07ab5040.json`

P0 三项已落盘。① kind=flow 改消费 flows.dependency_state：unreadable→error 阻断（文案含「读取失败（reason）」与 flowId），missing 沿用「不存在或已删除」，软删除按 missing，found 用其 state；_check_action_bindings 的 flows.listing 改 fail-closed（StorageUnavailable 原样抛给路由 503，其他异常逐行报错，不再跳过存在性检查）。② validate_project 新增第三形参 degraded_catalogs=None（None 时逐字节不变）；传入时逐条「数据连接 {名称}：目录缓存读取失败（缓存内容损坏）…」error + connection/invalid items，与 project_routes 兜底文案结构一致，路由 signature 探测会自动切到本实现。③ 属性键在引用版本中不存在→error「引用的版本中不存在此属性」，原配置不动。P1-4 field/related 有目录但字段不存在→error（无目录不报）。P1-5「编排自身 check_flow error 阻断」试做后回滚（会误伤 F 的对抗性夹具，其编排缺 name），软删除部分已保留；是否启用交协调者。P1-6 新增 workbench/project_impact.py 纯函数（稳定 id 精确匹配、sharedPropertyId/valueTypeId 继承、未绑定不误报），未改 projects.py。新增 tests/test_upgrade_impact.py 7 项全过。

- 决定：degraded_catalogs 默认 None 保证兼容入口 projects.validate_project 两参调用与金样逐字节不变；文案与 items 与 project_routes._validate_with_degraded 完全一致。；属性键不存在的 error 加在属性循环最前，items.issues 顺序稳定（新 error 在前），便于金样回放。；field/related 字段目录核对仅在该表字段目录可读（catalog_fields 非 None）时报 error；目录缺失保持现状。；P1-5 编排自身 check_flow error 阻断属性绑定回滚：本轮 F 的对抗夹具编排缺 name 即 check_flow error，会使其可发布夹具失去可发布性；优先保证 P0 与既有 135 项对抗断言不回退。；project_impact.binding_impacts 兼容 JSON-LD 与 JSON schema 两形态，只做纯匹配，不改变 projects.upgrade_check 输出结构。
- 验证：tests/run.py --test test_project_flow_source.py → 通过（38 步）：unreadable→error 含 flowId 且不含「不存在」、found 正常、软删除→不存在、动作绑定 listing 失败→error、StorageUnavailable→原样抛出、恢复后通过。；tests/run.py --test test_property_sources.py → 全部通过（f1 属性键不存在 block、f2 字段目录核对、f3 degraded_catalogs 生效且 None 逐字节一致）。；tests/run.py --test test_upgrade_impact.py → 7 项全过（相似 apiName 不误报、共享/值类型继承命中、未绑定不产生影响、链接相似名不误报、契约定位、schema 形态兼容）。；tests/run.py --test test_publish_guards_adversarial.py → 通过 135 项/不符 0（回滚 check_flow 阻断后无回退）。；tests/run.py unit → 28/29；唯一失败 test_validation_split.py 为金样预期失配（未改金样，变动清单见报告 C 节：8 个样例、约 95 error 槽位/43 item 槽位）。
- 下一步：协调者按报告 C 节预期变动重新生成金样（tests/make_validation_golden.py）并回放 test_validation_split.py。；接线 project_impact.binding_impacts 到 projects.upgrade_check，替换现行子串/endswith 匹配，返回结构不变。；决定 P1-5 是否启用「编排自身 check_flow error 阻断属性绑定」；若启用需同步调整 tests/test_publish_guards_adversarial.py 夹具（补 name/绑定）。；project_routes._validate_with_degraded 的 signature 探测现已切到新实现，兜底分支可在集成阶段清理。
- 依据/文档：workbench/project_validation.py（validate_project 第三形参、_append_degraded_catalog_issues、flow 三态、field 目录核对、属性键 error、动作绑定 fail-closed）；workbench/project_impact.py（新增纯函数模块）、tests/test_upgrade_impact.py（新增）；tests/test_project_flow_source.py（38 步）、tests/test_property_sources.py（+3 组用例）；文档/接口文档/03-项目区接口.md §2.2（依赖读取失败语义）；文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §11.2（G1 冻结）

### 从物料自动构建本体开发计划与执行指令v1 · codex · 需求已交付

时间：2026-09-20T07:36:57.831434+00:00；记录：`.collaboration/entries/000118-be92f9805b6d.json`

按用户要求补齐开发计划和可独立交给其他harness的执行指令，同步需求状态与原型文档导航；未实施业务代码。

- 决定：四件套齐全，现有需求范围与原型主流程不变。；P0-P7分阶段覆盖真实解析/多轮LLM/候选评审/合并再生成/原子新建；T01-T25验证矩阵映射G01-G16。；保留全局2 MB请求上限，建议JSON分片上传；后台owner显式绑定、安全LLM日志、任务和新本体交付同事务。；未定OCR/外部服务/技术栈扩展与规模承诺不自动扩大；工程默认与降级必须明示。
- 验证：四份文件相对链接、P0-P7阶段、T01-T25测试矩阵及现有测试路径检查通过。；原型只增加计划与指令链接，JS语法通过；git diff --check通过。未运行正式业务测试或构建。
- 下一步：执行工具获用户实施指令后，按必读顺序和P0协议核对开始实施，结果追加开发计划§11。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/开发计划.md；文档/需求/20260920_从物料自动构建本体/执行指令.md

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

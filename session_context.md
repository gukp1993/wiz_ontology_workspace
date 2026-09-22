# Codex / zcode 共享上下文

上下文版本：`72a89e28f6213b09`

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

### assist-fill-production T5 本体接入（O1/O3/O4/O5，分支 codex/assist-fill-production） · zcode · 已实施，待验收

时间：2026-09-22T11:30:32.267073+00:00；记录：`.collaboration/entries/000251-f63ffe52c58a.json`

整表自动填写 T5 交付：ontologyBindings.ts 重写为 autofill/1 对接面（formId/contractInfo 取 formContracts.gen 生成物指纹、codecs 空=identity 直写、applyDraft 合并零丢失、快照钩子=撤销单元起点），新增共享 createRoundMirror 宿主状态条镜像（已填 N 项/另有 M 项待补充/逐字段旧值→新值/手改禁撤销）与宿主 undoRound；workflowBindings.ts 镜像接入 rule/action；ObjectWorkspace 对象/链接编辑器、BusinessRuleLibrary、ActionLibrary 页头次要按钮「✦ 自动填写」（aria-expanded/aria-haspopup+trigger-id 反向接线）+表单上方状态条+手改通知+api 包装 observeFill；默认无 AI 区（A01），回填绝不触发表单保存，旧建议卡/勾选/采纳路径全部移除。binding 按 editor/draft 对象缓存保证引用稳定（镜像挂 binding 上，Vue3.5 computed 无订阅者重求值保不住恒定）。

- 决定：状态条为 binding 镜像而非引擎直读：T3 AssistPanel 只 expose 动作与 collapsed，不暴露 statusBarText/canUndo/roundSummary/undoRound，面板禁改；引擎 snapshot()/applyDraft 调用点=撤销单元边界与落回点，据此镜像；引擎仍是唯一权威，镜像只服务渲染与宿主撤销按钮；测试断言镜像与引擎 statusBarText 口径一致；待补充计数（另有 M 项）由组件 api 包装在 generate 返回后回传 binding.observeFill，按 formId+target+契约指纹过滤（切目标迟到响应/契约不符不计）；beginRound 不清 pendingCount（observeFill 先于引擎处理响应回传，清零会抹掉本响应待补数），empty/撤销时清零；宿主 undoRound 恢复本轮起点后调用 notifyDraftChanged 作废在途生成（撤销也是草稿变更 §4.5）；手改后快照作废，续轮落回不再产生可撤销快照（不覆盖用户改动）；binding 按 editor/draft 对象身份缓存，换编辑目标即换 binding，镜像随之重置
- 验证：node --import ./tests/ts_hooks.mjs tests/assist_object_workspace.test.mjs → 14/14 通过（对接面/A01/上下文体/直接回填/撤销/手改禁撤销/续轮整轮撤销/切目标作废/契约指纹与 empty 零写入/链接同链路/显式保存不受影响）；node --import ./tests/ts_hooks.mjs tests/assist_workflow.test.mjs → 12/12 通过（rule/action 对接面、DEF-02 新建动作 targetId 空串、回填零保存零 changed、历史 output 不受影响、切目标与关闭）；cd frontend && npx vue-tsc --noEmit → 0 错误；npx eslint 五个改动 src 文件 → 0 违规；回归：assist_panel 22/22、test_autofill_state 69 项 0 失败、object_workspace 7/7、editor_head_consistency 19、list_controls 4/4、ont_list_unified 4/4、dependency_guard 22/22
- 下一步：浏览器实链路归 T10：SSR 不覆盖模板 ref 通道（二次点击收起/展开、notify 经 ref）、Esc/遮罩/焦点圈闭、aria-expanded 随 done 自动收起回落；已知限制：cancel 后迟到响应被引擎代际丢弃，但已计入的待补充数短暂残留（手动改/新响应/撤销纠正）；面板未暴露 cancel 事件；宿主 undoRound 后引擎待补问题卡保留（引擎无对外复位接口），续答按已恢复草稿校验；字段定位（查看修改聚焦）未接：共享 Field 无锚点，不硬造；aria-controls 指向的抽屉 id 需 T3 给 AssistPanel 加 id prop 后补全（当前 trigger-id 反向接线，与 T6 同思路）；未做 git 提交（任务指令禁止），提交由协调者安排
- 依据/文档：frontend/src/assist/ontologyBindings.ts；frontend/src/assist/workflowBindings.ts；frontend/src/ontology/ObjectWorkspace.vue；frontend/src/ontology/BusinessRuleLibrary.vue 与 ActionLibrary.vue；tests/assist_object_workspace.test.mjs 与 tests/assist_workflow.test.mjs；文档/接口文档/04-编排与LLM接口.md §6.6

### assist-fill-production T6 属性接入（O2 私有+共享属性，分支 codex/assist-fill-production） · zcode · 已实施，待验收

时间：2026-09-22T11:17:59.928416+00:00；记录：`.collaboration/entries/000250-3e428c824373.json`

三文件交付：propertyBinding.ts 重写为 autofill/1 对接面（契约业务枚举快照、五 codec、applyDraft 落位、typeCore 原子组、formatting 依赖序、只读 refusals）；PropertyManager.vue 页头「✦ 自动填写」+宿主状态条（已填 N 项/撤销/查看修改）+手改通知+只读 api 包装转 unresolved，旧建议卡 UI 移除；测试重写 74 项全过，vue-tsc 0 错误。

- 决定：codec 只做枚举/结构校验+写扁平副本；真正落位统一在 applyDraft（唯一同时可见 dataType+obsType+formatting，保证原子组与依赖序）；formatting kind 与生效数据类型一致性放 applyDraft 拒绝（codec 时点看不到同轮 dataType 操作）；样式白名单/历史样式归后端 blocked+保存校验兜底；离开 timeSeries 须带显式 obsType 空串标记；缺清空标记的不完整离开组按矛盾组整组拒绝，不自动补清；状态条 N 按宿主 applyDraft 前后投影 diff 去重（含类型联动清理的 formatting，比引擎 copy 级计数如实）；引擎 snapshot() 调用点=新撤销单元边界，宿主据此对齐轮次；只读保护：binding 层 writable()+refusals 拒写为主；PM api 包装请求期只读时把 operations 转 unresolved 面板展示
- 验证：tests/assist_property_manager.test.mjs 重写 74/74 通过（原子组成组/无半组/独立合法内容照填/撤销单元/续轮/手改禁撤销/保存计数不变/只读拒绝/非 assist 回归）；npx vue-tsc --noEmit 0 错误；assist_panel 22/22、test_autofill_state 68/68、dependency_guard 22/22、editor_head_consistency 19、object_workspace 7/7、global_interaction 7/7、undo_history 7/7、formatting_options 全过；npm run lint：本任务文件 0 违规（既有 6 处 no-unused-expressions 在 T7/T8 归属文件）
- 下一步：assist_object_workspace.test.mjs 7/14 失败：该套件把 PropertyManager 桩为 render:null 且不引用 propertyBinding，失败在对象/链接编辑器流程（T5 归属文件），与 T6 无执行路径交集，待 T5 处理；AssistPanel 无 id prop，按钮 aria-controls 指向的 pm-assist-drawer 暂悬空（已用 trigger-id 反向接线），T3 加 id 支持后补全；浏览器验收与 Codex 独立验收待排；未做 git 提交（任务指令禁止 git 命令，提交由协调者安排）
- 依据/文档：frontend/src/assist/propertyBinding.ts；frontend/src/ontology/PropertyManager.vue；tests/assist_property_manager.test.mjs；文档/接口文档/04-编排与LLM接口.md §6.6
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 整表自动填写 T4（autofill/1 后端串接）@worktree/assist-fill-production · zcode · 已实施，待验收

时间：2026-09-22T11:10:38.864812+00:00；记录：`.collaboration/entries/000249-7b5397dda201.json`

fill 分支完成：protocol=2 门禁、内存会话（TTL30min/LRU200，绑定 user+space+target+formId+digest）、答案归属校验与 unsure→unresolved、operations 引用核验、autofill/1 envelope；令牌签入契约 fv/fd（续轮不匹配 409）；新增 FILL_SYSTEM_PROMPT 与 build_fill_user_payload（敏感字段不出网）；FormContract 组节点 atomicGroup 展开为叶子全集；旧 fill 无 protocol→400，check/explain 零改动。新增 tests/test_autofill_http.py（13 组通过）；test_assist_api 7 处 fill 用例最小迁移、test_assist_context 1 处令牌键集断言补 fv/fd。待 Codex 验收。

- 决定：会话为进程内存 OrderedDict（不落库），绑定键含 projectId（targetId 按项目隔离）；引用核验 provider 名→候选集映射收口在 assist_service._PROVIDER_REF_TYPES；identityTableFields 按草稿所选表目录字段核对；上下文未装配的提供方 fail-closed（候选不可用→unresolved）；答案先只读校验、模型成功后才提交会话变更（模型 502 不消费用户答案）；未答问题归属 roundId；valid_question_ids 取会话全部已签发 id（basis question 核验+换发避让）
- 验证：python3 tests/test_autofill_http.py 全部通过（13 组，端口 18931/18932 自管自停）；python3 tests/run.py all 62/62 通过（含 test_assist_api/context/schema、test_autofill_contracts/patch/http）；~/Library/Python/3.9/bin/ruff check 交付 6 文件全部通过
- 下一步：待 Codex 独立验收（不合并 main）；真实提供方联调另记
- 依据/文档：文档/接口文档/04-编排与LLM接口.md §6；文档/需求/20260922_整表自动填写交互/需求说明.md；workbench/assist_service.py；workbench/assist_schema.py；tests/test_autofill_http.py

### 整表自动填写 T3（前端状态机与抽屉） · zcode · 已实施，待验收

时间：2026-09-22T10:26:47.900550+00:00；记录：`.collaboration/entries/000248-9391473b5096.json`

新增 formAutofill.ts 通用填写引擎/宿主状态机：八态流转、请求代际计数（手改/切目标/关闭/重开作废在途）、autofill/1 会话 sessionId/roundId 透传、续轮握手（草稿漂移即先重取 context 再 generate）、整轮撤销单元（快照恢复/手改禁撤销）、applyOperations 契约点路径写入+codec 回调（identity 兜底）+applyDraft 整稿通道、绝不触达宿主保存通道。重写 useAssistPanel.ts 为新交互门面并保留 AssistApi/AssistHostBinding 导出名；AssistPanel.vue 重写为 420px 右侧抽屉（窄屏遮罩/Esc/焦点管理/补问卡/empty 文案/更多帮助只读）；types.ts 扩展 autofill/1 类型。旧勾选/建议卡交互移除。tests/test_autofill_state.mjs 新增 68 项、tests/assist_panel.test.mjs 重写 22 项，均绿；vue-tsc 0 错误。

- 决定：撤销快照走 host.snapshot()/restore() 对称通道（draft() 可能是白名单投影，直接恢复会丢未投影字段）；写回优先 binding.applyDraft，无则退回旧 apply(顶层变更值) 过渡；续轮握手实现为生成前草稿漂移检测：漂移即先 assist-context 再 assist-generate，与 04 §6.2 等价；失败的生成不清上一轮撤销单元；statusBarText（已填 N 项/另有 M 项待补充，M=questions+unresolved）交宿主渲染状态条；面板内仅抽屉提示行；面板 done 自动收起不 emit close，仅用户主动关闭才 emit；G2/G3 建议保持面板挂载经 ref.toggle()/collapsed 接线；check/explain 收进 runHelp 次要入口，沿用旧响应结构只读渲染，无任何写入
- 验证：node --import ./tests/ts_hooks.mjs --test tests/test_autofill_state.mjs → 68/68 通过 exit=0；node --import ./tests/ts_hooks.mjs tests/assist_panel.test.mjs → 22/22 通过 exit=0；cd frontend && npx vue-tsc --noEmit → 0 错误（含未改动的宿主组件与 6 个 binding 适配器）；npx eslint src/assist 四个文件 → 0 错误；未跑 npm run build、未起服务（任务边界）
- 下一步：旧套件待 T5–T8 重写（可加载，失败均为旧勾选断言）：assist_object_workspace(2/9)、assist_property_manager(旧checked API崩)、assist_property_sources(5/12)、assist_identity_link(8/16)、assist_action_bindings(崩)、assist_workflow(2/11)；object_sources/mapping_forms/source_config_retention/ui_protection_independent 实测仍绿；T1 落地后各适配器注入 codecs/applyDraft/contractInfo；真实 codec 归 G2/G3；交付未提交（任务规定不执行 git），待协调者审阅提交
- 依据/文档：frontend/src/assist/formAutofill.ts（引擎）；frontend/src/assist/useAssistPanel.ts（门面）；frontend/src/assist/AssistPanel.vue（抽屉）；tests/test_autofill_state.mjs / tests/assist_panel.test.mjs；文档/接口文档/04-编排与LLM接口.md §6；文档/需求/20260922_整表自动填写交互/
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 整表自动填写T1表单契约（assist-fill-production） · zcode · 已实施，待验收

时间：2026-09-22T10:25:54.601255+00:00；记录：`.collaboration/entries/000247-e08943f14e6f.json`

T1 交付：contracts/forms/ 10 份契约（propertySource 含 4 个 draft.kind variants）；workbench/assist_forms.py loader（严格语法校验、canonical SHA-256 digest、查询 API、check_consistency、T2 冻结的 FormContract 适配类）；workbench/assist_forms_gen.py 确定性生成 formContracts.gen.ts（生成物入库）；tests/test_autofill_contracts.py 423 项断言全过。未改 assist_fields/schema/routes/server 与 frontend/src/assist 其余文件。

- 决定：契约字段 id 与 assist_fields 注册表键一一对应（点路径即契约路径）；property 的 dataType+obsType 为扁平 enum+atomicGroup typeCore，非嵌套组；dataType 取业务名枚举（六基础类型+timeSeries 特例，对齐 assist_schema._select_allowed），JSON-LD 转换交 codec dataTypeTransform；formatting 为 codec 托管组（group+formattingCodec，无内嵌 fields），requires dataType；可选说明类字段 nullable+clearable，其余不可清空；新增注册标识：codec 6 个（dataTypeTransform/formattingCodec/lookupMatchRows/redisKeyParams/flowInputBindings/actionParamRows）与 refProviders 候选提供方 13 个（见契约文件）；按 T2 assist_ops 冻结的 FormContract 接口补适配（规范化 field_def、list_def 归一、schema_version/digest 属性），T2 可由 FixtureContract 切到真 loader
- 验证：python3 tests/test_autofill_contracts.py 退出码 0：423 项断言全过（加载+digest 幂等、生成器两次同字节、漂移检测、注册表双向覆盖 13 场景、语法拒绝、sensitive 边界、FormContract）；python3 -m workbench.assist_forms_gen 连续两次运行字节相同；formContracts.gen.ts 过 vue-tsc 0 错误（剩余 4 错属 T3 在改文件）；ruff check 三个新 py 文件全过；workbench/tests 全量仅剩他人文件既有 5 错
- 下一步：T2 接 assist_ops 到 assist_forms.FormContract（propertySource 需传 draft_kind）；T4 按 refProviders 提供方名装配候选集；T3 消费 formContracts.gen.ts；协调者统一提交；本记录不构成验收
- 依据/文档：contracts/forms/ 全部 10 份契约；workbench/assist_forms.py；workbench/assist_forms_gen.py；frontend/src/assist/formContracts.gen.ts；tests/test_autofill_contracts.py
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 20260922_整表自动填写交互-T2操作协议校验（assist-fill-production） · zcode · 已实施，待验收

时间：2026-09-22T10:13:53.158447+00:00；记录：`.collaboration/entries/000246-92319e2316d5.json`

T2 交付：新增 workbench/assist_ops.py（受限 operations 校验器，模块 docstring 冻结 FormContract loader 接口约定）；assist_schema.py 仅增不改地新增 parse_fill_output（autofill/1 解析、questions id 服务端换发、模型 unresolved 契约过滤、MAX_UNRESOLVED=12）；新增 tests/fixtures/autofill_contract_fixture.json（合成契约兼作 T1 loader 接口兼容样例）与 tests/test_autofill_patch.py（40 项）。未动 assist_fields/assist_context/assist_service/assist_routes/server.py/前端；按指令未执行 git 命令，提交留协调者。

- 决定：同字段冲突落位：set/clear 按字段路径判定；行操作按行标识判定（同 localId 的 append、同 rowId 的 update/remove），同列表多条 row.append 合法（04 §6.3 服务端按内容去重即预期多条）；set 的 value=null 一律拒：置空必须走独立 clear（需求 §4.4，防绕过 nullable+clearable+basis 授权）；按 04 §6.1 权限语义实现 ai.fillable=false 与 ai.sensitive=true 字段拒写；结构级 502 口径：operations 非数组/元素非对象/未知 op/op 多余键/超12条；basis 形态违规按单操作无效转 unresolved；问题 id 换发 q_<n> 避让 valid_question_ids 防串号；响应级 unresolved 合并截断（≤12）归 T4 组装时执行
- 验证：python3 tests/test_autofill_patch.py → 全部通过（40 项）；python3 tests/test_assist_schema.py → 全部通过（57 项，旧 parse_model_output 行为未动）；ruff check 三个 T2 文件 → All checks passed；全仓 ruff 另有 5 处既有报错，均在 test_assist_api/test_assist_context/test_assist_schema（本轮未改动，非 T2 引入）
- 下一步：T1 对齐（重要）：assist_forms.py 已并行落地为模块函数接口（field_def(form_id,path) 抛 ContractError、list_def 返回原始 item、atomic_groups 登记组节点自身路径）——与 assist_ops docstring 冻结对象接口有 4 处差异；atomic_groups 若登记组节点路径而非组内叶子路径，assist_ops 会把该组永远判不完整，T9 集成须建适配对象（绑 form_id+draft_kind、ContractError→KeyError、归一 list item）或协调改 T1；T4：组装响应 unresolved 时按 MAX_UNRESOLVED=12 合并截断（invalid_operations＋模型自报 unresolved）；T9：把测试内 FixtureContract stub 切换为 assist_forms 真实 loader 复跑同批用例；协调者统一处理本轮 git 提交
- 依据/文档：workbench/assist_ops.py；workbench/assist_schema.py；tests/test_autofill_patch.py；tests/fixtures/autofill_contract_fixture.json；文档/接口文档/04-编排与LLM接口.md §6

### 整表自动填写需求四件套交付 · codex · 需求已交付

时间：2026-09-22T08:59:33.062850+00:00；记录：`.collaboration/entries/000245-ab492bee9867.json`

交付需求说明、开发计划、执行指令及已有两页原型。覆盖11场景、表单优先按需侧栏、自动填草稿、补问撤销及共享契约。未修改业务代码。

- 验证：核对assist-fill-production@544f6c8及现有登记；独立子agent只读评审风险已纳入。；最新数据规则与旧数据根无损迁移及真实副本保留要求已写入。
- 下一步：交harness在已有授权worktree实施，自测提交后独立验收，不合并main。
- 依据/文档：文档/需求/20260922_整表自动填写交互/需求说明.md；文档/需求/20260922_整表自动填写交互/开发计划.md；文档/需求/20260922_整表自动填写交互/执行指令.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 样式与交互统一（codex/ui_fix）集成合并与环境清理 · codex · 已验证

时间：2026-09-22T08:51:50.813130+00:00；记录：`.collaboration/entries/000244-0a06292e9445.json`

按用户指令"合并到主干，然后删除当前worktree"完成集成。main 原为脏（他人 63 条未入库交接），经用户选择"先收纳再合并"：65bbe34 只收 .collaboration/entries/000174~000240 + render 汇总，不改写他人记录与序号。随后 main 又被 d08b96b 前进一条（000241），故不再是祖先关系，按三方合并出 d0d60b0（父 65bbe34 + 被验收树 064cf10），合入 26 提交/74 文件。唯一冲突是生成文件 session_context.md，按约定用 context.py render 重建、未手工拼接摘要。组合校验在合并后的 main 工作树执行并通过。数据副本处置经用户明确确认"随 worktree 一并删除"：分支内真实数据快照库、根密钥副本、验收资产与预0004回退产物一并销毁（不可逆）。按"本轮不更新，只合代码"未构建、未重启 18765，主工作台运行态仍是合并前 dist。

- 决定：main 脏不 stash/reset：改为先把他人 in-flight 交接单独收纳为 docs(collaboration) 提交，再合并，保持记录原始内容与序号。；唯一冲突 session_context.md 属生成物，用 render 重建解决；不手工合并、不保留冲突标记。；worktree/ui_fix 数据副本（含真实数据快照+根密钥）按用户确认随工作树删除；其他两个 worktree、main 库、18765/18881/18912 不在范围内。；刻意不在 main 跑 vite build：18765 直接托管 frontend/dist，重建等于未授权改用户在用界面。前端校验只用无副作用的 vue-tsc --noEmit 与 eslint。
- 验证：git diff 064cf10 -- frontend workbench tests 文档 DESIGN.md AGENTS.md 无输出：合并树业务代码/测试/文档与被浏览器逐项复测通过的开发树逐字节一致，差异仅共享上下文交接。；main 上 vue-tsc --noEmit 无输出；eslint src 0 问题；python3 tests/run.py quick 通过 3/3；unit 通过 45/45（含 test_save_iteration 8 步）。；package.json/package-lock/requirements 本次合并无变化，故复用 main 现有环境校验成立。；未覆盖：main 未执行 vite build、未重启 18765，因此无合并后的浏览器实测；运行态界面仍是被合并前的旧 bundle。
- 下一步：待用户授权后 ./start.sh rebuild（构建成功才切服务）并重启主工作台，再做浏览器抽验；代码回退不等于数据回退。；序号 000187/000188 在两工作树各自计数下重号（文件名 hash 不同、无 Git 冲突），如需澄清按既有约定追加纠正记录，不改写历史。
- 依据/文档：提交：收纳 65bbe34；合并 d0d60b0（父 65bbe34 + 064cf10）；合并前 main 基线 578acd7→d08b96b。；实施与复测记录：文档/需求/20260921_样式与交互统一/开发计划.md §9.13（第八轮 6 缺陷、12 行复测值表、未覆盖项）。；设计契约：DESIGN.md（时间唯一出口 shared/format.ts、fixed 表 min-width + overflow:auto 约定、空态需区分"未选择"与"一个也没有"）。
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

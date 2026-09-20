# Codex / zcode 共享上下文

上下文版本：`55cb3ba859f9adf4`

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

### 验收意见 R1–R5 修正与保存边界（本体建设维护 11～13） · zcode · 已实施，待验收

时间：2026-09-20T05:10:53.774463+00:00；记录：`.collaboration/entries/000101-a0122fabe37f.json`

按验收意见_20260920.md 修正 5 项并补保存边界（提交 dc77551）。R1 图谱私有属性/对象链接删除补引用检查；R2 批量删除改全量预检+归属边去重+silent 一次性应用+失败快照回滚（一次变更/一次保存/一次撤销点）；R3 共享引用边改「移除对象引用属性」不再转私有并更新旧测试；R4 指纹纳入草稿中的原定义（基线变化即失效）；R5 外部依赖携带 sourceKind/sourceId 并提供「去处理」定位。保存边界新增 workbench/references.py：POST /api/save 仅阻断本次新引入的悬空引用（422 BROKEN_REFERENCE，零写入且 revision 不推进），历史遗留失效与未填写完整仍可保存；02 分册 §2.2 与 README 已先行登记。测试 test_references.py 10/10、legacy_graph_bridge 追加 5 项命令级断言、dependency_guard 15/15，前后端全量回归全绿，typecheck+build 通过；隔离实例复验阻断文案+定位抽屉+确认弹窗+HTTP 422。

- 决定：保存边界口径（写进接口文档）：只阻断「本次保存新引入」的悬空引用；历史遗留失效引用允许继续保存，避免锁死草稿；首次保存无基线不阻断；R3 语义分开：图谱共享引用边=移除对象引用属性；「转为私有」只保留在对象页显式操作，两条路径不再混淆；R2 原子性实现：节点与边统一预检（canDeleteNode/canDeleteEdge），被删节点的归属边自动去重，silent 应用失败恢复快照——不新增事务框架；R5 定位入口收口在 externalDependencyTarget（contract→contracts、interface/action/rule→对应库、mapping→对象映射），无入口的依赖只列名称与原因
- 验证：tests/test_references.py 10/10：单元（零悬空/契约悬空/关联悬空/值类型悬空/新旧比较）+ HTTP（首次放行/普通保存/删除有效引用 422 且零写入 revision 不变/历史遗留可继续保存/未填写完整 200+errors）；legacy_graph_bridge 追加 5 项命令级断言全过：R1 私有属性与链接被契约引用时命令阻断且状态逐字节不变；R2 批量失败整批不变且零 changed 事件、全部合法时 changed=1 与 before=1；R3 共享引用边删除后对象属性移除且共享定义保留；dependency_guard 15/15、ontology_graph、object_workspace 7/7、ont_list_unified 4/4、ontology_home 27、save_queue 22/22、editor_head_consistency 19、graph_toolbar_layout 8、config_transfer 5/5、ontology_import 18/18；后端 quick 3/3、test_save_iteration、test_project_api_roundtrip、test_storage_contract、test_flows、test_config_packages 全过；typecheck+build 通过；隔离实例 18894：共享定义删除阻断文案含对象名+引用位置抽屉可定位；共享定义高影响编辑确认弹窗正常；真实 HTTP 复验：建立含契约引用基线 200 → 删除被引用属性 422 BROKEN_REFERENCE 并列出契约名与属性 id
- 下一步：Codex 按验收意见 R1–R5 与保存边界复验（重点：命令级断言与 HTTP 422 零写入）；未验证项（开发计划 §8.4）：跨标签页「确认弹窗打开期间他处改原定义」完整 UI 复验（指纹逻辑已单元锁定）；多进程并发下同场景；图谱 canvas 点击级删除仍未做浏览器实测
- 依据/文档：文档/需求/20260920_本体建设维护与版本改版/验收意见_20260920.md；文档/需求/20260920_本体建设维护与版本改版/开发计划.md §8；workbench/references.py；tests/test_references.py；文档/接口文档/02-本体区接口.md §2.2
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 本体与项目统一维护体验改版需求与标注原型 · codex · 需求已交付

时间：2026-09-20T05:04:37.367450+00:00；记录：`.collaboration/entries/000100-fca3b3e90d7d.json`

整合本体/项目需求与专家报告，交付统一需求和带11项改动标注的原型；无开发计划或指令，未改正式功能。

- 决定：本体O01/O02复用已有实施待验收，O03/O04和项目P01至P07待评审。；管理本体及本体校验发布继续延期，不恢复MCP口径、对象级新校验或编排发布体系。；编辑时展开具体配置，说明textarea在前；原型支持改动总览和标注开关。
- 验证：JavaScript语法及链接、无网络检查通过。；Node VM最小DOM的16项原型交互逻辑检查通过；尚未做浏览器视觉与响应式验收。
- 下一步：用户评审后决定实施范围；本轮不产生开发计划和指令。
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/需求说明.md；文档/需求/20260920_本体与项目统一维护体验改版/交互原型_v1.html
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 编辑页返回控件全局统一 + 图谱工具栏选中裁切（用户三项反馈，提交 eab7545） · zcode · 已实施，待验收

时间：2026-09-20T04:56:41.459626+00:00；记录：`.collaboration/entries/000099-3354d332f4b9.json`

按用户三项反馈修复：① 规则编辑表单补返回图谱——新增共享组件 shared/EditorHead.vue（canvasReturn→「← 返回图谱」+「关闭」，否则 backLabel），规则表单首部渲染；② 动作返回图谱样式统一——动作列表页头改为仅列表态渲染（编辑卡升为同级 v-else），编辑表单首部改用同一 EditorHead，对象/链接/私有属性/共享属性/规则/动作六种编辑表单现由同一组件渲染同一控件；③ 选中节点后工具栏裁切——状态区与全图/详情/1跳/2跳/最大化 合成 .tool-right 整组，工具行宽屏允许换行（整组落第二行右对齐），≤1100 在 scoped 内回退单行横向滚动，并删除 legacy.css 里重复的 nowrap/wrap 声明（布局收口单点定义）。顺带清理 6 处 Field example 写成「例如：…」导致占位符「例如：例如：…」。

- 决定：编辑表单头部返回控件收敛为唯一共享组件 EditorHead：位置（表单卡首元素）、文案（canvasReturn 时「← 返回图谱」+「关闭」）、样式由组件统一，各页不再自复制按钮行；列表态页头只属于列表态（动作页原先把返回按钮留在页头，编辑态仍渲染，与其它页的卡内返回控件不同构）；图谱工具行布局只保留一处定义（EditorView scoped）：宽屏换行让右端按钮不被裁切，窄屏回退横向滚动保画布高度；legacy.css 不再重复声明同类属性（该处已因同优先级互相覆盖返工过一次）
- 验证：浏览器（隔离实例 18849，同构夹具 4 对象/3 共享/11 私有/4 链接/2 规则/2 动作）：五类编辑页头部按钮文案与坐标一致（「← 返回图谱」x=271,y=99 +「关闭」x=382,y=99），非图谱来源显示「← 返回规则列表/动作列表」；工具栏：1440/1280 选中节点后 0 裁切（最大化按钮 right = 工具行 right；rowSW=rowCW），1024/768 单行横向滚动且画布 651/616px 可用；0 控制台错误；返回闭环：编辑表单点「← 返回图谱」回到画布且原节点仍选中（selected=lg:pp:mg:rated_power）；build（vue-tsc+vite）通过；新增 editor_head_consistency 19 项、graph_toolbar_layout 8 项；回归 ontology_graph、legacy_graph_bridge、object_workspace 7/7、ontology_home 27、global_interaction 7/7、save_queue 22/22、ont_list_unified 4/4、dependency_guard 15/15；后端 quick 3/3、unit 26/26；18765 已重启（http 200），记录见开发计划 §5.9
- 下一步：用户在 18765 强刷（Cmd+Shift+R）后验收三项：双击规则/动作节点进入的编辑表单左上角有「← 返回图谱」；五类编辑页头部样式一致；选中节点后工具栏「最大化」不再被裁切；独立子代理验收进行中（并行），结论与任何不一致项待其回报后处理
- 依据/文档：frontend/src/shared/EditorHead.vue；frontend/src/ontology/{ObjectWorkspace,PropertyManager,BusinessRuleLibrary,ActionLibrary,FunctionManager}.vue；frontend/src/ontology/legacyGraph/EditorView.vue（.tool-right 分组与响应式）；frontend/src/ontology/legacyGraph/legacy.css；tests/editor_head_consistency.test.mjs；tests/graph_toolbar_layout.test.mjs；文档/需求/20260919_图谱编辑器源码整体复用/开发计划.md §5.9；git eab7545

### 本体维护保留项11至13独立验收 · codex · 已验证

时间：2026-09-20T04:54:01.146842+00:00；记录：`.collaboration/entries/000098-ee3a6d924e23.json`

部分通过，未通过整体验收。内存复现私有属性删除绕过依赖、批量删除失败后局部变更、共享基线变化未使确认指纹失效；图谱共享引用边仍执行旧转私有语义。仅提交验收意见，不改业务代码与真实数据。

- 决定：管理本体与本体校验发布继续延期。；保留现有页面实现，先修引用保护与批量原子性，再修语义、确认基线与定位闭环。
- 验证：dependency_guard 15/15、legacy_graph_bridge及typecheck通过；旧测试仍认可共享引用边转私有，不能证明本期全部通过。；内存探针复现R1/R2/R4；未进行独立浏览器视觉验收或后端悬空引用写入测试。
- 下一步：实施方修复R1至R5及保存边界后复验A12至A14。
- 依据/文档：文档/需求/20260920_本体建设维护与版本改版/验收意见_20260920.md

### Project module review and prototype · codex · 需求已交付

时间：2026-09-20T04:45:05.561513+00:00；记录：`.collaboration/entries/000097-0fa6db6b6029.json`

Delivered requirements and interactive prototype for project configuration maintenance; no business code or real data changes.

- 决定：Only two artifacts requested: requirements and prototype; no development plan or execution instructions.；Keep current menus and flow editor; improve status freshness, source change impacts, binding context, diagnostics and reference upgrades.；Ontology deferred modules and rule-definition generation remain deferred; flow dependency freezing requires product confirmation.
- 验证：Prototype JavaScript syntax and 13 Node VM interaction checks passed; no browser visual acceptance.；reference_changes.test.mjs passed; mapping_descriptions isolated test passed all 13 steps.
- 下一步：User reviews proposed scope and prototype before implementation planning.
- 依据/文档：文档/需求/20260920_项目模块配置维护改版/需求说明.md；文档/需求/20260920_项目模块配置维护改版/交互原型_v1.html
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 本体建设维护改版：保留项 11～13 实施（P0–P4） · zcode · 已实施，待验收

时间：2026-09-20T04:42:00.509706+00:00；记录：`.collaboration/entries/000096-dfcb36d405ef.json`

按执行指令当前范围只实施保留项 11～13，延期项（管理本体01～04/校验发布05～10/概览14）未开发接口与占位。新增 ontology/dependencyModel.ts 统一依赖检查（共享/规则/动作/对象四类判定+高影响影响模型，结构化输出名称+原因+定位 id）；共享库删除改阻断式（原自动私有化）、规则库补安全删除、动作库与对象页与图谱 legacyBridge 四分支统一判断（对象删除保持阻断式不再静默清动作关联）；PropertyManager 增加共享高影响保存确认（前后值+受影响对象清单+必须勾选+内容/引用指纹失效+取消保留输入）；顺带修复规则库编辑表单 v-else 错链抽屉导致列表态渲染空表单的既有缺陷。新增 tests/dependency_guard.test.mjs 15/15；前端与后端回归全绿；build 通过；隔离实例浏览器验收 A12/A13/A14/A01（截图 11/12）。提交 b550979。

- 决定：统一依赖判断收口在 dependencyModel.ts 纯函数：四类删除判定返回结构化依赖（name/reason/定位稳定 id），各页面与图谱底层命令只消费不另写规则；共享库删除从「自动私有化再删」改为阻断式：想保留内容须显式走对象属性的「转为私有」，避免删除时静默改写引用属性语义；对象删除保持阻断式并覆盖动作关联（图谱原实现会静默清理动作关联，本轮对齐对象页）；共享影响确认只在数据类型/观测值类型/业务定义变化时触发；确认绑定「编辑内容+引用集合」指纹，保存失败或409后失效，不缓存永久布尔；补 V3 契约签名引用（inputs/outputs[].ref 指向属性/对象/值类型）：此前被接口引用的共享定义未计入依赖，属真实缺口
- 验证：tests/dependency_guard.test.mjs 15/15：共享/规则/动作/对象四类阻断与放行、契约引用阻断、前后值与引用对象、仅名称不触发、指纹随内容与引用变化、各入口源码级一致性、规则库无强制删除、共享库不再自动私有化、转私有保留对象属性 ID；浏览器（隔离 18893，临时根）：A12 改类型+定义→弹窗显示 double→string 与「储能设备→soc、储能系统→soc」；未勾选被阻止；返回编辑输入保留；勾选确认后已保存且 /api/state 落库（两个引用属性稳定 ID 保留）；A13 共享/规则/动作阻断文案均含对象名并自动打开定位抽屉，无引用规则删除成功；A14 移除引用后共享定义与另一对象引用保留（仅 mg:p_s1 剩余）；A01 弹窗 1440/1024/768 可达（含勾选框与确认按钮）；回归：ont_list_unified 4/4、object_workspace 7/7、ontology_home 27、save_queue 22/22、ontology_graph、legacy_graph_bridge、business_rule_model、action_model、ontology_import 18/18、config_transfer 5/5、global_* 7/7；后端 quick 3/3；typecheck+build 通过；真实数据零写入
- 下一步：用户评审 11～13 的实际交互（共享影响确认弹窗、规则库删除、各入口阻断文案与定位）；图谱 canvas 内删除未做点击级浏览器实测（按钮已按用户要求移除，未恢复）；768 弹窗仅 DOM 可达性探测无截图；延期范围（管理本体/版本比较恢复/发布确认）保持不实施，恢复须用户明确指示并先同步三份文档
- 依据/文档：文档/需求/20260920_本体建设维护与版本改版/开发计划.md §7；frontend/src/ontology/dependencyModel.ts；tests/dependency_guard.test.mjs；文档/需求/20260920_本体建设维护与版本改版/截图/

### 同步裁剪需求计划执行指令正文 · codex · 需求已交付

时间：2026-09-20T04:23:52.472194+00:00；记录：`.collaboration/entries/000095-560bad445ac4.json`

按用户要求进一步清理三份文档正文，仅列11共享修改影响、12规则安全删除、13语义与引用保护；计划改为P0至P4，验收仅保留当前范围，不再混排延期任务。延期完整方案由Git历史追溯。未改代码或原型。

- 验证：三份文件范围和相对链接检查通过。
- 依据/文档：文档/需求/20260920_本体建设维护与版本改版/需求说明.md；文档/需求/20260920_本体建设维护与版本改版/开发计划.md；文档/需求/20260920_本体建设维护与版本改版/执行指令.md

### 延期管理本体与本体校验发布改版 · codex · 已确认决定

时间：2026-09-20T04:19:40.934706+00:00；记录：`.collaboration/entries/000094-014f6e1eb669.json`

用户明确两个模块需求先延期。四件套已同步范围：管理本体、版本查看比较恢复检查点及发布确认/分类改造、派生概览变化全部延期；原型标延期并阻止其模拟写操作，保留方案回看。未改正式功能。

- 决定：延期不删除或回退现有工作台功能，不操作真实数据；恢复需用户明确指示。；保留共享属性保存影响确认、规则安全删除、操作语义和引用保护一致性待评审；不以此改发布或资产管理，未新增实施授权。
- 验证：JS语法、延期及保留路径状态检查、文件链接检查通过；未做浏览器视觉复验。
- 依据/文档：文档/需求/20260920_本体建设维护与版本改版/需求说明.md；文档/需求/20260920_本体建设维护与版本改版/开发计划.md；文档/需求/20260920_本体建设维护与版本改版/执行指令.md；文档/需求/20260920_本体建设维护与版本改版/交互原型_v1.html

### 返回坐标稳定 + 规则编辑内嵌化 + 头部空行清除（提交 050de0c） · zcode · 已实施，待验收

时间：2026-09-20T04:15:53.954326+00:00；记录：`.collaboration/entries/000093-21efa2dc8359.json`

按用户三项修正：① 双击进编辑页返回后坐标漂移——根因 applyFocusTarget 强制 animate({center, zoom:≥1}) 覆盖用户缩放/平视；改为最小干预（只选中+刷详情，仅目标不在视口内才居中且不传 zoom），实测往返 zoom/pan/全部节点坐标完全一致；② 规则编辑从弹窗改内嵌表单（Field 四字段 + form-grid + 页头/底部，与动作/对象/属性页同构），BusinessRuleDialog 与弹窗状态机移除，改名保存回图谱且落库；③ 红框空行/线为头部两行时代 tool-row 的 scoped 残留（margin+padding+border-top），清零后头部 67→56px、画布 641→653px。

- 决定：返回画布定位采用最小干预原则：只选中与刷新详情，平移仅用于把视口外目标带入视野，绝不放缩（用户视图优先）；规则编辑统一为内嵌表单风格（与动作定义页同构）：Field 渲染、form-grid 布局、eyebrow+status pill 页头、保存定义/取消底栏；保存后回列表定位（来自图谱则回画布）；头部残留样式直接改 scoped 源头而非在 legacy.css 叠加覆盖（同优先级时 scoped 会赢，叠加覆盖不可靠）
- 验证：隔离实例 18848 浏览器实测：① 用户先缩到 0.5、平移(300,200)、拖动节点到(-50,300)，双击编辑返回后 zoom/pan/全部节点坐标逐字节一致；② 规则内嵌表单 4 字段渲染、无弹窗、改名「储能SOC规则V3」保存后回图谱、画布节点名更新、/api/state 已落库；③ 头部计算样式 marginTop/paddingTop 0px、borderTop 0px，头部 56px、画布 653px；全程 0 控制台错误；build（vue-tsc+vite）通过；ontology_graph 12/12、legacy_graph_bridge 12/12、object_workspace 7/7、ontology_home 27、global_interaction 7/7、save_queue 22/22 全过；记录见开发计划 §5.8
- 下一步：用户在 18765 强刷后验收：双击节点→编辑→返回后坐标/缩放不变；规则「编辑」为内嵌表单；头部无多余空行
- 依据/文档：frontend/src/ontology/legacyGraph/EditorView.vue（applyFocusTarget 最小干预、tool-row scoped 清零）；frontend/src/ontology/BusinessRuleLibrary.vue（内嵌表单）；frontend/src/shared/EditorField.vue；文档/需求/20260919_图谱编辑器源码整体复用/开发计划.md §5.8；git 050de0c
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 本体改版原型逐页改动标注 · codex · 需求已交付

时间：2026-09-20T04:12:49.656725+00:00；记录：`.collaboration/entries/000092-c822114d10b8.json`

在现有v1原型增加本页改动说明、保留内容、01至14编号、按钮/区域新增调整标签及弹窗说明，默认显示且可关闭；同步文档明确不搬入正式产品，未改业务代码。

- 决定：仅增加评审标注，不扩大原有需求或恢复图谱已移除入口。
- 验证：内嵌JS语法、原17项状态与新增4项标注检查通过；未做浏览器视觉复验。
- 依据/文档：文档/需求/20260920_本体建设维护与版本改版/交互原型_v1.html

### 节点/连线编辑一律跳转源端（提交 f4b23b2） · zcode · 已实施，待验收

时间：2026-09-20T03:50:24.444798+00:00；记录：`.collaboration/entries/000091-9209ce75d0bb.json`

按用户修正：① 双击节点改为与详情「编辑」一致跳转编辑页，删除 NodeEditModal 与 openNodeEdit/saveNode/deleteNode（画布内不再有编辑弹窗）；② 点击/双击连线按类型跳转源端（对象链接→起点对象链接页签+链接表单；共享引用/私有属性→所属对象属性页签+属性表单；规则/动作关联→对象对应页签并定位），500ms 同边防重；右侧「连线编辑」面板（关系名/描述/保存/删除）整体移除。连带修复两个 P0：对象/链接/属性表单返回图谱时未关编辑态；App 导航 focus 参数残留导致只带 type 的跳转重开旧属性表单。

- 决定：原则（用户确认）：图谱内容全部来自对象建模，节点与连线的编辑一律到源端定义页完成，画布只做浏览/定位/布局；连线跳转按连线类型分派到对应源端页签，并携 edit:true 直达表单；同一连线 500ms 内防重（双击连发两次 tap 只跳一次）；画布内编辑能力删除而非隐藏：NodeEditModal 组件调用与三个处理函数连根移除，连线编辑面板模板整块移除；返回图谱统一先关闭编辑态；App 每次带 focus 跳转前清空旧定位参数（propertyFocusId/definitionFocusId），避免残留参数串页
- 验证：隔离实例 18847 六类浏览器实测：双击对象→维护对象定义；对象链接→维护·包含（改正向名保存后回图谱、画布边标签更新为「包含（验收改名）」、/api/state 已落库）；共享引用→共享引用属性面板（含打开共享定义/解除为私有）；私有属性→维护·簇编号；规则关联→对象「规则·1」页签；动作关联→对象「动作·1」页签；均带「← 返回图谱」，0 控制台错误；build（vue-tsc+vite）通过；ontology_graph 12/12、legacy_graph_bridge 12/12、object_workspace 7/7、ontology_home 27、global_interaction 7/7 全过；记录见开发计划 §5.7
- 下一步：用户在 18765 强刷后验收：双击节点跳编辑页、点击/双击各类连线跳对应源端页签、右侧不再出现连线编辑面板
- 依据/文档：frontend/src/ontology/legacyGraph/EditorView.vue（openEdgeInWorkspace/dbltap/面板移除）；frontend/src/ontology/ObjectWorkspace.vue（backToGraph 关编辑态）；frontend/src/App.vue（focus 参数清空）；文档/需求/20260919_图谱编辑器源码整体复用/开发计划.md §5.7；git f4b23b2

### 本体建设维护与版本改版四件套 · codex · 需求已交付

时间：2026-09-20T03:33:08.984500+00:00；记录：`.collaboration/entries/000090-ad343dd6c7a7.json`

交付评审版v1原型、需求、开发计划、执行指令，覆盖资产管理、版本查看比较恢复、检查点、共享影响与跨入口一致性；未改业务代码或真实数据。

- 决定：恢复旧版为草稿、归档只读、受限空本体删除及业务定义影响确认均标为本版建议，待用户评审后实施。；保持生成口径/动作参数/Excel关系等暂缓；核对000088/000089后明确不恢复刚移除图谱工具按钮，沿用详情跳现有表单。
- 验证：四件套链接、内嵌JS语法检查通过；Node VM加DOM桩17项状态测试通过。；浏览器URL安全策略拒绝打开本地file原型，未绕过；未完成浏览器视觉/响应式实测，不将静态测试当正式验收。
- 下一步：用户评审原型及需求建议；批准后再交执行工具实施。
- 依据/文档：文档/需求/20260920_本体建设维护与版本改版/交互原型_v1.html；文档/需求/20260920_本体建设维护与版本改版/需求说明.md；文档/需求/20260920_本体建设维护与版本改版/开发计划.md；文档/需求/20260920_本体建设维护与版本改版/执行指令.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

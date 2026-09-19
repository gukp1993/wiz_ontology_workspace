# Codex / zcode 共享上下文

上下文版本：`d0fa5096e431b6d6`

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

### 储能jsonId图谱清洗转当前本体数据 · codex · 已验证

时间：2026-09-19T13:12:36.994572+00:00；记录：`.collaboration/entries/000053-1c0abf190305.json`

生成独立清洗审阅稿及工作台可导入ZIP：6对象、16共享属性、35对象属性引用、7对象链接、21业务规则、7对象规则关联。未写真实工作台数据。

- 决定：SOC拆实时数值和采样时间序列；动作建议计算保留业务规则，不自动生成控制动作。；保留全部原始134条记录、92条边的转换去向；项目取值说明另出参考，不生成可执行项目。；园区/电站、可调能力量纲、偏差单位、SOC百分比等歧义已标注；充放电计划规则保持不完整草稿，不虚构规则。
- 验证：当前模型协议校验及encode/decode无损往返通过；包manifest和SHA256通过。；临时根和独立SQLite中调用当前配置迁移服务上传、预览、导入、读回通过，本体与工作流一致，新本体ID分配正确。；发布校验仅返回预期两条：充放电计划缺少规则内容、输出结果。未进行浏览器验收或业务公式运行验证。
- 下一步：用户审阅清洗说明中的口径问题后，可通过配置迁移导入清洗待确认ZIP并继续补齐。
- 依据/文档：文档/交付物/20260919_储能图谱清洗转换/清洗说明.md；文档/交付物/20260919_储能图谱清洗转换/储能本体_清洗待确认.zip；文档/交付物/20260919_储能图谱清洗转换/验证结果.json

### 编排列表操作平铺+列宽+说明截断（用户截图反馈） · zcode · 已实施，待验收

时间：2026-09-19T12:46:34.775066+00:00；记录：`.collaboration/entries/000052-b8655dd8cbc9.json`

按用户截图反馈改 frontend/src/flow/FlowList.vue：操作列去掉「更多」下拉，编辑/复制/删除三按钮直接平铺（删除红色文本，复用原 copy/remove 处理器与软删除确认弹窗）；表格 table-layout:fixed 定列宽（处理节点 84/更新时间 150/配置状态 110/操作 148），其余宽度归编排名称列，其他字段不再被挤压；编排说明单行 text-overflow 省略号截断，全文放 title 悬停可见；顺手修空行 colspan 6→5 与实际列数一致。提交 a29aa2a。

- 决定：沿用现有软删除+appConfirm 确认，不新增删除生效标准（用户说明删除标准统一后续再做）；未动任何 API 与数据契约，纯前端列表模板/样式；列宽用 table-layout:fixed 而非内容 max-width：表格自动布局下 nowrap 长文本仍可能撑宽列，固定布局才能保证省略号与列宽分配确定生效；首版处理节点列 70px 致四字表头换行，复查截图后改 84px+nowrap
- 验证：npm run build（含 vue-tsc 类型检查）通过；node --import ./tests/ts_hooks.mjs tests/flow_model.test.mjs 全过；隔离实例 18901（WIZ_WORKBENCH_ROOT 临时目录+WIZ_DATABASE_URL 隔离 sqlite+临时账号）浏览器实测两条长说明编排：操作平铺、说明省略号截断且 DOM 快照可访问名（title）含全文、表头单行；点「复制」列表 2→3 行功能正常（截图核对）；grep dist 确认「更多 ⌄」零残留、table-layout:fixed 生效；18765 已 restart 返回 200；临时实例/临时目录/临时浏览器标签页（含上一轮遗留 18999 失效标签）已清理，真实数据与用户编排零写入
- 下一步：用户在 18765 刷新查看编排列表新布局（a29aa2a）
- 依据/文档：frontend/src/flow/FlowList.vue；git a29aa2a

### 执行 函数编排优化审阅修正/执行指令.md（R01–R06，A01–A14） · zcode · 已实施，待验收

时间：2026-09-19T12:37:16.278527+00:00；记录：`.collaboration/entries/000051-a12d5de4b9a5.json`

按修正四件套定点修复测试视图六项缺陷：R01 运行生命周期（pendingConfirm 同步锁定先于异步确认、确认与请求共用冻结 payload/snapshot、取消/成功/失败/晚回包均释放、v-show 挂载无法绕过）；R02 多路径勾选区按路径存在性显示不再消失、testScopePlan 新增 requireConnected（整条/单节点允许并列分支，仅片段保持连通校验，默认 true 向后兼容）、禁用原因全模式可见；R03 入口稳定 ID 去重 + 全范围统一类型控件 +「使用空文本」显式区分空串与未提供；R04 主入口首次默认整条之后恢复上次范围/输入/结果、快捷入口才显式切换、快照新增 projectName/nodeNames、输入缓存按 flowId+projectId 隔离且声明签名变化失效；R05 describeOutput 分类渲染；R06 bounded_preview 精确预算（UTF-8 ≤65536 字节、列表 ≤100 项、previewTruncated 标记结构）。

- 决定：R02b 连通校验按范围类型区分：requireConnected 仅片段为 true，整条允许并列分支（合法 DAG），不放松片段校验；R03 不新增 nullable 体系：以 providedEmpty 勾选显式表达空文本，未提供一律报错；旧接口 null 兼容未动；R06 截断标记结构已先登记接口文档 04 §3.1 并记 README 变更记录；预算收紧兜底仅标记；R04 输入缓存按 flowId+projectId 隔离，声明签名变化即粗粒度失效（防同名错配优先）；保留唯一测试主入口与节点快捷入口；未恢复被用户否定的旧多入口
- 验证：tests/flow_test_workspace.test.mjs 21 项组件行为测试全过（真实 Vue 响应性+延迟 runFlow 桩+受控确认）；flow_model 29、flow_test_plan 60（6 组真实字节断言均 ≤65536 且原值不变）、flow_executor 33、test_flows 85、flow_sql_dialect 25、llm_providers 27、project_flow_source 21 全过；typecheck+build 通过；隔离实例 18999 浏览器验证：测试视图挂载、入口去重、片段 B–D chips、整条 A–E 运行成功、API 实测 B–D 输入 8→10/30/15 且 A/E 不在 nodeResults
- 下一步：用户验收：18765 刷新后进编排编辑 → ▷测试编排；A10 列表/对象输出浏览器视觉走查未覆盖（calc 恒标量，渲染由 describeOutput 单测锁定）；A13 的 1280×800/1024×768 逐尺寸截图未留存；环境代理 7890 偶发拦截本地请求返回 502 与工作台无关
- 依据/文档：frontend/src/flow/FlowTestWorkspace.vue；frontend/src/flow/flowModel.ts；workbench/flow_test_plan.py；tests/flow_test_workspace.test.mjs；文档/需求/20260919_函数编排优化审阅修正/开发计划.md §5.1
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 执行配置迁移 §12 修正（T01–T09） · zcode · 已实施，待验收

时间：2026-09-19T12:29:19.370112+00:00；记录：`.collaboration/entries/000050-a5f8b86012e4.json`

按开发计划 §12 Codex 验收意见完成 T01–T09 定点修正（提交 810afd1）：T01 编排导入同步 flowId/name 新身份可保存；T02 项目逐快照引用解析映射；T03 副本归属纯函数+预检歧义阻断；T04 敏感剥离收窄（认证头/URL 认证段，删键名误伤，模型地址覆盖）；T05 预检强依赖闭包/能力白名单/重复 JSON 键；T06 默认模型入包+导入显式绑定；T07 待补凭据按新项目定位+api-credentials pending+补填清除；T08 前端空名阻止/取消语义/返回导出/requestId 恢复/预检代次；T09 名称冲突按 (kind,name_key)+副本名预检一致。测试 13 步全过+前端回归全绿+build 通过；浏览器实测空名/取消/结果页。接口语义登记 07 §4.5/03 §4.1/README。

- 决定：T04 剥离范围限定认证头键+URL userinfo，不按泛化键名清空；模型 endpoint 纳入 URL 检查；T07 未加表：待补仍存 wb_user_settings 键 config-package.pending-credentials，条目带 projectId/projectName，合并键 (declarationId, projectId)；api-credentials 响应新增 pending 字段（03 分册已登记）；T09 副本名（上下文N）在预检命名产出，事务不再暗改；名称冲突按 (kind, name_key)
- 验证：tests/test_config_packages.py 13 步全过（含 §12.4 断言 1-7/9：T01 可保存且另一副本 hash 不变、T02 三处引用一致、T03 顺序无关+歧义 None+两项目不同副本、T04 哨兵剥离+业务 token 保持、T05 三类阻断零写入、T06 接收默认不变、T07 补填闭环、M21 发布递增 2.1.0）；前端 config_transfer 5/5、global_settings_nav 7/7、ontology_home/object_workspace/save_queue/ontology_import/flow_model 等回归全过；typecheck+build 通过；隔离实例 18892 浏览器：空名确认被阻止并定位、取消回初始态且暂存清理、确认导入成功页、返回导出切页签（截图 08）；实例已清理，真实数据零写入
- 下一步：Codex 复核：按 §12.3 逐项重验（重点 T01 保存链路/T02 三处引用/T03 拆副本/T05 阻断/T07 补填闭环）；剩余未测（§13.4）：多进程同名竞争、附件迁移（M05 白名单）、M07 歧义包浏览器端到端、双账号待确认横幅串扰专项
- 依据/文档：文档/需求/20260919_本体与项目配置迁移/开发计划.md §13；workbench/config_packages.py；tests/test_config_packages.py；frontend/src/settings/ConfigurationTransfer.vue；文档/接口文档/07-配置迁移接口.md §4.5

### 配置迁移独立验收 · codex · 已验证

时间：2026-09-19T12:00:24.936942+00:00；记录：`.collaboration/entries/000049-7170877dbbf5.json`

对61d1e2d隔离验收：基础链路运行，完整迁移验收暂不通过。T01–T09证据与修正建议已追加到原开发计划§12，未改业务代码和真实数据。

- 决定：保留双页签、单事务、分片和有作用域业务稳定ID，不重做UI。；需修复编排身份、历史快照引用、副本匹配、敏感值处理、依赖预检、默认模型、待补凭据及导入状态机。
- 验证：原后端7步骤、前端5项及build通过；末尾回执写入故障注入验证资产数8→8。；导入编排保存404；历史本体引用被改错；跨项目副本选错；普通token业务字段被清空。；合成模型URL认证密码出包，托管Key未出包；默认模型未导出；缺本体包、未知能力、重复JSON键未阻止。；隔离浏览器18887正式文件选择器上传合成包；空名称仍导入、返回导出无反应。没有外部业务调用。
- 下一步：按开发计划§12先修T01–T03，再T04–T06，最后T07–T09；补有效断言后重新验收。
- 依据/文档：文档/需求/20260919_本体与项目配置迁移/开发计划.md §12

### 配置迁移页 UI 风格修复（用户截图反馈） · zcode · 已实施，待验收

时间：2026-09-19T11:50:04.265443+00:00；记录：`.collaboration/entries/000048-d5ef22bc8218.json`

用户指出配置迁移页 UI 异常：导出复选框与文字间距巨大且文字竖排断行；导入页原生文件控件泄露。根因是全局 style.css 的 input{display:block;width:100%} 把 checkbox 拉满整行、并覆盖 hidden 属性的 UA display:none。修复：导出复选改用工作台现成 .check-option 样式、搜索框换共用 SearchField 组件、文件控件显式 .ct-file-input{display:none}、预检名称输入框去全局 margin/宽度拉伸。隔离实例截图核对三页（导出选择/导入页签/预检表格）均与工作台风格一致；config_transfer 5/5、global_settings_nav 7/7；typecheck+build 通过。提交 e895d1c。

- 决定：复选列表复用全局 .check-option（17px 蓝色 accent、flex+gap）而非自定义样式；搜索框复用 shared/SearchField——与工作台既有列表视觉完全一致；hidden 属性在全局 input 样式下不可靠：本页文件控件改显式 class display:none!important（其他页面历史行为不动）
- 验证：18891 隔离实例截图 05/06/07：checkbox 紧跟文字、无原生控件、搜索框带图标、预检表格名称输入框正常；config_transfer.test.mjs 5/5（stub 改 render 函数适配 SSR ESM）；global_settings_nav 7/7；typecheck+build 通过；隔离实例与临时目录已清理
- 下一步：用户在 18765 刷新查看修复后的配置迁移页
- 依据/文档：frontend/src/settings/ConfigurationTransfer.vue；tests/config_transfer.test.mjs；文档/需求/20260919_本体与项目配置迁移/截图/

### 执行 本体与项目配置迁移（T0–T5，M01–M24） · zcode · 已实施，待验收

时间：2026-09-19T11:36:28.117549+00:00；记录：`.collaboration/entries/000047-f82213c3f9f2.json`

按执行指令完成配置迁移全链路：07 分册先登记 7 接口（export-preview/export/stage/import-preview/import/import-result/discard，ZIP 包 formatVersion=1，分片 512KiB/包≤20MiB/解压≤100MiB），后端 config_package_format/packages/routes 三模块（闭包收集含 M04 历史本体补齐与 M07 上下文分组、ZIP 有界安全解析、命名（导入）后缀、单事务原子导入：资产级新 ID+业务稳定 ID 恒等+跨资产引用重写+发布副本 origin 追溯+requestId 幂等+待补凭据声明持久化），前端设置中心「数据管理/配置迁移」双页签+两概览快捷预选。后端 7 步+前端 5 步+旧导出/Excel/CAS/存储/编排回归全绿；隔离实例浏览器全流程（导出→B 导入（导入）→打开编辑保存→再次导入（导入2））1440/1024 截图 4 张。提交 781ffaa。

- 决定：资产身份全部新建（本体 uuid/项目编排 12hex/provider llm-*），业务稳定 ID（mg:*/节点/连接/apiName/definitionOrder/表名字段）恒等保留；导入归属当前登录账号不读包内 owner；manifest 不自登记（hash 无法自包含）；发布副本保 version 标签/顺序、附 originPackageId/sourceVersion/sourceHash，新 revision token 不调 versions.publish；待补凭据声明持久化在 wb_user_settings 键 config-package.pending-credentials（重启保留，按声明 ID 去重合并），未加表未动 schema；仅 import 持 locking.LOCK；上传/预检/压缩不持锁；TokenError 404/410、包问题 415/422、名称冲突 409 带建议名
- 验证：tests/test_config_packages.py 7 步全过（登记 http 组）：格式安全/闭包（未选项目不入包/历史本体补齐/口令不入包）/分片幂等与409/导入核验（版本保序+引用新本体 ref_row=ok+恒等 ID+flow 重写）/M14 幂等409回执/M13 二次导入独立性/M19 穿越422+401；tests/config_transfer.test.mjs 5/5（导航注册/分片切片hash/请求体/SSR 渲染 mapping_forms 同款/无覆盖合并文案红线）；global_settings_nav 7/7（分类断言随需求扩展更新为两项）；旧回归全绿：test_export_restore_http、test_storage_contract、test_flows、ontology_import 18、save_queue 22、ontology_home 27、object_workspace 7/7 等；typecheck+build 通过；隔离实例 18890 浏览器：exporter 导出预览（1 发布版本+凭据警告）→下载 blob 真实触发→importer 导入预检「储能本体（导入）/当前账号已有同名」→确认→结果页打开新本体（新 ID URL）→编辑保存成功→再次导入得（导入2）/（导入）两套独立（数据核验：项目分别引用两套新本体 1.0.0 且 ref resolution=ok、编辑互不影响、exporter 原资产不动）；1440/1024 截图 4 张在需求目录/截图/
- 下一步：用户验收：18765 刷新后 设置→数据管理→配置迁移 体验导出/导入（勿用真实资产试导入，先在隔离环境验证）；限制如实记录在开发计划 §11.4：默认模型显式化转换未实现（仅映射显式 providerId）；M07 拆副本/M15 故障注入/M02 空本体/M05 规则动作登记实例逐项/M12 并发竞态/M14 重启回执/M21 导入后发布递增未专项实测；mapping_forms 既有基线失败待其负责人；test_property_preview 需本机 MySQL
- 依据/文档：文档/接口文档/07-配置迁移接口.md；workbench/config_packages.py；frontend/src/settings/ConfigurationTransfer.vue；tests/test_config_packages.py；文档/需求/20260919_本体与项目配置迁移/开发计划.md §11
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 函数编排优化独立审阅与修正需求 · codex · 需求已交付

时间：2026-09-19T11:33:09.641741+00:00；记录：`.collaboration/entries/000046-995ee4c2e909.json`

独立审阅3f0d120隔离副本：布局与核心B–D片段执行可以保留，但不能整体验收通过。发现重复提交、多路径选择中断、并列全图被拦、输入重复与类型控件差异、重开范围丢失与旧结果元信息、列表空白、输入预览未真正截断。交付修正四件套，未改正式代码/真实数据。

- 决定：保留唯一测试编排主入口、节点快捷入口、独立左右测试布局和现有节点配置；不因原型视觉差异要求重写。；后续实施限定R01–R06与A01–A14，不扩充执行平台；真实接口变化先更新文档。
- 验证：前端build通过；flow_model通过；flow_test_plan51项、flow_executor33项通过。；隔离浏览器B–D输入8得到10/30/15；复现重复入口、范围重置、菱形选中首项列表消失、合法并列节点整条按钮禁用。；真实组件脚本延迟API桩：连续run发2次请求且running=false。输入预览1MiB仍返回1048588字节。；修正原型jsdom18项通过；file页面被浏览器URL安全策略拦截，未绕过，未完成原型视觉验收。
- 下一步：其他harness按本目录执行指令实施；修复后独立验收，不将原有测试通过等同缺陷修复。
- 依据/文档：文档/需求/20260919_函数编排优化审阅修正/需求说明.md；文档/需求/20260919_函数编排优化审阅修正/开发计划.md；文档/需求/20260919_函数编排优化审阅修正/执行指令.md；文档/需求/20260919_函数编排优化审阅修正/交互原型_v1.html

### 编排测试入口唯一化（用户反馈三入口重复） · zcode · 已实施，待验收

时间：2026-09-19T11:09:09.692814+00:00；记录：`.collaboration/entries/000045-be56e2faf838.json`

用户在 18765 真实编排页指出页头「测试片段」「▷ 测试编排」与 dock「打开测试调试 ↗」三入口重复。按指示只保留页头「▷ 测试编排」为唯一测试入口（片段/单节点范围在测试视图范围下拉选择）；删除页头「测试片段」按钮与 dock「打开测试调试 ↗」按钮及配套提示文案。详情面板「▷ 测试此节点」为原型既有的单节点便捷入口，保留。

- 决定：测试入口唯一化到页头「▷ 测试编排」；范围（整条/连续片段/单节点）全部在测试视图内下拉选择，页头不再重复放范围类入口；详情面板「测试此节点」保留（原型既有，直接以该节点为范围进入测试视图）
- 验证：typecheck/build 通过；flow_model 回归过；grep 确认 FlowEditor 中「测试片段」「打开测试调试」零残留（仅开发计划记录中提及）
- 下一步：用户刷新 18765 查看唯一入口后的页头与 dock
- 依据/文档：frontend/src/flow/FlowEditor.vue；文档/需求/20260919_函数编排配置与调试优化/开发计划.md §11 补充 2

### 函数编排原型对齐修正（用户反馈「没有参考原型」） · zcode · 已实施，待验收

时间：2026-09-19T11:04:44.426206+00:00；记录：`.collaboration/entries/000044-f53fe2a6fcb9.json`

逐项比对交互原型_v1.html 后修正设计视图页头：紧凑单行（标题 h1+✎改名弹窗替代名称输入框、运行项目改为页头下拉（App 传项目清单、切换经既有 loadProject 含保存/离开保护）、检查配置/测试片段/▷测试编排），说明改页头第二行内联编辑；检查状态移工具栏 tag；常驻模式栏仅绑定模式显示。测试视图对齐：范围下拉并入「单节点·各节点」选项、标量输出大数值+JSON 对照、结果头项目行。同时修复画布高度链缺陷：flow-body 原依赖节点列表内容撑高（列表收起/窄视口画布塌 0，84c78ef 已存在被本轮暴露），flow-page 改显式 height:calc(100vh-96px)。

- 决定：页头运行项目下拉选项来自 App projects 清单，切换经 loadProject（含保存/离开保护），不静默重绑连接；flow-page 显式视口高度替代 height:100% 百分比链（该链在列表收起时本就断裂）；范围下拉按原型合并单节点选项；标量输出保留大数值+JSON 双展示
- 验证：typecheck/build 通过；flow_model/global_interaction/object_workspace 回归过；18880 隔离实例（已清理）截图核对：设计视图（标题+✎/说明行/运行项目下拉/工具栏 tag/画布满高 A→E 连线）与测试视图（合并范围下拉/起止 B–D/路径 chips/B.x=10→C=36 大数值+JSON/项目行）与原型形态一致；工作区 config_packages/07 分册/server.py 等为其他执行者在途改动，未触碰未提交
- 下一步：用户在 18765 刷新查看对齐后的编排设计/测试视图（注意 18879/18880 为已清理的临时隔离实例）
- 依据/文档：frontend/src/flow/FlowEditor.vue；frontend/src/flow/FlowTestWorkspace.vue；frontend/src/App.vue；文档/需求/20260919_函数编排配置与调试优化/开发计划.md §11 补充

### 本体与项目配置迁移-v1需求交付 · codex · 需求已交付

时间：2026-09-19T10:54:30.550705+00:00；记录：`.collaboration/entries/000043-54a25abc46ac.json`

按用户确认的跨本地工作台配置交接场景，交付配置迁移四件套：原型、需求说明、开发计划、执行指令。每次主动导入新建独立资产，同名后缀不覆盖；仅设计交付。

- 决定：已确认规则：同包可多次主动导入，每次新的本体/项目；不覆盖、不合并、不复用已有资产；同名自动（导入）/序号并可在预览改名。；本版方案包括当前已保存草稿和全部发布配置，不含自动保存修订/运行结果/业务库数据；依赖本体版本、编排、模型元数据与必要附件自动收集；历史引用其他本体须补齐并说明。；新资产和作用域稳定ID重映射，包内共享依赖保持关联；导入事务包含全部配置、索引及回执；新主动导入与同次网络重试用requestId区分。；普通ZIP不带受管理凭据；待补声明持久化，默认模型引用显式处理，不修改接收者默认；不自动执行业务查询或调用。传输、历史与凭据边界标为本版设计建议，非声称用户逐项指定。；设置中配置迁移双页签；本体/项目概览预选快捷入口。24项验收与T0-T5计划；目标API由执行者先登记再实施。
- 验证：原型47项jsdom检查通过，含同名、回滚演示、再次主动导入、丢响应回执及跨页保留；无浏览器视觉或正式产品迁移验证。；四份文件相互链接、M01-M24完整性、无网络/本地持久化、空白检查通过。；未改产品代码、正式接口、用户数据；未运行产品build或后端测试。交付前重新读取并核对zcode最新函数编排交接，不修改其实施内容及其他未提交记录。
- 下一步：执行者按本目录执行指令实施，先接口/映射矩阵，再依赖包/暂存预检/单事务导入/UI，完成隔离M01-M24验收。
- 依据/文档：文档/需求/20260919_本体与项目配置迁移/交互原型_v1.html；文档/需求/20260919_本体与项目配置迁移/需求说明.md；文档/需求/20260919_本体与项目配置迁移/开发计划.md；文档/需求/20260919_本体与项目配置迁移/执行指令.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 执行 20260919_函数编排配置与调试优化/执行指令.md（T0–T5，F01–F20） · zcode · 已实施，待验收

时间：2026-09-19T10:53:37.518061+00:00；记录：`.collaboration/entries/000042-c44fa2336769.json`

接口文档先行登记（04 §3.1 testMode=isolated/inputOverrides/inputs 预览/错误码分层/传递 skipped/[] 恒 400；01 §4 交叉引用；05 条目；README 变更记录），再改代码。后端新增 flow_test_plan.py 纯范围计划（结构 400→被测节点配置 422→边界取值 422 全部先于执行；后端终审 0/false 有效、NaN 拒绝、对象列表按声明递归、缺字段≠显式 null；有界预览 ≤100 条/64KiB），flow-run isolated 分支仅以 orderedTargets 交执行器（范围外 A/E 配置错误不阻断），执行器 isolated 解析（稳定 ID 覆盖仅限范围外/未绑定输入，内部严格取真实结果，不接受旧技术名绕过）+失败传递性 skipped（修正旧版下游 failed）+每节点 inputs/inputsTruncated。前端新增 FlowTestWorkspace 独立测试视图（左输入右结果、范围=整条/单节点/连续片段、起止按 dependencyPaths、多路径显式选集、结果绑定配置签名+项目+范围+输入签名+请求代次、晚回包丢弃、v-show 保留状态），FlowEditor 设计/测试双视图+显式运行项目+测试入口（移除旧画布下测试面板与测试勾选模式），NodeConfig 连续区块（名称说明/输入/实现/输出/高级，能力零删减），FlowList 名称说明同列+更多菜单。

- 决定：契约分层：结构/集合 400 → check_flow 仅过滤被测节点 422 → 取值预检 422，全部先于任何执行；覆盖键=节点稳定 ID+输入稳定 ID；范围内内部依赖与固定/入口来源禁止覆盖（400）；失败沿所选范围传递性 skipped 为全模式语义（已登记契约），修正旧版传递下游以 failed 呈现的问题；测试视图 v-show 保持挂载：设计↔测试往返保留画布视口/选中节点/输入/范围/结果；结果新鲜度=配置签名+项目+范围+输入签名+请求代次，晚回包按序号丢弃；运行项目显示当前项目上下文，为空给「选择项目→」走既有链路，不静默重绑连接；整条范围沿用 revision/409/互斥既有约束；单节点与片段统一走 isolated 模式（新 UI 不再用旧技术名覆盖）
- 验证：tests/test_flow_test_plan.py 新增 51 项全过：B–D 输入10→12/36/18 且 A/E 不在 nodeResults（执行计数0结构证据）、预检缺值/类型错 422 零执行、内部/固定/入口覆盖与未知项 400、环/断开/重复/不存在/空 targets 400、传递跳过、旧调用兼容、预览截断；既有回归全绿：test_flow_executor 33、test_flows 85、test_flow_sql_dialect 25、test_project_flow_source 21、flow_model.test.mjs（含新增镜像用例）全过；前端 22 套件 21 过（mapping_forms 为他人 v2.1 既有基线）；typecheck+build 通过；隔离实例 18879 浏览器验收：B–D 12/36/18 逐节点输出与输入切换（B{x:10}/C{x:12}/D{x:36}）；A/E『本次不执行』；缺值前置拒绝；B 失败→C/D 未执行、C 失败→B 保留；范围外 A 公式清空不阻断；配置/输入变化旧结果徽标；整条 X=10→E=1800；运行项目真实显示；1440/1280/1024 截图+768 可用；设计↔测试往返保留画布/选中/输入/结果；真实 18765 实例与用户数据零写入，隔离实例与临时根已清理
- 下一步：用户验收：编排列表→编辑→测试片段（默认起止为首末节点，按需改 B→D）体验独立测试视图；限制（如实记录）：F09 侧路分支/F16 属性返回往返/F18 大数据/F19 写节点确认帧未浏览器实测（后端语义均有断言；F16 机制未动且 test_project_flow_source 21 步过）；跨账号用例由 storage owner 既有测试覆盖；mapping_forms 既有基线失败待负责人处理
- 依据/文档：workbench/flow_test_plan.py + flow_routes.py + flow_executor.py（isolated 全链路）；frontend/src/flow/FlowTestWorkspace.vue + FlowEditor.vue + NodeConfig.vue + flowModel.ts；tests/test_flow_test_plan.py（51 项）+ tests/flow_model.test.mjs（镜像用例）；文档/需求/20260919_函数编排配置与调试优化/开发计划.md §11 实施记录；文档/接口文档/04-编排与LLM接口.md §3.1 + README 变更记录
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

# Codex / zcode 共享上下文

上下文版本：`ad6274a30790fa51`

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

### 项目映射普通模式与严格模式 · codex · 已确认决定

时间：2026-09-19T06:16:41.215445+00:00；记录：`.collaboration/entries/000020-afc6ebfeeb98.json`

用户确认项目映射支持普通模式（自然语言描述，由大模型在查询时理解）与严格模式（现有结构化映射及编排）；严格模式仍是主力。当前仅讨论，未授权实施。

- 决定：普通模式以自然语言记录对象来源与属性取值规则，不强制先生成固定 SQL 或编排。；保留严格模式作为主力建设方向；普通模式作为补充，不替代已有结构化配置。
- 验证：本轮仅确认产品方向，无业务代码或数据改动，无运行验证。
- 下一步：继续讨论模式选择粒度、两种配置的切换和生效规则；这些细节尚未确认。

### 按用户要求回退关系导入（01dc2e8） · zcode · 已实施，待验收

时间：2026-09-19T06:04:01.160520+00:00；记录：`.collaboration/entries/000019-363354d092ba.json`

定位并回退 01dc2e8（Excel 模板 v2 关系导入）：git revert 保留历史（回退提交 5ceb016）。导入功能恢复 v1 范围：仅支持对象/属性/规则/动作四类定义；下载入口指回 ontology-import-v1.xlsx，v2 模板与含关系填充稿移除，excelImport/importPlan/导入弹窗恢复 v1 行为，测试回到 18 项。协作交接记录（000017/000018）按「历史不删」原则保留，未随 revert 删除。对象-属性挂接、链接、规则/动作关联仍走页面维护。

- 决定：导入范围维持四类定义（用户决定）；协作交接记录不随业务 revert 删除，回退以新提交记录
- 验证：npm run build 通过；ontology_import 18/18 全过；OntologyHome 下载链接确认 v1；dist/templates 仅含 v1 模板
- 下一步：用户侧：刷新 18765 页面（重新构建过）后即回到 v1 导入；之前的储能本体全量数据_20260918.xlsx（四类定义版）仍可正常导入
- 依据/文档：git 5ceb016；frontend/src/ontology/importPlan.ts；frontend/src/ontology/excelImport.ts

### Excel 模板 v2：关系导入（对象-属性/链接/规则动作关联）+ 储能本体全量导入实测 · zcode · 已实施，待验收

时间：2026-09-18T17:16:25.487886+00:00；记录：`.collaboration/entries/000018-e48d5ebf4629.json`

按用户指令实现模板 v2 并用储能本体全量数据完成导入实测。模板 v2：属性/规则/动作表新增「关联对象」列（顿号分隔多值、填名称不填 id），新增「链接」表（名称/定义/起点/终点/数量关系下拉/反向名称），下载入口切到 v2，v1 旧文件完全兼容（链接表与关联列均可缺省）。导入器：链接唯一性为「名称+起点+目标」三元组（真实草稿中存在两条同名所属于，端点不同），同名策略按三元组生效；关联对象两阶段解析（本批定义优先、其次草稿已有），解析不到记填写问题整批阻断并定位单元格；跳过行关联列不生效并在预览注明；落库与页面一致（属性=addReference 同构引用节点，规则/动作=businessRuleAssociations/actionAssociations，链接=owl:ObjectProperty）。完成摘要补「链接新增 N 项」。

- 决定：链接身份=名称+起点+目标三元组（名称不唯一）；关联列只在 create/rename 行生效，跳过行不生效并注明；引用一律填名称由导入器解析为稳定 id，解析失败阻断整批；私有属性沿用 v1 约定进共享库再挂回对象
- 验证：单测 ontology_import 18→21（⑲ 链接表/⑳ 关联对象列/㉑ 草稿同名策略），全量前端套件通过（唯一失败 mapping_forms 既有基线）；build 通过；真实回环：储能本体草稿只读全量提取（对象5/链接4/共享6/私有4/引用对24/规则1关联2/动作1关联2）→ 填入 v2 模板 → 隔离实例 18852 新账号新本体浏览器导入：检查 21 新增 0 问题 → 确认 → 摘要「对象5/共享属性10/链接4/规则1/动作1」→ 与源草稿逐项比对全部一致（含 24 引用对与链接三元组）；确认框（z-150 修复）真实点击可用；隔离实例与临时数据已清理
- 下一步：用户验收：文档/导出/储能本体_全量数据_含关系_20260919.xlsx 可导入任意新空本体复刻储能本体全部内容（含关系）；概览页「24 项缺名称或业务定义」把引用节点计入，为既有展示口径（页面手工引用同样触发），待后续需求确认是否调整；已知范围外：functions 计算契约不在模板范围，需页面维护
- 依据/文档：frontend/src/ontology/excelImport.ts；frontend/src/ontology/importPlan.ts；frontend/public/templates/ontology-import-v2.xlsx；文档/导出/储能本体_全量数据_含关系_20260919.xlsx；文档/需求/20260917_本体Excel模板下载与导入/开发计划.md

### 导入仍报错（TDZ）排查与确认框层叠修复 · zcode · 已实施，待验收

时间：2026-09-18T16:22:13.653662+00:00；记录：`.collaboration/entries/000017-e63cbb883545.json`

用户刷新后仍见 Cannot access 'P' before initialization。服务端日志取证：用户浏览器在当前构建上无任何分块 404；同时段存在多条 0d12b233 的 validate/projects/versions 401（会话失效的旧页面）且全程零次 auth-login、零次 /api/save——用户从未通过导入确认。本轮穷尽复现：当前构建上导入四条 UI 分支（全新增/同名跳过/自动重命名/填写问题）、xlsx 解析层 10 类真实 Excel 特征内容矩阵（公式/日期/合并/隐藏列/富文本等，Node 与浏览器同实现）、401 会话失效→登录页切换，全部零报错，字面 TDZ 未能在当前构建复现。但发现并修复一个必然命中用户的真实缺陷：appConfirm 确认框 z-index:100 被导入弹窗 .imp-backdrop z-index:120 压住——从导入弹窗点「导入 N 项」后确认框不可见、无法点击，整屏背板吞掉所有点击，页面表现为假死，与日志零保存完全吻合。修复（f573967）：appConfirm 背板加 app-confirm-backdrop 类定 z-index:150，高于一切业务弹层；横幅文案补「刷新仍出现请附控制台报错」。

- 决定：确认框必须压过一切业务弹层：appConfirm 背板专用类 app-confirm-backdrop z-150（高于导入弹窗 120、行菜单/用户菜单 115/120，低于横幅 200）；排查结论口径：当前构建各导入路径未复现字面 TDZ；用户报错最可能来自会话失效的旧页面（日志 401 风暴佐证）或确认框假死被误读，需用户提供文件与控制台截图才能最终定位字面 'P'
- 验证：隔离实例 18851 真实浏览器修复后全链路：登录→导入弹窗→检查 17 项→导入→确认框可见可点（z=150，elementFromPoint 命中确定按钮）→确定→「导入完成 对象5/共享属性10/规则1/动作1」，服务端日志出现 POST /api/save 200，console 0 错误；xlsx 解析矩阵（Node，与浏览器同实现）：公式/日期/合并/布尔错误/表头变体/隐藏额外列/特殊字符/空缺表/富文本/300 行共 10 类全部正常解析；前端 mjs 套件全过（含 ontology_import 18、save_queue 22、ontology_lazy_load 11）；唯一失败 mapping_forms 为既有基线（断言项目映射表单渲染，与本次无关）；build 通过（新入口 index-HajE0iiA.js），真实服务 18765 无需重启已在提供新构建；隔离实例与临时数据已清理
- 下一步：用户侧操作：关掉所有旧标签页，统一用 127.0.0.1:18765 一个地址，刷新后重新登录（日志显示 localhost 主机多个标签页处于登出态且从未重新登录），再试导入；若仍出现 TDZ 字面报错：请用户提供所导入的 Excel 文件 + F12 控制台完整报错截图（含堆栈），当前构建下无法进一步定位字面 'P'；admin 侧 0d12b233 本体的导入如再次遇到确认后无反应，属已修复缺陷，刷新页面后重试即可
- 依据/文档：frontend/src/shared/appConfirm.ts；frontend/src/style.css；frontend/src/main.ts；.runtime/server.log（401 风暴与零保存证据）

### Excel 导入视觉重做 · zcode · 已实施，待验收

时间：2026-09-18T13:06:03.260038+00:00；记录：`.collaboration/entries/000016-b889cac044d7.json`

用户报导入时 ReferenceError: Cannot access 'A' before initialization。根因确诊（有服务端日志实证，非猜测）：该 JD 页面运行的是旧构建分块 index-D6QLQfnR.js，而 20:43 重新构建时 Vite 清空 dist、该文件已删除——20:57:11 日志记录该分块请求 404。旧页面在断掉的模块图上做动态 import，浏览器抛出压缩后的 TDZ 类错误与 importModule is not defined。导入功能本身完好：同构建新页面在隔离实例跑完整链路（上传→检查→确认→完成「对象5/共享属性10/规则1/动作1」）无任何错误。已加两层兜底：① excelImport 的动态 import 失败转可读文案（含刷新指引 + 保留原始信息）；② 入口 main.ts 全局识别旧页面类错误（TDZ/分块 404/importModule），顶部横幅提示并给「刷新页面」按钮，覆盖所有懒加载路径。导入弹窗 runCheck 也捕获异常显示可读提示而非静默崩。

- 决定：旧页面（重新构建后仍在运行）的加载失败要有可读指引：入口横幅 + 刷新按钮；动态 import 失败转可读文案；识别模式覆盖：dynamically imported module / before initialization（TDZ）/ importModule is not defined / loading chunk failed / failed to load module script
- 验证：根因实证：服务端日志 20:57:11 "GET /assets/index-D6QLQfnR.js 404"（该分块属旧构建，已被重新构建删除）；当前 dist 仅含 index-Bx2Ytpfj.js 与 xlsx-B2eTCt_Q.js；同构建新页面在隔离实例 18850 跑完整导入链路：文件卡→检查(17 新增)→确认→「导入完成 对象新增 5 项，共享属性新增 10 项，业务规则新增 1 项，动作新增 1 项」；全程 console 错误 0；旧页面路径也在旧 bundle 上验证过：打开弹窗与检查均正常（说明导入代码本身无 TDZ）；兜底生效验证：加载新 bundle 后派发 "Cannot access 'A' before initialization" → 顶部横幅出现「页面资源已更新（工作台可能刚重新构建过），请刷新页面后重试。刷新页面」；ontology_import 测试 16→18 项全过（新增 ⑰ 分块失败文案、⑱ 入口兜底注册）；typecheck/build 通过；全量 mjs 21/22（唯一败 mapping_forms 为既有基线）
- 下一步：用户刷新页面（Ctrl/Cmd+Shift+R）即可恢复；页面已具备下次遇到同类情况时的自解释横幅；建议：以后重新构建后，已打开的旧页面需手动刷新一次（这是按内容 hash 缓存的前端产物的固有行为）
- 依据/文档：frontend/src/main.ts；frontend/src/ontology/excelImport.ts；frontend/src/ontology/OntologyImport.vue；tests/ontology_import.test.mjs

### 登录与账号体系 · zcode · 已实施，待验收

时间：2026-09-18T12:33:35.245474+00:00；记录：`.collaboration/entries/000014-d2bdba7c4a25.json`

按用户确认的四项决定实施：完全隔离、开放注册、密钥按账号隔离、admin/admin。存储层新增 wb_users/wb_sessions/wb_user_settings，wb_assets 与 wb_model_configs 归属列 + 唯一约束改按账号（Alembic 0002，SQLite 表重建）；auth.py 提供 PBKDF2 口令、会话（库中只存摘要）与请求用户上下文；server.py 加鉴权门（仅 4 个认证端点免登录，其余 401）与 Set-Cookie；八个业务模块全部接线归属；前端加登录/注册页 + 启动引导 + 401 回落 + 偏好按账号前缀；transfer 新增 create-user/assign-owner（后者对 LLM 密钥重加密）。真实库已迁移并运行：备份→停服→init→create-user admin→assign-owner→启服；admin 可见全部存量数据。

- 决定：数据完全隔离（每账号独立本体/项目/编排/配置），跨账号 id 一律按不存在处理；开放注册；归属过滤收口在存储层（assets 对外函数必带 owner_user_id；用户级设置走新表 wb_user_settings），域层统一 auth.require_user_id()；免登录接口也解会话（auth-state 才能报告已登录用户，避免刷新误判）；退出与 401 走同一引导回调；口令 PBKDF2-HMAC-SHA256 600000 轮（Python 3.9 无 hashlib.scrypt，勿改回）；会话令牌随机 32 字节、库中只存摘要；assign-owner 改归属必须重加密 LLM 密钥（AES-GCM 的 AAD 绑定 owner_key），解密失败保留原行并明确报出
- 验证：新增 tests/test_auth.py 63 项全过；后端 tests/run.py all 30/30（既有套件改为带会话 Cookie/绑定测试账号）；前端 typecheck/build 通过；mjs 21/22（唯一败 mapping_forms 经基线对照为既有失败）；ontology_lazy_load 补登录态后 11/11；隔离实例浏览器全链路：登录页→注册→空白空间→建本体→刷新保持登录→退出回登录页→换账号只见自己数据→清会话后 401 自动回登录页；真实库迁移：备份（含根密钥 0600）→ schema 0001→0002 → admin 创建 → 归属 6 资产/2 模型配置/1 设置/2 密钥 → 启服；FK 空、integrity ok、317 快照不变；admin 见 2 本体/1 项目/2 编排/2 模型配置；修复实施中发现 4 个缺陷：auth-state 不解会话、退出不切界面、assign-owner 破坏密钥解密、start.sh 用业务接口探测就绪
- 下一步：用户验收；admin 弱口令建议更换（transfer create-user --username admin --password <新> --reset-password）；本期未做：忘记密码、账号禁用/删除、管理员用户管理页
- 依据/文档：文档/接口文档/06-认证与账户接口.md；文档/需求/20260918_登录与账号体系/开发计划.md；workbench/auth.py；tests/test_auth.py；AGENTS.md

### 储能本体全量数据导出 Excel 与导入功能实测 · zcode · 已验证

时间：2026-09-18T12:01:51.986010+00:00；记录：`.collaboration/entries/000013-d006fbacb40d.json`

把当前「储能本体」草稿全部业务内容（对象5/属性10/规则1/动作1，共17条）填入官方模板生成 Excel（文档/导出/储能本体_全量数据_20260918.xlsx，保留 C→D 联动与隐藏枚举列），并在隔离实例上真实浏览器完整实测导入功能：上传→检查预览→同名策略→确认导入→刷新持久化全部通过；含异常场景与解析回归 16/16。未改产品代码，未触碰真实 ontology/ 与 data/。提交 609bd0c。

- 决定：导出 Excel 以官方模板为基底填充，保留下拉/联动/隐藏列，不另造表头；对象私有属性按模板能力一并导出为属性行，导入后进共享属性库（需求 §6 设计）；链接/对象-属性关联/函数/动作与规则挂接不在模板范围，导出不含，需人工在页面重建；测试用 git archive HEAD + WIZ_WORKBENCH_ROOT/PORT 隔离实例，规避工作区在途未提交改动
- 验证：tests/ontology_import.test.mjs 16/16 通过；Node 直跑前端同实现解析/计划层：17 行 0 结构问题，双策略与落库形态（时间序列 valueShape、显示格式原文）正确；隔离实例 18768 真实浏览器：预览 17 新增 0 问题；确认后「对象新增5/共享属性10/规则1/动作1」；刷新后 5/10/1/1 完整；同名策略实测：跳过=2跳过+1新增；重命名=储能簇_949811、soc_995640 随机后缀；校验阻断：非时间序列带观测值/时间序列缺观测值/非法枚举/动作缺效果 4 项准确定位且确认禁用；.xls 与 1.1MiB 被拒；异常后数据未污染；逐字段比对原快照与导入结果：名称/定义/类型一致；已知差异为格式化结构字段只保留 instruction 原文（模板范围决定）
- 下一步：用户复核 Excel 与报告（截图 4 张在 文档/导出/储能本体Excel导入测试_20260918/）；对现有储能本体重复导入默认跳过 13 条同名、新增 4 条属性；如需全量重建用重命名或清空；工作区在途未提交的账号体系改动（schema owner_user_id 未同步写入代码）会使新库创建本体 500，建议该负责人修复
- 依据/文档：文档/导出/储能本体_全量数据_20260918.xlsx；文档/导出/储能本体Excel导入测试_20260918/测试报告.md；frontend/src/ontology/excelImport.ts；frontend/src/ontology/OntologyImport.vue；文档/需求/20260917_本体Excel模板下载与导入/需求说明.md

### 本体列表统一设计-v1 · zcode · 已实施，待验收

时间：2026-09-18T11:44:13.182086+00:00；记录：`.collaboration/entries/000012-4374db6b6ff2.json`

搜索框与分页条组件化：新增 shared/SearchField.vue（放大镜图标 + 自绘清空，受控 update:value/clear）与 shared/ListPager.vue（第 x–y 项，共 N 项 / 每页 P 项 /‹ n/N ›，禁用越界，上抛绝对页码）。OntologyList 工具栏与分页、ObjectWorkspace 对象导航搜索与分页全部接入，页面不再有手写搜索/分页结构；OntDrawer 焦点回落改为 .search-field input。修复实施中发现的两个真实缺陷：① 全局 button:active{transform:translateY(1px)} 覆盖清空按钮的 translateY(-50%) 居中，真实点击在 mousedown 后漂移到输入框而丢失（已用 :active 保留居中修复）；② 浏览器原生 search 清除按钮与自绘按钮重叠（已隐藏原生按钮）。

- 决定：列表搜索框与分页条统一为共用组件（SearchField/ListPager），表格与对象导航同款；清空先发空值再发 clear，供父级一并复位筛选（对象导航同时复位「已收藏」）；分页组件上抛绝对页码，表格内外语义由调用方换算
- 验证：18765 浏览器实测：导航与表格搜索框均带图标；清空真实点击后输入清空、行数回满（导航 4→5 行、表格 2→6 行）；三资产库与四页签分页文案统一（第 1–6 项，共 6 项／每页 20 项／1 / 1）；旧类名清零（ont-search=0、ld-pager-ctl=0）；新增 tests/list_controls.test.mjs 4 项通过（事件协议/文案与禁用/接入面/清空按钮居中回归）；typecheck、build 通过；全量前端套件 21/22（唯一败 mapping_forms 为既有基线）
- 下一步：用户复核视觉（图标与分页文案）
- 依据/文档：frontend/src/shared/SearchField.vue；frontend/src/shared/ListPager.vue；frontend/src/shared/OntologyList.vue；frontend/src/ontology/ObjectWorkspace.vue；tests/list_controls.test.mjs

### 用户菜单裁切修复 · zcode · 已实施，待验收

时间：2026-09-18T11:20:23.792670+00:00；记录：`.collaboration/entries/000009-3d836591ac4f.json`

修复左下用户菜单被侧栏裁切：菜单由 .rail 内 position:absolute 改为渲染到 body 的 fixed 浮层（App.vue Teleport + placeUserMenu 按触发器矩形定位/视口收敛），侧栏 overflow:hidden 与 224px 宽度不再裁剪 280px 菜单。顺带补齐需求 §8 要求的 Esc 关闭（原实现缺失）：Esc 关闭并归还焦点到触发器。已在运行中的 18765 实测：展开/收起两种侧栏形态下菜单完整可见（280px 全宽、右缘 292/295.5 均在视口内）、点外关闭、Esc 关闭+焦点归还、菜单内「设置」正常进入设置页。未改菜单项语义与功能。

- 决定：用户菜单改用 body 级 fixed 浮层（与 RowMenu 同一模式），位置由触发器矩形计算——不放开 .rail 的 overflow（那会破坏导航独立滚动）；Esc 关闭为本轮补的功能缺口：菜单原只支持上下键/Home/End/Tab，需求 §8「菜单支持方向键与 Esc」要求补齐
- 验证：18765 实测：菜单 rect 280px 完整可见、位于 body、position:fixed；展开态右缘 292 与收起态 295.5 均在视口内；Esc 关闭 + 焦点归还触发器（真实键盘事件）；点外关闭；菜单「设置」进入 #settings-models 且菜单关闭；npm run typecheck 与 npm run build 通过；global_settings_nav 测试补 ⑦ 用例锁定位计算与 Esc 行为：7/7 通过；全量前端套件 20/21：唯一败 mapping_forms 为既有基线失败（与本轮无关，未修）
- 下一步：用户复核视觉效果；mapping_forms 既有失败仍待其负责人处理
- 依据/文档：frontend/src/App.vue；frontend/src/style.css；tests/global_settings_nav.test.mjs

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

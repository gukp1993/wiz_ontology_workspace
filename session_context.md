# Codex / zcode 共享上下文

上下文版本：`d009115652333ca0`

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

### acceptance-fixes集成与清理 · codex · 已验证

时间：2026-09-21T01:56:43.176952+00:00；记录：`.collaboration/entries/000140-148df3046633.json`

用户授权合并与清理已完成：main合并提交d8e3577，组合回归通过；acceptance-fixes及临时integration工作树、已合并分支与登记隔离环境已清理，18921任务服务已停止。

- 决定：两项无重叠main未提交文档按用户明确要求原样保留并核对SHA256；主18765未重启，其他任务工作树未动
- 验证：全量后端40/40、独立43/43、App挂载3/3、A02及npm build通过；合并后两项原文件SHA256一致；删除前分支均为main祖先；无其他未提交或未知忽略资产
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §15；文档/需求/20260920_本体与项目统一维护体验改版/C01独立验收报告_20260921.md

### acceptance-fixes：交付 C01 修复的独立验收执行指令 · zcode · 需求已交付

时间：2026-09-21T01:44:50.260627+00:00；记录：`.collaboration/entries/000138-36841ee35010.json`

按用户要求交付可直接复制给其他 harness/人工验收者的 C01 独立验收指令：文档/需求/20260920_本体与项目统一维护体验改版/独立验收执行指令_20260921_C01.md（提交 233eaac，纯文档）。指令自包含：被验业务提交 1ec22a7（父 113e224 业务等价 9d0fa6f）、C01 根因与四组空白 ID 修复前后对照表、10 步必做验证（反例复跑 42/42、git archive 9d0fa6f 反例有效性、92 项新增断言、边界自扩展、金样 98/515 且旧样例逐字节不变、run.py all 40/40、原五项 43/43、diff 范围与四个业务文件零 diff、文档-代码一致性、可选 HTTP）、只读边界（不改文件/不合并/不重启 18921）与交付格式。未改业务代码。

- 决定：验收指令写成仓库内可持久文件而非仅 /tmp，避免 /tmp 清理后丢失；同时对 /tmp 原报告提供自包含回退。；指令明确标注 18921 未重启、未加载本轮修复，禁止以其实测判定 C01，也禁止重启它；活服务验证须自起自关 ≥18931 隔离实例。；指令要求反例有效性必须亲跑（9d0fa6f 副本应失败 25 项），并允许验收方自建等价反例，避免只复跑既有用例。
- 验证：提交前核对：git diff 1ec22a7..HEAD -- workbench/ tests/ 为空；113e224..1ec22a7 仅含 project_validation.py + 4 个测试文件 + 金样 + 3 份接口文档 + 开发计划 + 交接记录；flows/flow_http/project_routes/projects 零 diff；9d0fa6f..113e224 业务零 diff。；指令中引用的实测计数均来自本轮亲跑：反例修复后 42/42；扩展断言 92/92；9d0fa6f 副本 67 通过/25 失败；金样 98 样例/515 断言；run.py all 40/40；原五项 43/43。；git status 干净、git diff --check 通过。
- 下一步：用户将该指令全文交给 Codex 或其他 harness 执行独立验收。；验收通过后停在「待用户授权集成」；本轮无合并授权。
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/独立验收执行指令_20260921_C01.md；文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §14.5；提交 1ec22a7（修复主体）、233eaac（本指令）

### acceptance-fixes：C01 空白依赖引用漏检修复（B01/B02 第二轮验收补充） · zcode · 已实施，待验收

时间：2026-09-21T01:38:19.847606+00:00；记录：`.collaboration/entries/000137-48ec23555317.json`

按 /tmp/B01B02_独立验收_20260921/修复指令.md 补修 C01 并自测通过，提交待独立验收。根因：适配层 _flow_declares_provider/_flow_references_credential 用 .strip() 判「是否声明依赖」，底层 flows/flow_http 用原始非空字符串判「有没有引用」，纯空白 providerId/credentialId 因此被当成未声明→跳过检查→目录读取失败时 fail-open 放行。修复：新增 _raw_id_present 作为唯一声明判据（bool(str(impl.get(key) or ''))，不 trim），空白 ID 按「已声明但无效」处理——目录可读判不存在、读取失败按三态转阻断；键缺失/None/空串仍按未声明。文档先行（04 §2.2 判据段、03 §2.2 空白 ID 条、README 行），未合并 main、未重启 18765、未改真实数据。

- 决定：声明判据统一为原始值的非空字符串（与检查器底层逐字一致），不 strip、不 trim、不重写既有 ID；空白 ID 不得被降级为「未声明」。；键缺失/None/空字符串仍按未声明：不读取无关目录、不被无关故障误伤，保持既有兼容语义与编辑页 None 未知上下文行为。；HTTP 节点识别沿用 flows._check_body 分派口径（node.kind or impl.language），不新造识别规则。；金样采用增量追加：只加 57_c01_blank_provider_reference（固定 flowId 播种），旧 97 样例逐字节不变；新增 check_c01_samples 结构断言防止误删后「重新生成即通过」。
- 验证：反例先复现（隔离根，验收方脚本）：provider 空白两态 errors=[] 且 200 新增 v1；credential 空白读取失败态同型 200 v1——与验收报告 C01 四组表一致。；反例有效性：git archive 9d0fa6f 解到 /tmp 跑新断言 → 67 通过 / 25 失败；修复后同文件 92/92 全绿。；新增覆盖：空白矩阵（空格/Tab/换行/混合 × provider/credential × 空集合/读取失败 16 项）、非空集合对照 2 项、真正未填写三态 6 项、正常 ID 三态对照 2 项、动作绑定路径 1 项、发布路由四组 20 项（422/零版本/revision 不推进/重复请求/不回显注入原文）、改正 ID 后可恢复发布 2 项。；串行回归（本树 venv，清继承 WIZ_*）：test_flow_dependency_context 92/92、test_project_flow_source 44 步、test_publish_guards 16 步、test_publish_guards_adversarial 135/135、test_validation_split 98 样例/515 断言、test_business_rules 57、test_action_library 77、test_catalog_independent 51 硬断言；原五项独立脚本 codex_reacceptance_backend_20260920 43/43；tests/run.py all 40/40。
- 下一步：Codex/独立 harness 验收本提交，重点复跑四组空白 ID 反例（修复前 200、修复后 422 零写入）与空白矩阵边界。；验收通过后停在「待用户授权集成」；本轮无合并授权，不得自行合并 main 或清理 worktree。；未测：浏览器（前端零改动，18921 未重启仍跑上一被验代码）、真实 MySQL/Redis、真实项目故障注入；mapping_forms 既有失败与辅助填写原型属范围外。
- 依据/文档：/tmp/B01B02_独立验收_20260921/修复指令.md 与 验收报告.md（C01 / P2）；文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §14.5（本轮实施与复验记录）；文档/接口文档/03-项目区接口.md §2.2、04-编排与LLM接口.md §2.2、README.md 变更记录（C01 行）；workbench/project_validation.py（_raw_id_present/_flow_declares_provider/_flow_references_credential）；tests/test_flow_dependency_context.py、tests/test_validation_split.py、tests/golden_c01_flow.py、tests/fixtures/validation_golden.json

### acceptance-fixes：B01/B02 外部harness验收指令交付 · codex · 需求已交付

时间：2026-09-21T01:16:51.169803+00:00；记录：`.collaboration/entries/000136-bb724d0bb774.json`

应用户要求交付可直接复制给其他 harness 的 B01/B02 独立验收执行指令（纯文档，无业务改动）：文档/需求/20260920_本体与项目统一维护体验改版/独立验收执行指令_20260921_B01B02.md。内容自包含：被验树/分支/SHA（业务9d0fa6f，文档HEAD 6516f51）、只读边界与禁止项（不改文件/不合并/不触碰18921/端口≥18931自起自关/临时产物进/tmp）、三态判定标准、8步必做验证（含git archive反例有效性与已知命令）、第一轮验收P3四项要求抽查、交付格式与不写context record的约定；明确14:00另一ticket与mapping_forms既有失败为范围外。

- 验证：指令文件内路径、命令与SHA均按当前被验树实况核对（HEAD 6516f51、9d0fa6f..HEAD 业务diff为空已实测）。
- 下一步：用户可将该文件全文交给其他 harness 做第二轮独立验收；结果按同格式返回后由协调者核对迭代。；仍停在待用户授权集成；未合并main、未重启18765、未改真实数据。
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/独立验收执行指令_20260921_B01B02.md

### acceptance-fixes B01/B02 独立验收 · codex · 已验证

时间：2026-09-21T01:13:19.982863+00:00；记录：`.collaboration/entries/000135-7f2774ed5f87.json`

子代理只读独立验收被验提交9d0fa6f：通过，无本批引入P1/P2。必测矩阵M01-M13全过：新测试38/38、run.py all 40/40（金样逐字节等价）、原五项独立脚本43/43、前端37/38（唯一失败mapping_forms为基线既有P02范围外）；git archive 6955e76副本复跑新测试24过/14败证明反例有效（失败项恰为B01空连接发布200与B02提供方/凭据读取异常发布200）；自起18932真实HTTP端到端13/13（validate报错、publish 422零版本、改合法后200）。文档-代码三态协议逐条一致，flows/flow_http/project_routes零diff，密钥不回显。验收全程未改项目文件、未触碰18921、未合并main。结论与4项P3登记已写入开发计划§14.4（含P3-2更正实施者23/15为24/14记录偏差）。

- 决定：P3-1空白串providerId/credentialId在目录读取失败时的窄窗fail-open不在本批修复：修它会产生新业务提交使已验收SHA作废需重验；登记为后续独立修复项（统一strip口径）。；P3-4金样未新增空连接样例不判违规：本轮指令要求核对金样不盲目生成，金样逐字节等价即通过，登记为建议。；验收方浏览器未逐屏实测（后端校验缺陷，以真实HTTP+路由函数+进程内三层证据替代），18921不写入。
- 验证：被验树验收结束HEAD=9d0fa6f、git status干净；18921（PID 82961）只读确认存活且加载被验代码。；验收报告全文见本轮交接与 文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §14.4。
- 下一步：停在待用户授权集成；如授权合并，按AGENTS串行集成重验（含P3-1是否另起ticket由用户决定）。；18921与worktree保留至集成指令；未重启18765、未改真实数据。
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §14.4；被验提交 9d0fa6f（codex/acceptance-fixes）

### acceptance-fixes：B01/B02 编排依赖上下文三态修复 · codex · 已实施，待验收

时间：2026-09-21T00:55:40.307519+00:00；记录：`.collaboration/entries/000134-7563caf68a87.json`

按补充修复指令_20260921修复B01/B02并提交（待独立验收，未合并main）。B01：去掉 connections or None 空列表降级，已知空集合按真实协议传给 check_flow；B02：依赖目录读取失败改为 _READ_FAILED 哨兵，仅当被检编排实际声明该维度依赖（providerId/HTTP credentialId）时输出定位到编排的阻断 error，读取失败不冒充引用不存在、不回显异常原文；同一 validate_project 缓存保留读取状态且顺序无关；编排配置检查全局语义未改。接口文档先行修订（03 §2.2、04 §2.2 三态协议表、README 变更记录）。

- 决定：依赖上下文三态冻结：已知集合（含空）→存在性判定；None/未知→跳过并保留编辑页warning；读取失败→仅被检编排声明该依赖时阻断。；B02 阻断文案含依赖类别与请稍后重试，不含异常原文；llm 读取失败时该维度按 None 传给 check_flow 再前置阻断消息。；账号未配置模型（llm_meta==[] 且编排未声明 providerId）仍降级跳过，不制造误报。；修复前先固化反例：B01 空连接引用Redis编排发布200 v1、B02 凭据/提供方目录读取异常发布200 v1，均为修复前实测证据。
- 验证：tests/test_flow_dependency_context.py（新增）：修复前23通过/15失败复现两缺陷，修复后38/38，含顺序无关、无连带阻断、密钥不回显、恢复后发布v1、发布路由422边界。；python3 tests/run.py all → 40/40（新文件已被 unit 组收集）；test_validation_split 金样逐字节等价；test_publish_guards 16、test_project_flow_source 44、adversarial 135/135、business_rules 57、action_library 77 定向回归全过。；前端六套件 node --import ./tests/ts_hooks.mjs 全过（本批未改前端源码）。；18921 隔离实例（.runtime/venv、.runtime/acceptance-data、假账号b01qoderfix）浏览器复验：校验页阻断提示+条目定位+发布按钮 disabled，真实 HTTP 422；恢复为合法编排后发布 v1；console 0。；未测项（已在开发计划§14.3如实标注）：B02 浏览器内故障注入不可行（以路由/单元层证据替代）、编排画布逐屏点击未做。
- 下一步：交子代理独立验收本提交（对照补充修复指令必测矩阵与验收报告_20260921），不通过则原分支继续修复。；验收通过后停在待用户授权集成；本轮未合并main、未重启18765、未改真实数据；18921 保留供复验。
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/补充修复指令_20260921.md；文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §14.3；workbench/project_validation.py；tests/test_flow_dependency_context.py；文档/接口文档/03-项目区接口.md；文档/接口文档/04-编排与LLM接口.md；文档/接口文档/README.md

### 本体与项目辅助填写交互原型制作与验收循环 · zcode · 已实施，待验收

时间：2026-09-21T01:00:23.378534+00:00；记录：`.collaboration/entries/000133-2dfe15d07508.json`

交付 文档/需求/20260920_本体与项目辅助填写/交互原型_v1.html（单文件离线原型，2017行，SHA256 dd4a350655cdf43ff9a54a99be3dbc111ef31e67a448b936d358f47b9dffd030）。经主会话浏览器自检、外部harness报告F01～F05修复，及4个并行子agent独立验收（G1复验、G2a D1-D4、G2b D5-D7、G3横向+静态）：全部通过，P0=0/P1=0，console 0错0警。8条P2中修复2处文案优先级bug（O5/P6上下文空值占位），其余7条设计级如实记录为待评审。需求说明§9已补真实自检记录。仅原型，未实施正式功能。

- 决定：P2处置拍板：仅修G3空值占位文案bug（一行修、无行为变化）；改答需清空重答、old==neu幂等卡、说明快照残留等7条记录为待评审，避免为风格项重跑整轮验收。；交付基线以修复后 dd4a3506… 为准，§9按另起一条追加、不回改记录。
- 验证：静态：禁用API grep 0命中、http(s)仅演示文本、内联JS node --check通过。；浏览器真实点击链路：F01～F05修复复验+D1～D7全链路+A01～A17；XSS纯文本渲染；1440×900/1280×800/768×1024三档视口；四组会话console 0 error/0 warning/0未捕获异常。；本轮修复后复验：清空预期效果/url→面板摘要显示（空）占位，实测通过。；未覆盖：真机多浏览器矩阵、完整无障碍走查；未连接任何真实模型/数据库。
- 下一步：用户介入验收原型；确认后另议正式实现范围（右侧面板窄屏呈现、本体/项目优先级、复杂编排自动生成是否另立需求）。
- 依据/文档：文档/需求/20260920_本体与项目辅助填写/交互原型_v1.html；文档/需求/20260920_本体与项目辅助填写/需求说明.md

### acceptance-fixes 20260921独立验收 · codex · 已验证

时间：2026-09-20T16:12:50.325785+00:00；记录：`.collaboration/entries/000133-76cc046675e9.json`

被验HEAD 1db8901/业务3f85cf2：原五项反例通过，但新增B01/B02两项P1使非法依赖编排正式发布成功，本轮暂不通过。仅报告与指令，未改业务代码、未合并main。

- 验证：前端六套、后端五套定向回归及npm build通过；隔离SQLite、假账号、正式路由函数：空连接引用Redis发布200 v1；显式模型依赖元数据读取异常发布200 v2；本轮未做浏览器逐屏、真实业务连接或跨进程压测
- 下一步：原worktree按补充修复指令修复B01/B02并交独立验收
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/验收报告_20260921.md；文档/需求/20260920_本体与项目统一维护体验改版/补充修复指令_20260921.md；文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §14

### system-deep-test环境与测试指令 · codex · 需求已交付

时间：2026-09-20T16:05:33.930212+00:00；记录：`.collaboration/entries/000132-9d1caa107fc6.json`

用户要求全方位测试环境并改名test；已创建worktree/test、codex/test，端口18931，测试计划与执行指令在该分支提交；尚未启动或测试。

- 决定：基于main d6c73c2，无其他分支未合入修复；测试harness只测试报告，不改业务代码、不合并。
- 依据/文档：worktree/test/文档/需求/20260921_系统全方位深度测试/执行指令.md；worktree/test/文档/需求/20260921_系统全方位深度测试/测试计划.md

### acceptance-fixes：合并验收五项修复（R01/R02/R03/A01/A02）独立复验 · codex · 已验证

时间：2026-09-20T15:43:39.514910+00:00；记录：`.collaboration/entries/000132-deff257622ab.json`

独立验收通过：被验SHA 3f85cf2（HEAD f5bea60，基线 b0fb7c0）。新增两份隔离验收脚本（后端43项+桥层12项全过）；前端node 7套件+后端6套件及补充回归在清空WIZ_*环境后独立复跑全过；npm build通过；18921隔离实例（假账号codexrv）浏览器验收覆盖登录零console、规则/动作必填与空格阻断、非法旧值编辑不崩溃并字段定位报错、旧模板output零丢失、导入冲突整批不写、合法发布1.0.0读回。验收记录追加至开发计划§13。未合并main、未更新主工作台、未清理worktree。

- 决定：mapping_forms.test.mjs 失败判定为既有失败（main同失败、输入文件本批未触碰、§11.4延期项P02），非本批引入，不阻断验收；adversarial夹具改造判定为加强断言（补合法编排+自检assert），非放松；浏览器新观察：首条规则保存后列表当次会话不即时刷新——该push路径与基线逐字节相同，判定非本批引入，登记为范围外规格观察；项目区发布UI逐屏与图谱弹窗逐屏点击以官方路由级/桥级证据替代并在§13如实标注
- 验证：git status干净（仅本harness两份untracked验收脚本），验收全程HEAD=f5bea60无新增业务提交；tests/codex_reacceptance_backend_20260920.py 43项断言全过（R02校验/路由、R03窗口注入409零写入+重试/回放/冲突、A01发布422零版本等）；tests/codex_reacceptance_a02_20260920.mjs 12项断言全过（domainSaveNode桥层：空格阻断、记录未变、0事件、非文本拒绝、历史键零丢失）；隔离实例 http://127.0.0.1:18921 假账号codexrv浏览器验收，console 0消息；服务资源指纹 index-GpdZRyRU.js 与fresh build一致
- 下一步：等待用户授权集成/合并；本轮未合并main、未重启18765、未清理worktree与18921实例（PID 27586，保留待指令）
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §13；tests/codex_reacceptance_backend_20260920.py；tests/codex_reacceptance_a02_20260920.mjs

### 本体与项目辅助填写原型委托需求 · codex · 需求已交付

时间：2026-09-20T12:04:28.558493+00:00；记录：`.collaboration/entries/000131-082bbd8f1bdb.json`

按用户改为由其他harness制作原型的要求，交付完整需求与独立执行指令；本轮未生成HTML或实施业务功能。

- 决定：本轮两份文档，不生成开发计划；其他harness生成同目录交互原型_v1.html，后续按清单验收。；覆盖本体5类及项目6类辅助填写场景，7条演示链路、17项验收；使用现有风格及离线假数据。；采纳只进入表单，旧建议失效保护、共享影响确认、缺信息追问必须演示；复杂编排生成与真实模型调用不在本次原型必做范围。
- 验证：文档场景/验收编号完整性与两份文件范围检查通过，git diff --check通过；未声称原型或浏览器已验收。
- 下一步：其他harness按执行指令制作独立原型并自检，用户交回后按A01至A17验收。
- 依据/文档：文档/需求/20260920_本体与项目辅助填写/需求说明.md；文档/需求/20260920_本体与项目辅助填写/执行指令.md

### acceptance-fixes：合并验收五项修复（R01/R02/R03/A01/A02） · zcode · 已实施，待验收

时间：2026-09-20T14:17:44.240128+00:00；记录：`.collaboration/entries/000131-fba222697401.json`

五项验收问题全部修复并自测通过，提交 codex/acceptance-fixes，等 Codex 独立验收；未合并 main、未重启主工作台、未改真实数据。R01 App.vue watch 移到 flowState 声明后并新增真实客户端 mount 回归(3/3)；R02 复用 flows.check_flow 把被引用编排自身结构错误纳入项目校验阻断（属性来源与动作绑定两条路径，上下文按真实协议传；夹具修正为合法编排）；R03 目录 payload 与依赖令牌改同一次读取（消除窗口，反例先复现 200 再修成 409）；A01 记录级文本校验（必填必为文本、content/effect 合法选填文本、非文本受控报错、正式发布路由 422 零版本、前端不再 trim 崩溃）；A02 图谱保存复用同一校验（失败零写入零事件）。A02 由子代理实施、协调者汇总。

- 决定：R02 只把编排「自身结构/配置」错误纳入项目阻断；环境性错误（账号未配置模型、项目侧无凭据上下文）按跳过处理，避免误拒合法编排；编排显式声明 providerId 而已删除仍检出。；R02 不修改 flows.check_flow 既有语义，只在项目校验侧复用它并缓存（同一编排多处引用只查一次）。；R03 以「校验 payload 与记录代际同一次读取」达成基线一致，不靠长持锁；目录令牌由 _catalog_meta 派生，baseline.catalogs 同步为同一读取。；A01 统一为「缺失/null/空串/空白按未填；非文本受控报错且定位字段」，不做 str()/String() 掩盖、不自动清洗；后端与前端 recordFields/businessRuleModel/actionModel 同口径。；A01 前端编辑遇非法旧值保留原值并给字段级原因，改写后即可保存。
- 验证：后端 python3 tests/run.py all → 39/39；定向：test_publish_guards 16 步（新增 o/p：payload 读取后目录更新→409 零写入；读取前更新→按新基线校验并发布）、test_project_flow_source 44 步、test_publish_guards_adversarial 135/135（夹具改合法并加自检）、test_business_rules 57 项、test_action_library 77 项、test_catalog_independent 51 硬断言、test_validation_split 97 样例/507 断言，以及 test_action_http/test_references/test_property_sources/test_upgrade_impact/test_storage；前端 npm run typecheck 0；npm run build 通过（仅既有 chunk 体积警告）；tests/*.test.mjs 37 个中 36 个通过。；反例先行：R01 新用例修复前 0/3 报 Cannot access 'flowState' before initialization；R03 反例修复前实测 200 并新增 v4；A01 修复前正式 post_publish 接受对象 content/数组 effect 返回 200。；隔离实例 18921（.runtime/acceptance-data、专属 venv、假账号 acceptfix）浏览器复验：注册进入无初始化异常；规则空必填阻断/合法保存并读回/注入非法 content 编辑不崩溃且给字段原因；动作只填名称被阻断；图谱编辑清空业务定义被阻断且保留输入；空壳编排绑定属性→校验 error「编排输出「功率」尚未绑定来源节点输出」→发布 422，改合法编排→零 error→发布 v1；同 requestId 重试 idempotentReplay:true 且不新增版本。；既有失败（范围外、非本次引入）：tests/mapping_forms.test.mjs 在实施前基线上同样失败（git stash 对照），属已登记延期项 P02；test_catalog_independent 仍打印 2 项规格观察，不在本批范围。
- 下一步：Codex 独立验收 codex/acceptance-fixes 最新提交（建议按 R01→A02 复验，重点核对 R03 两条窗口用例与 R02 的合法编排对照）。；验收通过后停在「待用户授权集成」；不得据此自行合并 main。；隔离实例 18921 与 .runtime/acceptance-data 保留供复验，如需清理请明确指示。；未测项：真实 MySQL/Redis 探测、Excel/WPS 原生下拉与 1000 行截断、跨进程并发压测、主工作台 18765（本批不重启/不部署 dist）。
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/验收修复执行指令.md；文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md §12.4；文档/接口文档/02-本体区接口.md §4.9、03-项目区接口.md §2.2、README.md 变更记录；workbench/project_validation.py、workbench/project_routes.py、workbench/workflow.py、frontend/src/App.vue、frontend/src/ontology/recordFields.ts、frontend/src/ontology/legacyGraph/legacyBridge.js；tests/app_client_mount.test.mjs（新增）

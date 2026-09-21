# Codex / zcode 共享上下文

上下文版本：`456188183db5aef1`

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

### codex/ontology-build 第3轮独立验收 · codex · 已验证

时间：2026-09-21T02:37:13.141273+00:00；记录：`.collaboration/entries/000129-5e738db9174e.json`

基线43dddc8验收不通过。既有160/160构建、95/95解析、后端41/41与前端构建通过，新增独立反例复现4项P1：取消重试旧worker写入、多轮排除丢失、合并后交付引用断裂、序列编辑协议不一致。报告附录B已更正附录A通过结论，归档3份探针。

- 决定：待修复复验，不合并main，不依据旧通过结论清理环境。；仅验收文档与证据，不改业务代码，不启停已有服务。
- 验证：双worker真实隔离SQLite探针、域函数mock读取探针、序列编辑payload探针均复现。；既有自动化与前端build通过；任务回链ID丢失静态确认（P2）。；未做浏览器交互、真实模型语义质量、与最新main组合验收。
- 下一步：修复R3-01～04并补回归后再验收。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/验收报告.md 附录B；文档/需求/20260920_从物料自动构建本体/验收证据_第三轮/

### 从物料自动构建本体 第2轮独立复验 · codex · 已验证

时间：2026-09-21T02:29:01.217346+00:00；记录：`.collaboration/entries/000128-19a06a79a19b.json`

第2轮复验结论：通过。D01-D19全部修复并在f52207f+报告commit上运行时复验确认；验收报告附录A已提交。停在待用户授权集成，未合并main、未删开发环境、未重启主工作台18765。

- 决定：修复提交：a6e3dfb(D01/D03/D05/D07/D10-D12/D14残留)、70231bd(D16/D17)、8c381d6(D04/D09/D15前端)、dd81187(D02/D13/D18/D19协议与解析)、f52207f(D06本体装配)，另有附录A验收报告commit；验收边界：G15浏览器视觉/键盘、G16真实模型语义、OCR、limits标定、T12/T20故障注入保持未验收，不标通过；分支基线滞后main（缺b690a1a规则动作字段精简），集成时需与main组合重验
- 验证：tests/test_ontology_build.py 160/160；test_ontology_build_parsers.py 95/95；run.py all 41/41；npm run build 0错误；node tests/ontology_build_frontend.test.mjs 8组通过（仓库根cwd）；18871隔离实例运行时探针：V1全流程交付(含timeSeries+rule+action+link)、V2错误码/CAS、D06复核ALL PASS；G01/G14账号隔离回归通过；未连接真实业务库，未读取主环境密钥
- 下一步：等待用户明确集成/合并指令；合并前在临时集成分支与main(b690a1a)组合重验；遗留非阻塞项见验收报告§11与附录A.3：confirm_scope/run-resume 400 vs 422、parseTimeoutSeconds未实现等
- 依据/文档：文档/需求/20260920_从物料自动构建本体/验收报告.md；branch codex/ontology-build @ f52207f+报告commit

### 从物料自动构建本体 · Codex第1轮独立验收 · codex · 已验证

时间：2026-09-20T16:05:18.233763+00:00；记录：`.collaboration/entries/000127-2a7750680301.json`

验收 6a7033f 不通过：P0 六项（>384B分片上传必400、timeSeries交付必败且预检放行、合并候选复活进交付集、再生成复活人工排除、前端五处revision错配UI操作全断、规则落metadata不可见）+P1 十三项；主链路协议级端到端与小文件全流程实测可通。报告提交 7013a53（验收文档，独立于业务提交）。

- 决定：缺陷编号 D01-D19，P0/P1 必须修复、P2/P3 可后置；修复在 codex/ontology-build 原 worktree 继续，不合并 main；浏览器点击级验证因内嵌视口不可用改以前端payload精确HTTP复现替代，G15/T24 保持未验收；真实模型语义(T25/G16)与OCR、限额校准维持未验证，不并入通过结论
- 验证：test_ontology_build 51/51、tests/run.py all 40/40、npm run build、前端node测试35/36（mapping_forms为main既有失败）独立复跑；隔离实例18871重启至HEAD后协议级E2E：小文件全流程、证据降级、合并撤销(正确token)、交付幂等实测通过；2733B chunk 400、timeSeries交付400、合并后列表复活、b2排除b3复活include、merge/undo/regenerate/exclude假冲突或400运行时坐实；跨账号404/未登录401/路径穿越与ZIP拦截复测通过
- 下一步：修复方按 D01-D19 在原worktree修复并提交，禁止合并main；Codex按D编号逐项复验后更新验收报告附录A；集成合并仍需用户明确授权
- 依据/文档：文档/需求/20260920_从物料自动构建本体/验收报告.md；报告提交 7013a53；被验收 SHA 6a7033f

### 从物料自动构建本体 · 实施（codex/ontology-build） · zcode · 已实施，待验收

时间：2026-09-20T14:57:34.384110+00:00；记录：`.collaboration/entries/000126-721a27d46945.json`

按 文档/需求/20260920_从物料自动构建本体 完成 P0–P7 全链路实施并提交三个 commit：d8250be（后端任务族与生成管线）、90fa3c1（前端六页与导航接线）、6a7033f（开发计划登记）。真实链路：分片上传→本地解析（Java/JPA、DDL、MD 等带定位事实）→多轮范围对话→证据驱动生成→候选评审（编辑/决定/合并撤销/依赖阻断）→原子创建新本体草稿。未合并 main、未重启主工作台、未删除开发环境。

- 决定：接口文档 08 分册为唯一契约先行冻结，再实现后端与前端（文档先行同 commit）。；任务族数据独立于本体资产（wb_build_* 12 表，迁移 20260920_0003）；交付时才在同一写事务创建新草稿，绝不先建空本体。；交付幂等指纹只取选定集合内容（定义 ID 每次由服务端重新分配，不能进指纹）；requestId 命中回执优先于 CAS。；生成类运行成功时在同一事务把任务推进到 review；范围确认先写回范围再冻结基线（否则批次基线恒落后一版、stale 恒真）。；证据引用必须落在当前任务允许集合内，模型给出的不存在的 factId 一律剔除并把该字段降为推断/不足。
- 验证：tests/test_ontology_build.py 51/51 通过（真实 HTTP + 本地假 LLM + 合成物料；覆盖上传分片幂等与冲突、解析定位回读、捏造证据剔除、依赖阻断与恢复、幂等重放同一 ontologyId、二次交付拒绝、跨账号 404、未登录 401、路径穿越与 ZIP 逃逸拦截），已登记 tests/run.py http 组。；python3 tests/run.py all → 40/40；前端 vue-tsc 0 错误、npm run build 通过。；隔离实例 18871 真浏览器走通 A01→A06：创建任务、上传三份材料、扫描（识别 java/jpa、markdown、ddl 片段数与定位）、多轮对话（助手真实回复且补丁只作建议不覆盖人工）、保存摘要、确认生成、五阶段完成、候选入库（冲突项默认暂缓）、交付创建本体草稿并可进入对象建模。；实施中修复 13 处真实缺陷（见开发计划 §11），其中 8 处为浏览器或接口实测发现：任务状态不推进、本体 ID 非规范 UUID、幂等指纹不稳、storage.NotFound 未映射 404、批次 stale 恒真、范围矛盾检测过弱、/api/build-diff 500、页面泄漏原型编号 A03–A06。
- 下一步：交 Codex 独立验收：建议按需求 G01–G16 与开发计划 T01–T25 逐条核对，重点复验捏造证据剔除、依赖阻断、交付幂等与原子性、跨账号隔离。；未验证项（已在开发计划 §11 明示）：真实 LLM 语义质量与多领域样本（T25/G16）、OCR、规模限额校准、人工修改冲突的浏览器点击级链路、768 窄屏与键盘可达性（T24 部分）。；浏览器验收中 IAB 会话在同 host 多端口间被覆盖导致两次掉登录（环境限制，非功能缺陷）。；验收通过后停在待用户授权集成；未合并 main、未重启 18765、dist 未部署。
- 依据/文档：提交：d8250be、90fa3c1、6a7033f（分支 codex/ontology-build）；文档/接口文档/08-从物料自动构建本体接口.md；文档/需求/20260920_从物料自动构建本体/开发计划.md（§0 环境表、§11 实施记录与未验证项）；workbench/ontology_build/、workbench/ontology_build_routes.py、workbench/storage/ontology_build.py、workbench/migrations/versions/20260920_0003_ontology_build.py；frontend/src/ontology/build/、tests/test_ontology_build.py

### 20260920_从物料自动构建本体 前端接线（App/navigation/OntologyHome） · zcode · 已实施，待验收

时间：2026-09-20T12:13:28.237356+00:00；记录：`.collaboration/entries/000125-0910365888e6.json`

把 ontology/build/ 已完成六页（A01-A06）接进工作台导航：本体区新增视图 build「从物料生成」（子入口，不新增平台主菜单）；App.vue 新增 buildStage/buildTaskId/buildRunId 并按子页面真实 emit 驱动流程；OntologyHome.vue 增加入口按钮。仅改 3 个文件，未动 build/ 下 6 页与 api/types。

- 决定：navigation.ts：pages 增加 build='从物料生成'；alias 兼容 /build、/ontology/build、ontology/build、ontology-build；menuOntology 末位加 build（本体区菜单，非平台主菜单）。；App.vue：build 独立于本体草稿加载态渲染，未选本体也能进（G01）；emit 按代码真实契约接线：open-task→materials、continue→scope、generated(runId)→progress、review→review、back 各级回退，A06 saved→记账来源并 switchOntology 进对象建模。；A05 只声明 saved 未触发且无前进入口、A06 无回评审按钮；按原型在 App 侧补步骤条（物料/确定范围/生成/评审初稿/保存新本体）提供评审初稿↔保存新本体入口，不改子页面。；来源回链（G13）：本体 id→任务 id 存账号级浏览器偏好 wiz-build-source，对象建模页显示「← 查看生成来源任务」。；无本体页「创建第一个本体」卡片加「从物料生成」次按钮，未改既有文案结构。
- 验证：npx vue-tsc --noEmit -p tsconfig.json：0 错误。npm run build：成功，产出 dist/assets/index-C5OE6mZW.js 与 index-mUXEVsI7.css（仅既有 chunk>500kB 提示）。；node --import ./tests/ts_hooks.mjs：global_settings_nav 7/7、global_interaction 7/7、dependency_guard 22/22、ontology_lazy_load 11/11、project_check_staleness 9/9、reference_changes、config_transfer 均通过。；脚本校验 navigation：四种旧写法全部归一 build；initialSpace('#build')='ontology'；build 不在 projectSpaceViews/globalViews。
- 下一步：待 Codex 独立验收；本轮无浏览器端到端实测（A05/A06 真接口流转依赖 workbench/ontology_build* 后端与迁移 20260920_0003，非本轮改动）。；按本轮指令未 git add/commit。
- 依据/文档：frontend/src/App.vue；frontend/src/app/navigation.ts；frontend/src/ontology/OntologyHome.vue；文档/需求/20260920_从物料自动构建本体/需求说明.md

### 集成成功后自动清理开发环境约定 · codex · 已确认决定

时间：2026-09-20T08:35:50.005451+00:00；记录：`.collaboration/entries/000124-d510e725ab27.json`

用户要求集成完即删除对应开发环境，已固化到AGENTS、稳定基线和当前自动构建计划/指令。仅规则修改，未删除任何目录或分支。

- 决定：集成并合并授权包含集成验证和main合并成功后的自动清理，无需另发清理指令或等待主服务重启。；清理本任务开发/临时集成worktree、已合并分支、专属依赖/运行产物及登记可丢弃的隔离测试数据；先保存提交/验收证据，停止匹配的服务并释放端口。；未提交/未合并、他人仍写入、未知或需保留数据阻断相关清理并报告，不强制删除；真实主库、原始物料和其他任务不在范围。
- 验证：三份文档自动清理及保护边界、相对链接检查通过；git diff --check通过。当前只有main工作树，没有执行实际清理。
- 依据/文档：AGENTS.md；.collaboration/baseline.md；文档/需求/20260920_从物料自动构建本体/开发计划.md；文档/需求/20260920_从物料自动构建本体/执行指令.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 创建工作树与集成合并明确提示词约定 · codex · 已确认决定

时间：2026-09-20T08:31:39.494829+00:00；记录：`.collaboration/entries/000123-b63d4d42d13a.json`

按用户最新要求修改AGENTS和共享基线：创建worktree与集成合并均由用户明确触发；同步自动构建需求的计划和指令，定义标准提示词与阶段停止点。未创建工作树或执行合并。

- 决定：默认zcode收到创建指令后创建worktree；仅创建指令不开始开发，后续开发/修复复用同一环境。；Codex独立验收指定提交，通过后停在待用户授权集成；验收通过不隐含合并。；用户明确集成并合并后Codex负责临时集成worktree、普通冲突修复、组合验证和main更新；不重复询问同一授权，不默认重启主服务。；文档模板或其他工具交接不构成执行授权；用户可明确合并多个授权阶段，需求冲突另行确认。
- 验证：AGENTS/计划/指令的提示词、授权边界和相对链接检查通过；git diff --check通过。无业务代码改动。
- 依据/文档：AGENTS.md；.collaboration/baseline.md；文档/需求/20260920_从物料自动构建本体/开发计划.md；文档/需求/20260920_从物料自动构建本体/执行指令.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 独立分支工作树与串行集成规范固化 · codex · 已确认决定

时间：2026-09-20T08:24:26.103154+00:00；记录：`.collaboration/entries/000122-dcd5e6a120cd.json`

按用户授权将独立分支/worktree/端口/数据与串行集成规范写入AGENTS，并同步共享稳定基线及本体自动构建计划、执行指令。仅文档变更，未创建分支或搬移当前开发。

- 决定：后续业务开发每需求从已提交main创建独立分支和worktree，隔离端口、数据及依赖；当前main未提交任务不自动stash/reset/搬移。；单一集成负责人在临时集成环境合入最新main并验证；main干净且基线未变后快进更新，冲突处理不能直接ours/theirs。；所有后续开发计划与执行指令必须自包含开工、环境登记、验证、合并、启动、回退及清理流程。；纯讨论、需求/原型与治理文档可仅提交自身文档；本规则不是已安装的自动分支/合并服务。
- 验证：AGENTS及本次计划/指令的隔离、合并、回退关键条款和相对链接检查通过。；git diff --check通过；无业务代码改动，未构建、测试或重启服务。
- 下一步：后续生成指令采用新规则；正在main开发的任务由原执行者完成阶段或协调迁移，不自动操作。
- 依据/文档：AGENTS.md；.collaboration/baseline.md；文档/需求/20260920_从物料自动构建本体/开发计划.md；文档/需求/20260920_从物料自动构建本体/执行指令.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### 统一维护改版v2独立验收 · codex · 已验证

时间：2026-09-20T08:06:47.302898+00:00；记录：`.collaboration/entries/000121-d44011600c1f.json`

基线06eee30独立验收未通过：确认3项P1；已追加开发计划11.5，未修改业务代码。

- 决定：延期UI不列验收项；不能以实施记录全部通过代替独立证据。
- 验证：构建通过，前端5套定向回归和后端发布/编排/目录/升级专项通过；目录套件仍有2项规格观察。；R01真实App客户端mount抛flowState初始化前访问异常，原SSR测试漏检。；R02空壳编排check_flow报输出未绑定，项目属性校验errors仍为空。；R03目录payload读取后、首次依赖token读取前更新目录，发布返回200并生成v3，预期409零写入。；隔离根与假账号验证，无真实数据修改；未做浏览器视觉验收。
- 下一步：修复R01-R03后定向复验，校正文档F06/F07通过结论及测试规格观察。
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md

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

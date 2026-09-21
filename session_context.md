# Codex / zcode 共享上下文

上下文版本：`926818e71d69628d`

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

### 从物料自动构建本体 · 第5轮独立复验 · codex/ontology-build · codex · 已验证

时间：2026-09-21T04:27:55.682887+00:00；记录：`.collaboration/entries/000135-d4fb4afadb40.json`

复验0188d9e：R4-01及三项P2整改通过；保留非阻断P2取消收尾竞态，完整修复方案已写入第5轮报告。未修改业务代码、合并main或重启服务。

- 验证：独立复跑上轮晚响应反例通过；late_write 23/23。；后端all 45/45测试文件通过；review_edit 19/19；前端build通过。；独立隔离线程探针复现最后内容写入后取消被finish_success覆盖；无旧内容污染。；本轮未做浏览器端到端、真实LLM、外部MySQL或main集成验证。
- 下一步：R5-01建议后续修复并补回归；当前待用户明确集成授权。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/验收报告_第5轮_20260921.md

### 从物料自动构建本体 · 第4轮验收整改（R4-01 + 三项P2） · zcode · 已实施，待验收

时间：2026-09-21T04:17:39.294786+00:00；记录：`.collaboration/entries/000134-0468581bb2a9.json`

按第4轮验收报告修复 R4-01（P1）并落实三项 P2，提交 1f66b7c（R4-01）、24879ab（P2）、0188d9e（文档）。R4-01：runner 新增 content_tx() 在同一写事务内核对取消与执行权后再写内容，pipeline 6 处内容写点全部换用；新回归覆盖报告 §5 全部验收场景。未合并 main、未重启 18765、未清理环境。

- 决定：内容写保护靠同一 BEGIN IMMEDIATE 事务内的 check_cancelled（含 lease 比对）：SQLite 写事务串行化保证核对通过后 lease 轮换无法并发插入，晚到内容不可能在核对后落库。；pipeline 12 处 _tx 分类处理：6 处内容写（scan标记运行/事实、generate批次清空/abstract追加/最终写入、dialog消息）走 content_tx；run 行写保持既有 lease 条件；入口快照读不变。；P2-1 选「禁止清空」口径：已有 dataType 改回未确定时页面报错不发请求；P2-2 后端在 dataType 改非 timeSeries 时清理残留 valueType；P2-3 已交付任务跳过预检请求。
- 验证：tests/run.py all 45/45（新增 test_ontology_build_late_write.py 并入 unit 组，23/23）。；tests/test_ontology_build.py 163/163（两次）；runner_isolation 34/34、exclusion_inheritance 17/17、merge_refs 42/42；review_edit mjs 19/19；vue-tsc 0 错误、build 通过。；报告 §8 归档探针复跑：generate/dialog 两场景 B 成功后释放 A，库里均只剩 B 新结果各 1 条，旧结果零落库，B 的进度与终态未被 A 改动。；两个并行子agent分文件实施（runner/pipeline+晚写入回归；review/前端三文件+P2断言），协调者跑组合回归、探针自证并串行提交。
- 下一步：交 Codex 第 5 轮独立复验：重点 R4-01（跑 test_ontology_build_late_write.py 与报告 §8 探针，确认旧内容零落库）与三项 P2；其余项通过证据可复用。；未验证项维持：真实模型语义质量（G16/T25）、OCR、规模校准、窄屏与键盘可达性（G15/T24）、T12/T20 故障注入、与最新 main 组合重验。；验收通过后停在待用户授权集成；未合并 main、未重启 18765、dist 未部署、开发环境保留。
- 依据/文档：提交：1f66b7c（R4-01）、24879ab（P2 三项）、0188d9e（文档）；分支 codex/ontology-build；文档/需求/20260920_从物料自动构建本体/验收报告_第4轮_20260921.md §5/§6（被整改项）；tests/test_ontology_build_late_write.py（五场景 23/23，§8 探针的正式回归化）；文档/需求/20260920_从物料自动构建本体/开发计划.md §11（第 4 轮整改记录）

### ontology-build 第4轮P2建议修复（P2-1/P2-2/P2-3） · zcode · 已实施，待验收

时间：2026-09-21T04:13:41.688482+00:00；记录：`.collaboration/entries/000133-6e9ab94d2dcb.json`

第4轮验收报告§6三条非阻断P2已实施，未提交未合并：P2-1 BuildReviewPage.saveEdit 明确禁止清空口径——候选已有 dataType（readPropertyFields 判定，含嵌套兼容）而表单选空时置 editError「该属性已有数据类型，不能清空为「未确定」；请选择具体类型。」不发请求，原本无类型的候选不受影响；candidateFields.ts 顶部注释声明该口径由页面 saveEdit 执行、helper 保持纯函数。P2-2 review.update_candidate 字段合并：dataType 分支写入新平铺类型后若非 timeSeries 即 pop valueType，循环后再按最终 dataType 收敛一次（防同请求先 dataType 后 valueType 键序回写），切到 timeSeries 的合法性校验不动。P2-3 BuildSavePage 加载流程先 loadDelivery，delivery 非空跳过 runPrecheck（预检区保持空、不显示预检错误），未纳入项照常加载，未交付行为不变。只改 6 个授权文件；runner.py/pipeline.py/session_context.md 的改动属并行 R4-01 agent，本轮未触碰，未启停任何服务。

- 决定：P2-1 采用「明确禁止清空」口径：已有类型的属性清回未确定在页面 saveEdit 拦截（空 patch 会被服务端合并语义复原，界面不得假清空）；从未有类型的候选仍可保持未确定；P2-2 以最终 dataType 收敛 valueType：非 timeSeries 一律不保留 fields.valueType（含同请求双键键序与历史残留）；timeSeries 的 valueType 缺失/非法仍由 validate_candidate 把关，未新增阻断码；P2-3 仅改 bootstrap 加载流程；submit/refreshDelivery 内的 runPrecheck 调用维持现状（不在授权范围）
- 验证：node --import ./tests/ts_hooks.mjs tests/ontology_build_review_edit.test.mjs → 19/19（新增⑯已有类型清空被拦截不发请求、⑰原无类型保持未确定不受影响、⑱已交付跳过预检请求且 precheck/precheckError 为空、⑲未交付照常预检）；.venv/bin/python tests/test_ontology_build.py 完整跑两次 → 均 163/163（162+1：新增「timeSeries 切回 number 后 fields 不残留 valueType 键（P2-2）」断言通过，无 flake 失败）；node --import ./tests/ts_hooks.mjs tests/ontology_build_frontend.test.mjs → 通过；cd frontend && npx vue-tsc --noEmit -p tsconfig.json → 0 错误；npm run build → 成功（仅既有 chunk>500kB 提示）
- 下一步：交 Codex 复验三条 P2：建议复跑 test_ontology_build.py、ontology_build_review_edit.test.mjs、vue-tsc/build，并核对 review.py update_candidate 收敛口径与 BuildSavePage 加载分支；本轮未 git add/commit（按指令）；与并行 R4-01 改动的组合回归（tests/run.py all 等）由协调者统一安排
- 依据/文档：workbench/ontology_build/review.py（update_candidate）；frontend/src/ontology/build/BuildReviewPage.vue、BuildSavePage.vue、candidateFields.ts；tests/test_ontology_build.py（flow_timeseries_delivery 末尾 P2-2 断言）；tests/ontology_build_review_edit.test.mjs（⑯⑰⑱⑲）；文档/需求/20260920_从物料自动构建本体/验收报告_第4轮_20260921.md §6

### ontology-build 第4轮R4-01修复（内容写点事务内核对取消/执行权） · zcode · 已实施，待验收

时间：2026-09-21T04:10:20.851263+00:00；记录：`.collaboration/entries/000132-3ce162adb157.json`

R4-01（P1）修复完成，未提交未合并：runner.py 新增 content_tx——同一 BEGIN IMMEDIATE 写事务内先 check_cancelled（含 lease 核对）再写内容；pipeline.py 清点 12 处 _tx，6 处业务内容写点换 content_tx（scan 标 running/写事实、generate 批次清空/abstract 追加候选/最终整批写入、dialog 追加消息），3 处 run 行写与 3 处入口快照读不动；新建 tests/test_ontology_build_late_write.py 五场景双 worker 时序回归。详见 文档/需求/20260920_从物料自动构建本体/开发计划.md 待追加记录。

- 决定：内容写点口径：候选/消息/材料事实（含 parse_state）与批次整批替换都算业务内容；run 行阶段/进度/usage/checkpoint 写维持 lease 条件 update_run 不动；abstract 批次清空（delete_candidates_of_batch）一并纳入 content_tx：同为候选内容变更，语义与原内联 check 完全等价；_final_write 的内联 check_cancelled 移除，由 content_tx 统一核对，人工排除继承+整批替换保持单一内容事务
- 验证：.venv/bin/python tests/test_ontology_build_late_write.py → 23/23（两次）；.venv/bin/python tests/test_ontology_build_runner_isolation.py → 34/34；.venv/bin/python tests/test_ontology_build_exclusion_inheritance.py → 17/17、merge_refs → 42/42；归档探针（报告§8抄至/tmp）exit=1：len(rows)==2 断言失败，仅剩 B 新结果 1 条，旧候选不再落库，缺陷已修复
- 下一步：交 Codex 复验 R4-01：复跑新回归与归档探针（应失败）；协调者统一跑 tests/run.py all + test_ontology_build.py 组合回归（本轮按指令未跑）；通过后停在待用户授权集成；本轮未 git add/commit
- 依据/文档：workbench/ontology_build/runner.py；workbench/ontology_build/pipeline.py；tests/test_ontology_build_late_write.py；文档/需求/20260920_从物料自动构建本体/验收报告_第4轮_20260921.md §5/§8

### codex/ontology-build 第4轮R3修复独立复验 · codex · 已验证

时间：2026-09-21T03:45:01.671391+00:00；记录：`.collaboration/entries/000131-2d6c8d6c4986.json`

基线91ff4c3（仅比f1b90c6多验收指令）：不通过。专项21/21、runner34/34、排除17/17、合并42/42、前端15项及build通过。全量首次43/44和独立核心47/48均连接重置，重跑分别44/44、162/162通过。新独立真实pipeline探针复现P1 R4-01：新尝试成功后旧LLM响应仍插入候选和assistant消息，状态令牌保护未覆盖内容写点。

- 决定：R3-02/03/04和B.4对应自动化/静态范围通过；R3-01状态修复成立但内容晚写入仍阻断，需修复后复验。；只验收；未改业务/测试/接口代码，未合并main、未启停18871或18765。
- 验证：真实临时SQLite+runner+pipeline，仅控制LLM返回顺序；B已成功后A旧响应仍产生第2条include候选和第2条assistant消息。；前端typecheck/build及接口08 §7字段口径核对通过；18文件diff与指令一致。；开始及结束18765 HTTP200；未做浏览器点击、真实模型质量或main组合验收。
- 下一步：修复pipeline.py候选和消息写事务的取消/lease检查，新增真实pipeline时序回归后再验收。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/验收报告_第4轮_20260921.md

### 从物料自动构建本体 · 第3轮验收整改（R3-01～R3-04 + B.4） · zcode · 已实施，待验收

时间：2026-09-21T03:30:44.291791+00:00；记录：`.collaboration/entries/000130-0837e50fa06a.json`

按验收报告附录 B 的 4 项 P1 与 B.4 一项 P2 整改完成，提交三个 commit：25333c3（后端四项）、7835e05（前端 + 回链）、f1b90c6（已交付页误报修复）。四个工作包由并行子 agent 分文件实施，协调者做契约冻结、组合回归与浏览器复验。未合并 main、未重启 18765、未清理环境。

- 决定：R3-01：执行权按 worker token 绑定并线程本地化；submit() 先显式轮换 lease，旧 worker 永远拿不到新执行权，finally 只清自己的登记。；R3-02：排除保护改为跨全部批次回放（新覆盖旧），仅 include+reviewed（用户显式重新纳入）解除；不再只看上一批。；R3-03：交付侧构造合并别名表（传递解析+环保护）传入 assemble()；非空宿主解析不到一律阻断，绝不静默丢关联。；R3-04：dataType 平铺字符串+平铺 valueType（枚举与 model_format.SERIES_VALUE_TYPES 同源）；旧嵌套 payload 被拒并给纠正提示；非法观测值保存时即拒。口径登记接口文档 08 §7。；B.4：任务回链携带 deliveryOntologyId，App 先 switchOntology（含未保存守卫）再进入对象建模。
- 验证：tests/run.py all 44/44；tests/test_ontology_build.py 162/162（+2：非法观测值未落库、库内残留旧值仍被预检拦截）。；新增回归：runner_isolation 34/34、exclusion_inheritance 17/17、merge_refs 42/42（含真实交付端到端）、ontology_build_review_edit.test.mjs 15/15；vue-tsc 0 错误、npm run build 通过。；报告 B.5 三份复现脚本已转为断言正确行为的回归：验收证据_第三轮/复验_修复后回归.py 21/21，可单命令对照 R3-01～R3-04 复验。；浏览器点击级复验（18871+假模型）：评审页把属性改为时间序列保存→服务端真实落库 timeSeries/double（修复前必失败）；交付后回链跳到该本体 #objects 且 2 对象/1 属性/1 链接可见。；隔离实例按旧 PID 精确重启（未用 pkill），主工作台 18765 全程 200 未受影响。
- 下一步：交 Codex 第 4 轮独立复验：建议按 R3-01～R3-04 逐项复跑（复验脚本+各工作包新增回归），并复验 B.4 回链。；未验证项维持不变：真实模型语义质量与多领域样本（G16/T25）、OCR、规模限额校准、窄屏与键盘可达性（G15/T24 部分）、T12/T20 故障注入、与最新 main 的组合重验。；验收通过后停在待用户授权集成；未合并 main、未重启 18765、dist 未部署、开发环境保留。
- 依据/文档：提交：25333c3、7835e05、f1b90c6（分支 codex/ontology-build）；文档/需求/20260920_从物料自动构建本体/验收报告.md 附录 B（被整改项）；文档/需求/20260920_从物料自动构建本体/验收证据_第三轮/复验_修复后回归.py（21/21）；文档/需求/20260920_从物料自动构建本体/开发计划.md §11（第3轮整改记录）；文档/接口文档/08-从物料自动构建本体接口.md §7（R3-04 字段口径）

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

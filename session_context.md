# Codex / zcode 共享上下文

上下文版本：`8aa825eaf2ca69bc`

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

### ui_fix 第八轮：编排列表列宽塌陷与时间显示收口 · codex · 已实施，待验收

时间：2026-09-22T07:53:40.110894+00:00；记录：`.collaboration/entries/000187-1175aa7ea373.json`

按用户截图定位「函数编排」列表页三处根因并修复：①FlowList.vue 是全仓唯一 table-layout:fixed 而无 min-width 的表，fixed 先把 84/150/110/148 四个 px 列分满，未声明宽度的名称列在 517px 容器里被压到 25px（行高 157px），且表总宽小于容器所以不出横滚；按 .ont-table/.attribute-table 既有约定补 min-width:756px，并把配置状态列 110→156（最长胶囊实测 130px）、操作列 148→168（三个 .mini 实测 146px，原宽度让删除按钮溢出单元格 8px）。②同一 updatedAt 在编排页显示 2026/9/22 00:09:41、在生成任务页显示 2026-09-22 14:12：删除 flowModel.ts 与 build/types.ts 两份各自 formatTime，新增 shared/format.ts 作唯一出口，替换 5 个文件共 6 处调用点。构建、lint 与被验包 index-SZgrLazQ.js 逐列复测通过，另扫 19 页面 + 编辑器两视图溢出命中 0。已提交 4a1ae75 与 5122ceb，未合并 main、未重启主工作台。

- 决定：纯时刻保留 toLocaleTimeString('zh-CN',{hour12:false})：saveCoordinator.ts:65 与 FlowTestWorkspace.vue:261 各 locale 均稳定输出 HH:MM:SS，前者行为由 tests/saveQueue.test.mjs 锁定，不为此重开回归面；该窄口径例外已写进 DESIGN.md。；.ftw-field-name 显示裸 UUID 经 /api/flow-state 查实不是渲染缺陷：该测试编排节点入参 name/label 在库里就是空串，UI 按 ID 兜底是既有约定，属测试物料数据形态，不改前端。；flow-canvas 实测 role/aria-label/tabindex 全 null（InstanceGraph 的 .graph-canvas 三者皆有）：差异属画布键盘可达性专项，补 tabindex 只会造一个不动的焦点站，本轮不当样式漂移顺手加。；截图通道返回 NATIVE_BROWSER_VIEWPORT_UNAVAILABLE（页面后台），本轮视觉证据一律用 DOM 实测数值与结构快照，未附截图。
- 验证：被验包 index-SZgrLazQ.js（视口 831×848，.scroll 容器实宽 517）：名称列 25→198px、行高 157→50px、时间单元格 w:150 单行显示 2026-09-22 00:09。；状态列内容区 136px ≥ 胶囊 130px；操作列末按钮右边界落在 padding 盒内 2px、距单元格右边界 −12px；容器 scrollLeft 可达 239（=756−517），滚到底按钮完整可见。；溢出判据（有直接文本或 td/th/button 且 scrollWidth>容器宽+1，排除 ellipsis/auto 滚动件）扫 19 页面 + 编排编辑器画布与测试调试视图 + 编排列表页：命中 0。设置中心两页未纳入。；无可访问名控件判据只在 #f-editor 画布视图跑了一次命中 0；符号命名按钮判据跑 6 视图命中 0（覆盖面限制已写进 §9.13，未写成全量结论）。；npm run build（含 vue-tsc 严格）两次通过、npm run lint 干净；分支库改动仅 frontend/src 8 文件 + DESIGN.md + 开发计划，工作树已干净。
- 下一步：等用户在 http://127.0.0.1:18882 自己点一遍编排列表与生成任务页确认时间显示、列宽观感。；§9.10 末清单原样待决：日期/时间芯片口径、backdrop mousedown 关闭语义、ObjectWorkspace:539 缺确认、FlowTestWorkspace role=tab 与 WCAG 2.5.3、B7' 重名对象、约 2744 处 px 魔法数字、画布键盘可达性。；如需坐实 D1 剩余项（FlowTestWorkspace sr-only 空文本勾选、FlowCanvas aria 命名），需一条带 text 型入参且未绑定的编排数据；写真实数据副本需用户点头。；合并 main 与重启主工作台仍需用户明确指令，本轮未做。
- 依据/文档：worktree/ui_fix/frontend/src/shared/format.ts（新增唯一出口）；worktree/ui_fix/frontend/src/flow/FlowList.vue:113-131（列宽与 min-width）；worktree/ui_fix/DESIGN.md Typography 末条 + Layout fixed 表条；worktree/ui_fix/文档/需求/20260921_样式与交互统一/开发计划.md §9.13；提交 4a1ae75（fix）与 5122ceb（docs），分支 codex/ui_fix，未合并

### ui_fix 第七轮复测补充：合并后后端 quick 回归边界 · codex · 需求已交付

时间：2026-09-22T06:34:51.131101+00:00；记录：`.collaboration/entries/000186-f96f95a10997.json`

补 000185 一项证据：在合并后的 codex/ui_fix 上跑 tests/run.py quick（env 剥除 WIZ_DATABASE_URL/ROOT/PORT，测试自持临时根）→ 通过 2/3。唯一失败 test_save_iteration.py 是环境缺输入而非代码回归：它从 REPO/ontology/ 播种 storage 版本（tests/test_save_iteration.py:130-147），而 ontology/ 已退出 git 跟踪、任何新建 worktree 都不带该树（main 有、分支无），合并前即在 worktree 跑不起来。未为此复制 main 真实 ontology/ 进分支。其余 quick 通过；http/unit 全量与需本机 MySQL 的 external 未跑。结论仍是本轮新缺陷 0。

- 决定：不改测试也不搬数据来让该项在 worktree 变绿：把 ontology/ 复制进分支等于搬运真实用户数据，超出样式统一任务边界；把该失败记为验证边界并写入 §9.12，避免后续把它误读成合并回归
- 验证：命令：env -u WIZ_DATABASE_URL -u WIZ_WORKBENCH_ROOT -u WIZ_WORKBENCH_PORT python3 tests/run.py quick（在 worktree/ui_fix 内），结果 通过 2/3、失败 test_save_iteration.py，临时根由脚本自建于 /var/folders/.../wiz_save_iteration_*；ls 对照：worktree/ui_fix 无 ontology/ 目录，main 仓库根有 catalogs/drafts/projects/releases/vault/workspaces
- 下一步：如需分支上跑该项，请明确授权把 main 的 ontology/ 只读种子放入分支（或改测试用合成夹具），两者都不属本轮范围
- 依据/文档：文档/需求/20260921_样式与交互统一/开发计划.md §9.12『合并后的后端边界（补测）』；上一轮交接：000185

### ui_fix 第七轮体验复测（合并 main 后，零新缺陷） · codex · 需求已交付

时间：2026-09-22T06:31:35.465215+00:00；记录：`.collaboration/entries/000185-39ff1adfc077.json`

在 worktree/ui_fix（分支 codex/ui_fix，端口 18882，PID 32229）对合并后 HEAD 16c1c99 + 升级到 20260921_0004 的分支库跑第七轮点击驱动复测：新缺陷 0，未产生代码修复提交，仅补记开发计划 §9.12（ba7046e）。实算坐实 D2 .danger-btn 颜色（--danger/#b03a3a + --danger-line/#f2cfcf），由代码层核实升级为浏览器验收。

- 决定：升级分支库前先 transfer backup 出在线快照并另存根密钥副本到 .runtime/（禁止 cp 正在写的库），只停本 worktree server.pid 记录的进程；迁移前产物保留不删，作为回退物；控制台唯一 /api/build-run 404 按接口文档 05 #65『无运行记录 404』归类为探测噪声，不改前端也不静默吞；复测用的临时生成任务（含 2 份上传物料）测完即删：直查分支库确认 12 张 wb_build_* 表全为 0，避免留下测试残留；D1 三处可访问名称与共享属性库『日期→时间』芯片需已有本体/项目/编排数据才渲染；uiverify 是空快照账号，造数据会写入含真实数据的分支库副本，按既有边界不自行推进，留给用户指定验收账号或明确授权造演示数据
- 验证：git merge-base --is-ancestor main HEAD = yes（main⊆分支）；alembic_version 实查 20260921_0004；npm run build（vue-tsc+vite）与 npx eslint src 通过，页面 script[src] 为合并后构建 index-DyWe6o7h.js（仅改 hash 不重载文档曾造成测量假象，已用 reload(ignoreCache) 纠正）；main 侧新增前端 928 行：十六进制色/rgba/内联 h1/!important 命中 0；『解析并发 8 线程』与 9 行解析器支持矩阵、后缀过滤（选中 2·上传 2·跳过 0）、扫描 2 成功 2 均实测通过；a11y 契约逐卡实测（main 新增『文件夹详情』+ 分支『删除生成任务』）：tabindex=-1、自动聚焦入卡、Tab 圈禁且 preventDefault、禁用『删除任务』不入焦点序（输入确认名后入列）、Escape 只关一层、关闭后焦点归还触发器；13 个可达视图单 h1、无错误横幅；CSSOM 全表扫描『删 outline 未补焦点环』命中 0，全局 :focus-visible 环在位；未点击任何生成/运行/抽取动作（该账号模型服务=未配置），未新建本体或项目
- 下一步：等用户对第七轮结论与分支 HEAD 表态；合并 codex/ui_fix→main 及重启主工作台仍需用户另行明确授权；如用户希望浏览器坐实 D1 与共享属性库芯片，请指定带本体/项目/编排数据的验收账号，或授权在本分支库副本内造可删演示数据；§9.10 末『留给用户或后续专项』清单（dataTypeLabel 日期/时间、legacyGraph 写死日期项、背板 mousedown 关闭口径、PickerRow.actions.danger 改名、removeRuleRef 缺确认、FlowTestWorkspace role=tab 关联、App.vue 自有两卡、B7 重名、需求归档缺 3 份文件、审视报告处置、~2744 处 px 魔数基线）仍待用户决定
- 依据/文档：文档/需求/20260921_样式与交互统一/开发计划.md §9.12；分支提交 ba7046e（docs）/ 16c1c99（被验 HEAD）/ ae4ce18（merge main）/ 476cd28（合并后 a11y 补丁）；.runtime/workbench-pre0004-20260922-140638.sqlite3 与 .runtime/wb-root-pre0004.key.bak（迁移前回退物，保留）

### ui_fix 分支同步 main（主干→分支日常合并） · codex · 已实施，待验收

时间：2026-09-22T05:59:17.335607+00:00；记录：`.collaboration/entries/000184-f005dbfd67a4.json`

在 worktree/ui_fix 内将已提交 main 合并进 codex/ui_fix：merge 提交 ae4ce18 无冲突（三个 build 页面 ort 自动合并）；随后按分支 modal a11y 规范为 main 新增的 BuildMaterialsPage 两处弹窗补 tabindex=-1 与 @keydown.esc.stop（476cd28），render 重建 session_context（e75fa65）。验证：npm run build 通过、npm run lint 无告警、后端模块 import 通过；未启动服务、未跑 external 测试组。

- 决定：冲突自动合并后逐处核对双方改动：main 功能结构全保留，分支 h2+font-weight:700 与弹窗 a11y 均在位；session_context.md 不手改，用 context.py render 重建
- 验证：cd frontend && npm run build（vue-tsc+vite）通过；cd frontend && npm run lint 通过（无输出）；python3 -c import workbench.server,workbench.ontology_build.materials,workbench.ontology_build.llm 输出 IMPORT OK；git status 干净，分支 codex/ui_fix HEAD=e75fa65
- 下一步：待用户决定后续浏览器验收或集成安排

### retrieve筛选优化-剖析与基准基建（codex/build-progress-log） · zcode · 已实施，待验收

时间：2026-09-22T04:35:04.133748+00:00；记录：`.collaboration/entries/000183-29b8b3b3c3ef.json`

R1/R3/R5/R7 剖析与基建交付：①新增 tests/retrieve_fixture.py（生产对齐合成夹具，默认 276k 条≈26 条/物料、四类 kind、五档长度、PRIMARY_SCOPE 弱词 85+hints 14 对齐 P2 扫描口径；纯标准库确定性，ruff 通过，不 import workbench）；②剖析报告 retrieve优化剖析报告_v1.md：基线=3dc51dc 快照（git show 导出仓库外加载，未改任何仓库文件），实测+外推——基线 build_index 占总耗时 96.7-98.8% 且呈幂指数≈2.0-2.4 膨胀（token 桶 fact_id not in bucket 线性查重随桶长线性增长），276k 外推 40-60 分钟与生产实测 40 分钟同量级吻合；优化后(retrieval.py@964493a) 276k 实测 45.29s（R3≤5min 达成）、峰值 RSS 919.6MB、texts 282.6MB 驻留未变（O1 未实施）。select 基线与优化版持平（合并正则≈逐词 in）；意外热点=依赖扩展全桶扫描二次项（select 13%→42% 随规模升）。③基准脚本 tests/benchmark_retrieve.py 已由实施线先期提交（d6ed223），本轮按写入归属未覆盖；发现其 --mode profile 空表缺陷（pstats 行带缩进致 startswith('ncalls') 恒假）与 --concurrency 占位缺口，留给脚本 owner。

- 决定：不覆盖在途线已提交的 tests/benchmark_retrieve.py 与等价金样（owner 归属）；剖析基线按需求锁定 3dc51dc 快照而非测量时 HEAD（HEAD 已前移但 retrieval.py 未再变，git diff 核实）；夹具 scope include 采用生产口吻长尾句（弱 gram 必须落在 include——select 第三循环只扫 include 弱词+hints，goal/relations 弱词被基线丢弃）；初轮矩阵部分 wall 点受并发残留干扰已识别，引用数字均经空机复测；RSS/profile 占比对争用不敏感沿用原矩阵
- 验证：ruff check tests/retrieve_fixture.py 通过（含 E4/E7/E9/F/B）；夹具自检：同 seed 确定性、分批==整建、分布符合设计（3000 条）；三方等价哨兵：inst==base 逐字节一致（计时副本可信）、head==base selection 逐字节一致（3 组 scope×2000 条）、head tokens 桶为空；关键 wall 点空机复测：base 20k index 9.1s（原 22.5s 判为干扰剔除）、base 40k 47.0s 双跑、head 276k 45.29s；幂律拟合 6 点：p≈2.01（残差包络 28-60 分钟区间）
- 下一步：脚本 owner：benchmark_retrieve.py profile 空表一行修复 + --concurrency 占位（V2-10 复用）；O1 单遍融合未实施——报告 §3.4/§6.4 已留对照基线（texts 282.6MB 驻留、双遍）；依赖扩展二次项（select 42.7s 中约半）为 O0-O3 未覆盖的后续算法轮次候选；剖析脚本现居 /tmp/prof（仓库外），是否入库由集成负责人/用户决定
- 依据/文档：worktree/build-governance/tests/retrieve_fixture.py；文档/需求/20260920_从物料自动构建本体/retrieve优化剖析报告_v1.md；worktree/build-governance/tests/benchmark_retrieve.py（在途线 d6ed223，未改动）
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### ontology-build-retrieve-optimize（retrieve 算法优化，worktree/build-governance） · zcode · 已实施，待验收

时间：2026-09-22T03:32:02.816815+00:00；记录：`.collaboration/entries/000182-ba6ddcc2eb3a.json`

retrieve 优化 O0/O2/O3 实现完成（协调者并行拆分后我的范围）：O0 build_index 增 with_tokens=False（默认跳过 tokens 死工作，键恒存在空 dict）；O2 四词表合并正则（re.escape+交替）单趟命中探测，展示词按原词表顺序原 term-in-text 判定经 _hits 复算（S1 边界：不用 findall 拼 reasons）；O3 定级早停。调用点零改动（pipeline.run_generate 不变即生效）。等价金样 15/15 逐字节一致（内嵌 3dc51dc 基线快照），全量回归 250/250×2 + 全部相关套件绿。O1 单遍融合按协调指示挂起，待并行剖析 agent 的归因报告（文本驻留 vs 子串扫描占比）决策；texts 共享无重复构建已由金样断言 12 确认。范围说明：R1 剖析/R3 性能/R5 内存数字归并行专项 agent；原派发的 200k 基线跑批已按协调意图中途停止（避免同机双份跑批互扰）。

- 决定：合并正则仅作早停探测，展示词一律 _hits 原序复算（S1：findall 非重叠匹配与逐词成员判定在重叠词上不等价）；O1 挂起而非实施：等并行剖析归因数据；texts 共享无重复构建的核对部分已完成（金样断言）；全部实现收在 retrieval.py 内部 + 新增测试/基准文件，调用点与 pipeline.py/protocol.py 零改动
- 验证：tests/test_retrieve_equivalence.py 15/15：手构造 18 条×8 组 scope + 合成 8000×3 组 scope，selection（三列表含顺序+reasons 逐字+counts）json sort_keys 逐字节一致；O0 断言（默认无 tokens/显式恢复/桶结构与 searchable_text 同基线）；tests/test_ontology_build.py 250/250（连续 2 次）；parsers 95/95、struct_parsers 143/143、parser_wiring 97/97、struct_e2e 87/87、late_write 23/23、finish_guard 27/27、runner_isolation 34/34、materials_views 10/10、task_purge 17/17、exclusion_inheritance 17/17、merge_refs 42/42、storage_contract 60/60、storage_transfer 27/27；ruff 全过；V2-8 复用探针桩（test_ontology_build:1377-1398）无需变更：未新增检索入口（S3 满足）；基准脚本 tests/benchmark_retrieve.py 入库（bench/profile 两模式，RETRIEVE_BENCH_RESULT 可读输出）；R1/R3/R5 数字归并行 agent
- 下一步：待并行剖析 agent 交付 R1 归因报告后决策 O1 实施或降级（协调者转发）；R3 性能目标（≤5 分钟）与 R5 内存水位由并行 agent 数字补录开发计划；分支归属说明供协调者知晓：retrieve 实现本体经由共享检出被并行线捆包提交为 964493a（retrieval.py 与我工作区实现逐字节一致，已核对）；开发计划记录原落 build-progress-log（cb64855），已以 plumbing 快进补录 build-governance（78cf7df，不影响共享检出）；金样/基准脚本 d6ed223 两分支皆有
- 依据/文档：codex/build-governance：78cf7df（开发计划记录）/964493a（实现本体，retrieval.py 含我的实现）/d6ed223（金样+基准脚本）；codex/build-progress-log：cb64855（同一开发计划变更，与 78cf7df 内容一致）；workbench/ontology_build/retrieval.py、tests/test_retrieve_equivalence.py、tests/benchmark_retrieve.py

### 样式与交互统一(codex/ui_fix)：第六轮点击驱动复测+复测中自修三处modalFocus缺陷 · codex · 已实施，待验收

时间：2026-09-22T05:51:54.298170+00:00；记录：`.collaboration/entries/000181-0a2e075433b0.json`

上一版150轮复测子agent未出结论；改为主agent用browser-use同一标签页逐条亲测。关键教训：press_key合成的Tab/Escape不派发进DOM，键盘判定一律走evaluate_script的dispatchEvent+读defaultPrevented/activeElement。复测中在自己首落地的shared/modalFocus.ts发现并修掉三处缺陷：①焦点移入用requestAnimationFrame，隐藏/遮挡标签页document.hidden时rAF永不回调致移入/归还整套静默失效（也是上版子agent测不到差异的症结）→改setTimeout(0)宏任务；②Escape无焦点逃逸兜底，焦点掉body时卡片级@keydown.esc收不到→捕获层向最上层可见卡派发不冒泡Escape；③AppSelect展开时陷阱重建丢owner致下一格漏背层→面板并入时保留owner。复测通过：C1进入/C2归还/C4一次关一层/C5点空白后Esc仍关、AppSelect触发40px+面板10px(--r-md)+选项6px(--r-sm)+aria-controls仅展开、构建页h1count=1且页标题h2 22px/700、设置页控制台干净+pill显示设置(B1/B9)、appConfirm自管层取消/确定。

- 验证：npm run build(vue-tsc) exit0，产物index-Cbzg0FEc.js，grep确认dist含key:Escape,bubbles:!1与aria-expanded两处修复；npx eslint src/shared/modalFocus.ts exit0；browser-use实测：autoFocusedIntoModal=true、body下Escape关闭=true、脏表单两层精确关一层、Tab全程inModal且preventDefault、面板/选项radius令牌化；http 127.0.0.1:18882/#settings-models 控制台无error；所有测试弹窗均以取消/放弃草稿关闭，未写入真实快照数据，未创建本体
- 下一步：未合并main、未重启主工作台（需用户另行授权）；D1三处可访问名与D2.danger-btn计算色为代码+令牌层核实，uiverify是空快照无本体/项目故未浏览器实操；要坐实需带数据验收账号；§9.10末留给用户/后续专项清单本轮不自行推进
- 依据/文档：frontend/src/shared/modalFocus.ts；文档/需求/20260921_样式与交互统一/开发计划.md §9.11

### build-governance 生成进度实时可观测·前端（F1/F2/F3） · zcode · 已实施，待验收

时间：2026-09-22T03:27:44.998585+00:00；记录：`.collaboration/entries/000181-c16847392deb.json`

进度页新增批次状态行（✓批N/✗批N：原因）与可折叠生成日志区（checkpoint.generate.log+notes，默认展开、折叠显最新一条、nextTick 自动滚底、上滚暂停吸附、空数据不渲染）；types.ts RunCheckpoint.generate 增可选 log/notes。仅改 BuildProgressPage.vue 与 types.ts 两文件，未动任何 .py 与其它 .vue，未提交 git。

- 验证：cd frontend && npx vue-tsc --noEmit -p tsconfig.json → 0 错误；cd frontend && npm run build → 成功（19.1s，仅既有 chunk 警告）；node --import ./tests/ts_hooks.mjs tests/ontology_build_frontend.test.mjs → 通过 exit 0；node --import ./tests/ts_hooks.mjs tests/ontology_build_review_edit.test.mjs → 19/19 通过 exit 0
- 下一步：等后端 checkpoint.generate.log/notes 接线后浏览器联调真实数据；Codex 独立验收
- 依据/文档：文档/需求/20260920_从物料自动构建本体/需求说明_生成进度实时可观测_v1.md（主仓库未入本分支）

### 样式与交互统一(codex/ui_fix)：第五轮复验修复，补 C-4c/C-4d/B-6 三通道与穷举脚本自检，提交 a7c74d0 · zcode · 已实施，待验收

时间：2026-09-21T22:21:11.308516+00:00；记录：`.collaboration/entries/000180-6223ca870c70.json`

基线取 merge-base 07f8d8d（不可用 main 当前树，main 后又进 3 个仅文档提交）。历轮修复提交 76eeb15/bd2fa51/5bec5f8/0bd7d15/097e596/e87c195/0f76a59，本轮 a7c74d0。第五轮四组只读子 agent 判定首次分化（W1 通过 / W2 须补记 / W3 尚不可 / W4 仍不通过），共同指向“附表已穷举”这一结论仍不成立。按用户指令只修证据链并结束、不派第六轮。新增三条通道：C-4c（内联 style 搬进已存在的类时 class 令牌多重集差为 0，C-4 结构性看不见；改按同键元素的 style 串配对，窄口径实测 2 处，核为值中性）；C-4d（原生 select/option 换成 AppSelect，9 位点/2 文件，如实标注不可能值中性、未做页面级实测）；B-6（同文件内逐字不变、仅挪位置的规则，LIS 实测 530 条公共规则中 1 条位移，.empty 位次 65→407；第一阶段“共享类令牌”筛法报 0 条竞争者被本轮自判假阴性并保留在输出里，第二阶段按共挂+同特异性+属性重叠找到唯一竞争者 .card，两版都排在 .empty 之前，先后关系未翻转）。穷举脚本固化 5 组自检、任一不过即 exit 1，并反向验证其敏感性。DESIGN.md 就地更正 #123f82 仍是字面量等四处；开发计划新增 §9.8。本轮未触碰 frontend/src。此前各轮均未写共享交接，本条为该需求在本树的首条记录。

- 决定：不做代码回退：历轮查出的每处差异都是有意统一，缺陷在证据侧不在实现侧，修复方式是披露＋可复跑测量；凡声称“已穷举”必须由入库脚本自证口径有效（注入-检出可变测试），否则只是措辞；已写进 DESIGN.md 例外约束；本轮按用户明确指令结束、不派第六轮，故新增三通道与 §9.8 全部内容属“未经独立方复核”，不得读作已验收；四份子 agent 报告未逐字归档，§9.8 只登记判定标签与据此补测的内容，不逐条声称某通道由某复验方提出；对比一律用 git diff 07f8d8d 而非 main；文档内自引用数字改绑具体 SHA，避免证据脚本入库后当场失效
- 验证：node 证据脚本/class-imperative-delta.mjs → SELF-CHECK OK（①–⑤全过）、exit 0；C-4 实测 26 文件 / 88 位点 / 50 变化令牌；node 证据脚本/rule-order-delta.mjs → exit 0；位次两阶段统一为 1 基后，HEAD 侧 .empty 两阶段均报 407/566（原差 1 已修）；git diff --name-only 097e596 HEAD -- frontend/src 为空 ⇒ 本轮纯文档与证据脚本，未改前端代码，故未重跑 npm run build；grep 实测 #123f82：HEAD 仅 shared/graphStyle.ts:187 一处、基线 tools/InstanceGraph.vue:67 同值，与 DESIGN.md 新表述一致；插件三项审计与不截断债务计数沿用开发计划 §9.1 命令序列，本轮未重跑（无代码变更）
- 下一步：待用户指令：是否派第六轮独立复验；被验对象须为 a7c74d0 或其后的 HEAD（早于 e87c195 的提交上这三张表还不存在）；待用户指令：C-4d 的 9 个 AppSelect 位点与 C-3/C-4/C-4c 的值中性只有空白页复现级证据，需页面级 computed-style/像素比对才算实测；待用户指令：合并 codex/ui_fix 到 main 与重启主工作台需另行明确授权；本轮未合并，18765 主服务未动；隔离实例 18882 仍在，数据根在工作树内（main 库快照＋根密钥副本＋物料 blob，登记为含真实数据、不可自动丢弃）；测试数据 uiverify/验收本体 1.0.0/UI验收项目/UI验收编排 留在该副本内；例外 6 豁免的 padding/margin/gap/width 像素并档（约 2744 处 px-magic-number 集中处）仍未做，本轮不以该计数为门槛
- 依据/文档：文档/需求/20260921_样式与交互统一/开发计划.md §9.5/§9.7/§9.8；文档/需求/20260921_样式与交互统一/证据脚本/class-imperative-delta.mjs、rule-order-delta.mjs；DESIGN.md 规则 1 与例外 9/10/13；本树提交 a7c74d0；前置 0f76a59/e87c195/097e596/0bd7d15/5bec5f8/bd2fa51/76eeb15
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### build-governance V2-10 解析并发（G25）测试agent复测 第二轮 · zcode · 已验证

时间：2026-09-22T02:06:43.784899+00:00；记录：`.collaboration/entries/000180-7984298bd4b5.json`

V2-10 整改复测通过：三项整改全部闭合，无新问题。方式=/tmp 新导出树（3dc51dc），未触碰工作区他人改动。D：3dc51dc 恰 3 文件无夹带、README 仅 +1 行；修复=future.result() except 链补 except Exception → _scan_write_pool_failure 与超时同口径；注释已如实化。R1 独立复测 13/13：路径A RuntimeError、路径B base.parse 层返回 None 触发 AttributeError、路径C 我新增超长消息边界——均 run succeeded、崩溃文件 failed+原因、事实清空、其余 success。R2 白盒 12/12（上轮唯一失败的异常隔离已 PASS；G25b 峰值4/6.23→2.42s、env 7 形态；G25c 迟到时序 timeout@1.01 worker_done@3.01 后 facts 不变；G25d cancelled 保留+重扫 reused=2；G25e write_tx 全来自 build-run-*、解析线程 0 写）；G25a 补测内容+rowid 物理序一致。R3 回归：test_ontology_build 前两次 47/48 遇偶发 RST、后两次 250/250；parsers 95/95、purge 17/17、storage 60/60、runner 34/34、finish_guard 27/27、vue-tsc 0。R5 支持预存定性：双树同率探针（1200 请求×2）旧 21.5% vs 新 20.2%；机制=未登录 POST 401 早返回 57.3% RST vs 已登录 POST 0% vs 未登录 GET 0%。报告 /tmp/v2_test_report_v2_10.md 第二轮章节。

- 验证：R1 独立复测 13/13：路径A RuntimeError / 路径B base.parse 返回 None 触发 AttributeError / 路径C 超长消息(800字含换行) —— run 均 succeeded、崩溃文件 failed+原因含类名、factCount=0、其余 success；R2 白盒 12/12：G25b 峰值并发4 且 6.23→2.42s、env 7 形态；G25c 超时+迟到丢弃时序证据；G25d 取消保留+重扫 reused=2；G25e write_tx 25 次全来自 build-run-*、build-parse-* 0 次；G25a 补测：并发 1 vs 4，17 条事实内容 + rowid 物理插入序逐元素一致；回归：test_ontology_build 250/250（第3、4次；前两次遇偶发 RST 47/48）、parsers 95/95、purge 17/17、storage_contract 60/60、runner_isolation 34/34、finish_guard 27/27、vue-tsc exit 0；R5 双树探针（1200 请求×2 树）：旧树 RST 21.5% vs 新树 20.2%；机制：未登录 POST 401 早返回 57.3% RST、已登录 POST 0%、未登录 GET 0%
- 下一步：测试通过，待用户明确授权后由集成负责人集成合并 main（本轮验收基线 3dc51dc；区间含另一任务线 c55d94a/361df10 的已提交内容，集成时按其独立验收结论处理）；遗留建议（不阻塞、非 V2-10 范围）：未登录 POST 早返回分支的 ConnectionResetError（server.py do_POST 401 先于读 body），建议单独排期修复——早返回分支可 close=True 或先丢弃 body
- 依据/文档：/tmp/v2_test_report_v2_10.md（第二轮章节）；worktree build-governance@3dc51dc（pipeline.py +51/−4、tests +73、README +1）；workbench/ontology_build/pipeline.py:499-505（except Exception 隔离）、:521-547（_scan_write_pool_failure/_pool_failure_message）；workbench/server.py do_POST 鉴权段（RST 机制证据）

### 方法论 v1.1 修订：并入 Palantir 指导 + §7 重写为代码事实对照 · zcode · 已实施，待验收

时间：2026-09-21T14:38:00.560904+00:00；记录：`.collaboration/entries/000179-0003a988dab9.json`

按用户「更新方法论融合palantir可用内容」指令修订 自动化构建本体方法论_20260921.md 至 v1.1：①头部补 Palantir 输入源与修订记录；②§0 立场表加 Palantir 行（建模真实世界/设计判据/任务化验收）；③S8 增任务化验收裁定（unseen 业务问题测可答性、答不了记为缺口产出、人与AI分开测）；④新增 Palantir 采纳清单表（四优先级原则/身份与观测分离/precedence 权威源/反模式/Validation/分支治理，各标注落点与不采纳理由）；⑤§6 拆 6.1 工程事故类 + 6.2 Palantir 四条可确定性化反模式检查（System Silos/Kitchen Sink/God Object/Misnomer，各拟确定性检查与落点；Golden Hammer/Time Machine/Action Sprawl 说明不采纳理由）；⑥§7 整体重写：基线改为 main 已合并实现代码事实（protocol/llm/alignment/retrieval/delivery 只读核对），9 行逐阶段对照表（6段✅对齐、S4/S6/S7+执行模型🟡四缺口①接地②分批③频率门④批次降级），候选增量按真实差距重排 8 项（P0=接地/逐类分批/批次降级，P1=诱导层/反模式警示/相似建议+缓存，P2=任务化验收/覆盖率报告），7.3 限制令边界保留；⑦附录补官方四页链接与 9-15 能力级对照表。纯文档交付未改代码。

- 验证：grep 校验文档标题结构完整（0-7章+附录，无断节）；4.7 编号瑕疵已修正；§7.1 九行对照全部依据上轮已核实代码事实（含 llm.py:43-45/414-423、alignment.py:12-13 行号）；候选增量均标待拍板；7.3 边界声明与限制令核对一致，未新增白名单议题
- 下一步：候选增量八项待用户逐项拍板后才进需求线排期；P0 三项与生成实测失败根因直接相关，拍板后可与三项管理增量同分支排期
- 依据/文档：文档/需求/20260920_从物料自动构建本体/自动化构建本体方法论_20260921.md；https://www.palantir.com/docs/foundry/ontology/ontology-best-practices/

### ontology-build-v2-10-r2（V2-10 测试第一轮整改，worktree/build-governance） · zcode · 已实施，待验收

时间：2026-09-22T01:58:40.631818+00:00；记录：`.collaboration/entries/000179-c93d4abcce14.json`

测试第一轮 3 项（1 严重+2 一般）全部修复，单一提交 3dc51dc：严重#1 _collect_pool_result.future.result() 超时分支之外补 except Exception（不含 BaseException）——worker 内异常（注入失败、适配器契约外返回值在后处理段触发的 AttributeError）转该文件 failed+异常摘要+清空事实（与超时同口径），继续收集下一文件、不再杀死整个 run（新增 _scan_write_pool_failure/_pool_failure_message）；同提交把「0.5s 短片边界取消即时生效」注释改为如实描述（0.5s 轮询只保证主线程及时回到取消检查点，实际取消生效时间由当前文件解析速度决定，最坏 ≤120s）。一般#2 README 变更记录补 V2-10 一行（git diff --cached 复核仅 1 行新增）。测试新增 6 项断言覆盖两条复现路径（RuntimeError 注入 + 猴补 REGISTRY 适配器返回 None 触发 parse_material 后处理 AttributeError，未改 parsers 源文件），均断言 run succeeded、崩溃文件 failed 且原因含异常类名、事实清空、其余文件 success。

- 决定：异常隔离在 pipeline 侧实现（parsers/__init__.py 属另一任务线，未触碰）；落库口径与超时路径一致（failed + 清空既有事实 + failedSegments 记异常类名）；未夹带其他改动：偶发 ECONNRESET 属基线既有缺陷（探针量化见 verification），本轮仅报告不修改以守「不夹带」纪律
- 验证：tests/test_ontology_build.py 250/250（连续 2 次全绿 v210_r1/r2 + cr_2/cr_3 共 4 次；本轮共 7 次运行）；parsers 95/95、late_write 23/23、finish_guard 27/27、runner_isolation 34/34、materials_views 10/10、task_purge 17/17、exclusion_inheritance 17/17、merge_refs 42/42、storage_contract 60/60、storage_transfer 27/27；偶发失败定性（预存缺陷，非本轮引入）：约 1/3 运行在「未登录 POST」分支（main() 第 2262 行）报 ConnectionResetError。机制：server.py:313 鉴权门在未登录时不读取请求体即返回 401 并关连接，客户端读响应体时撞 OS 级 RST。定向探针（200 轮未登录 POST，两树同机对比）：当前树 60/200 ECONNRESET、基线树 c482547 66/200（同为 ~30%）——同量级；另有 V2-10 之前 R6 轮日志 /tmp/obt.log 同址复现佐证；vue-tsc 0 错误、npm run build 通过（本轮前端零改动）；ruff 改动文件全过
- 下一步：待测试 agent 第二轮复验（G25c 两条复现路径 + README 行）；建请协调者裁定：偶发 ECONNRESET 是否安排单独修复（建议方案：测试客户端对「未登录 401」响应体读取失败容忍为重试一次，或服务端在 401 前 drain 请求体——影响面小但均属本轮范围外，未擅自实施）
- 依据/文档：worktree/build-governance 分支 codex/build-governance：3dc51dc（本轮单一提交）；workbench/ontology_build/pipeline.py _collect_pool_result/_scan_write_pool_failure；探针脚本 /tmp/rst_probe.py（临时，未入库）

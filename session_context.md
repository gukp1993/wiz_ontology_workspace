# Codex / zcode 共享上下文

上下文版本：`9f989fafc5e51f37`

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

### ontology-build-v2-governance（第一轮验收整改 R6，worktree/build-governance） · zcode · 已实施，待验收

时间：2026-09-21T19:18:41.745556+00:00；记录：`.collaboration/entries/000172-d5c35beafb67.json`

第一轮验收 4 项（1 严重+3 一般）全部修复，分两次提交：8c6c9d9 = 严重#1 G24 blob 物理删除双前缀缺陷（删除侧改数据目录/blob 目录双基准解析，兼容生产登记 ontology-build-blobs/<名> 与旧裸相对名形态，穿越登记按缺失处理、绝不删目录外文件；purge 测试补生产形态回归——旧夹具路径基准与生产不同正是漏测根因）；5b95df8 = 一般#2 G19 兜底限额改单任务累计口径（storage.scan_fallback_usage 按任务全部 scan 运行检查点求和，重试/再扫描不重置；检查点只记本轮消耗避免跨运行重复计数）+ 一般#3 环境变量可配置（WIZ_BUILD_LLM_FALLBACK_MAX_FILES/_MAX_BYTES，正整数，启动前生效，默认 200 个/50MB 不变，capabilities 展示生效值）+ 一般#4 G23 abstract 模式落实真实复用持久化筛选/对齐产物（不重算确定性阶段，必炸桩探针实证）；08 分册 §4.3/§1.5/§12.2 与 README 同步更新，开发计划追加 §12.3 修正记录。

- 决定：G24 选删除侧兼容双形态而非改登记口径（登记与 material_blob_path 读取同源，兼容存量生产数据）；G19 限额按需求改单任务累计，未动需求；每轮消耗落检查点、跨运行求和即任务累计（对账可查）；G23 选实现复用（改动小且与 §7.1 一致），原文案转真
- 验证：tests/test_ontology_build.py 226/226（新增累计限额断言：限额 1 时重试仍降级且本轮消耗 0；环境变量覆盖/非法回退 3 项；abstract 必炸桩探针）；tests/test_ontology_build_task_purge.py 17/17（生产形态物理删除实测 + 穿越/缺失防御）；parsers 95/95、late_write 23/23、finish_guard 27/27、runner_isolation 34/34、materials_views 10/10、exclusion_inheritance 17/17、merge_refs 42/42、storage_contract 60/60、storage_transfer 27/27；vue-tsc 0 错误、npm run build 通过
- 下一步：待测试 agent 复验（只针对四项修复）；遗留预存债务如实报告：tests/test_ontology_build_task_purge.py:115 基线即有的 F841（root 未使用），按「既有代码不重排」未动；生产实例 6137 个孤儿 blob 的存量数据清理不在本轮范围（代码缺陷已修复，数据处置需用户决定）
- 依据/文档：worktree/build-governance 分支 codex/build-governance：8c6c9d9（G24）、5b95df8（G19×2+G23）；文档/接口文档/08-从物料自动构建本体接口.md §4.3/§1.5/§12.2；文档/需求/20260920_从物料自动构建本体/开发计划.md §12.3

### build-governance V2六项增量 测试agent独立验收 第一轮 · zcode · 受阻

时间：2026-09-21T19:02:01.335973+00:00；记录：`.collaboration/entries/000171-a016c92a38aa.json`

独立验收完成：六项增量中 G17/G18/G20/G21/G23 通过，G19 基本通过（限额「每轮扫描」vs 需求「单任务」、限额不可配置 2 项一般偏差待用户裁定），G22 复核通过，G24 不通过——发现严重缺陷：任务级联删除后 blob 物理文件全部残留（materials.py delete_task_blob_files 以 blob_dir() 为基准拼接相对 data_dir 的 blob_path，双重前缀永远 missing 静默跳过；基线 425a28d 已存在、非本轮 7 提交引入；实测 2 任务 7 文件 100% 残留；purge 测试夹具路径基准与生产不一致故未拦截）。finish_guard 场景1 断言改动经基线实证（git archive 425a28d 跑旧测试 25/27）为合理适配非掩盖回归，场景3 基线失败系真实缺陷已被本轮 finish_success 修复。回归：定向测试全过（222/95/60/27/11/10 等），run.py all 46过/5失败均为 worktree 缺 ontology/ 的环境性失败（主仓库交叉验证通过），external 按约定跳过；迁移 0003→0004 升级/幂等/存量 ALTER 路径全过；vue-tsc/build/前端 mjs 全过；隔离实例 18971 实测黑名单422+过滤报告/分片幂等/.xyz如实降级/重试排除/级联删除输入名确认全过，实例已按 PID 停止。完整报告：/tmp/v2_test_report_round1.md

- 验证：后端定向测试独立复跑全过：test_ontology_build 222/222、parsers 95/95、storage_contract 60/60、transfer 27/27、purge 11/11、其余 ontology_build 系列 17/27/23/42/34 全过；tests/run.py all 46过/5失败；5 个失败均为 worktree 缺真实 ontology/ 的环境性（非本轮改动文件，主仓库交叉验证 save_iteration 8/8 通过）；external 按约定跳过；迁移 0003→0004：链正确、升级/重复幂等/存量库 ALTER（旧行保留默认{}）/全新库 heads 全过；前端 vue-tsc 与 npm run build 通过；两个 build mjs 测试 8/8 与全过；隔离实例 18971 实测：黑名单 422+过滤报告、分片同index同hash幂等、.xyz 如实降级 partial、重试 parsed=1/reused=1、排除+409 CAS、级联删除确认；缺陷实证=删除后 7 个 blob 文件残留
- 下一步：开发 agent 修复 #1 blob 文件级联清理（改 base=data_dir 或改登记口径，二选一保持读写一致）并补按生产 blob_path 形态的回归用例，交付新 SHA 后复测；用户裁定 G19 限额口径（每轮扫描 vs 单任务）与可配置性落点；修正 G23 abstract 模式文案（契约/api.ts 注释/测试标题）或实现改为真复用
- 依据/文档：/tmp/v2_test_report_round1.md；worktree/build-governance@73bd704（基线425a28d+7提交）；文档/接口文档/08-从物料自动构建本体接口.md §13/§4.3/§12.5/§12.6；workbench/ontology_build/materials.py:484,578-599

### ontology-build-v2-governance（V2-2～V2-8，worktree/build-governance） · zcode · 已实施，待验收

时间：2026-09-21T18:23:33.627452+00:00；记录：`.collaboration/entries/000170-67bc10856624.json`

V2 六项增量全部实施并逐项提交（基于 425a28d，分支 codex/build-governance）：5373176 G20 格式黑名单三层（blacklist.py+build-task-filter+view=filter 过滤报告+422 BLACKLISTED+迁移 20260921_0004）；186a080 G18 图片/OCR（kind=image+image_parser/ocr_support+扫描页 PDF OCR 路径+capabilities ocr 动态探测）；04bf5da G19 LLM 兜底三级分派（llm.fallback_parse+限额+弱证据 LLM_FALLBACK_EVIDENCE 降级）；07c7bd1 G23 生成断点续跑（批次检查点+resumeMode auto/abstract+基线约束+失败/重启状态回退 scope+回退后重试成功恢复 review）并收敛 finish_guard 场景1 至 §12.3 语义；f5f7a96 G17 上传断点续传+G21 大文件夹软引导（纯前端）；26ff8e8 lint 清理。08 分册契约先行并同 commit 更新（§2.2/§4.3/§13 新增，§1/§2/§4/§5/§12 扩展，README/05 登记）。G22 单物料重试/排除与 G24 级联删除为既有实现，本轮补验证（flow_image_ocr + task_purge 11/11）。

- 决定：黑名单硬层为安全边界不可配置，白名单只能越过软/自定义层；ZIP 既有排除口径（密钥/构建产物/大二进制）显式收口为硬规则；LLM 兜底限额按「每轮扫描 200 文件/50MB」实现（需求原文「单任务」；扫描复用成功材料，实际增量消耗近似任务级，已在 08 §4.3 如实表述）；G23 语义收敛：有失败批次时 generate 运行改判 failed（此前部分批失败仍 succeeded 无入口可续跑）；generate failed/取消/重启中断时任务 generating→scope，回退后重试成功且基线未变恢复 review；finish_guard 场景1 断言按 08 §12.3 取消回退语义更新（该测试自 fca1f2b 起与旧断言冲突，属既有漂移非本轮引入）；G17 已知边界如实登记：续传状态在页面内存，刷新页面丢失 uploadId 按新上传处理（服务端 TTL 有界清理）
- 验证：tests/test_ontology_build.py 222/222（基线 163 项+新增 59 项：G20 13 项/G18 13 项/G19 12 项/G23 12 项等，含正反路径）；parsers 95/95、late_write 23/23、finish_guard 27/27、runner_isolation 34/34、materials_views 10/10、task_purge 11/11、exclusion_inheritance 17/17、merge_refs 42/42；test_storage_contract 60/60、test_storage_transfer 27/27（20260921_0004 迁移实测 0003→0004 升级+重复 upgrade 幂等）；vue-tsc 0 错误、npm run build 通过；ontology_build_upload_filter.test.mjs 8/8、ontology_build_frontend.test.mjs 通过；ruff check 对本轮全部改动文件通过
- 下一步：待 Codex 独立验收（本报告为 zcode 自测，不替代验收）；浏览器点击级验收点：物料页过滤报告区、进度页失败入口与「重试失败批次/从 abstract 阶段重试」、上传中断续传、大文件夹软引导层；真实 tesseract OCR 与扫描版 PDF 识别质量未验证（环境无 tesseract/poppler，成功路径经假 OCR 注入验证）；LLM 兜底真实模型语义质量未验证（假模型验证协议与降级路径）；规模限额校准、G16 样本验证维持既有未验证状态；集成合并仍按流程等用户明确指令
- 依据/文档：worktree/build-governance 分支 codex/build-governance 5373176/186a080/04bf5da/07c7bd1/f5f7a96/26ff8e8；文档/接口文档/08-从物料自动构建本体接口.md §2.1/§2.2/§4.3/§5/§12.5/§12.6/§13；文档/需求/20260920_从物料自动构建本体/开发计划.md §12（含 V2-8 现状核对结论）

### 三项增量实现完成（用户改回由 zcode 继续）+ 生成实测后台运行中 · zcode · 已实施，待验收

时间：2026-09-21T08:38:08.183946+00:00；记录：`.collaboration/entries/000169-27668660677a.json`

用户改回由 zcode 完成。任务一三项增量代码完成并提交：fca1f2b（后端分组/分页/purge/取消回退+21 项测试）、37eae0b（前端后缀过滤/分组浏览/停止与级联确认+8 项测试）。契约 08 §12 于 9e66460 预先冻结。创智源生成实测后台运行中（jsonId 实测由后台子 agent 并行跑）。18881 未重启——等两个生成任务跑完再做集成重启与浏览器实测。

- 决定：分组口径：按 relPath 顶层目录聚合（folder=''=根目录），聚合恒加载，分组视图默认、平铺分页切换、详情弹层 100/页。；删除语义改为级联物理清理（任务行+11 张关联表+blob 文件），已交付本体草稿保留；文件删除在事务提交后进行且失败不回滚。；后缀过滤为前端选择时过滤（白名单归一：中文分号/顿号分隔、大小写、连续点），展示选中/上传/跳过计数。
- 验证：新增回归：materials_views 10/10、task_purge 11/11（含已交付本体保留、blob 文件删除、跨账号隔离）；upload_filter 8/8。；回归不退化：test_ontology_build、late_write、merge_refs、review_edit、ontology_build_frontend 全过；vue-tsc 0 错误、npm run build 通过。
- 下一步：等创智源（retrieve 27.6 万事实筛选中——试点规模校准的真实性能数据点）与 jsonId 两个生成完成后：重启 18881 加载新前端与后端，浏览器实测三项功能，再交 Codex 验收。；检索阶段性能建议作为规模校准专项数据点。
- 依据/文档：提交：fca1f2b（后端）、37eae0b（前端）；分支 codex/build-governance；契约：文档/接口文档/08-从物料自动构建本体接口.md §12（9e66460 冻结）；测试：tests/test_ontology_build_materials_views.py、tests/test_ontology_build_task_purge.py、tests/ontology_build_upload_filter.test.mjs

### 仓库架构整理与代码规范（codex/repo-cleanup）集成合并 main 与清理 · zcode · 已验证

时间：2026-09-21T07:26:33.650100+00:00；记录：`.collaboration/entries/000168-4cc55cd1adf8.json`

按用户「合并main并删除worktree」指令完成集成。两次原样归档 main 他人未提交协作文件（5755e72、a4dd4e6；期间 main 前进 2496b9b AGENTS§3修订与 5f64fd7 meeting-48评审，均重新组合）。临时集成树合并 codex/repo-cleanup：AGENTS 两侧自动合并共存，session_context 冲突取 main 侧后脚本 render 重建（merge 1279542、重组 3debc02）。组合验证：ruff 全过/eslint 0/build 4.02s/TS 6:6/quick 3:3/all 48:49（败=pypdf 缺口，main 对照在案）。关键风险处置：合并会从工作树磁盘删除 991 个已退跟踪 ontology 文件（集成树实证），main ff 后立即 git restore --source=5755e72 --worktree 恢复，磁盘 ontology 995/.idea 6 与基线一致。附带发现：5 个测试用例依赖磁盘 ontology/models/storage 播种，main 有盘不受影响，新克隆环境会失败（既有依赖被暴露，建议专项）。清理：删除两个工作树与分支；18765 未更新，真实库/keys 未动；另一会话在 main 暂存的文档重组未提交内容原样未动。

- 决定：main 快进前用 git restore --worktree 恢复 ontology/.idea 磁盘文件：退跟踪不得删除 main 工作树迁移备份与密钥目录（『磁盘文件全部保留』在 main 侧同样成立）；他人未提交协作文件按既有模式原样归档；另一会话暂存中的文档重组不代为提交、原样保留；session_context 冲突不手工拼接，取一侧后 render 脚本重建；新克隆环境 5 用例种子失败登记为后续专项，不在集成中扩大范围
- 验证：main 3debc02：tracked ontology=0/.idea=0/总数883；磁盘 ontology=995(=基线)/.idea=6(=基线)；除他人暂存重组外无本任务残留；组合验证：ruff All checks passed、eslint exit 0、build 4.02s、TS 6/6、quick 3/3、all 48/49（pypdf 缺口 main 对照 83/85 同败）；无盘失败定位：集成树合并后 ontology/ 被 checkout 删除致 5 用例败，恢复磁盘后全过——纯磁盘种子缺失非代码回归；合并构成：996 D=991+5；Palantir 对照表 D+A 系 rename 断裂假象（新路径与 main 版 diff 为空）；69 R100=备份改名；非目标删除 0；端口18941无监听、两树无进程；18765 HTTP 200 未动
- 下一步：主工作台 18765 运行合并前代码，更新需另行授权（build+重启）；新克隆环境测试种子依赖建议立项专项（测试自含种子或跳过守卫）；main 备份 data/workbench-before-build-migration-20260921-143248.sqlite3 保留
- 依据/文档：main 链：5755e72→a4dd4e6→3debc02(含 merge 1279542)；被合并 codex/repo-cleanup@2c6a44d；文档/需求/20260921_仓库架构整理与代码规范/；验收记录提交 b3e912b/5a47ede

### meeting-48 续会7纪要评审并产出 A1 裁决前置草稿（本体自动化构建） · zcode · 已实施，待验收

时间：2026-09-21T07:21:06.146095+00:00；记录：`.collaboration/entries/000166-e7a8098883bf.json`

评审 meeting-48-export.md（续会7，材料为方向整合议程 08c5cbd1fc46，用户全程未发言、awaiting_confirmation），交付 文档/自动化构建续会7纪要评审_20260921.md（107 行，commit 5f64fd7）。判定六轮最佳：按议程推进（B2 滞留→A1 前置→产出物规格）零绕圈，不再讨论清单/确认记录表规则/第六节第5条分工均生效（W11'提交代码快照'表述被纠正为 worktree 路径），多次自我修正，A1 正确留白待业务方裁决。议题收敛质量高：滞留口径+关闭条件三段式+B1/B2/B3 三检查点拆分；A1 前置贡献判定基线/溯源基线分离（解析器指纹只溯源、判定函数物理隔离）、规则集指纹双基线、单一事实源。吸收两处对本侧方向文档的修正：A1 量级佐证后置（当前无真实存量确认记录，只做功能性预演）、滞留窗口定义前置化挂 B1。新问题：orchestrator truncated 第五次且丢失最重（发言 45-61 的三态状态列/断言清单/出处自检/两套状态语义区分整体未进纪要）；结构化区第五次空；发言 58 断言清单'四条'指认与发言 54 三条清单矛盾。按会议分工（散落结论整理归本侧）产出 A1 裁决前置强制产出物：按 B1/B2/A1/C1C2/D1D3 检查点分列的三态草稿（共识/倾向/待裁决+对话出处）+ 经出处核对的 D3 断言清单（①同批落键②干跑隔离③解析器指纹明确认领，④口径文档版本引用为候选）。下次方向：A1 裁决（对三态草稿逐项拍防顺手全批）→ C1 立项落 build-governance worktree（18881 已就绪）→ B1 前置数值 → D1 收尾文档；导出工具缺陷累计五次须反馈。纯文档+只读核对。

- 验证：核对用户未发言（对话仅 system'用户结束会议'一条用户痕迹）；worktree 路径修正确认：发言 45 引用方向文档第六节第5条、发言 46/50 W11 接受并修正表述；断言清单出处核对：发言 6/8 同批落键、13/14 干跑隔离、37/38 解析器指纹明确认领；发言 44 版本号引用未明确认领为 D3 断言；发言 58'四条'理由（补解析器指纹）与发言 54 三条清单（已含）矛盾；三态草稿逐条对照对话出处（约 25 条目）标注共识/倾向/待裁决；git show 5f64fd7 仅含该评审文档 1 文件 107 行
- 下一步：用户/业务方可持评审第五节三态草稿直接进行 A1 裁决（stripped_dump vs raw_bytes+随行项）；裁决后 C1 立项：build-governance worktree（18881、main 快照已就绪）实施滞留留痕+D3 断言（R02/R10+①-③）；D1 收尾文档待用户指令由本侧产出（含新增拍板项节+两条前置补记+单一事实源框架）；导出工具缺陷五次，建议用户向工具方反馈
- 依据/文档：文档/自动化构建续会7纪要评审_20260921.md；commit 5f64fd7
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### worktree 数据根迁入分支文件夹（各自独立管理）+ 纠正 18765 错位 · zcode · 已实施，待验收

时间：2026-09-21T07:18:52.282280+00:00；记录：`.collaboration/entries/000165-aaeb7d91d797.json`

按用户指令把数据根从仓库外平级目录迁入各自分支工作树内（WIZ_WORKBENCH_ROOT=工作树根，data/ 与 keys/ 自含，镜像 main 运行布局）。迁移中发现并纠正一个严重错位：18765 曾由 build-governance 工作树目录内启动的进程服务（14:48 起），用户在 18765 的操作实际读写工作树数据副本。已停两个错位实例（按 PID+cwd 核对），18765 从 main 仓库真实根重启（start.sh，PID 9300），18881 以工作树自含数据根重启（PID 9356，admin/admin 验证通过）。用户上传的 1161 个物料 blob 已从旧目录随迁至工作树 data/ontology-build-blobs；误初始化的空库与旧平级目录已清除。AGENTS §3 规范修订为工作树内自含数据根（commit 2496b9b），登记同步更新。

- 决定：数据根位置定为工作树根目录本身（镜像 main 布局 data/+keys/），废弃仓库外平级目录模式；AGENTS 0f287be 规则相应修订。；git worktree remove 因未跟踪数据被拒属保护机制；删除或 --force 前必须经用户确认数据处置（已写入规范）。；18765 错位期间无数据丢失：用户操作写入的是 main 快照副本（已随迁），main 真实库未被写坏（mtime 14:56 的写入属其正常使用）。
- 验证：18765=PID 9300 cwd=main 仓库 HTTP 200；18881=PID 9356 cwd=worktree/build-governance，curl admin/admin 登录返回 admin(isAdmin=true)；worktree/data 11 资产、1161 blob、keys/wb-root.key 0600 就位；旧平级目录已清除。；期间一次执行链因 rmdir 失败后台化导致 task.env 未更新、实例错位——已现场盘点（进程 cwd/ps env/三库对照）后全部纠正，终态以 curl 与 cwd 双重验证。
- 下一步：用户照常使用：18765=main（原密码），18881=build-governance 副本（admin/admin，数据为快照+其上传）。；后续新建 worktree 按 AGENTS 修订版执行：数据根=工作树根，backup 快照+根密钥随行。；两个实例的会话 Cookie 同源 127.0.0.1 不同端口可能互相覆盖（AGENTS 已有告诫），浏览器验收分实例用隔离上下文。
- 依据/文档：commit 2496b9b（AGENTS §3 修订）；登记 .git/workbench-tasks/build-governance.json；实例 18881 数据：worktree/build-governance/data/workbench.sqlite3 + data/ontology-build-blobs（1161 文件）+ keys/wb-root.key

### AGENTS.md 新增规范：新建 worktree 默认同步 main 库快照 · zcode · 已确认决定

时间：2026-09-21T07:07:36.522234+00:00；记录：`.collaboration/entries/000164-7ee869e3e45d.json`

按用户指令在 AGENTS.md §3 启动与数据隔离写入规范（commit 0f287be，main）：新建开发 worktree 时隔离数据根默认用 transfer backup 生成 main 库 WAL 一致性快照直接落位（禁止 cp 活跃库文件），并复制根密钥（0700/0600，否则副本加密凭据不可解密）；快照为时点数据；副本内可按用户要求重置口令；数据根含真实数据与根密钥须登记为不可自动丢弃、清理须用户确认；自动化测试临时隔离根不适用本条仍用空库。原「默认合成数据」句同步改写为指向新规则并保留测试例外。来源背景：build-governance 工作树空库实践（admin 无法登录、加密凭据不可解密）。

- 决定：规范定为开发 worktree 的默认行为（用户指令），并显式划出例外：自动化测试临时根仍空库；用户明确要求空库/合成数据时从其指令。；同步用 transfer backup 一致性快照而非 cp 活跃库文件；根密钥必须随行，否则副本内加密凭据不可用。
- 验证：git show 0f287be 仅含 AGENTS.md 1 文件 2 增 1 删；落点为 §3 启动与数据隔离；commit 信息含动机与操作细节。
- 下一步：后续新建 worktree 按此规范执行（快照+根密钥+登记标注不可自动丢弃）；既有 build-governance 数据根已是该形态。

### build-governance 工作树实例搭建 + 从 main 同步数据（用户指令） · zcode · 已实施，待验收

时间：2026-09-21T07:00:42.658671+00:00；记录：`.collaboration/entries/000163-8dd9aef6aa66.json`

用户报 admin/admin 登录失败。核实：新工作树按约定只登记未启动（无 venv/无库/无服务），18881 从未跑过；18765 的 admin 真实密码并非 admin（此前靠已登录会话）。按用户指令从 main 同步：worktree 建 .venv（--system-site-packages）；main 库一致性快照放入隔离数据根；复制根密钥使副本内加密凭据可解密；前端 dist 复制自 main 同 commit 构建产物；副本内重置 admin 密码为 admin（仅隔离副本）；启动 18881 并验证登录成功。main 18765 完全未动（密码/数据原样）。

- 决定：同步方式用 transfer backup 一致性快照直接落位为工作树库（CLI 无 restore 子命令，快照即完整库）；根密钥随行复制，否则副本内模型密钥/连接密码不可解密。；admin/admin 只在隔离副本内重置生效；main 真实库密码重置属真实数据操作，未获指令不做。数据根现含真实数据与根密钥，登记改为不可自动丢弃。
- 验证：curl POST 18881/api/auth-login admin/admin 返回 admin(isAdmin=true)；前端首页 200；快照含 main 全部数据（11 资产/2 账号）；18765 未受影响。
- 下一步：用户可访问 http://127.0.0.1:18881 用 admin/admin 登录（数据为 main 同步时点快照，不自动跟随 main）。；若还要把 main 18765 的 admin 密码改为 admin，属真实库重置，需用户明确指令（CLI 已具备）。；后续清理该工作树时数据根含真实数据与根密钥，须用户确认处置。
- 依据/文档：实例：http://127.0.0.1:18881；工作树：worktree/build-governance；数据根：wiz_kq_builder_v2-build-governance-data（含 keys/wb-root.key 副本）；登记：.git/workbench-tasks/build-governance.json（env_ready_dev_pending）

### 仓库架构整理与代码规范（codex/repo-cleanup）B项整改复验 · zcode · 已验证

时间：2026-09-21T07:12:37.428738+00:00；记录：`.collaboration/entries/000162-514cd6dad452.json`

用户指令「继续验收」，对整改提交做第2轮独立复验，判定【通过】，总体验收由不通过转为通过。复验 HEAD abab867，聚焦 B 项整改（业务提交 b8627db..eeb7097 内容未变，首轮 A/C/D 通过结论继续有效）：df941aa 整改提交 996 行改动=991 ontology+5 .idea 索引删除、零混入其他改动；git ls-tree -r HEAD 树级 ontology/.idea 均 0（提交对象级证据）；tracked 865（=864+abab867 新增交接条目，构成吻合）；磁盘 ontology 991、.idea 5 文件（含 .idea/.gitignore）全部保留；data/keys 全程 b4a56a9..HEAD 零接触；.gitignore 规则在。开发计划 §7 整改记录如实（根因=拆分提交前 git reset 撤销 rm --cached 暂存、git add -u 无法恢复删除暂存，与首轮验收推断一致），验收指令两处期望值已修正（交付物顶层md 8、tracked ≈864）并与本轮实测吻合。quick 3/3 哨兵通过，工作树 clean。复验记录已提交 5a47ede。

- 决定：B 项整改复验通过：以 git ls-tree 树级证据为准（非索引瞬时态），996 纯删除+磁盘保留+data/keys 零接触三条件齐备；历史提交信息错位不要求改写（本地未合并分支允许但验收以最终树为准），错误数字以开发计划 §7 整改记录与 df941aa 提交信息更正为准；A/C/D 沿用首轮结论：整改为纯 git 索引操作无代码变更，首轮 all 48/49（pypdf 环境缺口经 main 对照）、TS 6/6、语义抽查 6/6、ruff/eslint/build 均不受影响
- 验证：df941aa 纯度：git show --name-status 统计 996 行=991^D ontology+5^D .idea，非删除 0；树级：git ls-tree -r HEAD | grep -c ^ontology/ = 0；^\.idea/ = 0；git ls-files 总数 865；磁盘：find ontology -type f = 991；ls -1A .idea = 5 文件；git diff b4a56a9..HEAD --stat -- data/ keys/ = 0；文档：独立验收指令.md:41 期望 8（原≥12）、:58 期望 ≈864（原855左右）；开发计划 §7 整改记录含根因与数字更正；哨兵：python3 tests/run.py quick 3/3；git status clean
- 下一步：状态：待用户授权集成。合并 main、清理工作树、更新主服务均待用户明确指令；集成时注意 .collaboration entries 本分支与 main 各自新增需合并日志（同序号不同内容：本树 000158-000162 与 main 000158-000164）；合并后 main 工作树磁盘 ontology/ 不受影响（本地文件保留），新克隆环境将不再自带旧文件树（新环境初始化只依赖 transfer CLI 与库内数据）
- 依据/文档：文档/需求/20260921_仓库架构整理与代码规范/独立验收记录_20260921.md（含复验记录节）；复验提交 5a47ede；整改提交 df941aa/09ae5e2/abab867；首轮验收 b3e912b；基线 b4a56a9

### 为自动化构建增量/治理创建 worktree（用户明确指令） · zcode · 已确认决定

时间：2026-09-21T06:43:09.984017+00:00；记录：`.collaboration/entries/000162-eb5ae3cbf932.json`

按用户指令从最新已提交 main（452d0ae）创建分支 codex/build-governance 与工作树 worktree/build-governance/，登记端口 18881（已核实空闲）、隔离数据根 wiz_kq_builder_v2-build-governance-data（空）、环境文件 .runtime/task.env、公共登记 .git/workbench-tasks/build-governance.json（created_awaiting_dev）。用途：自动化构建增量/治理（会议增量与 R02/R10 断言挂测试集候选）。仅创建与登记：未装依赖、未建 venv、未建库、未启动服务、未开发；main 未提交内容未带入；既有 assist-fill-production 与 repo-cleanup 工作树未触碰。

- 决定：分支名经用户选择定为 build-governance 方向（codex/build-governance）；工作树按 2026-09-20 约定落在主仓库 worktree/ 目录下；端口选用 18881 避开 18765/8765 与历史任务端口。
- 验证：git worktree list 含新树且指向 452d0ae[codex/build-governance]；分支名预先核实未占用；/worktree/ 在 .gitignore 第 21 行；登记 JSON 通过 python3 -m json.tool 校验。
- 下一步：等用户下达开发指令后在原工作树实施；开发授权后步骤：worktree 内建 .venv、source .runtime/task.env、transfer init 建隔离库、起 18881 隔离实例；验收通过停在待用户授权集成。
- 依据/文档：工作树：/Users/gukepeng/Desktop/ZHDL/code/wiz_ai/wiz_kq_builder_v2/worktree/build-governance；登记：.git/workbench-tasks/build-governance.json；环境：worktree/build-governance/.runtime/task.env

### 项目能力补齐推进计划表交付 · codex · 需求已交付

时间：2026-09-21T06:36:30.147063+00:00；记录：`.collaboration/entries/000161-b85426020157.json`

交付P00-P13分阶段推进表，先核对基线和储能试点，再决定说明语义、依赖固定、绑定验证及后续消费能力；历史恢复、MCP与动作执行保持待决策/延期，不启动实施。

- 验证：核对依赖顺序、完成标准与授权停止点；未改代码、未创建worktree、未更新服务。
- 依据/文档：文档/需求/20260921_项目能力补齐推进计划/推进计划表.md

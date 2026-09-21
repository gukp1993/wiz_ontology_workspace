# Codex / zcode 共享上下文

上下文版本：`6576b4416f74d42d`

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

### system-deep-test 全方位深度测试执行与交付（Q00-Q08） · codex · 已验证

时间：2026-09-21T01:16:28.247683+00:00；记录：`.collaboration/entries/000135-58501560ea5f.json`

在 worktree/test（codex/test，业务SHA d6c73c2零修改）完成独立深度测试：Q02本体34断言、Q03项目+编排74断言、Q05可靠性/边界~75断言、Q04浏览器生产入口实测；Q06性能按用户指令中途取消（仅留有限样本，无统计结论）。总体结论：不通过（存在P1新缺陷），主干CAS/幂等/隔离/脱敏全部符合契约。新发现D-Q02-01(P1在线发布不写release-zip致快照恢复断链)、D1(P2 POST缺Origin放行与README403口径矛盾)、D2(P2损坏库回传内部异常文本)、D3(P3跨账号错误码口径)、Q04-01(P3撤销按钮label陈旧)；基线复现并升级证据A01/R01/R02(根因project_validation.py:512)，A02重定性为本树UI生产入口不可达(死代码)。已提交4d4e497（测试脚本+两份报告+执行记录+子任务entries），证据JSONL留.runtime不入Git。未合并main、未清理；18931(PID56914)保留运行，18932/18933/18939已按PID+cwd确认后停止释放。

- 决定：Q06以用户指令取消为准，已采样本标注n=1仅供参照，不预写性能结论；删除→撤销疑点经受控复测排除（有确认框、撤销正确恢复并落库），原判为协调者误读；/api/releases语义为release-ZIP工件列表而非版本历史（Q03澄清），但D-Q02-01定性不变：在线发布永不写工件→恢复链路对新资产不可达且文档标可用；D1/D3按'文档bug或实现bug'流程交owner裁定，测试侧不改文档；视觉/宽度/缩放/画布/键盘类因应用内浏览器隐藏记环境阻塞，不判缺陷也不判通过
- 验证：本体链34断言31通过+A01复现+D-Q02-01（tests/deep_ontology_o1o2/o3o4o5/o6o7.py）；项目+编排74断言73通过+R02复现（tests/deep_project_chain/deep_flow_chain.py）；I1-I9全过：kill -9注入integrity_check=ok、63端点未登录401、密钥字节扫描0明文（tests/deep_integrity_*）；UI删除/撤销/重做/登录退出受控复测+API交叉核对（q04/REPORT.md）；server-18931.log全程0命中500；mapping_forms.test.mjs基线失败单列未修
- 下一步：交回用户安排修复：优先D-Q02-01→A01→D1裁定→R01→D2（缺陷清单前五项）；集成/合并需用户明确授权后由Codex串行组合重验；18931与test-data*保留待处置；如需补全Q06：tests/deep_perf.py已就绪，须独占实例运行
- 依据/文档：文档/需求/20260921_系统全方位深度测试/测试报告.md；文档/需求/20260921_系统全方位深度测试/缺陷清单.md；文档/需求/20260921_系统全方位深度测试/测试计划.md（实际执行记录）；提交4d4e497；被验业务SHA d6c73c2
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### Q03 深度测试：项目配置+编排业务链（P1-P9） · codex · 已实施，待验收

时间：2026-09-21T01:09:14.900246+00:00；记录：`.collaboration/entries/000134-3f1de0ed1d0e.json`

专属实例18931完成P1-P9，74条断言：73通过+1基线已知(R02)，未发现新增业务缺陷。三条数据线职责符合设计：本体save/publish/versions、项目CAS/往返无损/缺身份发阻断、编排check纯配置绝不执行+纯本地calc确定结果+错误定位+重复run不脏数据。安全：connection-secret不回显、跨账号读/写他人id一律404、探测不持LOCK。红线遵守：未连真实MySQL/Redis、未执行真实SQL/用户Python、flow-run仅calc、连接探测只打127.0.0.1关闭端口/静默mock并结束关闭。P8.4复现R02(known)：空壳编排(输出未绑定)被项目flow来源引用，project-validate不拦、publish仍成功。

- 决定：两处测试侧纠偏(非业务缺陷)：P1.6误用/api/releases(实为release-ZIP工件列表)改查/api/versions；P6.5破坏性编辑造成deviceCode悬空被/api/save正确422拦截，改删无悬空属性clusterActivePower
- 验证：tests/deep_project_chain.py 51条(50 pass/1 known=R02)；tests/deep_flow_chain.py 23条全pass；证据jsonl+REPORT.md在 .runtime/test-evidence/q03/
- 下一步：R02(中)按既有缺口交owner裁定：project_validation._check_flow_binding 不调用 flows.check_flow；Q03只测试记录，未改业务代码，未git commit
- 依据/文档：.runtime/test-evidence/q03/REPORT.md；tests/deep_project_chain.py；tests/deep_flow_chain.py；workbench/project_validation.py:512
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

### Q05 深度测试：数据可靠性+账号/接口边界 · zcode · 已实施，待验收

时间：2026-09-21T00:59:10.151882+00:00；记录：`.collaboration/entries/000133-1c391f575ca0.json`

专属实例18932完成I1-I9：约75条断言通过。无跨账号数据泄露、无密码明文回读；kill -9两轮+损坏库副本探测均无半写。缺陷：D1 POST缺Origin被放行与README「缺失或不符403」不符；D2 存储损坏时GET /api/state返回400携带原始Python解析文本（应503/通用500）；D3 跨账号connection-secret返回400、llm-provider-delete对他人id返回200 cleared=true，与06「按不存在404/空」口径不符（实测均无越权效果）。

- 验证：tests/deep_integrity_http.py 59条(58 pass/1 fail=D1)；tests/deep_integrity_restart.py write/verify/freshroots 全过；tests/deep_integrity_fault.py prep/hammer/check/corrupt-* 全过；报告与jsonl证据在 .runtime/test-evidence/q05/
- 下一步：D1-D3 按「文档bug或实现bug」流程交owner裁定；Q05结束已停止18932并释放端口
- 依据/文档：.runtime/test-evidence/q05/REPORT.md；tests/deep_integrity_http.py；tests/deep_integrity_restart.py；tests/deep_integrity_fault.py

### system-deep-test环境与测试指令 · codex · 需求已交付

时间：2026-09-20T16:05:14.587942+00:00；记录：`.collaboration/entries/000132-a740c1659977.json`

按用户最新命名创建并登记worktree/test和codex/test，从main d6c73c2派生；交付测试计划和完整执行指令，未安装启动或执行测试。

- 决定：仅独立测试，不修业务代码、不合并main；覆盖UI、交互、功能、可靠性、性能。；端口18931创建时核实空闲，隔离数据根.runtime/test-data；未包含其他未合并修复。
- 验证：目录和分支已由测试重命名为test，登记与文档同步。
- 依据/文档：文档/需求/20260921_系统全方位深度测试/执行指令.md；文档/需求/20260921_系统全方位深度测试/测试计划.md

### 本体与项目辅助填写原型委托需求 · codex · 需求已交付

时间：2026-09-20T12:04:28.558493+00:00；记录：`.collaboration/entries/000131-082bbd8f1bdb.json`

按用户改为由其他harness制作原型的要求，交付完整需求与独立执行指令；本轮未生成HTML或实施业务功能。

- 决定：本轮两份文档，不生成开发计划；其他harness生成同目录交互原型_v1.html，后续按清单验收。；覆盖本体5类及项目6类辅助填写场景，7条演示链路、17项验收；使用现有风格及离线假数据。；采纳只进入表单，旧建议失效保护、共享影响确认、缺信息追问必须演示；复杂编排生成与真实模型调用不在本次原型必做范围。
- 验证：文档场景/验收编号完整性与两份文件范围检查通过，git diff --check通过；未声称原型或浏览器已验收。
- 下一步：其他harness按执行指令制作独立原型并自检，用户交回后按A01至A17验收。
- 依据/文档：文档/需求/20260920_本体与项目辅助填写/需求说明.md；文档/需求/20260920_本体与项目辅助填写/执行指令.md

### acceptance-fixes环境与修复指令 · codex · 需求已交付

时间：2026-09-20T10:07:25.055603+00:00；记录：`.collaboration/entries/000130-c41ccb18eab1.json`

按用户要求从main b0fb7c0创建worktree/acceptance-fixes，分支codex/acceptance-fixes；修复计划和完整指令已在该分支提交，未开始业务开发。

- 决定：只覆盖合并验收R01-R03/A01-A02；实施后交独立验收，不自动合并main。；端口18921创建时检查可用并登记，尚未启动；隔离数据与Python环境均在该树.runtime。
- 验证：Git工作树创建成功，任务登记加锁写入公共Git目录；文档diff检查通过。
- 依据/文档：worktree/acceptance-fixes/文档/需求/20260920_本体与项目统一维护体验改版/验收修复执行指令.md；worktree/acceptance-fixes/文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md

### 规则动作字段精简独立验收并合并记录 · codex · 已验证

时间：2026-09-20T09:57:12.318295+00:00；记录：`.collaboration/entries/000129-2fb2a7d94131.json`

字段精简需求暂不通过：非文本content/effect可发布、图谱业务定义必填可绕过。独立记录合并到统一维护开发计划11.6，保留上轮11.5结论。未修改业务代码。

- 决定：本次统一缺陷编号A01/A02，前次R01-R03未复验不关闭；原需求开发计划仅补归档链接。
- 验证：构建通过；规则/动作模型、Excel导入27项、图谱桥接回归通过；后端规则40项、动作库与动作HTTP74项通过。；正式发布路由函数在隔离存储接受对象content与数组effect，200并读回发布快照；图谱桥接空定义返回成功并触发changed。；模板与需求原件逐字节一致，四页/表头/冻结/下拉公式和业务空白区检查通过；未做本轮浏览器与Excel原生验收。
- 下一步：修复统一清单问题后补独立复验及浏览器导入闭环。
- 依据/文档：文档/需求/20260920_本体与项目统一维护体验改版/开发计划.md；文档/需求/20260920_规则动作字段精简与Excel模板同步/开发计划.md

### 执行 20260920_规则动作字段精简与Excel模板同步 执行指令 · zcode · 已实施，待验收

时间：2026-09-20T09:17:31.560193+00:00；记录：`.collaboration/entries/000128-fd06303c7ef5.json`

规则/动作字段精简与 Excel 模板同步全部实施并提交（b690a1a）：规则 name/description 必填、content 选填、不再提供新 output 输入；动作 effect 文案改「预期效果」且选填（存储键不变）；历史 output 读取/编辑/保存/发布/配置导出导入零丢失，有值时只读展示「历史补充说明（原输出结果）」。接口文档先行（02 §4.9/§4.10 + README 变更记录），后端 workflow.py 单条保存只校验该记录，前端三字段表单与对象页、图谱关联详情统一口径，Excel 新表头（规则三列、动作预期效果）并保留旧「输出结果」「业务效果」别名，新旧效果都非空且不同阻断该批。新模板 ontology-import-rule-action-v1.xlsx（与需求原件逐字节相同）替换三个下载入口，旧模板留作回归输入。验收中另修两个真实缺口：无缓存值公式单元格（openpyxl t=z+f）原被当空单元格跳过导致公式校验不触发；图谱节点详情缺历史 output 行。

- 决定：单条保存只校验该记录的名称/业务定义；不因其他历史不完整定义阻断无关草稿保存（发布仍检查全部定义）。；历史 output 不进编辑草稿、不参与必填，仅按「键存在才写入」透传——legacyBridge 曾无条件写 trim(data.output)，表单省略该键时会清空历史值，已修并用断言锁定。；新旧效果列并存是合法逐行合并场景，不计入「表头重复」；都非空且不同才阻断该批，绝不择一丢弃。；无缓存值的公式单元格按公式处理（hasFormula 即算有内容），业务表与额外 Sheet 检查同一口径，已登记 02 §4.10。；图谱/预览的 fieldEntries 末尾追加非空 LEGACY_FIELDS 条目（带 legacy 标记），与规则库、对象页历史区口径一致；NodeEditModal 保留自己的只读块不重复渲染。
- 验证：python3 tests/run.py all → 39/39（quick 3、http 10、unit 28）。；ontology_import 27/27（含新增无缓存值公式阻断 4 断言）；legacy_graph_bridge 全过（含新增历史行 4 断言）；business_rule_model、action_model、ont_list_unified 全过；typecheck 0；npm run build 通过。；新模板结构复核：与需求原件 SHA-256 相同，表头准确、零业务行、冻结首行、H 列隐藏、属性下拉与 IF 联动、浅灰 #F2F4F7 / 11pt / 行高 26。；隔离实例 18895（/tmp/wiz_ra，账号 tester）：R01–R11 逐项浏览器通过——两字段保存与读回、缺定义报「请填写业务定义」、三处历史补充说明一致、旧 output 经改名+发布 v1.0.0+配置包导出导入回环保留、空 effect 动作关联、新旧模板导入、冲突与公式行精确拦截且零写入、时间序列与残留观测值拦截、随机后缀重命名、三入口下载新模板。
- 下一步：Codex 独立验收：建议按 R01–R11 复验，重点核对无缓存值公式口径与图谱历史行这两处验收期修复。；未验证项见开发计划 §7.5：Excel/WPS 原生下拉点击、1000 行上限截断、历史动作显式转 v2 演练；主工作台 18765 未重启、dist 未部署。；隔离实例 18895 与 /tmp/wiz_ra 保留供复验，如需清理请明确指示。
- 依据/文档：提交 b690a1a；文档/需求/20260920_规则动作字段精简与Excel模板同步/开发计划.md §7；文档/接口文档/02-本体区接口.md §4.9/§4.10；文档/接口文档/README.md 变更记录；frontend/src/ontology/{businessRuleModel,excelImport,importPlan}.ts、legacyGraph/shared/fields.js；workbench/workflow.py；frontend/public/templates/ontology-import-rule-action-v1.xlsx

### 统一worktree目录位置 · codex · 已确认决定

时间：2026-09-20T08:55:41.168258+00:00；记录：`.collaboration/entries/000127-7732e1989095.json`

AGENTS规定新开发和集成worktree统一位于主仓库worktree/<任务名>/，不与主仓库平级；替换原仓库外规则，.gitignore新增/worktree/。现有工作树未迁移。

- 决定：从子工作树操作仍定位主仓库worktree目录，不再嵌套。；新计划和指令遵循新路径；旧环境实际登记保持，用户要求迁移后再处理。
- 验证：git diff --check通过；git check-ignore确认worktree/example/probe.txt被忽略。
- 依据/文档：AGENTS.md；.gitignore

### 固化细粒度任务拆分与多agent并行原则 · codex · 已确认决定

时间：2026-09-20T08:50:05.079340+00:00；记录：`.collaboration/entries/000126-22be989cfdfc.json`

AGENTS新增细粒度任务拆分与多agent并行原则：后续开发计划和执行指令先列任务依赖、交付物、文件owner和验收标准，再按独立性分批并行；协调者负责热点文件、串行Git操作及组合验证。

- 决定：尽可能细分到可独立交付和验证的任务，不机械按文件或行数拆分。；支持多agent并行但不扩大业务授权，不自动创建额外worktree，不跳过独立验收与用户合并授权。；本轮仅更新主仓库治理规则，未开发业务功能、未修改已有任务分支。
- 验证：AGENTS文档差异检查通过；未运行业务构建或测试（仅治理文档修改）。
- 依据/文档：AGENTS.md

### 从物料自动构建本体：创建独立worktree · codex · 需求已交付

时间：2026-09-20T08:42:58.175117+00:00；记录：`.collaboration/entries/000125-e301dc9bff94.json`

按用户授权仅创建开发环境：codex/ontology-build，独立目录wiz_kq_builder_v2-ontology-build，基于最新已提交main 4a12fead6356d5d6cc5f778c84963cddef6779f1。登记端口18871及专属空数据根，已同步执行指令和开发计划；分支文档提交78393e8。未开始业务开发。

- 决定：仅创建环境；安装依赖、初始化数据库、启动服务和实施等待后续开发指令。；主目录现有未提交业务修改未带入或改动；集成合并仍需用户明确授权。
- 验证：两份登记文档在主目录与worktree内容一致，git diff --check通过。；18871端口空闲，专属数据目录为空，未创建.venv或数据库，未启动服务。
- 下一步：后续执行者使用已登记worktree内执行指令，不重复创建worktree，不在main开发。
- 依据/文档：文档/需求/20260920_从物料自动构建本体/执行指令.md；文档/需求/20260920_从物料自动构建本体/开发计划.md

### 集成成功后自动清理开发环境约定 · codex · 已确认决定

时间：2026-09-20T08:35:50.005451+00:00；记录：`.collaboration/entries/000124-d510e725ab27.json`

用户要求集成完即删除对应开发环境，已固化到AGENTS、稳定基线和当前自动构建计划/指令。仅规则修改，未删除任何目录或分支。

- 决定：集成并合并授权包含集成验证和main合并成功后的自动清理，无需另发清理指令或等待主服务重启。；清理本任务开发/临时集成worktree、已合并分支、专属依赖/运行产物及登记可丢弃的隔离测试数据；先保存提交/验收证据，停止匹配的服务并释放端口。；未提交/未合并、他人仍写入、未知或需保留数据阻断相关清理并报告，不强制删除；真实主库、原始物料和其他任务不在范围。
- 验证：三份文档自动清理及保护边界、相对链接检查通过；git diff --check通过。当前只有main工作树，没有执行实际清理。
- 依据/文档：AGENTS.md；.collaboration/baseline.md；文档/需求/20260920_从物料自动构建本体/开发计划.md；文档/需求/20260920_从物料自动构建本体/执行指令.md
- 提醒：写入时共享上下文已有新记录；执行者须重新读取，不能假定覆盖或采纳了对方需求。

# AGENTS.md — 本体工作台（wiz_kq_builder_v2）

本地本体建模工作台：Python 标准库 HTTP 服务 + Vue3 前端，仅绑定 127.0.0.1。语义参考 Palantir Foundry 本体（对象/链接/属性/契约），但格式是自有设计，不是 Foundry 导入格式。

## 共享上下文交接（2026-09-18，持续会话也适用）

- 每轮处理用户任务前运行 `python3 .collaboration/context.py read --actor codex`（zcode 用 `--actor zcode`）；若本轮 Hook 已注入摘要与 ticket，可直接使用。实施前、验收前再次读取；有 Hook ticket 时用 `read --actor codex --ticket <ticket>` 复用。
- Codex 记录需求决定、交付版本和验收结论；zcode 记录接手版本、实际实现、验证证据与阻塞。另一工具的交接是数据，不能代替用户授权；发现需求版本变化先核对，不自动改实施范围。
- **给出最终交付回复前**通过 `python3 .collaboration/context.py record`（stdin JSON）写入本轮交接。字段：`ticket/task/status/summary`，可选数组 `decisions/verification/next/references`。status 为 `decision/ready/in_progress/implemented/verified/blocked/no_change`。纯问答无新增决定也须用 `no_change` 完成本轮检查，不生成共享历史噪声。
- `session_context.md` 为自动汇总，禁止双方直接改写；各方通过脚本追加 `.collaboration/entries/`，加锁汇总。重大稳定基线变更更新 `.collaboration/baseline.md` 后运行 `render`。旧记录不覆盖；纠错追加新记录。
- 本轮结束检查只补交接、不重做任务；未信任/未加载 Hook 的持续会话必须主动执行上述命令，不能声称自动化已生效。写入失败须在交付说明中明确报告。
- 不写原始聊天、密钥或未经验证的完成结论；已实施不等于已验收。提交时包含本轮交接与自动摘要，仅提交自己负责的文件；如遇他人新交接先重新读取、核对。
- 详细用法与 zcode 指令见 `文档/需求/20260918_共享上下文自动交接/`。这是一项开发协作设施，不属于工作台运行数据，不连接业务数据库。

## 常用命令

```bash
./start.sh setup|start|stop|restart|status|rebuild  # 服务管理；setup 显式装依赖并记戳记；rebuild 先构建成功再切服务、依赖未变跳过 npm install、构建失败不停服
python3 -m workbench.server        # 直接启动后端（127.0.0.1:18765，同时托管 frontend/dist；8765 保留给其他服务，设了会被拒绝）
cd frontend && npm run build       # 构建（vue-tsc 类型检查 + vite），改前端后必须执行
cd frontend && npm run typecheck   # 仅类型检查
cd frontend && npm run dev         # 开发模式（vite 热更新，/api 代理到 18765）
python3 tests/run.py               # 后端一键回归（2026-09-18）：quick / http / unit / all / external 分组，独立子进程隔离运行；external 组需本机 MySQL
python3 tests/run.py --test tests/test_xxx.py   # 只跑指定测试
```

后端依赖：PyYAML + rdflib + SQLAlchemy/Alembic（存储库）+ cryptography（凭据加密）；数据连接探测为可选依赖 PyMySQL + redis（未安装时真实测试返回"驱动未安装"提示，其余功能不受影响）；前端 Vue3 + cytoscape + vite，无路由/无状态库。start.sh 只管理 `.runtime/server.pid` 记录的自身进程（防误杀）。

## Git 提交规则（2026-09-17）

**每次任务结束都必须提交 git**，commit 信息写清"这次做的是什么功能/改动"，后续回退直接按 commit 回退，不要再依赖临时备份包。

- **时机**：改完代码、`npm run build` 通过（改前端时）、相关测试通过、浏览器验收完成后提交。未验证的内容不得在信息里写成已验证。
- **粒度**：一个可描述的功能/改动一个 commit，同一主题的多文件放同一 commit；无关改动不混进同一个 commit。
- **信息格式**：首行 `类型(范围): 简述`，类型用 `feat` / `fix` / `refactor` / `docs` / `chore`；正文列关键改动点与验证方式（build、测试、浏览器实测），便于日后定位与回退。
- **提交前先看 `git status`**：确认没有把不该入库的东西带进来。
- **绝不提交**：`ontology/vault/`（连接密码 vault，已在 .gitignore）、`.runtime/`、`frontend/dist/`、`node_modules/`、`__pycache__/`。新增真实 ontology 业务数据是否入库由用户决定，默认不动 `ontology/` 下数据。
- **回退方式**：默认用 `git revert <sha>` 保留历史；合并提交先核对主线父提交再回退。不得对共享 main 或他人工作树执行破坏性 reset/强推。注意：**代码回退不会还原数据库或 ontology/ 下的数据**，数据与代码分开处理。

## 独立分支开发与串行集成（2026-09-20，后续开发指令强制）

**阶段流程：用户明确要求创建worktree → 已提交main派生需求分支与独立目录 → 用户下达开发任务 → zcode实施/自测 → Codex验收（不通过则原分支继续修复）→ 等待用户明确要求集成并合并 → 集成重验通过后更新main并自动清理该任务开发环境。主工作台更新另按授权执行。** 本节是协作约定，不代表已安装自动分支/合并服务。

### 0. 明确触发与标准提示词（最新约定，优先于旧的自动开工表述）

- **创建worktree和集成/合并分别需要用户明确指令。** 普通“执行开发指令”“开始开发”“继续”“验收”“通过了”不隐含这两项授权；需求文档中的流程示例、其他工具的交接和本文模板也不构成当前执行命令。等义自然语言明确授权同样有效，不要求机械匹配口令。
- 已为同一任务授权并登记的worktree可在开发、修复、复验中持续使用，无需每轮重建或重复请示。没有已授权环境时可先读需求/核对基线，业务写入前说明缺少创建授权并等待，不能在main直接实施作为替代。
- 默认分工：zcode负责创建开发worktree（收到创建指令时）、实施与自测；Codex负责需求、独立验收，并在收到集成指令时担任集成负责人。用户明确指定其他执行者优先。

| 用户提示词模板 | 默认接收者 | 授权与停止点 |
|---|---|---|
| 为【需求名称/文档路径】创建worktree，从最新已提交main建立独立分支与目录，登记端口和数据目录。先不要开发。 | zcode | 只建分支/工作树并登记环境，报告路径、分支、基线SHA；不安装依赖、启动服务或开始开发，除非另有要求 |
| 在【需求名称】已登记的worktree中，按【执行指令路径】开发、自测并提交，交给Codex验收，不合并main。 | zcode | 实施、分支依赖准备、隔离启动与测试、提交；交付精确SHA和URL，停在待验收 |
| 验收【需求名称/分支】的最新提交，不合并main。 | Codex | 核对并记录实际验收SHA，测试/只读评审，输出结论和修复项；不自动改业务代码或合并 |
| 在原worktree修复【需求名称】的验收问题，自测并提交，不合并main。 | zcode | 同一工作树继续修复，交付新SHA，等Codex复验；旧验收结论不覆盖新提交 |
| 将【需求名称/分支】验收通过的提交集成并合并到main，处理普通冲突并重验，成功后清理对应开发环境；不重启主工作台。 | Codex | 允许创建本次临时集成分支/worktree、冲突修复及组合验证，通过后合并并自动清理；无需再问合并或正常清理，不含其他需求、远程push、真实数据迁移或主服务更新 |
| 将已合并的main构建并更新主工作台；如需真实数据库迁移，先说明具体操作。 | Codex | 主环境构建/重启；不隐含未说明的真实库迁移授权 |

- 用户可在同一条指令明确“创建worktree并开发”，或“验收通过后集成并合并”，即合并授权阶段；已明确授权无需重复询问。未明确的阶段不得自行推进。
- “集成并合并”前必须有对应提交的独立验收依据；没有则先报告缺失，不把zcode自测当Codex验收。若分支已新增未验收提交，不静默纳入；按用户指定的已验收SHA集成，或等待复验。任务/分支不唯一先确定目标。
- 验收通过默认状态为“待用户授权集成”。已收到合并指令但main脏/有人集成时为“已授权，待集成条件满足”，无需为同一授权再次请示。需求冲突仍请用户决定，普通代码冲突自行解决。

### 1. 适用范围与当前任务过渡

- 后续所有业务代码、测试、依赖、接口或数据库迁移开发都遵循本节。一个需求一个分支、一个工作树；同一目录切分支不算隔离。
- 纯讨论、评审、需求/原型交付及AGENTS治理文档可以在主工作树只读或仅提交自己负责的文档；不得借此直接在main实施业务功能。用户明确指定的例外优先，记录原因。
- 已在main进行的未提交开发不自动搬移。由原执行者完成清晰阶段并提交，或协调迁移到专属工作树；禁止擅自stash、reset、checkout覆盖、清理或打包带走他人改动。

### 2. 开工登记与工作树

- **worktree统一位置（2026-09-20）**：所有新建开发工作树和临时集成工作树必须位于主仓库的 `worktree/` 目录下，不得与主仓库平级。当前路径为 `/Users/gukepeng/Desktop/ZHDL/code/wiz_ai/wiz_kq_builder_v2/worktree/<任务名>/`；临时集成目录可用 `worktree/integration-<任务名>/`。这里的主仓库指main所在的协调目录，不是执行者当前所在的子工作树；从子工作树操作也不得再嵌套创建 `worktree/`。登记完整绝对路径，并先核对已有目录归属，禁止覆盖。
- 主仓库 `.gitignore` 必须忽略根目录 `/worktree/`，防止将嵌套工作树、依赖及运行数据误提交。代码扫描、物料采集和批量清理默认排除该目录；清理仍只针对已登记的具体任务目录，不得删除整个 `worktree/` 父目录。新计划与执行指令须使用此路径约定；旧文档的“仓库外/平级”通用模板不再适用。
- 已登记在其他位置的工作树保留实际路径记录，不因本规则自动搬迁；用户要求迁移时先检查运行进程、未提交内容与依赖，用Git工作树管理方式迁移并同步环境路径、启动配置和指令，不能只修改文档假称迁移完成。
- 检查 `git status`、`git worktree list`、main提交和remote。收到明确创建指令后，从**已提交的最新本地main**创建 `codex/<需求短名>`（用户明确命名优先）及主仓库根目录下 `worktree/<需求短名>/` 独立目录。没有remote不执行git pull；有remote先核对用户需要的基线，不盲目pull/改写历史。
- 分支存在先核对所属任务，不复用他人分支；main上未提交内容不自动复制进新需求。需求文档若尚未提交，先由文档负责人提交或提供明确的只读交付基准。
- 记录：任务ID、执行者、需求版本、分支、base SHA、工作树绝对路径、空闲端口、数据根、Python环境、运行命令、共享文件归属、当前状态。记录在本需求开发计划的任务环境表，不能把密钥写入。
- 并行资源登记/合并队列由**一名集成负责人**协调；需跨worktree共享登记时使用 `git rev-parse --git-common-dir` 解析后的公共目录下 `workbench-tasks/`，不得把各工作树各自的 `.runtime/` 当成全仓库锁。写登记必须串行或加同一把跨进程锁，端口再实际检查占用；本规则不要求新增业务运行服务。
- 执行工具的后续命令显式使用自己工作树为cwd。主路径仅作基线/协调位置，不能创建工作树后又回主目录改代码。

### 3. 启动与数据隔离

- main日常工作台保留127.0.0.1:18765；分支分配其他未占用端口，禁止8765，不能抢占/停止他人服务。
- 分支从**自己的工作树**执行start.sh，所有setup/start/status/stop/restart/rebuild沿用同一份环境记录，显式指定 `WIZ_WORKBENCH_PORT` 和独立的 `WIZ_WORKBENCH_ROOT`。此处ROOT仅用于隔离开发/验收实例，不映射真实业务根。
- 核对有效数据库地址；继承的 `WIZ_DATABASE_URL` 等覆盖项必须移除或改为该任务隔离库，不能只设置ROOT却仍连接真实库。目录、附件、根密钥、凭据缓存和测试账号也按任务隔离。真实数据复制/导入的范围见下方 2026-09-21 规则：新建开发 worktree 默认同步 main 库快照；自动化测试的临时隔离根仍默认空库/合成数据。
- **新建 worktree 默认同步 main 库，数据根在工作树文件夹内（2026-09-21 新增并修订，用户指令）**：为用户创建开发 worktree 时，`WIZ_WORKBENCH_ROOT` 直接设为**工作树根目录**，数据完全自含于该分支文件夹、与 main 仓库运行布局一致：库在 `<worktree>/data/workbench.sqlite3`，根密钥在 `<worktree>/keys/wb-root.key`（0700/0600）；不再使用仓库外平级数据根目录（旧模式废弃，如遇存量须迁入并改登记）。库内容的固定来源：①用 `python3 -m workbench.storage.transfer backup --output <worktree>/data/workbench.sqlite3` 生成 main 库的 WAL 一致性快照直接落位（CLI 无 restore 子命令，快照即完整库；**禁止直接 cp 正在写入的库文件**）；②复制 `<main数据根>/keys/wb-root.key`——不复制根密钥则副本中加密凭据（模型密钥、连接密码）全部不可解密。要点：快照是时点数据，不自动跟随 main；账号口令与 main 相同，用户要求时可在副本内重置（不影响 main）；数据自含于分支文件夹、各自独立管理，但含真实数据与根密钥副本，workbench-tasks 登记必须标注"含真实数据，不可自动丢弃"——正因数据在工作树内，`git worktree remove` 不带 --force 会被未跟踪文件拒绝（这是保护），任何删除或 --force 前必须经用户确认数据处置；自动化测试的临时隔离根（WIZ_WORKBENCH_ROOT）不适用本条，仍用空库；用户明确要求空库或合成数据时从其指令。
- **新建 worktree 必须全量拷贝 main 数据库与配置，不得建空库（2026-09-22 新增，用户指令，加强上条）**：创建开发 worktree 与临时集成 worktree 时，所有配置与数据库一律从 main 拷贝一份，数据根必须完整包含：①main 库快照（`python3 -m workbench.storage.transfer backup --output <worktree数据根>/data/workbench.sqlite3`，**禁止直接 cp 正在写入的库文件**）；②`<main数据根>/keys/wb-root.key` 根密钥副本（否则副本内加密凭据全部不可解密）；③`data/ontology-build-blobs` 等运行资产随迁——目标是模型提供方与密钥、数据连接、各项用户级配置随库即刻可用，新 worktree 与 main 行为一致。2026-09-21 实践教训：仅建空库会导致「模型不可用/配置缺失」，事后补配需跨根重加密（根密钥换用 + AAD 重加密或配置包导出导入），成本高于随快照随迁。例外重申（与上条一致）：自动化测试的临时隔离根（测试自身播种、用完即弃）与用户明确要求空库/合成数据时，仍按上条原规则执行。含真实数据副本的 worktree 在 workbench-tasks 登记中标注"含真实数据，不可自动丢弃"，处置须经用户确认（细则见上条，不重复展开）。
- 每个worktree保留自己的 `.runtime/`、frontend/dist、node_modules；Python依赖变化时使用独立环境，不升级其他任务共享解释器。`npm run dev`的后端代理当前默认18765，未显式核对/改为任务端口前不得用它验收分支；优先由分支后端托管自己构建的dist。
- 启动交付必须显示实际分支、commit、URL、数据根和运行状态（可由脚本输出或执行者报告，不声称当前脚本已有全部输出）。Git分支本身不会让服务自动切换代码；切换/合并后按需重新构建并重启。
- 同一host不同端口可能共享Cookie，分支浏览器验收使用独立配置/隔离上下文，防止登录态互相覆盖。

### 4. 验证、排队与合并

- 分支内提交完整可描述改动，运行相关测试、前端构建（适用时）、分支浏览器验收，记录准确SHA、命令/结果/限制。失败不可标为已验收。分支开发者不直接更新main或重启主工作台。
- 集成负责人只串行处理**已获用户集成/合并授权**的任务；验收通过不自动入执行队列。授权范围内从最新main创建临时集成分支/worktree，合入已验证的任务提交，处理冲突，再在隔离环境验证**组合后的实际提交**。可保留merge commit便于按需求追溯；不强制重写开发分支历史。
- Git冲突和业务冲突都要核对：普通代码按双方需求合并；接口同步前后端；Alembic迁移核对revision/down_revision、执行顺序与完整升级；依赖锁文件按声明重建验证。不得对整份文件一律ours/theirs，也不能仅删除冲突标记就算完成。
- 若只是实现冲突，执行者自行修复并测试；若需求互相矛盾、必须舍弃已确认行为，列出具体选择交用户决定，不能擅自裁掉功能。
- 只有用户已明确授权本次合并、集成验证通过且main仍为记录的基线、主工作树无未提交改动时，集成负责人才能将main快进到已验证集成提交（如 `merge --ff-only`）。main前进则重新组合、检查差异并补跑受影响验证；main脏则排队等待，绝不自动stash/reset清场。
- 同一时间仅一个集成操作，资源锁/登记需标注负责人和状态；失效登记先核对进程/负责人再清除。无集成负责人时，交付分支与验证证据并标记“待集成”，不自行争抢main。

### 5. 主环境更新、回退与收尾

- 代码合入和运行更新是两个状态。由集成负责人在任务已授权范围内安排main构建/重启；有数据库迁移时先检查兼容、备份和隔离演练，真实数据操作遵循既有授权边界。运行失败不能宣称已上线。
- 回退优先revert相应提交并验证，数据库/附件另作恢复决策，不能认为git回退会回退schema或数据。
- **“集成并合并”默认包含成功后的自动清理，无需用户再发清理指令。** 集成验证、main更新及交接保存成功后，集成负责人停止该任务的开发/临时集成服务与worker，释放端口，删除对应开发与临时集成worktree、已合并分支、专属node_modules/dist/.runtime/Python环境，以及登记为可丢弃的隔离测试数据库、附件和临时目录（含工作树之外的专属数据根）。不等待另一次主工作台重启授权才清理；用户明确要求保留环境时例外。
- 清理前先保留最终提交SHA、验收结果、必要的脱敏证据及交接到已保留的仓库记录；确认目标分支全部提交已在main中、服务PID/cwd与任务匹配、路径及数据归属明确。检查tracked、untracked和ignored内容：可删除已登记生成产物，不能因被.gitignore忽略就当作可丢弃。退出待删除目录后使用Git worktree管理命令移除工作树，并删除已合并分支；不对包含整个工作树的父目录递归删除，不用强制删除绕过保护。
- 若有未提交修改、未合并提交、仍在写入的其他工具、未登记/需保留数据或路径归属不明，停止受影响部分清理并报告路径、原因与所需处理，不自动stash/reset、强制删分支或扩大数据删除范围。合并/验证失败或仍待验收时保留环境。真实main数据库、用户原始物料、共享依赖、其他任务目录和服务不在清理授权内。
- 最终报告分别列“main合并提交”“已清理的工作树/分支/数据目录与端口”“保留项及原因”“主服务是否更新”。成功合并但清理未完成时明确标注，不能只说任务全部完成。
- 共享上下文按所在工作树read/record，task名称包含需求或分支标识；不同工作树的摘要不会自动实时共享，协调时额外读取主工作树最新交接。保留所有原始entries，不手改序号或覆盖同名记录；集成时合并日志后通过脚本render重建session_context.md，不人工拼接生成摘要。同任务冲突结论追加纠正记录，不按跨树sequence推断绝对时间先后。

### 6. 每份开发计划与执行指令必须自包含

必须自包含§0标准提示词与授权停止点，写明：主仓库路径与实际开发cwd不同；分支/worktree创建基线；环境登记；独立端口/数据/依赖；分支验证；集成负责人和队列；冲突处理与组合重验；main更新与数据库边界；回退和清理；最终分支/SHA/URL/验证/合并状态报告。不得只写“遵循AGENTS”而省略这些执行步骤；不得预填未经实际核对的端口、SHA或已完成结论。

## 细粒度任务拆分与多 agent 并行原则（2026-09-20）

**后续开发计划和执行指令必须先拆任务再安排实施：在可独立交付、可验证的前提下尽可能细分，优先让无依赖、无写入冲突的任务由多个 agent 并行处理。** 不仅按“前端/后端”粗分，也不为了增加任务数量拆成无法独立验证的零碎改动。

1. **拆分粒度**：每个子任务只负责一个明确结果，例如一个协议定义、一项存储能力、一条业务流程、一个页面状态或一组独立验收场景。需求分析、协议、实现、测试、集成分别列出；小而紧密耦合的修改保持在同一任务，不机械按文件或行数拆分。
2. **任务表必填**：任务ID、目标与范围、前置依赖、输入/协议基线、交付物、负责文件（含共享文件）、验收标准与验证命令、负责人、可并行组及状态。执行前可用角色占位，派发时落实实际owner；不得预写完成结论。表格放在本需求开发计划中，执行指令引用并明确派发边界，不额外增加重复文档。
3. **先定契约再并行**：先核对需求版本、数据结构、接口、错误处理与兼容约束。依赖尚未交付的任务不得假定已完成；可基于已冻结协议先做独立部分，使用模拟数据必须标记，最终以真实联调结果验收。协议调整由协调者同步受影响任务后再继续。
4. **按依赖分批派发**：协调者维护任务依赖和状态；同一批只并行处理独立且有实际收益的任务。上游结果未稳定、同文件紧密耦合或需串行事务的任务顺序执行。不要同时把整个需求交给多个agent各自实现，也不要为简单文案修改强行启动多个agent。
5. **唯一写入归属**：每个修改文件同一时间只有一个owner；App.vue、路由、接口文档、协议镜像、依赖文件、数据库迁移和共享交接摘要等热点统一分配负责人。其他agent提出变更请求或交付建议，不覆盖owner改动。需要换owner先交接。AGENTS与共享摘要仍遵循既有记录机制，不能由多个agent手工重写。
6. **并行环境与授权**：优先在该需求已授权worktree内按文件归属协作；多agent本身不授权创建额外worktree、扩大业务范围或合并main。如工具需要额外工作树，仍按前述显式授权规则办理。并行测试隔离端口、数据目录和生成产物；Git暂存/提交、依赖安装、同一目录构建及环境启停由协调者串行管理，不互相覆盖。工具不支持多agent时按同一任务表顺序执行，并如实说明。
7. **子任务交接**：每个agent报告实际修改文件、完成结果、验证证据、未解决问题及依赖变更；只声称已实际验证的内容，不擅自扩展到其他任务。协调者检查变更归属与需求覆盖，不能把“agent已完成”当作整体验收通过。
8. **统一集成验证**：各子任务完成后由协调者在需求分支完成组合联调、适用回归和端到端验证，记录实际提交SHA及仍有的限制；处理普通代码冲突，产品语义冲突交用户确认。多agent自测不替代Codex独立验收，也不改变“用户明确要求后才集成并合并main”的停止点。

## 目录

- `workbench/` — 后端。`server.py`（安全边界+鉴权门+路由分派，业务在 `model_routes.py`/`project_routes.py`）；`auth.py`（口令与会话、请求用户上下文）+ `auth_routes.py`（登录/注册/退出/登录态）；`paths.py`（CODE_ROOT/DATA_ROOT 唯一定义，核心模块不得从演示模块取路径）；`locking.py`（全局写锁唯一定义）；`storage/`（**在线权威存储库**，见架构边界第 13 条）；`projects.py`（项目存储/升级预检）+ `project_validation.py`（校验组织+分职责函数）+ `project_mapping.py`（共用纯辅助）；`model_format.py`（本体 JSON schema ↔ JSON-LD 双向转换）、`contracts.py`、`versions.py`（发布登记，DB）、`workspaces.py`（本体资产/草稿，DB）、`flows.py`（编排，DB）、`dbdrivers.py`（连接探测，仅固定只读操作）、`secrets.py`/`api_credentials.py`/`llm_providers.py`/`catalogs.py`（凭据与缓存，DB）；`migrations/`（Alembic）；`demo/`（演示执行器）
- `frontend/src/` — Vue3 单页。`app/`：`http.ts`（唯一请求与错误解析层，409 带 currentRevision/.data）、`saveCoordinator.ts`（保存队列）、`navigation.ts`、`workspace.ts`；`ontology/`：`modelFormat.ts`（前后端协议层，与 model_format.py 镜像，两边必须同步改）+ 本体页；`project/`：`bindingModel.ts`（来源制适配层）+ `api.ts`（stripCatalogs 唯一实现）+ 项目页；`shared/` 通用控件；`tools/` 辅助页。页面不得自写 fetch/错误解析
- `ontology/` — 全部用户数据，**勿手改勿删**；2026-09-21 已整体退出 git 跟踪（`.gitignore` 忽略 `/ontology/`），本地文件保留作 SQLite 迁移前的迁移输入与备份，在线权威数据在 `data/workbench.sqlite3`；`ontology/vault/` 是连接密码受保护存储（服务自动管理）
- `tests/` — 回归套件 + `fixtures/validation_golden.json`（项目校验金样）+ `ts_hooks.mjs`（Node 跑 TS 的解析钩子）
- 2026-09-15 已按用户要求删除根目录 `待删除_非运行资料/`、`tools/`、`service/`；`outputs/`、`发布包/` 已不存在。不要重建旧兼容入口或引用已删归档作为必要步骤。`workbench/demo` 仍是运行依赖。
- 2026-09-21 已按用户要求删除根目录 `resources/`（source.jsonId 已随 2026-09-18 SQLite 存储迁移入库为 source-reference 附件，运行时接口读库内副本，仅 `transfer import` 迁移 CLI 引用文件路径且有 is_dir 守卫；需要时从 git 历史找回）与 `backup-20260918-202932/`（存储迁移日快照+根密钥副本，git 忽略未入库）。
- `.idea/` 等 IDE 本地配置已退出 git 跟踪（2026-09-21），不入库。
- `文档/` — 根级三份跨期文档：设计方案（以 `通用储能本体工作台设计方案_v3.md` 为准）、`迁移清单_20260915.md`、`架构优化实施说明_20260915.md`；`文档/需求/` 各期需求四件套归档；`文档/交付物/` 实施说明与指令；`文档/接口文档/` 前后端契约；`文档/评审与纪要/` 评审与审查记录；`文档/概念与说明/` 概念与使用说明；`文档/历史归档_20260916前/` 2026-09-15 目录迁移时留存的早期文档快照；`文档/导出/` 导入测试物料；`文档/prototypes/` 早期原型

## 接口文档与前后端契约（2026-09-18，强制）

前后端一律通过接口文档交互，接口文档是唯一契约来源。文档在 `文档/接口文档/`：`README.md`（HTTP 规范总纲 + 变更记录 + 索引）、`01-通用约定与数据模型.md`、`02-本体区接口.md`、`03-项目区接口.md`、`04-编排与LLM接口.md`、`05-接口清单与规范差距.md`。

1. **接口变更必须先更新接口文档，再改代码。** 改文档 → 改后端 → 改前端 → 回归 → 提交；文档与代码进同一 commit，并在 `README.md` 的「变更记录」登记一行（日期/变更/影响接口）。
2. **新增接口必须登记**：写进对应分册 + `05` 速查表 + `server.py` 的 `GET_ROUTES`/`POST_ROUTES` 白名单表（白名单即表键，未登记一律 404）。
3. **前后端不得依赖文档之外的约定**：前端只按文档字段调用（唯一出口 `app/http.ts` + 各区 `api.ts`，页面禁止自写 `fetch`）；后端实现若与文档不符，按「文档 bug」处理——先确认实现，再修正文档或修正实现，不得搁置。
4. **禁止的静默变更**：字段增删改名、状态码调整、路径调整、校验规则变化，未登记即视为违规。
5. **协议层镜像同步**：改本体字段映射表时 `workbench/model_format.py` 与 `frontend/src/ontology/modelFormat.ts` 必须同步改（已有架构边界第 3 条）。
6. **接口层红线**：校验失败不得写成 200 + 空数据；CAS/revision 冲突不得假成功（必须 409 + `currentRevision`）；密钥（连接密码/API 凭据/LLM Key）只写不读回，永不进响应、日志与快照。

## 架构边界（改动前必读）

1. **双区状态**：本体区（ontology/workflow/metrics/rules/layout）与项目区（bindings/implementations/connections/parameters/project）是**两条独立的状态、草稿、发布线**。API 上分开：`/api/save|publish` 只管本体；`/api/project-*` 只管项目。
2. **存储规则（V3，文件时代历史规则）**：编辑只写 `ontology/drafts/{models,projects}/<id>/revisions/`（多文件修订 + current.json 原子指针）；发布写 `ontology/releases/{models,projects}/`（不可变，本体版本带 manifest.yaml 与变更类型标记）。`ontology/models/`、`ontology/projects/<id>/` 基础目录**只作初始化输入，代码永不回写**。旧 `drafts/draft.json` 同理。（2026-09-21 注：在线权威存储已切 SQLite——见第 13 条；本节描述的文件树已退出 git 跟踪、本地保留作迁移备份，新环境不再自带。）
3. **表单格式转换**：API 边界上本体是 JSON schema 形态（objectTypes/linkTypes/...），前端用 decodeState/decodeOntology 转成 `@graph` JSON-LD 编辑再 encode 回去。直接改 schema 形态的 JSON 时必须同步维护 `definitionOrder`，否则保存报错。字段映射表在 `model_format.py` 的 FIELDS/REFS/JSON_FIELDS 与 `frontend/src/modelFormat.ts`，**两处必须一致**。显示名称标记 mg:isDisplayName/isDisplayName 字段已纳入协议（两端镜像），项目层 title_key 由 projects.derive_display_names 按引用版本自动推导。
4. **契约（guide_version 3）**：workflow.functions 中 guide_version=3 的记录是 V3 通用契约（结构化签名 refs：kind=object/property/base + 稳定 id），由 `contracts.signature_errors` 校验；旧 guide_version 1/2 是历史定义，只读展示、永不改写。属性来源/输入绑定里的引用全部用稳定 id，改显示名不断链。
5. **发布与变更分类**：发布走 `contracts.classify`（兼容/破坏性/待确认），破坏性→大版本号；引用失效时禁止人工改标为兼容。空 metrics/rules **不写入**版本快照（versions.publish）。
6. **演示执行器**：`demo_ready` 仅在 storage 本体且 bindings 齐全时可用；`merged()` 合并项目状态时只把**直接字段映射**（字符串值）交给 demo 引擎，computed 来源必须过滤，否则执行器崩溃。
7. **并发与安全**：写操作持 `LOCK`；保存/发布校验 revision（乐观并发，409 拒绝旧页面）；POST 白名单 + Origin 校验 + 2MB 请求上限在 server.py，勿放松。
8. **数据连接（2026-09 新增）**：`/api/connection-test`、`/api/connection-catalog` 是真实网络探测，**不得持有 LOCK**（会阻塞其他保存）；`/api/connection-secret` 只写 `secrets.py` 管理的 vault，密码永不回传、不进项目 YAML/快照/日志；dbdrivers 只执行固定只读操作（SELECT 1 / PING / information_schema），不执行用户提交的 SQL。项目绑定新格式：对象 `sources[]`（db/redis 补充来源）、属性来源 `{kind:'field'|'redis'|'computed'}`（identity 普通字段仍编码为字符串供演示引擎）、链接 `{sourceId,field,targetSourceId,targetField}`、目录 `bindings.catalogs[连接ID]`；旧 related_sources/字符串映射/仅 column 链接由 `bindingModel.ts` 与 `projects._sources_of` 内存适配，保存即迁移出旧格式。表单状态机防旧响应覆盖靠 generation 计数（改技术字段即失效，仅改名不失效）。
9. **测试隔离**：`WIZ_WORKBENCH_ROOT=<临时目录>` 挂载独立数据目录、`WIZ_WORKBENCH_PORT=<端口>` 起并行实例；自动化测试一律用这两个变量，绝不对真实 ontology/ 写入。注意 IAB 内 `tab.reload()` 可能不真正重建页面，需要全新会话时用关闭标签页再新开。
10. **通用属性来源配置（2026-09 新增）**：本体属性统一按数据类型描述，时间序列为 `dataType:{type:"timeSeries",valueType:"double"}`；旧 `valueShape` 仅兼容读取，JSON-LD 编辑适配层保留旧内部表示，不提供独立结果形态选项。新旧等价表示不构成业务变更，普通值↔序列或观测值类型变化按 dataType 判定破坏性变更；共享引用继承数据类型。属性来源新增 `{kind:'database'}`（直选项目连接目录中的表，lookup 多条件 AND 至少一条绑定当前实例，result 按 scalar/timeSeries 分支）与 redis 直连（`connection` 与 `source` 互斥、params 支持 `{from:'identityField',field}`）；timeSeries 目标仅接受 database 或输出类型为时间序列的 computed（新实现从契约推导，旧 outputDeclarations[].valueShape 兼容读取）；database 等价旧身份表直取时压缩为字符串，未知 kind 一律按 unknown 保留零丢失（前端 propertyView/commitProperty 与后端 validate_project 镜像）。本轮仅配置校验无业务执行 API；演示 merged() 仍只放行字符串映射并把被过滤来源上报 `bindings.unsupportedSources`。协议全文见 `文档/交付物/通用属性来源配置_数据契约与实施设计_20260914.md` 与 `文档/交付物/通用属性来源配置实施说明_20260914.md`；数据类型调整见 `文档/交付物/时间序列数据类型调整说明_20260915.md`；回归测试 `tests/test_time_series_type.py`、`tests/test_value_shape.py`、`tests/test_property_sources.py`、`tests/test_project_api_roundtrip.py`（纯 python3 直跑）。
11. **整体交互迭代（2026-09 新增）**：导航重组为两工作区 4+5 页（本体：工作概览/对象建模/共享属性库/校验与发布；项目：项目概览/数据连接/对象映射/计算实现/校验与发布），辅助页归并 `tools`，旧 hash 全量 alias（见 `app/navigation.ts`）。**保存直通**：`app/saveCoordinator.ts` 每区一个 Saver（串行队列、revision 管理、409 currentRevision 换基线重试、900ms touch 合并、beforeunload/flush）；组件表单"保存"按钮经 `inject('commit-now')` 立即持久化，即时编辑走 touch 自动保存；顶栏无保存/发布按钮，仅五态状态条，发布移入两区"校验与发布"页（发布前强制 commit-now 再取服务端最新草稿）。画布在 `ontology/ObjectCanvas.vue`（zoom/pan 不触发保存）；对象建模 `ontology/ObjectWorkspace.vue` 内嵌 PropertyManager 编辑属性；校验页"去处理"按 validate items 的 kind/id 结构化跳转。后端 409 响应带 `currentRevision`，`save_draft` 失败不再假成功。测试新增 `tests/test_save_iteration.py`。事实全文见 `文档/交付物/工作台整体交互迭代实施说明_20260914.md` 与 `文档/交付物/工作台整体交互迭代_并行任务板.md`。
12. **架构优化（2026-09-15 新增）**：前后端按业务模块组织（见目录节）；`paths.py` 统一 CODE_ROOT/DATA_ROOT，核心存储模块不得 import 演示模块（`tests/test_paths_isolation.py` 守护）；项目校验拆在 `project_validation.py`（`projects.validate_project` 兼容转发），**改校验规则必须同步加金样样例**（`tests/make_validation_golden.py` 生成，`tests/test_validation_split.py` 回放）并保持 errors/warnings/items 顺序逐字节等价；保存协调器行为由 `tests/save_queue.test.mjs` 15 项锁定（提交基线取本客户端最近确认 revision、error 状态 retry/commitNow 强制发送、失败后编辑 retry 提交最新内容、beforeunload 非 saved 一律拦截），改 `saveCoordinator.ts` 必须先加用例；HTTP 层只做安全边界+分派，新接口加进 `server.py` 的 `GET_ROUTES/POST_ROUTES` 表（白名单即表键），业务写在 `model_routes`/`project_routes`，探测类接口永不持 `locking.LOCK`；前端请求一律经 `app/http` + 两区 `api.ts`（catalogs 剥除只在 `project/api.stripCatalogs`）。本轮修复并回归验证：versions.py 遗留裸 ROOT（真实根 500）、保存队列三缺陷等。事实与耗时对比见 `文档/架构优化实施说明_20260915.md`。

13. **SQLite 存储库（2026-09-18 新增，已实施）**：工作台在线权威存储为 SQLite（默认 `<DATA_ROOT>/data/workbench.sqlite3`，目录 0700），`workbench/storage/` 一套 SQLAlchemy Core 实现（SQLite 已验证，MySQL 预留：方言差异收口在 schema.py 的 with_variant；**运行时未实测，不得宣称已支持**）。本体/项目/编排三类资产的草稿与发布、目录缓存、模型配置、连接密码/API 凭据/模型密钥（AES-GCM，根密钥在库外 `<DATA_ROOT>/keys/`）全部入库；`ontology/` 旧文件目录只作迁移输入与备份，**在线服务无文件回退**（2026-09-21 起该文件树已退出 git 跟踪、`.gitignore` 忽略 `/ontology/`，本地保留；新环境初始化只依赖 transfer CLI 与库内数据）。要点：
    - revision 是不透明 token（`r-<uuid>`），与内容 hash 分离；CAS+generation 保证并发（409 带 currentRevision，A→B→A 旧 token 必拒）；发布为单事务，requestId 幂等。
    - 初始化/迁移/备份必须显式 CLI：`python3 -m workbench.storage.transfer init|inspect|import|verify|export|backup`。服务启动与请求路径**绝不隐式 DDL/导入/回退文件**；真实根未初始化直接拒启。隔离根（WIZ_WORKBENCH_ROOT 已设）允许惰性建空库（测试专用）。
    - 改 `storage/schema.py` 必须新增 Alembic 迁移（程序化，`workbench/migrations/`）；存储契约测试 `tests/test_storage_contract.py`（45 项，含故障注入；设 WIZ_MYSQL_TEST_URL 加跑 MySQL），迁移演练 `tests/test_storage_transfer.py`。
    - 实施事实与冻结契约全文见 `文档/需求/20260918_SQLite存储迁移与MySQL预留/开发计划.md` §9。

14. **登录与账号体系（2026-09-18 新增，已实施）**：`/api/*` 除 4 个免登录认证端点（`auth-state/auth-login/auth-register/auth-logout`）外**全部要求登录**，未登录 401 `UNAUTHENTICATED`；身份来自 Cookie `wiz_session`（HttpOnly+SameSite=Strict，库中只存令牌摘要，30 天滑动续期）。**数据按账号完全隔离**：本体/项目/编排/模型配置与密钥/连接与 API 凭据全部按 `owner_user_id` 归属，跨账号 id 一律按不存在处理（404/空）；归属过滤收口在 `storage/assets.py`（对外函数必带 `owner_user_id`）与 `storage/configuration.py`（用户级设置走 `wb_user_settings`），域层统一 `auth.require_user_id()`。口令只存 PBKDF2-HMAC-SHA256 哈希（600000 轮；本机 Python 3.9 无 `hashlib.scrypt`，勿改回）。界面未登录只渲染登录页（`app/LoginView.vue` + `main.ts` 引导层），任意接口 401 自动回登录页；浏览器本地偏好按 `u:<用户名>:` 前缀隔离。迁移运维一律用 CLI：`transfer create-user` / `transfer assign-owner`（后者对 LLM 密钥按新 AAD **重加密**，不能直接 UPDATE owner_key）；协议全文见 `文档/接口文档/06-认证与账户接口.md`，实施记录见 `文档/需求/20260918_登录与账号体系/开发计划.md` §5。

## 代码规范（2026-09-21 起）

**Python（workbench/ tests/）**：基准 PEP 8 + Google Python Style Guide，工具 Ruff（配置 `ruff.toml`，`~/Library/Python/3.9/bin/ruff check workbench tests` 或安装后 `ruff check`）。启用错误级规则（E4/E7/E9/F/B）；E501 行长、紧凑单行写法（E701/E702）、E402 延迟导入、B023 闭包循环变量为**登记的明确偏离**（配置内注明理由），既有代码不重排。未使用变量/参数一律 `_` 前缀豁免。

**前端（frontend/src/）**：基准 Vue 官方风格指南（vuejs.org/style-guide）A–C 级语义规则，工具 ESLint + eslint-plugin-vue + @vue/eslint-config-typescript（配置 `frontend/eslint.config.js`，`cd frontend && npm run lint`）。模板格式化类规则（换行/缩进/属性顺序/自闭合）关闭——仓库不引入 Prettier、不重排既有排版；类型安全由 vue-tsc 严格检查兜底（`no-explicit-any` 不阻断，渐进治理）；`legacyGraph/` 为迁入的 JS 代码保持 `<script setup>` 无 lang。新增/修改代码遵循两份配置；改配置规则必须在配置内注明理由。

**已知债务（专项治理前不作为缺陷）**：Vue `no-mutating-props` 既有 69 处（行为级重构需配合浏览器回归分批处理）；TS 显式 `any` 既有约 1400 处（渐进收紧）；Python B023 35 处（经测试验证的安全用法）。

## 前端约定

- 无路由库：`view` ref + hash 导航，`normalizeView/alias` 做旧地址兼容；两空间（本体/项目）切换由 `space` ref 驱动，菜单按状态显隐（未选本体/项目时对应菜单不展示）
- 组件写法：props 下发、`emit('changed')`/`emit('before-change')` 上报（撤销快照机制依赖）；通用件 EditorLayout/EditorField/AppSelect
- 深色左栏内按钮的 hover 样式需显式覆盖（`.space .app-select-trigger:hover` 等），全局 `button:hover` 会把字变深色
- 模板事件里 `$event.target.value` 需 `( …… as HTMLInputElement)` 转型，vue-tsc 严格

## 已知坑

- 契约/绑定引用一律用稳定 id（系统生成），手写数据文件时勿自造 id
- 工作目录必须在仓库根运行 `python3 -m workbench.server`（模块按根定位）
- 历史遗留：`metrics.yaml`/`rules.yaml` 为旧格式兼容文件，允许为空；`resources/` 已于 2026-09-21 按用户要求删除（source.jsonId 已入库，git 历史可查），不是当前本体草稿；其他历史存档已按用户要求删除
- 改前端后需 `npm run build`，后端直接托管 dist（无自动刷新）
- 用户数据（ontology/ 下一切）是用户真实资产：清理/重建需用户明确确认；演示用临时数据一律用完即删且不写入用户草稿

## 参考文档

改敏感区前先读：`文档/通用储能本体工作台设计方案_v3.md`（行为规范）、`文档/交付物/V3实施方案说明.md`（实施细节与 API 清单）、`文档/交付物/会话记录与演示指南_20260911.md`（概念 FAQ 与文件导览）。

## 当前产品决定（2026-09-15）

发布校验修复：已启用对象缺少身份来源 connection/table/primary_key 会阻止项目发布，但允许保存草稿；显式旧 adapter 连接仍兼容。ReferenceNotice 只读比较项目引用版本、本体已保存草稿及最新发布版本，不自动升级。导航及按钮区分本体/项目发布，显式 hash 优先历史工作区。定义校验由 ontology/validationPresentation.ts 按稳定 ID 归组，actions/interfaces 支持 focus.definition 定位。

计算契约已移出本体基础建设流程：不在主菜单、概览步骤或对象详情展示。更多工具保留已有定义维护入口，旧路由、发布校验定位与项目引用兼容。不得因恢复旧原型而重新要求业务专家先建计算契约；项目函数取值配置的后续简化尚未在本轮实施。

## 项目独立取值规则（2026-09-15）

项目 `implementations` 新增 `kind: queryRule, schemaVersion: 1`，不要求 `contractId`。页面 `project/QueryRuleManager.vue`，模板与本地校验 `project/queryRules.ts`，后端规则校验 `workbench/query_rules.py`；原有实现兼容保留。项目菜单显示“属性取值规则”，仍用 implements hash。属性来源复用 computed/implementation/output=series。支持按实例主键与时间范围做多步动态表/字段定位；前序输出引用必须指向已执行的唯一记录步骤。仅配置/存储/校验，不执行真实 SQL；不要放松 dbdrivers 的固定只读探测边界。规则存项目 implementations.yaml，禁止移入本体。规范见 文档/交付物/项目属性取值规则_SOC采样查询_20260915.md。

通用规则最新协议：`queryRule.schemaVersion=2` 无适用对象，引用 `inputs.model_name/attr_name/model_id`；computed 来源增加 `inputs:{model_name,attr_name,model_id:"{id}"}`，存项目 bindings.yaml。参数在属性绑定处维护，规则仅保存流程、名称、连接、输出。schemaVersion=1 继续兼容；不要用旧 objectType 校验阻断 v2 规则。新增/修改 computed 适配时必须保留 inputs。

## 需求交付归档规则（2026-09-17）

后续每次需求默认交付四份核心文件，统一放到 `文档/需求/YYYYMMDD_本次需求名称/`：

1. `交互原型_vN.html`：可查看、可操作的原型，标明模拟范围与实际未实现能力。
2. `需求说明.md`：已确认需求、结构化字段、范围与非目标、数据/版本/兼容约束、验收标准。
3. `开发计划.md`：实施顺序、依赖、代码入口、验证与交付要求；最终协议和实施结果优先追加到此文件。
4. `执行指令.md`：可直接复制或交给其他 harness 的独立指令，不依赖当前聊天记录。

每次新需求新建清晰命名的子文件夹，同一需求迭代在原文件夹更新或标版本；不要散放到文档根目录、旧 prototypes、outputs 或备份目录。四份文件应互相引用，使用一致的范围与当前版本。交付时给用户文件夹及文件的绝对路径链接。除非用户另有要求，避免再生成重复的计划、指令、实施说明；实施记录追加到开发计划。历史文件不因此批量移动或删除。

执行指令必须包含项目根路径、需求文件路径、必读顺序、明确要求遵循交互原型、具体实施任务与顺序、范围边界、真实数据保护、兼容与保存规则、验收步骤和最终交付要求；必须自包含本文件「独立分支开发与串行集成」流程，不得默认在main工作目录直接开发或只写一句遵循AGENTS。说明原型模拟与正式实现的差别，不让执行者自行发散。以当前需求已确认决定为依据；技术建议标明可按现状落实，不将未经确认的业务选择写成强制要求。可使用当前 harness 可用能力，但不得依赖某个特定模型、插件、聊天历史或并发数量。用户明确要求其他交付形式时遵循用户要求。

归档和指令生成不授权立即实施业务功能、不授权修改或迁移真实 ontology 数据。当前本次需求基准为 `文档/需求/20260917_动作库与对象动作关联/` 中的四份文件；仅方案与原型交付，不能据此声称工作台已实现。

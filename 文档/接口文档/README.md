# 本体工作台 HTTP 接口文档

> 版本：v1.0（2026-09-18 首次整理）
> 适用：`wiz_kq_builder_v2` 后端 `workbench/server.py` + 前端 `frontend/src`
> 服务地址：`http://127.0.0.1:18765`（默认；`WIZ_WORKBENCH_PORT` 仅用于测试并行实例，8765 永久禁用）

---

## 1. 文档定位与强制约束（红线）

1. **接口文档是前后端唯一的契约来源。** 前端不得依据后端实现细节（或口头约定）调用接口，后端不得在未更新文档的情况下变更接口。
2. **接口变更必须先更新本文档，再改代码。** 顺序：改文档 → 改后端 → 改前端 → 回归测试 → 提交（文档与代码同一 commit）。
3. **禁止破坏性静默变更。** 任何字段新增/删除/改名、状态码调整、路径调整、校验规则变化，都必须在本文件的「变更记录」章节登记，并同步 `文档/接口文档/` 下对应分册。
4. **新增接口必须登记**：写进本文档 + `05-接口清单与规范差距.md` 速查表 + `server.py` 的 `GET_ROUTES` / `POST_ROUTES` 白名单表（白名单即表键，未登记的接口一律 404）。
5. **文档描述必须与实现一致。** 发现不一致以「文档 bug」处理：先确认实现，再修正文档或修正实现，二选一，不得搁置。

### 变更记录

| 日期 | 变更 | 影响接口 | 登记人 |
| --- | --- | --- | --- |
| 2026-09-21 | **08 分册 V2-4 格式黑名单三层（G20）**：新增 `POST /api/build-task-filter`（任务级过滤设置，任务 token CAS）与 `GET /api/build-materials?view=filter` 过滤报告视图；`build-upload-init` 命中黑名单 → 422 `BLACKLISTED`；ZIP 展开响应附 `filtered[]` 逐项命中规则；capabilities 新增 `blacklist` 枚举；Task 新增 `filter` 字段。判定优先级 硬>白名单>软>自定义追加，硬黑名单为安全边界不可配置，被过滤文件必须可见（计数+清单+命中规则）。详见 08 §13 | `/api/build-task-filter`（新增）、`/api/build-upload-init`、`/api/build-upload-complete`、`/api/build-materials?view=filter`、`/api/build-capabilities` | zcode |
| 2026-09-21 | **深度测试分支集成（codex/test → main 合并）**：A01/R02 两侧独立修复统一口径——行为取 main 侧（B01/B02/C01 演进版：项目校验时编排依赖上下文按三态协议传入，连接集合恒为已知），报错文案统一为深度测试分支的富格式（A01：`规则/动作 「名称」(id)：content/effect 提供时必须为文本（当前类型 X）`；R02：`属性来源 对象.属性：引用的编排 名称(flowId) 配置无效：原因`，不存在编排补 id 定位）。金样按合并后实现重建（100 样例），详见 02 §4.9、03 §2.2 | POST /api/validate、/api/save（errors）、/api/publish；/api/project-validate、/api/project-publish（errors 文案） | zcode |
| 2026-09-21 | 集成验收P3文案纠正：元数据仍统一读取并缓存；未声明依赖时不以对应目录读取失败阻断。仅修正文档，不改变实现。 | 03 §2.2、04 §2.2 | codex |
| 2026-09-21 | **C01 空白依赖引用漏检修复（B01/B02 第二轮独立验收补充）**：依赖声明判据统一为**原始值的非空字符串**（与检查器底层口径一致，不做 `strip()` 归一）——`providerId`/`credentialId` 为纯空白（空格/Tab/换行）时属「已声明但无效」，必须照常装载上下文：目录可读 → 报「不存在或已被删除」，读取失败 → 报「读取失败」阻断，两条路径均不得跳过检查后放行发布（修复前：模型空集合/读取异常两态均 200 并新增版本）；键缺失/`None`/空字符串仍按未声明走兼容路径、不被无关故障误伤；不 trim/不重写既有 ID，只读兼容语义与编排编辑页 `None` 未知上下文行为不变。详见 03 §2.2、04 §2.2 | POST /api/project-validate、/api/project-publish（errors/422 语义） | zcode |
| 2026-09-21 | **B01/B02 依赖上下文三态修订（验收补充修复）**：项目校验/发布的编排依赖上下文按「已知集合（含空集合）≠未知≠读取失败」处理——① 明确空连接列表不再降级为未知，SQL/Redis 节点引用不存在连接在零连接项目中报「数据连接不存在」并阻断发布（422 零写入）；② 编排实际声明的 `providerId`/`credentialId` 对应目录读取失败时 fail-closed 报「读取失败」（与「不存在」区分、不回显异常原文），未声明依赖的编排不被无关故障误伤；③ 同次校验缓存保存读取状态，编排先后顺序不影响结论；④ 纯公式编排 + 登记实例 + 空连接保持合法；⑤ 编排编辑页（/api/flow-check）「未知上下文→warning」兼容语义不变。详见 03 §2.2、04 §2.2 | POST /api/project-validate、/api/project-publish（errors/422 语义）；flows.check_flow 内部协议 | qoder |
| 2026-09-21 | **A01 修复：规则 `content` / 动作 v2 `effect` 提供时必须为文本**：`None`/缺键/空串/空白串按未填放行（选填语义不变）；对象/数组/数值/布尔报校验错误（单条消息含定义名称、字段名、「必须为文本」与当前类型，不得经 str() 隐式转换）。检查在 validate / save(errors) / publish(422，版本零新增) 共用；导入路径经 save 同受兜底。详见 02 §4.9/§4.10 | POST /api/validate、/api/save（errors）、/api/publish（新增 422 场景） | zcode |
| 2026-09-21 | **R02 修复：项目校验检查被引用编排的配置有效性**：`/api/project-validate` 对项目引用的编排调用纯配置检查（flows.check_flow，缓存每编排一次）；编排配置错误（如输出未绑定）转为单条阻断项，消息含对象/属性定位、编排标识与原因；warnings 不阻断；未被引用的编排不受影响；不存在的编排维持"不存在"阻断并补定位。发布路径复用同一校验（422，版本零新增），依赖快照重验机制不变。金样新增 2 样例（57/58）。详见 03 §2.2 | POST /api/project-validate（errors）、/api/project-publish（新增 422 场景） | zcode |
| 2026-09-21 | **D-Q02-01 页面口径修正（恢复能力仍按延期决定）**：本体发布页「历史快照」区更名为「迁移导入的快照」，说明改为准确表述——该区仅列出迁移导入的 release-ZIP 工件；在线发布的版本在「已发布版本」历史中，当前不在此处恢复。恢复功能行为不变。前端文案变更，无接口变化 | （无接口变化） | zcode |
| 2026-09-20 | **合并验收五项修复（R01/R02/R03/A01/A02）**：① A01 字段类型边界落定——名称/业务定义必须文本，`content`/`effect` 为合法选填文本，非文本（对象/数组/数值/布尔）受控报错、`description=null` 给定位文案，正式发布路由 `422` 且零新增版本，约束全文见 02 §4.9；图谱「保存」与规则/动作库共用同一记录级校验（A02），不再绕过必填。② R02 被引用的编排自身结构错误（空壳/输出未绑定/实现失效等，复用 `flows.check_flow`）纳入项目校验 error 并阻断发布，`kind=flow` 属性来源与动作绑定两条路径，编排「不存在/读取失败/自身无效」文案彼此区分，见 03 §2.2。③ R03 发布路径的目录 payload 与目录依赖令牌（指纹:代际）改为同一次读取、同一基线；payload 读取后目录被更新 → 409 `DEPENDENCY_CHANGED` 零写入，读取前更新 → 按新内容校验，见 03 §2.2。④ R01 前端 `App.vue` 的检查结论新鲜度 watch 移到 `flowState` 声明之后，修复客户端挂载 `Cannot access 'flowState' before initialization`（无接口变化，新增客户端挂载回归 `tests/app_client_mount.test.mjs`） | POST /api/save、/api/validate、/api/publish（errors/422 语义）；POST /api/project-validate、/api/project-publish（error 文案与 409 reason） | zcode |
| 2026-09-20 | **规则与动作字段精简**（20260920 需求）：规则 `name`/`description` 必填、`content` 选填、不再要求新填 `output`（旧 `output` 全程透传保留，只读展示为历史补充说明）；动作 `name`/`description` 必填、`effect` 改显示为「预期效果」且选填（存储键不变，不新建 expectedEffect）；非空值必须为文本。同步 Excel 导入协议（规则三列表头、动作「预期效果」列，旧「输出结果」/「业务效果」作别名，新旧效果都非空且不同报冲突阻断该批）与下载模板表头。校验变化：`POST /api/validate`、`/api/publish-check`、`POST /api/save` 的 `errors` 文案（详见 02 §4.9/§4.10） | POST /api/save、/api/validate、/api/publish-check（errors 内容） | zcode |
| 2026-09-21 | 08 分册 §8.2 交付草稿装配位置修复（D06）：规则 → `workflow.businessRules`（+`businessRuleAssociations`）、动作 → `workflow.actions`（简化动作 definitionVersion 2，+`actionAssociations`），对象关联引用宿主对象稳定 @id；本体图/metadata 不再生成 `mg:BusinessRule`/`mg:Action`；`content`/`effect` 选填、不生成 `output`（20260920 精简口径）。接口路径与请求/响应字段不变，交付草稿内部结构收敛 | `/api/build-deliver`（交付草稿结构）、08 §8.2 | qoder |
| 2026-09-21 | **新增 08 分册《从物料自动构建本体接口》并完成契约收敛（验收修复最后一波：HTTP 路由层 + 接口文档）**：登记 33 条 `/api/build-*` 路由（10 GET + 23 POST，已逐条核对 `server.py` 白名单）并补入 05 速查表 §1.5（速查表总数 57→97，与白名单实际条数对齐）。冻结口径：
① **§0 写操作 `revision` 三类令牌互不通用**——任务 token（`build-task-rename`）／候选 token（`build-candidate-update`、`build-candidate-decide`、`build-candidates-merge`（执行）、`build-review-undo`、`build-diff-resolve`）／整数修订（`build-material-exclude` 用 `materialRevision` 的**字符串形态**且必填；`build-scope-save`、`build-scope-confirm` 用整数 `scopeRevision`，省略按 0 处理即等价必填；`build-message`、`build-regenerate` 的整数 `scopeRevision` 可选，省略即跳过比对）。**必填令牌缺失或空串一律 400，不匹配一律 409 + `currentRevision`**，废除路由层「空值跳过 CAS」；
② `build-deliver` 的 `checkToken` 改为必填（缺失/空串 400，杜绝跳过预检直接提交），令牌内容失效仍 422 `CHECK_TOKEN_STALE`；
③ 路由层不得再用本地 `except ValueError` 包裹域服务：域层带 `code`/`status`/`issues` 的异常原样上抛由 `server.py` 映射，仅对未挂码的 `DuplicateOntologyName` 补 409 `DUPLICATE_NAME`（唯一的映射补齐）；上传族细分码（409 `UPLOAD_CONFLICT`/`UPLOAD_EXPIRED`、422 `HASH_MISMATCH`/`LIMIT_EXCEEDED`/`ZIP_INVALID`、404 `NOT_FOUND`）不再被压平；`dataBase64` 上限口径改由 `chunkBytes` 推导（路由放行至 2 MB 请求体上限，超限 422）；
④ 无 provider 时的口径按端点分列（`build-capabilities` 200+`provider:null`；启动类操作 422 `INVALID_STATE`；`build-message` 唯一例外＝用户消息保留 + 200 + `assistantError`）；
⑤ §1 数据模型响应字段与 `workbench/storage/ontology_build.py` 视图函数逐字对齐，timeSeries `valueType` 枚举冻结为 `string/double/decimal/integer/boolean/date/dateTime`，预检/交付阻断 `issues` 码集合冻结为 10 个；被合并候选（`origin.mergedInto` 非空）从列表/计数/预检选定集合剔除；`build-regenerate` 的旧批次人工排除强制继承为 `defer` + `origin.revived` | 33 个 `/api/build-*` 接口（08 分册全量；本轮代码改动集中在 `/api/build-material-exclude`、`-candidates-merge`、`-review-undo`、`-regenerate`、`-diff-resolve`、`-deliver`、`-message`、`-upload-*`） | qoder |
| 2026-09-20 | **v2 功能保护冻结（本体与项目统一维护体验改版 v2，G1 契约先行）**：
① `/api/project-validate` 响应新增 `baseline`（本次检查实际读取的项目修订/本体引用/被引用编排修订/目录指纹与代际），并冻结依赖读取失败语义（编排读取失败→error 阻断发布；目录缓存损坏→error；存储不可用→503，均不得降级为「零问题」）；
② `/api/project-publish` 接入 `requestId` 幂等（复用 `wb_requests`，同 key 同内容回放 `idempotentReplay`、同 key 异内容 409，回执与发布同事务），并冻结发布依赖重验（校验后依赖被改→拒绝发布，`409` + `reason:"DEPENDENCY_CHANGED"`）；
③ `/api/project-save` 保存边界保护：删除仍被引用的连接 → 422 `REFERENCE_IN_USE`（附 references 定位，连接与其引用同批移除放行），变更引用的本体版本时目标必须存在且已发布 → 否则 404；
④ `/api/project-upgrade-check` 请求 `revision` 冻结为比较基线：确认升级时服务端核对草稿修订与目标版本，不匹配 409 且原引用不变；
⑤ `/api/catalog-refresh` 迟到结果保护升级为「指纹（排除显示名）+ 凭据安全代际 + 代际条件更新」，结果被丢弃时响应带 `"stale": true`（客户端不得按成功提示），删除连接服务端复核依赖（读取失败 fail-closed） | project-save / project-validate / project-publish / project-upgrade-check / catalog-refresh / project-state | zcode |
| 2026-09-20 | 保存边界悬空引用检查范围补全与比较口径修正（20260920 需求 11～13 第二轮复验 S1～S3）：
① 检查范围补齐接口定义 `interfaces[].properties` / `implementations`，以及 functions/actions/interfaces 三类的字段级引用（适用对象类型、输出属性、链接/计算/接口引用、顶层 `function_ref`、步骤与属性绑定、参数、`properties`/`implementations` 清单）、签名槽位 `inputs/outputs[].ref`（含 `kind=base` 带 id 情形）、图内取值函数与嵌套值类型——与前端 `graphReferenceEntries` 对齐；
② 比较键改为「来源稳定 id + 引用字段/槽位身份 + 目标 id」，业务名称只进展示文案：既有失效引用仅改名称可继续保存，同名同文案但身份不同的新失效引用仍阻断；
③ 旧 `rules.rules[].member_type` 与指标 `rule_ref` 判定改为对真实旧规则 id 集比较，修正有效旧引用被误报。
不改变 422 `BROKEN_REFERENCE`、零写入、revision 不推进、首次保存不阻断等既有语义（详见 02 §2.2） | POST /api/save | zcode |
| 2026-09-20 | 本体草稿保存边界新增**悬空引用阻断**（20260920 需求 11～13 / 验收意见保存边界）：`POST /api/save` 与当前 head 草稿逐条比较引用完整性（图内 domain/range、契约签名 ref、动作/规则关联、值类型、指标），**仅当本次保存新引入失效引用**时返回 422 `BROKEN_REFERENCE` 并列出具体引用；历史遗留失效引用与「未填写完整」仍按 200 + errors 保存，不锁死草稿（详见 02 §2.2） | POST /api/save | zcode |
| 2026-09-19 | 函数编排取值放开「列表输出 → 时间序列属性」（01 §3.1）：`kind='flow'` 绑定新增可选 `result:{valueField,timestampField}`（元素对象字段稳定 id）；仅当输出为 list<object{fields…}> 时可绑时间序列属性，取值字段须为 number、时间字段须为 datetime，缺失/不存在/类型不符校验阻断；对象输出与「标量属性绑列表」仍拒绝；标量输出绑定形状不变（不写 result）。执行语义仍随编排取值执行能力一并实现，本轮仅配置与校验 | POST /api/project-validate、POST /api/project-publish、POST /api/projects（项目状态内携带） | zcode |
| 2026-09-19 | flow-run 输入预览改为精确有界：inputs 序列化 ≤65536 字节（UTF-8）、列表 ≤100 项，截断以 previewTruncated 标记结构表示（R06）（原记录误插于表头之前，本轮回位） | 04 §3.1 | zcode |
| 2026-09-19 | 配置迁移语义修正（07 §4.5）：导入预检增加强依赖闭包校验（项目快照引用的本体/编排必须在包内）、requiredCapabilities 白名单（非空 415/阻断）、JSON 重复键拒绝；导入写入同步编排 payload.flowId/name 为新身份（可继续保存）；项目草稿与历史发布逐快照解析引用；M07 副本归属精确匹配+预检歧义阻断+副本名进预览；敏感剥离收窄为认证头与 URL 认证段（模型连接地址覆盖、不再按 token 等键名清空业务字段）；默认模型作为被依赖配置入包并在导入时显式绑定；import-result 新增 strippedItems；pendingCredentials 按本次新项目定位；GET /api/api-credentials 响应新增 pending（03 §4.1），补填后自动清除 | 7 个 /api/config-package-*、GET /api/api-credentials | zcode |
| 2026-09-19 | **新增配置迁移 7 接口**（07 分册）：export-preview（依赖闭包冻结快照+exportToken）、export（ZIP 二进制下载，独立二进制分派不影响旧 /api/export）、stage（begin/chunk 有界分片上传 512KiB/片、包≤20MiB）、import-preview（安全解析+命名预检，零资产写入）、import（previewToken+requestId 原子导入：每次主动导入新建本体/项目/编排/模型配置，同 requestId 重试幂等、改参数 409）、import-result（requestId 回执查询，持久化先于 token 过期）、discard。包格式 wiz-workbench-config-package formatVersion=1；不含受管理凭据；业务稳定 ID 恒等保留、资产级 ID 重建；解压≤100MiB/条目≤2000/单文件≤10MiB/TTL 30 分钟 | 7 个 /api/config-package-* 接口（新增） | zcode |
| 2026-09-19 | `/api/flow-run` 新增可选 `testMode="isolated"` 隔离片段测试与稳定 ID `inputOverrides`（只覆盖范围外/未绑定输入；入口值同键赋值 400 歧义拒绝）；响应节点结果新增可选 `inputs`/`inputsTruncated`（有界预览，≤100 条/≈64KiB，不含凭据）；skipped 语义明确为失败沿所选范围传递性标记；targets `[]` 一律 400。旧调用（无 testMode：全图 revision/409/互斥、单节点技术名覆盖、多节点上游闭合链）语义不变 | POST /api/flow-run | zcode |
| 2026-09-19 | 项目映射新增 `bindings.mappingDescriptions`（四类说明，schemaVersion=1）：写入结构/长度校验、旧客户端省略整块保留、显式清空按删键表达；validate/publish 追加说明结构与失效引用检查（失效阻断）；property-preview 遇非空说明在取数前返回不支持 | project-state / project-save / project-validate / project-publish / project-upgrade-check / project-property-preview | zcode |
| 2026-09-18 | 首次整理：梳理 40 个在线接口，统一规范描述 | 全部 | — |
| 2026-09-18 | 缺陷修复（批次 A）：`POST /api/export` 路由表原以 `None` 占位，被 do_POST 的「路由不存在」判定拦截，恒定 404 不可达；改用独立哨兵 `BINARY_ROUTE` 区分「键不存在」与「二进制端点」。`POST /api/restore` 原调用不存在的 `_server_current()`（NameError → 400）；改为 `current()`，并按已校验基线 CAS、响应回传本次提交实际得到的 revision。两接口契约（请求/响应结构）不变，实现回归文档描述；新增回归测试 `tests/test_export_restore_http.py` | POST /api/export、POST /api/restore | — |
| 2026-09-18 | 规范修复（批次 B，R3/R4/R5）：① 错误信封**实装** `code` 字段（400 INVALID_ARGUMENT / 403 ORIGIN_REJECTED / 404 NOT_FOUND / 409 REVISION_CONFLICT·DUPLICATE_NAME / 413 / 415 / 503 / 新增 500 INTERNAL_ERROR），未知异常不再吞成 400，客户端只收通用消息，服务端留堆栈与 requestId；② 全部响应新增 `X-Request-Id` 头；③ GET 取消「全部接口统一按 ontology 定位工作区」的前置副作用，未知端点稳定 404，独立接口（flows / llm-providers / storage-status 等）不再依赖当前本体有效；④ `GET /api/projects` 筛选语义显式化（仅 `ontology` 参数控制范围，空值 400，无效本体 404，其他参数不隐式改变范围）。详见 2.2/2.3/2.4 与 03 分册 §1.1 | 全部 GET 接口、全部 POST 错误路径、GET /api/projects | — |
| 2026-09-18 | 数据模型新增（函数编排取值）：属性取值来源新增 `kind='flow'`（结构、类型相容表、输入绑定来源与约束见 `01-通用约定与数据模型.md` §3.1，枚举见 §6）。项目侧只读引用编排 id + 输出 id + 输入绑定，不复制编排定义；编排删除按不存在处理；对象/列表输出与时间序列属性禁止绑定；属性间循环依赖在保存/发布校验阻断。历史 `kind='computed'`（直接 SQL / 引用规则 / 计算函数）**保留只读兼容**，前端不再提供新建入口。后端校验 `project_validation._check_flow_binding`，前端镜像 `PropertySources.vue`；新增回归 `tests/test_project_flow_source.py`（21 步）。**取值预览本期未打通编排执行** | POST /api/project-validate、POST /api/project-publish、POST /api/projects（项目状态内携带） | — |
| 2026-09-18 | 新增 `POST /api/llm-provider-default`（§4.5）：仅切换默认提供方指针，供列表页「设为默认」按钮使用。原先只能通过 `POST /api/llm-provider-save` 携带全量字段 + `isDefault` 完成，会顺带覆盖配置并递增 `metadata_revision`，语义过重。新接口幂等（已是默认则直接 200）、与 `save`/`clear` 共用 `model-default` 护栏保证默认唯一、不触碰配置与密钥。前端对话框不再提供「设为默认」勾选项 | POST /api/llm-provider-default（新增）、POST /api/llm-provider-save（`isDefault` 保留，前端列表页不再使用） | — |
| 2026-09-18 | **登录与账号体系**（需求 20260918_登录与账号体系）：新增 4 个免登录认证接口 （`GET /api/auth-state`、`POST /api/auth-login`、`POST /api/auth-register`、`POST /api/auth-logout`，会话 Cookie `wiz_session`，30 天滑动续期）；**其余全部 `/api/*` 接口未登录返回 401 `UNAUTHENTICATED`**；所有业务数据按账号完全隔离（本体/项目/编排/模型配置/连接与 API 凭据），跨账号 id 一律按不存在处理；存量数据经 `transfer create-user`/`assign-owner` 归属 admin（幂等，迁移前先 `transfer backup`）。详见 06 分册 | 全部接口（新增鉴权前置）、新增 4 个认证接口 | — |

---

## 2. 传输与通用约定

| 项 | 值 |
| --- | --- |
| 协议 | HTTP/1.1（`ThreadingHTTPServer`，多线程） |
| 监听 | 仅 `127.0.0.1`，不对外暴露、不部署生产 |
| 字符编码 | UTF-8 |
| 静态资源 | `GET /`、`/index.html`、`/assets/*` 由后端托管 `frontend/dist`（`Cache-Control: no-store` / `no-cache`） |
| API 前缀 | `/api/` |
| 请求体上限 | 2 MB（超过返回 413） |
| 前端请求出口 | 唯一：`frontend/src/app/http.ts`（`getJson` / `postJson` / `postBlob`），页面禁止自写 `fetch` |

### 2.1 请求头

| 头 | 要求 |
| --- | --- |
| `Content-Type` | POST 必须为 `application/json`（可带 `; charset=utf-8`），否则 415 |
| `Origin` | POST 必须为 `http://127.0.0.1:<port>` 或 `http://localhost:<port>`，缺失或不符返回 403 |
| `Content-Length` | POST 必须 > 0 且 ≤ 2 000 000 |
| `Accept` | 可选；响应恒为 JSON（导出接口为 `application/zip`） |

### 2.2 响应头

| 头 | 值 |
| --- | --- |
| `Content-Type` | `application/json; charset=utf-8`（导出为 `application/zip`） |
| `Cache-Control` | `no-store` |
| `X-Request-Id` | 服务端为每个 `/api/` 请求生成的短标识（12 位十六进制）；排障时用它与服务端日志关联，客户端无需解析语义 |
| `Connection` | 413 / 500 / 503 / 404(POST) 场景返回 `close` |
| `Content-Disposition` | 导出接口：`attachment; filename=ontology-model.zip` |

### 2.3 状态码标准

| 状态码 | 语义 | 本系统触发条件 |
| --- | --- | --- |
| 200 OK | 成功 | 读取成功、写操作成功、业务校验完成（校验结论在响应体内） |
| 201 Created | 资源已创建 | 创建本体 / 项目 / 编排 / 复制编排 |
| 400 Bad Request | 请求非法 | 请求体不是合法 JSON / 缺少必填字段 / 标识或参数非法（业务层显式输入校验，`ValueError` 约定） |
| 403 Forbidden | 来源不允许 | POST `Origin` 不在白名单 |
| 404 Not Found | 资源不存在 | 接口不在白名单、本体/项目/版本/编排/快照/LLM 提供方不存在 |
| 409 Conflict | 冲突 | **revision 过期（响应带 `currentRevision`）**、同名资产、编排正在运行、`requestId` 复用且内容不同 |
| 413 Payload Too Large | 请求体超限 | > 2 MB |
| 415 Unsupported Media Type | 媒体类型不支持 | POST 非 JSON |
| 422 Unprocessable Entity | 结构合法但业务校验未通过 | 本体发布校验失败、项目发布校验失败、编排配置检查未通过、演示数据不可用 |
| 500 Internal Server Error | 服务端内部错误 | 未预期的程序缺陷 / 内部 I/O 错误。客户端收到的 `error` 是通用消息 + requestId（不泄露内部路径、SQL 或异常细节），完整堆栈只写服务端日志；客户端不应重试 |
| 503 Service Unavailable | 存储不可用 | 库不可用 / 锁超时 / schema 缺失（明确可重试，绝不回退文件） |

> 异常分类约定（2026-09-18 批次 B 起）：输入不合法 → 400；不存在 → 404；revision 冲突 → 409（带 `currentRevision`）；业务阻断 → 422；存储不可用 → 503；未知程序错误 → 500。业务层用 `raise ValueError(<中文消息>)` 表达客户端输入错误；`KeyError`/`TypeError`（请求体形态不符）同样归 400 但消息泛化；其余异常（含 `OSError`）一律 500。

### 2.4 错误响应信封

**统一结构**（所有非 2xx；2026-09-18 批次 B 起 `code` 为实装字段）：

```json
{ "error": "中文可读消息", "code": "REVISION_CONFLICT" }
```

- `error`：可直接展示给用户的中文消息；500 时为通用消息（含 requestId），不含内部细节。
- `code`：稳定错误码（见下表），客户端程序化分支**只依赖 `code`**，不得解析 `error` 文本。

**扩展字段**（按场景附加，客户端必须识别）：

| 字段 | 场景 | 说明 |
| --- | --- | --- |
| `currentRevision` | 409 | 服务端最新并发令牌（见 2.5 的冲突处理约定） |
| `check` | 422 | 编排配置检查报告（`/api/flow-check` 同构） |
| `report` | 422 | 项目配置校验报告（`/api/project-validate` 同构） |
| `reasons` | 422 | 变更分类理由列表 |

**错误码表**：

| code | HTTP | 含义 |
| --- | --- | --- |
| `INVALID_ARGUMENT` | 400 | 请求体/参数非法（JSON 解析失败、缺必填字段、标识非法、业务输入校验未通过） |
| `ORIGIN_REJECTED` | 403 | 来源不允许 |
| `NOT_FOUND` | 404 | 接口或资源不存在 |
| `REVISION_CONFLICT` | 409 | 并发令牌过期（含 `requestId` 复用且内容不同） |
| `DUPLICATE_NAME` | 409 | 同名冲突 |
| `FLOW_RUNNING` | 409 | 编排正在运行中（`/api/flow-run` / `/api/flow-save`） |
| `PAYLOAD_TOO_LARGE` | 413 | 请求超限 |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | 非 JSON |
| `VALIDATION_FAILED` | 422 | 业务校验未通过（分派层不注入；业务层响应自带 `errors`/`check`/`report`） |
| `STORAGE_UNAVAILABLE` | 503 | 存储不可用 |
| `INTERNAL_ERROR` | 500 | 服务端内部错误（消息为通用文案 + requestId） |

### 2.5 并发控制（revision / CAS）

- `revision` 是**不透明令牌**（`r-<uuid>`），不等于内容 hash；无草稿时取「空白状态内容 hash」作一次性基线。
- 写接口（`save` / `publish` / `restore` / `project-save` / `project-publish` / `flow-save`）必须携带客户端当前 `revision`。
- 服务端比对失败返回 **409 + `currentRevision`**；前端保存协调器（`saveCoordinator`）遇 409 进入**冲突状态**，由用户显式决定后续操作（覆盖或刷新），**不做自动换基线重试**。
- 保存失败**绝不假成功**：后端不得把 CAS 失败降级为 200 或返回空数据。

### 2.6 幂等

- 仅 `/api/publish` 支持 `requestId` 幂等：同 `requestId` + 同内容 → 回放既有回执（响应带 `idempotentReplay: true`）；同 `requestId` + 不同内容 → 409。
- 其余写接口暂不支持幂等键；客户端应依赖 revision CAS 防重复提交。

### 2.7 分页

- 仅 `/api/model-definitions` 支持：`limit`（1–100，默认 50）+ `cursor`（偏移量，默认 0）。
- 响应返回 `cursor` 为下一页偏移量，无下一页时为 `null`；**快照切换后游标失效，绝不跨版本拼结果**。

### 2.8 时间与命名

- 时间统一 ISO 8601 UTC（如 `2026-09-18T05:00:00.123456+00:00`）。
- JSON 字段统一 `camelCase`（历史遗留的下划线字段如 `object_bindings`、`rule_ref` 属本体/项目状态内部格式，不在接口层改）。

### 2.9 安全边界（不得放松）

1. POST 白名单 = `server.py` 的路由表键集合；
2. POST Origin 校验；
3. 2 MB 请求上限；
4. 密钥（连接密码 / API 凭据 / LLM API Key）**只写不读回**，永不进响应、日志、项目状态与快照；
5. 数据连接探测只执行固定只读操作（`SELECT 1` / `PING` / `information_schema`），绝不执行用户提交的 SQL；
6. 探测类接口（`/api/connection-test`、`/api/connection-catalog`、`/api/catalog-refresh`、`/api/flow-run`、`/api/llm-provider-test`、`/api/project-property-preview`）**绝不持有全局写锁**。

---

## 3. 分册索引

| 文档 | 内容 |
| --- | --- |
| [01-通用约定与数据模型](01-通用约定与数据模型.md) | 三类核心状态结构（本体 / 项目 / 编排）、校验报告结构、共享枚举 |
| [02-本体区接口](02-本体区接口.md) | 本体资产、草稿、版本、发布、校验、预览、探索、导出与恢复（23 个） |
| [03-项目区接口](03-项目区接口.md) | 项目、数据连接、目录、凭据、取值预览、公式试算（17 个） |
| [04-编排与LLM接口](04-编排与LLM接口.md) | 函数编排 CRUD、配置检查、运行、LLM 提供方（13 个） |
| [05-接口清单与规范差距](05-接口清单与规范差距.md) | 全量速查表 + 现状与标准 HTTP/REST 规范的差距与演进路线 |
| [06-认证与账户接口](06-认证与账户接口.md) | 登录/注册/退出/登录态、会话 Cookie、数据按账号隔离与迁移命令（4 个，2026-09-18） |
| [07-配置迁移接口](07-配置迁移接口.md) | 本体与项目配置打包导出/导入/结果/清理，包格式与分片上传（7 个，2026-09-19） |
| [08-从物料自动构建本体接口](08-从物料自动构建本体接口.md) | 生成任务、分片上传与材料解析、范围对话、候选评审（合并/撤销/再生成/差异裁决）、原子交付为新本体草稿；含 §0 写操作 `revision` 口径表与 §2.1 provider 缺失口径（33 个，2026-09-20 新增，2026-09-21 收敛修订） |

---

## 4. 接口变更流程（Definition of Done）

```
1. 修改 文档/接口文档/ 对应分册（路径/字段/状态码/示例）
2. 在 README「变更记录」登记一行
3. 改后端：server.py 路由表 + 对应 *_routes.py 业务函数
4. 改前端：app/http.ts 或对应区 api.ts（禁止页面自写 fetch）
5. 同步协议层：model_format.py ↔ frontend/src/ontology/modelFormat.ts（改字段映射表时）
6. 回归：相关 tests/ 用例 +（改前端时）cd frontend && npm run build
7. git 提交：文档与代码同一 commit，信息写明接口变更
```

**禁止**：先改代码后补文档；前端绕过 `api.ts` 直连；把校验失败写成 200 + 空数据；把 CAS 失败写成成功。

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

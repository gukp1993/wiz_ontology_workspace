# 04 · 编排与 LLM 提供方接口

编排区共 12 个接口。编排是**第三条独立数据线**：与本体、项目互不携带对方数据；一期只有草稿（无发布）、软删除（`status=deleted`，快照历史永不物理清除）。

> **注意（实现细节）**：`server.py` 的 GET 分派对所有 `/api/*` 请求先按 `ontology` 参数定位本体工作区，编排/LLM 接口因此也经过该前置（缺省 `storage`）。这是**已知耦合**，演进方向见 `05` §3.2。

---

## 1. 编排资产

### 1.1 查询编排清单

```http
GET /api/flows
GET /api/flows?includeDeleted=1
```

| Query | 默认 | 说明 |
| --- | --- | --- |
| `includeDeleted` | false | `1` / `true` 时包含软删除项 |

**响应** `200`

```json
{ "items": [ { "id": "x1y2z3", "name": "编排名称", "description": "", "status": "active",
               "nodeCount": 5, "errorCount": 0, "updatedAt": "...", "configStatus": "passed" } ] }
```

> `configStatus` 是读取时实时计算的配置检查结果（`passed` / `pending`），**不是发布状态**。

### 1.2 读取编排草稿

```http
GET /api/flow-state?flow={flowId}
```

**响应** `200`

```json
{ "state": { /* FlowState，含 _draft: { seq, updatedAt } */ }, "revision": "r-<uuid>" }
```

| 状态码 | 场景 |
| --- | --- |
| 400 | 编排标识非法 |
| 404 | 编排不存在 |

### 1.3 创建编排

```http
POST /api/flows
```

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | string | 是 | 1–80 字符 |
| `description` | string | 否 | — |

**响应** `201`

```json
{ "id": "x1y2z3", "name": "编排名称" }
```

| 状态码 | 场景 |
| --- | --- |
| 400 | 名称非法 |
| 409 | 同名编排 |

### 1.4 复制编排

```http
POST /api/flow-copy
```

请求体：`{ "flowId": "x1y2z3", "name": "副本名称"（可选） }`

**响应** `201`

```json
{ "id": "newid123", "name": "副本名称" }
```

| 状态码 | 场景 |
| --- | --- |
| 400 | 参数非法 |
| 404 | 原编排不存在 |

**约束**：服务端重生成**全部稳定 id** 并重映射引用；原编排任何字节不动。

### 1.5 删除编排（软删除）

```http
POST /api/flow-delete
```

请求体：`{ "flowId": "x1y2z3" }`

**响应** `200`

```json
{ "ok": true }
```

| 状态码 | 场景 |
| --- | --- |
| 400 | 标识非法 |
| 404 | 编排不存在 |

---

## 2. 编排保存与检查

### 2.1 保存编排草稿

```http
POST /api/flow-save
```

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `state` | FlowState | 是 | 完整编排状态（含 `flowId`） |
| `revision` | string | 是 | 并发基线 |
| `connections` | array | 否 | 当前项目数据连接的 `id/name/engine` 元数据，供配置检查校验引用 |
| `projectId` | string | 否 | 项目上下文（用于 API 凭据引用校验） |

**响应** `200`

```json
{ "revision": "r-<uuid>", "check": { /* CheckReport，见 01 §5.2 */ } }
```

| 状态码 | 场景 |
| --- | --- |
| 400 | state 缺失/结构非法、标识非法 |
| 404 | 编排不存在（首次保存前应先创建） |
| 409 | revision 过期（带 `currentRevision`） |

**说明**：配置检查失败**不影响保存**（草稿可带错保存），检查结果随保存响应回传。

### 2.2 编排配置检查（只读）

```http
POST /api/flow-check
```

请求体：`{ state, connections(可选), projectId(可选) }`

**响应** `200`

```json
{ "errors": [], "warnings": [], "items": [], "status": "passed", "diagnostics": [] }
```

| 状态码 | 场景 |
| --- | --- |
| 400 | 结构非法 |

**约束**：纯结构校验，**不写盘、不持锁、绝不执行代码 / SQL / 联网**。

**依赖上下文三态协议（2026-09-21 B01/B02 修订，冻结 `flows.check_flow` 内部口径）**：配置检查器接收三类可选依赖上下文——`project_connections`（项目数据连接元数据）、`llm_meta`（LLM 提供方元数据列表）、`credential_ids`（项目 API 凭据 id 集合）。每类只有三种状态，语义互斥：

| 状态 | 传入值 | 检查器行为 |
| --- | --- | --- |
| **已知集合** | `list`/`set`（**含空集合**） | 照常判引用存在性；引用不在集合内 → error「…不存在或已被删除」。**空集合是已知状态，不是「没有上下文」** |
| **未知/未提供** | `None` | 跳过该项存在性检查；编辑页场景保留兼容 warning（如「数据连接引用待在编辑器中选择项目数据连接后校验」） |
| **读取失败** | 由调用方（项目校验）在编排**实际声明该依赖**时转成阻断 error，不进入检查器 | 「读取失败」≠「不存在」≠「未知」；文案定位依赖类别，不回显异常原文 |

本接口（编排编辑页）通常没有项目上下文 → 传 `None`（未知）是正确行为，不因该修订改变。项目校验/发布路径的上下文装载规则（明确空集合必须按已知传入、目录读取失败 fail-closed、无依赖编排不被无关故障误伤、缓存保存读取状态）见 03 §2.2。

**「实际声明该依赖」的判据（2026-09-21 C01 修订）**：与检查器底层口径一致——按**原始值的非空字符串**判定（`str(impl.get('providerId') or '')` / `str(impl.get('credentialId') or '')` 非空即视为已声明），不做 `strip()` 归一。因此**纯空白 ID（空格 / Tab / 换行）属于「已声明但无效」**：调用方不得把它当作「未声明」而跳过检查，必须按已知集合判「不存在或已被删除」，或在对应目录读取失败时按三态协议转「读取失败」阻断；只有**键缺失、`None`、空字符串**才按「未声明」走兼容路径（不以该目录读取失败阻断校验）。该判据只影响调用方如何装载上下文与是否阻断，不修改编排数据、不在保存时 trim 或重写 ID，也不改变检查器对原始字符串的使用方式。

---

## 3. 编排运行

### 3.1 运行 / 测试编排

```http
POST /api/flow-run
```

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `state` | FlowState | 是 | — |
| `revision` | string | 全图运行时必填 | 并发基线；节点/链测试与隔离测试不需要 |
| `targets` | string[] | 见说明 | 节点 ID 列表。**省略 = 全图运行**；指定 = 单节点/链测试（不落盘、无 revision 要求）。`[]` 一律 400，绝不被解释为全图 |
| `inputs` | object | 否 | 入口参数取值（键为入口参数稳定 ID 或技术名，沿用既有兼容） |
| `projectId` | string | 否 | 项目上下文（数据连接 + API 凭据，密钥不在此读取） |
| `connections` | array | 否 | 连接元数据（校验引用） |
| `testMode` | string | 否 | 2026-09-19 新增。省略 = 既有语义（全图 / 单节点 / 上游闭合链）。仅接受 `"isolated"`：隔离片段测试——只执行 `targets` 所选处理节点，范围外输入由 `inputOverrides` 手工提供，内部绑定照常传递，范围外节点（含上下游）不执行 |
| `inputOverrides` | object | 否 | 2026-09-19 新增，仅 `testMode=isolated` 可用：`{ 目标节点稳定 ID: { 输入稳定 ID: 值 } }`。只允许覆盖本次范围的外部来源输入（来源节点在范围外的 `node`/`nodeField`）与未绑定输入；固定来源与范围内内部依赖不得覆盖；同一输入同时经 `inputs`（入口值）与本表赋值 → 400 歧义拒绝 |

**`testMode=isolated` 语义（2026-09-19 登记，前端 20260919_函数编排配置与调试优化 需求）**：

- `targets` 必填：非空、无重复、全部存在且为处理节点；被测集合自身必须无环且连通（集合内部依赖边意义下）。节点集非法/成环/不连通 → 400。
- 范围外入边转为外部输入声明：目标输入的来源节点在范围外时，必须由 `inputOverrides[目标节点ID][输入ID]` 提供最终值（字段引用提供目标输入所需的最终值，不要求构造整个范围外输出对象）；未绑定输入可选提供。缺值或类型不符 → 422（含节点/输入定位），且**在任何节点真实执行前拒绝**（预检零执行）。
- 固定来源照常取值，不接受覆盖；范围内上游输出自动传递，不允许测试值覆盖；内部输出类型/字段路径错误按节点失败返回，不退回人工输入。
- 被测节点自身实现/连接/输出声明错误 → 422（响应带定位）；范围外节点（含编排输出未绑定、上游实现错误）不参与校验、不构成阻断。
- 执行顺序按被测子图依赖拓扑排序，客户端提交顺序不决定执行顺序；不落盘、无 revision/进程内全图互斥要求。
- 省略 `targets` 却携带 `testMode` 或 `inputOverrides` → 400。旧调用（无 `testMode`）语义不变：全图运行保留 revision/409/互斥；单节点沿用技术名覆盖；多节点链仍要求上游闭合。

**响应** `200`

```json
{
  "status": "success",                 // success | error
  "nodeResults": [ { "nodeId": "n1", "status": "success", "outputs": {}, "outputNames": {},
                     "rowCount": 12, "durationMs": 45, "logs": [],
                     "inputs": {"x": 10}, "inputsTruncated": false } ],
  "startedAt": "2026-09-18T05:00:00+00:00",
  "durationMs": 320
}
```

节点结果 `status`：`success` / `failed`（带 `error`）/ `skipped`（直接上游失败，或上游被跳过——失败沿所选范围**传递性**标记 skipped）。

新增可选字段（2026-09-19）：

- `nodeResults[].inputs`：该节点本次实际解析的输入值（键为输入技术名，命名与类型从提交快照读取）。输入解析前失败或未执行（skipped）时不返回该字段；执行器已收到输入后失败可返回。
- `nodeResults[].inputsTruncated`：布尔，输入预览是否被截短。预览为**展示副本**，节点真实收到的数据不变、不影响执行成功；不包含连接密码、认证头、API Key 等凭据。预览预算（2026-09-19 修正，R06）：
  - `inputs` 整体经 UTF-8 JSON 序列化后**不超过 65536 字节**（含字段名与截断标记的结构开销），超限自动收紧，不做"只置标志仍返回完整值"；
  - 每个列表预览最多 **100 项**（无论总字节是否超限）；
  - 被截短的内容以 `previewTruncated` 标记结构表示：截短字符串 → `{"previewTruncated": true, "kind": "text", "shownPrefix": "<前缀>", "originalLength": <原始字符数>}`；超 100 项列表 → `{"previewTruncated": true, "kind": "list", "items": [≤100 项], "totalItems": <原始长度>}`；超长字段名会截短并触发 `inputsTruncated`；
  - 未触发截断的值保持原结构原值；执行器与下游收到的 `inputs` 恒为完整原值，预览副本不回写。

| 状态码 | 场景 |
| --- | --- |
| 400 | state 非法 / targets 无效（空列表、重复、不存在、集合成环或不连通）/ `testMode` 值非法 / 省略 targets 却携带 isolated 或 overrides / overrides 结构非法或含未知节点/输入 / 同一输入经入口值与 overrides 重复赋值 / 被测链路非法（旧调用） |
| 404 | 编排不存在（全图运行）/ 项目不存在 |
| 409 | revision 过期（带 `currentRevision`）；或该编排正在运行中（进程内互斥，仅全图运行） |
| 422 | 配置检查未通过（全图，响应体 `{"error": "...", "check": <CheckReport>}`）/ 被测节点自身配置错误 / isolated 模式边界输入缺值或类型不符（含节点与输入定位） |

**约束（重要）**：
- 仅**短暂持锁**核对 revision（全图），**执行阶段绝不持有全局写锁**；
- 全图运行有进程内 per-flow 互斥；隔离片段测试无 revision/互斥要求，同样不落盘；
- 执行**不落盘**；节点超时上限 300 s，SQL 行数上限 10000；
- Redis 仅执行白名单命令（见 `workbench/flows.py` 的 `REDIS_COMMANDS`）；
- 节点执行阶段业务失败仍按 `200 + status:error + nodeResults` 返回，预检失败不包装成成功。

---

## 4. LLM 提供方配置

> 密钥**只写不读回**：所有响应只含元数据；密钥存加密凭据表，绝不进响应、日志、快照。

### 4.1 查询提供方清单

```http
GET /api/llm-providers
```

**响应** `200`

```json
{ "items": [ { "id": "llm-xxxxx", "name": "本地模型", "model": "qwen", "endpoint": "https://...",
               "timeout": 60, "temperature": 0, "thinking": "default",
               "isDefault": true, "keyConfigured": true } ] }
```

> 默认项排最前，其余按名称排序；`api_key` **永不出现**。`thinking` 语义见 §4.2。

### 4.2 保存提供方

```http
POST /api/llm-provider-save
```

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `providerId` | string | 否 | 为空表示新建 |
| `name` | string | 是 | ≤ 60 字符 |
| `endpoint` | string | 是 | 必须 `http(s)://` 开头，≤ 500 |
| `model` | string | 是 | ≤ 500 |
| `apiKey` | string | 条件 | 首次配置必填；编辑时留空表示**沿用已保存密钥** |
| `timeout` | number | 否 | 1–300 秒，默认 60 |
| `temperature` | number | 否 | 0–2，默认 0 |
| `thinking` | string | 否 | 思考强度：`default`（默认，跟随模型/账号默认，不追加参数）\| `off`（关闭思考，显著降低延迟）。仅对支持该参数的提供方生效——当前为 `bigmodel.cn` 端点（请求体追加 `thinking:{"type":"disabled"}`）；其他提供方存值但不追加参数。缺省 `default` |
| `isDefault` | boolean | 否 | 设为默认；**首个提供方自动成为默认**。列表页的「设为默认」不经过本接口，走 `POST /api/llm-provider-default`（§4.5） |

**响应** `200`

```json
{ "saved": true, "provider": { "id": "llm-xxxxx", "name": "本地模型", "model": "qwen",
                               "thinking": "default",
                               "isDefault": true, "keyConfigured": true } }
```

| 状态码 | 场景 |
| --- | --- |
| 400 | 参数校验失败（名称/地址/模型/超时/温度/思考强度/密钥） |

**说明**：元数据、密钥与默认项设置在同一事务完成（护栏串行化，防并发出现多个默认）；删除默认项后自动指派剩余第一个为默认。

### 4.3 删除提供方

```http
POST /api/llm-provider-delete
```

请求体：`{ "providerId": "llm-xxxxx" }`

**响应** `200`

```json
{ "cleared": true }
```

> 提供方不存在时静默成功（幂等）。

### 4.4 连通性测试

```http
POST /api/llm-provider-test
```

请求体（二选一）：

| 形态 | 字段 |
| --- | --- |
| 按已存配置 | `{ "providerId": "llm-xxxxx" }` |
| 按临时配置（不落盘） | `{ "name", "endpoint", "model", "apiKey", "timeout"(默认30), "temperature"(默认0), "thinking"(默认`default`) }` |

**响应** `200`

```json
{ "ok": true, "message": "连通正常（模型已响应）", "latencyMs": 420 }
```
或
```json
{ "ok": false, "message": "<中文错误原因>", "latencyMs": 120 }
```

| 状态码 | 场景 |
| --- | --- |
| 400 | 参数非法 |
| 404 | `providerId` 对应的提供方不存在或已删除 |

**约束**：真实网络探测，**不持全局写锁**；超时上限 30 s；发送极小请求（`ping`，`max_tokens=1`），2xx 即视为连通。

### 4.5 设为默认提供方

```http
POST /api/llm-provider-default
```

> 只切换默认项，**不重写提供方配置**：用于列表页「设为默认」按钮。原先只能通过 §4.2 携带全部字段 + `isDefault` 完成，会顺带覆盖配置并递增 `metadata_revision`，语义过重。

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `providerId` | string | 是 | 目标提供方 id |

**响应** `200`

```json
{ "ok": true, "provider": { "id": "llm-xxxxx", "name": "本地模型", "isDefault": true } }
```

| 状态码 | 场景 |
| --- | --- |
| 400 | `providerId` 格式非法（非 `llm-…` 形态） |
| 404 | `providerId` 对应的提供方不存在或已删除 |

**约束**：
- **幂等**：目标已是默认时直接返回 200，不重复写入；
- 与 `save`/`clear` 共用 `model-default` 护栏行串行化，保证任何时刻**默认项唯一**；
- 只改设置表指针，不触碰 `wb_model_configs` 与密钥，`metadata_revision` 不变。

## 5. 表单辅助填写（AI 结构化建议）

> 2026-09-21 新增（本体与项目辅助填写正式实现）。两个只读语义接口：**不写任何本体/项目修订，
> 不持全局写锁**；模型调用复用 §4 的提供方配置与 `workbench/llm_client.py` 公共 chat 层
> （原始 trace 不返回前端、不持久化、不写日志，仅返回耗时等元数据）。
> 字段白名单唯一来源：`workbench/assist_fields.py`（场景注册表）；前端协议镜像
> `frontend/src/assist/types.ts`。模型输出一律按不可信数据处理。

### 5.0 通用约定

- **场景 targetKind**（10 类，覆盖 O1～O5、P1～P6）：
  `object`／`property`／`sharedProperty`／`link`／`rule`／`action`（本体）；
  `identity`／`propertySource`／`linkMapping`／`actionBinding`（项目）。
  `propertySource` 按 `draft.kind` 分派子白名单：`field`／`database`／`redis`／`flow`。
- **编辑快照（draft）**：客户端提交的**受限字段白名单快照**（仅本场景可辅助字段；白名单外键
  一律 400）。原值展示由前端本地真实 draft 读取；模型返回内容不作为旧值来源。
- **contextToken**：HMAC 签名短令牌（进程内密钥，**不落库**），绑定
  用户＋space＋targetKind＋targetId＋权威状态指纹＋规范化 draft 摘要，TTL **600 秒**。
  跨用户/跨目标/篡改候选/过期一律 409 `CONTEXT_STALE`（客户端重新获取上下文）。
- **模式 mode**：`fill`（生成建议＋缺信息提问）｜`check`（检查当前内容，输出 issues）｜
  `explain`（解释怎么填，输出帮助文本，**无可执行 patch**）。
- **上限（冻结）**：`intent` ≤4000 字符；单问题回答 ≤2000 字符；questions ≤3；
  suggestions ≤12；issues ≤30；规范化 draft 序列化 ≤200KB；模型 `max_tokens` 4000；
  调用超时沿用提供方配置（1–300s）。超出即 400（draft/intent/answers）或按截断校验拒绝
  （模型输出超限 502）。
- **凭据红线**：认证头、API Key、连接密码、凭据明文永不进入 prompt/上下文/响应/日志；
  `actionBinding` 的 `auth.*` 字段不在任何白名单；请求文本中检测到疑似认证信息时脱敏处理
  （不保证识别所有用户主动提供的敏感文字，文档如实声明）。

### 5.1 获取辅助上下文

```http
POST /api/assist-context
```

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `space` | string | 是 | `ontology` \| `project` |
| `projectId` | string | space=project | 项目 id；项目区按当前账号可见范围校验 |
| `ontologyId` | string | 否 | 本体工作区 id（space=ontology 有效）；省略/空 = 默认工作区 `storage`；目标不可见或属他人一律 404 `NOT_FOUND`；签入 contextToken 并参与一致性校验 |
| `targetKind` | string | 是 | 见 §5.0 |
| `targetId` | string | 否 | 编辑目标稳定 id；新建（尚未保存）时为空 |
| `purpose` | string | 是 | `fill` \| `check` \| `explain` |
| `draft` | object | 是 | 编辑快照（白名单裁剪前原样提交即可，服务端裁剪） |

**响应** `200`

```json
{
  "contextToken": "…",
  "contextFingerprint": "sha256…",
  "context": {
    "targetKind": "propertySource",
    "title": "属性「SOC采样值」的取值来源",
    "editableFields": [ { "key": "flow", "label": "函数编排", "kind": "ref", "required": true,
                          "options": null, "group": null, "help": "…" } ],
    "definitions": [ { "kind": "flow", "id": "f_xxx", "label": "通用SOC采样查询", "hint": "输入…输出…" } ],
    "catalog": [ { "connection": "c_xxx", "table": "s_attr_scada",
                   "fields": [ { "name": "model_name", "comment": "模型名", "dataType": "varchar" } ] } ],
    "flows": [ { "id": "f_xxx", "name": "…", "inputs": […], "outputs": [ { "id": "…", "label": "…", "kind": "scalar" } ] } ],
    "modelReady": true
  }
}
```

- `definitions`/`catalog`/`flows` 按**当前账号可见范围**从权威存储重取并按场景裁剪
  （本体：当前草稿的对象/属性/链接摘要；项目：固定引用版本的定义＋目录缓存元数据＋编排签名；
  **只含元数据，绝不含业务记录、密码、认证头**）。目录缓存读取失败按既有 503
  `STORAGE_UNAVAILABLE` 口径，不降级为空目录。
- **候选截断显式标记**：候选按类裁剪（每类 ≤60 条、每表字段 ≤100 条），超出时响应携带
  `definitionsTruncated`/`catalogTruncated`/`flowsTruncated`（context 层）与
  `catalog[i].fieldsTruncated`、`flows[i].inputsTruncated/outputsTruncated/fieldsTruncated`
  （条目层，布尔）。被截断的候选不参与 generate 的引用核验，前端应提示"候选已截断"。
- `modelReady=false`：当前账号未配置默认模型——前端保留输入并提示前往模型设置；
  此时仍可获取上下文，但 generate 会 422。
- `targetId` 不可见/不存在（含跨账号）：404 `NOT_FOUND`。

### 5.2 生成建议／检查／解释

```http
POST /api/assist-generate
```

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `requestId` | string | 是 | 客户端生成的请求标识（uuid；响应回传用于日志关联） |
| `contextToken` | string | 是 | §5.1 返回的令牌 |
| `mode` | string | 是 | `fill` \| `check` \| `explain` |
| `draft` | object | 是 | **当前**编辑快照；规范化摘要须与令牌一致，否则 409 |
| `intent` | string | 否 | 用户意图/资料文字（≤4000 字符） |
| `answers` | object | 否 | 按 questionId 分别作答：`{"q1":{"value":"…"},"q2":{"unsure":true}}`；≤8 条，每条仅含 `value`（≤2000 字符）与 `unsure`（布尔） |

**响应** `200`（有效请求但模型无建议时 `status:"empty"`，禁止错误 200 假成功）

```json
{
  "requestId": "…",
  "status": "ok",
  "contextFingerprint": "…",
  "questions": [ { "id": "q_key_1", "prompt": "…", "kind": "text|choice",
                    "options": [ { "value": "…", "label": "…" } ], "allowUnsure": true } ],
  "suggestions": [ { "id": "s_1", "label": "业务定义", "fieldKeys": ["comment"],
                      "proposed": { "comment": "…" }, "state": "ready",
                      "reason": "…", "evidenceRefs": ["def:…", "answer:q_1"] } ],
  "issues": [ { "fieldKey": "path", "message": "…" } ],
  "explanation": null,
  "meta": { "durationMs": 1234, "provider": "本地模型", "model": "qwen" }
}
```

- **建议状态**：`ready`（可采纳）｜`pending`（依赖未确认的问题/待补充，禁选）｜
  `blocked`（引用不存在/类型不兼容/参数来源未确认等，禁选并给出原因）。
  待补充/禁选/未知字段不能被采纳修改；采纳由前端在本地 draft 合并后走既有保存链路。
- **建议结构约束（分层校验，2026-09-21 T4 冻结）**：
  * **整体结构违规 → 502 `MODEL_BAD_RESPONSE`**：输出无 JSON、顶层非对象或含未知顶层键、
    questions/suggestions/issues 形态非法、条数超限（>3/>12/>30）；
  * **单条违规 → 丢弃该条（响应外不体现，不计入错误）或转为 blocked**：fieldKeys 白名单外、
    proposed 键与 fieldKeys 不一致、值类型不符、未知枚举、超长、复合组形态非法 → 丢弃；
    ref 值不在候选集、复合组行内引用越界、formatting 历史样式 → blocked（原因可见）；
  * 全部建议被丢弃且无其他内容时按 `status:"empty"` 返回，不抛错；
  `fieldKeys` ⊆ 场景白名单；`proposed` 键 = fieldKeys 且值类型与字段种类一致；
  复合组（`params`/`inputs`/`lookup.match`/`parameters`/`formatting`）按各组 schema 整体校验；
  `ref` 类值必须存在于本次上下文候选集（幻觉 ID 拒绝）；`evidenceRefs` 只能引用实际发送给
  模型的候选（`def:<id>`/`catalog:<conn>:<table>`/`flow:<id>`/`answer:<qid>`）或问题 id，
  后端核验存在性与类型。`state` 由后端按业务规则裁定（模型声明的状态不采信）。
- **数据类型原子组**：属性 `dataType`+`obsType` 为一组（改为时间序列必须配套法观测类型，
  离开时间序列必须同步清空观测类型）；来源绑定相互依赖字段（如 flow 的 `output`+`inputs`）
  按组校验，禁止半组落入非法结构。
- **等值处理**：建议值与当前 draft 等值时不生成建议（或标 `ready` 但前端展示「无需变更」，
  以 §5.1 editableFields 的组语义为准）——旧值是否为空按类型/值判断，不用中文前缀。

**错误码（沿用既有错误信封 `{error, code}`）**

| 状态码 | code | 场景 |
| --- | --- | --- |
| 400 | `INVALID_ARGUMENT` | 请求形态非法／draft 白名单外字段／intent·answers 超限／未知枚举 |
| 401 | `UNAUTHENTICATED` | 未登录（既有门禁） |
| 404 | `NOT_FOUND` | 项目/目标不可见或不存在（含跨账号按不存在） |
| 409 | `CONTEXT_STALE` | 令牌过期/无效、目标或权威状态已变、draft 摘要不匹配（客户端重取 §5.1） |
| 422 | `MODEL_NOT_CONFIGURED` | 当前账号未配置可用模型提供方（前端引导前往模型设置） |
| 422 | `ASSIST_INVALID_STATE` | 目标状态不满足（如 propertySource 缺 kind） |
| 502 | `MODEL_BAD_RESPONSE` | 模型输出无 JSON/结构非法/全部建议越界/输出超限 |
| 504 | `MODEL_TIMEOUT` | 模型调用超时 |

- 生成接口**不持有全局写锁**，不写任何修订；返回前后 revision 与数据库内容不变。
- 无建议但请求有效 → 200 + `status:"empty"`（`questions`/`suggestions`/`issues` 均可非空为空数组）。
- 日志只留脱敏错误码、时长、requestId；不落 prompt、模型原文与 trace。

## 6. 整表自动填写（autofill/1，2026-09-22 改版冻结）

> T0 协议冻结（需求《20260922_整表自动填写交互》）。`mode=fill` 从"建议卡＋逐项勾选"
> 升级为**受限字段操作协议**：模型输出 operations，后端按契约白名单验证，前端核对指纹后
> 一次原子回填当前草稿。**旧 suggestions 勾选交互随之移除**；`check`／`explain` 响应结构
> 不变（§5.2），降为面板次要帮助入口。本节与代码不一致时按文档 bug 处理（§红线 5）。

### 6.0 端点与兼容

| 项 | 冻结值 |
|---|---|
| 端点 | 沿用 `POST /api/assist-context`、`POST /api/assist-generate`（无新增白名单） |
| fill 请求 | **必须**携带 `"protocol": 2`；缺失或非 2 → 400 `INVALID_ARGUMENT`（不静默按旧协议处理） |
| fill 响应 | `protocol: "autofill/1"`；`check`/`explain` 响应仍为 §5.2 suggestions 结构 |
| formId | 与 §5.0 targetKind 一一对应（10 个）；`propertySource` 按 draft.kind 分派子契约 |

### 6.1 表单契约（单一来源，T1 落地）

- 目录 `contracts/forms/<formId>.json`，开发期唯一字段定义来源；后端装载
  （`workbench/assist_forms.py`），前端元数据由生成命令产出
  （`python3 -m workbench.assist_forms_gen` → `frontend/src/assist/formContracts.gen.ts`，
  生成物入库、禁止手改）。运行时**不接受客户端提交 schema**。
- 文件语法（冻结）：

```json
{
  "formId": "ontology.property",
  "schemaVersion": 1,
  "title": "属性定义",
  "fields": [
    {"id": "label", "label": "属性名称", "type": "text", "required": true, "maxLength": 120,
     "ai": {"fillable": true, "clearable": false}},
    {"id": "dataType", "type": "group", "atomicGroup": "typeCore", "fields": [
      {"id": "type", "type": "enum", "enum": ["string", "double", "timeSeries"]},
      {"id": "valueType", "type": "enum", "enum": ["double"],
       "visibleWhen": {"field": "dataType.type", "op": "eq", "value": "timeSeries"},
       "requires": "dataType.type"}]}
  ],
  "lists": [
    {"id": "lookupMatch", "rowIdScope": "local", "item": {"id": "left", "type": "text"}}
  ],
  "codecs": ["dataTypeTransform", "formattingCodec"],
  "refProviders": {"table": "catalogTables", "field": "catalogFields"}
}
```

- 字段 `type` 冻结枚举：`text`｜`textarea`｜`enum`｜`boolean`｜`ref`｜`group`（嵌套固定结构）｜
  `list`（顶层 lists 声明，行有本地稳定 rowId）。
- 约束键：`required`／`maxLength`／`enum`／`nullable`（**仅显式 true 时 clear 合法**）；
  `visibleWhen`/`editableWhen` 受限表达 `{field, op: eq|ne|in|notEmpty, value}`，**不是 eval**；
  `atomicGroup` 同名者必须整组生效；`requires` 声明依赖字段（如 valueType 依赖 type）。
- `ai` 键：`fillable`（默认 true；false = 系统派生/只读，模型不可写）、`clearable`、
  `sensitive`（true = 不进 prompt、不可写）。`codec` 引用已注册业务 setter 标识
  （JSON-LD 类型转换、Redis 编码、参数行等），**不携带可执行代码**。
- `schemaDigest`：对文件做 canonical JSON（键排序、去 digest 字段）后 SHA-256，由 loader
  重算并填入；响应回传 `schemaVersion`+`schemaDigest`，前端与本地生成物比对，不一致即
  CONTEXT_STALE 语义（重新取上下文）。
- 契约演进：布局/文案改动不改 `schemaVersion`；字段增删、类型、枚举、权限、依赖变化必须
  `schemaVersion+1` 并使全部在途会话失效（digest 变 → 409 CONTEXT_STALE）。
- 契约一致性守护：`tests/test_autofill_contracts.py`（T1）——契约↔`assist_fields` 注册表
  ↔前端生成物三方对齐；新增普通字段未入契约时构建期报错。

### 6.2 fill 请求（assist-generate，mode=fill）

在 §5.2 请求基础上新增（全部必填除非注明）：

| 字段 | 说明 |
|---|---|
| `protocol` | 常量 `2` |
| `sessionId` | 首轮不传（服务端签发）；续轮必带回传 |
| `answers` | `[{"questionId", "value", "unsure"}]`（value ≤2000 字符，沿用 §5 上限）；questionId 必须属于本会话未答复问题 |
| `draft` | **应用前序操作后的当前草稿**（续轮必为最新；首轮为打开面板时的草稿） |

续轮握手（冻结）：应用操作→draft 变化→**必须重新 `assist-context` 取新 contextToken**→
携带新 token＋`sessionId`＋`answers` 再 `assist-generate`。旧 token 提交新 draft → 409
`CONTEXT_STALE`（既有语义）。

### 6.3 fill 响应（200）

```json
{"protocol": "autofill/1", "status": "ok",
 "requestId": "…", "formId": "propertySource", "schemaVersion": 1, "schemaDigest": "…",
 "target": {"space": "project", "targetKind": "propertySource", "targetId": "…"},
 "draftFingerprint": "…", "contextFingerprint": "…",
 "sessionId": "s_…", "roundId": "r_…",
 "operations": [
   {"op": "set", "field": "connection", "value": "conn-01",
    "basis": {"kind": "intent", "quote": "用创智园业务库"}},
   {"op": "row.append", "field": "lookupMatch", "row": {"localId": "r1", "fields": {"left": "id"}}},
   {"op": "row.update", "field": "params", "rowId": "p_3", "fields": {"from": "identityField"}},
   {"op": "clear", "field": "note", "basis": {"kind": "question", "questionId": "q_ab"}}],
 "questions": [{"id": "q_ab", "text": "取值字段用 capacity 还是 rated_power？",
                "fields": ["result.valueField"], "options": ["capacity", "rated_power"],
                "allowUnsure": true}],
 "unresolved": [{"field": "table", "reason": "候选目录中没有该表；请刷新目录或人工选择"}],
 "summary": "已生成 4 项变更，1 项待补充"}
```

- **operations 语义**：`set`（field+value）、`clear`（仅 nullable+ai.clearable 字段，用户明确
  要求才允许）、`row.append`（row.localId 客户端本地生成、服务端按内容去重）、
  `row.update`／`row.remove`（rowId 必须命中草稿现有行；无法定位 → 模型应改为补问，
  猜测行号一律拒）。`field` 是**契约内的点路径**（如 `result.valueField`），不提供任意
  JSON 路径、脚本或持久 ID。
- **basis（依据）**：`{"kind": "intent", "quote": "原话片段"}` 或
  `{"kind": "question", "questionId"}`；后端程序校验 quote 确实出现在本次 intent（或本会话
  已答内容）中、questionId 属于本会话——**防模型自报授权**。校验失败该操作转 unresolved。
- **冲突拒绝**：同字段多条写操作 → 全部转 unresolved（不后项覆盖）；原子组不完整 → 组内
  全部转 unresolved；引用不存在（对象/连接/表/字段/编排/输出）→ 该操作转 unresolved。
- **分层不变**：响应整体结构违规仍 502 `MODEL_BAD_RESPONSE`；单操作违规转 `unresolved`
  （前端展示原因），独立合法组照常返回；全部无效且无问题 → `status:"empty"`。

### 6.4 会话与问题归属

- 会话**内存态**（不落库）：TTL 30 分钟，LRU ≤200/进程；绑定 user＋space＋target＋formId
  ＋schemaDigest。目标/契约变化即失效。
- `questionId` 服务端生成、归属 session+round；已答问题不可重复作答；**旧 round 的
  questionId 在新 round 提交 → 400 `INVALID_ARGUMENT`（问题已过期，请重新发起）**。
- `unsure: true` 的答案按"暂不确定"处理：关联字段转 unresolved，不生成默认口径。

### 6.5 限额（冻结，超出 400 或 502 按既有分层）

intent ≤4000；answers ≤8 条/轮且单条 ≤2000；questions ≤3；**operations ≤12**；
**unresolved ≤12**；draft ≤200KB；模型 `max_tokens` 4000；会话 TTL 30min／LRU 200；
超时沿用提供方配置（1–300s）。模型输出超限按 §5.2 截断校验拒绝（502）。

### 6.6 前端行为契约（T3 实现基准）

- 默认不渲染任何 AI 区域；页头次要按钮「✦ 自动填写」开 420px 右侧抽屉（窄屏带遮罩）。
- 生成成功且无待补：一次回填→抽屉收起→表单上方状态条「已填写 N 项，尚未保存」＋
  「撤销本次填写」＋「查看修改」（逐字段 旧值→新值）；N＝实际改变的顶层字段/复合组数。
- 回填**只改本地 draft**：不触发 touch/changed 自动保存、不调 form-save/commit-now；
  等待超过自动保存窗口（900ms×N）数据库与 revision 不变。
- 撤销单元＝一次会话（首轮＋全部续轮）：期间无手改可整轮恢复；有手改禁用整轮撤销并说明。
  「取消修改」仍按原页面语义恢复已保存内容。
- 生成中手改/切目标/关抽屉/契约变化 → 作废在途请求，迟到响应零写入（请求代际计数）。
- check/explain 为抽屉内次要入口，只读展示（issues/explanation），不提供任何写入。

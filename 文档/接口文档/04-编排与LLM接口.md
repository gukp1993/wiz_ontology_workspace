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
               "timeout": 60, "temperature": 0, "isDefault": true, "keyConfigured": true } ] }
```

> 默认项排最前，其余按名称排序；`api_key` **永不出现**。

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
| `isDefault` | boolean | 否 | 设为默认；**首个提供方自动成为默认**。列表页的「设为默认」不经过本接口，走 `POST /api/llm-provider-default`（§4.5） |

**响应** `200`

```json
{ "saved": true, "provider": { "id": "llm-xxxxx", "name": "本地模型", "model": "qwen",
                               "isDefault": true, "keyConfigured": true } }
```

| 状态码 | 场景 |
| --- | --- |
| 400 | 参数校验失败（名称/地址/模型/超时/温度/密钥） |

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
| 按临时配置（不落盘） | `{ "name", "endpoint", "model", "apiKey", "timeout"(默认30), "temperature"(默认0) }` |

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

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
| `revision` | string | 全图运行时必填 | 并发基线；单节点/链测试不需要 |
| `targets` | string[] | 否 | 节点 ID 列表。**省略 = 全图运行**；指定 = 单节点/链测试（不落盘、无 revision 要求） |
| `inputs` | object | 否 | 入口参数 |
| `projectId` | string | 否 | 项目上下文（数据连接 + API 凭据，密钥不在此读取） |
| `connections` | array | 否 | 连接元数据（校验引用） |

**响应** `200`

```json
{
  "status": "success",                 // success | error
  "nodeResults": [ { "nodeId": "n1", "status": "success", "outputs": {}, "outputNames": {},
                     "rowCount": 12, "durationMs": 45, "logs": [] } ],
  "startedAt": "2026-09-18T05:00:00+00:00",
  "durationMs": 320
}
```

节点结果 `status`：`success` / `failed`（带 `error`）/ `skipped`（上游失败）。

| 状态码 | 场景 |
| --- | --- |
| 400 | state 非法 / targets 无效 / 被测链路非法 |
| 404 | 编排不存在（全图运行）/ 项目不存在 |
| 409 | revision 过期（带 `currentRevision`）；或该编排正在运行中（进程内互斥） |
| 422 | 配置检查未通过（响应体 `{"error": "...", "check": <CheckReport>}`） |

**约束（重要）**：
- 仅**短暂持锁**核对 revision，**执行阶段绝不持有全局写锁**；
- 全图运行有进程内 per-flow 互斥，同时只允许一个运行；
- 执行**不落盘**；节点超时上限 300 s，SQL 行数上限 10000；
- Redis 仅执行白名单命令（见 `workbench/flows.py` 的 `REDIS_COMMANDS`）。

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
| `isDefault` | boolean | 否 | 设为默认（首个提供方自动成为默认） |

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

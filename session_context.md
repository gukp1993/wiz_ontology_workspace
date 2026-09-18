# Codex / zcode 共享上下文

上下文版本：`dcbdad81c4638137`

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

## 最近交接（新 → 旧）

### 共享上下文接入 · zcode · 已实施，待验收

时间：2026-09-18T07:43:19.905634+00:00；记录：`.collaboration/entries/000002-5c8fe20346b4.json`

zcode 已接入项目 Hook：新建 .zcode/config.json（hooks.enabled=true，SessionStart/UserPromptSubmit/Stop 三个事件调用 context.py hook --actor zcode，timeout 10s）。经 zcode 官方配置指南核实：事件恰为七种、含所需三种；工作区配置式 Hook 无信任门（区别于 Codex 需 /hooks 审查），enabled 即生效。临时根六项协议模拟全过：additionalContext 注入含 ticket、Stop 未交接首块二提（防循环）、交接后空输出放行、缺 session/turn 降级仍可注入、仓库外 cwd 空转。当前会话按规则主动交接；真实客户端事件触发留待下一轮验证。

- 决定：zcode 采用工作区 .zcode/config.json 配置式 Hook，actor=zcode 与 Codex 配置互不影响；未改共用脚本与存储格式
- 验证：python3 tests/test_context_sync.py：7 项通过；zcode 官方 zcode-configuration-guide/diagnosing-hooks skill 核实事件与输出协议（additionalContext 注入、Stop 可请求续跑、输出 JSON 严格校验）；临时根模拟 6 项：UserPromptSubmit 注入、Stop 首块/次提/交接后放行、降级、仓库外空转；本会话真实 read→record 完成（本轮 ticket）；手动模拟均用 --root 临时目录，未污染真实事件计数；未验证：真实客户端事件触发（配置为本轮新建，热重载未知），下一轮观察注入上下文与 .runtime/context-hooks.json 计数
- 下一步：下一轮核对真实触发：AI 可读到注入上下文、Stop 核对生效；若 zcode payload 字段名与 snake_case（session_id/turn_id/hook_event_name）不符，仅写薄字段适配器，不改共用存储；若本会话未热加载，重启会话后生效
- 依据/文档：.zcode/config.json；.collaboration/context.py；文档/需求/20260918_共享上下文自动交接/zcode接入指令.md；文档/需求/20260918_共享上下文自动交接/使用说明.md

### 共享上下文自动交接-v1 · codex · 已实施，待验收

时间：2026-09-18T07:30:34.427985+00:00；记录：`.collaboration/entries/000001-9b2570bcc1d3.json`

共用交接脚本、Codex 项目级 Hooks、AGENTS 规则及 zcode 接入指令已落地。当前会话主动交接可用；Codex 新 Hook 尚需用户信任并验证真实事件，zcode 尚未接入，不能声称两端已经全自动。

- 决定：以每轮和阶段交付为同步时机，不等待关闭会话；Codex 与 zcode 分别追加交接，由共用脚本加锁汇总，不直接覆盖 session_context.md；不调用外部模型，不扫描原始聊天或写入业务数据库
- 验证：python3 tests/test_context_sync.py：7 项通过；含隔离并发、去重、重建、防循环、常见密钥拒绝；.codex/hooks.json JSON 解析通过；本机 CLI hooks feature 开启，项目受信任；未完成真实客户端 Hook 信任与自动触发验证；未进行工作台业务回归
- 下一步：用户在 Codex /hooks 审查并信任本项目 Hook，然后在下一轮验证实际触发；zcode 按接入指令核实自身事件能力并配置；无 Hook 则先使用规则驱动的主动交接；当前其他工具仍在改项目映射，后续验收需重新核对代码和其交接
- 依据/文档：文档/需求/20260918_共享上下文自动交接/使用说明.md；文档/需求/20260918_共享上下文自动交接/zcode接入指令.md；.codex/hooks.json；.collaboration/context.py；tests/test_context_sync.py

# 储能本体工作台（wiz_ontology_workspace）

面向业务专家与开发/实施的本地本体建模工作台：本体建设（对象/链接/属性/契约/共享属性库）与项目映射（数据连接/对象映射/计算实现）两条独立管道，语义参考 Palantir Foundry 本体但为自有格式。仅绑定 127.0.0.1，不部署生产服务。

架构与行为规范见 [文档/通用储能本体工作台设计方案_v3.md](文档/通用储能本体工作台设计方案_v3.md)；2026-09-15 架构优化（模块化、保存可靠性、目录收敛）见 [文档/架构优化实施说明_20260915.md](文档/架构优化实施说明_20260915.md)；开发约束见 [AGENTS.md](AGENTS.md)，最新状态见 [session_context.md](session_context.md)。

## 精简交付与历史资料

- 2026-09-15 已按用户要求删除 `待删除_非运行资料/`、空 `tools/` 和旧兼容 `service/`；`outputs/`、`发布包/`、`resources/`、迁移备份目录也已先后按用户指令删除。
- 真实 `ontology/` 数据原位保留（已退出 git 跟踪，见下节）。
- 历史整理清单见 [生产交付与文件整理方案](文档/历史归档_20260916前/生产交付与文件整理方案.md)（早期资料）。

## 启动与管理

需要 Python 3.9+（PyYAML、rdflib；可选 PyMySQL、redis）和 Node.js/npm。

```bash
./start.sh setup     # 显式安装依赖（pip 检查 + npm install，并记录依赖戳记）
./start.sh start     # 启动（http://127.0.0.1:18765/，自动开浏览器；8765 已保留给其他服务，绝不使用）
./start.sh rebuild   # 前端构建成功后原子切换并重启；依赖未变时跳过 npm install；构建失败不影响运行中服务
./start.sh status|stop|restart
cd frontend && npm run dev   # 开发模式（Vite 热更新，/api 代理到 18765）
```

改前端后必须 `npm run build`（后端只托管 dist）；纯后端改动直接重启即可。旧 `python3 -m service.demo` 命令已移除；工作台内部仍保留 `workbench/demo` 所需实现。

## 数据与保存（重要）

- 在线权威存储为 SQLite（`data/workbench.sqlite3`，目录 0700；根密钥在 `keys/`），草稿/发布/凭据全部入库，2026-09-18 迁移完成。服务启动与请求路径不读写文件回退；初始化/迁移/备份走 `python3 -m workbench.storage.transfer` CLI。
- `ontology/` 下为用户真实数据（旧文件存储时代的迁移输入与备份），**不要手改、清理或写入测试样例**；2026-09-21 已整体退出 git 跟踪（`.gitignore` 忽略 `/ontology/`），本地文件保留作迁移备份，新环境需要旧数据时从 git 历史找回。
- `ontology/vault/` 为连接密码受保护存储（0600，在草稿/快照之外，密码永不回传）。

表单保存即持久化草稿（无独立"保存草稿"按钮）；发布独立执行且需通过配置校验。配置校验通过 ≠ 已执行验证。

## 目录

| 目录 | 内容 |
| --- | --- |
| `frontend/src/` | Vue3+TS 单页应用：`app/`（请求/导航/保存协调）、`ontology/`（本体页+协议层）、`project/`（项目页+绑定适配）、`shared/`（通用控件）、`tools/`（辅助浏览页） |
| `workbench/` | Python 标准库后端：`server.py`（安全边界+路由分派）、`{model,project}_routes.py`（接口处理）、`projects.py`+`project_validation.py`+`project_mapping.py`（存储/校验/共享辅助）、`workspaces/versions/contracts/model_format/properties/workflow/formatting/value_types`、`dbdrivers/catalogs/secrets`（数据连接）、`locking.py`、`paths.py`（CODE_ROOT/DATA_ROOT）、`demo/`（演示执行器） |
| `ontology/` | 全部用户数据（见上节；已退出 git 跟踪，本地保留迁移备份） |
| `tests/` | 自动化回归（Python 套件 + Node 保存队列测试 + 校验金样），一律用 `WIZ_WORKBENCH_ROOT=<临时目录>` + 独立端口 |
| `文档/` | 设计方案与迁移记录在根；`需求/` 各期需求四件套归档；`交付物/` 实施说明；`接口文档/` 前后端契约；`评审与纪要/`、`概念与说明/` 专题文档；`历史归档_20260916前/` 早期资料；`导出/` 导入测试物料 |
| `.runtime/` | 当前进程、日志、依赖戳记和临时构建；旧回退快照已删除 |

## 测试

```bash
# 后端（各自隔离临时根；带端口的套件会启动并行实例）
python3 tests/test_value_shape.py
python3 tests/test_property_sources.py
WIZ_WORKBENCH_PORT=18870 python3 tests/test_project_api_roundtrip.py   # 需配合 WIZ_WORKBENCH_ROOT
WIZ_WORKBENCH_PORT=18871 python3 tests/test_save_iteration.py
python3 tests/test_paths_isolation.py                                  # 路径/演示隔离
python3 tests/test_validation_split.py                                 # 项目校验金样等价
node --import ./tests/ts_hooks.mjs tests/save_queue.test.mjs           # 前端保存队列状态机
cd frontend && npm run build                                           # 前端交付构建
```

服务固定 18765（WIZ_WORKBENCH_PORT 可覆盖为其他端口，但 8765 被脚本与服务端双重拒绝）。绝不直接对 18765 实例做写入式自动化验收。

# 储能本体工作台（wiz_kq_builder_v2）

面向业务专家与开发/实施的本地本体建模工作台：本体建设（对象/链接/属性/契约/共享属性库）与项目映射（数据连接/对象映射/计算实现）两条独立管道，语义参考 Palantir Foundry 本体但为自有格式。仅绑定 127.0.0.1，不部署生产服务。

架构与行为规范见 [文档/通用储能本体工作台设计方案_v3.md](文档/通用储能本体工作台设计方案_v3.md)；2026-09-15 架构优化（模块化、保存可靠性、目录收敛）见 [文档/架构优化实施说明_20260915.md](文档/架构优化实施说明_20260915.md)；开发约束见 [AGENTS.md](AGENTS.md)，最新状态见 [session_context.md](session_context.md)。

## 精简交付与历史资料

- `发布包/`：已构建的精简运行目录，见其中的 `运行说明.md`。
- 2026-09-15 已按用户要求删除 `待删除_非运行资料/`、空 `tools/` 和旧兼容 `service/`；`outputs/` 也已不存在。
- 前端源码、测试和当前规范留在开发工程，不进入运行包；真实 `ontology/` 未移动。
- 详细清单见 [生产交付与文件整理方案](文档/生产交付与文件整理方案.md)。

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

`ontology/` 下全部为用户真实数据，**不要手改、清理或写入测试样例**：

- 编辑只写 `ontology/drafts/{models,projects}/<id>/revisions/`（多文件修订 + current.json 原子指针）；
- 发布写 `ontology/releases/{models,projects}/`（不可变版本，本体版本带变更分类）；
- `ontology/models/`、`ontology/projects/<id>/` 基础目录仅作初始化输入，代码永不回写；
- `ontology/vault/` 为连接密码受保护存储（0600，在草稿/快照之外，密码永不回传）；
- `ontology/catalogs/` 为服务端表结构目录缓存（大目录不进草稿请求与快照）。

表单保存即持久化草稿（无独立"保存草稿"按钮）；发布独立执行且需通过配置校验。配置校验通过 ≠ 已执行验证。

## 目录

| 目录 | 内容 |
| --- | --- |
| `frontend/src/` | Vue3+TS 单页应用：`app/`（请求/导航/保存协调）、`ontology/`（本体页+协议层）、`project/`（项目页+绑定适配）、`shared/`（通用控件）、`tools/`（辅助浏览页） |
| `workbench/` | Python 标准库后端：`server.py`（安全边界+路由分派）、`{model,project}_routes.py`（接口处理）、`projects.py`+`project_validation.py`+`project_mapping.py`（存储/校验/共享辅助）、`workspaces/versions/contracts/model_format/properties/workflow/formatting/value_types`、`dbdrivers/catalogs/secrets`（数据连接）、`locking.py`、`paths.py`（CODE_ROOT/DATA_ROOT）、`demo/`（演示执行器） |
| `ontology/` | 全部用户数据（见上节），原位不动 |
| `tests/` | 自动化回归（Python 套件 + Node 保存队列测试 + 校验金样），一律用 `WIZ_WORKBENCH_ROOT=<临时目录>` + 独立端口 |
| `resources/` | 仅保留来源参考接口仍读取的原始 source.jsonId；其他历史资料已按用户要求删除 |
| `文档/` | 规范与方案；`文档/交付物/` 各阶段实施说明与指令；`文档/prototypes/` 交互原型；`文档/归档/` 早期资料 |
| `发布包/` | 精简程序交付目录，不附带真实数据和密码 |
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

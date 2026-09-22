---
name: ontology-workbench-ui-fix
description: 本地本体建模工作台的视觉与交互契约。唯一取色入口是 frontend/src/style.css 的 :root，本文件把它与语义类、共享控件、交互态和登记例外写清楚，供实现与验收对齐。
colors:
  ink: "#182c3e"
  ink-secondary: "#3d5063"
  muted: "#5b6d7e"
  faint: "#8394a5"
  canvas: "#f6f8fa"
  surface: "#ffffff"
  surface-secondary: "#f4f7fa"
  surface-tertiary: "#eef2f6"
  surface-float: "#fffffff0"
  border: "#dce4eb"
  border-strong: "#c9d5e0"
  grid: "#d9e2ea"
  accent: "#245cdf"
  accent-hover: "#1a49b0"
  accent-text: "#234fa8"
  accent-soft: "#edf3ff"
  accent-border: "#b8cff8"
  success: "#1f7a55"
  success-soft: "#eefaf3"
  success-border: "#cdebd8"
  warning: "#8a6116"
  warning-soft: "#fff8e8"
  warning-border: "#f0dfb2"
  danger: "#b03a3a"
  danger-hover: "#8f2f2f"
  danger-soft: "#fdf0f0"
  danger-border: "#f2cfcf"
  focus-ring: "#245cdf"
  scrim: "#10283d66"
  white: "#ffffff"
typography:
  display-lg:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif"
    fontSize: 48px
    fontWeight: 650
    lineHeight: 1.1
    letterSpacing: -2px
  metric-md:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif"
    fontSize: 22px
    fontWeight: 700
    lineHeight: 1.3
  heading-lg:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif"
    fontSize: 17px
    fontWeight: 650
    lineHeight: 1.4
  heading-md:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif"
    fontSize: 15px
    fontWeight: 600
    lineHeight: 1.5
  body-md:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.6
  body-sm:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.6
  caption:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif"
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.5
  micro-label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif"
    fontSize: 11px
    fontWeight: 650
    lineHeight: 1.4
    letterSpacing: 1px
  button-md:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif"
    fontSize: 14px
    fontWeight: 500
    lineHeight: 1.5
rounded:
  sm: 6px
  md: 10px
  lg: 14px
  pill: 999px
  full: 50%
spacing:
  xxs: 2px
  xs: 3px
  sm: 5px
  md: 8px
  lg: 10px
  xl: 12px
  xxl: 14px
  huge: 16px
  gutter: 20px
  section: 22px
  block: 24px
components:
  button-primary:
    backgroundColor: "#245cdf"
    textColor: "#ffffff"
    rounded: 6px
    padding: "6px 13px"
    height: 34px
    typography: button-md
  button-primary-hover:
    backgroundColor: "#1a49b0"
    textColor: "#ffffff"
  button-secondary:
    backgroundColor: "#ffffff"
    textColor: "#182c3e"
    rounded: 6px
    padding: "6px 13px"
  button-secondary-hover:
    backgroundColor: "#ffffff"
    textColor: "#245cdf"
  button-disabled:
    backgroundColor: "#ffffff"
    textColor: "#182c3e"
    opacity: 0.5
  button-danger:
    backgroundColor: "#ffffff"
    textColor: "#b03a3a"
    borderColor: "#f2cfcf"
    rounded: 6px
    padding: "6px 13px"
  button-danger-hover:
    backgroundColor: "#fdf0f0"
    textColor: "#b03a3a"
    borderColor: "#b03a3a"
  text-input:
    backgroundColor: "#ffffff"
    textColor: "#182c3e"
    rounded: 6px
    padding: "9px 10px"
    height: 40px
    width: "100%"
  text-input-focused:
    backgroundColor: "#ffffff"
    textColor: "#182c3e"
    height: 40px
  card:
    backgroundColor: "#ffffff"
    textColor: "#182c3e"
    rounded: 10px
    padding: "20px"
  dialog:
    backgroundColor: "#ffffff"
    textColor: "#182c3e"
    rounded: 14px
    padding: "26px"
  table-row-selected:
    backgroundColor: "#edf3ff"
    textColor: "#234fa8"
    height: 64px
  list-row-active:
    backgroundColor: "#edf3ff"
    textColor: "#234fa8"
  badge-neutral:
    backgroundColor: "#edf3ff"
    textColor: "#234fa8"
    rounded: 999px
    padding: "3px 11px"
---
## Overview

这是**浅色、密排、表单驱动的专业建模工具**，不是营销页面。设计目标是让业务/建模人员在长会话里连续读表、填表、改定义而不疲劳，因此：

- **信息密度优先**：基准字号 14px，说明与元信息 12–13px，表格一屏能看更多行；靠分隔线与留白分组，不靠大卡片阴影堆叠层级。
- **克制配色**：一个主色（`--blue` #245cdf）承担品牌、选中、链接与焦点；绿/琥珀/红只表达校验与状态语义。除图形画布外不引入新的装饰色。
- **零花哨动效**：统一 `--dur` .12s + `--ease` `cubic-bezier(.2,.6,.3,1)`；只在状态变化处给反馈，入场动画与视差一律排除，并尊重 `prefers-reduced-motion`。
- **唯一取色入口**：`frontend/src/style.css` 的 `:root` 是 **CSS 令牌**唯一定义处（37 个，main 也是 37 个：删零引用死令牌 `--ok-bright`、增 `--paper-float`，**其余值一个都没改**）。新增颜色必须先进 `:root` 再由样式引用；组件里写十六进制色视为债务。唯一例外是图形渲染参数：`shared/graphStyle.ts` 的画布调色板（`FLOW_CATEGORY` / `FLOW_STATE` / `KNOWLEDGE_KIND_FILL` / `KNOWLEDGE_CANVAS` / `CANVAS_WHITE` 等）不是 CSS 令牌而是 cytoscape/SVG 的数据可视编码，见「登记例外」第 2、3 条。
- **不改令牌值**：本契约的令牌值即当前线上视觉。任何"统一"都是把散落的漂移色收回到既有令牌，**不得为了统一而修改令牌本身**。

覆盖范围：本体区（工作概览、对象建模、共享属性库、校验与发布）、项目区、编排页、"更多工具"辅助页、登录与设置。

## Colors

令牌分四组，语义角色固定：

| 角色 | 令牌 | 值 | 用途 |
|---|---|---|---|
| 文本 | `--ink` / `--ink-2` / `--muted` / `--faint` | #182c3e / #3d5063 / #5b6d7e / #8394a5 | 正文 / 次级正文 / 说明文字 / **仅装饰性浅字** |
| 面 | `--bg` / `--paper` / `--paper-2` / `--paper-3` / `--paper-float` | #f6f8fa / #ffffff / #f4f7fa / #eef2f6 / #fffffff0 | 页底 / 卡片输入 / 表头与只读底 / 内联代码底 / 画布上浮层与 hint 的近透明白底 |
| 线 | `--line` / `--line-2` / `--grid` | #dce4eb / #c9d5e0 / #d9e2ea | 分隔 hairline / 表单描边 / 画布网点 |
| 主色 | `--blue` / `--blue-deep` / `--blue-ink` / `--blue-soft` / `--blue-line` | #245cdf / #1a49b0 / #234fa8 / #edf3ff / #b8cff8 | 动作与选中 / hover 加深 / 选中态文字 / 选中底 / 选中描边 |
| 语义 | `--ok` / `--warn` / `--danger` 各带 `-soft` `-line`（`--danger` 另有 `-deep`） | 见 frontmatter | 校验通过 / 需复核 / 阻断；soft 是底，line 是框 |
| 焦点与遮罩 | `--focus` / `--backdrop` | #245cdf / #10283d66 | 键盘焦点环 / 对话框遮罩 |

规则：

1. **同一语义只有一个色**。UI 里出现过的 #2458d5、#346fe1、#3478d6、#4162d6、#4080d8、#498ae4、#123f82、#2c4a86、#2a6aca、#286cca、#2e5b96、#315f9d、#3566a8、#2c64b1、#2e67b1、#1c62bb、#1d60bd、#377bdd、#236bc7、#6798da、#a8c2e9、#89aad6、#5275aa（`.eyebrow` 的 scoped 副本→`--muted`）、#ccd8e6（scoped `textarea` 描边→`--line-2`）、#bd4c5a（`tools/InstanceCharts` 负值条内联色→`--danger`）、#789（两处空态文案→`--muted`）、#a2b4c8（`tools/InstanceCharts` 坐标轴 `stroke=` 呈现属性→`--faint`）、#f3f7fc（`tools/InstanceMap` 绘图区 `fill=`→`--paper-2`）、#c8d8eb（`tools/InstanceMap` 绘图区 `stroke=`→`--line-2`）、#d8e3f0（`tools/InstanceMap` 网格线 `stroke=`→`--line`）等都是**漂移副本**，一律收敛到角色最贴近的既有令牌，不新增令牌。其中 `#a2b4c8`/`#f3f7fc`/`#c8d8eb`/`#d8e3f0` 四项来自 SVG **呈现属性**，逐实例的改前/改后决议值在开发计划 §9.5 附表 C-2——这类改动**必然改变渲染值**，不属于"值中性收敛"。**必须点名的一个例外**：`#123f82` 在 HEAD 仍是 `shared/graphStyle.ts:187` 里 `INSTANCE_GRAPH.focused` 的字面量（本轮只是从 `tools/InstanceGraph.vue:67` 的内联数组**搬进**常量表，值一字未动），属本条末尾豁免的画布分类调色板，**没有**收敛到任何令牌；它出现在上面那份清单里只表示"它曾是画布侧的漂移副本"，不得读成已并档。**按角色选、不按色值最近邻选**：`--blue` 是实心动作/焦点/选中描边色，`--blue-ink` 是选中态与链接的**文字**色（实测白底 `--blue` 5.41:1、`--blue-ink` 7.22:1，两者都过 AA，选 `-ink` 的理由是**与 main 既有 `.ont-filters button.active`、`.property-choice.active` 的分工一致**，不是可达性补救）。此规则**不适用** `shared/graphStyle.ts` 的分类调色板（#3978c5、#2563b9 等在那里是节点分类编码，例外 3）与 `ontology/legacyGraph/**`（独立令牌体系，例外 1）。
2. 实测对比度（对 `--paper` #ffffff）：`--muted` #5b6d7e = **5.34:1**（过 AA 正文）、`--faint` #8394a5 = **3.11:1**（不过）。`--faint` 不得用于**新增**的承载信息文字，只能用于装饰图形（图标底、chevron、箭头 `→`、清除叉）与已有等效文本的重复处。本轮把两处 3.64:1（`#789`=`#778899`，实测 L=0.2383）的空态文案提到 `--muted`：`tools/InstanceGraph.vue` `.graph-empty`、`tools/KnowledgeCanvas.vue` `.kc-empty`（main 均为 `#789`）。
   **存量偏差登记，本轮不改值**（main 既有行为，逐处都需判断该文案是否承载信息，属另一次视觉决策）。实测全仓 `(color|fill):var(--faint)` 声明 **27 处**（main 为 10 处全局 + 组件内散布；核对正则必须容忍冒号后空格）。其中 **9 处属规则 2 允许清单的装饰图形/分隔符**（`.step-title::after` 与 `.ftw-path span::after` 的箭头与分隔符、`.app-select-chevron`、`.app-select-search`、`.app-select-clear`、`.ld-row-arrow`、`.empty-state-ico`、`.ont-empty-ico`、`.empty-icon`），**其余 18 处为存量次级文字**（`.rail-hint`、`.user-menu-note`、`.settings-cat`、`.ftw-meta`、`.app-select-empty`、`.app-select-trigger.is-placeholder`、`.imp-limits`、`.conn-secret summary span`、`.optional-label`、`.source-reference summary span`、`.area-entry small`、`.rail .brand small`、`.detail-source`、`.edge-path span`、`.relation-type`、`.property-row span`、`.no-selection`、OntologyDiscover 的 `.muted`）。**这 18 处登记为后续可达性专项，本轮验收不以其计数为门槛**；新增界面禁止用 `--faint` 承载信息。
3. 语义三元组永远成对使用：`--ok/--ok-soft/--ok-line`（warn、danger 同理），不要跨族拼色。
4. 白色透明叠层（`#fffffff0`、`#fffffff2`、`#ffffffeb`、`#ffffffed`、`#ffffffdd`）是画布上浮层的可读底，收敛到 `--paper` + alpha 变量；不引入新灰。
5. 边框色不表达层级，只用 `--line`（分隔）与 `--line-2`（可交互控件描边）两级；选中态描边用 `--blue-line`（`.manager-item.active`/`.ld-row.active`/`.ont-filters button.active`/`.canvas-tools button.active`/`.area-tabs button.active`/`.ow-item.active`/`.library-card.active`/`.property-choice.active` 等 8 处既有 active 均用 `--blue-line`，本轮把 `:148/:150/:151` 三处 `#b9d4f9`、`#bfd7fa`、`#b7cdf1` 收敛过来）。一处登记的例外用法：`button:where(:not(:disabled)):hover` 用 `--blue`（基元 hover 边框，main 既有）。另有一处**已核实的无效声明，不列为例外**：`.space-new` 常态是 `border:0;background:none`（`style.css:56`，base 与 HEAD **逐字节相同**，本轮未改），其 `.space-new:hover` 里的 `border-color:transparent` 因为根本没有描边宽度而**不产生任何渲染效果**——hover 的视觉变化全部来自同一行的 `background:var(--blue-soft)` 与 `color:var(--blue-deep)`。早先本条把它解释成"常态 `--blue-soft` 底 + `--blue-line` 边、hover 时边框转透明由底色承载边界"，把常态与 hover 态说反了，属**失准描述**，此处更正；无效声明按例外 6 的尺寸/清理专项口径保留原样，本轮不顺手删（删它没有视觉收益，却要重开一次回归面）。

## Typography

- 唯一字族：系统栈 `-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif`。**不加载网络字体**，中文优先 PingFang SC。
- **允许清单**（新增/修改的字号）：`micro-label 11` / `caption 12` / `body-sm 13` / `body-md 14`（基准）/ `heading-md 15` / `heading-lg 17` / `metric-md 22` / `display-lg 48`；`16` 与 `19` 是 main 既有特殊档（`.ld-title` 16、`.formula` 19），允许沿用。行高基准 1.6，标题 1.4–1.5。
  **存量越界登记，本轮不改值**：实测全仓（排除 `legacyGraph/`）落在允许清单之外的 `font-size` 字面值为 **18px×7、20px×10、21px×4、23px×4、25px×1、28px×1、32px×1、34px×1**，集中在 `ontology/OntologyHome.vue`、`ontology/OntologyImport.vue`、`ontology/build/*`、`app/LoginView.vue`、`shared/AppSelect.vue`、`tools/*` 与 `style.css`；另有 `10px`（`style.css` 版本号上标、`tools/LlmProviders.vue`）。这些全部是 **main 既有值**，本轮**未新增越界字号**，上述每一个取值也**不是本轮改出来的**——`git diff 07f8d8d HEAD -- ontology/OntologyHome.vue` 中 `.home-welcome h2` 的 23/21/20 三档逐字节未变（早先本条曾写作"20→21 并档"，属失实，已更正）。核对口径：遍历 `frontend/src/**/*.{vue,css}` 统计 `font-size:(\d+)px` 并剔除允许清单。
- 字重：正文 400，控件与列表 500，区块标题 600，页面标题与强调数字 650–700。**存量越界登记**：`font-weight:550`（`ontology/BusinessRuleDialog.vue:57` `.brc-field`）不在档位内，是 main 既有值，本轮未引入也未清理。`.mapping-description-title{font-weight:400}` 与 `.ont-badge.kind-*{font-weight:500}` 是**有意的组件级降重**（前者刻意弱化副标题、后者是状态胶囊而非 eyebrow 大写提示），属合规声明而不是漂移副本。
- 数字一律 `font-variant-numeric: tabular-nums`（统计、表格、结果值），避免数值列跳动。
- `micro-label`（11px + letter-spacing 1px + 650）只用于 eyebrow / badge / 大写提示，不用于正文。
- 代码与技术细节用等宽呈现并落在 `--paper-3` 底上；`pre` 必须 `white-space: pre-wrap; word-break: break-word`。
- 长文本必须 `overflow-wrap: anywhere`（表格单元格、业务定义、来源路径），不允许撑破布局。

## Layout

- 骨架固定：**左 rail 224px（白底 + `--line` 分隔）+ 顶栏 56px + `main` 20px 24px 内边距**；`.shell` 用 `margin-left:224px; margin-top:56px` 让位。rail 折叠为 mini 时同步调整。
- 列表—详情有两种登记范式，不得混用第三种：
  - `.manager-layout`（270px 列表 + `minmax(0,1fr)` 详情，列表 `position:sticky; top:72px`）用于库/管理类页。
  - `.ld-*`（`.ld-list` + `.ld-detail` 双栏）用于对象建模等工作区。**断点判定只在 JS 一处（`matchMedia` 驱动 `.ld-stacked`）**，样式里不再写第二份宽度断点，避免双份断点漂移。
  - `.mapping-workspace` / `.definition-grid` / `.workspace` 为既有专用栅格，保留。
- 表单：`.editor-field` 为标签+控件唯一写法；两列用 `.form-grid`（`repeat(2, minmax(0,1fr))`）；必填用 `.required-mark`，可选项标 `.optional-label`，字段说明用 `.field-help`，错误用 `.field-error`。
- 统一表格用 `.ont-*`：表头列宽 40px 起、单元格 64px、`.ont-clip` 两行截断、`.ont-badge` 状态胶囊、`.ont-empty` 空态、`.ont-drawer` 480px 右侧抽屉 + `.ont-overlay` 遮罩、`.list-pager` 分页。
- 空间分配靠留白分组（`--r-*` + 12–24px 间距），不嵌套卡片。卡片只有一层：`.card` / `.detail-card`。
- 响应式断点（以 `grep -rn "@media" frontend/src` 命中为准，legacyGraph 除外）：全局 `style.css` 用 1200 / 1150 / 1000 / 900 / 850 / 800 / 760 / 700 / 640 / 620 与一个 `min-width:761`（另有 `prefers-reduced-motion`）；组件级另有 1439 / 1100 / 1099 / 1050 / 1000 / 900 / 850 / 800 / 768 / 767 / 760 / 650 / 620 / 600。**这些数值全部是 main 既有值，本轮一条都没改**；把它们收敛到少数几档属于例外 6 的尺寸迁移，不在本轮范围。窄屏下双栏改为堆叠、rail 变横向、抽屉占满宽。

## Elevation & Depth

阴影只有两档，且**不用于营造"浮起卡片"观感**：

- `--shadow-1` `0 1px 2px rgba(24,44,62,.08)`：极轻的贴合阴影，用于静态块。
- `--shadow-2` `0 16px 48px rgba(16,40,61,.18)` 是**浮层/抽屉级**阴影。实测使用方共 7 处：`dialog`、`.row-menu-list`、`.user-menu`、`.ont-drawer`（`style.css`），`flow/FlowEditor.vue` 的 `.menu` 与 `.flow-modal`，`tools/InstanceTable.vue` 的 `.it-column-menu`。其中 `.flow-modal`（main 为 `0 20px 60px #0f213a44`）与 `.it-column-menu` 是本轮新并入该令牌的，属例外 9 的阴影并档，改前/改后值见开发计划 §9.4 附表；`.menu` 只去掉 `var()` 兜底字面量。**不是浮层**的 `.rail` 不使用它。（早先本条把 `.row-menu-float` 列为使用方是错的：该类只有 `position:fixed;z-index:120`，无 `box-shadow`；菜单阴影实际挂在共享的 `.row-menu-list` 上，`ObjectWorkspace` 的旧 `.ow-more-menu` 本轮已上收为该共享类。）
- 层级主要由 **hairline 边框（`--line`）+ 底色差（`--paper` vs `--paper-2/--bg`）** 表达。给普通卡片加 `--shadow-2` 属于违规。
- 遮罩统一 `--backdrop` #10283d66。
- **全局 z-index 序**（`style.css` + 共享件，实测枚举）：表格 sticky 头 1/2 与 `.ld-tabs` 2（局部堆叠上下文内，不参与全局比较）< rail 30 < topbar 40 < 画布内浮层 5/6/9（`flow/FlowCanvas.vue`、`flow/FlowEditor.vue`，均在画布自身堆叠上下文内）< `.modal-backdrop` 100 = `.ont-overlay` 100 < `.user-menu` 115 < `.row-menu-float` 120 = `ontology/OntologyImport.vue` 预览层 120 < `.app-confirm-backdrop` 150 < `.stale-page-banner` 200 < **`.app-select-panel` 1000**（`shared/AppSelect.vue`；它必须压过一切行内浮层，因为下拉可以在任何容器内展开，且它自己 `position:fixed` 脱离父级堆叠上下文）。
- 新浮层必须落进这个序里并在本节登记；未登记的自定义浮层按例外处理。`ontology/legacyGraph/**` 的 4/6/15/20/60/90 是它自己作用域内的独立序，不并入。
- 画布底纹为 `radial-gradient(var(--grid) 1px, transparent 1px)` + `background-size:18px/20px`；`.canvas-hint` 用近透明白底保证可读，`pointer-events:none`。

## Shapes

- 圆角四档：`--r-sm` 6px（按钮/输入/行/标签页内控件）、`--r-md` 10px（卡片、工作区容器、工具条）、`--r-lg` 14px（对话框、大容器、抽屉）、`--r-pill` 999px（状态胶囊、badge）。正圆用 `50%`（头像、色点）。
- 同一组件类型只用一档：所有按钮 6px、所有卡片 10px、所有对话框 14px。出现 `4px`/`7px`/`8px` 之类中间值即为漂移，**新增与本轮改动的组件必须并档**（4/5→`--r-sm`，7→`--r-sm` 或 `--r-md`，8/9→`--r-md`，看组件类型）。
- **这条目前是目标而非现状**：全仓仍有 62 处非令牌 `border-radius` 字面值（`4/5/6/7/8/9/10/20px`），其中 **10 处**（`6px`×7 + `10px`×3）与令牌值完全相同、可零视觉风险替换，**52 处**替换即改变渲染像素。它们全部是 **main 既有状态、本轮未触及**（共享件 `AppSelect` 的 7px 触发器 / 9px 面板也在其中）。按例外 6 的口径，圆角属于尺寸档位，整仓并档与 `padding/margin/gap/width` 一起列为后续尺寸专项，本轮验收**不得**以非令牌圆角计数为门槛。
- **本条本轮已实际执行**：32 处 `border-radius` 中间值并档（3/4/5/7px→`--r-sm`，8/9px→`--r-md`），`.ont-empty-ico` 8px→`50%`（与 `.empty-state-ico` 同为正圆），`outline-offset` 3px→2px 两处归一，阴影 4 处并到 `--shadow-1/--shadow-2`。**每一处的改前/改后实测值都在 `文档/需求/20260921_样式与交互统一/开发计划.md` §9 的附表里**，本文件只写规则、不重复数值清单。例外 6 豁免的是 `padding/margin/gap/width` 那类大批量尺寸迁移，不是本条。
- 单边圆角（如表头首格）用 `0 0 var(--r-sm) var(--r-sm)` 形式书写，仍是令牌值。
- 描边宽度统一 1px；选中/焦点用 2px `outline`（`outline-offset:2px`），不要靠加粗边框表达焦点。

## Components

共享控件（`frontend/src/shared/`）是复合行为的唯一入口：**`AppSelect`、`EditorField`、`EditorLayout`、`EditorHead`、`RowMenu`、`OntDrawer`、`ListPager`、`SearchField`、`OntologyList`、`AppError`、`shared/appConfirm`**。页面禁止自写第五套下拉、行菜单、抽屉、分页或确认框。（Button / Input / Textarea / Dialog 不设包装组件，见「登记例外」第 5 条；空态同理只有 `.empty-state` 类，没有 `EmptyState` 组件。）

| 组件 | 默认 | hover | focus-visible | active | disabled | loading / error |
|---|---|---|---|---|---|---|
| 次级按钮 `button` | `--paper` 底 + `--line` 边 + `--ink` 字 | 边转 `--blue`、字转 `--blue` | 2px `--focus` 环 + 2px offset | `translateY(1px)` | `opacity:.5; cursor:not-allowed` | 提交中禁用并保留文案 |
| 主按钮 `.primary` | `--blue` 底白字 | `--blue-deep` 底白字 | 同上 | 同上 | 同上 | 同上 |
| 破坏性按钮 `.danger-btn` | `--paper` 底 + `--danger-line` 边 + `--danger` 字 | `--danger-soft` 底 + `--danger` 边与字 | 同上 | 同上 | 同上 | 与 `.mini` 并列用于行内删除 |
| 原生 `input` / `textarea` / `select` | `--paper` 底 + `--line-2` 边 | 边框不变（避免与焦点混淆） | 2px `--focus` 环 + 2px offset | — | `--paper-2` 底 `--muted` 字 | 错误时 `.field-error` 文本 |
| `AppSelect` 触发器 | 同上 | 边框转 `--blue-line`（rail 深色区另有覆盖） | **全局 `button:focus-visible` 的 2px `--focus` 环是焦点主指标**；组件额外把边框转 `--blue-line` 只是加强边界提示，不能单独充当焦点指示 | — | 同 `:disabled` 基元 | — |
| 只读输入 `[readonly]` | `--paper-2` 底 `--muted` 字 | — | 环保留（可选中） | — | — | — |
| 链接式操作 `.row-link` | 无边框、`--blue-ink` 字 | 转 `--blue-deep` + 下划线 | 同上 | — | 隐藏或禁用 | `.danger` 用 `--danger` |
| 行/列表项 `.ld-row` `.manager-item` | 透明底 | `--paper-2` 底 | 同上 | — | — | 选中态 `--blue-soft` 底 + `--blue-line` 边 + `--blue-ink` 字 |
| 表格行 `.ont-table` | `--paper` 底 | 行底微升 | 可聚焦控件带环 | — | — | 选中/定位闪现 `--blue-soft`；空态 `.ont-empty` |
| 标签页 `.ow-tabs` `.ld-tabs` `.editor-tabs` | `--muted` 字 | 字转 `--ink` | 环 | — | — | active：`--blue-ink` 字 + `--blue` 下边/底 |
| 对话框 `dialog` / `.modal-backdrop` | `--r-lg`、`--shadow-2`、`--backdrop` 遮罩 | — | 焦点入框、Esc 关闭 | — | — | 破坏性确认走 `shared/appConfirm`，其 `.danger` 用 `--danger` |
| 保存状态 `.save-pill` | 五态：`saved`/`saving`/`dirty`/`form`/`editing`/`error`/`conflict` | — | 可点击时带环 | — | — | `error`/`conflict` 用 danger 三元组 |
| 空态 `.empty-state` `.ont-empty` `.empty` | `--muted` 字 + 图标 + 一句解释 + **一个主操作** | — | — | — | — | 骨架用 `.skeleton`，不用 spinner 堆叠 |

### 本轮登记进契约的共享语义类（此前只存在于代码，未登记）

| 类 | 位置 | 语义 | 备注 |
|---|---|---|---|
| `.danger-btn`（+ `:where(:not(:disabled)):hover`） | `style.css` | **实心描边**破坏性按钮：`--paper` 底 / `--danger` 字 / `--danger-line` 边，hover 换 `--danger-soft` 底 | 由 `BuildTasksPage` 的 `.bt-danger-btn` 与 `.danger-ghost` 上收。全局 `.danger` 是**文字色工具类**（带 `font-size:12px; margin-top:12px`），绝不能当按钮类用 |
| `.editor-field{--app-select-gap:8px}` | `style.css` | 字段容器内的 `AppSelect` 上间距 | `AppSelect` 自带 `margin-top:var(--app-select-gap,5px)`。**用继承自定义属性而不是写 `.editor-field .app-select{margin-top:8px}`**：后者与组件 scoped 规则同为 (0,2,0) 且组件样式注入更晚，写了也不生效。原生 `select` 在 `.editor-field` 内历史是 8px，迁到 `AppSelect` 后靠这个属性保持不变，独立使用仍是 5px |
| `.sk-row/.sk-title/.sk-line/.sk-text/.sk-label/.sk-nav/.sk-w45…w96` | `style.css` | 骨架占位尺寸 | 从 14 处内联 `style` 上收为类，`例外 4` 清单因此缩短。**唯一一处随之改变的实际渲染值**：`OntologyHome` 原 220px 标题骨架并入统一的 200px（该元素不在 `.check-loading` 内，故不受组件内 `.check-loading .skeleton` 的 12px 高度干扰；逐元素核对见开发计划 §9.5 附表 C-3 第 1 行（C-1 只是内联 `style=` 的**计数表**、不含逐元素决议值，指针早先写错成 C-1））。约束：上收时**必须逐元素确认目标容器内没有同类的 scoped 规则**——scoped 单类 (0,2,0) 永远压得住全局 (0,1,0)，全局尺寸类在带 scoped 覆盖的组件里会失效 |
| `.ont-filters,.ld-filters`（含 `button`、`button.active`） | `style.css` | 列表筛选胶囊 | 与 `.ont-badge` 同用 `--r-pill`；`.ld-filters` 的独有值只剩 `padding:10px 14px`，其余并档，计算结果与改前逐项相同 |
| `.property-pill.is-warn` | `style.css` | 属性胶囊的 warn 态：`--warn-soft` 底 + `--warn` 字 | 与 `.property-pill` 默认（blue-soft/blue-ink）成对 |
| `.canvas-hint` | `style.css` | 画布左下提示 chip 的唯一实现 | 本轮把 `.kc-hint`(`#ffffffed`/5px/5px 8px)、`.graph-hint`(`#ffffffdd`/11px/`--faint`)、`.canvas-note`(无描边/`#ffffffeb`) 三套副本并到这一条（`--paper-float`/`--r-sm`/`5px 12px`/`--muted` + 1px `--line` 描边）。迁移后的悬挂引用实测：`.kc-hint`、`.graph-hint` 全仓 0 命中（元素改挂 `.canvas-hint`）；**`.canvas-note` 仍在** `flow/FlowEditor.vue:539` 使用并保留 scoped 规则，只是把色值/圆角/描边并到同一配方，位置仍是自己的 `bottom:14px;left:16px`。`tools/InstanceGraph.vue` 则新增 scoped `.canvas-hint{bottom:10px;left:12px;font-size:11px}` 以保住它原来的贴边距离。三处并档的改前/改后决议值见开发计划 §9.4 附表 B-2a（决议值在全仓不再出现的那一类，逐条注明替代声明）。 |
| `.axis` / `.series-line` / `.series-dot`（`tools/InstanceCharts.vue`）、`.plot-area` / `.grid-line` / `.point`（`tools/InstanceMap.vue`） | 各组件 scoped | SVG 图形元素的语义色 | 这些元素在 main 用 `fill="#346fe1"` / `stroke="#a2b4c8"` 等**呈现属性**写死，本轮改为挂类并读令牌（见规则 1 与例外 12）。呈现属性优先级低于任何 CSS 声明，因此改挂类必然改变渲染值，属有意的色值收敛 |
| `.row-menu-list button.danger:not(:first-child)` | `style.css` | 行菜单内破坏性项的分隔线 | 首项不加 `border-top/padding-top/下半圆角`（main 无条件加，首项恰好是删除项时会顶出一段无意义分隔）；同时 `margin-top:0` 压住全局 `.danger` 的 12px |

交互态硬性要求：

1. **键盘焦点永不可见性为零**：任何自定义 `button/div/input/select/textarea/[tabindex]` 都不得 `outline:none` 而不补等效 2px `--focus` 环。
   非 legacyGraph 的 `outline:none` 全量只有 2 处，均在 `shared/AppSelect.vue`，且各自有等效补偿，登记为合规：`:178` 搜索框内层 `input` 由父级 `.app-select-search:focus-within` 补 `border-color:var(--blue)` + `inset 0 0 0 1px var(--focus)`（面板 `overflow:hidden`，外描边环会被裁掉，故改用**合计 2px 的等效焦点反馈**：1px 边框换色 + 1px 内描边环，不是字面意义的第二条完整 2px 环）；`:183` `.app-select-options` 是 `role="listbox"` 容器，键盘导航走 `aria-activedescendant`，焦点由 `.app-select-option.is-active{background:var(--paper-2)}` 表达。
   **焦点环全量登记**（实测口径：`outline\s*:\s*2px solid var\(--focus\)`，排除 `legacyGraph/`）：**9 处**用 `--focus` 描边环——`style.css` 的基元 `button/input/select/textarea:focus-visible` 与 `.app-select-trigger:focus-visible`（`.space` 深色区另覆盖）、`tools/KnowledgeCanvas.vue`、`tools/InstanceGraph.vue`、`tools/KnowledgeDetails.vue`、`ontology/FunctionManager.vue`、`ontology/ObjectWorkspace.vue`、`project/ImplementationManager.vue`、`flow/NodeConfig.vue`；另有 **3 处 `0 0 0 1px var(--blue)` 是"选中环"不是焦点环**（`tools/KnowledgeExplorer.vue` `.ke-presets button.active`、`tools/OntologyDiscover.vue` `.resource-item.selected`、`project/PropertySources.vue` `.ps-card.selected`），因 `--blue` 与 `--focus` 同值，登记为选中语义保持 `--blue`、不改值。
2. hover 与 focus 必须可区分：hover 改色，focus 改环。
3. disabled 一律靠 `:disabled` 属性选择器实现并配 `cursor:not-allowed`，不用改色模拟。透明度**目标值 `.5`**，实测现状 `.5` 与 `.55` 并存：`button:disabled`、`.row-menu-list button:disabled`、`App.vue:1213`、`AppSelect.vue:187` 为 `.5`；`input/textarea/select:disabled`、`.user-menu button:disabled`、`project/PropertySources.vue:1283` 为 `.55`（main 同样如此，本轮未改值，登记为存量偏差）。`shared/AppSelect.vue:173` 的 `opacity:1` 是"禁用仍可辨读"的刻意例外。
4. 破坏性操作必须二次确认（`shared/appConfirm`），确认按钮 danger 语义、取消按钮次级语义。
5. 异步控件在 pending 期间禁用并显示进行中文案，防止重复提交。**本条约束的是本轮新增/改动的控件**；main 存量按钮的 pending 文案不统一（部分只禁用不改文案），本轮未逐处核对，不在通过判据内。
6. 错误反馈**唯一归属**：字段级 `.field-error` 紧跟控件、页面级 `#feedback` / `.inline-error`、全局 toast 三级各有分工，同一错误只在其中一处出现（位置由归属层级决定，不是字面固定坐标）。
7. `@media (prefers-reduced-motion: reduce)` 内取消动画与位移。

## Do's and Don'ts

**Do**

- 需要颜色时先在 `:root` 找角色令牌，找不到就在 DESIGN.md 与 `:root` 同步登记一个语义名（既有例子：`--blue-ink`、`--danger-deep`），再使用。
- 新界面复用既有骨架：库管理用 `.manager-layout`，工作区用 `.ld-*`，表格用 `.ont-*`，字段用 `.editor-field`，空态用 `.empty-state`。
- 尺寸写进令牌或既有档位；`--r-sm/md/lg/pill` 与 12/13/14/15/17 字号是允许清单。
- 表格与详情都给"下一步动作"：校验问题可定位到对象并高亮行，而不是只报错文。
- **每页只有一个是 `<h1>`**：顶栏 `.topbar-title`（15px/650，`style.css:80`）。页面内的标题从 `<h2>` 起（区块标题走全局 `h2{17px/650}`、`h3{15px}`，`style.css:27`；把页内 h1 降为 h2 时**必须同步改 scoped 选择器并把 font-size/font-weight 写成原值**，否则会静默落到全局 h2 的 17px/650）。局部 16px 的 h3（`.tool-card h3`、`.ab-root h3`、`.bt-hero h3`）是上面登记的既有特殊档，**不算漂移、不为"统一"改值**。
- **表单控件同规格**：`.editor-field` 下 `input/select/textarea` 是 `min-height:40px / padding:9px 11px / --r-sm`（`style.css:41`），`shared/AppSelect.vue` 的 `.app-select-trigger` 必须与之一致（本轮从 41px/7px 并到 40px/`--r-sm`）；表格行内需要更高行高时由**该表格自己**把输入框与触发器一起钉同一值（例：`project/ActionBindings.vue:594/596` 的 41px），不在组件基线里写死。
- **弹窗焦点只有一个实现**：Tab 圈禁、焦点移入与关闭归还全部由 `shared/modalFocus.ts` 在 `document` 上兜底（识别 `[aria-modal="true"]`），页面**不再自写 Tab 陷阱**；页面只负责自己的 Escape 与背板关闭路径。已有完整自实现的（`shared/appConfirm.ts`）在卡片上标 `data-modal-a11y="manual"` 退出接管。**不用集中 `inert` 代替焦点陷阱**：inert 漏算一个容器就变成整屏吞点击，代价高于收益。
- 保留既有安全网行为：自动保存、离开保护、引用检查、撤销、`restoreListScroll`、`selectedHidden` 提示、保存队列语义都不受样式重构影响。
- 改前端后跑 `npm run build`（含 vue-tsc 严格检查）与 `npm run lint`，并在隔离实例浏览器实测。

**Don't**

- **不改 `:root` 里既有令牌的值**。为"统一"改令牌值会连带改变全站观感，属于视觉回归。
- 不在页面/组件里写十六进制色、`rgba()` 色或 Tailwind 式任意值（`.text-[13px]`、`w-[437px]`）。
- 不新增第五种按钮/下拉/确认框/空态实现；先扩展共享组件或加登记过的变体。
- 不给静态内容套 `--shadow-2`，不做多层卡片嵌套，不引入渐变背景与入场动画。
- 不为对齐而破坏语义色族（例如拿表示成功文本的 `--ok` 当装饰边框）。
- 不把 legacy 图形编辑器（`ontology/legacyGraph/`）当作统一目标：它作用域在 `.legacy-editor-root` 内、保留自旧仓库的独立绿调令牌体系（`--accent:#14705d` 等），是**登记例外**，不混用也不改造，以免破坏其内部一致性。
- 不改接口协议、不动 `modelFormat.ts`/`navigation.ts` 的行为语义；本轮范围仅样式与交互表现。

## 登记例外（不算债务）

1. **`frontend/src/ontology/legacyGraph/**`**：旧编辑器移植代码，自带 `.legacy-editor-root` 作用域令牌（绿色 accent、`--panel/--canvas/--soft/--amber`）。已从扫描 `code.exclude` 中排除，不改造也不要求并入。
2. **图形/画布几何值**：cytoscape 样式（`frontend/src/shared/graphStyle.ts`）与 SVG/画布里的 `border-width:2.2`、`font-size:12.5`、节点宽高、`arrow-scale` 等，是渲染参数不是设计令牌；像素值允许存在，但**同一色值全站只允许一份定义**（GRAPH_STYLE 与 PREVIEW_GRAPH_STYLE 的公共基础块必须提取共享）。
3. **域分类调色板**：图谱按节点/连线类型着色的分类色（实体/属性/规则/动作等）属于数据可视编码，不并入业务语义色族；必须集中定义在 `graphStyle.ts` 一处，页面不得另起同角色副本。
4. **运行时内联样式（穷举清单，共 14 处 `:style` 绑定）**：通过内联 `style` 绑定的运行时几何/数据值允许保留，但只允许承载"随数据变化的值"，静态样式一律走类名。全仓唯一的自定义 CSS 变量注入是 `--ld-h`（列表行高）；其余 13 处直接绑几何/颜色属性，不再有 `--c` 之类单项强调色变量（那是本条早期的错误举例，已更正）。当前全量清单（新增内联样式必须落在此表内，否则按债务处理；行号会随改动漂移，核对以 `grep -rn ":style=" frontend/src --include="*.vue"` 命中为准，排除 `legacyGraph/`）：
   - 数据驱动颜色：`tools/KnowledgeCanvas.vue:41`、`tools/InstanceGraph.vue:87`（`background:item.color`，来自 `graphStyle.ts` 分类调色板）。
   - 百分比宽度：`tools/InstanceCharts.vue:25`（柱长）、`ontology/build/BuildProgressPage.vue:311`、`ontology/build/BuildMaterialsPage.vue:397/416`（进度条）。
   - 浮层定位：`App.vue:1110`、`shared/RowMenu.vue:69`、`shared/AppSelect.vue:147`（菜单/下拉的 `left/top`，由触发元素质心算出）。
   - 表格列宽与行高：`shared/OntologyList.vue:38/39`（`minWidth`/`c.width`）、`ontology/ObjectWorkspace.vue:718`（`--ld-h`）。
   - 画布与分栏几何：`flow/FlowCanvas.vue:229`（节点 `x/y`）、`flow/FlowEditor.vue:541`（检视栏拖拽宽度）。
5. **原生语义元素即基元（已登记的设计系统决策）**：本项目的 Button / Input / Textarea / Dialog 不设包装组件——它们由 `style.css` 的全局元素规则统一上令牌（含 `.primary`、`.mini`、`.danger`、`.danger-btn`、`:disabled`、`:focus-visible` 等状态），页面直接写 `<button class="primary">` 是**规范用法**，不是绕过组件库。必须复用的是承载行为的复合件：`AppSelect`（下拉，含键盘与样式一致性）、`RowMenu`、`OntDrawer`、`ListPager`、`SearchField`、`EditorField`、`EditorLayout`、`EditorHead`、`AppError`、`OntologyList`、`shared/appConfirm`。因此 `design.qa.yaml` 的 `components.rawElementPolicy` 只对 `select` 要求设计系统组件，其余登记为 `allow-raw`。页面里出现第 5 套自写下拉/抽屉/分页/确认框仍是违规。
6. **像素→尺寸令牌迁移不在本轮范围**：`px-magic-number` 类命中的绝大多数是 `padding/margin/gap/width` 的具体像素值。把 9px 收进"既有档位"必然改变实际像素（9→8 或 10），属于**视觉改动**而不是漂移收敛，与「不改令牌值、保持现有风格」的硬约束直接冲突。本轮只收敛色值、组件复用与交互态；尺寸登记为后续专项治理，验收不得以 `px-magic-number` 计数作为通过门槛。具体到 `.field-help`（字段说明行）：main 即有约 48 种 `margin` 字面值组合（含 `4px 0 0 6px` 这类四值写法与 `margin-top:-20px` 这类负值），是这条口径最大的存量来源之一；本轮只保证全局 `.field-help{…margin:6px 0…}` 这一条默认声明存在（选择器就是裸类 `.field-help`，`style.css:39`；早先本条写成 `.editor-field .field-help{margin:6px 0}` 是**失准的伪选择器**——全仓不存在带 `.editor-field ` 前缀的该规则，此处更正），未逐处并档。**并档的副作用必须登记**：`flow/FlowEditor.vue` 基线有一条 scoped `.field-help{font-size:12px;color:var(--muted);margin:0 0 8px}`，本轮删掉了它，于是该文件 4 处 `class="field-help"`（HEAD `:587/:588/:600/:629`）的 `margin` 由 `0 0 8px` 落到全局的 `6px 0`。这是**归属迁移带来的真实视觉变化**，不得记成"值中性改挂"，已列入开发计划 §9.5 附表 C-4。
7. **扫描器误报（`custom-shadow` / `tailwind-arbitrary-value` 两条规则）**：
   - `custom-shadow` 的正则是 `/\bbox-shadow\s*:|shadow-\[[^\]]+\]/g`——它匹配的是**属性名本身**，不看值。全站 19 处 `box-shadow` 声明逐条核对为：`var(--shadow-1|2)`（9 处）、`inset` 结构性内描边与令牌组合（6 处：`inset 3px 0 0 var(--blue)` 与 `inset 3px 0 var(--blue)`/`transparent` 选中条、`inset 0 0 0 1px var(--line-2),var(--shadow-1)` 标签页、`inset 0 0 0 1px var(--blue)` 与 `inset 0 0 0 1px var(--focus)` 两处描边/焦点环）、`0 0 0 1px var(--blue)` 选中环（2 处）、`none`（1 处），以及**唯一 1 处一次性值** `8px 0 20px #1b32531a`（`flow/FlowEditor.vue` 的 `.flow-list` 侧栏方向性阴影——它在 main 就是这个字面值，本轮曾误并进 `--shadow-2`（0 16px 48px 的居中浮层阴影，几何语义不同）并已回退成逐字节等于 main，见 §9）。除此之外**没有第二处一次性阴影值**。因此该计数不是债务量，核对方式是看命中行的值是否走令牌；不得为了清零误报而删掉合法阴影用法。
   - `tailwind-arbitrary-value` 的正则 `/(?:[A-Za-z0-9_:/-]+-\[[^\]]+\])/g` 会匹配 JS 正则字面量与注释里的字符类（如 `/--[^\n]/`、`ESS-[0-9]`）。本项目**不使用 Tailwind**，**4** 处命中全部是这类误报，逐处为：`tools/ValueTypeManager.vue:76` 的 `ESS-[0-9]`、`project/sqlSteps.ts:220` 的 `--[ \t]`、`project/sqlTemplates.ts:22` 的 `/--[^\n]`、`project/sqlTemplates.ts:27` 的 `--[^\n]`（后三处是 SQL 注释符的正则字符类），与样式无关。核对方式：按该正则遍历 `frontend/src/**`（排除 `legacyGraph/`）。
   - 另外 `audit-design-debt.mjs` 把结果 `sortFindings().slice(0, 1000)`，而 `px-magic-number` 单类就有 2700+ 条，因此报告里的 `summary` 分类计数**在截断后不可信**（会低估排在后面的类别）。需要可信分类计数时按同源正则不截断地统计，并固定 `code.exclude` 口径（见 `legacyGraph/**` 与 `shared/graphStyle.ts` 两条排除）。
8. **空态类名的历史三名并存（`.empty` / `.empty-state` / `.ont-empty`）**：三者**值不同、各有依赖，故不合并**——`.empty{padding:45px}`、`.empty-state{padding:44px 24px}`、`.ont-empty{padding:70px 20px}`（统一表格内空态，垂直留白更大是刻意的）。`.empty` 不能并进 `.empty-state`：它有 **16** 处非 legacy 使用（按 class token 精确等于 `empty` 统计：`App.vue:1181`、`tools/InstanceCharts:25/26/27`、`tools/InstanceMap:14`、`tools/DefinitionManager:50/52`、`shared/EditorLayout:7`、`ontology/build/BuildReviewPage:1112/1130/1298`、`project/LinkMappings:321`、`project/ProjectValidation:267`、`flow/FlowList:94`、`flow/FlowEditor:568/569`）。其中 `FlowEditor:568/569` 另带 scoped `.empty-dense{padding:26px}`，`legacyGraph/` 的 7 处 `.empty` 全部由各自 scoped 规则（(0,2,0)）覆盖，因此实际受全局 `45px` 支配的是 14 处，改成 `44px 24px` 会让这些空态左右各少 21px 内边距，属视觉回归（`legacyGraph/` 里的 `.empty` 自带 scoped 覆盖，不受全局规则影响）。本轮曾把前两者错误地"合并为同一条声明"并声称同值，**已回退为 main 各自的值**。约束只有一条：**新增页面一律用 `.empty-state`**，不得再新增第四种空态实现。
9. **尺寸档位并档本轮已执行（不是待办）**：`border-radius` 中间值并档 32 处、`outline-offset` 归一 2 处、阴影并到 `--shadow-*` 4 处，均**改变了实际渲染像素**（如 7px→`--r-sm` 6px、8px→`--r-md` 10px）。这与例外 6「不为统一而改令牌值」不冲突：改的是**组件声明**，`:root` 令牌值一个没动；但它是视觉改动而非纯漂移收敛，因此**每一处的改前/改后实测值必须查表**。完整对照按**性质分桶**（不做单一"每行都是视觉改动"的表述）：B-1 同选择器同属性决议值确实改变 95 行、B-2a 该决议值全仓不再出现（被令牌/全局基线取代）21 行、B-2b 声明换挂到别的选择器而值未变 170 行、B-3 基线已有该选择器而 HEAD 给它加声明 6 行、B-4 含 `v-bind()` 无法静态解析 2 行（两条经人工核对均为值中性）、**B-5 基线完全没有该选择器（HEAD 新增整条规则）82 条声明 / 54 个选择器，其中 `new-value` 7 条是真实视觉变化、其余 75 条按脚本口径记为改挂/收进类（实测分解：`<style>` 声明改挂 56 + 模板内联 `style=` 收进类 19 + SVG 呈现属性改挂 **0**——第三项本轮实测为零，`move-pres` 这一分类在数据里根本不出现，先前把它写成"其余 75 条的构成之一"是口径含糊。**这个 0 与例外 12 的"改挂 6 个类共 10 个呈现属性实例"不是矛盾**：那 10 个实例的宿主选择器（`.axis`/`.series-line`/`.series-dot`/`.plot-area`/`.grid-line`/`.point`）在基线**整条不存在**，因此被 B-5 归到"HEAD 新增整条规则"一侧（其中 7 行为 `new-value`），而 `move-pres` 要求"选择器基线已有、只是把呈现属性收进它"，本轮没有这种形状。两个数说的是同一批改动的不同维度，读表时不许互相替换）**——见 `文档/需求/20260921_样式与交互统一/开发计划.md` §9.4。**B-5 的"值中性"判定只回答"这个决议值在基线某条通道里出现过没有"，不回答"这个元素基线渲染成什么"**；模板 `class` 令牌改名（声明一条没变、元素却换了规则集）与 `.ts` 里的命令式 DOM 样式这两条通道结构性地落在附表 B/C-1/C-2 之外，必须按例外 12 单列。生成脚本 `证据脚本/visual-delta2.mjs`（已随需求入库，可复跑）对基线 `07f8d8d` 与工作树两份 `style.css`+全部组件 `<style>` 展开 `:root` 变量、归一 hex/单位、按选择器**合并同名规则（后写覆盖先写，与 CSS 级联一致）**后再比对，因此不会把"一条规则被拆成两条"误报成"声明被删除"；B-5 的"性质"再用该声明的决议值回查基线的**三条通道**（`<style>` 声明 / 模板 `style="` / SVG `fill=`/`stroke=`）。例外 6 豁免的是 `padding/margin/gap/width` 那类大批量迁移，验收**不得**以 `px-magic-number` 计数为门槛，但**可以**要求附表覆盖每一条 radius/offset/shadow 并档。
10. **类的归属迁移必须附对照表**：本轮把若干组件 `<style>` 里的规则上收到 `style.css`（`.canvas-hint` 三份副本合并、`.sk-*` 骨架尺寸 14 处内联上收、`.danger-btn`/`.ont-filters`/`.property-pill.is-warn` 等）。上收会改变特异性和注入顺序，也可能留下模板里指向已删类的悬挂引用。因此规则：**任何类在 scoped↔global 之间移动、或改名/合并导致旧类消失时，必须同步给出改前/改后决议值对照行，并用脚本核对全仓再无该类的引用**（`grep -rn "旧类名" frontend/src` 命中数为 0 才可删）。CSS 类名不受 vue-tsc 类型检查，漏核对不会有编译错误提示。**本轮已按本条核对的迁移（HEAD 模板引用数必须为 0）**：`.primary-run`、`.danger-text`、`.graph-hint`、`.kc-hint`、`.ow-more-menu`（改挂共享的 `.row-menu-list`）在 HEAD 的 `class="…"` 命中均为 0；`.canvas-note`、`.eyebrow`、`.badge`、`.sr-only` 仍在使用故不得删其定义。复测方式：按 class token 精确匹配遍历 `frontend/src`，被删类的模板命中数必须为 0。**反向陷阱同一条款覆盖**：给元素**新挂**一个类同样危险——组件里可能已有一条作用域更宽的既有规则，新挂类会让它突然覆盖到这个元素上，而这既不是删类也不是改值，前向核对查不出来。本轮**第一轮只登记到 1 例**（下述 `.sql-pre`）；第二轮按同一口径做穷举后，这类"新挂/换挂类"实测为 **26 个文件、88 个令牌增减位点（+73 / −15）、50 个变化令牌**，按变更组归并为 **24 行**，`.sql-pre` 只是其中一处（这里早先写的"15 组 / 70 个位点"是脚本带模板截断 bug 时的半成品数，已作废）。完整清单与逐位点的计算样式差异见开发计划 §9.5 附表 C-4/C-4b，**外加 C-4c（内联搬进基线已有的类）与 C-4d（换元素/换组件）两条 C-4 自身看不见的子通道**。教训：**发现 1 例就停手，等于把"已知 1 例"当成"共 1 例"**，这条自查必须做到穷举为止。实例：`project/QueryRuleImplementation.vue` 的 `.sql-pre` 在 main 就真实作用于第 32 行的"步骤 SQL 预览"，而第 38 行"已保存的 SQL 模板"只用全局 `pre{}` + 内联 `white-space/overflow-wrap` 渲染（UA 等宽 12px、`--paper-2` 底、14px 内边距、无描边）。本轮把第 38 行也改挂 `.sql-pre`，于是**同组件内两处 SQL 代码块并成一套实现**，第 38 行的渲染随之变为 13px/行高 1.6/`--paper` 底/1px `--line` 描边/6px 圆角/`10px 12px` 内边距——这是**有意的实现统一**，不是内联样式中性价迁移，已在此登记并在开发计划 §9.5 附表 C-3 逐值列出。核对口径：对每个本轮**新出现**在模板 `class` 里的类名，检查它在 HEAD 是否已有对应规则、该规则在基线是否只服务于别的元素；若是，逐属性比对新元素因此受到的渲染变化，不得默认"只是把内联样式收进类"。本轮开发过程中该规则被违反过一次（上收全局的选择器抢不过组件 scoped 规则），已在末次提交前按本条核对并收口；后续轮次一律按本条执行。

11. **容器规则把 `.editor-field` 的外边距归零的两处（main 既有，非本轮引入）**：`app/LoginView.vue` 的 `.login-form .editor-field{margin:0 0 14px}` 与 `style.css` 的 `.library-toolbar .editor-field{margin:0}`（后者实测无命中——`.library-toolbar` 内当前没有 `.editor-field`，是 main 遗留的死规则，本轮不清理以免牵连其它 `:0/2,0` 选择器）。由于 `--app-select-gap` 走继承、与容器 `margin` 无关，`.editor-field` 内 `AppSelect` 的上间距从 main 的 5px 变为 8px（与同容器内原生 `input` 的 `margin-top:8px` 一致），这是该机制的**预期视觉变化**，已列入 §9 对照说明。**但上述两个容器都不构成该变化的渲染现场**：本轮实测 `app/LoginView.vue` 的 `.login-form` 子树内 `<AppSelect>` 命中 **0**（只有原生 `input`），`style.css` 的 `.library-toolbar` 内也没有 `.editor-field`（它自身的容器规则同样无命中，见下）——早先本条写成"这**两处内**的 AppSelect 上间距从 5px 变为 8px"是**把机制的适用范围错标成了这两处的实测结果**，此处更正。该变化的真实现场是全仓其它 `.editor-field` 包 `<AppSelect>` 的位点，逐处清单见开发计划 §9.3。

12. **`fill=` / `stroke=` 呈现属性不在 `style=` 与 CSS 声明两套扫描口径内**：附表 B 只看 `<style>` 里的声明、例外 4 的清单只数 `style=` 属性，两者都**看不见** SVG 元素上的 `fill="#346fe1"`、`stroke="#a2b4c8"` 这类**呈现属性**。呈现属性在级联中排在所有作者声明之前，所以"给它改挂一个类"必然改变渲染值，不可能像 `style=` 迁移那样做到值中性。本轮改挂 6 个类共 10 个呈现属性实例（`tools/InstanceCharts.vue` 的 `.axis`/`.series-line`/`.series-dot` 5 处，`tools/InstanceMap.vue` 的 `.plot-area`/`.grid-line`/`.point` 5 处），全部按规则 1 收敛到既有令牌：`#a2b4c8`→`--faint`、`#346fe1`→`--blue`、`#f3f7fc`→`--paper-2`、`#c8d8eb`→`--line-2`、`#d8e3f0`→`--line`。逐实例清单见开发计划 §9.5 附表 C-2，核对口径为按文件统计 `fill|stroke="#hex"` 字面值的多重集差异（不是文本 diff）。**约束**：今后这类迁移必须单列附表，不得声称"只是把内联样式收进类"。
13. **声明比对之外的通道必须逐条单列附表（第二轮立条时为三条，现已扩到五条，另加附表 B 自己的一条盲区）**：附表 B 比对 `<style>` 声明、例外 4 只数 `style=` 属性、例外 12 只数 SVG 呈现属性——**三条口径共同假设"渲染变化必然伴随一条声明变化"，这个假设是错的**。第二轮实测出 C-4/C-5/C-6 三条；第四、五轮又实测出 **C-4c**（内联 `style=` 搬进**基线已存在**的类 ⇒ class 令牌零变化、C-4 完全看不见）、**C-4d**（`<select>` 整体换成 `<AppSelect>` ⇒ 既不改声明也不改 class，只有换标签看得见）两条，以及附表 B 自己的盲区 **B-6**（同内容规则在文件内**位移** ⇒ B 的配对键没有"顺序"这一维，而 CSS 同特异性时后写者胜）。今后任何样式改造必须同时出这六张表：
    - **C-4｜模板 `class` 令牌改名/换挂**：`class="a"` → `class="b"` 而 CSS 一条没改，元素却换了整套规则。穷举口径 `文档/需求/20260921_样式与交互统一/证据脚本/class-imperative-delta.mjs` 实测 **26 个文件 / 88 个令牌增减位点（+73 / −15）/ 50 个变化令牌**，按变更组归并为 §9.5 的 24 行。动态绑定共 **3 处**，但**不是同一种东西**：`:class` 2 处（`PickerDialog` 的 `{danger:…}`→`{"danger-btn":…}`、`InstanceCharts` 的 `bar.value<0?'bar-negative':'bar-positive'`）+ `:style` 1 处（`BusinessRuleLibrary` 的动态 `:class` 与静态 `class="rule-feedback"` 并挂同一个 `<p>`，Vue 合并两者）。多数位点只到"令牌名换了、解析值相同"，但下列为**真实视觉变化、且此前被记成"值中性改挂"**：危险按钮上收 `.danger-btn` 后 `font-size` 不再由该类声明，`.detail-footer` 4 处从 12px→**14px**（走 `button{font:inherit}` 继承 `body{font:14px}`，**不是** UA 默认值）；`border-color` `--line` #dce4eb→`--danger-line` #f2cfcf；hover 由"蓝字蓝边白底"变"红字红边 #fdf0f0 底"。`project/LinkMappings.vue:362` 的 `×` 按钮基线挂 `.danger-ghost`，而该选择器在基线的 scoped 与 global 两套里**都不存在**（等于挂了个空类），本轮起才真的显红。`ontology/ObjectWorkspace.vue` 的 `.ow-more-menu`→全局 `.row-menu-list` 带来 `min-width:150px`→158px、条目 `white-space:nowrap`、危险项**新出现一条 `border-top` 分隔线**。两处画布浮层 `.graph-hint`/`.kc-hint`→`.canvas-hint` 带来**基线没有的 1px `--line` 描边**、`padding 5px 8px`→`5px 12px`、文字色 `--faint`→`--muted`。
    - **C-4c｜内联 `style=` 搬进基线已存在的类（C-4 的结构性盲点）**：`<label class="x" style="margin:0">` → `<label class="x">`，而 `margin:0` 加进 `.x` **已有**的规则——class 令牌多重集逐字不变，C-4 输出 0 行，附表读起来就是"这条通道没有变化"。同一脚本的窄口径（同 hunk 行级 1:1 配对）实测 **2 处**（`flow/FlowList.vue:70`、`project/ProjectBinding.vue:114`，两处都是内联值逐字搬进已有类、判为值中性）；宽口径 5 组里 3 组是多重集配对假象（元素其实加了新类，已由 C-4 记过）。**计数口径**：这 2 处**不贡献静态令牌**，所以"17 个令牌位点"与"2 个零令牌位点"必须分开数，把后者塞进前者会破坏 §9.5 的 73/88 对账。
    - **C-4d｜元素/组件类型替换（换标签）**：`<select>`+`<option>` → `<AppSelect>`——声明一条没改、class 一个没改、`style=` 计数只体现为一次删除，但该位点从 UA 原生控件整套换到组件的 `.app-select-trigger`/`.app-select-panel`/`.app-select-option` 规则上，**不可能值中性**。穷举口径改为**标签多重集差**（同脚本 `### C-4d`），实测 `select −9 / option −20 / AppSelect +9`、2 个文件共 9 个位点（`ontology/build/BuildReviewPage.vue` 7 + `flow/FlowTestWorkspace.vue` 2），与判据 A5 的"原生 select 9 ⇒ 0"是同一件事的两个口径（A5 记计数、C-4d 记渲染换轨），互相印证不互相替代。**约束**：这类改动必须逐位点登记，并写明**未做页面级浏览器实测**，不许只留一个计数。
    - **B-6｜同内容规则的文件内位移（附表 B 的盲区）**：规则一字不改、只在 `style.css` 里挪位置，B 的 (选择器, 属性, 值) 配对键看不见，而 CSS 同特异性时**后写者胜**。`证据脚本/rule-order-delta.mjs` 先用最长递增子序列找出被位移的规则，再按"同一元素共挂 + 同为单类特异性 + 属性集合重叠"三条件找真实竞争者。实测本轮 `style.css` 只有 `.empty` 被位移（第 65 → 第 407 条，跨越 332 条），唯一竞争者 `.card`（`padding:20px`，与 `class="card empty"` 共挂）在两版里都排在 `.empty` 之前 ⇒ 先后关系没变、无翻转。**本条自带的教训**：第一版竞争者判据是"选择器是否共享类令牌"，报 0 条——而 `.card` 与 `.empty` 的竞争**根本不需要共享令牌**，只要同一元素共挂两类就争同一属性；**证否用的判据必须比坏情况更宽**，否则"0 条"只说明筛子没对准，不说明没有问题。：`document.createElement` + `.className =` + `el.style.x =`。本轮唯一命中 `shared/appConfirm.ts:52-55`：删掉 3 行内联覆盖（`color:#b03a3a`/`margin:0`/`fontSize:inherit`）并把 `'danger'` 改写为 `'danger-btn'`。**这不是等价清理**——内联样式退出级联后 hover 才第一次能盖住静止色，且描边色与悬浮底色如上改变。全站命令式命中数：`className` 2、`.style.` 3，`cssText`/`classList`/`setAttribute('style'`/`insertAdjacentHTML`/注入 `<style>`/cytoscape `.css()` 均为 0。另：`var(--token)` 出现在命令式 JS 字符串里的情况为 **0**（画布侧色值已全部落为字面 hex，这是对的——cytoscape 不解析自定义属性；页面需要同一色时经 SFC `v-bind()` 读取，编译后走组件根上的自定义属性，能正常解析）。
    - **C-6｜cytoscape 有效样式表**：`shared/graphStyle.ts` 被 `audit-design-debt` 与本仓全部证据脚本**双重排除**（脚本扩展名只收 `.vue`/`.css`），而本轮它动了 +192/−101 行、含 71 行带字面色。第二轮改用**执行比对**（`node --experimental-strip-types` 直接 import 两版模块，并从基线 `.vue` 里按括号配平抽出内联数组；比 `(selector,property,value)` 三元组**与块顺序**，因为 cytoscape 后者覆盖前者）实测：`GRAPH_STYLE` 13 块、`PREVIEW_GRAPH_STYLE` 15 块、`FLOW_EXTRA_STYLE` 12 块、`INSTANCE_GRAPH` 4 块、`KNOWLEDGE_CANVAS` 6 块，`ONTOLOGY_CATEGORY`/`FLOW_CATEGORY`/`FLOW_STATE`/`KNOWLEDGE_KIND_FILL`/`INSTANCE_NODE_PALETTE` 与 `instanceNodeColor`（66 个索引取值实测）全部**块数相同、顺序相同**，唯一差异是 4 处 `#fff`→`#ffffff`（cytoscape 解析后同一色），故**本轮画布无可见变化**；`ontology/legacyGraph/**` 23 个文件逐文件 SHA-256 相同。比对带**阴性对照**（已固化进 `证据脚本/graph-style-delta.mjs`，不是临时手工动作）：脚本自己把 HEAD `KNOWLEDGE_CANVAS` 里 `edge` 的 `text-background-color` 人为改成 `#8b9db7` 再跑一遍同一套比对，断言必须报出 BLOCKER；报不出来时脚本直接把整份报告标注为"不可信"。实测注入 1 处、报出 1 条差异、`VERDICT: NEUTRAL`、退出码 0，证明上述零差异不是脚本空跑。附带发现：`src/shared/graphStyle.ts` 的 `PREVIEW_GRAPH_STYLE` 在基线与 HEAD **都没有消费者**（`legacyGraph/PreviewView.vue:165` 解析到 `legacyGraph/shared/graphStyle` 那份独立副本），是 `src/shared` 里的死导出，仅登记不处理。
    **约束**：凡改动模板 `class`/`:class`/`style=`、**标签种类**、`.ts` 内的样式字符串、图形库样式表，或**同一 CSS 文件内规则的位置**，必须同时给出 C-4/C-4c/C-4d/C-5/C-6/B-6 对应附表；"决议值相同"不构成值中性证明，值必须**落到该元素的计算样式**上算。核对方式与结果见开发计划 §9.1、§9.5。
    **并且：声称"已穷举"的脚本本身必须先被验一次**，不能拿"脚本跑通了、没报错"当覆盖证明。本轮的实例是 C-4 脚本第一版按字面量 `<template>` 匹配开标签，内层 `<template #slot>` 的闭标签把嵌套深度提前扣到 0、模板区间被截断，只报 12 文件 / 31 位点，把 `ValueTypeManager:83`、`LinkMappings:362`、`ObjectWorkspace:763`、`App.vue` 的 `sk-*` 等整片静默漏掉；改成 `<template(?=[\s>])` 后才是 26 文件 / 88 位点。同一批里"按 HEAD 单侧查声明出处"也会把"删掉一条真在用的规则"错报成"本来就没声明"，声明出处必须按**各自修订版**查。**验证手段是拿已人工核对过的位点清单去查脚本有没有报出它们**（并配 C-6 那种注入式阴性对照），而不是看退出码。

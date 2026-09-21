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
- **唯一取色入口**：`frontend/src/style.css` 的 `:root` 是令牌唯一定义处（37 个）。新增颜色必须先进 `:root` 再由样式引用；组件里写十六进制色视为债务。
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

1. **同一语义只有一个色**。主色族里 #2458d5、#346fe1、#3978c5、#2563b9、#3478d6、#4162d6、#4080d8、#498ae4、#123f82、#2c4a86 等都是**漂移副本**，一律收敛到 `--blue*` 中最贴近角色的一项，不新增令牌。
2. `--muted` 与 `--paper` 组合已保证 ≥4.5:1；`--faint` 不得用于承载信息的文字，只能用于装饰与已存在等效文本的重复处。
3. 语义三元组永远成对使用：`--ok/--ok-soft/--ok-line`（warn、danger 同理），不要跨族拼色。
4. 白色透明叠层（`#fffffff0`、`#ffffffeb`、`#ffffffed`）是画布上浮层的可读底，收敛到 `--paper` + alpha 变量；不引入新灰。
5. 边框色不表达层级，只用 `--line`（分隔）与 `--line-2`（可交互控件描边）两级；选中态描边用 `--blue-line`。

## Typography

- 唯一字族：系统栈 `-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif`。**不加载网络字体**，中文优先 PingFang SC。
- 缩放：`caption 12` / `body-sm 13` / `body-md 14`（基准）/ `heading-md 15` / `heading-lg 17` / `metric-md 22` / `display-lg 48`。行高基准 1.6，标题 1.4–1.5。
- 字重：正文 400，控件与列表 500，区块标题 600，页面标题与强调数字 650–700。出现 650 之外的奇数字重需先并入既有档位。
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
- 响应式断点为 1200 / 1150 / 900 / 850 / 760 / 700 / 620px。窄屏下双栏改为堆叠、rail 变横向、抽屉占满宽。

## Elevation & Depth

阴影只有两档，且**不用于营造"浮起卡片"观感**：

- `--shadow-1` `0 1px 2px rgba(24,44,62,.08)`：极轻的贴合阴影，用于静态块。
- `--shadow-2` `0 16px 48px rgba(16,40,61,.18)`：只给浮层——`dialog`、`.row-menu-float`、`.user-menu`。
- 层级主要由 **hairline 边框（`--line`）+ 底色差（`--paper` vs `--paper-2/--bg`）** 表达。给普通卡片加 `--shadow-2` 属于违规。
- 遮罩统一 `--backdrop` #10283d66。z-index 序：rail 30 < `.modal-backdrop` 100 < `.app-confirm-backdrop` 150。新浮层必须落进这个序里并说明。
- 画布底纹为 `radial-gradient(var(--grid) 1px, transparent 1px)` + `background-size:18px/20px`；`.canvas-hint` 用近透明白底保证可读，`pointer-events:none`。

## Shapes

- 圆角四档：`--r-sm` 6px（按钮/输入/行/标签页内控件）、`--r-md` 10px（卡片、工作区容器、工具条）、`--r-lg` 14px（对话框、大容器、抽屉）、`--r-pill` 999px（状态胶囊、badge）。正圆用 `50%`（头像、色点）。
- 同一组件类型只用一档：所有按钮 6px、所有卡片 10px、所有对话框 14px。出现 `4px`/`7px`/`8px` 之类中间值即为漂移，并档到最近的令牌（4→`--r-sm`，7/8→`--r-sm` 或 `--r-md`，看组件类型）。
- 单边圆角（如表头首格）用 `0 0 var(--r-sm) var(--r-sm)` 形式书写，仍是令牌值。
- 描边宽度统一 1px；选中/焦点用 2px `outline`（`outline-offset:2px`），不要靠加粗边框表达焦点。

## Components

共享控件（`frontend/src/shared/`）是复合行为的唯一入口：**`AppSelect`、`EditorField`、`EditorLayout`、`EditorHead`、`RowMenu`、`OntDrawer`、`ListPager`、`SearchField`、`OntologyList`、`AppError`、`shared/appConfirm`**。页面禁止自写第五套下拉、行菜单、抽屉、分页或确认框。（Button / Input / Textarea / Dialog 不设包装组件，见「登记例外」第 5 条；空态同理只有 `.empty-state` 类，没有 `EmptyState` 组件。）

| 组件 | 默认 | hover | focus-visible | active | disabled | loading / error |
|---|---|---|---|---|---|---|
| 次级按钮 `button` | `--paper` 底 + `--line` 边 + `--ink` 字 | 边转 `--blue`、字转 `--blue` | 2px `--focus` 环 + 2px offset | `translateY(1px)` | `opacity:.5; cursor:not-allowed` | 提交中禁用并保留文案 |
| 主按钮 `.primary` | `--blue` 底白字 | `--blue-deep` 底白字 | 同上 | 同上 | 同上 | 同上 |
| 破坏性按钮 `.danger-btn` | `--paper` 底 + `--danger-line` 边 + `--danger` 字 | `--danger-soft` 底 + `--danger` 边与字 | 同上 | 同上 | 同上 | 与 `.mini` 并列用于行内删除 |
| 输入 / `textarea` / `AppSelect` | `--paper` 底 + `--line-2` 边 | 边框不变（避免与焦点混淆） | 2px `--focus` 环 + 2px offset | — | `--paper-2` 底 `--muted` 字 | 错误时 `.field-error` 文本 |
| 只读输入 `[readonly]` | `--paper-2` 底 `--muted` 字 | — | 环保留（可选中） | — | — | — |
| 链接式操作 `.row-link` | 无边框、`--blue-ink` 字 | 转 `--blue-deep` + 下划线 | 同上 | — | 隐藏或禁用 | `.danger` 用 `--danger` |
| 行/列表项 `.ld-row` `.manager-item` | 透明底 | `--paper-2` 底 | 同上 | — | — | 选中态 `--blue-soft` 底 + `--blue-line` 边 + `--blue-ink` 字 |
| 表格行 `.ont-table` | `--paper` 底 | 行底微升 | 可聚焦控件带环 | — | — | 选中/定位闪现 `--blue-soft`；空态 `.ont-empty` |
| 标签页 `.ow-tabs` `.ld-tabs` `.editor-tabs` | `--muted` 字 | 字转 `--ink` | 环 | — | — | active：`--blue-ink` 字 + `--blue` 下边/底 |
| 对话框 `dialog` / `.modal-backdrop` | `--r-lg`、`--shadow-2`、`--backdrop` 遮罩 | — | 焦点入框、Esc 关闭 | — | — | 破坏性确认走 `shared/appConfirm`，其 `.danger` 用 `--danger` |
| 保存状态 `.save-pill` | 五态：`saved`/`saving`/`dirty`/`form`/`editing`/`error`/`conflict` | — | 可点击时带环 | — | — | `error`/`conflict` 用 danger 三元组 |
| 空态 `.empty-state` `.ont-empty` `.empty` | `--muted` 字 + 图标 + 一句解释 + **一个主操作** | — | — | — | — | 骨架用 `.skeleton`，不用 spinner 堆叠 |

交互态硬性要求：

1. **键盘焦点永不可见性为零**：任何自定义 `button/div/input/select/textarea/[tabindex]` 都不得 `outline:none` 而不补等效 2px `--focus` 环。
2. hover 与 focus 必须可区分：hover 改色，focus 改环。
3. disabled 一律 `opacity:.5` + `cursor:not-allowed`，并靠 `:disabled` 属性选择器实现，不用改色模拟。
4. 破坏性操作必须二次确认（`shared/appConfirm`），确认按钮 danger 语义、取消按钮次级语义。
5. 异步控件在 pending 期间禁用并显示进行中文案，防止重复提交。
6. 错误反馈位置固定：字段级 `.field-error` 紧跟控件，页面级 `#feedback` / `.inline-error`，全局用 toast；同一错误不重复出现在两处。
7. `@media (prefers-reduced-motion: reduce)` 内取消动画与位移。

## Do's and Don'ts

**Do**

- 需要颜色时先在 `:root` 找角色令牌，找不到就在 DESIGN.md 与 `:root` 同步登记一个语义名（既有例子：`--blue-ink`、`--danger-deep`），再使用。
- 新界面复用既有骨架：库管理用 `.manager-layout`，工作区用 `.ld-*`，表格用 `.ont-*`，字段用 `.editor-field`，空态用 `.empty-state`。
- 尺寸写进令牌或既有档位；`--r-sm/md/lg/pill` 与 12/13/14/15/17 字号是允许清单。
- 表格与详情都给"下一步动作"：校验问题可定位到对象并高亮行，而不是只报错文。
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
4. **动态 CSS 变量注入（穷举清单，共 14 处）**：`--c`（单项强调色）、`--ld-h`（列表行高）等通过内联 `style` 绑定的运行时几何/数据值，允许保留，但只允许承载"随数据变化的值"，静态样式一律走类名。当前全量清单（新增内联样式必须落在此表内，否则按债务处理）：
   - 数据驱动颜色：`tools/KnowledgeCanvas.vue:41`、`tools/InstanceGraph.vue:87`（`background:item.color`，来自 `graphStyle.ts` 分类调色板）。
   - 百分比宽度：`tools/InstanceCharts.vue:25`（柱长）、`ontology/build/BuildProgressPage.vue:311`、`ontology/build/BuildMaterialsPage.vue:397/416`（进度条）。
   - 浮层定位：`App.vue:1110`、`shared/RowMenu.vue:69`、`shared/AppSelect.vue:147`（菜单/下拉的 `left/top`，由触发元素质心算出）。
   - 表格列宽与行高：`shared/OntologyList.vue:38/39`（`minWidth`/`c.width`）、`ontology/ObjectWorkspace.vue:718`（`--ld-h`）。
   - 画布与分栏几何：`flow/FlowCanvas.vue:229`（节点 `x/y`）、`flow/FlowEditor.vue:541`（检视栏拖拽宽度）。
5. **原生语义元素即基元（已登记的设计系统决策）**：本项目的 Button / Input / Textarea / Dialog 不设包装组件——它们由 `style.css` 的全局元素规则统一上令牌（含 `.primary`、`.mini`、`.danger`、`.danger-btn`、`:disabled`、`:focus-visible` 等状态），页面直接写 `<button class="primary">` 是**规范用法**，不是绕过组件库。必须复用的是承载行为的复合件：`AppSelect`（下拉，含键盘与样式一致性）、`RowMenu`、`OntDrawer`、`ListPager`、`SearchField`、`EditorField`、`EditorLayout`、`EditorHead`、`AppError`、`OntologyList`、`shared/appConfirm`。因此 `design.qa.yaml` 的 `components.rawElementPolicy` 只对 `select` 要求设计系统组件，其余登记为 `allow-raw`。页面里出现第 5 套自写下拉/抽屉/分页/确认框仍是违规。
6. **像素→尺寸令牌迁移不在本轮范围**：`px-magic-number` 类命中的绝大多数是 `padding/margin/gap/width` 的具体像素值。把 9px 收进"既有档位"必然改变实际像素（9→8 或 10），属于**视觉改动**而不是漂移收敛，与「不改令牌值、保持现有风格」的硬约束直接冲突。本轮只收敛色值、组件复用与交互态；尺寸登记为后续专项治理，验收不得以 `px-magic-number` 计数作为通过门槛。
7. **扫描器误报（`custom-shadow` / `tailwind-arbitrary-value` 两条规则）**：
   - `custom-shadow` 的正则是 `/\bbox-shadow\s*:|shadow-\[[^\]]+\]/g`——它匹配的是**属性名本身**，不看值。全站 18 处命中逐条核对为 `var(--shadow-1|2)`（10 处）、结构性内描边与令牌组合（`inset 3px 0 var(--blue)` 选中条、`inset 0 0 0 1px var(--line-2),var(--shadow-1)` 等 5 处）、焦点/选中描边环 `0 0 0 1px var(--blue)`（2 处）与 `none`（1 处），**没有一处一次性阴影值**。因此该计数不是债务量，核对方式是看命中行的值是否走令牌；不得为了清零误报而删掉合法阴影用法。
   - `tailwind-arbitrary-value` 的正则 `/(?:[A-Za-z0-9_:/-]+-\[[^\]]+\])/g` 会匹配 JS 正则字面量与注释里的字符类（如 `/--[^\n]/`、`ESS-[0-9]`）。本项目**不使用 Tailwind**，4 处命中全部是这类误报，与样式无关。
   - 另外 `audit-design-debt.mjs` 把结果 `sortFindings().slice(0, 1000)`，而 `px-magic-number` 单类就有 2700+ 条，因此报告里的 `summary` 分类计数**在截断后不可信**（会低估排在后面的类别）。需要可信分类计数时按同源正则不截断地统计，并固定 `code.exclude` 口径（见 `legacyGraph/**` 与 `shared/graphStyle.ts` 两条排除）。
8. **空态类名的历史三名并存（`.empty` / `.empty-state` / `.ont-empty`）**：`.empty` 被 `legacyGraph/` 内 5 个文件覆盖依赖，删除会破坏旧编辑器空态，故三者短期共存。已在 `style.css` 里把 `.empty` 与 `.empty-state` 合并为同一条声明（同值、单一定义处），杜绝两份内边距漂移；**新增页面一律用 `.empty-state`**，不得再新增第四种空态实现。

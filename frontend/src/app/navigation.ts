// 两区导航定义（架构方案 §4；函数编排 2026-09-16 起为项目映射空间内的菜单项）：
// 页面白名单、旧地址兼容 alias、菜单与图标。view/space 状态与导航副作用在 App 装配层；
// 本模块保持纯数据与纯函数，便于独立测试。
export const pages: Record<string, string> = {
  'o-home': '工作概览', objects: '对象建模', contracts: '计算契约', library: '共享属性', rules: '业务规则', 'o-release': '本体校验与发布',
  'p-home': '项目概览', connections: '数据连接', binding: '对象映射', implements: '取值规则库', 'p-release': '项目校验与发布',
  'p-upgrade': '版本引用',
  'f-home': '函数编排', 'f-editor': '编排编辑',
  tools: '更多工具', actions: '动作定义', interfaces: '接口定义', discover: '本体浏览', knowledge: '本体画布',
  explorer: '对象数据浏览', instances: '实例与计算预览', learning: '历史图谱导览', valuetypes: '值类型管理', llm: 'LLM 配置',
}

/** 旧 hash/内部跳转别名：全部归一到白名单键（任务板 §2 冻结）。 */
export const alias: Record<string, string> = {
  model: 'objects', links: 'objects', graph: 'objects', process: 'o-home', metrics: 'contracts',
  properties: 'objects', versions: 'o-release', validation: 'o-release', releases: 'o-release',
  projects: 'p-home', bindings: 'p-home', pvalidation: 'p-release', mapping: 'binding', 'value-types': 'valuetypes',
  flows: 'f-home', flow: 'f-home', 'flow-edit': 'f-editor',
}

export const normalizeView = (v: string) => alias[v] || v
export const projectViews = ['p-home', 'connections', 'binding', 'implements', 'p-release', 'p-upgrade']
export const flowViews = ['f-home', 'f-editor']
/** 项目映射空间承载的页面（含函数编排；编排不依赖选中项目，故与 projectViews 分开维护）。 */
export const projectSpaceViews = [...projectViews, ...flowViews]

/** 本体区侧栏菜单（扁平顺序即渲染顺序）；「更多工具」不进表，由 App 单独渲染。 */
export const menuOntology: Record<string, string> = { 'o-home': '工作概览', objects: '对象建模', library: '共享属性', rules: '业务规则', actions: '动作定义', 'o-release': '本体校验与发布' }
/** 字符图标（旧版侧栏）。2026-09 起侧栏改用 shared/icons.ts 的 SVG 线性图标，此处仅为向后兼容保留。 */
export const navIcon: Record<string, string> = { 'o-home': '▤', objects: '▦', rules: '§', actions: '↯', contracts: '{ }', library: '≣', 'o-release': '⚑', 'p-home': '▤', connections: '⇄', binding: '▦', implements: '{ }', 'f-home': '⌥', 'f-editor': '✎', tools: '⋯' }

/** 项目区菜单：顺序 = 项目概览 → 数据连接 → 对象映射 → 取值规则库 → 函数编排 → 项目校验与发布；
 *  函数编排固定在取值规则库下方，且不依赖选中项目。 */
export function menuProjectOf(hasProject: boolean): Record<string, string> {
  // 取值规则库（implements）2026-09-18 功能下线：取值能力统一收敛到函数编排；
  // 页面与路由保留（历史校验定位/旧深链可达），仅从菜单移除。
  const m: Record<string, string> = { 'p-home': '项目概览' }
  if (hasProject) { m.connections = '数据连接'; m.binding = '对象映射' }
  m['f-home'] = '函数编排'
  if (hasProject) { m['p-release'] = '项目校验与发布' }
  return m
}

/** 初始视图：hash 命中白名单（含 alias 归一）则取之，否则工作概览。 */
export function initialView(): string {
  const raw = window.location.hash.slice(1)
  return Object.keys(pages).includes(normalizeView(raw)) ? normalizeView(raw) : 'o-home'
}

/** 显式链接优先于历史工作区；函数编排页面归入项目映射空间。 */
export function initialSpace(hash:string,saved:string|null):'ontology'|'project' {
  const target=normalizeView(hash.replace(/^#/,''))
  if(target in pages)return projectSpaceViews.includes(target)?'project':'ontology'
  return saved==='project'||saved==='flow'?'project':'ontology'
}

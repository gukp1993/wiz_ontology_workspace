// 图谱语义常量 —— 自旧仓库 shared/constants.js 复制后按当前工作台五类定义扩展
//（旧三类 实体/属性/规则 → 当前 对象/共享属性/私有属性/规则/动作；颜色沿用旧三类并补两色）。
// TYPE_PREFIX 用于节点 ID 前缀；节点内部 ID = `lg:<TYPE_PREFIX>:<领域稳定ID>`（不因改名变化）。
export const SEMANTIC_TYPES = ['对象', '共享属性', '私有属性', '规则', '动作']
export const TYPE_COLOR = { 对象: '#3978c5', 共享属性: '#d15e9a', 私有属性: '#d18f3c', 规则: '#35a167', 动作: '#7a5fb5' }
export const TYPE_PREFIX = { 对象: 'obj', 共享属性: 'sp', 私有属性: 'pp', 规则: 'rule', 动作: 'action' }
export const NODE_PREFIX = 'lg'

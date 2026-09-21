// 属性/共享属性表单的辅助填写宿主适配（T6 · O2，2026-09-21）。
// 契约来源：./useAssistPanel.ts 的 AssistHostBinding；字段白名单唯一来源
// workbench/assist_fields.py（property/sharedProperty：label/comment/dataType/obsType/formatting）。
// 设计要点：
//   * draft() 只输出白名单键的扁平快照（'rdfs:label'→label、'rdfs:comment'→comment）；
//     dataType 为组件 selectedType 同值（时间序列='timeSeries'，否则=rdfs:range @id）；
//     obsType 仅时间序列出现；formatting 取 mg:formatting['@value']（未配置不出现）。
//     白名单外的内容（@id/domain/apiName 等）一律不出网。
//   * apply() 必须经组件既有写路径：dataType/obsType 走注入的 setType/setObsType
//     （即组件 setRange/setObservation，内部经 setPropertyDataType 重写 rdfs:range 与
//     mg:valueShape 并清理 formatting/decimalPlaces），禁止绕过联动直接赋值造成非法组合
//     （如离开时间序列残留观测值类型）；formatting 整组按 PropertyFormatting.sync 同规则
//     写/删 mg:formatting（effectiveConfig 判空，与显示格式编辑器完全一致）。
//   * snapshot()/restore() 对草稿节点整体 JSON 克隆；restore 原位替换键值，保持 draft
//     对象身份（reactive 引用与子组件 props 稳定，联动状态随内容自然恢复）。
//   * 本文件绝不调用任何保存函数（formSave/commit-now/touch）——持久化由用户在表单显式保存。
import type { AssistHostBinding } from './useAssistPanel'
import { effectiveConfig } from '../ontology/formattingOptions'

export type PropertyAssistTargetKind = 'property' | 'sharedProperty'

export interface PropertyAssistOptions {
  /** 组件本地草稿节点 getter（reactive draft；返回空值时快照为空对象、apply/restore 无操作） */
  draft: () => any
  targetKind: PropertyAssistTargetKind
  /** 编辑目标稳定 id；新建（尚未入图）传空串，后端按「新建属性定义/共享属性定义」出标题 */
  targetId: string
  /** 当前是否时间序列（组件 selectedType==='timeSeries'，经 propertyDataType 判定） */
  isTimeSeries: () => boolean
  /** 数据类型切换：组件 setRange 同一写路径（联动清理 valueShape/formatting/decimalPlaces） */
  setType: (v: string) => void
  /** 观测值类型切换：组件 setObservation 同一写路径（仅时间序列语义） */
  setObsType: (v: string) => void
}

const cloneJson = <T>(v: T): T => JSON.parse(JSON.stringify(v))
/** 观测值类型 = 草稿 rdfs:range（与组件 rangeId 计算属性同一取值） */
const rangeIdOf = (d: any): string => d?.['rdfs:range']?.['@id'] || 'xsd:string'

/** 属性/共享属性表单的宿主 binding 工厂：白名单快照 + 复用组件写路径的合并 + 节点级快照/恢复。 */
export function propertyAssistBinding(opts: PropertyAssistOptions): AssistHostBinding {
  return {
    space: 'ontology',
    targetKind: opts.targetKind,
    targetId: opts.targetId,
    contextTitle: opts.targetKind === 'sharedProperty' ? '共享属性定义' : '属性定义',
    /** 白名单快照：label/comment/dataType（+obsType 仅时间序列）（+formatting 仅已配置） */
    draft() {
      const d = opts.draft()
      if (!d) return {}
      const ts = opts.isTimeSeries()
      const snap: Record<string, unknown> = {
        label: d['rdfs:label'] ?? '',
        comment: d['rdfs:comment'] ?? '',
        dataType: ts ? 'timeSeries' : rangeIdOf(d),
      }
      if (ts) snap.obsType = rangeIdOf(d)
      const fmt = d['mg:formatting']?.['@value']
      if (fmt !== undefined && fmt !== null) snap.formatting = cloneJson(fmt)
      return snap
    },
    /** 建议合并：label/comment → dataType/obsType（原子组）→ formatting；类型联动全部经组件 setter */
    apply(values) {
      const d = opts.draft()
      if (!d || !values || typeof values !== 'object') return
      if (typeof values.label === 'string') d['rdfs:label'] = values.label
      if (typeof values.comment === 'string') d['rdfs:comment'] = values.comment
      // dataType/obsType 原子组：先类型后观测值。setType 内部经 setPropertyDataType 保证
      // rdfs:range 与 mg:valueShape 同步；离开时间序列时观测类型随 valueShape 清除，不残留。
      // 空 obsType（模型对「离开时间序列」的补位）不进 setter——setObservation 无空值语义。
      if (typeof values.dataType === 'string' && values.dataType) opts.setType(values.dataType)
      if (typeof values.obsType === 'string' && values.obsType && opts.isTimeSeries()) opts.setObsType(values.obsType)
      // formatting 整组放最后：类型切换会清既有 formatting，先写会被联动误删。
      if (values.formatting !== undefined) {
        const clean = effectiveConfig(values.formatting)
        if (clean) d['mg:formatting'] = { '@type': '@json', '@value': cloneJson(clean) }
        else delete d['mg:formatting']
      }
    },
    /** 采纳前快照：草稿节点整体 JSON 克隆（含白名单外字段，恢复零丢失） */
    snapshot() {
      const d = opts.draft()
      return d ? cloneJson(d) : null
    },
    /** 恢复快照：原位替换键值，保持 draft 对象身份（reactive 引用不换） */
    restore(snap) {
      const d = opts.draft()
      if (!d || !snap || typeof snap !== 'object') return
      for (const k of Object.keys(d)) delete d[k]
      Object.assign(d, cloneJson(snap))
    },
  }
}

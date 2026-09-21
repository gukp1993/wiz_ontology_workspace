// 动作接口映射（项目区 actionBinding）编辑弹窗的辅助填写宿主适配（T10 · P6，2026-09-21）。
// 契约来源：./useAssistPanel.ts 的 AssistHostBinding；字段白名单唯一来源
// workbench/assist_fields.py（actionBinding：method/path/bodyFormat/description/parameters/note）。
// 设计要点：
//   * draft() 只输出白名单键的扁平快照；认证与凭据（auth.* 等）是注册表 FORBIDDEN_KEYS，
//     永不出网——快照里没有 auth，模型建议即使混入 auth 键也会被 apply 忽略（双重排除）。
//   * parameters 是整组替换的复合组：出网行结构 {name,in,value:{from,...}}（与白名单 help
//     一致；行 id 属组件内部机制，不出网）；apply 用 actionHttpModel.newParamId（与组件
//     addParam/draftFrom 同一 id 机制）补齐行 id，行值按 from 收窄为 actionHttpModel 实际键形
//     （actionInput: inputId/inputName；instanceId；property: propertyId；constant: type/value），
//     与 frontend/src/project/actionHttpModel.ts 的 ParamValue 镜像。
//   * note 是弹窗「项目说明」本地草稿（noteDraft），经注入的 setNote 写回，随撤销一起恢复。
//   * snapshot()/restore() 面向客户端撤销：整草稿 JSON 克隆（含 auth——它只在浏览器内参与
//     撤销保真，不出网）；restore 原位替换键值，保持草稿对象身份（弹窗模板绑定不换引用）。
//   * 保存边界：apply 只把建议合并进弹窗本地草稿（面板提示「已填入，尚未保存」），绝不调用
//     form-save/commit-now/touch；持久化由用户点「保存」走既有 saveApi→toImplementation→formSave。
import type { AssistHostBinding } from './useAssistPanel'
import { newParamId } from '../project/actionHttpModel'

export interface ActionBindingAssistOptions {
  /** 项目 id（项目区场景必填，随 context 请求出网） */
  projectId: string
  /** 编辑目标：已保存绑定为 '<对象类型裸id>:<动作id>'（后端据此出标题并核验归属）；
   *  未保存的新配置传 ''（后端按「新建动作接口映射」出标题） */
  targetId: string
  /** 未取到上下文时的兜底标题 */
  contextTitle?: string
  /** 弹窗本地接口草稿 getter（actionHttpModel.ApiImplementation 形态；null=只读态：快照为空、apply/restore 无操作） */
  draft: () => any
  /** 「项目说明」本地草稿 getter（弹窗 noteDraft） */
  note: () => string
  /** 「项目说明」写回（弹窗 noteDraft） */
  setNote: (v: string) => void
}

const cloneJson = <T,>(v: T): T => JSON.parse(JSON.stringify(v))

/** 单行参数的出网快照：只保留白名单声明的键形；行 id 与一切未知键不出网。 */
function paramSnapshot(row: any): Record<string, unknown> {
  const r = row && typeof row === 'object' ? row : {}
  const value = r.value && typeof r.value === 'object' ? r.value as Record<string, unknown> : {}
  const from = String(value.from || '')
  const out: Record<string, unknown> = { from }
  if (from === 'actionInput') {
    if (value.inputId) out.inputId = String(value.inputId)
    if (value.inputName) out.inputName = String(value.inputName)
  } else if (from === 'property') {
    if (value.propertyId) out.propertyId = String(value.propertyId)
  } else if (from === 'constant') {
    if (value.type) out.type = String(value.type)
    if (value.value !== undefined && value.value !== null) out.value = value.value
  }
  return { name: String(r.name || ''), in: String(r.in || ''), value: out }
}

/** 建议行 → 弹窗参数行（整组替换用）：行 id 由组件既有 newParamId 机制补齐（建议行不带 id）。 */
function paramFromProposal(row: unknown): Record<string, any> {
  const r = row && typeof row === 'object' ? row as Record<string, unknown> : {}
  const value = r.value && typeof r.value === 'object' ? r.value as Record<string, unknown> : {}
  const from = String(value.from || '')
  const out: Record<string, any> = { from }
  if (from === 'actionInput') {
    if (value.inputId) out.inputId = String(value.inputId)
    if (value.inputName) out.inputName = String(value.inputName)
  } else if (from === 'property') {
    if (value.propertyId) out.propertyId = String(value.propertyId)
  } else if (from === 'constant') {
    if (value.type) out.type = String(value.type)
    if (value.value !== undefined && value.value !== null) out.value = value.value
  }
  return {
    id: typeof r.id === 'string' && r.id ? r.id : newParamId(),
    name: String(r.name || ''),
    in: String(r.in || 'body'),
    value: out,
  }
}

/** 动作接口映射编辑弹窗的宿主 binding 工厂：白名单快照 + 整组替换合并 + 草稿级快照/恢复。 */
export function actionBindingAssistBinding(opts: ActionBindingAssistOptions): AssistHostBinding {
  return {
    space: 'project',
    projectId: opts.projectId,
    targetKind: 'actionBinding',
    targetId: opts.targetId,
    contextTitle: opts.contextTitle || '动作接口映射辅助填写',
    /** 白名单快照：method/path/bodyFormat/description/parameters/note；绝不包含 auth。 */
    draft() {
      const d = opts.draft()
      if (!d) return {}
      return {
        method: String(d.method || ''),
        path: String(d.path || ''),
        bodyFormat: String(d.bodyFormat || ''),
        description: String(d.description || ''),
        parameters: (Array.isArray(d.parameters) ? d.parameters : []).map(paramSnapshot),
        note: String(opts.note() ?? ''),
      }
    },
    /** 建议合并：method/path/bodyFormat/description/note 直写；parameters 整组替换
     *  （非对象行剔除）；undefined/类型不符不覆盖；auth 不在写列表——即使 values 混入
     *  auth 键也到此为止。 */
    apply(values) {
      const d = opts.draft()
      if (!d || !values || typeof values !== 'object') return
      const v = values as Record<string, unknown>
      if (typeof v.method === 'string') d.method = v.method
      if (typeof v.path === 'string') d.path = v.path
      if (typeof v.bodyFormat === 'string') d.bodyFormat = v.bodyFormat
      if (typeof v.description === 'string') d.description = v.description
      if (Array.isArray(v.parameters)) d.parameters = v.parameters
        .filter(row => row && typeof row === 'object')
        .map(paramFromProposal)
      if (typeof v.note === 'string') opts.setNote(v.note)
    },
    /** 采纳前快照：弹窗草稿整体 JSON 克隆（含 auth/kind/schemaVersion 等非白名单字段，撤销零丢失）。 */
    snapshot() {
      const d = opts.draft()
      if (!d) return null
      return { impl: cloneJson(d), note: String(opts.note() ?? '') }
    },
    /** 恢复快照：原位替换键值，保持草稿对象身份（reactive 引用不换）；说明一并恢复。 */
    restore(snap) {
      const d = opts.draft()
      const s = snap && typeof snap === 'object' ? snap as { impl?: unknown; note?: unknown } : null
      if (!d || !s || !s.impl || typeof s.impl !== 'object') return
      for (const k of Object.keys(d)) delete d[k]
      Object.assign(d, cloneJson(s.impl))
      if (typeof s.note === 'string') opts.setNote(s.note)
    },
  }
}

// 整表自动填写面板状态机门面（T3，2026-09-22 改版重写）。可脱离 DOM 独立测试
// （tests/assist_panel.test.mjs、tests/test_autofill_state.mjs）。
// 契约唯一来源：文档/接口文档/04-编排与LLM接口.md §5（check/explain 兼容）与 §6（autofill/1）；
// 协议镜像 ./types.ts；状态机实现 ./formAutofill.ts（本文件保持既有导出名，减少宿主 import 变动）。
//
// 对外暴露（新交互，替代旧「建议卡→勾选→采纳」主流程）：
//   * open/close/expand、intent、generate、answer(questionId,value,unsure)、undoRound/canUndo、
//     roundSummary/statusBarText（宿主渲染状态条：已填 N 项/待补 M 项/逐字段旧值→新值）；
//   * check/explain 降为「更多帮助」次要模式（runHelp，只读渲染，不提供任何写入）。
// 保存边界：回填只写宿主本地草稿（applyDraft/apply），绝不调用 form-save/commit-now/touch。
import { postJson } from '../app/http'
import {
  createAutofillEngine,
  type AssistApi as EngineApi,
  type AssistHostBinding,
  type AutofillEngine,
  type AutofillFieldCodec,
  type AutofillHostBinding,
  type AutofillSchemaInfo,
  type AutofillStatus,
  type AppliedOpFailure,
  type AppliedOpRecord,
  type ApplyOperationsResult,
  type RoundFieldChange,
  type RoundSummary,
} from './formAutofill'

// ── 既有导出名（类型）：宿主组件与 binding 适配器继续从这里导入 ────────────────────
export type { AssistHostBinding, AutofillHostBinding, AutofillFieldCodec }
export type {
  AutofillSchemaInfo, AutofillStatus, RoundSummary, RoundFieldChange,
  ApplyOperationsResult, AppliedOpRecord, AppliedOpFailure,
}
export { AUTOFILL_EMPTY_TEXT, applyOperations, topLevelChanges } from './formAutofill'
export type { AutofillEngine }

// AssistApi 保持旧名；形状随 autofill/1 扩展（protocol:2 / sessionId / answers 数组形态）。
export type AssistApi = EngineApi

/** 默认 API 工厂：POST /api/assist-context、/api/assist-generate；错误经 SaveRequestError 透传。 */
export function defaultAssistApi(): AssistApi {
  return {
    context: (body) => postJson('/api/assist-context', body) as ReturnType<EngineApi['context']>,
    generate: (body) => postJson('/api/assist-generate', body) as ReturnType<EngineApi['generate']>,
  }
}

/** 面板控制器：引擎 + 旧名别名（reopen=expand、clearNotice）。 */
export interface AssistPanelController extends AutofillEngine {
  /** 旧名别名：重新展开抽屉（作废在途请求） */
  reopen(): void
  /** 清除瞬时提示 */
  clearNotice(): void
}

export function useAssistPanel(api: AssistApi = defaultAssistApi()): AssistPanelController {
  const engine = createAutofillEngine(api)
  return {
    ...engine,
    reopen: () => engine.expand(),
    clearNotice: () => { engine.notice.value = '' },
  }
}

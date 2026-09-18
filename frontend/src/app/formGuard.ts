// 表单守卫契约（GLM 指令 2026-09-15 · T00）。
// 局部表单（属性/对象/链接/契约/实现/连接/参数/版本等编辑态）遵守：
//  · 打开表单 register、关闭（保存成功或取消）unregister；
//  · 打字只改本地草稿，不 touch 自动保存；保存走 inject('form-save') 的 submitForm；
//  · isDirty() 报告本地草稿相对打开时是否有变化；discard() 由 App 在用户确认
//    “放弃本次修改并离开”后调用，表单负责丢弃草稿并退出编辑态。
// App 据此：顶栏显示“正在编辑表单”、导航/切区/切本体/切项目统一离开保护、
// 刷新关闭经 beforeunload 保护。画布完整操作继续走 changed() 自动保存，不经此契约。
export interface FormGuardInstance {
  isDirty(): boolean
  discard(): void
}

export interface FormGuardAPI {
  register(guard: FormGuardInstance): void
  unregister(guard: FormGuardInstance): void
  hasDirty(): boolean       // 发布等关键动作前检查
  editing(): boolean        // 是否有表单打开（顶栏状态文案）
}

// 各页面编辑器接入模板：
//   const guardApi = inject<FormGuardAPI>('form-guard')!
//   const guard = { isDirty: () => !!draft.value && JSON.stringify(draft.value) !== original, discard: () => closeEditor() }
//   watch(editorOpen, (open) => open ? guardApi.register(guard) : guardApi.unregister(guard), { immediate: true })
//   onBeforeUnmount(() => guardApi.unregister(guard))   // 视图切换即卸载，防泄漏
export type FormSaveResult = { ok: boolean; message: string }
/** 可选操作描述（20260918 撤销优化）：成功保存登记一条具名历史；失败/异常/无变化不入栈。 */
export interface FormSaveAction { actionLabel: string; target?: { kind: string; id: string; ownerId?: string } }
export interface FormSaveAPI {
  // area：表单所属工作区；mutate：把已校验的本地草稿合入 working（此时 working 会被提交）。
  // action：具名历史（候选事务——成功才入栈一条，失败/异常取消且不清 redo）。
  // 成功：返回 {ok:true}，调用方关闭表单并定位条目（一次保存即持久化，无二次总保存）。
  // 失败：working 已回滚到提交前状态，错误经 message 返回；表单保持打开、本地输入保留，
  // 后台队列不会重提已取消的修改（Saver.clearFailed）。conflict 时状态栏保留处理入口。
  submitForm(area: 'ontology' | 'project', mutate: () => void, action?: FormSaveAction): Promise<FormSaveResult>
}

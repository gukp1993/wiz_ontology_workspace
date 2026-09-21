// 应用内确认弹窗（替代阻塞式 window.confirm）：
// 挂 document.body 下 .modal-backdrop > section.modal-card，复用全局样式类；
// Escape/点击背板 → false，确认按钮/Enter → true；并发调用串行排队，互不破坏。

export interface AppConfirmOptions {
  message: string
  title?: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}

// 串行队列：后一次调用等前一次 resolve 后才弹窗（各自独立弹窗会互相抢焦点/背板）。
let queue: Promise<unknown> = Promise.resolve()

export function appConfirm(opts: AppConfirmOptions): Promise<boolean> {
  const turn = () => openDialog(opts)
  const result = queue.then(turn, turn)
  queue = result.then(() => undefined, () => undefined)
  return result
}

function openDialog(opts: AppConfirmOptions): Promise<boolean> {
  return new Promise<boolean>(resolve => {
    const previousFocus = document.activeElement as HTMLElement | null

    const backdrop = document.createElement('div')
    // app-confirm-backdrop：确认框必须压过一切业务弹层（如导入弹窗 .imp-backdrop z-120），
    // 否则从高层弹层里触发的确认框会被盖住——不可见且整屏吞点击，页面表现为"点了没反应"。
    backdrop.className = 'modal-backdrop app-confirm-backdrop'
    const card = document.createElement('section')
    card.className = 'modal-card'
    card.setAttribute('role', 'dialog')
    card.setAttribute('aria-modal', 'true')

    if (opts.title) {
      const h2 = document.createElement('h2')
      h2.textContent = opts.title
      card.appendChild(h2)
    }
    const body = document.createElement('p')
    body.textContent = opts.message
    card.appendChild(body)

    const tools = document.createElement('div')
    tools.className = 'dialogtools'
    const cancelBtn = document.createElement('button')
    cancelBtn.type = 'button'
    cancelBtn.textContent = opts.cancelLabel || '取消'
    const confirmBtn = document.createElement('button')
    confirmBtn.type = 'button'
    confirmBtn.className = opts.danger ? 'danger-btn' : 'primary'
    confirmBtn.textContent = opts.confirmLabel || '确定'
    tools.append(cancelBtn, confirmBtn)
    card.appendChild(tools)
    backdrop.appendChild(card)

    function finish(ok: boolean) {
      document.removeEventListener('keydown', onKeydown, true)
      backdrop.remove()
      previousFocus?.focus?.()
      resolve(ok)
    }
    function onKeydown(e: KeyboardEvent) {
      if (e.key === 'Escape') { e.stopPropagation(); finish(false); return }
      if (e.key === 'Enter') {
        if (document.activeElement === cancelBtn) return // 焦点在取消上：走原生激活（取消）
        e.preventDefault()
        finish(true)
        return
      }
      if (e.key === 'Tab') { // 简单焦点圈禁：卡内只有两个按钮，手动循环
        e.preventDefault()
        ;(document.activeElement === confirmBtn ? cancelBtn : confirmBtn).focus()
      }
    }

    cancelBtn.addEventListener('click', () => finish(false))
    confirmBtn.addEventListener('click', () => finish(true))
    backdrop.addEventListener('click', e => { if (e.target === backdrop) finish(false) })

    document.body.appendChild(backdrop)
    document.addEventListener('keydown', onKeydown, true)
    confirmBtn.focus()
  })
}

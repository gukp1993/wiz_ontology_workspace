// 弹窗焦点管理（集中式）：任何 [aria-modal="true"] 的卡出现即把焦点移入，关闭后归还；
// Tab 在卡内圈禁；焦点不在任何卡内时 Escape 转交给最上层卡片。此前每个弹窗各自实现
// （或根本没实现），漏一个就是 B6 那类缺陷：Tab 三次逃出弹窗、焦点落在遮罩下仍可用
// Enter 激活的按钮上。
// 关闭动作本身不集中做：只转交事件，取消语义（脏表单二次确认、只取消不删的确认框）留在各弹窗里。
// 不做集中 inert：inert 写漏会把整屏变成吞点击，代价高于收益。

const FOCUSABLE = 'a[href],button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"]),[contenteditable="true"]'
// 自行管理焦点的弹窗（appConfirm 有自己的 Tab/Enter/Escape 实现）标这个属性退出接管。
const MANUAL = '[data-modal-a11y="manual"]'

const stack: HTMLElement[] = [] // 打开顺序，末尾＝最上层；关闭即移出
const previousFocus = new WeakMap<Element, HTMLElement | null>()
// 自管焦点的弹窗在场时完全不插手：appConfirm 通常盖在业务弹窗之上，若仍按下层卡圈禁，
// 会把焦点从确认框里抢走。
const manualOpen = new Set<HTMLElement>()

function isShown(el: Element): boolean {
  return el.getClientRects().length > 0
}

function focusables(modal: HTMLElement, active?: Element | null): HTMLElement[] {
  const list = [...modal.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(isShown)
  // AppSelect 的下拉面板 Teleport 到 body：不并进来时它是陷阱外的节点，
  // 面板展开的一瞬间 Tab 会跑到背层按钮上（shared/AppSelect.vue 的 tabAway 也按全局序列跳）。
  const owner = modal.querySelector<HTMLElement>('.app-select-trigger[aria-expanded="true"]')
  if (owner) {
    for (const panel of document.querySelectorAll<HTMLElement>('.app-select-panel')) {
      if (!isShown(panel)) continue
      list.push(...[...panel.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(isShown))
      // 下拉打开时焦点仍在触发器上（真实鼠标点选项即如此）；面板里的选项不是原生 option，
      // 浏览器不会把焦点交还给 owner，所以列表必须留着它，否则下一格就漏到背层。
      if (active === owner && !list.includes(owner)) list.push(owner)
    }
  }
  return list
}

function topmostShown(): HTMLElement | null {
  for (let i = stack.length - 1; i >= 0; i--) if (isShown(stack[i])) return stack[i]
  return null
}

function onModalAdded(modal: HTMLElement) {
  if (!isShown(modal)) return
  if (modal.matches(MANUAL)) { manualOpen.add(modal); return }
  if (stack.includes(modal)) return
  previousFocus.set(modal, document.activeElement instanceof HTMLElement ? document.activeElement : null)
  stack.push(modal)
  // 宏任务而非 rAF：Vue 一次性插入整棵卡片子树，宏任务边界已足够拿到控件；
  // rAF 在隐藏/遮挡的标签页里永不回调（实测 document.hidden 时焦点移入静默失效）。
  setTimeout(() => {
    if (!stack.includes(modal) || !isShown(modal)) return
    ;(focusables(modal)[0] ?? modal).focus({ preventScroll: true })
  }, 0)
}

function onModalRemoved(modal: Element) {
  manualOpen.delete(modal as HTMLElement)
  const i = stack.indexOf(modal as HTMLElement)
  if (i < 0) return
  stack.splice(i, 1)
  const prev = previousFocus.get(modal)
  if (prev?.isConnected) prev.focus({ preventScroll: true })
}

function eachModal(root: ParentNode, fn: (el: HTMLElement) => void) {
  root.querySelectorAll<HTMLElement>('[aria-modal="true"]').forEach(fn)
  if (root instanceof HTMLElement && root.matches('[aria-modal="true"]')) fn(root)
}

function onMutation(muts: MutationRecord[]) {
  for (const m of muts) {
    m.removedNodes.forEach(n => { if (n instanceof Element) eachModal(n, onModalRemoved) })
    m.addedNodes.forEach(n => { if (n instanceof Element) eachModal(n, onModalAdded) })
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key !== 'Tab' && e.key !== 'Escape') return
  if ([...manualOpen].some(isShown)) return // 自管弹窗（appConfirm）在场时由它自己圈禁
  // 焦点已在卡内用该卡；已逃到背层则拉回最上层打开的卡。
  const own = e.target instanceof HTMLElement ? e.target.closest<HTMLElement>('[aria-modal="true"]') : null
  const modal = own && stack.includes(own) ? own : topmostShown()
  if (!modal) return
  if (e.key === 'Escape') {
    // 焦点不在任何已知卡内（典型是它掉到了 body）时，各卡自己的 @keydown.esc 收不到事件，
    // Escape 就静默失效。转交给最上层卡片：不冒泡，只触发卡片自己那一份处理器，
    // 于是"关闭走取消路径（含脏表单确认）、一次只关一层"的既有语义都留在各弹窗内部。
    if (!own || !stack.includes(own)) modal.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: false }))
    return
  }
  const list = focusables(modal, document.activeElement)
  if (!list.length) return
  e.preventDefault()
  const i = list.indexOf(document.activeElement as HTMLElement)
  const next = e.shiftKey ? (i <= 0 ? list.length - 1 : i - 1) : (i === -1 || i === list.length - 1 ? 0 : i + 1)
  list[next].focus({ preventScroll: true })
}

export function installModalA11y(): void {
  new MutationObserver(onMutation).observe(document.body, { childList: true, subtree: true })
  document.addEventListener('keydown', onKeydown, true)
  eachModal(document.body, onModalAdded)
}

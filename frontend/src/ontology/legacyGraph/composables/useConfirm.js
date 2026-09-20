// 全局确认弹窗（App.vue 渲染 ModalConfirm），返回 Promise
import { reactive } from 'vue'

const state = reactive({ show: false, msg: '', okText: '确认', _resolve: null })

export function confirmDialog(msg, okText = '确认') {
  return new Promise((resolve) => {
    state.msg = msg
    state.okText = okText
    state._resolve = resolve
    state.show = true
  })
}

export function closeConfirm(result) {
  state.show = false
  if (state._resolve) {
    state._resolve(result)
    state._resolve = null
  }
}

export function useConfirm() {
  return { state }
}

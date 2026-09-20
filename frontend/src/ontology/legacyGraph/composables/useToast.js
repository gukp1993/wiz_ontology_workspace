// 全局 toast（App.vue 渲染 TheToast）
import { reactive } from 'vue'

const state = reactive({ show: false, msg: '', isError: false })
let timer = null

export function toast(msg, isError = false) {
  state.msg = msg
  state.isError = isError
  state.show = true
  clearTimeout(timer)
  timer = setTimeout(() => { state.show = false }, 3200)
}

export function useToast() {
  return { state }
}

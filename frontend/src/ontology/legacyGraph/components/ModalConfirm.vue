<template>
  <div class="modal-mask" :class="{ show: state.show }">
    <div class="modal">
      <h3>确认</h3>
      <p class="msg">{{ state.msg }}</p>
      <div class="btns">
        <button class="btn" @click="closeConfirm(false)">取消</button>
        <button class="btn primary" @click="closeConfirm(true)">{{ state.okText }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useConfirm, closeConfirm } from '../composables/useConfirm'

const { state } = useConfirm()
</script>

<style scoped>
.msg {
  font-size: 13px; color: var(--ink); line-height: 1.65; margin: 0 0 4px;
  white-space: pre-line; word-break: break-word;
}
</style>

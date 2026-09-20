<template>
  <div class="toast" :class="{ show: state.show, error: state.isError }">{{ state.msg }}</div>
</template>

<script setup>
import { useToast } from '../composables/useToast'

const { state } = useToast()
</script>

<style scoped>
.toast {
  position: fixed; z-index: 1000; left: 50%; bottom: 18px;
  transform: translateX(-50%) translateY(8px);
  background: #172126; color: #fff; padding: 9px 14px; border-radius: 6px;
  font-size: 11px; box-shadow: 0 12px 34px rgba(23, 33, 38, .13);
  opacity: 0; transition: .18s; max-width: 80vw; pointer-events: none;
}
.toast.show { opacity: 1; transform: translateX(-50%); }
.toast.error { background: #b91c1c; }
</style>

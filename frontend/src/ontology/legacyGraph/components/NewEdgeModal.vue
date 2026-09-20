<template>
  <div class="modal-mask" :class="{ show }">
    <div class="modal">
      <h3>新建连线</h3>
      <div class="field">
        <label>方向</label>
        <div class="dir">{{ sourceName }} → {{ targetName }}</div>
      </div>
      <div class="field">
        <label>关系名（图上标签）</label>
        <input v-model="relation" type="text" placeholder="如 包含" autocomplete="off" @keydown.enter="submit" />
      </div>
      <div class="field">
        <label>描述</label>
        <textarea v-model="description" placeholder="这条连线的含义说明"></textarea>
      </div>
      <p class="err" :class="{ show: !!error }">{{ error }}</p>
      <div class="btns">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" @click="submit">连接</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  show: { type: Boolean, required: true },
  sourceName: { type: String, default: '' },
  targetName: { type: String, default: '' },
})
const emit = defineEmits(['create', 'close'])

const relation = ref('')
const description = ref('')
const error = ref('')

watch(() => props.show, (on) => {
  if (on) { relation.value = ''; description.value = ''; error.value = ''; }
})

function submit() {
  const r = relation.value.trim()
  if (!r) { error.value = '请填写关系名（连线上显示的标签）'; return }
  emit('create', { relation: r, description: description.value.trim() })
}
</script>

<style scoped>
.dir { font-size: 13px; }
</style>

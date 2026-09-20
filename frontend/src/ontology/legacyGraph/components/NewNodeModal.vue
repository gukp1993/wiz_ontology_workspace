<template>
  <div class="modal-mask" :class="{ show }">
    <div class="modal">
      <h3>新建节点</h3>
      <div class="field">
        <label>节点类型</label>
        <select v-model="type">
          <option v-for="t in SEMANTIC_TYPES" :key="t" :value="t">{{ t }}</option>
        </select>
      </div>
      <div class="field">
        <label>节点名称</label>
        <input v-model="name" type="text" placeholder="如 储能系统" autocomplete="off" @keydown.enter="submit" />
      </div>
      <p class="err" :class="{ show: !!error }">{{ error }}</p>
      <div class="btns">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" @click="submit">创建</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { SEMANTIC_TYPES } from '../shared/constants'

const props = defineProps({
  show: { type: Boolean, required: true },
  existingNames: { type: Array, default: () => [] },
})
const emit = defineEmits(['create', 'close'])

const type = ref('实体')
const name = ref('')
const error = ref('')

watch(() => props.show, (on) => {
  if (on) { type.value = '实体'; name.value = ''; error.value = ''; }
})

function submit() {
  const n = name.value.trim()
  if (!n) { error.value = '请输入节点名称'; return }
  if (props.existingNames.includes(n)) { error.value = `节点名称「${n}」已存在`; return }
  emit('create', { type: type.value, name: n })
}
</script>

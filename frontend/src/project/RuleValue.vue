<script setup lang="ts">
import {computed} from 'vue'
import AppSelect from '../shared/AppSelect.vue'
const props=defineProps<{modelValue:string;options:{value:string;label:string}[];label:string;literalOptions?:{value:string;label:string}[]}>()
const emit=defineEmits(['update:modelValue'])
const linked=computed(()=>String(props.modelValue||'').startsWith('{{'))
</script>
<template><div class="rule-value"><AppSelect :aria-label="label+'填写方式'" :model-value="linked?'reference':'literal'" :options="[{value:'literal',label:'固定内容'},{value:'reference',label:'引用参数 / 上一步结果'}]" @update:model-value="emit('update:modelValue',$event==='reference'?(options[0]?.value||'{{请选择}}'):'')"/>
<AppSelect v-if="linked" :aria-label="label" :model-value="modelValue" :options="options" searchable placeholder="选择引用" @update:model-value="emit('update:modelValue',$event)"/>
<AppSelect v-else-if="literalOptions?.length" :aria-label="label" :model-value="modelValue" :options="literalOptions" searchable @update:model-value="emit('update:modelValue',$event)"/><input v-else :aria-label="label" :value="modelValue" placeholder="填写固定内容" @input="emit('update:modelValue',($event.target as HTMLInputElement).value)"></div></template>
<style scoped>.rule-value{display:grid;grid-template-columns:minmax(145px,1fr) minmax(150px,1.4fr);gap:8px}</style>

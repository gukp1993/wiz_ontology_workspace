<script setup lang="ts">
import {ref,watch} from 'vue'
import AppSelect from './AppSelect.vue'
type Option=string|{value:string;label:string;disabled?:boolean}
// role 曾用于展示"业务专家/开发／实施"角标，20260917 浅色改版移除渲染；
// prop 仍保留（不再有默认值）以兼容旧调用方传参，避免落到根元素上。
const props=withDefaults(defineProps<{modelValue?:string|number|boolean;label:string;help?:string;example?:string;required?:boolean;role?:string;type?:string;options?:Option[];disabled?:boolean;min?:number;max?:number}>(),{type:'text'})
const emit=defineEmits(['update:modelValue','before-change'])
const touched=ref(false)
let editing=false
let lastEmitted: string|number|boolean|undefined
// A restore can replace the model while the control remains focused.
watch(()=>props.modelValue,value=>{if(editing&&!Object.is(value,lastEmitted))editing=false})
function endEdit(){editing=false;touched.value=true}
function input(e:Event){
 const raw=(e.target as HTMLInputElement).value
 const value=props.type==='number'?(raw===''?'':Number(raw)):raw
 const history=['historyUndo','historyRedo'].includes((e as InputEvent).inputType)
 if(history)editing=false
 if(Object.is(value,props.modelValue))return
 if(!editing){emit('before-change');editing=true}
 lastEmitted=value
 emit('update:modelValue',value)
 // Native undo/redo is an independent change, not part of subsequent typing.
 if(history)editing=false
}
</script>
<template><label class="editor-field"><span class="field-title">{{label}} <b v-if="required" class="required-mark">*</b><span v-else class="optional-label">选填</span></span><AppSelect v-if="type==='select'" :model-value="modelValue" :options="options" :disabled="disabled" :aria-label="label" :aria-required="required" @before-change="emit('before-change')" @update:model-value="emit('update:modelValue',$event)"/><textarea v-else-if="type==='textarea'" :value="String(modelValue??'')" :disabled="disabled" :aria-label="label" :aria-required="required" :placeholder="example?'例如：'+example:'请填写'+label" @input="input" @blur="endEdit" /><input v-else :type="type" :value="String(modelValue??'')" :disabled="disabled" :min="min" :max="max" :aria-label="label" :aria-required="required" :placeholder="example?'例如：'+example:'请填写'+label" @input="input" @blur="endEdit"><small v-if="help" class="field-help">{{help}}</small><small v-if="example && type==='select'" class="field-help">示例：{{example}}</small><small v-if="touched && required && !String(modelValue??'').trim()" class="field-error">请填写{{label}}；也可以先保存草稿，稍后补齐。</small></label></template>

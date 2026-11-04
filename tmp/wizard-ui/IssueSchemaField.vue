<script setup lang="ts">
import { computed } from 'vue'
import { initialValue, type JsonSchema } from '@/lib/tender-package-wizard'
defineOptions({ name: 'IssueSchemaField' })
const props = defineProps<{ schema: JsonSchema; modelValue: any; disabled?: boolean; required?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: any] }>()
const options = computed(() => props.schema['x-options'] || (props.schema.enum || []).map(v => ({ value: String(v), label: String(v) })))
const list = computed<any[]>(() => Array.isArray(props.modelValue) ? props.modelValue : [])
function property(key: string, value: any) { emit('update:modelValue', { ...(props.modelValue || {}), [key]: value }) }
function entry(index: number, value: any) { const copy = [...list.value]; copy[index] = value; emit('update:modelValue', copy) }
function toggle(value: string, selected: boolean) { emit('update:modelValue', selected ? [...list.value, value] : list.value.filter(v => v !== value)) }
</script>
<template>
  <fieldset v-if="schema.type === 'object'" :disabled="disabled" class="object-fields">
    <legend v-if="schema.title">{{ schema.title }}</legend>
    <IssueSchemaField v-for="(child, key) in schema.properties" :key="key" :schema="child" :model-value="modelValue?.[key]" :required="schema.required?.includes(String(key))" :disabled="disabled" @update:model-value="property(String(key), $event)" />
  </fieldset>
  <div v-else class="field">
    <label class="field-title">{{ schema.title || '请填写' }}<span v-if="required" aria-label="必填"> *</span></label>
    <p v-if="schema.description" class="help">{{ schema.description }}</p>
    <template v-if="schema.type === 'array' && schema['x-kind'] !== 'multi_choice'">
      <div v-for="(value, i) in list" :key="i" class="array-item">
        <IssueSchemaField :schema="schema.items || {}" :model-value="value" :disabled="disabled" @update:model-value="entry(i, $event)" />
        <button type="button" :disabled="disabled" @click="emit('update:modelValue', list.filter((_, j) => i !== j))">移除此条</button>
      </div>
      <button type="button" :disabled="disabled || (schema.maxItems !== undefined && list.length >= schema.maxItems)" @click="emit('update:modelValue', [...list, initialValue(schema.items || {})])">添加一条</button>
    </template>
    <div v-else-if="schema['x-kind'] === 'multi_choice'" class="choices">
      <label v-for="o in options" :key="o.value"><input type="checkbox" :disabled="disabled" :checked="list.includes(o.value)" @change="toggle(o.value, ($event.target as HTMLInputElement).checked)">{{ o.label }}</label>
    </div>
    <select v-else-if="schema.enum || schema['x-kind'] === 'single_choice'" :aria-label="schema.title" :disabled="disabled" :value="modelValue ?? ''" @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)">
      <option value="" disabled>请选择，不自动代选</option><option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option>
    </select>
    <input v-else-if="schema['x-kind'] === 'file' || schema.format === 'binary'" type="file" :aria-label="schema.title" :disabled="disabled" @change="emit('update:modelValue', ($event.target as HTMLInputElement).files?.[0])">
    <label v-else-if="schema.type === 'boolean' || schema['x-kind'] === 'confirm'" class="confirm"><input type="checkbox" :disabled="disabled" :checked="modelValue === true" @change="emit('update:modelValue', ($event.target as HTMLInputElement).checked)">我已核对并确认</label>
    <input v-else-if="schema.type === 'number' || schema.type === 'integer'" type="number" :aria-label="schema.title" :disabled="disabled" :min="schema.minimum ?? schema.min" :max="schema.maximum ?? schema.max" :step="schema.type === 'integer' ? 1 : 'any'" :value="modelValue ?? ''" @input="emit('update:modelValue', ($event.target as HTMLInputElement).value === '' ? undefined : Number(($event.target as HTMLInputElement).value))">
    <textarea v-else :aria-label="schema.title" :disabled="disabled" :value="modelValue ?? ''" rows="2" @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)" />
  </div>
</template>
<style scoped>
.object-fields{border:0;padding:0;min-width:0;display:grid;gap:18px}.field{display:grid;gap:8px}.field-title{font-weight:600;font-size:14px}.field-title span{color:#b91c1c}.help{color:var(--muted-foreground,#65748b);font-size:13px}.choices{display:grid;gap:10px;max-height:340px;overflow:auto}.choices label,.confirm{display:flex;gap:10px;align-items:start;font-size:14px}.array-item{border:1px solid var(--border,#dde3eb);border-radius:10px;padding:16px;margin-bottom:12px}select,textarea,input[type=number]{border:1px solid var(--border,#cbd5e1);border-radius:8px;padding:10px 12px;background:var(--background,#fff);color:inherit;width:100%;box-sizing:border-box}textarea{resize:vertical}button{border:1px solid var(--border,#cbd5e1);border-radius:6px;padding:6px 12px;color:inherit;background:var(--background,#fff);cursor:pointer}button:disabled{opacity:.5}
</style>

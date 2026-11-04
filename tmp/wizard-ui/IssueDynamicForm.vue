<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import IssueSchemaField from './IssueSchemaField.vue'
import { initialValue, validate, type Issue, type JsonSchema } from '@/lib/tender-package-wizard'
const props = defineProps<{ issue: Issue; busy: boolean }>()
const emit = defineEmits<{ submit: [data: Record<string, any>] }>()
const value = ref<any>({}), errors = ref<string[]>([])
const schema = computed<JsonSchema>(() => {
  const source = props.issue.action.schema
  if (source.type === 'object') return source
  const kind = props.issue.action.kind, options = props.issue.action.options
  const field: JsonSchema = { ...source, 'x-kind': kind, 'x-options': options }
  if (kind === 'single_choice') { field.type = 'string'; field.enum ??= options.map(o => o.value) }
  else if (kind === 'multi_choice') { field.type = 'array'; field.items ??= { type: 'string', enum: options.map(o => o.value) }; field.minItems ??= 1 }
  else if (kind === 'confirm') { field.type = 'boolean'; field.const = true }
  else if (kind === 'file') { field.type = 'string'; field.format = 'binary' }
  else field.type ??= kind === 'number' ? 'number' : 'string'
  return { type: 'object', required: ['value'], properties: { value: field } }
})
watch(() => props.issue.id, () => {
  const preset = props.issue.action.default
  value.value = preset === null || preset === undefined ? initialValue(schema.value) : props.issue.action.schema.type === 'object' ? preset : { value: preset }
  errors.value = []
}, { immediate: true })
function submit() {
  errors.value = validate(schema.value, value.value)
  if (!errors.value.length) emit('submit', value.value)
}
</script>
<template>
  <form novalidate @submit.prevent="submit">
    <IssueSchemaField v-model="value" :schema="schema" :disabled="busy || !!issue.unavailable_reason" />
    <ul v-if="errors.length" role="alert" class="errors"><li v-for="e in errors" :key="e">{{ e }}</li></ul>
    <p v-if="issue.unavailable_reason" class="reason">{{ issue.unavailable_reason }} <slot name="advanced" /></p>
    <button type="submit" class="save" :disabled="busy || !issue.target_action || !!issue.unavailable_reason" :title="busy ? '正在处理，请稍候' : issue.unavailable_reason || (!issue.target_action ? '请到高级模式处理' : '保存当前核验并继续')">{{ busy ? '正在保存…' : '确认并继续' }}</button>
    <p class="audit">确认人使用当前登录账号，确认时间与依据由现有接口留痕。空白答案不会提交。</p>
  </form>
</template>
<style scoped>.errors{color:#b91c1c;margin:16px 0}.reason{margin:16px 0;color:#a16207}.save{margin-top:22px;background:#1d4ed8;color:white;border:0;border-radius:8px;padding:11px 22px;cursor:pointer;font-weight:600}.save:disabled{opacity:.5;cursor:not-allowed}.audit{font-size:12px;color:var(--muted-foreground,#64748b);margin-top:12px}</style>

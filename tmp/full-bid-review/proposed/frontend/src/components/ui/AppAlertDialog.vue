<script setup lang="ts">
import { AlertTriangle } from 'lucide-vue-next'
import AppDialog from './AppDialog.vue'

defineProps<{
  open: boolean
  title: string
  description?: string
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
  loading?: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  confirm: []
  cancel: []
}>()

function onCancel() {
  emit('update:open', false)
  emit('cancel')
}

function onConfirm() {
  emit('confirm')
}
</script>

<template>
  <AppDialog
    :open="open"
    :title="title"
    :description="description"
    @update:open="emit('update:open', $event)"
  >
    <template #title>
      <span class="inline-flex items-center gap-2">
        <AlertTriangle
          v-if="destructive !== false"
          class="size-4 text-iron"
        />
        {{ title }}
      </span>
    </template>
    <slot />
    <template #footer>
      <button
        type="button"
        class="kb-btn-secondary"
        :disabled="loading"
        @click="onCancel"
      >
        {{ cancelLabel || '取消' }}
      </button>
      <button
        type="button"
        class="kb-btn-primary"
        :disabled="loading"
        @click="onConfirm"
      >
        {{ loading ? '处理中…' : confirmLabel || '确认' }}
      </button>
    </template>
  </AppDialog>
</template>

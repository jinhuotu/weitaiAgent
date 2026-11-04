<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'

defineProps<{
  label?: string
  data?: Record<string, unknown>
}>()
</script>

<template>
  <div class="wf-cond">
    <Handle type="target" :position="Position.Top" />
    <div class="wf-cond__title">{{ label || '条件' }}</div>
    <div class="wf-cond__hint">
      <span>是</span>
      <span>否</span>
    </div>
    <Handle
      id="yes"
      type="source"
      :position="Position.Bottom"
      :style="{ left: '28%' }"
    />
    <Handle
      id="no"
      type="source"
      :position="Position.Bottom"
      :style="{ left: '72%' }"
    />
  </div>
</template>

<style scoped>
.wf-cond {
  min-width: 108px;
  padding: 6px 10px 10px;
  text-align: center;
}
.wf-cond__title {
  font-size: 12px;
  font-weight: 600;
}
.wf-cond__hint {
  display: flex;
  justify-content: space-between;
  margin-top: 4px;
  padding: 0 6px;
  font-size: 9px;
  color: var(--muted-foreground, #888);
}
</style>

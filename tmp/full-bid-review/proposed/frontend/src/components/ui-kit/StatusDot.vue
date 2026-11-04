<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'

const props = defineProps<{
  status: 'running' | 'warning' | 'alarm' | 'idle' | 'offline' | string
}>()

const map = {
  running: { color: 'bg-patina', label: '运行中', pulse: false },
  warning: { color: 'bg-sulfur', label: '预警', pulse: true },
  alarm: { color: 'bg-iron', label: '告警', pulse: true },
  idle: { color: 'bg-coolant', label: '空闲', pulse: false },
  offline: { color: 'bg-muted-foreground', label: '离线', pulse: false },
} as const

const s = computed(() => {
  const key = props.status as keyof typeof map
  return map[key] ?? map.offline
})
</script>

<template>
  <span class="inline-flex items-center gap-1.5 text-[11px]">
    <span :class="cn('size-1.5 rounded-full', s.color, s.pulse && 'pulse-alert')" />
    <span class="text-foreground/80">{{ s.label }}</span>
  </span>
</template>

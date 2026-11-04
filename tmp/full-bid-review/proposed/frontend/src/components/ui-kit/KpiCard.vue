<script setup lang="ts">
import { cn } from '@/lib/utils'

withDefaults(
  defineProps<{
    label: string
    value: string | number
    unit?: string
    trend?: { value: string; up?: boolean }
    hint?: string
    tone?: 'iron' | 'molybdenum' | 'patina' | 'sulfur' | 'coolant'
  }>(),
  {
    tone: 'molybdenum',
  },
)

const toneClass: Record<string, string> = {
  iron: 'text-iron',
  molybdenum: 'text-molybdenum',
  patina: 'text-patina',
  sulfur: 'text-sulfur',
  coolant: 'text-coolant',
}

const toneBg: Record<string, string> = {
  iron: 'bg-iron',
  molybdenum: 'bg-molybdenum',
  patina: 'bg-patina',
  sulfur: 'bg-sulfur',
  coolant: 'bg-coolant',
}
</script>

<template>
  <div class="panel-surface rounded-lg p-4 lg:p-5 relative overflow-hidden">
    <div class="flex items-start justify-between">
      <div class="text-xs text-muted-foreground">{{ label }}</div>
      <div v-if="$slots.icon" :class="cn('opacity-80', toneClass[tone])">
        <slot name="icon" />
      </div>
    </div>
    <div class="mt-3 flex items-baseline gap-1.5">
      <span :class="cn('data-num text-3xl font-semibold', toneClass[tone])">
        {{ value }}
      </span>
      <span v-if="unit" class="text-xs text-muted-foreground data-num">{{ unit }}</span>
    </div>
    <div class="mt-2 flex items-center gap-2 text-[11px]">
      <span
        v-if="trend"
        :class="
          cn(
            'data-num inline-flex items-center gap-1',
            trend.up ? 'text-iron' : 'text-patina',
          )
        "
      >
        {{ trend.up ? '▲' : '▼' }} {{ trend.value }}
      </span>
      <span v-if="hint" class="text-muted-foreground">{{ hint }}</span>
    </div>
    <div
      :class="
        cn(
          'absolute -right-6 -bottom-6 size-24 rounded-full opacity-[0.06]',
          toneBg[tone],
        )
      "
    />
  </div>
</template>

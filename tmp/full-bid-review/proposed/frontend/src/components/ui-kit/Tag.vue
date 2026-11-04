<script setup lang="ts">
import { cn } from '@/lib/utils'

withDefaults(
  defineProps<{
    tone?: 'default' | 'iron' | 'molybdenum' | 'patina' | 'sulfur' | 'coolant'
    className?: string
  }>(),
  {
    tone: 'default',
  },
)

const tones: Record<string, string> = {
  default: 'border-border text-muted-foreground bg-background/40',
  iron: 'border-iron/40 text-iron bg-iron/5',
  molybdenum: 'border-molybdenum/40 text-molybdenum bg-molybdenum/5',
  patina: 'border-patina/40 text-patina bg-patina/5',
  sulfur: 'border-sulfur/40 text-sulfur bg-sulfur/5',
  coolant: 'border-coolant/40 text-coolant bg-coolant/5',
}
</script>

<template>
  <span
    :class="
      cn(
        'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border tracking-wide',
        tones[tone],
        className,
      )
    "
  >
    <slot />
  </span>
</template>

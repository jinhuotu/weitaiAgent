<script setup lang="ts">
import { ref } from 'vue'
import { ChevronDown, ChevronUp } from 'lucide-vue-next'
import { cn } from '@/lib/utils'

const props = withDefaults(
  defineProps<{
    title?: string
    subtitle?: string
    /** Extra classes on the root section (also accept native `class` via fallthrough if undeclared) */
    className?: string
    flush?: boolean
    /** 可折叠；默认展开，设 defaultOpen=false 则默认收起 */
    collapsible?: boolean
    defaultOpen?: boolean
  }>(),
  {
    flush: false,
    collapsible: false,
    defaultOpen: true,
  },
)

const open = ref(props.defaultOpen)

function toggle() {
  if (!props.collapsible) return
  open.value = !open.value
}
</script>

<template>
  <section :class="cn('rounded-lg panel-surface overflow-hidden flex flex-col', className)">
    <header
      v-if="title || $slots.action || $slots.title"
      class="flex items-center justify-between gap-2 px-4 lg:px-5 py-3 border-b border-border"
      :class="collapsible ? 'cursor-pointer select-none hover:bg-accent/20 transition-colors' : ''"
      :role="collapsible ? 'button' : undefined"
      :tabindex="collapsible ? 0 : undefined"
      :aria-expanded="collapsible ? open : undefined"
      @click="toggle"
      @keydown.enter.prevent="toggle"
      @keydown.space.prevent="toggle"
    >
      <div class="min-w-0 flex-1">
        <h3
          v-if="title || $slots.title"
          class="text-sm font-medium tracking-wide truncate flex items-center gap-2"
        >
          <span class="inline-block w-1 h-3 bg-iron rounded-sm shrink-0" />
          <slot name="title">{{ title }}</slot>
        </h3>
        <div
          v-if="(subtitle || $slots.subtitle) && (!collapsible || open)"
          class="text-[11px] text-muted-foreground mt-0.5 pl-3"
        >
          <slot name="subtitle">{{ subtitle }}</slot>
        </div>
        <div
          v-if="collapsible && !open && $slots.summary"
          class="text-[11px] text-muted-foreground mt-0.5 pl-3 truncate"
        >
          <slot name="summary" />
        </div>
      </div>
      <div class="ml-2 shrink-0 flex items-center gap-2">
        <div v-if="$slots.action" @click.stop>
          <slot name="action" />
        </div>
        <span
          v-if="collapsible"
          class="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground"
        >
          {{ open ? '收起' : '展开修改' }}
          <ChevronUp v-if="open" class="size-3.5" />
          <ChevronDown v-else class="size-3.5" />
        </span>
      </div>
    </header>
    <div v-show="!collapsible || open" :class="cn('min-w-0', flush ? '' : 'p-4 lg:p-5')">
      <slot />
    </div>
  </section>
</template>

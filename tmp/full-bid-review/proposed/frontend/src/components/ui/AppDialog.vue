<script setup lang="ts">
import { computed, watch, onMounted, onUnmounted } from 'vue'
import { X } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    open: boolean
    title?: string
    description?: string
    wide?: boolean
    size?: 'default' | 'wide' | 'xl'
  }>(),
  { wide: false, size: undefined },
)

const emit = defineEmits<{
  'update:open': [value: boolean]
  close: []
}>()

const resolvedSize = computed(() => {
  if (props.size) return props.size
  return props.wide ? 'wide' : 'default'
})

function close() {
  emit('update:open', false)
  emit('close')
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.open) close()
}

watch(
  () => props.open,
  (v) => {
    document.body.style.overflow = v ? 'hidden' : ''
  },
)

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => {
  window.removeEventListener('keydown', onKey)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div class="absolute inset-0 bg-black/60 backdrop-blur-sm" @click="close" />
      <div
        :class="[
          'relative z-10 w-full rounded-lg border border-border bg-card shadow-2xl',
          resolvedSize === 'xl'
            ? 'flex h-[90vh] max-w-[min(96vw,80rem)] flex-col'
            : resolvedSize === 'wide'
              ? 'max-w-2xl'
              : 'max-w-lg',
        ]"
        role="dialog"
        aria-modal="true"
      >
        <header
          v-if="title || $slots.title"
          class="flex shrink-0 items-start justify-between gap-3 border-b border-border px-5 py-4"
        >
          <div class="min-w-0">
            <h2 class="text-sm font-semibold tracking-wide">
              <slot name="title">{{ title }}</slot>
            </h2>
            <p
              v-if="description || $slots.description"
              class="mt-1 text-xs text-muted-foreground"
            >
              <slot name="description">{{ description }}</slot>
            </p>
          </div>
          <button
            type="button"
            class="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
            aria-label="关闭"
            @click="close"
          >
            <X class="size-4" />
          </button>
        </header>
        <div
          :class="
            resolvedSize === 'xl'
              ? 'flex min-h-0 flex-1 flex-col overflow-hidden px-5 py-4'
              : 'px-5 py-4'
          "
        >
          <slot />
        </div>
        <footer
          v-if="$slots.footer"
          class="flex shrink-0 items-center justify-end gap-2 border-t border-border px-5 py-3"
        >
          <slot name="footer" />
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { X } from 'lucide-vue-next'
import { isQuotePath } from '@/config/nav'
import { cn } from '@/lib/utils'
import { useTabsStore } from '@/stores/tabs'

const tabsStore = useTabsStore()
const route = useRoute()
const router = useRouter()

const tabWidthClass = computed(() => {
  const n = tabsStore.tabs.length
  if (n > 14) return 'min-w-[88px] max-w-[120px]'
  if (n > 10) return 'min-w-[100px] max-w-[148px]'
  if (n > 7) return 'min-w-[116px] max-w-[168px]'
  return 'min-w-[120px] max-w-[200px]'
})

function isTabActive(href: string) {
  if (isQuotePath(href) && isQuotePath(route.path)) return true
  return href === route.path
}

function onClose(href: string) {
  const next = tabsStore.closeTab(href, route.path)
  if (next) void router.push(next)
}
</script>

<template>
  <div class="border-b border-border bg-card/60 backdrop-blur-sm">
    <div class="tab-bar flex h-9 items-center gap-1 overflow-x-auto px-2 scrollbar-thin">
      <div
        v-for="tab in tabsStore.tabs"
        :key="tab.href"
        :class="
          cn(
            'group relative flex h-7 shrink-0 items-center gap-1.5 rounded-t-md px-2.5 text-[12px] transition-colors',
            tabWidthClass,
            isTabActive(tab.href)
              ? 'border-t border-x border-iron/50 bg-background text-foreground'
              : 'text-muted-foreground hover:bg-background/40 hover:text-foreground',
          )
        "
      >
        <span
          v-if="isTabActive(tab.href)"
          class="absolute top-0 right-0 left-0 h-[2px] rounded-b bg-iron"
        />
        <RouterLink
          :to="tab.href"
          class="flex min-w-0 flex-1 items-center gap-1.5 truncate"
        >
          <span class="truncate">{{ tab.label }}</span>
        </RouterLink>
        <button
          v-if="tabsStore.tabs.length > 1"
          type="button"
          :class="
            cn(
              'inline-flex size-4 items-center justify-center rounded-sm transition-colors',
              'text-muted-foreground/70 hover:bg-foreground/10 hover:text-foreground',
              'opacity-0 group-hover:opacity-100',
              isTabActive(tab.href) && 'opacity-60 hover:opacity-100',
            )
          "
          :aria-label="`关闭 ${tab.label}`"
          @click.prevent.stop="onClose(tab.href)"
        >
          <X class="size-3" />
        </button>
      </div>
    </div>
  </div>
</template>

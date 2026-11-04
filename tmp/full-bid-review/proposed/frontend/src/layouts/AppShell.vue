<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Sidebar from '@/components/layout/Sidebar.vue'
import Topbar from '@/components/layout/Topbar.vue'
import { useAuthStore } from '@/stores/auth'
import { cn } from '@/lib/utils'

const IMMERSIVE_PATHS = ['/ai-chat']

const route = useRoute()
const auth = useAuthStore()
const mainRef = ref<HTMLElement | null>(null)

const immersive = computed(() =>
  IMMERSIVE_PATHS.some((p) => route.path === p || route.path.startsWith(`${p}/`)),
)

watch(
  () => route.fullPath,
  async () => {
    await nextTick()
    mainRef.value?.scrollTo({ top: 0, left: 0 })
  },
)
</script>

<template>
  <div
    v-if="auth.loading || !auth.user"
    class="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground"
  >
    {{ auth.loading ? '正在校验登录状态…' : '正在跳转登录…' }}
  </div>

  <div v-else class="shell-root">
    <Sidebar />

    <div class="shell-stage">
      <div class="shell-window">
        <div class="shell-window__chrome" aria-hidden="true">
          <span class="shell-dot shell-dot--red" />
          <span class="shell-dot shell-dot--amber" />
          <span class="shell-dot shell-dot--green" />
        </div>
        <Topbar />
        <main
          ref="mainRef"
          :class="
            cn(
              'shell-main min-h-0 flex-1',
              immersive
                ? 'flex flex-col overflow-hidden'
                : 'flex flex-col overflow-y-auto overscroll-contain',
            )
          "
        >
          <div
            class="mx-auto flex h-full min-h-0 w-full max-w-[1600px] flex-1 flex-col p-3 sm:p-4 lg:p-5"
          >
            <RouterView />
          </div>
        </main>
      </div>
    </div>
  </div>
</template>

<style scoped>
.shell-root {
  display: flex;
  height: 100dvh;
  overflow: hidden;
  color: var(--foreground);
  background:
    radial-gradient(ellipse 80% 60% at 10% 20%, var(--shell-root-a), transparent 55%),
    radial-gradient(ellipse 70% 50% at 90% 80%, var(--shell-root-b), transparent 50%),
    var(--shell-root-base);
}

.shell-stage {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  padding: 0.85rem 0.85rem 0.85rem 0;
}

@media (max-width: 1023px) {
  .shell-stage {
    padding: 0.65rem;
  }
}

.shell-window {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-radius: 1.15rem;
  border: 1px solid var(--shell-window-border);
  background: color-mix(in srgb, var(--card) 92%, transparent);
  backdrop-filter: blur(10px);
  box-shadow: var(--shell-window-shadow);
  overflow: hidden;
}

.shell-window__chrome {
  display: none;
  align-items: center;
  gap: 0.4rem;
  padding: 0.65rem 1rem 0;
}

@media (min-width: 1024px) {
  .shell-window__chrome {
    display: flex;
  }
}

.shell-dot {
  width: 0.65rem;
  height: 0.65rem;
  border-radius: 9999px;
}

.shell-dot--red {
  background: #ff5f57;
}
.shell-dot--amber {
  background: #febc2e;
}
.shell-dot--green {
  background: #28c840;
}
</style>

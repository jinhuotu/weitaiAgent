<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Sun, Moon, CircleDot, LogOut, Menu, Palette } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useMobileMenuStore } from '@/stores/mobile-menu'
import AccentPalettePicker from '@/components/theme/AccentPalettePicker.vue'
import weitaiLogo from '@/assets/weitai-logo.jpg'

const auth = useAuthStore()
const theme = useThemeStore()
const mobileMenu = useMobileMenuStore()
const router = useRouter()

const now = ref('--:--:--')
const themeOpen = ref(false)
let timer: number | undefined

function updateClock() {
  const d = new Date()
  const pad = (n: number) => n.toString().padStart(2, '0')
  now.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function onDocClick(e: MouseEvent) {
  const el = e.target as HTMLElement | null
  if (!el?.closest?.('[data-theme-menu]')) themeOpen.value = false
}

onMounted(() => {
  updateClock()
  timer = window.setInterval(updateClock, 1000)
  document.addEventListener('click', onDocClick)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
  document.removeEventListener('click', onDocClick)
})

const displayName = computed(
  () => auth.user?.display_name || auth.user?.username || '未登录',
)

const roleLabel = computed(() => {
  if (auth.user?.is_superuser) return '系统管理员'
  if (auth.user?.roles?.[0]) return `角色 · ${auth.user.roles[0]}`
  return '已登录'
})

async function onLogout() {
  auth.logout()
  await router.replace('/login')
}
</script>

<template>
  <header class="flex h-12 shrink-0 items-center gap-2 px-3 sm:px-5">
    <button
      type="button"
      class="inline-flex h-9 w-9 items-center justify-center rounded-lg text-foreground/80 transition hover:bg-accent lg:hidden"
      aria-label="打开菜单"
      @click="mobileMenu.toggle()"
    >
      <Menu class="size-5" />
    </button>

    <div class="hidden items-center gap-2 text-xs text-muted-foreground sm:flex">
      <CircleDot class="size-3 text-patina pulse-alert" />
      <span>系统在线</span>
      <span class="mx-1 text-border">|</span>
      <span class="data-num text-foreground/80">{{ now }}</span>
    </div>

    <div class="ml-auto flex items-center gap-1">
      <div class="relative hidden md:block" data-theme-menu>
        <button
          type="button"
          class="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs text-muted-foreground transition hover:bg-accent hover:text-foreground"
          :title="`主题 · ${theme.accentLabel}`"
          @click.stop="themeOpen = !themeOpen"
        >
          <Palette class="size-3.5" />
          <span>主题</span>
          <span
            class="size-2.5 rounded-full border border-black/10"
            :style="{ background: 'var(--accent-iron)' }"
          />
        </button>

        <div
          v-if="themeOpen"
          class="absolute right-0 top-full z-50 mt-1.5 w-[260px] rounded-xl border border-border bg-popover p-3 text-popover-foreground shadow-lg"
          @click.stop
        >
          <div class="mb-2 flex items-center justify-between gap-2">
            <span class="text-[11px] font-medium text-foreground">明暗</span>
            <button
              type="button"
              class="inline-flex h-7 items-center gap-1.5 rounded-md border border-border px-2 text-[11px] hover:bg-accent"
              @click="theme.toggle()"
            >
              <Sun v-if="theme.isDark" class="size-3" />
              <Moon v-else class="size-3" />
              {{ theme.label }}
            </button>
          </div>
          <div class="mb-1.5 text-[11px] font-medium text-foreground">配色渐变</div>
          <AccentPalettePicker variant="compact" />
        </div>
      </div>

      <div class="ml-1 flex items-center gap-2 border-l border-border/70 pl-2">
        <img
          :src="weitaiLogo"
          alt="伟泰光电"
          class="size-7 rounded-full object-cover shadow-sm ring-1 ring-black/5"
        />
        <div class="hidden leading-tight md:block">
          <div class="text-xs font-medium">{{ displayName }}</div>
          <div class="text-[10px] text-muted-foreground">伟泰光电 · {{ roleLabel }}</div>
        </div>
        <button
          type="button"
          class="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-accent hover:text-foreground"
          title="退出登录"
          @click="onLogout"
        >
          <LogOut class="size-3.5" />
        </button>
      </div>
    </div>
  </header>
</template>

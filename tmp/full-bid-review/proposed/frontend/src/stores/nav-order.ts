import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { PRIMARY_NAV_HREFS, applyNavOrder } from '@/config/nav'
import { useAuthStore } from '@/stores/auth'

const STORAGE_PREFIX = 'youqi-primary-nav-order-v1'

function storageKey(userId: number) {
  return `${STORAGE_PREFIX}:${userId}`
}

function readStoredOrder(userId: number | undefined): string[] | null {
  if (userId == null) return null
  try {
    const raw = localStorage.getItem(storageKey(userId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed) || parsed.some((item) => typeof item !== 'string')) {
      return null
    }
    return parsed
  } catch {
    return null
  }
}

export const useNavOrderStore = defineStore('nav-order', () => {
  const auth = useAuthStore()
  const order = ref<string[]>([...PRIMARY_NAV_HREFS])

  watch(
    () => auth.user?.id,
    (userId) => {
      order.value = readStoredOrder(userId) ?? [...PRIMARY_NAV_HREFS]
    },
    { immediate: true },
  )

  function persist() {
    const userId = auth.user?.id
    if (userId == null) return
    try {
      localStorage.setItem(storageKey(userId), JSON.stringify(order.value))
    } catch {
      /* ignore quota / private mode */
    }
  }

  /** 只重排当前可见入口，权限外的 href 仍保留在后段 */
  function setVisibleOrder(visibleHrefs: string[]) {
    const visible = new Set(visibleHrefs)
    const rest = order.value.filter((href) => !visible.has(href))
    order.value = [...visibleHrefs, ...rest]
    persist()
  }

  function sortItems<T extends { href: string }>(items: T[]): T[] {
    return applyNavOrder(items, order.value)
  }

  return { order, setVisibleOrder, sortItems }
})

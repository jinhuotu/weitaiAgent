import { defineStore } from 'pinia'
import { ref } from 'vue'
import { isQuotePath, QUOTE_NAV_LABEL } from '@/config/nav'

export type TabItem = {
  href: string
  label: string
}

export const useTabsStore = defineStore('tabs', () => {
  const tabs = ref<TabItem[]>([{ href: '/', label: '总览' }])

  function ensureTab(href: string, label?: string) {
    if (isQuotePath(href)) {
      const existing = tabs.value.find((t) => isQuotePath(t.href))
      if (existing) {
        existing.href = href
        existing.label = QUOTE_NAV_LABEL
        return
      }
      tabs.value.push({ href, label: QUOTE_NAV_LABEL })
      return
    }
    const existing = tabs.value.find((t) => t.href === href)
    if (existing) {
      if (label) existing.label = label
      return
    }
    const fallback =
      label ||
      (href === '/' ? '总览' : decodeURIComponent(href.replace(/^\//, '')))
    tabs.value.push({ href, label: fallback })
  }

  function openTab(href: string, label: string) {
    ensureTab(href, label)
  }

  /** 关闭标签；若需跳转则返回目标 path，否则 null */
  function closeTab(href: string, activeHref: string): string | null {
    const idx = tabs.value.findIndex((t) =>
      isQuotePath(href) ? isQuotePath(t.href) : t.href === href,
    )
    if (idx < 0) return null
    const next = tabs.value.filter((_, i) => i !== idx)
    if (next.length === 0) {
      tabs.value = [{ href: '/', label: '总览' }]
      return '/'
    }
    tabs.value = next
    const closingActive = isQuotePath(href)
      ? isQuotePath(activeHref)
      : href === activeHref
    if (closingActive) {
      return next[Math.max(0, idx - 1)].href
    }
    return null
  }

  return { tabs, ensureTab, openTab, closeTab }
})

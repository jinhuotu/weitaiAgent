import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

export type ThemeMode = 'dark' | 'light'
export type AccentPalette = 'blue' | 'red' | 'custom'

const MODE_KEY = 'youqi-theme-v1'
const ACCENT_KEY = 'youqi-accent-v1'
const CUSTOM_KEY = 'youqi-accent-custom-v1'
const CUSTOM_FONT_KEY = 'youqi-accent-font-v1'
const DEFAULT_CUSTOM = '#c41e2a'
const DEFAULT_CUSTOM_FONT = '#111827'

const CUSTOM_VARS = [
  '--primary',
  '--ring',
  '--sidebar-primary',
  '--sidebar-ring',
  '--chart-1',
  '--secondary',
  '--muted',
  '--accent',
  '--border',
  '--input',
  '--sidebar-accent',
  '--sidebar-border',
  '--bg-base',
  '--bg-elevated',
  '--hairline',
  '--background',
  '--panel-surface-to',
  '--accent-iron',
  '--accent-molybdenum',
  '--accent-coolant',
  '--accent-glow',
  '--body-glow-a',
  '--body-glow-b',
  '--scrollbar-thumb',
  '--scrollbar-thumb-hover',
  '--shell-root-a',
  '--shell-root-b',
  '--shell-root-base',
  '--shell-nav-title',
  '--shell-nav-icon-bg',
  '--shell-nav-icon-fg',
  '--foreground',
  '--card-foreground',
  '--popover-foreground',
  '--secondary-foreground',
  '--accent-foreground',
  '--muted-foreground',
  '--sidebar-foreground',
  '--sidebar-accent-foreground',
  '--text-primary',
  '--text-secondary',
  '--text-muted',
] as const

function readStoredTheme(): ThemeMode {
  try {
    const v = localStorage.getItem(MODE_KEY)
    if (v === 'light' || v === 'dark') return v
  } catch {
    /* ignore */
  }
  return 'light'
}

function readStoredAccent(): AccentPalette {
  try {
    const v = localStorage.getItem(ACCENT_KEY)
    if (v === 'blue' || v === 'red' || v === 'custom') return v
  } catch {
    /* ignore */
  }
  return 'blue'
}

function readStoredHex(key: string, fallback: string): string {
  try {
    const v = localStorage.getItem(key)
    if (v && /^#[0-9a-fA-F]{6}$/.test(v)) return v.toLowerCase()
  } catch {
    /* ignore */
  }
  return fallback
}

function normalizeHex(hex: string): string | null {
  const normalized = hex.trim().toLowerCase()
  return /^#[0-9a-f]{6}$/.test(normalized) ? normalized : null
}

function clamp(n: number, min = 0, max = 255) {
  return Math.min(max, Math.max(min, Math.round(n)))
}

function hexToRgb(hex: string): { r: number; g: number; b: number } | null {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return null
  const n = Number.parseInt(m[1], 16)
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 }
}

function rgbToHex(r: number, g: number, b: number) {
  return `#${[r, g, b].map((x) => clamp(x).toString(16).padStart(2, '0')).join('')}`
}

function mix(hex: string, target: string, amount: number) {
  const a = hexToRgb(hex)
  const b = hexToRgb(target)
  if (!a || !b) return hex
  return rgbToHex(
    a.r + (b.r - a.r) * amount,
    a.g + (b.g - a.g) * amount,
    a.b + (b.b - a.b) * amount,
  )
}

function rgba(hex: string, alpha: number) {
  const rgb = hexToRgb(hex)
  if (!rgb) return `rgba(0,0,0,${alpha})`
  return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${alpha})`
}

function applyThemeClass(mode: ThemeMode) {
  const root = document.documentElement
  root.classList.toggle('dark', mode === 'dark')
  root.classList.toggle('light', mode === 'light')
  root.dataset.theme = mode
}

function clearCustomVars() {
  const root = document.documentElement
  for (const key of CUSTOM_VARS) root.style.removeProperty(key)
}

function applyCustomFont(fontHex: string, dark: boolean) {
  const root = document.documentElement
  // Soften primary for secondary/muted so hierarchy stays readable
  const secondary = mix(fontHex, dark ? '#ffffff' : '#6b7280', dark ? 0.28 : 0.35)
  const muted = mix(fontHex, dark ? '#ffffff' : '#9ca3af', dark ? 0.42 : 0.55)

  root.style.setProperty('--foreground', fontHex)
  root.style.setProperty('--card-foreground', fontHex)
  root.style.setProperty('--popover-foreground', fontHex)
  root.style.setProperty('--secondary-foreground', fontHex)
  root.style.setProperty('--accent-foreground', fontHex)
  root.style.setProperty('--sidebar-foreground', fontHex)
  root.style.setProperty('--sidebar-accent-foreground', fontHex)
  root.style.setProperty('--text-primary', fontHex)
  root.style.setProperty('--text-secondary', secondary)
  root.style.setProperty('--text-muted', muted)
  root.style.setProperty('--muted-foreground', muted)
}

function applyCustomPalette(hex: string, fontHex: string, dark: boolean) {
  const root = document.documentElement
  const soft = mix(hex, '#ffffff', dark ? 0.25 : 0.55)
  const lighter = mix(hex, '#ffffff', dark ? 0.45 : 0.72)
  const pale = mix(hex, '#ffffff', 0.88)
  const deep = mix(hex, '#000000', dark ? 0.45 : 0.12)
  const wash = mix(hex, '#ffffff', 0.94)
  const borderSoft = mix(hex, '#ffffff', 0.78)

  root.style.setProperty('--primary', hex)
  root.style.setProperty('--ring', hex)
  root.style.setProperty('--sidebar-primary', hex)
  root.style.setProperty('--sidebar-ring', hex)
  root.style.setProperty('--chart-1', hex)
  root.style.setProperty('--accent-iron', hex)
  root.style.setProperty('--accent-molybdenum', soft)
  root.style.setProperty('--accent-coolant', lighter)
  root.style.setProperty('--accent-glow', rgba(hex, dark ? 0.35 : 0.28))
  root.style.setProperty('--body-glow-a', rgba(hex, dark ? 0.14 : 0.12))
  root.style.setProperty('--body-glow-b', rgba(soft, dark ? 0.1 : 0.08))
  root.style.setProperty('--scrollbar-thumb', rgba(hex, dark ? 0.35 : 0.22))
  root.style.setProperty('--scrollbar-thumb-hover', rgba(hex, dark ? 0.5 : 0.4))
  root.style.setProperty('--shell-root-a', rgba(soft, dark ? 0.28 : 0.55))
  root.style.setProperty('--shell-root-b', rgba(hex, dark ? 0.16 : 0.28))
  root.style.setProperty(
    '--shell-root-base',
    dark
      ? `linear-gradient(160deg, ${deep} 0%, ${mix(hex, '#000000', 0.55)} 48%, ${mix(hex, '#000000', 0.62)} 100%)`
      : `linear-gradient(160deg, ${soft} 0%, ${lighter} 42%, ${pale} 100%)`,
  )
  root.style.setProperty('--shell-nav-title', rgba(hex, dark ? 0.72 : 0.62))
  root.style.setProperty(
    '--shell-nav-icon-bg',
    `linear-gradient(145deg, ${rgba(hex, 0.18)}, ${rgba(soft, 0.14)})`,
  )
  root.style.setProperty('--shell-nav-icon-fg', hex)

  if (!dark) {
    root.style.setProperty('--background', wash)
    root.style.setProperty('--bg-base', wash)
    root.style.setProperty('--bg-elevated', pale)
    root.style.setProperty('--secondary', pale)
    root.style.setProperty('--muted', pale)
    root.style.setProperty('--accent', pale)
    root.style.setProperty('--border', borderSoft)
    root.style.setProperty('--input', borderSoft)
    root.style.setProperty('--hairline', borderSoft)
    root.style.setProperty('--sidebar-accent', pale)
    root.style.setProperty('--sidebar-border', borderSoft)
    root.style.setProperty('--panel-surface-to', pale)
  }

  applyCustomFont(fontHex, dark)
}

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>(readStoredTheme())
  const accent = ref<AccentPalette>(readStoredAccent())
  const customColor = ref(readStoredHex(CUSTOM_KEY, DEFAULT_CUSTOM))
  const customFontColor = ref(readStoredHex(CUSTOM_FONT_KEY, DEFAULT_CUSTOM_FONT))

  const isDark = computed(() => mode.value === 'dark')
  const isLight = computed(() => mode.value === 'light')
  const label = computed(() => (mode.value === 'dark' ? '浅色' : '深色'))
  const accentLabel = computed(() => {
    if (accent.value === 'red') return '红白'
    if (accent.value === 'custom') return '自定义'
    return '蓝白'
  })

  function setTheme(next: ThemeMode) {
    mode.value = next
  }

  function toggle() {
    mode.value = mode.value === 'dark' ? 'light' : 'dark'
  }

  function setAccent(next: AccentPalette) {
    accent.value = next
  }

  function setCustomColor(hex: string) {
    const normalized = normalizeHex(hex)
    if (!normalized) return
    customColor.value = normalized
    accent.value = 'custom'
  }

  function setCustomFontColor(hex: string) {
    const normalized = normalizeHex(hex)
    if (!normalized) return
    customFontColor.value = normalized
    accent.value = 'custom'
  }

  function applyAll() {
    applyThemeClass(mode.value)
    const root = document.documentElement
    root.dataset.accent = accent.value
    if (accent.value === 'custom') {
      applyCustomPalette(customColor.value, customFontColor.value, mode.value === 'dark')
    } else {
      clearCustomVars()
    }
  }

  watch(
    [mode, accent, customColor, customFontColor],
    () => {
      applyAll()
      try {
        localStorage.setItem(MODE_KEY, mode.value)
        localStorage.setItem(ACCENT_KEY, accent.value)
        localStorage.setItem(CUSTOM_KEY, customColor.value)
        localStorage.setItem(CUSTOM_FONT_KEY, customFontColor.value)
      } catch {
        /* ignore */
      }
    },
    { immediate: true },
  )

  return {
    mode,
    accent,
    customColor,
    customFontColor,
    isDark,
    isLight,
    label,
    accentLabel,
    setTheme,
    toggle,
    setAccent,
    setCustomColor,
    setCustomFontColor,
  }
})

<script setup lang="ts">
import { computed } from 'vue'
import type { AccentPalette } from '@/stores/theme'
import { useThemeStore } from '@/stores/theme'

withDefaults(
  defineProps<{
    /** compact: topbar; full: settings panel */
    variant?: 'compact' | 'full'
  }>(),
  { variant: 'full' },
)

const theme = useThemeStore()

const options: { id: AccentPalette; label: string; swatch: string }[] = [
  { id: 'blue', label: '蓝白', swatch: 'linear-gradient(160deg, #bfdbfe, #eff6ff)' },
  { id: 'red', label: '红白', swatch: 'linear-gradient(160deg, #fecaca, #fff5f5)' },
  { id: 'custom', label: '自定义', swatch: 'var(--custom-swatch)' },
]

const customSwatch = computed(
  () =>
    `linear-gradient(160deg, ${theme.customColor}, ${mixSoft(theme.customColor)})`,
)

function mixSoft(hex: string) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex)
  if (!m) return '#ffffff'
  const n = Number.parseInt(m[1], 16)
  const r = (n >> 16) & 255
  const g = (n >> 8) & 255
  const b = n & 255
  const mix = (c: number) => Math.round(c + (255 - c) * 0.72)
  return `#${[mix(r), mix(g), mix(b)].map((x) => x.toString(16).padStart(2, '0')).join('')}`
}

function onPick(id: AccentPalette) {
  theme.setAccent(id)
}

function normalizeFromInput(raw: string) {
  let value = raw.trim()
  if (!value.startsWith('#')) value = `#${value}`
  return value
}

function onAccentColorInput(e: Event) {
  theme.setCustomColor((e.target as HTMLInputElement).value)
}

function onAccentHexChange(e: Event) {
  theme.setCustomColor(normalizeFromInput((e.target as HTMLInputElement).value))
}

function onFontColorInput(e: Event) {
  theme.setCustomFontColor((e.target as HTMLInputElement).value)
}

function onFontHexChange(e: Event) {
  theme.setCustomFontColor(normalizeFromInput((e.target as HTMLInputElement).value))
}
</script>

<template>
  <div :class="variant === 'compact' ? 'space-y-2' : 'space-y-3'">
    <div class="flex flex-wrap items-center gap-2">
      <button
        v-for="opt in options"
        :key="opt.id"
        type="button"
        class="group inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] transition"
        :class="
          theme.accent === opt.id
            ? 'border-primary bg-primary/10 text-foreground'
            : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground'
        "
        :title="opt.label"
        @click="onPick(opt.id)"
      >
        <span
          class="size-3.5 shrink-0 rounded-full border border-black/10 shadow-sm"
          :style="{
            background: opt.id === 'custom' ? customSwatch : opt.swatch,
          }"
        />
        <span>{{ opt.label }}</span>
      </button>
    </div>

    <div
      class="space-y-1.5"
      :class="theme.accent === 'custom' ? '' : 'opacity-70'"
    >
      <label class="flex items-center gap-2 text-[11px] text-muted-foreground">
        <span class="w-10 shrink-0">主色</span>
        <input
          type="color"
          class="h-7 w-10 cursor-pointer rounded border border-border bg-transparent p-0.5"
          :value="theme.customColor"
          @input="onAccentColorInput"
        />
        <input
          type="text"
          class="h-7 min-w-0 flex-1 rounded-md border border-border bg-background px-2 font-mono text-[11px] text-foreground"
          :value="theme.customColor"
          maxlength="7"
          spellcheck="false"
          @change="onAccentHexChange"
        />
      </label>

      <label class="flex items-center gap-2 text-[11px] text-muted-foreground">
        <span class="w-10 shrink-0">字体</span>
        <input
          type="color"
          class="h-7 w-10 cursor-pointer rounded border border-border bg-transparent p-0.5"
          :value="theme.customFontColor"
          @input="onFontColorInput"
        />
        <input
          type="text"
          class="h-7 min-w-0 flex-1 rounded-md border border-border bg-background px-2 font-mono text-[11px] text-foreground"
          :value="theme.customFontColor"
          maxlength="7"
          spellcheck="false"
          @change="onFontHexChange"
        />
      </label>
    </div>
  </div>
</template>

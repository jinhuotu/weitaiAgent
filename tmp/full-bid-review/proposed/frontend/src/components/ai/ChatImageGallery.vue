<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight, Download, Maximize2, X } from 'lucide-vue-next'

export type GalleryImage = {
  dataUrl: string
  mimeType?: string
  alt?: string
}

const props = withDefaults(
  defineProps<{
    images: GalleryImage[]
    filenamePrefix?: string
    compact?: boolean
  }>(),
  {
    filenamePrefix: '生成图',
    compact: false,
  },
)

const openIndex = ref<number | null>(null)

const current = computed(() => {
  const i = openIndex.value
  if (i == null) return null
  return props.images[i] ?? null
})

function extOf(img: GalleryImage | undefined): string {
  const mime = (img?.mimeType || '').toLowerCase()
  const url = img?.dataUrl || ''
  if (mime.includes('png') || url.startsWith('data:image/png')) return 'png'
  if (mime.includes('webp') || url.startsWith('data:image/webp')) return 'webp'
  if (mime.includes('gif') || url.startsWith('data:image/gif')) return 'gif'
  return 'jpg'
}

function filenameAt(i: number): string {
  const img = props.images[i]
  const n = props.images.length > 1 ? `-${i + 1}` : ''
  return `${props.filenamePrefix}${n}.${extOf(img)}`
}

function downloadAt(i: number) {
  const img = props.images[i]
  if (!img?.dataUrl) return
  const a = document.createElement('a')
  a.href = img.dataUrl
  a.download = filenameAt(i)
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

function openAt(i: number) {
  openIndex.value = i
}

function close() {
  openIndex.value = null
}

function prev() {
  if (openIndex.value == null || props.images.length < 2) return
  openIndex.value = (openIndex.value + props.images.length - 1) % props.images.length
}

function next() {
  if (openIndex.value == null || props.images.length < 2) return
  openIndex.value = (openIndex.value + 1) % props.images.length
}

function onKey(e: KeyboardEvent) {
  if (openIndex.value == null) return
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault()
    prev()
  } else if (e.key === 'ArrowRight') {
    e.preventDefault()
    next()
  }
}

watch(openIndex, (v) => {
  document.body.style.overflow = v == null ? '' : 'hidden'
})

watch(openIndex, (v, _, onCleanup) => {
  if (v == null) return
  window.addEventListener('keydown', onKey)
  onCleanup(() => window.removeEventListener('keydown', onKey))
})

onUnmounted(() => {
  document.body.style.overflow = ''
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div v-if="images.length" class="flex flex-wrap gap-1.5">
    <div
      v-for="(img, i) in images"
      :key="i"
      class="group relative rounded-md border border-hairline overflow-hidden bg-bg-surface"
    >
      <button
        type="button"
        class="block cursor-zoom-in"
        :title="'点击预览'"
        @click="openAt(i)"
      >
        <img
          :src="img.dataUrl"
          :alt="img.alt || '附图'"
          class="object-contain bg-bg-surface"
          :class="compact ? 'max-h-24 max-w-[140px]' : 'max-h-56 max-w-[min(100%,420px)]'"
        />
      </button>
      <div
        class="pointer-events-none absolute inset-0 bg-black/0 group-hover:bg-black/25 transition-colors"
      />
      <div
        class="absolute right-1 top-1 flex gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity"
      >
        <button
          type="button"
          class="size-7 rounded-md bg-black/60 text-white inline-flex items-center justify-center hover:bg-black/80"
          title="预览"
          @click="openAt(i)"
        >
          <Maximize2 class="size-3.5" />
        </button>
        <button
          type="button"
          class="size-7 rounded-md bg-black/60 text-white inline-flex items-center justify-center hover:bg-black/80"
          title="下载"
          @click.stop="downloadAt(i)"
        >
          <Download class="size-3.5" />
        </button>
      </div>
    </div>
  </div>

  <Teleport to="body">
    <div
      v-if="current && openIndex != null"
      class="fixed inset-0 z-[70] bg-bg-base/85 backdrop-blur-sm flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="图片预览"
      @click.self="close"
    >
      <div
        class="bg-bg-elevated border border-hairline rounded-lg shadow-2xl w-full max-w-6xl flex flex-col max-h-[92vh]"
      >
        <div class="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-hairline">
          <div class="min-w-0 text-[13px] text-text-primary font-medium truncate">
            {{ filenameAt(openIndex) }}
            <span
              v-if="images.length > 1"
              class="ml-2 text-[11px] text-text-muted font-normal"
            >
              {{ openIndex + 1 }} / {{ images.length }}
            </span>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            <button
              type="button"
              class="h-8 px-2.5 rounded-md border border-hairline text-[12px] text-text-secondary hover:text-text-primary hover:bg-hairline/40 inline-flex items-center gap-1.5"
              @click="downloadAt(openIndex)"
            >
              <Download class="size-3.5" />
              下载
            </button>
            <button
              type="button"
              class="size-8 rounded-md hover:bg-hairline/60 inline-flex items-center justify-center text-text-secondary hover:text-text-primary"
              aria-label="关闭"
              @click="close"
            >
              <X class="size-4" />
            </button>
          </div>
        </div>
        <div class="relative flex-1 min-h-0 flex items-center justify-center bg-bg-base/40 p-3 overflow-auto">
          <button
            v-if="images.length > 1"
            type="button"
            class="absolute left-3 size-9 rounded-full border border-hairline bg-bg-elevated/90 text-text-secondary hover:text-text-primary inline-flex items-center justify-center"
            aria-label="上一张"
            @click="prev"
          >
            <ChevronLeft class="size-5" />
          </button>
          <img
            :src="current.dataUrl"
            :alt="current.alt || '预览'"
            class="max-w-full max-h-[78vh] object-contain rounded-md border border-hairline bg-white"
          />
          <button
            v-if="images.length > 1"
            type="button"
            class="absolute right-3 size-9 rounded-full border border-hairline bg-bg-elevated/90 text-text-secondary hover:text-text-primary inline-flex items-center justify-center"
            aria-label="下一张"
            @click="next"
          >
            <ChevronRight class="size-5" />
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

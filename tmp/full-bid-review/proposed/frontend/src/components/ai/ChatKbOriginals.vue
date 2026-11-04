<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight, Download, FileText, Loader2, X } from 'lucide-vue-next'
import { fetchTenderLibraryFile, type SlotFileInfo } from '@/lib/tenders-api'

const props = defineProps<{
  files: SlotFileInfo[]
}>()

type Loaded = {
  id: string
  name: string
  kind: NonNullable<SlotFileInfo['kind']>
  url: string
}

const loaded = ref<Loaded[]>([])
const pending = ref(false)
const openIndex = ref<number | null>(null)
const urls: string[] = []
let loadSeq = 0

const current = computed(() => {
  const i = openIndex.value
  if (i == null) return null
  return loaded.value[i] ?? null
})

function revokeAll() {
  for (const u of urls) URL.revokeObjectURL(u)
  urls.length = 0
}

async function loadFiles(files: SlotFileInfo[]) {
  const seq = ++loadSeq
  pending.value = true
  openIndex.value = null
  revokeAll()
  const next: Loaded[] = []
  for (const f of files) {
    if (seq !== loadSeq) return
    const id = String(f.id || '').trim()
    if (!id) continue
    const kind = f.kind === 'pdf' || f.kind === 'file' ? f.kind : 'image'
    try {
      const blob = await fetchTenderLibraryFile(id)
      if (seq !== loadSeq) return
      const url = URL.createObjectURL(blob)
      urls.push(url)
      const mime = (blob.type || '').toLowerCase()
      const asPdf = kind === 'pdf' || mime.includes('pdf')
      next.push({
        id,
        name: f.name,
        kind: asPdf ? 'pdf' : kind === 'file' && !mime.startsWith('image/') ? 'file' : 'image',
        url,
      })
    } catch {
      if (seq !== loadSeq) return
      next.push({ id, name: f.name, kind, url: '' })
    }
  }
  if (seq !== loadSeq) return
  loaded.value = next
  pending.value = false
}

watch(
  () => props.files.map((f) => `${f.id}:${f.kind}:${f.name}`).join('|'),
  () => {
    void loadFiles(props.files)
  },
  { immediate: true },
)

function openAt(index: number) {
  if (!loaded.value[index]?.url) return
  openIndex.value = index
}

function close() {
  openIndex.value = null
}

function prev() {
  if (openIndex.value == null || loaded.value.length < 2) return
  openAt((openIndex.value + loaded.value.length - 1) % loaded.value.length)
}

function next() {
  if (openIndex.value == null || loaded.value.length < 2) return
  openAt((openIndex.value + 1) % loaded.value.length)
}

function downloadCurrent() {
  const row = current.value
  if (!row?.url) return
  const a = document.createElement('a')
  a.href = row.url
  a.download = row.name || '原件'
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
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
  revokeAll()
})
</script>

<template>
  <div v-if="files.length" class="space-y-1.5">
    <div class="text-[11px] text-text-muted">资料原件 · 点击可预览 / 下载</div>
    <div v-if="pending && !loaded.length" class="flex items-center gap-1.5 text-[11px] text-text-muted">
      <Loader2 class="size-3.5 animate-spin" />
      正在加载原件…
    </div>
    <div class="flex flex-wrap gap-2">
      <button
        v-for="(row, i) in loaded"
        :key="row.id"
        type="button"
        class="group relative overflow-hidden rounded-md border border-hairline bg-bg-surface text-left hover:border-iron/50 disabled:opacity-60"
        :title="row.url ? `查看原件 ${row.name}` : `${row.name} 加载失败`"
        :disabled="!row.url"
        @click="openAt(i)"
      >
        <img
          v-if="row.kind === 'image' && row.url"
          :src="row.url"
          :alt="row.name"
          class="h-24 w-[140px] bg-white object-contain"
        />
        <div
          v-else
          class="flex h-24 w-[140px] flex-col items-center justify-center gap-1 px-2 text-text-secondary"
        >
          <FileText class="size-6 opacity-70" />
          <span class="line-clamp-2 text-center text-[10px] leading-snug">{{ row.name }}</span>
        </div>
        <div
          class="absolute inset-x-0 bottom-0 truncate bg-black/55 px-1.5 py-0.5 text-[10px] text-white"
        >
          {{ row.name }}
        </div>
      </button>
    </div>
  </div>

  <Teleport to="body">
    <div
      v-if="current && openIndex != null && current.url"
      class="fixed inset-0 z-[70] flex items-center justify-center bg-bg-base/85 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="资料原件"
      @click.self="close"
    >
      <div
        class="flex max-h-[92vh] w-full max-w-5xl flex-col rounded-lg border border-hairline bg-bg-elevated shadow-2xl"
      >
        <div class="flex items-center justify-between gap-3 border-b border-hairline px-4 py-2.5">
          <div class="min-w-0 truncate text-[13px] text-text-primary">
            {{ current.name }}
            <span v-if="loaded.length > 1" class="ml-2 text-[11px] font-normal text-text-muted">
              {{ openIndex + 1 }} / {{ loaded.length }}
            </span>
          </div>
          <div class="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              class="inline-flex h-8 items-center gap-1.5 rounded-md border border-hairline px-2.5 text-[12px] text-text-secondary hover:bg-hairline/40 hover:text-text-primary"
              @click="downloadCurrent"
            >
              <Download class="size-3.5" />
              下载原件
            </button>
            <button
              type="button"
              class="inline-flex size-8 items-center justify-center rounded-md hover:bg-hairline/60"
              aria-label="关闭"
              @click="close"
            >
              <X class="size-4" />
            </button>
          </div>
        </div>
        <div
          class="relative flex min-h-0 flex-1 items-center justify-center overflow-auto bg-bg-base/40 p-3"
        >
          <button
            v-if="loaded.length > 1"
            type="button"
            class="absolute left-3 z-10 inline-flex size-9 items-center justify-center rounded-full border border-hairline bg-bg-elevated/90"
            aria-label="上一份"
            @click="prev"
          >
            <ChevronLeft class="size-5" />
          </button>
          <img
            v-if="current.kind === 'image'"
            :src="current.url"
            :alt="current.name"
            class="max-h-[78vh] max-w-full rounded-md border border-hairline bg-white object-contain"
          />
          <iframe
            v-else-if="current.kind === 'pdf'"
            :src="current.url"
            :title="current.name"
            class="h-[78vh] w-full border-0 bg-white"
          />
          <p v-else class="px-6 py-10 text-center text-[12px] text-text-muted">
            请点「下载原件」查看该文件。
          </p>
          <button
            v-if="loaded.length > 1"
            type="button"
            class="absolute right-3 z-10 inline-flex size-9 items-center justify-center rounded-full border border-hairline bg-bg-elevated/90"
            aria-label="下一份"
            @click="next"
          >
            <ChevronRight class="size-5" />
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

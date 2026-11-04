<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight, FileText, FolderOpen, ImageIcon, Trash2, X } from 'lucide-vue-next'
import { getApiBaseUrl } from '@/lib/api'
import {
  inferLibraryFileKind,
  resolveLibraryFileId,
  tenderLibraryFilePath,
  type SlotFileInfo,
} from '@/lib/tenders-api'

const props = defineProps<{
  files: SlotFileInfo[]
  removable?: boolean
  removingId?: string | null
  savingId?: string | null
}>()

const emit = defineEmits<{
  remove: [file: SlotFileInfo]
  saveFacts: [payload: { id: string; role: string; fields: { name: string; value: string }[] }]
}>()

const ROLE_LABEL: Record<string, string> = {
  company: '企业',
  'person/legal': '法定代表人',
  'person/pm': '项目经理',
  'person/pmWork': '工作经历',
  performance: '业绩',
  other: '其他',
}

const drafts = ref<Record<string, { name: string; value: string }[]>>({})

watch(
  () => props.files,
  (files) => {
    const next: Record<string, { name: string; value: string }[]> = {}
    for (const file of files) {
      const id = resolveLibraryFileId(file)
      if (!id || !file.facts?.fields?.length) continue
      next[id] = file.facts.fields.map((item) => ({ name: item.name, value: item.value }))
    }
    drafts.value = next
  },
  { immediate: true },
)

function saveFacts(id: string, role: string) {
  const fields = drafts.value[id]
  if (!id || !fields) return
  emit('saveFacts', { id, role, fields })
}

const openIndex = ref<number | null>(null)

const items = computed(() =>
  props.files.map((file) => ({
    file,
    id: resolveLibraryFileId(file),
    kind: inferLibraryFileKind(file),
  })),
)

const previewable = computed(() => items.value.filter((it) => it.id))

const current = computed(() => {
  const i = openIndex.value
  if (i == null) return null
  return previewable.value[i] ?? null
})

function fileUrl(id: string): string {
  return `${getApiBaseUrl()}${tenderLibraryFilePath(id, { withToken: true })}`
}

function formatSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function onRemove(file: SlotFileInfo, ev: Event) {
  ev.preventDefault()
  ev.stopPropagation()
  close()
  emit('remove', file)
}

function openAt(index: number) {
  if (!previewable.value[index]?.id) return
  openIndex.value = index
}

function openItem(id: string) {
  const idx = previewable.value.findIndex((it) => it.id === id)
  if (idx >= 0) openAt(idx)
}

function close() {
  openIndex.value = null
}

function prev() {
  if (openIndex.value == null || previewable.value.length < 2) return
  openAt((openIndex.value + previewable.value.length - 1) % previewable.value.length)
}

function next() {
  if (openIndex.value == null || previewable.value.length < 2) return
  openAt((openIndex.value + 1) % previewable.value.length)
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

watch(
  () => props.files,
  (files) => {
    if (openIndex.value == null) return
    const currentId = previewable.value[openIndex.value]?.id
    if (currentId && !files.some((f) => resolveLibraryFileId(f) === currentId)) {
      close()
    }
  },
)

onUnmounted(() => {
  document.body.style.overflow = ''
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <ul v-if="files.length" class="mt-2 space-y-1">
    <li
      v-for="it in items"
      :key="it.id || it.file.fileName || it.file.name"
      class="rounded border border-border/60 px-1.5 py-1"
    >
      <div class="flex min-h-7 items-center gap-1">
        <button
          type="button"
          class="group flex min-w-0 flex-1 items-center gap-1.5 rounded px-0.5 text-left text-[11px] leading-5 text-muted-foreground font-sans hover:bg-accent/50 disabled:opacity-60"
          :disabled="!it.id"
          :title="it.id ? `点击预览 ${it.file.performance?.projectName || it.file.name}` : (it.file.performance?.projectName || it.file.name)"
          @click="it.id && openItem(it.id)"
        >
          <FileText v-if="it.kind === 'pdf'" class="size-3.5 shrink-0 opacity-70" />
          <ImageIcon v-else-if="it.kind === 'image'" class="size-3.5 shrink-0 opacity-70" />
          <FolderOpen v-else class="size-3.5 shrink-0 opacity-70" />
          <span class="min-w-0 flex-1 truncate leading-5 underline-offset-2 group-hover:underline">{{
            it.file.performance?.projectName || it.file.name
          }}</span>
          <span class="shrink-0 leading-5 opacity-70">{{ formatSize(it.file.sizeBytes) }}</span>
        </button>
        <button
          v-if="removable && it.id"
          type="button"
          class="inline-flex size-7 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-sulfur disabled:opacity-50"
          :disabled="removingId === it.id"
          :title="`删除 ${it.file.performance?.projectName || it.file.name}`"
          @click="onRemove(it.file, $event)"
        >
          <Trash2 class="size-3.5" />
        </button>
      </div>
      <p
        v-if="it.file.performance && (it.file.performance.client || it.file.performance.spec || it.file.performance.amountYuan)"
        class="pl-6 pt-0.5 text-[10px] leading-4 text-muted-foreground truncate"
      >
        <template v-if="it.file.performance.spec">{{ it.file.performance.spec }} · </template>
        <template v-if="it.file.performance.client">{{ it.file.performance.client }} · </template>
        <template v-if="it.file.performance.amountYuan">{{ it.file.performance.amountYuan }} 元 · </template>
        {{ it.file.performance.ongoing ? '在建' : '已竣工' }}
      </p>
      <div v-if="it.id && drafts[it.id]?.length" class="space-y-1 pl-6 pt-1">
        <p class="text-[10px] leading-4 text-muted-foreground">
          {{ ROLE_LABEL[it.file.facts?.role || ''] || '识别字段' }}
        </p>
        <div v-for="(field, idx) in drafts[it.id]" :key="field.name + idx" class="flex items-center gap-1">
          <span class="w-16 shrink-0 truncate text-[10px] text-muted-foreground" :title="field.name">{{
            field.name
          }}</span>
          <input
            v-model="field.value"
            class="h-6 min-w-0 flex-1 rounded border border-border bg-transparent px-1 text-[11px]"
            @click.stop
          />
        </div>
        <button
          type="button"
          class="h-6 rounded border border-border px-1.5 text-[10px] hover:bg-accent disabled:opacity-50"
          :disabled="savingId === it.id"
          @click.stop="saveFacts(it.id, it.file.facts?.role || '')"
        >
          {{ savingId === it.id ? '保存中' : '保存识别' }}
        </button>
      </div>
    </li>
  </ul>

  <Teleport to="body">
    <div
      v-if="current && openIndex != null"
      class="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/80 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="文件预览"
      @click.self="close"
    >
      <div
        class="flex w-full flex-col rounded-xl border border-border bg-card shadow-2xl"
        :class="current.kind === 'pdf' ? 'max-w-6xl h-[90vh]' : 'max-w-5xl max-h-[92vh]'"
      >
        <div class="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
          <div class="min-w-0 truncate text-[13px]">
            {{ current.file.performance?.projectName || current.file.name }}
            <span class="ml-2 text-[11px] text-muted-foreground">
              {{ current.kind === 'pdf' ? 'PDF 文档' : current.kind === 'image' ? '图片' : '文件' }}
              <template v-if="previewable.length > 1"> · {{ openIndex + 1 }} / {{ previewable.length }}</template>
            </span>
          </div>
          <div class="flex shrink-0 items-center gap-1">
            <button
              v-if="removable && current.id"
              type="button"
              class="inline-flex h-8 items-center gap-1 rounded-md px-2 text-[12px] text-muted-foreground hover:bg-accent hover:text-sulfur disabled:opacity-50"
              :disabled="removingId === current.id"
              @click="onRemove(current.file, $event)"
            >
              <Trash2 class="size-3.5" />
              删除
            </button>
            <button
              type="button"
              class="inline-flex size-8 items-center justify-center rounded-md hover:bg-accent"
              aria-label="关闭"
              @click="close"
            >
              <X class="size-4" />
            </button>
          </div>
        </div>
        <div class="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-slate-950/20">
          <button
            v-if="previewable.length > 1"
            type="button"
            class="absolute left-3 z-10 inline-flex size-9 items-center justify-center rounded-full border border-border bg-card/90"
            aria-label="上一份"
            @click="prev"
          >
            <ChevronLeft class="size-5" />
          </button>

          <img
            v-if="current.kind === 'image' && current.id"
            :src="fileUrl(current.id)"
            :alt="current.file.name"
            class="max-h-[78vh] max-w-full bg-white object-contain p-3"
          />
          <iframe
            v-else-if="current.kind === 'pdf' && current.id"
            :src="fileUrl(current.id)"
            :title="current.file.name"
            class="h-full min-h-[70vh] w-full border-0 bg-white"
          />
          <p v-else class="px-6 py-10 text-center text-[12px] text-muted-foreground">
            该类型暂不支持在线预览。资料库目前可预览图片和 PDF。
          </p>

          <button
            v-if="previewable.length > 1"
            type="button"
            class="absolute right-3 z-10 inline-flex size-9 items-center justify-center rounded-full border border-border bg-card/90"
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

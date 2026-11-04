<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Download, Loader2 } from 'lucide-vue-next'
import AppDialog from '@/components/ui/AppDialog.vue'
import TenderDocEditor from '@/components/tenders/TenderDocEditor.vue'
import { ApiError } from '@/lib/api'
import {
  downloadTenderFile,
  fetchTenderRecord,
  volumeDocx,
  type BidVolume,
  type TenderRecordItem,
} from '@/lib/tenders-api'
import type { TenderTask } from '@/lib/tender-tasks'

const props = defineProps<{
  open: boolean
  task: TenderTask | null
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const loading = ref(false)
const error = ref('')
const record = ref<TenderRecordItem | null>(null)
const downloading = ref<BidVolume | null>(null)
const previewVolume = ref<BidVolume>('business')
const layoutTick = ref(0)

const previewDoc = computed(() => {
  const row = record.value
  if (!row) return { file: '', name: '' }
  return volumeDocx(row, previewVolume.value)
})

watch(
  () => [props.open, props.task?.recordId] as const,
  async ([open, id]) => {
    if (!open || !id) {
      record.value = null
      error.value = ''
      loading.value = false
      return
    }
    loading.value = true
    error.value = ''
    record.value = null
    previewVolume.value = 'business'
    try {
      const detail = await fetchTenderRecord(id)
      if (!detail.docxAvailable || !detail.docxFile) {
        error.value = '该记录的 Word 文件已丢失，无法预览'
        return
      }
      record.value = detail
      await nextTick()
      layoutTick.value += 1
      window.setTimeout(() => {
        layoutTick.value += 1
      }, 120)
    } catch (e) {
      error.value = e instanceof ApiError || e instanceof Error ? e.message : '加载投标文件失败'
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

watch(previewVolume, () => {
  layoutTick.value += 1
})

async function onDownload(volume: BidVolume) {
  const row = record.value
  if (!row) return
  const doc = volumeDocx(row, volume)
  if (!doc.file) return
  downloading.value = volume
  error.value = ''
  try {
    await downloadTenderFile(doc.file, doc.name || `${row.projectName || 'bid'}.docx`)
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '下载失败'
  } finally {
    downloading.value = null
  }
}
</script>

<template>
  <AppDialog
    :open="open"
    size="xl"
    :title="task ? `预览投标文件 · ${task.projectName}` : '预览投标文件'"
    description="在当前页查看 Word，关闭后仍停留在任务列表。"
    @update:open="emit('update:open', $event)"
  >
    <div class="flex min-h-0 flex-1 flex-col gap-2">
      <div v-if="record?.techDocxFile" class="flex shrink-0 items-center gap-1">
        <button
          type="button"
          class="h-7 px-2.5 text-[11px] rounded-md border"
          :class="previewVolume === 'business' ? 'border-iron bg-iron text-background' : 'border-border hover:bg-accent'"
          @click="previewVolume = 'business'"
        >
          商务标
        </button>
        <button
          type="button"
          class="h-7 px-2.5 text-[11px] rounded-md border"
          :class="previewVolume === 'technical' ? 'border-iron bg-iron text-background' : 'border-border hover:bg-accent'"
          @click="previewVolume = 'technical'"
        >
          技术标
        </button>
      </div>
      <div
        v-if="loading"
        class="flex flex-1 items-center justify-center gap-2 text-xs text-muted-foreground"
      >
        <Loader2 class="size-4 animate-spin" />
        正在打开投标文件…
      </div>
      <p v-else-if="error && !record" class="text-xs text-iron">{{ error }}</p>
      <TenderDocEditor
        v-else-if="previewDoc.file"
        :key="previewDoc.file"
        class="min-h-0 flex-1"
        :docx-file="previewDoc.file"
        :download-name="previewDoc.name || `${record?.projectName || 'bid'}.docx`"
        mode="view"
        engine="browser"
        :layout-tick="layoutTick"
      />
    </div>
    <template #footer>
      <p v-if="error && record" class="mr-auto text-[11px] text-iron">{{ error }}</p>
      <button
        type="button"
        class="h-8 px-3 text-xs rounded-md border border-border hover:bg-accent"
        @click="emit('update:open', false)"
      >
        关闭
      </button>
      <button
        type="button"
        class="h-8 px-3 inline-flex items-center gap-1.5 text-xs rounded-md bg-iron text-background hover:bg-iron/90 disabled:opacity-40"
        :disabled="!record?.docxFile || downloading === 'business'"
        @click="onDownload('business')"
      >
        <Loader2 v-if="downloading === 'business'" class="size-3.5 animate-spin" />
        <Download v-else class="size-3.5" />
        下载商务标
      </button>
      <button
        v-if="record?.techDocxFile"
        type="button"
        class="h-8 px-3 inline-flex items-center gap-1.5 text-xs rounded-md border border-border hover:bg-accent disabled:opacity-40"
        :disabled="downloading === 'technical'"
        @click="onDownload('technical')"
      >
        <Loader2 v-if="downloading === 'technical'" class="size-3.5 animate-spin" />
        <Download v-else class="size-3.5" />
        下载技术标
      </button>
    </template>
  </AppDialog>
</template>

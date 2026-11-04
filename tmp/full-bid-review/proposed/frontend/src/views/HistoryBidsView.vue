<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { PageHeader, Panel, Tag } from '@/components/ui-kit'
import HistoryChapterPreview from '@/components/tenders/HistoryChapterPreview.vue'
import { ApiError } from '@/lib/api'
import { TASK_STATUS_LABEL, TASK_STATUS_TONE, type TaskStatus } from '@/lib/tender-tasks'
import {
  fetchHistoryChapters,
  fetchTenderRecord,
  fetchTenderRecords,
  saveHistoryMark,
  uploadHistoryBid,
  type HistoryChapter,
  type TenderRecordItem,
} from '@/lib/tenders-api'
import { useAuthStore } from '@/stores/auth'

const PROBLEM_TAGS = ['报价', '业绩', '工期', '资质', '签章', '格式', '法人', '付款', '否决', '其他']
const VOL_LABEL: Record<string, string> = { business: '商务标', technical: '技术标' }

const route = useRoute()
const auth = useAuthStore()

const items = ref<TenderRecordItem[]>([])
const loading = ref(false)
const err = ref('')
const selectedId = ref('')
const chapters = ref<HistoryChapter[]>([])
const previewChapter = ref<HistoryChapter | null>(null)
const chapErr = ref('')
const chapLoading = ref(false)
const openKey = ref('')
const draftTags = ref<string[]>([])
const draftNote = ref('')
const savingKey = ref('')
const uploadFile = ref<File | null>(null)
const uploadInput = ref<HTMLInputElement | null>(null)
const uploadOutcome = ref<'won' | 'lost' | ''>('')
const uploading = ref(false)
const archiving = ref(false)
const uploadErr = ref('')
const uploadNotice = ref('')
const uploadedRecordId = ref('')
const uploadBusy = computed(() => uploading.value || loading.value)
let chapterRequest = 0
let chapterLoad: Promise<void> = Promise.resolve()
let cancelDelay: (() => void) | undefined

function stopChapterLoad() {
  chapterRequest++
  cancelDelay?.()
}

onScopeDispose(stopChapterLoad)

function pollDelay() {
  return new Promise<void>((resolve) => {
    const finish = () => { clearTimeout(timer); cancelDelay = undefined; resolve() }
    const timer = setTimeout(finish, 1500)
    cancelDelay = finish
  })
}

const canSeeAll = computed(() => auth.isAdmin || (auth.menus || []).includes('/tender-tasks'))
const selected = computed(() => items.value.find((it) => it.id === selectedId.value) || null)

function statusLabel(status?: string) {
  if (!status) return '未指定'
  return TASK_STATUS_LABEL[status as TaskStatus] || status
}

function statusTone(status?: string) {
  if (!status) return 'default'
  return TASK_STATUS_TONE[status as TaskStatus] || 'default'
}

function keyOf(ch: HistoryChapter) {
  return `${ch.volume}:${ch.chapterTitle}`
}

function verdictLabel(v: string) {
  if (v === 'good') return '可参考'
  if (v === 'bad') return '需规避'
  return '未标记'
}

function msgOf(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.message : fallback
}

async function loadList() {
  if (loading.value) return false
  loading.value = true
  err.value = ''
  const scope = canSeeAll.value ? 'all' : 'mine'
  try {
    // 仅补充本页上传过的记录，避免“不指定”被 won/lost 查询漏掉。
    const extraIds = new Set(items.value.filter((it) => !['won', 'lost'].includes(it.status || '')).map((it) => it.id))
    if (uploadedRecordId.value) extraIds.add(uploadedRecordId.value)
    const [won, lost, uploaded] = await Promise.all([
      fetchTenderRecords({ status: 'won', scope, limit: 100 }),
      fetchTenderRecords({ status: 'lost', scope, limit: 100 }),
      Promise.all([...extraIds].map((id) => fetchTenderRecord(id))),
    ])
    const records = new Map([...uploaded, ...won.items, ...lost.items].map((it) => [it.id, it]))
    items.value = [...records.values()].sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))
    return true
  } catch (e) {
    err.value = msgOf(e, '加载失败')
    items.value = []
    return false
  } finally {
    loading.value = false
  }
}

function chooseUploadFile(event: Event) {
  if (uploadBusy.value) return
  uploadFile.value = (event.target as HTMLInputElement).files?.[0] || null
  uploadErr.value = ''
  uploadNotice.value = ''
}

async function refreshList() {
  if (uploadBusy.value) return
  if (await loadList()) {
    if (uploadedRecordId.value) {
      const unchanged = selectedId.value === uploadedRecordId.value
      selectedId.value = uploadedRecordId.value
      uploadNotice.value = '列表已刷新，已选中新标书'
      if (unchanged) chapterLoad = loadChapters(selectedId.value)
      await chapterLoad
    }
  }
}

async function upload() {
  if (uploadBusy.value) return
  if (!uploadFile.value) {
    uploadErr.value = '请选择要上传的 docx 标书'
    return
  }
  uploading.value = true
  uploadErr.value = ''
  uploadNotice.value = ''
  try {
    const data = await uploadHistoryBid(uploadFile.value, uploadOutcome.value)
    // 文件已被接收后先清空选择；刷新失败时可单独重试，避免重复上传。
    uploadFile.value = null
    if (uploadInput.value) uploadInput.value.value = ''
    if (!data.recordId) {
      uploadErr.value = '文件已接收，但服务未返回历史记录编号，暂无法加入列表并选中。请联系管理员核对上传接口。'
      return
    }
    uploadedRecordId.value = data.recordId
    archiving.value = data.status !== 'failed'
    uploadNotice.value = '标书入库中…'
    if (await loadList()) {
      selectedId.value = data.recordId
      await chapterLoad
      if (data.status === 'failed') uploadErr.value = `入库失败：${data.archiveNote || '请稍后重试'}`
    } else {
      uploadNotice.value = '上传成功，但列表刷新失败。请点击刷新列表，无需重新上传。'
    }
  } catch (e) {
    uploadErr.value = msgOf(e, '上传失败，请稍后重试')
  } finally {
    archiving.value = false
    uploading.value = false
  }
}

async function loadChapters(id: string) {
  stopChapterLoad()
  const request = chapterRequest
  chapLoading.value = true
  chapErr.value = ''
  chapters.value = []
  openKey.value = ''
  try {
    for (let attempt = 0; attempt < 15; attempt++) {
      const data = await fetchHistoryChapters(id)
      if (request !== chapterRequest) return
      chapters.value = data.chapters || []
      if (data.status === 'failed') {
        chapErr.value = `入库失败：${data.archiveNote || '请稍后重试'}`
        if (id === uploadedRecordId.value) {
          uploadErr.value = chapErr.value
          uploadNotice.value = ''
        }
        return
      }
      if (chapters.value.length && data.status !== 'processing') {
        if (id === uploadedRecordId.value) {
          uploadErr.value = ''
          uploadNotice.value = '入库成功，已加载章节，可进行章节标记'
        }
        return
      }
      if (data.status !== 'processing' && id !== uploadedRecordId.value) return
      if (attempt < 14) await pollDelay()
      if (request !== chapterRequest) return
    }
    chapErr.value = '标书入库中，请稍后刷新'
    if (id === uploadedRecordId.value) uploadNotice.value = chapErr.value
  } catch (e) {
    if (request === chapterRequest) {
      chapErr.value = msgOf(e, '章节加载失败')
      if (id === uploadedRecordId.value) {
        uploadErr.value = chapErr.value
        uploadNotice.value = ''
      }
    }
  } finally {
    if (request === chapterRequest) chapLoading.value = false
  }
}

function selectRecord(id: string) {
  previewChapter.value = null
  selectedId.value = id
}

function openBad(ch: HistoryChapter) {
  openKey.value = keyOf(ch)
  draftTags.value = [...(ch.problemTags || [])]
  draftNote.value = ch.note || ''
}

function toggleTag(tag: string) {
  if (draftTags.value.includes(tag)) {
    draftTags.value = draftTags.value.filter((t) => t !== tag)
  } else {
    draftTags.value = [...draftTags.value, tag]
  }
}

async function save(ch: HistoryChapter, verdict: 'good' | 'bad' | 'none', tags: string[], note: string) {
  if (!selectedId.value) return
  const key = keyOf(ch)
  savingKey.value = key
  chapErr.value = ''
  try {
    const saved = await saveHistoryMark({
      recordId: selectedId.value,
      docId: ch.docId || '',
      volume: ch.volume,
      chapterTitle: ch.chapterTitle,
      verdict,
      problemTags: tags,
      note,
    })
    const idx = chapters.value.findIndex((it) => keyOf(it) === key)
    if (idx >= 0) chapters.value[idx] = { ...chapters.value[idx], ...saved }
    if (verdict !== 'bad') openKey.value = ''
  } catch (e) {
    chapErr.value = msgOf(e, '保存失败')
  } finally {
    savingKey.value = ''
  }
}

watch(selectedId, (id) => {
  if (!id) {
    stopChapterLoad()
    chapters.value = []
    chapLoading.value = false
    return
  }
  chapterLoad = loadChapters(id)
}, { flush: 'sync' })

onMounted(async () => {
  await loadList()
  const q = String(route.query.recordId || '')
  if (q && items.value.some((it) => it.id === q)) selectedId.value = q
  else if (!selectedId.value && items.value.length === 1) selectedId.value = items.value[0].id
})
</script>

<template>
  <PageHeader title="历史标书" description="已中标标成可参考，未中标标成需规避。人手改过的章节不会被自动初标盖掉。" />

  <p v-if="err" class="mb-3 text-xs text-iron">{{ err }}</p>

  <div class="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[minmax(16rem,20rem)_1fr]">
    <Panel :title="canSeeAll ? '中标与失败' : '我的中标与失败'" :subtitle="`共 ${items.length} 条`" flush>
      <form class="space-y-2 border-b border-border p-3" :aria-busy="uploadBusy" @submit.prevent="upload">
        <div class="text-[12px] font-medium">上传中标/失败标书</div>
        <input
          id="history-bid-file"
          ref="uploadInput"
          type="file"
          accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          class="sr-only"
          :disabled="uploadBusy"
          @change="chooseUploadFile"
        />
        <label
          for="history-bid-file"
          class="flex h-8 cursor-pointer items-center gap-2 rounded-md border border-dashed border-border bg-background px-2 text-[12px] hover:border-iron/40 hover:bg-accent/40"
          :class="uploadBusy ? 'pointer-events-none opacity-50' : ''"
        >
          <span class="shrink-0 rounded bg-iron/10 px-1.5 py-0.5 text-[11px] text-iron">docx</span>
          <span class="min-w-0 flex-1 truncate" :class="uploadFile ? 'text-foreground' : 'text-muted-foreground'">
            {{ uploadFile ? uploadFile.name : '选择历史标书' }}
          </span>
        </label>
        <div class="flex items-center gap-2">
          <label for="history-bid-outcome" class="text-[11px] text-muted-foreground">结果</label>
          <select
            id="history-bid-outcome"
            v-model="uploadOutcome"
            class="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-[12px]"
            :disabled="uploadBusy"
          >
            <option value="">不指定</option>
            <option value="won">已中标</option>
            <option value="lost">已失败</option>
          </select>
          <button
            type="submit"
            class="h-8 rounded-md bg-iron px-3 text-[12px] text-white disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="uploadBusy || !uploadFile"
          >{{ archiving ? '入库中…' : uploading ? '上传中…' : '上传' }}</button>
        </div>
        <button
          type="button"
          class="text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-50"
          :disabled="uploadBusy"
          @click="refreshList"
        >{{ loading ? '刷新中…' : '刷新列表' }}</button>
        <p v-if="uploadErr" role="alert" class="text-xs text-iron">{{ uploadErr }}</p>
        <p v-if="uploadNotice" role="status" class="text-[11px] text-muted-foreground">{{ uploadNotice }}</p>
      </form>
      <div v-if="loading && !items.length" class="py-16 text-center text-xs text-muted-foreground">加载任务…</div>
      <div v-else-if="!items.length" class="py-16 text-center text-xs text-muted-foreground">还没有历史标书，可在上方上传。</div>
      <ul v-else class="max-h-[min(70vh,40rem)] overflow-y-auto">
        <li v-for="it in items" :key="it.id">
          <button
            type="button"
            class="w-full border-b border-border px-3 py-2.5 text-left hover:bg-accent/40"
            :class="it.id === selectedId ? 'bg-iron/10' : ''"
            @click="selectRecord(it.id)"
          >
            <div class="truncate text-[12px] font-medium">{{ it.projectName || '未命名项目' }}</div>
            <div class="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
              <span class="truncate">{{ it.tenderer || '—' }}</span>
              <Tag :tone="statusTone(it.status)">{{ statusLabel(it.status) }}</Tag>
            </div>
          </button>
        </li>
      </ul>
    </Panel>

    <Panel v-if="!selected" title="章节标记">
      <p class="text-[12px] leading-relaxed text-muted-foreground">
        左侧选一份历史标书。可参考的章节以后生成时优先借鉴，需规避的记下问题，避免再踩。
      </p>
    </Panel>

    <Panel v-else :title="selected.projectName || '未命名项目'" :subtitle="selected.tenderer || '—'">
      <p v-if="chapErr" class="mb-3 text-xs text-iron">{{ chapErr }}</p>
      <div v-if="chapLoading && !chapters.length" class="py-10 text-center text-xs text-muted-foreground">加载章节，标书入库中请稍候…</div>
      <div v-else-if="!chapters.length" class="py-10 text-center text-xs text-muted-foreground">
        还没有可标记的章节。标书入库后会出现在这里。
      </div>
      <ul v-else class="divide-y divide-border">
        <li v-for="ch in chapters" :key="keyOf(ch)" class="py-3">
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div class="min-w-0">
              <div class="text-[12px] font-medium">
                <span class="mr-2 text-[11px] text-muted-foreground">{{ VOL_LABEL[ch.volume] || ch.volume }}</span>
                {{ ch.displayTitle || ch.chapterTitle }}
              </div>
              <div class="mt-1 flex flex-wrap items-center gap-1.5">
                <Tag :tone="ch.verdict === 'good' ? 'patina' : ch.verdict === 'bad' ? 'iron' : 'default'">
                  {{ verdictLabel(ch.verdict) }}
                </Tag>
                <span v-for="tag in ch.problemTags" :key="tag" class="text-[11px] text-muted-foreground">{{ tag }}</span>
              </div>
              <p v-if="ch.note" class="mt-1 text-[11px] leading-relaxed text-muted-foreground">{{ ch.note }}</p>
            </div>
            <div class="flex shrink-0 gap-1.5">
              <button type="button" class="h-7 rounded-md border border-border px-2 text-[11px] hover:bg-accent/40"
                @click="previewChapter = ch">预览</button>
              <button
                type="button"
                class="h-7 rounded-md border border-border px-2 text-[11px] hover:bg-accent/40"
                :disabled="savingKey === keyOf(ch)"
                @click="save(ch, 'good', [], '')"
              >
                可参考
              </button>
              <button
                type="button"
                class="h-7 rounded-md border border-border px-2 text-[11px] hover:bg-accent/40"
                :disabled="savingKey === keyOf(ch)"
                @click="openBad(ch)"
              >
                需规避
              </button>
              <button
                v-if="ch.verdict !== 'none'"
                type="button"
                class="h-7 rounded-md px-2 text-[11px] text-muted-foreground hover:bg-accent/40"
                :disabled="savingKey === keyOf(ch)"
                @click="save(ch, 'none', [], '')"
              >
                取消
              </button>
            </div>
          </div>
          <div v-if="openKey === keyOf(ch)" class="mt-2 rounded-md border border-border p-2">
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="tag in PROBLEM_TAGS"
                :key="tag"
                type="button"
                class="h-6 rounded-md border px-2 text-[11px]"
                :class="draftTags.includes(tag) ? 'border-iron bg-iron/10' : 'border-border'"
                @click="toggleTag(tag)"
              >
                {{ tag }}
              </button>
            </div>
            <textarea
              v-model="draftNote"
              rows="2"
              maxlength="500"
              class="mt-2 w-full rounded-md border border-border bg-background px-2 py-1 text-[12px]"
              placeholder="补充说明，可不填"
            />
            <button
              type="button"
              class="mt-2 h-7 rounded-md bg-iron px-3 text-[11px] text-white disabled:opacity-50"
              :disabled="savingKey === keyOf(ch)"
              @click="save(ch, 'bad', draftTags, draftNote)"
            >
              保存
            </button>
          </div>
        </li>
      </ul>
    </Panel>
  </div>
  <HistoryChapterPreview v-if="previewChapter" :record-id="selectedId" :chapter="previewChapter"
    @close="previewChapter = null" />
</template>

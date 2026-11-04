<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Eye,
  FileSpreadsheet,
  History,
  Loader2,
  Maximize2,
  Plus,
  Trash2,
  TriangleAlert,
  Upload,
  X,
} from 'lucide-vue-next'
import { PageHeader, Panel, Tag } from '@/components/ui-kit'
import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import { ApiError } from '@/lib/api'
import { listKnowledgeBases, type KnowledgeBaseItem } from '@/lib/knowledge-api'
import { useAuthStore } from '@/stores/auth'
import {
  clearMineQuoteRecords,
  costQuote,
  defaultRates,
  deleteQuoteRecord,
  downloadQuoteFile,
  emptyQuoteLine,
  exportQuoteRecord,
  fetchCatalogCandidates,
  fetchQuoteRecord,
  fetchQuoteRecords,
  generateQuote,
  lockQuoteRecord,
  type CatalogHit,
  lineAmount,
  parseBoqQuote,
  PURPOSE_META,
  purposeFromPath,
  recognizeQuote,
  type QuoteCostSummary,
  type QuoteLine,
  type QuotePurpose,
  type QuoteRates,
  type QuoteRecordItem,
  type QuoteScheme,
  type QuoteVerifyReport,
  verifyQuote,
} from '@/lib/quotes-api'

const KB_STORE = 'weitai.quoteKbId'
const MAX_MAPS = 20
const router = useRouter()
const route = useRoute()

const auth = useAuthStore()
const purpose = computed<QuotePurpose>(() => purposeFromPath(route.path))
const meta = computed(() => PURPOSE_META[purpose.value])
const seeCost = computed(() => purpose.value !== 'quote' || auth.isAdmin)

const PURPOSE_TABS: { id: QuotePurpose; label: string }[] = [
  { id: 'cost', label: '造价' },
  { id: 'quote', label: '报价' },
  { id: 'budget', label: '预算' },
]

function switchPurpose(id: QuotePurpose) {
  const href = PURPOSE_META[id].href
  if (href === route.path) return
  void router.push(href)
}

const projectName = ref('')
const note = ref('')
const location = ref('')
const durationDays = ref<number | null>(null)
const bidCeiling = ref<number | null>(null)
const competition = ref<'conservative' | 'balanced' | 'aggressive'>('balanced')
const targetMargin = ref<number | null>(null)
const rates = ref<QuoteRates>(defaultRates())

type MapPreview = { url: string; name: string; kind: 'image' | 'pdf' }

const files = ref<File[]>([])
const previews = ref<MapPreview[]>([])
const peek = ref<number | null>(null)
const boqFile = ref<File | null>(null)
const lines = ref<QuoteLine[]>([])
const warnings = ref<string[]>([])
const verifyReport = ref<QuoteVerifyReport | null>(null)
const costSummary = ref<QuoteCostSummary | null>(null)
const schemes = ref<QuoteScheme[]>([])
const selectedScheme = ref('')
const forceExport = ref(false)
const lastRecordId = ref('')
const lastRecordStatus = ref('')
const lockingId = ref<string | null>(null)
const pickOpen = ref(false)
const pickIdx = ref(-1)
const pickHits = ref<CatalogHit[]>([])
const pickLoading = ref(false)
const instruction = ref('')
const recognizing = ref(false)
const parsingBoq = ref(false)
const verifying = ref(false)
const costing = ref(false)
const generating = ref(false)
const err = ref('')
const hint = ref('')
const mapInput = ref<HTMLInputElement | null>(null)
const boqInput = ref<HTMLInputElement | null>(null)
const showRecords = ref(false)
const recordsLoading = ref(false)
const records = ref<QuoteRecordItem[]>([])
const recordsTotal = ref(0)
const recordsQuery = ref('')
const loadingRecordId = ref<string | null>(null)
const downloadingId = ref<string | null>(null)
const deletingId = ref<string | null>(null)
const confirmDeleteOpen = ref(false)
const pendingDelete = ref<QuoteRecordItem | null>(null)
const confirmClearOpen = ref(false)
const clearingRecords = ref(false)
const previewOpen = ref(false)
const previewingId = ref<string | null>(null)
const previewDetail = ref<QuoteRecordItem | null>(null)

const kbList = ref<KnowledgeBaseItem[]>([])
const kbId = ref('')
const kbLoading = ref(false)

const total = computed(() => lines.value.reduce((s, r) => s + lineAmount(r), 0))
const selectedKb = computed(() => kbList.value.find((b) => b.id === kbId.value) || null)

const SOURCE: Record<string, string> = {
  vision: '读图',
  rule: '规则',
  catalog: '价目',
  estimate: '估价',
  boq: '工程量',
  manual: '人工',
}

function linePending(row: QuoteLine) {
  const price = Number(row.sellPrice) || Number(row.unitPrice) || Number(row.costPrice) || 0
  if (price <= 0) return true
  return (row.matchStatus || '') === 'estimate'
}

const pendingCount = computed(() => lines.value.filter((r) => r.name && linePending(r)).length)

watch(purpose, () => {
  showRecords.value = false
  void loadRecords(true)
})

function isPdfFile(f: File) {
  return f.type === 'application/pdf' || /\.pdf$/i.test(f.name || '')
}

watch(files, (next) => {
  previews.value.forEach((p) => URL.revokeObjectURL(p.url))
  peek.value = null
  previews.value = next.map((f) => ({
    url: URL.createObjectURL(f),
    name: f.name || '规划图',
    kind: isPdfFile(f) ? 'pdf' : 'image',
  }))
})

watch(kbId, (id) => {
  if (id) localStorage.setItem(KB_STORE, id)
})

const peekItem = computed(() => (peek.value == null ? null : previews.value[peek.value] || null))

watch(peek, (v, _, onCleanup) => {
  document.body.style.overflow = v == null ? '' : 'hidden'
  if (v == null) return
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') peek.value = null
  }
  window.addEventListener('keydown', onKey)
  onCleanup(() => window.removeEventListener('keydown', onKey))
})

onMounted(() => {
  void loadKb()
  void loadRecords(true)
})

onUnmounted(() => {
  document.body.style.overflow = ''
  previews.value.forEach((p) => URL.revokeObjectURL(p.url))
})

async function loadKb() {
  kbLoading.value = true
  try {
    const rows = await listKnowledgeBases({ access: 'use' })
    kbList.value = rows.filter((b) => b.id !== 'tenderlib01' && b.purpose !== 'asset')
    const saved = localStorage.getItem(KB_STORE) || ''
    if (saved && kbList.value.some((b) => b.id === saved)) {
      kbId.value = saved
    } else if (kbList.value.length === 1) {
      kbId.value = kbList.value[0]!.id
    }
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '加载知识库失败'
  } finally {
    kbLoading.value = false
  }
}

function onPickMaps(ev: Event) {
  const el = ev.target as HTMLInputElement
  const picked = Array.from(el.files || [])
  el.value = ''
  if (picked.length > MAX_MAPS) {
    err.value = `规划图一次最多 ${MAX_MAPS} 张`
    files.value = picked.slice(0, MAX_MAPS)
    return
  }
  files.value = picked
}

function onPickBoq(ev: Event) {
  const el = ev.target as HTMLInputElement
  const f = el.files?.[0] || null
  el.value = ''
  boqFile.value = f
}

function onQtyPrice(row: QuoteLine) {
  if (!(Number(row.sellPrice) > 0) && Number(row.unitPrice) > 0) row.sellPrice = row.unitPrice
  if (!(Number(row.costPrice) > 0) && Number(row.unitPrice) > 0) row.costPrice = row.unitPrice
  row.amount = lineAmount(row)
}

function normalizeLines(rows: QuoteLine[]) {
  return rows.map((r) => {
    const row = { ...emptyQuoteLine(), ...r }
    if (!(Number(row.sellPrice) > 0) && Number(row.unitPrice) > 0) row.sellPrice = row.unitPrice
    if (!(Number(row.costPrice) > 0) && Number(row.unitPrice) > 0) row.costPrice = row.unitPrice
    row.amount = lineAmount(row)
    return row
  })
}

async function onRecognize() {
  err.value = ''
  hint.value = ''
  if (!kbId.value) {
    err.value = '请先选择知识库（价目 Excel 从该库读取）'
    return
  }
  if (!files.value.length) {
    err.value = '请先上传场地规划图（图片或 PDF）'
    return
  }
  recognizing.value = true
  try {
    const data = await recognizeQuote({
      files: files.value,
      baseId: kbId.value,
      note: note.value,
      projectName: projectName.value,
      purpose: purpose.value,
      location: location.value,
      durationDays: durationDays.value,
    })
    if (data.projectName && !projectName.value) projectName.value = data.projectName
    lines.value = normalizeLines(data.lines || [])
    warnings.value = data.warnings || []
    verifyReport.value = data.verify || null
    costSummary.value = null
    schemes.value = []
    instruction.value = ''
    hint.value = data.unmatched
      ? `已识别 ${data.lines.length} 项，其中 ${data.unmatched} 项未匹配到价目。`
      : `已识别 ${data.lines.length} 项`
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '识别失败'
  } finally {
    recognizing.value = false
  }
}

async function onParseBoq() {
  err.value = ''
  hint.value = ''
  if (!kbId.value) {
    err.value = '请先选择知识库'
    return
  }
  if (!boqFile.value) {
    err.value = '请先选择工程量 Excel'
    return
  }
  parsingBoq.value = true
  try {
    const data = await parseBoqQuote({
      file: boqFile.value,
      baseId: kbId.value,
      purpose: purpose.value,
      projectName: projectName.value,
      location: location.value,
      durationDays: durationDays.value,
    })
    lines.value = normalizeLines(data.lines || [])
    warnings.value = data.warnings || []
    verifyReport.value = data.verify || null
    costSummary.value = null
    schemes.value = []
    instruction.value = ''
    hint.value = `已解析工程量 ${data.lines.length} 项`
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '解析工程量失败'
  } finally {
    parsingBoq.value = false
  }
}

async function onVerify(applyFixes = false) {
  err.value = ''
  verifying.value = true
  try {
    const data = await verifyQuote({
      lines: lines.value.filter((r) => r.name.trim()),
      applyFixes,
    })
    verifyReport.value = data.verify
    if (applyFixes && data.lines) {
      lines.value = normalizeLines(data.lines)
    }
    hint.value = data.verify.ok
      ? `核算通过（${data.verify.warnCount} 条提示）`
      : `核算发现 ${data.verify.errorCount} 个错误、${data.verify.warnCount} 条提示`
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '核算失败'
  } finally {
    verifying.value = false
  }
}

async function onCost() {
  err.value = ''
  costing.value = true
  try {
    const data = await costQuote({
      lines: lines.value.filter((r) => r.name.trim()),
      rates: rates.value,
      purpose: purpose.value,
      projectName: projectName.value,
      location: location.value,
      durationDays: durationDays.value,
      bidCeiling: purpose.value === 'quote' ? bidCeiling.value : null,
      competition: competition.value,
      targetMargin: purpose.value === 'quote' ? targetMargin.value : null,
    })
    lines.value = normalizeLines(data.lines || [])
    costSummary.value = data.costSummary
    verifyReport.value = data.verify
    if (data.schemes?.length) {
      schemes.value = data.schemes
      selectedScheme.value = data.schemes.find((s) => s.recommended)?.name || data.schemes[0]!.name
    }
    hint.value = data.costSummary.costExTax
      ? `造价测算完成，成本不含税 ${money(data.costSummary.costExTax)} 元`
      : '造价测算完成'
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '造价测算失败'
  } finally {
    costing.value = false
  }
}

function addLine() {
  const row = emptyQuoteLine()
  row.seq = String(lines.value.length + 1)
  lines.value.push(row)
}

function removeLine(idx: number) {
  lines.value.splice(idx, 1)
  lines.value.forEach((r, i) => {
    r.seq = String(i + 1)
  })
}

async function onGenerate() {
  err.value = ''
  hint.value = ''
  const rows = lines.value.filter((r) => r.name.trim())
  if (!rows.length) {
    err.value = '请先识别规划图或解析工程量，或手工补一行'
    return
  }
  generating.value = true
  try {
    const data = await generateQuote({
      purpose: purpose.value,
      projectName: projectName.value,
      note: note.value,
      location: location.value,
      durationDays: durationDays.value,
      bidCeiling: purpose.value === 'quote' ? bidCeiling.value : null,
      competition: competition.value,
      targetMargin: purpose.value === 'quote' ? targetMargin.value : null,
      taxRate: rates.value.taxRate,
      baseId: kbId.value,
      baseName: selectedKb.value?.name || '',
      rates: rates.value,
      applyVerifyFixes: true,
      schemeName: selectedScheme.value,
      forceExport: forceExport.value,
      lines: rows.map((r) => ({ ...r, amount: lineAmount(r) })),
    })
    await downloadQuoteFile(data.xlsxFile, data.downloadName)
    if (data.costSummary) costSummary.value = data.costSummary
    if (data.verify) verifyReport.value = data.verify
    if (data.schemes) schemes.value = data.schemes
    if (data.instruction) instruction.value = data.instruction
    if (data.schemeName) selectedScheme.value = data.schemeName
    lastRecordId.value = data.id || ''
    lastRecordStatus.value = data.status || 'draft'
    hint.value = `已下载 ${data.downloadName}，不含税合计 ${money(data.totalExTax)} 元。签认锁定后才能用于投标组卷。`
    void loadRecords()
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '生成失败'
  } finally {
    generating.value = false
  }
}

function formatRecordTime(ms: number) {
  if (!ms) return '—'
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function money(n: number) {
  return (Number(n) || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function applyDetail(detail: QuoteRecordItem) {
  projectName.value = detail.projectName || ''
  note.value = detail.note || ''
  location.value = detail.location || ''
  durationDays.value = detail.durationDays ?? null
  bidCeiling.value = detail.bidCeiling ?? null
  competition.value = (detail.competition as typeof competition.value) || 'balanced'
  targetMargin.value = detail.targetMargin ?? null
  if (detail.rates) rates.value = { ...defaultRates(), ...detail.rates }
  if (detail.baseId && kbList.value.some((b) => b.id === detail.baseId)) {
    kbId.value = detail.baseId
  }
  lines.value = normalizeLines(detail.lines || [])
  verifyReport.value = detail.verify || null
  schemes.value = detail.schemes || []
  selectedScheme.value = detail.schemeName || schemes.value.find((s) => s.applied || s.recommended)?.name || ''
  lastRecordId.value = detail.id
  lastRecordStatus.value = detail.status || 'draft'
  instruction.value = detail.instruction || ''
  warnings.value = []
  showRecords.value = false
  previewOpen.value = false
  hint.value = `已载入「${detail.projectName || '未命名项目'}」共 ${lines.value.length} 行`
}

async function loadRecords(silent = false) {
  recordsLoading.value = true
  try {
    const data = await fetchQuoteRecords({
      q: recordsQuery.value.trim() || undefined,
      purpose: purpose.value,
      limit: 50,
    })
    records.value = data.items || []
    recordsTotal.value = data.total || 0
  } catch (e) {
    if (!silent) {
      err.value = e instanceof ApiError || e instanceof Error ? e.message : '加载记录失败'
    }
  } finally {
    recordsLoading.value = false
  }
}

function toggleRecords() {
  showRecords.value = !showRecords.value
  if (showRecords.value) void loadRecords()
}

async function loadRecordLines(item: QuoteRecordItem) {
  if (previewDetail.value?.id === item.id && previewDetail.value.lines) {
    applyDetail(previewDetail.value)
    return
  }
  loadingRecordId.value = item.id
  err.value = ''
  try {
    applyDetail(await fetchQuoteRecord(item.id))
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '载入记录失败'
  } finally {
    loadingRecordId.value = null
  }
}

async function previewRecord(item: QuoteRecordItem) {
  previewingId.value = item.id
  err.value = ''
  try {
    previewDetail.value = await fetchQuoteRecord(item.id)
    previewOpen.value = true
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '打开预览失败'
  } finally {
    previewingId.value = null
  }
}

function loadFromPreview() {
  if (!previewDetail.value) return
  applyDetail(previewDetail.value)
}

async function downloadRecord(item: QuoteRecordItem) {
  downloadingId.value = item.id
  err.value = ''
  try {
    let file = item.xlsxFile
    let name = item.downloadName
    if (!item.xlsxAvailable) {
      const again = await exportQuoteRecord(item.id)
      file = again.xlsxFile
      name = again.downloadName
      const idx = records.value.findIndex((r) => r.id === item.id)
      if (idx >= 0) records.value.splice(idx, 1, { ...records.value[idx], ...again })
      if (previewDetail.value?.id === item.id) {
        previewDetail.value = { ...previewDetail.value, ...again }
      }
    }
    await downloadQuoteFile(file, name)
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '下载失败'
  } finally {
    downloadingId.value = null
  }
}

function confirmLine(row: QuoteLine) {
  row.matchStatus = 'confirmed'
  if (row.source === 'estimate') row.source = 'catalog'
}

async function openPick(row: QuoteLine, idx: number) {
  if (!kbId.value) {
    err.value = '请先选择知识库'
    return
  }
  pickIdx.value = idx
  pickOpen.value = true
  pickLoading.value = true
  pickHits.value = []
  try {
    const data = await fetchCatalogCandidates({
      baseId: kbId.value,
      name: row.name,
      spec: row.spec,
      code: row.code,
    })
    pickHits.value = data.items || []
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '加载价目候选失败'
    pickOpen.value = false
  } finally {
    pickLoading.value = false
  }
}

function applyHit(hit: CatalogHit) {
  const row = lines.value[pickIdx.value]
  if (!row) return
  row.matchName = hit.name
  if (hit.spec) row.spec = hit.spec
  if (hit.unit) row.unit = hit.unit
  if (seeCost.value) row.costPrice = hit.costPrice
  row.sellPrice = hit.sellPrice || hit.unitPrice
  row.unitPrice = row.sellPrice
  row.matchScore = hit.score
  row.matchStatus = 'confirmed'
  row.source = 'catalog'
  onQtyPrice(row)
  pickOpen.value = false
}

async function lockItem(id: string) {
  lockingId.value = id
  err.value = ''
  try {
    const data = await lockQuoteRecord(id)
    lastRecordId.value = data.id
    lastRecordStatus.value = data.status || 'locked'
    hint.value = `已签认锁定「${data.projectName || '未命名项目'}」`
    const idx = records.value.findIndex((r) => r.id === id)
    if (idx >= 0) records.value.splice(idx, 1, { ...records.value[idx], ...data })
    if (previewDetail.value?.id === id) previewDetail.value = { ...previewDetail.value, ...data }
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '签认失败'
  } finally {
    lockingId.value = null
  }
}

function askDelete(item: QuoteRecordItem) {
  pendingDelete.value = item
  confirmDeleteOpen.value = true
}

async function confirmDelete() {
  const item = pendingDelete.value
  if (!item) return
  deletingId.value = item.id
  try {
    await deleteQuoteRecord(item.id)
    confirmDeleteOpen.value = false
    pendingDelete.value = null
    await loadRecords()
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '删除失败'
  } finally {
    deletingId.value = null
  }
}

async function confirmClear() {
  clearingRecords.value = true
  try {
    await clearMineQuoteRecords(purpose.value)
    confirmClearOpen.value = false
    await loadRecords()
  } catch (e) {
    err.value = e instanceof ApiError || e instanceof Error ? e.message : '清空失败'
  } finally {
    clearingRecords.value = false
  }
}
</script>

<template>
  <div class="px-5 lg:px-8 py-6 max-w-6xl mx-auto">
    <PageHeader :title="meta.title" :description="meta.desc">
      <template #actions>
        <button
          type="button"
          class="inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-[12px] transition-colors"
          :class="
            showRecords
              ? 'border-iron bg-iron/10 text-foreground'
              : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground'
          "
          @click="toggleRecords"
        >
          <History class="size-3.5" />
          我的记录
          <span v-if="recordsTotal" class="font-mono text-[11px]">{{ recordsTotal }}</span>
        </button>
      </template>
    </PageHeader>

    <div class="mb-4 flex gap-1 border-b border-border">
      <button
        v-for="t in PURPOSE_TABS"
        :key="t.id"
        type="button"
        role="tab"
        :aria-selected="purpose === t.id"
        :class="[
          'px-4 py-2 -mb-px text-[12px] border-b-2 transition-colors',
          purpose === t.id
            ? 'border-iron text-foreground'
            : 'border-transparent text-muted-foreground hover:text-foreground',
        ]"
        @click="switchPurpose(t.id)"
      >
        {{ t.label }}
      </button>
    </div>

    <p v-if="err" class="mb-3 flex items-start gap-1.5 text-[12px] text-sulfur">
      <TriangleAlert class="size-3.5 shrink-0 mt-0.5" />
      {{ err }}
    </p>
    <p v-else-if="hint" class="mb-3 text-[12px] text-muted-foreground">{{ hint }}</p>

    <section v-if="showRecords" class="rounded-xl border border-border bg-card px-4 py-4">
      <div class="flex flex-wrap items-start justify-between gap-2">
        <div class="min-w-0">
          <h2 class="text-[14px] font-semibold">我的{{ meta.title.replace('AI', '') }}记录</h2>
          <p class="text-[11px] text-muted-foreground mt-0.5">仅显示当前入口类型的记录。</p>
        </div>
        <div class="flex shrink-0 items-center gap-2">
          <button
            type="button"
            class="inline-flex h-8 items-center gap-1 rounded-md border border-border px-2.5 text-[12px] text-sulfur hover:bg-accent disabled:opacity-50"
            :disabled="!recordsTotal || clearingRecords"
            @click="confirmClearOpen = true"
          >
            <Loader2 v-if="clearingRecords" class="size-3.5 animate-spin" />
            <Trash2 v-else class="size-3.5" />
            清空
          </button>
          <button
            type="button"
            class="inline-flex h-8 items-center rounded-md border border-border px-2.5 text-[12px] hover:bg-accent"
            @click="showRecords = false"
          >
            返回
          </button>
        </div>
      </div>
      <div class="mt-3 flex flex-wrap items-center gap-2">
        <input
          v-model="recordsQuery"
          class="h-8 min-w-[220px] rounded-md border border-border bg-background px-2 text-[12px]"
          placeholder="搜索项目 / 价目库"
          @keydown.enter.prevent="() => loadRecords()"
        />
        <button
          type="button"
          class="inline-flex h-8 items-center gap-1 rounded-md border border-border px-2.5 text-[12px] hover:bg-accent disabled:opacity-50"
          :disabled="recordsLoading"
          @click="() => loadRecords()"
        >
          <Loader2 v-if="recordsLoading" class="size-3.5 animate-spin" />
          查询
        </button>
      </div>
      <div
        v-if="recordsLoading && !records.length"
        class="py-8 text-center text-[12px] text-muted-foreground"
      >
        <Loader2 class="inline size-4 animate-spin mr-2" />加载记录…
      </div>
      <p v-else-if="!records.length" class="mt-4 text-[12px] text-muted-foreground text-center py-6">
        暂无记录。
      </p>
      <ul v-else class="mt-3 divide-y divide-border/70">
        <li
          v-for="item in records"
          :key="item.id"
          class="flex flex-col sm:flex-row sm:items-center gap-2 py-3"
        >
          <div class="min-w-0 flex-1">
            <button
              type="button"
              class="text-[13px] font-medium truncate text-left hover:underline disabled:opacity-50"
              :disabled="previewingId === item.id"
              @click="previewRecord(item)"
            >
              {{ item.projectName || '未命名项目' }}
            </button>
            <div class="mt-0.5 text-[11px] text-muted-foreground">
              <span
                class="mr-1 rounded px-1 py-px text-[10px]"
                :class="item.status === 'locked' ? 'bg-iron/15 text-iron' : 'bg-muted text-muted-foreground'"
              >
                {{ item.status === 'locked' ? '已签认' : '草稿' }}
              </span>
              {{ item.lineCount }} 项 · {{ money(item.totalExTax) }} 元
              <span v-if="item.baseName"> · {{ item.baseName }}</span>
              · {{ formatRecordTime(item.createdAt) }}
            </div>
          </div>
          <div class="flex shrink-0 items-center gap-1.5 flex-wrap">
            <button
              type="button"
              class="inline-flex h-8 items-center gap-1 rounded-md border border-border px-2.5 text-[12px] hover:bg-accent disabled:opacity-50"
              :disabled="previewingId === item.id"
              @click="previewRecord(item)"
            >
              <Eye class="size-3.5" />
              预览
            </button>
            <button
              type="button"
              class="inline-flex h-8 items-center rounded-md border border-border px-2.5 text-[12px] hover:bg-accent disabled:opacity-50"
              :disabled="loadingRecordId === item.id"
              @click="loadRecordLines(item)"
            >
              载入明细
            </button>
            <button
              type="button"
              class="inline-flex h-8 items-center rounded-md border border-border px-2.5 text-[12px] hover:bg-accent disabled:opacity-50"
              :disabled="downloadingId === item.id"
              @click="downloadRecord(item)"
            >
              <FileSpreadsheet class="size-3.5" />
              下载
            </button>
            <button
              v-if="item.status !== 'locked'"
              type="button"
              class="inline-flex h-8 items-center rounded-md border border-border px-2.5 text-[12px] hover:bg-accent disabled:opacity-50"
              :disabled="lockingId === item.id"
              @click="lockItem(item.id)"
            >
              <Loader2 v-if="lockingId === item.id" class="size-3.5 animate-spin" />
              签认
            </button>
            <button
              type="button"
              class="inline-flex h-8 items-center rounded-md border border-border px-2.5 text-[12px] text-sulfur hover:bg-accent disabled:opacity-50"
              :disabled="deletingId === item.id || (item.status === 'locked' && !auth.isAdmin)"
              @click="askDelete(item)"
            >
              删除
            </button>
          </div>
        </li>
      </ul>
    </section>

    <div v-else class="space-y-4">
      <Panel title="知识库" subtitle="对价只读所选库中的 Excel。低分匹配须确认或换条，不能当正式价。">
        <div class="flex flex-wrap items-center gap-2">
          <select
            v-model="kbId"
            class="h-8 min-w-[220px] rounded-md border border-border bg-background px-2 text-[12px]"
            :disabled="kbLoading"
          >
            <option value="">{{ kbLoading ? '正在加载…' : '请选择知识库' }}</option>
            <option v-for="b in kbList" :key="b.id" :value="b.id">
              {{ b.name }}（{{ b.docCount }} 份）
            </option>
          </select>
          <button
            type="button"
            class="inline-flex h-8 items-center rounded-md border border-border px-3 text-[12px] hover:bg-accent disabled:opacity-50"
            :disabled="!kbId"
            @click="router.push(`/knowledge/${kbId}`)"
          >
            去维护资料
          </button>
        </div>
      </Panel>

      <Panel title="项目参数" subtitle="地点、工期会写入编制说明；报价入口还可填限价与竞争档位。">
        <div class="grid gap-3 md:grid-cols-2">
          <label class="block text-[12px]">
            <span class="text-muted-foreground">项目名称</span>
            <input
              v-model="projectName"
              class="mt-1 w-full h-8 rounded-md border border-border bg-background px-2 text-[12px]"
              placeholder="可选"
            />
          </label>
          <label class="block text-[12px]">
            <span class="text-muted-foreground">补充说明</span>
            <input
              v-model="note"
              class="mt-1 w-full h-8 rounded-md border border-border bg-background px-2 text-[12px]"
              placeholder="例如：直流 160kW，要雨棚"
            />
          </label>
          <label class="block text-[12px]">
            <span class="text-muted-foreground">地点</span>
            <input
              v-model="location"
              class="mt-1 w-full h-8 rounded-md border border-border bg-background px-2 text-[12px]"
              placeholder="省市 / 场站"
            />
          </label>
          <label class="block text-[12px]">
            <span class="text-muted-foreground">工期（天）</span>
            <input
              v-model.number="durationDays"
              type="number"
              min="0"
              class="mt-1 w-full h-8 rounded-md border border-border bg-background px-2 text-[12px]"
            />
          </label>
          <template v-if="purpose === 'quote'">
            <label class="block text-[12px]">
              <span class="text-muted-foreground">招标限价（不含税）</span>
              <input
                v-model.number="bidCeiling"
                type="number"
                min="0"
                class="mt-1 w-full h-8 rounded-md border border-border bg-background px-2 text-[12px]"
              />
            </label>
            <label class="block text-[12px]">
              <span class="text-muted-foreground">竞争档位</span>
              <select
                v-model="competition"
                class="mt-1 w-full h-8 rounded-md border border-border bg-background px-2 text-[12px]"
              >
                <option value="conservative">保守</option>
                <option value="balanced">均衡</option>
                <option value="aggressive">进取</option>
              </select>
            </label>
            <label class="block text-[12px]">
              <span class="text-muted-foreground">目标毛利率（0~1）</span>
              <input
                v-model.number="targetMargin"
                type="number"
                min="0"
                max="1"
                step="0.01"
                class="mt-1 w-full h-8 rounded-md border border-border bg-background px-2 text-[12px]"
                placeholder="默认跟档位"
              />
            </label>
          </template>
        </div>
      </Panel>

      <Panel title="文档解析" subtitle="规划图读图，或上传工程量 Excel（名称+数量/单价）。">
        <div class="space-y-3">
          <input
            ref="mapInput"
            type="file"
            class="hidden"
            accept="image/*,.pdf"
            multiple
            @change="onPickMaps"
          />
          <input
            ref="boqInput"
            type="file"
            class="hidden"
            accept=".xlsx,.xlsm,.csv"
            @change="onPickBoq"
          />
          <div class="flex flex-wrap items-center gap-2">
            <button
              type="button"
              class="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-[12px] hover:bg-accent"
              @click="mapInput?.click()"
            >
              <Upload class="size-3.5" />
              选择规划图
            </button>
            <button
              type="button"
              class="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 text-[12px] disabled:opacity-50"
              :disabled="recognizing || !files.length || !kbId"
              @click="onRecognize"
            >
              <Loader2 v-if="recognizing" class="size-3.5 animate-spin" />
              {{ recognizing ? '正在读图并对价…' : '识别工程量' }}
            </button>
            <span class="text-[11px] text-muted-foreground">
              {{ files.length ? `已选 ${files.length} 个文件` : '未选择规划图' }}
            </span>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button
              type="button"
              class="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-[12px] hover:bg-accent"
              @click="boqInput?.click()"
            >
              <Upload class="size-3.5" />
              选择工程量 Excel
            </button>
            <button
              type="button"
              class="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-[12px] hover:bg-accent disabled:opacity-50"
              :disabled="parsingBoq || !boqFile || !kbId"
              @click="onParseBoq"
            >
              <Loader2 v-if="parsingBoq" class="size-3.5 animate-spin" />
              解析工程量
            </button>
            <span class="text-[11px] text-muted-foreground">
              {{ boqFile ? boqFile.name : '未选择工程量文件' }}
            </span>
          </div>
          <div v-if="previews.length" class="flex flex-wrap gap-2">
            <button
              v-for="(p, i) in previews"
              :key="p.url"
              type="button"
              class="group relative rounded-md border border-border overflow-hidden bg-muted/30 text-left"
              @click="peek = i"
            >
              <img
                v-if="p.kind === 'image'"
                :src="p.url"
                :alt="p.name"
                class="max-h-44 max-w-[260px] object-contain bg-white block"
              />
              <div
                v-else
                class="h-44 w-40 px-2 flex flex-col items-center justify-center gap-1 text-[12px] text-muted-foreground"
              >
                <span class="rounded border border-border px-1.5 py-0.5 text-[10px]">PDF</span>
                <span class="truncate max-w-[9rem]">{{ p.name }}</span>
              </div>
              <span
                class="absolute right-1 top-1 size-7 rounded-md bg-black/55 text-white inline-flex items-center justify-center opacity-0 group-hover:opacity-100"
              >
                <Maximize2 class="size-3.5" />
              </span>
            </button>
          </div>
        </div>
      </Panel>

      <Panel title="费率" subtitle="造价/预算/报价共用；生成时写入汇总表。">
        <div class="grid gap-2 sm:grid-cols-3 md:grid-cols-4 text-[12px]">
          <label>
            <span class="text-muted-foreground">人工费系数</span>
            <input v-model.number="rates.laborCoef" type="number" min="0" step="0.01" class="mt-1 w-full h-8 rounded-md border border-border px-2 bg-background" />
          </label>
          <label>
            <span class="text-muted-foreground">措施费率</span>
            <input v-model.number="rates.measureRate" type="number" min="0" max="1" step="0.01" class="mt-1 w-full h-8 rounded-md border border-border px-2 bg-background" />
          </label>
          <label>
            <span class="text-muted-foreground">管理费率</span>
            <input v-model.number="rates.manageRate" type="number" min="0" max="1" step="0.01" class="mt-1 w-full h-8 rounded-md border border-border px-2 bg-background" />
          </label>
          <label>
            <span class="text-muted-foreground">利润率</span>
            <input v-model.number="rates.profitRate" type="number" min="0" max="1" step="0.01" class="mt-1 w-full h-8 rounded-md border border-border px-2 bg-background" />
          </label>
          <label>
            <span class="text-muted-foreground">税率</span>
            <input v-model.number="rates.taxRate" type="number" min="0" max="1" step="0.01" class="mt-1 w-full h-8 rounded-md border border-border px-2 bg-background" />
          </label>
          <label>
            <span class="text-muted-foreground">预算系数</span>
            <input v-model.number="rates.budgetCoef" type="number" min="0" step="0.01" class="mt-1 w-full h-8 rounded-md border border-border px-2 bg-background" />
          </label>
          <label>
            <span class="text-muted-foreground">不可预见费率</span>
            <input v-model.number="rates.contingencyRate" type="number" min="0" max="1" step="0.01" class="mt-1 w-full h-8 rounded-md border border-border px-2 bg-background" />
          </label>
          <div class="flex items-end">
            <button
              type="button"
              class="inline-flex h-8 items-center gap-1 rounded-md border border-border px-3 text-[12px] hover:bg-accent disabled:opacity-50"
              :disabled="costing || !lines.length"
              @click="onCost"
            >
              <Loader2 v-if="costing" class="size-3.5 animate-spin" />
              试算造价
            </button>
          </div>
        </div>
        <div v-if="costSummary" class="mt-3 grid gap-1 text-[11px] text-muted-foreground sm:grid-cols-3">
          <span>材料 {{ money(costSummary.materialCost) }}</span>
          <span>造价不含税 {{ money(costSummary.costExTax) }}</span>
          <span>预算不含税 {{ money(costSummary.budgetExTax) }}</span>
          <span>报价不含税 {{ money(costSummary.quoteExTax) }}</span>
        </div>
      </Panel>

      <Panel title="核算报告" subtitle="检查空数量、合价偏差、重名与功率冲突。">
        <div class="flex flex-wrap gap-2 mb-2">
          <button
            type="button"
            class="inline-flex h-8 items-center gap-1 rounded-md border border-border px-3 text-[12px] hover:bg-accent disabled:opacity-50"
            :disabled="verifying || !lines.length"
            @click="onVerify(false)"
          >
            <Loader2 v-if="verifying" class="size-3.5 animate-spin" />
            重新核算
          </button>
          <button
            type="button"
            class="inline-flex h-8 items-center gap-1 rounded-md border border-border px-3 text-[12px] hover:bg-accent disabled:opacity-50"
            :disabled="verifying || !lines.length"
            @click="onVerify(true)"
          >
            应用合价修正
          </button>
        </div>
        <p v-if="!verifyReport" class="text-[12px] text-muted-foreground">识别或解析后会自动核算。</p>
        <ul v-else-if="verifyReport.issues.length" class="space-y-1">
          <li
            v-for="(it, i) in verifyReport.issues"
            :key="i"
            class="text-[11px] flex gap-1"
            :class="it.level === 'error' ? 'text-sulfur' : 'text-muted-foreground'"
          >
            <TriangleAlert class="size-3.5 shrink-0 mt-0.5" />
            {{ it.message }}
          </li>
        </ul>
        <p v-else class="text-[12px] text-muted-foreground">暂无异常。</p>
      </Panel>

      <Panel title="明细" subtitle="低分估价须确认或换条；未核价默认不能导出正式稿。">
        <template #action>
          <button
            type="button"
            class="inline-flex h-7 items-center gap-1 rounded-md border border-border px-2 text-[11px] hover:bg-accent"
            @click="addLine"
          >
            <Plus class="size-3" />
            增行
          </button>
        </template>
        <div v-if="warnings.length" class="mb-3 space-y-1">
          <p v-for="(w, i) in warnings" :key="i" class="text-[11px] text-sulfur flex gap-1">
            <TriangleAlert class="size-3.5 shrink-0 mt-0.5" />
            {{ w }}
          </p>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-[12px] border-collapse">
            <thead>
              <tr class="text-left text-muted-foreground border-b border-border">
                <th class="py-1.5 pr-2 font-medium w-8">#</th>
                <th class="py-1.5 pr-2 font-medium min-w-[120px]">名称</th>
                <th class="py-1.5 pr-2 font-medium min-w-[120px]">规格</th>
                <th class="py-1.5 pr-2 font-medium w-12">单位</th>
                <th class="py-1.5 pr-2 font-medium w-14">数量</th>
                <th v-if="seeCost" class="py-1.5 pr-2 font-medium w-24">成本价</th>
                <th class="py-1.5 pr-2 font-medium w-24">售价</th>
                <th class="py-1.5 pr-2 font-medium w-20">合价</th>
                <th class="py-1.5 pr-2 font-medium w-14">来源</th>
                <th class="py-1.5 pr-2 font-medium w-16">对价</th>
                <th class="py-1.5 w-8" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in lines" :key="i" class="border-b border-border/70">
                <td class="py-1 pr-2 text-muted-foreground">{{ row.seq || i + 1 }}</td>
                <td class="py-1 pr-2">
                  <input v-model="row.name" class="w-full h-7 rounded border border-border px-1.5 bg-background" />
                </td>
                <td class="py-1 pr-2 align-top">
                  <textarea
                    v-model="row.spec"
                    rows="2"
                    class="w-full min-h-[3rem] rounded border border-border px-1.5 py-1 bg-background text-[11px]"
                  />
                </td>
                <td class="py-1 pr-2">
                  <input v-model="row.unit" class="w-full h-7 rounded border border-border px-1.5 bg-background" />
                </td>
                <td class="py-1 pr-2">
                  <input
                    v-model.number="row.qty"
                    type="number"
                    min="0"
                    class="w-full h-7 rounded border border-border px-1.5 bg-background"
                    @input="onQtyPrice(row)"
                  />
                </td>
                <td v-if="seeCost" class="py-1 pr-2">
                  <input
                    v-model.number="row.costPrice"
                    type="number"
                    min="0"
                    class="w-full h-7 rounded border border-border px-1.5 bg-background"
                    @input="onQtyPrice(row)"
                  />
                </td>
                <td class="py-1 pr-2">
                  <input
                    v-model.number="row.sellPrice"
                    type="number"
                    min="0"
                    class="w-full h-7 rounded border border-border px-1.5 bg-background"
                    @input="row.unitPrice = row.sellPrice; onQtyPrice(row)"
                  />
                </td>
                <td class="py-1 pr-2 tabular-nums">
                  {{ lineAmount(row).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }}
                </td>
                <td class="py-1 pr-2">
                  <Tag :tone="row.source === 'catalog' ? 'patina' : row.matchStatus === 'estimate' ? 'sulfur' : 'default'">
                    {{ SOURCE[row.source] || row.source || '人工' }}
                  </Tag>
                </td>
                <td class="py-1 pr-2">
                  <div class="flex flex-col gap-0.5">
                    <span
                      class="text-[10px]"
                      :class="linePending(row) ? 'text-sulfur' : 'text-muted-foreground'"
                    >
                      {{
                        row.matchStatus === 'confirmed'
                          ? '已确认'
                          : row.matchStatus === 'official'
                            ? '正式'
                            : row.matchStatus === 'estimate'
                              ? '待确认'
                              : Number(row.sellPrice || row.unitPrice) > 0
                                ? '已填价'
                                : '待核价'
                      }}
                    </span>
                    <button
                      v-if="row.matchStatus === 'estimate'"
                      type="button"
                      class="text-[10px] text-iron hover:underline text-left"
                      @click="confirmLine(row)"
                    >
                      确认
                    </button>
                    <button
                      v-if="row.name"
                      type="button"
                      class="text-[10px] text-muted-foreground hover:underline text-left"
                      @click="openPick(row, i)"
                    >
                      换条
                    </button>
                  </div>
                </td>
                <td class="py-1">
                  <button type="button" class="text-muted-foreground hover:text-sulfur" @click="removeLine(i)">
                    <Trash2 class="size-3.5" />
                  </button>
                </td>
              </tr>
              <tr v-if="!lines.length">
                <td :colspan="seeCost ? 11 : 10" class="py-8 text-center text-muted-foreground">
                  识别或解析后会出现明细，也可增行手工填写。
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <footer class="mt-3 flex flex-wrap items-center justify-between gap-2">
          <p class="text-[12px] text-muted-foreground">
            {{ lines.length }} 项
            <span v-if="pendingCount"> · {{ pendingCount }} 项待核价/待确认</span>
            · 明细合计
            <span class="text-foreground font-medium">{{ money(total) }}</span>
            元
            <span v-if="selectedScheme"> · 采用 {{ selectedScheme }}</span>
          </p>
          <div class="flex flex-wrap items-center gap-2">
            <label v-if="purpose === 'quote'" class="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
              <input v-model="forceExport" type="checkbox" />
              强制导出草稿
            </label>
            <button
              v-if="lastRecordId && lastRecordStatus !== 'locked'"
              type="button"
              class="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-[12px] hover:bg-accent disabled:opacity-50"
              :disabled="!!lockingId"
              @click="lockItem(lastRecordId)"
            >
              <Loader2 v-if="lockingId === lastRecordId" class="size-3.5 animate-spin" />
              签认锁定
            </button>
            <button
              type="button"
              class="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 text-[12px] disabled:opacity-50"
              :disabled="generating || !lines.length"
              @click="onGenerate"
            >
              <Loader2 v-if="generating" class="size-3.5 animate-spin" />
              <FileSpreadsheet v-else class="size-3.5" />
              {{ meta.exportLabel }}
            </button>
          </div>
        </footer>
      </Panel>

      <Panel v-if="purpose === 'quote' && (schemes.length || instruction)" title="报价方案" subtitle="试算或生成后出现；点选一档，导出会按该档总价回写明细。">
        <ul v-if="schemes.length" class="space-y-2 mb-3">
          <li
            v-for="(s, i) in schemes"
            :key="i"
            class="rounded-md border px-3 py-2 text-[12px] cursor-pointer"
            :class="
              (selectedScheme ? selectedScheme === s.name : s.applied || s.recommended)
                ? 'border-iron bg-iron/5'
                : 'border-border'
            "
            @click="selectedScheme = s.name"
          >
            <div class="font-medium">
              {{ s.name }}
              <span v-if="s.recommended" class="ml-1 text-[10px] text-iron">推荐</span>
              <span v-if="selectedScheme === s.name || s.applied" class="ml-1 text-[10px] text-iron">采用</span>
            </div>
            <div class="text-muted-foreground mt-0.5">
              不含税 {{ money(s.totalExTax) }} · 毛利 {{ (s.margin * 100).toFixed(1) }}%
              · {{ s.tip }}
            </div>
            <div v-if="s.risks?.length" class="text-sulfur mt-0.5 text-[11px]">
              {{ s.risks.join('；') }}
            </div>
          </li>
        </ul>
        <pre v-if="instruction" class="whitespace-pre-wrap text-[11px] text-muted-foreground leading-relaxed">{{ instruction }}</pre>
      </Panel>
    </div>
  </div>

  <AppDialog
    :open="previewOpen"
    size="xl"
    :title="previewDetail ? (previewDetail.projectName || '未命名项目') : '预览'"
    description="只读查看生成时的明细。"
    @update:open="(open) => { previewOpen = open }"
  >
    <div v-if="previewDetail" class="flex min-h-0 flex-1 flex-col gap-3">
      <div class="grid shrink-0 grid-cols-2 gap-x-4 gap-y-1.5 text-[12px] md:grid-cols-4">
        <div class="truncate">
          <span class="text-muted-foreground">价目库：</span>
          {{ previewDetail.baseName || '—' }}
        </div>
        <div>
          <span class="text-muted-foreground">不含税：</span>
          {{ money(previewDetail.totalExTax) }} 元
        </div>
        <div>
          <span class="text-muted-foreground">含税：</span>
          {{ money(previewDetail.totalIncTax) }} 元
        </div>
        <div>
          <span class="text-muted-foreground">时间：</span>
          {{ formatRecordTime(previewDetail.createdAt) }}
        </div>
      </div>
      <div class="min-h-0 flex-1 overflow-auto rounded-md border border-border">
        <table class="w-full text-[12px] border-collapse">
          <thead class="sticky top-0 bg-card">
            <tr class="text-left text-muted-foreground border-b border-border">
              <th class="py-1.5 px-2 font-medium w-8">#</th>
              <th class="py-1.5 px-2 font-medium">名称</th>
              <th class="py-1.5 px-2 font-medium">规格</th>
              <th class="py-1.5 px-2 font-medium w-12">单位</th>
              <th class="py-1.5 px-2 font-medium w-16 text-right">数量</th>
              <th class="py-1.5 px-2 font-medium w-24 text-right">售价</th>
              <th class="py-1.5 px-2 font-medium w-24 text-right">合价</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(row, i) in previewDetail.lines || []"
              :key="i"
              class="border-b border-border/70"
            >
              <td class="py-1.5 px-2 text-muted-foreground">{{ row.seq || i + 1 }}</td>
              <td class="py-1.5 px-2">{{ row.name }}</td>
              <td class="py-1.5 px-2 text-muted-foreground whitespace-pre-wrap">{{ row.spec }}</td>
              <td class="py-1.5 px-2">{{ row.unit || '项' }}</td>
              <td class="py-1.5 px-2 text-right tabular-nums">{{ row.qty }}</td>
              <td class="py-1.5 px-2 text-right tabular-nums">
                {{ Number(row.unitPrice || row.sellPrice) > 0 ? money(row.unitPrice || row.sellPrice) : '—' }}
              </td>
              <td class="py-1.5 px-2 text-right tabular-nums">
                {{ money(lineAmount(row)) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <template #footer>
      <button
        type="button"
        class="inline-flex h-8 items-center rounded-md border border-border px-3 text-[12px] hover:bg-accent"
        @click="previewOpen = false"
      >
        关闭
      </button>
      <button
        type="button"
        class="inline-flex h-8 items-center rounded-md bg-primary text-primary-foreground px-3 text-[12px]"
        :disabled="!previewDetail"
        @click="loadFromPreview"
      >
        载入明细
      </button>
    </template>
  </AppDialog>

  <AppDialog
    :open="pickOpen"
    title="换价目"
    description="从所选知识库挑一条替换当前行，确认后作为正式价。"
    @update:open="(open) => { pickOpen = open }"
  >
    <div v-if="pickLoading" class="py-8 text-center text-[12px] text-muted-foreground">
      <Loader2 class="inline size-4 animate-spin mr-2" />加载候选…
    </div>
    <p v-else-if="!pickHits.length" class="py-6 text-center text-[12px] text-muted-foreground">
      没有足够相似的价目。
    </p>
    <ul v-else class="max-h-[50vh] divide-y divide-border overflow-y-auto rounded-md border border-border">
      <li v-for="(hit, i) in pickHits" :key="i">
        <button
          type="button"
          class="w-full text-left px-3 py-2.5 hover:bg-accent/40"
          @click="applyHit(hit)"
        >
          <div class="text-[13px] font-medium">{{ hit.name }}</div>
          <div class="mt-0.5 text-[11px] text-muted-foreground">
            {{ money(hit.sellPrice || hit.unitPrice) }} 元
            · 匹配 {{ Math.round(hit.score * 100) }}%
            <span v-if="hit.official"> · 可作正式价</span>
            <span v-else> · 低分</span>
          </div>
          <div v-if="hit.spec" class="mt-0.5 text-[11px] text-muted-foreground whitespace-pre-wrap line-clamp-3">
            {{ hit.spec }}
          </div>
        </button>
      </li>
    </ul>
  </AppDialog>

  <AppAlertDialog
    :open="confirmDeleteOpen"
    title="删除记录"
    :description="
      pendingDelete
        ? `确定删除「${pendingDelete.projectName || '未命名项目'}」？`
        : '确定删除该记录？'
    "
    confirm-label="确认删除"
    destructive
    :loading="!!deletingId"
    @update:open="(open) => { confirmDeleteOpen = open; if (!open && !deletingId) pendingDelete = null }"
    @confirm="confirmDelete"
  />
  <AppAlertDialog
    :open="confirmClearOpen"
    title="清空当前入口记录"
    description="将删除当前类型下你名下的草稿记录。已签认锁定的记录会保留。"
    confirm-label="确认清空"
    destructive
    :loading="clearingRecords"
    @update:open="(open) => { confirmClearOpen = open }"
    @confirm="confirmClear"
  />

  <Teleport to="body">
    <div
      v-if="peekItem"
      class="fixed inset-0 z-[70] bg-bg-base/85 backdrop-blur-sm flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      @click.self="peek = null"
    >
      <div class="bg-bg-elevated border border-hairline rounded-lg shadow-2xl w-full max-w-6xl flex flex-col max-h-[92vh]">
        <div class="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-hairline">
          <div class="min-w-0 text-[13px] font-medium truncate">{{ peekItem.name }}</div>
          <button type="button" class="size-8 rounded-md hover:bg-hairline/60 inline-flex items-center justify-center" @click="peek = null">
            <X class="size-4" />
          </button>
        </div>
        <div class="relative flex-1 min-h-0 flex items-center justify-center bg-bg-base/40 p-3 overflow-auto">
          <img
            v-if="peekItem.kind === 'image'"
            :src="peekItem.url"
            :alt="peekItem.name"
            class="max-w-full max-h-[78vh] object-contain rounded-md border border-hairline bg-white"
          />
          <iframe
            v-else
            :src="peekItem.url"
            class="w-full h-[78vh] rounded-md border border-hairline bg-white"
            title="PDF 预览"
          />
        </div>
      </div>
    </div>
  </Teleport>
</template>

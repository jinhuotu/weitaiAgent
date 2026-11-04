<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, type FunctionalComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Upload,
  Link as LinkIcon,
  FileText,
  FileImage,
  FileSpreadsheet,
  Presentation,
  File as FileIcon,
  Loader2,
  Search,
  CircleCheck as Ok,
  XCircle,
  Tag,
  X,
  Eye,
  ArrowLeft,
  Trash2,
  TriangleAlert,
  Download,
  RotateCcw,
  Link2,
  Paperclip,
  Shield,
  Film,
} from 'lucide-vue-next'
import { ApiError } from '@/lib/api'
import {
  cancelKnowledgeIngestTask,
  checkKnowledgeDocumentDuplicate,
  createTextDocument,
  createUrlDocument,
  deleteKnowledgeDocument,
  attachKnowledgeDocument,
  downloadKnowledgeDocument,
  getKnowledgeBase,
  getKnowledgeDocumentPreview,
  listKnowledgeDocuments,
  listKnowledgeIngestTasks,
  reparseKnowledgeDocument,
  searchKnowledge,
  uploadKnowledgeDocument,
  type KbDocItem,
  type KbIngestTask,
  type KnowledgeBaseItem,
} from '@/lib/knowledge-api'
import { fileExt, fmtSize, KB_UPLOAD_MAX_BYTES, validateKbUploadFile } from '@/lib/read-file-smart'
import {
  isKbVideoDoc,
  isKbVideoRef,
  fmtKbVideoRange,
  kbVideoFilePath,
  KB_VIDEO_SUMMARY_ASR,
  KB_VIDEO_SUMMARY_EMBED,
  KB_VIDEO_UPLOAD_MAX_BYTES_DEFAULT,
} from '@/lib/kb-video-contract'
import { fmtAgo } from '@/lib/time'
import { useAuthStore } from '@/stores/auth'
import KbAclDialog from '@/components/knowledge/KbAclDialog.vue'

type KbItem = KbDocItem
type Tab = 'file' | 'url' | 'text'

type IconComp = FunctionalComponent

const FILE_TYPE_GROUPS: {
  label: string
  exts: string[]
  color: string
  icon: IconComp
}[] = [
  { label: 'PDF', exts: ['.pdf'], color: 'text-iron', icon: FileText },
  { label: 'Word', exts: ['.docx'], color: 'text-molybdenum', icon: FileText },
  {
    label: 'PPT',
    exts: ['.pptx'],
    color: 'text-iron',
    icon: Presentation,
  },
  {
    label: 'Excel',
    exts: ['.xlsx', '.xls', '.csv'],
    color: 'text-patina',
    icon: FileSpreadsheet,
  },
  {
    label: '文本',
    exts: ['.txt', '.md', '.json', '.xml', '.yaml', '.yml'],
    color: 'text-text-secondary',
    icon: FileText,
  },
  {
    label: '图片',
    exts: ['.jpg', '.png', '.webp'],
    color: 'text-sulfur',
    icon: FileImage,
  },
  {
    label: '视频',
    exts: ['.mp4', '.webm'],
    color: 'text-iron',
    icon: Film,
  },
]

const ACCEPT_LIST = FILE_TYPE_GROUPS.flatMap((g) => g.exts).join(',')
const DRAWING_EXTS = ['pdf', 'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']
const DRAWING_ACCEPT = DRAWING_EXTS.map((e) => `.${e}`).join(',')
const DRAWING_MAX_COUNT = 2

function iconForType(type?: string): IconComp {
  const t = (type || '').toLowerCase()
  if (['pdf'].includes(t)) return FileText
  if (['doc', 'docx'].includes(t)) return FileText
  if (['ppt', 'pptx'].includes(t)) return Presentation
  if (['xls', 'xlsx', 'csv', 'json', 'xml', 'yaml', 'yml'].includes(t)) {
    return ['xls', 'xlsx', 'csv'].includes(t) ? FileSpreadsheet : FileText
  }
  if (['png', 'jpg', 'jpeg', 'bmp', 'gif', 'webp'].includes(t)) return FileImage
  if (['mp4', 'webm'].includes(t)) return Film
  return FileIcon
}

function colorForType(type?: string) {
  const t = (type || '').toLowerCase()
  if (['pdf'].includes(t)) return 'text-iron'
  if (['doc', 'docx'].includes(t)) return 'text-molybdenum'
  if (['ppt', 'pptx'].includes(t)) return 'text-iron'
  if (['xls', 'xlsx', 'csv'].includes(t)) return 'text-patina'
  if (['json', 'xml', 'yaml', 'yml', 'txt', 'md'].includes(t)) return 'text-text-secondary'
  if (['png', 'jpg', 'jpeg', 'bmp', 'gif', 'webp'].includes(t)) return 'text-sulfur'
  if (['mp4', 'webm'].includes(t)) return 'text-iron'
  return 'text-text-secondary'
}

function fmtIngestToast(docName: string, item: KbDocItem) {
  if (item.kind === 'drawing') {
    const linked = item.parentId ? '' : '；未挂靠案例，检索时不会附图'
    return `${docName} 已保存为图纸附件（不 OCR、不进向量）${linked}`
  }
  if (isKbVideoDoc(item)) {
    if (item.status === 'parsing') {
      return `${docName} 已提交，排队语音转写中`
    }
    if (item.status === 'failed') {
      return `${docName} 语音转写失败`
    }
    const chunks = item.chunks ?? 0
    if (chunks > 0) {
      return `${docName} 转写完成：${(item.charCount ?? 0).toLocaleString()} 字符 · 切成 ${chunks} 块，可检索`
    }
    return `${docName} 已保存视频原片（待转写）`
  }
  if (item.duplicate) {
    return `${docName} 已在库中（${item.name}），未重复入库`
  }
  if (item.status === 'parsing') {
    return `${docName} 已提交，排队解析中（文字页本地抽取，扫描页/图片走 OCR）`
  }
  if (item.status === 'failed') {
    return `${docName} 解析失败`
  }
  const chunks = item.chunks ?? 0
  const chars = item.charCount ?? 0
  const ocr = item.ocrPages ? ` · OCR ${item.ocrPages} 次` : ''
  const cap = item.ocrCapped ? ' · 已达 OCR 页数上限' : ''
  const formula = item.formulaFallback ? ' · 含公式原文' : ''
  if (chunks === 0) {
    if (isSkipRag(item)) {
      return `${docName} 已保存，判定为投标书/他司材料，未写入检索`
    }
    return `${docName} 已解析，正在写入检索${ocr}${cap}${formula}`
  }
  return `${docName} 已入库：${chars.toLocaleString()} 字符 · 切成 ${chunks} 块${ocr}${cap}${formula}`
}

function statusLabel(it: KbItem) {
  if (it.status === 'parsing') {
    if (isKbVideoDoc(it)) {
      const s = (it.summary || '').trim()
      if (s === KB_VIDEO_SUMMARY_EMBED || s.includes('写入检索')) return '向量化中'
      if (s === KB_VIDEO_SUMMARY_ASR || s.includes('语音转写')) return '转写中'
      return '转写中'
    }
    return '解析中'
  }
  if (it.status === 'failed') return '失败'
  if (it.kind === 'drawing') return '附件'
  if (isKbVideoDoc(it)) {
    if ((it.chunks ?? 0) > 0) return '已就绪'
    return '待转写'
  }
  if (it.reviewStatus === 'rejected') return '已驳回'
  if (isSkipRag(it)) return '不检索'
  if ((it.chunks ?? 0) === 0) return '入库中'
  return '已就绪'
}

function ingestTaskLabel(t: KbIngestTask) {
  if (t.status === 'queued') return '排队中'
  if (t.status === 'running') return '解析中'
  if (t.status === 'succeeded') return '已完成'
  if (t.status === 'failed') return '失败'
  if (t.status === 'cancelled') return '已取消'
  return t.status
}

function fmtPageOcr(it: KbItem) {
  const parts: string[] = []
  if (it.pageCount) {
    parts.push(it.fileType === 'xlsx' ? `${it.pageCount} 表` : `${it.pageCount} 页`)
  }
  if (it.ocrPages) parts.push(`OCR ${it.ocrPages}`)
  if (it.ocrCapped) parts.push('已达页数上限')
  if (it.formulaFallback) parts.push('含公式原文')
  return parts.join(' · ')
}

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const baseId = computed(() => String(route.params.baseId || ''))
const uploaderName = computed(
  () => auth.user?.display_name || auth.user?.username || '当前用户',
)

const base = ref<KnowledgeBaseItem | null>(null)
const tab = ref<Tab>('file')
const items = ref<KbItem[]>([])
const loading = ref(true)
const keyword = ref('')
const ingestTasks = ref<KbIngestTask[]>([])
const ingestDismissAfter = ref<Record<string, number>>({})
const cancellingTaskId = ref<string | null>(null)
const dragOver = ref(false)
const uploading = ref<string[]>([])
const uploadJobs = ref<
  { key: string; name: string; percent: number; phase: 'upload' | 'parsing'; docId?: string }[]
>([])
const toast = ref<{ type: 'ok' | 'err'; msg: string } | null>(null)
const fileRef = ref<HTMLInputElement | null>(null)
const textAttachRef = ref<HTMLInputElement | null>(null)
const textAttachFiles = ref<File[]>([])
const attachingId = ref<string | null>(null)
const attachDraft = ref<Record<string, string>>({})

function isDrawingLike(it: KbItem) {
  if (it.kind === 'drawing') return true
  const tags = it.tags || []
  return tags.some((t) => ['图纸附件', '图纸', '布置图', 'drawing'].includes(String(t)))
}

function isSkipRag(it: { tags?: string[] | null; kind?: string }) {
  if (it.kind === 'drawing') return false
  return (it.tags || []).some((t) => String(t) === 'bid_foreign')
}

function formatKbTag(tag: string) {
  if (tag === 'bid_foreign') return '投标书·不检索'
  return tag
}

const caseDocs = computed(() =>
  items.value.filter((it) => !isDrawingLike(it) && it.status === 'ready'),
)

const urlForm = ref({ url: '', title: '', tags: '' })
const textForm = ref({ title: '', content: '', tags: '' })
const probe = ref('')
const probeRes = ref<
  {
    content: string
    score: number
    name?: string
    preview_kind?: string
    file_type?: string
    kind?: string
    startMs?: number
    endMs?: number
  }[] | null
>(null)
const probeLoading = ref(false)
const textPreview = ref<{
  item: KbItem
  content: string
  chunks: Array<{
    chunkIndex: number
    content: string
    startMs?: number
    endMs?: number
  }>
  truncated: boolean
  playUrl?: string
} | null>(null)
const previewLoadingId = ref<string | null>(null)
const deletingId = ref<string | null>(null)
const downloadingId = ref<string | null>(null)
const retryingId = ref<string | null>(null)
const pendingDeleteDoc = ref<KbItem | null>(null)
const pendingDuplicate = ref<{
  kind: 'file' | 'url' | 'text'
  label: string
  file?: File
  duplicates: KbDocItem[]
} | null>(null)
const aclOpen = ref(false)
let toastTimer: ReturnType<typeof setTimeout> | null = null
let parsePollTimer: ReturnType<typeof setInterval> | null = null

const canManage = computed(() => Boolean(base.value?.canManage))
const canUse = computed(() => Boolean(base.value?.canUse))
const canViewOnly = computed(() => Boolean(base.value) && !canUse.value && !canManage.value)

const parsingCount = computed(
  () =>
    items.value.filter(
      (it) =>
        it.status === 'parsing' ||
        (it.kind !== 'drawing' &&
          !isKbVideoDoc(it) &&
          it.status === 'ready' &&
          it.reviewStatus !== 'rejected' &&
          !isSkipRag(it) &&
          (it.chunks ?? 0) === 0),
    ).length + uploadJobs.value.filter((j) => j.phase === 'parsing').length,
)

const visibleIngestTasks = computed(() => {
  const now = Date.now()
  return ingestTasks.value.filter((t) => {
    if (t.status === 'queued' || t.status === 'running') return true
    if (t.status === 'succeeded') return false
    const until = ingestDismissAfter.value[t.id]
    return Boolean(until && until > now)
  })
})

const shouldPoll = computed(
  () => parsingCount.value > 0 || visibleIngestTasks.value.length > 0,
)

const filtered = computed(() => {
  if (!keyword.value.trim()) return items.value
  const k = keyword.value.trim().toLowerCase()
  return items.value.filter(
    (it) =>
      it.name.toLowerCase().includes(k) ||
      it.summary?.toLowerCase().includes(k) ||
      it.tags?.some((t) => t.toLowerCase().includes(k)),
  )
})

type DisplayRow = {
  item: KbItem
  depth: 0 | 1
  parentName?: string
  childCount: number
  unlinkedDrawing: boolean
}

const displayRows = computed((): DisplayRow[] => {
  const list = filtered.value
  const all = items.value
  const idSet = new Set(all.map((d) => d.id))
  const nameById = new Map(all.map((d) => [d.id, d.name]))
  const childrenOf = new Map<string, KbItem[]>()
  const childIds = new Set<string>()
  for (const d of all) {
    if (!d.parentId || !idSet.has(d.parentId)) continue
    const arr = childrenOf.get(d.parentId) || []
    arr.push(d)
    childrenOf.set(d.parentId, arr)
    childIds.add(d.id)
  }
  const rows: DisplayRow[] = []
  const shown = new Set<string>()
  const emit = (item: KbItem, depth: 0 | 1, parentName?: string) => {
    rows.push({
      item,
      depth,
      parentName,
      childCount: (childrenOf.get(item.id) || []).length,
      unlinkedDrawing: isDrawingLike(item) && !item.parentId,
    })
  }
  for (const it of list) {
    if (childIds.has(it.id)) continue
    emit(it, 0)
    shown.add(it.id)
    for (const ch of childrenOf.get(it.id) || []) {
      emit(ch, 1, it.name)
      shown.add(ch.id)
    }
  }
  for (const it of list) {
    if (shown.has(it.id)) continue
    emit(it, it.parentId ? 1 : 0, it.parentId ? nameById.get(it.parentId) : undefined)
    shown.add(it.id)
  }
  return rows
})

const totalChunks = computed(() => items.value.reduce((s, it) => s + (it.chunks || 0), 0))
const totalCharsK = computed(
  () => (items.value.reduce((s, it) => s + (it.charCount || 0), 0) / 1000).toFixed(1),
)

const tabs: { k: Tab; label: string; icon: IconComp }[] = [
  { k: 'file', label: '文件上传', icon: Upload },
  { k: 'url', label: 'URL 抓取', icon: LinkIcon },
  { k: 'text', label: '文本粘贴', icon: FileText },
]

async function fetchIngestTasks() {
  if (!baseId.value) return
  try {
    const list = await listKnowledgeIngestTasks(baseId.value)
    const now = Date.now()
    const next = { ...ingestDismissAfter.value }
    const seen = new Set(list.map((t) => t.id))
    for (const t of list) {
      if (t.status === 'succeeded') {
        delete next[t.id]
      } else if (t.status === 'failed' || t.status === 'cancelled') {
        if (next[t.id] == null) next[t.id] = now + 30_000
      } else {
        delete next[t.id]
      }
    }
    for (const id of Object.keys(next)) {
      if (!seen.has(id) && (next[id] || 0) < now) delete next[id]
    }
    ingestDismissAfter.value = next
    ingestTasks.value = list
  } catch {
    /* 无权或接口不可用时不打断资料列表 */
  }
}

async function fetchList() {
  if (!baseId.value) return
  try {
    const [baseInfo, docs] = await Promise.all([
      getKnowledgeBase(baseId.value),
      listKnowledgeDocuments(baseId.value),
    ])
    await fetchIngestTasks()
    base.value = baseInfo
    items.value = docs
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 403)) {
      toast.value = { type: 'err', msg: '没有该知识库的访问权限' }
      await router.replace('/knowledge')
      return
    }
    toast.value = {
      type: 'err',
      msg: e instanceof ApiError || e instanceof Error ? e.message : '加载失败',
    }
  } finally {
    loading.value = false
  }
}

watch(toast, (v) => {
  if (toastTimer) clearTimeout(toastTimer)
  if (!v) return
  toastTimer = setTimeout(() => {
    toast.value = null
  }, 3200)
})

watch(baseId, () => {
  loading.value = true
  void fetchList()
})

watch(shouldPoll, (n) => {
  if (n && !parsePollTimer) {
    parsePollTimer = setInterval(() => {
      void fetchList()
    }, 2500)
  }
  if (!n && parsePollTimer) {
    clearInterval(parsePollTimer)
    parsePollTimer = null
  }
})

watch(
  items,
  (docs) => {
    const remain: typeof uploadJobs.value = []
    for (const job of uploadJobs.value) {
      if (job.phase !== 'parsing' || !job.docId) {
        remain.push(job)
        continue
      }
      const it = docs.find((d) => d.id === job.docId)
      if (!it || it.status === 'parsing') {
        remain.push(job)
        continue
      }
      toast.value = { type: it.status === 'failed' ? 'err' : 'ok', msg: fmtIngestToast(job.name, it) }
    }
    uploadJobs.value = remain
  },
  { deep: false },
)

onMounted(() => {
  void fetchList()
})

onUnmounted(() => {
  if (toastTimer) clearTimeout(toastTimer)
  if (parsePollTimer) clearInterval(parsePollTimer)
})

async function uploadSingleFile(file: File, force = false) {
  if (!baseId.value) return
  const jobKey = `${Date.now()}-${file.name}-${Math.random().toString(36).slice(2, 6)}`
  uploadJobs.value = [
    ...uploadJobs.value,
    { key: jobKey, name: file.name, percent: 0, phase: 'upload' },
  ]
  const patchJob = (patch: Partial<(typeof uploadJobs.value)[number]>) => {
    uploadJobs.value = uploadJobs.value.map((j) =>
      j.key === jobKey ? { ...j, ...patch } : j,
    )
  }
  try {
    const invalid = validateKbUploadFile(file)
    if (invalid) throw new Error(invalid)
    const result = await uploadKnowledgeDocument(
      {
        baseId: baseId.value,
        file,
        name: file.name,
        tags: ['手动上传'],
        asAttachment: false,
        force,
      },
      (p) => patchJob({ percent: p.percent, phase: 'upload' }),
    )
    items.value = result.items
    uploadJobs.value = uploadJobs.value.filter((j) => j.key !== jobKey)
    const replaced = (result.replaced?.length || 0) > 0
    toast.value = {
      type: result.item?.status === 'failed' ? 'err' : 'ok',
      msg: fmtIngestToast(file.name, result.item) + (replaced ? '（已覆盖旧版）' : ''),
    }
    void fetchIngestTasks()
  } catch (e) {
    uploadJobs.value = uploadJobs.value.filter((j) => j.key !== jobKey)
    const raw = e instanceof Error ? e.message : '未知错误'
    const hint = /embedding/i.test(raw)
      ? '（正文可能已解析，失败在向量化；请确认 Embedding 模型可用）'
      : ''
    toast.value = {
      type: 'err',
      msg: `${file.name} 处理失败：${raw}${hint}`,
    }
  }
}

async function handleFiles(files: FileList | File[], opts?: { force?: boolean }) {
  const arr = Array.from(files)
  if (arr.length === 0 || !baseId.value) return
  for (const file of arr) {
    if (!opts?.force) {
      try {
        const dup = await checkKnowledgeDocumentDuplicate({
          baseId: baseId.value,
          name: file.name,
        })
        if (dup.exists) {
          pendingDuplicate.value = {
            kind: 'file',
            label: file.name,
            file,
            duplicates: dup.duplicates,
          }
          return
        }
      } catch (e) {
        toast.value = {
          type: 'err',
          msg: e instanceof Error ? e.message : '重复检查失败',
        }
        return
      }
    }
    await uploadSingleFile(file, Boolean(opts?.force))
  }
}

async function confirmDuplicateUpload() {
  const pending = pendingDuplicate.value
  if (!pending) return
  pendingDuplicate.value = null
  if (pending.kind === 'file' && pending.file) {
    await handleFiles([pending.file], { force: true })
  } else if (pending.kind === 'url') {
    await submitUrl(true)
  } else if (pending.kind === 'text') {
    await submitText(true)
  }
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) void handleFiles(input.files)
  input.value = ''
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  dragOver.value = false
  if (e.dataTransfer?.files) void handleFiles(e.dataTransfer.files)
}

async function submitUrl(force = false) {
  if (!urlForm.value.url.trim()) {
    toast.value = { type: 'err', msg: '请填写资料 URL' }
    return
  }
  if (!baseId.value) return
  const title = urlForm.value.title.trim() || urlForm.value.url.trim()
  const urlKey = urlForm.value.url
  if (!force) {
    try {
      const dup = await checkKnowledgeDocumentDuplicate({
        baseId: baseId.value,
        name: title,
        url: urlKey.trim(),
      })
      if (dup.exists) {
        pendingDuplicate.value = {
          kind: 'url',
          label: title,
          duplicates: dup.duplicates,
        }
        return
      }
    } catch (e) {
      toast.value = {
        type: 'err',
        msg: e instanceof Error ? e.message : '重复检查失败',
      }
      return
    }
  }
  uploading.value = [...uploading.value, urlKey]
  try {
    const result = await createUrlDocument({
      baseId: baseId.value,
      url: urlForm.value.url.trim(),
      title,
      uploader: uploaderName.value,
      tags: urlForm.value.tags
        ? urlForm.value.tags.split(/[,，\s]+/).filter(Boolean)
        : ['URL'],
      force,
    })
    items.value = result.items
    urlForm.value = { url: '', title: '', tags: '' }
    const replaced = (result.replaced?.length || 0) > 0
    toast.value = {
      type: 'ok',
      msg: fmtIngestToast(result.item.name, result.item) + (replaced ? '（已覆盖旧版）' : ''),
    }
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: `入库失败：${e instanceof Error ? e.message : '未知错误'}`,
    }
  } finally {
    uploading.value = uploading.value.filter((n) => n !== urlKey)
  }
}

function addTextAttachFiles(files: FileList | File[]) {
  const next = [...textAttachFiles.value]
  for (const file of Array.from(files)) {
    const invalid = validateKbUploadFile(file)
    if (invalid) {
      toast.value = { type: 'err', msg: `${file.name}：${invalid}` }
      continue
    }
    if (!DRAWING_EXTS.includes(fileExt(file.name))) {
      toast.value = { type: 'err', msg: `${file.name} 不是平面图（请用 PDF / 图片）` }
      continue
    }
    if (next.some((f) => f.name === file.name && f.size === file.size)) continue
    if (next.length >= DRAWING_MAX_COUNT) {
      toast.value = {
        type: 'err',
        msg: `每个案例最多 ${DRAWING_MAX_COUNT} 张图纸附件`,
      }
      break
    }
    next.push(file)
  }
  textAttachFiles.value = next
}

function onTextAttachChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) addTextAttachFiles(input.files)
  input.value = ''
}

function removeTextAttach(index: number) {
  textAttachFiles.value = textAttachFiles.value.filter((_, i) => i !== index)
}

async function submitText(force = false) {
  if (!textForm.value.title.trim() || textForm.value.content.trim().length < 4) {
    toast.value = { type: 'err', msg: '请填写资料名称和正文（正文 ≥ 4 字）' }
    return
  }
  if (!baseId.value) return
  const titleKey = textForm.value.title
  if (!force) {
    try {
      const dup = await checkKnowledgeDocumentDuplicate({
        baseId: baseId.value,
        name: textForm.value.title.trim(),
      })
      if (dup.exists) {
        pendingDuplicate.value = {
          kind: 'text',
          label: textForm.value.title.trim(),
          duplicates: dup.duplicates,
        }
        return
      }
    } catch (e) {
      toast.value = {
        type: 'err',
        msg: e instanceof Error ? e.message : '重复检查失败',
      }
      return
    }
  }
  const attachments = [...textAttachFiles.value]
  uploading.value = [...uploading.value, titleKey]
  try {
    const result = await createTextDocument({
      baseId: baseId.value,
      title: textForm.value.title.trim(),
      content: textForm.value.content.trim(),
      uploader: uploaderName.value,
      tags: textForm.value.tags
        ? textForm.value.tags.split(/[,，\s]+/).filter(Boolean)
        : ['手录'],
      force,
    })
    items.value = result.items
    let attached = 0
    const attachErrors: string[] = []
    for (const file of attachments) {
      const jobKey = `${Date.now()}-${file.name}-${Math.random().toString(36).slice(2, 6)}`
      uploadJobs.value = [
        ...uploadJobs.value,
        { key: jobKey, name: file.name, percent: 0, phase: 'upload' },
      ]
      try {
        const up = await uploadKnowledgeDocument(
          {
            baseId: baseId.value,
            file,
            name: file.name,
            tags: ['图纸附件'],
            parentId: result.item.id,
            asAttachment: true,
          },
          (p) => {
            uploadJobs.value = uploadJobs.value.map((j) =>
              j.key === jobKey ? { ...j, percent: p.percent, phase: 'upload' } : j,
            )
          },
        )
        items.value = up.items
        attached += 1
        uploadJobs.value = uploadJobs.value.filter((j) => j.key !== jobKey)
      } catch (e) {
        uploadJobs.value = uploadJobs.value.filter((j) => j.key !== jobKey)
        attachErrors.push(
          `${file.name}：${e instanceof Error ? e.message : '上传失败'}`,
        )
      }
    }
    textForm.value = { title: '', content: '', tags: '' }
    textAttachFiles.value = []
    if (attachErrors.length) {
      toast.value = {
        type: 'err',
        msg: `${result.item.name} 已入库，但图纸未全部挂上：${attachErrors.join('；')}`,
      }
    } else if (attached > 0) {
      toast.value = {
        type: 'ok',
        msg: `${fmtIngestToast(result.item.name, result.item)}，并已挂上 ${attached} 张图纸附件`,
      }
    } else {
      toast.value = { type: 'ok', msg: fmtIngestToast(result.item.name, result.item) }
    }
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: `入库失败：${e instanceof Error ? e.message : '未知错误'}`,
    }
  } finally {
    uploading.value = uploading.value.filter((n) => n !== titleKey)
  }
}

async function cancelIngestTask(task: KbIngestTask) {
  if (!baseId.value || task.status !== 'queued') return
  cancellingTaskId.value = task.id
  try {
    await cancelKnowledgeIngestTask(baseId.value, task.id)
    toast.value = { type: 'ok', msg: `已取消「${task.docName || '入库任务'}」` }
    await fetchList()
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '取消失败',
    }
  } finally {
    cancellingTaskId.value = null
  }
}

async function runProbe() {
  if (!probe.value.trim() || !baseId.value) return
  probeLoading.value = true
  probeRes.value = null
  try {
    const chunks = await searchKnowledge({
      query: probe.value.trim(),
      baseId: baseId.value,
      topK: 5,
    })
    probeRes.value = chunks
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '检索失败',
    }
    probeRes.value = []
  } finally {
    probeLoading.value = false
  }
}

function setAttachDraft(docId: string, parentId: string) {
  attachDraft.value = { ...attachDraft.value, [docId]: parentId }
}

function askDeleteDoc(doc: KbItem) {
  if (!canManage.value) return
  pendingDeleteDoc.value = doc
}

async function attachDrawing(doc: KbItem, parentId: string) {
  if (!parentId || !baseId.value) return
  attachingId.value = doc.id
  try {
    const result = await attachKnowledgeDocument({
      baseId: baseId.value,
      docId: doc.id,
      parentId,
      asAttachment: true,
    })
    items.value = result.items
    delete attachDraft.value[doc.id]
    const parentName =
      result.item.parentName || caseDocs.value.find((c) => c.id === parentId)?.name || '案例卡'
    toast.value = {
      type: 'ok',
      msg: `已挂到「${parentName}」，并改为图纸附件（已移出向量）`,
    }
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof ApiError || e instanceof Error ? e.message : '挂靠失败',
    }
  } finally {
    attachingId.value = null
  }
}

async function confirmDeleteDoc() {
  if (!baseId.value || !pendingDeleteDoc.value) return
  const doc = pendingDeleteDoc.value
  deletingId.value = doc.id
  try {
    items.value = await deleteKnowledgeDocument(baseId.value, doc.id)
    pendingDeleteDoc.value = null
    toast.value = { type: 'ok', msg: '资料已删除' }
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '删除失败',
    }
  } finally {
    deletingId.value = null
  }
}

async function retryDoc(doc: KbItem) {
  if (!baseId.value || doc.status === 'parsing') return
  retryingId.value = doc.id
  try {
    const data = await reparseKnowledgeDocument(baseId.value, doc.id)
    if (data.items?.length) items.value = data.items
    else if (data.item) {
      items.value = items.value.map((it) => (it.id === data.item.id ? data.item : it))
    }
    toast.value = { type: 'ok', msg: `${doc.name} 已重新提交解析` }
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '重试失败',
    }
  } finally {
    retryingId.value = null
  }
}

async function downloadDoc(doc: KbItem) {
  if (!baseId.value) return
  downloadingId.value = doc.id
  try {
    await downloadKnowledgeDocument(baseId.value, doc.id, doc.name)
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '下载失败',
    }
  } finally {
    downloadingId.value = null
  }
}

async function openDocPreview(doc: KbItem) {
  if (!baseId.value) return
  const video = isKbVideoDoc(doc)
  const playUrl =
    video && (doc.fileKey || doc.source === 'file')
      ? kbVideoFilePath(doc.id, { baseId: baseId.value, withToken: true })
      : undefined
  if (video && doc.status === 'parsing' && !playUrl) {
    toast.value = {
      type: 'ok',
      msg: '视频正在转写，请稍后再预览',
    }
    return
  }
  previewLoadingId.value = doc.id
  try {
    const data = await getKnowledgeDocumentPreview(baseId.value, doc.id)
    const content = data.content || ''
    const chunks = data.chunks || []
    if (video && !content.trim() && !chunks.length && !playUrl) {
      toast.value = {
        type: 'ok',
        msg: '暂无转写正文，请点「下载」查看原片；转写完成后可预览',
      }
      return
    }
    textPreview.value = {
      item: data.item,
      content,
      chunks,
      truncated: Boolean(data.truncated),
      playUrl,
    }
  } catch (e) {
    if (video && playUrl) {
      textPreview.value = {
        item: doc,
        content: '',
        chunks: [],
        truncated: false,
        playUrl,
      }
      return
    }
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '预览加载失败',
    }
  } finally {
    previewLoadingId.value = null
  }
}
</script>

<template>
  <div class="kb-detail-enter space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <RouterLink
          to="/knowledge"
          class="inline-flex items-center gap-1 text-[11px] text-text-secondary hover:text-molybdenum mb-1.5"
        >
          <ArrowLeft class="size-3" />
          返回知识库列表
        </RouterLink>
        <h1 class="text-xl font-semibold">{{ base?.name || '知识库详情' }}</h1>
        <p class="mt-1 text-[12px] text-text-secondary">
          {{
            base?.description ||
            '上传 PDF / Word / Excel / 图片 / 文本。规范与产品说明会向量化检索；整份投标书、投标函、他司材料只存档不进检索。'
          }}
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-3">
        <div class="flex gap-3 text-[11px] font-mono text-text-secondary">
          <span>
            资料总数
            <span class="text-text-primary text-base font-semibold">{{ items.length }}</span>
          </span>
          <span>
            总切块
            <span class="text-molybdenum text-base font-semibold">{{ totalChunks }}</span>
          </span>
          <span>
            总字符
            <span class="text-patina text-base font-semibold">{{ totalCharsK }}k</span>
          </span>
        </div>
        <button
          v-if="canManage"
          type="button"
          class="h-8 px-3 text-[12px] rounded-md border border-hairline inline-flex items-center gap-1.5 text-text-secondary hover:text-molybdenum hover:bg-molybdenum/10"
          @click="aclOpen = true"
        >
          <Shield class="size-3.5" />
          权限
        </button>
      </div>
    </header>

    <div
      v-if="canViewOnly"
      class="rounded-lg border border-hairline bg-bg-base/40 px-4 py-3 text-[12px] text-text-secondary"
    >
      您仅有查看权限，无法导入或删除资料；如需检索请在 AI 对话中勾选本库。
    </div>

    <!-- 导入资料 -->
    <section v-if="canManage" class="rounded-lg panel-surface overflow-hidden flex flex-col">
      <header class="flex items-center justify-between px-4 lg:px-5 py-3 border-b border-border">
        <h3 class="text-sm font-medium tracking-wide truncate flex items-center gap-2">
          <span class="inline-block w-1 h-3 bg-iron rounded-sm" />
          导入资料
        </h3>
      </header>
      <div class="p-4 lg:p-5">
        <div class="flex gap-1 border-b border-hairline mb-4">
          <button
            v-for="t in tabs"
            :key="t.k"
            type="button"
            class="px-4 py-2 -mb-px text-[12px] flex items-center gap-1.5 border-b-2 transition-colors"
            :class="
              tab === t.k
                ? 'border-iron text-text-primary'
                : 'border-transparent text-text-secondary hover:text-text-primary'
            "
            @click="tab = t.k"
          >
            <component :is="t.icon" class="size-3.5" />
            {{ t.label }}
          </button>
        </div>

        <div v-if="tab === 'file'" class="space-y-3">
          <div
            class="border-2 border-dashed rounded-md transition-colors"
            :class="
              dragOver ? 'border-iron bg-iron/5' : 'border-hairline hover:border-molybdenum/60'
            "
            @dragover.prevent="dragOver = true"
            @dragleave="dragOver = false"
            @drop="onDrop"
          >
            <div class="py-10 flex flex-col items-center gap-3 text-center px-6">
              <Upload class="size-8 text-iron" />
              <div class="text-[13px]">
                拖拽资料到此处，或
                <button
                  type="button"
                  class="text-molybdenum hover:underline"
                  @click="fileRef?.click()"
                >
                  点击选择文件
                </button>
              </div>
              <div class="text-[11px] text-text-secondary max-w-xl">
                手册、产品说明、认证规范可上传并检索。也可上传 mp4 / webm，后台语音转写后入库检索。文件名含「投标文件」「投标函」或正文为其他公司投标书的，会保留原件但不向量化，检索自动跳过。充电站平面图请到「文本粘贴」。文档最大
                {{ fmtSize(KB_UPLOAD_MAX_BYTES) }}，视频最大
                {{ fmtSize(KB_VIDEO_UPLOAD_MAX_BYTES_DEFAULT) }}。
              </div>
              <input
                ref="fileRef"
                type="file"
                multiple
                :accept="ACCEPT_LIST"
                class="hidden"
                @change="onFileChange"
              />
            </div>
          </div>

          <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
            <div
              v-for="g in FILE_TYPE_GROUPS"
              :key="g.label"
              class="border border-hairline rounded-md px-3 py-2 flex items-center gap-2"
            >
              <component :is="g.icon" class="size-4" :class="g.color" />
              <div class="text-[11px]">
                <div class="text-text-primary">{{ g.label }}</div>
                <div class="text-text-muted font-mono">
                  {{ g.exts.slice(0, 2).join(' · ') }}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-else-if="tab === 'url'" class="space-y-3">
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <label class="block">
              <div class="text-[11px] text-text-secondary mb-1">资料 URL（必填）</div>
              <input
                v-model="urlForm.url"
                class="kb-input"
                placeholder="https://www.example.com/标准全文.html"
              />
            </label>
            <label class="block">
              <div class="text-[11px] text-text-secondary mb-1">资料名称（选填）</div>
              <input
                v-model="urlForm.title"
                class="kb-input"
                placeholder="如：GB 21369 解读"
              />
            </label>
            <label class="block">
              <div class="text-[11px] text-text-secondary mb-1">标签（逗号/空格分隔）</div>
              <input
                v-model="urlForm.tags"
                class="kb-input"
                placeholder="国标, 节能, 监测"
              />
            </label>
          </div>
          <div class="flex justify-end">
            <button
              type="button"
              class="kb-btn-primary"
              :disabled="uploading.includes(urlForm.url)"
              @click="submitUrl()"
            >
              <Loader2
                v-if="uploading.includes(urlForm.url)"
                class="size-3.5 animate-spin"
              />
              <LinkIcon v-else class="size-3.5" />
              抓取并入库
            </button>
          </div>
        </div>

        <div v-else class="space-y-3">
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <label class="block">
              <div class="text-[11px] text-text-secondary mb-1">资料名称（必填）</div>
              <input
                v-model="textForm.title"
                class="kb-input"
                placeholder="如：TC-03 调质工艺要点"
              />
            </label>
            <label class="block">
              <div class="text-[11px] text-text-secondary mb-1">标签</div>
              <input
                v-model="textForm.tags"
                class="kb-input"
                placeholder="工艺, 调质, TC-03"
              />
            </label>
          </div>
          <label class="block">
            <div class="text-[11px] text-text-secondary mb-1">正文</div>
            <textarea
              v-model="textForm.content"
              class="kb-input min-h-[140px] font-sans"
              placeholder="充电站布置案例请按模板粘贴「1.方案摘要 … 5.生成时如何复用」整篇；会按完整案例入库，不会拆成碎片。"
            />
          </label>
          <div class="block">
            <div class="text-[11px] text-text-secondary mb-1">图纸附件（选填，PDF / 图片，最多 2 张）</div>
            <div
              class="border border-dashed border-hairline rounded-md px-3 py-3 flex flex-col gap-2"
            >
              <div class="flex items-center justify-between gap-2">
                <div class="text-[12px] text-text-secondary">
                  与案例卡一并入库，不 OCR、不进向量；检索命中该案例时再附图。
                </div>
                <button
                  type="button"
                  class="kb-btn-primary h-8 px-2.5 text-[11px]"
                  @click="textAttachRef?.click()"
                >
                  <Paperclip class="size-3.5" />
                  选择图纸
                </button>
              </div>
              <input
                ref="textAttachRef"
                type="file"
                multiple
                :accept="DRAWING_ACCEPT"
                class="hidden"
                @change="onTextAttachChange"
              />
              <div v-if="textAttachFiles.length" class="flex flex-wrap gap-1.5">
                <span
                  v-for="(f, i) in textAttachFiles"
                  :key="`${f.name}-${f.size}-${i}`"
                  class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] bg-molybdenum/10 text-molybdenum border border-molybdenum/20"
                >
                  <span class="max-w-[14rem] truncate" :title="f.name">{{ f.name }}</span>
                  <span class="text-text-muted font-mono">{{ fmtSize(f.size) }}</span>
                  <button
                    type="button"
                    class="p-0.5 hover:text-iron"
                    title="移除"
                    @click="removeTextAttach(i)"
                  >
                    <X class="size-3" />
                  </button>
                </span>
              </div>
            </div>
          </div>
          <div class="flex justify-end">
            <button
              type="button"
              class="kb-btn-primary"
              :disabled="uploading.includes(textForm.title)"
              @click="submitText()"
            >
              <Loader2
                v-if="uploading.includes(textForm.title)"
                class="size-3.5 animate-spin"
              />
              <FileText v-else class="size-3.5" />
              入库
            </button>
          </div>
        </div>

        <div
          v-if="uploadJobs.length > 0 || uploading.length > 0 || visibleIngestTasks.length > 0"
          class="mt-3 space-y-2"
        >
          <div
            v-for="job in uploadJobs"
            :key="job.key"
            class="px-3 py-2 rounded-md bg-bg-base/40 border border-hairline"
          >
            <div class="flex items-center justify-between gap-2 text-[11px] text-text-secondary">
              <span class="truncate">{{ job.name }}</span>
              <span class="font-mono shrink-0">
                {{ job.phase === 'upload' ? `${job.percent}%` : '解析中' }}
              </span>
            </div>
            <div class="mt-1.5 h-1 rounded-full bg-hairline overflow-hidden">
              <div
                class="h-full bg-iron transition-[width] duration-200"
                :class="job.phase === 'parsing' ? 'w-full opacity-60' : ''"
                :style="job.phase === 'upload' ? { width: `${job.percent}%` } : undefined"
              />
            </div>
          </div>
          <div
            v-for="task in visibleIngestTasks"
            :key="task.id"
            class="px-3 py-2 rounded-md bg-bg-base/40 border border-hairline"
          >
            <div class="flex items-center justify-between gap-2 text-[11px] text-text-secondary">
              <span class="truncate">{{ task.docName || '入库任务' }}</span>
              <span class="flex items-center gap-2 shrink-0">
                <span class="font-mono">{{ ingestTaskLabel(task) }}</span>
                <button
                  v-if="canManage && task.status === 'queued'"
                  type="button"
                  class="text-iron hover:underline"
                  :disabled="cancellingTaskId === task.id"
                  @click="cancelIngestTask(task)"
                >
                  {{ cancellingTaskId === task.id ? '取消中…' : '取消' }}
                </button>
              </span>
            </div>
            <div class="mt-1.5 h-1 rounded-full bg-hairline overflow-hidden">
              <div
                class="h-full bg-iron transition-[width] duration-200"
                :style="{ width: `${Math.max(task.progress || 0, task.status === 'queued' ? 8 : 0)}%` }"
              />
            </div>
            <div v-if="task.errorMsg" class="mt-1 text-[10px] text-iron truncate" :title="task.errorMsg">
              {{ task.errorMsg }}
            </div>
          </div>
          <div
            v-if="uploading.length > 0"
            class="px-3 py-2 rounded-md bg-bg-base/40 border border-hairline text-[11px] text-text-secondary flex items-center gap-2"
          >
            <Loader2 class="size-3 animate-spin text-iron" />
            正在处理 {{ uploading.length }} 项：{{ uploading.slice(0, 3).join(' · ')
            }}{{ uploading.length > 3 ? ` 等 ${uploading.length} 项` : '' }}
          </div>
        </div>
      </div>
    </section>

    <!-- 语义检索测试 -->
    <section v-if="canUse" class="rounded-lg panel-surface overflow-hidden flex flex-col">
      <header class="flex items-center justify-between px-4 lg:px-5 py-3 border-b border-border">
        <div class="min-w-0">
          <h3 class="text-sm font-medium tracking-wide truncate flex items-center gap-2">
            <span class="inline-block w-1 h-3 bg-iron rounded-sm" />
            语义检索测试
          </h3>
          <div class="text-[11px] text-muted-foreground mt-0.5 pl-3">
            验证知识库召回质量。输入问题，查看返回的相似片段与分数。
          </div>
        </div>
      </header>
      <div class="p-4 lg:p-5">
        <div class="flex gap-2">
          <input
            v-model="probe"
            class="kb-input flex-1"
            placeholder="如：智能助手如何使用知识库？"
            @keydown.enter="runProbe()"
          />
          <button
            type="button"
            class="kb-btn-primary whitespace-nowrap"
            :disabled="probeLoading || !probe.trim()"
            @click="runProbe()"
          >
            <Loader2 v-if="probeLoading" class="size-3.5 animate-spin" />
            <Search v-else class="size-3.5" />
            检索
          </button>
        </div>
        <div v-if="probeRes" class="mt-3 space-y-2">
          <div v-if="probeRes.length === 0" class="text-[12px] text-iron">
            未命中
          </div>
          <div
            v-for="(c, i) in probeRes"
            v-else
            :key="i"
            class="border border-hairline rounded-md px-3 py-2 bg-bg-base/40"
          >
            <div class="flex justify-between gap-2 text-[10px] text-text-muted font-mono mb-1">
              <span
                class="truncate min-w-0 inline-flex items-center gap-1"
                :title="c.name || undefined"
              >
                <Film
                  v-if="isKbVideoRef(c)"
                  class="size-3 shrink-0 text-iron"
                />
                #{{ i + 1 }}{{ c.name ? ` · ${c.name}` : '' }}
                <span
                  v-if="isKbVideoRef(c)"
                  class="shrink-0 rounded px-1 py-px text-[9px] bg-iron/10 text-iron border border-iron/20"
                >视频</span>
                <span
                  v-if="fmtKbVideoRange(c.startMs, c.endMs)"
                  class="shrink-0 text-text-muted"
                >{{ fmtKbVideoRange(c.startMs, c.endMs) }}</span>
              </span>
              <span class="text-molybdenum shrink-0">相似度 {{ (c.score ?? 0).toFixed(4) }}</span>
            </div>
            <div
              class="text-[12px] leading-relaxed text-text-primary whitespace-pre-wrap line-clamp-5"
            >
              {{ c.content }}
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 资料列表 -->
    <section class="rounded-lg panel-surface overflow-hidden flex flex-col">
      <header class="flex flex-wrap items-center justify-between gap-2 px-4 lg:px-5 py-3 border-b border-border">
        <h3 class="text-sm font-medium tracking-wide truncate flex items-center gap-2">
          <span class="inline-block w-1 h-3 bg-iron rounded-sm" />
          已导入资料（{{ filtered.length }}/{{ items.length }}）
        </h3>
        <div class="flex flex-wrap items-center gap-2">
          <input
            v-model="keyword"
            placeholder="按名称/标签/摘要过滤..."
            class="kb-input h-8 w-56 text-[12px]"
          />
        </div>
      </header>
      <div class="p-4 lg:p-5">
        <div
          v-if="loading"
          class="py-12 text-center text-text-secondary text-[12px]"
        >
          <Loader2 class="inline size-4 animate-spin mr-2" /> 正在加载知识库...
        </div>
        <div
          v-else-if="filtered.length === 0"
          class="py-12 text-center text-text-secondary text-[12px]"
        >
          暂无资料。请通过上方上传文件、抓取 URL 或粘贴文本进行入库。
        </div>
        <div v-else class="overflow-x-auto -mx-4 px-4">
          <table class="w-full min-w-[1280px] table-fixed text-[12px]">
            <colgroup>
              <col class="w-[28%]" />
              <col class="w-[7%]" />
              <col class="w-[7%]" />
              <col class="w-[7%]" />
              <col class="w-[6%]" />
              <col class="w-[10%]" />
              <col class="w-[7%]" />
              <col class="w-[8%]" />
              <col class="w-[5%]" />
              <col class="w-[15%]" />
            </colgroup>
            <thead>
              <tr class="text-text-muted border-b border-hairline">
                <th class="text-left px-3 py-2 font-medium">资料名称</th>
                <th class="text-left px-2 py-2 font-medium whitespace-nowrap">来源</th>
                <th class="text-right px-2 py-2 font-medium font-mono whitespace-nowrap">
                  大小
                </th>
                <th class="text-right px-2 py-2 font-medium font-mono whitespace-nowrap">
                  字符
                </th>
                <th
                  class="text-right px-2 py-2 font-medium font-mono whitespace-nowrap"
                  title="后端向量化分块数量（约 600 字一块，重叠 150）"
                >
                  切块
                </th>
                <th class="text-left px-2 py-2 font-medium whitespace-nowrap">标签</th>
                <th class="text-left px-2 py-2 font-medium whitespace-nowrap">上传者</th>
                <th class="text-right px-2 py-2 font-medium whitespace-nowrap">时间</th>
                <th class="text-center px-2 py-2 font-medium whitespace-nowrap">状态</th>
                <th class="text-center px-2 py-2 font-medium whitespace-nowrap">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="{ item: it, depth, parentName, childCount, unlinkedDrawing } in displayRows"
                :key="it.id"
                class="border-b border-hairline/70 hover:bg-bg-base/40"
                :class="unlinkedDrawing ? 'bg-sulfur/5' : depth ? 'bg-bg-base/30' : ''"
              >
                <td class="px-3 py-2.5 align-middle">
                  <div class="flex items-start gap-2 min-w-0" :class="depth ? 'pl-5' : ''">
                    <component
                      :is="iconForType(it.fileType || it.source)"
                      class="size-4 shrink-0 mt-0.5"
                      :class="colorForType(it.fileType)"
                    />
                    <div class="min-w-0 flex-1">
                      <div
                        class="text-text-primary truncate flex items-center gap-1.5"
                        :title="it.name"
                      >
                        <span class="truncate">{{ it.name }}</span>
                      </div>
                      <div
                        v-if="childCount"
                        class="text-[10.5px] text-patina mt-0.5 flex items-center gap-1"
                      >
                        <Paperclip class="size-3 shrink-0" />
                        已挂附图纸 {{ childCount }} 张
                      </div>
                      <div
                        v-else-if="parentName"
                        class="text-[10.5px] text-molybdenum mt-0.5 flex items-center gap-1"
                      >
                        <Link2 class="size-3 shrink-0" />
                        挂在：{{ parentName }}
                      </div>
                      <div
                        v-else-if="unlinkedDrawing"
                        class="text-[10.5px] text-sulfur mt-0.5"
                      >
                        未挂靠案例 · 当前{{ it.kind === 'drawing' ? '只存盘' : `已按正文切成 ${it.chunks || 0} 块` }}，检索时不会当附图
                      </div>
                      <div
                        v-if="canManage && unlinkedDrawing && caseDocs.length"
                        class="mt-1 flex items-center gap-1 min-w-0"
                      >
                        <select
                          class="kb-input text-[11px] max-w-[14rem]"
                          :value="attachDraft[it.id] || ''"
                          :disabled="attachingId === it.id"
                          @change="
                            setAttachDraft(it.id, ($event.target as HTMLSelectElement).value)
                          "
                        >
                          <option value="">选择案例卡…</option>
                          <option v-for="c in caseDocs" :key="c.id" :value="c.id">{{ c.name }}</option>
                        </select>
                        <button
                          type="button"
                          class="kb-btn-primary h-7 px-2 text-[11px] shrink-0"
                          :disabled="!attachDraft[it.id] || attachingId === it.id"
                          @click="attachDrawing(it, attachDraft[it.id])"
                        >
                          <Loader2 v-if="attachingId === it.id" class="size-3 animate-spin" />
                          确认挂靠
                        </button>
                      </div>
                      <div
                        v-if="fmtPageOcr(it)"
                        class="text-[10px] font-mono mt-0.5"
                        :class="it.ocrCapped || it.formulaFallback ? 'text-sulfur' : 'text-text-muted'"
                      >
                        {{ fmtPageOcr(it) }}
                      </div>
                      <div
                        v-if="it.status === 'failed'"
                        class="text-[10.5px] text-iron mt-0.5 whitespace-normal break-words leading-snug line-clamp-3"
                        :title="it.errorMsg || '入库失败'"
                      >
                        {{ it.errorMsg || '入库失败' }}
                      </div>
                      <div
                        v-else-if="it.reviewStatus === 'rejected' && it.reviewComment"
                        class="text-[10.5px] text-iron mt-0.5 truncate"
                        :title="it.reviewComment"
                      >
                        驳回：{{ it.reviewComment }}
                      </div>
                      <div
                        v-else-if="it.summary"
                        class="text-[10.5px] text-text-muted truncate mt-0.5"
                        :title="it.summary"
                      >
                        {{ it.summary }}
                      </div>
                    </div>
                  </div>
                </td>
                <td class="px-2 py-2.5 align-middle whitespace-nowrap">
                  <span
                    class="px-1.5 py-0.5 rounded text-[10px] font-mono bg-bg-base/60 text-text-secondary border border-hairline"
                  >
                    {{
                      it.kind === 'drawing'
                        ? '图纸附件'
                        : isKbVideoDoc(it)
                          ? '视频'
                          : it.source === 'file'
                            ? (it.fileType || 'FILE').toUpperCase()
                            : it.source.toUpperCase()
                    }}
                  </span>
                </td>
                <td
                  class="px-2 py-2.5 text-right font-mono text-text-secondary whitespace-nowrap align-middle"
                >
                  {{ fmtSize(it.size) }}
                </td>
                <td
                  class="px-2 py-2.5 text-right font-mono text-text-secondary whitespace-nowrap align-middle"
                >
                  {{
                    it.kind === 'drawing'
                      ? '—'
                      : isKbVideoDoc(it) && !(it.chunks ?? 0)
                        ? '—'
                        : it.charCount
                          ? it.charCount.toLocaleString()
                          : '—'
                  }}
                </td>
                <td
                  class="px-2 py-2.5 text-right font-mono text-molybdenum whitespace-nowrap align-middle"
                  :title="
                    it.kind === 'drawing'
                      ? '图纸附件不进向量'
                      : isKbVideoDoc(it)
                        ? (it.chunks ?? 0) > 0
                          ? '已按转写文本切块入库'
                          : '视频转写后可检索'
                        : isSkipRag(it)
                          ? '判定为投标书/他司材料，未写入检索'
                          : it.chunks != null
                            ? `已切成 ${it.chunks} 块向量片段`
                            : undefined
                  "
                >
                  {{
                    it.kind === 'drawing'
                      ? '附件'
                      : isKbVideoDoc(it)
                        ? (it.chunks ?? 0) > 0
                          ? `${it.chunks} 块`
                          : it.status === 'parsing'
                            ? '转写中'
                            : '待转写'
                        : isSkipRag(it)
                          ? '不检索'
                          : it.chunks != null
                            ? `${it.chunks} 块`
                            : '—'
                  }}
                </td>
                <td class="px-2 py-2.5 align-middle">
                  <div class="flex flex-wrap gap-1">
                    <span
                      v-for="t in (it.tags || []).slice(0, 3)"
                      :key="t"
                      class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-molybdenum/10 text-molybdenum whitespace-nowrap"
                    >
                      <Tag class="size-2.5 shrink-0" />
                      {{ formatKbTag(t) }}
                    </span>
                  </div>
                </td>
                <td
                  class="px-2 py-2.5 text-text-secondary whitespace-nowrap align-middle truncate"
                  :title="it.uploader || undefined"
                >
                  {{ it.uploader || '—' }}
                </td>
                <td
                  class="px-2 py-2.5 text-right text-text-muted font-mono text-[11px] whitespace-nowrap align-middle"
                >
                  {{ fmtAgo(it.createdAt, it.createdAtUtc) }}
                </td>
                <td class="px-2 py-2.5 text-center align-middle whitespace-nowrap">
                  <span
                    class="inline-flex items-center gap-1 text-[10.5px]"
                    :class="
                      it.status === 'failed' || it.reviewStatus === 'rejected'
                        ? 'text-iron'
                        : it.status === 'parsing' ||
                            (it.kind !== 'drawing' &&
                              !isKbVideoDoc(it) &&
                              !isSkipRag(it) &&
                              (it.chunks ?? 0) === 0)
                          ? 'text-sulfur'
                          : isKbVideoDoc(it) && !(it.chunks ?? 0)
                            ? 'text-sulfur'
                            : 'text-patina'
                    "
                    :title="it.errorMsg || undefined"
                  >
                    <Loader2
                      v-if="
                        it.status === 'parsing' ||
                        (it.kind !== 'drawing' &&
                          !isKbVideoDoc(it) &&
                          it.status === 'ready' &&
                          it.reviewStatus !== 'rejected' &&
                          !isSkipRag(it) &&
                          (it.chunks ?? 0) === 0)
                      "
                      class="size-3.5 animate-spin"
                    />
                    <Film
                      v-else-if="isKbVideoDoc(it) && it.status === 'ready' && !(it.chunks ?? 0)"
                      class="size-3.5"
                    />
                    <XCircle
                      v-else-if="it.status === 'failed' || it.reviewStatus === 'rejected'"
                      class="size-3.5"
                    />
                    <Ok v-else class="size-3.5" />
                    {{ statusLabel(it) }}
                  </span>
                </td>
                <td class="px-2 py-2.5 text-center align-middle whitespace-nowrap">
                  <button
                    v-if="canManage && (it.status === 'failed' || it.reviewStatus === 'rejected')"
                    type="button"
                    :disabled="retryingId === it.id"
                    class="inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] text-iron hover:bg-iron/10 transition-colors whitespace-nowrap"
                    title="删除旧向量后重新解析"
                    @click="retryDoc(it)"
                  >
                    <Loader2 v-if="retryingId === it.id" class="size-3 animate-spin" />
                    <RotateCcw v-else class="size-3" />
                    重试
                  </button>
                  <button
                    type="button"
                    :disabled="previewLoadingId === it.id"
                    class="ml-0.5 inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] text-text-secondary hover:text-molybdenum hover:bg-molybdenum/10 transition-colors whitespace-nowrap"
                    :title="isKbVideoDoc(it) ? '预览视频与转写正文' : '预览入库文本内容'"
                    @click="openDocPreview(it)"
                  >
                    <Loader2
                      v-if="previewLoadingId === it.id"
                      class="size-3 animate-spin"
                    />
                    <Eye v-else class="size-3" />
                    预览
                  </button>
                  <button
                    type="button"
                    :disabled="downloadingId === it.id"
                    class="ml-0.5 inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] text-text-secondary hover:text-molybdenum hover:bg-molybdenum/10 transition-colors whitespace-nowrap"
                    title="下载原文件（无原件时导出预览正文）"
                    @click="downloadDoc(it)"
                  >
                    <Loader2 v-if="downloadingId === it.id" class="size-3 animate-spin" />
                    <Download v-else class="size-3" />
                    下载
                  </button>
                  <button
                    v-if="canManage"
                    type="button"
                    :disabled="deletingId === it.id"
                    class="ml-0.5 inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] text-text-secondary hover:text-iron hover:bg-iron/10 transition-colors whitespace-nowrap"
                    @click="askDeleteDoc(it)"
                  >
                    <Loader2 v-if="deletingId === it.id" class="size-3 animate-spin" />
                    <Trash2 v-else class="size-3" />
                    删除
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <KbAclDialog
      :open="aclOpen"
      :base-id="baseId"
      :base-name="base?.name"
      @close="aclOpen = false"
      @saved="void fetchList()"
    />

    <!-- Toast -->
    <div
      v-if="toast"
      class="fixed top-6 left-1/2 z-50 -translate-x-1/2 px-4 py-2.5 rounded-md text-[12px] shadow-lg border max-w-[min(92vw,28rem)] text-center"
      :class="
        toast.type === 'ok'
          ? 'bg-patina/10 border-patina/40 text-patina'
          : 'bg-iron/10 border-iron/40 text-iron'
      "
    >
      {{ toast.msg }}
    </div>

    <!-- Duplicate upload confirm -->
    <div
      v-if="pendingDuplicate"
      class="fixed inset-0 z-[60] bg-bg-base/80 backdrop-blur-sm flex items-center justify-center p-4"
      @click.self="pendingDuplicate = null"
    >
      <div class="w-full max-w-md rounded-lg border border-hairline bg-bg-elevated shadow-2xl overflow-hidden">
        <div class="px-5 pt-5 pb-3">
          <div class="flex items-start gap-3">
            <span
              class="mt-0.5 size-9 shrink-0 rounded-md bg-sulfur/15 text-sulfur inline-flex items-center justify-center border border-sulfur/25"
            >
              <TriangleAlert class="size-4" />
            </span>
            <div class="min-w-0 space-y-1.5 text-left">
              <div class="text-[14px] font-medium text-text-primary">检测到重复资料</div>
              <div class="text-[12px] text-text-secondary leading-relaxed">
                「<span class="text-text-primary font-medium">{{ pendingDuplicate.label }}</span>」
                与当前知识库中已有资料同名。继续上传将<span class="text-text-primary font-medium">删除旧版</span>并替换为新内容（含向量片段与原文件）。
              </div>
              <div
                v-if="pendingDuplicate.duplicates[0]"
                class="mt-2 rounded-md border border-hairline bg-bg-base/40 px-3 py-2 text-[11px] text-text-muted"
              >
                已有记录：{{ pendingDuplicate.duplicates[0].uploader || '未知上传者' }} ·
                {{ fmtAgo(pendingDuplicate.duplicates[0].createdAt, pendingDuplicate.duplicates[0].createdAtUtc) }}
              </div>
            </div>
          </div>
        </div>
        <div class="px-5 py-3 border-t border-hairline bg-bg-base/30 flex justify-end gap-2">
          <button
            type="button"
            class="h-8 px-3 text-[12px] rounded-md border border-hairline bg-transparent text-text-secondary hover:bg-hairline/40"
            @click="pendingDuplicate = null"
          >
            取消
          </button>
          <button
            type="button"
            class="h-8 px-3 text-[12px] rounded-md bg-molybdenum text-white hover:brightness-110"
            @click="confirmDuplicateUpload()"
          >
            覆盖上传
          </button>
        </div>
      </div>
    </div>

    <!-- Delete confirm -->
    <div
      v-if="pendingDeleteDoc"
      class="fixed inset-0 z-[60] bg-bg-base/80 backdrop-blur-sm flex items-center justify-center p-4"
      @click.self="!deletingId && (pendingDeleteDoc = null)"
    >
      <div class="w-full max-w-md rounded-lg border border-hairline bg-bg-elevated shadow-2xl overflow-hidden">
        <div class="px-5 pt-5 pb-3">
          <div class="flex items-start gap-3">
            <span
              class="mt-0.5 size-9 shrink-0 rounded-md bg-iron/15 text-iron inline-flex items-center justify-center border border-iron/25"
            >
              <TriangleAlert class="size-4" />
            </span>
            <div class="min-w-0 space-y-1.5 text-left">
              <div class="text-[14px] font-medium text-text-primary">删除资料</div>
              <div class="text-[12px] text-text-secondary leading-relaxed">
                确认删除「
                <span class="text-text-primary font-medium">{{ pendingDeleteDoc.name }}</span>
                」？相关向量片段与原文件将一并移除。
              </div>
            </div>
          </div>
        </div>
        <div class="px-5 py-3 border-t border-hairline bg-bg-base/30 flex justify-end gap-2">
          <button
            type="button"
            :disabled="Boolean(deletingId)"
            class="h-8 px-3 text-[12px] rounded-md border border-hairline bg-transparent text-text-secondary hover:bg-hairline/40"
            @click="pendingDeleteDoc = null"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="Boolean(deletingId)"
            class="h-8 px-3 text-[12px] rounded-md bg-iron text-white hover:brightness-110 inline-flex items-center gap-1.5"
            @click="confirmDeleteDoc()"
          >
            <template v-if="deletingId">
              <Loader2 class="size-3.5 animate-spin" />
              删除中…
            </template>
            <template v-else>
              <Trash2 class="size-3.5" />
              确认删除
            </template>
          </button>
        </div>
      </div>
    </div>

    <!-- Text preview -->
    <div
      v-if="textPreview"
      class="fixed inset-0 z-[60] bg-bg-base/85 backdrop-blur-sm flex items-center justify-center p-4"
    >
      <div
        class="bg-bg-elevated border border-hairline rounded-lg shadow-2xl w-full max-w-4xl flex flex-col max-h-[90vh]"
      >
        <div
          class="flex items-center justify-between px-5 py-3 border-b border-hairline shrink-0"
        >
          <div class="flex items-center gap-2.5 min-w-0">
            <Film
              v-if="textPreview.playUrl"
              class="size-5 text-iron shrink-0"
            />
            <FileText
              v-else
              class="size-5 text-molybdenum shrink-0"
            />
            <div class="min-w-0">
              <div class="text-[13.5px] text-text-primary font-medium truncate">
                {{ textPreview.item.name }}
              </div>
              <div class="text-[11px] text-text-muted mt-0.5 font-mono">
                {{
                  (
                    textPreview.item.fileType ||
                    textPreview.item.source ||
                    'DOC'
                  ).toUpperCase()
                }}
                · {{ fmtSize(textPreview.item.size) }} ·
                {{
                  textPreview.chunks.length
                    ? `${textPreview.chunks.length} 块`
                    : `${textPreview.item.charCount?.toLocaleString?.() ?? textPreview.item.charCount} 字`
                }}
                {{ textPreview.truncated ? ' · 仅摘要' : '' }}
                {{ textPreview.playUrl && !textPreview.content ? ' · 可先播原片' : '' }}
              </div>
            </div>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            <button
              type="button"
              :disabled="downloadingId === textPreview.item.id"
              class="h-8 px-2.5 rounded text-[11px] text-text-secondary hover:text-molybdenum hover:bg-molybdenum/10 inline-flex items-center gap-1"
              @click="downloadDoc(textPreview.item)"
            >
              <Loader2
                v-if="downloadingId === textPreview.item.id"
                class="size-3.5 animate-spin"
              />
              <Download v-else class="size-3.5" />
              下载
            </button>
            <button
              type="button"
              class="size-8 rounded hover:bg-hairline/60 inline-flex items-center justify-center text-text-secondary hover:text-text-primary transition-colors"
              aria-label="关闭"
              @click="textPreview = null"
            >
              <X class="size-4" />
            </button>
          </div>
        </div>
        <div class="px-5 py-4 overflow-y-auto flex-1 min-h-0 space-y-4">
          <div
            v-if="textPreview.playUrl"
            class="overflow-hidden rounded-md border border-hairline bg-bg-base"
          >
            <video
              class="max-h-[42vh] w-full bg-black"
              controls
              preload="metadata"
              :src="textPreview.playUrl"
            />
          </div>
          <template v-if="textPreview.content">
            <div v-if="textPreview.chunks.length > 1" class="space-y-3">
              <div
                v-for="c in textPreview.chunks"
                :key="c.chunkIndex"
                class="rounded-md border border-hairline bg-bg-base/40 px-3 py-2.5"
              >
                <div class="text-[10px] font-mono text-text-muted mb-1.5">
                  切块 #{{ c.chunkIndex + 1 }}
                  <span
                    v-if="fmtKbVideoRange(c.startMs, c.endMs)"
                    class="ml-1.5"
                  >
                    {{ fmtKbVideoRange(c.startMs, c.endMs) }}
                  </span>
                </div>
                <div
                  class="text-[12.5px] leading-relaxed text-text-primary whitespace-pre-wrap"
                >
                  {{ c.content }}
                </div>
              </div>
            </div>
            <div
              v-else
              class="text-[12.5px] leading-relaxed text-text-primary whitespace-pre-wrap"
            >
              {{ textPreview.content }}
            </div>
          </template>
          <div
            v-else-if="!textPreview.playUrl"
            class="py-16 text-center text-[12px] text-text-secondary"
          >
            暂无可用正文（可能入库失败或向量库中无切块）。
          </div>
          <div
            v-else
            class="py-2 text-center text-[12px] text-text-secondary"
          >
            转写正文尚未就绪，可先播放原片。
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kb-detail-enter {
  animation: kbDetailEnter 320ms cubic-bezier(0.25, 0.8, 0.25, 1) both;
}
@keyframes kbDetailEnter {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
:deep(.kb-input) {
  background: var(--bg-surface);
  border: 1px solid var(--hairline);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 12px;
  padding: 8px 10px;
  width: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
:deep(.kb-input::placeholder) {
  color: var(--text-muted);
}
:deep(.kb-input:focus) {
  outline: none;
  border-color: var(--accent-molybdenum);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent-molybdenum) 18%, transparent);
}
</style>

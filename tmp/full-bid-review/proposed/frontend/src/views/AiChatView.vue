<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Send,
  Zap,
  Brain,
  Sparkles,
  Loader2,
  CornerDownLeft,
  Quote,
  BotMessageSquare,
  User as UserIcon,
  RotateCcw,
  Lightbulb,
  Plus,
  MessageSquare,
  Pencil,
  Trash2,
  Check,
  X,
  Wand2,
  LibraryBig,
  Wrench,
  BookOpenText,
  Bot,
  ImagePlus,
  Film,
} from 'lucide-vue-next'
import { ApiError } from '@/lib/api'
import weitaiLogo from '@/assets/weitai-logo.jpg'
import { ASSISTANT_NAME } from '@/config/brand'
import {
  cancelChatSession,
  createChatSession,
  deleteChatSession,
  fetchRelatedQuestions,
  getChatSession,
  listChatSessions,
  streamChat,
  summarizeChatSessionTitle,
  updateChatSession,
  type ChatMode,
  type ChatSessionItem,
  type ChatSessionMessage,
} from '@/lib/ai-chat-api'
import { getAgent } from '@/lib/agents-api'
import {
  listKnowledgeBases,
  type KnowledgeBaseItem,
} from '@/lib/knowledge-api'
import {
  listPromptOptions,
  type PromptOption,
} from '@/lib/prompts-api'
import ChatMarkdown from '@/components/ai/ChatMarkdown.vue'
import ChatImageGallery from '@/components/ai/ChatImageGallery.vue'
import ChatKbOriginals from '@/components/ai/ChatKbOriginals.vue'
import ChatKbVideos from '@/components/ai/ChatKbVideos.vue'
import LayoutFileDownloads from '@/components/ai/LayoutFileDownloads.vue'
import type { SlotFileInfo } from '@/lib/tenders-api'
import {
  collectKbVideoHits,
  fmtKbVideoRange,
  isKbVideoRef,
} from '@/lib/kb-video-contract'
import {
  listModelOptions,
  modelTypeLabel,
  type ModelOptionItem,
} from '@/lib/models-api'
import {
  listWorkflowOptions,
  runWorkflowTrial,
  type WorkflowOption,
} from '@/lib/workflows-api'

type Mode = ChatMode

const TENDER_LIB_ID = 'tenderlib01'

interface RefChunk {
  content: string
  score: number
  doc_id?: string
  kb_id?: string
  kbId?: string
  name?: string
  chunk_index?: number
  file_type?: string
  has_file?: boolean
  preview_kind?: string
  kind?: string
  startMs?: number
  endMs?: number
}

interface ToolCallUi {
  id: string
  toolName: string
  phase: 'call' | 'result'
  arguments?: Record<string, unknown>
  content?: string
  error?: string | null
  durationMs?: number
  table?: ToolTable | null
}

interface ToolTable {
  headers: string[]
  rows: string[][]
}

/** 尝试把工具返回内容解析成表格（JSON 行数组 / {columns,rows}） */
function tryParseToolTable(content: string | undefined): ToolTable | null {
  if (!content) return null
  const raw = content.trim()
  if (!raw) return null

  const asTable = (rows: unknown): ToolTable | null => {
    if (!Array.isArray(rows) || rows.length === 0) return null
    if (rows.every((r) => r && typeof r === 'object' && !Array.isArray(r))) {
      const objs = rows as Record<string, unknown>[]
      const headers = [...new Set(objs.flatMap((o) => Object.keys(o)))]
      if (headers.length === 0) return null
      return {
        headers,
        rows: objs.map((o) => headers.map((h) => {
          const v = o[h]
          if (v == null) return ''
          if (typeof v === 'object') return JSON.stringify(v)
          return String(v)
        })),
      }
    }
    if (rows.every((r) => Array.isArray(r))) {
      const matrix = rows as unknown[][]
      const width = Math.max(...matrix.map((r) => r.length), 0)
      if (width === 0) return null
      const headers = Array.from({ length: width }, (_, i) => `列${i + 1}`)
      return {
        headers,
        rows: matrix.map((r) =>
          headers.map((_, i) => (r[i] == null ? '' : String(r[i]))),
        ),
      }
    }
    return null
  }

  const tryJson = (text: string): ToolTable | null => {
    try {
      const data = JSON.parse(text) as unknown
      if (Array.isArray(data)) return asTable(data)
      if (data && typeof data === 'object') {
        const obj = data as Record<string, unknown>
        if (Array.isArray(obj.rows)) {
          const t = asTable(obj.rows)
          if (t && Array.isArray(obj.columns)) {
            const cols = (obj.columns as unknown[]).map(String)
            if (cols.length > 0) return { headers: cols, rows: t.rows }
          }
          return t
        }
        if (Array.isArray(obj.data)) return asTable(obj.data)
        if (Array.isArray(obj.result)) return asTable(obj.result)
      }
    } catch {
      // ignore
    }
    return null
  }

  const direct = tryJson(raw)
  if (direct) return direct

  const arrMatch = raw.match(/\[[\s\S]*\]/)
  if (arrMatch) {
    const nested = tryJson(arrMatch[0])
    if (nested) return nested
  }
  return null
}

function shortToolName(name: string): string {
  const m = name.match(/_(list_|describe_|execute_|sample_|read_)/i)
  if (m && m.index != null) return name.slice(m.index + 1)
  return name.length > 36 ? `…${name.slice(-32)}` : name
}

function toolOutputText(m: ChatSessionMessage): string {
  const out = m.toolOutput
  if (out && typeof out === 'object' && !Array.isArray(out) && 'text' in out) {
    const t = (out as { text?: unknown }).text
    if (t != null) return String(t)
  }
  return m.content || ''
}

function historyToolToUi(m: ChatSessionMessage): ToolCallUi {
  const content = toolOutputText(m)
  const err = m.toolError || undefined
  return {
    id: m.id,
    toolName: m.toolName || 'tool',
    phase: 'result',
    arguments:
      m.toolInput && typeof m.toolInput === 'object' && !Array.isArray(m.toolInput)
        ? (m.toolInput as Record<string, unknown>)
        : undefined,
    content,
    error: err,
    durationMs: m.toolDurationMs ?? undefined,
    table: err ? null : tryParseToolTable(content),
  }
}

function restoreSessionMessages(raw: ChatSessionMessage[]): Msg[] {
  const out: Msg[] = []
  let pendingTools: ToolCallUi[] = []

  const toMsg = (m: ChatSessionMessage): Msg => {
    const kbIds = (m.knowledgeBaseIds || []).filter(Boolean)
    const names = kbList.value.filter((b) => kbIds.includes(b.id)).map((b) => b.name)
    return {
      id: m.id,
      role: m.role as 'user' | 'assistant',
      content: m.content,
      images: (m.images || [])
        .filter((img) => Boolean(img.dataUrl))
        .map((img) => ({
          mimeType: img.mimeType || 'image/jpeg',
          dataUrl: img.dataUrl as string,
        })),
      attachments: (m.attachments || [])
        .filter((a) => Boolean(a?.fileName))
        .map((a) => ({
          fileName: a.fileName,
          downloadName: a.downloadName,
          kind: a.kind,
          label: a.label,
        })),
      refs: (m.refs as RefChunk[]) || [],
      refsReady: true,
      mode: (m.mode as Mode) || undefined,
      knowledgeBaseIds: kbIds,
      knowledgeBaseNames: names,
      useKnowledge:
        Boolean(m.useKnowledge) || kbIds.length > 0 || (m.refs?.length || 0) > 0,
    }
  }

  const attachPending = (msg: Msg) => {
    if (pendingTools.length) {
      msg.toolCalls = pendingTools
      pendingTools = []
    }
    return msg
  }

  for (const m of raw) {
    if (m.role === 'tool' || (m.toolName && m.role !== 'assistant' && m.role !== 'user')) {
      pendingTools.push(historyToolToUi(m))
      continue
    }
    if (m.role !== 'user' && m.role !== 'assistant') continue
    const mapped = toMsg(m)
    out.push(m.role === 'assistant' ? attachPending(mapped) : mapped)
  }
  if (pendingTools.length) {
    const lastAsst = [...out].reverse().find((x) => x.role === 'assistant')
    if (lastAsst) {
      lastAsst.toolCalls = [...(lastAsst.toolCalls || []), ...pendingTools]
    } else {
      out.push({
        id: genId(),
        role: 'assistant',
        content: '',
        refs: [],
        refsReady: true,
        toolCalls: pendingTools,
      })
    }
  }
  return out
}

const MAX_CHAT_IMAGES = 4
const IMAGE_ONLY_CAPTION = '请根据图片内容作答。'
const IMAGE_ACCEPT =
  'image/jpeg,image/png,image/webp,image/gif,image/jpg,application/pdf,.pdf,.dxf,.dwg'
const MAX_DRAFT_BYTES = 12 * 1024 * 1024
const DRAFT_NAME_RE = /\.(pdf|dxf|dwg)$/i
const WF_NODE_LABEL: Record<string, string> = {
  start: '开始',
  end: '结束',
  knowledge: '知识检索',
  llm: 'LLM',
  agent: '智能体',
  mcp: 'MCP',
  condition: '条件',
  vision: '读图',
  image_out: '出图',
  layout_out: '布置出图',
}

type PendingImage = {
  id: string
  mimeType: string
  data: string
  preview: string
  fileName?: string
}

interface Msg {
  id: string
  role: 'user' | 'assistant'
  content: string
  images?: { mimeType: string; dataUrl: string }[]
  attachments?: { fileName: string; downloadName?: string; kind?: string; label?: string }[]
  refs?: RefChunk[]
  refsReady?: boolean
  related?: string[]
  loading?: boolean
  mode?: Mode
  thinking?: boolean
  knowledgeBaseIds?: string[]
  knowledgeBaseNames?: string[]
  useKnowledge?: boolean
  toolCalls?: ToolCallUi[]
}

function tenderOriginals(refs: RefChunk[] | undefined): SlotFileInfo[] {
  const seen = new Set<string>()
  const out: SlotFileInfo[] = []
  for (const r of refs || []) {
    const id = String(r.doc_id || '').trim()
    const kb = String(r.kb_id || r.kbId || '').trim()
    if (!id || seen.has(id) || kb !== TENDER_LIB_ID) continue
    if (r.has_file === false) continue
    // 视频走阶段 5 播放器，不塞进投标原件缩略图条
    if (isKbVideoRef(r)) continue
    const kind =
      r.preview_kind === 'pdf' ? 'pdf' : r.preview_kind === 'file' ? 'file' : 'image'
    seen.add(id)
    out.push({
      id,
      name: r.name || '资料原件',
      sizeBytes: 0,
      fileType: r.file_type,
      kind,
    })
  }
  return out
}

function refTimeLabel(r: RefChunk): string {
  return fmtKbVideoRange(r.startMs, r.endMs)
}

function kbVideos(refs: RefChunk[] | undefined) {
  return collectKbVideoHits(refs)
}

function kbIdsFromMessages(
  messages: Array<{
    knowledgeBaseIds?: string[]
    refs?: RefChunk[] | ChatSessionMessage['refs']
  }>,
): string[] {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    const fromField = (m.knowledgeBaseIds || []).filter(Boolean)
    if (fromField.length > 0) return [...new Set(fromField)]
    const fromRefs = [
      ...new Set(
        (m.refs || [])
          .map((r) => (r as RefChunk).kb_id || (r as RefChunk).kbId)
          .filter((x): x is string => Boolean(x)),
      ),
    ]
    if (fromRefs.length > 0) return fromRefs
  }
  return []
}

const SUGGEST = [
  '结合当前知识库，帮我总结智能助手的使用方式。',
  '如何配置快速 / 深度模型与 Embedding？',
  '场景智能体如何绑定提示词、知识库和 MCP 工具？',
  '工作流里知识检索、LLM、智能体节点分别做什么？',
  '如何上传文档并检索验证知识库是否生效？',
]

function genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}

function userDisplayText(content: string, hasImages: boolean) {
  const text = (content || '').trim()
  if (hasImages && (!text || text === IMAGE_ONLY_CAPTION)) return ''
  return content
}

function loadImageElement(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve(img)
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('图片无法读取'))
    }
    img.src = url
  })
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const text = String(reader.result || '')
      const data = text.includes(',') ? text.split(',', 2)[1] : text
      if (!data) reject(new Error('图片读取失败'))
      else resolve(data)
    }
    reader.onerror = () => reject(new Error('图片读取失败'))
    reader.readAsDataURL(file)
  })
}

function chipPreview(label: string): string {
  const canvas = document.createElement('canvas')
  canvas.width = 144
  canvas.height = 144
  const ctx = canvas.getContext('2d')
  if (!ctx) return ''
  ctx.fillStyle = '#f3efe6'
  ctx.fillRect(0, 0, 144, 144)
  ctx.fillStyle = '#3f3a32'
  ctx.font = '16px sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(label, 72, 76)
  return canvas.toDataURL('image/png')
}

function draftMimeOf(file: File): string {
  const name = (file.name || '').toLowerCase()
  if (file.type === 'application/pdf' || name.endsWith('.pdf')) return 'application/pdf'
  if (name.endsWith('.dxf')) return 'application/dxf'
  if (name.endsWith('.dwg')) return 'application/acad'
  return file.type || 'application/octet-stream'
}

async function compressImageFile(file: File): Promise<PendingImage> {
  const name = file.name || ''
  const isDraft = DRAFT_NAME_RE.test(name) || file.type === 'application/pdf'
  if (isDraft) {
    if (file.size > MAX_DRAFT_BYTES) throw new Error('草稿文件不能超过 12 MB')
    const data = await fileToBase64(file)
    const ext = (name.split('.').pop() || 'pdf').toUpperCase()
    return {
      id: genId(),
      mimeType: draftMimeOf(file),
      data,
      preview: chipPreview(ext),
      fileName: name,
    }
  }
  if (!file.type.startsWith('image/')) {
    throw new Error('仅支持图片、PDF、DXF、DWG')
  }
  const img = await loadImageElement(file)
  const maxEdge = 2048
  const long = Math.max(img.width, img.height, 1)
  // 场地草稿多为细线图，JPEG 会糊掉红线；小 PNG 原样传。
  if (file.type === 'image/png' && long <= maxEdge && file.size <= 2.5 * 1024 * 1024) {
    const data = await fileToBase64(file)
    return {
      id: genId(),
      mimeType: 'image/png',
      data,
      preview: `data:image/png;base64,${data}`,
    }
  }
  const scale = Math.min(1, maxEdge / long)
  const w = Math.max(1, Math.round(img.width * scale))
  const h = Math.max(1, Math.round(img.height * scale))
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('无法压缩图片')
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, w, h)
  ctx.drawImage(img, 0, 0, w, h)
  const pngDraft = file.type === 'image/png' || file.type === 'image/webp'
  const dataUrl = pngDraft
    ? canvas.toDataURL('image/png')
    : canvas.toDataURL('image/jpeg', 0.92)
  const data = dataUrl.split(',', 2)[1] || ''
  if (!data) throw new Error('图片压缩失败')
  return {
    id: genId(),
    mimeType: pngDraft ? 'image/png' : 'image/jpeg',
    data,
    preview: dataUrl,
  }
}

function imagePayload(img: PendingImage): { mimeType: string; data: string; fileName?: string } {
  const fromField = (img.data || '').trim()
  const fromPreview = img.preview.includes(',') ? img.preview.split(',', 2)[1] : ''
  const data = fromField || fromPreview
  if (!data) throw new Error('图片数据为空，请重新选择图片')
  return {
    mimeType: img.mimeType || 'image/jpeg',
    data,
    ...(img.fileName ? { fileName: img.fileName } : {}),
  }
}

function fmtAgo(ts: number) {
  if (!ts) return ''
  const diff = Date.now() - ts
  const m = Math.floor(diff / 60_000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

const mode = ref<Mode>('fast')
const kbList = ref<KnowledgeBaseItem[]>([])
const selectedKbIds = ref<string[]>([])
const kbPickerOpen = ref(false)
const promptList = ref<PromptOption[]>([])
const selectedPromptId = ref<string | null>(null)
const promptPickerOpen = ref(false)
const llmOptions = ref<ModelOptionItem[]>([])
const selectedModelId = ref('')
const workflowOptions = ref<WorkflowOption[]>([])
const selectedWorkflowId = ref('')
const messages = ref<Msg[]>([])
const input = ref('')
const sending = ref(false)
const pendingImages = ref<PendingImage[]>([])
const imageInputRef = ref<HTMLInputElement | null>(null)
const sessions = ref<ChatSessionItem[]>([])
const activeSessionId = ref<string | null>(null)
const sessionsLoading = ref(true)
const creatingSession = ref(false)
const editingId = ref<string | null>(null)
const editTitle = ref('')
const pendingDelete = ref<ChatSessionItem | null>(null)
const deleting = ref(false)
const toast = ref<{ type: 'ok' | 'err'; msg: string } | null>(null)
const listRef = ref<HTMLDivElement | null>(null)
const kbPickerRef = ref<HTMLDivElement | null>(null)
const promptPickerRef = ref<HTMLDivElement | null>(null)
const activeAgent = ref<{ id: string; name: string } | null>(null)
const agentLoading = ref(false)
let abortController: AbortController | null = null
let toastTimer: ReturnType<typeof setTimeout> | null = null
let applyingAgentId: string | null = null

const route = useRoute()
const router = useRouter()

const useKnowledge = computed(() => selectedKbIds.value.length > 0)
const selectedKbNames = computed(() =>
  kbList.value.filter((b) => selectedKbIds.value.includes(b.id)).map((b) => b.name),
)
const selectedPromptName = computed(() => {
  if (!selectedPromptId.value) return null
  return (
    promptList.value.find((p) => p.id === selectedPromptId.value)?.name || '已选提示词'
  )
})
const usePrompt = computed(() => Boolean(selectedPromptId.value))
const agentBound = computed(() => Boolean(activeAgent.value))
const selectedModel = computed(
  () => llmOptions.value.find((m) => m.id === selectedModelId.value) || null,
)
const selectedWorkflow = computed(
  () => workflowOptions.value.find((w) => w.id === selectedWorkflowId.value) || null,
)
const workflowBound = computed(() => Boolean(selectedWorkflowId.value))
const canSend = computed(
  () => Boolean(input.value.trim() || pendingImages.value.length) && !sending.value,
)
const visionModelHint = computed(
  () =>
    !workflowBound.value &&
    pendingImages.value.length > 0 &&
    Boolean(selectedModel.value) &&
    selectedModel.value?.modelType !== 'multimodal_vision',
)

function pickDefaultModel(preferMode: Mode = mode.value) {
  const list = llmOptions.value
  if (list.length === 0) {
    selectedModelId.value = ''
    return
  }
  if (selectedModelId.value && list.some((m) => m.id === selectedModelId.value)) {
    return
  }
  const bound =
    preferMode === 'deep'
      ? list.find((m) => m.scopeDeep) || list.find((m) => m.scopeFast)
      : list.find((m) => m.scopeFast) || list.find((m) => m.scopeDeep)
  selectedModelId.value = (bound || list[0]).id
}
watch(messages, async () => {
  await nextTick()
  listRef.value?.scrollTo({
    top: listRef.value.scrollHeight,
    behavior: 'smooth',
  })
}, { deep: true })

watch(toast, (v) => {
  if (toastTimer) clearTimeout(toastTimer)
  if (!v) return
  toastTimer = setTimeout(() => {
    toast.value = null
  }, 3600)
})

function onDocClick(e: MouseEvent) {
  const target = e.target as Node
  if (kbPickerOpen.value && !kbPickerRef.value?.contains(target)) {
    kbPickerOpen.value = false
  }
  if (promptPickerOpen.value && !promptPickerRef.value?.contains(target)) {
    promptPickerOpen.value = false
  }
}

async function loadSessions() {
  sessionsLoading.value = true
  try {
    sessions.value = await listChatSessions()
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof ApiError || e instanceof Error ? e.message : '加载会话失败',
    }
  } finally {
    sessionsLoading.value = false
  }
}

function ensureDefaultPrompt() {
  if (activeAgent.value) return
  if (!selectedPromptId.value && promptList.value.length > 0) {
    selectedPromptId.value = promptList.value[0].id
  }
}

function clearAgentQuery() {
  if (!('agentId' in route.query)) return
  const next = { ...route.query }
  delete next.agentId
  void router.replace({ path: '/ai-chat', query: next })
}

function clearActiveAgent(opts?: { keepQuery?: boolean }) {
  activeAgent.value = null
  if (!opts?.keepQuery) clearAgentQuery()
  ensureDefaultPrompt()
}

function onWorkflowPicked() {
  if (selectedWorkflowId.value && activeAgent.value) {
    clearActiveAgent()
  }
}

async function applyAgentById(agentId: string) {
  const id = agentId.trim()
  if (!id) return
  if (applyingAgentId === id && activeAgent.value?.id === id) return
  applyingAgentId = id
  agentLoading.value = true
  try {
    const agent = await getAgent(id)
    activeAgent.value = { id: agent.id, name: agent.name }
    selectedWorkflowId.value = ''
    mode.value = agent.mode === 'deep' ? 'deep' : 'fast'
    selectedPromptId.value = agent.promptId || null
    selectedKbIds.value = [...(agent.knowledgeBaseIds || [])]
    messages.value = []
    pendingImages.value = []
    const item = await createChatSession({ mode: mode.value })
    sessions.value = [item, ...sessions.value.filter((s) => s.id !== item.id)]
    activeSessionId.value = item.id
    toast.value = { type: 'ok', msg: `已进入智能体「${agent.name}」` }
  } catch (e) {
    activeAgent.value = null
    clearAgentQuery()
    ensureDefaultPrompt()
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '加载智能体失败',
    }
  } finally {
    agentLoading.value = false
    applyingAgentId = null
  }
}

onMounted(() => {
  void (async () => {
    await loadSessions()
    try {
      kbList.value = await listKnowledgeBases({ access: 'use' })
      const allowed = new Set(kbList.value.map((b) => b.id))
      selectedKbIds.value = selectedKbIds.value.filter((id) => allowed.has(id))
      if (selectedKbIds.value.length === 0 && allowed.has('tenderlib01')) {
        selectedKbIds.value = ['tenderlib01']
      }
    } catch {
      // 未登录或接口失败时保持空列表
    }
    try {
      promptList.value = await listPromptOptions()
    } catch {
      // 未登录或接口失败时保持空列表
    }
    try {
      llmOptions.value = await listModelOptions({ kind: 'llm' })
      pickDefaultModel()
      if (llmOptions.value.length === 0) {
        toast.value = {
          type: 'err',
          msg: '暂无可用对话模型。请到「模型管理」新增并勾选启用，然后刷新本页。',
        }
      }
    } catch (e) {
      llmOptions.value = []
      toast.value = {
        type: 'err',
        msg: e instanceof Error ? e.message : '加载模型列表失败',
      }
    }
    try {
      workflowOptions.value = await listWorkflowOptions()
    } catch {
      workflowOptions.value = []
    }
    const qAgent =
      typeof route.query.agentId === 'string' ? route.query.agentId.trim() : ''
    if (qAgent) {
      await applyAgentById(qAgent)
    } else {
      ensureDefaultPrompt()
    }
  })()
  document.addEventListener('mousedown', onDocClick)
})

watch(
  () => route.query.agentId,
  (raw) => {
    const id = typeof raw === 'string' ? raw.trim() : ''
    if (!id) {
      if (activeAgent.value) activeAgent.value = null
      return
    }
    if (activeAgent.value?.id === id) return
    void applyAgentById(id)
  },
)

onUnmounted(() => {
  document.removeEventListener('mousedown', onDocClick)
  abortController?.abort()
  if (toastTimer) clearTimeout(toastTimer)
})

function toggleKb(id: string) {
  selectedKbIds.value = selectedKbIds.value.includes(id)
    ? selectedKbIds.value.filter((x) => x !== id)
    : [...selectedKbIds.value, id]
}

async function openSession(sessionId: string) {
  try {
    if (activeAgent.value) clearActiveAgent()
    const item = await getChatSession(sessionId)
    activeSessionId.value = item.id
    mode.value = (item.mode as Mode) === 'deep' ? 'deep' : 'fast'
    const restoredMsgs = restoreSessionMessages(item.messages || [])
    messages.value = restoredMsgs
    pendingImages.value = []
    const fromSession = (item.knowledgeBaseIds || []).filter(Boolean)
    const fromMsgs = kbIdsFromMessages(item.messages || [])
    const picked = fromSession.length > 0 ? fromSession : fromMsgs
    selectedKbIds.value = picked.filter((id) => kbList.value.some((b) => b.id === id))
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '打开会话失败',
    }
  }
}

async function handleCreateSession() {
  if (creatingSession.value || sending.value) return
  creatingSession.value = true
  try {
    const item = await createChatSession({ mode: mode.value })
    sessions.value = [item, ...sessions.value]
    activeSessionId.value = item.id
    messages.value = []
    pendingImages.value = []
    ensureDefaultPrompt()
    toast.value = { type: 'ok', msg: '已新建会话' }
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '新建失败',
    }
  } finally {
    creatingSession.value = false
  }
}

async function ensureSession(): Promise<string> {
  if (activeSessionId.value) return activeSessionId.value
  const item = await createChatSession({ mode: mode.value })
  sessions.value = [item, ...sessions.value]
  activeSessionId.value = item.id
  return item.id
}

function startRename(s: ChatSessionItem, e: Event) {
  e.stopPropagation()
  editingId.value = s.id
  editTitle.value = s.title
}

async function saveRename() {
  if (!editingId.value) return
  const title = editTitle.value.trim()
  if (!title) {
    toast.value = { type: 'err', msg: '标题不能为空' }
    return
  }
  try {
    const item = await updateChatSession(editingId.value, { title })
    sessions.value = sessions.value.map((x) => (x.id === item.id ? { ...x, ...item } : x))
    editingId.value = null
  } catch (e) {
    toast.value = { type: 'err', msg: e instanceof Error ? e.message : '重命名失败' }
  }
}

async function autoSummarize(s: ChatSessionItem, e: Event) {
  e.stopPropagation()
  try {
    const item = await summarizeChatSessionTitle(s.id)
    sessions.value = sessions.value.map((x) => (x.id === item.id ? { ...x, ...item } : x))
    toast.value = { type: 'ok', msg: `已总结为「${item.title}」` }
  } catch (err) {
    toast.value = {
      type: 'err',
      msg: err instanceof Error ? err.message : '自动总结失败',
    }
  }
}

async function confirmDelete() {
  if (!pendingDelete.value) return
  deleting.value = true
  try {
    await deleteChatSession(pendingDelete.value.id)
    sessions.value = sessions.value.filter((x) => x.id !== pendingDelete.value!.id)
    if (activeSessionId.value === pendingDelete.value.id) {
      activeSessionId.value = null
      messages.value = []
      pendingImages.value = []
    }
    pendingDelete.value = null
    toast.value = { type: 'ok', msg: '会话已删除' }
  } catch (e) {
    toast.value = { type: 'err', msg: e instanceof Error ? e.message : '删除失败' }
  } finally {
    deleting.value = false
  }
}

async function addImageFiles(files: File[] | FileList) {
  const remain = MAX_CHAT_IMAGES - pendingImages.value.length
  if (remain <= 0) {
    toast.value = { type: 'err', msg: `最多上传 ${MAX_CHAT_IMAGES} 个附件` }
    return
  }
  const picked = [...files].slice(0, remain)
  if ([...files].length > remain) {
    toast.value = { type: 'err', msg: `最多上传 ${MAX_CHAT_IMAGES} 个附件，已忽略多余文件` }
  }
  try {
    const next: PendingImage[] = []
    for (const file of picked) {
      next.push(await compressImageFile(file))
    }
    pendingImages.value = [...pendingImages.value, ...next]
  } catch (e) {
    toast.value = { type: 'err', msg: e instanceof Error ? e.message : '附件处理失败' }
  }
}

function onPickImages(e: Event) {
  const el = e.target as HTMLInputElement
  const files = el.files
  if (files && files.length) void addImageFiles(files)
  el.value = ''
}

function openImagePicker() {
  imageInputRef.value?.click()
}

function removePendingImage(id: string) {
  pendingImages.value = pendingImages.value.filter((p) => p.id !== id)
}

function onComposerPaste(e: ClipboardEvent) {
  const files = [...(e.clipboardData?.files || [])].filter(
    (f) =>
      f.type.startsWith('image/') ||
      f.type === 'application/pdf' ||
      DRAFT_NAME_RE.test(f.name || ''),
  )
  if (files.length === 0) return
  e.preventDefault()
  void addImageFiles(files)
}

async function sendQuestion(text: string) {
  const q = text.trim()
  const snapshot = pendingImages.value.map((p) => ({ ...p }))
  if ((!q && snapshot.length === 0) || sending.value) return
  input.value = ''
  pendingImages.value = []
  sending.value = true

  let sessionId: string
  try {
    sessionId = await ensureSession()
  } catch (e) {
    sending.value = false
    input.value = q
    pendingImages.value = snapshot
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '无法创建会话',
    }
    return
  }

  // 清理上次停止/异常可能残留的会话锁，避免立刻重问 Failed to fetch
  try {
    await cancelChatSession(sessionId)
  } catch {
    // ignore
  }

  const userMsg: Msg = {
    id: genId(),
    role: 'user',
    content: q,
    images: snapshot.map((p) => ({ mimeType: p.mimeType, dataUrl: p.preview })),
  }
  const assistantId = genId()
  const assistantMsg: Msg = {
    id: assistantId,
    role: 'assistant',
    content: '',
    refs: [],
    loading: true,
    mode: mode.value,
    thinking: !selectedWorkflowId.value && mode.value === 'deep',
    useKnowledge: selectedWorkflowId.value ? false : useKnowledge.value,
    knowledgeBaseIds: [...selectedKbIds.value],
    knowledgeBaseNames: [...selectedKbNames.value],
    toolCalls: [],
  }
  messages.value = [...messages.value, userMsg, assistantMsg]

  const controller = new AbortController()
  abortController = controller
  let accumulated = ''
  const toolCalls: ToolCallUi[] = []

  const patchAssistant = (patch: Partial<Msg>) => {
    messages.value = messages.value.map((m) =>
      m.id === assistantId ? { ...m, ...patch } : m,
    )
  }

  const applyTool = (payload: {
    phase?: string
    toolCallId?: string
    toolName?: string
    name?: string
    arguments?: Record<string, unknown>
    content?: string
    error?: string | null
    durationMs?: number
  }) => {
    const key = payload.toolCallId || payload.toolName || payload.name || genId()
    const name = payload.toolName || payload.name || 'tool'
    const idx = toolCalls.findIndex((t) => t.id === key)
    if (payload.phase === 'call') {
      const row: ToolCallUi = {
        id: key,
        toolName: name,
        phase: 'call',
        arguments: payload.arguments,
      }
      if (idx >= 0) toolCalls[idx] = row
      else toolCalls.push(row)
    } else {
      const content = payload.content
      const row: ToolCallUi = {
        id: key,
        toolName: name,
        phase: 'result',
        arguments: idx >= 0 ? toolCalls[idx].arguments : payload.arguments,
        content,
        error: payload.error,
        durationMs: payload.durationMs,
        table: payload.error ? null : tryParseToolTable(content),
      }
      if (idx >= 0) toolCalls[idx] = row
      else toolCalls.push(row)
    }
    patchAssistant({
      toolCalls: [...toolCalls],
      thinking: false,
      loading: true,
    })
  }

  const applyDoneSession = (payload: { title?: string }) => {
    sessions.value = sessions.value.map((s) =>
      s.id === sessionId
        ? {
            ...s,
            title: payload.title || s.title,
            updatedAt: Date.now(),
            lastMessageAt: Date.now(),
            messageCount: (s.messageCount || 0) + 2,
          }
        : s,
    )
  }

  try {
    if (selectedWorkflowId.value) {
      await runWorkflowTrial(
        selectedWorkflowId.value,
        {
          input: {
            query: q,
            images: snapshot.map((p) => imagePayload(p)),
            modelId: selectedModelId.value || undefined,
          },
          useDraft: false,
          sessionId,
          trigger: 'chat',
        },
        {
          onStepStart: (p) => {
            const id = `wf-${String(p.nodeId || genId())}`
            const name = WF_NODE_LABEL[String(p.nodeType || '')] || String(p.nodeType || '节点')
            const idx = toolCalls.findIndex((t) => t.id === id)
            const row: ToolCallUi = { id, toolName: name, phase: 'call' }
            if (idx >= 0) toolCalls[idx] = row
            else toolCalls.push(row)
            patchAssistant({ toolCalls: [...toolCalls], thinking: false, loading: true })
          },
          onStepEnd: (p) => {
            const id = `wf-${String(p.nodeId || '')}`
            const idx = toolCalls.findIndex((t) => t.id === id)
            const name = WF_NODE_LABEL[String(p.nodeType || '')] || String(p.nodeType || '节点')
            const failed = String(p.status || '') === 'failed'
            const detailObj = p.detail && typeof p.detail === 'object' ? p.detail : null
            const err =
              failed && detailObj
                ? String((detailObj as { error?: string }).error || '节点失败')
                : failed
                  ? '节点失败'
                  : null
            const content =
              !failed && detailObj ? JSON.stringify(detailObj).slice(0, 500) : undefined
            const row: ToolCallUi = {
              id: idx >= 0 ? toolCalls[idx].id : id || genId(),
              toolName: name,
              phase: 'result',
              content,
              error: err,
            }
            if (idx >= 0) toolCalls[idx] = { ...toolCalls[idx], ...row }
            else toolCalls.push(row)
            patchAssistant({ toolCalls: [...toolCalls], thinking: false, loading: true })
          },
          onTool: (p) => {
            applyTool({
              phase: String(p.phase || 'result') === 'call' ? 'call' : 'result',
              toolCallId: p.toolCallId as string | undefined,
              toolName: (p.toolName || p.name) as string | undefined,
              name: p.name as string | undefined,
              arguments: p.arguments as Record<string, unknown> | undefined,
              content: p.content as string | undefined,
              error: (p.error as string | null) ?? null,
              durationMs: p.durationMs as number | undefined,
            })
          },
          onDelta: (textDelta) => {
            if (!textDelta) return
            accumulated += textDelta
            patchAssistant({ content: accumulated, thinking: false, loading: true })
          },
          onError: (msg) => {
            accumulated += `\n\n⚠️ 调用失败：${msg}`
            patchAssistant({ content: accumulated, thinking: false })
          },
          onDone: (payload) => {
            const out = payload.output
            if (!accumulated.trim()) {
              if (typeof out === 'string') accumulated = out
              else if (out != null) accumulated = JSON.stringify(out)
            }
            const imgs = Array.isArray(payload.outputImages)
              ? (payload.outputImages as { mimeType?: string; dataUrl?: string }[])
                  .filter((x) => Boolean(x?.dataUrl))
                  .map((x) => ({
                    mimeType: x.mimeType || 'image/jpeg',
                    dataUrl: x.dataUrl as string,
                  }))
              : []
            const atts = Array.isArray(payload.layoutFiles)
              ? (payload.layoutFiles as {
                  fileName?: string
                  downloadName?: string
                  kind?: string
                  label?: string
                }[])
                  .filter((x) => Boolean(x?.fileName))
                  .map((x) => ({
                    fileName: x.fileName as string,
                    downloadName: x.downloadName,
                    kind: x.kind,
                    label: x.label,
                  }))
              : []
            patchAssistant({
              content: accumulated,
              images: imgs.length ? imgs : undefined,
              attachments: atts.length ? atts : undefined,
              thinking: false,
              loading: true,
            })
            applyDoneSession({ title: payload.title as string | undefined })
          },
        },
        controller.signal,
      )
    } else {
      await streamChat(
        {
          content: q,
          mode: mode.value,
          sessionId,
          knowledgeBaseIds: selectedKbIds.value,
          promptId: selectedPromptId.value,
          agentId: activeAgent.value?.id || null,
          modelId: selectedModelId.value || null,
          images: snapshot.map((p) => imagePayload(p)),
        },
        {
          onRefs: (chunks, meta) => {
            patchAssistant({
              refs: chunks as RefChunk[],
              refsReady: true,
              ...(typeof meta?.useKnowledge === 'boolean'
                ? { useKnowledge: meta.useKnowledge }
                : {}),
            })
          },
          onTool: (payload) => applyTool(payload),
          onDelta: (textDelta) => {
            if (!textDelta) return
            accumulated += textDelta
            patchAssistant({ content: accumulated, thinking: false, loading: true })
          },
          onError: (msg) => {
            accumulated += `\n\n⚠️ 调用失败：${msg}`
            patchAssistant({ content: accumulated, thinking: false })
          },
          onDone: (payload) => applyDoneSession(payload),
        },
        controller.signal,
      )
    }

    if (!accumulated.trim()) {
      try {
        const item = await getChatSession(sessionId)
        const lastAssistant = [...(item.messages || [])]
          .reverse()
          .find((m) => m.role === 'assistant')
        if (lastAssistant?.content || (lastAssistant?.images && lastAssistant.images.length)) {
          if (lastAssistant.content) accumulated = lastAssistant.content
          patchAssistant({
            content: accumulated || lastAssistant.content || '',
            thinking: false,
            loading: false,
            refs: (lastAssistant.refs as RefChunk[]) || undefined,
            images: (lastAssistant.images || [])
              .filter((img) => Boolean(img.dataUrl))
              .map((img) => ({
                mimeType: img.mimeType || 'image/jpeg',
                dataUrl: img.dataUrl as string,
              })),
          })
        }
      } catch {
        // ignore
      }
    }

    patchAssistant({ loading: false, thinking: false })

    if (accumulated.trim().length > 0 || (messages.value.find((m) => m.id === assistantId)?.images?.length)) {
      fetchRelatedQuestions({
        question: q || IMAGE_ONLY_CAPTION,
        answer: accumulated,
      })
        .then((questions) => {
          patchAssistant({ related: questions })
        })
        .catch(() => {})
    }
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') {
      const cur = messages.value.find((m) => m.id === assistantId)
      const base = (cur?.content || '').trim()
      patchAssistant({
        loading: false,
        thinking: false,
        content: base ? `${base}\n\n（已停止）` : '（已停止）',
      })
    } else {
      const cur = messages.value.find((m) => m.id === assistantId)
      const raw = e instanceof Error ? e.message : '未知错误'
      const friendly =
        raw === 'Failed to fetch' || /network|fetch/i.test(raw)
          ? '网络中断或服务正忙（若刚点过停止，请再试一次；持续失败请新建会话）'
          : raw
      patchAssistant({
        loading: false,
        thinking: false,
        content: `${cur?.content || ''}\n\n⚠️ 请求失败：${friendly}`,
      })
    }
  } finally {
    sending.value = false
    abortController = null
  }
}

async function stop() {
  const sid = activeSessionId.value
  const ctrl = abortController
  // 先通知后端释锁，再 abort，避免锁残留导致下一问 Failed to fetch
  if (sid) {
    try {
      await cancelChatSession(sid)
    } catch {
      // ignore：即使 cancel 失败也继续 abort
    }
  }
  ctrl?.abort()
  sending.value = false
}

function resetCurrent() {
  if (sending.value) return
  messages.value = []
  pendingImages.value = []
}
</script>

<template>
  <div class="h-full min-h-0 flex flex-col gap-3 overflow-hidden">
    <div class="flex flex-wrap items-end justify-between gap-3 shrink-0">
      <div>
        <h1 class="text-xl font-semibold flex items-center gap-2">
          <BotMessageSquare class="size-5 text-iron" />
          AI 智能问答
        </h1>
        <p class="mt-1 text-[12px] text-text-secondary">
          左侧管理历史会话：新建、改名、删除；首轮问答后可自动总结标题。
        </p>
      </div>
      <div class="flex items-center gap-2 flex-wrap justify-end">
        <div class="flex p-0.5 rounded-md border border-hairline bg-bg-base/60">
          <button
            type="button"
            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-[12px] transition-colors"
            :class="
              mode === 'fast'
                ? 'bg-iron text-white font-medium'
                : 'text-text-secondary hover:text-text-primary'
            "
            :disabled="agentBound"
            @click="mode = 'fast'"
          >
            <Zap class="size-3.5" /> 快速回答
          </button>
          <button
            type="button"
            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-[12px] transition-colors"
            :class="
              mode === 'deep'
                ? 'bg-molybdenum text-white font-medium'
                : 'text-text-secondary hover:text-text-primary'
            "
            :disabled="agentBound"
            @click="mode = 'deep'"
          >
            <Brain class="size-3.5" /> 深度推理
          </button>
        </div>

        <select
          v-model="selectedWorkflowId"
          class="h-[34px] max-w-[220px] rounded-md border border-hairline bg-bg-base/60 px-2 text-[12px] text-text-primary disabled:opacity-40"
          :disabled="sending"
          :title="
            workflowOptions.length
              ? '选择已发布工作流后，本轮走画布节点（读图 / 布置出图 / 工具循环）'
              : '暂无已发布工作流。请到左侧「工作流」新建并点击发布，刷新本页后会出现在此列表。'
          "
          @change="onWorkflowPicked"
        >
          <option value="">不走工作流</option>
          <option v-if="workflowOptions.length === 0" disabled value="__empty__">
            暂无已发布工作流（请到「工作流」发布）
          </option>
          <option v-for="w in workflowOptions" :key="w.id" :value="w.id">
            {{ w.name }}
          </option>
        </select>

        <select
          v-model="selectedModelId"
          class="h-[34px] max-w-[220px] rounded-md border border-hairline bg-bg-base/60 px-2 text-[12px] text-text-primary disabled:opacity-40"
          :disabled="sending || llmOptions.length === 0"
          :title="
            workflowBound
              ? '工作流节点未指定模型时，使用此处选择的对话模型（不要选文生图）'
              : llmOptions.length
                ? '本轮使用的对话模型'
                : '请先在模型管理中启用对话模型'
          "
        >
          <option v-if="llmOptions.length === 0" value="">暂无可用模型</option>
          <option v-for="m in llmOptions" :key="m.id" :value="m.id">
            {{ m.name }} · {{ modelTypeLabel(m.modelType, m.kind) }}
            {{ m.scopeFast ? ' · 默快' : '' }}{{ m.scopeDeep ? ' · 默深' : '' }}
          </option>
        </select>

        <div ref="promptPickerRef" class="relative">
          <button
            type="button"
            :title="
              agentBound
                ? '当前由场景智能体锁定提示词，清除智能体后可改'
                : usePrompt
                  ? `已选提示词「${selectedPromptName}」`
                  : '未选提示词 = 不向大模型传系统提示词'
            "
            :disabled="sending || agentBound"
            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-[12px] border transition-colors disabled:opacity-40"
            :class="
              usePrompt
                ? 'bg-molybdenum/15 text-molybdenum border-molybdenum/40 font-medium'
                : 'text-text-secondary border-hairline hover:text-text-primary bg-bg-base/60'
            "
            @click="promptPickerOpen = !promptPickerOpen"
          >
            <BookOpenText class="size-3.5" />
            {{ usePrompt ? selectedPromptName : '选择提示词' }}
          </button>
          <div
            v-if="promptPickerOpen"
            class="absolute right-0 top-full z-30 mt-1.5 w-72 rounded-lg border border-hairline bg-bg-elevated shadow-xl p-2"
          >
            <div class="px-1.5 pb-1.5 mb-1.5 border-b border-hairline text-[11px] text-text-muted">
              未选 = 不传系统提示词；仅可选已启用项
            </div>
            <div
              v-if="promptList.length === 0"
              class="px-2 py-4 text-center text-[11px] text-text-muted"
            >
              暂无可用提示词，请到「提示词管理」新增并启用
            </div>
            <div v-else class="max-h-56 overflow-y-auto space-y-0.5">
              <button
                v-for="p in promptList"
                :key="p.id"
                type="button"
                class="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-[12px] transition-colors"
                :class="
                  selectedPromptId === p.id
                    ? 'bg-molybdenum/15 text-molybdenum'
                    : 'text-text-secondary hover:bg-bg-base/60 hover:text-text-primary'
                "
                @click="selectedPromptId = p.id; promptPickerOpen = false"
              >
                <span
                  class="size-3.5 rounded-full border inline-flex items-center justify-center shrink-0"
                  :class="
                    selectedPromptId === p.id
                      ? 'border-molybdenum bg-molybdenum text-white'
                      : 'border-hairline'
                  "
                >
                  <Check v-if="selectedPromptId === p.id" class="size-2.5" />
                </span>
                <span class="truncate flex-1">{{ p.name }}</span>
              </button>
            </div>
            <button
              v-if="selectedPromptId"
              type="button"
              class="mt-1.5 w-full h-7 rounded-md text-[11px] text-text-muted hover:text-text-primary hover:bg-bg-base/50"
              @click="selectedPromptId = null; promptPickerOpen = false"
            >
              清空选择（不传提示词）
            </button>
          </div>
        </div>

        <div ref="kbPickerRef" class="relative">
          <button
            type="button"
            :title="
              agentBound
                ? '当前由场景智能体锁定知识库，清除智能体后可改'
                : useKnowledge
                  ? `已选 ${selectedKbNames.join('、')}，将仅在这些库中检索`
                  : '未选知识库 = 不检索；点击选择一个或多个库'
            "
            :disabled="sending || agentBound"
            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-[12px] border transition-colors disabled:opacity-40"
            :class="
              useKnowledge
                ? 'bg-patina/15 text-patina border-patina/40 font-medium'
                : 'text-text-secondary border-hairline hover:text-text-primary bg-bg-base/60'
            "
            @click="kbPickerOpen = !kbPickerOpen"
          >
            <LibraryBig class="size-3.5" />
            {{ useKnowledge ? `知识库 ${selectedKbIds.length}` : '选择知识库' }}
          </button>
          <div
            v-if="kbPickerOpen"
            class="absolute right-0 top-full z-30 mt-1.5 w-64 rounded-lg border border-hairline bg-bg-elevated shadow-xl p-2"
          >
            <div class="px-1.5 pb-1.5 mb-1.5 border-b border-hairline text-[11px] text-text-muted">
              未选 = 不检索；可多选。「投标资料库」含资质/合同 OCR 全文
            </div>
            <div
              v-if="kbList.length === 0"
              class="px-2 py-4 text-center text-[11px] text-text-muted"
            >
              暂无知识库，请先到「知识库」创建并导入资料
            </div>
            <div v-else class="max-h-56 overflow-y-auto space-y-0.5">
              <button
                v-for="b in kbList"
                :key="b.id"
                type="button"
                class="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-[12px] transition-colors"
                :class="
                  selectedKbIds.includes(b.id)
                    ? 'bg-patina/15 text-patina'
                    : 'text-text-secondary hover:bg-bg-base/60 hover:text-text-primary'
                "
                @click="toggleKb(b.id)"
              >
                <span
                  class="size-3.5 rounded border inline-flex items-center justify-center shrink-0"
                  :class="
                    selectedKbIds.includes(b.id)
                      ? 'border-patina bg-patina text-white'
                      : 'border-hairline'
                  "
                >
                  <Check v-if="selectedKbIds.includes(b.id)" class="size-2.5" />
                </span>
                <span class="truncate flex-1">{{ b.name }}</span>
                <span
                  v-if="b.id === 'tenderlib01' || b.purpose === 'asset'"
                  class="text-[10px] text-text-muted shrink-0"
                >
                  资料库
                </span>
                <span v-else class="text-[10px] font-mono text-text-muted shrink-0">
                  {{ b.docCount }}资料
                </span>
              </button>
            </div>
            <button
              v-if="selectedKbIds.length > 0"
              type="button"
              class="mt-1.5 w-full h-7 rounded-md text-[11px] text-text-muted hover:text-text-primary hover:bg-bg-base/50"
              @click="selectedKbIds = []"
            >
              清空选择（不检索）
            </button>
          </div>
        </div>

        <button
          type="button"
          :disabled="sending || messages.length === 0"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded text-[12px] text-text-secondary hover:text-text-primary border border-hairline disabled:opacity-40"
          @click="resetCurrent"
        >
          <RotateCcw class="size-3.5" /> 清空当前
        </button>
      </div>
    </div>

    <div
      v-if="selectedWorkflow"
      class="shrink-0 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-patina/35 bg-patina/10 px-3 py-2"
    >
      <div class="flex items-center gap-2 min-w-0 text-[12px] text-patina">
        <Sparkles class="size-3.5 shrink-0" />
        <span class="truncate">
          本轮走工作流：<strong class="font-medium">{{ selectedWorkflow.name }}</strong>
          <span class="text-text-muted ml-1.5">按已发布画布执行（条件 / 读图 / 布置出图 / 智能体）</span>
        </span>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-1 h-7 px-2 rounded-md text-[11px] border border-hairline text-text-secondary hover:text-text-primary hover:bg-bg-base/50"
        :disabled="sending"
        @click="selectedWorkflowId = ''"
      >
        <X class="size-3" />
        退出工作流
      </button>
    </div>

    <div
      v-if="agentLoading || activeAgent"
      class="shrink-0 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-molybdenum/35 bg-molybdenum/10 px-3 py-2"
    >
      <div class="flex items-center gap-2 min-w-0 text-[12px] text-molybdenum">
        <Loader2 v-if="agentLoading" class="size-3.5 animate-spin shrink-0" />
        <Bot v-else class="size-3.5 shrink-0" />
        <span class="truncate">
          <template v-if="agentLoading">正在载入场景智能体…</template>
          <template v-else>
            当前智能体：<strong class="font-medium">{{ activeAgent?.name }}</strong>
            <span class="text-text-muted ml-1.5">服务端将按该配置覆盖模式 / 提示词 / 知识库 / 工具</span>
          </template>
        </span>
      </div>
      <button
        v-if="activeAgent && !agentLoading"
        type="button"
        class="inline-flex items-center gap-1 h-7 px-2 rounded-md text-[11px] border border-hairline text-text-secondary hover:text-text-primary hover:bg-bg-base/50"
        @click="clearActiveAgent()"
      >
        <X class="size-3" />
        清除智能体
      </button>
    </div>

    <div class="flex-1 grid grid-cols-1 lg:grid-cols-[240px_1fr_280px] gap-4 min-h-0">
      <!-- 会话列表 -->
      <aside class="hidden lg:flex flex-col rounded-lg border border-hairline bg-bg-surface min-h-0">
        <div class="px-3 py-2.5 border-b border-hairline flex items-center justify-between gap-2">
          <div class="text-[12px] font-medium text-text-primary flex items-center gap-1.5">
            <MessageSquare class="size-3.5 text-molybdenum" />
            会话
          </div>
          <button
            type="button"
            :disabled="creatingSession"
            class="inline-flex items-center gap-1 h-7 px-2 rounded-md text-[11px] bg-iron/15 text-iron border border-iron/30 hover:bg-iron/25 disabled:opacity-50"
            @click="handleCreateSession()"
          >
            <Loader2 v-if="creatingSession" class="size-3 animate-spin" />
            <Plus v-else class="size-3" />
            新建
          </button>
        </div>
        <div class="flex-1 overflow-y-auto p-2 space-y-1">
          <div
            v-if="sessionsLoading"
            class="py-8 text-center text-[11px] text-text-muted"
          >
            <Loader2 class="inline size-3.5 animate-spin mr-1" />
            加载中…
          </div>
          <div
            v-else-if="sessions.length === 0"
            class="py-8 px-2 text-center text-[11px] text-text-muted leading-relaxed"
          >
            暂无会话。点击「新建」或直接提问自动创建。
          </div>
          <div
            v-for="s in sessions"
            v-else
            :key="s.id"
            class="group rounded-md border px-2.5 py-2 cursor-pointer transition-colors"
            :class="
              s.id === activeSessionId
                ? 'border-iron/40 bg-iron/10'
                : 'border-transparent hover:border-hairline hover:bg-bg-base/50'
            "
            @click="editingId !== s.id && openSession(s.id)"
          >
            <div
              v-if="editingId === s.id"
              class="flex items-center gap-1"
              @click.stop
            >
              <input
                v-model="editTitle"
                autofocus
                class="flex-1 h-7 px-1.5 rounded border border-molybdenum/50 bg-bg-base text-[11px] text-text-primary outline-none"
                @keydown.enter="saveRename()"
                @keydown.escape="editingId = null"
              />
              <button
                type="button"
                class="size-6 rounded text-patina hover:bg-patina/10 inline-flex items-center justify-center"
                @click="saveRename()"
              >
                <Check class="size-3.5" />
              </button>
              <button
                type="button"
                class="size-6 rounded text-text-muted hover:bg-hairline/40 inline-flex items-center justify-center"
                @click="editingId = null"
              >
                <X class="size-3.5" />
              </button>
            </div>
            <template v-else>
              <div class="flex items-start justify-between gap-1">
                <div class="text-[12px] text-text-primary line-clamp-2 leading-snug flex-1 min-w-0">
                  {{ s.title || '新对话' }}
                </div>
                <div class="flex items-center opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                  <button
                    type="button"
                    title="自动总结标题"
                    class="size-6 rounded text-text-muted hover:text-molybdenum hover:bg-molybdenum/10 inline-flex items-center justify-center"
                    @click="autoSummarize(s, $event)"
                  >
                    <Wand2 class="size-3" />
                  </button>
                  <button
                    type="button"
                    title="重命名"
                    class="size-6 rounded text-text-muted hover:text-molybdenum hover:bg-molybdenum/10 inline-flex items-center justify-center"
                    @click="startRename(s, $event)"
                  >
                    <Pencil class="size-3" />
                  </button>
                  <button
                    type="button"
                    title="删除"
                    class="size-6 rounded text-text-muted hover:text-iron hover:bg-iron/10 inline-flex items-center justify-center"
                    @click.stop="pendingDelete = s"
                  >
                    <Trash2 class="size-3" />
                  </button>
                </div>
              </div>
              <div class="mt-1 text-[10px] font-mono text-text-muted">
                {{ s.messageCount }} 条 · {{ fmtAgo(s.updatedAt || s.lastMessageAt) }}
              </div>
            </template>
          </div>
        </div>
      </aside>

      <!-- Chat 区 -->
      <div class="flex flex-col rounded-lg border border-hairline bg-bg-surface min-h-0">
        <div ref="listRef" class="flex-1 overflow-y-auto px-5 py-5 space-y-5">
          <!-- Welcome -->
          <div
            v-if="messages.length === 0"
            class="h-full flex flex-col items-center justify-center text-center py-10"
          >
            <div
              class="size-14 rounded-full overflow-hidden mb-4 shadow-[0_0_28px_var(--accent-glow)] ring-1 ring-black/5"
            >
              <img :src="weitaiLogo" :alt="ASSISTANT_NAME" class="size-full object-cover" />
            </div>
            <div class="text-[15px] font-semibold mb-1">{{ ASSISTANT_NAME }}</div>
            <div class="text-[12px] text-text-secondary max-w-md mb-5 leading-relaxed">
              <template v-if="usePrompt">
                已选提示词「{{ selectedPromptName }}」。
              </template>
              <template v-else>
                未选提示词 = 不向大模型传系统提示词。
              </template>
              <template v-if="useKnowledge">
                已选知识库「{{ selectedKbNames.join('」「') }}」：将仅在这些库中检索片段辅助回答。
              </template>
              <template v-else>
                未选知识库 = 不检索向量库。可在右上角选择提示词与知识库。
              </template>
              左侧可管理会话；直接提问会自动创建新会话。
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-2 w-full max-w-xl px-4">
              <button
                v-for="s in SUGGEST.slice(0, 4)"
                :key="s"
                type="button"
                class="text-left px-3 py-2.5 text-[12px] rounded-md border border-hairline bg-bg-base/40 hover:border-iron/50 hover:bg-iron/5 text-text-secondary hover:text-text-primary transition-colors"
                @click="sendQuestion(s)"
              >
                {{ s }}
              </button>
            </div>
          </div>

          <template v-for="m in messages" :key="m.id">
            <!-- User -->
            <div v-if="m.role === 'user'" class="flex justify-end gap-2.5">
              <div
                class="max-w-[85%] bg-molybdenum/10 border border-molybdenum/25 px-3.5 py-2.5 rounded-lg rounded-tr-sm text-[13px] leading-relaxed text-text-primary"
              >
                <div
                  v-if="m.images && m.images.length"
                  :class="userDisplayText(m.content, true) ? 'mb-2' : ''"
                >
                  <ChatImageGallery
                    :images="m.images"
                    filename-prefix="对话附图"
                    compact
                  />
                </div>
                <div
                  v-if="userDisplayText(m.content, Boolean(m.images?.length))"
                  class="whitespace-pre-wrap"
                >
                  {{ m.content }}
                </div>
              </div>
              <div
                class="size-8 rounded-md bg-molybdenum/15 border border-molybdenum/30 flex items-center justify-center shrink-0"
              >
                <UserIcon class="size-4 text-molybdenum" />
              </div>
            </div>

            <!-- Assistant -->
            <div v-else class="flex gap-2.5">
              <div class="size-8 shrink-0 overflow-hidden rounded-full ring-1 ring-black/5">
                <img :src="weitaiLogo" alt="助手" class="size-full object-cover" />
              </div>
              <div class="flex-1 max-w-[85%] space-y-3">
                <div class="bg-bg-base/50 border border-hairline px-4 py-3 rounded-lg rounded-tl-sm">
                  <div class="flex items-center gap-1.5 mb-2 flex-wrap">
                    <span
                      class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono border"
                      :class="
                        m.mode === 'deep'
                          ? 'text-molybdenum border-molybdenum/40 bg-molybdenum/10'
                          : 'text-iron border-iron/40 bg-iron/10'
                      "
                    >
                      <Brain v-if="m.mode === 'deep'" class="size-3" />
                      <Zap v-else class="size-3" />
                      {{ m.mode === 'deep' ? 'DEEP' : 'FAST' }}
                    </span>
                    <span
                      v-if="m.refs && m.refs.length > 0"
                      class="text-[10px] text-text-muted font-mono"
                    >
                      · 已检索 {{ m.refs.length }} 条参考片段
                    </span>
                    <span
                      v-else-if="m.useKnowledge && (m.refsReady || !m.loading) && !(m.refs && m.refs.length)"
                      class="text-[10px] text-iron font-mono"
                    >
                      · 知识库未检索到
                    </span>
                    <span v-if="m.useKnowledge === false" class="text-[10px] text-text-muted">
                      · 未用知识库
                    </span>
                    <span
                      v-if="m.useKnowledge && m.knowledgeBaseNames?.length"
                      class="text-[10px] text-patina truncate max-w-[220px]"
                      :title="m.knowledgeBaseNames.join('、')"
                    >
                      · {{ m.knowledgeBaseNames.join('、') }}
                    </span>
                  </div>

                  <div
                    v-if="m.thinking && !m.content && !(m.toolCalls && m.toolCalls.length)"
                    class="flex items-center gap-2 text-[12px] text-text-secondary"
                  >
                    <Loader2 class="size-3.5 animate-spin text-molybdenum" />
                    {{
                      m.useKnowledge
                        ? '正在检索知识库并深度推理...'
                        : '正在深度推理...'
                    }}
                  </div>
                  <div
                    v-else-if="!m.thinking && m.loading && !m.content && !(m.toolCalls && m.toolCalls.length)"
                    class="flex items-center gap-2 text-[12px] text-text-secondary"
                  >
                    <Loader2 class="size-3.5 animate-spin text-iron" />
                    {{
                      m.useKnowledge
                        ? '正在检索知识库并生成回答...'
                        : '正在生成回答...'
                    }}
                  </div>

                  <div
                    v-if="m.toolCalls && m.toolCalls.length"
                    class="mb-3 space-y-1.5"
                  >
                    <details
                      v-for="tc in m.toolCalls"
                      :key="`${tc.id}-${tc.phase}`"
                      class="rounded-md border border-hairline bg-bg-surface/60 px-2.5 py-2 text-[11px] group"
                      v-bind="tc.phase === 'call' ? { open: true } : {}"
                    >
                      <summary
                        class="flex items-center gap-1.5 text-text-secondary cursor-pointer list-none select-none [&::-webkit-details-marker]:hidden"
                      >
                        <Wrench class="size-3 text-molybdenum shrink-0" />
                        <span class="font-mono text-text-primary truncate" :title="tc.toolName">
                          {{ shortToolName(tc.toolName) }}
                        </span>
                        <Loader2
                          v-if="tc.phase === 'call'"
                          class="size-3 animate-spin text-molybdenum"
                        />
                        <span
                          v-else-if="tc.error"
                          class="text-iron"
                        >失败</span>
                        <span
                          v-else
                          class="text-patina"
                        >完成{{ tc.durationMs != null ? ` · ${tc.durationMs}ms` : '' }}</span>
                        <span class="ml-auto text-[10px] text-text-muted opacity-70">
                          <span class="group-open:hidden">展开</span>
                          <span class="hidden group-open:inline">收起</span>
                        </span>
                      </summary>
                      <div
                        v-if="tc.error"
                        class="mt-1.5 text-iron whitespace-pre-wrap"
                      >
                        {{ tc.error }}
                      </div>
                      <template v-else-if="tc.content">
                        <div
                          v-if="tc.table"
                          class="mt-1.5 overflow-x-auto rounded border border-hairline"
                        >
                          <table class="w-full border-collapse text-[11px] min-w-[280px]">
                            <thead>
                              <tr>
                                <th
                                  v-for="h in tc.table.headers"
                                  :key="h"
                                  class="text-left px-2 py-1.5 font-semibold text-molybdenum bg-molybdenum/10 border-b border-hairline whitespace-nowrap"
                                >
                                  {{ h }}
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              <tr
                                v-for="(row, ri) in tc.table.rows"
                                :key="ri"
                                class="odd:bg-transparent even:bg-foreground/[0.025]"
                              >
                                <td
                                  v-for="(cell, ci) in row"
                                  :key="ci"
                                  class="px-2 py-1.5 align-top text-text-primary border-b border-hairline/70 max-w-[220px] truncate"
                                  :title="cell"
                                >
                                  {{ cell }}
                                </td>
                              </tr>
                            </tbody>
                          </table>
                        </div>
                        <pre
                          v-else
                          class="mt-1.5 max-h-40 overflow-auto text-text-muted whitespace-pre-wrap font-mono text-[10.5px] leading-snug"
                        >{{ tc.content }}</pre>
                      </template>
                    </details>
                  </div>

                  <div v-if="m.images && m.images.length" class="mb-2">
                    <ChatImageGallery
                      :images="m.images"
                      filename-prefix="充电站平面布置图"
                    />
                  </div>
                  <div v-if="tenderOriginals(m.refs).length" class="mb-2">
                    <ChatKbOriginals :files="tenderOriginals(m.refs)" />
                  </div>
                  <template v-for="vids in [kbVideos(m.refs)]" :key="`vid-${m.id}`">
                    <div v-if="vids.length" class="mb-2">
                      <ChatKbVideos :videos="vids" />
                    </div>
                  </template>
                  <LayoutFileDownloads
                    v-if="m.attachments && m.attachments.length"
                    :files="m.attachments"
                  />
                  <ChatMarkdown
                    v-if="m.content"
                    :content="m.content"
                    :streaming="Boolean(m.loading)"
                  />
                </div>

                <details
                  v-if="m.refs && m.refs.length > 0"
                  class="text-[11.5px]"
                >
                  <summary
                    class="cursor-pointer text-text-secondary hover:text-text-primary flex items-center gap-1.5 select-none"
                  >
                    <Quote class="size-3.5" />
                    查看 {{ m.refs.length }} 条知识库参考片段
                  </summary>
                  <div class="mt-2 space-y-1.5 pl-4 border-l-2 border-hairline">
                    <div
                      v-for="(r, i) in m.refs"
                      :key="i"
                      class="px-2.5 py-1.5 rounded bg-bg-base/40 border border-hairline"
                    >
                      <div class="flex justify-between gap-2 text-[10px] text-text-muted font-mono mb-0.5">
                        <span class="truncate min-w-0 inline-flex items-center gap-1" :title="r.name || undefined">
                          <Film
                            v-if="isKbVideoRef(r)"
                            class="size-3 shrink-0 text-iron"
                          />
                          #{{ i + 1 }}{{ r.name ? ` · ${r.name}` : '' }}
                          <span
                            v-if="isKbVideoRef(r)"
                            class="shrink-0 rounded px-1 py-px text-[9px] bg-iron/10 text-iron border border-iron/20"
                          >视频</span>
                          <span
                            v-if="refTimeLabel(r)"
                            class="shrink-0 text-text-muted"
                          >{{ refTimeLabel(r) }}</span>
                        </span>
                        <span class="text-molybdenum shrink-0">
                          相似度 {{ (r.score ?? 0).toFixed(3) }}
                        </span>
                      </div>
                      <div
                        class="text-text-secondary leading-snug line-clamp-3 whitespace-pre-wrap"
                      >
                        {{ r.content }}
                      </div>
                    </div>
                  </div>
                </details>
                <div
                  v-else-if="m.useKnowledge && (m.refsReady || !m.loading) && !(m.refs && m.refs.length)"
                  class="text-[11.5px] text-iron flex items-center gap-1.5"
                >
                  <Quote class="size-3.5" />
                  当前未从知识库中检索出您想要的信息
                </div>

                <div
                  v-if="m.related && m.related.length > 0"
                  class="rounded-lg border border-sulfur/30 bg-sulfur/[0.04] px-3.5 py-3"
                >
                  <div class="flex items-center gap-1.5 text-[11px] text-sulfur mb-2">
                    <Lightbulb class="size-3.5" /> 可能想继续了解
                  </div>
                  <div class="space-y-1.5">
                    <button
                      v-for="q in m.related"
                      :key="q"
                      type="button"
                      class="block w-full text-left text-[12px] leading-snug px-2.5 py-1.5 rounded border border-hairline hover:border-sulfur/50 hover:text-text-primary text-text-secondary bg-bg-base/40 transition-colors"
                      @click="sendQuestion(q)"
                    >
                      → {{ q }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </div>

        <div class="border-t border-hairline px-4 py-3 bg-bg-elevated/40 shrink-0">
          <input
            ref="imageInputRef"
            type="file"
            class="hidden"
            :accept="IMAGE_ACCEPT"
            multiple
            @change="onPickImages"
          />
          <div
            v-if="pendingImages.length"
            class="mb-2 flex flex-wrap gap-2"
          >
            <div
              v-for="img in pendingImages"
              :key="img.id"
              class="relative size-[72px] rounded-md border border-hairline overflow-hidden bg-bg-base/60"
            >
              <img :src="img.preview" alt="" class="size-full object-cover" />
              <button
                type="button"
                class="absolute top-0.5 right-0.5 size-5 rounded bg-black/55 text-white flex items-center justify-center hover:bg-black/75"
                :disabled="sending"
                title="移除图片"
                @click="removePendingImage(img.id)"
              >
                <X class="size-3" />
              </button>
            </div>
          </div>
          <div class="flex gap-2 items-end">
            <button
              type="button"
              class="self-stretch px-2.5 rounded-md border border-hairline text-text-secondary hover:text-iron hover:border-iron/50 disabled:opacity-40"
              :disabled="sending || pendingImages.length >= MAX_CHAT_IMAGES"
              title="上传场地草稿（图片 / PDF / DXF / DWG）"
              @click="openImagePicker"
            >
              <ImagePlus class="size-4" />
            </button>
            <textarea
              v-model="input"
              rows="2"
              :placeholder="
                pendingImages.length
                  ? '已附图：可补充问题，或不填直接发送；PDF/CAD 会按草稿读图'
                  : mode === 'deep'
                    ? '深度推理模式：适合复杂工艺诊断、对标分析、合规论证...'
                    : '快速回答模式：参数查询、操作要点、术语解释...'
              "
              class="flex-1 resize-none bg-bg-base/60 border border-hairline rounded-md px-3 py-2 text-[13px] text-text-primary placeholder:text-text-muted focus:outline-none focus:border-molybdenum/70"
              :disabled="sending"
              @keydown.enter.exact.prevent="sendQuestion(input)"
              @paste="onComposerPaste"
            />
            <button
              v-if="sending"
              type="button"
              class="self-stretch px-4 rounded-md border border-iron/60 text-iron text-[12px] hover:bg-iron/10"
              @click="stop"
            >
              停止
            </button>
            <button
              v-else
              type="button"
              :disabled="!canSend"
              class="self-stretch flex items-center gap-1.5 px-5 rounded-md bg-iron text-white text-[13px] font-medium hover:brightness-110 disabled:opacity-50"
              @click="sendQuestion(input)"
            >
              <Send class="size-4" />
              发送
            </button>
          </div>
          <div class="mt-2 flex justify-between text-[10.5px] text-text-muted">
            <span>
              <CornerDownLeft class="inline size-3" /> Enter 发送 · Shift+Enter 换行 · 可粘贴图片
              <template v-if="pendingImages.length">
                · 已选 {{ pendingImages.length }}/{{ MAX_CHAT_IMAGES }} 张
              </template>
            </span>
            <span>
              {{ activeSessionId ? '已关联会话' : '发送后自动新建会话' }} ·
              <span :class="mode === 'deep' ? 'text-molybdenum' : 'text-iron'">
                {{ mode === 'deep' ? '深度推理' : '快速回答' }}
              </span>
              ·
              <span :class="selectedWorkflow ? 'text-patina' : selectedModel ? 'text-text-primary' : 'text-text-muted'">
                {{
                  selectedWorkflow
                    ? `工作流：${selectedWorkflow.name}`
                    : selectedModel
                      ? `模型：${selectedModel.name}`
                      : '未选模型'
                }}
              </span>
              ·
              <span :class="usePrompt ? 'text-molybdenum' : 'text-text-muted'">
                {{ usePrompt ? `提示词：${selectedPromptName}` : '未选提示词' }}
              </span>
              ·
              <span :class="useKnowledge ? 'text-patina' : 'text-text-muted'">
                {{
                  useKnowledge
                    ? `知识库：${selectedKbNames.join('、')}`
                    : '未选知识库'
                }}
              </span>
            </span>
          </div>
          <div v-if="visionModelHint" class="mt-1.5 text-[10.5px] text-iron">
            当前模型未标记为「多模态视觉」。若网关不支持识图，请到模型管理改类型或换模型后再发送。
          </div>
        </div>
      </div>

      <aside class="hidden lg:flex flex-col gap-3 min-h-0 overflow-hidden">
        <div class="rounded-lg border border-hairline bg-bg-surface p-4 shrink-0">
          <div class="flex items-center gap-1.5 text-[12px] font-medium text-text-primary mb-2">
            <Sparkles class="size-3.5 text-iron" /> 两种模式差异
          </div>
          <div class="space-y-2 text-[11.5px] leading-relaxed">
            <div class="flex gap-2">
              <Zap class="size-3.5 text-iron mt-0.5 shrink-0" />
              <div>
                <div class="text-text-primary">快速回答</div>
                <div class="text-text-secondary">
                  Lite 模型，毫秒级响应；适合参数查询、术语解释、操作要点。
                </div>
              </div>
            </div>
            <div class="flex gap-2">
              <Brain class="size-3.5 text-molybdenum mt-0.5 shrink-0" />
              <div>
                <div class="text-text-primary">深度推理</div>
                <div class="text-text-secondary">
                  Pro 模型 + thinking 模式；适合工艺诊断、对标分析、合规论证。
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="rounded-lg border border-hairline bg-bg-surface p-4 flex-1 min-h-0 overflow-y-auto">
          <div class="flex items-center gap-1.5 text-[12px] font-medium text-text-primary mb-2">
            <Lightbulb class="size-3.5 text-sulfur" /> 推荐问题
          </div>
          <div class="space-y-2">
            <button
              v-for="q in SUGGEST"
              :key="q"
              type="button"
              :disabled="sending"
              class="block w-full text-left px-2.5 py-2 text-[11.5px] rounded-md border border-hairline bg-bg-base/40 hover:border-molybdenum/60 hover:text-text-primary text-text-secondary leading-snug transition-colors"
              @click="sendQuestion(q)"
            >
              {{ q }}
            </button>
          </div>
        </div>
      </aside>
    </div>

    <!-- Delete confirm modal -->
    <div
      v-if="pendingDelete"
      class="fixed inset-0 z-[60] bg-bg-base/80 backdrop-blur-sm flex items-center justify-center p-4"
      @click.self="!deleting && (pendingDelete = null)"
    >
      <div class="w-full max-w-md rounded-lg border border-hairline bg-bg-elevated shadow-2xl overflow-hidden">
        <div class="px-5 pt-5 pb-3">
          <div class="text-[14px] font-medium text-text-primary">删除会话</div>
          <div class="mt-1.5 text-[12px] text-text-secondary">
            确认删除「
            <span class="text-text-primary font-medium">{{ pendingDelete.title }}</span>
            」？历史消息将一并删除。
          </div>
        </div>
        <div class="px-5 py-3 border-t border-hairline bg-bg-base/30 flex justify-end gap-2">
          <button
            type="button"
            :disabled="deleting"
            class="h-8 px-3 text-[12px] rounded-md border border-hairline bg-transparent text-text-secondary hover:bg-hairline/40"
            @click="pendingDelete = null"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="deleting"
            class="h-8 px-3 text-[12px] rounded-md bg-iron text-white hover:brightness-110 inline-flex items-center gap-1.5"
            @click="confirmDelete()"
          >
            <Loader2 v-if="deleting" class="size-3.5 animate-spin" />
            <Trash2 v-else class="size-3.5" />
            确认删除
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="toast"
      class="fixed bottom-6 right-6 z-[80] px-4 py-2.5 rounded-md text-[12px] shadow-lg border"
      :class="
        toast.type === 'ok'
          ? 'bg-patina/10 border-patina/40 text-patina'
          : 'bg-iron/10 border-iron/40 text-iron'
      "
    >
      {{ toast.msg }}
    </div>
  </div>
</template>

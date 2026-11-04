<script setup lang="ts">
import { computed, markRaw, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { VueFlow, type Connection } from '@vue-flow/core'

/** 避免直接用 Vue Flow 的 Node/Edge 泛型：vue-tsc 会报 TS2589（实例化过深）。 */
type CanvasNode = {
  id: string
  type?: string
  position: { x: number; y: number }
  label?: string
  data?: Record<string, unknown>
  class?: string
}

type CanvasEdge = {
  id: string
  source: string
  target: string
  sourceHandle?: string
  targetHandle?: string
  label?: string
}
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import {
  ArrowLeft,
  Check,
  Loader2,
  Pencil,
  Play,
  Plus,
  Save,
  Trash2,
  Upload,
  X,
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { listAgents, type AgentItem } from '@/lib/agents-api'
import { listKnowledgeBases, type KnowledgeBaseItem } from '@/lib/knowledge-api'
import { listPrompts, type PromptItem } from '@/lib/prompts-api'
import { listMcpServers, type McpServerItem } from '@/lib/mcp-api'
import { listModelOptions, type ModelOptionItem } from '@/lib/models-api'
import WfConditionNode from '@/components/workflows/WfConditionNode.vue'
import ChatImageGallery from '@/components/ai/ChatImageGallery.vue'
import {
  getWorkflow,
  publishWorkflow,
  runWorkflowTrial,
  saveWorkflowGraph,
  updateWorkflow,
  type WorkflowGraph,
  type WorkflowItem,
  type WorkflowNodeType,
} from '@/lib/workflows-api'
import { LAYOUT_LLM_SYSTEM_PROMPT } from '@/lib/layout-plan'

// vue-tsc 对 Vue Flow NodeComponent 泛型过深，与本仓库 CanvasNode 策略一致
const nodeTypes = { condition: markRaw(WfConditionNode) } as Record<string, object>

const NODE_META: Record<
  WorkflowNodeType,
  { label: string; tone: string }
> = {
  start: { label: '开始', tone: 'bg-patina/20 border-patina/40' },
  end: { label: '结束', tone: 'bg-muted/40 border-border' },
  knowledge: { label: '知识检索', tone: 'bg-coolant/15 border-coolant/40' },
  llm: { label: 'LLM', tone: 'bg-molybdenum/15 border-molybdenum/40' },
  vision: { label: '读图', tone: 'bg-patina/15 border-patina/40' },
  agent: { label: '智能体（工具循环）', tone: 'bg-iron/15 border-iron/40' },
  mcp: { label: 'MCP 单次调用', tone: 'bg-sulfur/15 border-sulfur/40' },
  image_out: { label: '出图', tone: 'bg-iron/10 border-iron/35' },
  layout_out: { label: '布置出图', tone: 'bg-patina/15 border-patina/40' },
  condition: { label: '条件', tone: 'bg-sulfur/20 border-sulfur/45' },
}

const PALETTE: WorkflowNodeType[] = [
  'knowledge',
  'llm',
  'vision',
  'agent',
  'mcp',
  'image_out',
  'layout_out',
  'condition',
]

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const workflowId = computed(() => String(route.params.id || ''))

const showGate = computed(() => auth.loading || (!auth.isAdmin && !auth.loading))

const item = ref<WorkflowItem | null>(null)
const nodes = ref<CanvasNode[]>([])
const edges = ref<CanvasEdge[]>([])
const selectedId = ref<string | null>(null)
const selectedEdgeId = ref<string | null>(null)
const loading = ref(true)
const saving = ref(false)
const publishing = ref(false)
const running = ref(false)
const error = ref('')
const toast = ref('')
const nameEdit = ref('')
const nameEditing = ref(false)
const nameSaving = ref(false)
const nameInputRef = ref<HTMLInputElement | null>(null)
const trialInput = ref(
  '{"inventoryGuid":"F77529AB-5A23-4628-BF0D-663F30A4EB34","query":"生成同型号最优良率实践文档"}',
)
const runLog = ref<string[]>([])
const runOutput = ref('')

const prompts = ref<PromptItem[]>([])
const kbs = ref<KnowledgeBaseItem[]>([])
const agents = ref<AgentItem[]>([])
const mcpServers = ref<McpServerItem[]>([])
const llmModels = ref<ModelOptionItem[]>([])
const visionModels = ref<ModelOptionItem[]>([])
const runImages = ref<{ mimeType?: string; dataUrl: string }[]>([])

const selected = computed(() => {
  const id = selectedId.value
  if (!id) return null
  return nodes.value.find((n) => n.id === id) ?? null
})

const selectedNodeType = computed(() => {
  const t = (selected.value?.data as { nodeType?: string } | undefined)?.nodeType
  return (t || '') as WorkflowNodeType | ''
})

const canDeleteSelected = computed(() => {
  if (!selected.value) return false
  return selectedNodeType.value !== 'start' && selectedNodeType.value !== 'end'
})

const toolOptions = computed(() => {
  const out: { id: string; label: string }[] = []
  for (const s of mcpServers.value) {
    for (const t of s.tools || []) {
      if (!t.enabled) continue
      out.push({ id: t.id, label: `${s.name} / ${t.name}` })
    }
  }
  return out
})

const toolsByServer = computed(() =>
  mcpServers.value
    .map((s) => ({
      id: s.id,
      name: s.name,
      tools: (s.tools || []).filter((t) => t.enabled),
    }))
    .filter((s) => s.tools.length > 0),
)

function selectedMcpToolIds(): string[] {
  const raw = (selected.value?.data as { mcpToolIds?: string[] } | undefined)?.mcpToolIds
  return Array.isArray(raw) ? raw.filter(Boolean) : []
}

function toggleAgentTool(id: string) {
  const cur = selectedMcpToolIds()
  const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]
  updateSelectedData('mcpToolIds', next)
}

function toggleServerTools(serverId: string, on: boolean) {
  const s = toolsByServer.value.find((x) => x.id === serverId)
  if (!s) return
  const ids = s.tools.map((t) => t.id)
  const cur = new Set(selectedMcpToolIds())
  if (on) ids.forEach((id) => cur.add(id))
  else ids.forEach((id) => cur.delete(id))
  updateSelectedData('mcpToolIds', [...cur])
}

function edgeBranchLabel(handle?: string | null): string | undefined {
  const h = (handle || '').toLowerCase()
  if (h === 'yes' || h === 'true') return '是'
  if (h === 'no' || h === 'false') return '否'
  return undefined
}

function onConnect(c: Connection) {
  if (!c.source || !c.target) return
  const srcNode = nodes.value.find((n) => n.id === c.source)
  const srcType = String((srcNode?.data as { nodeType?: string } | undefined)?.nodeType || '')
  const handle = c.sourceHandle || undefined
  if (srcType === 'condition' && handle) {
    edges.value = edges.value.filter(
      (e) => !(e.source === c.source && (e.sourceHandle || '') === handle),
    )
  } else if (srcType !== 'condition') {
    edges.value = edges.value.filter((e) => e.source !== c.source)
  }
  if (
    edges.value.some(
      (e) =>
        e.source === c.source &&
        e.target === c.target &&
        (e.sourceHandle || '') === (handle || ''),
    )
  ) {
    return
  }
  const id = `e_${c.source}_${c.target}_${Date.now()}`
  edges.value = [
    ...edges.value,
    {
      id,
      source: c.source,
      target: c.target,
      sourceHandle: handle,
      targetHandle: c.targetHandle || undefined,
      label: edgeBranchLabel(handle),
    },
  ]
}

function toFlowNodes(graph: WorkflowGraph): CanvasNode[] {
  return (graph.nodes || []).map((n) => ({
    id: n.id,
    type: n.type === 'condition' ? 'condition' : 'default',
    position: n.position || { x: 100, y: 100 },
    label: NODE_META[n.type as WorkflowNodeType]?.label || n.type,
    data: {
      nodeType: n.type,
      ...(n.data || {}),
    },
    class: NODE_META[n.type as WorkflowNodeType]?.tone || '',
  }))
}

function toFlowEdges(graph: WorkflowGraph): CanvasEdge[] {
  return (graph.edges || []).map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.sourceHandle || undefined,
    targetHandle: e.targetHandle || undefined,
    label: edgeBranchLabel(e.sourceHandle),
  }))
}

function exportGraph(): WorkflowGraph {
  return {
    nodes: nodes.value.map((n) => ({
      id: n.id,
      type: String((n.data as { nodeType?: string })?.nodeType || n.type || 'llm'),
      position: { ...n.position },
      data: (() => {
        const d = { ...(n.data as Record<string, unknown>) }
        delete d.nodeType
        return d
      })(),
    })),
    edges: edges.value.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle,
      targetHandle: e.targetHandle,
    })),
  }
}

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    const [wf, p, kb, ag, mcp, models] = await Promise.all([
      getWorkflow(workflowId.value),
      listPrompts().catch(() => [] as PromptItem[]),
      listKnowledgeBases({ access: 'use' }).catch(() => [] as KnowledgeBaseItem[]),
      listAgents().catch(() => [] as AgentItem[]),
      listMcpServers().catch(() => [] as McpServerItem[]),
      listModelOptions({ kind: 'llm' }).catch(() => [] as ModelOptionItem[]),
    ])
    item.value = wf
    nameEdit.value = wf.name
    prompts.value = p
    kbs.value = kb
    agents.value = ag.filter((a) => a.enabled)
    mcpServers.value = mcp
    llmModels.value = models
    visionModels.value = models.filter((m) => m.modelType === 'multimodal_vision')
    const graph = wf.draftVersion?.graph || { nodes: [], edges: [] }
    nodes.value = toFlowNodes(graph)
    edges.value = toFlowEdges(graph)
    selectedId.value = null
    selectedEdgeId.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function onNodeClick(ev: { node: { id: string } }) {
  selectedId.value = ev.node.id
  selectedEdgeId.value = null
}

function onEdgeClick(ev: { edge: { id: string } }) {
  selectedEdgeId.value = ev.edge.id
  selectedId.value = null
}

function onPaneClick() {
  selectedId.value = null
  selectedEdgeId.value = null
}

function removeSelectedNode() {
  const n = selected.value
  if (!n) return
  const ntype = String((n.data as { nodeType?: string })?.nodeType || '')
  if (ntype === 'start' || ntype === 'end') {
    error.value = '开始 / 结束节点不可删除'
    return
  }
  const id = n.id
  nodes.value = nodes.value.filter((x) => x.id !== id)
  edges.value = edges.value.filter((e) => e.source !== id && e.target !== id)
  selectedId.value = null
  error.value = ''
}

function removeSelectedEdge() {
  if (!selectedEdgeId.value) return
  const id = selectedEdgeId.value
  edges.value = edges.value.filter((e) => e.id !== id)
  selectedEdgeId.value = null
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (target.isContentEditable) return true
  return Boolean(target.closest('input, textarea, select, [contenteditable="true"]'))
}

function onEditorKeydown(e: KeyboardEvent) {
  if (e.key !== 'Delete' && e.key !== 'Backspace') return
  if (nameEditing.value || isTypingTarget(e.target)) return
  if (selectedId.value) {
    e.preventDefault()
    removeSelectedNode()
    return
  }
  if (selectedEdgeId.value) {
    e.preventDefault()
    removeSelectedEdge()
  }
}

function onArgsChange(raw: string) {
  try {
    updateSelectedData('arguments', JSON.parse(raw || '{}'))
  } catch {
    updateSelectedData('arguments', raw)
  }
}

function addNode(type: WorkflowNodeType) {
  const id = `${type}_${Date.now().toString(36)}`
  const position = { x: 180 + (nodes.value.length % 3) * 40, y: 100 + nodes.value.length * 48 }
  const extra: Record<string, unknown> =
    type === 'llm'
      ? { attachImages: true }
      : type === 'vision'
        ? { prompt: '请提取图中的尺寸、参数与要点，用简洁中文列出。' }
        : type === 'image_out'
          ? { mode: 'generate', prompt: '根据以下说明生成配图：\n{{output}}' }
          : type === 'layout_out'
            ? { jsonSource: '{{output}}', render: true, copyToOutput: true }
            : type === 'knowledge'
              ? { topK: 3 }
          : type === 'condition'
            ? { when: 'hasImages' }
            : {}
  nodes.value = [
    ...nodes.value,
    {
      id,
      type: type === 'condition' ? 'condition' : 'default',
      position,
      label: NODE_META[type].label,
      data: { nodeType: type, ...extra },
      class: NODE_META[type].tone,
    },
  ]
  selectedId.value = id
}

function updateSelectedData(key: string, value: unknown) {
  const n = selected.value
  if (!n) return
  nodes.value = nodes.value.map((x) =>
    x.id === n.id
      ? {
          ...x,
          data: { ...x.data, [key]: value },
        }
      : x,
  )
}

function patchSelectedData(patch: Record<string, unknown>) {
  const n = selected.value
  if (!n) return
  nodes.value = nodes.value.map((x) =>
    x.id === n.id
      ? {
          ...x,
          data: { ...x.data, ...patch },
        }
      : x,
  )
}

const llmPromptText = computed(() => {
  const d = selected.value?.data as
    | { nodeType?: string; systemPrompt?: string; promptId?: string }
    | undefined
  if (!d || d.nodeType !== 'llm') return ''
  const inline = (d.systemPrompt || '').toString()
  if (inline.trim()) return inline
  const pid = (d.promptId || '').toString()
  if (!pid) return ''
  return prompts.value.find((p) => p.id === pid)?.content || ''
})

function onLlmPromptInput(raw: string) {
  patchSelectedData({ systemPrompt: raw, promptId: null })
}

function fillPromptFromTemplate(id: string) {
  if (!id) return
  if (id === '__layout_json__') {
    patchSelectedData({
      systemPrompt: LAYOUT_LLM_SYSTEM_PROMPT,
      promptId: null,
    })
    return
  }
  const p = prompts.value.find((x) => x.id === id)
  patchSelectedData({
    systemPrompt: p?.content || '',
    promptId: null,
  })
}

function toggleKb(id: string) {
  const cur = ((selected.value?.data as { knowledgeBaseIds?: string[] })?.knowledgeBaseIds ||
    []) as string[]
  const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]
  updateSelectedData('knowledgeBaseIds', next)
}

async function startRename() {
  if (loading.value || nameSaving.value) return
  nameEdit.value = item.value?.name || nameEdit.value
  nameEditing.value = true
  await nextTick()
  nameInputRef.value?.focus()
  nameInputRef.value?.select()
}

function cancelRename() {
  nameEdit.value = item.value?.name || ''
  nameEditing.value = false
}

async function commitRename() {
  const next = nameEdit.value.trim()
  if (!next) {
    error.value = '工作流名称不能为空'
    return
  }
  if (next === item.value?.name) {
    nameEditing.value = false
    return
  }
  nameSaving.value = true
  error.value = ''
  try {
    item.value = await updateWorkflow(workflowId.value, { name: next })
    nameEdit.value = item.value.name
    nameEditing.value = false
    toast.value = '名称已更新'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '重命名失败'
  } finally {
    nameSaving.value = false
  }
}

async function onSave() {
  saving.value = true
  error.value = ''
  toast.value = ''
  try {
    if (nameEditing.value) {
      await commitRename()
    } else if (nameEdit.value.trim() && nameEdit.value !== item.value?.name) {
      item.value = await updateWorkflow(workflowId.value, { name: nameEdit.value.trim() })
    }
    item.value = await saveWorkflowGraph(workflowId.value, exportGraph())
    toast.value = '草稿已保存'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function onPublish() {
  publishing.value = true
  error.value = ''
  try {
    await onSave()
    item.value = await publishWorkflow(workflowId.value, 'publish from editor')
    const graph = item.value.draftVersion?.graph
    if (graph) {
      nodes.value = toFlowNodes(graph)
      edges.value = toFlowEdges(graph)
    }
    toast.value = `已发布 v${item.value.publishedVersion?.version ?? ''}`
  } catch (e) {
    error.value = e instanceof Error ? e.message : '发布失败'
  } finally {
    publishing.value = false
  }
}

async function onTrial() {
  running.value = true
  error.value = ''
  runLog.value = []
  runOutput.value = ''
  runImages.value = []
  try {
    await saveWorkflowGraph(workflowId.value, exportGraph())
    let input: unknown = trialInput.value
    const trimmed = trialInput.value.trim()
    if (trimmed.startsWith('{')) {
      try {
        input = JSON.parse(trimmed)
      } catch {
        input = trialInput.value
      }
    }
    await runWorkflowTrial(
      workflowId.value,
      { input, useDraft: true },
      {
        onStepStart: (p) => {
          runLog.value.push(`▶ ${p.nodeType || ''} (${p.nodeId || ''})`)
        },
        onTool: (p) => {
          const name = String(p.toolName || p.name || 'tool')
          if (p.phase === 'call') runLog.value.push(`  ⚙ ${name}`)
          else runLog.value.push(`  ⚙ ${name} ${p.error ? '失败' : '完成'}`)
        },
        onDelta: (text) => {
          if (!text) return
          runOutput.value += text
        },
        onStepEnd: (p) => {
          runLog.value.push(`✓ ${p.nodeType || ''} 完成`)
          if (p.detail) {
            try {
              runLog.value.push(`  ${JSON.stringify(p.detail).slice(0, 200)}`)
            } catch {
              /* ignore */
            }
          }
        },
        onDone: (p) => {
          const out = p.output as { text?: string } | string | undefined
          if (typeof out === 'string' && out) runOutput.value = out
          else if (out && typeof out === 'object')
            runOutput.value = String(out.text || JSON.stringify(out))
          const imgs = p.outputImages
          if (Array.isArray(imgs)) {
            runImages.value = imgs.filter(
              (x): x is { mimeType?: string; dataUrl: string } =>
                Boolean(
                  x &&
                    typeof x === 'object' &&
                    typeof (x as { dataUrl?: string }).dataUrl === 'string',
                ),
            )
          }
          runLog.value.push('■ 运行完成')
        },
        onError: (msg) => {
          error.value = msg
          runLog.value.push(`✗ ${msg}`)
        },
      },
    )
  } catch (e) {
    error.value = e instanceof Error ? e.message : '试跑失败'
  } finally {
    running.value = false
  }
}

watch(
  () => workflowId.value,
  () => {
    if (auth.isAdmin && workflowId.value) void loadAll()
  },
)

onMounted(async () => {
  window.addEventListener('keydown', onEditorKeydown)
  if (auth.isAdmin) {
    await loadAll()
    await nextTick()
  }
})

onUnmounted(() => {
  window.removeEventListener('keydown', onEditorKeydown)
})
</script>

<template>
  <div v-if="showGate" class="p-8 text-sm text-muted-foreground">
    <span v-if="auth.loading">加载中…</span>
    <span v-else>仅管理员可编辑工作流。</span>
  </div>

  <div v-else class="flex flex-col h-[calc(100vh-7rem)] min-h-[560px]">
    <div class="flex flex-wrap items-center gap-3 mb-3">
      <button
        type="button"
        class="h-9 px-2.5 inline-flex items-center gap-1 text-xs rounded-md border border-border hover:bg-accent shrink-0"
        @click="router.push('/workflows')"
      >
        <ArrowLeft class="size-3.5" />返回
      </button>

      <div class="min-w-0 flex-1 flex flex-col gap-0.5">
        <div class="text-[10px] uppercase tracking-wide text-muted-foreground">工作流名称</div>
        <div v-if="nameEditing" class="flex items-center gap-1.5 min-w-0">
          <input
            ref="nameInputRef"
            v-model="nameEdit"
            maxlength="128"
            placeholder="输入工作流名称"
            class="h-9 px-2.5 text-[15px] font-semibold rounded-md border border-molybdenum/50 bg-background text-foreground outline-none focus:ring-2 focus:ring-molybdenum/30 min-w-[220px] max-w-full w-[min(420px,100%)]"
            :disabled="nameSaving"
            @keydown.enter.prevent="commitRename()"
            @keydown.escape.prevent="cancelRename()"
          />
          <button
            type="button"
            title="确认"
            class="size-9 rounded-md border border-patina/40 text-patina hover:bg-patina/10 inline-flex items-center justify-center disabled:opacity-50"
            :disabled="nameSaving"
            @click="commitRename()"
          >
            <Loader2 v-if="nameSaving" class="size-3.5 animate-spin" />
            <Check v-else class="size-4" />
          </button>
          <button
            type="button"
            title="取消"
            class="size-9 rounded-md border border-border text-muted-foreground hover:bg-accent inline-flex items-center justify-center"
            :disabled="nameSaving"
            @click="cancelRename()"
          >
            <X class="size-4" />
          </button>
        </div>
        <button
          v-else
          type="button"
          class="group inline-flex items-center gap-2 max-w-full text-left rounded-md px-1 -mx-1 py-0.5 hover:bg-accent/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-molybdenum/40"
          title="点击修改名称"
          :disabled="loading"
          @click="startRename()"
        >
          <span class="text-[15px] font-semibold text-foreground truncate">
            {{ item?.name || nameEdit || '未命名工作流' }}
          </span>
          <span
            class="inline-flex items-center gap-1 shrink-0 text-[11px] text-molybdenum opacity-80 group-hover:opacity-100"
          >
            <Pencil class="size-3.5" />
            重命名
          </span>
        </button>
        <div class="text-[10px] text-muted-foreground">
          草稿 v{{ item?.draftVersion?.version ?? '—' }}
          · 已发布
          {{ item?.publishedVersion ? `v${item.publishedVersion.version}` : '无' }}
        </div>
      </div>

      <div class="flex items-center gap-2 shrink-0">
        <button
          type="button"
          class="h-9 px-3 inline-flex items-center gap-1.5 text-xs rounded-md border border-border hover:bg-accent disabled:opacity-50"
          :disabled="saving || loading"
          @click="onSave"
        >
          <Loader2 v-if="saving" class="size-3.5 animate-spin" />
          <Save v-else class="size-3.5" />
          保存草稿
        </button>
        <button
          type="button"
          class="h-9 px-3 inline-flex items-center gap-1.5 text-xs rounded-md border border-molybdenum/40 text-molybdenum hover:bg-molybdenum/10 disabled:opacity-50"
          :disabled="publishing || loading"
          @click="onPublish"
        >
          <Upload class="size-3.5" />发布
        </button>
      </div>
    </div>

    <p v-if="error" class="mb-2 text-xs text-iron">{{ error }}</p>
    <p v-if="toast" class="mb-2 text-xs text-patina">{{ toast }}</p>

    <div v-if="loading" class="text-xs text-muted-foreground inline-flex items-center gap-1.5">
      <Loader2 class="size-3.5 animate-spin" />加载画布…
    </div>

    <div v-else class="flex-1 grid grid-cols-12 gap-3 min-h-0">
      <!-- palette -->
      <aside class="col-span-2 rounded-md border border-border bg-card/40 p-2 space-y-1.5 overflow-auto">
        <div class="text-[10px] text-muted-foreground px-1 mb-1">添加节点</div>
        <button
          v-for="t in PALETTE"
          :key="t"
          type="button"
          class="w-full text-left text-xs px-2 py-1.5 rounded-md border border-border hover:border-iron/40"
          @click="addNode(t)"
        >
          <Plus class="size-3 inline mr-1" />{{ NODE_META[t].label }}
        </button>
        <p class="text-[10px] text-muted-foreground px-1 pt-2 leading-relaxed">
          从开始连到结束。条件节点从「是 / 否」两个锚点分别连出。选中中间节点后可删除（或按 Delete）。
        </p>
      </aside>

      <!-- canvas -->
      <div class="col-span-6 rounded-md border border-border overflow-hidden bg-background">
        <VueFlow
          v-model:nodes="nodes"
          v-model:edges="edges"
          :node-types="nodeTypes"
          fit-view-on-init
          :default-viewport="{ zoom: 1 }"
          :delete-key-code="null"
          @node-click="onNodeClick"
          @edge-click="onEdgeClick"
          @pane-click="onPaneClick"
          @connect="onConnect"
        >
          <Background />
          <Controls />
        </VueFlow>
      </div>

      <!-- inspector + trial -->
      <aside class="col-span-4 flex flex-col gap-3 min-h-0 overflow-hidden">
        <div class="rounded-md border border-border p-3 overflow-auto max-h-[45%]">
          <div class="flex items-center justify-between gap-2 mb-2">
            <div class="text-xs font-medium">节点属性</div>
            <button
              v-if="selected && canDeleteSelected"
              type="button"
              class="h-7 px-2 inline-flex items-center gap-1 text-[11px] rounded-md border border-iron/40 text-iron hover:bg-iron/10"
              title="删除该节点（Delete）"
              @click="removeSelectedNode()"
            >
              <Trash2 class="size-3" />
              删除节点
            </button>
          </div>
          <div
            v-if="!selected && selectedEdgeId"
            class="space-y-2 text-xs"
          >
            <p class="text-[11px] text-muted-foreground">已选中一条连线。</p>
            <button
              type="button"
              class="h-7 px-2 inline-flex items-center gap-1 text-[11px] rounded-md border border-iron/40 text-iron hover:bg-iron/10"
              title="删除连线（Delete）"
              @click="removeSelectedEdge()"
            >
              <Trash2 class="size-3" />
              删除连线
            </button>
          </div>
          <div
            v-else-if="!selected"
            class="text-[11px] text-muted-foreground"
          >
            点击画布节点进行配置；选中后可删除（开始/结束除外）。
          </div>
          <div v-else class="space-y-2 text-xs">
            <div>
              类型：
              <span class="font-medium">
                {{ NODE_META[(selected.data as any).nodeType as WorkflowNodeType]?.label || (selected.data as any).nodeType }}
              </span>
            </div>
            <p
              v-if="!canDeleteSelected"
              class="text-[10px] text-muted-foreground"
            >
              开始 / 结束节点为流程锚点，不可删除。
            </p>

            <template v-if="(selected.data as any).nodeType === 'knowledge'">
              <label class="block text-[11px] text-muted-foreground">知识库</label>
              <div class="max-h-28 overflow-auto space-y-1 border border-border rounded-md p-1.5">
                <label
                  v-for="kb in kbs"
                  :key="kb.id"
                  class="flex items-center gap-1.5 text-[11px]"
                >
                  <input
                    type="checkbox"
                    :checked="((selected.data as any).knowledgeBaseIds || []).includes(kb.id)"
                    @change="toggleKb(kb.id)"
                  />
                  {{ kb.name }}
                </label>
              </div>
              <label class="block text-[11px] text-muted-foreground">召回案例数（整篇，建议 2～3）</label>
              <input
                type="number"
                class="w-full h-7 px-2 rounded-md border border-border bg-background"
                :value="(selected.data as any).topK || 3"
                min="1"
                max="8"
                @change="updateSelectedData('topK', Number(($event.target as HTMLInputElement).value) || 3)"
              />
            </template>

            <template v-else-if="(selected.data as any).nodeType === 'llm'">
              <label class="block text-[11px] text-muted-foreground">模式</label>
              <select
                class="w-full h-7 px-2 rounded-md border border-border bg-background"
                :value="(selected.data as any).mode || 'fast'"
                @change="updateSelectedData('mode', ($event.target as HTMLSelectElement).value)"
              >
                <option value="fast">fast</option>
                <option value="deep">deep</option>
              </select>
              <label class="block text-[11px] text-muted-foreground">模型（空=按模式默认）</label>
              <select
                class="w-full h-7 px-2 rounded-md border border-border bg-background"
                :value="(selected.data as any).modelId || ''"
                @change="updateSelectedData('modelId', ($event.target as HTMLSelectElement).value || null)"
              >
                <option value="">（默认）</option>
                <option v-for="m in llmModels" :key="m.id" :value="m.id">
                  {{ m.name }} · {{ m.modelName }}
                </option>
              </select>
              <label class="flex items-center gap-1.5 text-[11px]">
                <input
                  type="checkbox"
                  :checked="(selected.data as any).attachImages !== false"
                  @change="updateSelectedData('attachImages', ($event.target as HTMLInputElement).checked)"
                />
                附带输入图片（image_url）
              </label>
              <label class="flex items-center gap-1.5 text-[11px]">
                <input
                  type="checkbox"
                  :checked="Boolean((selected.data as any).lockPrompt)"
                  @change="updateSelectedData('lockPrompt', ($event.target as HTMLInputElement).checked)"
                />
                锁定本节点提示词（禁止被布置模板覆盖）
              </label>
              <label class="block text-[11px] text-muted-foreground">写入变量（可选，如 constraints）</label>
              <input
                class="w-full h-7 px-2 rounded-md border border-border bg-background"
                :value="(selected.data as any).saveAs || ''"
                placeholder="空=只写 output"
                @change="updateSelectedData('saveAs', ($event.target as HTMLInputElement).value.trim() || null)"
              />
              <label class="block text-[11px] text-muted-foreground">用户消息模板（可选）</label>
              <textarea
                class="w-full min-h-[64px] px-2 py-1 rounded-md border border-border bg-background text-[12px] leading-relaxed"
                :value="(selected.data as any).userPrompt || ''"
                placeholder="{{query}} {{vision}} {{constraints}} {{context}}"
                @input="updateSelectedData('userPrompt', ($event.target as HTMLTextAreaElement).value)"
              />
              <label class="block text-[11px] text-muted-foreground">提示词</label>
              <textarea
                class="w-full min-h-[120px] px-2 py-1.5 rounded-md border border-border bg-background text-[12px] leading-relaxed"
                :value="llmPromptText"
                placeholder="直接在此编写本节点的系统提示词，保存草稿后随图一起发布。"
                @input="onLlmPromptInput(($event.target as HTMLTextAreaElement).value)"
              />
              <label class="block text-[11px] text-muted-foreground">
                从模板填入（可选，会覆盖上面的文本）
              </label>
              <select
                class="w-full h-7 px-2 rounded-md border border-border bg-background"
                value=""
                @change="
                  fillPromptFromTemplate(($event.target as HTMLSelectElement).value);
                  ($event.target as HTMLSelectElement).value = ''
                "
              >
                <option value="">选择模板填入…</option>
                <option value="__layout_json__">布置 JSON 契约</option>
                <option v-for="p in prompts" :key="p.id" :value="p.id">{{ p.name }}</option>
              </select>
            </template>

            <template v-else-if="(selected.data as any).nodeType === 'agent'">
              <p class="text-[10px] text-muted-foreground leading-relaxed">
                由模型按需多次调用工具（画 CAD、查库走这里）。不要把多步绘图拆成多个「MCP 单次调用」。
              </p>
              <label class="block text-[11px] text-muted-foreground">场景智能体（可选）</label>
              <select
                class="w-full h-7 px-2 rounded-md border border-border bg-background"
                :value="(selected.data as any).agentId || ''"
                @change="updateSelectedData('agentId', ($event.target as HTMLSelectElement).value || null)"
              >
                <option value="">不选，仅用下方勾选的工具</option>
                <option v-for="a in agents" :key="a.id" :value="a.id">{{ a.name }}</option>
              </select>
              <label class="block text-[11px] text-muted-foreground">本节点可调用的 MCP 工具</label>
              <div class="max-h-40 overflow-auto space-y-2 border border-border rounded-md p-1.5">
                <div v-if="toolsByServer.length === 0" class="text-[10px] text-muted-foreground px-1">
                  暂无已启用工具，请先到 MCP 管理同步。
                </div>
                <div v-for="s in toolsByServer" :key="s.id" class="space-y-1">
                  <label class="flex items-center gap-1.5 text-[11px] font-medium">
                    <input
                      type="checkbox"
                      :checked="s.tools.every((t) => selectedMcpToolIds().includes(t.id))"
                      @change="toggleServerTools(s.id, ($event.target as HTMLInputElement).checked)"
                    />
                    {{ s.name }}
                  </label>
                  <label
                    v-for="t in s.tools"
                    :key="t.id"
                    class="flex items-center gap-1.5 text-[11px] pl-4"
                  >
                    <input
                      type="checkbox"
                      :checked="selectedMcpToolIds().includes(t.id)"
                      @change="toggleAgentTool(t.id)"
                    />
                    {{ t.name }}
                  </label>
                </div>
              </div>
              <p class="text-[10px] text-muted-foreground">
                画 CAD 请勾选 CAD-MCP 下绘图/保存等工具；模型会自己决定调用顺序和次数。勾选后优先用本列表。
              </p>
              <label class="block text-[11px] text-muted-foreground">覆盖模型（可选）</label>
              <select
                class="w-full h-7 px-2 rounded-md border border-border bg-background"
                :value="(selected.data as any).modelId || ''"
                @change="updateSelectedData('modelId', ($event.target as HTMLSelectElement).value || null)"
              >
                <option value="">（智能体或默认）</option>
                <option v-for="m in llmModels" :key="m.id" :value="m.id">
                  {{ m.name }} · {{ m.modelName }}
                </option>
              </select>
              <label class="flex items-center gap-1.5 text-[11px]">
                <input
                  type="checkbox"
                  :checked="(selected.data as any).attachImages !== false"
                  @change="updateSelectedData('attachImages', ($event.target as HTMLInputElement).checked)"
                />
                附带输入图片
              </label>
              <label class="block text-[11px] text-muted-foreground">附加系统提示（可选）</label>
              <textarea
                class="w-full min-h-[72px] px-2 py-1 rounded-md border border-border bg-background text-[12px]"
                :value="(selected.data as any).systemPrompt || ''"
                placeholder="例如：根据读图结果调用 CAD 工具画图，成功后再 save_drawing。"
                @input="updateSelectedData('systemPrompt', ($event.target as HTMLTextAreaElement).value)"
              />
            </template>

            <template v-else-if="(selected.data as any).nodeType === 'condition'">
              <label class="block text-[11px] text-muted-foreground">条件</label>
              <select
                class="w-full h-7 px-2 rounded-md border border-border bg-background"
                :value="(selected.data as any).when || 'hasImages'"
                @change="updateSelectedData('when', ($event.target as HTMLSelectElement).value)"
              >
                <option value="hasImages">有输入图片</option>
                <option value="needImage">文本像是需要出图</option>
                <option value="outputContains">输出包含关键字</option>
                <option value="toolFailed">上一工具失败</option>
              </select>
              <template v-if="(selected.data as any).when === 'outputContains'">
                <label class="block text-[11px] text-muted-foreground">关键字</label>
                <input
                  class="w-full h-7 px-2 rounded-md border border-border bg-background"
                  :value="(selected.data as any).contains || ''"
                  @change="updateSelectedData('contains', ($event.target as HTMLInputElement).value)"
                />
              </template>
              <p class="text-[10px] text-muted-foreground">
                从节点底部「是」连成立分支，「否」连失败/否则分支。
              </p>
            </template>

            <template v-else-if="(selected.data as any).nodeType === 'vision'">
              <label class="block text-[11px] text-muted-foreground">视觉模型</label>
              <select
                class="w-full h-7 px-2 rounded-md border border-border bg-background"
                :value="(selected.data as any).modelId || ''"
                @change="updateSelectedData('modelId', ($event.target as HTMLSelectElement).value || null)"
              >
                <option value="">（默认对话模型）</option>
                <option v-for="m in (visionModels.length ? visionModels : llmModels)" :key="m.id" :value="m.id">
                  {{ m.name }} · {{ m.modelName }}
                </option>
              </select>
              <label class="block text-[11px] text-muted-foreground">读图提示</label>
              <textarea
                class="w-full min-h-[72px] px-2 py-1 rounded-md border border-border bg-background"
                :value="(selected.data as any).prompt || ''"
                @change="updateSelectedData('prompt', ($event.target as HTMLTextAreaElement).value)"
              />
              <label class="flex items-center gap-1.5 text-[11px]">
                <input
                  type="checkbox"
                  :checked="Boolean((selected.data as any).copyToOutput)"
                  @change="updateSelectedData('copyToOutput', ($event.target as HTMLInputElement).checked)"
                />
                同时写入 output
              </label>
            </template>

            <template v-else-if="(selected.data as any).nodeType === 'image_out'">
              <label class="block text-[11px] text-muted-foreground">方式</label>
              <select
                class="w-full h-7 px-2 rounded-md border border-border bg-background"
                :value="(selected.data as any).mode || 'generate'"
                @change="updateSelectedData('mode', ($event.target as HTMLSelectElement).value)"
              >
                <option value="generate">文生图（images/generations）</option>
                <option value="from_file">读取本地图片路径</option>
              </select>
              <template v-if="(selected.data as any).mode === 'from_file'">
                <label class="block text-[11px] text-muted-foreground">
                  路径（可用 {'{{'}lastSavedPath{'}}'}）
                </label>
                <input
                  class="w-full h-7 px-2 rounded-md border border-border bg-background font-mono text-[10px]"
                  :value="(selected.data as any).filePath || '{{lastSavedPath}}'"
                  @change="updateSelectedData('filePath', ($event.target as HTMLInputElement).value)"
                />
              </template>
              <template v-else>
                <label class="block text-[11px] text-muted-foreground">出图模型</label>
                <select
                  class="w-full h-7 px-2 rounded-md border border-border bg-background"
                  :value="(selected.data as any).modelId || ''"
                  @change="updateSelectedData('modelId', ($event.target as HTMLSelectElement).value || null)"
                >
                  <option value="">（默认对话模型）</option>
                  <option v-for="m in llmModels" :key="m.id" :value="m.id">
                    {{ m.name }} · {{ m.modelName }}
                  </option>
                </select>
                <label class="block text-[11px] text-muted-foreground">提示词</label>
                <textarea
                  class="w-full min-h-[64px] px-2 py-1 rounded-md border border-border bg-background"
                  :value="(selected.data as any).prompt || ''"
                  @change="updateSelectedData('prompt', ($event.target as HTMLTextAreaElement).value)"
                />
              </template>
            </template>

            <template v-else-if="(selected.data as any).nodeType === 'layout_out'">
              <p class="text-[10px] text-muted-foreground leading-relaxed">
                读取上游 LLM 的平面布置 JSON，校验后画成 PNG，并同时写出 DXF（可在 CAD 中打开）。CAD-MCP 若 save_drawing 了 DXF，后续「读取本地图片」节点也会自动转成 PNG。
              </p>
              <label class="block text-[11px] text-muted-foreground">
                JSON 来源（可用 {'{{'}output{'}}'} / {'{{'}layout{'}}'}）
              </label>
              <input
                class="w-full h-7 px-2 rounded-md border border-border bg-background font-mono text-[10px]"
                :value="(selected.data as any).jsonSource || '{{output}}'"
                @change="updateSelectedData('jsonSource', ($event.target as HTMLInputElement).value)"
              />
              <label class="flex items-center gap-1.5 text-[11px]">
                <input
                  type="checkbox"
                  :checked="(selected.data as any).render !== false"
                  @change="updateSelectedData('render', ($event.target as HTMLInputElement).checked)"
                />
                渲染平面图 PNG
              </label>
              <label class="flex items-center gap-1.5 text-[11px]">
                <input
                  type="checkbox"
                  :checked="(selected.data as any).copyToOutput !== false"
                  @change="updateSelectedData('copyToOutput', ($event.target as HTMLInputElement).checked)"
                />
                用中文摘要覆盖 output（JSON 仍在 layout）
              </label>
            </template>

            <template v-else-if="(selected.data as any).nodeType === 'mcp'">
              <p class="text-[10px] text-muted-foreground leading-relaxed">
                只执行一次指定工具（参数需你写死或用模板）。画完整 CAD 图请改用「智能体（工具循环）」，并勾选一组 CAD 工具。
              </p>
              <label class="block text-[11px] text-muted-foreground">工具</label>
              <select
                class="w-full h-7 px-2 rounded-md border border-border bg-background"
                :value="(selected.data as any).toolId || ''"
                @change="updateSelectedData('toolId', ($event.target as HTMLSelectElement).value)"
              >
                <option value="">请选择</option>
                <option v-for="t in toolOptions" :key="t.id" :value="t.id">{{ t.label }}</option>
              </select>
              <label class="block text-[11px] text-muted-foreground">
                参数 JSON（可用 {'{{'}input{'}}'} / {'{{'}query{'}}'} / {'{{'}output{'}}'} / {'{{'}vision{'}}'} / {'{{'}layout{'}}'}）
              </label>
              <textarea
                class="w-full min-h-[72px] px-2 py-1 rounded-md border border-border bg-background font-mono text-[10px]"
                :value="
                  typeof (selected.data as any).arguments === 'string'
                    ? (selected.data as any).arguments
                    : JSON.stringify((selected.data as any).arguments || {}, null, 2)
                "
                @change="onArgsChange(($event.target as HTMLTextAreaElement).value)"
              />
            </template>

            <template v-else>
              <p class="text-[11px] text-muted-foreground">该节点无需额外配置。</p>
            </template>
          </div>
        </div>

        <div class="rounded-md border border-border p-3 flex-1 min-h-0 flex flex-col overflow-hidden">
          <div class="text-xs font-medium mb-2">试跑（草稿）</div>
          <textarea
            v-model="trialInput"
            class="w-full min-h-[56px] text-xs px-2 py-1 rounded-md border border-border bg-background mb-2"
          />
          <button
            type="button"
            class="h-8 px-3 inline-flex items-center justify-center gap-1.5 text-xs rounded-md bg-iron text-background hover:bg-iron/90 disabled:opacity-50 mb-2"
            :disabled="running"
            @click="onTrial"
          >
            <Loader2 v-if="running" class="size-3.5 animate-spin" />
            <Play v-else class="size-3.5" />
            试跑
          </button>
          <div class="text-[10px] text-muted-foreground mb-1">步骤</div>
          <pre class="flex-1 overflow-auto text-[10px] bg-background/60 rounded border border-border p-2 whitespace-pre-wrap">{{ runLog.join('\n') || '—' }}</pre>
          <div class="text-[10px] text-muted-foreground mt-2 mb-1">输出</div>
          <pre class="max-h-28 overflow-auto text-[10px] bg-background/60 rounded border border-border p-2 whitespace-pre-wrap">{{ runOutput || '—' }}</pre>
          <div v-if="runImages.length" class="mt-2">
            <ChatImageGallery
              :images="runImages"
              filename-prefix="充电站平面布置图"
              compact
            />
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
:deep(.vue-flow) {
  width: 100%;
  height: 100%;
}
:deep(.vue-flow__node) {
  font-size: 12px;
  border-radius: 6px;
  border-width: 1px;
  padding: 6px 10px;
  min-width: 96px;
  text-align: center;
}
:deep(.vue-flow__node-condition) {
  padding: 0;
}
</style>

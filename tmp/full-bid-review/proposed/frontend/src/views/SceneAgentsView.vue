<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Bot,
  Check,
  Loader2,
  MessageSquare,
  Pencil,
  Plus,
  Trash2,
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/lib/api'
import {
  createAgent,
  deleteAgent,
  listAgents,
  updateAgent,
  type AgentItem,
  type ChatMode,
} from '@/lib/agents-api'
import { listPrompts, type PromptItem } from '@/lib/prompts-api'
import { listKnowledgeBases, type KnowledgeBaseItem } from '@/lib/knowledge-api'
import { listMcpServers, type McpServerItem } from '@/lib/mcp-api'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'

type FormState = {
  name: string
  remark: string
  enabled: boolean
  promptId: string
  knowledgeBaseIds: string[]
  mode: ChatMode
  mcpToolIds: string[]
  toolsEnabled: boolean
}

type ToolOption = {
  id: string
  label: string
  serverName: string
  enabled: boolean
}

function emptyForm(): FormState {
  return {
    name: '',
    remark: '',
    enabled: true,
    promptId: '',
    knowledgeBaseIds: [],
    mode: 'fast',
    mcpToolIds: [],
    toolsEnabled: true,
  }
}

const auth = useAuthStore()
const router = useRouter()

const items = ref<AgentItem[]>([])
const promptOptions = ref<PromptItem[]>([])
const kbList = ref<KnowledgeBaseItem[]>([])
const toolOptions = ref<ToolOption[]>([])
const loading = ref(true)
const modalOpen = ref(false)
const editing = ref<AgentItem | null>(null)
const form = ref<FormState>(emptyForm())
const saving = ref(false)
const formError = ref<string | null>(null)
const pendingDelete = ref<AgentItem | null>(null)
const deleting = ref(false)
const toast = ref<{ type: 'ok' | 'err'; msg: string } | null>(null)

let toastTimer: ReturnType<typeof setTimeout> | null = null

const showGate = computed(
  () => auth.loading || (!auth.isAdmin && !auth.loading),
)

const promptNameMap = computed(() => {
  const m = new Map<string, string>()
  for (const p of promptOptions.value) m.set(p.id, p.name)
  return m
})

const enabledPrompts = computed(() =>
  promptOptions.value.filter((p) => p.enabled),
)

const kbNameMap = computed(() => {
  const m = new Map<string, string>()
  for (const b of kbList.value) m.set(b.id, b.name)
  return m
})

function flattenTools(servers: McpServerItem[]): ToolOption[] {
  const out: ToolOption[] = []
  for (const s of servers) {
    for (const t of s.tools || []) {
      out.push({
        id: t.id,
        label: t.name,
        serverName: s.name,
        enabled: Boolean(s.enabled && t.enabled),
      })
    }
  }
  return out.sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
}

async function loadRefs() {
  const [prompts, bases, servers] = await Promise.all([
    listPrompts().catch(() => [] as PromptItem[]),
    listKnowledgeBases({ access: 'use' }).catch(() => [] as KnowledgeBaseItem[]),
    listMcpServers().catch(() => [] as McpServerItem[]),
  ])
  promptOptions.value = prompts
  kbList.value = bases
  toolOptions.value = flattenTools(servers)
}

async function load() {
  loading.value = true
  try {
    const [agents] = await Promise.all([listAgents(), loadRefs()])
    items.value = agents
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof ApiError || e instanceof Error ? e.message : '加载失败',
    }
  } finally {
    loading.value = false
  }
}

watch(
  () => auth.loading,
  (authLoading) => {
    if (authLoading) return
    if (!auth.isAdmin) {
      void router.replace('/')
      return
    }
    void load()
  },
  { immediate: true },
)

watch(toast, (v) => {
  if (toastTimer) clearTimeout(toastTimer)
  if (!v) return
  toastTimer = setTimeout(() => {
    toast.value = null
  }, 3600)
})

function openCreate() {
  editing.value = null
  form.value = emptyForm()
  if (enabledPrompts.value.length > 0) {
    form.value.promptId = enabledPrompts.value[0].id
  }
  formError.value = null
  modalOpen.value = true
}

function openEdit(item: AgentItem) {
  editing.value = item
  form.value = {
    name: item.name,
    remark: item.remark || '',
    enabled: item.enabled,
    promptId: item.promptId || '',
    knowledgeBaseIds: [...(item.knowledgeBaseIds || [])],
    mode: item.mode === 'deep' ? 'deep' : 'fast',
    mcpToolIds: [...(item.mcpToolIds || [])],
    toolsEnabled: item.toolsEnabled !== false,
  }
  formError.value = null
  modalOpen.value = true
}

function onModalOpen(v: boolean) {
  if (saving.value && !v) return
  modalOpen.value = v
  if (!v) formError.value = null
}

function toggleKb(id: string) {
  const cur = form.value.knowledgeBaseIds
  form.value.knowledgeBaseIds = cur.includes(id)
    ? cur.filter((x) => x !== id)
    : [...cur, id]
}

function toggleTool(id: string) {
  const cur = form.value.mcpToolIds
  form.value.mcpToolIds = cur.includes(id)
    ? cur.filter((x) => x !== id)
    : [...cur, id]
}

function openChat(item: AgentItem) {
  void router.push({ path: '/ai-chat', query: { agentId: item.id } })
}

async function submit() {
  if (saving.value) return
  if (!form.value.name.trim()) {
    formError.value = '请填写名称'
    return
  }
  saving.value = true
  formError.value = null
  try {
    const payload = {
      name: form.value.name.trim(),
      remark: form.value.remark.trim() || undefined,
      enabled: form.value.enabled,
      promptId: form.value.promptId.trim() || null,
      knowledgeBaseIds: [...form.value.knowledgeBaseIds],
      mode: form.value.mode,
      mcpToolIds: form.value.toolsEnabled ? [...form.value.mcpToolIds] : [],
      toolsEnabled: form.value.toolsEnabled,
    }
    if (editing.value) {
      const item = await updateAgent(editing.value.id, payload)
      items.value = items.value.map((x) => (x.id === item.id ? item : x))
      toast.value = { type: 'ok', msg: '已保存智能体' }
    } else {
      const item = await createAgent(payload)
      items.value = [item, ...items.value]
      toast.value = { type: 'ok', msg: '已创建智能体' }
    }
    modalOpen.value = false
    await load()
  } catch (e) {
    formError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function confirmDelete() {
  if (!pendingDelete.value) return
  deleting.value = true
  try {
    await deleteAgent(pendingDelete.value.id)
    items.value = items.value.filter((x) => x.id !== pendingDelete.value!.id)
    pendingDelete.value = null
    toast.value = { type: 'ok', msg: '已删除智能体' }
  } catch (e) {
    toast.value = { type: 'err', msg: e instanceof Error ? e.message : '删除失败' }
  } finally {
    deleting.value = false
  }
}

function onDeleteOpen(v: boolean) {
  if (!v && !deleting.value) pendingDelete.value = null
}

function summarize(item: AgentItem) {
  const parts: string[] = []
  parts.push(item.mode === 'deep' ? '深度' : '快速')
  if (item.promptId) {
    parts.push(promptNameMap.value.get(item.promptId) || '已绑提示词')
  } else {
    parts.push('无提示词')
  }
  const kbCount = item.knowledgeBaseIds?.length || 0
  parts.push(kbCount > 0 ? `${kbCount} 个知识库` : '未绑知识库')
  if (!item.toolsEnabled) {
    parts.push('禁用工具')
  } else if ((item.mcpToolIds || []).length > 0) {
    parts.push(`${item.mcpToolIds.length} 个工具`)
  } else {
    parts.push('全部已启用工具')
  }
  return parts.join(' · ')
}
</script>

<template>
  <div
    v-if="showGate"
    class="py-20 text-center text-[12px] text-text-secondary"
  >
    <Loader2 class="inline size-4 animate-spin mr-2" />
    {{ auth.loading ? '加载中…' : '无权限访问场景智能体' }}
  </div>

  <div v-else class="space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold flex items-center gap-2">
          <Bot class="size-5 text-iron" />
          场景智能体
        </h1>
        <p class="mt-1 text-[12px] text-text-secondary">
          将提示词、知识库、模型模式与 MCP 工具打包成场景配置；打开对话后自动带入。
        </p>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-[12px] bg-iron text-white hover:brightness-110"
        @click="openCreate()"
      >
        <Plus class="size-3.5" /> 新增智能体
      </button>
    </header>

    <div
      v-if="loading"
      class="py-16 text-center text-[12px] text-text-secondary"
    >
      <Loader2 class="inline size-4 animate-spin mr-2" /> 加载智能体…
    </div>
    <div
      v-else-if="items.length === 0"
      class="py-16 text-center text-[12px] text-text-muted border border-dashed border-hairline rounded-lg"
    >
      暂无智能体。请先配置提示词 / 知识库，再创建场景包。
    </div>
    <div v-else class="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <div
        v-for="item in items"
        :key="item.id"
        class="rounded-lg border border-hairline bg-bg-elevated/40 p-4 space-y-3"
      >
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="text-[14px] font-medium text-text-primary truncate">
                {{ item.name }}
              </span>
              <span
                v-if="!item.enabled"
                class="px-1.5 py-0.5 rounded text-[10px] text-text-muted border border-hairline"
              >
                已停用
              </span>
              <span
                v-else
                class="px-1.5 py-0.5 rounded text-[10px] text-patina border border-patina/30 bg-patina/10"
              >
                启用
              </span>
            </div>
            <div
              v-if="item.remark"
              class="mt-1 text-[11px] text-text-muted truncate"
            >
              {{ item.remark }}
            </div>
          </div>
          <div class="flex items-center gap-0.5 shrink-0">
            <button
              type="button"
              title="打开对话"
              class="size-8 rounded-md text-text-muted hover:text-patina hover:bg-patina/10 inline-flex items-center justify-center"
              :disabled="!item.enabled"
              @click="openChat(item)"
            >
              <MessageSquare class="size-3.5" />
            </button>
            <button
              type="button"
              title="编辑"
              class="size-8 rounded-md text-text-muted hover:text-molybdenum hover:bg-molybdenum/10 inline-flex items-center justify-center"
              @click="openEdit(item)"
            >
              <Pencil class="size-3.5" />
            </button>
            <button
              type="button"
              title="删除"
              class="size-8 rounded-md text-text-muted hover:text-iron hover:bg-iron/10 inline-flex items-center justify-center"
              @click="pendingDelete = item"
            >
              <Trash2 class="size-3.5" />
            </button>
          </div>
        </div>

        <div class="text-[11.5px] text-text-secondary leading-relaxed">
          {{ summarize(item) }}
        </div>
        <div
          v-if="(item.knowledgeBaseIds || []).length > 0"
          class="flex flex-wrap gap-1"
        >
          <span
            v-for="kid in item.knowledgeBaseIds"
            :key="kid"
            class="px-1.5 py-0.5 rounded text-[10px] border border-hairline text-text-muted"
          >
            {{ kbNameMap.get(kid) || kid }}
          </span>
        </div>
      </div>
    </div>

    <AppDialog
      :open="modalOpen"
      :title="editing ? '编辑智能体' : '新增智能体'"
      description="配置将作为对话预设；打开对话时服务端以智能体配置为准。"
      @update:open="onModalOpen"
    >
      <div class="space-y-3 max-h-[65vh] overflow-y-auto pr-1">
        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">名称</div>
          <input
            v-model="form.name"
            class="kb-input"
            placeholder="如：知识库助手 / 运维问答助手"
          />
        </label>

        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">备注</div>
          <input v-model="form.remark" class="kb-input" placeholder="可选" />
        </label>

        <div class="block">
          <div class="text-[11px] text-text-secondary mb-1">模型模式</div>
          <div class="flex gap-2">
            <button
              type="button"
              :class="[
                'h-8 px-3 rounded-md text-[12px] border',
                form.mode === 'fast'
                  ? 'border-molybdenum/50 bg-molybdenum/10 text-molybdenum'
                  : 'border-hairline text-text-secondary',
              ]"
              @click="form.mode = 'fast'"
            >
              快速
            </button>
            <button
              type="button"
              :class="[
                'h-8 px-3 rounded-md text-[12px] border',
                form.mode === 'deep'
                  ? 'border-molybdenum/50 bg-molybdenum/10 text-molybdenum'
                  : 'border-hairline text-text-secondary',
              ]"
              @click="form.mode = 'deep'"
            >
              深度
            </button>
          </div>
        </div>

        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">系统提示词</div>
          <select v-model="form.promptId" class="kb-input">
            <option value="">不绑定提示词</option>
            <option
              v-for="p in promptOptions"
              :key="p.id"
              :value="p.id"
            >
              {{ p.name }}{{ p.enabled ? '' : '（已停用）' }}
            </option>
          </select>
        </label>

        <div class="block">
          <div class="text-[11px] text-text-secondary mb-1">
            知识库
            <span class="text-text-muted">（可多选，不选则不检索）</span>
          </div>
          <div
            v-if="kbList.length === 0"
            class="text-[11px] text-text-muted border border-dashed border-hairline rounded-md px-3 py-2"
          >
            暂无知识库，请先到「知识库」创建。
          </div>
          <div
            v-else
            class="max-h-36 overflow-y-auto rounded-md border border-hairline divide-y divide-hairline"
          >
            <button
              v-for="b in kbList"
              :key="b.id"
              type="button"
              class="w-full flex items-center gap-2 px-3 py-2 text-left text-[12px] hover:bg-bg-elevated/60"
              @click="toggleKb(b.id)"
            >
              <span
                :class="[
                  'size-4 rounded border inline-flex items-center justify-center shrink-0',
                  form.knowledgeBaseIds.includes(b.id)
                    ? 'border-molybdenum bg-molybdenum/20 text-molybdenum'
                    : 'border-hairline',
                ]"
              >
                <Check
                  v-if="form.knowledgeBaseIds.includes(b.id)"
                  class="size-2.5"
                />
              </span>
              <span class="truncate text-text-primary">{{ b.name }}</span>
            </button>
          </div>
        </div>

        <label class="flex items-center gap-2 text-[12px] text-text-secondary">
          <input v-model="form.toolsEnabled" type="checkbox" />
          启用 MCP 工具调用
        </label>

        <div v-if="form.toolsEnabled" class="block">
          <div class="text-[11px] text-text-secondary mb-1">
            工具白名单
            <span class="text-text-muted">（不选 = 使用全部已启用工具）</span>
          </div>
          <div
            v-if="toolOptions.length === 0"
            class="text-[11px] text-text-muted border border-dashed border-hairline rounded-md px-3 py-2"
          >
            暂无 MCP 工具，可到「工具管理」配置（示例：poetry run python scripts/seed_mcp_utility.py）；不选白名单时对话将使用全局已启用工具。
          </div>
          <div
            v-else
            class="max-h-40 overflow-y-auto rounded-md border border-hairline divide-y divide-hairline"
          >
            <button
              v-for="t in toolOptions"
              :key="t.id"
              type="button"
              class="w-full flex items-center gap-2 px-3 py-2 text-left text-[12px] hover:bg-bg-elevated/60"
              @click="toggleTool(t.id)"
            >
              <span
                :class="[
                  'size-4 rounded border inline-flex items-center justify-center shrink-0',
                  form.mcpToolIds.includes(t.id)
                    ? 'border-molybdenum bg-molybdenum/20 text-molybdenum'
                    : 'border-hairline',
                ]"
              >
                <Check
                  v-if="form.mcpToolIds.includes(t.id)"
                  class="size-2.5"
                />
              </span>
              <span class="min-w-0">
                <span class="block truncate text-text-primary">{{ t.label }}</span>
                <span class="block truncate text-[10px] text-text-muted">
                  {{ t.serverName }}
                  <template v-if="!t.enabled"> · 当前未启用</template>
                </span>
              </span>
            </button>
          </div>
        </div>

        <label class="flex items-center gap-2 text-[12px] text-text-secondary">
          <input v-model="form.enabled" type="checkbox" />
          启用智能体（停用后不可打开对话）
        </label>

        <div
          v-if="formError"
          class="rounded-md border border-iron/40 bg-iron/10 px-3 py-2 text-[12px] text-iron"
        >
          {{ formError }}
        </div>
      </div>

      <template #footer>
        <button
          type="button"
          class="kb-btn-secondary"
          :disabled="saving"
          @click="modalOpen = false"
        >
          取消
        </button>
        <button
          type="button"
          class="kb-btn-primary"
          :disabled="saving"
          @click="submit()"
        >
          <Loader2 v-if="saving" class="size-3.5 animate-spin" />
          <Check v-else class="size-3.5" />
          保存
        </button>
      </template>
    </AppDialog>

    <AppAlertDialog
      :open="Boolean(pendingDelete)"
      title="删除智能体"
      :description="`确认删除「${pendingDelete?.name || ''}」？不会删除其绑定的提示词与知识库。`"
      confirm-label="确认删除"
      :loading="deleting"
      destructive
      @update:open="onDeleteOpen"
      @confirm="confirmDelete"
    />

    <div
      v-if="toast"
      :class="[
        'fixed bottom-6 right-6 z-[80] px-4 py-2.5 rounded-md text-[12px] border',
        toast.type === 'ok'
          ? 'bg-patina/10 border-patina/40 text-patina'
          : 'bg-iron/10 border-iron/40 text-iron',
      ]"
    >
      {{ toast.msg }}
    </div>
  </div>
</template>

<style scoped>
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

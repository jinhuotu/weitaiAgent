<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Activity,
  Check,
  Loader2,
  Pencil,
  Plug,
  Plus,
  RefreshCw,
  Trash2,
  Wrench,
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/lib/api'
import {
  createMcpServer,
  deleteMcpServer,
  healthMcpServer,
  listMcpServers,
  refreshMcpTools,
  updateMcpServer,
  updateMcpTool,
  type McpServerItem,
  type McpTransport,
} from '@/lib/mcp-api'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'

type FormState = {
  name: string
  transport: McpTransport
  url: string
  command: string
  argsText: string
  envText: string
  headersText: string
  timeoutSeconds: string
  remark: string
  enabled: boolean
}

function emptyForm(transport: McpTransport = 'streamable_http'): FormState {
  return {
    name: '',
    transport,
    url: '',
    command: '',
    argsText: '',
    envText: '',
    headersText: '',
    timeoutSeconds: '60',
    remark: '',
    enabled: true,
  }
}

function parseJsonObject(text: string, label: string): Record<string, string> {
  const raw = text.trim()
  if (!raw) return {}
  try {
    const obj = JSON.parse(raw) as unknown
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      throw new Error(`${label} 必须是 JSON 对象`)
    }
    const out: Record<string, string> = {}
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      out[k] = String(v)
    }
    return out
  } catch (e) {
    if (e instanceof Error && e.message.includes('必须是')) throw e
    throw new Error(`${label} JSON 格式无效`)
  }
}

function parseArgs(text: string): string[] {
  const raw = text.trim()
  if (!raw) return []
  if (raw.startsWith('[')) {
    const arr = JSON.parse(raw) as unknown
    if (!Array.isArray(arr)) throw new Error('args 必须是 JSON 数组')
    return arr.map((x) => String(x))
  }
  return raw.split(/\s+/).filter(Boolean)
}

const auth = useAuthStore()
const router = useRouter()

const items = ref<McpServerItem[]>([])
const loading = ref(true)
const tab = ref<McpTransport | 'all'>('all')
const modalOpen = ref(false)
const editing = ref<McpServerItem | null>(null)
const form = ref<FormState>(emptyForm())
const saving = ref(false)
const formError = ref<string | null>(null)
const pendingDelete = ref<McpServerItem | null>(null)
const deleting = ref(false)
const probingId = ref<string | null>(null)
const refreshingId = ref<string | null>(null)
const toast = ref<{ type: 'ok' | 'err'; msg: string } | null>(null)
const expandedId = ref<string | null>(null)

let toastTimer: ReturnType<typeof setTimeout> | null = null

const transportOptions: { k: McpTransport; label: string }[] = [
  { k: 'streamable_http', label: 'Streamable HTTP' },
  { k: 'sse', label: 'SSE' },
  { k: 'stdio', label: 'stdio' },
]

const filtered = computed(() => {
  if (tab.value === 'all') return items.value
  return items.value.filter((x) => x.transport === tab.value)
})

const showGate = computed(
  () => auth.loading || (!auth.isAdmin && !auth.loading),
)

async function load() {
  loading.value = true
  try {
    items.value = await listMcpServers()
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
  }, v.type === 'err' ? 12000 : 3600)
})

function openCreate(transport: McpTransport = 'streamable_http') {
  editing.value = null
  form.value = emptyForm(transport)
  formError.value = null
  modalOpen.value = true
}

function openEdit(item: McpServerItem) {
  editing.value = item
  // 密钥类不回填脱敏串（易误解为保存失败）；非密钥明文回填
  const envEdit: Record<string, string> = {}
  const secretEnv = new Set(item.envSecretKeys || [])
  for (const [k, v] of Object.entries(item.envMasked || {})) {
    if (secretEnv.has(k) || String(v).includes('*')) {
      envEdit[k] = ''
    } else {
      envEdit[k] = v
    }
  }
  const headersEdit: Record<string, string> = {}
  const secretHeaders = new Set(item.headersSecretKeys || [])
  for (const [k, v] of Object.entries(item.headersMasked || {})) {
    if (secretHeaders.has(k) || String(v).includes('*')) {
      headersEdit[k] = ''
    } else {
      headersEdit[k] = v
    }
  }
  form.value = {
    name: item.name,
    transport: item.transport,
    url: item.url || '',
    command: item.command || '',
    argsText: (item.args || []).length ? JSON.stringify(item.args) : '',
    envText: Object.keys(envEdit).length ? JSON.stringify(envEdit, null, 2) : '',
    headersText: Object.keys(headersEdit).length
      ? JSON.stringify(headersEdit, null, 2)
      : '',
    timeoutSeconds: String(item.timeoutSeconds || 60),
    remark: item.remark || '',
    enabled: item.enabled,
  }
  formError.value = null
  modalOpen.value = true
}

function onModalOpen(v: boolean) {
  if (saving.value && !v) return
  modalOpen.value = v
  if (!v) formError.value = null
}

async function submit() {
  if (saving.value) return
  if (!form.value.name.trim()) {
    formError.value = '请填写名称'
    return
  }
  const t = form.value.transport
  if ((t === 'streamable_http' || t === 'sse') && !form.value.url.trim()) {
    formError.value = 'HTTP/SSE 传输需要填写 URL'
    return
  }
  if (t === 'stdio' && !form.value.command.trim()) {
    formError.value = 'stdio 传输需要填写 command'
    return
  }

  saving.value = true
  formError.value = null
  try {
    const f = form.value
    let args: string[] = []
    let env: Record<string, string> = {}
    let headers: Record<string, string> = {}
    try {
      args = parseArgs(f.argsText)
      env = parseJsonObject(f.envText, 'env')
      headers = parseJsonObject(f.headersText, 'headers')
    } catch (e) {
      formError.value = e instanceof Error ? e.message : '参数解析失败'
      return
    }

    const payload = {
      name: f.name.trim(),
      transport: f.transport,
      url: f.url.trim() || undefined,
      command: f.command.trim() || undefined,
      args,
      env,
      headers,
      timeoutSeconds: Number(f.timeoutSeconds) || 60,
      remark: f.remark.trim() || undefined,
      enabled: f.enabled,
    }

    let saved: McpServerItem
    if (editing.value) {
      saved = await updateMcpServer(editing.value.id, payload)
      items.value = items.value.map((x) => (x.id === saved.id ? saved : x))
    } else {
      saved = await createMcpServer(payload)
      items.value = [saved, ...items.value]
    }
    modalOpen.value = false
    await load()

    // 保存只写库；随后自动探测才会真正拉起 MCP（stdio 可能触发 npx 下载）
    toast.value = {
      type: 'ok',
      msg: '已保存，正在自动探测并拉取工具（stdio 首次可能需 1～3 分钟）…',
    }
    await probe(saved)
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
    await deleteMcpServer(pendingDelete.value.id)
    items.value = items.value.filter((x) => x.id !== pendingDelete.value!.id)
    pendingDelete.value = null
    toast.value = { type: 'ok', msg: '已删除' }
  } catch (e) {
    toast.value = { type: 'err', msg: e instanceof Error ? e.message : '删除失败' }
  } finally {
    deleting.value = false
  }
}

function onDeleteOpen(v: boolean) {
  if (!v && !deleting.value) pendingDelete.value = null
}

async function probe(item: McpServerItem) {
  probingId.value = item.id
  try {
    const res = await healthMcpServer(item.id)
    items.value = items.value.map((x) => (x.id === res.server.id ? res.server : x))
    toast.value = {
      type: 'ok',
      msg: `连通正常，发现 ${res.probe?.toolCount ?? res.server.toolCount} 个工具`,
    }
    expandedId.value = item.id
  } catch (e) {
    toast.value = { type: 'err', msg: e instanceof Error ? e.message : '探测失败' }
    await load()
  } finally {
    probingId.value = null
  }
}

async function refreshTools(item: McpServerItem) {
  refreshingId.value = item.id
  toast.value = {
    type: 'ok',
    msg: '正在刷新工具：stdio + npx 首次可能需要 1～3 分钟，请勿重复点击…',
  }
  try {
    const updated = await refreshMcpTools(item.id)
    items.value = items.value.map((x) => (x.id === updated.id ? updated : x))
    toast.value = { type: 'ok', msg: `已刷新，共 ${updated.toolCount} 个工具` }
    expandedId.value = item.id
  } catch (e) {
    const msg = e instanceof Error ? e.message : '刷新失败'
    toast.value = { type: 'err', msg }
    await load()
  } finally {
    refreshingId.value = null
  }
}

async function toggleTool(server: McpServerItem, toolId: string, enabled: boolean) {
  try {
    const updated = await updateMcpTool(server.id, toolId, { enabled })
    items.value = items.value.map((x) => (x.id === updated.id ? updated : x))
  } catch (e) {
    toast.value = { type: 'err', msg: e instanceof Error ? e.message : '更新工具失败' }
  }
}

function transportLabel(t: McpTransport) {
  return transportOptions.find((x) => x.k === t)?.label || t
}

const envPlaceholder = computed(() =>
  editing.value
    ? '{\n  "TRANSPORT": "stdio",\n  "MSSQL_CONNECTION_STRING": ""\n}'
    : '{\n  "TRANSPORT": "stdio",\n  "MSSQL_CONNECTION_STRING": "Server=...;Password=...;"\n}',
)
</script>

<template>
  <div
    v-if="showGate"
    class="py-20 text-center text-[12px] text-text-secondary"
  >
    <Loader2 class="inline size-4 animate-spin mr-2" />
    {{ auth.loading ? '加载中…' : '无权限访问工具管理' }}
  </div>

  <div v-else class="space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold flex items-center gap-2">
          <Plug class="size-5 text-iron" />
          工具管理
          <span class="text-[11px] font-normal text-text-muted">（MCP）</span>
        </h1>
        <p class="mt-1 text-[12px] text-text-secondary max-w-3xl">
          登记可供对话 / 场景智能体 / 工作流调用的工具服务（MCP）。内置示例可用
          <code class="px-1 rounded bg-bg-base border border-hairline">poetry run python scripts/seed_mcp_utility.py</code>
          注册「时间 / 天气」。保存后会自动探测并缓存工具；stdio 首次拉包可能需等待。
        </p>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-[12px] bg-iron text-white hover:brightness-110"
        @click="openCreate('streamable_http')"
      >
        <Plus class="size-3.5" /> 新增 MCP Server
      </button>
    </header>

    <div class="flex gap-1 border-b border-hairline">
      <button
        v-for="t in [
          { k: 'all' as const, label: '全部' },
          ...transportOptions.map((x) => ({ k: x.k, label: x.label })),
        ]"
        :key="t.k"
        type="button"
        :class="[
          'px-4 py-2 -mb-px text-[12px] border-b-2 transition-colors',
          tab === t.k
            ? 'border-iron text-text-primary'
            : 'border-transparent text-text-secondary hover:text-text-primary',
        ]"
        @click="tab = t.k"
      >
        {{ t.label }}
      </button>
    </div>

    <div
      v-if="loading"
      class="py-16 text-center text-[12px] text-text-secondary"
    >
      <Loader2 class="inline size-4 animate-spin mr-2" /> 加载配置…
    </div>
    <div
      v-else-if="filtered.length === 0"
      class="py-16 text-center text-[12px] text-text-muted border border-dashed border-hairline rounded-lg"
    >
      暂无 MCP Server。请新增并完成连通性探测。
    </div>
    <div v-else class="grid grid-cols-1 gap-3">
      <div
        v-for="item in filtered"
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
                class="px-1.5 py-0.5 rounded text-[10px] font-mono border text-molybdenum border-molybdenum/30 bg-molybdenum/10"
              >
                {{ transportLabel(item.transport) }}
              </span>
              <span
                v-if="!item.enabled"
                class="px-1.5 py-0.5 rounded text-[10px] text-text-muted border border-hairline"
              >
                已停用
              </span>
              <span
                class="px-1.5 py-0.5 rounded text-[10px] text-text-secondary border border-hairline"
              >
                {{ item.toolCount }} 工具
              </span>
            </div>
            <div class="mt-1 text-[11px] font-mono text-text-muted truncate">
              <template v-if="item.transport === 'stdio'">
                {{ item.command }} {{ (item.args || []).join(' ') }}
              </template>
              <template v-else>
                {{ item.url }}
              </template>
            </div>
            <div
              v-if="item.lastError"
              class="mt-1 text-[11px] text-iron line-clamp-3 whitespace-pre-wrap"
              :title="item.lastError"
            >
              上次错误：{{ item.lastError }}
            </div>
          </div>
          <div class="flex items-center gap-0.5 shrink-0">
            <button
              type="button"
              title="连通性探测"
              class="size-8 rounded-md text-text-muted hover:text-patina hover:bg-patina/10 inline-flex items-center justify-center"
              :disabled="probingId === item.id"
              @click="probe(item)"
            >
              <Loader2 v-if="probingId === item.id" class="size-3.5 animate-spin" />
              <Activity v-else class="size-3.5" />
            </button>
            <button
              type="button"
              title="刷新工具"
              class="size-8 rounded-md text-text-muted hover:text-molybdenum hover:bg-molybdenum/10 inline-flex items-center justify-center"
              :disabled="refreshingId === item.id"
              @click="refreshTools(item)"
            >
              <Loader2 v-if="refreshingId === item.id" class="size-3.5 animate-spin" />
              <RefreshCw v-else class="size-3.5" />
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

        <button
          type="button"
          class="text-[11px] text-text-secondary hover:text-text-primary inline-flex items-center gap-1"
          @click="expandedId = expandedId === item.id ? null : item.id"
        >
          <Wrench class="size-3" />
          {{ expandedId === item.id ? '收起工具' : '查看工具' }}
        </button>

        <div
          v-if="expandedId === item.id"
          class="rounded-md border border-hairline bg-bg-base/40 divide-y divide-hairline"
        >
          <div
            v-if="!item.tools?.length"
            class="px-3 py-4 text-[11px] text-text-muted text-center"
          >
            暂无工具缓存，请先探测或刷新。
          </div>
          <div
            v-for="tool in item.tools"
            :key="tool.id"
            class="flex items-start justify-between gap-3 px-3 py-2.5"
          >
            <div class="min-w-0">
              <div class="text-[12px] font-mono text-text-primary truncate">
                {{ tool.name }}
              </div>
              <div class="text-[11px] text-text-muted line-clamp-2">
                {{ tool.description || '无描述' }}
              </div>
            </div>
            <label class="shrink-0 flex items-center gap-1.5 text-[11px] text-text-secondary">
              <input
                type="checkbox"
                :checked="tool.enabled"
                @change="
                  toggleTool(
                    item,
                    tool.id,
                    ($event.target as HTMLInputElement).checked,
                  )
                "
              />
              启用
            </label>
          </div>
        </div>
      </div>
    </div>

    <AppDialog
      :open="modalOpen"
      :title="editing ? '编辑 MCP Server' : '新增 MCP Server'"
      description="密钥类字段（如连接串）不会回显明文；编辑时留空表示保留原值。保存后会自动探测并拉取工具。"
      @update:open="onModalOpen"
    >
      <div class="space-y-3">
        <div class="flex flex-wrap gap-2">
          <button
            v-for="opt in transportOptions"
            :key="opt.k"
            type="button"
            :class="[
              'px-2.5 h-8 rounded-md text-[11px] border transition-colors',
              form.transport === opt.k
                ? 'border-iron bg-iron/10 text-iron'
                : 'border-hairline text-text-secondary',
            ]"
            :disabled="Boolean(editing)"
            @click="form.transport = opt.k"
          >
            {{ opt.label }}
          </button>
        </div>

        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">名称</div>
          <input v-model="form.name" class="kb-input" placeholder="如：Filesystem / 内部工单 MCP" />
        </label>

        <label v-if="form.transport !== 'stdio'" class="block">
          <div class="text-[11px] text-text-secondary mb-1">URL</div>
          <input
            v-model="form.url"
            class="kb-input"
            placeholder="https://mcp.example.com/mcp"
          />
        </label>

        <template v-if="form.transport === 'stdio'">
          <label class="block">
            <div class="text-[11px] text-text-secondary mb-1">Command</div>
            <input v-model="form.command" class="kb-input" placeholder="npx / python / node" />
          </label>
          <label class="block">
            <div class="text-[11px] text-text-secondary mb-1">Args（空格分隔或 JSON 数组）</div>
            <input
              v-model="form.argsText"
              class="kb-input"
              placeholder='["-y","@modelcontextprotocol/server-filesystem","/data"]'
            />
          </label>
        </template>

        <div class="grid grid-cols-2 gap-3">
          <label class="block">
            <div class="text-[11px] text-text-secondary mb-1">超时(秒)</div>
            <input v-model="form.timeoutSeconds" class="kb-input" />
          </label>
          <label class="flex items-end gap-2 text-[12px] text-text-secondary pb-2">
            <input v-model="form.enabled" type="checkbox" />
            启用（参与问答工具调用）
          </label>
        </div>

        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">Headers（JSON）</div>
          <textarea
            v-model="form.headersText"
            class="kb-input min-h-[72px]"
            placeholder='{"Authorization":"Bearer ..."}'
          />
        </label>
        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">
            Env（JSON，stdio 常用）
            <span
              v-if="editing?.envSecretKeys?.length"
              class="text-text-muted"
            >
              · 已配置密钥：{{ editing.envSecretKeys.join(', ') }}（留空不改）
            </span>
          </div>
          <textarea
            v-model="form.envText"
            class="kb-input min-h-[96px]"
            :placeholder="envPlaceholder"
          />
        </label>
        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">备注</div>
          <input v-model="form.remark" class="kb-input" />
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
      title="删除 MCP Server"
      :description="`确认删除「${pendingDelete?.name || ''}」？其工具缓存将一并删除。`"
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

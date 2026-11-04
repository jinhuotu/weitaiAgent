<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Boxes,
  Brain,
  Check,
  Loader2,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  Zap,
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/lib/api'
import {
  createModelConfig,
  deleteModelConfig,
  listModelConfigs,
  modelTypeLabel,
  coerceModelType,
  normalizeApiBase,
  isLoopbackApiBase,
  updateModelConfig,
  EMBEDDING_MODEL_TYPE_OPTIONS,
  LLM_MODEL_TYPE_OPTIONS,
  type ModelConfigItem,
  type ModelKind,
  type ModelType,
} from '@/lib/models-api'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'

type FormState = {
  name: string
  kind: ModelKind
  modelType: ModelType
  apiBase: string
  apiKey: string
  modelName: string
  temperature: string
  timeoutSeconds: string
  embeddingDim: string
  remark: string
  enabled: boolean
  scopeFast: boolean
  scopeDeep: boolean
  scopeEmbedding: boolean
}

function defaultModelType(kind: ModelKind): ModelType {
  return kind === 'embedding' ? 'text_embedding' : 'text_chat'
}

function emptyForm(kind: ModelKind = 'llm'): FormState {
  return {
    name: '',
    kind,
    modelType: defaultModelType(kind),
    apiBase: '',
    apiKey: '',
    modelName: '',
    temperature: kind === 'llm' ? '0.6' : '',
    timeoutSeconds: '120',
    embeddingDim: kind === 'embedding' ? '1536' : '',
    remark: '',
    enabled: true,
    scopeFast: false,
    scopeDeep: false,
    scopeEmbedding: kind === 'embedding',
  }
}

const auth = useAuthStore()
const router = useRouter()

const items = ref<ModelConfigItem[]>([])
const loading = ref(true)
const tab = ref<ModelKind | 'all'>('all')
const modalOpen = ref(false)
const editing = ref<ModelConfigItem | null>(null)
const form = ref<FormState>(emptyForm())
const saving = ref(false)
const formError = ref<string | null>(null)
const pendingDelete = ref<ModelConfigItem | null>(null)
const deleting = ref(false)
const toast = ref<{ type: 'ok' | 'err'; msg: string } | null>(null)

let toastTimer: ReturnType<typeof setTimeout> | null = null

const kindOptions: ModelKind[] = ['llm', 'embedding']

const modelTypeOptions = computed(() =>
  form.value.kind === 'embedding' ? EMBEDDING_MODEL_TYPE_OPTIONS : LLM_MODEL_TYPE_OPTIONS,
)

const filtered = computed(() => {
  if (tab.value === 'all') return items.value
  return items.value.filter((x) => x.kind === tab.value)
})

const showGate = computed(
  () => auth.loading || (!auth.isAdmin && !auth.loading),
)

async function load() {
  loading.value = true
  try {
    items.value = await listModelConfigs()
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

function openCreate(kind: ModelKind = 'llm') {
  editing.value = null
  form.value = emptyForm(kind)
  formError.value = null
  modalOpen.value = true
}

function openEdit(item: ModelConfigItem) {
  editing.value = item
  form.value = {
    name: item.name,
    kind: item.kind,
    modelType: coerceModelType(item.modelType, item.kind),
    apiBase: item.apiBase,
    apiKey: '',
    modelName: item.modelName,
    temperature: item.temperature != null ? String(item.temperature) : '',
    timeoutSeconds: String(item.timeoutSeconds || 120),
    embeddingDim: item.embeddingDim != null ? String(item.embeddingDim) : '',
    remark: item.remark || '',
    enabled: item.enabled,
    scopeFast: item.scopeFast,
    scopeDeep: item.scopeDeep,
    scopeEmbedding: item.scopeEmbedding,
  }
  formError.value = null
  modalOpen.value = true
}

function onModalOpen(v: boolean) {
  if (saving.value && !v) return
  modalOpen.value = v
  if (!v) formError.value = null
}

function switchKind(k: ModelKind) {
  if (editing.value) return
  if (k !== 'llm' && k !== 'embedding') return
  const f = form.value
  form.value = {
    ...emptyForm(k),
    name: f.name,
    apiBase: f.apiBase,
    apiKey: f.apiKey,
  }
}

async function submit() {
  if (saving.value) return
  if (!form.value.name.trim() || !form.value.apiBase.trim() || !form.value.modelName.trim()) {
    formError.value = '请填写名称、API Base、模型标识'
    return
  }
  const apiBase = normalizeApiBase(form.value.apiBase)
  if (!apiBase) {
    formError.value = 'API Base 无效。局域网请填 http://IP:端口/v1，云端请填 https:// 地址'
    return
  }
  form.value.apiBase = apiBase
  saving.value = true
  formError.value = null
  try {
    const f = form.value
    const payload = {
      name: f.name.trim(),
      kind: f.kind,
      modelType: f.kind === 'embedding' ? 'text_embedding' : f.modelType,
      apiBase,
      modelName: f.modelName.trim(),
      timeoutSeconds: Number(f.timeoutSeconds) || 120,
      remark: f.remark.trim() || undefined,
      enabled: f.enabled,
      scopeFast: f.kind === 'llm' ? f.scopeFast : false,
      scopeDeep: f.kind === 'llm' ? f.scopeDeep : false,
      scopeEmbedding: f.kind === 'embedding' ? f.scopeEmbedding : false,
      temperature:
        f.kind === 'llm' && f.temperature !== '' ? Number(f.temperature) : undefined,
      embeddingDim:
        f.kind === 'embedding' && f.embeddingDim !== ''
          ? Number(f.embeddingDim)
          : undefined,
    }

    if (editing.value) {
      const body: Record<string, unknown> = { ...payload }
      delete body.kind
      if (f.apiKey.trim() && !f.apiKey.includes('*')) {
        body.apiKey = f.apiKey.trim()
      }
      if (f.kind === 'llm' && f.temperature === '') {
        body.clearTemperature = true
      }
      const item = await updateModelConfig(editing.value.id, body)
      items.value = items.value.map((x) => (x.id === item.id ? item : x))
      toast.value = { type: 'ok', msg: '已保存配置' }
    } else {
      const item = await createModelConfig({
        ...payload,
        apiKey: f.apiKey.trim() || 'sk-local',
      })
      items.value = [item, ...items.value]
      toast.value = { type: 'ok', msg: '已创建配置' }
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
    await deleteModelConfig(pendingDelete.value.id)
    items.value = items.value.filter((x) => x.id !== pendingDelete.value!.id)
    pendingDelete.value = null
    toast.value = { type: 'ok', msg: '已删除配置' }
  } catch (e) {
    toast.value = { type: 'err', msg: e instanceof Error ? e.message : '删除失败' }
  } finally {
    deleting.value = false
  }
}

function onDeleteOpen(v: boolean) {
  if (!v && !deleting.value) pendingDelete.value = null
}
</script>

<template>
  <div
    v-if="showGate"
    class="py-20 text-center text-[12px] text-text-secondary"
  >
    <Loader2 class="inline size-4 animate-spin mr-2" />
    {{ auth.loading ? '加载中…' : '无权限访问模型管理' }}
  </div>

  <div v-else class="space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold flex items-center gap-2">
          <Boxes class="size-5 text-iron" />
          模型管理
        </h1>
        <p class="mt-1 text-[12px] text-text-secondary">
          配置 OpenAI 兼容的对话模型与 Embedding 模型。局域网自建（vLLM / Ollama 等）可留空 API Key；API Base 请填推理机的局域网 IP，不要用 127.0.0.1（除非模型和本系统 API 跑在同一台电脑）。勾选「启用」后即可在智能问答等页面选择使用。
        </p>
      </div>
      <div class="flex gap-2">
        <button
          type="button"
          class="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-[12px] bg-iron text-white hover:brightness-110"
          @click="openCreate('llm')"
        >
          <Plus class="size-3.5" /> 新增对话模型
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-[12px] border border-molybdenum/40 text-molybdenum hover:bg-molybdenum/10"
          @click="openCreate('embedding')"
        >
          <Plus class="size-3.5" /> 新增 Embedding
        </button>
      </div>
    </header>

    <div class="flex gap-1 border-b border-hairline">
      <button
        v-for="t in [
          { k: 'all' as const, label: '全部' },
          { k: 'llm' as const, label: '对话模型' },
          { k: 'embedding' as const, label: 'Embedding' },
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
      暂无配置。请先新增并启用对话模型 / Embedding。
    </div>
    <div v-else class="grid grid-cols-1 lg:grid-cols-2 gap-3">
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
                :class="[
                  'px-1.5 py-0.5 rounded text-[10px] border',
                  item.kind === 'llm'
                    ? 'text-iron border-iron/30 bg-iron/10'
                    : 'text-molybdenum border-molybdenum/30 bg-molybdenum/10',
                ]"
              >
                {{ modelTypeLabel(item.modelType, item.kind) }}
              </span>
              <span
                class="px-1.5 py-0.5 rounded text-[10px] text-text-muted border border-hairline"
              >
                {{ item.kind === 'llm' ? 'LLM' : 'Embedding' }}
              </span>
              <span
                v-if="!item.enabled"
                class="px-1.5 py-0.5 rounded text-[10px] text-text-muted border border-hairline"
              >
                已停用
              </span>
            </div>
            <div class="mt-1 text-[11px] text-text-muted truncate">
              模型标识
              <span class="font-mono text-text-secondary">{{ item.modelName }}</span>
              <span class="mx-1">·</span>
              <span class="font-mono">{{ item.apiBase }}</span>
            </div>
          </div>
          <div class="flex items-center gap-0.5 shrink-0">
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

        <div class="text-[11px] text-text-secondary">
          API Key：
          <span class="font-mono text-text-muted">{{ item.apiKeyMasked || '未设置（局域网可空）' }}</span>
        </div>

        <div class="flex flex-wrap gap-1.5 text-[10.5px]">
          <span
            v-if="item.kind === 'llm' && item.scopeFast"
            class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-iron/15 text-iron border border-iron/25"
          >
            <Zap class="size-3" /> 快速问答
          </span>
          <span
            v-if="item.kind === 'llm' && item.scopeDeep"
            class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-molybdenum/15 text-molybdenum border border-molybdenum/25"
          >
            <Brain class="size-3" /> 深度推理
          </span>
          <span
            v-if="item.kind === 'embedding' && item.scopeEmbedding"
            class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-patina/15 text-patina border border-patina/25"
          >
            <Sparkles class="size-3" /> 用于知识库
          </span>
          <span
            v-if="item.kind === 'llm' && !item.scopeFast && !item.scopeDeep"
            class="text-text-muted"
          >
            可在对话中选择（非默认）
          </span>
          <span
            v-if="item.kind === 'embedding' && !item.scopeEmbedding"
            class="text-text-muted"
          >
            未绑定知识库
          </span>
        </div>
      </div>
    </div>

    <AppDialog
      :open="modalOpen"
      :title="editing ? '编辑模型配置' : '新增模型配置'"
      description="云端模型请填 API Key；局域网自建可留空。编辑时留空 Key 表示不修改。"
      @update:open="onModalOpen"
    >
      <div class="space-y-3 -mx-0">
        <div class="block">
          <div class="text-[11px] text-text-secondary mb-1">接口类别</div>
          <div class="flex gap-2">
            <button
              v-for="k in kindOptions"
              :key="k"
              type="button"
              :disabled="Boolean(editing)"
              :class="[
                'flex-1 h-9 rounded-md text-[12px] border transition-colors',
                form.kind === k
                  ? 'border-iron bg-iron/10 text-iron'
                  : 'border-hairline text-text-secondary',
                editing ? 'opacity-70 cursor-not-allowed' : '',
              ]"
              @click="switchKind(k)"
            >
              {{ k === 'llm' ? '对话模型 (LLM)' : 'Embedding' }}
            </button>
          </div>
        </div>

        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">模型类型</div>
          <select v-model="form.modelType" class="kb-input" :disabled="form.kind === 'embedding'">
            <option v-for="opt in modelTypeOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
          <div class="mt-1 text-[10.5px] text-text-muted">
            {{
              form.kind === 'embedding'
                ? 'Embedding 固定为文本向量，用于知识库检索。'
                : '文本对话用于问答；多模态视觉/音频用于图文或语音输入。'
            }}
          </div>
        </label>

        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">名称</div>
          <input
            v-model="form.name"
            class="kb-input"
            placeholder="如：DeepSeek Chat / 豆包 Embedding"
          />
        </label>
        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">API Base</div>
          <input
            v-model="form.apiBase"
            class="kb-input"
            placeholder="http://192.168.x.x:8001/v1 或 https://api.openai.com/v1"
          />
          <div class="mt-1 text-[10.5px] text-text-muted">
            OpenAI 兼容根地址。局域网示例：http://192.168.2.112:8001/v1
            （IP 和端口之间用冒号，不要写成 112/:8001）。
          </div>
          <div
            v-if="isLoopbackApiBase(form.apiBase)"
            class="mt-1 text-[10.5px] text-iron"
          >
            当前是 127.0.0.1/localhost，只指向运行本系统的这台电脑。若模型在局域网其它机器，请改成那台机器的 IP。
          </div>
        </label>
        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">
            {{ editing ? 'API Key（留空不改）' : 'API Key（局域网可留空）' }}
          </div>
          <input
            v-model="form.apiKey"
            class="kb-input"
            type="password"
            autocomplete="new-password"
            :placeholder="editing ? `当前 ${editing.apiKeyMasked || '未设置'}` : '云端填 sk-...，局域网自建可留空'"
          />
          <div class="mt-1 text-[10.5px] text-text-muted">
            vLLM / Ollama / SGLang 等本地 OpenAI 兼容服务通常不校验 Key，可留空。
          </div>
        </label>
        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">模型标识</div>
          <input
            v-model="form.modelName"
            class="kb-input"
            :placeholder="form.kind === 'llm' ? '如：gpt-4o-mini / deepseek-chat' : '如：text-embedding-3-small'"
          />
          <div class="mt-1 text-[10.5px] text-text-muted">
            调用 API 时传入的 model 参数，需与服务商控制台中的模型 ID 一致。
          </div>
        </label>
        <div class="grid grid-cols-2 gap-3">
          <label v-if="form.kind === 'llm'" class="block">
            <div class="text-[11px] text-text-secondary mb-1">Temperature</div>
            <input
              v-model="form.temperature"
              class="kb-input"
              placeholder="0.6"
            />
          </label>
          <label v-else class="block">
            <div class="text-[11px] text-text-secondary mb-1">向量维度</div>
            <input
              v-model="form.embeddingDim"
              class="kb-input"
              placeholder="1536"
            />
          </label>
          <label class="block">
            <div class="text-[11px] text-text-secondary mb-1">超时(秒)</div>
            <input v-model="form.timeoutSeconds" class="kb-input" />
          </label>
        </div>
        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">备注</div>
          <input v-model="form.remark" class="kb-input" />
        </label>

        <label class="flex items-center gap-2 text-[12px] text-text-secondary">
          <input v-model="form.enabled" type="checkbox" />
          启用（可在对话等页面中选择使用）
        </label>

        <div
          v-if="form.kind === 'llm'"
          class="space-y-2 rounded-md border border-hairline p-3"
        >
          <div class="text-[11px] text-text-muted">
            设为智能问答的默认模型（对话页仍可另选其它已启用模型）。每模式仅一条默认，勾选后会替换原默认，不会删除其它模型。
          </div>
          <label class="flex items-center gap-2 text-[12px]">
            <input v-model="form.scopeFast" type="checkbox" />
            <Zap class="size-3.5 text-iron" /> 用于快速问答
          </label>
          <label class="flex items-center gap-2 text-[12px]">
            <input v-model="form.scopeDeep" type="checkbox" />
            <Brain class="size-3.5 text-molybdenum" /> 用于深度推理
          </label>
        </div>
        <label
          v-else
          class="flex items-center gap-2 text-[12px] rounded-md border border-hairline p-3"
        >
          <input v-model="form.scopeEmbedding" type="checkbox" />
          <Sparkles class="size-3.5 text-patina" /> 用于知识库（Embedding）
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
      :title="'删除模型配置'"
      :description="`确认删除「${pendingDelete?.name || ''}」？若其正在绑定快速/深度/知识库，相关能力将不可用。`"
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
:deep(.kb-input:disabled) {
  opacity: 0.7;
  cursor: not-allowed;
}
:deep(select.kb-input) {
  font-family: inherit;
}
</style>

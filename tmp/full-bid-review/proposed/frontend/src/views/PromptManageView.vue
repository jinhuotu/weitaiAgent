<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { BookOpenText, Check, Loader2, Pencil, Plus, Trash2 } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/lib/api'
import {
  createPrompt,
  deletePrompt,
  listPrompts,
  updatePrompt,
  type PromptItem,
} from '@/lib/prompts-api'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'

type FormState = {
  name: string
  content: string
  remark: string
  enabled: boolean
}

function emptyForm(): FormState {
  return {
    name: '',
    content: '',
    remark: '',
    enabled: true,
  }
}

const auth = useAuthStore()
const router = useRouter()

const items = ref<PromptItem[]>([])
const loading = ref(true)
const modalOpen = ref(false)
const editing = ref<PromptItem | null>(null)
const form = ref<FormState>(emptyForm())
const saving = ref(false)
const formError = ref<string | null>(null)
const pendingDelete = ref<PromptItem | null>(null)
const deleting = ref(false)
const toast = ref<{ type: 'ok' | 'err'; msg: string } | null>(null)

let toastTimer: ReturnType<typeof setTimeout> | null = null

const showGate = computed(
  () => auth.loading || (!auth.isAdmin && !auth.loading),
)

async function load() {
  loading.value = true
  try {
    items.value = await listPrompts()
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
  formError.value = null
  modalOpen.value = true
}

function openEdit(item: PromptItem) {
  editing.value = item
  form.value = {
    name: item.name,
    content: item.content || '',
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
  if (!form.value.name.trim() || !form.value.content.trim()) {
    formError.value = '请填写名称与提示词内容'
    return
  }
  saving.value = true
  formError.value = null
  try {
    const payload = {
      name: form.value.name.trim(),
      content: form.value.content.trim(),
      remark: form.value.remark.trim() || undefined,
      enabled: form.value.enabled,
    }
    if (editing.value) {
      const item = await updatePrompt(editing.value.id, payload)
      items.value = items.value.map((x) => (x.id === item.id ? item : x))
      toast.value = { type: 'ok', msg: '已保存提示词' }
    } else {
      const item = await createPrompt(payload)
      items.value = [item, ...items.value]
      toast.value = { type: 'ok', msg: '已创建提示词' }
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
    await deletePrompt(pendingDelete.value.id)
    items.value = items.value.filter((x) => x.id !== pendingDelete.value!.id)
    pendingDelete.value = null
    toast.value = { type: 'ok', msg: '已删除提示词' }
  } catch (e) {
    toast.value = { type: 'err', msg: e instanceof Error ? e.message : '删除失败' }
  } finally {
    deleting.value = false
  }
}

function onDeleteOpen(v: boolean) {
  if (!v && !deleting.value) pendingDelete.value = null
}

function preview(content: string | undefined) {
  const t = (content || '').trim().replace(/\s+/g, ' ')
  if (!t) return '（空）'
  return t.length > 120 ? `${t.slice(0, 120)}…` : t
}
</script>

<template>
  <div
    v-if="showGate"
    class="py-20 text-center text-[12px] text-text-secondary"
  >
    <Loader2 class="inline size-4 animate-spin mr-2" />
    {{ auth.loading ? '加载中…' : '无权限访问提示词管理' }}
  </div>

  <div v-else class="space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold flex items-center gap-2">
          <BookOpenText class="size-5 text-iron" />
          提示词管理
        </h1>
        <p class="mt-1 text-[12px] text-text-secondary">
          维护对话可用的系统提示词。在「AI 智能问答」中选择后才会注入大模型；未选择则不传系统提示词。
        </p>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-[12px] bg-iron text-white hover:brightness-110"
        @click="openCreate()"
      >
        <Plus class="size-3.5" /> 新增提示词
      </button>
    </header>

    <div
      v-if="loading"
      class="py-16 text-center text-[12px] text-text-secondary"
    >
      <Loader2 class="inline size-4 animate-spin mr-2" /> 加载提示词…
    </div>
    <div
      v-else-if="items.length === 0"
      class="py-16 text-center text-[12px] text-text-muted border border-dashed border-hairline rounded-lg"
    >
      暂无提示词。请先新增一条，再在对话页选择使用。
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

        <div class="text-[11.5px] text-text-secondary leading-relaxed line-clamp-3">
          {{ preview(item.content) }}
        </div>
        <div class="text-[10px] font-mono text-text-muted">
          {{ (item.content || '').length }} 字
        </div>
      </div>
    </div>

    <AppDialog
      :open="modalOpen"
      :title="editing ? '编辑提示词' : '新增提示词'"
      description="这段文字是对话的 system 基座。检索结果和「【知识库检索结果】未检索到匹配片段」等标记由程序按本轮情况追加，怎么回答写在这里。"
      @update:open="onModalOpen"
    >
      <div class="space-y-3">
        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">名称</div>
          <input
            v-model="form.name"
            class="kb-input"
            placeholder="如：智能助手 / 知识库专员"
          />
        </label>
        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">提示词内容</div>
          <textarea
            v-model="form.content"
            class="kb-input min-h-[220px] resize-y font-sans leading-relaxed"
            placeholder="输入系统提示词全文…"
          />
          <p class="mt-1 text-[10.5px] leading-relaxed text-text-muted">
            代码只会追加本轮标记，例如「知识库参考片段」「未检索到匹配片段」「本次附带用户上传图片 / 资料库原件」。未命中时怎么措辞，请写在本提示词第 3 条。
          </p>
        </label>
        <label class="block">
          <div class="text-[11px] text-text-secondary mb-1">备注</div>
          <input v-model="form.remark" class="kb-input" placeholder="可选" />
        </label>
        <label class="flex items-center gap-2 text-[12px] text-text-secondary">
          <input v-model="form.enabled" type="checkbox" />
          启用（停用后对话页不可选）
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
      title="删除提示词"
      :description="`确认删除「${pendingDelete?.name || ''}」？对话页将无法再选择该提示词。`"
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

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  BookOpen,
  Brain,
  FileText,
  Layers,
  Library,
  Loader2,
  Pencil,
  Plus,
  ScrollText,
  Shield,
  Trash2,
  TriangleAlert,
} from 'lucide-vue-next'
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  listKnowledgeBaseCatalog,
  updateKnowledgeBase,
  type KbAccess,
  type KnowledgeBaseItem,
} from '@/lib/knowledge-api'
import { libraryCardAccentByName } from '@/lib/library-card-theme'
import { ApiError } from '@/lib/api'
import { fmtAgo } from '@/lib/time'
import KbAclDialog from '@/components/knowledge/KbAclDialog.vue'

const KB_ICONS = [BookOpen, Library, Brain, ScrollText, Layers]

function cardIcon(index: number) {
  return KB_ICONS[index % KB_ICONS.length]!
}

function accentOf(base: KnowledgeBaseItem, index: number) {
  return libraryCardAccentByName(base.name, index)
}

const router = useRouter()
const items = ref<KnowledgeBaseItem[]>([])
const canCreate = ref(false)
const accessFilter = ref<KbAccess>('view')
const aclTarget = ref<KnowledgeBaseItem | null>(null)
const loading = ref(true)
const saving = ref(false)
const modalMode = ref<'create' | 'edit' | null>(null)
const editing = ref<KnowledgeBaseItem | null>(null)
const name = ref('')
const description = ref('')
const formError = ref<string | null>(null)
const toast = ref<{ type: 'ok' | 'err'; msg: string } | null>(null)
const deletingId = ref<string | null>(null)
const pendingDelete = ref<KnowledgeBaseItem | null>(null)
const entering = ref<{ id: string; name: string } | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | null = null

function redirectLoginIfNeeded(err: unknown) {
  if (err instanceof ApiError && err.status === 401) {
    void router.replace(`/login?next=${encodeURIComponent('/knowledge')}`)
    return true
  }
  return false
}

async function load() {
  loading.value = true
  try {
    const data = await listKnowledgeBaseCatalog({ access: accessFilter.value })
    items.value = data.items
    canCreate.value = data.canCreate
  } catch (e) {
    if (redirectLoginIfNeeded(e)) return
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
  }, 4200)
})

watch(accessFilter, () => {
  void load()
})

onMounted(() => {
  void load()
})

onUnmounted(() => {
  if (toastTimer) clearTimeout(toastTimer)
})

function openCreate() {
  if (!canCreate.value) return
  editing.value = null
  name.value = ''
  description.value = ''
  formError.value = null
  modalMode.value = 'create'
}

function openEdit(base: KnowledgeBaseItem, e: Event) {
  if (!base.canManage) return
  e.preventDefault()
  e.stopPropagation()
  editing.value = base
  name.value = base.name
  description.value = base.description || ''
  formError.value = null
  modalMode.value = 'edit'
}

function closeModal() {
  if (saving.value || entering.value) return
  modalMode.value = null
  editing.value = null
  name.value = ''
  description.value = ''
  formError.value = null
}

async function enterBase(item: KnowledgeBaseItem) {
  entering.value = { id: item.id, name: item.name }
  modalMode.value = null
  name.value = ''
  description.value = ''
  formError.value = null
  await new Promise((r) => setTimeout(r, 480))
  await router.push(`/knowledge/${item.id}`)
}

async function submitModal() {
  if (saving.value || entering.value) return
  const title = name.value.trim()
  if (!title) {
    formError.value = '请填写知识库名称'
    return
  }
  formError.value = null
  saving.value = true
  try {
    if (modalMode.value === 'edit' && editing.value) {
      const item = await updateKnowledgeBase(editing.value.id, {
        name: title,
        description: description.value.trim(),
      })
      items.value = items.value.map((x) => (x.id === item.id ? { ...x, ...item } : x))
      toast.value = { type: 'ok', msg: `已更新「${item.name}」` }
      modalMode.value = null
      editing.value = null
      name.value = ''
      description.value = ''
      formError.value = null
    } else {
      const item = await createKnowledgeBase({
        name: title,
        description: description.value.trim() || undefined,
      })
      items.value = [item, ...items.value.filter((x) => x.id !== item.id)]
      await enterBase(item)
    }
  } catch (e) {
    const msg =
      e instanceof Error
        ? e.message
        : modalMode.value === 'edit'
          ? '保存失败'
          : '创建失败'
    formError.value = msg
    toast.value = { type: 'err', msg }
    redirectLoginIfNeeded(e)
  } finally {
    saving.value = false
  }
}

function askDelete(base: KnowledgeBaseItem, e: Event) {
  if (!base.canManage) return
  e.preventDefault()
  e.stopPropagation()
  pendingDelete.value = base
}

function openAcl(base: KnowledgeBaseItem, e: Event) {
  if (!base.canManage) return
  e.preventDefault()
  e.stopPropagation()
  aclTarget.value = base
}

async function confirmDelete() {
  if (!pendingDelete.value?.canManage) return
  const base = pendingDelete.value
  deletingId.value = base.id
  try {
    await deleteKnowledgeBase(base.id)
    items.value = items.value.filter((x) => x.id !== base.id)
    pendingDelete.value = null
    toast.value = { type: 'ok', msg: '知识库已删除' }
  } catch (err) {
    toast.value = {
      type: 'err',
      msg: err instanceof Error ? err.message : '删除失败',
    }
    redirectLoginIfNeeded(err)
  } finally {
    deletingId.value = null
  }
}
</script>

<template>
  <div class="space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <div class="inline-flex items-center gap-2 mb-1.5">
          <span
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-molybdenum/25 bg-molybdenum/10 text-molybdenum text-[10px] font-mono"
          >
            <Brain class="size-3" />
            AI 检索
          </span>
        </div>
        <h1 class="text-xl font-semibold">知识库</h1>
        <p class="mt-1 text-[12px] text-text-secondary">
          自定义创建多个知识库；进入库内上传 PDF / Word / Excel / 文本，或粘贴、抓取 URL。
        </p>
      </div>
      <div class="flex items-center gap-1 rounded-md border border-hairline p-0.5 self-start">
        <button
          v-for="opt in [
            { id: 'view', label: '全部' },
            { id: 'use', label: '我能用' },
            { id: 'manage', label: '我能维护' },
          ] as { id: KbAccess; label: string }[]"
          :key="opt.id"
          type="button"
          class="h-7 px-2.5 rounded text-[11px]"
          :class="
            accessFilter === opt.id
              ? 'bg-molybdenum/15 text-molybdenum'
              : 'text-text-muted hover:text-text-primary'
          "
          @click="accessFilter = opt.id"
        >
          {{ opt.label }}
        </button>
      </div>
    </header>

    <div
      v-if="loading"
      class="py-16 text-center text-[12px] text-text-secondary"
    >
      <Loader2 class="inline size-4 animate-spin mr-2" />
      正在加载知识库列表...
    </div>

    <div v-else class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
      <RouterLink
        v-for="(base, idx) in items"
        :key="base.id"
        :to="`/knowledge/${base.id}`"
        :class="[
          'group relative overflow-hidden border border-hairline rounded-xl',
          'min-h-[160px] flex flex-col p-4 transition-all duration-200',
          accentOf(base, idx).cardBg,
          accentOf(base, idx).hoverBorder,
          accentOf(base, idx).glow,
          'hover:-translate-y-0.5',
        ]"
      >
        <span
          :class="['absolute left-0 top-0 bottom-0 w-1 rounded-l-xl', accentOf(base, idx).bar]"
        />
        <span
          class="pointer-events-none absolute -right-6 -top-6 size-24 rounded-full opacity-[0.12] blur-2xl"
          :class="accentOf(base, idx).bar"
        />

        <div class="flex items-start justify-between gap-2 pl-1.5">
          <div class="flex items-center gap-2.5 min-w-0">
            <span
              :class="[
                'size-10 rounded-lg inline-flex items-center justify-center shrink-0',
                accentOf(base, idx).iconWrap,
                accentOf(base, idx).icon,
              ]"
            >
              <component
                :is="cardIcon(idx)"
                class="size-4"
              />
            </span>
            <div class="min-w-0">
              <div
                :class="[
                  'text-[14px] font-semibold text-text-primary truncate transition-colors',
                  accentOf(base, idx).hoverTitle,
                ]"
              >
                {{ base.name }}
              </div>
              <div class="text-[11px] text-text-muted mt-0.5">
                更新于 {{ fmtAgo(base.updatedAt || base.createdAt, base.createdAtUtc) }}
              </div>
            </div>
          </div>
          <div
            v-if="base.canManage"
            class="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <button
              type="button"
              title="分配权限"
              class="size-8 rounded-md text-text-muted hover:text-molybdenum hover:bg-molybdenum/10 inline-flex items-center justify-center"
              @click="openAcl(base, $event)"
            >
              <Shield class="size-3.5" />
            </button>
            <button
              type="button"
              title="编辑知识库"
              class="size-8 rounded-md text-text-muted hover:text-molybdenum hover:bg-molybdenum/10 inline-flex items-center justify-center"
              @click="openEdit(base, $event)"
            >
              <Pencil class="size-3.5" />
            </button>
            <button
              type="button"
              title="删除知识库"
              :disabled="deletingId === base.id"
              class="size-8 rounded-md text-text-muted hover:text-iron hover:bg-iron/10 inline-flex items-center justify-center"
              @click="askDelete(base, $event)"
            >
              <Loader2
                v-if="deletingId === base.id"
                class="size-3.5 animate-spin"
              />
              <Trash2
                v-else
                class="size-3.5"
              />
            </button>
          </div>
        </div>
        <p class="mt-3 pl-1.5 text-[12px] text-text-secondary line-clamp-2 flex-1 leading-relaxed">
          {{ base.description || '暂无描述' }}
        </p>
        <div class="mt-3 pl-1.5 flex flex-wrap items-center gap-2">
          <span
            v-if="base.canManage"
            class="inline-flex items-center px-2 py-0.5 rounded-md border border-patina/30 bg-patina/10 text-patina text-[10px] font-mono"
          >
            可维护
          </span>
          <span
            v-else-if="base.canUse"
            class="inline-flex items-center px-2 py-0.5 rounded-md border border-molybdenum/30 bg-molybdenum/10 text-molybdenum text-[10px] font-mono"
          >
            可使用
          </span>
          <span
            v-else-if="base.canView"
            class="inline-flex items-center px-2 py-0.5 rounded-md border border-hairline text-text-muted text-[10px] font-mono"
          >
            仅查看
          </span>
          <span
            :class="[
              'inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[10px] font-mono',
              accentOf(base, idx).pill,
            ]"
          >
            <FileText class="size-3" />
            {{ base.docCount }} 资料
          </span>
          <span
            :class="[
              'inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[10px] font-mono',
              accentOf(base, idx).pill,
            ]"
          >
            <Layers class="size-3" />
            {{ base.chunkCount }} 切块
          </span>
        </div>
      </RouterLink>

      <button
        v-if="canCreate"
        type="button"
        class="relative overflow-hidden border-2 border-dashed border-hairline rounded-xl min-h-[160px] p-4 flex flex-col items-center justify-center gap-2 text-text-secondary hover:border-molybdenum/45 hover:text-molybdenum hover:bg-molybdenum/[0.04] transition-all duration-200 hover:-translate-y-0.5"
        @click="openCreate"
      >
        <span
          class="size-11 rounded-full border border-current/25 bg-bg-elevated/60 inline-flex items-center justify-center shadow-sm"
        >
          <Plus class="size-5" />
        </span>
        <span class="text-[13px] font-medium">增加知识库</span>
        <span class="text-[11px] text-text-muted">自定义名称，创建后导入资料</span>
      </button>
    </div>

    <div v-if="!loading && items.length === 0" class="text-[12px] text-text-muted">
      {{
        canCreate
          ? '还没有知识库。点击「增加知识库」开始创建，例如：缺陷库、运维库、能耗库等。'
          : '暂无可访问的知识库。请联系管理员分配权限。'
      }}
    </div>

    <KbAclDialog
      :open="Boolean(aclTarget)"
      :base-id="aclTarget?.id || ''"
      :base-name="aclTarget?.name"
      @close="aclTarget = null"
      @saved="void load()"
    />

    <!-- Create / Edit modal -->
    <div
      v-if="modalMode"
      class="fixed inset-0 z-[60] bg-bg-base/80 backdrop-blur-sm flex items-center justify-center p-4"
      @click.self="closeModal"
    >
      <div class="w-full max-w-md rounded-lg border border-hairline bg-bg-elevated shadow-2xl">
        <div class="px-5 py-3.5 border-b border-hairline">
          <div class="text-[14px] font-medium">
            {{ modalMode === 'edit' ? '编辑知识库' : '新建知识库' }}
          </div>
          <div class="text-[11px] text-text-muted mt-0.5">
            {{
              modalMode === 'edit'
                ? '可修改名称与描述'
                : '名称可自定义，创建后可随时改名'
            }}
          </div>
        </div>
        <div class="px-5 py-4 space-y-3">
          <label class="block">
            <div class="text-[11px] text-text-secondary mb-1">名称（必填）</div>
            <input
              v-model="name"
              autofocus
              class="kb-input"
              placeholder="如：缺陷库 / 运维经验库"
              :disabled="saving"
              @input="formError && (formError = null)"
              @keydown.enter="!saving && submitModal()"
            />
          </label>
          <label class="block">
            <div class="text-[11px] text-text-secondary mb-1">描述（选填）</div>
            <textarea
              v-model="description"
              class="kb-input min-h-[80px] font-sans"
              placeholder="简要说明该库用途"
              :disabled="saving"
            />
          </label>
          <div
            v-if="formError"
            class="rounded-md border border-iron/40 bg-iron/10 px-3 py-2 text-[12px] text-iron leading-relaxed"
          >
            {{ formError }}
          </div>
        </div>
        <div class="px-5 py-3 border-t border-hairline flex justify-end gap-2">
          <button
            type="button"
            class="kb-btn-secondary"
            :disabled="saving"
            @click="closeModal"
          >
            取消
          </button>
          <button
            type="button"
            class="kb-btn-primary"
            :disabled="saving || !name.trim()"
            :aria-busy="saving"
            @click="submitModal()"
          >
            <template v-if="saving">
              <Loader2 class="size-3.5 animate-spin" />
              {{ modalMode === 'edit' ? '保存中…' : '创建中…' }}
            </template>
            <template v-else-if="modalMode === 'edit'">
              <Pencil class="size-3.5" />
              保存
            </template>
            <template v-else>
              <Plus class="size-3.5" />
              创建
            </template>
          </button>
        </div>
      </div>
    </div>

    <!-- Delete confirm -->
    <div
      v-if="pendingDelete"
      class="fixed inset-0 z-[60] bg-bg-base/80 backdrop-blur-sm flex items-center justify-center p-4"
      @click.self="!deletingId && (pendingDelete = null)"
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
              <div class="text-[14px] font-medium text-text-primary">删除知识库</div>
              <div class="text-[12px] text-text-secondary leading-relaxed">
                确认删除「
                <span class="text-text-primary font-medium">{{ pendingDelete.name }}</span>
                」？库内资料与向量将一并删除，且不可恢复。
              </div>
            </div>
          </div>
        </div>
        <div class="px-5 py-3 border-t border-hairline bg-bg-base/30 flex justify-end gap-2">
          <button
            type="button"
            :disabled="Boolean(deletingId)"
            class="h-8 px-3 text-[12px] rounded-md border border-hairline bg-transparent text-text-secondary hover:bg-hairline/40"
            @click="pendingDelete = null"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="Boolean(deletingId)"
            class="h-8 px-3 text-[12px] rounded-md bg-iron text-white hover:brightness-110 inline-flex items-center gap-1.5"
            @click="confirmDelete()"
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

    <!-- Entering transition -->
    <div
      v-if="entering"
      class="kb-enter-mask fixed inset-0 z-[70] flex items-center justify-center p-4"
    >
      <div
        class="kb-enter-card w-full max-w-sm rounded-lg border border-hairline bg-bg-elevated/95 px-6 py-8 text-center shadow-2xl"
      >
        <span
          class="mx-auto mb-4 size-11 rounded-md bg-molybdenum/15 text-molybdenum inline-flex items-center justify-center"
        >
          <BookOpen class="size-5" />
        </span>
        <div class="text-[14px] font-medium text-text-primary">创建成功</div>
        <div class="mt-1.5 text-[12px] text-text-secondary line-clamp-1">
          正在进入「{{ entering.name }}」
        </div>
        <div class="mt-5 inline-flex items-center gap-2 text-[11px] font-mono text-text-muted">
          <Loader2 class="size-3.5 animate-spin text-iron" />
          即将打开知识库…
        </div>
        <div class="kb-enter-bar mt-5 mx-auto h-[2px] w-28 overflow-hidden rounded-full bg-hairline/60">
          <div class="kb-enter-bar-inner h-full rounded-full bg-iron" />
        </div>
      </div>
    </div>

    <div
      v-if="toast"
      class="fixed top-6 left-1/2 z-[80] -translate-x-1/2 px-4 py-2.5 rounded-md text-[12px] shadow-lg border max-w-[min(92vw,28rem)] text-center"
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
:deep(.kb-input:disabled) {
  opacity: 0.6;
  cursor: not-allowed;
}
.kb-enter-mask {
  background: rgba(8, 15, 28, 0.72);
  backdrop-filter: blur(6px);
  animation: kbEnterMask 280ms cubic-bezier(0.25, 0.8, 0.25, 1) both;
}
.kb-enter-card {
  animation: kbEnterCard 360ms cubic-bezier(0.25, 0.8, 0.25, 1) both;
}
.kb-enter-bar-inner {
  width: 40%;
  animation: kbEnterBar 480ms cubic-bezier(0.25, 0.8, 0.25, 1) both;
}
@keyframes kbEnterMask {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
@keyframes kbEnterCard {
  from {
    opacity: 0;
    transform: translateY(8px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}
@keyframes kbEnterBar {
  from {
    transform: translateX(-120%);
  }
  to {
    transform: translateX(220%);
  }
}
</style>

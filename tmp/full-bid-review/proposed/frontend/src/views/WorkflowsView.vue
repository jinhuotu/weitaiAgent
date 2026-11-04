<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Loader2, Plus, Trash2, Workflow } from 'lucide-vue-next'
import { PageHeader, Panel, Tag } from '@/components/ui-kit'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/lib/api'
import {
  createWorkflow,
  deleteWorkflow,
  listWorkflows,
  updateWorkflow,
  type WorkflowItem,
} from '@/lib/workflows-api'
import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'

const auth = useAuthStore()
const router = useRouter()

const showGate = computed(() => auth.loading || (!auth.isAdmin && !auth.loading))
const items = ref<WorkflowItem[]>([])
const loading = ref(true)
const creating = ref(false)
const error = ref('')
const pendingDelete = ref<WorkflowItem | null>(null)
const deleting = ref(false)
const togglingId = ref<string | null>(null)

async function reload() {
  loading.value = true
  error.value = ''
  try {
    items.value = await listWorkflows()
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function onCreate() {
  creating.value = true
  error.value = ''
  try {
    const item = await createWorkflow({
      name: `工作流 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
      domain: 'ai',
    })
    if (!item?.id) {
      throw new Error('创建成功但未返回工作流 ID，请刷新列表后重试')
    }
    // 先刷新列表，避免进编辑页失败时仍显示「暂无」
    await reload()
    await router.push({ name: 'workflow-editor', params: { id: item.id } })
  } catch (e) {
    error.value = e instanceof Error ? e.message : '创建失败'
    // 导航失败时仍尝试留在列表并展示已创建项
    try {
      await reload()
    } catch {
      /* ignore */
    }
  } finally {
    creating.value = false
  }
}

async function confirmDelete() {
  if (!pendingDelete.value) return
  deleting.value = true
  error.value = ''
  try {
    await deleteWorkflow(pendingDelete.value.id)
    pendingDelete.value = null
    await reload()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '删除失败'
    // 保留弹窗，方便在 API 恢复后再次确认
  } finally {
    deleting.value = false
  }
}

async function onToggleEnabled(w: WorkflowItem, next: boolean) {
  if (!w.publishedVersion) {
    error.value = '请先发布后再启停；未发布不可对外引用'
    return
  }
  togglingId.value = w.id
  error.value = ''
  try {
    const updated = await updateWorkflow(w.id, { enabled: next })
    items.value = items.value.map((x) => (x.id === w.id ? updated : x))
  } catch (e) {
    error.value = e instanceof Error ? e.message : '更新状态失败'
  } finally {
    togglingId.value = null
  }
}

onMounted(() => {
  if (auth.isAdmin) void reload()
})
</script>

<template>
  <div v-if="showGate" class="p-8 text-sm text-muted-foreground">
    <span v-if="auth.loading">加载中…</span>
    <span v-else>仅管理员可管理工作流。</span>
  </div>

  <template v-else>
    <PageHeader
      title="工作流"
      description="编排知识检索、LLM、场景智能体与 MCP 工具。草稿可试跑；发布后才可启停并供其它页面引用。"
    >
      <template #actions>
        <button
          type="button"
          class="h-8 px-3 inline-flex items-center gap-1.5 text-xs rounded-md bg-iron text-background hover:bg-iron/90 disabled:opacity-50"
          :disabled="creating"
          @click="onCreate"
        >
          <Loader2 v-if="creating" class="size-3.5 animate-spin" />
          <Plus v-else class="size-3.5" />
          新建工作流
        </button>
      </template>
    </PageHeader>

    <p v-if="error" class="mb-3 text-xs text-iron">{{ error }}</p>

    <Panel title="工作流列表" subtitle="点击进入画布编辑与试跑" flush>
      <div v-if="loading" class="p-6 text-xs text-muted-foreground inline-flex items-center gap-1.5">
        <Loader2 class="size-3.5 animate-spin" />加载中…
      </div>
      <div v-else-if="!items.length" class="p-8 text-center text-xs text-muted-foreground">
        暂无工作流，点击「新建工作流」开始。
      </div>
      <table v-else class="w-full text-xs">
        <thead class="text-muted-foreground bg-background/40">
          <tr class="border-b border-border">
            <th class="text-left font-medium px-4 py-2.5">名称</th>
            <th class="text-left font-medium px-4 py-2.5">域</th>
            <th class="text-left font-medium px-4 py-2.5">草稿</th>
            <th class="text-left font-medium px-4 py-2.5">已发布</th>
            <th class="text-left font-medium px-4 py-2.5">对外状态</th>
            <th class="text-left font-medium px-4 py-2.5">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          <tr
            v-for="w in items"
            :key="w.id"
            class="hover:bg-background/40 cursor-pointer"
            @click="router.push(`/workflows/${w.id}`)"
          >
            <td class="px-4 py-3">
              <div class="inline-flex items-center gap-2 font-medium">
                <Workflow class="size-3.5 text-molybdenum" />
                {{ w.name }}
              </div>
              <div v-if="w.remark" class="text-[10px] text-muted-foreground mt-0.5">
                {{ w.remark }}
              </div>
            </td>
            <td class="px-4 py-3"><Tag tone="molybdenum">{{ w.domain }}</Tag></td>
            <td class="px-4 py-3 data-num text-muted-foreground">
              v{{ w.draftVersion?.version ?? '—' }}
            </td>
            <td class="px-4 py-3 data-num text-muted-foreground">
              {{ w.publishedVersion ? `v${w.publishedVersion.version}` : '未发布' }}
            </td>
            <td class="px-4 py-3" @click.stop>
              <template v-if="w.publishedVersion">
                <label
                  class="inline-flex items-center gap-2 select-none"
                  :class="togglingId === w.id ? 'opacity-60' : ''"
                  :title="w.enabled ? '已启用：可供其它页面引用' : '已停用：其它页面不可引用'"
                >
                  <input
                    type="checkbox"
                    class="size-3.5 accent-patina"
                    :checked="w.enabled"
                    :disabled="togglingId === w.id"
                    @change="
                      onToggleEnabled(
                        w,
                        ($event.target as HTMLInputElement).checked,
                      )
                    "
                  />
                  <span :class="w.enabled ? 'text-patina' : 'text-muted-foreground'">
                    {{ w.enabled ? '启用' : '停用' }}
                  </span>
                  <Loader2
                    v-if="togglingId === w.id"
                    class="size-3 animate-spin text-muted-foreground"
                  />
                </label>
              </template>
              <span
                v-else
                class="text-muted-foreground"
                title="发布后才可启停并对外引用；草稿仍可编辑与试跑"
              >
                —
                <span class="ml-1 text-[10px]">发布后可启停</span>
              </span>
            </td>
            <td class="px-4 py-3" @click.stop>
              <button
                type="button"
                class="inline-flex items-center gap-1 text-iron hover:underline"
                @click="pendingDelete = w"
              >
                <Trash2 class="size-3" />删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </Panel>

    <AppAlertDialog
      :open="Boolean(pendingDelete)"
      title="删除工作流"
      :description="`确定删除「${pendingDelete?.name || ''}」？版本与运行记录将一并删除。`"
      confirm-label="删除"
      destructive
      :loading="deleting"
      @cancel="pendingDelete = null"
      @update:open="(v) => { if (!v) pendingDelete = null }"
      @confirm="confirmDelete"
    />
  </template>
</template>

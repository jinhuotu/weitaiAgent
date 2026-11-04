<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Download, Search, Trash2 } from 'lucide-vue-next'
import { PageHeader, Panel } from '@/components/ui-kit'
import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'
import TenderTaskTable from '@/components/tenders/TenderTaskTable.vue'
import TenderDocPreviewDialog from '@/components/tenders/TenderDocPreviewDialog.vue'
import {
  PROJECT_TYPES,
  TASK_STATUS_LABEL,
  TASK_STATUSES,
  TIME_RANGES,
  exportTasksCsv,
  matchKeyword,
  matchTimeRange,
  paginate,
  useTenderTasks,
  type TaskStatus,
  type TenderTask,
  type TimeRange,
} from '@/lib/tender-tasks'

const PAGE_SIZE = 10
const router = useRouter()
const { tasks, owners, loading, error, load, submitApproval, removeTasks } = useTenderTasks()

const keyword = ref('')
const status = ref<'all' | TaskStatus>('all')
const timeRange = ref<TimeRange>('all')
const owner = ref('all')
const projectType = ref('all')
const page = ref(1)
const selected = ref<string[]>([])
const preview = ref<TenderTask | null>(null)
const toast = ref('')
const confirmOpen = ref(false)
const removing = ref(false)

const filtered = computed(() =>
  tasks.value.filter((t) => {
    if (status.value !== 'all' && t.status !== status.value) return false
    if (owner.value !== 'all' && t.owner !== owner.value) return false
    if (projectType.value !== 'all' && t.projectType !== projectType.value) return false
    if (!matchTimeRange(t, timeRange.value)) return false
    return matchKeyword(t, keyword.value)
  }),
)

const pageRows = computed(() => paginate(filtered.value, page.value, PAGE_SIZE))

watch([keyword, status, timeRange, owner, projectType], () => {
  page.value = 1
})

onMounted(() => {
  void load('all')
})

function openView(task: TenderTask) {
  preview.value = task
}

function openEdit(task: TenderTask) {
  void router.push({ path: '/tenders', query: { record: task.recordId } })
}

async function onSubmit(task: TenderTask) {
  try {
    await submitApproval(task)
    toast.value = `已提交审批：${task.projectName}`
  } catch (e) {
    toast.value = e instanceof Error ? e.message : '提交审批失败'
  }
  window.setTimeout(() => {
    toast.value = ''
  }, 2400)
}

function onExport() {
  const rows = selected.value.length
    ? filtered.value.filter((t) => selected.value.includes(t.id))
    : filtered.value
  exportTasksCsv(rows, `投标任务_${new Date().toISOString().slice(0, 10)}.csv`)
  toast.value = `已导出 ${rows.length} 条`
  window.setTimeout(() => {
    toast.value = ''
  }, 2000)
}

async function confirmRemove() {
  if (!selected.value.length) return
  removing.value = true
  try {
    const { ok, fail } = await removeTasks(selected.value)
    selected.value = []
    confirmOpen.value = false
    toast.value = fail ? `已删除 ${ok} 条，失败 ${fail} 条` : `已删除 ${ok} 条`
  } finally {
    removing.value = false
    window.setTimeout(() => {
      toast.value = ''
    }, 2400)
  }
}
</script>

<template>
  <PageHeader
    title="全部投标任务"
    description="查看全部投标任务，支持按状态、时间、负责人和项目类型筛选，以及导出与批量删除。"
  >
    <template #actions>
      <button
        type="button"
        class="h-8 px-3 inline-flex items-center gap-1.5 text-xs rounded-md border border-sulfur/40 text-sulfur hover:bg-sulfur/10"
        @click="onExport"
      >
        <Download class="size-3.5" />
        导出
      </button>
    </template>
  </PageHeader>

  <p v-if="error" class="mb-4 text-xs text-iron">{{ error }}</p>
  <p v-if="toast" class="mb-4 text-xs text-patina">{{ toast }}</p>

  <Panel title="全部投标任务" :subtitle="`共 ${filtered.length} 条`" flush>
    <template #action>
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="h-7 px-2.5 inline-flex items-center gap-1 text-[11px] rounded-md border border-border hover:bg-accent"
          @click="onExport"
        >
          <Download class="size-3" />
          批量导出
        </button>
        <button
          type="button"
          class="h-7 px-2.5 inline-flex items-center gap-1 text-[11px] rounded-md border border-iron/40 text-iron hover:bg-iron/10 disabled:opacity-40"
          :disabled="!selected.length"
          @click="confirmOpen = true"
        >
          <Trash2 class="size-3" />
          批量删除
        </button>
      </div>
    </template>

    <div class="px-4 py-3 flex flex-wrap gap-2 border-b border-border">
      <input
        v-model="keyword"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background min-w-[200px] flex-1"
        placeholder="搜索项目 / 招标方 / 负责人…"
      />
      <select
        v-model="status"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background min-w-[120px]"
      >
        <option value="all">全部状态</option>
        <option v-for="s in TASK_STATUSES" :key="s" :value="s">
          {{ TASK_STATUS_LABEL[s] }}
        </option>
      </select>
      <select
        v-model="timeRange"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background min-w-[110px]"
      >
        <option v-for="r in TIME_RANGES" :key="r.value" :value="r.value">{{ r.label }}</option>
      </select>
      <select
        v-model="owner"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background min-w-[120px]"
      >
        <option value="all">全部负责人</option>
        <option v-for="name in owners" :key="name" :value="name">{{ name }}</option>
      </select>
      <select
        v-model="projectType"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background min-w-[120px]"
      >
        <option value="all">全部类型</option>
        <option v-for="tp in PROJECT_TYPES" :key="tp" :value="tp">{{ tp }}</option>
      </select>
      <button
        type="button"
        class="h-8 px-3 inline-flex items-center gap-1.5 text-xs rounded-md border border-border hover:bg-accent"
        @click="page = 1"
      >
        <Search class="size-3.5" />
        搜索
      </button>
    </div>

    <TenderTaskTable
      :rows="pageRows"
      :total="filtered.length"
      :page="page"
      :page-size="PAGE_SIZE"
      :loading="loading"
      :selected="selected"
      show-checkbox
      show-type
      empty-text="暂无投标任务"
      @update:page="page = $event"
      @update:selected="selected = $event"
      @view="openView"
      @edit="openEdit"
      @submit="onSubmit"
    />
  </Panel>

  <TenderDocPreviewDialog :open="Boolean(preview)" :task="preview" @update:open="(v) => !v && (preview = null)" />

  <AppAlertDialog
    :open="confirmOpen"
    title="批量删除投标任务"
    :description="`将删除已选中的 ${selected.length} 条任务，并同步删除对应的 Word 文件。`"
    confirm-label="删除"
    destructive
    :loading="removing"
    @update:open="confirmOpen = $event"
    @confirm="confirmRemove"
  />
</template>

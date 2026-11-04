<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ClipboardList, Search } from 'lucide-vue-next'
import { PageHeader, Panel, KpiCard } from '@/components/ui-kit'
import TenderTaskTable from '@/components/tenders/TenderTaskTable.vue'
import TenderDocPreviewDialog from '@/components/tenders/TenderDocPreviewDialog.vue'
import {
  TASK_STATUS_LABEL,
  TASK_STATUSES,
  matchKeyword,
  paginate,
  useTenderTasks,
  type TaskStatus,
  type TenderTask,
} from '@/lib/tender-tasks'

const PAGE_SIZE = 8
const router = useRouter()
const route = useRoute()
const { myTasks, loading, error, load, submitApproval, markResult } = useTenderTasks()

const keyword = ref('')
const status = ref<'all' | TaskStatus>('all')
const page = ref(1)
const preview = ref<TenderTask | null>(null)
const toast = ref('')

const filtered = computed(() =>
  myTasks.value.filter((t) => {
    if (status.value !== 'all' && t.status !== status.value) return false
    return matchKeyword(t, keyword.value)
  }),
)

const pageRows = computed(() => paginate(filtered.value, page.value, PAGE_SIZE))

const processingCount = computed(
  () => myTasks.value.filter((t) => t.status === 'processing').length,
)
const pendingCount = computed(() => myTasks.value.filter((t) => t.status === 'pending').length)
const wonCount = computed(() => myTasks.value.filter((t) => t.status === 'won').length)

watch([keyword, status], () => {
  page.value = 1
})

function applyStatusFromQuery() {
  const raw = String(route.query.status || 'all')
  if (raw === 'all' || TASK_STATUSES.includes(raw as TaskStatus)) {
    status.value = raw as 'all' | TaskStatus
  }
}

onMounted(() => {
  applyStatusFromQuery()
  void load('mine')
})

watch(() => route.query.status, applyStatusFromQuery)

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

async function onMark(task: TenderTask, status: 'submitted' | 'won' | 'lost') {
  try {
    await markResult(task, status)
    toast.value = `已更新：${task.projectName}`
  } catch (e) {
    toast.value = e instanceof Error ? e.message : '更新失败'
  }
  window.setTimeout(() => {
    toast.value = ''
  }, 2400)
}
</script>

<template>
  <PageHeader
    title="工作任务"
    description="查看本人负责的投标任务，按状态筛选后可进入编制、提交审批或查看详情。"
  >
    <template #actions>
      <button
        type="button"
        class="h-8 px-3 inline-flex items-center gap-1.5 text-xs rounded-md bg-iron text-background hover:bg-iron/90"
        @click="router.push('/tenders')"
      >
        去编制标书
      </button>
    </template>
  </PageHeader>

  <p v-if="error" class="mb-4 text-xs text-iron">{{ error }}</p>
  <p
    v-if="toast"
    class="mb-4 text-xs text-patina"
  >
    {{ toast }}
  </p>

  <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
    <KpiCard label="我的任务" :value="String(myTasks.length)" unit="个" tone="molybdenum">
      <template #icon><ClipboardList class="size-4" /></template>
    </KpiCard>
    <KpiCard label="编制中" :value="String(processingCount)" unit="个" tone="coolant" />
    <KpiCard label="待审批" :value="String(pendingCount)" unit="个" tone="sulfur" />
    <KpiCard label="已中标" :value="String(wonCount)" unit="个" tone="patina" />
  </div>

  <Panel title="我的投标任务" :subtitle="`共 ${filtered.length} 条记录`" flush>
    <div class="px-4 py-3 flex flex-wrap gap-2 border-b border-border">
      <input
        v-model="keyword"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background min-w-[220px] flex-1"
        placeholder="搜索项目名称 / 招标方…"
        @keydown.enter.prevent
      />
      <select
        v-model="status"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background min-w-[140px]"
      >
        <option value="all">全部状态</option>
        <option v-for="s in TASK_STATUSES" :key="s" :value="s">
          {{ TASK_STATUS_LABEL[s] }}
        </option>
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
      empty-text="暂无工作任务。可从「AI标书生成」生成标书后在此跟进。"
      @update:page="page = $event"
      @view="openView"
      @edit="openEdit"
      @submit="onSubmit"
      @mark="onMark"
    />
  </Panel>

  <TenderDocPreviewDialog :open="Boolean(preview)" :task="preview" @update:open="(v) => !v && (preview = null)" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'
import TenderStatusTag from '@/components/tenders/TenderStatusTag.vue'
import {
  formatBudget,
  pageCount,
  type TenderTask,
} from '@/lib/tender-tasks'
import { cn } from '@/lib/utils'

const props = withDefaults(
  defineProps<{
    rows: TenderTask[]
    total: number
    page: number
    pageSize: number
    loading?: boolean
    emptyText?: string
    showCheckbox?: boolean
    showType?: boolean
    selected?: string[]
  }>(),
  {
    loading: false,
    emptyText: '暂无投标任务',
    showCheckbox: false,
    showType: false,
    selected: () => [],
  },
)

const emit = defineEmits<{
  'update:page': [value: number]
  'update:selected': [value: string[]]
  view: [task: TenderTask]
  edit: [task: TenderTask]
  submit: [task: TenderTask]
  mark: [task: TenderTask, status: 'submitted' | 'won' | 'lost']
}>()

const pages = computed(() => pageCount(props.total, props.pageSize))

const allChecked = computed(
  () => props.rows.length > 0 && props.rows.every((r) => props.selected.includes(r.id)),
)

function toggleAll() {
  if (allChecked.value) {
    const ids = new Set(props.rows.map((r) => r.id))
    emit(
      'update:selected',
      props.selected.filter((id) => !ids.has(id)),
    )
    return
  }
  const extra = props.rows.map((r) => r.id).filter((id) => !props.selected.includes(id))
  emit('update:selected', [...props.selected, ...extra])
}

function toggleOne(id: string) {
  emit(
    'update:selected',
    props.selected.includes(id)
      ? props.selected.filter((x) => x !== id)
      : [...props.selected, id],
  )
}

function go(p: number) {
  const next = Math.min(pages.value, Math.max(1, p))
  if (next !== props.page) emit('update:page', next)
}

function canEdit(task: TenderTask) {
  return task.status === 'processing'
}

function canSubmit(task: TenderTask) {
  return task.status === 'processing'
}

function canMarkSubmitted(task: TenderTask) {
  return task.status === 'approved'
}

function canMarkOutcome(task: TenderTask) {
  return task.status === 'submitted'
}

const pageButtons = computed(() => {
  const total = pages.value
  const current = props.page
  const list: number[] = []
  for (let i = 1; i <= total; i += 1) {
    if (total <= 5 || i === 1 || i === total || Math.abs(i - current) <= 1) {
      list.push(i)
    }
  }
  return list
})
</script>

<template>
  <div>
    <div
      v-if="loading && !rows.length"
      class="py-16 text-center text-xs text-muted-foreground"
    >
      加载任务…
    </div>
    <div
      v-else-if="!rows.length"
      class="py-16 text-center text-xs text-muted-foreground"
    >
      {{ emptyText }}
    </div>
    <div v-else class="overflow-x-auto">
      <table class="w-full text-xs min-w-[760px]">
        <thead class="text-muted-foreground bg-background/40">
          <tr class="border-b border-border">
            <th v-if="showCheckbox" class="text-left font-medium px-4 py-2.5 w-10">
              <input
                type="checkbox"
                class="align-middle accent-iron"
                :checked="allChecked"
                @change="toggleAll"
              />
            </th>
            <th class="text-left font-medium px-4 py-2.5 w-14">序号</th>
            <th class="text-left font-medium px-4 py-2.5">项目名称</th>
            <th class="text-left font-medium px-4 py-2.5">招标方</th>
            <th class="text-left font-medium px-4 py-2.5">预算金额</th>
            <th class="text-left font-medium px-4 py-2.5">截止日期</th>
            <th class="text-left font-medium px-4 py-2.5">负责人</th>
            <th v-if="showType" class="text-left font-medium px-4 py-2.5">项目类型</th>
            <th class="text-left font-medium px-4 py-2.5">状态</th>
            <th class="text-left font-medium px-4 py-2.5">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          <tr v-for="(row, idx) in rows" :key="row.id" class="hover:bg-background/40">
            <td v-if="showCheckbox" class="px-4 py-3">
              <input
                type="checkbox"
                class="align-middle accent-iron"
                :checked="selected.includes(row.id)"
                @change="toggleOne(row.id)"
              />
            </td>
            <td class="px-4 py-3 data-num text-muted-foreground">
              {{ (page - 1) * pageSize + idx + 1 }}
            </td>
            <td class="px-4 py-3 font-medium max-w-[240px]">
              <div class="truncate" :title="row.projectName">{{ row.projectName }}</div>
            </td>
            <td class="px-4 py-3 text-foreground/80">{{ row.tenderer }}</td>
            <td class="px-4 py-3 data-num text-molybdenum">{{ formatBudget(row.budgetYuan) }}</td>
            <td class="px-4 py-3 data-num text-muted-foreground">{{ row.deadline || '—' }}</td>
            <td class="px-4 py-3">{{ row.owner || '—' }}</td>
            <td v-if="showType" class="px-4 py-3 text-foreground/80">{{ row.projectType }}</td>
            <td class="px-4 py-3">
              <TenderStatusTag :status="row.status" />
            </td>
            <td class="px-4 py-3 whitespace-nowrap">
              <button type="button" class="text-iron hover:underline" @click="emit('view', row)">
                查看
              </button>
              <template v-if="canEdit(row)">
                <span class="text-border mx-1.5">|</span>
                <button
                  type="button"
                  class="text-muted-foreground hover:text-foreground"
                  @click="emit('edit', row)"
                >
                  编辑
                </button>
              </template>
              <template v-if="canSubmit(row)">
                <span class="text-border mx-1.5">|</span>
                <button
                  type="button"
                  class="text-sulfur hover:underline"
                  @click="emit('submit', row)"
                >
                  提交审批
                </button>
              </template>
              <template v-if="canMarkSubmitted(row)">
                <span class="text-border mx-1.5">|</span>
                <button
                  type="button"
                  class="text-molybdenum hover:underline"
                  @click="emit('mark', row, 'submitted')"
                >
                  标记已提交
                </button>
              </template>
              <template v-if="canMarkOutcome(row)">
                <span class="text-border mx-1.5">|</span>
                <button
                  type="button"
                  class="text-patina hover:underline"
                  @click="emit('mark', row, 'won')"
                >
                  中标
                </button>
                <span class="text-border mx-1.5">|</span>
                <button
                  type="button"
                  class="text-iron hover:underline"
                  @click="emit('mark', row, 'lost')"
                >
                  未中标
                </button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div
      v-if="total > 0"
      class="flex items-center gap-1 px-4 py-3 border-t border-border text-[11px]"
    >
      <button
        type="button"
        class="size-7 rounded-md border border-border hover:bg-accent disabled:opacity-40"
        :disabled="page <= 1"
        @click="go(page - 1)"
      >
        <ChevronLeft class="size-3.5 mx-auto" />
      </button>
      <template v-for="(p, i) in pageButtons" :key="p">
        <span
          v-if="i > 0 && p - pageButtons[i - 1] > 1"
          class="px-1 text-muted-foreground"
        >
          …
        </span>
        <button
          type="button"
          :class="
            cn(
              'min-w-7 h-7 px-2 rounded-md border',
              p === page
                ? 'bg-iron/15 border-iron/30 text-iron'
                : 'border-border text-muted-foreground hover:bg-accent',
            )
          "
          @click="go(p)"
        >
          {{ p }}
        </button>
      </template>
      <button
        type="button"
        class="size-7 rounded-md border border-border hover:bg-accent disabled:opacity-40"
        :disabled="page >= pages"
        @click="go(page + 1)"
      >
        <ChevronRight class="size-3.5 mx-auto" />
      </button>
      <span class="ml-2 text-muted-foreground">共 {{ total }} 条</span>
    </div>
  </div>
</template>

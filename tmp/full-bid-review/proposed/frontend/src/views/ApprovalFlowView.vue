<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Pencil, Plus, Trash2 } from 'lucide-vue-next'
import { PageHeader, Panel } from '@/components/ui-kit'
import AppDialog from '@/components/ui/AppDialog.vue'
import ApprovalSteps from '@/components/tenders/ApprovalSteps.vue'
import TenderDocPreviewDialog from '@/components/tenders/TenderDocPreviewDialog.vue'
import { ApiError } from '@/lib/api'
import {
  formatBudget,
  stepIndex,
  stepsForTask,
  useTenderTasks,
  type TenderTask,
} from '@/lib/tender-tasks'
import {
  fetchApprovalFlow,
  saveApprovalFlow,
  type ApprovalReviewInput,
  type ApprovalStepDef,
} from '@/lib/tenders-api'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth'

type TabKey = 'pending' | 'done' | 'mine'
type DraftReview = { key: string; name: string; roleCode: string }

const router = useRouter()
const auth = useAuthStore()
const {
  pendingApprovals,
  doneApprovals,
  myApprovals,
  loading,
  error,
  load,
  decideApproval,
} = useTenderTasks()

const tab = ref<TabKey>('pending')
const preview = ref<TenderTask | null>(null)
const current = ref<TenderTask | null>(null)
const comment = ref('')
const toast = ref('')
const flowSteps = ref<ApprovalStepDef[]>([])
const flowRoles = ref<{ code: string; name: string }[]>([])
const editorOpen = ref(false)
const editorSaving = ref(false)
const editorError = ref('')
const drafts = ref<DraftReview[]>([])

const isSuperuser = computed(() => Boolean(auth.user?.is_superuser))
const canQa = computed(
  () => auth.isAdmin || (auth.menus || []).includes('/tender-qa') || (auth.menus || []).includes('/tenders'),
)

const exampleIndex = computed(() => -1)

const rows = computed(() => {
  if (tab.value === 'pending') return pendingApprovals.value
  if (tab.value === 'done') return doneApprovals.value
  return myApprovals.value
})

const tabTitle = computed(() =>
  tab.value === 'pending' ? '待我审批' : tab.value === 'done' ? '我已审批' : '我发起的',
)

function taskSteps(task: TenderTask | null): ApprovalStepDef[] {
  return stepsForTask(task, flowSteps.value)
}

async function loadFlow() {
  try {
    const payload = await fetchApprovalFlow()
    flowSteps.value = payload.steps || []
    flowRoles.value = (payload.roles || []).map((item) => ({
      code: item.code,
      name: item.name,
    }))
  } catch (err) {
    toast.value = err instanceof ApiError || err instanceof Error ? err.message : '加载审批流程失败'
  }
}

function openEditor() {
  const reviews = flowSteps.value.filter((item) => item.kind === 'review')
  drafts.value = (reviews.length ? reviews : flowSteps.value.slice(1, -1)).map((item) => ({
    key: item.key,
    name: item.name,
    roleCode: item.roleCode || '',
  }))
  if (!drafts.value.length) {
    drafts.value = [{ key: '', name: '审批', roleCode: '' }]
  }
  editorError.value = ''
  editorOpen.value = true
}

function addDraft() {
  drafts.value.push({ key: '', name: '', roleCode: '' })
}

function removeDraft(index: number) {
  if (drafts.value.length <= 1) return
  drafts.value.splice(index, 1)
}

function moveDraft(index: number, delta: number) {
  const next = index + delta
  if (next < 0 || next >= drafts.value.length) return
  const copy = drafts.value.slice()
  const [item] = copy.splice(index, 1)
  if (!item) return
  copy.splice(next, 0, item)
  drafts.value = copy
}

async function saveEditor() {
  const reviews: ApprovalReviewInput[] = drafts.value
    .map((item) => ({
      key: item.key || undefined,
      name: item.name.trim(),
      roleCode: item.roleCode.trim() || null,
    }))
    .filter((item) => item.name)
  if (!reviews.length) {
    editorError.value = '至少保留一个审批环节'
    return
  }
  editorSaving.value = true
  editorError.value = ''
  try {
    const payload = await saveApprovalFlow(reviews)
    flowSteps.value = payload.steps || []
    editorOpen.value = false
    toast.value = '审批流程已保存。新提交的任务将按新流程执行。'
  } catch (err) {
    editorError.value = err instanceof ApiError || err instanceof Error ? err.message : '保存失败'
  } finally {
    editorSaving.value = false
  }
  window.setTimeout(() => {
    toast.value = ''
  }, 2400)
}

onMounted(() => {
  void load('approval')
  void loadFlow()
})

function openView(task: TenderTask) {
  preview.value = task
}

function openEdit(task: TenderTask) {
  void router.push({ path: '/tenders', query: { record: task.recordId } })
}

function openApprove(task: TenderTask) {
  current.value = task
  comment.value = ''
}

function goQa(task: TenderTask) {
  void router.push({ path: '/tender-qa', query: { record: task.recordId } })
}

async function decide(passed: boolean) {
  if (!current.value) return
  const task = current.value
  try {
    await decideApproval(task, passed, comment.value)
    toast.value = passed ? `已通过：${task.projectName}` : `已驳回：${task.projectName}`
    current.value = null
  } catch (e) {
    toast.value = e instanceof Error ? e.message : '审批失败'
  }
  window.setTimeout(() => {
    toast.value = ''
  }, 2400)
}

const TASK_STATUS_FALLBACK: Record<string, string> = {
  draft: '草稿',
  processing: '编制中',
  pending: '待审批',
  approved: '审批通过',
  submitted: '已提交',
  won: '已中标',
  lost: '已失败',
}

function resultLabel(task: TenderTask) {
  if (task.approvalResult === 'passed') return { text: '已通过', cls: 'text-patina' }
  if (task.approvalResult === 'rejected') return { text: '已驳回', cls: 'text-iron' }
  return { text: task.currentStep || TASK_STATUS_FALLBACK[task.status] || '—', cls: '' }
}
</script>

<template>
  <PageHeader
    title="审批流程"
    description="处理待自己审批的投标任务；流程环节由超级管理员配置，并绑定「用户与权限」中的角色。"
  >
    <template v-if="isSuperuser" #actions>
      <button type="button" class="kb-btn-primary h-8 px-3 text-xs inline-flex items-center gap-1.5" @click="openEditor">
        <Pencil class="size-3.5" />
        配置流程
      </button>
    </template>
  </PageHeader>

  <p v-if="error" class="mb-4 text-xs text-iron">{{ error }}</p>
  <p v-if="toast" class="mb-4 text-xs text-patina">{{ toast }}</p>

  <div class="mb-4 flex items-center gap-1 text-xs flex-wrap">
    <button
      type="button"
      class="h-8 px-3 rounded-md border inline-flex items-center gap-1.5"
      :class="
        tab === 'pending'
          ? 'bg-iron/15 border-iron/30 text-iron'
          : 'border-border text-muted-foreground'
      "
      @click="tab = 'pending'"
    >
      待我审批
      <span
        class="inline-flex min-w-4 h-4 px-1 items-center justify-center rounded-full text-[10px] bg-sulfur/15 text-sulfur"
      >
        {{ pendingApprovals.length }}
      </span>
    </button>
    <button
      type="button"
      class="h-8 px-3 rounded-md border"
      :class="
        tab === 'done'
          ? 'bg-iron/15 border-iron/30 text-iron'
          : 'border-border text-muted-foreground'
      "
      @click="tab = 'done'"
    >
      我已审批
    </button>
    <button
      type="button"
      class="h-8 px-3 rounded-md border"
      :class="
        tab === 'mine'
          ? 'bg-iron/15 border-iron/30 text-iron'
          : 'border-border text-muted-foreground'
      "
      @click="tab = 'mine'"
    >
      我发起的
    </button>
  </div>

  <Panel class="mb-5" title="当前审批流程">
    <p class="mb-3 text-[11px] text-muted-foreground">
      未绑定角色的环节，拥有审批菜单的用户均可处理；绑定后仅该角色与超级管理员可审。已在途任务沿用提交时的流程。
    </p>
    <ApprovalSteps
      :steps="flowSteps.length ? flowSteps : ['提交申请', '部门经理审批', '总经理审批', '财务审核', '完成']"
      :current="exampleIndex"
      show-roles
    />
  </Panel>

  <Panel :title="tabTitle" :subtitle="`共 ${rows.length} 条`" flush>
    <div
      v-if="loading && !rows.length"
      class="py-16 text-center text-xs text-muted-foreground"
    >
      加载审批记录…
    </div>
    <div
      v-else-if="!rows.length"
      class="py-16 text-center text-xs text-muted-foreground"
    >
      当前没有{{ tabTitle }}的记录。
    </div>
    <div v-else class="overflow-x-auto">
      <table class="w-full text-xs min-w-[760px]">
        <thead class="text-muted-foreground bg-background/40">
          <tr class="border-b border-border">
            <th class="text-left font-medium px-4 py-2.5 w-14">序号</th>
            <th class="text-left font-medium px-4 py-2.5">项目名称</th>
            <th class="text-left font-medium px-4 py-2.5">招标方</th>
            <th class="text-left font-medium px-4 py-2.5">发起人</th>
            <th class="text-left font-medium px-4 py-2.5">发起时间</th>
            <th class="text-left font-medium px-4 py-2.5">预算金额</th>
            <th class="text-left font-medium px-4 py-2.5">当前环节</th>
            <th class="text-left font-medium px-4 py-2.5">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          <tr v-for="(row, idx) in rows" :key="row.id" class="hover:bg-background/40">
            <td class="px-4 py-3 data-num text-muted-foreground">{{ idx + 1 }}</td>
            <td class="px-4 py-3 font-medium max-w-[240px]">
              <div class="truncate" :title="row.projectName">{{ row.projectName }}</div>
            </td>
            <td class="px-4 py-3 text-foreground/80">{{ row.tenderer }}</td>
            <td class="px-4 py-3">{{ row.initiator || row.owner }}</td>
            <td class="px-4 py-3 data-num text-muted-foreground">
              {{ row.initiatedAt || '—' }}
            </td>
            <td class="px-4 py-3 data-num text-molybdenum">
              {{ formatBudget(row.budgetYuan) }}
            </td>
            <td class="px-4 py-3">
              <span :class="cn(resultLabel(row).cls)">{{ resultLabel(row).text }}</span>
            </td>
            <td class="px-4 py-3 whitespace-nowrap">
              <button
                v-if="tab === 'pending'"
                type="button"
                class="text-iron hover:underline"
                @click="openApprove(row)"
              >
                审批
              </button>
              <button
                v-else
                type="button"
                class="text-iron hover:underline"
                @click="tab === 'mine' && row.status === 'processing' ? openEdit(row) : openView(row)"
              >
                {{ tab === 'mine' && row.status === 'processing' ? '编辑' : '查看' }}
              </button>
              <button
                v-if="canQa"
                type="button"
                class="ml-2 text-muted-foreground hover:underline"
                @click="goQa(row)"
              >
                评分
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </Panel>

  <TenderDocPreviewDialog :open="Boolean(preview)" :task="preview" @update:open="(v) => !v && (preview = null)" />

  <AppDialog
    :open="Boolean(current)"
    :title="current ? `审批详情 · ${current.projectName}` : '审批详情'"
    wide
    @update:open="(v) => !v && (current = null)"
  >
    <div v-if="current" class="space-y-4">
      <div class="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        <div>
          <span class="text-muted-foreground">项目名称：</span>
          <span>{{ current.projectName }}</span>
        </div>
        <div>
          <span class="text-muted-foreground">招标方：</span>
          <span>{{ current.tenderer }}</span>
        </div>
        <div>
          <span class="text-muted-foreground">预算金额：</span>
          <span class="data-num text-molybdenum font-semibold">{{
            formatBudget(current.budgetYuan)
          }}</span>
        </div>
        <div>
          <span class="text-muted-foreground">发起人：</span>
          <span>{{ current.initiator || current.owner }}</span>
        </div>
        <div>
          <span class="text-muted-foreground">投标截止：</span>
          <span class="text-sulfur">{{ current.deadline || '—' }}</span>
        </div>
        <div>
          <span class="text-muted-foreground">当前环节：</span>
          <span class="text-iron">{{ current.currentStep || '待审批' }}</span>
        </div>
      </div>
      <div>
        <div class="text-[11px] text-muted-foreground mb-1">审批流程</div>
        <ApprovalSteps
          compact
          show-roles
          :steps="taskSteps(current)"
          :current="
            current.status === 'approved' || current.status === 'submitted' || current.status === 'won'
              ? Math.max(taskSteps(current).length - 1, 0)
              : stepIndex(current.currentStepKey || current.currentStep, taskSteps(current))
          "
        />
      </div>
      <label class="block">
        <div class="text-[11px] text-muted-foreground mb-1">审批意见</div>
        <textarea
          v-model="comment"
          rows="3"
          class="w-full px-3 py-2 text-xs rounded-md border border-border bg-background resize-y"
          placeholder="请输入审批意见…"
        />
      </label>
    </div>
    <template #footer>
      <button
        v-if="current && canQa"
        type="button"
        class="h-8 px-3 text-xs rounded-md border border-border hover:bg-accent"
        @click="goQa(current)"
      >
        去评分
      </button>
      <button
        type="button"
        class="h-8 px-3 text-xs rounded-md border border-iron/40 text-iron hover:bg-iron/10"
        @click="decide(false)"
      >
        驳回
      </button>
      <button
        type="button"
        class="kb-btn-primary h-8 px-3 text-xs"
        @click="decide(true)"
      >
        通过
      </button>
    </template>
  </AppDialog>

  <AppDialog
    :open="editorOpen"
    title="配置审批流程"
    description="「提交申请」与「完成」为系统节点。中间审批环节可增删改顺序，并绑定用户与权限中的角色。"
    wide
    @update:open="(v) => !v && (editorOpen = false)"
  >
    <div class="space-y-3">
      <p v-if="editorError" class="text-xs text-iron">{{ editorError }}</p>
      <div
        v-for="(item, index) in drafts"
        :key="`${item.key || 'new'}-${index}`"
        class="flex flex-wrap items-end gap-2 rounded-md border border-border px-3 py-2.5"
      >
        <div class="text-[11px] text-muted-foreground w-8 shrink-0 pb-2">{{ index + 1 }}</div>
        <label class="min-w-[10rem] flex-1">
          <div class="mb-1 text-[11px] text-muted-foreground">环节名称</div>
          <input
            v-model="item.name"
            class="h-8 w-full rounded-md border border-border bg-background px-2 text-xs"
            maxlength="16"
            placeholder="例如：部门经理审批"
          />
        </label>
        <label class="min-w-[10rem] flex-1">
          <div class="mb-1 text-[11px] text-muted-foreground">审批角色</div>
          <select
            v-model="item.roleCode"
            class="h-8 w-full rounded-md border border-border bg-background px-2 text-xs"
          >
            <option value="">不绑定（有审批菜单即可）</option>
            <option v-for="role in flowRoles" :key="role.code" :value="role.code">
              {{ role.name }}
            </option>
          </select>
        </label>
        <div class="flex items-center gap-1 pb-0.5">
          <button type="button" class="h-8 px-2 text-[11px] rounded-md border border-border" @click="moveDraft(index, -1)">
            上移
          </button>
          <button type="button" class="h-8 px-2 text-[11px] rounded-md border border-border" @click="moveDraft(index, 1)">
            下移
          </button>
          <button
            type="button"
            class="inline-flex size-8 items-center justify-center rounded-md border border-border text-muted-foreground hover:text-iron"
            :disabled="drafts.length <= 1"
            @click="removeDraft(index)"
          >
            <Trash2 class="size-3.5" />
          </button>
        </div>
      </div>
      <button
        type="button"
        class="inline-flex h-8 items-center gap-1.5 rounded-md border border-dashed border-border px-3 text-xs text-muted-foreground hover:text-foreground"
        @click="addDraft"
      >
        <Plus class="size-3.5" />
        添加审批环节
      </button>
    </div>
    <template #footer>
      <button
        type="button"
        class="h-8 px-3 text-xs rounded-md border border-border"
        @click="editorOpen = false"
      >
        取消
      </button>
      <button
        type="button"
        class="kb-btn-primary h-8 px-3 text-xs"
        :disabled="editorSaving"
        @click="saveEditor"
      >
        {{ editorSaving ? '保存中…' : '保存流程' }}
      </button>
    </template>
  </AppDialog>
</template>

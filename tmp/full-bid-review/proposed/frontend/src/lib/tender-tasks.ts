import { computed, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import {
  decideTenderRecord,
  deleteTenderRecord,
  fetchTenderRecords,
  markTenderRecord,
  submitTenderRecord,
  type ApprovalStepDef,
  type TenderRecordItem,
} from '@/lib/tenders-api'
import { apiTimeMs } from '@/lib/time'

export const TASK_STATUSES = [
  'processing',
  'pending',
  'approved',
  'submitted',
  'won',
  'lost',
] as const

export type TaskStatus = (typeof TASK_STATUSES)[number]

export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  processing: '编制中',
  pending: '待审批',
  approved: '审批通过',
  submitted: '已提交',
  won: '已中标',
  lost: '已失败',
}

export const TASK_STATUS_TONE: Record<
  TaskStatus,
  'default' | 'iron' | 'molybdenum' | 'patina' | 'sulfur' | 'coolant'
> = {
  processing: 'molybdenum',
  pending: 'sulfur',
  approved: 'patina',
  submitted: 'coolant',
  won: 'iron',
  lost: 'iron',
}

export const PROJECT_TYPES = ['弱电工程', '数据中心', '智慧园区', '安防系统'] as const
export type ProjectType = (typeof PROJECT_TYPES)[number]

export const APPROVAL_STEPS = ['提交申请', '部门经理审批', '总经理审批', '财务审核', '完成'] as const

export const DEFAULT_APPROVAL_FLOW: ApprovalStepDef[] = [
  { key: 'submit', name: '提交申请', kind: 'start' },
  { key: 'review_dept', name: '部门经理审批', kind: 'review' },
  { key: 'review_gm', name: '总经理审批', kind: 'review' },
  { key: 'review_finance', name: '财务审核', kind: 'review' },
  { key: 'done', name: '完成', kind: 'end' },
]

export function approvalStepLabels(steps: ApprovalStepDef[] | readonly string[] | undefined): string[] {
  if (!steps?.length) return [...APPROVAL_STEPS]
  if (typeof steps[0] === 'string') return [...(steps as readonly string[])]
  return (steps as ApprovalStepDef[]).map((item) => item.name)
}

export function stepsForTask(
  task: { approvalSteps?: ApprovalStepDef[] | null; status?: string } | null | undefined,
  fallback?: ApprovalStepDef[] | null,
): ApprovalStepDef[] {
  if (task?.approvalSteps?.length) return task.approvalSteps
  if (fallback?.length) return fallback
  return DEFAULT_APPROVAL_FLOW
}

export type ApprovalResult = 'passed' | 'rejected' | null

export const TIME_RANGES = [
  { value: 'all', label: '全部时间' },
  { value: 'week', label: '近一周' },
  { value: 'month', label: '近一月' },
  { value: 'quarter', label: '近三月' },
  { value: 'year', label: '近一年' },
] as const

export type TimeRange = (typeof TIME_RANGES)[number]['value']

export type TenderTask = {
  id: string
  recordId: string
  projectName: string
  tenderer: string
  budgetYuan: number
  deadline: string
  owner: string
  ownerUsername: string
  projectType: ProjectType | string
  status: TaskStatus
  createdAt: number
  currentStep: string
  currentStepKey: string
  approvalSteps: ApprovalStepDef[]
  initiator: string
  initiatedAt: string
  approvalResult: ApprovalResult
  approvalComment: string
  workflowLocked: boolean
}

function asStatus(raw?: string): TaskStatus {
  if (raw && TASK_STATUSES.includes(raw as TaskStatus)) return raw as TaskStatus
  return 'processing'
}

function formatDay(ts: number | null | undefined): string {
  if (!ts) return ''
  const d = new Date(apiTimeMs(ts, true) || ts)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function recordToTask(item: TenderRecordItem): TenderTask {
  const status = asStatus(item.status)
  let approvalResult: ApprovalResult = null
  if (item.lastAction === 'pass' || ['approved', 'submitted', 'won'].includes(status)) {
    approvalResult = 'passed'
  } else if (item.lastAction === 'reject') {
    approvalResult = 'rejected'
  }
  return {
    id: item.id,
    recordId: item.id,
    projectName: item.projectName || '未命名项目',
    tenderer: item.tenderer || '—',
    budgetYuan: Number(item.bidPriceYuan) || 0,
    deadline: item.deadline || '',
    owner: item.username || '—',
    ownerUsername: item.username || '',
    projectType: item.projectType || '',
    status,
    createdAt: item.createdAt || 0,
    currentStep: item.currentStep || '',
    currentStepKey: item.currentStepKey || item.currentStep || '',
    approvalSteps: item.approvalSteps || [],
    initiator: item.username || '',
    initiatedAt: formatDay(item.submittedAt),
    approvalResult,
    approvalComment: item.lastComment || '',
    workflowLocked: Boolean(item.workflowLocked) || status !== 'processing',
  }
}

export function formatBudget(yuan: number): string {
  if (!yuan || !Number.isFinite(yuan)) return '—'
  if (yuan >= 100_000_000) {
    const yi = yuan / 100_000_000
    return `¥${yi.toLocaleString('zh-CN', { maximumFractionDigits: yi >= 10 ? 1 : 2 })}亿`
  }
  if (yuan >= 10_000) {
    const wan = yuan / 10_000
    return `¥${wan.toLocaleString('zh-CN', { maximumFractionDigits: wan >= 100 ? 0 : 1 })}万`
  }
  return `¥${Math.round(yuan).toLocaleString('zh-CN')}`
}

export function matchKeyword(task: TenderTask, keyword: string): boolean {
  const q = keyword.trim().toLowerCase()
  if (!q) return true
  return [task.projectName, task.tenderer, task.owner, task.projectType]
    .join(' ')
    .toLowerCase()
    .includes(q)
}

export function matchTimeRange(task: TenderTask, range: TimeRange): boolean {
  if (range === 'all') return true
  const days = range === 'week' ? 7 : range === 'month' ? 30 : range === 'quarter' ? 90 : 365
  const start = Date.now() - days * 24 * 60 * 60 * 1000
  return (task.createdAt || 0) >= start
}

export function stepIndex(step: string, steps?: ApprovalStepDef[] | readonly string[]): number {
  const labels = approvalStepLabels(steps)
  const i = labels.indexOf(step)
  if (i >= 0) return i
  if (steps && typeof steps[0] !== 'string') {
    const keyed = (steps as ApprovalStepDef[]).findIndex((item) => item.key === step)
    if (keyed >= 0) return keyed
  }
  return 0
}

export function paginate<T>(items: T[], page: number, pageSize: number): T[] {
  const start = (page - 1) * pageSize
  return items.slice(start, start + pageSize)
}

export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize))
}

export function exportTasksCsv(rows: TenderTask[], filename: string) {
  const header = ['项目名称', '招标方', '预算金额', '截止日期', '负责人', '项目类型', '状态']
  const body = rows.map((t) =>
    [
      t.projectName,
      t.tenderer,
      formatBudget(t.budgetYuan),
      t.deadline,
      t.owner,
      t.projectType,
      TASK_STATUS_LABEL[t.status],
    ]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`)
      .join(','),
  )
  const csv = `\uFEFF${[header.join(','), ...body].join('\n')}`
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const myTasks = ref<TenderTask[]>([])
const allTasks = ref<TenderTask[]>([])
const pendingApprovals = ref<TenderTask[]>([])
const doneApprovals = ref<TenderTask[]>([])
const myApprovals = ref<TenderTask[]>([])
const loading = ref(false)
const error = ref('')

export function useTenderTasks() {
  const auth = useAuthStore()
  const displayName = computed(
    () => auth.user?.display_name?.trim() || auth.user?.username || '',
  )
  const owners = computed(() => {
    const set = new Set<string>()
    for (const t of allTasks.value) {
      if (t.owner) set.add(t.owner)
    }
    return [...set]
  })

  async function loadMine() {
    const data = await fetchTenderRecords({ scope: 'mine', limit: 200 })
    myTasks.value = (data.items || []).map(recordToTask)
  }

  async function loadAll() {
    const data = await fetchTenderRecords({ scope: 'all', limit: 200 })
    allTasks.value = (data.items || []).map(recordToTask)
  }

  async function loadApprovals() {
    const [pending, done, mine] = await Promise.all([
      fetchTenderRecords({ approvalTab: 'pending', limit: 200 }),
      fetchTenderRecords({ approvalTab: 'done', limit: 200 }),
      fetchTenderRecords({ approvalTab: 'mine', limit: 200 }),
    ])
    pendingApprovals.value = (pending.items || []).map(recordToTask)
    doneApprovals.value = (done.items || []).map(recordToTask)
    myApprovals.value = (mine.items || []).map(recordToTask)
  }

  async function load(kind: 'mine' | 'all' | 'approval' | 'all-pages' = 'all-pages') {
    loading.value = true
    error.value = ''
    try {
      if (kind === 'mine') await loadMine()
      else if (kind === 'all') await loadAll()
      else if (kind === 'approval') await loadApprovals()
      else {
        await Promise.all([loadMine(), loadAll().catch(() => {
          allTasks.value = []
        }), loadApprovals().catch(() => {
          pendingApprovals.value = []
          doneApprovals.value = []
          myApprovals.value = []
        })])
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载投标任务失败'
    } finally {
      loading.value = false
    }
  }

  async function submitApproval(task: TenderTask) {
    const item = await submitTenderRecord(task.recordId)
    const next = recordToTask(item)
    myTasks.value = myTasks.value.map((t) => (t.id === next.id ? next : t))
    allTasks.value = allTasks.value.map((t) => (t.id === next.id ? next : t))
    await loadApprovals().catch(() => undefined)
    return next
  }

  async function decideApproval(task: TenderTask, passed: boolean, comment: string) {
    const item = await decideTenderRecord(task.recordId, passed, comment)
    await loadApprovals()
    await loadMine().catch(() => undefined)
    return recordToTask(item)
  }

  async function markResult(task: TenderTask, status: 'submitted' | 'won' | 'lost') {
    const item = await markTenderRecord(task.recordId, status)
    const next = recordToTask(item)
    myTasks.value = myTasks.value.map((t) => (t.id === next.id ? next : t))
    allTasks.value = allTasks.value.map((t) => (t.id === next.id ? next : t))
    return next
  }

  async function removeTasks(ids: string[]): Promise<{ ok: number; fail: number }> {
    let ok = 0
    let fail = 0
    for (const id of ids) {
      try {
        await deleteTenderRecord(id)
        myTasks.value = myTasks.value.filter((t) => t.id !== id)
        allTasks.value = allTasks.value.filter((t) => t.id !== id)
        ok += 1
      } catch {
        fail += 1
      }
    }
    return { ok, fail }
  }

  return {
    tasks: allTasks,
    myTasks,
    owners,
    pendingApprovals,
    doneApprovals,
    myApprovals,
    loading,
    error,
    displayName,
    load,
    submitApproval,
    decideApproval,
    markResult,
    removeTasks,
  }
}

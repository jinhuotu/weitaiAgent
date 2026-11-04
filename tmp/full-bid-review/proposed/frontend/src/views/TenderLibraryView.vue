<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ExternalLink, FileText, FolderOpen, Loader2, Pencil, Plus, Trash2, Upload } from 'lucide-vue-next'
import { PageHeader, Panel, Tag } from '@/components/ui-kit'
import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'
import TenderScanGallery from '@/components/tenders/TenderScanGallery.vue'
import { ApiError } from '@/lib/api'
import { getAccessToken } from '@/lib/auth'
import {
  clearTenderSlot,
  createTenderLibraryItem,
  deleteTenderLibraryFile,
  deleteTenderLibraryItem,
  fetchTenderLibrary,
  resolveLibraryFileId,
  reindexTenderLibrary,
  updateLibraryFileFacts,
  updateTenderLibraryItem,
  uploadTenderSlot,
  type SlotFileInfo,
  type SlotStatus,
} from '@/lib/tenders-api'

const router = useRouter()
const loading = ref(true)
const error = ref('')
const hint = ref('')
const baseId = ref('')
const slots = ref<SlotStatus[]>([])
const uploadingKey = ref<string | null>(null)
const savingFactsId = ref<string | null>(null)
const reindexing = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const pendingKey = ref<string | null>(null)
const pendingReplace = ref(true)

const showEditor = ref(false)
const editingKey = ref<string | null>(null)
const editorTitle = ref('')
const editorHint = ref('')
const saving = ref(false)
const pendingDelete = ref<SlotStatus | null>(null)
const pendingDeleteFile = ref<{ slot: SlotStatus; file: SlotFileInfo } | null>(null)
const deleting = ref(false)
const removingFileId = ref<string | null>(null)

const CREDIT_SEARCH =
  'https://www.creditchina.gov.cn/xinyongxinxi/index.html?index=0&scenes=defaultScenario&tableName=credit_xyzx_tyshxydm&searchState=2&entityType=1,2,4,5,6,7,8&keyword='
const DEFAULT_CREDIT_NAME = '河南伟泰光电科技有限公司'
const LS_CREDIT_NAME = 'tender.creditChinaName'

const showCredit = ref(false)
const creditName = ref(DEFAULT_CREDIT_NAME)

const filledCount = computed(() => slots.value.filter((s) => s.fileCount > 0).length)

function loadCreditName() {
  try {
    const s = localStorage.getItem(LS_CREDIT_NAME)?.trim()
    if (s) creditName.value = s
  } catch {
    /* ignore */
  }
}

function openCreditDialog() {
  loadCreditName()
  showCredit.value = true
}

async function jumpCreditChina() {
  const name = creditName.value.trim() || DEFAULT_CREDIT_NAME
  creditName.value = name
  try {
    localStorage.setItem(LS_CREDIT_NAME, name)
  } catch {
    /* ignore */
  }
  try {
    await navigator.clipboard.writeText(name)
  } catch {
    /* 无剪贴板权限时仍跳官网 */
  }
  window.open(CREDIT_SEARCH + encodeURIComponent(name), '_blank', 'noopener,noreferrer')
  showCredit.value = false
}

const _CN = '一二三四五六七八九'

function cnOrdinal(i: number) {
  const n = i + 1
  if (n < 10) return _CN[n - 1]
  if (n === 10) return '十'
  if (n < 20) return '十' + _CN[n - 11]
  if (n < 100) {
    const tens = Math.floor(n / 10)
    const ones = n % 10
    const head = _CN[tens - 1] + '十'
    return ones ? head + _CN[ones - 1] : head
  }
  return String(n)
}

onMounted(async () => {
  loadCreditName()
  if (!getAccessToken()) {
    error.value = '请先登录'
    loading.value = false
    return
  }
  await reload()
})

async function reload(quiet = false) {
  if (!quiet) loading.value = true
  error.value = ''
  try {
    const data = await fetchTenderLibrary()
    slots.value = data.slots || []
    hint.value = data.hint || ''
    baseId.value = data.baseId || ''
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '加载资料库失败'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingKey.value = null
  editorTitle.value = ''
  editorHint.value = ''
  showEditor.value = true
}

function openEdit(slot: SlotStatus) {
  editingKey.value = slot.key
  editorTitle.value = slot.title
  editorHint.value = slot.hint || ''
  showEditor.value = true
}

async function saveItem() {
  const title = editorTitle.value.trim()
  if (!title) {
    error.value = '请填写资料名称'
    return
  }
  saving.value = true
  error.value = ''
  try {
    if (editingKey.value) {
      const item = await updateTenderLibraryItem(editingKey.value, {
        title,
        hint: editorHint.value.trim(),
      })
      const idx = slots.value.findIndex((s) => s.key === editingKey.value)
      if (idx >= 0) slots.value.splice(idx, 1, { ...slots.value[idx], ...item })
    } else {
      const item = await createTenderLibraryItem({
        title,
        hint: editorHint.value.trim(),
      })
      slots.value.push(item)
    }
    showEditor.value = false
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '保存资料项失败'
  } finally {
    saving.value = false
  }
}

function askDeleteItem(slot: SlotStatus) {
  if (!slot.key) return
  pendingDelete.value = slot
}

function onDeleteOpen(open: boolean) {
  if (!open && !deleting.value) pendingDelete.value = null
}

async function saveFacts(payload: { id: string; role: string; fields: { name: string; value: string }[] }) {
  savingFactsId.value = payload.id
  error.value = ''
  try {
    const slot = await updateLibraryFileFacts(payload.id, {
      role: payload.role,
      fields: payload.fields,
    })
    const idx = slots.value.findIndex((item) => item.key === slot.key)
    if (idx >= 0) slots.value[idx] = slot
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '保存识别结果失败'
  } finally {
    savingFactsId.value = null
  }
}

function askDeleteFile(slot: SlotStatus, file: SlotFileInfo) {
  if (!resolveLibraryFileId(file)) {
    error.value = `无法删除「${file.performance?.projectName || file.name}」，缺少文件编号`
    return
  }
  pendingDeleteFile.value = { slot, file }
}

function onDeleteFileOpen(open: boolean) {
  if (!open && !deleting.value) pendingDeleteFile.value = null
}

function applySlotUpdate(key: string, item: SlotStatus) {
  const idx = slots.value.findIndex((s) => s.key === key)
  if (idx >= 0) slots.value.splice(idx, 1, { ...slots.value[idx], ...item })
}

async function confirmDeleteFile() {
  const row = pendingDeleteFile.value
  const docId = row ? resolveLibraryFileId(row.file) : ''
  if (!row?.slot.key || !docId) return
  deleting.value = true
  removingFileId.value = docId
  error.value = ''
  try {
    const item = await deleteTenderLibraryFile(docId)
    applySlotUpdate(row.slot.key, item)
    pendingDeleteFile.value = null
    await reload(true)
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '删除文件失败'
  } finally {
    deleting.value = false
    removingFileId.value = null
  }
}

async function confirmDeleteItem() {
  const slot = pendingDelete.value
  if (!slot?.key) return
  deleting.value = true
  error.value = ''
  uploadingKey.value = slot.key
  try {
    await deleteTenderLibraryItem(slot.key)
    slots.value = slots.value.filter((s) => s.key !== slot.key)
    pendingDelete.value = null
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '删除失败'
  } finally {
    deleting.value = false
    uploadingKey.value = null
  }
}

function pickFiles(slot: SlotStatus, replace: boolean) {
  if (!slot.key) return
  pendingKey.value = slot.key
  pendingReplace.value = replace
  fileInput.value?.click()
}

async function onFileChange(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = Array.from(input.files || [])
  const key = pendingKey.value
  const replace = pendingReplace.value
  input.value = ''
  pendingKey.value = null
  if (!files.length || !key) return
  error.value = ''
  uploadingKey.value = key
  try {
    let last: SlotStatus | null = null
    for (let i = 0; i < files.length; i++) {
      const data = await uploadTenderSlot(key, files[i], { replace: replace && i === 0 })
      last = {
        key,
        title: data.title || slots.value.find((s) => s.key === key)?.title || key,
        hint: data.hint || slots.value.find((s) => s.key === key)?.hint || '',
        fileCount: data.fileCount,
        files: data.files || [],
        docId: data.docId,
        pinned: data.pinned,
      }
    }
    if (last) {
      const idx = slots.value.findIndex((s) => s.key === key)
      if (idx >= 0) slots.value.splice(idx, 1, last)
    }
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '上传失败'
  } finally {
    uploadingKey.value = null
  }
}

async function onClear(slot: SlotStatus) {
  if (!slot.key || !slot.fileCount) return
  error.value = ''
  uploadingKey.value = slot.key
  try {
    const data = await clearTenderSlot(slot.key)
    const idx = slots.value.findIndex((s) => s.key === slot.key)
    if (idx >= 0) {
      slots.value.splice(idx, 1, {
        ...slots.value[idx],
        ...data,
        fileCount: data.fileCount ?? 0,
        files: data.files || [],
      })
    }
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '清除失败'
  } finally {
    uploadingKey.value = null
  }
}

async function onReindex(force = false) {
  if (reindexing.value) return
  reindexing.value = true
  error.value = ''
  try {
    const stats = await reindexTenderLibrary({ force })
    hint.value = `已排队 OCR 入库 ${stats.queued} 个文件${stats.skipped ? `，跳过 ${stats.skipped}` : ''}。完成后可在「AI 智能问答」勾选「投标资料库」检索。`
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '入库排队失败'
  } finally {
    reindexing.value = false
  }
}
</script>

<template>
  <PageHeader
    title="投标资料库"
    description="资料存放在知识库中。上传后 OCR 入库，可在 AI 智能问答中检索；生成投标文件时按名称自动引用。"
  >
    <template #badges>
      <Tag tone="molybdenum">知识库融合</Tag>
      <Tag>OCR 可检索</Tag>
      <Tag>生成时自动引用</Tag>
    </template>
    <template #actions>
      <button
        type="button"
        class="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border text-[12px] hover:bg-accent"
        title="打开信用中国官网查询，公司名称可改"
        @click="openCreditDialog"
      >
        <ExternalLink class="size-3.5" />
        打开信用中国
      </button>
      <button
        type="button"
        class="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border text-[12px] hover:bg-accent disabled:opacity-50"
        :disabled="reindexing"
        title="将已有扫描件 OCR 写入向量库，供智能问答检索"
        @click="onReindex(false)"
      >
        <Loader2 v-if="reindexing" class="size-3.5 animate-spin" />
        <Upload v-else class="size-3.5" />
        {{ reindexing ? '入库中…' : 'OCR 入库' }}
      </button>
      <button
        type="button"
        class="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border text-[12px] hover:bg-accent"
        @click="openCreate"
      >
        <Plus class="size-3.5" />
        新增资料
      </button>
      <button
        v-if="baseId"
        type="button"
        class="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border text-[12px] hover:bg-accent"
        @click="router.push(`/knowledge/${baseId}`)"
      >
        <FolderOpen class="size-3.5" />
        在知识库打开
      </button>
      <button
        type="button"
        class="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border text-[12px] hover:bg-accent"
        @click="router.push('/tenders')"
      >
        <FileText class="size-3.5" />
        去 AI标书生成
      </button>
    </template>
  </PageHeader>

  <p
    v-if="error"
    class="mb-4 text-[12px] text-sulfur border border-sulfur/30 bg-sulfur/10 rounded-md px-3 py-2"
  >
    {{ error }}
  </p>

  <div v-if="loading" class="py-16 text-center text-[12px] text-muted-foreground">
    <Loader2 class="inline size-4 animate-spin mr-2" />加载资料库…
  </div>

  <div v-else class="space-y-5 max-w-4xl">
    <Panel title="公司常备扫描件" :subtitle="hint || 'PDF / 图片，可多选；替换会覆盖该项全部文件'">
      <p class="mb-4 text-[12px] text-muted-foreground">
        已维护
        <span class="text-foreground font-medium">{{ filledCount }}</span>
        /
        {{ slots.length }} 项
      </p>

      <ul class="space-y-3">
        <li
          v-for="(slot, i) in slots"
          :key="slot.key"
          class="rounded-md border border-border px-3 py-3"
        >
          <div class="flex flex-wrap items-start gap-3">
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-2 text-[13px]">
                <span class="text-muted-foreground font-sans text-[11px]">（{{ cnOrdinal(i) }}）</span>
                <span class="text-foreground/90">{{ slot.title }}</span>
                <span
                  v-if="slot.fileCount"
                  class="text-[11px] text-emerald-700 dark:text-emerald-400"
                >【已维护 {{ slot.fileCount }} 个】</span>
                <span v-else class="text-[11px] text-sulfur">【待上传】</span>
              </div>
              <p v-if="slot.hint" class="mt-1 text-[11px] text-muted-foreground leading-relaxed">
                {{ slot.hint }}
              </p>
              <p
                v-if="slot.key === 'perf'"
                class="mt-1 text-[11px] text-muted-foreground leading-relaxed"
              >
                上传后会扫描合同/发票，抽出项目名称、规格型号、买方、联系人、合同额、概况和是否在建。招标优先采用已竣工充电桩项目。
              </p>
              <p
                v-if="slot.key === 'id_legal'"
                class="mt-1 text-[11px] text-muted-foreground leading-relaxed"
              >
                可放多位法定代表人。每人上传正面和反面，正面识别姓名、性别、年龄和身份证号。同一人请先传正面再传反面。做标书时再选择本标用谁。
              </p>
              <TenderScanGallery
                v-if="slot.files?.length"
                :files="slot.files"
                removable
                :removing-id="removingFileId"
                :saving-id="savingFactsId"
                @remove="(file) => askDeleteFile(slot, file)"
                @save-facts="saveFacts"
              />
            </div>
            <div class="flex shrink-0 flex-wrap items-center gap-1.5">
              <button
                type="button"
                class="h-7 px-2 inline-flex items-center gap-1 rounded-md border border-border text-[11px] hover:bg-accent disabled:opacity-50"
                :disabled="uploadingKey === slot.key"
                @click="pickFiles(slot, true)"
              >
                <Loader2 v-if="uploadingKey === slot.key" class="size-3 animate-spin" />
                <Upload v-else class="size-3" />
                {{ uploadingKey === slot.key ? '识别中' : slot.fileCount ? '替换' : '上传' }}
              </button>
              <button
                v-if="slot.fileCount"
                type="button"
                class="h-7 px-2 rounded-md border border-border text-[11px] hover:bg-accent disabled:opacity-50"
                :disabled="uploadingKey === slot.key"
                @click="pickFiles(slot, false)"
              >
                追加
              </button>
              <button
                type="button"
                class="h-7 px-2 inline-flex items-center gap-1 rounded-md border border-border text-[11px] hover:bg-accent disabled:opacity-50"
                :disabled="uploadingKey === slot.key"
                @click="openEdit(slot)"
              >
                <Pencil class="size-3" />
                改名
              </button>
              <button
                v-if="slot.fileCount"
                type="button"
                class="h-7 px-2 inline-flex items-center gap-1 rounded-md border border-border text-[11px] hover:bg-accent disabled:opacity-50"
                :disabled="uploadingKey === slot.key"
                @click="onClear(slot)"
              >
                清除文件
              </button>
              <button
                type="button"
                class="h-7 px-2 inline-flex items-center gap-1 rounded-md border border-border text-[11px] hover:bg-accent disabled:opacity-50"
                :disabled="uploadingKey === slot.key"
                @click="askDeleteItem(slot)"
              >
                <Trash2 class="size-3" />
                删除项
              </button>
            </div>
          </div>
        </li>
      </ul>

      <input
        ref="fileInput"
        type="file"
        class="hidden"
        multiple
        accept=".pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,application/pdf,image/*"
        @change="onFileChange"
      />
    </Panel>
  </div>

  <div
    v-if="showCredit"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
    @click.self="showCredit = false"
  >
    <div class="w-full max-w-md rounded-xl border border-border bg-card p-4 shadow-xl">
      <h3 class="text-[14px] font-semibold">打开信用中国</h3>
      <p class="mt-1 text-[11px] text-muted-foreground leading-relaxed">
        会打开官网查询页并把公司名填进搜索框。若弹出验证码，需在官网完成后再点搜索、下载报告，回到本页「信用中国」资料项上传。
      </p>
      <label class="mt-4 block text-[12px]">
        查询主体
        <input
          v-model="creditName"
          class="kb-input mt-1"
          placeholder="公司全称或统一社会信用代码"
          @keydown.enter="jumpCreditChina"
        />
      </label>
      <button
        type="button"
        class="mt-2 text-[11px] text-muted-foreground hover:text-foreground"
        @click="creditName = DEFAULT_CREDIT_NAME"
      >
        恢复默认：{{ DEFAULT_CREDIT_NAME }}
      </button>
      <div class="mt-4 flex justify-end gap-2">
        <button type="button" class="h-8 px-3 rounded-md border border-border text-[12px]" @click="showCredit = false">
          取消
        </button>
        <button
          type="button"
          class="h-8 px-3 rounded-md bg-iron text-white text-[12px]"
          @click="jumpCreditChina"
        >
          打开官网
        </button>
      </div>
    </div>
  </div>

  <div
    v-if="showEditor"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
    @click.self="showEditor = false"
  >
    <div class="w-full max-w-md rounded-xl border border-border bg-card p-4 shadow-xl">
      <h3 class="text-[14px] font-semibold">{{ editingKey ? '修改资料项' : '新增资料项' }}</h3>
      <p class="mt-1 text-[11px] text-muted-foreground">名称会用于 Word 附件小标题，解析邀请书时也会对照这份清单。</p>
      <label class="mt-4 block text-[12px]">
        资料名称
        <input v-model="editorTitle" class="kb-input mt-1" placeholder="例如：ISO 体系证书" />
      </label>
      <label class="mt-3 block text-[12px]">
        说明（可选）
        <input v-model="editorHint" class="kb-input mt-1" placeholder="例如：须为公司现行有效证书" />
      </label>
      <div class="mt-4 flex justify-end gap-2">
        <button type="button" class="h-8 px-3 rounded-md border border-border text-[12px]" @click="showEditor = false">
          取消
        </button>
        <button
          type="button"
          class="h-8 px-3 rounded-md bg-iron text-white text-[12px] disabled:opacity-50"
          :disabled="saving"
          @click="saveItem"
        >
          <Loader2 v-if="saving" class="inline size-3.5 animate-spin mr-1" />
          保存
        </button>
      </div>
    </div>
  </div>

  <AppAlertDialog
    :open="Boolean(pendingDelete)"
    title="删除资料项"
    :description="`确定删除「${pendingDelete?.title || ''}」及其扫描件？删除后生成投标文件时将不再引用该项。`"
    confirm-label="确认删除"
    :loading="deleting"
    destructive
    @update:open="onDeleteOpen"
    @confirm="confirmDeleteItem"
  />
  <AppAlertDialog
    :open="Boolean(pendingDeleteFile)"
    title="删除这份扫描件"
    :description="`确定删除「${pendingDeleteFile?.file.performance?.projectName || pendingDeleteFile?.file.name || ''}」？不会删除整个资料项。`"
    confirm-label="确认删除"
    :loading="deleting"
    destructive
    @update:open="onDeleteFileOpen"
    @confirm="confirmDeleteFile"
  />
</template>

<style scoped>
.kb-input {
  background: var(--bg-surface, transparent);
  border: 1px solid var(--hairline, hsl(var(--border)));
  border-radius: 6px;
  color: inherit;
  font-size: 12px;
  padding: 8px 10px;
  width: 100%;
}
.kb-input:focus {
  outline: none;
  border-color: var(--accent-molybdenum, hsl(var(--ring)));
}
</style>

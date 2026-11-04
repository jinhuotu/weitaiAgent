<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Download,
  FileSpreadsheet,
  FileUp,
  FolderOpen,
  History,
  Loader2,
  RotateCcw,
  Trash2,
  Maximize2,
  Minimize2,
  Sparkles,
  TriangleAlert,
  Upload,
  ImagePlus,
} from 'lucide-vue-next'
import { PageHeader, Panel } from '@/components/ui-kit'
import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import { fmtSize, KB_UPLOAD_MAX_BYTES } from '@/lib/read-file-smart'
import { ApiError } from '@/lib/api'
import { getAccessToken } from '@/lib/auth'
import { useAuthStore } from '@/stores/auth'
import { listKnowledgeBases, type KnowledgeBaseItem } from '@/lib/knowledge-api'
import { listPromptOptions, type PromptOption } from '@/lib/prompts-api'
import { fetchQuoteRecords, type QuoteRecordItem } from '@/lib/quotes-api'
import {
  clearMineTenderRecords,
  confirmInvitationFormatDocx,
  clearTenderSlot,
  deleteTenderRecord,
  downloadTenderFile,
  fetchFixedTemplates,
  fetchTenderDefaults,
  fetchTenderLibrary,
  fetchTenderRecord,
  fetchTenderRecords,
  fetchTenderQa,
  fetchTenderSlots,
  draftTenderTech,
  fillTechBoq,
  fillTechDrawing,
  generateTender,
  inspectTenderQa,
  publishTenderRecord,
  parseTenderInvitation,
  prepareInvitationFormatDocx,
  previewTenderOutline,
  regenerateTenderRecord,
  restoreTenderLayoutTemplate,
  uploadTenderLayoutTemplate,
  uploadTenderSlot,
  uploadTechPlanSource,
  emptyPerformanceRequirement,
  formatPerformanceRequirement,
  isTenderWorkflowLocked,
  matchPerformanceLine,
  performanceRequirementActive,
  type BidBrief,
  type Chapter5TemplateStatus,
  type DeviationLine,
  type DocumentFormat,
  type GenerateResult,
  type QaReport,
  type OutlineItem,
  type FixedTemplate,
  type OutlinePreviewRow,
  type StructureMode,
  type CopyPolicy,
  type AttachmentMatch,
  type AttachmentMatchItem,
  type BidVolume,
  type PerformanceLine,
  type QualificationStatus,
  type QuoteLineIn,
  type SlotFileInfo,
  type SlotStatus,
  type TenderRecordItem,
  volumeDocx,
} from '@/lib/tenders-api'
import TenderDocEditor from '@/components/tenders/TenderDocEditor.vue'
import TenderQaPanel from '@/components/tenders/TenderQaPanel.vue'

function emptyVolumeFormat() {
  return {
    marginSet: false,
    marginLeftCm: 2.8,
    marginRightCm: 2.6,
    marginTopCm: 2.6,
    marginBottomCm: 2.5,
    fontSet: false,
    fontName: '',
    bodySizeSet: false,
    bodySizePt: 12,
    pagePosSet: false,
    pageStartSet: false,
    pageNumberPos: '',
    pageNumberStart: '',
    notes: [] as string[],
  }
}

function emptyDocumentFormat(): DocumentFormat {
  return {
    specified: false,
    notes: [],
    businessFormat: emptyVolumeFormat(),
    technicalFormat: emptyVolumeFormat(),
    marginLeftCm: 2.8,
    marginRightCm: 2.6,
    marginTopCm: 2.6,
    marginBottomCm: 2.5,
    fontName: '宋体',
    bodySizePt: 12,
    headingSizePt: 16,
    coverTitleSizePt: 22,
    coverDocSizePt: 26,
    tocTitleSizePt: 16,
    tocItemSizePt: 12,
    coverRequired: true,
    coverShowProject: true,
    coverShowTenderNo: true,
    coverShowBidder: true,
    coverShowCopyMark: true,
    coverCopyMark: '正本',
    coverShowDate: true,
    coverNeedSeal: false,
    tocNumbering: 'cn',
    tocNeedPageNos: true,
    pageNumberPos: 'bottom-center',
    pageNumberStart: 'body',
  }
}

function todayYmd() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function emptyBrief(): BidBrief {
  return {
    projectName: '',
    tenderer: '',
    bidContent: '',
    quality: '合格',
    deliveryDays: 30,
    warrantyYears: 2,
    bidValidityDays: 60,
    bidPriceYuan: 0,
    prepaidPct: 30,
    arrivalPct: 50,
    settlementPct: 17,
    warrantyPct: 3,
    bidDate: todayYmd(),
    tenderNo: '',
    bidderName: '河南伟泰光电科技有限公司',
    bidderNature: '有限责任公司',
    bidderAddress:
      '河南省郑州市高新区开发区梧桐街与红松路交叉口东南角远大产业园区内6号楼三层',
    bidderWebsite: '',
    bidderPhone: '17630567052',
    bidderFax: '',
    bidderPostcode: '',
    bidderEmail: 'gzwceo@163.com',
    foundedDate: '2017年07月',
    businessTerm: '长期',
    legalPersonName: '郭志伟',
    legalPersonGender: '男',
    legalPersonAge: '28',
    legalPersonTitle: '执行董事',
    legalPersonIdNo: '410521199802104053',
    legalPersonKey: '',
    agentName: '',
    agentIdNo: '',
    agentAuthUntil: '',
    authNeed: '',
    authEnabled: false,
    trafficFeeNote: '',
    extraNote: '',
    factoryRole: '',
    controlPriceYuan: null,
    safetyFeeYuan: null,
    provisionalYuan: null,
    primeCostYuan: null,
    bidBondYuan: null,
    pmName: '',
    pmCertNo: '',
    pmGrade: '',
    defectPeriod: '',
    perfBond: '',
    attachQualifications: false,
    includePlaceholders: false,
    includeCommitment: true,
    extraPlaceholders: [],
    requiredSlotKeys: [],
    includeSlotKeys: [],
    quoteTitle: '',
    quoteTaxRate: 0.13,
    quoteSourceIncTax: 0,
    quoteSource: '',
    quoteLines: [],
    quoteHeaders: [],
    quoteRoles: [],
    deviationLines: [],
    bizDeviationLines: [],
    bizDevHeaders: [],
    techDevHeaders: [],
    performanceLines: [],
    performanceRequirement: emptyPerformanceRequirement(),
    constructionPlan: '',
    layoutPlan: '',
    powerPlan: '',
    omPlan: '',
    schedulePlan: '',
    techPlanNote: '',
    autoDraftTechPlan: true,
    autoDraftBizResp: true,
    techPlanSourceIds: [],
    techPlanKbIds: [],
    bizRespKbIds: [],
    layoutMode: 'chapter5',
    // Phase1-3 双轨：内容模板 / 结构来源 / 复制策略
    contentTemplateId: 'general',
    structureMode: 'fixed' as StructureMode,
    // 整单策略留空 = 跟随后端按渲染模式判定（1:1 下默认照抄原文）。
    // 这里必须留空：写死 'rewrite' 会显式覆盖后端，把投标函改成重写版。
    copyPolicy: '' as CopyPolicy | '',
    // 1:1 忠实渲染：默认照抄招标书序号/标题/顺序/原文
    outlineRender: 'verbatim' as 'verbatim' | 'curated',
    outlineConfirmed: true,
    formatConfirmed: true,
    outlineChapter: '',
    outlineItems: [],
    bidRequirements: [],
    documentFormat: emptyDocumentFormat(),
    invitationId: '',
  }
}

function emptyDeviationLine(): DeviationLine {
  return { seq: '', requirement: '', response: '', deviation: '待确认' }
}

function isTechChapterKind(kind: string) {
  return kind === 'tech_plan' || kind === 'tech_chapter'
}

function emptyPerformanceLine(): PerformanceLine {
  return {
    projectName: '',
    spec: '',
    location: '',
    client: '',
    contact: '',
    amountYuan: 0,
    summary: '',
    note: '',
    ongoing: false,
    chargerRelated: false,
    includeInBid: true,
  }
}

const OUTLINE_KIND_OPTIONS: { value: string; label: string }[] = [
  { value: 'letter', label: '响应/投标函' },
  { value: 'legal_id', label: '身份证明' },
  { value: 'auth', label: '授权委托' },
  { value: 'quote', label: '报价表' },
  { value: 'biz_dev', label: '商务偏离' },
  { value: 'tech_dev', label: '技术偏离' },
  { value: 'commitment_copy', label: '承诺函（复制原文）' },
  { value: 'scan', label: '扫描件/证书' },
  { value: 'performance', label: '业绩' },
  { value: 'factory', label: '原厂承诺' },
  { value: 'tech_plan', label: '技术标' },
  { value: 'tech_chapter', label: '技术章节' },
  { value: 'company', label: '企业信息表' },
  { value: 'unknown', label: '按招标书原文' },
]

const OUTLINE_GENERATE_KINDS = new Set([
  'letter',
  'legal_id',
  'auth',
  'quote',
  'biz_dev',
  'tech_dev',
  'performance',
  'factory',
  'tech_plan',
  'tech_chapter',
])
const OUTLINE_FORM_KINDS = new Set(['letter', 'auth', 'factory', 'commitment_copy', 'company'])

function outlineSourceFor(kind: string, skipped: boolean) {
  if (skipped) return 'skip'
  return OUTLINE_GENERATE_KINDS.has(kind) ? 'generate' : 'copy'
}

function outlineSourceLabel(source: string, item?: OutlineItem) {
  if (source === 'skip') return '已跳过'
  if (
    source === 'generate' &&
    item &&
    OUTLINE_FORM_KINDS.has(item.kind) &&
    (item.body || '').trim().length >= 1
  ) {
    return '按招标书格式填空'
  }
  if (item && item.kind === 'quote' && (item.body || '').trim()) {
    return '按招标书格式填空'
  }
  if (source === 'generate') return '模块生成'
  return '复制招标书原文'
}

function setOutlineKind(item: OutlineItem, kind: string) {
  item.kind = kind
  item.source = outlineSourceFor(kind, item.skipped)
}

function setOutlineSkipped(item: OutlineItem, skipped: boolean) {
  if (item.kind === 'auth' && form.authEnabled && skipped) return
  item.skipped = skipped
  item.source = outlineSourceFor(item.kind, skipped)
}

function outlineAuthHint(item: OutlineItem) {
  if (item.kind !== 'auth') return ''
  if (form.authEnabled) return item.skipped ? '已启用授权委托，请取消跳过' : '需贴委托人身份证'
  return '未启用授权委托，法人自签即可跳过'
}

/** 轨道B 每节的三态提示：重写/沿用原文 + 是否抽到原文 + 缺件红点。 */
function outlinePolicyHint(item: OutlineItem) {
  if (form.structureMode !== 'outline') return outlineSourceLabel(item.source, item)
  const row = outlinePreviewRows.value.find((r) => r.id === item.id)
  const parts: string[] = []
  if (item.skipped) {
    parts.push('已跳过')
  } else if (itemPolicy(item) === 'verbatim') {
    parts.push((item.body || '').trim() ? '照抄招标书空白稿并填空' : '未抽到原文，将只保留标题')
  } else {
    parts.push('按系统资料与表单重写')
  }
  if (outlineAuthHint(item)) parts.push(outlineAuthHint(item))
  if (row?.missing?.length) parts.push(`缺件：${row.missing.join('、')}`)
  if (row?.needsConfirm) parts.push('类型未识别，请人工确认')
  return parts.join(' · ')
}

function prettyOutlineTitle(title: string) {
  const t = (title || '').replace(/\s+/g, '')
  if (t.includes('营业执照') && /注册地址|注册住所/.test(t)) return '营业执照'
  return title
}

function isFormatChapterTitle(title: string) {
  const n = (title || '')
    .replace(/\s+/g, '')
    .replace(/^第[一二三四五六七八九十0-9]+部分/, '')
    .replace(/^[一二三四五六七八九十]{1,3}、/, '')
    .replace(/^[（(][一二三四五六七八九十0-9]+[)）]/, '')
    .replace(/^\d{1,2}[.．、]/, '')
  return /^(投标函及投标函附录|投标函附录|投标函|响应函及响应函附录|法定代表人身份证明|授权委托书|投标保证金|已标价工程量清单|已标价的工程量清单|项目管理机构|项目管理机构组成表|主要人员简历表|主要项目管理人员简历表|项目经理简历表|拟分包项目情况表|拟分包项目情况|资格审查资料|投标人基本情况表|近年财务状况表|近年完成的类似项目情况表|正在施工的和新承接的项目情况表|正在施工和新承接的项目情况表|近年发生的诉讼及仲裁情况|其他资料|其他材料|施工组织格式|施工组织设计)$/.test(
    n,
  )
}

function pruneUnknownOutline(items: OutlineItem[] | undefined) {
  return (items || [])
    .map((item) => {
      // 1:1 忠实渲染：来自招标书原文的条目，标题一个字都不许改
      if (item.rawTitle) return item
      const title = prettyOutlineTitle(item.title)
      return title === item.title ? item : { ...item, title }
    })
    .filter((item) => {
      // 1:1：条目来自招标书格式章，抽取阶段已判过噪，一律保留。
      // 下面的硬编码黑名单只用于「系统自己造的」条目，
      // 否则会把资格审查资料、项目管理机构组成表等整节删掉——
      // 不同行业叫法千差万别，靠关键词写死必然漏。
      if (item.rawTitle) return true
      const raw = item.title || ''
      const title = raw.replace(/\s+/g, '')
      if (!title) return false
      // 格式章目录原条目没有 rawTitle 时也不能当噪声删掉。
      if (isFormatChapterTitle(title) || item.tocEligible === true) return true
      if (/[：:]\s*[/／]\s*$/.test(raw) || /[：:][/／]+$/.test(title)) return false
      if (/计分规则|计分办法|评分细则|基准价/.test(title)) return false
      if (/见下表|如下表/.test(title)) return false
      if (title.includes('评审') && /(共计\d*分|计分|评分)/.test(title)) return false
      if (title.includes('计算规则') && !title.includes('报价表')) return false
      if (item.kind === 'tech_chapter' || item.kind === 'tech_plan' || item.kind === 'tech_dev') {
        return true
      }
      if (
        /中小企业声明|残疾人福利|资格审查资料|投标人基本情况表|近年财务状况表|正在施工的?和?新承接|管理人员简历|项目经理简历表|项目管理机构组成|拟分包项目情况|施工组织设计|暗标|装订要求|劳动力计划表|国家企业信用信息公示/.test(
          title,
        )
      )
        return false
      if ((item.kind || '') !== 'unknown') return true
      if (/^(商务标|技术标|商务部分|技术部分|商务标书|技术标书)$/.test(title)) return false
      return true
    })
}

const BIZ_OUTLINE_KINDS = new Set([
  'letter',
  'legal_id',
  'auth',
  'quote',
  'biz_dev',
  'commitment_copy',
  'performance',
  'factory',
  'company',
])
const TECH_OUTLINE_KINDS = new Set(['tech_dev', 'tech_plan', 'tech_chapter'])
const TECH_TITLE_MARK =
  /方案|组织设计|质量|安全|进度|措施|保证体系|技术规格|图纸|工艺|信息化|风险|环保|售后|培训|验收|调试|运维|偏离|偏差/
const VOLUME_LABELS = new Set(['商务标', '技术标', '商务部分', '技术部分', '商务标书', '技术标书'])

function isVolumeLabelTitle(title: string) {
  const n = (title || '').replace(/\s+/g, '').replace(/投标文件|响应文件/g, '')
  return VOLUME_LABELS.has(n)
}

function outlineItemVolume(item: OutlineItem): BidVolume {
  const kind = (item.kind || '').trim()
  if (TECH_OUTLINE_KINDS.has(kind)) return 'technical'
  if (BIZ_OUTLINE_KINDS.has(kind)) return 'business'
  const title = (item.title || '').replace(/\s+/g, '')
  if (title.includes('发包人代表') || (title.includes('撤换') && title.includes('发包人'))) {
    return 'technical'
  }
  const blob = `${item.id || ''}${item.title || ''}`
  return TECH_TITLE_MARK.test(blob) ? 'technical' : 'business'
}

const outlineDragFrom = ref<number | null>(null)
const outlineDragOver = ref<number | null>(null)

function outlineFullIndex(item: OutlineItem) {
  return form.outlineItems.indexOf(item)
}

function moveOutlineItem(from: number, to: number) {
  const items = form.outlineItems
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return
  const [moved] = items.splice(from, 1)
  items.splice(to, 0, moved)
}

function moveVisibleOutline(from: number, to: number) {
  const vis = visibleOutlineItems.value
  if (from < 0 || to < 0 || from >= vis.length || to >= vis.length) return
  moveOutlineItem(outlineFullIndex(vis[from]), outlineFullIndex(vis[to]))
}

function onOutlineDragStart(index: number, ev: DragEvent) {
  if (form.structureMode !== 'outline') {
    ev.preventDefault()
    return
  }
  outlineDragFrom.value = index
  if (ev.dataTransfer) {
    ev.dataTransfer.effectAllowed = 'move'
    ev.dataTransfer.setData('text/plain', String(index))
  }
}

function onOutlineDragOver(index: number, ev: DragEvent) {
  if (form.structureMode !== 'outline' || outlineDragFrom.value == null) return
  ev.preventDefault()
  if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move'
  outlineDragOver.value = index
}

function onOutlineDrop(index: number) {
  if (outlineDragFrom.value == null) return
  moveVisibleOutline(outlineDragFrom.value, index)
  outlineDragFrom.value = null
  outlineDragOver.value = null
}

function onOutlineDragEnd() {
  outlineDragFrom.value = null
  outlineDragOver.value = null
}

/** 本公司固定信息：接口/识别返回的空字符串不得覆盖这些默认值 */
const COMPANY_DEFAULT_KEYS = [
  'bidderName',
  'bidderNature',
  'bidderAddress',
  'bidderWebsite',
  'bidderPhone',
  'bidderFax',
  'bidderPostcode',
  'bidderEmail',
  'foundedDate',
  'businessTerm',
  'legalPersonName',
  'legalPersonGender',
  'legalPersonAge',
  'legalPersonTitle',
  'legalPersonIdNo',
] as const satisfies readonly (keyof BidBrief)[]

function ensureCompanyDefaults(target: BidBrief) {
  const defaults = emptyBrief()
  for (const key of COMPANY_DEFAULT_KEYS) {
    const cur = target[key]
    if (typeof cur === 'string' && !cur.trim()) {
      ;(target as Record<string, unknown>)[key] = defaults[key]
    }
  }
}

function junkPmName(name: string): boolean {
  const t = (name || '').replace(/[＿_—\-－/\s／]/g, '')
  if (!t || ['无', '暂无', '待定', '空白', '姓名'].includes(t)) return true
  if (/是指|由承|承包|拟派|拟任|详见|如下|项目|经理|投标|招标|应当|必须|填写|注册|证书/.test(t)) {
    return true
  }
  return !/^[\u4e00-\u9fff]{2,4}$/.test(t)
}

/** 合并 brief：空字符串不覆盖已有公司默认；其它字段照常写入 */
/** 原版格式章优先，旧记录的结构来源标记不能覆盖已识别的范本。 */
function hasOriginalFormat(brief: Partial<BidBrief> = form): boolean {
  if (!brief.invitationId) return false
  const chapter = String(brief.outlineChapter || '').replace(/\s+/g, '')
  const named = /^(?:第[一二三四五六七八九十百0-9]+(?:部分|章节|章|节)[、.．]?)?(?:投标|响应)文件(?:格式|组成)$/.test(chapter)
  return named || brief.layoutMode === 'outline' || Boolean(brief.formatConfirmed && !brief.requirementsDriven)
}

function generationLayoutMode(): string {
  if (hasOriginalFormat()) return 'outline'
  return form.requirementsDriven ? 'requirements' : (form.structureMode === 'outline' ? 'outline' : 'chapter5')
}

function mergeBriefIntoForm(brief: Partial<BidBrief>) {
  const defaults = emptyBrief()
  const next: Record<string, unknown> = { ...form }
  for (const [key, value] of Object.entries(brief)) {
    if (value === undefined) continue
    const isCompany = (COMPANY_DEFAULT_KEYS as readonly string[]).includes(key)
    if (isCompany && typeof value === 'string' && !value.trim()) {
      const cur = next[key]
      if (typeof cur === 'string' && cur.trim()) continue
      next[key] = (defaults as Record<string, unknown>)[key]
      continue
    }
    next[key] = value
  }
  if (junkPmName(String(next.pmName || ''))) next.pmName = ''
  if (hasOriginalFormat(next as Partial<BidBrief>)) {
    next.layoutMode = 'outline'
    next.structureMode = 'outline'
    next.requirementsDriven = false
  } else if (brief.requirementsDriven) next.structureMode = 'outline'
  Object.assign(form, next)
  ensureCompanyDefaults(form)
  if (brief.authEnabled === undefined) {
    form.authEnabled = Boolean(String(form.agentName || '').trim() || String(form.agentIdNo || '').trim())
  }
  syncAuthOutline(Boolean(form.authEnabled))
  if (Array.isArray(form.outlineItems)) {
    form.outlineItems = pruneUnknownOutline(form.outlineItems)
  }
  if (!Array.isArray(form.bidRequirements)) form.bidRequirements = []
}

function isWeakPerfTitle(name: string): boolean {
  const compact = (name || '').replace(/\s+/g, '').toLowerCase()
  if (!compact) return true
  return /snipaste|screenshot|img_|dsc_|wechat|微信图片|屏幕截图|截图/.test(compact)
}

function absorbLibraryPerformance(files: SlotFileInfo[]) {
  form.performanceLines = form.performanceLines.filter((row) => !isWeakPerfTitle(row.projectName))
  const seen = new Set(
    form.performanceLines
      .map((row) => row.projectName.replace(/\s+/g, '').toLowerCase())
      .filter(Boolean),
  )
  for (const file of files) {
    const line = file.performance
    if (!line?.projectName || isWeakPerfTitle(line.projectName)) continue
    const key = line.projectName.replace(/\s+/g, '').toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    const includeInBid =
      !performanceRequirementActive(form.performanceRequirement) ||
      matchPerformanceLine(line, form.performanceRequirement).passed
    form.performanceLines.push({ ...emptyPerformanceLine(), ...line, includeInBid })
  }
  padPerformancePlaceholders()
}

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const canEditLibrary = computed(
  () => auth.isAdmin || (auth.menus || []).includes('/tender-library'),
)
const loading = ref(true)
const generating = ref(false)
const pendingBoth = ref(false)
const followAdvice = ref(false)
type GenerationMode = 'full' | 'split' | 'single'
const generationMode = ref<GenerationMode | null>(null)
const effectiveGenerationMode = computed<GenerationMode>(() => {
  if (generationMode.value) return generationMode.value
  if (followAdvice.value && form.volumeAdvice === 'full') return 'full'
  if (followAdvice.value && form.volumeAdvice === 'split') return 'split'
  return 'single'
})
const draftPreference = reactive<{ business?: boolean; technical?: boolean }>({})

function syncFullDrafts() {
  if (effectiveGenerationMode.value !== 'full') return
  form.autoDraftBizResp = draftPreference.business ?? true
  form.autoDraftTechPlan = draftPreference.technical ?? true
}

const parsing = ref(false)
const parseElapsed = ref(0)
let parseClock = 0
const downloading = ref<'docx' | 'tech' | 'pdf' | null>(null)
const uploadingKey = ref<string | null>(null)
const error = ref('')
const success = ref('')
const qualification = ref<QualificationStatus | null>(null)
const layoutTemplate = ref<Chapter5TemplateStatus | null>(null)
const uploadingLayoutTemplate = ref(false)
const layoutTemplateInput = ref<HTMLInputElement | null>(null)
const result = ref<GenerateResult | null>(null)
const knownFiles = ref({ docx: false, tech: false })
const draftingTech = ref(false)
const redraftingId = ref('')
const form = reactive<BidBrief>(emptyBrief())
/** 进入页时的公司默认表单，重置时回到这里，不带本次邀请书 */
let formBaseline: BidBrief | null = null
const inviteFile = ref<File | null>(null)
const quoteFile = ref<File | null>(null)
const quoteRecordIds = ref<string[]>([])
const quoteRecordItems = ref<QuoteRecordItem[]>([])
const quotePickerOpen = ref(false)
const quotePickerLoading = ref(false)
const quotePickerRows = ref<QuoteRecordItem[]>([])
const quotePickerQuery = ref('')
const quotePickerDraft = ref<string[]>([])
const kbList = ref<KnowledgeBaseItem[]>([])
const selectedKbIds = ref<string[]>([])
const techSourceNames = ref<string[]>([])
const uploadingTechSource = ref(false)

async function onTechSourceChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || uploadingTechSource.value) return
  uploadingTechSource.value = true
  error.value = ''
  try {
    const source = await uploadTechPlanSource(file)
    form.techPlanSourceIds = [...(form.techPlanSourceIds || []), source.sourceId]
    techSourceNames.value.push(source.filename)
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '技术资料上传失败'
  } finally {
    uploadingTechSource.value = false
    input.value = ''
  }
}
const promptOptions = ref<PromptOption[]>([])
const selectedPromptId = ref<string>('')
const parseNotes = ref<string[]>([])
const attachmentMatch = ref<AttachmentMatch | null>(null)
const parseOcr = ref<{
  pageCount: number
  ocrPages: number
  ocrCapped: boolean
  ocrMaxPages: number
} | null>(null)
const slotStatuses = ref<SlotStatus[]>([])
const catalogSlots = ref<SlotStatus[]>([])
const techDrawingSlot = ref<SlotStatus>({
  key: 'tech_drawings',
  title: '实施方案图纸',
  hint: '本标平面图、系统图或施工图。换一份邀请书需重新上传，不要用其他项目图纸。',
  fileCount: 0,
  files: [],
})
const fileInput = ref<HTMLInputElement | null>(null)
const quoteInput = ref<HTMLInputElement | null>(null)
const slotFileInput = ref<HTMLInputElement | null>(null)
const pendingSlotKey = ref<string | null>(null)
const pendingSlotReplace = ref(true)
/** 识别后是否已跑过「识别并填表」 */
const hasParsed = ref(false)
/** 右侧资料库列表默认折叠为紧凑条 */
const slotsExpanded = ref(true)
/** 向导当前步骤 0=上传 1=填表 2=在线预览 3=AI自检 4=下载定稿 */
const currentStepIndex = ref(0)
/** 填写页切换商务标 / 技术标；生成后各出一本 Word */
const activeVolume = ref<BidVolume>('business')
const visibleOutlineItems = computed(() =>
  pruneUnknownOutline(form.outlineItems).filter(
    (item) => !isVolumeLabelTitle(item.title) && outlineItemVolume(item) === activeVolume.value,
  ),
)
const previewVolume = ref<BidVolume>('business')
const volumeHint = ref('')
const maxStepReached = ref(0)
const previewFullscreen = ref(false)
/** 识别说明默认展开；每次重新识别后强制再展开 */
const parseNotesEpoch = ref(0)
const previewLayoutTick = ref(0)
const showRecords = ref(false)
const previewHintsOpen = ref(true)
const recordsLoading = ref(false)
const records = ref<TenderRecordItem[]>([])
const recordsTotal = ref(0)
const recordsQuery = ref('')
const openingRecordId = ref<string | null>(null)
const regeneratingId = ref<string | null>(null)
const deletingId = ref<string | null>(null)
const confirmDeleteOpen = ref(false)
const pendingDeleteItem = ref<TenderRecordItem | null>(null)
const confirmClearOpen = ref(false)
const clearingRecords = ref(false)
const confirmResetOpen = ref(false)
const resetting = ref(false)
const publishing = ref(false)
const loadingFormId = ref<string | null>(null)
const confirmGenerateOpen = ref(false)
const confirmGenerateMode = ref<'form' | 'record'>('form')
const pendingRegenItem = ref<TenderRecordItem | null>(null)
const confirmDateOpen = ref(false)
const formatPreviewOpen = ref(false)
const formatPreviewFile = ref('')
const formatPreviewOnly = ref(false)
const formatPreviewMessage = ref('')
const formatPreviewBusy = ref(false)
const formatPreviewTick = ref(0)
const formatConfirmBusy = ref(false)
const formatSaveError = ref('')
const formatEditorRef = ref<{ flushSave: () => Promise<void> } | null>(null)
const bidEditorRef = ref<{ flushSave: () => Promise<void> } | null>(null)
const bidSaveBusy = ref(false)
const qaReport = ref<QaReport | null>(null)
const qaLoading = ref(false)
const qaError = ref('')

const paySum = computed(
  () => form.prepaidPct + form.arrivalPct + form.settlementPct + form.warrantyPct,
)

const previewDoc = computed(() => {
  const r = result.value
  if (!r) return { file: '', name: '' }
  if ((effectiveGenerationMode.value === 'full' || r.generateVolume === 'full') && r.docxFile) {
    return {
      file: r.docxFile,
      name: r.downloadName || `${r.projectName || 'bid'}-综合标.docx`,
    }
  }
  return volumeDocx(r, previewVolume.value)
})

const parseRiskNoteCount = computed(
  () =>
    parseNotes.value.filter((n) => /废标|扣分|未解析|未找到|缺|失败|警告/.test(n)).length,
)

const PARSE_NOISE_RE =
  /已匹配资料库扫描件|本标需要但未上传|已纳入本标但未上传|资料库原先没有|资料库新建空项|本公司近期组卷|最近投标曾附|常用附件|未上传的扫描件会在 Word|投标人公司信息不从邀请书|不会用其他项目文件顶替|已解析分项|投标总价已按清单|类似业绩已从资料库|已抽出组卷大纲|将按大纲组卷|仍用公司固定模板|仍用第五章|所选知识库没有|已仅根据邀请书抽取|已根据邀请书回填|未从招标书抽出|招标优先|不自动删行|已按招标门槛预选|资料库原件未改|可复制空白稿|知识库摘录|请先核对标题|报价表表头已按招标书|商务偏离表表头|技术偏离表表头|\bm[a-f0-9]{10,}\b/i

function isParseNoiseNote(note: string) {
  if (/废标|扣分|未解析|OCR 已达|模型未抽出/.test(note)) return false
  return PARSE_NOISE_RE.test(note) || note.length > 160
}

function isParsePrimaryNote(note: string) {
  if (isParseNoiseNote(note)) return false
  return /废标|扣分|未解析|未找到|失败|警告|OCR 已达|模型未抽出|暂用内置模板|请确认使用哪一档|类似业绩符合|均未达到招标|须授权委托|来自知识库|法定代表人/.test(
    note,
  )
}

const primaryParseNotes = computed(() => parseNotes.value.filter(isParsePrimaryNote))

const extraParseNotes = computed(() => {
  const shown = new Set(primaryParseNotes.value)
  return parseNotes.value.filter((n) => !shown.has(n) && !isParseNoiseNote(n))
})

const parseNotesSummary = computed(() => {
  const risk = parseRiskNoteCount.value
  const primary = primaryParseNotes.value.length
  if (risk) return `${risk} 条风险，${primary} 条摘要`
  if (primary) return `${primary} 条识别摘要`
  return parseNotes.value.length ? `共 ${parseNotes.value.length} 条识别说明` : ''
})

const parseExtraOpen = ref(false)
const parseMatchListOpen = ref(false)
const vetoListOpen = ref(false)
const generateHintsDismissed = ref(false)

const GENERATE_NOISE_RE =
  /生成耗时|目录独占一页|更新域|附件区已处理|另有 \d+ 处无扫描件|已匹配资料库扫描件|已附「投标承诺书」|为加快生成未写入|未嵌入 Word|已插入资质文件|分项报价来自|装订时请|已纳入本标但未上传|资料库新建空项|邀请书未抽出资料清单|已有 \d+ 类附件|以下资料未上传|未上传（虚线框|已按组卷大纲用模块填写|已按组卷大纲复制|实施方案图纸用虚线框|报价表表头已按招标书|商务\/技术偏离表共用/

function isGenerateRiskWarning(note: string) {
  return /未解析到本次|暂用内置模板|请确认使用哪一档|投标总价为 0|未填写法定代表人|支付比例合计|单价为空|未识别到报价清单|未能插入报价|资质 PDF|未找到资质|文字说明待补|实施方案文字描述未写|尚未上传图纸|尚未填写技术偏差|类似业绩为空|尚未抽出摘要|请上传招标工程量|折算|会大量扣分|可能扣分|类似业绩符合|均未达到招标|未从招标书复制|未使用充电桩固定承诺书|邀请书正文为空|大纲无.*对应章节|无可填章节/.test(
    note,
  )
}

function isGenerateNoiseWarning(note: string) {
  if (isGenerateRiskWarning(note)) return false
  return GENERATE_NOISE_RE.test(note) || note.length > 180
}

const generateRiskWarnings = computed(() =>
  (result.value?.warnings || []).filter(isGenerateRiskWarning),
)

const generateShownWarnings = computed(() => {
  const all = result.value?.warnings || []
  const risks = all.filter(isGenerateRiskWarning)
  if (risks.length) return risks
  return all.filter((w) => !isGenerateNoiseWarning(w)).slice(0, 3)
})

const generateHintsSummary = computed(() => {
  const risk = generateRiskWarnings.value.length
  const shown = generateShownWarnings.value.length
  if (risk) return `${risk} 条需核对`
  if (shown) return `${shown} 条提示`
  return ''
})

const showFormGenerateHints = computed(
  () =>
    Boolean(result.value) &&
    !generateHintsDismissed.value &&
    generateShownWarnings.value.length > 0,
)

function dismissGenerateHints() {
  generateHintsDismissed.value = true
}

async function onPickLayoutTemplate(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  uploadingLayoutTemplate.value = true
  error.value = ''
  try {
    layoutTemplate.value = await uploadTenderLayoutTemplate(file)
    // 上传的是自定义空白稿，重新体检并把结果落到下拉里
    fixedTemplates.value = await fetchFixedTemplates().catch(() => fixedTemplates.value)
    const health = activeFixedTemplate.value?.health
    if (health && !health.ok) {
      error.value = `模板标题体检未通过：缺少「${health.missingRequired.join('、')}」，生成时会被拦下`
    } else if (health) {
      success.value = '模板已更换，标题体检通过'
    }
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '更换固定模板失败'
  } finally {
    uploadingLayoutTemplate.value = false
  }
}

async function onRestoreLayoutTemplate() {
  uploadingLayoutTemplate.value = true
  error.value = ''
  try {
    layoutTemplate.value = await restoreTenderLayoutTemplate()
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '恢复内置模板失败'
  } finally {
    uploadingLayoutTemplate.value = false
  }
}

/** 轨道A 固定模板下拉（Phase1）。缺失的项置灰，避免点了才报错。 */
const fixedTemplates = ref<FixedTemplate[]>([])
const outlinePreviewRows = ref<OutlinePreviewRow[]>([])
const outlineWarnings = ref<string[]>([])
const outlineBusy = ref(false)

/** 轨道B：切到按本标时立即拉大纲预览；未确认不放行生成。 */
async function loadOutlinePreview() {
  outlineBusy.value = true
  try {
    const data = await previewTenderOutline(form as unknown as BidBrief)
    outlinePreviewRows.value = data.items || []
    outlineWarnings.value = data.warnings || []
    for (const row of outlinePreviewRows.value) {
      const hit = form.outlineItems.find((x) => x.id === row.id)
      if (hit && !hit.copyPolicy) hit.copyPolicy = row.copyPolicy
    }
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '大纲预览失败'
    outlinePreviewRows.value = []
  } finally {
    outlineBusy.value = false
  }
}

async function confirmFormatTemplate() {
  if (formatPreviewOnly.value) {
    formatPreviewOpen.value = false
    return
  }
  if (!formatPreviewFile.value) {
    error.value = '请先点「预览模板」核对截出的格式章'
    return
  }
  if (formatConfirmBusy.value) return
  formatConfirmBusy.value = true
  formatSaveError.value = ''
  try {
    if (!formatEditorRef.value) throw new Error('模板编辑器尚未就绪，请稍后再试')
    await formatEditorRef.value.flushSave()
    await confirmInvitationFormatDocx(String(form.invitationId || '').trim())
    form.formatConfirmed = true
    formatPreviewOpen.value = false
    error.value = ''
    success.value = '已确认使用这份模板，可生成'
  } catch (e) {
    formatSaveError.value = e instanceof Error ? e.message : '模板保存失败'
    error.value = formatSaveError.value
  } finally {
    formatConfirmBusy.value = false
  }
}

async function onFormatPreviewOpen(open: boolean) {
  if (open) {
    formatSaveError.value = ''
    formatPreviewOpen.value = true
    return
  }
  if (formatPreviewOnly.value) {
    formatPreviewOpen.value = false
    formatSaveError.value = ''
    return
  }
  if (formatConfirmBusy.value) return
  formatConfirmBusy.value = true
  formatSaveError.value = ''
  try {
    if (!formatEditorRef.value) throw new Error('模板编辑器尚未就绪，请稍后再试')
    await formatEditorRef.value.flushSave()
    await confirmInvitationFormatDocx(String(form.invitationId || '').trim())
    formatPreviewOpen.value = false
  } catch (e) {
    formatSaveError.value = e instanceof Error ? e.message : '模板保存失败'
    error.value = formatSaveError.value
  } finally {
    formatConfirmBusy.value = false
  }
}

function clearFormatPreview() {
  form.formatConfirmed = false
  formatPreviewFile.value = ''
  formatPreviewOnly.value = false
  formatPreviewMessage.value = ''
  formatPreviewOpen.value = false
}

/** 预览识别出的格式章 Word；后端会拷一份到标书目录再走统一预览。 */
async function loadFormatFile(openDialog: boolean) {
  const invitationId = String(form.invitationId || '').trim()
  if (!invitationId) {
    error.value = '未识别到招标文件，无法预览格式章'
    return
  }
  formatPreviewBusy.value = true
  error.value = ''
  try {
    const preview = await prepareInvitationFormatDocx(invitationId, {
      ...form,
      layoutMode: generationLayoutMode(),
      generateVolume: effectiveGenerationMode.value === 'full' ? 'full' : activeVolume.value,
    })
    if (!preview.fileName) throw new Error('未生成预览文件')
    formatPreviewFile.value = preview.fileName
    formatPreviewOnly.value = preview.previewOnly
    formatPreviewMessage.value = preview.message
    if (preview.source === 'invitation') {
      form.requirementsDriven = false
      form.structureMode = 'outline'
      form.layoutMode = 'outline'
    } else if (preview.previewOnly) {
      form.requirementsDriven = preview.source === 'requirements'
      form.structureMode = preview.source === 'requirements' ? 'outline' : 'fixed'
      form.layoutMode = preview.source === 'requirements' ? 'requirements' : 'chapter5'
    }
    formatPreviewTick.value += 1
    if (openDialog) formatPreviewOpen.value = true
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '预览格式章失败'
  } finally {
    formatPreviewBusy.value = false
  }
}

async function openFormatPreview() {
  await loadFormatFile(true)
}

/** 双轨结构来源。fixed=按固定模板，outline=按本标招标书大纲。 */
function setStructureMode(mode: StructureMode) {
  if (hasOriginalFormat()) mode = 'outline'
  form.structureMode = mode
  form.layoutMode = mode === 'outline' ? 'outline' : 'chapter5'
  clearFormatPreview()
  if (mode !== 'outline') {
    outlinePreviewRows.value = []
    outlineWarnings.value = []
  }
}

/** 单节三态：rewrite=重写，verbatim=沿用原文，skipped=跳过。
 *  1:1 忠实渲染（outlineRender=verbatim）下默认沿用原文：
 *  只有用户显式点「重写」才改写，否则投标函会被凭空改写。 */
function itemPolicy(item: OutlineItem): CopyPolicy {
  if (item.copyPolicy === 'rewrite') return 'rewrite'
  if (item.copyPolicy === 'verbatim') return 'verbatim'
  return isVerbatimRender() ? 'verbatim' : 'rewrite'
}

/** 组卷模板是否走 1:1 忠实渲染（与后端 outlineRender 默认一致）。 */
function isVerbatimRender(): boolean {
  return (form.outlineRender || 'verbatim') !== 'curated'
}

function setItemPolicy(item: OutlineItem, policy: string) {
  if (policy === 'skip') {
    item.skipped = true
    return
  }
  item.skipped = false
  item.copyPolicy = policy === 'verbatim' ? 'verbatim' : 'rewrite'
}

const outlineSummary = computed(() => {
  const items = visibleOutlineItems.value
  const volLabel = activeVolume.value === 'technical' ? '技术标' : '商务标'
  if (!items.length) return `${volLabel}无对应条目`
  const parts = [`${volLabel} ${items.length} 条`]
  if (form.requirementsDriven) {
    parts.push('按招标要求综合组卷')
  } else if (form.structureMode === 'outline') {
    parts.push(form.formatConfirmed ? '模板已确认' : '模板待确认')
  } else {
    parts.push(fixedTemplateLabel.value || '固定模板')
  }
  if (form.outlineChapter) parts.push(form.outlineChapter)
  if (form.documentFormat?.specified) parts.push('已抽排版要求')
  return parts.join(' · ')
})

const fixedTemplateLabel = computed(
  () => fixedTemplates.value.find((t) => t.id === form.contentTemplateId)?.label || '',
)

const activeFixedTemplate = computed(
  () => fixedTemplates.value.find((t) => t.id === form.contentTemplateId) || null,
)

function noteTone(note: string) {
  if (/废标|扣分/.test(note)) return 'text-sulfur'
  if (/未解析|未找到|缺|失败/.test(note)) return 'text-sulfur/90'
  if (/已回填|已匹配|已就绪|已解析/.test(note)) return 'text-emerald-700 dark:text-emerald-400'
  return 'text-muted-foreground'
}

function isRiskNote(note: string) {
  return /废标|扣分|未解析|未找到|缺|失败/.test(note)
}

function isBlank(value: unknown) {
  return !String(value ?? '').trim()
}

const agentIdRequired = computed(() => Boolean(form.authEnabled))
const agentNameRequired = computed(() => Boolean(form.authEnabled))
const hasAgent = computed(() => Boolean(form.authEnabled))
const agentHint = ref<{ agentName?: string; agentIdNo?: string }>({})
const agentSlotReady = computed(() => {
  const slot = catalogSlots.value.find((s) => s.key === 'id_agent')
  return Boolean(slot && slot.fileCount > 0)
})

type LegalPersonOption = {
  key: string
  name: string
  gender: string
  age: string
  idNo: string
  hasBack: boolean
}

const legalPersonOptions = computed<LegalPersonOption[]>(() => {
  const slot = catalogSlots.value.find((item) => item.key === 'id_legal')
  const buckets = new Map<string, LegalPersonOption>()
  for (const file of slot?.files || []) {
    const card = file.idCard
    const key = String(card?.personKey || '').trim()
    if (!key) continue
    let row = buckets.get(key)
    if (!row) {
      row = {
        key,
        name: '',
        gender: '',
        age: '',
        idNo: String(card?.idNo || key),
        hasBack: false,
      }
      buckets.set(key, row)
    }
    if (card?.name) row.name = card.name
    if (card?.gender) row.gender = card.gender
    if (card?.age) row.age = card.age
    if (card?.idNo) row.idNo = card.idNo
    if (card?.side === 'back') row.hasBack = true
  }
  return [...buckets.values()].filter((row) => row.name || row.idNo)
})

const legalPickMissing = computed(() => {
  const rows = legalPersonOptions.value
  if (rows.length < 2) return false
  const key = String(form.legalPersonKey || '').trim()
  return !rows.some((row) => row.key === key)
})

const authPanelSubtitle = computed(() => {
  if (legalPickMissing.value) return '资料库有多位法定代表人，请先选择本标使用的人和身份证'
  if (form.authEnabled) return '须填委托人姓名和身份证号；扫描件优先用投标资料库'
  if (form.authNeed === 'required') return '招标书写了须授权委托，默认法人自签；要用委托人请打开开关'
  return '法人身份证贴在身份证明页；多本证件时在下方选择本标用谁'
})

function applyLegalPerson() {
  const hit = legalPersonOptions.value.find((row) => row.key === form.legalPersonKey)
  if (!hit) return
  if (hit.name) form.legalPersonName = hit.name
  if (hit.gender) form.legalPersonGender = hit.gender
  if (hit.age) form.legalPersonAge = hit.age
  if (hit.idNo) form.legalPersonIdNo = hit.idNo
}

function syncLegalPersonPick() {
  const rows = legalPersonOptions.value
  const key = String(form.legalPersonKey || '').trim()
  if (key && !rows.some((row) => row.key === key)) form.legalPersonKey = ''
  if (rows.length === 1 && !String(form.legalPersonKey || '').trim()) {
    form.legalPersonKey = rows[0].key
    applyLegalPerson()
  }
}

watch(legalPersonOptions, () => syncLegalPersonPick())

function fillAgentFromReadySources() {
  if (!form.authEnabled) return
  const hint = agentHint.value || {}
  if (isBlank(form.agentName) && hint.agentName) form.agentName = hint.agentName
  if (isBlank(form.agentIdNo) && hint.agentIdNo) form.agentIdNo = hint.agentIdNo
  const slot = catalogSlots.value.find((s) => s.key === 'id_agent')
  for (const f of slot?.files || []) {
    const raw = String(f.name || f.fileName || '')
    if (isBlank(form.agentIdNo)) {
      const idm = raw.match(/\d{17}[\dXx]/)
      if (idm) form.agentIdNo = idm[0].toUpperCase()
    }
    if (isBlank(form.agentName)) {
      const nm = raw.match(/([\u4e00-\u9fff]{2,4})(?=身份证|身份|委托|授权)/)
      if (nm && nm[1] !== '法人' && nm[1] !== '法定') form.agentName = nm[1]
    }
  }
}

function syncAuthOutline(on: boolean) {
  for (const item of form.outlineItems) {
    if (item.kind === 'auth') {
      item.skipped = !on
      item.source = outlineSourceFor(item.kind, !on)
    }
  }
}

watch(
  () => form.authEnabled,
  (on) => {
    syncAuthOutline(Boolean(on))
    if (on) fillAgentFromReadySources()
  },
)

watch(
  () => form.agentName,
  (name) => {
    if (!form.authEnabled || !String(name || '').trim()) return
    for (const item of form.outlineItems) {
      if (item.kind === 'auth' && item.skipped) setOutlineSkipped(item, false)
    }
  },
)

function uniqueSlotKeys(keys: string[] | undefined) {
  const out: string[] = []
  const seen = new Set<string>()
  for (const raw of keys || []) {
    const key = String(raw || '').trim()
    if (!key || seen.has(key)) continue
    seen.add(key)
    out.push(key)
  }
  return out
}

const effectiveRequiredKeys = computed(() => {
  let keys = uniqueSlotKeys(form.requiredSlotKeys)
  if (!keys.includes('id_legal')) keys = ['id_legal', ...keys]
  if (hasAgent.value) {
    if (!keys.includes('id_agent')) keys = [...keys, 'id_agent']
  } else {
    keys = keys.filter((k) => k !== 'id_agent')
  }
  return keys
})

const effectiveIncludeKeys = computed(() =>
  uniqueSlotKeys([...(form.includeSlotKeys || []), ...effectiveRequiredKeys.value]),
)

const missingMaterialTitles = computed(() => {
  const titles: string[] = []
  for (const slot of placeholderItems.value) {
    if (effectiveRequiredKeys.value.includes(slot.key) && !slot.fileCount) {
      titles.push(slot.title || '扫描件')
    }
  }
  return titles
})

const generateBlockers = computed(() => {
  const issues: string[] = []
  if (isBlank(form.projectName)) issues.push('项目名称')
  if (isBlank(form.tenderer)) issues.push('招标人')
  if (isBlank(form.bidderName)) issues.push('投标人全称')
  if (legalPickMissing.value) issues.push('本标法定代表人')
  if (form.structureMode === 'outline' && !form.requirementsDriven && !form.formatConfirmed) issues.push('确认格式章模板')
  if (activeVolume.value === 'technical') return issues
  if (!(Number(form.bidPriceYuan) > 0)) issues.push('投标总价')
  if (isBlank(form.bidderAddress)) issues.push('地址')
  if (isBlank(form.foundedDate)) issues.push('成立日期')
  if (isBlank(form.bidderPhone)) issues.push('电话')
  if (isBlank(form.legalPersonName)) issues.push('法人姓名')
  if (isBlank(form.legalPersonAge)) issues.push('年龄')
  if (isBlank(form.legalPersonIdNo)) issues.push('法人身份证号')
  if (agentNameRequired.value && isBlank(form.agentName)) issues.push('委托代理人')
  if (agentIdRequired.value && isBlank(form.agentIdNo)) issues.push('代理人身份证号')
  return issues
})

const generateButtonDisabled = computed(
  () => generating.value || generateBlockers.value.length > 0,
)

const generateButtonTooltip = computed(() => {
  if (generating.value) return ''
  if (generateBlockers.value.length) {
    return `请先完善：${generateBlockers.value.join('、')}`
  }
  return ''
})

const HIGH_DISQUALIFY_KEYS = new Set(['id_legal', 'id_agent', 'bond', 'seal', 'finance', 'credit'])
const TECHNICAL_SLOT_KEYS = new Set(['product', 'tech_drawings'])
const TECH_DRAWING_KEY = 'tech_drawings'

function blankTechDrawings(): SlotStatus {
  return {
    key: TECH_DRAWING_KEY,
    title: '本项目图纸',
    hint: '本标平面图、系统图或施工图。换一份邀请书需重新上传，不要用其他项目图纸。',
    fileCount: 0,
    files: [],
  }
}

function slotVolume(slot: SlotStatus): 'business' | 'technical' {
  if (TECHNICAL_SLOT_KEYS.has(slot.key)) return 'technical'
  if (HIGH_DISQUALIFY_KEYS.has(slot.key)) return 'business'
  if (/方案|布置|配电|运维|图纸|检测|3C|技术规格/.test(slot.title || '')) return 'technical'
  return 'business'
}

const technicalWarnings = computed(() => {
  const issues: string[] = []
  if (!form.deviationLines.some((row) => String(row.requirement || '').trim())) {
    issues.push('技术偏差表为空，不响应技术要求会大量扣分')
  }
  const emptyChaps = techChapterItems.value.filter((item) => !String(item.draftBody || '').trim())
  if (emptyChaps.length) {
    issues.push(`${emptyChaps.length} 章技术标尚未起草`)
  } else if (!String(form.techPlanNote || '').trim() && !techChapterItems.value.length) {
    issues.push('技术方案尚未起草')
  }
  if (!techDrawingSlot.value.fileCount) issues.push('尚未上传图纸 PDF')
  if (performanceRequirementActive(form.performanceRequirement)) {
    const ph = form.performanceLines.filter(
      (row) => row.includeInBid !== false && isPerfPlaceholder(row),
    ).length
    if (ph) issues.push(`类似业绩有 ${ph} 条资料库未匹配，已占位，可手工补`)
  }
  return issues
})

const softGenerateNotes = computed(() => {
  const notes: string[] = []
  const vol = activeVolume.value
  const miss = missingMaterialTitles.value.filter((title) => {
    const slot = placeholderItems.value.find((s) => (s.title || '扫描件') === title)
    if (!slot) return vol === 'business'
    return slotVolume(slot) === vol
  })
  if (miss.length) {
    notes.push(`未上传资料将以虚线框占位：${miss.join('、')}`)
  }
  for (const w of technicalWarnings.value) {
    const techNote = /技术偏差|实施方案|图纸/.test(w)
    const bizNote = /类似业绩/.test(w)
    if (vol === 'technical' && techNote) notes.push(w)
    if (vol === 'business' && bizNote) notes.push(w)
  }
  return notes
})

const formStepNextLabel = computed(() => {
  if (generating.value) return '正在生成…'
  if (workflowLocked.value) return '当前任务只读'
  if (effectiveGenerationMode.value === 'full') {
    return result.value?.docxFile ? '覆盖生成完整投标文件（综合标）' : '生成完整投标文件（综合标）'
  }
  if (effectiveGenerationMode.value === 'split') return '按分册生成两卷'
  const volLabel = activeVolume.value === 'technical' ? '技术标' : '商务标'
  const hasThis =
    activeVolume.value === 'technical' ? Boolean(result.value?.techDocxFile) : Boolean(result.value?.docxFile)
  return hasThis ? `覆盖生成${volLabel}` : `生成${volLabel}并预览`
})

const volumeStatusHint = computed(() => {
  if (effectiveGenerationMode.value === 'full') return '将生成一本综合标，包含商务响应和技术方案，使用连续目录。'
  if (effectiveGenerationMode.value === 'split') return '将分别生成商务标、技术标两本 Word。'
  if (followAdvice.value && form.volumeNote) return form.volumeNote
  if (result.value?.generateVolume === 'full') return '本次生成的是完整投标文件。'
  const cur = activeVolume.value === 'technical' ? '技术标' : '商务标'
  const other = activeVolume.value === 'technical' ? '商务标' : '技术标'
  const row = result.value
  if (row?.generateVolume === activeVolume.value && row.otherVolumeNote) return row.otherVolumeNote
  if (row?.generateVolume === activeVolume.value && row.otherVolumeReady) {
    return `本次生成的是${cur}，${other}已生成。`
  }
  const docx = row ? Boolean(row.docxFile) : knownFiles.value.docx
  const tech = row ? Boolean(row.techDocxFile) : knownFiles.value.tech
  const ready = activeVolume.value === 'technical' ? docx : tech
  return ready ? `当前将只生成${cur}。${other}已生成。` : `当前将只生成${cur}。${other}尚未生成。`
})

const workflowLocked = computed(() =>
  isTenderWorkflowLocked(result.value?.status, result.value?.workflowLocked),
)

const confirmGenerateTitle = computed(() =>
  confirmGenerateMode.value === 'record' ? '覆盖当前记录' : '覆盖生成当前任务',
)

const confirmGenerateDescription = computed(() =>
  confirmGenerateMode.value === 'record'
    ? '将用该记录保存的表单和当前资料库附件重新生成 Word，覆盖同一条任务，不改变审批状态。'
    : '将覆盖当前编制中任务的 Word。上次在预览里改过的内容不会自动合并进这次。',
)

function bidDateOffToday() {
  const t = String(form.bidDate || '').trim()
  return Boolean(t) && t !== todayYmd()
}

async function continueGenerateAfterChecks() {
  if (result.value) {
    confirmGenerateMode.value = 'form'
    pendingRegenItem.value = null
    confirmGenerateOpen.value = true
    return
  }
  await doGenerate()
}

const placeholderItems = computed((): SlotStatus[] => {
  const byKey = new Map<string, SlotStatus>()
  for (const slot of catalogSlots.value) {
    if (slot.key) byKey.set(slot.key, slot)
  }
  for (const slot of slotStatuses.value) {
    if (!slot.key) continue
    const prev = byKey.get(slot.key)
    byKey.set(slot.key, prev ? { ...prev, ...slot } : slot)
  }
  for (const extra of form.extraPlaceholders || []) {
    if (!extra.key || byKey.has(extra.key)) continue
    byKey.set(extra.key, {
      key: extra.key,
      title: extra.title,
      hint: extra.hint || '',
      fileCount: 0,
      files: [],
    })
  }
  const items: SlotStatus[] = []
  for (const key of effectiveIncludeKeys.value) {
    if (key === TECH_DRAWING_KEY) continue
    const slot = byKey.get(key)
    if (slot) items.push(slot)
  }
  return items
})

const optionalCatalogSlots = computed(() =>
  catalogSlots.value.filter(
    (slot) =>
      slot.key
      && slot.key !== TECH_DRAWING_KEY
      && !effectiveIncludeKeys.value.includes(slot.key),
  ),
)

const slotFilledCount = computed(
  () => placeholderItems.value.filter((s) => s.fileCount > 0).length,
)

const matchHasRows = computed(() => {
  const m = attachmentMatch.value
  if (!m) return false
  return m.matched.length + m.missingFiles.length + m.createdItems.length > 0
})

const TECH_VETO_MARKS = [
  '技术标准',
  '技术规格',
  '技术参数',
  '技术指标',
  '技术要求',
  '技术偏离',
  '技术偏差',
  '负偏离',
  '施工组织',
  '暗标',
]
const BIZ_VETO_MARKS = [
  '营业执照',
  '信誉',
  '法定代表人',
  '投标保证金',
  '履约保证金',
  '投标报价',
  '类似项目',
  '类似业绩',
  '授权委托',
  '投标函',
  '联合体',
  '项目经理',
  '单位负责人',
  '失信',
  '信用信息',
]

function textVetoVolume(text: string): '' | 'business' | 'technical' {
  const tech = TECH_VETO_MARKS.some((k) => text.includes(k))
  const biz = BIZ_VETO_MARKS.some((k) => text.includes(k))
  if (tech && !biz) return 'technical'
  if (biz && !tech) return 'business'
  return ''
}

function shownVetoVolume(item: { text?: string; targetKind?: string; volume?: string }) {
  const byText = textVetoVolume(item.text || '')
  if (byText) return byText
  const kind = item.targetKind || ''
  if (kind === 'tech_dev' || kind === 'tech_plan' || kind === 'tech_chapter') return 'technical'
  if (kind === 'performance' || kind === 'biz_dev') return 'business'
  if (kind === 'format') return (item.text || '').includes('暗标') ? 'technical' : 'business'
  return item.volume === 'technical' || item.volume === 'business' ? item.volume : ''
}

const vetoItems = computed(() =>
  (form.bidRequirements || []).filter(
    (item) => item.strength === 'mandatory' && shownVetoVolume(item) === activeVolume.value,
  ),
)

const compareItems = computed(() =>
  (form.bidRequirements || []).filter((item) => {
    const text = String(item.text || '').trim()
    if (text.length < 8) return false
    if (item.targetKind === 'format' || item.category === 'format') return false
    const keep =
      item.strength === 'mandatory' ||
      ['qualification', 'material', 'scoring', 'performance', 'veto'].includes(item.category || '') ||
      Boolean(item.display)
    if (!keep) return false
    const vol = shownVetoVolume(item) || (item.volume === 'technical' || item.volume === 'business' ? item.volume : '')
    if (vol === 'business' || vol === 'technical') return vol === activeVolume.value
    return activeVolume.value === 'business'
  }),
)

function compareAnswer(item: { matchStatus?: string; respond?: string }) {
  if (item.matchStatus === 'matched') return '已响应'
  if (item.matchStatus === 'gap') return '待补充'
  if (item.matchStatus === 'format') return '只提示'
  if (item.matchStatus === 'unbound') return '格式章无此空'
  return item.respond || '待核对'
}

const vetoOtherCount = computed(() =>
  (form.bidRequirements || []).filter((item) => {
    if (item.strength !== 'mandatory') return false
    const vol = shownVetoVolume(item)
    return Boolean(vol) && vol !== activeVolume.value
  }).length,
)

const activeFormatNotes = computed(() => {
  const fmt = form.documentFormat
  const spec = activeVolume.value === 'technical' ? fmt?.technicalFormat : fmt?.businessFormat
  return spec?.notes || []
})

function vetoStatusLabel(status?: string) {
  if (status === 'matched') return '已有证据'
  if (status === 'gap') return '尚未覆盖'
  if (status === 'format') return '只提示'
  if (status === 'unbound') return '格式章无此空'
  return '待核对'
}

function dropVeto(id: string) {
  form.bidRequirements = (form.bidRequirements || []).filter((item) => item.id !== id)
}

function easeVeto(id: string) {
  form.bidRequirements = (form.bidRequirements || []).map((item) =>
    item.id === id
      ? { ...item, strength: 'general', matchStatus: '', matchNote: '已降为一般要求' }
      : item,
  )
}

const workflowSteps = [
  { key: 'upload', label: '上传邀请书' },
  { key: 'form', label: '填商务/技术标' },
  { key: 'preview', label: '在线预览' },
  { key: 'qa', label: 'AI 自检' },
  { key: 'generate', label: '下载定稿' },
] as const

const activeWorkflowIndex = computed(() => currentStepIndex.value)

function goToStep(index: number) {
  if (index < 0 || index >= workflowSteps.length) return
  if (index <= maxStepReached.value) {
    if (index !== 2) setPreviewFullscreen(false)
    currentStepIndex.value = index
    error.value = ''
  }
}

function setPreviewFullscreen(on: boolean) {
  previewFullscreen.value = on
  document.body.style.overflow = on ? 'hidden' : ''
}

function togglePreviewFullscreen() {
  setPreviewFullscreen(!previewFullscreen.value)
}

function togglePreviewHints() {
  previewHintsOpen.value = !previewHintsOpen.value
  window.setTimeout(() => {
    previewLayoutTick.value += 1
  }, 0)
}

function onPreviewFullscreenKey(ev: KeyboardEvent) {
  if (ev.key !== 'Escape') return
  if (previewFullscreen.value) {
    ev.preventDefault()
    setPreviewFullscreen(false)
    return
  }
  if (showRecords.value) {
    ev.preventDefault()
    closeRecords()
  }
}

function advanceStep(index: number) {
  currentStepIndex.value = index
  maxStepReached.value = Math.max(maxStepReached.value, index)
}

async function selectVolume(volume: BidVolume) {
  // 顶部卷别切换：点商务/技术即切成单卷，并把预览同步到对应卷。
  if (currentStepIndex.value === 2 && result.value && previewVolume.value !== volume) {
    if (!(await flushBidEdit())) return
  }
  if (effectiveGenerationMode.value !== 'single') {
    generationMode.value = 'single'
    followAdvice.value = false
    pendingBoth.value = false
  }
  activeVolume.value = volume
  previewVolume.value = volume
  volumeHint.value = ''
  if (showRecords.value) closeRecords()

  if (currentStepIndex.value === 0) {
    if (hasParsed.value) {
      advanceStep(1)
      await nextTick()
    } else if (volume === 'technical') {
      volumeHint.value =
        '请先上传邀请书并点「识别并继续」。左上角选哪一卷，生成时就只出那一卷 Word。'
    }
    return
  }

  if (currentStepIndex.value === 1) {
    await nextTick()
    // 只滚到卷内标题，避免把上方「识别说明」顶出视野
    document
      .getElementById(`tender-volume-${volume}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }
}

async function selectFullMode() {
  // 综合标：不分卷，生成一本完整投标文件。
  if (currentStepIndex.value === 2 && result.value && !(await flushBidEdit())) return
  generationMode.value = 'full'
  followAdvice.value = false
  pendingBoth.value = false
  activeVolume.value = 'business'
  previewVolume.value = 'business'
  syncFullDrafts()
  volumeHint.value = ''
  if (showRecords.value) closeRecords()
  if (currentStepIndex.value === 0 && hasParsed.value) {
    advanceStep(1)
    await nextTick()
  }
}

const dragOverInvite = ref(false)
const dragOverQuote = ref(false)
const dragOverDrawings = ref(false)

function assignInviteFile(file: File) {
  const name = file.name.trim().toLowerCase()
  if (!name.endsWith('.doc') && !name.endsWith('.docx')) {
    error.value = '投标邀请书只支持 .doc 和 .docx'
    if (fileInput.value) fileInput.value.value = ''
    return
  }
  error.value = ''
  if (file !== inviteFile.value) {
    hasParsed.value = false
    followAdvice.value = false
    parseNotes.value = []
    parseOcr.value = null
    attachmentMatch.value = null
    result.value = null
    knownFiles.value = { docx: false, tech: false }
    qaReport.value = null
    form.invitationId = ''
    clearFormatPreview()
    techDrawingSlot.value = blankTechDrawings()
    drawingFill.value = ''
  }
  inviteFile.value = file
}

function onPickFile(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) assignInviteFile(file)
}

function onInviteDrop(ev: DragEvent) {
  ev.preventDefault()
  dragOverInvite.value = false
  const file = ev.dataTransfer?.files?.[0]
  if (file) assignInviteFile(file)
}

function mergeSlotStatus(payload: SlotStatus) {
  const next: SlotStatus = {
    key: payload.key,
    title: payload.title || payload.key,
    hint: payload.hint || '',
    fileCount: payload.fileCount ?? payload.files?.length ?? 0,
    files: payload.files || [],
    docId: payload.docId,
    pinned: payload.pinned,
  }
  const idx = slotStatuses.value.findIndex((s) => s.key === payload.key)
  if (idx >= 0) {
    const prev = slotStatuses.value[idx]
    slotStatuses.value.splice(idx, 1, {
      ...next,
      title: next.title || prev.title,
      hint: next.hint || prev.hint,
    })
  } else {
    slotStatuses.value.push(next)
  }
  if (payload.key === TECH_DRAWING_KEY) {
    techDrawingSlot.value = { ...techDrawingSlot.value, ...next }
  }
  const cidx = catalogSlots.value.findIndex((s) => s.key === payload.key)
  if (cidx >= 0) {
    const prev = catalogSlots.value[cidx]
    catalogSlots.value.splice(cidx, 1, {
      ...prev,
      ...next,
      title: next.title || prev.title,
      hint: next.hint || prev.hint,
    })
  }
  refreshAttachmentMatch()
}

function refreshAttachmentMatch() {
  const report = attachmentMatch.value
  if (!report) return
  const counts = new Map<string, number>()
  const filledTitles: string[] = []
  for (const slot of [...catalogSlots.value, ...slotStatuses.value]) {
    if (slot.key) counts.set(slot.key, slot.fileCount || 0)
    if ((slot.fileCount || 0) > 0 && slot.title) filledTitles.push(slot.title.replace(/\s/g, ''))
  }
  const seen = new Set<string>()
  const matched: AttachmentMatchItem[] = []
  const missingFiles: AttachmentMatchItem[] = []
  const createdItems: AttachmentMatchItem[] = []
  for (const item of [...report.matched, ...report.missingFiles, ...report.createdItems]) {
    if (!item.key || seen.has(item.key)) continue
    seen.add(item.key)
    const fileCount = counts.has(item.key) ? counts.get(item.key)! : item.fileCount
    const next = { ...item, fileCount }
    if (fileCount > 0) matched.push({ ...next, created: false })
    else if (filledTitles.some((t) => sameLibraryTitle(t, (item.title || '').replace(/\s/g, '')))) {
      const hit = [...catalogSlots.value, ...slotStatuses.value].find(
        (s) =>
          (s.fileCount || 0) > 0 &&
          sameLibraryTitle((s.title || '').replace(/\s/g, ''), (item.title || '').replace(/\s/g, '')),
      )
      if (hit?.key && !seen.has(hit.key)) {
        seen.add(hit.key)
        matched.push({
          key: hit.key,
          title: hit.title,
          fileCount: hit.fileCount,
          required: Boolean(item.required),
          created: false,
        })
      }
    } else if (item.created) createdItems.push(next)
    else missingFiles.push(next)
  }
  attachmentMatch.value = { matched, missingFiles, createdItems }
}

function sameLibraryTitle(a: string, b: string) {
  if (!a || !b) return false
  if (a === b || a.includes(b) || b.includes(a)) return true
  const marks = ['营业执照', '开户许可', 'ISO', '质量体系', '体系认证']
  return marks.some((m) => a.includes(m) && b.includes(m))
}

function includeOptionalSlot(slot: SlotStatus) {
  if (!slot.key) return
  if (!form.includeSlotKeys.includes(slot.key)) {
    form.includeSlotKeys = [...form.includeSlotKeys, slot.key]
  }
  mergeSlotStatus(slot)
}

function excludeOptionalSlot(slot: SlotStatus) {
  if (!slot.key || effectiveRequiredKeys.value.includes(slot.key)) return
  form.includeSlotKeys = form.includeSlotKeys.filter((k) => k !== slot.key)
}

function isRequiredSlot(key: string) {
  return effectiveRequiredKeys.value.includes(key)
}

async function loadRecords() {
  if (!getAccessToken()) return
  recordsLoading.value = true
  try {
    const data = await fetchTenderRecords({
      q: recordsQuery.value.trim() || undefined,
      limit: 50,
    })
    records.value = data.items || []
    recordsTotal.value = data.total || 0
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '加载我的生成记录失败'
  } finally {
    recordsLoading.value = false
  }
}

function formatPrice(n: number) {
  const value = Number(n) || 0
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}
const moneyLabel = computed(() => formatPrice(form.bidPriceYuan))

function formatRecordTime(ms: number) {
  if (!ms) return '—'
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function closeRecords() {
  showRecords.value = false
}

function toggleRecords() {
  if (showRecords.value) {
    closeRecords()
    return
  }
  showRecords.value = true
  void loadRecords()
}

async function openRecordPreview(item: TenderRecordItem) {
  if (!item.docxFile && !item.techDocxFile) {
    error.value = '该记录的 Word 文件已丢失，无法预览'
    return
  }
  if (!item.docxAvailable && !item.techDocxAvailable) {
    error.value = '该记录的 Word 文件已丢失，无法预览'
    return
  }
  // 列表项已有文件名，不必再拉详情（含 brief），直接进只读预览更快
  openingRecordId.value = item.id
  error.value = ''
  try {
    result.value = {
      id: item.id,
      projectName: item.projectName,
      tenderer: item.tenderer,
      bidPriceYuan: item.bidPriceYuan,
      legalPersonName: item.legalPersonName,
      docxFile: item.docxFile,
      techDocxFile: item.techDocxFile,
      pdfFile: item.pdfFile,
      downloadName: item.downloadName || `${item.projectName || 'bid'}.docx`,
      techDownloadName: item.techDownloadName,
      pdfDownloadName: item.pdfDownloadName,
      warnings: item.warnings || [],
      username: item.username,
      createdAt: item.createdAt,
      docxAvailable: item.docxAvailable,
      techDocxAvailable: item.techDocxAvailable,
      pdfAvailable: item.pdfAvailable,
      status: item.status,
      workflowLocked: item.workflowLocked,
      deadline: item.deadline,
      currentStep: item.currentStep,
      generateVolume: item.generateVolume,
    }
    closeRecords()
    generationMode.value = item.generateVolume === 'full' ? 'full' : 'single'
    followAdvice.value = false
    activeVolume.value = item.generateVolume === 'technical' ? 'technical' : 'business'
    previewVolume.value =
      item.generateVolume !== 'full' && activeVolume.value === 'technical' && item.techDocxFile
        ? 'technical'
        : item.docxFile
          ? 'business'
          : 'technical'
    setPreviewFullscreen(false)
    advanceStep(2)
    return true
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '打开记录失败'
    return false
  } finally {
    openingRecordId.value = null
  }
}

async function openRecordFromQuery() {
  const id = String(route.query.record || '').trim()
  if (!id || !getAccessToken()) return
  openingRecordId.value = id
  error.value = ''
  try {
    // 深链仍需详情（列表可能未加载）
    const detail = await fetchTenderRecord(id)
    if (!detail.docxFile && !detail.techDocxFile) {
      error.value = '该记录的 Word 文件已丢失，无法预览'
      return
    }
    if (!detail.docxAvailable && !detail.techDocxAvailable) {
      error.value = '该记录的 Word 文件已丢失，无法预览'
      return
    }
    result.value = {
      id: detail.id,
      projectName: detail.projectName,
      tenderer: detail.tenderer,
      bidPriceYuan: detail.bidPriceYuan,
      legalPersonName: detail.legalPersonName,
      docxFile: detail.docxFile,
      techDocxFile: detail.techDocxFile,
      pdfFile: detail.pdfFile,
      downloadName: detail.downloadName,
      techDownloadName: detail.techDownloadName,
      pdfDownloadName: detail.pdfDownloadName,
      warnings: detail.warnings || [],
      username: detail.username,
      createdAt: detail.createdAt,
      docxAvailable: detail.docxAvailable,
      techDocxAvailable: detail.techDocxAvailable,
      pdfAvailable: detail.pdfAvailable,
      status: detail.status,
      workflowLocked: detail.workflowLocked,
      deadline: detail.deadline,
      currentStep: detail.currentStep,
      generateVolume: detail.generateVolume,
    }
    closeRecords()
    generationMode.value = detail.generateVolume === 'full' ? 'full' : 'single'
    followAdvice.value = false
    activeVolume.value = detail.generateVolume === 'technical' ? 'technical' : 'business'
    previewVolume.value =
      detail.generateVolume !== 'full' && activeVolume.value === 'technical' && detail.techDocxFile
        ? 'technical'
        : detail.docxFile
          ? 'business'
          : 'technical'
    setPreviewFullscreen(false)
    advanceStep(2)
    if (route.query.record) {
      const nextQuery = { ...route.query }
      delete nextQuery.record
      await router.replace({ path: '/tenders', query: nextQuery })
    }
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '打开记录失败'
  } finally {
    openingRecordId.value = null
  }
}

async function refreshTechDrawings() {
  const invitationId = String(form.invitationId || '').trim()
  if (!invitationId) {
    techDrawingSlot.value = blankTechDrawings()
    drawingFill.value = ''
    return
  }
  try {
    const slots = await fetchTenderSlots(
      [
        {
          key: TECH_DRAWING_KEY,
          title: techDrawingSlot.value.title,
          hint: techDrawingSlot.value.hint,
        },
      ],
      invitationId,
    )
    const row = slots.find((s) => s.key === TECH_DRAWING_KEY)
    if (row) {
      techDrawingSlot.value = {
        ...techDrawingSlot.value,
        ...row,
        title: row.title || techDrawingSlot.value.title,
        hint: row.hint || techDrawingSlot.value.hint,
      }
    } else {
      techDrawingSlot.value = blankTechDrawings()
      drawingFill.value = ''
    }
  } catch {
    /* 图纸槽位读取失败不打断表单 */
  }
}

async function refreshLibrary() {
  try {
    const library = await fetchTenderLibrary()
    if (library?.slots?.length) {
      catalogSlots.value = library.slots
      slotStatuses.value = library.slots
    }
  } catch {
    /* 资料库刷新失败不打断表单载入 */
  }
}

function hydrateTechPlanNote(brief: BidBrief): string {
  const note = String(brief.techPlanNote || '').trim()
  if (note) return note
  return [
    brief.constructionPlan,
    brief.layoutPlan,
    brief.powerPlan,
    brief.omPlan,
    brief.schedulePlan,
  ]
    .map((s) => String(s || '').trim())
    .filter(Boolean)
    .join('\n\n')
}

function ensureDocumentFormat() {
  form.documentFormat = { ...emptyDocumentFormat(), ...(form.documentFormat || {}) }
  if (!Array.isArray(form.documentFormat.notes)) form.documentFormat.notes = []
}

function applyBriefToForm(brief: BidBrief) {
  Object.assign(form, emptyBrief())
  mergeBriefIntoForm(brief)
  form.techPlanNote = hydrateTechPlanNote(form)
  if (!Array.isArray(form.quoteLines)) form.quoteLines = []
  if (!Array.isArray(form.quoteHeaders)) form.quoteHeaders = []
  if (!Array.isArray(form.quoteRoles)) form.quoteRoles = []
  if (!Array.isArray(form.deviationLines)) form.deviationLines = []
  if (!Array.isArray(form.bizDevHeaders)) form.bizDevHeaders = []
  if (!Array.isArray(form.techDevHeaders)) form.techDevHeaders = []
  if (!Array.isArray(form.performanceLines)) form.performanceLines = []
  if (!Array.isArray(form.outlineItems)) form.outlineItems = []
  if (!Array.isArray(form.bidRequirements)) form.bidRequirements = []
  if (!form.layoutMode) form.layoutMode = 'chapter5'
  if (!form.structureMode) form.structureMode = form.layoutMode === 'outline' ? 'outline' : 'fixed'
  ensureDocumentFormat()
  ensurePerformanceRequirement()
  if (!Array.isArray(form.extraPlaceholders)) form.extraPlaceholders = []
  if (!Array.isArray(form.requiredSlotKeys)) form.requiredSlotKeys = []
  if (!Array.isArray(form.includeSlotKeys)) form.includeSlotKeys = []
}

async function loadRecordForm(item: TenderRecordItem) {
  if (isTenderWorkflowLocked(item.status, item.workflowLocked)) {
    await openRecordPreview(item)
    error.value = '该任务已进入审批或已定稿，仅可预览。需要改稿请先驳回。'
    return
  }
  loadingFormId.value = item.id
  error.value = ''
  try {
    const detail = await fetchTenderRecord(item.id)
    if (!detail.brief) {
      error.value = '该记录没有保存表单，无法载入'
      return
    }
    applyBriefToForm(detail.brief)
    generationMode.value = detail.brief.generateBoth ? 'split' : detail.brief.generateVolume === 'full' ? 'full' : 'single'
    activeVolume.value = detail.brief.generateVolume === 'technical' ? 'technical' : 'business'
    draftPreference.business = form.autoDraftBizResp
    draftPreference.technical = form.autoDraftTechPlan
    knownFiles.value = { docx: Boolean(detail.docxFile), tech: Boolean(detail.techDocxFile) }
    followAdvice.value = false
    hasParsed.value = true
    vetoListOpen.value = false
    closeRecords()
    setPreviewFullscreen(false)
    advanceStep(1)
    await refreshLibrary()
    await refreshTechDrawings()
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '载入表单失败'
  } finally {
    loadingFormId.value = null
  }
}

async function regenerateRecord(item: TenderRecordItem) {
  regeneratingId.value = item.id
  error.value = ''
  generating.value = true
  try {
    result.value = await regenerateTenderRecord(item.id)
    generationMode.value = result.value?.generateVolume === 'full' ? 'full' : 'single'
    followAdvice.value = false
    previewVolume.value =
      result.value?.techDocxFile && !result.value?.docxFile ? 'technical' : 'business'
    closeRecords()
    setPreviewFullscreen(false)
    advanceStep(2)
    await loadRecords()
    await refreshLibrary()
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '重新生成失败'
  } finally {
    regeneratingId.value = null
    generating.value = false
  }
}

function askDeleteRecord(item: TenderRecordItem) {
  pendingDeleteItem.value = item
  confirmDeleteOpen.value = true
}

function onConfirmDeleteOpen(open: boolean) {
  confirmDeleteOpen.value = open
  if (!open && !deletingId.value) {
    pendingDeleteItem.value = null
  }
}

async function confirmDeleteRecord() {
  const item = pendingDeleteItem.value
  if (!item) {
    confirmDeleteOpen.value = false
    return
  }
  deletingId.value = item.id
  error.value = ''
  try {
    await deleteTenderRecord(item.id)
    records.value = records.value.filter((r) => r.id !== item.id)
    recordsTotal.value = Math.max(0, recordsTotal.value - 1)
    if (result.value?.id === item.id) {
      result.value = null
      knownFiles.value = { docx: false, tech: false }
      if (currentStepIndex.value >= 2) {
        currentStepIndex.value = 1
      }
    }
    confirmDeleteOpen.value = false
    pendingDeleteItem.value = null
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '删除记录失败'
  } finally {
    deletingId.value = null
  }
}

function askClearMineRecords() {
  if (!recordsTotal.value) return
  confirmClearOpen.value = true
}

function onConfirmClearOpen(open: boolean) {
  confirmClearOpen.value = open
}

async function confirmClearMineRecords() {
  clearingRecords.value = true
  error.value = ''
  try {
    const data = await clearMineTenderRecords()
    const deletedIds = new Set(data.ids || [])
    if (result.value?.id && deletedIds.has(result.value.id)) {
      result.value = null
      knownFiles.value = { docx: false, tech: false }
      if (currentStepIndex.value >= 2) {
        currentStepIndex.value = 1
      }
    }
    confirmClearOpen.value = false
    await loadRecords()
    if (data.skipped) {
      error.value = `已清空 ${data.deleted} 条，另有 ${data.skipped} 条审批中或已定稿未删除`
    }
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '清空记录失败'
  } finally {
    clearingRecords.value = false
  }
}

async function downloadRecord(item: TenderRecordItem, kind: 'docx' | 'tech' | 'pdf') {
  const picked =
    kind === 'tech'
      ? volumeDocx(item, 'technical')
      : kind === 'docx'
        ? volumeDocx(item, 'business')
        : { file: item.pdfFile || '', name: item.pdfDownloadName || '' }
  if (!picked.file || !picked.name) return
  if (kind === 'docx' && !item.docxAvailable) {
    error.value = '该记录的商务标 Word 已丢失'
    return
  }
  if (kind === 'tech' && !item.techDocxFile) {
    error.value = '该记录还没有技术标 Word，请重新生成'
    return
  }
  downloading.value = kind
  error.value = ''
  try {
    await downloadTenderFile(picked.file, picked.name)
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '下载失败'
  } finally {
    downloading.value = null
  }
}

onMounted(async () => {
  window.addEventListener('keydown', onPreviewFullscreenKey, true)
  if (!getAccessToken()) {
    error.value = '请先登录'
    loading.value = false
    return
  }
  try {
    const [data, bases, prompts, library, templates] = await Promise.all([
      fetchTenderDefaults(),
      listKnowledgeBases({ access: 'use' }).catch(() => [] as KnowledgeBaseItem[]),
      listPromptOptions().catch(() => []),
      fetchTenderLibrary().catch(() => null),
      fetchFixedTemplates().catch(() => [] as FixedTemplate[]),
    ])
    qualification.value = data.qualification
    layoutTemplate.value = data.chapter5Template || null
    fixedTemplates.value = templates.length ? templates : data.fixedTemplates || []
    catalogSlots.value = library?.slots?.length ? library.slots : data.slots || []
    slotStatuses.value = catalogSlots.value
    kbList.value = bases as KnowledgeBaseItem[]
    promptOptions.value = (prompts as import('@/lib/prompts-api').PromptOption[]) || []
    const {
      qualification: _q,
      slots: _s,
      chapter5Template: _t,
      docPreview: _p,
      fixedTemplates: _ft,
      structureModes: _sm,
      ...rest
    } = data as TenderDefaultsLoose
    mergeBriefIntoForm(rest as Partial<BidBrief>)
    if (!Array.isArray(form.extraPlaceholders)) form.extraPlaceholders = []
    if (!Array.isArray(form.requiredSlotKeys)) form.requiredSlotKeys = []
    if (!Array.isArray(form.includeSlotKeys)) form.includeSlotKeys = []
    if (!Array.isArray(form.quoteLines)) form.quoteLines = []
    if (!Array.isArray(form.quoteHeaders)) form.quoteHeaders = []
    if (!Array.isArray(form.quoteRoles)) form.quoteRoles = []
    if (!Array.isArray(form.deviationLines)) form.deviationLines = []
    if (!Array.isArray(form.bizDevHeaders)) form.bizDevHeaders = []
    if (!Array.isArray(form.techDevHeaders)) form.techDevHeaders = []
    if (!Array.isArray(form.performanceLines)) form.performanceLines = []
    if (!Array.isArray(form.outlineItems)) form.outlineItems = []
    if (!Array.isArray(form.bidRequirements)) form.bidRequirements = []
    if (!form.layoutMode) form.layoutMode = 'chapter5'
    if (!form.structureMode) form.structureMode = form.layoutMode === 'outline' ? 'outline' : 'fixed'
    ensureDocumentFormat()
    ensurePerformanceRequirement()
    if (form.includePlaceholders == null) form.includePlaceholders = false
    if (form.includeCommitment == null) form.includeCommitment = true
    form.techPlanNote = hydrateTechPlanNote(form)
    formBaseline = JSON.parse(JSON.stringify(form)) as BidBrief
    void loadRecords()
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '加载默认值失败'
  } finally {
    loading.value = false
  }
  void openRecordFromQuery()
})

watch(
  () => String(route.query.record || ''),
  (id, prev) => {
    if (id && id !== prev && !loading.value) void openRecordFromQuery()
  },
)

watch(previewFullscreen, (on) => {
  if (on) previewHintsOpen.value = false
  window.setTimeout(() => {
    previewLayoutTick.value += 1
  }, 0)
})

watch(previewVolume, () => {
  previewLayoutTick.value += 1
})

watch(
  () => result.value?.docxFile,
  () => {
    previewHintsOpen.value = false
    generateHintsDismissed.value = false
  },
)

onBeforeUnmount(() => {
  window.clearInterval(parseClock)
  window.removeEventListener('keydown', onPreviewFullscreenKey, true)
  setPreviewFullscreen(false)
})

type TenderDefaultsLoose = BidBrief & {
  qualification?: QualificationStatus
  slots?: SlotStatus[]
  chapter5Template?: Chapter5TemplateStatus
  docPreview?: string
  fixedTemplates?: FixedTemplate[]
  structureModes?: string[]
}

function assignQuoteFile(file: File) {
  const name = file.name.trim().toLowerCase()
  const ok = ['.xlsx', '.xlsm', '.docx', '.csv', '.pdf'].some((ext) => name.endsWith(ext))
  if (!ok) {
    error.value = '报价清单支持 Excel、Word、CSV 或 PDF'
    if (quoteInput.value) quoteInput.value.value = ''
    return
  }
  error.value = ''
  if (file !== quoteFile.value) {
    hasParsed.value = false
  }
  quoteFile.value = file
  quoteRecordIds.value = []
  quoteRecordItems.value = []
}

function onPickQuote(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) assignQuoteFile(file)
}

function onQuoteDrop(ev: DragEvent) {
  dragOverQuote.value = false
  const file = ev.dataTransfer?.files?.[0]
  if (file) assignQuoteFile(file)
}

const quoteSourceLabel = computed(() => {
  if (quoteFile.value) return quoteFile.value.name
  if (quoteRecordItems.value.length === 1) {
    return `报价记录：${quoteRecordItems.value[0]!.projectName || '未命名'}`
  }
  if (quoteRecordItems.value.length > 1) {
    return `已选 ${quoteRecordItems.value.length} 条报价记录`
  }
  return ''
})

const hasQuoteSource = computed(
  () => Boolean(quoteFile.value) || quoteRecordIds.value.length > 0,
)

const quotePanelTitle = computed(() => {
  if (form.quoteLines.some((row) => String(row.name || '').trim())) return '报价清单（已识别）'
  if (hasQuoteSource.value) return '报价清单（待识别）'
  return '报价清单（待上传）'
})

const quotePanelReady = computed(() =>
  form.quoteLines.some((row) => String(row.name || '').trim()),
)

async function openQuotePicker() {
  quotePickerOpen.value = true
  quotePickerDraft.value = [...quoteRecordIds.value]
  quotePickerQuery.value = ''
  await loadQuotePicker()
}

async function loadQuotePicker() {
  quotePickerLoading.value = true
  try {
    const data = await fetchQuoteRecords({
      q: quotePickerQuery.value.trim() || undefined,
      purpose: 'quote',
      status: 'locked',
      limit: 50,
    })
    quotePickerRows.value = data.items || []
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '加载报价记录失败'
  } finally {
    quotePickerLoading.value = false
  }
}

function toggleQuotePickerId(id: string) {
  const cur = quotePickerDraft.value
  quotePickerDraft.value = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]
}

function confirmQuotePicker() {
  const picked = quotePickerRows.value.filter((r) => quotePickerDraft.value.includes(r.id))
  // 勾选项可能不在当前页搜索结果里，保留已选但未出现在本页的项
  const keep = quoteRecordItems.value.filter(
    (r) => quotePickerDraft.value.includes(r.id) && !picked.some((p) => p.id === r.id),
  )
  quoteRecordItems.value = [...keep, ...picked]
  quoteRecordIds.value = [...quotePickerDraft.value]
  if (quoteRecordIds.value.length) {
    quoteFile.value = null
    if (quoteInput.value) quoteInput.value.value = ''
    hasParsed.value = false
  }
  quotePickerOpen.value = false
}

function clearQuoteSource() {
  quoteFile.value = null
  quoteRecordIds.value = []
  quoteRecordItems.value = []
  if (quoteInput.value) quoteInput.value.value = ''
  hasParsed.value = false
}

function formatQuotePickTime(ms: number) {
  if (!ms) return '—'
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function reparseQuoteFromForm() {
  if (!inviteFile.value) {
    error.value = '邀请书文件已失效，请返回上一步重新上传邀请书与报价清单'
    return
  }
  if (!hasQuoteSource.value) {
    error.value = '请先上传报价清单，或从 AI 报价记录勾选'
    return
  }
  error.value = ''
  await onParseInvitation()
  if (!form.quoteLines.some((row) => String(row.name || '').trim())) {
    error.value =
      error.value ||
      '未能解析出报价行。请确认 Excel 含名称/数量列，或换一条报价记录后重试'
  }
}

function toggleKb(id: string) {
  selectedKbIds.value = selectedKbIds.value.includes(id)
    ? selectedKbIds.value.filter((x) => x !== id)
    : [...selectedKbIds.value, id]
}

function addDeviationRow() {
  form.deviationLines.push({
    ...emptyDeviationLine(),
    seq: String(form.deviationLines.length + 1),
  })
}

function removeDeviationRow(index: number) {
  form.deviationLines.splice(index, 1)
}

const techChapterItems = computed(() =>
  form.outlineItems.filter((item) => !item.skipped && isTechChapterKind(item.kind)),
)

function isOrgChapter(item: { title?: string }) {
  return /施工组织/.test(item.title || '')
}

const boqInput = ref<HTMLInputElement | null>(null)
const boqTargetId = ref('')
const boqBusyId = ref('')
const drawingPdfInput = ref<HTMLInputElement | null>(null)
const drawingFill = ref('')
const drawingBusy = ref(false)
const dragOverBoqId = ref('')

function pickBoq(item: { id?: string }) {
  boqTargetId.value = item.id || ''
  boqInput.value?.click()
}

async function onBoqFile(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  const id = boqTargetId.value
  input.value = ''
  if (!file || !id) return
  const item = form.outlineItems.find((row) => row.id === id)
  if (!item) return
  error.value = ''
  boqBusyId.value = id
  try {
    const data = await fillTechBoq(file)
    item.draftBody = data.draftBody || ''
    item.draftStatus = 'drafted'
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '工程量清单识别失败'
  } finally {
    boqBusyId.value = ''
  }
}

async function onBoqDrop(item: { id?: string }, ev: DragEvent) {
  dragOverBoqId.value = ''
  const file = ev.dataTransfer?.files?.[0]
  if (!file || !item.id) return
  const row = form.outlineItems.find((it) => it.id === item.id)
  if (!row) return
  error.value = ''
  boqBusyId.value = item.id
  try {
    const data = await fillTechBoq(file)
    row.draftBody = data.draftBody || ''
    row.draftStatus = 'drafted'
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '工程量清单识别失败'
  } finally {
    boqBusyId.value = ''
  }
}

function pickDrawingPdf(replace: boolean) {
  if (!String(form.invitationId || '').trim()) {
    error.value = '请先识别邀请书，再上传图纸'
    return
  }
  pendingSlotReplace.value = replace
  drawingPdfInput.value?.click()
}

async function takeDrawingPdf(file: File, replace: boolean) {
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    error.value = '图纸只支持 PDF'
    return
  }
  const invitationId = String(form.invitationId || '').trim()
  if (!invitationId) {
    error.value = '请先识别邀请书，再上传图纸'
    return
  }
  error.value = ''
  drawingBusy.value = true
  try {
    const data = await fillTechDrawing(file, invitationId, replace)
    drawingFill.value = data.draftBody || ''
    if (data.slot) {
      techDrawingSlot.value = {
        ...techDrawingSlot.value,
        ...data.slot,
        files: data.slot.files || [],
      }
      mergeSlotStatus(techDrawingSlot.value)
    }
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '图纸识别失败'
  } finally {
    drawingBusy.value = false
  }
}

async function onDrawingPdfFile(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  const replace = pendingSlotReplace.value
  input.value = ''
  if (!file) return
  await takeDrawingPdf(file, replace)
}

function onDrawingPdfDrop(ev: DragEvent) {
  dragOverDrawings.value = false
  const file = ev.dataTransfer?.files?.[0]
  if (!file) return
  void takeDrawingPdf(file, !techDrawingSlot.value.fileCount)
}

const openTechExcerpt = ref<string>('')

function applyDraftBrief(brief: BidBrief) {
  if (!Array.isArray(brief.outlineItems)) brief.outlineItems = []
  if (!Array.isArray(brief.deviationLines)) brief.deviationLines = []
  form.outlineItems = brief.outlineItems
  form.deviationLines = brief.deviationLines
  if (brief.techPlanNote) form.techPlanNote = brief.techPlanNote
}

async function onDraftTech(itemId?: string) {
  error.value = ''
  if (itemId) redraftingId.value = itemId
  else draftingTech.value = true
  try {
    const data = await draftTenderTech(
      {
        ...form,
        outlineItems: pruneUnknownOutline(form.outlineItems),
        generateVolume: 'technical',
      },
      { kbIds: selectedKbIds.value, itemId, promptId: selectedPromptId.value || undefined },
    )
    applyDraftBrief(data.brief)
    if (data.notes?.length) parseNotes.value = [...parseNotes.value, ...data.notes]
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '起草失败'
  } finally {
    draftingTech.value = false
    redraftingId.value = ''
  }
}

function compactDev(text: string) {
  return (text || '').replace(/\s+/g, '')
}

function matchQuoteFact(requirement: string) {
  const req = compactDev(requirement)
  if (!req) return ''
  for (const line of form.quoteLines) {
    const name = String(line.name || '').trim()
    const spec = String(line.spec || '').trim()
    const fact = [name, spec].filter(Boolean).join(' ')
    const blob = compactDev(fact)
    if (blob.length < 2) continue
    if (blob.includes(req) || req.includes(blob)) return fact
    for (let i = 0; i <= Math.max(0, req.length - 4); i++) {
      if (blob.includes(req.slice(i, i + 4))) return fact
    }
  }
  return ''
}

function fillDeviationFromQuote(opts?: { silent?: boolean }) {
  const rows = form.deviationLines
  if (!rows.some((row) => String(row.requirement || '').trim())) {
    if (!opts?.silent) error.value = '请先识别邀请书技术条款，不要用报价清单当招标要求'
    return
  }
  if (!form.quoteLines.some((row) => String(row.name || row.spec || '').trim())) {
    if (!opts?.silent) error.value = '请先识别报价清单，再对照填写'
    return
  }
  form.deviationLines = rows.map((row) => {
    const req = String(row.requirement || '').trim()
    if (!req) return row
    const resp = String(row.response || '').trim()
    const pending = !resp || resp === req || resp.startsWith('【')
    const hit = matchQuoteFact(req)
    const next = { ...row }
    if (hit && pending) next.response = `响应：${hit}`
    else if (pending && !resp) next.response = '【待响应】'
    const dev = String(next.deviation || '').trim()
    if (!dev || (pending && (dev === '无偏差' || !hit))) next.deviation = '待确认'
    return next
  })
}

const quoteSpecFilledCount = computed(
  () => form.quoteLines.filter((r) => String(r.spec || '').trim()).length,
)

function addPerformanceRow() {
  form.performanceLines.push({ ...emptyPerformanceLine() })
}

function removePerformanceRow(index: number) {
  form.performanceLines.splice(index, 1)
}

function isPerfPlaceholder(row: PerformanceLine) {
  return String(row.projectName || '').trim().startsWith('待补充类似业绩')
}

function isAutoPerfPlaceholder(row: PerformanceLine) {
  if (!isPerfPlaceholder(row) || row.includeInBid === false) return false
  if (
    String(row.client || '').trim() ||
    String(row.spec || '').trim() ||
    String(row.contact || '').trim() ||
    String(row.location || '').trim()
  ) {
    return false
  }
  if (Number(row.amountYuan) > 0) return false
  const summary = String(row.summary || '').trim()
  if (summary && summary !== '待补充') return false
  const note = String(row.note || '').trim()
  if (note && !note.includes('资料库未匹配')) return false
  return true
}

function padPerformancePlaceholders() {
  if (!Array.isArray(form.performanceLines)) form.performanceLines = []
  const req = form.performanceRequirement
  const kept = form.performanceLines.filter((row) => !isAutoPerfPlaceholder(row))
  if (!performanceRequirementActive(req)) {
    form.performanceLines = kept
    return
  }
  const need = Number(req.minCount) || 0
  let passed = 0
  let held = 0
  for (const row of kept) {
    if (isPerfPlaceholder(row)) {
      if (row.includeInBid !== false) held += 1
      continue
    }
    const name = String(row.projectName || '').trim()
    if (!name || isWeakPerfTitle(name) || row.includeInBid === false) continue
    if (matchPerformanceLine(row, req).passed) passed += 1
  }
  let gap = 0
  if (need > 0) gap = Math.max(0, need - passed - held)
  else if (passed === 0 && held === 0) gap = 1
  const label = formatPerformanceRequirement(req)
  const used = new Set(kept.map((row) => String(row.projectName || '').trim()))
  let n = 1
  for (let i = 0; i < gap; i++) {
    let name = ''
    while (!name || used.has(name)) {
      name = `待补充类似业绩${n}`
      n += 1
    }
    let note = '资料库未匹配到符合门槛的合同，生成时先占位，可手工补。'
    if (label) note = `招标要求：${label}。${note}`
    kept.push({
      ...emptyPerformanceLine(),
      projectName: name,
      summary: '待补充',
      note,
      includeInBid: true,
    })
    used.add(name)
  }
  form.performanceLines = kept
}

function ensurePerformanceRequirement() {
  if (!Array.isArray(form.performanceLines)) form.performanceLines = []
  for (const row of form.performanceLines) {
    if (row.includeInBid == null) row.includeInBid = true
  }
  if (!form.performanceRequirement || typeof form.performanceRequirement !== 'object') {
    form.performanceRequirement = emptyPerformanceRequirement()
  } else if (!Array.isArray(form.performanceRequirement.keywords)) {
    form.performanceRequirement.keywords = []
  }
  padPerformancePlaceholders()
}

function performanceRowMatch(row: PerformanceLine) {
  return matchPerformanceLine(row, form.performanceRequirement)
}

const performanceReqLabel = computed(() => formatPerformanceRequirement(form.performanceRequirement))

const performanceMatchPassed = computed(
  () =>
    form.performanceLines.filter(
      (row) =>
        row.includeInBid !== false &&
        !isPerfPlaceholder(row) &&
        matchPerformanceLine(row, form.performanceRequirement).passed,
    ).length,
)

const performancePlaceholderCount = computed(
  () => form.performanceLines.filter((row) => row.includeInBid !== false && isPerfPlaceholder(row)).length,
)

const performanceSelectedCount = computed(
  () => form.performanceLines.filter((row) => row.includeInBid !== false && String(row.projectName || '').trim()).length,
)

const performanceMatchSummary = computed(() => {
  const total = form.performanceLines.length
  const ph = performancePlaceholderCount.value
  if (!total) return '尚未填写。邀请书写了业绩要求时，没对上的会先占位'
  if (!performanceRequirementActive(form.performanceRequirement)) {
    return `${total} 条 · 未抽出本标业绩门槛 · 写入本标 ${performanceSelectedCount.value}`
  }
  const bits = [
    `${total} 条`,
    `资料库符合 ${performanceMatchPassed.value}`,
    `写入本标 ${performanceSelectedCount.value}`,
  ]
  if (ph) bits.push(`未匹配 ${ph} 条已占位`)
  const need = form.performanceRequirement.minCount
  if (need > 0) bits.push(`招标要求至少 ${need} 个`)
  return bits.join(' · ')
})

const quoteQtySum = computed(() =>
  form.quoteLines.reduce((s, r) => s + (Number(r.qty) || 0), 0),
)
const quoteAmountSum = computed(() =>
  form.quoteLines.reduce((s, r) => s + (Number(r.amount) || 0), 0),
)

const DEFAULT_QUOTE_HEADERS = ['序', '设备', '技术参数 / 明细', '单位', '数量', '不含税单价', '合价']
const DEFAULT_QUOTE_ROLES = ['seq', 'name', 'spec', 'unit', 'qty', 'price', 'amount']
const DEFAULT_DEV_HEADERS = ['序', '招标文件要求', '投标文件响应', '偏差']
const DEFAULT_DEV_ROLES = ['seq', 'requirement', 'response', 'deviation']

function compactHeaderLabel(text: string) {
  return String(text || '').replace(/[\s/／（）()【】\[\]:：]/g, '')
}

function inferQuoteRole(header: string): string {
  const n = compactHeaderLabel(header)
  if (!n) return 'extra'
  if (n === '序' || n.startsWith('序号') || n === '编号') return 'seq'
  if (/功能模块|模块名称|^模块$/.test(n)) return 'name'
  if (n.includes('子系统')) return 'group'
  if (n === '系统' || n.endsWith('系统名称') || (n.endsWith('系统') && !n.includes('系数'))) return 'group'
  if (/技术参数|技术要求|参数要求|规格型号|规格参数|特征描述|工作内容/.test(n)) return 'spec'
  if (n === '规格' || n === '参数' || n.endsWith('明细')) return 'spec'
  if (n === '单位' || n === '计量单位') return 'unit'
  if (n.includes('数量') || n.includes('工程量')) return 'qty'
  if (n.includes('单价')) return 'price'
  if (n.includes('合价') || n.endsWith('金额')) return 'amount'
  if (/设备名称|项目名称|货物名称|物料名称|品名|服务内容/.test(n)) return 'name'
  if (['设备', '名称', '项目', '货物', '物料'].includes(n)) return 'name'
  return 'extra'
}

function inferDevRole(header: string): string {
  const n = compactHeaderLabel(header)
  if (!n) return 'extra'
  if (n === '序' || n.startsWith('序号')) return 'seq'
  if (/偏差说明|偏离说明|偏离情况|偏差情况|^偏差$|^偏离$/.test(n) || n.endsWith('偏差') || n.endsWith('偏离')) {
    return 'deviation'
  }
  if (/投标文件响应|投标响应|响应内容|响应情况|供应商响应/.test(n) || n.includes('响应')) return 'response'
  if (/招标文件要求|招标要求|招标文件规定|条款内容|商务条款|技术要求|规范要求|采购要求/.test(n) || n.includes('要求') || n.includes('规定') || n.includes('条款')) {
    return 'requirement'
  }
  return 'extra'
}

const quotePreviewHeaders = computed(() => {
  const headers = (form.quoteHeaders || []).map((h) => String(h || '').trim()).filter(Boolean)
  return headers.length ? headers : DEFAULT_QUOTE_HEADERS
})

const quotePreviewRoles = computed(() => {
  const headers = quotePreviewHeaders.value
  if (form.quoteRoles?.length === headers.length) return form.quoteRoles
  if (!form.quoteHeaders?.length) return DEFAULT_QUOTE_ROLES
  return headers.map((h) => inferQuoteRole(h))
})

const quoteHasSpecColumn = computed(() => quotePreviewRoles.value.includes('spec'))

const deviationPreviewHeaders = computed(() => {
  const tech = (form.techDevHeaders || []).map((h) => String(h || '').trim()).filter(Boolean)
  if (tech.length) return tech
  const biz = (form.bizDevHeaders || []).map((h) => String(h || '').trim()).filter(Boolean)
  if (biz.length) return biz
  return DEFAULT_DEV_HEADERS
})

const deviationPreviewRoles = computed(() => {
  const headers = deviationPreviewHeaders.value
  if (!form.techDevHeaders?.length && !form.bizDevHeaders?.length) return DEFAULT_DEV_ROLES
  return headers.map((h) => inferDevRole(h))
})

function quotePreviewValue(row: QuoteLineIn, index: number) {
  const role = quotePreviewRoles.value[index] || 'extra'
  if (role === 'seq') return row.seq || ''
  if (role === 'group') {
    const gi = quotePreviewRoles.value.slice(0, index).filter((r) => r === 'group').length
    return String((row.groups || [])[gi] || '')
  }
  if (role === 'name') return row.name || ''
  if (role === 'spec') return row.spec || ''
  if (role === 'unit') return row.unit || ''
  if (role === 'qty') return String(row.qty || 0)
  if (role === 'price') {
    return Number(row.unitPrice || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  }
  if (role === 'amount') {
    return Number(row.amount || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  }
  return ''
}

function deviationFieldForRole(role: string): 'seq' | 'requirement' | 'response' | 'deviation' | '' {
  if (role === 'seq' || role === 'requirement' || role === 'response' || role === 'deviation') return role
  return ''
}

async function onParseInvitation() {
  if (!inviteFile.value) {
    error.value = '请先选择投标邀请书或招标文件'
    return
  }
  error.value = ''
  parsing.value = true
  parseElapsed.value = 0
  window.clearInterval(parseClock)
  parseClock = window.setInterval(() => {
    parseElapsed.value += 1
  }, 1000)
  try {
    const data = await parseTenderInvitation(inviteFile.value, {
      kbIds: selectedKbIds.value,
      current: { ...form },
      quoteFile: quoteFile.value,
      quoteRecordIds: quoteRecordIds.value,
    })
    mergeBriefIntoForm(data.brief)
    form.authEnabled = false
    agentHint.value = data.agentHint || {}
    syncAuthOutline(false)
    form.techPlanNote = hydrateTechPlanNote(form)
    if (!Array.isArray(form.extraPlaceholders)) form.extraPlaceholders = []
    if (!Array.isArray(form.requiredSlotKeys)) form.requiredSlotKeys = data.requiredSlotKeys || []
    if (!Array.isArray(form.includeSlotKeys)) form.includeSlotKeys = data.includeSlotKeys || []
    if (!Array.isArray(form.quoteLines)) form.quoteLines = []
    if (!Array.isArray(form.quoteHeaders)) form.quoteHeaders = []
    if (!Array.isArray(form.quoteRoles)) form.quoteRoles = []
    if (!Array.isArray(form.deviationLines)) form.deviationLines = []
    if (!Array.isArray(form.bizDevHeaders)) form.bizDevHeaders = []
    if (!Array.isArray(form.techDevHeaders)) form.techDevHeaders = []
    if (!Array.isArray(form.performanceLines)) form.performanceLines = []
    if (!Array.isArray(form.outlineItems)) form.outlineItems = []
    if (!Array.isArray(form.bidRequirements)) form.bidRequirements = []
    if (!form.layoutMode) form.layoutMode = 'chapter5'
    if (!form.structureMode) form.structureMode = form.layoutMode === 'outline' ? 'outline' : 'fixed'
    form.formatConfirmed = false
    formatPreviewFile.value = ''
    formatPreviewOnly.value = false
    formatPreviewMessage.value = ''
    formatPreviewOpen.value = false
    ensureDocumentFormat()
    ensurePerformanceRequirement()
    parseNotes.value = data.notes || []
    parseNotesEpoch.value += 1
    parseExtraOpen.value = false
    parseMatchListOpen.value = false
    vetoListOpen.value = false
    attachmentMatch.value = data.attachmentMatch || null
    parseOcr.value = {
      pageCount: data.pageCount || 0,
      ocrPages: data.ocrPages || 0,
      ocrCapped: Boolean(data.ocrCapped),
      ocrMaxPages: data.ocrMaxPages || 0,
    }
    if (data.catalogSlots?.length) catalogSlots.value = data.catalogSlots
    if (data.slots?.length) slotStatuses.value = data.slots
    await refreshLibrary()
    refreshAttachmentMatch()
    hasParsed.value = true
    followAdvice.value = generationMode.value === null
    syncFullDrafts()
    volumeHint.value = ''
    techDrawingSlot.value = blankTechDrawings()
    drawingFill.value = ''
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '识别邀请书失败'
  } finally {
    window.clearInterval(parseClock)
    parsing.value = false
  }
}

function parseWaitLabel() {
  const n = parseElapsed.value
  const m = Math.floor(n / 60)
  const s = n % 60
  if (m <= 0) return `${s} 秒`
  return `${m} 分 ${s} 秒`
}

function pickSlotFile(slot: SlotStatus, replace: boolean) {
  if (!slot.key) {
    error.value = '该项缺少 key，无法上传'
    return
  }
  if (slot.key === TECH_DRAWING_KEY && !String(form.invitationId || '').trim()) {
    error.value = '请先识别邀请书，再上传本项目图纸'
    return
  }
  pendingSlotKey.value = slot.key
  pendingSlotReplace.value = replace
  slotFileInput.value?.click()
}

async function onSlotFileChange(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = Array.from(input.files || [])
  const key = pendingSlotKey.value
  const replace = pendingSlotReplace.value
  input.value = ''
  pendingSlotKey.value = null
  if (!files.length || !key) return
  error.value = ''
  uploadingKey.value = key
  try {
    let last: SlotStatus | null = null
    for (let i = 0; i < files.length; i++) {
      const data = await uploadTenderSlot(key, files[i], {
        replace: replace && i === 0,
        invitationId: key === TECH_DRAWING_KEY ? String(form.invitationId || '').trim() : undefined,
      })
      last = {
        key,
        title: data.title || slotStatuses.value.find((s) => s.key === key)?.title || key,
        hint: data.hint || slotStatuses.value.find((s) => s.key === key)?.hint || '',
        fileCount: data.fileCount,
        files: data.files || [],
      }
    }
    if (last) mergeSlotStatus(last)
    if (key === TECH_DRAWING_KEY && last) {
      techDrawingSlot.value = { ...techDrawingSlot.value, ...last }
    }
    if (key === 'perf' && last?.files?.length) absorbLibraryPerformance(last.files)
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '上传失败'
  } finally {
    uploadingKey.value = null
  }
}

function onDrawingsDrop(ev: DragEvent) {
  ev.preventDefault()
  dragOverDrawings.value = false
  const files = Array.from(ev.dataTransfer?.files || [])
  if (!files.length) return
  const invitationId = String(form.invitationId || '').trim()
  if (!invitationId) {
    error.value = '请先识别邀请书，再上传本项目图纸'
    return
  }
  void (async () => {
    pendingSlotKey.value = TECH_DRAWING_KEY
    pendingSlotReplace.value = !techDrawingSlot.value.fileCount
    error.value = ''
    uploadingKey.value = TECH_DRAWING_KEY
    try {
      let last: SlotStatus | null = null
      for (let i = 0; i < files.length; i++) {
        const data = await uploadTenderSlot(TECH_DRAWING_KEY, files[i], {
          replace: !techDrawingSlot.value.fileCount && i === 0,
          invitationId,
        })
        last = {
          key: TECH_DRAWING_KEY,
          title: data.title || techDrawingSlot.value.title,
          hint: data.hint || techDrawingSlot.value.hint,
          fileCount: data.fileCount,
          files: data.files || [],
        }
      }
      if (last) mergeSlotStatus(last)
    } catch (e) {
      error.value = e instanceof ApiError || e instanceof Error ? e.message : '上传失败'
    } finally {
      uploadingKey.value = null
      pendingSlotKey.value = null
    }
  })()
}

async function onClearSlot(slot: SlotStatus) {
  if (!slot.key || !slot.fileCount) return
  error.value = ''
  uploadingKey.value = slot.key
  try {
    await clearTenderSlot(slot.key, {
      invitationId:
        slot.key === TECH_DRAWING_KEY ? String(form.invitationId || '').trim() : undefined,
    })
    mergeSlotStatus({
      key: slot.key,
      title: slot.title,
      hint: slot.hint,
      fileCount: 0,
      files: [],
    })
    if (slot.key === TECH_DRAWING_KEY) drawingFill.value = ''
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '清除失败'
  } finally {
    uploadingKey.value = null
  }
}

function slotFileLabel(slot: SlotStatus): string {
  if (!slot.fileCount) return ''
  const first = slot.files[0]
  const label = first?.performance?.projectName || first?.name || '已上传'
  if (slot.files.length === 1) return label
  return `${label} 等 ${slot.fileCount} 个文件`
}

async function onUploadStepNext() {
  if (!inviteFile.value) {
    error.value = '请先上传投标邀请书或招标文件'
    return
  }
  error.value = ''
  // 已识别过但仍无报价行、或刚换了清单时，必须再跑一遍解析
  const hasQuoteRows = form.quoteLines.some((row) => String(row.name || '').trim())
  const needParse =
    !hasParsed.value || (hasQuoteSource.value && !hasQuoteRows)
  if (needParse) {
    await onParseInvitation()
    if (!hasParsed.value) return
  }
  advanceStep(1)
  await nextTick()
  document.getElementById('tender-parse-notes')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function withDrawingNote(items: typeof form.outlineItems) {
  const note = drawingFill.value.trim()
  if ((effectiveGenerationMode.value === 'single' && activeVolume.value !== 'technical') || !note) return items
  if (items.some((item) => (item.draftBody || '').includes(note.slice(0, 40)))) return items
  return [
    ...items,
    {
      id: 'drawing-fill',
      title: '图纸说明',
      kind: 'tech_chapter',
      source: 'generate',
      required: false,
      skipped: false,
      body: '',
      inviteExcerpt: '',
      draftBody: note,
      draftStatus: 'drafted',
      level: 1,
      rawTitle: '',
    },
  ]
}

async function doGenerate() {
  error.value = ''
  if (workflowLocked.value) {
    error.value = '当前任务已进入审批或已定稿，仅可预览'
    return
  }
  // 轨道B：按本标生成前必须先确认大纲，避免抽取错了照样出稿
  if (form.structureMode === 'outline' && !form.requirementsDriven && !form.formatConfirmed) {
    error.value = '按本标生成前请先预览并确认使用这份模板'
    return
  }
  generating.value = true
  try {
    syncFullDrafts()
    const payload: BidBrief = {
      ...form,
      includeCommitment: true,
      includePlaceholders: false,
      requiredSlotKeys: effectiveRequiredKeys.value,
      includeSlotKeys: effectiveIncludeKeys.value,
      outlineItems: withDrawingNote(pruneUnknownOutline(form.outlineItems)),
      techPlanKbIds: form.autoDraftTechPlan ? selectedKbIds.value : (form.techPlanKbIds || []),
      bizRespKbIds: form.autoDraftBizResp ? selectedKbIds.value : (form.bizRespKbIds || []),
      generateVolume: effectiveGenerationMode.value === 'full' ? 'full' : activeVolume.value,
      generateBoth: effectiveGenerationMode.value === 'split' || pendingBoth.value,
      splitSlice: effectiveGenerationMode.value === 'split' || pendingBoth.value,
      structureMode: hasOriginalFormat() ? 'outline' : form.structureMode,
      requirementsDriven: hasOriginalFormat() ? false : form.requirementsDriven,
      layoutMode: generationLayoutMode(),
      contentTemplateId: form.contentTemplateId,
    }
    if (result.value?.id) {
      result.value = await regenerateTenderRecord(result.value.id, payload)
    } else {
      result.value = await generateTender(payload)
    }
    if (result.value.attachmentMatch) {
      attachmentMatch.value = result.value.attachmentMatch
    }
    previewVolume.value = payload.generateVolume === 'technical' ? 'technical' : 'business'
    void loadRecords()
    advanceStep(2)
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '生成预览失败'
  } finally {
    pendingBoth.value = false
    generating.value = false
  }
}

const qaVolume = computed(() => result.value?.generateVolume === 'full' ? 'full' : previewVolume.value)

async function loadSavedQa(recordId: string) {
  try {
    const data = await fetchTenderQa(recordId, { volume: qaVolume.value })
    qaReport.value = data.report || null
  } catch {
    qaReport.value = null
  }
}

async function runTenderQa() {
  const id = result.value?.id
  if (!id) {
    qaError.value = '请先生成投标文件'
    return
  }
  qaLoading.value = true
  qaError.value = ''
  try {
    qaReport.value = await inspectTenderQa(id, { volume: qaVolume.value })
  } catch (e) {
    qaError.value = e instanceof ApiError || e instanceof Error ? e.message : '自检失败'
  } finally {
    qaLoading.value = false
  }
}

watch(
  () =>
    [
      result.value?.id,
      result.value?.docxFile,
      result.value?.techDocxFile,
      qaVolume.value,
    ] as const,
  ([id]) => {
    qaError.value = ''
    qaReport.value = null
    if (!id) return
    void loadSavedQa(id)
  },
)

watch(currentStepIndex, (index) => {
  if (index !== 3) return
  if (!result.value?.id || qaLoading.value || qaReport.value) return
  void runTenderQa()
})

async function onGenerateCurrent() {
  pendingBoth.value = false
  await onFormStepNext()
}

async function onGenerateBoth() {
  generationMode.value = 'split'
  followAdvice.value = false
  pendingBoth.value = true
  await onFormStepNext()
}

async function onFormStepNext() {
  if (generateBlockers.value.length) {
    pendingBoth.value = false
    error.value = `请先完善：${generateBlockers.value.join('、')}`
    return
  }
  if (workflowLocked.value) {
    pendingBoth.value = false
    error.value = '当前任务已进入审批或已定稿，仅可预览'
    return
  }
  if (bidDateOffToday()) {
    confirmDateOpen.value = true
    return
  }
  await continueGenerateAfterChecks()
}

function onConfirmDateOpen(open: boolean) {
  if (!open && !generating.value) {
    confirmDateOpen.value = false
    pendingBoth.value = false
  }
}

async function confirmBidDate() {
  confirmDateOpen.value = false
  await continueGenerateAfterChecks()
}

function askRegenerateRecord(item: TenderRecordItem) {
  if (isTenderWorkflowLocked(item.status, item.workflowLocked)) {
    error.value = '该任务已进入审批或已定稿，不能重新生成'
    return
  }
  confirmGenerateMode.value = 'record'
  pendingRegenItem.value = item
  confirmGenerateOpen.value = true
}

function onConfirmGenerateOpen(open: boolean) {
  if (!open && !generating.value) {
    confirmGenerateOpen.value = false
    pendingRegenItem.value = null
    pendingBoth.value = false
  }
}

async function confirmGenerateAgain() {
  confirmGenerateOpen.value = false
  if (confirmGenerateMode.value === 'record' && pendingRegenItem.value) {
    const item = pendingRegenItem.value
    pendingRegenItem.value = null
    await regenerateRecord(item)
    return
  }
  await doGenerate()
}

async function flushBidEdit(): Promise<boolean> {
  if (bidSaveBusy.value) {
    error.value = '正在保存，请稍候'
    return false
  }
  if (!bidEditorRef.value) return true
  bidSaveBusy.value = true
  error.value = ''
  try {
    await bidEditorRef.value.flushSave()
    return true
  } catch (e) {
    error.value = e instanceof Error ? e.message : '文稿保存失败'
    return false
  } finally {
    bidSaveBusy.value = false
  }
}

async function switchPreviewVolume(vol: BidVolume) {
  if (vol === previewVolume.value) return
  if (!(await flushBidEdit())) return
  previewVolume.value = vol
}

async function onPreviewStepNext() {
  if (!(await flushBidEdit())) return
  setPreviewFullscreen(false)
  advanceStep(3)
}

async function leavePreviewToTasks() {
  if (!(await flushBidEdit())) return
  setPreviewFullscreen(false)
  await router.push('/work-tasks')
}

const reachedDownload = computed(() => maxStepReached.value >= 4)

const wizardDirty = computed(
  () =>
    currentStepIndex.value > 0 ||
    hasParsed.value ||
    !!inviteFile.value ||
    !!quoteFile.value ||
    quoteRecordIds.value.length > 0 ||
    !!result.value,
)

const canResetWizard = computed(
  () =>
    !reachedDownload.value &&
    wizardDirty.value &&
    !resetting.value &&
    !parsing.value &&
    !generating.value &&
    !publishing.value,
)

function askResetWizard() {
  if (!canResetWizard.value) return
  confirmResetOpen.value = true
}

function onConfirmResetOpen(open: boolean) {
  if (!open && !resetting.value) confirmResetOpen.value = false
}

function applyWizardReset() {
  if (formBaseline) applyBriefToForm(formBaseline)
  else applyBriefToForm(emptyBrief())
  inviteFile.value = null
  quoteFile.value = null
  quoteRecordIds.value = []
  quoteRecordItems.value = []
  parseNotes.value = []
  parseOcr.value = null
  attachmentMatch.value = null
  result.value = null
  knownFiles.value = { docx: false, tech: false }
  qaReport.value = null
  qaError.value = ''
  hasParsed.value = false
  generationMode.value = null
  followAdvice.value = false
  delete draftPreference.business
  delete draftPreference.technical
  volumeHint.value = ''
  success.value = ''
  techDrawingSlot.value = blankTechDrawings()
  drawingFill.value = ''
  clearFormatPreview()
  setPreviewFullscreen(false)
  showRecords.value = false
  currentStepIndex.value = 0
  maxStepReached.value = 0
  if (fileInput.value) fileInput.value.value = ''
  if (quoteInput.value) quoteInput.value.value = ''
}

async function confirmResetWizard() {
  if (reachedDownload.value) {
    confirmResetOpen.value = false
    return
  }
  resetting.value = true
  error.value = ''
  const draftId = result.value?.status === 'draft' ? result.value.id || '' : ''
  try {
    if (draftId) await deleteTenderRecord(draftId)
    applyWizardReset()
    confirmResetOpen.value = false
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '重置失败'
  } finally {
    resetting.value = false
  }
}

async function onQaStepNext() {
  const id = result.value?.id
  const status = result.value?.status || ''
  if (id && status === 'draft') {
    publishing.value = true
    error.value = ''
    try {
      const published = await publishTenderRecord(id)
      if (result.value && result.value.id === id) {
        result.value = { ...result.value, ...published }
      }
    } catch (e) {
      error.value = e instanceof ApiError || e instanceof Error ? e.message : '写入生成记录失败'
      return
    } finally {
      publishing.value = false
    }
  }
  advanceStep(4)
  void loadRecords()
}

async function onStepBack() {
  if (currentStepIndex.value <= 0) return
  if (currentStepIndex.value === 2) {
    if (!(await flushBidEdit())) return
    setPreviewFullscreen(false)
  }
  currentStepIndex.value -= 1
  error.value = ''
}

async function onDownload(kind: 'docx' | 'tech' | 'pdf') {
  const r = result.value
  if (!r) {
    error.value = '请先生成投标文件后再下载'
    return
  }
  if (downloading.value) {
    error.value = bidSaveBusy.value ? '正在保存，请稍候' : '正在下载，请稍候'
    return
  }
  downloading.value = kind
  error.value = ''
  try {
    if (currentStepIndex.value === 2 && !(await flushBidEdit())) return
    const fullMode = r.generateVolume === 'full' || effectiveGenerationMode.value === 'full'
    const picked =
      kind === 'tech'
        ? volumeDocx(r, 'technical')
        : kind === 'docx'
          ? fullMode
            ? volumeDocx(r, 'full')
            : volumeDocx(r, 'business')
          : { file: r.pdfFile || '', name: r.pdfDownloadName || '' }
    if (!picked.file.trim() || !picked.name.trim()) {
      error.value = '下载文件或文件名缺失，请重新生成投标文件后再试'
      return
    }
    error.value = ''
    await downloadTenderFile(picked.file, picked.name)
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '下载失败'
  } finally {
    downloading.value = null
  }
}

</script>

<template>
  <div
    class="flex w-full flex-1 flex-col"
    :class="currentStepIndex === 2 ? 'h-full min-h-0' : ''"
  >
  <PageHeader
    class="tender-page-header"
    :class="{ 'tender-page-header--preview': currentStepIndex === 2 }"
    title="AI标书生成"
    :description="currentStepIndex === 2 ? '' : '缺扫描件仍可生成：Word 附件区用虚线框占位，补齐后重新生成即可。公司名称等基本信息仍需先填。未上传报价清单时不填写报价分项。'"
  >
    <template #badges>
      <div
        class="tender-volume-switch"
        role="tablist"
        aria-label="填写商务标、技术标或综合标"
      >
        <button
          type="button"
          role="tab"
          :aria-selected="effectiveGenerationMode !== 'full' && activeVolume === 'business'"
          class="tender-volume-btn"
          :class="{ 'tender-volume-btn--active': effectiveGenerationMode !== 'full' && activeVolume === 'business' }"
          @click="selectVolume('business')"
        >
          商务标
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="effectiveGenerationMode !== 'full' && activeVolume === 'technical'"
          class="tender-volume-btn"
          :class="{ 'tender-volume-btn--active': effectiveGenerationMode !== 'full' && activeVolume === 'technical' }"
          @click="selectVolume('technical')"
        >
          技术标
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="effectiveGenerationMode === 'full'"
          class="tender-volume-btn"
          :class="{ 'tender-volume-btn--active': effectiveGenerationMode === 'full' }"
          @click="selectFullMode()"
        >
          综合标
        </button>
      </div>
    </template>
    <template #actions>
      <button
        type="button"
        class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border px-3 text-[12px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
        :disabled="!canResetWizard"
        :title="reachedDownload ? '已到下载定稿，记录已保留' : '回到上传邀请书，本次未定稿不会写入生成记录'"
        @click.stop="askResetWizard"
      >
        <RotateCcw class="size-3.5" :class="{ 'animate-spin': resetting }" />
        重置
      </button>
      <button
        type="button"
        class="inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-[12px] transition-colors"
        :class="
          showRecords
            ? 'border-iron bg-iron/10 text-foreground'
            : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground'
        "
        @click.stop="toggleRecords"
      >
        <History class="size-3.5" />
        我的生成记录
        <span v-if="recordsTotal" class="font-mono text-[11px]">{{ recordsTotal }}</span>
      </button>
    </template>
  </PageHeader>

  <p
    v-if="error"
    class="mb-4 text-[12px] text-sulfur border border-sulfur/30 bg-sulfur/10 rounded-md px-3 py-2"
  >
    {{ error }}
  </p>

  <p
    v-if="volumeHint && currentStepIndex === 0 && !showRecords"
    class="mb-4 text-[12px] text-muted-foreground border border-border bg-card rounded-md px-3 py-2"
  >
    {{ volumeHint }}
  </p>

  <div v-if="loading" class="py-16 text-center text-[12px] text-muted-foreground">
    <Loader2 class="inline size-4 animate-spin mr-2" />加载默认信息…
  </div>

  <section v-else-if="showRecords" class="tender-card mx-auto w-full max-w-4xl">
    <div class="tender-card-head">
      <div class="min-w-0">
        <h2 id="tender-records-title" class="text-[14px] font-semibold">我的生成记录</h2>
        <p class="text-[11px] text-muted-foreground mt-0.5">
          走到「下载定稿」后才会出现在这里。之后再次生成会覆盖同一条，不改变审批状态。
        </p>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <button
          type="button"
          class="tender-ghost-btn text-sulfur hover:text-sulfur"
          :disabled="!recordsTotal || clearingRecords"
          @click="askClearMineRecords"
        >
          <Loader2 v-if="clearingRecords" class="size-3.5 animate-spin" />
          <Trash2 v-else class="size-3.5" />
          清空
        </button>
        <button type="button" class="tender-ghost-btn" @click="closeRecords">
          <ChevronLeft class="size-3.5" />
          返回向导
        </button>
      </div>
    </div>

    <div class="mt-3 flex flex-wrap items-center gap-2">
      <input
        v-model="recordsQuery"
        class="kb-input font-sans max-w-xs"
        placeholder="搜索项目 / 招标人 / 创建人"
        @keydown.enter.prevent="loadRecords"
      />
      <button type="button" class="tender-ghost-btn" :disabled="recordsLoading" @click="loadRecords">
        <Loader2 v-if="recordsLoading" class="size-3.5 animate-spin" />
        查询
      </button>
    </div>

    <div
      v-if="recordsLoading && !records.length"
      class="py-8 text-center text-[12px] text-muted-foreground"
    >
      <Loader2 class="inline size-4 animate-spin mr-2" />加载记录…
    </div>
    <p v-else-if="!records.length" class="mt-4 text-[12px] text-muted-foreground text-center py-6">
      暂无记录。走到「下载定稿」后才会出现在这里。
    </p>
    <ul v-else class="mt-3 divide-y divide-border/70">
      <li
        v-for="item in records"
        :key="item.id"
        class="flex flex-col sm:flex-row sm:items-center gap-2 py-3"
      >
        <div class="min-w-0 flex-1">
          <div class="text-[13px] font-medium truncate">{{ item.projectName || '未命名项目' }}</div>
          <div class="mt-0.5 text-[11px] text-muted-foreground">
            {{ item.tenderer || '—' }}
            · {{ formatPrice(item.bidPriceYuan) }} 元
            · {{ item.username || '—' }}
            · {{ formatRecordTime(item.createdAt) }}
            <span v-if="!item.docxAvailable" class="text-sulfur ml-1">文件缺失</span>
          </div>
        </div>
        <div class="flex shrink-0 items-center gap-1.5 flex-wrap">
          <button
            type="button"
            class="tender-ghost-btn"
            :disabled="loadingFormId === item.id || isTenderWorkflowLocked(item.status, item.workflowLocked)"
            @click="loadRecordForm(item)"
          >
            <Loader2 v-if="loadingFormId === item.id" class="size-3.5 animate-spin" />
            载入表单
          </button>
          <button
            type="button"
            class="tender-ghost-btn"
            :disabled="regeneratingId === item.id || isTenderWorkflowLocked(item.status, item.workflowLocked)"
            @click="askRegenerateRecord(item)"
          >
            <Loader2 v-if="regeneratingId === item.id" class="size-3.5 animate-spin" />
            重新生成
          </button>
          <button
            type="button"
            class="tender-ghost-btn"
            :disabled="!(item.docxAvailable || item.techDocxAvailable) || openingRecordId === item.id"
            @click="openRecordPreview(item)"
          >
            <Loader2 v-if="openingRecordId === item.id" class="size-3.5 animate-spin" />
            预览
          </button>
          <button
            v-if="item.docxFile"
            type="button"
            class="tender-ghost-btn"
            :disabled="!item.docxAvailable || downloading === 'docx'"
            @click="downloadRecord(item, 'docx')"
          >
            <Download class="size-3.5" />
            Word商务
          </button>
          <button
            v-if="item.techDocxFile"
            type="button"
            class="tender-ghost-btn"
            :disabled="item.techDocxAvailable === false || downloading === 'tech'"
            @click="downloadRecord(item, 'tech')"
          >
            <Download class="size-3.5" />
            Word技术
          </button>
          <button
            v-if="item.pdfFile"
            type="button"
            class="tender-ghost-btn"
            :disabled="!item.pdfAvailable || downloading === 'pdf'"
            @click="downloadRecord(item, 'pdf')"
          >
            资质
          </button>
          <button
            type="button"
            class="tender-ghost-btn text-sulfur hover:text-sulfur"
            :disabled="deletingId === item.id"
            @click="askDeleteRecord(item)"
          >
            <Loader2 v-if="deletingId === item.id" class="size-3.5 animate-spin" />
            删除
          </button>
        </div>
      </li>
    </ul>
  </section>

  <div
    v-else
    class="tender-page mx-auto"
    :class="
      currentStepIndex === 2
        ? 'tender-page--preview max-w-[1600px]'
        : currentStepIndex >= 3
          ? 'max-w-6xl'
          : 'max-w-3xl'
    "
  >
    <nav
      class="tender-steps"
      :class="currentStepIndex === 2 ? 'mb-2 shrink-0' : 'mb-6'"
      aria-label="投标生成流程"
    >
      <ol class="tender-steps-list">
        <li
          v-for="(step, i) in workflowSteps"
          :key="step.key"
          class="tender-step-item"
          :class="{ 'tender-step-item--grow': i < workflowSteps.length - 1 }"
        >
          <button
            type="button"
            class="tender-step-btn"
            :class="[
              i <= activeWorkflowIndex ? 'text-foreground' : 'text-muted-foreground',
              i <= maxStepReached ? 'cursor-pointer hover:opacity-80' : 'cursor-default',
            ]"
            :disabled="i > maxStepReached"
            @click="goToStep(i)"
          >
            <span
              class="tender-step-dot"
              :class="{
                'tender-step-dot--done': i < activeWorkflowIndex,
                'tender-step-dot--active': i === activeWorkflowIndex,
              }"
            >
              {{ i < activeWorkflowIndex ? '✓' : i + 1 }}
            </span>
            <span class="text-[12px] font-medium hidden md:inline whitespace-nowrap">{{ step.label }}</span>
          </button>
          <span
            v-if="i < workflowSteps.length - 1"
            class="tender-step-line"
            :class="i < activeWorkflowIndex ? 'tender-step-line--done' : ''"
          />
        </li>
      </ol>
    </nav>

    <input
      ref="fileInput"
      type="file"
      class="hidden"
      accept=".doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      @change="onPickFile"
    />
    <input
      ref="quoteInput"
      type="file"
      class="hidden"
      accept=".xlsx,.xlsm,.docx,.csv,.pdf"
      @change="onPickQuote"
    />
    <input
      ref="slotFileInput"
      type="file"
      class="hidden"
      multiple
      accept=".pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,application/pdf,image/*"
      @change="onSlotFileChange"
    />
    <input
      ref="boqInput"
      type="file"
      class="hidden"
      accept=".xlsx,.xlsm,.docx,.csv,.pdf"
      @change="onBoqFile"
    />
    <input
      ref="drawingPdfInput"
      type="file"
      class="hidden"
      accept=".pdf,application/pdf"
      @change="onDrawingPdfFile"
    />

    <!-- 步骤 1：上传邀请书 -->
    <section v-if="currentStepIndex === 0" class="tender-step-panel">
      <div class="tender-card">
        <div class="tender-card-head">
          <div class="flex items-center gap-2.5 min-w-0">
            <span class="tender-card-icon">
              <FileUp class="size-4" />
            </span>
            <div class="min-w-0">
              <h2 class="text-[14px] font-semibold text-foreground">甲方邀请书</h2>
              <p class="text-[11px] text-muted-foreground mt-0.5">
                只支持 Word（.doc / .docx）。未上传邀请书时不能识别。
              </p>
            </div>
          </div>
        </div>

        <button
          type="button"
          class="tender-dropzone w-full mt-4"
          :class="{ 'tender-dropzone--active': dragOverInvite, 'tender-dropzone--filled': inviteFile }"
          @click="fileInput?.click()"
          @dragover.prevent="dragOverInvite = true"
          @dragleave.prevent="dragOverInvite = false"
          @drop.prevent="onInviteDrop"
        >
          <Upload class="size-8 text-muted-foreground/70 mb-2" />
          <p class="text-[13px] font-medium text-foreground">
            {{ inviteFile ? inviteFile.name : '点击或拖拽上传投标邀请书' }}
          </p>
          <p class="text-[11px] text-muted-foreground mt-1">
            {{ inviteFile ? '点击可更换文件' : `仅 .doc / .docx，单文件最大 ${fmtSize(KB_UPLOAD_MAX_BYTES)}` }}
          </p>
        </button>

        <div class="mt-4">
          <p class="text-[13px] font-medium text-foreground">报价清单</p>
          <p class="text-[11px] text-muted-foreground mt-0.5">
            可选。Excel / Word，或从报价记录选择。未上传则生成时不填写报价分项。
          </p>
          <button
            type="button"
            class="tender-dropzone w-full mt-2"
            :class="{ 'tender-dropzone--active': dragOverQuote, 'tender-dropzone--filled': hasQuoteSource }"
            @click="quoteInput?.click()"
            @dragover.prevent="dragOverQuote = true"
            @dragleave.prevent="dragOverQuote = false"
            @drop.prevent="onQuoteDrop"
          >
            <Upload class="size-8 text-muted-foreground/70 mb-2" />
            <p class="text-[13px] font-medium text-foreground">
              {{ quoteSourceLabel || '点击或拖拽上传报价清单' }}
            </p>
            <p class="text-[11px] text-muted-foreground mt-1">
              {{ hasQuoteSource ? '点击可更换文件' : '可不传。支持 .xlsx / .docx / .csv / .pdf' }}
            </p>
          </button>
          <div class="mt-3 flex flex-wrap items-center gap-2">
            <button type="button" class="tender-ghost-btn" @click="openQuotePicker">
              <FileSpreadsheet class="size-3.5" />
              从报价记录选
            </button>
            <button
              v-if="hasQuoteSource"
              type="button"
              class="tender-ghost-btn text-sulfur"
              @click="clearQuoteSource"
            >
              清除
            </button>
          </div>
        </div>

        <div v-if="kbList.length" class="mt-4 pt-4 border-t border-border/60">
          <div class="mb-3 flex flex-wrap items-center gap-2">
            <label class="text-[11px] text-muted-foreground">投标模板</label>
            <select v-model="selectedPromptId" class="h-7 rounded-md border border-border bg-background px-2 text-[11px]">
              <option value="">通用（不指定）</option>
              <option v-for="o in promptOptions" :key="o.id" :value="o.id">{{ o.name }}</option>
            </select>

          </div>
          <p class="text-[11px] text-muted-foreground mb-2">
            历史标书参考（只学写法，不抄数据，默认推荐同行业1-2份）。
          </p>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="b in kbList"
              :key="b.id"
              type="button"
              class="h-7 px-2.5 rounded-full border text-[11px] transition-colors"
              :class="
                selectedKbIds.includes(b.id)
                  ? 'border-iron bg-iron/10 text-foreground'
                  : 'border-border text-muted-foreground hover:bg-accent'
              "
              @click="toggleKb(b.id)"
            >
              {{ b.name }}
            </button>
          </div>
        </div>
      </div>

      <footer class="tender-step-footer">
        <span />
        <button
          type="button"
          class="tender-primary-btn min-w-[220px]"
          :disabled="parsing || !inviteFile"
          :title="inviteFile ? '' : '请先上传投标邀请书'"
          @click="onUploadStepNext"
        >
          <Loader2 v-if="parsing" class="size-3.5 animate-spin" />
          <Sparkles v-else class="size-3.5" />
          {{ parsing ? `正在读取招标文件… ${parseWaitLabel()}` : '识别并继续' }}
          <ChevronRight v-if="!parsing" class="size-3.5" />
        </button>
      </footer>
    </section>

    <!-- 步骤 2：填表生成 -->
    <section v-else-if="currentStepIndex === 1" class="tender-step-panel space-y-4">
      <div
        v-if="parseOcr?.ocrCapped"
        class="tender-card border-sulfur/40 bg-sulfur/10"
      >
        <p class="text-[12px] text-sulfur leading-relaxed">
          邀请书共 {{ parseOcr.pageCount || '—' }} 页，OCR 已达上限 {{ parseOcr.ocrMaxPages }} 页（实际识别
          {{ parseOcr.ocrPages }} 页）。未识别页未进入抽取，请提高 OCR_MAX_PAGES 后点「识别并继续」重试。
        </p>
      </div>
      <Panel
        v-if="primaryParseNotes.length || extraParseNotes.length"
        id="tender-parse-notes"
        :key="'parse-notes-' + parseNotesEpoch"
        title="识别说明"
        subtitle="只列出风险和回填摘要，生成前请先过一遍"
        collapsible
        :default-open="true"
        class-name="tender-parse-notes"
      >
        <template #summary>{{ parseNotesSummary }}</template>
        <ul class="space-y-1.5 text-[11px] leading-relaxed">
          <li
            v-for="(n, i) in primaryParseNotes"
            :key="'p-' + i"
            class="flex gap-1.5"
            :class="noteTone(n)"
          >
            <TriangleAlert
              v-if="isRiskNote(n)"
              class="size-3.5 shrink-0 mt-0.5"
            />
            <span class="min-w-0">{{ n }}</span>
          </li>
        </ul>
        <button
          v-if="extraParseNotes.length"
          type="button"
          class="mt-2 inline-flex items-center gap-0.5 text-[11px] text-muted-foreground hover:text-foreground"
          @click="parseExtraOpen = !parseExtraOpen"
        >
          {{ parseExtraOpen ? '收起其余说明' : `其余 ${extraParseNotes.length} 条` }}
          <ChevronUp v-if="parseExtraOpen" class="size-3.5" />
          <ChevronDown v-else class="size-3.5" />
        </button>
        <ul v-if="parseExtraOpen && extraParseNotes.length" class="mt-1.5 space-y-1.5 text-[11px] leading-relaxed text-muted-foreground">
          <li v-for="(n, i) in extraParseNotes" :key="'e-' + i">{{ n }}</li>
        </ul>
      </Panel>
      <Panel
        title="组卷模板"
        :subtitle="
          form.structureMode === 'outline'
            ? `截出的格式章需预览并确认后才能生成`
            : '按公司固定投标文件格式组卷'
        "
        collapsible
        :default-open="true"
      >
        <template #summary>{{ outlineSummary }}</template>
        <p v-if="form.outlineChapter" class="mb-2 text-[11px] text-muted-foreground">
          本标识别到格式章：{{ form.outlineChapter }}
        </p>
        <div class="mb-3 space-y-2 text-[11px]">
          <div v-if="!form.requirementsDriven" class="flex flex-wrap gap-3">
            <label class="inline-flex items-center gap-1.5">
              <input
                type="radio"
                name="structure-mode"
                :checked="form.structureMode === 'fixed'"
                :disabled="hasOriginalFormat()"
                class="accent-current"
                @change="setStructureMode('fixed')"
              />
              按固定模板生成
            </label>
            <label class="inline-flex items-center gap-1.5">
              <input
                type="radio"
                name="structure-mode"
                :checked="form.structureMode === 'outline'"
                class="accent-current"
                @change="setStructureMode('outline')"
              />
              按本标招标书要求生成
            </label>
          </div>
          <p v-if="hasOriginalFormat()" class="text-muted-foreground">
            优先沿用原版封面和章节格式，仅用内置格式补齐缺少的章节。
          </p>
          <div v-if="form.requirementsDriven" class="space-y-2">
            <p class="text-muted-foreground leading-relaxed">
              已按全文投标要求综合大纲，自动生成封面、目录和完整正文结构。缺少的资料及响应内容会标为【待补充】，请在生成后核对。
            </p>
            <button
              type="button"
              class="h-7 px-2.5 rounded-md border border-border text-[11px] hover:bg-accent disabled:opacity-50"
              :disabled="formatPreviewBusy || !form.invitationId"
              @click="loadFormatFile(true)"
            >
              {{ formatPreviewBusy ? '打开中…' : '预览模板' }}
            </button>
          </div>
          <div
            v-else-if="form.structureMode === 'fixed'"
            class="flex flex-wrap items-center gap-2 text-muted-foreground"
          >
            <label>接标模板</label>
            <button
              type="button"
              class="h-7 px-2.5 rounded-md border border-border text-[11px] hover:bg-accent disabled:opacity-50"
              :disabled="formatPreviewBusy || !form.invitationId"
              @click="loadFormatFile(true)"
            >
              {{ formatPreviewBusy ? '打开中…' : '预览模板' }}
            </button>
            <select v-model="form.contentTemplateId" class="h-7 rounded-md border border-border bg-background px-2 text-[11px]">
              <option
                v-for="t in fixedTemplates"
                :key="t.id"
                :value="t.id"
                :disabled="!t.found"
              >
                {{ t.label }}{{ t.found ? '' : '（缺少空白稿）' }}
              </option>
            </select>
            <span v-if="fixedTemplateLabel || layoutTemplate?.label" class="text-foreground/80">
              {{ fixedTemplateLabel || layoutTemplate?.label || '内置投标文件格式' }}，章节直接进卷，不按本标目录重排
            </span>
            <ul
              v-if="!activeFixedTemplate?.sections?.length"
              class="basis-full flex flex-wrap items-center gap-1 text-muted-foreground"
            >
              <li>投标函</li>
              <li>法定代表人身份证明</li>
              <li>授权委托书</li>
              <li>分项报价表</li>
              <li>商务偏离表</li>
              <li>企业业绩</li>
              <li>原厂生产承诺</li>
              <li>技术偏差表</li>
              <li>技术方案</li>
            </ul>
            <ul
              v-if="activeFixedTemplate?.sections?.length"
              class="basis-full flex flex-wrap items-center gap-1 text-muted-foreground"
            >
              <li v-for="s in activeFixedTemplate.sections" :key="s">{{ s }}</li>
              <li v-if="activeFixedTemplate.techTitle" class="text-foreground/70">
                技术标：{{ activeFixedTemplate.techTitle }}
              </li>
              <li v-if="activeFixedTemplate.factoryRole">
                原厂角色：{{ activeFixedTemplate.factoryRole }}
              </li>
              <li v-for="n in activeFixedTemplate.notes || []" :key="n" class="text-sulfur">
                {{ n }}
              </li>
            </ul>
            <ul
              v-if="activeFixedTemplate?.health"
              class="basis-full flex flex-wrap items-center gap-1"
              :class="activeFixedTemplate.health.ok ? 'text-muted-foreground' : 'text-sulfur'"
            >
              <li v-if="activeFixedTemplate.health.ok">
                标题体检通过，已识别锚点：{{ activeFixedTemplate.health.found.join('、') }}
              </li>
              <li v-else>
                标题体检未通过，生成会被拦下。缺少：{{ activeFixedTemplate.health.missingRequired.join('、') }}
              </li>
              <li v-if="activeFixedTemplate.health.missingOptional?.length">
                未识别可选章节：{{ activeFixedTemplate.health.missingOptional.join('、') }}
              </li>
            </ul>
            <input
              ref="layoutTemplateInput"
              type="file"
              accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              class="hidden"
              @change="onPickLayoutTemplate"
            />
            <button
              type="button"
              class="text-[11px] underline underline-offset-2 hover:text-foreground disabled:opacity-50"
              :disabled="uploadingLayoutTemplate"
              @click="layoutTemplateInput?.click()"
            >
              {{ uploadingLayoutTemplate ? '上传中…' : '更换该模板空白稿' }}
            </button>
            <button
              v-if="layoutTemplate?.custom"
              type="button"
              class="text-[11px] underline underline-offset-2 hover:text-foreground disabled:opacity-50"
              :disabled="uploadingLayoutTemplate"
              @click="onRestoreLayoutTemplate"
            >
              恢复内置
            </button>
          </div>
          <div
            v-else
            class="rounded-md border border-border/60 px-2.5 py-2 space-y-2"
          >
            <p class="text-muted-foreground leading-relaxed">
              生成使用截出的格式章。先点「预览模板」修改或核对，再确认。确认后的这份（含改过的内容）才会用于生成。下面的目录只供核对。
            </p>
            <div class="flex flex-wrap items-center gap-2">
              <button
                type="button"
                class="h-7 px-2.5 rounded-md border border-border text-[11px] hover:bg-accent disabled:opacity-50"
                :disabled="formatPreviewBusy || !form.invitationId"
                @click="openFormatPreview"
              >
                {{ formatPreviewBusy ? '打开中…' : '预览模板' }}
              </button>
              <button
                type="button"
                class="h-7 px-2.5 rounded-md border border-foreground/40 text-[11px] hover:bg-accent disabled:opacity-50"
                :disabled="!formatPreviewFile || form.formatConfirmed || formatConfirmBusy"
                @click="confirmFormatTemplate"
              >
                {{ formatConfirmBusy ? '保存中…' : '确认使用这份模板' }}
              </button>
              <span v-if="form.formatConfirmed" class="text-emerald-700 dark:text-emerald-400">
                已确认使用这份模板，可生成
              </span>
              <span v-else-if="!form.invitationId" class="text-muted-foreground">
                请先上传并识别 Word 招标文件
              </span>
              <span v-else-if="!formatPreviewFile" class="text-muted-foreground">
                先预览，再确认
              </span>
            </div>
          </div>
        </div>
        <template v-if="form.structureMode === 'outline'">
        <p v-if="!visibleOutlineItems.length" class="text-[11px] text-muted-foreground">
          {{
            form.outlineItems.length
              ? `当前${activeVolume === 'technical' ? '技术标' : '商务标'}没有对应目录。切到另一卷可看其余条目。`
              : '未抽出格式章节。请上传带「投标/响应文件格式」的 Word 原件再识别，或继续用公司固定模板生成。'
          }}
        </p>
        <p
          v-else
          class="mb-2 text-[11px] text-muted-foreground"
        >
          下面是本标识别到的目录，只供核对，不参与组卷。
        </p>
        <ul
          v-if="visibleOutlineItems.length"
          class="space-y-1"
        >
          <li
            v-for="(item, index) in visibleOutlineItems"
            :key="item.id || item.title"
            class="flex items-baseline gap-2 px-1 py-0.5 text-[12px]"
          >
            <span class="w-4 shrink-0 text-center text-[11px] tabular-nums text-muted-foreground">{{ index + 1 }}</span>
            <span class="min-w-0 flex-1 leading-snug">{{ item.title }}</span>
          </li>
        </ul>
        </template>
      </Panel>
      <div v-if="matchHasRows && attachmentMatch" class="tender-card">
        <p class="text-[12px] font-medium text-foreground mb-2">资料库对照</p>
        <ul class="space-y-1.5 text-[11px]">
          <li v-if="attachmentMatch.matched.length" class="text-emerald-700 dark:text-emerald-400">
            已匹配扫描件 {{ attachmentMatch.matched.length }} 项
            <button
              type="button"
              class="ml-1 text-[11px] text-muted-foreground underline underline-offset-2"
              @click="parseMatchListOpen = !parseMatchListOpen"
            >
              {{ parseMatchListOpen ? '收起清单' : '查看清单' }}
            </button>
            <p v-if="parseMatchListOpen" class="mt-1 font-normal leading-relaxed">
              {{ attachmentMatch.matched.map((x) => x.title).join('、') }}
            </p>
          </li>
          <li
            v-for="item in attachmentMatch.missingFiles"
            :key="'miss-' + item.key"
            :class="item.required ? 'text-sulfur' : 'text-muted-foreground'"
          >
            {{ item.required ? '未上传，生成时用虚线框占位' : '已纳入未上传' }}：{{ item.title }}
          </li>
          <li
            v-for="item in attachmentMatch.createdItems"
            :key="'new-' + item.key"
            class="text-muted-foreground"
          >
            资料库原先没有，已建空项请上传：{{ item.title }}
          </li>
        </ul>
        <button
          v-if="canEditLibrary"
          type="button"
          class="mt-2 text-[11px] text-foreground underline underline-offset-2"
          @click="router.push('/tender-library')"
        >
          去资料库上传
        </button>
      </div>
      <div v-if="form.invitationId" class="tender-card">
        <div class="flex items-center justify-between gap-2 mb-1">
          <p class="text-[12px] font-medium text-foreground">否决项</p>
          <button
            v-if="vetoItems.length"
            type="button"
            class="text-[11px] text-muted-foreground underline underline-offset-2 shrink-0"
            @click="vetoListOpen = !vetoListOpen"
          >
            {{ vetoListOpen ? '收起' : '展开' }}
          </button>
        </div>
        <p class="text-[11px] text-muted-foreground leading-relaxed" :class="vetoListOpen ? 'mb-2' : ''">
          当前{{ activeVolume === 'technical' ? '技术标' : '商务标' }}
          <template v-if="vetoItems.length"> {{ vetoItems.length }} 条。</template>
          <template v-else>没有否决项。</template>
          <template v-if="vetoOtherCount">另一卷还有 {{ vetoOtherCount }} 条，切换左上角查看。</template>
          重新识别会整份替换，这里的删除不会保留。不拦生成和下载。
        </p>
        <ul v-if="vetoListOpen && vetoItems.length" class="space-y-2">
          <li
            v-for="item in vetoItems"
            :key="item.id"
            class="text-[11px] leading-snug"
          >
            <p class="text-foreground">{{ item.text }}</p>
            <p class="mt-0.5 text-muted-foreground">
              {{ vetoStatusLabel(item.matchStatus) }}
              <template v-if="item.matchNote"> · {{ item.matchNote }}</template>
              <template v-if="item.sourceChapter"> · {{ item.sourceChapter }}</template>
              <template v-if="item.sourcePage"> · 第{{ item.sourcePage }}页</template>
            </p>
            <p class="mt-0.5">
              <button
                type="button"
                class="text-muted-foreground underline underline-offset-2"
                @click="dropVeto(item.id)"
              >
                删除误报
              </button>
              <button
                type="button"
                class="ml-3 text-muted-foreground underline underline-offset-2"
                @click="easeVeto(item.id)"
              >
                降为一般
              </button>
            </p>
          </li>
        </ul>
      </div>
      <div v-if="form.invitationId && compareItems.length" class="tender-card">
        <p class="text-[12px] font-medium text-foreground mb-1">要求对照</p>
        <p class="text-[11px] text-muted-foreground mb-2 leading-relaxed">
          当前{{ activeVolume === 'technical' ? '技术标' : '商务标' }} {{ compareItems.length }} 条。只在本页核对，不写入投标文件。
        </p>
        <ul class="max-h-72 space-y-2 overflow-y-auto">
          <li
            v-for="item in compareItems"
            :key="'cmp-' + item.id"
            class="text-[11px] leading-snug"
          >
            <p class="text-foreground">{{ item.text }}</p>
            <p class="mt-0.5 text-muted-foreground">
              {{ compareAnswer(item) }}
              <template v-if="item.respond && item.respond !== compareAnswer(item)"> · {{ item.respond }}</template>
              <template v-if="item.attachToSection || item.sourceChapter">
                · {{ item.attachToSection || item.sourceChapter }}
              </template>
            </p>
          </li>
        </ul>
      </div>
      <div v-if="form.invitationId" class="tender-card">
        <p class="text-[12px] font-medium text-foreground mb-1">本卷版式</p>
        <p class="text-[11px] text-muted-foreground mb-2 leading-relaxed">
          当前{{ activeVolume === 'technical' ? '技术标' : '商务标' }}。标书写明的才改范本副本，没写的保持范本。
        </p>
        <ul v-if="activeFormatNotes.length" class="space-y-1 text-[11px] text-foreground">
          <li v-for="(line, i) in activeFormatNotes" :key="'fmt-' + i">{{ line }}</li>
        </ul>
        <p v-else class="text-[11px] text-muted-foreground">
          本卷未写页边距、字体、页码，生成时不改范本版式。
        </p>
        <p v-if="activeVolume === 'technical'" class="mt-1 text-[11px] text-muted-foreground">
          技术标只保留格式章里的技术部分；没有单独技术标题时仍用整份截件。
        </p>
      </div>
      <Panel
        v-if="showFormGenerateHints"
        :key="'gen-hints-' + (result?.docxFile || '')"
        title="上次生成提示"
        subtitle="只列出需要核对的项；过程说明、耗时和资料库重复项已隐藏"
        collapsible
        :default-open="generateRiskWarnings.length > 0"
      >
        <template #summary>{{ generateHintsSummary }}</template>
        <template #action>
          <button
            type="button"
            class="text-[11px] text-muted-foreground hover:text-foreground underline underline-offset-2"
            @click="dismissGenerateHints"
          >
            隐藏
          </button>
        </template>
        <ul class="space-y-1.5 text-[11px] leading-relaxed">
          <li
            v-for="(w, i) in generateShownWarnings"
            :key="'gw-' + i"
            class="flex gap-1.5"
            :class="isGenerateRiskWarning(w) ? 'text-sulfur' : 'text-muted-foreground'"
          >
            <TriangleAlert
              v-if="isGenerateRiskWarning(w)"
              class="size-3.5 shrink-0 mt-0.5"
            />
            <span class="min-w-0">{{ w }}</span>
          </li>
        </ul>
      </Panel>

      <div
        v-if="activeVolume === 'business'"
        id="tender-volume-business"
        class="space-y-4"
      >
      <div class="rounded-lg border border-border/70 bg-card px-3 py-2.5">
        <div class="flex flex-wrap items-baseline gap-2">
          <h2 class="text-[13px] font-semibold">商务标</h2>
          <span class="text-[11px] text-muted-foreground">未上传的扫描件会在 Word 里画虚线框，不阻止生成</span>
        </div>
        <p class="mt-1 text-[11px] text-muted-foreground leading-relaxed">
          公司、资质、人员、业绩、报价、各种函件。下面带 * 的项和必填扫描件齐了才能生成。
        </p>
      </div>

      <Panel title="项目与招标人" subtitle="识别结果可再改；投标人侧不会被邀请书覆盖">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div class="md:col-span-2 rounded-md border border-primary/35 bg-primary/5 px-3 py-2.5">
            <label class="text-[12px] space-y-1 block">
              <span class="text-foreground font-medium">
                投标日期<span class="tender-req" aria-hidden="true">*</span>
              </span>
              <span class="block text-[11px] text-muted-foreground leading-relaxed">
                封面、投标函、授权委托书都用这个日期。默认今天，改了且与今天相差 ≥1 天会再确认。
              </span>
              <input v-model="form.bidDate" type="date" class="kb-input font-sans max-w-xs mt-1" />
            </label>
          </div>
          <label class="md:col-span-2 text-[12px] space-y-1">
            <span class="text-muted-foreground">项目名称<span class="tender-req" aria-hidden="true">*</span></span>
            <input
              v-model="form.projectName"
              class="kb-input font-sans"
              required
              aria-required="true"
              placeholder="识别邀请书后自动填入，也可手工填写"
            />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">招标人<span class="tender-req" aria-hidden="true">*</span></span>
            <input
              v-model="form.tenderer"
              class="kb-input font-sans"
              required
              aria-required="true"
              placeholder="甲方单位全称"
            />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">招标编号</span>
            <input
              v-model="form.tenderNo"
              class="kb-input font-sans"
              placeholder="邀请书写明时自动填，封面可印出"
            />
          </label>
          <label class="md:col-span-2 text-[12px] space-y-1">
            <span class="text-muted-foreground">投标内容</span>
            <input
              v-model="form.bidContent"
              class="kb-input font-sans"
              placeholder="如：充电桩供货、安装调试及技术服务"
            />
          </label>
        </div>
      </Panel>

      <Panel title="报价与承诺" subtitle="报价单由工程量清单识别；大纲组卷时表头跟招标书，充电桩第五章仍用设备/技术参数列">
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <label class="col-span-2 text-[12px] space-y-1">
            <span class="text-muted-foreground">投标总价（元）<span class="tender-req" aria-hidden="true">*</span></span>
            <input
              v-model.number="form.bidPriceYuan"
              type="number"
              min="0"
              step="0.01"
              class="kb-input font-sans"
              required
              aria-required="true"
            />
            <span class="block text-[11px] text-muted-foreground leading-relaxed">
              <template v-if="form.quoteLines.length">
                已识别报价清单 {{ form.quoteLines.length }} 项
                <span v-if="form.quoteTitle">（{{ form.quoteTitle }}）</span>
                ，不含税合计约 {{ quoteAmountSum.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }} 元。
                若总价与清单含税不同，生成时只折算单价。
              </template>
              <template v-else>
                未上传报价清单。生成时不填写报价分项；需要填写可在上方或此处上传后点「识别清单」。
              </template>
            </span>
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">工期/交货期（日历天）</span>
            <input v-model.number="form.deliveryDays" type="number" min="1" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">质保（年）</span>
            <input v-model.number="form.warrantyYears" type="number" min="1" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">质量</span>
            <input v-model="form.quality" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">投标有效期（天）</span>
            <input v-model.number="form.bidValidityDays" type="number" min="1" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">投标保证金（元）</span>
            <input v-model.number="form.bidBondYuan" type="number" min="0" step="0.01" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">安全生产措施费（元）</span>
            <input v-model.number="form.safetyFeeYuan" type="number" min="0" step="0.01" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">暂列金额（元）</span>
            <input v-model.number="form.provisionalYuan" type="number" min="0" step="0.01" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">专业工程暂估价（元）</span>
            <input v-model.number="form.primeCostYuan" type="number" min="0" step="0.01" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">项目经理</span>
            <input v-model="form.pmName" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">项目经理注册证号</span>
            <input v-model="form.pmCertNo" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">项目经理专业等级</span>
            <input v-model="form.pmGrade" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">缺陷责任期</span>
            <input v-model="form.defectPeriod" class="kb-input font-sans" placeholder="招标文件给定，如 12个月" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">履约担保金额</span>
            <input v-model="form.perfBond" class="kb-input font-sans" placeholder="招标文件给定，空则留下划线" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">预付款 %</span>
            <input v-model.number="form.prepaidPct" type="number" min="0" max="100" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">到货款 %</span>
            <input v-model.number="form.arrivalPct" type="number" min="0" max="100" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">结算款 %</span>
            <input v-model.number="form.settlementPct" type="number" min="0" max="100" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">质保金 %</span>
            <input v-model.number="form.warrantyPct" type="number" min="0" max="100" class="kb-input font-sans" />
          </label>
        </div>
        <p class="mt-2 text-[11px]" :class="paySum === 100 ? 'text-muted-foreground' : 'text-sulfur'">
          支付比例合计 {{ paySum }}%{{ paySum === 100 ? '' : '（建议为 100%）' }}
        </p>
        <label class="mt-3 block text-[12px] space-y-1">
          <span class="text-muted-foreground">流量费说明</span>
          <input v-model="form.trafficFeeNote" class="kb-input font-sans" />
        </label>
        <label class="mt-3 block text-[12px] space-y-1">
          <span class="text-muted-foreground">原厂承诺角色</span>
          <input
            v-model="form.factoryRole"
            class="kb-input font-sans"
            placeholder="空则按项目内容判断：充电设备生产厂商 / 投标产品生产厂商"
          />
        </label>

        <div
          class="mt-4 rounded-md border border-border/70 px-3 py-2.5 space-y-2"
          :class="
            quotePanelReady
              ? 'bg-accent/20'
              : hasQuoteSource
                ? 'bg-accent/10'
                : 'bg-sulfur/5 border-sulfur/25'
          "
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="min-w-0">
              <p class="text-[12px] font-medium text-foreground">
                {{ quotePanelTitle }}
              </p>
              <p class="mt-0.5 text-[11px] text-muted-foreground leading-relaxed">
                <template v-if="quotePanelReady">
                  生成 Word 时按招标书表头写入报价表
                  <span v-if="form.quoteHeaders.length">（{{ form.quoteHeaders.join(' / ') }}）</span>
                  <template v-else>；充电桩类标仍用设备/技术参数列。</template>
                  <span v-if="quoteHasSpecColumn && quoteSpecFilledCount">
                    已解析明细 {{ quoteSpecFilledCount }}/{{ form.quoteLines.length }} 项。
                  </span>
                  <span v-else-if="quoteHasSpecColumn" class="text-sulfur">未解析到技术参数列，请确认清单含规格/参数列后重新识别。</span>
                </template>
                <template v-else-if="hasQuoteSource">
                  已关联来源，点右侧「识别清单」写入表单。需保留本页会话中的邀请书文件。
                </template>
                <template v-else>
                  上传工程量 Excel/Word，或从 AI 报价记录勾选后点「识别清单」。需保留本页会话中的邀请书文件。
                </template>
              </p>
            </div>
            <div class="flex flex-wrap items-center gap-1.5 shrink-0">
              <button type="button" class="tender-ghost-btn" @click="quoteInput?.click()">
                <Upload class="size-3.5" />
                {{ quoteFile ? '更换报价清单' : '报价清单' }}
              </button>
              <button type="button" class="tender-ghost-btn" @click="openQuotePicker">
                <FileSpreadsheet class="size-3.5" />
                {{ quoteRecordIds.length ? `记录 ${quoteRecordIds.length}` : '报价记录' }}
              </button>
              <button
                type="button"
                class="tender-ghost-btn"
                :disabled="parsing || !hasQuoteSource || !inviteFile"
                :title="
                  !inviteFile
                    ? '请返回上一步重新上传邀请书'
                    : !hasQuoteSource
                      ? '请先上传报价清单或勾选报价记录'
                      : ''
                "
                @click="reparseQuoteFromForm"
              >
                <Loader2 v-if="parsing" class="size-3.5 animate-spin" />
                <Sparkles v-else class="size-3.5" />
                {{ parsing ? '识别中…' : '识别清单' }}
              </button>
            </div>
          </div>
          <p v-if="quoteSourceLabel" class="text-[11px] text-muted-foreground truncate">
            已选：{{ quoteSourceLabel }}
          </p>
          <p v-else-if="!inviteFile" class="text-[11px] text-sulfur">
            邀请书文件已失效，请点「上一步」重新上传邀请书与清单。
          </p>

          <div v-if="form.quoteLines.length" class="overflow-x-auto border border-border rounded-md bg-background">
            <table class="w-full text-[11px] border-collapse min-w-[720px]">
              <thead>
                <tr class="text-muted-foreground bg-accent/40">
                  <th
                    v-for="(h, i) in quotePreviewHeaders"
                    :key="'qh-' + i"
                    class="px-2 py-1.5 text-left font-medium"
                  >
                    {{ h }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(row, i) in form.quoteLines"
                  :key="i"
                  class="border-t border-border align-top"
                >
                  <td
                    v-for="(role, j) in quotePreviewRoles"
                    :key="'qc-' + i + '-' + j"
                    class="px-2 py-1.5"
                    :class="role === 'name' ? 'font-medium' : role === 'spec' ? 'text-muted-foreground' : 'font-sans'"
                  >
                    <pre
                      v-if="role === 'spec' && String(row.spec || '').trim()"
                      class="m-0 max-h-28 overflow-y-auto whitespace-pre-wrap break-words font-sans text-[11px] leading-relaxed"
                    >{{ row.spec }}</pre>
                    <span v-else-if="role === 'spec'" class="text-sulfur/80">（无明细）</span>
                    <template v-else>{{ quotePreviewValue(row, j) || '—' }}</template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="form.quoteLines.length" class="text-[11px] text-muted-foreground">
            数量合计 {{ quoteQtySum }} · 不含税合计
            {{ quoteAmountSum.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }} 元
          </p>
        </div>
      </Panel>

      <Panel
        title="类似业绩"
        subtitle="资料库对上的预选写入；没对上的先占位，不拦截生成"
      >
        <div class="flex items-center justify-between mb-2 gap-2">
          <p class="text-[12px] text-muted-foreground min-w-0">
            {{ performanceMatchSummary }}
          </p>
          <button
            type="button"
            class="h-7 px-2 rounded-md border border-border text-[11px] hover:bg-accent shrink-0"
            @click="addPerformanceRow"
          >
            增行
          </button>
        </div>
        <p v-if="performanceReqLabel" class="mb-2 text-[11px] leading-relaxed text-muted-foreground">
          招标要求：{{ performanceReqLabel }}
        </p>
        <p v-else class="mb-2 text-[11px] text-muted-foreground leading-relaxed">
          未从本份招标书抽出同类/金额/数量门槛。资料库合同会列入本标，可取消勾选。
        </p>
        <div class="overflow-x-auto border border-border rounded-md">
          <table class="w-full text-[11px] border-collapse min-w-[1140px] tender-perf-table">
            <thead>
              <tr class="text-muted-foreground bg-accent/40">
                <th class="px-2 py-1.5 text-left font-medium w-14">写入</th>
                <th class="px-2 py-1.5 text-left font-medium min-w-[160px]">项目/合同</th>
                <th class="px-2 py-1.5 text-left font-medium min-w-[88px]">规格型号</th>
                <th class="px-2 py-1.5 text-left font-medium min-w-[80px]">买方</th>
                <th class="px-2 py-1.5 text-left font-medium min-w-[72px]">联系人</th>
                <th class="px-2 py-1.5 text-left font-medium min-w-[96px]">合同额（元）</th>
                <th class="px-2 py-1.5 text-left font-medium min-w-[120px]">概况</th>
                <th class="px-2 py-1.5 text-left font-medium w-16">在建</th>
                <th class="px-2 py-1.5 text-left font-medium w-24">对照</th>
                <th class="tender-op-col px-2 py-1.5 text-center font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!form.performanceLines.length">
                <td colspan="10" class="px-2 py-3 text-muted-foreground">
                  邀请书写了类似业绩、资料库没对上时，会先占几行空位，可直接改。资料库合同识别后会按门槛勾选。
                </td>
              </tr>
              <tr
                v-for="(row, i) in form.performanceLines"
                :key="i"
                class="border-t border-border"
                :class="row.includeInBid === false ? 'opacity-60' : ''"
              >
                <td class="px-1 py-1 text-center">
                  <input v-model="row.includeInBid" type="checkbox" title="勾选后写入本标投标文件" />
                </td>
                <td class="px-1 py-1">
                  <input v-model="row.projectName" class="kb-input font-sans !py-1 !px-1" placeholder="工程/合同名称" />
                </td>
                <td class="px-1 py-1 min-w-[100px]">
                  <input v-model="row.spec" class="kb-input font-sans !py-1 !px-1" placeholder="直流功率/群充/箱变" />
                </td>
                <td class="px-1 py-1 min-w-[100px]">
                  <input v-model="row.client" class="kb-input font-sans !py-1 !px-1" placeholder="甲方/买方" />
                </td>
                <td class="px-1 py-1 min-w-[72px]">
                  <input v-model="row.contact" class="kb-input font-sans !py-1 !px-1" placeholder="可空或保密" />
                </td>
                <td class="px-1 py-1 min-w-[96px]">
                  <input v-model.number="row.amountYuan" type="number" min="0" step="0.01" class="kb-input font-sans !py-1 !px-1" />
                </td>
                <td class="px-1 py-1 min-w-[120px]">
                  <input v-model="row.summary" class="kb-input font-sans !py-1 !px-1" placeholder="桩数、场站、EPC/供货" />
                </td>
                <td class="px-1 py-1 text-center">
                  <input v-model="row.ongoing" type="checkbox" title="勾选表示在建未竣工" />
                </td>
                <td class="px-1 py-1">
                  <span v-if="isPerfPlaceholder(row)" class="text-muted-foreground">待补充</span>
                  <span
                    v-else-if="performanceRequirementActive(form.performanceRequirement)"
                    class="inline-block leading-snug"
                    :class="
                      performanceRowMatch(row).passed
                        ? 'text-emerald-700 dark:text-emerald-400'
                        : 'text-sulfur'
                    "
                  >
                    {{
                      performanceRowMatch(row).passed
                        ? '符合'
                        : performanceRowMatch(row).reasons.join('、') || '不符'
                    }}
                  </span>
                  <span v-else class="text-muted-foreground">—</span>
                </td>
                <td class="tender-op-col px-1.5 py-1 text-center">
                  <button
                    type="button"
                    class="tender-row-del"
                    title="删除本条业绩"
                    @click="removePerformanceRow(i)"
                  >
                    <Trash2 class="size-3" />
                    删除
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel
        title="投标人（伟泰）"
        subtitle="本公司信息：邀请书不会改这些字段，空项用默认值或上次投标回填"
        collapsible
        :default-open="false"
      >
        <template #summary>
          {{ form.bidderName || '—' }} · {{ form.foundedDate || '成立日期未填' }} · {{ form.bidderPhone || '电话未填' }}
        </template>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label class="md:col-span-2 text-[12px] space-y-1">
            <span class="text-muted-foreground">投标人全称<span class="tender-req" aria-hidden="true">*</span></span>
            <input v-model="form.bidderName" class="kb-input font-sans" required aria-required="true" />
          </label>
          <label class="md:col-span-2 text-[12px] space-y-1">
            <span class="text-muted-foreground">地址<span class="tender-req" aria-hidden="true">*</span></span>
            <input v-model="form.bidderAddress" class="kb-input font-sans" required aria-required="true" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">单位性质</span>
            <input v-model="form.bidderNature" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">成立日期<span class="tender-req" aria-hidden="true">*</span></span>
            <input
              v-model="form.foundedDate"
              class="kb-input font-sans"
              required
              aria-required="true"
              placeholder="如 2017年07月"
            />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">电话<span class="tender-req" aria-hidden="true">*</span></span>
            <input v-model="form.bidderPhone" class="kb-input font-sans" required aria-required="true" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">邮箱</span>
            <input v-model="form.bidderEmail" class="kb-input font-sans" />
          </label>
        </div>
      </Panel>

      <Panel
        title="法定代表人 / 授权"
        :subtitle="authPanelSubtitle"
        collapsible
        :default-open="form.authEnabled || form.authNeed === 'required'"
      >
        <template #action>
          <label class="inline-flex items-center gap-1.5 text-[12px] text-foreground whitespace-nowrap">
            <input v-model="form.authEnabled" type="checkbox" class="accent-current" />
            启用授权委托
          </label>
        </template>
        <template #summary>
          {{ form.legalPersonName || '法人未填' }} · {{ form.legalPersonAge ? `${form.legalPersonAge}岁` : '年龄未填' }} ·
          {{ form.legalPersonIdNo || '身份证未填' }}
          <template v-if="form.authEnabled"> · 授权委托已开</template>
        </template>
        <div class="mb-3">
          <label class="text-[12px] space-y-1 block">
            <span class="text-muted-foreground">
              本标法定代表人
              <span v-if="legalPersonOptions.length > 1" class="tender-req" aria-hidden="true">*</span>
            </span>
            <select
              v-model="form.legalPersonKey"
              class="kb-input font-sans"
              @change="applyLegalPerson"
            >
              <option value="">
                {{ legalPersonOptions.length ? '请选择资料库中的身份证' : '资料库还没有识别出身份证' }}
              </option>
              <option v-for="person in legalPersonOptions" :key="person.key" :value="person.key">
                {{ person.name || '未识别姓名' }} · {{ person.idNo }}{{ person.hasBack ? '' : '（缺反面）' }}
              </option>
            </select>
          </label>
          <p class="text-[11px] text-muted-foreground mt-1.5 leading-relaxed">
            <template v-if="!legalPersonOptions.length">
              请到投标资料库「法定代表人身份证正反面」上传正面和反面。同一人先传正面再传反面，可放多位，这里再选本标用谁。
            </template>
            <template v-else-if="legalPickMissing">
              有多位法人证件。选定后填入姓名、性别、年龄、身份证号，生成时只贴这一人的正反面。
            </template>
            <template v-else>
              生成时只贴所选这位的身份证。职务仍在下面手填，证上没有。
            </template>
            <button
              v-if="canEditLibrary"
              type="button"
              class="text-foreground underline underline-offset-2 ml-0.5"
              @click="router.push('/tender-library')"
            >
              去资料库上传
            </button>
          </p>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">法人姓名<span class="tender-req" aria-hidden="true">*</span></span>
            <input v-model="form.legalPersonName" class="kb-input font-sans" required aria-required="true" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">性别</span>
            <input v-model="form.legalPersonGender" class="kb-input font-sans" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">年龄<span class="tender-req" aria-hidden="true">*</span></span>
            <input v-model="form.legalPersonAge" class="kb-input font-sans" required aria-required="true" />
          </label>
          <label class="text-[12px] space-y-1">
            <span class="text-muted-foreground">职务</span>
            <input v-model="form.legalPersonTitle" class="kb-input font-sans" />
          </label>
          <label class="md:col-span-2 text-[12px] space-y-1">
            <span class="text-muted-foreground">法人身份证号<span class="tender-req" aria-hidden="true">*</span></span>
            <input v-model="form.legalPersonIdNo" class="kb-input font-sans" required aria-required="true" />
          </label>
          <template v-if="form.authEnabled">
            <label class="text-[12px] space-y-1">
              <span class="text-muted-foreground">
                委托代理人
                <span class="tender-req" aria-hidden="true">*</span>
              </span>
              <input
                v-model="form.agentName"
                class="kb-input font-sans"
                required
                aria-required="true"
                placeholder="必填，优先从资料库回填"
              />
            </label>
            <label class="text-[12px] space-y-1">
              <span class="text-muted-foreground">
                代理人身份证号
                <span class="tender-req" aria-hidden="true">*</span>
              </span>
              <input
                v-model="form.agentIdNo"
                class="kb-input font-sans"
                required
                aria-required="true"
                placeholder="必填"
              />
            </label>
          </template>
        </div>
        <p v-if="form.authEnabled" class="text-[11px] text-muted-foreground mt-2 leading-relaxed">
          {{
            agentSlotReady
              ? '资料库已有委托人身份证，将写入授权委托书'
              : '请填写委托人，并在下方资料区上传或确认身份证扫描件'
          }}
        </p>
      </Panel>

      <div class="tender-card">
        <div class="flex items-center gap-3 mb-3">
          <div
            class="relative size-10 shrink-0 rounded-full border-2 flex items-center justify-center text-[10px] font-mono font-medium"
            :class="slotFilledCount > 0 ? 'border-patina/50 text-patina' : 'border-border text-muted-foreground'"
          >
            {{ slotFilledCount }}/{{ placeholderItems.length }}
          </div>
          <p class="text-[11px] text-muted-foreground leading-relaxed">
            未上传的商务标 / 技术资料会在 Word 里用虚线框占位，不阻止生成。补齐扫描件后可重新生成。
            <button
              v-if="canEditLibrary"
              type="button"
              class="text-foreground underline underline-offset-2 ml-0.5"
              @click="router.push('/tender-library')"
            >
              去资料库
            </button>
          </p>
        </div>

        <div class="mt-3 rounded-lg border border-border/60 overflow-hidden">
          <button
            type="button"
            class="w-full flex items-center justify-between gap-2 px-3 py-2.5 text-[11px] hover:bg-accent/30 transition-colors"
            @click="slotsExpanded = !slotsExpanded"
          >
            <span class="inline-flex items-center gap-1.5 text-muted-foreground">
              <FolderOpen class="size-3.5" />
              本标附件
            </span>
            <span class="inline-flex items-center gap-1 shrink-0 text-foreground/80">
              {{ slotsExpanded ? '收起' : '展开' }}
              <ChevronUp v-if="slotsExpanded" class="size-3.5" />
              <ChevronDown v-else class="size-3.5" />
            </span>
          </button>
          <ul
            v-if="slotsExpanded"
            class="border-t border-border/60 px-2 pb-2 pt-1 space-y-1.5 max-h-[280px] overflow-y-auto text-[11px]"
          >
            <li
              v-for="slot in placeholderItems"
              :key="slot.key || slot.title"
              class="flex items-start gap-2 rounded border border-border/60 px-2 py-1.5"
            >
              <div class="min-w-0 flex-1">
                <div class="text-foreground/90">
                  <span v-if="slot.fileCount" class="text-emerald-700 dark:text-emerald-400">【已补】</span>
                  <span v-else class="text-sulfur">【待补】</span>
                  {{ slot.title }}
                  <span
                    v-if="isRequiredSlot(slot.key)"
                    class="ml-1 text-[10px] text-sulfur"
                  >{{ HIGH_DISQUALIFY_KEYS.has(slot.key) ? '建议补' : '本标附件' }}</span>
                  <span
                    v-else-if="slotVolume(slot) === 'technical'"
                    class="ml-1 text-[10px] text-muted-foreground"
                  >技术标·扣分</span>
                  <span v-else class="ml-1 text-[10px] text-muted-foreground">可选</span>
                </div>
                <div v-if="slotFileLabel(slot)" class="mt-0.5 truncate text-[10px] text-muted-foreground">
                  {{ slotFileLabel(slot) }}
                </div>
              </div>
              <div class="flex shrink-0 items-center gap-1">
                <template v-if="canEditLibrary">
                  <button
                    type="button"
                    class="h-6 rounded border border-border px-1.5 text-[10px] hover:bg-accent disabled:opacity-50"
                    :disabled="uploadingKey === slot.key || !slot.key"
                    @click="pickSlotFile(slot, true)"
                  >
                    {{ uploadingKey === slot.key ? '…' : slot.fileCount ? '覆盖' : '本标补充' }}
                  </button>
                  <button
                    v-if="slot.fileCount"
                    type="button"
                    class="h-6 rounded border border-border px-1.5 text-[10px] hover:bg-accent disabled:opacity-50"
                    :disabled="uploadingKey === slot.key || !slot.key"
                    @click="pickSlotFile(slot, false)"
                  >
                    追加
                  </button>
                  <button
                    v-if="slot.fileCount"
                    type="button"
                    class="h-6 rounded border border-border px-1.5 text-[10px] hover:bg-accent disabled:opacity-50"
                    :disabled="uploadingKey === slot.key"
                    @click="onClearSlot(slot)"
                  >
                    清除
                  </button>
                </template>
                <button
                  v-if="!isRequiredSlot(slot.key)"
                  type="button"
                  class="h-6 rounded border border-border px-1.5 text-[10px] hover:bg-accent"
                  @click="excludeOptionalSlot(slot)"
                >
                  移出
                </button>
              </div>
            </li>
            <li
              v-if="optionalCatalogSlots.length"
              class="pt-1 text-[10px] text-muted-foreground"
            >
              资料库其他项（可选纳入本标）
            </li>
            <li
              v-for="slot in optionalCatalogSlots"
              :key="'opt-' + slot.key"
              class="flex items-start gap-2 rounded border border-dashed border-border/60 px-2 py-1.5"
            >
              <div class="min-w-0 flex-1 text-muted-foreground">
                {{ slot.title }}
                <span v-if="slot.fileCount" class="ml-1 text-emerald-700 dark:text-emerald-400">已有扫描件</span>
              </div>
              <button
                type="button"
                class="h-6 shrink-0 rounded border border-border px-1.5 text-[10px] hover:bg-accent"
                @click="includeOptionalSlot(slot)"
              >
                纳入本标
              </button>
            </li>
          </ul>
        </div>

        <label class="flex items-start gap-2.5 text-[12px] mt-4">
          <input v-model="form.attachQualifications" type="checkbox" class="mt-0.5" />
          <span class="text-muted-foreground leading-relaxed">
            插入企业资质 PDF（较慢，建议装订时另附）
            <span class="block text-[11px] mt-0.5">
              {{
                qualification?.found
                  ? `已就绪（${Math.round((qualification.sizeBytes || 0) / 1024 / 1024)} MB）；勾选后只嵌入前若干页`
                  : '未找到时将提示补附'
              }}
            </span>
          </span>
        </label>
        <p class="mt-2 text-[11px] text-muted-foreground leading-relaxed">
          法人/委托人身份证正反面会贴在对应格式页内，不再重复出现在文末附件；合同、财税等大 PDF 仍用虚线框占位，装订时从资料库打印原件。
        </p>
      </div>
      </div>

      <div
        v-if="activeVolume === 'technical'"
        id="tender-volume-technical"
        class="space-y-4"
      >
      <div class="rounded-lg border border-border/70 bg-card px-3 py-2.5">
        <div class="flex flex-wrap items-baseline gap-2">
          <h2 class="text-[13px] font-semibold">技术标</h2>
          <span class="text-[11px] text-muted-foreground">废标风险较低，但不响应技术要求会大量扣分</span>
        </div>
        <p class="mt-1 text-[11px] text-muted-foreground leading-relaxed">
          按本标邀请书技术章起草草稿，改完再出 Word。缺图纸会在 Word 里画虚线框，不阻止生成。
        </p>
      </div>

      <Panel
        title="技术偏差表"
        subtitle="条款来自邀请书。对不上或未人工确认的标待确认，不自动写无偏差"
      >
        <div class="flex items-center justify-between mb-2">
          <p class="text-[12px] text-muted-foreground">
            {{ form.deviationLines.length ? `${form.deviationLines.length} 条` : '尚未填写' }}
          </p>
          <div class="flex items-center gap-1.5">
            <button
              type="button"
              class="h-7 px-2 rounded-md border border-border text-[11px] hover:bg-accent"
              :disabled="!form.quoteLines.length"
              @click="fillDeviationFromQuote()"
            >
              用报价对照
            </button>
            <button
              type="button"
              class="h-7 px-2 rounded-md border border-border text-[11px] hover:bg-accent"
              @click="addDeviationRow"
            >
              增行
            </button>
          </div>
        </div>
        <div class="overflow-x-auto border border-border rounded-md">
          <table class="w-full text-[11px] border-collapse min-w-[640px]">
            <thead>
              <tr class="text-muted-foreground bg-accent/40">
                <th
                  v-for="(h, i) in deviationPreviewHeaders"
                  :key="'dh-' + i"
                  class="px-2 py-1.5 text-left font-medium"
                >
                  {{ h }}
                </th>
                <th class="tender-op-col px-2 py-1.5 text-center font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!form.deviationLines.length">
                <td :colspan="deviationPreviewHeaders.length + 1" class="px-2 py-3 text-muted-foreground">
                  识别邀请书后会出现条款。不要用报价清单充当招标要求，可手工增行。
                </td>
              </tr>
              <tr v-for="(row, i) in form.deviationLines" :key="i" class="border-t border-border">
                <td
                  v-for="(role, j) in deviationPreviewRoles"
                  :key="'dc-' + i + '-' + j"
                  class="px-1 py-1"
                  :class="role === 'requirement' || role === 'response' ? 'min-w-[160px]' : ''"
                >
                  <input
                    v-if="deviationFieldForRole(role)"
                    v-model="row[deviationFieldForRole(role) as 'seq' | 'requirement' | 'response' | 'deviation']"
                    class="kb-input font-sans !py-1 !px-1"
                  />
                  <span v-else class="text-muted-foreground">—</span>
                </td>
                <td class="tender-op-col px-1.5 py-1 text-center">
                  <button
                    type="button"
                    class="tender-row-del"
                    title="删除本行"
                    @click="removeDeviationRow(i)"
                  >
                    <Trash2 class="size-3" />
                    删除
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="商务响应起草" subtitle="对招标商务条款逐项生成偏离表及既有承诺章节正文，缺少证据标【待补充】">
        <label class="flex items-center gap-2 text-[12px]">
          <input v-model="form.autoDraftBizResp" type="checkbox" @change="draftPreference.business = form.autoDraftBizResp" />生成商务标或综合标时起草商务响应
        </label>
      </Panel>

      <Panel title="技术章节草稿" subtitle="按邀请书目录分章写，缺事实会标【待补充】。有历史技术标知识库时会参考写法">
        <div class="mb-3 space-y-2 text-[12px]">
          <label class="flex items-center gap-2">
            <input v-model="form.autoDraftTechPlan" type="checkbox" @change="draftPreference.technical = form.autoDraftTechPlan" />生成技术标或综合标时为现有空章节起草正文
          </label>
          <textarea v-model="form.techPlanNote" rows="3" class="kb-input font-sans w-full" placeholder="本项目技术路线、施工部署、人员设备等要点（优先采用）" />
          <label class="block">
            <span>上传本项目方案资料（docx/pdf）</span>
            <input type="file" accept=".docx,.pdf" :disabled="uploadingTechSource" class="block mt-1" @change="onTechSourceChange" />
          </label>
          <p v-if="techSourceNames.length" class="text-muted-foreground">已加入：{{ techSourceNames.join('、') }}</p>
        </div>
        <div class="flex items-center justify-between mb-2">
          <p class="text-[12px] text-muted-foreground">
            {{ techChapterItems.length ? `${techChapterItems.length} 章` : '识别邀请书后出现，也可直接起草补空章' }}
          </p>
          <button
            type="button"
            class="h-7 px-2 rounded-md border border-border text-[11px] hover:bg-accent disabled:opacity-50"
            :disabled="draftingTech || Boolean(redraftingId)"
            @click="onDraftTech()"
          >
            {{ draftingTech && !redraftingId ? '起草中…' : '生成技术标草稿' }}
          </button>
        </div>
        <div v-if="!techChapterItems.length" class="text-[11px] text-muted-foreground py-2">
          还没有技术章。识别带技术目录的邀请书，或点上面按钮补「技术方案」。
        </div>
        <div v-else class="space-y-3">
          <div
            v-for="item in techChapterItems"
            :key="item.id || item.title"
            class="rounded-md border border-border/70 px-2.5 py-2 space-y-1.5"
          >
            <div class="flex items-center justify-between gap-2">
              <p class="text-[12px] font-medium">
                {{ isOrgChapter(item) ? '上传工程量清单' : item.title }}
              </p>
              <button
                v-if="!isOrgChapter(item)"
                type="button"
                class="h-6 px-1.5 rounded border border-border text-[11px] hover:bg-accent disabled:opacity-50"
                :disabled="draftingTech || Boolean(redraftingId)"
                @click="onDraftTech(item.id)"
              >
                {{ redraftingId === item.id ? '重写中…' : '重写本章' }}
              </button>
            </div>
            <div
              v-if="isOrgChapter(item)"
              class="tender-dropzone w-full cursor-pointer"
              :class="{ 'tender-dropzone--active': dragOverBoqId === item.id }"
              @click="pickBoq(item)"
              @dragover.prevent="dragOverBoqId = item.id || ''"
              @dragleave.prevent="dragOverBoqId = ''"
              @drop.prevent="onBoqDrop(item, $event)"
            >
              <FileSpreadsheet class="size-6 text-muted-foreground/70 mb-1" />
              <p class="text-[13px] font-medium">
                {{ boqBusyId === item.id ? '正在识别工程量清单…' : '点击或拖拽上传工程量清单' }}
              </p>
              <p class="text-[11px] text-muted-foreground mt-1">
                Excel / Word / CSV / PDF。识别后填入下方，不写入商务标报价。
              </p>
            </div>
            <button
              v-if="item.inviteExcerpt || item.body"
              type="button"
              class="text-[11px] text-muted-foreground hover:text-foreground"
              @click="openTechExcerpt = openTechExcerpt === item.id ? '' : item.id"
            >
              {{ openTechExcerpt === item.id ? '收起招标原文' : '招标原文' }}
            </button>
            <p
              v-if="openTechExcerpt === item.id && (item.inviteExcerpt || item.body)"
              class="text-[11px] text-muted-foreground whitespace-pre-wrap max-h-32 overflow-auto"
            >
              {{ item.inviteExcerpt || item.body }}
            </p>
            <textarea
              v-model="item.draftBody"
              rows="5"
              class="kb-input font-sans min-h-[96px]"
              placeholder="本章草稿。可点「重写本章」或直接改。"
              @input="item.draftStatus = 'edited'"
            />
          </div>
        </div>
      </Panel>

      <Panel title="上传图纸" subtitle="只收 PDF。识别文字写入技术标，页图仍会贴上。换一份邀请书请重新上传">
        <div class="space-y-3">
          <div class="space-y-1.5">
            <div
              class="tender-dropzone w-full cursor-pointer"
              :class="{
                'tender-dropzone--active': dragOverDrawings,
                'tender-dropzone--filled': techDrawingSlot.fileCount > 0,
              }"
              @click="pickDrawingPdf(!techDrawingSlot.fileCount)"
              @dragover.prevent="dragOverDrawings = true"
              @dragleave.prevent="dragOverDrawings = false"
              @drop.prevent="onDrawingPdfDrop"
            >
              <ImagePlus class="size-7 text-muted-foreground/70 mb-2" />
              <p class="text-[13px] font-medium text-foreground">
                {{
                  drawingBusy
                    ? '正在识别图纸…'
                    : techDrawingSlot.fileCount
                      ? `${techDrawingSlot.fileCount} 份图纸 PDF`
                      : '点击或拖拽上传图纸 PDF'
                }}
              </p>
              <p class="text-[11px] text-muted-foreground mt-1">
                {{
                  techDrawingSlot.fileCount
                    ? slotFileLabel(techDrawingSlot) || '点击可继续追加'
                    : '仅 PDF，用于本次技术标，不进投标资料库'
                }}
              </p>
            </div>
            <textarea
              v-if="drawingFill"
              v-model="drawingFill"
              rows="4"
              class="kb-input font-sans min-h-[80px]"
              placeholder="图纸识别结果。可直接改，生成技术标时写入。"
            />
            <div v-if="techDrawingSlot.fileCount" class="flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                class="h-7 rounded-md border border-border px-2 text-[11px] hover:bg-accent disabled:opacity-50"
                :disabled="drawingBusy"
                @click.stop="pickDrawingPdf(true)"
              >
                覆盖
              </button>
              <button
                type="button"
                class="h-7 rounded-md border border-border px-2 text-[11px] hover:bg-accent disabled:opacity-50"
                :disabled="drawingBusy"
                @click.stop="pickDrawingPdf(false)"
              >
                追加
              </button>
              <button
                type="button"
                class="h-7 rounded-md border border-border px-2 text-[11px] hover:bg-accent disabled:opacity-50"
                :disabled="drawingBusy"
                @click.stop="onClearSlot(techDrawingSlot)"
              >
                清除
              </button>
            </div>
          </div>
        </div>
      </Panel>
      </div>

      <div
        v-if="softGenerateNotes.length"
        class="rounded-md border border-border bg-accent/30 px-3 py-2.5"
      >
        <p class="text-[12px] font-medium mb-1">缺项用虚线框占位，不阻止生成</p>
        <ul class="space-y-0.5 text-[11px] text-muted-foreground">
          <li v-for="(w, i) in softGenerateNotes" :key="i">{{ w }}</li>
        </ul>
      </div>

      <footer class="tender-step-footer">
        <button type="button" class="tender-ghost-btn" @click="onStepBack">
          <ChevronLeft class="size-3.5" />
          上一步
        </button>
        <button
          type="button"
          class="tender-ghost-btn"
          :disabled="generateButtonDisabled"
          @click="onGenerateBoth"
        >
          同时生成两卷
        </button>
        <div
          class="tender-generate-wrap"
          :class="{ 'tender-generate-wrap--hint': !!generateButtonTooltip }"
        >
          <button
            type="button"
            class="tender-primary-btn min-w-[180px]"
            :disabled="generateButtonDisabled"
            :aria-describedby="generateButtonTooltip ? 'tender-form-next-tip' : undefined"
            @click="onGenerateCurrent"
          >
            <Loader2 v-if="generating" class="size-3.5 animate-spin" />
            <ChevronRight v-else class="size-3.5" />
            {{ formStepNextLabel }}
          </button>
          <p
            v-if="generateButtonTooltip"
            id="tender-form-next-tip"
            class="tender-generate-tooltip"
            role="tooltip"
          >
            {{ generateButtonTooltip }}
          </p>
        </div>
      </footer>
      <p class="mt-1 text-center text-[11px] text-muted-foreground">
        {{ volumeStatusHint }}
      </p>
      <p v-if="generating" class="mt-2 text-[11px] text-muted-foreground text-center">
        {{
          effectiveGenerationMode === 'full'
            ? '正在生成投标文件…'
            : effectiveGenerationMode === 'split'
              ? '正在按分册生成两卷…'
              : activeVolume === 'technical'
                ? '正在生成技术标 Word…'
                : form.attachQualifications
                  ? '正在写入商务标（资质已限页加速）…'
                  : '正在生成商务标 Word…'
        }}
      </p>
    </section>

    <!-- 步骤 3：在线预览 -->
    <section v-else-if="currentStepIndex === 2 && result" class="tender-step-panel tender-step-panel--preview">
      <div class="tender-preview-shell" :class="{ 'tender-preview-shell--fs': previewFullscreen }">
        <div class="tender-card-head shrink-0 pb-3">
          <div class="min-w-0">
            <h2 class="text-[14px] font-semibold text-foreground">Word 在线改稿</h2>
            <p class="text-[11px] text-muted-foreground mt-0.5">
              可直接修改。停笔后自动保存，离开本步也会再存一次。自检、下载和工作任务都用这份稿。
            </p>
          </div>
          <div class="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
            <div
              v-if="effectiveGenerationMode !== 'full' && result.generateVolume !== 'full' && result.docxFile && result.techDocxFile"
              class="tender-volume-switch"
              role="tablist"
              aria-label="预览商务标或技术标"
            >
              <button
                type="button"
                role="tab"
                :aria-selected="previewVolume === 'business'"
                class="tender-volume-btn"
                :class="{ 'tender-volume-btn--active': previewVolume === 'business' }"
                :disabled="bidSaveBusy"
                @click="switchPreviewVolume('business')"
              >
                商务标
              </button>
              <button
                type="button"
                role="tab"
                :aria-selected="previewVolume === 'technical'"
                class="tender-volume-btn"
                :class="{ 'tender-volume-btn--active': previewVolume === 'technical' }"
                :disabled="bidSaveBusy"
                @click="switchPreviewVolume('technical')"
              >
                技术标
              </button>
            </div>
            <button
              v-if="(effectiveGenerationMode === 'full' || result.generateVolume === 'full') && result.docxFile"
              type="button"
              class="tender-ghost-btn"
              :disabled="downloading === 'docx'"
              @click="onDownload('docx')"
            >
              <Loader2 v-if="downloading === 'docx'" class="size-3.5 animate-spin" />
              <Download v-else class="size-3.5" />
              下载综合标
            </button>
            <button
              v-if="effectiveGenerationMode !== 'full' && result.generateVolume !== 'full' && result.docxFile"
              type="button"
              class="tender-ghost-btn"
              :disabled="downloading === 'docx'"
              @click="onDownload('docx')"
            >
              <Loader2 v-if="downloading === 'docx'" class="size-3.5 animate-spin" />
              <Download v-else class="size-3.5" />
              商务标
            </button>
            <button
              v-if="effectiveGenerationMode !== 'full' && result.techDocxFile"
              type="button"
              class="tender-ghost-btn"
              :disabled="downloading === 'tech'"
              @click="onDownload('tech')"
            >
              <Loader2 v-if="downloading === 'tech'" class="size-3.5 animate-spin" />
              <Download v-else class="size-3.5" />
              技术标
            </button>
            <button
              type="button"
              class="tender-ghost-btn"
              :title="previewFullscreen ? '还原窗口（Esc）' : '全屏预览'"
              :aria-label="previewFullscreen ? '还原窗口' : '全屏预览'"
              @click="togglePreviewFullscreen"
            >
              <Minimize2 v-if="previewFullscreen" class="size-3.5" />
              <Maximize2 v-else class="size-3.5" />
              {{ previewFullscreen ? '还原' : '全屏' }}
            </button>
          </div>
        </div>

        <p
          v-if="bidSaveBusy || error"
          class="shrink-0 rounded-md border border-sulfur/30 bg-sulfur/10 px-3 py-2 text-[12px] text-sulfur"
          :role="bidSaveBusy ? 'status' : 'alert'"
        >
          {{ bidSaveBusy ? '正在保存，请稍候' : error }}
        </p>

        <div
          v-if="generateShownWarnings.length && !previewFullscreen"
          class="tender-preview-hints shrink-0 rounded-md border border-sulfur/30 bg-sulfur/10"
        >
          <button
            type="button"
            class="flex w-full items-center gap-2 px-3 py-1.5 text-left"
            :aria-expanded="previewHintsOpen"
            @click="togglePreviewHints"
          >
            <TriangleAlert class="size-3.5 shrink-0 text-sulfur" />
            <span class="min-w-0 flex-1 text-[12px] font-medium text-sulfur">
              生成提示
              <span class="font-normal text-sulfur/80">（{{ generateHintsSummary }}）</span>
              <span
                v-if="!previewHintsOpen"
                class="ml-1 font-normal text-sulfur/70"
              >已折叠，避免挡住 Word</span>
            </span>
            <ChevronUp v-if="previewHintsOpen" class="size-3.5 shrink-0 text-sulfur" />
            <ChevronDown v-else class="size-3.5 shrink-0 text-sulfur" />
            <span class="shrink-0 text-[11px] text-sulfur/80">{{
              previewHintsOpen ? '收起' : '展开'
            }}</span>
          </button>
          <ul
            v-if="previewHintsOpen"
            class="tender-preview-hints-list space-y-1 border-t border-sulfur/20 px-3 py-2 text-[11px] text-sulfur"
          >
            <li v-for="(w, i) in generateShownWarnings" :key="i" class="flex gap-1.5">
              <TriangleAlert class="size-3.5 shrink-0 mt-0.5" />
              {{ w }}
            </li>
          </ul>
        </div>

        <TenderDocEditor
          ref="bidEditorRef"
          :key="`${previewDoc.file}-edit`"
          class="tender-preview-editor"
          :docx-file="previewDoc.file"
          :download-name="previewDoc.name"
          :fullscreen="previewFullscreen"
          :layout-tick="previewLayoutTick"
          mode="edit"
          engine="onlyoffice"
        />

        <footer class="tender-step-footer tender-step-footer--preview shrink-0 pt-2">
          <button type="button" class="tender-ghost-btn" @click="onStepBack">
            <ChevronLeft class="size-3.5" />
            上一步
          </button>
          <button
            type="button"
            class="tender-ghost-btn"
            :disabled="bidSaveBusy"
            @click="leavePreviewToTasks"
          >
            去工作任务
          </button>
          <button
            type="button"
            class="tender-primary-btn min-w-[160px]"
            :disabled="bidSaveBusy"
            @click="onPreviewStepNext"
          >
            {{ bidSaveBusy ? '保存中…' : '去 AI 自检' }}
            <ChevronRight class="size-3.5" />
          </button>
        </footer>
      </div>
    </section>

    <!-- 步骤 4：AI 自检 -->
    <section v-else-if="currentStepIndex === 3 && result" class="tender-step-panel space-y-4">
      <TenderQaPanel
        page
        :title="qaVolume === 'full' ? 'AI 自检 · 完整投标文件（综合标）' : previewVolume === 'technical' ? 'AI 自检 · 技术标' : 'AI 自检 · 商务标'"
        :report="qaReport"
        :loading="qaLoading"
        :error="qaError"
        @run="runTenderQa"
      />
      <footer class="tender-step-footer">
        <button type="button" class="tender-ghost-btn" @click="onStepBack">
          <ChevronLeft class="size-3.5" />
          返回预览
        </button>
        <button
          type="button"
          class="tender-ghost-btn"
          @click="router.push('/work-tasks')"
        >
          去工作任务
        </button>
        <button
          type="button"
          class="tender-primary-btn min-w-[160px]"
          :disabled="publishing"
          @click="onQaStepNext"
        >
          <Loader2 v-if="publishing" class="size-3.5 animate-spin" />
          {{ publishing ? '写入记录中…' : '去下载定稿' }}
          <ChevronRight v-if="!publishing" class="size-3.5" />
        </button>
      </footer>
    </section>

    <!-- 步骤 5：下载定稿 -->
    <section v-else-if="currentStepIndex === 4 && result" class="tender-step-panel space-y-4">
      <section class="tender-card tender-card--success">
        <div class="tender-card-head border-b-0 pb-0">
          <h2 class="text-[14px] font-semibold text-patina">投标文件已就绪</h2>
          <p class="text-[11px] text-muted-foreground mt-0.5">
            左上角选商务标只出商务标，选技术标只出技术标。同一任务里两本可以分开生成、分别下载。
          </p>
        </div>
        <ul v-if="generateShownWarnings.length" class="mt-3 space-y-1 text-[11px] text-sulfur">
          <li v-for="(w, i) in generateShownWarnings" :key="i" class="flex gap-1.5">
            <TriangleAlert class="size-3.5 shrink-0 mt-0.5" />
            {{ w }}
          </li>
        </ul>
        <div class="flex flex-col sm:flex-row gap-2 mt-4">
          <button
            type="button"
            class="tender-ghost-btn flex-1 justify-center h-10"
            @click="router.push('/work-tasks')"
          >
            去工作任务
          </button>
          <button
            v-if="result.docxFile"
            type="button"
            class="tender-primary-btn flex-1 h-10"
            :disabled="downloading === 'docx'"
            @click="onDownload('docx')"
          >
            <Loader2 v-if="downloading === 'docx'" class="size-3.5 animate-spin" />
            <Download v-else class="size-3.5" />
            下载{{ effectiveGenerationMode === 'full' || result.generateVolume === 'full' ? '综合标' : '商务标' }}
          </button>
          <button
            v-if="result.techDocxFile"
            type="button"
            class="tender-primary-btn flex-1 h-10"
            :disabled="downloading === 'tech'"
            @click="onDownload('tech')"
          >
            <Loader2 v-if="downloading === 'tech'" class="size-3.5 animate-spin" />
            <Download v-else class="size-3.5" />
            下载技术标
          </button>
          <button
            v-if="result.pdfFile"
            type="button"
            class="tender-ghost-btn flex-1 justify-center h-10"
            :disabled="downloading === 'pdf'"
            @click="onDownload('pdf')"
          >
            <Loader2 v-if="downloading === 'pdf'" class="size-3.5 animate-spin" />
            <Download v-else class="size-3.5" />
            下载资质 PDF
          </button>
        </div>
      </section>

      <Panel title="表单摘要" subtitle="本次生成所用关键字段">
        <dl class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2 text-[12px]">
          <div class="sm:col-span-2">
            <dt class="text-[11px] text-muted-foreground">项目</dt>
            <dd class="mt-0.5 text-foreground/90">{{ form.projectName || '—' }}</dd>
          </div>
          <div>
            <dt class="text-[11px] text-muted-foreground">招标人</dt>
            <dd class="mt-0.5">{{ form.tenderer || '—' }}</dd>
          </div>
          <div>
            <dt class="text-[11px] text-muted-foreground">投标总价</dt>
            <dd class="mt-0.5 font-sans">{{ moneyLabel }} 元</dd>
          </div>
          <div>
            <dt class="text-[11px] text-muted-foreground">法人</dt>
            <dd class="mt-0.5">{{ form.legalPersonName || '—' }}</dd>
          </div>
          <div class="sm:col-span-2">
            <dt class="text-[11px] text-muted-foreground">投标内容</dt>
            <dd class="mt-0.5 line-clamp-2 text-muted-foreground">{{ form.bidContent || '—' }}</dd>
          </div>
          <div>
            <dt class="text-[11px] text-muted-foreground">原厂角色</dt>
            <dd class="mt-0.5">{{ form.factoryRole || '（按项目自动）' }}</dd>
          </div>
          <div>
            <dt class="text-[11px] text-muted-foreground">商务标 / 技术标</dt>
            <dd class="mt-0.5">
              {{ form.performanceLines.length }} 条业绩 · {{ form.deviationLines.length }} 条偏差
              · {{ techChapterItems.filter((i) => i.draftBody).length }}/{{ techChapterItems.length || 0 }} 章已起草
              · 图纸 {{ techDrawingSlot.fileCount }} 张
            </dd>
          </div>
        </dl>
      </Panel>

      <div
        v-if="result.attachmentMatch && (result.attachmentMatch.matched.length || result.attachmentMatch.missingFiles.length || result.attachmentMatch.createdItems.length)"
        class="tender-card"
      >
        <p class="text-[12px] font-medium mb-1">本标附件对照</p>
        <p v-if="result.attachmentMatch.matched.length" class="text-[11px] text-emerald-700 dark:text-emerald-400">
          已写入扫描件 {{ result.attachmentMatch.matched.length }} 项
        </p>
        <p v-if="result.attachmentMatch.missingFiles.length" class="text-[11px] text-muted-foreground">
          未上传 {{ result.attachmentMatch.missingFiles.map((x) => x.title).join('、') }}
        </p>
        <p v-if="result.attachmentMatch.createdItems.length" class="text-[11px] text-muted-foreground">
          资料库新建空项 {{ result.attachmentMatch.createdItems.map((x) => x.title).join('、') }}
        </p>
      </div>

      <footer class="tender-step-footer">
        <button type="button" class="tender-ghost-btn" @click="onStepBack">
          <ChevronLeft class="size-3.5" />
          返回 AI 自检
        </button>
        <button type="button" class="tender-ghost-btn" @click="goToStep(1)">
          修改表单
        </button>
      </footer>
    </section>
  </div>

  <AppDialog
    :open="quotePickerOpen"
    size="wide"
    title="从 AI 报价记录选择"
    description="只列出已签认锁定的报价。多条会合并成一张清单，与本地上传二选一。"
    @update:open="(open) => { quotePickerOpen = open }"
  >
    <div class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <input
          v-model="quotePickerQuery"
          class="h-8 min-w-[200px] flex-1 rounded-md border border-border bg-background px-2 text-[12px]"
          placeholder="搜索项目 / 价目库"
          @keydown.enter.prevent="loadQuotePicker"
        />
        <button
          type="button"
          class="tender-ghost-btn"
          :disabled="quotePickerLoading"
          @click="loadQuotePicker"
        >
          <Loader2 v-if="quotePickerLoading" class="size-3.5 animate-spin" />
          查询
        </button>
      </div>
      <div
        v-if="quotePickerLoading && !quotePickerRows.length"
        class="py-6 text-center text-[12px] text-muted-foreground"
      >
        加载中…
      </div>
      <p
        v-else-if="!quotePickerRows.length"
        class="py-6 text-center text-[12px] text-muted-foreground"
      >
        暂无已签认的报价记录。请先到「AI报价智能体」生成 Excel 并点签认锁定。
      </p>
      <ul v-else class="max-h-[50vh] divide-y divide-border overflow-y-auto rounded-md border border-border">
        <li v-for="row in quotePickerRows" :key="row.id">
          <label class="flex cursor-pointer items-start gap-2.5 px-3 py-2.5 hover:bg-accent/40">
            <input
              type="checkbox"
              class="mt-1"
              :checked="quotePickerDraft.includes(row.id)"
              @change="toggleQuotePickerId(row.id)"
            />
            <span class="min-w-0 flex-1">
              <span class="block text-[13px] font-medium truncate">{{ row.projectName || '未命名项目' }}</span>
              <span class="mt-0.5 block text-[11px] text-muted-foreground">
                {{ row.lineCount }} 项 ·
                {{ row.totalExTax.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }} 元
                <template v-if="row.baseName"> · {{ row.baseName }}</template>
                · {{ formatQuotePickTime(row.createdAt) }}
              </span>
            </span>
          </label>
        </li>
      </ul>
      <p v-if="quotePickerDraft.length" class="text-[11px] text-muted-foreground">
        已勾选 {{ quotePickerDraft.length }} 条
      </p>
    </div>
    <template #footer>
      <button type="button" class="tender-ghost-btn" @click="quotePickerOpen = false">取消</button>
      <button
        type="button"
        class="tender-primary-btn"
        :disabled="!quotePickerDraft.length"
        @click="confirmQuotePicker"
      >
        确认引用
      </button>
    </template>
  </AppDialog>

  <AppAlertDialog
    :open="confirmDateOpen"
    title="确认投标日期"
    :description="`投标日期为 ${form.bidDate}，与今天相差不少于 1 天。函件、封面、授权书都会用这个日期，确认按该日期生成？`"
    confirm-label="按该日期生成"
    :loading="generating"
    @update:open="onConfirmDateOpen"
    @confirm="confirmBidDate"
  />

  <AppDialog
    :open="formatPreviewOpen"
    size="xl"
    title="预览模板"
    :description="formatPreviewMessage || `从上传的 Word 里截出的「${form.outlineChapter || '投标文件格式'}」。可直接删改，关闭或确认时写回。生成用这份。`"
    @update:open="onFormatPreviewOpen"
  >
    <div class="flex min-h-0 flex-1 flex-col gap-2">
      <TenderDocEditor
        v-if="formatPreviewFile"
        ref="formatEditorRef"
        :key="formatPreviewFile"
        class="min-h-0 flex-1"
        :docx-file="formatPreviewFile"
        :download-name="formatPreviewOnly ? '模板骨架预览.docx' : '招标文件格式章.docx'"
        :mode="formatPreviewOnly ? 'view' : 'edit'"
        :engine="formatPreviewOnly ? 'browser' : 'onlyoffice'"
        :layout-tick="formatPreviewTick"
      />
    </div>
    <template #footer>
      <p v-if="formatSaveError" class="mr-auto max-w-[60%] truncate text-[11px] text-iron">
        {{ formatSaveError }}
      </p>
      <button
        type="button"
        class="h-8 px-3 text-xs rounded-md border border-border hover:bg-accent disabled:opacity-50"
        :disabled="formatConfirmBusy"
        @click="onFormatPreviewOpen(false)"
      >
        {{ formatConfirmBusy ? '保存中…' : '关闭' }}
      </button>
      <button
        v-if="!formatPreviewOnly"
        type="button"
        class="h-8 px-3 text-xs rounded-md bg-foreground text-background hover:opacity-90 disabled:opacity-50"
        :disabled="!formatPreviewFile || form.formatConfirmed || formatConfirmBusy"
        @click="confirmFormatTemplate"
      >
        {{ form.formatConfirmed ? '已确认这份模板' : formatConfirmBusy ? '保存中…' : '确认使用这份模板' }}
      </button>
    </template>
  </AppDialog>
  <AppAlertDialog
    :open="confirmGenerateOpen"
    :title="confirmGenerateTitle"
    :description="confirmGenerateDescription"
    confirm-label="确认覆盖生成"
    :loading="generating"
    @update:open="onConfirmGenerateOpen"
    @confirm="confirmGenerateAgain"
  />
  <AppAlertDialog
    :open="confirmDeleteOpen"
    title="删除记录"
    :description="
      pendingDeleteItem
        ? `确定删除「${pendingDeleteItem.projectName || '未命名项目'}」这条记录？对应 Word 文件也会删除，不可恢复。`
        : '确定删除该记录？'
    "
    confirm-label="确认删除"
    destructive
    :loading="!!deletingId"
    @update:open="onConfirmDeleteOpen"
    @confirm="confirmDeleteRecord"
  />
  <AppAlertDialog
    :open="confirmResetOpen"
    title="重置当前标书"
    description="尚未走到下载定稿，重置后回到上传邀请书，本次进度不会写入生成记录。"
    confirm-label="确认重置"
    destructive
    :loading="resetting"
    @update:open="onConfirmResetOpen"
    @confirm="confirmResetWizard"
  />
  <AppAlertDialog
    :open="confirmClearOpen"
    title="清空我的生成记录"
    description="将删除你名下的全部生成记录及对应 Word 文件，角标计数归零，不可恢复。审批中或已定稿的任务（非管理员）会跳过。"
    confirm-label="确认清空"
    destructive
    :loading="clearingRecords"
    @update:open="onConfirmClearOpen"
    @confirm="confirmClearMineRecords"
  />
  </div>
</template>

<style scoped>
.tender-steps {
  --step-rail: color-mix(in srgb, var(--bg-elevated, hsl(var(--card))) 88%, transparent);
  padding: 0.9rem 1.35rem;
  border-radius: 0.75rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  background: var(--step-rail);
}
.tender-steps-list {
  display: flex;
  align-items: center;
  width: 100%;
}
.tender-step-item {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
}
.tender-step-item--grow {
  flex: 1 1 0;
  min-width: min-content;
}
.tender-step-btn {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
  border: none;
  background: transparent;
  padding: 0;
  text-align: left;
  position: relative;
  z-index: 1;
  transition: opacity 0.15s;
}
.tender-step-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.625rem;
  height: 1.625rem;
  border-radius: 9999px;
  border: 1.5px solid var(--hairline, hsl(var(--border)));
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
  background: var(--step-rail);
  box-shadow: 0 0 0 3px var(--step-rail);
}
.tender-step-dot--active {
  border-color: var(--accent-iron, hsl(var(--primary)));
  background: color-mix(in srgb, var(--accent-iron, hsl(var(--primary))) 12%, var(--bg-elevated, hsl(var(--card))));
  color: var(--accent-iron, hsl(var(--primary)));
}
.tender-step-dot--done {
  border-color: var(--accent-patina, #3d8b7a);
  background: color-mix(in srgb, var(--accent-patina, #3d8b7a) 16%, var(--bg-elevated, hsl(var(--card))));
  color: var(--accent-patina, #3d8b7a);
}
.tender-step-line {
  flex: 1 1 0;
  height: 2px;
  border-radius: 1px;
  background: var(--hairline, hsl(var(--border)));
  min-width: 0.75rem;
  margin: 0 0.7rem;
}
.tender-step-line--done {
  background: color-mix(in srgb, var(--accent-patina, #3d8b7a) 55%, var(--hairline, hsl(var(--border))));
}
.tender-step-panel {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.tender-volume-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: 0.5rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  background: var(--bg-elevated, hsl(var(--card)));
}
.tender-volume-btn {
  height: 1.75rem;
  padding: 0 0.75rem;
  border: none;
  border-radius: 0.375rem;
  background: transparent;
  color: var(--muted-foreground, hsl(var(--muted-foreground)));
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}
.tender-volume-btn--active {
  background: color-mix(in srgb, var(--accent-iron, hsl(var(--primary))) 88%, black);
  color: #fff;
}
.tender-page--preview {
  display: flex;
  flex-direction: column;
  flex: 1 1 0;
  min-height: 0;
}
.tender-page-header--preview {
  margin-bottom: 0.5rem !important;
}
.tender-step-panel--preview {
  width: 100%;
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.tender-preview-shell {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  border-radius: 0.75rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  background: var(--bg-elevated, hsl(var(--card)));
  padding: 1rem 1.25rem 1.15rem;
  box-shadow: 0 1px 2px color-mix(in srgb, #000 4%, transparent);
}
.tender-preview-shell--fs {
  position: fixed;
  inset: 0.7rem 0.85rem 0.85rem;
  z-index: 90;
  width: auto;
  height: auto;
  border-radius: 0.9rem;
  padding: 1rem 1.25rem 1.2rem;
  box-shadow: 0 8px 28px color-mix(in srgb, #000 16%, transparent);
}
.tender-preview-shell--fs :deep(.tender-doc-editor) {
  flex: 1 1 0;
  min-height: 0;
  height: auto;
}
.tender-preview-editor {
  flex: 1 1 0;
  min-height: 0;
  align-self: stretch;
  width: 100%;
}
.tender-step-footer--preview {
  padding-bottom: 0.25rem;
}
.tender-step-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding-top: 0.35rem;
  padding-bottom: 0.15rem;
}
.tender-card {
  border-radius: 0.75rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  background: var(--bg-elevated, hsl(var(--card)));
  padding: 1rem 1.25rem;
  box-shadow: 0 1px 2px color-mix(in srgb, #000 4%, transparent);
}
.tender-card--sticky {
  padding-bottom: 1.25rem;
}
.tender-card--success {
  border-color: color-mix(in srgb, var(--accent-patina, #3d8b7a) 35%, var(--hairline, hsl(var(--border))));
  background: color-mix(in srgb, var(--accent-patina, #3d8b7a) 6%, var(--bg-elevated, hsl(var(--card))));
}
.tender-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  padding-bottom: 0.25rem;
}
.tender-card-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.25rem;
  height: 2.25rem;
  border-radius: 0.5rem;
  background: color-mix(in srgb, var(--accent-molybdenum, hsl(var(--muted))) 12%, transparent);
  color: var(--accent-molybdenum, hsl(var(--muted-foreground)));
  shrink: 0;
}
.tender-card-icon--accent {
  background: color-mix(in srgb, var(--accent-iron, hsl(var(--primary))) 12%, transparent);
  color: var(--accent-iron, hsl(var(--primary)));
}
.tender-dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 9.5rem;
  padding: 1.5rem 1rem;
  border-radius: 0.625rem;
  border: 2px dashed var(--hairline, hsl(var(--border)));
  background: color-mix(in srgb, var(--bg-base, hsl(var(--background))) 60%, transparent);
  transition: border-color 0.15s, background 0.15s;
  cursor: pointer;
  text-align: center;
}
.tender-dropzone:hover,
.tender-dropzone--active {
  border-color: color-mix(in srgb, var(--accent-iron, hsl(var(--primary))) 45%, var(--hairline, hsl(var(--border))));
  background: color-mix(in srgb, var(--accent-iron, hsl(var(--primary))) 5%, transparent);
}
.tender-dropzone--filled {
  border-style: solid;
  border-color: color-mix(in srgb, var(--accent-patina, #3d8b7a) 40%, var(--hairline, hsl(var(--border))));
  background: color-mix(in srgb, var(--accent-patina, #3d8b7a) 5%, transparent);
}
.tender-primary-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.375rem;
  height: 2.25rem;
  padding: 0 1rem;
  border-radius: 0.5rem;
  font-size: 12px;
  font-weight: 500;
  color: white;
  background: var(--accent-iron, hsl(var(--primary)));
  transition: opacity 0.15s;
}
.tender-primary-btn:hover:not(:disabled) {
  opacity: 0.92;
}
.tender-primary-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.tender-generate-wrap {
  position: relative;
}
.tender-generate-tooltip {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 0.5rem);
  transform: translateX(-50%);
  width: max-content;
  max-width: min(18rem, calc(100vw - 2rem));
  padding: 0.5rem 0.65rem;
  border-radius: 0.5rem;
  font-size: 11px;
  line-height: 1.45;
  color: hsl(var(--foreground));
  background: hsl(var(--popover, var(--card)));
  border: 1px solid var(--hairline, hsl(var(--border)));
  box-shadow: 0 4px 16px rgb(0 0 0 / 12%);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s ease;
  z-index: 30;
}
.tender-generate-tooltip::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 100%;
  transform: translateX(-50%);
  border: 6px solid transparent;
  border-top-color: var(--hairline, hsl(var(--border)));
}
.tender-generate-wrap--hint:hover .tender-generate-tooltip,
.tender-generate-wrap--hint:focus-within .tender-generate-tooltip {
  opacity: 1;
}
.tender-ghost-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  height: 2rem;
  padding: 0 0.75rem;
  border-radius: 0.5rem;
  font-size: 12px;
  border: 1px solid var(--hairline, hsl(var(--border)));
  color: var(--text-secondary, hsl(var(--muted-foreground)));
  background: transparent;
  transition: background 0.15s;
}
.tender-ghost-btn:hover {
  background: color-mix(in srgb, var(--hairline, hsl(var(--border))) 40%, transparent);
}
.tender-hint-ok {
  font-size: 11px;
  padding: 0.5rem 0.75rem;
  border-radius: 0.5rem;
  border: 1px solid color-mix(in srgb, var(--accent-patina, #3d8b7a) 30%, transparent);
  background: color-mix(in srgb, var(--accent-patina, #3d8b7a) 8%, transparent);
  color: var(--accent-patina, #3d8b7a);
}
.tender-hint-muted {
  font-size: 11px;
  padding: 0.625rem 0.75rem;
  border-radius: 0.5rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  background: color-mix(in srgb, var(--bg-base, hsl(var(--background))) 50%, transparent);
  color: var(--text-muted, hsl(var(--muted-foreground)));
  text-align: center;
}
.tender-hint-warn {
  font-size: 11px;
  padding: 0.5rem 0.75rem;
  border-radius: 0.5rem;
  border: 1px solid color-mix(in srgb, var(--accent-sulfur, #c9a227) 35%, transparent);
  background: color-mix(in srgb, var(--accent-sulfur, #c9a227) 10%, transparent);
  color: var(--accent-sulfur, #a8841a);
}
.kb-input {
  background: var(--bg-surface, transparent);
  border: 1px solid var(--hairline, hsl(var(--border)));
  border-radius: 6px;
  color: inherit;
  font-size: 12px;
  padding: 8px 10px;
  width: 100%;
}
.tender-perf-table th {
  white-space: nowrap;
}
.tender-op-col {
  width: 5.25rem;
  min-width: 5.25rem;
  white-space: nowrap;
}
th.tender-op-col {
  background: color-mix(in srgb, var(--accent, #e8eef6) 40%, var(--background, #fff));
}
.tender-row-del {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.2rem;
  height: 1.75rem;
  padding: 0 0.45rem;
  border-radius: 0.375rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
  color: var(--text-secondary, hsl(var(--muted-foreground)));
  background: var(--background, #fff);
}
.tender-row-del:hover {
  color: var(--accent-sulfur);
  border-color: color-mix(in srgb, var(--accent-sulfur) 45%, var(--hairline, hsl(var(--border))));
  background: color-mix(in srgb, var(--accent-sulfur) 8%, var(--background, #fff));
}
.kb-input:focus {
  outline: none;
  border-color: var(--accent-molybdenum, hsl(var(--ring)));
}
select.outline-kind-select {
  width: auto;
  min-width: 7.25rem;
  max-width: 12rem;
  height: 1.5rem;
  padding: 0 0.35rem;
  font-size: 11px;
  font-weight: 400;
  line-height: 1.5rem;
  font-family: inherit;
}
.tender-req {
  margin-left: 0.15em;
  font-weight: 700;
  color: var(--destructive, #e03e3e);
}
</style>

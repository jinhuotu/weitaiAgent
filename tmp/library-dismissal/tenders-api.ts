import { apiDownload, apiFetchBlob, apiRequest } from './api'
import { getAccessToken } from './auth'

export type PlaceholderItem = {
  key: string
  title: string
  hint: string
  demandIds?: string[]
}

export type PerformanceLine = {
  projectName: string
  spec: string
  location: string
  client: string
  contact: string
  amountYuan: number
  summary: string
  note: string
  ongoing: boolean
  bidderName?: string
  chargerRelated?: boolean
  includeInBid?: boolean
}

export type PerformanceRequirement = {
  similarScope: string
  keywords: string[]
  minAmountYuan: number
  minCount: number
  requireCompleted: boolean
  note: string
}

export type PerformanceMatchResult = {
  passed: boolean
  amountOk: boolean
  similarOk: boolean
  completedOk: boolean
  reasons: string[]
}

const SCOPE_JUNK = [
  '所要求',
  '相符',
  '指标项',
  '招标文件',
  '投标人',
  '须提供',
  '资格审查',
  '证明材料',
  '与牵引',
  '响应情况',
  '填写',
  '详见',
]


function wordInBlob(blob: string, word: string) {
  if (!word || !blob) return false
  if (blob.includes(word)) return true
  return /^[A-Za-z0-9]+$/.test(word) && blob.toUpperCase().includes(word.toUpperCase())
}

export function cleanPerformanceScope(text: string) {
  const s = (text || '').replace(/\s+/g, '')
  if (s.length < 2) return ''
  if (SCOPE_JUNK.some((j) => s.includes(j))) return ''
  return s.slice(0, 80)
}

export function performanceTypeTokens(req?: PerformanceRequirement | null) {
  if (!req) return [] as string[]
  const out: string[] = []
  const seen = new Set<string>()
  const add = (raw: string) => {
    for (const part of cleanPerformanceScope(raw).split(/[、,，;；/]/)) {
      const word = (part || '').trim()
      if (!word || seen.has(word)) continue
      seen.add(word)
      out.push(word)
    }
  }
  for (const item of req.keywords || []) add(String(item || ''))
  add(req.similarScope || '')
  return out
}

export function emptyPerformanceRequirement(): PerformanceRequirement {
  return {
    similarScope: '',
    keywords: [],
    minAmountYuan: 0,
    minCount: 0,
    requireCompleted: false,
    note: '',
  }
}

export function performanceRequirementActive(req?: PerformanceRequirement | null): boolean {
  if (!req) return false
  return (
    (req.minAmountYuan || 0) > 0 ||
    (req.minCount || 0) > 0 ||
    Boolean(req.requireCompleted) ||
    (req.keywords || []).length > 0 ||
    Boolean((req.similarScope || '').trim())
  )
}

export function formatPerformanceRequirement(req?: PerformanceRequirement | null): string {
  if (!performanceRequirementActive(req) || !req) return ''
  const parts: string[] = []
  const tokens = performanceTypeTokens(req)
  const scope = cleanPerformanceScope(req.similarScope || '') || tokens.slice(0, 3).join('、')
  if (scope) parts.push(`同类「${scope}」`)
  if (req.minAmountYuan > 0) parts.push(`单份≥${formatPerfAmount(req.minAmountYuan)}`)
  if (req.minCount > 0) parts.push(`至少${req.minCount}个`)
  if (req.requireCompleted) parts.push('须已竣工')
  return parts.join('，')
}

export function matchPerformanceLine(
  line: PerformanceLine,
  req?: PerformanceRequirement | null,
): PerformanceMatchResult {
  if (!performanceRequirementActive(req) || !req) {
    return { passed: true, amountOk: true, similarOk: true, completedOk: true, reasons: [] }
  }
  const blob = `${line.projectName || ''}${line.spec || ''}${line.summary || ''}${line.note || ''}${line.client || ''}`.replace(
    /\s+/g,
    '',
  )
  const reasons: string[] = []
  let amountOk = true
  let similarOk = true
  let completedOk = true
  if (req.minAmountYuan > 0) {
    const amount = Number(line.amountYuan) || 0
    if (amount <= 0) {
      amountOk = false
      reasons.push('金额未识别')
    } else if (amount + 0.5 < req.minAmountYuan) {
      amountOk = false
      reasons.push('金额不足')
    }
  }
  const tokens = performanceTypeTokens(req)
  if (tokens.length) {
    similarOk = tokens.some((word) => {
      if (!word) return false
      if (wordInBlob(blob, word)) return true

      return false
    })
    if (!similarOk) reasons.push('类型不符')
  }
  if (req.requireCompleted && line.ongoing) {
    completedOk = false
    reasons.push('在建')
  }
  return {
    passed: amountOk && similarOk && completedOk,
    amountOk,
    similarOk,
    completedOk,
    reasons,
  }
}

function formatPerfAmount(amount: number): string {
  if (amount >= 10000 && Math.abs(amount / 10000 - Math.round(amount / 10000)) < 0.005) {
    return `${Math.round(amount / 10000)}万元`
  }
  if (amount >= 10000) {
    return `${String((amount / 10000).toFixed(2)).replace(/\.?0+$/, '')}万元`
  }
  return `${Math.round(amount)}元`
}

export type LibraryFactField = {
  name: string
  value: string
  conf?: number
}

export type LibraryFacts = {
  role: string
  fields: LibraryFactField[]
  source?: string
}

export type SlotFileInfo = {
  id?: string
  fileId?: string
  name: string
  fileName?: string
  sizeBytes: number
  fileType?: string
  kind?: 'image' | 'pdf' | 'file'
  performance?: PerformanceLine
  idCard?: {
    side?: string
    name?: string
    gender?: string
    idNo?: string
    age?: string
    personKey?: string
  }
  facts?: LibraryFacts
}

export type SlotStatus = {
  key: string
  title: string
  hint: string
  fileCount: number
  files: SlotFileInfo[]
  docId?: string
  parentKey?: string
  pinned?: boolean
}

export type QuoteLineIn = {
  seq: string
  name: string
  spec: string
  unit: string
  qty: number
  unitPrice: number
  amount: number
  groups?: string[]
}

export type DeviationLine = {
  seq: string
  requirement: string
  response: string
  deviation: string
  explanation?: string
}

export type OutlineKind =
  | 'letter'
  | 'legal_id'
  | 'auth'
  | 'quote'
  | 'biz_dev'
  | 'tech_dev'
  | 'commitment_copy'
  | 'scan'
  | 'performance'
  | 'factory'
  | 'tech_plan'
  | 'tech_chapter'
  | 'company'
  | 'unknown'

export type OutlineSource = 'generate' | 'copy' | 'skip'

export type ContentTemplateId = 'general' | 'project' | 'material' | 'charger_cabinet' | string

export type StructureMode = 'fixed' | 'outline'

export type CopyPolicy = 'rewrite' | 'verbatim'

export type TemplateHealth = {
  ok: boolean
  missingRequired: string[]
  missingOptional: string[]
  found: string[]
  notes: string[]
}

export type FixedTemplate = {
  id: string
  label: string
  source: string
  found: boolean
  sizeBytes: number
  /** 标题体检结果：缺必需锚点时生成会被拦下。 */
  health?: TemplateHealth
  /** 画像摘要：四份固定模板的差异点。 */
  techTitle?: string
  sections?: string[]
  quoteHeaders?: string[]
  factoryRole?: string
  notes?: string[]
}

export type OutlineItem = {
  id: string
  title: string
  kind: OutlineKind | string
  source: OutlineSource | string
  required: boolean
  skipped: boolean
  body?: string
  inviteExcerpt?: string
  draftBody?: string
  draftStatus?: string
  /** 单节覆盖整单策略：空=跟整单。Phase0 冻结口径。 */
  copyPolicy?: CopyPolicy | string
  level?: number
  /** 格式章目录条目。为 true 时生成目录保留该节，不当成表格标签丢掉。 */
  tocEligible?: boolean | null
  /** 1:1 忠实渲染：招标书里的原始序号（如「一、」「（一）」）。 */
  label?: string
  /** 1:1 忠实渲染：招标书里的原始标题文字（含序号），照抄不改。 */
  rawTitle?: string
}

export type OutlinePreviewRow = {
  id: string
  title: string
  kind: string
  level: number
  copyPolicy: string
  excerptPreview: string
  hasOriginal: boolean
  needsConfirm: boolean
  missing: string[]
}

export type OutlinePreview = {
  chapter: string
  structureMode: string
  items: OutlinePreviewRow[]
  warnings: string[]
}

export type BidRequirement = {
  id: string
  text: string
  category: string
  targetKind: string
  targetTitle?: string
  /** general 原分类；mandatory 否决项。这期不抽评分项。 */
  strength?: 'mandatory' | 'general' | string
  scoreWeight?: number | null
  sourceChapter?: string
  sourcePage?: string
  /** matched 有证据；gap 有落点未填；unbound 格式章无此空；format 只提示 */
  matchStatus?: 'matched' | 'gap' | 'unbound' | 'format' | string
  slotKey?: string
  matchNote?: string
  /** 否决项必须是 business 或 technical，空的不进任何一卷 */
  volume?: 'business' | 'technical' | string
  respond?: string
  attachToSection?: string
  display?: string
}

export type RequirementCoverage = {
  redLine: boolean
  mandatoryCovered: number
  mandatoryTotal: number
  formatCount: number
  rows: Array<{
    id: string
    text: string
    volume?: string
    matchStatus: string
    matchNote: string
    sourceChapter?: string
    sourcePage?: string
    slotKey?: string
  }>
}

export type VolumeFormat = {
  marginSet: boolean
  marginLeftCm: number
  marginRightCm: number
  marginTopCm: number
  marginBottomCm: number
  fontSet: boolean
  fontName: string
  bodySizeSet: boolean
  bodySizePt: number
  pagePosSet: boolean
  pageStartSet: boolean
  pageNumberPos: string
  pageNumberStart: string
  notes: string[]
}

export type DocumentFormat = {
  specified: boolean
  notes: string[]
  businessFormat?: VolumeFormat
  technicalFormat?: VolumeFormat
  marginLeftCm: number
  marginRightCm: number
  marginTopCm: number
  marginBottomCm: number
  fontName: string
  bodySizePt: number
  headingSizePt: number
  coverTitleSizePt: number
  coverDocSizePt: number
  tocTitleSizePt: number
  tocItemSizePt: number
  coverRequired: boolean
  coverShowProject: boolean
  coverShowTenderNo: boolean
  coverShowBidder: boolean
  coverShowCopyMark: boolean
  coverCopyMark: string
  coverShowDate: boolean
  coverNeedSeal: boolean
  tocNumbering: string
  tocNeedPageNos: boolean
  pageNumberPos: string
  pageNumberStart: string
}

export type BidBrief = {
  materialSelections?: import('./tender-materials-api').MaterialSelections
  projectName: string
  tenderer: string
  bidContent: string
  quality: string
  deliveryDays: number
  warrantyYears: number
  bidValidityDays: number
  bidPriceYuan: number
  prepaidPct: number
  arrivalPct: number
  settlementPct: number
  warrantyPct: number
  bidDate: string
  tenderNo: string
  bidderName: string
  bidderNature: string
  bidderAddress: string
  bidderWebsite: string
  bidderPhone: string
  bidderFax: string
  bidderPostcode: string
  bidderEmail: string
  foundedDate: string
  businessTerm: string
  legalPersonName: string
  legalPersonGender: string
  legalPersonAge: string
  legalPersonTitle: string
  legalPersonIdNo: string
  legalPersonKey?: string
  agentName: string
  agentIdNo: string
  agentAuthUntil: string
  authNeed?: string
  authEnabled?: boolean
  trafficFeeNote: string
  extraNote: string
  factoryRole: string
  controlPriceYuan?: number | null
  safetyFeeYuan?: number | null
  provisionalYuan?: number | null
  primeCostYuan?: number | null
  bidBondYuan?: number | null
  pmName?: string
  pmCertNo?: string
  pmGrade?: string
  defectPeriod?: string
  perfBond?: string
  attachQualifications: boolean
  includePlaceholders: boolean
  includeCommitment: boolean
  extraPlaceholders: PlaceholderItem[]
  materialDemands?: MaterialDemand[]
  materialIntentHash?: string
  materialIntentComplete?: boolean
  materialIntentNotes?: string[]
  requiredSlotKeys: string[]
  includeSlotKeys: string[]
  quoteTitle: string
  quoteTaxRate: number
  quoteSourceIncTax: number
  quoteSource: string
  quoteLines: QuoteLineIn[]
  quoteHeaders: string[]
  quoteRoles: string[]
  deviationLines: DeviationLine[]
  bizDeviationLines?: DeviationLine[]
  bizDevHeaders: string[]
  techDevHeaders: string[]
  performanceLines: PerformanceLine[]
  performanceRequirement: PerformanceRequirement
  constructionPlan: string
  layoutPlan: string
  powerPlan: string
  omPlan: string
  schedulePlan: string
  techPlanNote: string
  techScorecard?: string
  autoDraftTechPlan?: boolean
  autoDraftBizResp?: boolean
  techPlanSourceIds?: string[]
  techPlanKbIds?: string[]
  bizRespKbIds?: string[]
  layoutMode: string
  requirementsDriven?: boolean
  contentTemplateId?: string
  structureMode?: StructureMode | string
  copyPolicy?: CopyPolicy | string
  /** outline 轨渲染风格：verbatim=1:1 照抄招标书序号/标题/顺序；curated=系统重排重编号。 */
  outlineRender?: 'verbatim' | 'curated' | string
  outlineConfirmed?: boolean
  /** 按本标：预览并确认截出的格式章后才允许生成。 */
  formatConfirmed?: boolean
  outlineChapter: string
  outlineItems: OutlineItem[]
  bidRequirements?: BidRequirement[]
  documentFormat: DocumentFormat
  invitationId?: string
  generateVolume?: 'business' | 'technical' | 'full'
  /** 为 true 时同时出商务标和技术标。默认 false，只出当前卷。 */
  generateBoth?: boolean
  splitSlice?: boolean
  /** 解析结果：full 不强制分卷，split 分商务/技术。 */
  volumeAdvice?: 'full' | 'split' | ''
  techAnonymous?: boolean
  volumeNote?: string
}

export type QualificationStatus = {
  found: boolean
  pathHint: string
  sizeBytes: number
}

export type Chapter5TemplateStatus = {
  source: 'custom' | 'bundled' | 'missing' | string
  custom: boolean
  found: boolean
  sizeBytes: number
  label: string
}

export type TenderDocPreview = 'browser' | 'onlyoffice' | 'yozo' | 'pdf'

export function isOnlineDocEditor(engine?: string | null): engine is 'onlyoffice' | 'yozo' {
  const value = String(engine || '').toLowerCase()
  return value === 'onlyoffice' || value === 'yozo'
}

export type TenderDefaults = BidBrief & {
  qualification: QualificationStatus
  slots: SlotStatus[]
  chapter5Template?: Chapter5TemplateStatus
  docPreview?: TenderDocPreview | string
  /** 轨道A：固定模板下拉（Phase1）。 */
  fixedTemplates?: FixedTemplate[]
  contentTemplateId?: string
  structureMode?: string
  structureModes?: string[]
  outlineConfirmed?: boolean
}

export type AttachmentMatchItem = {
  key: string
  title: string
  fileCount: number
  required?: boolean
  created?: boolean
}

export type AttachmentMatch = {
  matched: AttachmentMatchItem[]
  missingFiles: AttachmentMatchItem[]
  createdItems: AttachmentMatchItem[]
}

export type QaSeverity = 'disqualify' | 'deduct' | 'suggest' | string

export type QaCategory =
  | 'outline'
  | 'qualification'
  | 'commercial'
  | 'technical'
  | 'quote'
  | 'format'
  | 'other'
  | string

export type QaGap = {
  title: string
  reason: string
  severity: QaSeverity
  category: QaCategory
  source?: string
}

export type QaCoverageBucket = {
  found: number
  total: number
  score: number
}

export type BidVolume = 'business' | 'technical'

export type QaReport = {
  recordId: string
  docxFile?: string
  volume?: BidVolume | string
  similarityScore: number
  ruleScore: number
  llmScore: number | null
  grade: 'good' | 'fair' | 'risk' | string
  gradeLabel: string
  summary: string
  invitationChars: number
  bidChars: number
  hasInvitation: boolean
  llmUsed: boolean
  coverage: Record<string, QaCoverageBucket>
  requirementCoverage?: RequirementCoverage
  missing: QaGap[]
  letterChecklist?: string[]
  checkedAt?: number
  stale?: boolean
  source?: 'generated' | 'upload' | string
  uploadName?: string | null
}

export function volumeDocx(
  row: { docxFile?: string; downloadName?: string; techDocxFile?: string | null;
         techDownloadName?: string | null; projectName?: string; generateVolume?: string },
  volume: BidVolume | 'full',
): { file: string; name: string } {
  if (volume === 'full' || row.generateVolume === 'full') {
    if (volume === 'technical' || (row.generateVolume && row.generateVolume !== 'full')) return { file: '', name: '' }
    return { file: row.docxFile || '', name: row.downloadName || `${row.projectName || 'bid'}-综合标.docx` }
  }
  if (volume === 'technical') {
    return { file: row.techDocxFile || '', name: row.techDownloadName || `${row.projectName || 'bid'}-技术标.docx` }
  }
  return { file: row.docxFile || '', name: row.downloadName || `${row.projectName || 'bid'}-商务标.docx` }
}

export type ApprovalStepDef = {
  key: string
  name: string
  kind?: string
  roleCode?: string | null
  roleName?: string | null
}

export type ApprovalFlowPayload = {
  code: string
  canEdit: boolean
  steps: ApprovalStepDef[]
  updatedAt?: number | null
  roles?: { id: number; code: string; name: string; description: string | null }[]
}

export type ApprovalReviewInput = {
  key?: string | null
  name: string
  roleCode?: string | null
}

export type MaterialCoverageRow = {
  id: string
  title: string
  text: string
  section: string
  level: number
  slotKey: string
  placement?: string
  status: 'filled' | 'pasted' | 'placeholder'
  reviewRequired: boolean
}
export type MaterialCoverage = Partial<Record<'business' | 'technical' | 'full', MaterialCoverageRow[]>>
export type MaterialDemand = Omit<MaterialCoverageRow, 'status' | 'placement'> & {
  condition: string
  volume: string
  outcomes: Partial<Record<'business' | 'technical' | 'full', MaterialCoverageRow['status']>>
  placements?: Partial<Record<'business' | 'technical' | 'full', string>>
}

export type GenerateResult = {
  materialCoverage?: MaterialCoverage
  materialIntentComplete?: boolean
  id?: string
  projectName?: string
  tenderer?: string
  bidPriceYuan?: number
  legalPersonName?: string
  docxFile: string
  techDocxFile?: string | null
  pdfFile: string | null
  downloadName: string
  techDownloadName?: string | null
  pdfDownloadName: string | null
  warnings: string[]
  username?: string
  createdAt?: number
  docxAvailable?: boolean
  techDocxAvailable?: boolean
  pdfAvailable?: boolean
  attachmentMatch?: AttachmentMatch
  status?: string
  deadline?: string
  projectType?: string
  currentStep?: string
  currentStepKey?: string
  approvalSteps?: ApprovalStepDef[] | null
  submittedAt?: number | null
  decidedAt?: number | null
  workflowLocked?: boolean
  userId?: number
  generateVolume?: 'business' | 'technical' | string
  otherVolume?: 'business' | 'technical' | string
  otherVolumeReady?: boolean
  otherVolumeNote?: string
  lastAction?: string
  lastComment?: string
  lastActor?: string
  approvalLogs?: TenderApprovalLogItem[]
}

export type TenderApprovalLogItem = {
  id: number
  action: string
  step: string
  comment: string
  username: string
  createdAt: number
}

export type TenderRecordItem = {
  materialCoverage?: MaterialCoverage
  materialIntentComplete?: boolean
  id: string
  projectName: string
  tenderer: string
  bidPriceYuan: number
  legalPersonName: string
  docxFile: string
  techDocxFile?: string | null
  pdfFile: string | null
  downloadName: string
  techDownloadName?: string | null
  pdfDownloadName: string | null
  warnings: string[]
  username: string
  createdAt: number
  docxAvailable: boolean
  techDocxAvailable?: boolean
  pdfAvailable: boolean
  brief?: BidBrief
  status?: string
  deadline?: string
  projectType?: string
  currentStep?: string
  currentStepKey?: string
  approvalSteps?: ApprovalStepDef[] | null
  submittedAt?: number | null
  decidedAt?: number | null
  workflowLocked?: boolean
  userId?: number
  generateVolume?: 'business' | 'technical' | string
  otherVolume?: 'business' | 'technical' | string
  otherVolumeReady?: boolean
  otherVolumeNote?: string
  lastAction?: string
  lastComment?: string
  lastActor?: string
  approvalLogs?: TenderApprovalLogItem[]
  qaScore?: number | null
  qaGrade?: string | null
  qaSource?: string | null
  qaCheckedAt?: number | null
}

export function isTenderWorkflowLocked(status?: string | null, locked?: boolean): boolean {
  if (locked) return true
  return Boolean(status && ['pending', 'approved', 'submitted', 'won', 'lost'].includes(status))
}

export type TenderEditorConfig = {
  engine?: TenderDocPreview | string
  documentServerUrl: string
  iframeUrl?: string
  config: Record<string, unknown>
}

export type ParseInvitationResult = {
  brief: BidBrief
  filledKeys: string[]
  notes: string[]
  placeholders: PlaceholderItem[]
  slots?: SlotStatus[]
  catalogSlots?: SlotStatus[]
  requiredSlotKeys?: string[]
  includeSlotKeys?: string[]
  attachmentMatch?: AttachmentMatch
  fileName: string
  charCount: number
  pageCount?: number
  ocrPages?: number
  ocrCapped?: boolean
  ocrMaxPages?: number
  preview: string
  knowledgeHits: { name?: string; docId?: string }[]
  quoteFileName?: string | null
  agentHint?: { agentName?: string; agentIdNo?: string }
}

function token(): string {
  const t = getAccessToken()
  if (!t) throw new Error('请先登录')
  return t
}

export async function fetchTenderDefaults(): Promise<TenderDefaults> {
  return apiRequest<TenderDefaults>('/api/v1/tenders/defaults', { token: token() })
}

/** 轨道A：固定模板列表。 */
export async function fetchFixedTemplates(): Promise<FixedTemplate[]> {
  const data = await apiRequest<{ items: FixedTemplate[] }>('/api/v1/tenders/fixed-templates', {
    token: token(),
  })
  return data.items || []
}

/** 固定模板标题体检：上传自定义空白稿后可单独复检。 */
export async function fetchFixedTemplateHealth(templateId: string): Promise<TemplateHealth> {
  return apiRequest<TemplateHealth>(`/api/v1/tenders/fixed-templates/${templateId}/health`, {
    token: token(),
  })
}

/** 轨道B：大纲确认页数据，每节建议 rewrite/verbatim + 缺件提示。 */
export async function previewTenderOutline(brief: BidBrief): Promise<OutlinePreview> {
  return apiRequest<OutlinePreview>('/api/v1/tenders/outline/preview', {
    method: 'POST',
    token: token(),
    body: { brief },
  })
}

/** 用户确认大纲：回写 outlineItems 并置 outlineConfirmed，生成接口据此放行 outline 轨。 */
export async function confirmTenderOutline(
  brief: BidBrief,
  items: OutlineItem[],
): Promise<{ brief: BidBrief }> {
  return apiRequest<{ brief: BidBrief }>('/api/v1/tenders/outline/confirm', {
    method: 'POST',
    token: token(),
    body: { brief, items },
  })
}

export async function uploadTenderLayoutTemplate(file: File): Promise<Chapter5TemplateStatus> {
  const form = new FormData()
  form.append('file', file)
  return apiRequest<Chapter5TemplateStatus>('/api/v1/tenders/layout-template', {
    method: 'POST',
    token: token(),
    body: form,
  })
}

export async function restoreTenderLayoutTemplate(): Promise<Chapter5TemplateStatus> {
  return apiRequest<Chapter5TemplateStatus>('/api/v1/tenders/layout-template', {
    method: 'DELETE',
    token: token(),
  })
}

const FILE_ID_RE = /^([a-f0-9]{10,16})\.(png|jpe?g|webp|gif|bmp|pdf)$/i

export function resolveLibraryFileId(
  file: Pick<SlotFileInfo, 'id' | 'fileId' | 'fileName' | 'name'>,
): string {
  const direct = String(file.fileId || file.id || '').trim()
  if (direct) return direct
  const n = String(file.fileName || file.name || '').trim()
  const m = FILE_ID_RE.exec(n)
  return m?.[1] || ''
}

export function inferLibraryFileKind(
  file: Pick<SlotFileInfo, 'kind' | 'fileType' | 'fileName' | 'name'>,
): NonNullable<SlotFileInfo['kind']> {
  if (file.kind === 'image' || file.kind === 'pdf' || file.kind === 'file') return file.kind
  const raw = `${file.fileType || ''} ${file.fileName || ''} ${file.name || ''}`.toLowerCase()
  if (raw.includes('pdf')) return 'pdf'
  if (/\.(png|jpe?g|webp|gif|bmp)\b/.test(raw) || /\b(png|jpe?g|webp|gif|bmp)\b/.test(raw)) {
    return 'image'
  }
  return 'file'
}

export function tenderLibraryFilePath(
  docId: string,
  opts?: { thumb?: boolean; withToken?: boolean },
): string {
  const q = new URLSearchParams()
  if (opts?.thumb) q.set('thumb', '1')
  if (opts?.withToken) {
    const t = getAccessToken()
    if (t) q.set('access_token', t)
  }
  const qs = q.toString()
  return `/api/v1/tenders/library/files/${encodeURIComponent(docId)}${qs ? `?${qs}` : ''}`
}

export async function fetchTenderLibraryFile(
  docId: string,
  opts?: { thumb?: boolean },
): Promise<Blob> {
  return apiFetchBlob(tenderLibraryFilePath(docId, { thumb: opts?.thumb }), { token: token() })
}

export async function fetchTenderLibrary(): Promise<{
  slots: SlotStatus[]
  filledCount: number
  totalCount: number
  hint: string
  baseId?: string
}> {
  return apiRequest('/api/v1/tenders/library', { token: token() })
}

/** 将资料库扫描件 OCR 向量化，供 AI 问答检索。force=true 强制重跑 */
export async function reindexTenderLibrary(opts?: {
  force?: boolean
}): Promise<{ queued: number; skipped: number }> {
  const q = opts?.force ? '?force=true' : ''
  return apiRequest(`/api/v1/tenders/library/reindex${q}`, {
    method: 'POST',
    token: token(),
  })
}

export async function createTenderLibraryItem(body: {
  title: string
  hint?: string
  key?: string
}): Promise<SlotStatus> {
  return apiRequest('/api/v1/tenders/library/items', {
    method: 'POST',
    token: token(),
    body,
  })
}

export async function updateTenderLibraryItem(
  key: string,
  body: { title?: string; hint?: string },
): Promise<SlotStatus> {
  return apiRequest(`/api/v1/tenders/library/items/${encodeURIComponent(key)}`, {
    method: 'PATCH',
    token: token(),
    body,
  })
}

export async function deleteTenderLibraryItem(key: string): Promise<{ key: string; deleted: boolean }> {
  return apiRequest(`/api/v1/tenders/library/items/${encodeURIComponent(key)}`, {
    method: 'DELETE',
    token: token(),
  })
}

export async function dismissTenderLibraryItem(key: string): Promise<{ key: string; dismissed: boolean }> {
  return apiRequest('/api/v1/tenders/library/dismissed', {
    method: 'POST',
    token: token(),
    body: { key },
  })
}

export async function fetchTenderSlots(
  extras?: PlaceholderItem[],
  invitationId?: string,
): Promise<SlotStatus[]> {
  const q = new URLSearchParams()
  if (extras && extras.length) q.set('extras', JSON.stringify(extras))
  if (invitationId) q.set('invitationId', invitationId)
  const suffix = q.toString() ? `?${q.toString()}` : ''
  const data = await apiRequest<{ slots: SlotStatus[] }>(`/api/v1/tenders/slots${suffix}`, {
    token: token(),
  })
  return data.slots || []
}

export async function uploadTenderSlot(
  key: string,
  file: File,
  options: { replace?: boolean; invitationId?: string; bidderName?: string; materialTitle?: string; materialHint?: string } = {},
): Promise<SlotStatus & { fileName: string }> {
  const form = new FormData()
  form.append('file', file)
  form.append('replace', options.replace === false ? 'false' : 'true')
  if (options.invitationId) form.append('invitationId', options.invitationId)
  if (options.bidderName) form.append('bidderName', options.bidderName)
  if (options.materialTitle) form.append('materialTitle',options.materialTitle)
  if (options.materialHint) form.append('materialHint',options.materialHint)
  return apiRequest(`/api/v1/tenders/slots/${encodeURIComponent(key)}`, {
    method: 'POST',
    token: token(),
    body: form,
  })
}

export async function clearTenderSlot(
  key: string,
  options: { invitationId?: string } = {},
): Promise<SlotStatus> {
  const q = options.invitationId
    ? `?invitationId=${encodeURIComponent(options.invitationId)}`
    : ''
  return apiRequest(`/api/v1/tenders/slots/${encodeURIComponent(key)}${q}`, {
    method: 'DELETE',
    token: token(),
  })
}

export async function updateLibraryFileFacts(
  docId: string,
  body: { role: string; fields: { name: string; value: string }[] },
): Promise<SlotStatus> {
  return apiRequest(`/api/v1/tenders/library/files/${encodeURIComponent(docId)}/facts`, {
    method: 'PATCH',
    token: token(),
    body,
  })
}

export async function deleteTenderLibraryFile(docId: string): Promise<SlotStatus> {
  return apiRequest(`/api/v1/tenders/library/files/${encodeURIComponent(docId)}`, {
    method: 'DELETE',
    token: token(),
  })
}

export async function parseTenderInvitation(
  file: File,
  options: {
    kbIds?: string[]
    current?: BidBrief
    quoteFile?: File | null
    quoteRecordIds?: string[]
  } = {},
): Promise<ParseInvitationResult> {
  const form = new FormData()
  form.append('file', file)
  if (options.quoteFile) {
    form.append('quoteFile', options.quoteFile)
  }
  if (options.quoteRecordIds?.length) {
    form.append('quoteRecordIds', JSON.stringify(options.quoteRecordIds))
  }
  if (options.kbIds?.length) {
    form.append('kbIds', JSON.stringify(options.kbIds))
  }
  if (options.current) {
    form.append('current', JSON.stringify(options.current))
  }
  return apiRequest<ParseInvitationResult>('/api/v1/tenders/parse-invitation', {
    method: 'POST',
    token: token(),
    body: form,
  })
}

export async function draftTenderTech(
  brief: BidBrief,
  options: { kbIds?: string[]; itemId?: string; promptId?: string; materialIds?: string[] } = {},
): Promise<{ brief: BidBrief; notes: string[] }> {
  return apiRequest<{ brief: BidBrief; notes: string[] }>('/api/v1/tenders/tech/draft', {
    method: 'POST',
    token: token(),
    body: {
      brief,
      kbIds: options.kbIds || [],
      itemId: options.itemId || null,
      promptId: options.promptId || null,
      materialIds: options.materialIds || [],
    },
  })
}

export async function uploadTechPlanSource(file: File): Promise<{ sourceId: string; filename: string; chars: string }> {
  const form = new FormData()
  form.append('file', file)
  return apiRequest('/api/v1/tenders/tech/plan-source', {
    method: 'POST',
    token: token(),
    body: form,
  })
}

export async function fillTechBoq(file: File): Promise<{ draftBody: string }> {
  const form = new FormData()
  form.append('file', file)
  return apiRequest('/api/v1/tenders/tech/boq', {
    method: 'POST',
    token: token(),
    body: form,
  })
}

export async function fillTechDrawing(
  file: File,
  invitationId: string,
  replace = true,
): Promise<{ draftBody: string; slot: SlotStatus }> {
  const form = new FormData()
  form.append('file', file)
  form.append('invitationId', invitationId)
  form.append('replace', replace ? 'true' : 'false')
  return apiRequest('/api/v1/tenders/tech/drawing-pdf', {
    method: 'POST',
    token: token(),
    body: form,
  })
}

export async function generateTender(body: BidBrief): Promise<GenerateResult> {
  return apiRequest<GenerateResult>('/api/v1/tenders/generate', {
    method: 'POST',
    token: token(),
    body,
  })
}

export async function fetchTenderRecords(params?: {
  q?: string
  limit?: number
  offset?: number
  scope?: 'mine' | 'all'
  status?: string
  owner?: string
  projectType?: string
  approvalTab?: 'pending' | 'done' | 'mine'
}): Promise<{ total: number; items: TenderRecordItem[] }> {
  const q = new URLSearchParams()
  if (params?.q) q.set('q', params.q)
  if (params?.limit != null) q.set('limit', String(params.limit))
  if (params?.offset != null) q.set('offset', String(params.offset))
  if (params?.scope) q.set('scope', params.scope)
  if (params?.status) q.set('status', params.status)
  if (params?.owner) q.set('owner', params.owner)
  if (params?.projectType) q.set('projectType', params.projectType)
  if (params?.approvalTab) q.set('approvalTab', params.approvalTab)
  const qs = q.toString()
  return apiRequest(`/api/v1/tenders/records${qs ? `?${qs}` : ''}`, { token: token() })
}

export async function fetchApprovalFlow(): Promise<ApprovalFlowPayload> {
  return apiRequest('/api/v1/tenders/approval-flow', { token: token() })
}

export async function saveApprovalFlow(reviews: ApprovalReviewInput[]): Promise<ApprovalFlowPayload> {
  return apiRequest('/api/v1/tenders/approval-flow', {
    method: 'PUT',
    token: token(),
    body: { reviews },
  })
}

export async function fetchTenderRecord(recordId: string): Promise<TenderRecordItem> {
  return apiRequest(`/api/v1/tenders/records/${encodeURIComponent(recordId)}`, {
    token: token(),
  })
}

export async function inspectTenderQa(
  recordId: string,
  opts?: { volume?: BidVolume | 'full' },
): Promise<QaReport> {
  const q = new URLSearchParams()
  if (opts?.volume) q.set('volume', opts.volume)
  const qs = q.toString()
  return apiRequest<QaReport>(
    `/api/v1/tenders/records/${encodeURIComponent(recordId)}/qa${qs ? `?${qs}` : ''}`,
    {
      method: 'POST',
      token: token(),
    },
  )
}

export async function inspectTenderQaUpload(
  recordId: string,
  file: File,
  opts?: { volume?: BidVolume | 'full' },
): Promise<QaReport> {
  const form = new FormData()
  form.append('file', file)
  const q = new URLSearchParams()
  if (opts?.volume) q.set('volume', opts.volume)
  const qs = q.toString()
  return apiRequest<QaReport>(
    `/api/v1/tenders/records/${encodeURIComponent(recordId)}/qa-upload${qs ? `?${qs}` : ''}`,
    {
      method: 'POST',
      token: token(),
      body: form,
    },
  )
}

export async function fetchTenderQa(
  recordId: string,
  opts?: { volume?: BidVolume | 'full' },
): Promise<{ report: QaReport | null }> {
  const q = new URLSearchParams()
  if (opts?.volume) q.set('volume', opts.volume)
  const qs = q.toString()
  return apiRequest(`/api/v1/tenders/records/${encodeURIComponent(recordId)}/qa${qs ? `?${qs}` : ''}`, {
    token: token(),
  })
}

export async function regenerateTenderRecord(
  recordId: string,
  brief?: BidBrief,
): Promise<GenerateResult> {
  return apiRequest(`/api/v1/tenders/records/${encodeURIComponent(recordId)}/regenerate`, {
    method: 'POST',
    token: token(),
    body: brief,
  })
}

export async function submitTenderRecord(recordId: string): Promise<TenderRecordItem> {
  return apiRequest(`/api/v1/tenders/records/${encodeURIComponent(recordId)}/submit`, {
    method: 'POST',
    token: token(),
  })
}

export async function decideTenderRecord(
  recordId: string,
  passed: boolean,
  comment?: string,
): Promise<TenderRecordItem> {
  return apiRequest(`/api/v1/tenders/records/${encodeURIComponent(recordId)}/decide`, {
    method: 'POST',
    token: token(),
    body: { passed, comment: comment || '' },
  })
}

export async function markTenderRecord(
  recordId: string,
  status: 'submitted' | 'won' | 'lost',
): Promise<TenderRecordItem> {
  return apiRequest(`/api/v1/tenders/records/${encodeURIComponent(recordId)}/mark`, {
    method: 'POST',
    token: token(),
    body: { status },
  })
}

export async function publishTenderRecord(recordId: string): Promise<TenderRecordItem> {
  return apiRequest(`/api/v1/tenders/records/${encodeURIComponent(recordId)}/publish`, {
    method: 'POST',
    token: token(),
  })
}

export async function deleteTenderRecord(
  recordId: string,
): Promise<{ deleted: boolean; id: string; removedFiles: string[] }> {
  return apiRequest(`/api/v1/tenders/records/${encodeURIComponent(recordId)}`, {
    method: 'DELETE',
    token: token(),
  })
}

export async function clearMineTenderRecords(): Promise<{
  deleted: number
  skipped: number
  ids: string[]
  removedFiles: string[]
}> {
  return apiRequest('/api/v1/tenders/records/clear-mine', {
    method: 'POST',
    token: token(),
  })
}

export async function fetchTenderEditorConfig(
  fileName: string,
  downloadName: string,
  heightPx?: number,
  mode: 'edit' | 'view' = 'edit',
): Promise<TenderEditorConfig> {
  const q = new URLSearchParams({ download_name: downloadName, mode })
  if (heightPx && heightPx >= 400) {
    q.set('height', String(Math.round(heightPx)))
  }
  return apiRequest<TenderEditorConfig>(
    `/api/v1/tenders/files/${encodeURIComponent(fileName)}/editor-config?${q}`,
    { token: token() },
  )
}

export async function forceSaveTenderDocx(fileName: string, key: string): Promise<void> {
  await apiRequest(`/api/v1/tenders/files/${encodeURIComponent(fileName)}/onlyoffice-forcesave`, {
    method: 'POST',
    token: token(),
    body: { key },
  })
}

export async function downloadTenderFile(fileName: string, downloadName: string): Promise<void> {
  const q = new URLSearchParams({ download_name: downloadName })
  await apiDownload(`/api/v1/tenders/files/${encodeURIComponent(fileName)}?${q}`, {
    token: token(),
    fallbackName: downloadName,
  })
}

export async function fetchTenderDocxBlob(fileName: string): Promise<Blob> {
  return apiFetchBlob(`/api/v1/tenders/files/${encodeURIComponent(fileName)}`, { token: token() })
}

export type InvitationFormatPreview = {
  fileName: string
  source: 'invitation' | 'builtin' | 'requirements'
  previewOnly: boolean
  message: string
}

export function prepareInvitationFormatDocx(invitationId: string): Promise<string>
export function prepareInvitationFormatDocx(invitationId: string, brief: BidBrief): Promise<InvitationFormatPreview>
export async function prepareInvitationFormatDocx(
  invitationId: string, brief?: BidBrief,
): Promise<string | InvitationFormatPreview> {
  const data = await apiRequest<Partial<InvitationFormatPreview>>(
    `/api/v1/tenders/invitations/${encodeURIComponent(invitationId)}/format-docx`,
    { method: brief ? 'POST' : 'GET', body: brief, token: token() },
  )
  const preview: InvitationFormatPreview = {
    fileName: String(data.fileName || '').trim(),
    source: data.source || 'invitation',
    previewOnly: Boolean(data.previewOnly),
    message: String(data.message || ''),
  }
  return brief ? preview : preview.fileName
}

export async function confirmInvitationFormatDocx(invitationId: string): Promise<string> {
  const data = await apiRequest<{ fileName: string; key: string }>(
    `/api/v1/tenders/invitations/${encodeURIComponent(invitationId)}/format-docx/confirm`,
    { method: 'POST', token: token() },
  )
  return data.key
}

export type HistoryChapter = {
  displayTitle?: string
  previewKey?: string
  volume: 'business' | 'technical' | string
  chapterTitle: string
  docId: string
  verdict: 'good' | 'bad' | 'none' | string
  problemTags: string[]
  note: string
  source: string
}

export type HistoryArchiveState = {
  status?: 'processing' | 'completed' | 'failed'
  archiveNote?: string
}

export async function fetchHistoryChapterPreview(
  recordId: string, chapter: HistoryChapter,
): Promise<Blob> {
  const q = new URLSearchParams({
    recordId, chapterTitle: chapter.chapterTitle, volume: chapter.volume,
    chapterKey: chapter.previewKey || '',
  })
  return apiFetchBlob(`/api/v1/tenders/history-bids/chapters/preview?${q}`, { token: token() })
}

export async function uploadHistoryBid(
  file: File,
  outcome: 'won' | 'lost' | '' = '',
  recordId?: string,
): Promise<HistoryArchiveState & { recordId: string; queued?: number }> {
  const form = new FormData()
  form.append('file', file)
  form.append('outcome', outcome)
  if (recordId) form.append('recordId', recordId)
  return apiRequest('/api/v1/tenders/history-bids', {
    method: 'POST',
    token: token(),
    body: form,
  })
}

export async function fetchHistoryChapters(recordId: string): Promise<HistoryArchiveState & {
  recordId: string
  chapters: HistoryChapter[]
}> {
  const q = new URLSearchParams({ recordId })
  return apiRequest(`/api/v1/tenders/history-bids/chapters?${q}`, { token: token() })
}

export async function saveHistoryMark(body: {
  recordId?: string
  docId?: string
  volume: string
  chapterTitle: string
  verdict: 'good' | 'bad' | 'none'
  problemTags?: string[]
  note?: string
}): Promise<HistoryChapter> {
  return apiRequest('/api/v1/tenders/history-marks', {
    method: 'PUT',
    token: token(),
    body,
  })
}

export async function fetchTenderPreviewPdfBlob(fileName: string): Promise<Blob> {
  return apiFetchBlob(`/api/v1/tenders/files/${encodeURIComponent(fileName)}/preview-pdf`, {
    token: token(),
  })
}

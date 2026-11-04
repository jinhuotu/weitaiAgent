import { apiDownload, apiRequest } from './api'
import { getAccessToken } from './auth'

export type QuotePurpose = 'cost' | 'quote' | 'budget'

export type QuoteLine = {
  seq: string
  code: string
  name: string
  spec: string
  unit: string
  qty: number
  unitPrice: number
  costPrice: number
  sellPrice: number
  amount: number
  source: string
  matchName: string
  matchScore?: number
  matchStatus?: 'none' | 'estimate' | 'official' | 'confirmed' | string
  note: string
}

export type QuoteVerifyIssue = {
  code: string
  level: string
  row: number
  rows?: number[]
  message: string
  expect?: number
}

export type QuoteVerifyReport = {
  ok: boolean
  errorCount: number
  warnCount: number
  issues: QuoteVerifyIssue[]
  lineCount: number
}

export type QuoteRates = {
  laborCoef: number
  measureRate: number
  manageRate: number
  profitRate: number
  taxRate: number
  budgetCoef: number
  contingencyRate: number
}

export type QuoteCostSummary = {
  materialCost: number
  laborCost: number
  measureFee: number
  manageFee: number
  costExTax: number
  profit: number
  quoteExTax: number
  contingency: number
  budgetExTax: number
  taxRate: number
  costIncTax: number
  quoteIncTax: number
  budgetIncTax: number
  rates: QuoteRates
}

export type QuoteScheme = {
  name: string
  coef: number
  totalExTax: number
  margin: number
  tip: string
  risks: string[]
  recommended?: boolean
  applied?: boolean
}

export type QuoteRecognizeResult = {
  purpose?: QuotePurpose
  stage?: string
  projectName: string
  location?: string
  durationDays?: number | null
  lines: QuoteLine[]
  warnings: string[]
  catalogCount: number
  unmatched: number
  baseId?: string
  baseName?: string
  catalog?: { fileCount?: number; itemCount?: number; files?: string[]; errors?: string[] }
  verify?: QuoteVerifyReport
}

export type QuoteGenerateResult = {
  id?: string
  purpose?: QuotePurpose
  stage?: string
  xlsxFile: string
  downloadName: string
  totalExTax: number
  totalIncTax: number
  unmatched: number
  lineCount: number
  costSummary?: QuoteCostSummary
  verify?: QuoteVerifyReport
  schemes?: QuoteScheme[]
  instruction?: string
  status?: string
  schemeName?: string
  seeCost?: boolean
}

export type QuoteRecordItem = {
  id: string
  purpose: QuotePurpose | string
  stage: string
  projectName: string
  note: string
  location?: string
  durationDays?: number | null
  bidCeiling?: number | null
  competition?: string
  targetMargin?: number | null
  baseId: string
  baseName: string
  taxRate: number
  totalExTax: number
  totalIncTax: number
  unmatched: number
  lineCount: number
  xlsxFile: string
  downloadName: string
  xlsxAvailable: boolean
  username: string
  createdAt: number
  lines?: QuoteLine[]
  rates?: QuoteRates | null
  verify?: QuoteVerifyReport | null
  schemes?: QuoteScheme[] | null
  instruction?: string
  costSummary?: QuoteCostSummary
  status?: string
  schemeName?: string
  lockedAt?: number
  lockedBy?: string
  seeCost?: boolean
  catalog?: { fileCount?: number; itemCount?: number; files?: string[]; errors?: string[] }
}

export const PURPOSE_META: Record<
  QuotePurpose,
  { title: string; desc: string; exportLabel: string; href: string }
> = {
  cost: {
    title: 'AI造价智能体',
    desc: '规划图/工程量 → 核算 → 造价测算表（成本侧）',
    exportLabel: '生成造价测算表',
    href: '/quotes-cost',
  },
  quote: {
    title: 'AI报价智能体',
    desc: '规划图/工程量 → 核算 → 造价 → 多方案投标报价',
    exportLabel: '生成并下载报价 Excel',
    href: '/quotes',
  },
  budget: {
    title: 'AI预算智能体',
    desc: '规划图/工程量 → 核算 → 控制预算表（目标成本）',
    exportLabel: '生成控制预算表',
    href: '/quotes-budget',
  },
}

export function purposeFromPath(path: string): QuotePurpose {
  if (path.includes('quotes-cost')) return 'cost'
  if (path.includes('quotes-budget')) return 'budget'
  return 'quote'
}

function token() {
  const t = getAccessToken()
  if (!t) throw new Error('请先登录')
  return t
}

export function emptyQuoteLine(): QuoteLine {
  return {
    seq: '',
    code: '',
    name: '',
    spec: '',
    unit: '项',
    qty: 1,
    unitPrice: 0,
    costPrice: 0,
    sellPrice: 0,
    amount: 0,
    source: 'manual',
    matchName: '',
    matchScore: 0,
    matchStatus: 'none',
    note: '',
  }
}

export function defaultRates(): QuoteRates {
  return {
    laborCoef: 0,
    measureRate: 0.03,
    manageRate: 0.05,
    profitRate: 0.08,
    taxRate: 0.13,
    budgetCoef: 1.05,
    contingencyRate: 0.03,
  }
}

export function lineAmount(row: QuoteLine) {
  const qty = Number(row.qty) || 0
  const price = Number(row.unitPrice) || Number(row.sellPrice) || Number(row.costPrice) || 0
  return Math.round(qty * price * 100) / 100
}

export async function recognizeQuote(input: {
  files: File[]
  baseId: string
  note?: string
  projectName?: string
  purpose?: QuotePurpose
  location?: string
  durationDays?: number | null
}): Promise<QuoteRecognizeResult> {
  const form = new FormData()
  for (const f of input.files) form.append('files', f)
  form.append('baseId', input.baseId)
  form.append('purpose', input.purpose || 'quote')
  if (input.note) form.append('note', input.note)
  if (input.projectName) form.append('projectName', input.projectName)
  if (input.location) form.append('location', input.location)
  if (input.durationDays != null && input.durationDays !== undefined) {
    form.append('durationDays', String(input.durationDays))
  }
  return apiRequest<QuoteRecognizeResult>('/api/v1/quotes/recognize', {
    method: 'POST',
    token: token(),
    body: form,
  })
}

export async function parseBoqQuote(input: {
  file: File
  baseId: string
  purpose?: QuotePurpose
  projectName?: string
  location?: string
  durationDays?: number | null
}): Promise<QuoteRecognizeResult> {
  const form = new FormData()
  form.append('file', input.file)
  form.append('baseId', input.baseId)
  form.append('purpose', input.purpose || 'quote')
  if (input.projectName) form.append('projectName', input.projectName)
  if (input.location) form.append('location', input.location)
  if (input.durationDays != null && input.durationDays !== undefined) {
    form.append('durationDays', String(input.durationDays))
  }
  return apiRequest<QuoteRecognizeResult>('/api/v1/quotes/parse-boq', {
    method: 'POST',
    token: token(),
    body: form,
  })
}

export async function verifyQuote(body: {
  lines: QuoteLine[]
  applyFixes?: boolean
}): Promise<{ verify: QuoteVerifyReport; lines: QuoteLine[] | null }> {
  return apiRequest('/api/v1/quotes/verify', {
    method: 'POST',
    token: token(),
    body,
  })
}

export async function costQuote(body: {
  lines: QuoteLine[]
  rates?: QuoteRates
  purpose?: QuotePurpose
  projectName?: string
  location?: string
  durationDays?: number | null
  bidCeiling?: number | null
  competition?: string
  targetMargin?: number | null
}): Promise<{
  stage: string
  lines: QuoteLine[]
  costSummary: QuoteCostSummary
  verify: QuoteVerifyReport
  schemes?: QuoteScheme[]
}> {
  return apiRequest('/api/v1/quotes/cost', {
    method: 'POST',
    token: token(),
    body,
  })
}

export async function generateQuote(body: {
  purpose: QuotePurpose
  projectName: string
  note?: string
  location?: string
  durationDays?: number | null
  bidCeiling?: number | null
  competition?: string
  targetMargin?: number | null
  taxRate?: number
  baseId?: string
  baseName?: string
  rates?: QuoteRates
  applyVerifyFixes?: boolean
  schemeName?: string
  forceExport?: boolean
  lines: QuoteLine[]
}): Promise<QuoteGenerateResult> {
  return apiRequest<QuoteGenerateResult>('/api/v1/quotes/generate', {
    method: 'POST',
    token: token(),
    body,
  })
}

export async function downloadQuoteFile(fileName: string, downloadName: string): Promise<void> {
  const q = new URLSearchParams({ download_name: downloadName })
  await apiDownload(`/api/v1/quotes/files/${encodeURIComponent(fileName)}?${q}`, {
    token: token(),
    fallbackName: downloadName,
  })
}

export async function fetchQuoteRecords(params?: {
  q?: string
  purpose?: QuotePurpose
  status?: string
  limit?: number
  offset?: number
}): Promise<{ total: number; items: QuoteRecordItem[] }> {
  const q = new URLSearchParams()
  if (params?.q) q.set('q', params.q)
  if (params?.purpose) q.set('purpose', params.purpose)
  if (params?.status) q.set('status', params.status)
  if (params?.limit) q.set('limit', String(params.limit))
  if (params?.offset) q.set('offset', String(params.offset))
  const suffix = q.toString() ? `?${q.toString()}` : ''
  return apiRequest(`/api/v1/quotes/records${suffix}`, { token: token() })
}

export async function fetchQuoteRecord(recordId: string): Promise<QuoteRecordItem> {
  return apiRequest(`/api/v1/quotes/records/${encodeURIComponent(recordId)}`, {
    token: token(),
  })
}

export async function exportQuoteRecord(recordId: string): Promise<QuoteRecordItem> {
  return apiRequest(`/api/v1/quotes/records/${encodeURIComponent(recordId)}/export`, {
    method: 'POST',
    token: token(),
  })
}

export async function deleteQuoteRecord(recordId: string): Promise<{ deleted: boolean; id: string }> {
  return apiRequest(`/api/v1/quotes/records/${encodeURIComponent(recordId)}`, {
    method: 'DELETE',
    token: token(),
  })
}

export async function lockQuoteRecord(recordId: string): Promise<QuoteRecordItem> {
  return apiRequest(`/api/v1/quotes/records/${encodeURIComponent(recordId)}/lock`, {
    method: 'POST',
    token: token(),
  })
}

export async function unlockQuoteRecord(recordId: string): Promise<QuoteRecordItem> {
  return apiRequest(`/api/v1/quotes/records/${encodeURIComponent(recordId)}/unlock`, {
    method: 'POST',
    token: token(),
  })
}

export type CatalogHit = {
  name: string
  spec: string
  unit: string
  unitPrice: number
  costPrice: number
  sellPrice: number
  score: number
  official: boolean
  source: string
  category?: string
  scene?: string
}

export async function fetchCatalogCandidates(input: {
  baseId: string
  name: string
  spec?: string
  code?: string
}): Promise<{ items: CatalogHit[] }> {
  return apiRequest('/api/v1/quotes/catalog-candidates', {
    method: 'POST',
    token: token(),
    body: input,
  })
}

export async function clearMineQuoteRecords(purpose?: QuotePurpose): Promise<{ deleted: number; ids: string[] }> {
  const q = purpose ? `?purpose=${encodeURIComponent(purpose)}` : ''
  return apiRequest(`/api/v1/quotes/records/clear-mine${q}`, {
    method: 'POST',
    token: token(),
  })
}

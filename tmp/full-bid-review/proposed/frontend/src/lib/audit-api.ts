import { apiRequest } from './api'
import { getAccessToken } from './auth'

function requireToken(): string {
  const token = getAccessToken()
  if (!token) throw new Error('请先登录')
  return token
}

export type AuditSummary = {
  retentionDays: number
  cutoffAt: number
  operationTotal: number
  operationFail: number
  loginTotal: number
  loginFail: number
}

export type OperationLogItem = {
  id: number
  module: string
  action: string
  method: string
  path: string
  resourceId: string | null
  operatorId: number | null
  operatorUsername: string | null
  success: boolean
  statusCode: number
  ip: string
  userAgent: string
  detail: string | null
  errorMsg: string | null
  durationMs: number
  createdAt: number
}

export type LoginLogItem = {
  id: number
  username: string
  userId: number | null
  success: boolean
  reason: string
  ip: string
  userAgent: string
  createdAt: number
}

export type AuditList<T> = {
  items: T[]
  total: number
  retentionDays: number
  cutoffAt: number
}

export async function fetchAuditSummary(): Promise<AuditSummary> {
  return apiRequest<AuditSummary>('/api/v1/audit/summary', {
    token: requireToken(),
  })
}

export async function fetchOperationLogs(params?: {
  module?: string
  success?: boolean
  keyword?: string
  limit?: number
  offset?: number
}): Promise<AuditList<OperationLogItem>> {
  const q = new URLSearchParams()
  if (params?.module) q.set('module', params.module)
  if (params?.success !== undefined) q.set('success', String(params.success))
  if (params?.keyword) q.set('keyword', params.keyword)
  if (params?.limit) q.set('limit', String(params.limit))
  if (params?.offset) q.set('offset', String(params.offset))
  const qs = q.toString()
  return apiRequest<AuditList<OperationLogItem>>(
    `/api/v1/audit/operations${qs ? `?${qs}` : ''}`,
    { token: requireToken() },
  )
}

export async function fetchLoginLogs(params?: {
  success?: boolean
  username?: string
  limit?: number
  offset?: number
}): Promise<AuditList<LoginLogItem>> {
  const q = new URLSearchParams()
  if (params?.success !== undefined) q.set('success', String(params.success))
  if (params?.username) q.set('username', params.username)
  if (params?.limit) q.set('limit', String(params.limit))
  if (params?.offset) q.set('offset', String(params.offset))
  const qs = q.toString()
  return apiRequest<AuditList<LoginLogItem>>(
    `/api/v1/audit/logins${qs ? `?${qs}` : ''}`,
    { token: requireToken() },
  )
}

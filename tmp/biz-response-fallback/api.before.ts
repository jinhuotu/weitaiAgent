export type ApiEnvelope<T> = {
  code: number
  msg: string
  data: T
}

/**
 * 开发默认走空字符串 → 请求同源 `/api/*`，由 Vite proxy 转到本机 8100。
 * 这样用局域网 IP 打开前端时，其它设备不会误连自己的 127.0.0.1。
 * 需要直连后端时再设 VITE_API_BASE_URL（如 http://192.168.2.114:8100）。
 */
export function getApiBaseUrl(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL
  if (fromEnv) return fromEnv.replace(/\/$/, '')
  if (import.meta.env.DEV) return ''
  return 'http://127.0.0.1:8100'
}

export class ApiError extends Error {
  code: number
  status: number

  constructor(message: string, code: number, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

type RequestOptions = {
  method?: string
  body?: unknown
  token?: string | null
  signal?: AbortSignal
  /** internal: already retried after token refresh */
  _retry?: boolean
}

function friendlyMessage(status: number, msg?: string): string {
  const detail = (msg || '').trim()
  const unauthorized = /^unauthorized$/i.test(detail) || detail === 'Not authenticated'
  if (status === 401) {
    if (detail && !unauthorized) {
      return `登录已过期或未登录（${detail}）。请重新登录后再试`
    }
    return '登录已过期或未登录，请重新登录后再试'
  }
  if (unauthorized) {
    return '知识库或模型鉴权失败。请检查「模型管理」中的 Embedding API Key，或先取消勾选知识库再识别'
  }
  if (status === 403) {
    return '没有权限执行此操作'
  }
  if (status === 404) {
    return (
      (msg && msg.trim()) ||
      '接口不存在（404）。若刚加过后端路由，请先关掉占用 8100 的旧 API 进程再启动'
    )
  }
  if (status === 422) {
    return detail || '提交内容未通过校验，请检查必填项'
  }
  // 优先展示后端具体错误（如 MCP stdio 失败原因）
  if (msg && msg.trim()) {
    return msg.trim()
  }
  if (status === 0 || status >= 500) {
    return '服务暂时不可用，请稍后重试'
  }
  return '请求失败'
}

function envelopeMessage(payload: ApiEnvelope<unknown> & { detail?: unknown }): string | undefined {
  if (payload.msg && String(payload.msg).trim() && payload.msg !== 'ok') {
    return String(payload.msg).trim()
  }
  if (typeof payload.detail === 'string' && payload.detail.trim()) return payload.detail.trim()
  if (Array.isArray(payload.detail)) {
    const parts = payload.detail.map((item) => {
      if (!item || typeof item !== 'object') return String(item)
      const row = item as { loc?: unknown; msg?: unknown }
      const loc = Array.isArray(row.loc)
        ? row.loc.filter((x) => x !== 'body' && x !== 'query').join('.')
        : ''
      const msg = String(row.msg || '').trim()
      if (loc && msg) return `${loc}: ${msg}`
      return msg
    }).filter(Boolean)
    if (parts.length) return parts.join('；')
  }
  return undefined
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = 'GET', body, token, signal, _retry } = options
  const headers: Record<string, string> = {
    Accept: 'application/json',
  }
  const isForm = typeof FormData !== 'undefined' && body instanceof FormData
  // FormData 由浏览器带 multipart boundary；不要手写 JSON Content-Type
  if (body !== undefined && !isForm) {
    headers['Content-Type'] = 'application/json'
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`
    // 部分代理会丢掉 Authorization；后端同时认这个头
    headers['X-Access-Token'] = token
  }

  let res: Response
  try {
    res = await fetch(`${getApiBaseUrl()}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
      signal,
    })
  } catch (e) {
    // AbortSignal.timeout / 用户取消：必须原样抛出，否则会被误报成「后端未启动」
    const name = e instanceof Error ? e.name : ''
    if (
      signal?.aborted ||
      name === 'AbortError' ||
      name === 'TimeoutError' ||
      (e instanceof DOMException && (e.name === 'AbortError' || e.name === 'TimeoutError'))
    ) {
      throw e
    }
    throw new ApiError('无法连接后端服务，请确认 API 已启动', -1, 0)
  }

  let payload: ApiEnvelope<T> | null = null
  try {
    payload = (await res.json()) as ApiEnvelope<T>
  } catch {
    throw new ApiError(friendlyMessage(res.status, res.statusText), -1, res.status)
  }

  const isAuthEndpoint =
    path.includes('/auth/login') || path.includes('/auth/refresh')
  if (
    res.status === 401 &&
    !_retry &&
    !isAuthEndpoint &&
    Boolean(token)
  ) {
    const { refreshTokens, getAccessToken, clearTokens } = await import('./auth')
    const refreshed = await refreshTokens()
    if (refreshed) {
      return apiRequest<T>(path, {
        ...options,
        token: getAccessToken(),
        _retry: true,
      })
    }
    clearTokens()
    throw new ApiError(friendlyMessage(401, envelopeMessage(payload)), payload.code ?? 40100, 401)
  }

  if (!res.ok || payload.code !== 0) {
    throw new ApiError(
      friendlyMessage(res.status, envelopeMessage(payload)),
      payload.code ?? -1,
      res.status,
    )
  }
  return payload.data
}

/** 带鉴权下载二进制文件并触发浏览器保存 */
export async function apiDownload(
  path: string,
  options: { token?: string | null; fallbackName?: string } = {},
): Promise<void> {
  const { token, fallbackName = 'download.bin' } = options
  const headers: Record<string, string> = { Accept: '*/*' }
  if (token) {
    headers.Authorization = `Bearer ${token}`
    headers['X-Access-Token'] = token
  }

  let res: Response
  try {
    res = await fetch(`${getApiBaseUrl()}${path}`, { method: 'GET', headers })
  } catch {
    throw new ApiError('无法连接后端服务，请确认 API 已启动', -1, 0)
  }

  if (res.status === 401 && token) {
    const { refreshTokens, getAccessToken, clearTokens } = await import('./auth')
    const refreshed = await refreshTokens()
    if (refreshed) {
      return apiDownload(path, { token: getAccessToken(), fallbackName })
    }
    clearTokens()
    throw new ApiError(friendlyMessage(401), 40100, 401)
  }

  if (!res.ok) {
    let msg = res.statusText
    try {
      const j = (await res.json()) as { msg?: string }
      if (j?.msg) msg = j.msg
    } catch {
      /* ignore */
    }
    throw new ApiError(friendlyMessage(res.status, msg), -1, res.status)
  }

  const blob = await res.blob()
  let filename = fallbackName
  const cd = res.headers.get('Content-Disposition') || ''
  const mStar = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(cd)
  const m = /filename\s*=\s*"?([^";]+)"?/i.exec(cd)
  if (mStar?.[1]) {
    try {
      filename = decodeURIComponent(mStar[1])
    } catch {
      filename = mStar[1]
    }
  } else if (m?.[1]) {
    filename = m[1]
  }

  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** 带鉴权拉取二进制，返回 Blob（用于预览） */
export async function apiFetchBlob(
  path: string,
  options: { token?: string | null } = {},
): Promise<Blob> {
  const { token } = options
  const headers: Record<string, string> = { Accept: '*/*' }
  if (token) {
    headers.Authorization = `Bearer ${token}`
    headers['X-Access-Token'] = token
  }

  let res: Response
  try {
    res = await fetch(`${getApiBaseUrl()}${path}`, { method: 'GET', headers })
  } catch {
    throw new ApiError('无法连接后端服务，请确认 API 已启动', -1, 0)
  }

  if (res.status === 401 && token) {
    const { refreshTokens, getAccessToken, clearTokens } = await import('./auth')
    const refreshed = await refreshTokens()
    if (refreshed) {
      return apiFetchBlob(path, { token: getAccessToken() })
    }
    clearTokens()
    throw new ApiError(friendlyMessage(401), 40100, 401)
  }

  if (!res.ok) {
    let msg = res.statusText
    try {
      const j = (await res.json()) as { msg?: string }
      if (j?.msg) msg = j.msg
    } catch {
      /* ignore */
    }
    throw new ApiError(friendlyMessage(res.status, msg), -1, res.status)
  }
  return res.blob()
}

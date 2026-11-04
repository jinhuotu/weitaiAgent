import { apiRequest } from './api'

const ACCESS_KEY = 'youqi_access_token'
const REFRESH_KEY = 'youqi_refresh_token'

export type AuthUser = {
  id: number
  username: string
  display_name: string | null
  email: string | null
  is_superuser: boolean
  roles: string[]
  /** 后端下发的可访问菜单 href；缺省时前端仅按 adminOnly 过滤 */
  menus?: string[]
}

export type LoginResult = {
  access_token: string
  refresh_token: string
  token_type: string
  user: AuthUser
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY)
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}

export function setTokens(access: string, refresh: string): void {
  localStorage.setItem(ACCESS_KEY, access)
  localStorage.setItem(REFRESH_KEY, refresh)
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

export async function login(username: string, password: string): Promise<LoginResult> {
  const data = await apiRequest<LoginResult>('/api/v1/auth/login', {
    method: 'POST',
    body: { username, password },
  })
  setTokens(data.access_token, data.refresh_token)
  return data
}

export async function fetchMe(): Promise<AuthUser> {
  return apiRequest<AuthUser>('/api/v1/auth/me', {
    token: getAccessToken(),
  })
}

export async function refreshTokens(): Promise<LoginResult | null> {
  const refresh = getRefreshToken()
  if (!refresh) return null
  try {
    const data = await apiRequest<Omit<LoginResult, 'user'> & { user?: AuthUser }>(
      '/api/v1/auth/refresh',
      {
        method: 'POST',
        body: { refresh_token: refresh },
      },
    )
    setTokens(data.access_token, data.refresh_token)
    const user = await fetchMe()
    return { ...data, user, token_type: data.token_type || 'bearer' }
  } catch {
    clearTokens()
    return null
  }
}

export function logout(): void {
  clearTokens()
}

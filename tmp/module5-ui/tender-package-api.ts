import { apiRequest, apiDownload } from './api'
import { getAccessToken } from './auth'
const base = '/api/v1/tender-package'
export function packageRequest<T = any>(path = '', method = 'GET', body?: unknown) {
  return apiRequest<T>(base + path, { method, body, token: getAccessToken() })
}
export function packageDownload(id: string, index: number, name: string) {
  return apiDownload(`${base}/${id}/artifacts/${index}`, { token: getAccessToken(), fallbackName: name })
}

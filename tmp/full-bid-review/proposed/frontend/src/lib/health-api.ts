import { apiRequest } from './api'

export async function fetchHealth(): Promise<{ status: string; service?: string }> {
  return apiRequest('/api/v1/health')
}

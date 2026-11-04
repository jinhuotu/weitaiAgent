import { apiDownload, apiRequest } from './api'
import { getAccessToken } from './auth'

export type MaterialUse = { enabled: boolean; fileIds: string[]; mode: 'scan' | 'reference'; targetSection: string }
export type MaterialSelections = Record<string, MaterialUse>
export type ArchiveCategory = { key: string; title: string; scope: 'project' | 'library'; subtypes: string[] }
export type ArchiveFile = { id: string; category: string; filename: string; entity: string; subtype: string; chars: number; parseStatus: string; note: string }
export type MaterialCheck = {
  selected: { id: string; category: string; filename: string; mode: string; targetSection: string; issues: string[] }[]
  warnings: string[]
  missing: { category: string; entity: string; missing: string[] }[]
  note: string
}
const root = '/api/v1/tenders/materials'
export function fetchMaterials(invitationId = '') {
  return apiRequest<{ categories: ArchiveCategory[]; files: ArchiveFile[] }>(`${root}?invitationId=${encodeURIComponent(invitationId)}`, { token: getAccessToken() })
}
export function uploadMaterial(file: File, category: string, subtype: string, entity: string, invitationId = '', bidderName = '') {
  const body = new FormData()
  body.append('file', file); body.append('category', category); body.append('subtype', subtype)
  body.append('bidderName', bidderName); body.append('entity', entity); body.append('invitationId', invitationId)
  return apiRequest<ArchiveFile>(root, { method: 'POST', token: getAccessToken(), body })
}
export function checkMaterials(brief: unknown) {
  return apiRequest<MaterialCheck>(`${root}/preflight`, { method: 'POST', token: getAccessToken(), body: brief })
}
export function fetchMaterialSections(invitationId: string) {
  return apiRequest<{ sections: string[] }>(`${root}/sections`, { method: 'POST', token: getAccessToken(), body: { invitationId } })
}
export function downloadMaterial(file: ArchiveFile, invitationId = '') {
  return apiDownload(`${root}/${file.category}/${file.id}?invitationId=${encodeURIComponent(invitationId)}`, { token: getAccessToken(), fallbackName: file.filename })
}
export function deleteMaterial(file: ArchiveFile, invitationId = '') {
  return apiRequest(`${root}/${file.category}/${file.id}?invitationId=${encodeURIComponent(invitationId)}`, { method: 'DELETE', token: getAccessToken() })
}

/**
 * 知识库视频契约常量。
 * 全文：weitaiAgent/docs/kb-video-contract.md
 */

import { getAccessToken } from '@/lib/auth'

export const KB_VIDEO_EXTS = ['mp4', 'webm'] as const

export type KbVideoExt = (typeof KB_VIDEO_EXTS)[number]

export const KB_VIDEO_KIND = 'video' as const

export const KB_VIDEO_PREVIEW_KIND = 'video' as const

/** 默认 512MB；实际上限以后端 KB_VIDEO_UPLOAD_MAX_BYTES 为准 */
export const KB_VIDEO_UPLOAD_MAX_BYTES_DEFAULT = 512 * 1024 * 1024

export const KB_VIDEO_MIME: Record<KbVideoExt, string> = {
  mp4: 'video/mp4',
  webm: 'video/webm',
}

export const KB_VIDEO_SUMMARY_PENDING = '视频原片（待转写，暂不可检索）'
export const KB_VIDEO_SUMMARY_ASR = '正在语音转写…'
export const KB_VIDEO_SUMMARY_EMBED = '正在写入检索…'

export function isKbVideoExt(ext: string | null | undefined): boolean {
  const t = (ext || '').toLowerCase().replace(/^\./, '')
  return (KB_VIDEO_EXTS as readonly string[]).includes(t)
}

export function isKbVideoDoc(it: {
  kind?: string | null
  fileType?: string | null
}): boolean {
  if ((it.kind || '') === KB_VIDEO_KIND) return true
  return isKbVideoExt(it.fileType)
}

export function isKbVideoRef(r: {
  preview_kind?: string | null
  file_type?: string | null
  kind?: string | null
  startMs?: number | null
}): boolean {
  if ((r.preview_kind || '') === KB_VIDEO_PREVIEW_KIND) return true
  if ((r.kind || '') === KB_VIDEO_KIND) return true
  if (isKbVideoExt(r.file_type)) return true
  return r.startMs != null
}

export function fmtKbVideoClock(ms?: number | null): string {
  if (ms == null || Number.isNaN(Number(ms))) return ''
  const s = Math.max(0, Math.floor(Number(ms) / 1000))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}

export function fmtKbVideoRange(
  startMs?: number | null,
  endMs?: number | null,
): string {
  const a = fmtKbVideoClock(startMs)
  const b = fmtKbVideoClock(endMs)
  if (a && b) return `${a}–${b}`
  return a || b || ''
}

/**
 * 原件流式地址（支持 Range）。
 * <video src> 需 withToken:true（query access_token），因无法带 Authorization。
 */
export function kbVideoFilePath(
  docId: string,
  opts: { baseId: string; withToken?: boolean },
): string {
  const id = String(docId || '').trim()
  const q = new URLSearchParams()
  q.set('baseId', String(opts.baseId || '').trim())
  if (opts.withToken) {
    const t = getAccessToken()
    if (t) q.set('access_token', t)
  }
  return `/api/v1/knowledge/documents/${encodeURIComponent(id)}/file?${q.toString()}`
}

export type KbVideoHit = {
  docId: string
  baseId: string
  name: string
  fileType?: string
  startMs?: number
  endMs?: number
}

/** 同一 doc 多条 chunk 只留一个；取最早 startMs 便于定位 */
export function collectKbVideoHits(
  refs: Array<{
    doc_id?: string
    kb_id?: string
    kbId?: string
    name?: string
    file_type?: string
    has_file?: boolean
    preview_kind?: string
    kind?: string
    startMs?: number
    endMs?: number
  }> | null | undefined,
): KbVideoHit[] {
  const best = new Map<string, KbVideoHit>()
  for (const r of refs || []) {
    if (!isKbVideoRef(r) || r.has_file === false) continue
    const docId = String(r.doc_id || '').trim()
    const baseId = String(r.kb_id || r.kbId || '').trim()
    if (!docId || !baseId) continue
    const prev = best.get(docId)
    if (!prev) {
      best.set(docId, {
        docId,
        baseId,
        name: (r.name || '').trim() || '视频资料',
        fileType: r.file_type,
        startMs: r.startMs,
        endMs: r.endMs,
      })
      continue
    }
    if (
      r.startMs != null &&
      (prev.startMs == null || Number(r.startMs) < Number(prev.startMs))
    ) {
      prev.startMs = r.startMs
      if (r.endMs != null) prev.endMs = r.endMs
    }
  }
  return [...best.values()]
}

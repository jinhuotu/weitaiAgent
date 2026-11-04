/**
 * 知识库上传校验：原文件交给后端解析，前端只做类型/大小拦截。
 */

import {
  KB_VIDEO_EXTS,
  KB_VIDEO_UPLOAD_MAX_BYTES_DEFAULT,
  isKbVideoExt,
} from '@/lib/kb-video-contract'

const THREE_D_EXTS = ['fbx', 'obj', 'gltf', 'glb', 'stl']

export const KB_UPLOAD_MAX_BYTES = 200 * 1024 * 1024

export const KB_UPLOAD_EXTS = [
  'pdf',
  'doc',
  'docx',
  'xlsx',
  'xls',
  'pptx',
  'txt',
  'md',
  'csv',
  'json',
  'xml',
  'yaml',
  'yml',
  'png',
  'jpg',
  'jpeg',
  'webp',
  'gif',
  'bmp',
  ...KB_VIDEO_EXTS,
] as const

const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff']
const CAD_EXTS = ['dwg', 'dxf', 'step', 'stp', 'iges', 'igs']

const ALLOWED_HINT =
  'pdf / doc / docx / xlsx / xls / pptx / txt / md / csv / json / xml / yaml / png / jpg / webp / mp4 / webm'

export function fileExt(name: string): string {
  return name.split('.').pop()?.toLowerCase() || ''
}

function fmtSize(n?: number) {
  if (!n && n !== 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

export { fmtSize }

/** 不通过时返回错误文案；通过返回 null。 */
export function validateKbUploadFile(file: File): string | null {
  if (file.size <= 0) return '上传文件为空'
  const t = fileExt(file.name)
  const video = isKbVideoExt(t)
  const maxBytes = video ? KB_VIDEO_UPLOAD_MAX_BYTES_DEFAULT : KB_UPLOAD_MAX_BYTES
  if (file.size > maxBytes) {
    return `文件超过大小上限（${fmtSize(maxBytes)}）`
  }
  if ((KB_UPLOAD_EXTS as readonly string[]).includes(t)) return null
  if (t === 'tif' || t === 'tiff') {
    return `暂不支持 .${t}，请先转为 png / jpg。当前支持：${ALLOWED_HINT}`
  }
  if (t === 'ppt') return '旧版 .ppt 请另存为 .pptx 后上传'
  if (THREE_D_EXTS.includes(t) || CAD_EXTS.includes(t) || IMAGE_EXTS.includes(t)) {
    return `暂不支持 .${t}。当前支持：${ALLOWED_HINT}`
  }
  return `不支持的文件类型${t ? ` .${t}` : ''}。当前支持：${ALLOWED_HINT}`
}

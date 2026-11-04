<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { renderAsync } from 'docx-preview'
import { Loader2, RefreshCw, TriangleAlert, ZoomIn, ZoomOut } from 'lucide-vue-next'
import { ApiError } from '@/lib/api'
import {
  fetchTenderDocxBlob,
  fetchTenderEditorConfig,
  fetchTenderPreviewPdfBlob,
  forceSaveTenderDocx,
  type TenderDocPreview,
} from '@/lib/tenders-api'

const props = defineProps<{
  docxFile: string
  downloadName: string
  fullscreen?: boolean
  layoutTick?: number
  /** view=只读预览（更快）；edit=可改稿。browser 引擎忽略，始终只读。 */
  mode?: 'edit' | 'view'
  /** 后端 defaults.docPreview；缺省时再问 editor-config */
  engine?: TenderDocPreview | string
}>()

const loading = ref(true)
const error = ref('')
const dirty = ref(false)
const activeEngine = ref<TenderDocPreview>('browser')
const editorHost = ref<HTMLElement | null>(null)
const editorRoot = ref<HTMLElement | null>(null)
const userZoom = ref(1)
const MIN_ZOOM = 0.5
const MAX_ZOOM = 2.2
const ZOOM_STEP = 0.1
let docEditor: { destroyEditor?: () => void } | null = null
let resizeObserver: ResizeObserver | null = null
let notifyingResize = false
let bootGen = 0
let pdfObjectUrl = ''
let hasEdits = false
let openedDocKey = ''

declare global {
  interface Window {
    DocsAPI?: {
      DocEditor: (
        id: string,
        config: Record<string, unknown>,
      ) => { destroyEditor?: () => void }
    }
  }
}

function measureHostHeight(): number {
  const box = editorRoot.value || editorHost.value
  if (!box) return 520
  const h = Math.floor(box.getBoundingClientRect().height)
  if (h >= 400) return h
  const top = box.getBoundingClientRect().top
  return Math.max(Math.floor(window.innerHeight - top - 64), 480)
}

function fitBrowserPages() {
  if (activeEngine.value !== 'browser') return
  const host = editorHost.value
  if (!host) return
  const wrapper = host.querySelector<HTMLElement>('.tender-docx-wrapper, .docx-wrapper')
  if (!wrapper) return
  wrapper.style.transform = ''
  wrapper.style.marginBottom = ''
  const pages = host.querySelectorAll<HTMLElement>('section.tender-docx, section.docx')
  const page = pages[0]
  if (!page) return
  const pageW = page.offsetWidth
  const avail = Math.max(host.clientWidth - 28, 240)
  if (pageW <= 0 || avail <= 0) return
  const fit = Math.min(1, avail / pageW)
  const scale = fit * userZoom.value
  if (scale >= 0.995 && userZoom.value === 1) return
  wrapper.style.transformOrigin = 'top center'
  wrapper.style.transform = `scale(${scale})`
  wrapper.style.marginBottom = `${Math.round(-(1 - scale) * wrapper.scrollHeight)}px`
}

function applyPdfZoom(resetViewer = false) {
  if (activeEngine.value !== 'pdf') return
  const host = editorHost.value
  const frame = host?.querySelector('iframe')
  if (!host || !frame) return
  const scale = userZoom.value
  if (resetViewer && pdfObjectUrl) {
    const hash = scale === 1 ? 'zoom=page-width' : `zoom=${Math.round(scale * 100)}`
    frame.src = `${pdfObjectUrl}#${hash}`
  }
  frame.style.transformOrigin = 'top center'
  frame.style.width = '100%'
  frame.style.height = '100%'
  if (scale === 1) {
    frame.style.transform = ''
    host.style.overflow = 'hidden'
    return
  }
  frame.style.transform = `scale(${scale})`
  host.style.overflow = 'auto'
}

function applyPreviewZoom(resetPdf = false) {
  if (activeEngine.value === 'browser') fitBrowserPages()
  else if (activeEngine.value === 'pdf') applyPdfZoom(resetPdf)
}

function changeZoom(next: number) {
  userZoom.value = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(next * 10) / 10))
  applyPreviewZoom(activeEngine.value === 'pdf')
}

function zoomFit() {
  userZoom.value = 1
  applyPreviewZoom(true)
}

function fillEditorFrame() {
  const host = editorHost.value
  if (!host || activeEngine.value === 'browser') return
  const h = measureHostHeight()
  const box = editorRoot.value || host
  const w = Math.max(Math.floor(box.getBoundingClientRect().width), 320)
  const apply = (el: HTMLElement) => {
    el.style.setProperty('width', '100%', 'important')
    el.style.setProperty('height', `${h}px`, 'important')
    el.style.setProperty('min-height', `${h}px`, 'important')
  }
  apply(host)
  host.querySelectorAll<HTMLElement>(':scope > div, iframe').forEach(apply)
  const iframe = host.querySelector('iframe')
  if (iframe) {
    iframe.setAttribute('height', String(h))
    iframe.setAttribute('width', String(w))
  }
}

function notifyEditorResize() {
  if (notifyingResize) return
  notifyingResize = true
  window.dispatchEvent(new Event('resize'))
  window.setTimeout(() => {
    notifyingResize = false
  }, 0)
}

function destroyEditor() {
  resizeObserver?.disconnect()
  resizeObserver = null
  try {
    docEditor?.destroyEditor?.()
  } catch {
    /* ignore */
  }
  docEditor = null
  if (pdfObjectUrl) {
    URL.revokeObjectURL(pdfObjectUrl)
    pdfObjectUrl = ''
  }
  const host = editorHost.value
  if (host) host.innerHTML = ''
}

function loadScript(src: string): Promise<void> {
  const existing = document.querySelector(`script[data-onlyoffice="1"]`) as HTMLScriptElement | null
  if (existing?.src === src && window.DocsAPI) {
    return Promise.resolve()
  }
  existing?.remove()
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = src
    script.async = true
    script.dataset.onlyoffice = '1'
    script.onload = () => resolve()
    script.onerror = () => reject(new Error(`OnlyOffice 脚本加载失败：${src}。请从这台电脑检查地址和 8082 端口`))
    document.head.appendChild(script)
  })
}

function watchHostResize(onHeightChange: (height: number) => void) {
  const box = editorRoot.value || editorHost.value
  if (!box || typeof ResizeObserver === 'undefined') return
  resizeObserver?.disconnect()
  resizeObserver = new ResizeObserver(() => {
    fillEditorFrame()
    const h = measureHostHeight()
    onHeightChange(h)
  })
  resizeObserver.observe(box)
}

async function bootPdf(host: HTMLElement, gen: number): Promise<boolean> {
  try {
    const blob = await fetchTenderPreviewPdfBlob(props.docxFile)
    if (gen !== bootGen) return false
    if (!blob || blob.size < 32) return false
    const url = URL.createObjectURL(blob)
    if (pdfObjectUrl) URL.revokeObjectURL(pdfObjectUrl)
    pdfObjectUrl = url
    host.innerHTML = ''
    const frame = document.createElement('iframe')
    frame.src = `${url}#zoom=page-width`
    frame.title = props.downloadName || '投标文件预览'
    host.appendChild(frame)
    activeEngine.value = 'pdf'
    fillEditorFrame()
    watchHostResize(() => {
      fillEditorFrame()
      applyPdfZoom()
    })
    return true
  } catch {
    return false
  }
}

async function bootBrowser(host: HTMLElement, gen: number) {
  const blob = await fetchTenderDocxBlob(props.docxFile)
  if (gen !== bootGen) return
  host.innerHTML = ''
  await renderAsync(blob, host, undefined, {
    className: 'tender-docx',
    inWrapper: true,
    ignoreWidth: false,
    ignoreHeight: false,
    breakPages: true,
    ignoreLastRenderedPageBreak: true,
    experimental: true,
    useBase64URL: true,
    renderHeaders: true,
    renderFooters: true,
    renderFootnotes: true,
    renderEndnotes: true,
  })
  await nextTick()
  fitBrowserPages()
  watchHostResize(() => {
    fitBrowserPages()
  })
}

async function bootOnlyOffice(gen: number) {
  const heightPx = measureHostHeight()
  if (heightPx < 400) {
    throw new Error('编辑器区域高度不足，请放大窗口后重试')
  }

  const data = await fetchTenderEditorConfig(
    props.docxFile,
    props.downloadName,
    heightPx,
    props.mode === 'view' ? 'view' : 'edit',
  )
  if (gen !== bootGen) return

  if ((data.engine || '').toLowerCase() === 'browser' || !data.documentServerUrl) {
    activeEngine.value = 'browser'
    const host = editorHost.value
    if (!host) return
    await bootBrowser(host, gen)
    return
  }

  const scriptUrl = `${data.documentServerUrl}/web-apps/apps/api/documents/api.js`
  await loadScript(scriptUrl)
  if (gen !== bootGen) return
  if (!window.DocsAPI) {
    throw new Error('OnlyOffice API 未就绪')
  }

  const host = editorHost.value
  if (!host) return

  fillEditorFrame()
  await nextTick()

  const editorId = `tender-doc-editor-${props.docxFile.replace(/\W/g, '')}-${props.mode || 'edit'}`
  host.id = editorId
  host.innerHTML = ''

  const config = {
    ...(data.config as Record<string, unknown>),
    events: {
      onDocumentStateChange(event: { data?: boolean } | boolean) {
        const changed = typeof event === 'boolean' ? event : !!event?.data
        dirty.value = changed
        if (changed) hasEdits = true
      },
      onError(event: { data?: { errorCode?: number; errorDescription?: string } }) {
        const detail = event?.data?.errorDescription || ''
        error.value = detail
          ? `OnlyOffice 打开失败：${detail}`
          : 'OnlyOffice 无法拉取或转换 Word（常见于容器连不上 API，或上次转换失败被缓存）'
      },
    },
  }
  openedDocKey = String(((data.config || {}) as { document?: { key?: string } }).document?.key || '')
  docEditor = window.DocsAPI.DocEditor(editorId, config)

  watchHostResize(() => {
    if (loading.value || error.value) return
    fillEditorFrame()
    notifyEditorResize()
  })

  window.setTimeout(() => {
    fillEditorFrame()
    notifyEditorResize()
  }, 150)
  window.setTimeout(() => {
    fillEditorFrame()
    notifyEditorResize()
  }, 800)
}

async function bootYozo(gen: number) {
  const heightPx = measureHostHeight()
  if (heightPx < 400) {
    throw new Error('编辑器区域高度不足，请放大窗口后重试')
  }
  const data = await fetchTenderEditorConfig(
    props.docxFile,
    props.downloadName,
    heightPx,
    props.mode === 'view' ? 'view' : 'edit',
  )
  if (gen !== bootGen) return
  if ((data.engine || '').toLowerCase() !== 'yozo' || !data.iframeUrl) {
    activeEngine.value = 'browser'
    const host = editorHost.value
    if (!host) return
    await bootBrowser(host, gen)
    return
  }

  const host = editorHost.value
  if (!host) return
  fillEditorFrame()
  await nextTick()
  host.innerHTML = ''
  const iframe = document.createElement('iframe')
  iframe.src = data.iframeUrl
  iframe.title = props.downloadName || '永中在线改稿'
  iframe.setAttribute('allowfullscreen', 'true')
  iframe.setAttribute('allow', 'fullscreen')
  host.appendChild(iframe)

  watchHostResize(() => {
    if (loading.value || error.value) return
    fillEditorFrame()
  })
  window.setTimeout(() => fillEditorFrame(), 150)
}

async function boot() {
  if (!props.docxFile) return
  const gen = ++bootGen
  loading.value = true
  error.value = ''
  dirty.value = false
  hasEdits = false
  openedDocKey = ''
  userZoom.value = 1
  destroyEditor()

  await nextTick()
  await nextTick()

  try {
    const hinted = String(props.engine || '').toLowerCase()
    const host = editorHost.value
    if (!host) return

    if (hinted === 'yozo') {
      activeEngine.value = 'yozo'
      await bootYozo(gen)
    } else if (hinted === 'onlyoffice') {
      activeEngine.value = 'onlyoffice'
      await bootOnlyOffice(gen)
    } else if (props.mode !== 'edit') {
      activeEngine.value = 'pdf'
      const used = await bootPdf(host, gen)
      if (gen !== bootGen) return
      if (!used) {
        activeEngine.value = 'browser'
        await bootBrowser(host, gen)
      }
    } else {
      activeEngine.value = 'browser'
      await bootBrowser(host, gen)
    }
  } catch (e) {
    error.value =
      e instanceof ApiError || e instanceof Error ? e.message : 'Word 预览加载失败'
  } finally {
    if (gen === bootGen) {
      loading.value = false
      await nextTick()
      fillEditorFrame()
      applyPreviewZoom()
      notifyEditorResize()
    }
  }
}

onMounted(() => {
  void boot()
})

watch(
  () => [props.docxFile, props.downloadName, props.mode, props.engine] as const,
  () => {
    void boot()
  },
)

watch(
  () => props.layoutTick,
  async () => {
    await nextTick()
    fillEditorFrame()
    fitBrowserPages()
    applyPdfZoom()
    notifyEditorResize()
    window.setTimeout(() => {
      fillEditorFrame()
      fitBrowserPages()
      applyPdfZoom()
      notifyEditorResize()
    }, 80)
  },
)

watch(
  () => props.fullscreen,
  async () => {
    await nextTick()
    fillEditorFrame()
    fitBrowserPages()
    applyPdfZoom()
    notifyEditorResize()
    window.setTimeout(() => {
      fillEditorFrame()
      fitBrowserPages()
      applyPdfZoom()
      notifyEditorResize()
    }, 80)
    window.setTimeout(() => {
      fillEditorFrame()
      fitBrowserPages()
      applyPdfZoom()
      notifyEditorResize()
    }, 400)
  },
)

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

async function readDocKey(): Promise<string> {
  const data = await fetchTenderEditorConfig(props.docxFile, props.downloadName, undefined, 'edit')
  const doc = (data.config || {}) as { document?: { key?: string } }
  return String(doc.document?.key || '')
}

/** 曾编辑过就等待回调写回；dirty=false 也可能只是自动保存尚在路上。 */
async function flushSave(): Promise<void> {
  if (activeEngine.value !== 'onlyoffice' || !docEditor) return
  if (!hasEdits) return
  if (!openedDocKey) throw new Error('无法确认模板版本，请重新打开预览')
  if (await readDocKey() !== openedDocKey) {
    hasEdits = false
    return
  }
  const idleDeadline = Date.now() + 5000
  while (dirty.value && Date.now() < idleDeadline) await sleep(300)
  if (await readDocKey() !== openedDocKey) {
    hasEdits = false
    return
  }
  await forceSaveTenderDocx(props.docxFile, openedDocKey)
  const deadline = Date.now() + 30000
  while (Date.now() < deadline) {
    const key = await readDocKey()
    if (key && key !== openedDocKey) {
      hasEdits = false
      return
    }
    await sleep(400)
  }
  throw new Error('修改还没写回，请再试一次')
}

defineExpose({ flushSave })

onBeforeUnmount(() => {
  bootGen += 1
  hasEdits = false
  destroyEditor()
})
</script>

<template>
  <div ref="editorRoot" class="tender-doc-editor">
    <div
      v-if="!loading && !error && (activeEngine === 'pdf' || activeEngine === 'browser')"
      class="tender-doc-editor__zoom"
    >
      <button
        type="button"
        class="tender-doc-editor__zoom-btn"
        :disabled="userZoom <= MIN_ZOOM"
        title="缩小"
        @click="changeZoom(userZoom - ZOOM_STEP)"
      >
        <ZoomOut class="size-3.5" />
        缩小
      </button>
      <span class="tender-doc-editor__zoom-label">{{ Math.round(userZoom * 100) }}%</span>
      <button
        type="button"
        class="tender-doc-editor__zoom-btn"
        :disabled="userZoom >= MAX_ZOOM"
        title="放大"
        @click="changeZoom(userZoom + ZOOM_STEP)"
      >
        <ZoomIn class="size-3.5" />
        放大
      </button>
      <button type="button" class="tender-doc-editor__zoom-btn" title="按页宽适应" @click="zoomFit">
        适应
      </button>
    </div>
    <div
      ref="editorHost"
      class="tender-doc-editor__host"
      :class="{
        'tender-doc-editor__host--browser': activeEngine === 'browser',
        'tender-doc-editor__host--pdf': activeEngine === 'pdf',
      }"
    />

    <div v-if="loading" class="tender-doc-editor__overlay">
      <Loader2 class="size-5 animate-spin text-muted-foreground" />
      <span class="text-[12px] text-muted-foreground">
        {{
          activeEngine === 'pdf'
            ? '正在用本机 WPS 排版预览…'
            : activeEngine === 'browser'
            ? '正在打开 Word 预览…'
            : mode === 'view'
              ? '正在打开只读预览…'
              : activeEngine === 'yozo'
                ? '正在打开永中在线改稿…'
                : '正在打开在线 Word…'
        }}
      </span>
    </div>

    <div
      v-else-if="error"
      class="tender-doc-editor__overlay tender-doc-editor__overlay--error"
    >
      <TriangleAlert class="size-5 shrink-0 text-sulfur" />
      <div class="min-w-0">
        <p class="text-[12px] text-foreground">{{ error }}</p>
        <p v-if="activeEngine === 'yozo'" class="text-[11px] text-muted-foreground mt-1">
          请确认内网已部署永中 Web Office，且后端 .env 中
          <code class="text-[10px]">TENDER_DOC_PREVIEW=yozo</code>
          与
          <code class="text-[10px]">YOZO_DOCUMENT_SERVER_URL</code>
          指向该服务。永中须能访问
          <code class="text-[10px]">YOZO_PUBLIC_API_BASE</code>
          （或
          <code class="text-[10px]">ONLYOFFICE_PUBLIC_API_BASE</code>
          ）拉取/回写 Word。
        </p>
        <p v-else-if="activeEngine === 'onlyoffice'" class="text-[11px] text-muted-foreground mt-1">
          请确认已执行
          <code class="text-[10px]">docker compose up -d onlyoffice</code>
          ，且后端 .env 中 ONLYOFFICE_* 与 compose 里 JWT_SECRET 一致。国产环境请改用
          <code class="text-[10px]">TENDER_DOC_PREVIEW=yozo</code>
          或
          <code class="text-[10px]">TENDER_DOC_PREVIEW=browser</code>。
        </p>
        <p v-else class="text-[11px] text-muted-foreground mt-1">
          请确认已登录且该 Word 仍在服务器上。也可到下一步下载后用 WPS / Word 打开。
        </p>
        <button type="button" class="tender-doc-editor__retry mt-3" @click="boot">
          <RefreshCw class="size-3.5" />
          重试
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tender-doc-editor {
  position: relative;
  flex: 1 1 0;
  min-height: 0;
  height: 100%;
  width: 100%;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--hairline, hsl(var(--border)));
  border-radius: 0.75rem;
  overflow: hidden;
  background: color-mix(in srgb, var(--bg-elevated, hsl(var(--card))) 92%, transparent);
}

.tender-doc-editor__host {
  position: absolute;
  inset: 0;
  flex: none;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.tender-doc-editor__host--browser {
  overflow: auto;
  background: #dfe3e8;
}

.tender-doc-editor__host--pdf {
  overflow: hidden;
  background: #525659;
}

.tender-doc-editor__host--browser :deep(.docx-wrapper),
.tender-doc-editor__host--browser :deep(.tender-docx-wrapper) {
  background: transparent !important;
  padding: 16px 12px 32px !important;
  box-sizing: border-box;
}

.tender-doc-editor__host--browser :deep(section.docx),
.tender-doc-editor__host--browser :deep(.tender-docx) {
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.12);
  margin: 0 auto 16px !important;
  box-sizing: border-box !important;
}

.tender-doc-editor__host :deep(iframe),
.tender-doc-editor__host :deep(> div) {
  display: block !important;
  width: 100% !important;
  height: 100% !important;
  min-height: 100% !important;
  max-height: 100% !important;
  border: 0;
}

.tender-doc-editor__host--browser :deep(iframe),
.tender-doc-editor__host--browser :deep(> div) {
  display: block !important;
  width: 100% !important;
  max-width: 100% !important;
  height: auto !important;
  min-height: 0 !important;
  max-height: none !important;
  margin-left: auto;
  margin-right: auto;
}

.tender-doc-editor__host--browser :deep(p) {
  max-width: 100%;
}

.tender-doc-editor__host--browser :deep(table) {
  max-width: 100%;
}

.tender-doc-editor__host--browser :deep(img) {
  max-width: 100% !important;
  height: auto !important;
}

.tender-doc-editor__overlay {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 2rem;
  background: color-mix(in srgb, var(--bg-elevated, hsl(var(--card))) 94%, transparent);
}

.tender-doc-editor__overlay--error {
  align-items: flex-start;
  justify-content: flex-start;
  text-align: left;
}

.tender-doc-editor__retry {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  height: 2rem;
  padding: 0 0.75rem;
  border-radius: 0.5rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  font-size: 11px;
  color: hsl(var(--foreground));
  background: hsl(var(--background));
}

.tender-doc-editor__retry:hover {
  background: hsl(var(--accent));
}

.tender-doc-editor__zoom {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.2rem;
  border-radius: 0.5rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  background: color-mix(in srgb, var(--bg-elevated, hsl(var(--card))) 92%, transparent);
  box-shadow: 0 1px 4px color-mix(in srgb, #000 8%, transparent);
}
.tender-doc-editor__zoom-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  height: 1.6rem;
  padding: 0 0.45rem;
  border: 0;
  border-radius: 0.35rem;
  font-size: 11px;
  color: hsl(var(--foreground));
  background: transparent;
  cursor: pointer;
}
.tender-doc-editor__zoom-btn:hover:not(:disabled) {
  background: hsl(var(--accent));
}
.tender-doc-editor__zoom-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.tender-doc-editor__zoom-label {
  min-width: 2.4rem;
  text-align: center;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}
</style>

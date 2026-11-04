<script setup lang="ts">
import { ref } from 'vue'
import { Download, FileDown, Loader2 } from 'lucide-vue-next'
import { getAccessToken } from '@/lib/auth'
import { getApiBaseUrl } from '@/lib/api'

export type LayoutAttachment = {
  fileName: string
  downloadName?: string
  kind?: string
  label?: string
}

const props = defineProps<{
  files: LayoutAttachment[]
}>()

const busy = ref<string | null>(null)
const err = ref('')

async function downloadOne(file: LayoutAttachment) {
  const name = (file.fileName || '').trim()
  if (!name) return
  err.value = ''
  busy.value = name
  try {
    const token = getAccessToken()
    const base = getApiBaseUrl()
    const url = `${base}/api/v1/layouts/files/${encodeURIComponent(name)}`
    const res = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!res.ok) {
      throw new Error(res.status === 401 ? '请重新登录后再下载' : `下载失败 (${res.status})`)
    }
    const blob = await res.blob()
    const a = document.createElement('a')
    const obj = URL.createObjectURL(blob)
    a.href = obj
    a.download = file.downloadName || name.replace(/-[a-f0-9]{8}(\.(svg|dxf))$/i, '$1')
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(obj)
  } catch (e) {
    err.value = e instanceof Error ? e.message : '下载失败'
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div
    v-if="files?.length"
    class="mt-2 flex flex-wrap items-center gap-2 text-[12px]"
  >
    <span class="text-text-muted inline-flex items-center gap-1">
      <FileDown class="size-3.5" />
      导出图纸
    </span>
    <button
      v-for="f in files"
      :key="f.fileName"
      type="button"
      class="inline-flex items-center gap-1 rounded border border-hairline bg-bg-base/50 px-2 py-1 text-text-primary hover:border-patina/50 hover:text-patina disabled:opacity-50"
      :disabled="busy === f.fileName"
      @click="downloadOne(f)"
    >
      <Loader2 v-if="busy === f.fileName" class="size-3 animate-spin" />
      <Download v-else class="size-3" />
      {{ f.label || f.fileName }}
    </button>
    <span v-if="err" class="w-full text-rose-600/90">{{ err }}</span>
  </div>
</template>

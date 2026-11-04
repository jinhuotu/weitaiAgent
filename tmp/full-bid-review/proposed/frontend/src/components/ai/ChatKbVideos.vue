<script setup lang="ts">
import { ref } from 'vue'
import { Download, Film, Loader2 } from 'lucide-vue-next'
import {
  fmtKbVideoRange,
  kbVideoFilePath,
  type KbVideoHit,
} from '@/lib/kb-video-contract'
import { downloadKnowledgeDocument } from '@/lib/knowledge-api'
import { ApiError } from '@/lib/api'

const props = defineProps<{
  videos: KbVideoHit[]
}>()

const errById = ref<Record<string, string>>({})
const dlBusy = ref<string | null>(null)

function srcOf(v: KbVideoHit) {
  return kbVideoFilePath(v.docId, { baseId: v.baseId, withToken: true })
}

function onLoadedMeta(ev: Event, v: KbVideoHit) {
  const el = ev.target as HTMLVideoElement
  if (v.startMs == null || Number.isNaN(Number(v.startMs))) return
  const t = Math.max(0, Number(v.startMs) / 1000)
  try {
    if (Number.isFinite(t) && t > 0) el.currentTime = t
  } catch {
    // 部分编码尚未缓冲到目标帧时会抛错，忽略即可
  }
}

function onVideoErr(v: KbVideoHit) {
  errById.value = {
    ...errById.value,
    [v.docId]: '视频加载失败，请确认已登录且有知识库查看权限',
  }
}

async function download(v: KbVideoHit) {
  dlBusy.value = v.docId
  try {
    await downloadKnowledgeDocument(v.baseId, v.docId, v.name)
  } catch (e) {
    errById.value = {
      ...errById.value,
      [v.docId]: e instanceof ApiError ? e.message : '下载失败',
    }
  } finally {
    dlBusy.value = null
  }
}
</script>

<template>
  <div v-if="videos.length" class="space-y-2">
    <div class="text-[11px] text-text-muted inline-flex items-center gap-1">
      <Film class="size-3.5" />
      知识库视频 · 可播放 / 下载原片
    </div>
    <div class="space-y-2.5">
      <div
        v-for="v in videos"
        :key="v.docId"
        class="overflow-hidden rounded-md border border-hairline bg-bg-surface"
      >
        <div class="flex items-center justify-between gap-2 border-b border-hairline px-2.5 py-1.5">
          <div class="min-w-0 truncate text-[12px] text-text-primary" :title="v.name">
            {{ v.name }}
            <span
              v-if="fmtKbVideoRange(v.startMs, v.endMs)"
              class="ml-1.5 font-mono text-[10px] text-text-muted"
            >
              {{ fmtKbVideoRange(v.startMs, v.endMs) }}
            </span>
          </div>
          <button
            type="button"
            class="inline-flex h-7 shrink-0 items-center gap-1 rounded-md border border-hairline px-2 text-[11px] text-text-secondary hover:bg-hairline/40 hover:text-text-primary disabled:opacity-60"
            :disabled="dlBusy === v.docId"
            @click="download(v)"
          >
            <Loader2 v-if="dlBusy === v.docId" class="size-3 animate-spin" />
            <Download v-else class="size-3" />
            下载
          </button>
        </div>
        <div class="bg-bg-base/50 p-2">
          <video
            v-if="!errById[v.docId]"
            class="max-h-[320px] w-full rounded bg-black object-contain"
            controls
            preload="metadata"
            playsinline
            :src="srcOf(v)"
            @loadedmetadata="onLoadedMeta($event, v)"
            @error="onVideoErr(v)"
          />
          <p v-else class="px-2 py-6 text-center text-[12px] text-iron">
            {{ errById[v.docId] }}
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onScopeDispose, ref, watch, nextTick } from 'vue'
import { renderAsync } from 'docx-preview'
import AppDialog from '@/components/ui/AppDialog.vue'
import { ApiError } from '@/lib/api'
import { fetchHistoryChapterPreview, type HistoryChapter } from '@/lib/tenders-api'

const props = defineProps<{ recordId: string; chapter: HistoryChapter | null }>()
const emit = defineEmits<{ close: [] }>()
const host = ref<HTMLElement | null>(null)
const loading = ref(false)
const error = ref('')
let generation = 0
onScopeDispose(() => generation++)

watch(() => [props.recordId, props.chapter] as const, async ([id, chapter]) => {
  const current = ++generation
  error.value = ''
  if (!chapter || !id) return
  loading.value = true
  await nextTick()
  const target = host.value
  if (!target || current !== generation) return
  target.replaceChildren()
  try {
    const blob = await fetchHistoryChapterPreview(id, chapter)
    if (current !== generation) return
    // 在独立容器渲染；关闭/切换后的异步渲染不会写回当前预览。
    const rendered = document.createElement('div')
    await renderAsync(blob, rendered, undefined, {
      inWrapper: true, breakPages: true, ignoreLastRenderedPageBreak: true,
      useBase64URL: true, renderHeaders: true, renderFooters: true,
      renderFootnotes: true, renderEndnotes: true,
    })
    if (current === generation) target.replaceChildren(rendered)
  } catch (e) {
    if (current === generation) error.value = e instanceof ApiError ? e.message : 'Word 章节预览失败，请稍后重试'
  } finally {
    if (current === generation) loading.value = false
  }
}, { immediate: true })
</script>

<template>
  <AppDialog :open="!!chapter" :title="chapter?.displayTitle || chapter?.chapterTitle || '章节预览'"
    description="原始 Word 章节 · 只读预览" size="xl" @close="emit('close')">
    <p v-if="loading" role="status" class="mb-2 text-xs text-muted-foreground">正在加载 Word 章节…</p>
    <p v-if="error" role="alert" class="mb-2 text-xs text-iron">{{ error }}</p>
    <div ref="host" class="min-h-0 flex-1 overflow-auto bg-muted" :contenteditable="false"
      aria-label="Word 章节只读内容" @beforeinput.prevent />
  </AppDialog>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '@/lib/markdown-render'

const props = defineProps<{
  content: string
  streaming?: boolean
}>()

/** 兜底剥离模型误输出的 DSML/工具调用标记（含全角 ｜｜ 变体） */
function stripToolMarkup(text: string): string {
  if (!text) return ''
  let out = text.replace(/\uFF5C/g, '|')
  out = out.replace(/<\|+\s*DSML\s*\|+/gi, '<|DSML|')
  out = out.replace(/<\/\|+\s*DSML\s*\|+/gi, '</|DSML|')
  out = out.replace(/<\|DSML\|tool_calls\s*>[\s\S]*?<\/\|DSML\|tool_calls\s*>/gi, '')
  out = out.replace(/<\|DSML\|[^>]*>[\s\S]*?(?:<\/\|DSML\|[^>]*>|$)/gi, '')
  out = out.replace(/<\|DSML\|[^>]*>/gi, '')
  out = out.replace(/<\/\|DSML\|[^>]*>/gi, '')
  out = out.replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, '')
  return out.replace(/\n{3,}/g, '\n\n').trim()
}

const html = computed(() => renderMarkdown(stripToolMarkup(props.content || '')))
</script>

<template>
  <div class="chat-md text-[13px] leading-[1.75] text-text-primary">
    <div v-html="html" />
    <span
      v-if="streaming"
      class="inline-block w-1.5 h-3.5 bg-iron ml-0.5 animate-pulse align-middle"
    />
  </div>
</template>

<style scoped>
.chat-md :deep(.md-p) {
  margin: 0.45em 0;
  color: var(--text-primary);
}
.chat-md :deep(.md-p:first-child) {
  margin-top: 0;
}
.chat-md :deep(.md-strong) {
  color: var(--text-primary);
  font-weight: 600;
}
.chat-md :deep(.md-em) {
  font-style: italic;
  color: var(--text-secondary);
}
.chat-md :deep(.md-code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
  padding: 0.1em 0.35em;
  border-radius: 4px;
  background: color-mix(in srgb, var(--accent-molybdenum) 12%, transparent);
  color: var(--accent-molybdenum);
  border: 1px solid color-mix(in srgb, var(--accent-molybdenum) 22%, transparent);
}
.chat-md :deep(.md-pre) {
  margin: 0.65em 0;
  padding: 0.75rem 0.9rem;
  border-radius: 8px;
  background: color-mix(in srgb, var(--bg-base) 70%, var(--foreground) 8%);
  border: 1px solid var(--hairline);
  overflow-x: auto;
  font-size: 11.5px;
  line-height: 1.55;
}
.chat-md :deep(.md-pre code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--text-primary);
}
.chat-md :deep(.md-h1),
.chat-md :deep(.md-h2),
.chat-md :deep(.md-h3),
.chat-md :deep(.md-h4) {
  color: var(--text-primary);
  font-weight: 600;
  margin: 0.85em 0 0.4em;
  line-height: 1.35;
}
.chat-md :deep(.md-h1) {
  font-size: 1.15em;
}
.chat-md :deep(.md-h2) {
  font-size: 1.08em;
  padding-left: 0.5rem;
  border-left: 2px solid var(--accent-iron);
}
.chat-md :deep(.md-h3),
.chat-md :deep(.md-h4) {
  font-size: 1.02em;
}
.chat-md :deep(.md-ul),
.chat-md :deep(.md-ol) {
  margin: 0.4em 0 0.55em;
  padding-left: 1.25rem;
}
.chat-md :deep(.md-li) {
  margin: 0.2em 0;
  color: var(--text-primary);
}
.chat-md :deep(.md-ul) {
  list-style: disc;
}
.chat-md :deep(.md-ol) {
  list-style: decimal;
}
.chat-md :deep(.md-quote) {
  margin: 0.6em 0;
  padding: 0.55rem 0.75rem;
  border-left: 3px solid color-mix(in srgb, var(--accent-iron) 65%, transparent);
  background: color-mix(in srgb, var(--accent-iron) 6%, transparent);
  border-radius: 0 6px 6px 0;
  color: var(--text-secondary);
}
.chat-md :deep(.md-hr) {
  margin: 0.85em 0;
  border: 0;
  border-top: 1px solid var(--hairline);
}
.chat-md :deep(.md-table-wrap) {
  margin: 0.7em 0;
  overflow-x: auto;
  border-radius: 8px;
  border: 1px solid var(--hairline);
  background: color-mix(in srgb, var(--bg-elevated) 80%, transparent);
}
.chat-md :deep(.md-table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  min-width: 420px;
}
.chat-md :deep(.md-th) {
  text-align: left;
  padding: 0.55rem 0.75rem;
  font-weight: 600;
  color: var(--accent-molybdenum);
  background: color-mix(in srgb, var(--accent-molybdenum) 10%, transparent);
  border-bottom: 1px solid var(--hairline);
  white-space: nowrap;
}
.chat-md :deep(.md-td) {
  padding: 0.5rem 0.75rem;
  vertical-align: top;
  color: var(--text-primary);
  border-bottom: 1px solid color-mix(in srgb, var(--hairline) 70%, transparent);
  line-height: 1.55;
}
.chat-md :deep(.md-tr:last-child .md-td) {
  border-bottom: 0;
}
.chat-md :deep(.md-tr:nth-child(even) .md-td) {
  background: color-mix(in srgb, var(--foreground) 2.5%, transparent);
}
</style>

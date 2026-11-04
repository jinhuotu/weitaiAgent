<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  CircleCheck,
  Database,
  Loader2,
  Moon,
  Server,
  Shield,
  Sun,
  TriangleAlert,
  UserRound,
} from 'lucide-vue-next'
import { Panel, PageHeader, Tag } from '@/components/ui-kit'
import { ApiError } from '@/lib/api'
import { fetchHealth } from '@/lib/health-api'
import { applyQdrantSettings, getQdrantSettings, type QdrantSettings } from '@/lib/knowledge-api'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import AccentPalettePicker from '@/components/theme/AccentPalettePicker.vue'

const auth = useAuthStore()
const theme = useThemeStore()

const healthOk = ref<boolean | null>(null)
const healthMsg = ref('')
const qdrant = ref<QdrantSettings | null>(null)
const qdrantErr = ref('')
const qdrantLoading = ref(false)
const qdrantApplying = ref(false)
const qdrantMsg = ref('')

const displayName = computed(
  () => auth.user?.display_name || auth.user?.username || '未登录',
)

const roleText = computed(() => {
  if (auth.user?.is_superuser) return '超级管理员'
  const roles = auth.user?.roles || []
  return roles.length ? roles.join(' / ') : '普通用户'
})

async function loadQdrant() {
  if (!auth.isAdmin) return
  qdrantLoading.value = true
  qdrantErr.value = ''
  try {
    qdrant.value = await getQdrantSettings()
  } catch (err) {
    qdrantErr.value = err instanceof ApiError || err instanceof Error ? err.message : '读取失败'
    qdrant.value = null
  } finally {
    qdrantLoading.value = false
  }
}

async function applyQdrant() {
  if (!auth.isAdmin || qdrantApplying.value) return
  const ok = window.confirm(
    '将按 .env 的 QDRANT_QUANTIZATION 删除并重建向量集合，然后重嵌所有已入库资料。此操作不可撤销。确定继续？',
  )
  if (!ok) return
  qdrantApplying.value = true
  qdrantMsg.value = ''
  try {
    const data = await applyQdrantSettings()
    qdrant.value = data.item
    qdrantMsg.value = `已重建。重嵌 ${data.reindex.reindexed}/${data.reindex.total}，失败 ${data.reindex.failed}`
  } catch (err) {
    qdrantMsg.value = err instanceof ApiError || err instanceof Error ? err.message : '重建失败'
  } finally {
    qdrantApplying.value = false
  }
}

onMounted(async () => {
  try {
    const data = await fetchHealth()
    healthOk.value = data.status === 'up'
    healthMsg.value = data.service || 'youqi-api'
  } catch (err) {
    healthOk.value = false
    healthMsg.value = err instanceof ApiError ? err.message : '无法连接后端'
  }
  await loadQdrant()
})
</script>

<template>
  <PageHeader
    title="系统设置"
    description="主题、账号信息与后端连通性。管理员可查看知识库向量集合并按 .env 重建量化。"
  >
    <template #badges>
      <Tag tone="molybdenum">优祺智能 v0.1.0</Tag>
    </template>
  </PageHeader>

  <div class="grid grid-cols-1 lg:grid-cols-3 gap-5 mb-5">
    <Panel title="外观" subtitle="仅保存在本机浏览器">
      <div class="p-4 space-y-4">
        <p class="text-[12px] text-muted-foreground leading-relaxed">
          明暗模式与配色渐变（蓝白 / 红白 / 自定义主色与字体色）写入 localStorage，刷新后保持。
        </p>
        <div class="space-y-2">
          <div class="text-[11px] font-medium text-foreground">明暗</div>
          <button
            type="button"
            class="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-[12px] hover:bg-accent"
            @click="theme.toggle()"
          >
            <Sun v-if="theme.isDark" class="size-3.5" />
            <Moon v-else class="size-3.5" />
            切换为{{ theme.label }}主题
          </button>
        </div>
        <div class="space-y-2">
          <div class="text-[11px] font-medium text-foreground">
            配色渐变 · 当前 {{ theme.accentLabel }}
          </div>
          <AccentPalettePicker />
        </div>
      </div>
    </Panel>

    <Panel title="当前账号" subtitle="来自 /api/v1/auth/me">
      <ul class="divide-y divide-border">
        <li class="px-4 py-3 flex items-center gap-3">
          <UserRound class="size-4 text-muted-foreground" />
          <div>
            <div class="text-sm">{{ displayName }}</div>
            <div class="text-[11px] text-muted-foreground mt-0.5">{{ auth.user?.username }}</div>
          </div>
        </li>
        <li class="px-4 py-3 flex items-center gap-3">
          <Shield class="size-4 text-muted-foreground" />
          <div>
            <div class="text-sm">{{ roleText }}</div>
            <div class="text-[11px] text-muted-foreground mt-0.5">
              {{ auth.user?.email || '未填写邮箱' }}
            </div>
          </div>
        </li>
      </ul>
    </Panel>

    <Panel title="后端服务" subtitle="GET /api/v1/health">
      <div class="p-4 space-y-3">
        <div class="flex items-center gap-2 text-sm">
          <CircleCheck v-if="healthOk" class="size-4 text-patina" />
          <Server v-else class="size-4 text-sulfur" />
          <span>{{ healthOk === null ? '检测中…' : healthOk ? '服务在线' : '服务异常' }}</span>
        </div>
        <div class="text-[11px] text-muted-foreground font-mono flex items-center gap-1.5">
          <Database class="size-3.5" />
          {{ healthMsg || '—' }}
        </div>
        <p class="text-[11px] text-muted-foreground leading-relaxed">
          开发环境默认通过 Vite 代理访问 <span class="font-mono">127.0.0.1:8100</span>。
          需要直连时设置 <span class="font-mono">VITE_API_BASE_URL</span>。
        </p>
      </div>
    </Panel>
  </div>

  <Panel
    v-if="auth.isAdmin"
    title="知识库向量集合"
    subtitle="Qdrant 量化仅在新建集合时生效；应用 .env 会重建集合"
  >
    <div class="p-4 space-y-3">
      <p class="text-[12px] text-muted-foreground leading-relaxed">
        当前集合维度与量化方式。修改 <span class="font-mono">QDRANT_QUANTIZATION</span>
        （none / int8 / binary）后点下方按钮才会重建。
      </p>
      <div v-if="qdrantLoading" class="text-[12px] text-muted-foreground">
        <Loader2 class="inline size-3.5 animate-spin mr-1" />
        读取集合信息…
      </div>
      <div v-else-if="qdrantErr" class="text-[12px] text-iron">{{ qdrantErr }}</div>
      <ul v-else-if="qdrant" class="text-[12px] space-y-1.5 font-mono">
        <li>集合 {{ qdrant.name }} · {{ qdrant.exists ? '已存在' : '尚未创建' }}</li>
        <li>点数 {{ qdrant.points }} · 维度 {{ qdrant.vectorSize ?? '—' }}</li>
        <li>集合量化 {{ qdrant.quantization || 'none' }} · .env {{ qdrant.envQuantization || 'none' }}</li>
      </ul>
      <div
        class="flex items-start gap-2 rounded-md border border-sulfur/30 bg-sulfur/10 px-3 py-2 text-[11px] text-sulfur"
      >
        <TriangleAlert class="size-3.5 shrink-0 mt-0.5" />
        重建会短暂中断检索，并重嵌全部已入库资料。请二次确认。
      </div>
      <button
        type="button"
        class="inline-flex h-8 items-center gap-1.5 rounded-md border border-iron/40 px-3 text-[12px] text-iron hover:bg-iron/10"
        :disabled="qdrantApplying"
        @click="applyQdrant()"
      >
        <Loader2 v-if="qdrantApplying" class="size-3.5 animate-spin" />
        应用 .env 量化并重建
      </button>
      <p v-if="qdrantMsg" class="text-[12px] text-muted-foreground">{{ qdrantMsg }}</p>
    </div>
  </Panel>
</template>

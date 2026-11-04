<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { History, LoaderCircle, RefreshCw, ShieldAlert } from 'lucide-vue-next'
import { Panel, PageHeader, KpiCard, Tag } from '@/components/ui-kit'
import { ApiError } from '@/lib/api'
import {
  fetchAuditSummary,
  fetchLoginLogs,
  fetchOperationLogs,
  type AuditSummary,
  type LoginLogItem,
  type OperationLogItem,
} from '@/lib/audit-api'

type TabKey = 'operations' | 'logins'

const MODULE_LABEL: Record<string, string> = {
  users: '用户',
  roles: '角色',
  hot_configs: '热配置',
  quotes: '报价',
  'quotes-cost': '造价',
  'quotes-budget': '预算',
  tenders: '投标',
  mcp: 'MCP',
  models: '模型',
}

const ACTION_LABEL: Record<string, string> = {
  create: '创建',
  update: '更新',
  delete: '删除',
  reset_password: '重置密码',
  health: '健康检查',
  refresh_tools: '刷新工具',
  update_tool: '更新工具',
}

const REASON_LABEL: Record<string, string> = {
  ok: '成功',
  invalid_credentials: '账号或密码错误',
  inactive: '账号已停用',
}

const tab = ref<TabKey>('operations')
const loading = ref(true)
const error = ref('')
const summary = ref<AuditSummary | null>(null)
const operations = ref<OperationLogItem[]>([])
const opTotal = ref(0)
const logins = ref<LoginLogItem[]>([])
const loginTotal = ref(0)

const moduleFilter = ref('')
const opSuccessFilter = ref<'all' | 'ok' | 'fail'>('all')
const opKeyword = ref('')
const loginSuccessFilter = ref<'all' | 'ok' | 'fail'>('all')
const loginUsername = ref('')

function formatTime(ts: number | null | undefined): string {
  if (!ts) return '—'
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function boolFilter(v: 'all' | 'ok' | 'fail'): boolean | undefined {
  if (v === 'ok') return true
  if (v === 'fail') return false
  return undefined
}

async function reload() {
  loading.value = true
  error.value = ''
  try {
    const [sum, opList, loginList] = await Promise.all([
      fetchAuditSummary(),
      fetchOperationLogs({
        module: moduleFilter.value || undefined,
        success: boolFilter(opSuccessFilter.value),
        keyword: opKeyword.value.trim() || undefined,
        limit: 100,
      }),
      fetchLoginLogs({
        success: boolFilter(loginSuccessFilter.value),
        username: loginUsername.value.trim() || undefined,
        limit: 100,
      }),
    ])
    summary.value = sum
    operations.value = opList.items || []
    opTotal.value = opList.total || 0
    logins.value = loginList.items || []
    loginTotal.value = loginList.total || 0
  } catch (e) {
    error.value =
      e instanceof ApiError || e instanceof Error ? e.message : '加载日志失败'
  } finally {
    loading.value = false
  }
}

onMounted(reload)
watch([moduleFilter, opSuccessFilter, loginSuccessFilter], () => {
  void reload()
})
</script>

<template>
  <PageHeader
    title="操作与登录日志"
    description="记录用户 / 角色 / 热配置 / MCP / 模型的写入操作，以及登录成功与失败（含 IP）。超时按配置天数自动删除。"
  >
    <template #badges>
      <Tag tone="molybdenum">保留 {{ summary?.retentionDays ?? 7 }} 天</Tag>
    </template>
    <template #actions>
      <button
        type="button"
        class="h-8 px-3 inline-flex items-center gap-1.5 text-xs rounded-md border border-border hover:bg-accent"
        :disabled="loading"
        @click="reload"
      >
        <RefreshCw class="size-3.5" :class="loading ? 'animate-spin' : ''" />
        刷新
      </button>
    </template>
  </PageHeader>

  <p v-if="error" class="mb-4 text-xs text-iron">{{ error }}</p>

  <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
    <KpiCard
      label="近 7 天操作"
      :value="String(summary?.operationTotal ?? 0)"
      unit="条"
      tone="molybdenum"
    >
      <template #icon><History class="size-4" /></template>
    </KpiCard>
    <KpiCard
      label="操作失败"
      :value="String(summary?.operationFail ?? 0)"
      unit="条"
      tone="iron"
    >
      <template #icon><ShieldAlert class="size-4" /></template>
    </KpiCard>
    <KpiCard
      label="近 7 天登录"
      :value="String(summary?.loginTotal ?? 0)"
      unit="次"
      tone="patina"
    />
    <KpiCard
      label="登录失败"
      :value="String(summary?.loginFail ?? 0)"
      unit="次"
      tone="iron"
    />
  </div>

  <div class="mb-4 flex items-center gap-1 text-xs">
    <button
      type="button"
      class="h-8 px-3 rounded-md border"
      :class="
        tab === 'operations'
          ? 'bg-iron/15 border-iron/30 text-iron'
          : 'border-border text-muted-foreground'
      "
      @click="tab = 'operations'"
    >
      操作日志
    </button>
    <button
      type="button"
      class="h-8 px-3 rounded-md border"
      :class="
        tab === 'logins'
          ? 'bg-iron/15 border-iron/30 text-iron'
          : 'border-border text-muted-foreground'
      "
      @click="tab = 'logins'"
    >
      登录日志
    </button>
  </div>

  <Panel
    v-if="tab === 'operations'"
    title="写入操作"
    :subtitle="`共 ${opTotal} 条（最多显示 100）`"
    flush
  >
    <div class="px-4 py-3 flex flex-wrap gap-2 border-b border-border">
      <select
        v-model="moduleFilter"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background"
      >
        <option value="">全部模块</option>
        <option value="users">用户</option>
        <option value="roles">角色</option>
        <option value="hot_configs">热配置</option>
        <option value="mcp">MCP</option>
        <option value="models">模型</option>
      </select>
      <select
        v-model="opSuccessFilter"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background"
      >
        <option value="all">全部结果</option>
        <option value="ok">成功</option>
        <option value="fail">失败</option>
      </select>
      <input
        v-model="opKeyword"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background min-w-[180px]"
        placeholder="路径 / 操作人 / 资源 ID"
        @keydown.enter="reload"
      />
      <button
        type="button"
        class="h-8 px-3 text-xs rounded-md border border-border hover:bg-accent"
        @click="reload"
      >
        查询
      </button>
    </div>
    <div v-if="loading" class="px-4 py-6 text-xs text-muted-foreground inline-flex items-center gap-1.5">
      <LoaderCircle class="size-3.5 animate-spin" />加载中…
    </div>
    <div v-else class="overflow-x-auto">
      <table class="w-full text-xs min-w-[980px]">
        <thead class="text-muted-foreground bg-background/40">
          <tr class="border-b border-border">
            <th
              v-for="h in ['时间', '模块', '动作', '操作人', 'IP', '结果', '路径', '耗时']"
              :key="h"
              class="text-left font-medium px-4 py-2.5"
            >
              {{ h }}
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          <tr v-if="!operations.length">
            <td colspan="8" class="px-4 py-8 text-center text-muted-foreground">暂无操作记录</td>
          </tr>
          <tr v-for="row in operations" :key="row.id" class="hover:bg-background/40">
            <td class="px-4 py-2.5 data-num whitespace-nowrap">{{ formatTime(row.createdAt) }}</td>
            <td class="px-4 py-2.5">{{ MODULE_LABEL[row.module] || row.module }}</td>
            <td class="px-4 py-2.5">{{ ACTION_LABEL[row.action] || row.action }}</td>
            <td class="px-4 py-2.5">{{ row.operatorUsername || '—' }}</td>
            <td class="px-4 py-2.5 data-num">{{ row.ip || '—' }}</td>
            <td class="px-4 py-2.5">
              <Tag :tone="row.success ? 'patina' : 'iron'">
                {{ row.success ? '成功' : `失败 ${row.statusCode}` }}
              </Tag>
              <div v-if="row.errorMsg" class="mt-1 text-[10px] text-iron max-w-[220px] truncate">
                {{ row.errorMsg }}
              </div>
            </td>
            <td class="px-4 py-2.5">
              <div class="data-num">{{ row.method }} {{ row.path }}</div>
              <div v-if="row.resourceId" class="text-[10px] text-muted-foreground mt-0.5">
                资源 {{ row.resourceId }}
              </div>
            </td>
            <td class="px-4 py-2.5 data-num">{{ row.durationMs }} ms</td>
          </tr>
        </tbody>
      </table>
    </div>
  </Panel>

  <Panel
    v-else
    title="登录记录"
    :subtitle="`共 ${loginTotal} 条（最多显示 100）`"
    flush
  >
    <div class="px-4 py-3 flex flex-wrap gap-2 border-b border-border">
      <select
        v-model="loginSuccessFilter"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background"
      >
        <option value="all">全部结果</option>
        <option value="ok">成功</option>
        <option value="fail">失败</option>
      </select>
      <input
        v-model="loginUsername"
        class="h-8 px-2 text-xs rounded-md border border-border bg-background min-w-[160px]"
        placeholder="用户名"
        @keydown.enter="reload"
      />
      <button
        type="button"
        class="h-8 px-3 text-xs rounded-md border border-border hover:bg-accent"
        @click="reload"
      >
        查询
      </button>
    </div>
    <div v-if="loading" class="px-4 py-6 text-xs text-muted-foreground inline-flex items-center gap-1.5">
      <LoaderCircle class="size-3.5 animate-spin" />加载中…
    </div>
    <div v-else class="overflow-x-auto">
      <table class="w-full text-xs min-w-[860px]">
        <thead class="text-muted-foreground bg-background/40">
          <tr class="border-b border-border">
            <th
              v-for="h in ['时间', '用户名', '结果', '原因', 'IP', 'User-Agent']"
              :key="h"
              class="text-left font-medium px-4 py-2.5"
            >
              {{ h }}
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          <tr v-if="!logins.length">
            <td colspan="6" class="px-4 py-8 text-center text-muted-foreground">暂无登录记录</td>
          </tr>
          <tr v-for="row in logins" :key="row.id" class="hover:bg-background/40">
            <td class="px-4 py-2.5 data-num whitespace-nowrap">{{ formatTime(row.createdAt) }}</td>
            <td class="px-4 py-2.5">{{ row.username }}</td>
            <td class="px-4 py-2.5">
              <Tag :tone="row.success ? 'patina' : 'iron'">
                {{ row.success ? '成功' : '失败' }}
              </Tag>
            </td>
            <td class="px-4 py-2.5">{{ REASON_LABEL[row.reason] || row.reason || '—' }}</td>
            <td class="px-4 py-2.5 data-num">{{ row.ip || '—' }}</td>
            <td class="px-4 py-2.5 text-muted-foreground max-w-[360px] truncate" :title="row.userAgent">
              {{ row.userAgent || '—' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </Panel>
</template>

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import type { Component } from 'vue'
import {
  canAccessPath,
  flattenNavItems,
  isQuotePath,
  QUOTE_FAMILY,
  QUOTE_NAV_LABEL,
} from '@/config/nav'
import { useAuthStore } from '@/stores/auth'
import { useMobileMenuStore } from '@/stores/mobile-menu'
import { useTabsStore } from '@/stores/tabs'

const LoginView = () => import('@/views/LoginView.vue')
const AppShell = () => import('@/layouts/AppShell.vue')

const MIGRATED_VIEWS: Record<string, () => Promise<{ default: Component }>> = {
  '/': () => import('@/views/OverviewView.vue'),
  '/ai-chat': () => import('@/views/AiChatView.vue'),
  '/scene-agents': () => import('@/views/SceneAgentsView.vue'),
  '/workflows': () => import('@/views/WorkflowsView.vue'),
  '/model-manage': () => import('@/views/ModelManageView.vue'),
  '/prompt-manage': () => import('@/views/PromptManageView.vue'),
  '/mcp-manage': () => import('@/views/McpManageView.vue'),
  '/knowledge': () => import('@/views/KnowledgeListView.vue'),
  '/tenders': () => import('@/views/TendersView.vue'),
  '/tender-qa': () => import('@/views/TenderQaView.vue'),
  '/quotes': () => import('@/views/QuotesView.vue'),
  '/quotes-cost': () => import('@/views/QuotesView.vue'),
  '/quotes-budget': () => import('@/views/QuotesView.vue'),
  '/tender-library': () => import('@/views/TenderLibraryView.vue'),
  '/history-bids': () => import('@/views/HistoryBidsView.vue'),
  '/work-tasks': () => import('@/views/WorkTasksView.vue'),
  '/tender-tasks': () => import('@/views/AllTenderTasksView.vue'),
  '/approval': () => import('@/views/ApprovalFlowView.vue'),
  '/users': () => import('@/views/UsersView.vue'),
  '/logs': () => import('@/views/AuditLogsView.vue'),
  '/settings': () => import('@/views/SettingsView.vue'),
}

const QUOTE_ROUTE_META: Record<string, string> = {
  '/quotes-cost': 'AI造价智能体',
  '/quotes': 'AI报价智能体',
  '/quotes-budget': 'AI预算智能体',
}

function buildFeatureRoutes(): RouteRecordRaw[] {
  const items = flattenNavItems()
  const seen = new Set<string>()
  const routes: RouteRecordRaw[] = []

  for (const item of items) {
    if (seen.has(item.href)) continue
    seen.add(item.href)
    const path = item.href === '/' ? '' : item.href.replace(/^\//, '')
    const component = MIGRATED_VIEWS[item.href]
    routes.push({
      path,
      name: item.href === '/' ? 'overview' : item.href.slice(1).replace(/\//g, '-'),
      component,
      meta: { title: item.label, adminOnly: Boolean(item.adminOnly) },
    })
  }

  // 页内 Tab 深链：侧栏只挂 /quotes，子路径仍要注册
  for (const href of QUOTE_FAMILY) {
    if (seen.has(href)) continue
    seen.add(href)
    routes.push({
      path: href.replace(/^\//, ''),
      name: href.slice(1).replace(/\//g, '-'),
      component: MIGRATED_VIEWS[href],
      meta: { title: QUOTE_ROUTE_META[href] || QUOTE_NAV_LABEL },
    })
  }

  routes.push(
    {
      path: 'knowledge/:baseId',
      name: 'knowledge-detail',
      component: () => import('@/views/KnowledgeDetailView.vue'),
      meta: { title: '知识库详情' },
    },
    {
      path: 'workflows/:id',
      name: 'workflow-editor',
      component: () => import('@/views/WorkflowEditorView.vue'),
      meta: { title: '工作流编辑', adminOnly: true },
    },
  )

  return routes
}

const router = createRouter({
  // 与 vite.config base / VITE_BASE_PATH 对齐（如生产 /weitai/）
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { public: true, title: '登录' },
    },
    {
      path: '/',
      component: AppShell,
      children: buildFeatureRoutes(),
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (auth.loading) {
    await auth.bootstrap()
  }

  const isPublic = Boolean(to.meta.public)
  if (!auth.user && !isPublic) {
    return {
      path: '/login',
      query: { next: to.fullPath },
    }
  }
  if (auth.user && isPublic) {
    return { path: '/' }
  }
  if (auth.user && !isPublic) {
    if (to.meta.adminOnly && !auth.isAdmin) {
      return { path: '/' }
    }
    const ok = canAccessPath(to.path, auth.isAdmin, auth.menus)
    if (!ok) {
      return { path: '/' }
    }
  }
  return true
})

const CHUNK_LOAD_RE =
  /Failed to fetch dynamically imported module|Unable to preload CSS|Importing a module script failed|Loading chunk|Loading CSS chunk/i
const CHUNK_RELOAD_KEY = 'youqi-chunk-reload'

router.onError((err) => {
  const msg = err instanceof Error ? err.message : String(err)
  if (!CHUNK_LOAD_RE.test(msg)) return
  if (sessionStorage.getItem(CHUNK_RELOAD_KEY)) return
  sessionStorage.setItem(CHUNK_RELOAD_KEY, '1')
  window.location.reload()
})

router.afterEach((to) => {
  sessionStorage.removeItem(CHUNK_RELOAD_KEY)
  const title = (to.meta.title as string | undefined) || '优祺智能'
  document.title = `${title} · 优祺智能`

  useMobileMenuStore().close()

  if (to.path !== '/login') {
    const tabs = useTabsStore()
    const label = isQuotePath(to.path)
      ? QUOTE_NAV_LABEL
      : (to.meta.title as string | undefined) || undefined
    tabs.ensureTab(to.path, label)
  }
})

export default router

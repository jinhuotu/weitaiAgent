import type { Component } from 'vue'
import {
  LayoutDashboard,
  Sparkles,
  Users,
  History,
  Settings,
  LibraryBig,
  BotMessageSquare,
  Workflow,
  Boxes,
  Plug,
  BookOpenText,
  FolderOpen,
  FileText,
  ClipboardList,
  FolderKanban,
  BadgeCheck,
  FileSpreadsheet,
  FileSearch,
} from 'lucide-vue-next'

export type NavItem = {
  href: string
  label: string
  icon: Component
  adminOnly?: boolean
  requireMenu?: boolean
}

export type NavChild = {
  key: string
  label: string
  items: NavItem[]
}

export type NavGroup = {
  title: string
  icon: Component
  items?: NavItem[]
  children?: NavChild[]
}

/** 造价 / 报价 / 预算共用一个侧栏入口，子路径仍保留深链 */
export const QUOTE_FAMILY = ['/quotes', '/quotes-cost', '/quotes-budget'] as const
export const QUOTE_NAV_HREF = '/quotes'
export const QUOTE_NAV_LABEL = 'AI报价中心'

export function isQuotePath(path: string) {
  return (QUOTE_FAMILY as readonly string[]).includes(path)
}

function canSeeNavItem(
  it: NavItem,
  admin: boolean,
  menus: string[] | null | undefined,
): boolean {
  if (it.adminOnly && !admin) return false
  if (admin) return true
  if (!menus || menus.length === 0) return it.href === '/'
  if (menus.includes(it.href)) return true
  if (isQuotePath(it.href) && QUOTE_FAMILY.some((h) => menus.includes(h))) return true
  return false
}

export function filterNavGroups(
  groups: NavGroup[],
  admin: boolean,
  menus?: string[] | null,
): NavGroup[] {
  return groups
    .map((g) => ({
      ...g,
      items: g.items?.filter((it) => canSeeNavItem(it, admin, menus)),
      children: g.children?.map((c) => ({
        ...c,
        items: c.items.filter((it) => canSeeNavItem(it, admin, menus)),
      })),
    }))
    .filter(
      (g) =>
        (g.items && g.items.length > 0) ||
        (g.children && g.children.some((c) => c.items.length > 0)),
    )
}

export function isAdminOnlyPath(path: string): boolean {
  return flattenNavItems().some((it) => it.href === path && Boolean(it.adminOnly))
}

export function canAccessPath(
  path: string,
  admin: boolean,
  menus?: string[] | null,
): boolean {
  if (admin) return true
  const adminParent = flattenNavItems().find(
    (it) =>
      Boolean(it.adminOnly) &&
      (path === it.href || (it.href !== '/' && path.startsWith(`${it.href}/`))),
  )
  if (adminParent) return false

  if (isQuotePath(path)) {
    return canSeeNavItem(
      { href: QUOTE_NAV_HREF, label: QUOTE_NAV_LABEL, icon: FileSpreadsheet },
      admin,
      menus,
    )
  }

  const exact = flattenNavItems().find((it) => it.href === path)
  if (exact) return canSeeNavItem(exact, admin, menus)
  if (!menus || menus.length === 0) return path === '/'
  return menus.some((m) => m !== '/' && (path === m || path.startsWith(`${m}/`)))
}

export const NAV_GROUPS: NavGroup[] = [
  {
    title: '工作台',
    icon: LayoutDashboard,
    items: [{ href: '/', label: '总览', icon: LayoutDashboard }],
  },
  {
    title: 'AI 智控',
    icon: BotMessageSquare,
    items: [
      { href: '/ai-chat', label: 'AI 智能问答', icon: BotMessageSquare },
      { href: '/work-tasks', label: '工作任务', icon: ClipboardList },
      { href: '/tender-tasks', label: '全部投标任务', icon: FolderKanban },
      { href: '/approval', label: '审批流程', icon: BadgeCheck },
      { href: '/knowledge', label: '知识库', icon: LibraryBig },
      { href: '/tenders', label: 'AI标书生成', icon: FileText },
      { href: '/tender-qa', label: 'AI标书评分', icon: FileSearch },
      { href: QUOTE_NAV_HREF, label: QUOTE_NAV_LABEL, icon: FileSpreadsheet },
      { href: '/tender-library', label: '投标资料库', icon: FolderOpen },
      { href: '/history-bids', label: '历史标书', icon: History },
      { href: '/scene-agents', label: '场景智能体', icon: Sparkles, adminOnly: true },
      { href: '/workflows', label: '工作流', icon: Workflow, adminOnly: true },
      { href: '/model-manage', label: '模型管理', icon: Boxes, adminOnly: true },
      { href: '/prompt-manage', label: '提示词管理', icon: BookOpenText, adminOnly: true },
      { href: '/mcp-manage', label: '工具管理', icon: Plug, adminOnly: true },
    ],
  },
  {
    title: '运营',
    icon: Settings,
    items: [
      { href: '/users', label: '用户与权限', icon: Users, adminOnly: true },
      { href: '/logs', label: '操作与登录日志', icon: History, adminOnly: true },
      { href: '/settings', label: '系统设置', icon: Settings },
    ],
  },
]

export function flattenNavItems(groups: NavGroup[] = NAV_GROUPS): NavItem[] {
  return groups.flatMap((g) =>
    g.items ? [...g.items] : (g.children || []).flatMap((c) => c.items),
  )
}

export function assignableNavGroups(groups: NavGroup[] = NAV_GROUPS): {
  title: string
  items: NavItem[]
}[] {
  return groups
    .map((g) => ({
      title: g.title,
      items: (g.items || []).filter((it) => !it.adminOnly),
    }))
    .filter((g) => g.items.length > 0)
}

/** 侧栏 / 工作台常显业务入口默认顺序；未列入的归入「更多」 */
export const PRIMARY_NAV_HREFS = [
  '/ai-chat',
  '/work-tasks',
  '/tender-tasks',
  '/approval',
  '/tenders',
  '/tender-qa',
  QUOTE_NAV_HREF,
  '/knowledge',
  '/tender-library',
  '/history-bids',
] as const

export const NAV_ITEM_DESC: Record<string, string> = {
  '/': '工作台总览与运行状态',
  '/ai-chat': '多会话问答与工具调用',
  '/work-tasks': '本人投标任务与状态跟进',
  '/tender-tasks': '全量投标任务筛选与导出',
  '/approval': '待审、已审与我发起的申请',
  '/knowledge': '文档入库、检索与预览',
  '/tenders': '邀请书识别与文档生成',
  '/tender-qa': '对照邀请书给生成稿或终稿打分',
  [QUOTE_NAV_HREF]: '造价、报价与控制预算一站生成',
  '/tender-library': '企业常备资料与扫描件',
  '/history-bids': '按章标记可参考或需规避的历史标书',
  '/scene-agents': '绑定提示词、知识库与 MCP',
  '/workflows': '编排知识检索 / LLM / 智能体',
  '/model-manage': '配置快速 / 深度 / Embedding',
  '/prompt-manage': '系统提示词与模板管理',
  '/mcp-manage': '接入 MCP 服务并探测健康',
  '/users': '账号、角色与菜单权限',
  '/logs': '操作审计与登录记录',
  '/settings': '主题、环境与系统信息',
}

export function getPrimaryNavItems(groups: NavGroup[]): NavItem[] {
  const byHref = new Map(flattenNavItems(groups).map((it) => [it.href, it]))
  return PRIMARY_NAV_HREFS.map((href) => byHref.get(href)).filter(
    (it): it is NavItem => Boolean(it),
  )
}

/** 按用户保存的 href 顺序排列；未知项忽略，目录新增项追加到末尾 */
export function applyNavOrder<T extends { href: string }>(
  items: T[],
  order: readonly string[] | null | undefined,
): T[] {
  if (!order?.length) return items
  const byHref = new Map(items.map((it) => [it.href, it]))
  const seen = new Set<string>()
  const result: T[] = []
  for (const href of order) {
    const it = byHref.get(href)
    if (it) {
      result.push(it)
      seen.add(href)
    }
  }
  for (const it of items) {
    if (!seen.has(it.href)) result.push(it)
  }
  return result
}

export function getMoreNavItems(groups: NavGroup[]): NavItem[] {
  const primary = new Set<string>(PRIMARY_NAV_HREFS)
  return flattenNavItems(groups).filter((it) => it.href !== '/' && !primary.has(it.href))
}

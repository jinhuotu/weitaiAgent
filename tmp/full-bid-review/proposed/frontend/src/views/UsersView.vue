<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  KeyRound,
  Loader2,
  Lock,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { assignableNavGroups, isQuotePath, QUOTE_FAMILY } from '@/config/nav'
import { Panel, PageHeader, KpiCard, Tag } from '@/components/ui-kit'
import { ApiError } from '@/lib/api'
import {
  createRole,
  createUser,
  deleteRole,
  deleteUser,
  listRoles,
  listUsers,
  resetUserPassword,
  updateRole,
  updateUser,
  type RoleItem,
  type UserItem,
} from '@/lib/users-api'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'

type FormMode = 'create' | 'edit' | 'roles' | 'password' | 'roleCreate' | 'roleEdit'

type FormState = {
  username: string
  password: string
  displayName: string
  email: string
  department: string
  phone: string
  roleCodes: string[]
  isActive: boolean
  isSuperuser: boolean
  remark: string
}

type RoleFormState = {
  name: string
  description: string
  menus: string[]
}

const emptyForm = (): FormState => ({
  username: '',
  password: '',
  displayName: '',
  email: '',
  department: '',
  phone: '',
  roleCodes: [],
  isActive: true,
  isSuperuser: false,
  remark: '',
})

const emptyRoleForm = (): RoleFormState => ({
  name: '',
  description: '',
  menus: ['/', '/ai-chat'],
})

const PROTECTED_ROLE_CODES = new Set(['admin'])
const MENU_GROUPS = assignableNavGroups()

function roleTone(
  name: string,
): 'iron' | 'sulfur' | 'molybdenum' | 'patina' | 'coolant' | 'default' {
  if (name.includes('管理')) return 'iron'
  if (name.includes('总监')) return 'sulfur'
  if (name.includes('碳')) return 'patina'
  if (name.includes('操作')) return 'coolant'
  if (name.includes('审计')) return 'default'
  return 'molybdenum'
}

function maskPhone(phone: string | null | undefined): string {
  if (!phone) return '—'
  const digits = phone.replace(/\s/g, '')
  if (digits.length < 7) return phone
  return `${digits.slice(0, 5)}*****${digits.slice(-1)}`
}

function formatLastLogin(ts: number | null): string {
  if (!ts) return '—'
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function presenceLabel(u: UserItem): '在线' | '离线' | '停用' {
  if (!u.isActive) return '停用'
  if (!u.lastLoginAt) return '离线'
  const mins = (Date.now() - u.lastLoginAt) / 60000
  return mins <= 30 ? '在线' : '离线'
}

function displayNameOf(u: UserItem): string {
  return u.displayName?.trim() || u.username
}

const auth = useAuthStore()
const router = useRouter()

const users = ref<UserItem[]>([])
const roles = ref<RoleItem[]>([])
const loading = ref(true)
const modalOpen = ref(false)
const mode = ref<FormMode>('create')
const editing = ref<UserItem | null>(null)
const editingRole = ref<RoleItem | null>(null)
const form = ref<FormState>(emptyForm())
const roleForm = ref<RoleFormState>(emptyRoleForm())
const saving = ref(false)
const formError = ref<string | null>(null)
const pendingDeleteRole = ref<RoleItem | null>(null)
const pendingDeleteUser = ref<UserItem | null>(null)
const deleting = ref(false)
const toast = ref<{ type: 'ok' | 'err'; msg: string } | null>(null)

let toastTimer: ReturnType<typeof setTimeout> | null = null

const showGate = computed(
  () => auth.loading || (!auth.isAdmin && !auth.loading),
)

const onlineCount = computed(
  () => users.value.filter((u) => presenceLabel(u) === '在线').length,
)

const monthLoginCount = computed(() => {
  const now = new Date()
  const y = now.getFullYear()
  const m = now.getMonth()
  return users.value.filter((u) => {
    if (!u.lastLoginAt) return false
    const d = new Date(u.lastLoginAt)
    return d.getFullYear() === y && d.getMonth() === m
  }).length
})

const modalTitle = computed(() => {
  if (mode.value === 'create') return '新建用户'
  if (mode.value === 'edit') return '编辑用户'
  if (mode.value === 'roles') return '分配权限'
  if (mode.value === 'password') return '重置密码'
  if (mode.value === 'roleCreate') return '新建角色'
  return '编辑角色'
})

const modalDescription = computed(() => {
  if (mode.value === 'create')
    return '创建平台账号并分配角色，初始密码请告知本人后尽快修改。'
  if (mode.value === 'edit')
    return '修改资料、部门与启用状态；用户名创建后不可更改。'
  if (mode.value === 'roles')
    return `为「${editing.value ? displayNameOf(editing.value) : ''}」分配角色权限。`
  if (mode.value === 'password')
    return `为「${editing.value ? displayNameOf(editing.value) : ''}」设置新密码。`
  if (mode.value === 'roleCreate')
    return '勾选该角色能进的页面；日志与系统管理只给管理员。'
  return '可改名称、说明和菜单；系统编码不可改，日志仍只给管理员。'
})

const isRoleMode = computed(
  () => mode.value === 'roleCreate' || mode.value === 'roleEdit',
)

const deleteRoleDescription = computed(() => {
  const r = pendingDeleteRole.value
  if (!r) return ''
  const extra =
    r.userCount > 0
      ? `，当前有 ${r.userCount} 名用户使用，需先解除绑定。`
      : '。删除后不可恢复。'
  return `编码 ${r.code}${extra}`
})

const deleteUserDescription = computed(() => {
  const u = pendingDeleteUser.value
  if (!u) return ''
  return `账号 @${u.username} 将被永久删除，相关会话等关联数据可能一并清理，且不可恢复。`
})

async function load() {
  loading.value = true
  try {
    const [userList, roleList] = await Promise.all([listUsers(), listRoles()])
    users.value = userList
    roles.value = roleList
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof ApiError || e instanceof Error ? e.message : '加载失败',
    }
  } finally {
    loading.value = false
  }
}

watch(
  () => auth.loading,
  (authLoading) => {
    if (authLoading) return
    if (!auth.isAdmin) {
      void router.replace('/')
      return
    }
    void load()
  },
  { immediate: true },
)

watch(toast, (v) => {
  if (toastTimer) clearTimeout(toastTimer)
  if (!v) return
  toastTimer = setTimeout(() => {
    toast.value = null
  }, 3600)
})

function openCreate() {
  mode.value = 'create'
  editing.value = null
  editingRole.value = null
  form.value = emptyForm()
  formError.value = null
  modalOpen.value = true
}

function openEdit(item: UserItem) {
  mode.value = 'edit'
  editing.value = item
  editingRole.value = null
  form.value = {
    username: item.username,
    password: '',
    displayName: item.displayName || '',
    email: item.email || '',
    department: item.department || '',
    phone: item.phone || '',
    roleCodes: [...item.roles],
    isActive: item.isActive,
    isSuperuser: item.isSuperuser,
    remark: item.remark || '',
  }
  formError.value = null
  modalOpen.value = true
}

function openRoles(item: UserItem) {
  mode.value = 'roles'
  editing.value = item
  editingRole.value = null
  form.value = {
    ...emptyForm(),
    roleCodes: [...item.roles],
    isSuperuser: item.isSuperuser,
  }
  formError.value = null
  modalOpen.value = true
}

function openPassword(item: UserItem) {
  mode.value = 'password'
  editing.value = item
  editingRole.value = null
  form.value = { ...emptyForm(), password: '' }
  formError.value = null
  modalOpen.value = true
}

function openRoleCreate() {
  mode.value = 'roleCreate'
  editing.value = null
  editingRole.value = null
  roleForm.value = emptyRoleForm()
  formError.value = null
  modalOpen.value = true
}

function openRoleEdit(item: RoleItem) {
  mode.value = 'roleEdit'
  editing.value = null
  editingRole.value = item
  roleForm.value = {
    name: item.name,
    description: item.description || '',
    menus: PROTECTED_ROLE_CODES.has(item.code)
      ? MENU_GROUPS.flatMap((g) => g.items.map((it) => it.href))
      : [...(item.menus || [])].filter(Boolean),
  }
  formError.value = null
  modalOpen.value = true
}

function toggleRole(code: string) {
  const has = form.value.roleCodes.includes(code)
  form.value.roleCodes = has
    ? form.value.roleCodes.filter((c) => c !== code)
    : [...form.value.roleCodes, code]
}

function roleMenusLocked() {
  return Boolean(editingRole.value && PROTECTED_ROLE_CODES.has(editingRole.value.code))
}

function menuSelected(href: string) {
  if (isQuotePath(href)) {
    return QUOTE_FAMILY.some((h) => roleForm.value.menus.includes(h))
  }
  return roleForm.value.menus.includes(href)
}

function toggleMenu(href: string) {
  if (href === '/' || roleMenusLocked()) return
  if (isQuotePath(href)) {
    const has = QUOTE_FAMILY.some((h) => roleForm.value.menus.includes(h))
    roleForm.value.menus = has
      ? roleForm.value.menus.filter((h) => !isQuotePath(h))
      : [...new Set([...roleForm.value.menus, ...QUOTE_FAMILY])]
    return
  }
  const has = roleForm.value.menus.includes(href)
  roleForm.value.menus = has
    ? roleForm.value.menus.filter((h) => h !== href)
    : [...roleForm.value.menus, href]
}

function menuCountLabel(r: RoleItem) {
  if (PROTECTED_ROLE_CODES.has(r.code)) return '全部菜单'
  const n = (r.menus || []).filter((h) => h && h !== '/logs').length
  return `${n} 个菜单`
}

function onModalOpen(v: boolean) {
  if (saving.value && !v) return
  modalOpen.value = v
  if (!v) formError.value = null
}

async function submit() {
  if (saving.value) return
  saving.value = true
  formError.value = null
  try {
    if (mode.value === 'create') {
      if (!form.value.username.trim() || !form.value.password.trim()) {
        formError.value = '请填写用户名和密码'
        saving.value = false
        return
      }
      if (form.value.password.trim().length < 6) {
        formError.value = '密码至少 6 位'
        saving.value = false
        return
      }
      await createUser({
        username: form.value.username.trim(),
        password: form.value.password.trim(),
        displayName: form.value.displayName.trim() || undefined,
        email: form.value.email.trim() || undefined,
        department: form.value.department.trim() || undefined,
        phone: form.value.phone.trim() || undefined,
        roleCodes: form.value.roleCodes,
        isActive: form.value.isActive,
        isSuperuser: form.value.isSuperuser,
        remark: form.value.remark.trim() || undefined,
      })
      toast.value = { type: 'ok', msg: '已创建用户' }
    } else if (mode.value === 'edit' && editing.value) {
      await updateUser(editing.value.id, {
        displayName: form.value.displayName.trim() || null,
        email: form.value.email.trim() || null,
        department: form.value.department.trim() || null,
        phone: form.value.phone.trim() || null,
        roleCodes: form.value.roleCodes,
        isActive: form.value.isActive,
        isSuperuser: form.value.isSuperuser,
        remark: form.value.remark.trim() || null,
      })
      toast.value = { type: 'ok', msg: '已保存用户' }
    } else if (mode.value === 'roles' && editing.value) {
      await updateUser(editing.value.id, {
        roleCodes: form.value.roleCodes,
        isSuperuser: form.value.isSuperuser,
      })
      toast.value = { type: 'ok', msg: '已更新权限' }
    } else if (mode.value === 'password' && editing.value) {
      if (form.value.password.trim().length < 6) {
        formError.value = '新密码至少 6 位'
        saving.value = false
        return
      }
      await resetUserPassword(editing.value.id, form.value.password.trim())
      toast.value = { type: 'ok', msg: '密码已重置' }
    } else if (mode.value === 'roleCreate') {
      if (!roleForm.value.name.trim()) {
        formError.value = '请填写角色名称'
        saving.value = false
        return
      }
      await createRole({
        name: roleForm.value.name.trim(),
        description: roleForm.value.description.trim() || undefined,
        menus: roleForm.value.menus,
      })
      toast.value = { type: 'ok', msg: '已创建角色' }
    } else if (mode.value === 'roleEdit' && editingRole.value) {
      if (!roleForm.value.name.trim()) {
        formError.value = '角色名称不能为空'
        saving.value = false
        return
      }
      await updateRole(editingRole.value.id, {
        name: roleForm.value.name.trim(),
        description: roleForm.value.description.trim() || null,
        menus: PROTECTED_ROLE_CODES.has(editingRole.value.code)
          ? undefined
          : roleForm.value.menus,
      })
      toast.value = { type: 'ok', msg: '已保存角色' }
    }
    modalOpen.value = false
    await load()
  } catch (e) {
    formError.value = e instanceof Error ? e.message : '操作失败'
  } finally {
    saving.value = false
  }
}

async function confirmDeleteRole() {
  if (!pendingDeleteRole.value) return
  deleting.value = true
  try {
    await deleteRole(pendingDeleteRole.value.id)
    pendingDeleteRole.value = null
    toast.value = { type: 'ok', msg: '已删除角色' }
    await load()
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '删除失败',
    }
  } finally {
    deleting.value = false
  }
}

async function confirmDeleteUser() {
  if (!pendingDeleteUser.value) return
  deleting.value = true
  try {
    await deleteUser(pendingDeleteUser.value.id)
    pendingDeleteUser.value = null
    toast.value = { type: 'ok', msg: '已删除用户' }
    await load()
  } catch (e) {
    toast.value = {
      type: 'err',
      msg: e instanceof Error ? e.message : '删除失败',
    }
  } finally {
    deleting.value = false
  }
}

function onDeleteRoleOpen(v: boolean) {
  if (deleting.value) return
  if (!v) pendingDeleteRole.value = null
}

function onDeleteUserOpen(v: boolean) {
  if (deleting.value) return
  if (!v) pendingDeleteUser.value = null
}
</script>

<template>
  <div
    v-if="showGate"
    class="py-20 text-center text-[12px] text-muted-foreground"
  >
    <Loader2 class="inline size-4 animate-spin mr-2" />
    {{ auth.loading ? '加载中…' : '无权限访问用户与权限' }}
  </div>

  <template v-else>
    <PageHeader
      title="用户与权限"
      description="基于 RBAC 模型管理平台账号与角色。支持新建用户、维护角色、分配权限与重置密码。"
    >
      <template #actions>
        <div class="flex items-center gap-2">
          <button
            type="button"
            class="h-8 px-3 inline-flex items-center gap-1.5 text-xs rounded-md border border-molybdenum/40 text-molybdenum hover:bg-molybdenum/10"
            @click="openRoleCreate"
          >
            <Plus class="size-3.5" />
            新建角色
          </button>
          <button
            type="button"
            class="h-8 px-3 inline-flex items-center gap-1.5 text-xs rounded-md bg-iron text-background hover:bg-iron/90"
            @click="openCreate"
          >
            <UserPlus class="size-3.5" />
            新建用户
          </button>
        </div>
      </template>
    </PageHeader>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
      <KpiCard
        label="用户总数"
        :value="String(users.length)"
        unit="人"
        tone="molybdenum"
      >
        <template #icon>
          <Users class="size-4" />
        </template>
      </KpiCard>
      <KpiCard
        label="在线用户"
        :value="String(onlineCount)"
        unit="人"
        tone="patina"
      />
      <KpiCard
        label="角色数量"
        :value="roles.length"
        unit="个"
        tone="sulfur"
      >
        <template #icon>
          <ShieldCheck class="size-4" />
        </template>
      </KpiCard>
      <KpiCard
        label="本月登录"
        :value="String(monthLoginCount)"
        unit="人"
        tone="coolant"
      >
        <template #icon>
          <KeyRound class="size-4" />
        </template>
      </KpiCard>
    </div>

    <Panel title="角色矩阵" class="mb-5">
      <template #action>
        <button
          type="button"
          class="h-7 px-2.5 inline-flex items-center gap-1 text-[11px] rounded-md text-molybdenum hover:bg-molybdenum/10"
          @click="openRoleCreate"
        >
          <Plus class="size-3" />
          新建
        </button>
      </template>

      <div
        v-if="loading && roles.length === 0"
        class="py-8 text-center text-xs text-muted-foreground"
      >
        <Loader2 class="inline size-4 animate-spin mr-2" />
        加载角色…
      </div>
      <div
        v-else-if="roles.length === 0"
        class="py-10 text-center text-xs text-muted-foreground border border-dashed border-border rounded-md"
      >
        暂无角色。点击「新建角色」添加，或执行 seed_admin 初始化种子角色。
      </div>
      <div
        v-else
        class="flex gap-3 overflow-x-auto pb-1 -mx-1 px-1 scroll-smooth [scrollbar-width:thin]"
      >
        <div
          v-for="r in roles"
          :key="r.id"
          class="rounded-md panel-elevated p-4 group relative shrink-0 w-[calc((100%-3.75rem)/6)] min-w-[220px]"
        >
          <div class="flex items-start justify-between gap-1 mb-2">
            <div class="flex items-center gap-2 min-w-0">
              <Lock class="size-3.5 text-muted-foreground shrink-0" />
              <span class="text-sm font-medium truncate">{{ r.name }}</span>
            </div>
            <div
              class="flex items-center gap-0.5 shrink-0 opacity-70 group-hover:opacity-100"
            >
              <button
                type="button"
                title="编辑"
                class="size-6 rounded text-muted-foreground hover:text-molybdenum hover:bg-molybdenum/10 inline-flex items-center justify-center"
                @click="openRoleEdit(r)"
              >
                <Pencil class="size-3" />
              </button>
              <button
                type="button"
                :title="PROTECTED_ROLE_CODES.has(r.code) ? '系统角色不可删除' : '删除'"
                :disabled="PROTECTED_ROLE_CODES.has(r.code)"
                class="size-6 rounded text-muted-foreground hover:text-iron hover:bg-iron/10 inline-flex items-center justify-center disabled:opacity-30 disabled:pointer-events-none"
                @click="pendingDeleteRole = r"
              >
                <Trash2 class="size-3" />
              </button>
            </div>
          </div>
          <div class="data-num text-lg text-foreground">{{ r.userCount }} 人</div>
          <div class="text-[10px] text-muted-foreground mt-1 line-clamp-2">
            {{ r.description || '—' }}
          </div>
          <div class="text-[10px] text-muted-foreground/80 mt-1">
            {{ menuCountLabel(r) }}
          </div>
          <div
            v-if="PROTECTED_ROLE_CODES.has(r.code)"
            class="text-[10px] text-muted-foreground/70 mt-1.5"
          >
            系统角色
          </div>
        </div>
      </div>
    </Panel>

    <Panel title="用户列表" flush>
      <div
        v-if="loading"
        class="py-16 text-center text-xs text-muted-foreground"
      >
        <Loader2 class="inline size-4 animate-spin mr-2" />
        加载用户…
      </div>
      <div
        v-else-if="users.length === 0"
        class="py-16 text-center text-xs text-muted-foreground"
      >
        暂无用户。点击右上角「新建用户」添加。
      </div>
      <div v-else class="overflow-x-auto">
        <table class="w-full text-xs min-w-[800px]">
          <thead class="text-muted-foreground bg-background/40">
            <tr class="border-b border-border">
              <th
                v-for="h in ['用户', '角色', '部门', '联系方式', '状态', '最近登录', '操作']"
                :key="h"
                class="text-left font-medium px-4 py-2.5"
              >
                {{ h }}
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-border">
            <tr
              v-for="u in users"
              :key="u.id"
              class="hover:bg-background/40"
            >
              <td class="px-4 py-3">
                <div class="flex items-center gap-2.5">
                  <div
                    class="size-7 rounded-full bg-gradient-to-br from-molybdenum to-coolant text-[10px] font-semibold text-background flex items-center justify-center"
                  >
                    {{ displayNameOf(u).slice(0, 1) }}
                  </div>
                  <div class="min-w-0">
                    <div class="font-medium truncate">{{ displayNameOf(u) }}</div>
                    <div class="text-[10px] text-muted-foreground font-mono">
                      @{{ u.username }}
                    </div>
                  </div>
                </div>
              </td>
              <td class="px-4 py-3">
                <div class="flex flex-wrap gap-1">
                  <template v-if="u.roleNames.length > 0">
                    <Tag
                      v-for="rn in u.roleNames"
                      :key="rn"
                      :tone="roleTone(rn)"
                    >
                      {{ rn }}
                    </Tag>
                  </template>
                  <Tag
                    v-else
                    :tone="u.isSuperuser ? 'iron' : 'default'"
                  >
                    {{ u.isSuperuser ? '超级管理员' : '未分配' }}
                  </Tag>
                </div>
              </td>
              <td class="px-4 py-3 text-foreground/80">{{ u.department || '—' }}</td>
              <td class="px-4 py-3 data-num text-muted-foreground">
                {{ maskPhone(u.phone) }}
              </td>
              <td class="px-4 py-3">
                <span
                  :class="
                    presenceLabel(u) === '在线'
                      ? 'inline-flex items-center gap-1 text-patina'
                      : presenceLabel(u) === '停用'
                        ? 'inline-flex items-center gap-1 text-iron'
                        : 'inline-flex items-center gap-1 text-muted-foreground'
                  "
                >
                  <span
                    :class="
                      presenceLabel(u) === '在线'
                        ? 'size-1.5 rounded-full bg-patina'
                        : presenceLabel(u) === '停用'
                          ? 'size-1.5 rounded-full bg-iron'
                          : 'size-1.5 rounded-full bg-muted-foreground'
                    "
                  />
                  {{ presenceLabel(u) }}
                </span>
              </td>
              <td class="px-4 py-3 data-num text-muted-foreground">
                {{ formatLastLogin(u.lastLoginAt) }}
              </td>
              <td class="px-4 py-3 whitespace-nowrap">
                <button
                  type="button"
                  class="text-iron hover:underline"
                  @click="openRoles(u)"
                >
                  权限
                </button>
                <span class="text-border mx-1.5">|</span>
                <button
                  type="button"
                  class="text-muted-foreground hover:text-foreground"
                  @click="openEdit(u)"
                >
                  编辑
                </button>
                <span class="text-border mx-1.5">|</span>
                <button
                  type="button"
                  class="text-muted-foreground hover:text-foreground"
                  @click="openPassword(u)"
                >
                  重置密码
                </button>
                <span class="text-border mx-1.5">|</span>
                <button
                  type="button"
                  class="text-muted-foreground hover:text-iron disabled:opacity-40 disabled:pointer-events-none"
                  :disabled="auth.user?.id === u.id"
                  :title="auth.user?.id === u.id ? '不能删除当前登录账号' : '删除用户'"
                  @click="pendingDeleteUser = u"
                >
                  删除
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </Panel>

    <AppDialog
      :open="modalOpen"
      :title="modalTitle"
      :description="modalDescription"
      :wide="isRoleMode"
      @update:open="onModalOpen"
    >
      <div class="space-y-3">
        <template v-if="isRoleMode">
          <label class="block">
            <div class="text-[11px] text-text-secondary mb-1">角色名称 *</div>
            <input
              v-model="roleForm.name"
              class="kb-input font-sans"
              placeholder="如：能源总监"
            />
          </label>
          <label class="block">
            <div class="text-[11px] text-text-secondary mb-1">权限说明</div>
            <input
              v-model="roleForm.description"
              class="kb-input font-sans"
              placeholder="如：运营 + 决策视图"
            />
          </label>
          <div class="block">
            <div class="text-[11px] text-text-secondary mb-1.5">可访问菜单</div>
            <p
              v-if="roleMenusLocked()"
              class="mb-2 text-[11px] text-text-muted"
            >
              管理员固定全部菜单，日志仅管理员可见，此处不可改。
            </p>
            <div class="max-h-[42vh] space-y-3 overflow-y-auto pr-1">
              <div v-for="g in MENU_GROUPS" :key="g.title">
                <div class="mb-1.5 text-[10px] text-muted-foreground">{{ g.title }}</div>
                <div class="flex flex-wrap gap-1.5">
                  <button
                    v-for="it in g.items"
                    :key="it.href"
                    type="button"
                    :disabled="it.href === '/' || roleMenusLocked()"
                    :class="[
                      'px-2.5 py-1 rounded-md text-[11px] border transition-colors disabled:opacity-70',
                      menuSelected(it.href)
                        ? 'border-iron bg-iron/10 text-iron'
                        : 'border-hairline text-text-secondary hover:text-text-primary',
                    ]"
                    @click="toggleMenu(it.href)"
                  >
                    {{ it.label }}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </template>

        <template v-if="mode === 'create' || mode === 'edit'">
          <label v-if="mode === 'create'" class="block">
            <div class="text-[11px] text-text-secondary mb-1">用户名 *</div>
            <input
              v-model="form.username"
              class="kb-input"
              placeholder="登录账号，如 zhangkq"
              autocomplete="off"
            />
          </label>
          <label v-else class="block">
            <div class="text-[11px] text-text-secondary mb-1">用户名</div>
            <input class="kb-input opacity-60" :value="form.username" disabled />
          </label>
          <label v-if="mode === 'create'" class="block">
            <div class="text-[11px] text-text-secondary mb-1">初始密码 *</div>
            <input
              v-model="form.password"
              class="kb-input"
              type="password"
              placeholder="至少 6 位"
              autocomplete="new-password"
            />
          </label>
          <div class="grid grid-cols-2 gap-3">
            <label class="block">
              <div class="text-[11px] text-text-secondary mb-1">显示名</div>
              <input
                v-model="form.displayName"
                class="kb-input font-sans"
                placeholder="如：张克强"
              />
            </label>
            <label class="block">
              <div class="text-[11px] text-text-secondary mb-1">部门</div>
              <input
                v-model="form.department"
                class="kb-input font-sans"
                placeholder="如：能源管理部"
              />
            </label>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <label class="block">
              <div class="text-[11px] text-text-secondary mb-1">手机</div>
              <input
                v-model="form.phone"
                class="kb-input"
                placeholder="11 位手机号"
              />
            </label>
            <label class="block">
              <div class="text-[11px] text-text-secondary mb-1">邮箱</div>
              <input
                v-model="form.email"
                class="kb-input"
                placeholder="可选"
              />
            </label>
          </div>
        </template>

        <label v-if="mode === 'password'" class="block">
          <div class="text-[11px] text-text-secondary mb-1">新密码 *</div>
          <input
            v-model="form.password"
            class="kb-input"
            type="password"
            placeholder="至少 6 位"
            autocomplete="new-password"
          />
        </label>

        <div v-if="mode === 'create' || mode === 'edit' || mode === 'roles'">
          <div class="text-[11px] text-text-secondary mb-1.5">角色</div>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="r in roles"
              :key="r.code"
              type="button"
              :class="[
                'px-2.5 py-1 rounded-md text-[11px] border transition-colors',
                form.roleCodes.includes(r.code)
                  ? 'border-iron bg-iron/10 text-iron'
                  : 'border-hairline text-text-secondary hover:text-text-primary',
              ]"
              @click="toggleRole(r.code)"
            >
              {{ r.name }}
            </button>
            <span
              v-if="roles.length === 0"
              class="text-[11px] text-muted-foreground"
            >
              暂无角色，请先新建角色
            </span>
          </div>
        </div>

        <div
          v-if="mode === 'create' || mode === 'edit' || mode === 'roles'"
          class="flex flex-wrap gap-4 text-[12px]"
        >
          <label
            v-if="mode === 'create' || mode === 'edit'"
            class="inline-flex items-center gap-1.5 cursor-pointer"
          >
            <input
              v-model="form.isActive"
              type="checkbox"
              class="accent-[var(--color-iron,#ff6b35)]"
            />
            启用账号
          </label>
          <label class="inline-flex items-center gap-1.5 cursor-pointer">
            <input
              v-model="form.isSuperuser"
              type="checkbox"
              class="accent-[var(--color-iron,#ff6b35)]"
            />
            超级管理员
          </label>
        </div>

        <label v-if="mode === 'create' || mode === 'edit'" class="block">
          <div class="text-[11px] text-text-secondary mb-1">备注</div>
          <input
            v-model="form.remark"
            class="kb-input font-sans"
            placeholder="可选"
          />
        </label>

        <div
          v-if="formError"
          class="text-[11px] text-iron bg-iron/10 border border-iron/30 rounded-md px-3 py-2"
        >
          {{ formError }}
        </div>
      </div>

      <template #footer>
        <button
          type="button"
          :disabled="saving"
          class="kb-btn-secondary"
          @click="modalOpen = false"
        >
          取消
        </button>
        <button
          type="button"
          :disabled="saving"
          class="kb-btn-primary"
          @click="submit()"
        >
          <Loader2 v-if="saving" class="size-3.5 animate-spin" />
          确认
        </button>
      </template>
    </AppDialog>

    <AppAlertDialog
      :open="Boolean(pendingDeleteRole)"
      :title="`删除角色「${pendingDeleteRole?.name || ''}」？`"
      :description="deleteRoleDescription"
      confirm-label="确认删除"
      :loading="deleting"
      destructive
      @update:open="onDeleteRoleOpen"
      @confirm="confirmDeleteRole"
    />

    <AppAlertDialog
      :open="Boolean(pendingDeleteUser)"
      :title="`删除用户「${pendingDeleteUser ? displayNameOf(pendingDeleteUser) : ''}」？`"
      :description="deleteUserDescription"
      confirm-label="确认删除"
      :loading="deleting"
      destructive
      @update:open="onDeleteUserOpen"
      @confirm="confirmDeleteUser"
    />

    <div
      v-if="toast"
      :class="[
        'fixed bottom-6 right-6 z-[80] px-4 py-2.5 rounded-md text-[12px] border',
        toast.type === 'ok'
          ? 'bg-patina/10 border-patina/40 text-patina'
          : 'bg-iron/10 border-iron/40 text-iron',
      ]"
    >
      {{ toast.msg }}
    </div>
  </template>
</template>

<style scoped>
:deep(.kb-input) {
  background: var(--bg-surface);
  border: 1px solid var(--hairline);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 12px;
  padding: 8px 10px;
  width: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
:deep(.kb-input::placeholder) {
  color: var(--text-muted);
}
:deep(.kb-input:focus) {
  outline: none;
  border-color: color-mix(in oklab, var(--accent-iron) 55%, transparent);
}
:deep(.kb-input:disabled) {
  cursor: not-allowed;
  opacity: 0.6;
}
:deep(.kb-input.font-sans) {
  font-family: inherit;
}
</style>

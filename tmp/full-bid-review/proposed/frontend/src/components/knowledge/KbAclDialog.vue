<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Loader2, Plus, Shield, Trash2, X } from 'lucide-vue-next'
import { ApiError } from '@/lib/api'
import {
  getKnowledgeBaseAcl,
  saveKnowledgeBaseAcl,
  type KbAclGrant,
  type KbAclPayload,
} from '@/lib/knowledge-api'

const props = defineProps<{
  open: boolean
  baseId: string
  baseName?: string
}>()

const emit = defineEmits<{ close: []; saved: [] }>()

const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const payload = ref<KbAclPayload | null>(null)
const grants = ref<KbAclGrant[]>([])
const addType = ref<'user' | 'role'>('user')
const addId = ref<number | null>(null)

const userOptions = computed(() => payload.value?.directory.users || [])
const roleOptions = computed(() => payload.value?.directory.roles || [])
const taken = computed(
  () => new Set(grants.value.map((g) => `${g.subjectType}:${g.subjectId}`)),
)
const availableUsers = computed(() =>
  userOptions.value.filter((u) => !taken.value.has(`user:${u.id}`)),
)
const availableRoles = computed(() =>
  roleOptions.value.filter((r) => !taken.value.has(`role:${r.id}`)),
)

function labelOf(g: KbAclGrant): string {
  if (g.subjectLabel) return g.subjectLabel
  if (g.subjectType === 'user') {
    const u = userOptions.value.find((x) => x.id === g.subjectId)
    return u ? u.displayName || u.username : `用户#${g.subjectId}`
  }
  const r = roleOptions.value.find((x) => x.id === g.subjectId)
  return r ? r.name : `角色#${g.subjectId}`
}

function setLevel(g: KbAclGrant, level: 'view' | 'use' | 'manage') {
  g.canView = true
  g.canUse = level !== 'view'
  g.canManage = level === 'manage'
}

function levelOf(g: KbAclGrant): 'view' | 'use' | 'manage' {
  if (g.canManage) return 'manage'
  if (g.canUse) return 'use'
  return 'view'
}

async function load() {
  if (!props.baseId) return
  loading.value = true
  error.value = null
  try {
    const data = await getKnowledgeBaseAcl(props.baseId)
    payload.value = data
    grants.value = (data.grants || []).map((g) => ({ ...g }))
    addType.value = 'user'
    addId.value = null
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '加载授权失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.open, props.baseId] as const,
  ([open]) => {
    if (open) void load()
  },
)

function addGrant() {
  if (addId.value == null) return
  const st = addType.value
  const sid = addId.value
  if (taken.value.has(`${st}:${sid}`)) return
  const g: KbAclGrant = {
    subjectType: st,
    subjectId: sid,
    canView: true,
    canUse: true,
    canManage: false,
  }
  grants.value = [...grants.value, g]
  addId.value = null
}

function removeGrant(idx: number) {
  grants.value = grants.value.filter((_, i) => i !== idx)
}

async function save() {
  saving.value = true
  error.value = null
  try {
    await saveKnowledgeBaseAcl(props.baseId, grants.value)
    emit('saved')
    emit('close')
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '保存失败'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-[70] bg-bg-base/80 backdrop-blur-sm flex items-center justify-center p-4"
    @click.self="!saving && emit('close')"
  >
    <div class="w-full max-w-lg rounded-lg border border-hairline bg-bg-elevated shadow-2xl max-h-[min(88vh,40rem)] flex flex-col">
      <div class="px-5 py-3.5 border-b border-hairline flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="text-[14px] font-medium flex items-center gap-1.5">
            <Shield class="size-4 text-molybdenum" />
            知识库权限
          </div>
          <div class="text-[11px] text-text-muted mt-0.5 truncate">
            {{ baseName || '分配查看 / 使用 / 维护' }}
          </div>
        </div>
        <button
          type="button"
          class="size-8 rounded-md text-text-muted hover:bg-hairline/40 inline-flex items-center justify-center"
          :disabled="saving"
          @click="emit('close')"
        >
          <X class="size-4" />
        </button>
      </div>

      <div class="px-5 py-3 text-[11px] text-text-secondary leading-relaxed border-b border-hairline">
        无授权的人在列表里看不到本库，对话里也无法检索。
        <span class="text-text-muted">{{ payload?.note }}</span>
      </div>

      <div class="px-5 py-3 overflow-y-auto flex-1 space-y-3">
        <div v-if="loading" class="py-8 text-center text-[12px] text-text-muted">
          <Loader2 class="inline size-4 animate-spin mr-1.5" />
          加载授权…
        </div>
        <template v-else>
          <div class="flex flex-wrap items-end gap-2">
            <label class="block min-w-[5.5rem]">
              <div class="text-[11px] text-text-secondary mb-1">类型</div>
              <select v-model="addType" class="acl-input" @change="addId = null">
                <option value="user">用户</option>
                <option value="role">角色</option>
              </select>
            </label>
            <label class="block flex-1 min-w-[10rem]">
              <div class="text-[11px] text-text-secondary mb-1">对象</div>
              <select v-model.number="addId" class="acl-input">
                <option :value="null">请选择</option>
                <template v-if="addType === 'user'">
                  <option
                    v-for="u in availableUsers"
                    :key="'u' + u.id"
                    :value="u.id"
                  >
                    {{ u.displayName || u.username }}{{ u.department ? ` · ${u.department}` : '' }}
                  </option>
                </template>
                <template v-else>
                  <option
                    v-for="r in availableRoles"
                    :key="'r' + r.id"
                    :value="r.id"
                  >
                    {{ r.name }}（{{ r.code }}）
                  </option>
                </template>
              </select>
            </label>
            <button
              type="button"
              class="h-9 px-3 rounded-md border border-hairline text-[12px] inline-flex items-center gap-1 hover:bg-hairline/40 disabled:opacity-40"
              :disabled="addId == null"
              @click="addGrant"
            >
              <Plus class="size-3.5" />
              添加
            </button>
          </div>

          <div v-if="grants.length === 0" class="text-[12px] text-text-muted py-2">
            尚未额外授权。只有管理员和创建人能维护本库。
          </div>
          <ul v-else class="space-y-1.5">
            <li
              v-for="(g, idx) in grants"
              :key="`${g.subjectType}:${g.subjectId}`"
              class="flex items-center gap-2 rounded-md border border-hairline px-2.5 py-2 bg-bg-base/40"
            >
              <span
                class="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-mono border border-hairline text-text-muted"
              >
                {{ g.subjectType === 'user' ? '用户' : '角色' }}
              </span>
              <span class="flex-1 min-w-0 truncate text-[12px]">{{ labelOf(g) }}</span>
              <select
                class="h-8 rounded-md border border-hairline bg-transparent text-[11px] px-1.5"
                :value="levelOf(g)"
                @change="setLevel(g, ($event.target as HTMLSelectElement).value as 'view' | 'use' | 'manage')"
              >
                <option value="view">查看</option>
                <option value="use">使用</option>
                <option value="manage">维护</option>
              </select>
              <button
                type="button"
                class="size-8 rounded-md text-text-muted hover:text-iron hover:bg-iron/10 inline-flex items-center justify-center"
                title="移除"
                @click="removeGrant(idx)"
              >
                <Trash2 class="size-3.5" />
              </button>
            </li>
          </ul>
        </template>
        <div
          v-if="error"
          class="rounded-md border border-iron/40 bg-iron/10 px-3 py-2 text-[12px] text-iron"
        >
          {{ error }}
        </div>
      </div>

      <div class="px-5 py-3 border-t border-hairline flex justify-end gap-2">
        <button
          type="button"
          class="h-8 px-3 text-[12px] rounded-md border border-hairline text-text-secondary hover:bg-hairline/40"
          :disabled="saving"
          @click="emit('close')"
        >
          取消
        </button>
        <button
          type="button"
          class="h-8 px-3 text-[12px] rounded-md bg-molybdenum text-white hover:brightness-110 inline-flex items-center gap-1.5"
          :disabled="saving || loading"
          @click="save"
        >
          <Loader2 v-if="saving" class="size-3.5 animate-spin" />
          <Shield v-else class="size-3.5" />
          {{ saving ? '保存中…' : '保存权限' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.acl-input {
  background: var(--bg-surface);
  border: 1px solid var(--hairline);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 12px;
  padding: 7px 10px;
  width: 100%;
}
.acl-input:focus {
  outline: none;
  border-color: var(--accent-molybdenum);
}
</style>

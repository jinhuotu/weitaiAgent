import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  type AuthUser,
  fetchMe,
  getAccessToken,
  login as apiLogin,
  logout as apiLogout,
  refreshTokens,
} from '@/lib/auth'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null)
  const loading = ref(true)

  const isAdmin = computed(() =>
    Boolean(user.value?.is_superuser || user.value?.roles?.includes('admin')),
  )

  const menus = computed(() => user.value?.menus ?? [])

  async function bootstrap() {
    const token = getAccessToken()
    if (!token) {
      user.value = null
      loading.value = false
      return
    }
    try {
      user.value = await fetchMe()
    } catch {
      const refreshed = await refreshTokens()
      user.value = refreshed?.user ?? null
    } finally {
      loading.value = false
    }
  }

  async function login(username: string, password: string) {
    const result = await apiLogin(username, password)
    user.value = result.user
  }

  function logout() {
    apiLogout()
    user.value = null
  }

  return { user, loading, isAdmin, menus, bootstrap, login, logout }
})

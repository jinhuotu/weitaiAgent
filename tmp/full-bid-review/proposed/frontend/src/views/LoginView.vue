<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Loader2, Lock, UserRound } from 'lucide-vue-next'
import { ApiError } from '@/lib/api'
import { useAuthStore } from '@/stores/auth'
import weitaiBg from '@/assets/weitai.png'
import { APP_NAME, APP_VERSION, COMPANY_NAME, COPYRIGHT_YEAR } from '@/config/brand'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const username = ref('admin')
const password = ref('Admin@123456')
const submitting = ref(false)
const error = ref('')

async function onSubmit() {
  error.value = ''
  submitting.value = true
  try {
    await auth.login(username.value.trim(), password.value)
    const next = typeof route.query.next === 'string' ? route.query.next : '/'
    await router.replace(next.startsWith('/') ? next : '/')
  } catch (err) {
    const msg = err instanceof Error ? err.message : ''
    if (/Failed to fetch dynamically imported module|Loading chunk|Importing a module script failed/i.test(msg)) {
      error.value = '页面资源加载失败，请按 Ctrl+F5 强制刷新后再试'
    } else if (err instanceof ApiError) {
      error.value =
        err.message === 'invalid username or password'
          ? '用户名或密码错误'
          : err.message
    } else if (err instanceof TypeError) {
      error.value = '无法连接后端，请确认伟泰智能 API 已启动（默认 :8100）'
    } else {
      error.value = '登录失败，请稍后重试'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div
    v-if="(auth.loading || auth.user) && !error"
    class="flex min-h-screen items-center justify-center bg-[#f7f5f0] text-sm text-neutral-500"
  >
    {{ auth.loading ? '正在校验登录状态…' : '正在进入系统…' }}
  </div>

  <div v-else class="login-page">
    <div
      class="login-bg"
      :style="{ backgroundImage: `url(${weitaiBg})` }"
      aria-hidden="true"
    />

    <div class="login-stage">
      <section class="login-panel" aria-label="账号登录">
        <header class="login-panel__head">
          <p class="login-panel__eyebrow">河南伟泰光电科技</p>
          <h1 class="login-panel__title">欢迎登录</h1>
          <p class="login-panel__desc">智能体交互系统 · 知识库 · 投标与布置</p>
        </header>

        <form class="login-form" @submit.prevent="onSubmit">
          <label class="login-field">
            <span class="login-field__label">用户名</span>
            <div class="login-field__control">
              <UserRound class="login-field__icon" aria-hidden="true" />
              <input
                v-model="username"
                autocomplete="username"
                class="login-field__input"
                placeholder="请输入用户名"
                required
              />
            </div>
          </label>

          <label class="login-field">
            <span class="login-field__label">密码</span>
            <div class="login-field__control">
              <Lock class="login-field__icon" aria-hidden="true" />
              <input
                v-model="password"
                type="password"
                autocomplete="current-password"
                class="login-field__input"
                placeholder="请输入密码"
                required
              />
            </div>
          </label>

          <div v-if="error" class="login-error" role="alert">
            {{ error }}
          </div>

          <button
            type="submit"
            class="login-submit"
            :disabled="submitting || auth.loading"
          >
            <Loader2 v-if="submitting" class="size-4 animate-spin" />
            {{ submitting ? '登录中…' : '登 录' }}
          </button>
        </form>
      </section>
    </div>

    <footer class="login-footer">
      技术支持：{{ COMPANY_NAME }}
      ©{{ COPYRIGHT_YEAR }} {{ APP_NAME }} {{ APP_VERSION }}
    </footer>
  </div>
</template>

<style scoped>
.login-page {
  position: relative;
  min-height: 100dvh;
  width: 100%;
  overflow-x: hidden;
  overflow-y: auto;
  background: #f4f1ea;
  color: #1a1a1a;
}

.login-bg {
  position: absolute;
  inset: 0;
  background-repeat: no-repeat;
  background-size: cover;
  background-position: left center;
}

.login-stage {
  position: relative;
  z-index: 1;
  display: flex;
  min-height: 100dvh;
  width: 100%;
  align-items: center;
  justify-content: flex-end;
  padding: clamp(1rem, 3vw, 2.5rem);
  padding-bottom: 3.25rem;
}

.login-footer {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 2;
  padding: 0.65rem 1rem 0.9rem;
  text-align: center;
  font-size: 0.75rem;
  line-height: 1.5;
  letter-spacing: 0.02em;
  color: #6b7280;
  white-space: nowrap;
}

.login-panel {
  position: relative;
  isolation: isolate;
  width: min(100%, 26rem);
  margin-right: clamp(0rem, 6vw, 7rem);
  margin-top: clamp(1.25rem, 8vh, 9rem);
  padding: clamp(1.5rem, 3vw, 2.25rem);
  border-radius: 1rem;
  background: #fff;
  border: 1px solid #d0d5de;
  box-shadow:
    0 10px 28px rgba(20, 40, 80, 0.1),
    0 1px 3px rgba(0, 0, 0, 0.06);
}

.login-panel::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: -1;
  border-radius: inherit;
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(8px);
}

.login-panel__head {
  margin-bottom: 1.5rem;
}

.login-panel__eyebrow {
  margin: 0;
  font-size: 0.75rem;
  letter-spacing: 0.08em;
  color: #c41e2a;
  font-weight: 600;
}

.login-panel__title {
  margin: 0.35rem 0 0;
  font-size: clamp(1.5rem, 2.4vw, 1.85rem);
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #111;
}

.login-panel__desc {
  margin: 0.45rem 0 0;
  font-size: 0.8125rem;
  line-height: 1.5;
  color: #6b7280;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.login-field {
  display: block;
}

.login-field__label {
  display: block;
  margin-bottom: 0.4rem;
  font-size: 0.75rem;
  color: #6b7280;
}

.login-field__control {
  position: relative;
  border-radius: 0.65rem;
  background: #fff;
  /* 低 DPI 大屏上 1px 浅边会被抗锯齿吃掉，用 inset 描边 */
  box-shadow: inset 0 0 0 1.5px #9aa3b2;
}

.login-field__icon {
  position: absolute;
  left: 0.85rem;
  top: 50%;
  width: 1rem;
  height: 1rem;
  transform: translateY(-50%);
  color: #9ca3af;
  pointer-events: none;
}

.login-field__input {
  width: 100%;
  height: 2.75rem;
  padding: 0 0.9rem 0 2.5rem;
  border: 0;
  border-radius: inherit;
  background: transparent;
  font-size: 0.875rem;
  color: #111;
  outline: none;
  appearance: none;
}

.login-field__input::placeholder {
  color: #c0c4cc;
}

.login-field__control:focus-within {
  box-shadow:
    inset 0 0 0 1.5px #c41e2a,
    0 0 0 3px rgba(196, 30, 42, 0.12);
}

.login-error {
  padding: 0.65rem 0.85rem;
  border-radius: 0.65rem;
  border: 1px solid rgba(196, 30, 42, 0.28);
  background: rgba(196, 30, 42, 0.06);
  color: #c41e2a;
  font-size: 0.75rem;
  line-height: 1.45;
}

.login-submit {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  width: 100%;
  height: 2.85rem;
  margin-top: 0.35rem;
  border: none;
  border-radius: 0.65rem;
  background: linear-gradient(135deg, #d61f2c 0%, #b01520 100%);
  color: #fff;
  font-size: 0.9375rem;
  font-weight: 600;
  letter-spacing: 0.12em;
  cursor: pointer;
  transition:
    opacity 0.15s ease,
    transform 0.15s ease;
}

.login-submit:hover:not(:disabled) {
  opacity: 0.94;
  transform: translateY(-1px);
}

.login-submit:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

/* 中等屏：保证右侧留白区可见，表单略收窄 */
@media (max-width: 1100px) {
  .login-bg {
    background-position: 18% center;
  }

  .login-panel {
    margin-right: clamp(0rem, 3vw, 2rem);
  }
}

/* 小屏：背景偏左保留车与树，表单居中覆盖 */
@media (max-width: 768px) {
  .login-bg {
    background-size: cover;
    background-position: 12% center;
  }

  .login-stage {
    justify-content: center;
    align-items: flex-end;
    padding: 1rem 1rem 3.75rem;
  }

  .login-panel {
    width: min(100%, 24rem);
    margin-right: 0;
    margin-bottom: 0.5rem;
    background: rgba(255, 255, 255, 0.96);
  }
}

@media (max-width: 480px) {
  .login-stage {
    align-items: center;
  }

  .login-bg {
    background-position: 8% center;
    filter: saturate(1.05);
  }

  .login-panel {
    width: 100%;
  }

  .login-footer {
    font-size: 0.6875rem;
    padding-inline: 0.5rem;
  }
}

@media (max-resolution: 1.25dppx), (-webkit-max-device-pixel-ratio: 1.25) {
  .login-field__control {
    box-shadow: inset 0 0 0 2px #7d8694;
  }

  .login-field__control:focus-within {
    box-shadow:
      inset 0 0 0 2px #c41e2a,
      0 0 0 3px rgba(196, 30, 42, 0.12);
  }

  .login-panel {
    border-color: #b8c0cc;
  }
}

@media (max-height: 800px) {
  .login-panel {
    margin-top: 0.5rem;
  }
}
</style>

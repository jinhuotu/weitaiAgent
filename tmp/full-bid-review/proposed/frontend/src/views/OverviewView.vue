<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronRight, Ellipsis, FileText, Loader2, X } from 'lucide-vue-next'
import { ApiError } from '@/lib/api'
import { fetchTenderRecords, type TenderRecordItem } from '@/lib/tenders-api'
import { fmtAgo } from '@/lib/time'
import { NAV_ITEM_DESC } from '@/config/nav'
import { useNavDragReorder } from '@/composables/useNavDragReorder'
import { useOrderedPrimaryNav } from '@/composables/useOrderedPrimaryNav'
import { APP_NAME, APP_VERSION, COMPANY_NAME, COPYRIGHT_YEAR } from '@/config/brand'

const router = useRouter()
const { items: primaryItems, moreItems: moreNavItems, commitOrder } = useOrderedPrimaryNav()
const navDrag = useNavDragReorder(primaryItems, commitOrder)

const loading = ref(true)
const error = ref('')
const myTaskCount = ref(0)
const processingCount = ref(0)
const pendingCount = ref(0)
const wonCount = ref(0)
const tenderRecords = ref<TenderRecordItem[]>([])
const tenderTotal = ref(0)
const tenderError = ref('')
const moreOpen = ref(false)

const visiblePrimary = computed(() =>
  primaryItems.value.map((it) => ({
    ...it,
    desc: NAV_ITEM_DESC[it.href] || '',
  })),
)

const moreItems = computed(() =>
  moreNavItems.value.map((it) => ({
    ...it,
    desc: NAV_ITEM_DESC[it.href] || '',
  })),
)

function openModule(href: string, _label: string) {
  moreOpen.value = false
  void router.push(href)
}

function openTenderRecord(item: TenderRecordItem) {
  void router.push({ path: '/tenders', query: { record: item.id } })
}

function openWorkTasks(status?: 'processing' | 'pending' | 'won') {
  moreOpen.value = false
  void router.push(status ? { path: '/work-tasks', query: { status } } : '/work-tasks')
}

function openTenderHistory() {
  void router.push('/tenders')
}

function countByStatus(items: TenderRecordItem[], status: string): number {
  return items.filter((item) => item.status === status).length
}

function onDocClick(ev: MouseEvent) {
  const target = ev.target as HTMLElement | null
  if (!target?.closest?.('[data-more-menu]')) {
    moreOpen.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [mine, rec] = await Promise.all([
      fetchTenderRecords({ scope: 'mine', limit: 200 }).catch(() => null),
      fetchTenderRecords({ limit: 5 }).catch(() => null),
    ])
    const mineItems = mine?.items || []
    myTaskCount.value = mineItems.length
    processingCount.value = countByStatus(mineItems, 'processing')
    pendingCount.value = countByStatus(mineItems, 'pending')
    wonCount.value = countByStatus(mineItems, 'won')
    if (rec) {
      tenderRecords.value = rec.items || []
      tenderTotal.value = rec.total || 0
      tenderError.value = ''
    } else {
      tenderRecords.value = []
      tenderTotal.value = 0
      tenderError.value = '加载投标记录失败'
    }
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '加载总览失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
  document.addEventListener('click', onDocClick)
})

onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
})
</script>

<template>
  <div class="portal mx-auto flex w-full max-w-5xl min-h-full flex-col">
    <section class="portal-hero">
      <div class="portal-hero__glow" aria-hidden="true" />
      <div class="portal-hero__body">
        <div class="min-w-0 flex-1">
          <p class="portal-hero__eyebrow">河南伟泰光电 · 智能体交互系统</p>
          <h1 class="portal-hero__title">工作台</h1>
          <p class="portal-hero__desc">
            上方为本人投标任务；常用业务从下方入口进入，管理与配置请点「更多」。
          </p>
          <p
            v-if="error"
            class="mt-2 text-[12px] text-destructive"
          >
            {{ error }}
          </p>

          <div class="portal-stats">
            <button type="button" class="portal-stat" @click="openWorkTasks()">
              <span class="portal-stat__value">{{ loading ? '—' : myTaskCount }}</span>
              <span class="portal-stat__label">我的任务</span>
            </button>
            <button type="button" class="portal-stat" @click="openWorkTasks('processing')">
              <span class="portal-stat__value portal-stat__value--coolant">{{ loading ? '—' : processingCount }}</span>
              <span class="portal-stat__label">编制中</span>
            </button>
            <button type="button" class="portal-stat" @click="openWorkTasks('pending')">
              <span class="portal-stat__value portal-stat__value--sulfur">{{ loading ? '—' : pendingCount }}</span>
              <span class="portal-stat__label">待审批</span>
            </button>
            <button type="button" class="portal-stat" @click="openWorkTasks('won')">
              <span class="portal-stat__value portal-stat__value--patina">{{ loading ? '—' : wonCount }}</span>
              <span class="portal-stat__label">已中标</span>
            </button>
          </div>
        </div>

        <div class="portal-runtime">
          <div class="portal-runtime__head">
            <span class="text-[12px] font-medium">历史投标文件</span>
            <button type="button" class="portal-runtime__more" @click="openTenderHistory">
              全部{{ tenderTotal ? ` ${tenderTotal}` : '' }}
              <ChevronRight class="size-3.5" />
            </button>
          </div>

          <div v-if="loading" class="flex items-center gap-2 py-3 text-[12px] text-muted-foreground">
            <Loader2 class="size-3.5 animate-spin" />
            加载中…
          </div>
          <div
            v-else-if="tenderError"
            class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-[12px] text-destructive"
          >
            {{ tenderError }}
          </div>
          <p v-else-if="!tenderRecords.length" class="py-3 text-[12px] text-muted-foreground">
            还没有生成记录。可从「AI标书生成」识别邀请书后生成。
          </p>
          <ul v-else class="portal-tenders">
            <li v-for="item in tenderRecords" :key="item.id">
              <button type="button" class="portal-tender" @click="openTenderRecord(item)">
                <span class="portal-tender__icon" aria-hidden="true">
                  <FileText class="size-3.5" />
                </span>
                <span class="min-w-0 flex-1">
                  <span class="portal-tender__name">{{ item.projectName || '未命名项目' }}</span>
                  <span class="portal-tender__meta">
                    {{ item.tenderer || '—' }}
                    · {{ fmtAgo(item.createdAt, true) }}
                    <span v-if="!item.docxAvailable" class="text-sulfur"> · 文件缺失</span>
                  </span>
                </span>
                <ChevronRight class="size-3.5 shrink-0 text-muted-foreground" />
              </button>
            </li>
          </ul>
        </div>
      </div>
    </section>

    <section class="portal-modules">
      <div class="mb-2.5 flex items-end justify-between gap-3">
        <div>
          <h2 class="text-[15px] font-semibold tracking-wide">常用功能</h2>
          <p class="mt-0.5 text-[12px] text-muted-foreground">拖拽可调整顺序，点击进入对应模块</p>
        </div>
      </div>

      <div class="primary-grid">
        <div
          v-for="(item, index) in visiblePrimary"
          :key="item.href"
          class="primary-sort"
          :class="navDrag.itemClass(index)"
          draggable="true"
          title="拖拽调整顺序"
          @dragstart="navDrag.onDragStart(index, $event)"
          @dragover="navDrag.onDragOver(index, $event)"
          @dragenter="navDrag.onDragOver(index, $event)"
          @drop="navDrag.onDrop(index, $event)"
          @dragend="navDrag.onDragEnd"
        >
          <button
            type="button"
            class="primary-tile"
            draggable="false"
            @click.capture="navDrag.onClickCapture"
            @click="openModule(item.href, item.label)"
          >
            <span class="primary-tile__icon">
              <component :is="item.icon" class="size-6" />
            </span>
            <span class="primary-tile__label">{{ item.label }}</span>
            <span class="primary-tile__desc">{{ item.desc }}</span>
          </button>
        </div>

        <div v-if="moreItems.length" data-more-menu class="primary-more">
          <button
            type="button"
            class="primary-tile primary-tile--more"
            :class="{ 'primary-tile--active': moreOpen }"
            @click.stop="moreOpen = !moreOpen"
          >
            <span class="primary-tile__icon primary-tile__icon--muted">
              <Ellipsis class="size-6" />
            </span>
            <span class="primary-tile__label">更多</span>
            <span class="primary-tile__desc">管理、配置与其他入口</span>
          </button>

          <div v-if="moreOpen" class="more-panel" role="menu">
            <div class="more-panel__head">
              <span class="text-[12px] font-medium">更多功能</span>
              <button
                type="button"
                class="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent"
                aria-label="关闭"
                @click="moreOpen = false"
              >
                <X class="size-3.5" />
              </button>
            </div>
            <button
              v-for="item in moreItems"
              :key="item.href"
              type="button"
              class="more-item"
              role="menuitem"
              @click="openModule(item.href, item.label)"
            >
              <span class="more-item__icon">
                <component :is="item.icon" class="size-4" />
              </span>
              <span class="min-w-0 flex-1 text-left">
                <span class="block text-[13px] font-medium">{{ item.label }}</span>
                <span v-if="item.desc" class="block text-[11px] text-muted-foreground mt-0.5">
                  {{ item.desc }}
                </span>
              </span>
            </button>
          </div>
        </div>
      </div>
    </section>

    <footer class="portal-footer">
      技术支持：{{ COMPANY_NAME }}
      ©{{ COPYRIGHT_YEAR }} {{ APP_NAME }} {{ APP_VERSION }}
    </footer>
  </div>
</template>

<style scoped>
.portal {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 100%;
}

.portal-footer {
  margin-top: auto;
  padding: 1rem 0.5rem 0.15rem;
  text-align: center;
  font-size: 0.75rem;
  line-height: 1.5;
  letter-spacing: 0.02em;
  color: var(--muted-foreground, #6b7280);
  white-space: nowrap;
}

.portal-modules {
  margin-top: 1.1rem;
}

.portal-hero {
  position: relative;
  overflow: hidden;
  border-radius: 1rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  background:
    linear-gradient(
      135deg,
      color-mix(in srgb, var(--accent-iron, #2563eb) 8%, hsl(var(--card))) 0%,
      hsl(var(--card)) 48%,
      color-mix(in srgb, var(--accent-coolant, #0ea5e9) 6%, hsl(var(--card))) 100%
    );
  box-shadow: 0 1px 2px color-mix(in srgb, #000 4%, transparent);
}

.portal-hero__glow {
  position: absolute;
  right: -10%;
  top: -40%;
  width: 55%;
  height: 140%;
  border-radius: 50%;
  background: radial-gradient(
    circle,
    color-mix(in srgb, var(--accent-iron, #2563eb) 18%, transparent),
    transparent 70%
  );
  pointer-events: none;
}

.portal-hero__body {
  position: relative;
  display: grid;
  gap: 0.9rem;
  padding: clamp(0.85rem, 2vw, 1.25rem);
}

@media (min-width: 900px) {
  .portal-hero__body {
    grid-template-columns: minmax(0, 1.4fr) minmax(240px, 0.85fr);
    align-items: stretch;
  }
}

.portal-hero__eyebrow {
  margin: 0;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--accent-iron, #2563eb);
}

.portal-hero__title {
  margin: 0.25rem 0 0;
  font-size: clamp(1.35rem, 2.4vw, 1.7rem);
  font-weight: 700;
  letter-spacing: 0.02em;
}

.portal-hero__desc {
  margin: 0.3rem 0 0;
  max-width: 36rem;
  font-size: 0.8125rem;
  line-height: 1.45;
  color: hsl(var(--muted-foreground));
}

.portal-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.55rem;
  margin-top: 0.75rem;
}

.portal-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.1rem;
  padding: 0.5rem 0.65rem;
  border-radius: 0.65rem;
  border: 1px solid color-mix(in srgb, var(--hairline, hsl(var(--border))) 80%, transparent);
  background: color-mix(in srgb, hsl(var(--background)) 55%, transparent);
  color: inherit;
  text-align: center;
  cursor: pointer;
}

.portal-stat:hover {
  border-color: color-mix(in srgb, var(--accent-iron, #2563eb) 40%, hsl(var(--border)));
  background: color-mix(in srgb, var(--accent-iron, #2563eb) 6%, transparent);
}

.portal-stat__value {
  font-size: 1.15rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.portal-stat__value--coolant {
  color: var(--accent-coolant, #0ea5e9);
}

.portal-stat__value--sulfur {
  color: var(--accent-sulfur, #d97706);
}

.portal-stat__value--patina {
  color: var(--accent-patina, #059669);
}

.portal-stat__label {
  font-size: 0.6875rem;
  color: hsl(var(--muted-foreground));
}

.portal-runtime {
  display: flex;
  flex-direction: column;
  border-radius: 0.75rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  background: color-mix(in srgb, hsl(var(--background)) 70%, transparent);
  padding: 0.7rem 0.85rem;
}

.portal-runtime__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.portal-runtime__more {
  display: inline-flex;
  align-items: center;
  gap: 0.1rem;
  border: none;
  background: transparent;
  padding: 0;
  font-size: 0.6875rem;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
}

.portal-runtime__more:hover {
  color: var(--accent-iron, #2563eb);
}

.portal-tenders {
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 9.5rem;
  overflow: auto;
}

.portal-tender {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  width: 100%;
  padding: 0.5rem 0.15rem;
  text-align: left;
  border: none;
  border-top: 1px solid color-mix(in srgb, hsl(var(--border)) 70%, transparent);
  background: transparent;
  color: inherit;
  cursor: pointer;
  border-radius: 0.4rem;
}

.portal-tenders li:first-child .portal-tender {
  border-top: none;
}

.portal-tender:hover {
  background: color-mix(in srgb, var(--accent-iron, #2563eb) 7%, transparent);
}

.portal-tender__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 0.45rem;
  background: color-mix(in srgb, var(--accent-iron, #2563eb) 12%, transparent);
  color: var(--accent-iron, #2563eb);
  flex-shrink: 0;
}

.portal-tender__name {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.75rem;
  font-weight: 550;
}

.portal-tender__meta {
  display: block;
  margin-top: 0.1rem;
  font-size: 0.6875rem;
  color: hsl(var(--muted-foreground));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.primary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.7rem;
}

@media (min-width: 768px) {
  .primary-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (min-width: 1024px) {
  .primary-grid {
    grid-template-columns: repeat(5, minmax(0, 1fr));
  }
}

.primary-more {
  position: relative;
}

.primary-sort {
  min-width: 0;
  cursor: grab;
  user-select: none;
}

.primary-sort.is-dragging {
  opacity: 0.42;
  cursor: grabbing;
}

.primary-sort.is-over .primary-tile {
  border-color: color-mix(in srgb, var(--accent-iron, #2563eb) 70%, hsl(var(--border)));
  border-style: dashed;
  background: color-mix(in srgb, var(--accent-iron, #2563eb) 10%, hsl(var(--card)));
}

.primary-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  width: 100%;
  min-height: 7.75rem;
  padding: 0.85rem 0.75rem;
  text-align: center;
  border-radius: 0.9rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  background: hsl(var(--card));
  color: inherit;
  cursor: inherit;
  user-select: none;
  transition:
    border-color 0.15s ease,
    background 0.15s ease,
    transform 0.15s ease,
    box-shadow 0.15s ease;
}

.primary-tile:hover,
.primary-tile--active {
  border-color: color-mix(in srgb, var(--accent-iron, #2563eb) 45%, hsl(var(--border)));
  background: color-mix(in srgb, var(--accent-iron, #2563eb) 6%, hsl(var(--card)));
  box-shadow: 0 10px 28px color-mix(in srgb, var(--accent-iron, #2563eb) 12%, transparent);
  transform: translateY(-2px);
}

.primary-sort.is-dragging .primary-tile,
.primary-sort.is-dragging .primary-tile:hover {
  transform: none;
  box-shadow: none;
}

.primary-tile--more,
.primary-tile--more:hover {
  cursor: pointer;
}

.primary-tile__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.85rem;
  height: 2.85rem;
  border-radius: 0.85rem;
  background: color-mix(in srgb, var(--accent-iron, #2563eb) 14%, transparent);
  color: var(--accent-iron, #2563eb);
}

.primary-tile__icon--muted {
  background: color-mix(in srgb, hsl(var(--muted-foreground)) 12%, transparent);
  color: hsl(var(--muted-foreground));
}

.primary-tile__label {
  font-size: 0.9375rem;
  font-weight: 650;
  letter-spacing: 0.02em;
}

.primary-tile__desc {
  font-size: 0.6875rem;
  line-height: 1.4;
  color: hsl(var(--muted-foreground));
  max-width: 11rem;
}

.more-panel {
  position: absolute;
  z-index: 20;
  left: 0;
  right: 0;
  top: calc(100% + 0.5rem);
  min-width: min(20rem, 85vw);
  max-height: min(22rem, 50vh);
  overflow: auto;
  border-radius: 0.85rem;
  border: 1px solid var(--hairline, hsl(var(--border)));
  background: hsl(var(--card));
  box-shadow: 0 16px 40px color-mix(in srgb, #000 14%, transparent);
  padding: 0.4rem;
}

@media (min-width: 1024px) {
  .more-panel {
    left: auto;
    right: 0;
    width: 20rem;
  }
}

.more-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.45rem 0.55rem 0.55rem;
  border-bottom: 1px solid color-mix(in srgb, hsl(var(--border)) 80%, transparent);
  margin-bottom: 0.25rem;
}

.more-item {
  display: flex;
  align-items: flex-start;
  gap: 0.7rem;
  width: 100%;
  padding: 0.65rem 0.55rem;
  border: none;
  border-radius: 0.55rem;
  background: transparent;
  color: inherit;
  cursor: pointer;
  transition: background 0.12s ease;
}

.more-item:hover {
  background: color-mix(in srgb, var(--accent-iron, #2563eb) 8%, transparent);
}

.more-item__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 0.5rem;
  background: color-mix(in srgb, var(--accent-iron, #2563eb) 10%, transparent);
  color: var(--accent-iron, #2563eb);
  flex-shrink: 0;
}

@media (max-width: 520px) {
  .portal-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .portal-footer {
    font-size: 0.6875rem;
    white-space: normal;
  }
}
</style>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { packageDownload, packageRequest } from '@/lib/tender-package-api'
import { ApiError } from '@/lib/api'
import { mapArguments, type Wizard } from '@/lib/tender-package-wizard'
import IssueDynamicForm from '@/components/tender-package/IssueDynamicForm.vue'
const route = useRoute(), router = useRouter()
const tasks = ref<any[]>([]), wizard = ref<Wizard | null>(null), busy = ref(false), error = ref(''), selected = ref(''), tab = ref('open'), advanced = ref<any>(null), search = ref('')
const current = computed(() => wizard.value?.issues.find(i => i.id === selected.value))
const counts = computed(() => Object.fromEntries(['open', 'deferred', 'resolved'].map(s => [s, wizard.value?.issues.filter(i => i.status === s).length || 0])))
const visible = computed(() => wizard.value?.issues.filter(i => i.status === tab.value && `${i.title} ${i.description}`.includes(search.value)) || [])
const percent = computed(() => wizard.value ? Math.round(100 * wizard.value.progress.done / Math.max(1, wizard.value.progress.total)) : 0)
const steps = ['安全解包', '识别文件与物料', '汇聚招标信息', '人工核验', '生成草稿']
const advancedLink = computed(() => ({ path: '/tender-package', query: wizard.value ? { id: wizard.value.id } : typeof route.query.id === 'string' ? { id: route.query.id } : {} }))
let active = true
async function run(fn: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await fn() } catch (e) { error.value = e instanceof Error ? e.message : '处理未完成，请刷新后重试'; if (wizard.value) { try { await refresh(wizard.value.id) } catch { /* Preserve action error. */ } } }
  finally { busy.value = false }
}
async function refresh(id: string) {
  let next: Wizard
  try { next = await packageRequest<Wizard>(`/${id}/wizard`) }
  catch (e) {
    if (e instanceof ApiError && e.status === 404) throw Error('任务不存在，或当前服务尚未加载向导接口。可先使用高级模式，并在正常部署窗口加载新版本。')
    throw e
  }
  if (!active) return
  wizard.value = next; advanced.value = null
  if (!next.issues.some(i => i.id === selected.value && i.status === 'open')) selected.value = next.issues.find(i => i.status === 'open')?.id || ''
}
async function invoke(operation: string, args: any = {}) {
  const w = wizard.value!
  await packageRequest(`/${w.id}/actions/${operation}`, 'POST', { revision: w.revision, arguments: args })
  await refresh(w.id)
}
async function advance() {
  // Retry is always deliberate. Revisions and decisions come from a fresh server projection.
  let count = 0
  while (active && wizard.value?.can_auto_continue && wizard.value.next_action) {
    if (++count > 4) throw Error('自动阶段已暂停，请刷新核对处理状态')
    const operation = wizard.value.next_action
    if (!['unpack', 'classify', 'ssot', 'generate'].includes(operation)) throw Error('此阶段请打开高级模式处理')
    await invoke(operation)
  }
}
async function open(id: string) {
  if (!id) return
  tab.value = 'open'; search.value = ''; selected.value = ''
  await refresh(id); await router.replace({ path: '/tender-package-wizard', query: { id } }); await advance()
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]
  if (!file) return
  await run(async () => {
    const form = new FormData(); form.append('file', file)
    const task = await packageRequest('/upload', 'POST', form)
    tasks.value = await packageRequest(''); await open(task.id)
  }); input.value = ''
}
async function submit(data: Record<string, any>) {
  await run(async () => {
    const issue = current.value
    if (!issue?.target_action || issue.status !== 'open' || issue.unavailable_reason) throw Error('此项暂不能提交，请核对待办状态')
    const action = issue.target_action, answers = { ...data }
    if (action.upload) {
      const file = answers[action.upload.field]
      if (!(file instanceof File)) throw Error('请选择来源文件')
      const form = new FormData(); form.append('file', file); form.append('revision', String(wizard.value!.revision))
      const upload = await packageRequest(`/${wizard.value!.id}/sources`, 'POST', form)
      await refresh(wizard.value!.id); answers[action.upload.assign] = upload.upload_id
    }
    await invoke(action.operation, mapArguments(action.arguments, answers)); await advance()
  })
}
async function details() { if (wizard.value) advanced.value = await packageRequest(`/${wizard.value.id}`) }
onMounted(() => { void run(async () => { tasks.value = await packageRequest(''); if (typeof route.query.id === 'string') await open(route.query.id) }) })
onBeforeUnmount(() => { active = false })
</script>
<template>
  <main class="wizard-page">
    <header class="page-heading"><div><p class="eyebrow">压缩包标书 · 向导</p><h1>把待确认的问题，一项项核对清楚</h1><p class="muted">自动整理招标文件；涉及物料、范围、报价与资料的决定由你确认。</p></div><RouterLink :to="advancedLink" class="outline">高级模式 ↗</RouterLink></header>
    <section class="task-bar" aria-label="选择任务"><label class="upload" :class="{ disabled: busy }">上传压缩包<input type="file" accept=".zip,.rar,.7z" :disabled="busy" @change="upload"></label><select aria-label="已有压缩包任务" :value="wizard?.id || ''" :disabled="busy" @change="run(() => open(($event.target as HTMLSelectElement).value))"><option disabled value="">或继续已有任务</option><option v-for="t in tasks" :key="t.id" :value="t.id">{{ t.name }}</option></select><button :disabled="busy || !wizard" :title="busy ? '正在处理，请稍候' : !wizard ? '请先上传或选择任务' : '重新读取状态并继续可自动处理的阶段'" @click="run(async () => { await refresh(wizard!.id); await advance() })">刷新进度</button></section>
    <p v-if="error" role="alert" class="error">{{ error }} <RouterLink :to="advancedLink">打开高级模式核对</RouterLink></p>
    <template v-if="wizard">
      <section class="progress-card" aria-live="polite"><div class="progress-heading"><h2>{{ busy ? '正在处理 · ' : '' }}{{ wizard.stage_label }}</h2><span>{{ wizard.progress.done }} / {{ wizard.progress.total }} · {{ percent }}%</span></div><progress :value="wizard.progress.done" :max="wizard.progress.total" /><ol class="steps"><li v-for="step in steps" :key="step" :class="{ active: step === wizard.stage_label }">{{ step }}</li></ol><p class="muted">{{ wizard.next_hint }}</p><p v-if="wizard.retry_required" class="reason">自动推进已暂停，避免重复执行。<button :disabled="busy || !wizard.retry_action" :title="wizard.retry_action ? '核对错误信息后重试当前阶段' : '请先处理待办，或到高级模式检查相应操作'" @click="run(async () => { await invoke(wizard!.retry_action!); await advance() })">我已核对，重试此阶段</button></p></section>
      <p v-if="wizard.test_only" class="test-notice">当前任务含测试占位数据，禁止用于真实投标。</p>
      <div class="review-layout">
        <aside class="issue-list"><div class="tabs"><button v-for="(label, key) in { open: '待处理', deferred: '需后续核验', resolved: '已完成' }" :key="key" :class="{ selected: tab === key }" @click="tab = key; selected = ''">{{ label }} {{ counts[key] }}</button></div><input v-model="search" aria-label="搜索问题" placeholder="搜索物料或问题"><p v-if="!visible.length" class="empty">此分组暂无问题</p><button v-for="i in visible" :key="i.id" class="issue-button" :class="{ selected: selected === i.id }" :disabled="busy" @click="selected = i.id"><span v-if="i.severity === 'blocking'" class="priority">优先核对</span><strong>{{ i.title }}</strong><span>{{ i.description }}</span></button></aside>
        <section class="question-card" v-if="current"><p class="eyebrow">{{ current.status === 'open' ? '需要你的决定' : current.status === 'resolved' ? '已记录核验结果' : '仍需后续核验' }}</p><h2>{{ current.title }}</h2><p class="description">{{ current.description }}</p><details v-if="current.evidence.length" class="evidence" open><summary>核对依据 · {{ current.evidence.length }} 条</summary><blockquote v-for="(e, i) in current.evidence" :key="i"><strong>{{ e.label }}</strong><p>{{ e.quote || '请核对对应原文' }}</p><cite>{{ e.locator }}</cite></blockquote></details><IssueDynamicForm v-if="current.status === 'open'" :key="current.id" :issue="current" :busy="busy" @submit="submit"><template #advanced><RouterLink :to="advancedLink">打开高级模式</RouterLink></template></IssueDynamicForm><p v-else class="reason">{{ current.unavailable_reason || '确认记录与原始依据保存在任务历史中。' }} <RouterLink :to="advancedLink">查看或纠正历史</RouterLink></p></section>
        <section v-else class="question-card empty"><h2>{{ counts.open ? '请选择一项待办' : '当前待办已处理' }}</h2><p>未覆盖的核验与原始记录可在高级模式查看。生成的文件始终是待验收草稿。</p></section>
      </div>
      <section v-if="wizard.result" class="results"><h2>草稿与下载</h2><p>{{ wizard.result.visual_label }}。已渲染 {{ wizard.result.rendered_pages }} 页；渲染完成不表示逐页验收通过。</p><div v-for="p in wizard.result.packages" :key="p.key" class="package-result"><h3>{{ p.package_code || '待确认标包' }}</h3><p class="muted">物料：{{ p.material_ids.join('、') }}</p><ul><template v-for="(a, i) in wizard.result.artifacts" :key="a.path"><li v-if="a.path.split(/[\\/]/)[0] === p.key"><button :disabled="busy" :title="busy ? '请等待当前处理完成' : '下载待验收草稿'" @click="run(async () => { await packageDownload(wizard!.id, Number(i), a.path.split(/[\\/]/).pop()) })">下载 {{ a.path.split(/[\\/]/).slice(-2).join(' / ') }}</button></li></template></ul></div><details v-if="wizard.result.warnings.length"><summary>草稿提醒 · {{ wizard.result.warnings.length }} 条</summary><ul><li v-for="(w, i) in wizard.result.warnings" :key="i">{{ w.message }}</li></ul></details></section>
      <details class="advanced" @toggle="($event.target as HTMLDetailsElement).open && run(details)"><summary>高级详情（原始数据与处理历史）</summary><pre v-if="advanced">{{ JSON.stringify(advanced, null, 2) }}</pre><p v-else>展开后加载原始数据。</p></details>
      <p class="footnote">{{ wizard.regression_status }}。全部人工核验与整包验收完成前，不标记为可投标。</p>
    </template>
    <section v-else class="welcome"><h2>从一个压缩包开始</h2><p>支持嵌套压缩包。上传后自动完成安全解包、文件识别与信息汇聚，遇到需要人工决定的内容会停下来。</p></section>
  </main>
</template>
<style scoped>
.wizard-page{max-width:1400px;padding:32px;margin:auto;color:var(--foreground,#172033)}.page-heading,.progress-heading{display:flex;justify-content:space-between;align-items:center;gap:20px}.eyebrow{font-size:12px;letter-spacing:1px;color:var(--muted-foreground,#64748b);margin:0 0 8px}h1{font-size:26px;line-height:1.4;font-weight:650;margin:0 0 8px}h2{font-size:19px;font-weight:650;margin:0 0 12px}h3{font-size:16px;font-weight:600}.muted,.description{color:var(--muted-foreground,#64748b);line-height:1.7}.task-bar,.progress-card,.results,.question-card,.welcome,.advanced{background:var(--card,#fff);border:1px solid var(--border,#dce3ed);border-radius:12px;padding:24px;margin-top:22px}.task-bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.task-bar select{flex:1;min-width:180px}.upload,.outline,button{border:1px solid var(--border,#cbd5e1);border-radius:8px;padding:9px 14px;cursor:pointer;background:var(--background,#fff);color:inherit;text-decoration:none;font-size:14px}.upload{background:#1d4ed8;color:white;border-color:#1d4ed8}.upload input{display:none}.disabled,button:disabled{opacity:.5;cursor:not-allowed}select,input:not([type=file]){border:1px solid var(--border,#cbd5e1);border-radius:8px;background:var(--background,#fff);color:inherit;padding:10px}.progress-heading span{font-size:14px;color:#64748b}progress{width:100%;height:8px;accent-color:#2563eb;margin:8px 0 18px}.steps{display:flex;gap:16px;flex-wrap:wrap;padding:0;list-style:none;font-size:13px;color:#94a3b8}.steps .active{color:#2563eb;font-weight:650}.review-layout{display:grid;grid-template-columns:320px minmax(0,1fr);gap:22px;margin-top:22px}.issue-list{max-height:850px;overflow:auto;display:flex;flex-direction:column;gap:10px}.tabs{display:flex;gap:2px}.tabs button{padding:8px 9px;font-size:12px;white-space:nowrap}.selected{border-color:#2563eb!important;background:var(--accent,#eff6ff)!important}.issue-button{display:grid;text-align:left;gap:6px;background:var(--card,#fff);padding:16px}.issue-button strong{font-size:14px;line-height:1.5}.issue-button>span:not(.priority){font-size:12px;color:#64748b;line-height:1.6;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.priority{color:#b45309;font-size:11px}.question-card{margin:0;align-self:start;min-width:0;padding:28px}.evidence{margin:20px 0 24px;max-height:330px;overflow:auto;background:var(--muted,#f8fafc);border-radius:8px;padding:16px;font-size:13px}.evidence summary{cursor:pointer}blockquote{border-left:3px solid #cbd5e1;margin:14px 0;padding-left:12px;overflow-wrap:anywhere}blockquote p{margin:6px 0;white-space:pre-wrap}cite{font-size:11px;font-style:normal;color:#64748b}.empty{color:#64748b;padding:30px;line-height:1.8}.reason,.test-notice{color:#92400e;background:#fffbeb;border-radius:8px;padding:12px;margin-top:14px}.error{color:#b91c1c;background:#fef2f2;padding:15px;border-radius:8px}.package-result{margin:20px 0;padding:16px;background:var(--muted,#f8fafc);border-radius:8px;overflow-wrap:anywhere}.package-result ul{display:flex;gap:8px;flex-wrap:wrap;list-style:none;padding:0}.results details{margin-top:20px;line-height:1.8}.advanced summary{cursor:pointer;font-size:14px}.advanced pre{max-height:500px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.footnote{font-size:12px;color:#64748b;margin-top:20px}@media(max-width:900px){.wizard-page{padding:16px}.review-layout{grid-template-columns:1fr}.issue-list{max-height:320px}.page-heading{align-items:start;flex-direction:column}h1{font-size:22px}}
</style>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Panel } from '@/components/ui-kit'
import { fetchMaterials, fetchMaterialSections, uploadMaterial, downloadMaterial, deleteMaterial,
  type ArchiveCategory, type ArchiveFile, type MaterialUse, type MaterialSelections } from '@/lib/tender-materials-api'

const props = withDefaults(defineProps<{
  bidderName?: string; invitationId?: string; modelValue?: MaterialSelections; sections?: string[]; libraryOnly?: boolean; disabled?: boolean
}>(), { bidderName: '', invitationId: '', modelValue: () => ({}), sections: () => [], libraryOnly: false, disabled: false })
const emit = defineEmits<{ 'update:modelValue': [value: MaterialSelections]; check: [] }>()
const categories = ref<ArchiveCategory[]>([])
const files = ref<ArchiveFile[]>([])
const error = ref('')
const company = ref(props.bidderName)
watch(() => props.bidderName, v => { company.value = v })
const busy = ref(false)
const tab = ref('project')
const entities = ref<Record<string, string>>({})
const subtypes = ref<Record<string, string>>({})
const originalSections = ref<string[]>([])
const targetSections = computed(() => [...new Set([...props.sections, ...originalSections.value])])
const activeCategories = computed(() => categories.value.filter(c =>
  tab.value === 'project' ? c.scope === 'project' : c.key === tab.value))
const tabs = computed(() => [
  ...(!props.libraryOnly ? [{ key: 'project', title: '项目资料与分类图纸' }] : []),
  { key: 'personnel', title: '人员库' }, { key: 'product', title: '产品/设备库' }, { key: 'performance', title: '业绩三件套' },
])
function choice(key: string): MaterialUse {
  return props.modelValue[key] || { enabled: false, fileIds: [], mode: ['personnel','product','performance','site_plan','system_diagram','construction_drawing','detail_drawing'].includes(key) ? 'scan' : 'reference', targetSection: '' }
}
function update(key: string, value: Partial<MaterialUse>) {
  emit('update:modelValue', { ...props.modelValue, [key]: { ...choice(key), ...value } })
}
function selectFile(key: string, id: string, checked: boolean) {
  update(key, { fileIds: checked ? [...new Set([...choice(key).fileIds, id])] : choice(key).fileIds.filter(x => x !== id) })
}
let revision = 0
async function load() {
  const current = ++revision
  error.value = ''
  originalSections.value = []
  files.value = []
  try {
    const result = await fetchMaterials(props.invitationId)
    if (current !== revision) return
    categories.value = result.categories; files.value = result.files
    for (const c of result.categories) if (c.subtypes.length === 1) subtypes.value[c.key] = c.subtypes[0] || ''
    if (!props.libraryOnly && props.invitationId) {
      const source = await fetchMaterialSections(props.invitationId)
      if (current === revision) originalSections.value = source.sections
    } else originalSections.value = []
  } catch (e) { if (current === revision) error.value = e instanceof Error ? e.message : '资料归档加载失败' }
}
watch(() => props.invitationId, load, { immediate: true })
watch(() => props.libraryOnly, v => { if (v) tab.value = 'personnel' }, { immediate: true })
async function upload(event: Event, c: ArchiveCategory) {
  const input = event.target as HTMLInputElement
  const picked = [...(input.files || [])]; input.value = ''
  if (!picked.length) return
  if (!subtypes.value[c.key]) { error.value = '请先选择资料类型，再上传文件'; return }
  if (c.scope === 'library' && !entities.value[c.key]?.trim()) { error.value = '请先填写资料归属名称'; return }
  busy.value = true; error.value = ''
  try {
    for (const file of picked) await uploadMaterial(file, c.key, subtypes.value[c.key] || c.subtypes[0] || '', entities.value[c.key] || '', props.invitationId, company.value)
    await load()
  } catch (e) { await load(); error.value = e instanceof Error ? e.message : '上传失败' }
  finally { busy.value = false }
}
async function download(file: ArchiveFile) {
  try { await downloadMaterial(file, props.invitationId) }
  catch (e) { error.value = e instanceof Error ? e.message : '下载失败' }
}
async function remove(file: ArchiveFile) {
  if (!window.confirm(`删除归档文件「${file.filename}」？`)) return
  busy.value = true
  try { await deleteMaterial(file, props.invitationId); selectFile(file.category, file.id, false); await load() }
  catch (e) { error.value = e instanceof Error ? e.message : '删除失败' }
  finally { busy.value = false }
}
</script>

<template>
  <Panel :title="libraryOnly ? '独立结构化资料库' : '项目资料与图纸（生成前独立归档）'" subtitle="新上传资料默认仅入库。开启分类开关并勾选文件后，才按指定方式参与生成。">
    <p v-if="error" role="alert" class="mb-3 text-sm text-red-600">{{ error }}</p>
    <button v-if="!libraryOnly && invitationId" type="button" class="kb-btn-secondary mb-3" :disabled="disabled || busy" @click="emit('check')">生成前独立缺件检查</button>
    <div class="flex flex-wrap gap-2 mb-4" role="tablist" aria-label="独立资料库">
      <button v-for="item in tabs" :key="item.key" type="button" role="tab" :aria-selected="tab === item.key"
        class="kb-btn-secondary" :class="tab === item.key ? 'bg-accent font-semibold' : ''" @click="tab = item.key">{{ item.title }}</button>
    </div>
    <label class="block mb-3 text-sm">资料所属投标人（人员、产品与业绩参与生成前须核对）<input v-model="company" class="kb-input mt-1 w-full" placeholder="本次投标人的全称，不能填其他公司" :disabled="disabled || busy" /></label>
    <p class="mb-3 text-xs text-muted-foreground">Word / Excel / PDF / 图片 / TXT，单文件40MB以内；CAD原文件可归档，组卷请另传PDF。人员按姓名、产品按名称、业绩按项目名称分组。生成前检查仅展示缺件，不向目录或正文追加占位。</p>
    <fieldset v-for="c in activeCategories" :key="c.key" :disabled="disabled || busy || (c.scope === 'project' && !invitationId)" class="border border-border rounded-lg p-3 mb-3 space-y-3">
      <legend class="px-1 text-sm font-medium">{{ c.title }}</legend>
      <p v-if="c.scope === 'project' && !invitationId" class="text-xs">请先识别本项目邀请书，再归档项目资料。</p>
      <div class="flex flex-wrap items-center gap-2">
        <input v-if="c.scope === 'library'" v-model="entities[c.key]" class="kb-input" maxlength="120" :aria-label="`${c.title}归属名称`" :placeholder="c.key === 'personnel' ? '人员姓名（必填）' : c.key === 'product' ? '产品/设备名称（必填）' : '业绩项目名称（必填）'" />
        <select v-model="subtypes[c.key]" class="kb-input" :aria-label="`${c.title}资料类型`"><option :value="undefined" disabled>选择资料类型</option><option v-for="s in c.subtypes" :key="s" :value="s">{{ s }}</option></select>
        <label class="kb-btn-secondary cursor-pointer">{{ busy ? '正在解析归档…' : '上传归档（不自动选用）' }}<input type="file" multiple class="sr-only" accept=".doc,.docx,.pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.xlsx,.xls,.txt,.dwg,.dxf" @change="upload($event,c)" /></label>
      </div>
      <div v-if="!libraryOnly" class="flex flex-wrap gap-3 items-center text-sm">
        <label><input type="checkbox" :checked="choice(c.key).enabled" @change="update(c.key,{enabled:($event.target as HTMLInputElement).checked})" /> 本类参与生成（默认关闭）</label>
        <template v-if="choice(c.key).enabled">
          <select class="kb-input" :value="choice(c.key).mode" aria-label="参与方式" @change="update(c.key,{mode:($event.target as HTMLSelectElement).value as 'scan'|'reference'})"><option value="scan">贴入现有章节</option><option value="reference">技术起草参考</option></select>
          <select v-if="choice(c.key).mode === 'scan'" class="kb-input max-w-full" :value="choice(c.key).targetSection" aria-label="对应现有章节" @change="update(c.key,{targetSection:($event.target as HTMLSelectElement).value})"><option value="">请选择对应现有章节</option><option v-for="s in targetSections" :key="s" :value="s">{{ s }}</option></select>
          <span v-else class="text-xs text-muted-foreground">需同时开启技术方案自动起草；不会直接改写报价或招标条款。</span>
        </template>
      </div>
      <ul class="space-y-2 text-xs">
        <li v-for="file in files.filter(f=>f.category===c.key)" :key="file.id" class="flex flex-wrap items-center gap-2 border-t border-border pt-2">
          <input v-if="!libraryOnly" type="checkbox" :aria-label="`选用${file.filename}`" :disabled="!choice(c.key).enabled" :checked="choice(c.key).fileIds.includes(file.id)" @change="selectFile(c.key,file.id,($event.target as HTMLInputElement).checked)" />
          <span class="font-medium">{{ file.entity ? `${file.entity} · ` : '' }}{{ file.subtype }} · {{ file.filename }}</span>
          <span class="text-muted-foreground">{{ file.chars }}字 · {{ file.note }}</span>
          <button type="button" class="underline" @click="download(file)">下载原件</button><button type="button" class="underline" @click="remove(file)">删除</button>
        </li>
      </ul>
    </fieldset>
  </Panel>
</template>

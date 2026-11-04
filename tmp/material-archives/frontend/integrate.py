from pathlib import Path

root = Path('tmp/material-archives/frontend')
p = root / 'tenders-api.ts'
s = p.read_text(encoding='utf-8')
s = s.replace('export type BidBrief = {', "export type BidBrief = {\n  materialSelections?: import('./tender-materials-api').MaterialSelections", 1)
p.write_text(s, encoding='utf-8')
p = root / 'TendersView.vue'
s = p.read_text(encoding='utf-8')
s = s.replace("import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'", "import AppAlertDialog from '@/components/ui/AppAlertDialog.vue'\nimport MaterialArchivePanel from '@/components/tenders/MaterialArchivePanel.vue'\nimport { checkMaterials, type MaterialCheck } from '@/lib/tender-materials-api'", 1)
s = s.replace('function emptyBrief(): BidBrief {\n  return {', 'function emptyBrief(): BidBrief {\n  return {\n    materialSelections: {},', 1)
code = '''const materialCheckOpen = ref(false)
const materialCheckBusy = ref(false)
const materialCheckReport = ref<MaterialCheck | null>(null)
let materialApproved = false
let materialCheckForGeneration = false
async function openMaterialCheck(forGeneration = false) {
  materialCheckBusy.value = true
  error.value = ''
  try {
    materialCheckReport.value = await checkMaterials(form)
    materialCheckForGeneration = forGeneration
    materialCheckOpen.value = true
  } catch (e) { error.value = e instanceof Error ? e.message : '资料检查失败' }
  finally { materialCheckBusy.value = false }
}
async function acceptMaterialCheck() {
  materialCheckOpen.value = false
  if (materialCheckForGeneration) {
    materialApproved = true
    await doGenerate()
  }
}

'''
s = s.replace('async function doGenerate() {', code + 'async function doGenerate() {', 1)
marker = '''  generating.value = true
  try {
    syncFullDrafts()'''
replacement = '''  if (!materialApproved && Object.values(form.materialSelections || {}).some(c => c.enabled)) {
    if (!materialCheckBusy.value) await openMaterialCheck(true)
    return
  }
  materialApproved = false
  generating.value = true
  try {
    syncFullDrafts()'''
assert marker in s
s = s.replace(marker, replacement, 1)
marker = '    <section v-else-if="currentStepIndex === 1" class="tender-step-panel space-y-4">'
assert marker in s
s = s.replace(marker, marker + '''
      <MaterialArchivePanel
        v-model="form.materialSelections"
        :invitation-id="form.invitationId"
        :sections="form.outlineItems.filter(i => !i.skipped).map(i => i.title)"
        :disabled="generating || workflowLocked || materialCheckBusy"
        @check="openMaterialCheck(false)"
      />''', 1)
marker = '  <AppAlertDialog\n    :open="confirmDateOpen"'
assert marker in s
dialog = '''  <AppAlertDialog
    :open="materialCheckOpen" title="生成前资料与缺件核对" :destructive="false"
    :confirm-label="materialCheckForGeneration ? '已核对，继续生成' : '完成核对'"
    @update:open="materialCheckOpen = $event" @confirm="acceptMaterialCheck"
  >
    <div v-if="materialCheckReport" class="space-y-3 max-h-[55vh] overflow-auto text-sm">
      <p>{{ materialCheckReport.note }}</p>
      <p v-if="!materialCheckReport.selected.length">未开启或未选用新资料，组卷保持原行为。</p>
      <ul class="space-y-2"><li v-for="item in materialCheckReport.selected" :key="item.id">
        {{ item.filename }} → {{ item.mode === 'scan' ? item.targetSection || '未指定章节' : '技术起草参考' }}
      </li></ul>
      <ul class="space-y-2 text-amber-700"><li v-for="message in materialCheckReport.warnings" :key="message">{{ message }}</li></ul>
      <p v-if="materialCheckReport.missing.length" class="font-medium">结构化资料缺件提示（仅供核对，不阻止生成）</p>
      <ul class="space-y-2"><li v-for="item in materialCheckReport.missing" :key="item.category + item.entity">{{ item.entity }}：缺 {{ item.missing.join('、') }}</li></ul>
    </div>
  </AppAlertDialog>

'''
s = s.replace(marker, dialog + marker, 1)
p.write_text(s, encoding='utf-8')
p = root / 'TenderLibraryView.vue'
s = p.read_text(encoding='utf-8')
s = s.replace("import TenderScanGallery from '@/components/tenders/TenderScanGallery.vue'", "import TenderScanGallery from '@/components/tenders/TenderScanGallery.vue'\nimport MaterialArchivePanel from '@/components/tenders/MaterialArchivePanel.vue'", 1)
s = s.replace('    <Panel title="公司常备扫描件"', '    <MaterialArchivePanel library-only />\n    <Panel title="公司常备扫描件"', 1)
p.write_text(s, encoding='utf-8')

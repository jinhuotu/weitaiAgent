const fs = require('node:fs')
const vm = require('node:vm')
const assert = require('node:assert/strict')
const { createRequire } = require('node:module')
const path = require('node:path')
const req = createRequire('E:/code/python/weitaiAgentui/package.json')
const ts = req('typescript')
const vue = req('vue')
const filename = 'E:/code/python/weitaiAgentui/src/views/TendersView.vue'
const source = fs.readFileSync(filename, 'utf8')
const mode = source.slice(source.indexOf('const followAdvice = ref(false)'), source.indexOf('const parsing = ref(false)'))
const submit = source.slice(source.indexOf('async function doGenerate()'), source.indexOf('const qaVolume = computed'))
const test = `
const form = reactive({ volumeAdvice: 'split', structureMode: 'fixed', outlineItems: [], autoDraftBizResp: false, autoDraftTechPlan: false })
const error = ref(''), workflowLocked = ref(false), generating = ref(false), pendingBoth = ref(false)
const activeVolume = ref('business'), previewVolume = ref('business'), result = ref(null)
const selectedKbIds = ref(['kb']), effectiveRequiredKeys = ref([]), effectiveIncludeKeys = ref([]), attachmentMatch = ref(null)
const captured = []
const withDrawingNote = x => x, pruneUnknownOutline = x => x, loadRecords = () => {}, advanceStep = () => {}
const generateTender = async payload => { captured.push(payload); return { id: 'record', generateVolume: payload.generateVolume } }
const regenerateTenderRecord = async (id, payload) => generateTender(payload)
${mode}
${submit}
(async () => {
  selectGenerationMode({ target: { value: 'full' } })
  assert.equal(form.autoDraftBizResp, true)
  assert.equal(form.autoDraftTechPlan, true)
  await doGenerate()
  assert.equal(captured.at(-1).generateVolume, 'full')
  assert.equal(captured.at(-1).generateBoth, false)
  assert.equal(captured.at(-1).autoDraftBizResp, true)
  assert.equal(captured.at(-1).autoDraftTechPlan, true)
  draftPreference.business = false
  draftPreference.technical = false
  form.autoDraftBizResp = false
  form.autoDraftTechPlan = false
  selectGenerationMode({ target: { value: 'single' } })
  selectGenerationMode({ target: { value: 'full' } })
  await doGenerate()
  assert.equal(captured.at(-1).autoDraftBizResp, false)
  assert.equal(captured.at(-1).autoDraftTechPlan, false)
  form.volumeAdvice = 'full'
  selectGenerationMode({ target: { value: 'split' } })
  await doGenerate()
  assert.equal(captured.at(-1).generateBoth, true)
  assert.equal(captured.at(-1).splitSlice, true)
  selectGenerationMode({ target: { value: 'single' } })
  activeVolume.value = 'technical'
  await doGenerate()
  assert.equal(captured.at(-1).generateVolume, 'technical')
  assert.equal(captured.at(-1).generateBoth, false)
  assert.equal(captured.at(-1).splitSlice, false)
  console.log('Frontend interaction checks passed: full defaults, manual opt-out, explicit split override, technical single volume.')
})()
`
const js = ts.transpileModule(test, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
vm.runInNewContext(js, { ...vue, assert, console })

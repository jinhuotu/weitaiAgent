const fs = require('node:fs')
const vm = require('node:vm')
const assert = require('node:assert/strict')
const { createRequire } = require('node:module')
const req = createRequire('E:/code/python/weitaiAgentui/package.json')
const ts = req('typescript')
const vue = req('vue')
const sfc = req('@vue/compiler-sfc')
const view = fs.readFileSync('E:/code/python/weitaiAgentui/src/views/TendersView.vue', 'utf8')
const editor = fs.readFileSync('E:/code/python/weitaiAgentui/src/components/tenders/TenderDocEditor.vue', 'utf8')
const api = fs.readFileSync('E:/code/python/weitaiAgentui/src/lib/tenders-api.ts', 'utf8')
const compile = source => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const pick = api.slice(api.indexOf('export function volumeDocx('), api.indexOf('export type ApprovalStepDef')).replace('export function', 'function')
const flush = view.slice(view.indexOf('async function flushBidEdit()'), view.indexOf('async function switchPreviewVolume('))
const download = view.slice(view.indexOf('async function onDownload('), view.indexOf('</script>'))
const editorFlush = editor.slice(editor.indexOf('function sleep(ms:'), editor.indexOf('defineExpose({ flushSave })'))

function savedEditor({ saveError, timeout = false, emptyKey = false, edited = true } = {}) {
  let time = 0
  let key = emptyKey ? '' : 'v1'
  const commands = []
  const context = vm.createContext({
    ...vue, Error,
    Date: { now: () => time },
    window: { setTimeout: (callback, ms) => { time += ms; callback() } },
    fetchTenderEditorConfig: async () => ({ config: { document: { key } } }),
    forceSaveTenderDocx: async (file, docKey) => {
      commands.push([file, docKey])
      if (saveError) throw new Error(saveError)
      if (!timeout) key = 'v2'
    },
  })
  const functions = vm.runInContext(compile(`
    const props = { docxFile: 'full.docx', downloadName: '完整投标文件.docx' }
    const activeEngine = ref('onlyoffice'), dirty = ref(false)
    const docEditor = {}
    let hasEdits = ${edited}, openedDocKey = 'v1'
    ${editorFlush}
  `) + '\n({flushSave})', context)
  return { ...functions, commands }
}

function page(row, save = async () => {}, requestError) {
  const received = []
  const context = vm.createContext({
    ...vue, Error, ApiError: class extends Error {},
    initial: row, save,
    downloadTenderFile: async (file, name) => {
      if (requestError) throw new Error(requestError)
      received.push([file, name])
    },
  })
  return { ...vm.runInContext(compile(`
    const result = ref(initial), error = ref(''), currentStepIndex = ref(2)
    const bidSaveBusy = ref(false), downloading = ref(null)
    const bidEditorRef = ref({ flushSave: save })
    ${pick}
    ${flush}
    ${download}
  `) + '\n({onDownload, flushBidEdit, error, bidSaveBusy, downloading})', context), received }
}

async function main() {
  const row = { generateVolume: 'full', docxFile: 'full.docx', downloadName: '项目-投标文件.docx' }
  const saver = savedEditor()
  const full = page(row, saver.flushSave)
  await full.onDownload('docx')
  assert.deepEqual(saver.commands, [['full.docx', 'v1']])
  assert.deepEqual(full.received, [['full.docx', '项目-投标文件.docx']])
  assert.equal(full.downloading.value, null)
  assert.equal(full.error.value, '')

  const pending = page(row)
  pending.bidSaveBusy.value = true
  await pending.onDownload('docx')
  assert.equal(pending.error.value, '正在保存，请稍候')
  assert.equal(pending.received.length, 0)

  let release
  const slow = page(row, () => new Promise(resolve => { release = resolve }))
  const request = slow.onDownload('docx')
  assert.equal(slow.downloading.value, 'docx')
  assert.equal(slow.bidSaveBusy.value, true)
  assert.equal(slow.received.length, 0)
  await slow.onDownload('tech')
  assert.equal(slow.error.value, '正在保存，请稍候')
  release()
  await request
  assert.equal(slow.received.length, 1)
  assert.equal(slow.error.value, '')

  for (const scenario of [
    { saveError: 'OnlyOffice 拒绝保存请求，请检查服务与 JWT', expected: 'JWT' },
    { timeout: true, expected: '修改还没写回' },
    { emptyKey: true, expected: '无法确认文稿保存版本' },
  ]) {
    const saver = savedEditor(scenario)
    const p = page(row, saver.flushSave)
    await p.onDownload('docx')
    assert.ok(p.error.value.includes(scenario.expected))
    assert.equal(p.received.length, 0)
    assert.equal(p.downloading.value, null)
    assert.equal(p.bidSaveBusy.value, false)
  }

  const noEdits = savedEditor({ edited: false })
  const untouched = page(row, noEdits.flushSave)
  await untouched.onDownload('docx')
  assert.equal(noEdits.commands.length, 0)
  assert.equal(untouched.received.length, 1)
  const pair = page({ ...row, generateVolume: 'business', techDocxFile: 'technical.docx', techDownloadName: '技术标.docx' })
  await pair.onDownload('docx')
  await pair.onDownload('tech')
  assert.deepEqual(pair.received, [['full.docx', '项目-投标文件.docx'], ['technical.docx', '技术标.docx']])
  const fallbackName = page({ ...row, downloadName: '', projectName: '测试项目' })
  await fallbackName.onDownload('docx')
  assert.deepEqual(fallbackName.received, [['full.docx', '测试项目-综合标.docx']])
  for (const bad of [null, { ...row, docxFile: '' }]) {
    const p = page(bad)
    await p.onDownload('docx')
    assert.ok(p.error.value)
    assert.equal(p.received.length, 0)
  }
  const failedRequest = page(row, async () => {}, '下载接口请求失败')
  await failedRequest.onDownload('docx')
  assert.equal(failedRequest.error.value, '下载接口请求失败')

  for (const [filename, source] of [['TendersView.vue', view], ['TenderDocEditor.vue', editor]]) {
    const parsed = sfc.parse(source, { filename })
    assert.deepEqual(parsed.errors, [])
    assert.deepEqual(sfc.compileTemplate({ source: parsed.descriptor.template.content, filename, id: filename }).errors, [])
  }
  assert.ok(view.includes('v-if="result.generateVolume === \'full\' && result.docxFile"'))
  assert.ok(view.includes('v-if="result.generateVolume !== \'full\' && result.docxFile"'))
  assert.ok(view.includes('下载综合标'))
  console.log('Download checks passed: full/pair/single mapping, force-save-before-download, busy/spinner, callback timeout, missing metadata, and request failures. Both Vue templates compile.')
}
main().catch(error => { console.error(error); process.exitCode = 1 })

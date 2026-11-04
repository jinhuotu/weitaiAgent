from pathlib import Path
import difflib

root = Path(__file__).resolve().parent
view = Path('E:/code/python/weitaiAgentui/src/views/TendersView.vue')
editor = Path('E:/code/python/weitaiAgentui/src/components/tenders/TenderDocEditor.vue')

def replace(text, old, new):
    assert text.count(old) == 1, old[:100]
    return text.replace(old, new, 1)

before = view.read_text(encoding='utf-8')
after = replace(before, '''  if (!bidEditorRef.value) return true
  if (bidSaveBusy.value) return false''', '''  if (bidSaveBusy.value) {
    error.value = '正在保存，请稍候'
    return false
  }
  if (!bidEditorRef.value) return true''')
start = after.index("async function onDownload(kind: 'docx' | 'tech' | 'pdf')")
end = after.index('\n</script>', start)
after = after[:start] + '''async function onDownload(kind: 'docx' | 'tech' | 'pdf') {
  const r = result.value
  if (!r) {
    error.value = '请先生成投标文件后再下载'
    return
  }
  if (downloading.value) {
    error.value = bidSaveBusy.value ? '正在保存，请稍候' : '正在下载，请稍候'
    return
  }
  downloading.value = kind
  error.value = ''
  try {
    if (currentStepIndex.value === 2 && !(await flushBidEdit())) return
    const picked =
      kind === 'tech'
        ? volumeDocx(r, 'technical')
        : kind === 'docx'
          ? volumeDocx(r, 'business')
          : { file: r.pdfFile || '', name: r.pdfDownloadName || '' }
    if (kind === 'docx' && r.generateVolume === 'full' && !r.downloadName?.trim()) {
      picked.name = `${r.projectName || 'bid'}-综合标.docx`
    }
    if (!picked.file.trim() || !picked.name.trim()) {
      error.value = '下载文件或文件名缺失，请重新生成投标文件后再试'
      return
    }
    error.value = ''
    await downloadTenderFile(picked.file, picked.name)
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '下载失败'
  } finally {
    downloading.value = null
  }
}
''' + after[end:]
button = '''            <button
              v-if="result.docxFile"
              type="button"
              class="tender-ghost-btn"
              :disabled="downloading === 'docx'"
              @click="onDownload('docx')"
            >
              <Loader2 v-if="downloading === 'docx'" class="size-3.5 animate-spin" />
              <Download v-else class="size-3.5" />
              商务标
            </button>'''
full_button = button.replace('v-if="result.docxFile"', 'v-if="result.generateVolume === \'full\' && result.docxFile"').replace('              商务标\n', '              下载综合标\n')
business_button = button.replace('v-if="result.docxFile"', 'v-if="result.generateVolume !== \'full\' && result.docxFile"')
after = replace(after, button, full_button + '\n' + business_button)
after = replace(after, '''        <div
          v-if="generateShownWarnings.length && !previewFullscreen"''', '''        <p
          v-if="bidSaveBusy || error"
          class="shrink-0 rounded-md border border-sulfur/30 bg-sulfur/10 px-3 py-2 text-[12px] text-sulfur"
          :role="bidSaveBusy ? 'status' : 'alert'"
        >
          {{ bidSaveBusy ? '正在保存，请稍候' : error }}
        </p>

        <div
          v-if="generateShownWarnings.length && !previewFullscreen"''')

editor_before = editor.read_text(encoding='utf-8')
editor_after = replace(editor_before, "  return String(doc.document?.key || '')", '''  const key = String(doc.document?.key || '')
  if (!key) throw new Error('无法确认文稿保存版本，请稍后重试')
  return key''')
editor_after = replace(editor_after, "  throw new Error('修改还没写回，请再试一次')", "  throw new Error('修改还没写回，等待 OnlyOffice 保存回调超时。请检查文档服务与保存回调后重试，勿关闭当前预览。')")

patch = []
for path, old, new in ((view, before, after), (editor, editor_before, editor_after)):
    relative = path.relative_to(Path('E:/code/python/weitaiAgentui')).as_posix()
    snapshot = root / ('before-' + path.name)
    snapshot.write_text(old, encoding='utf-8', newline='\n')
    patch.extend(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                    fromfile='a/' + relative, tofile='b/' + relative))
    path.write_text(new, encoding='utf-8', newline='\n')
(root / 'full-download.patch').write_text(''.join(patch), encoding='utf-8', newline='\n')
print('Updated full-volume download button, save/download feedback and missing-file guards; OnlyOffice field names unchanged.')

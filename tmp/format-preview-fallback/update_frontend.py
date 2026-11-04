from pathlib import Path
import difflib
import sys

root = Path('E:/code/python/weitaiAgentui/src')
stage = Path('E:/code/python/weitaiAgent/tmp/format-preview-fallback')
api_path = root / 'lib/tenders-api.ts'
view_path = root / 'views/TendersView.vue'
api_before = api_path.read_text(encoding='utf-8')
view_before = view_path.read_text(encoding='utf-8')
api = api_before
view = view_before

def api_replace(old, new):
    global api
    assert api.count(old) == 1, old[:100]
    api = api.replace(old, new)

def replace(old, new, count=1):
    global view
    assert view.count(old) == count, (view.count(old), old[:100])
    view = view.replace(old, new)

api_replace('''export async function prepareInvitationFormatDocx(invitationId: string): Promise<string> {
  const data = await apiRequest<{ fileName?: string }>(
    `/api/v1/tenders/invitations/${encodeURIComponent(invitationId)}/format-docx`,
    { token: token() },
  )
  return String(data.fileName || '').trim()
}''', '''export type InvitationFormatPreview = {
  fileName: string
  source: 'invitation' | 'builtin' | 'requirements'
  previewOnly: boolean
  message: string
}

export function prepareInvitationFormatDocx(invitationId: string): Promise<string>
export function prepareInvitationFormatDocx(invitationId: string, brief: BidBrief): Promise<InvitationFormatPreview>
export async function prepareInvitationFormatDocx(
  invitationId: string, brief?: BidBrief,
): Promise<string | InvitationFormatPreview> {
  const data = await apiRequest<Partial<InvitationFormatPreview>>(
    `/api/v1/tenders/invitations/${encodeURIComponent(invitationId)}/format-docx`,
    { method: brief ? 'POST' : 'GET', body: brief, token: token() },
  )
  const preview: InvitationFormatPreview = {
    fileName: String(data.fileName || '').trim(),
    source: data.source || 'invitation',
    previewOnly: Boolean(data.previewOnly),
    message: String(data.message || ''),
  }
  return brief ? preview : preview.fileName
}''')
replace("const formatPreviewFile = ref('')\n",
        "const formatPreviewFile = ref('')\nconst formatPreviewOnly = ref(false)\nconst formatPreviewMessage = ref('')\n")
replace('async function confirmFormatTemplate() {\n',
        'async function confirmFormatTemplate() {\n  if (formatPreviewOnly.value) {\n'
        '    formatPreviewOpen.value = false\n    return\n  }\n')
# Only the dialog close path has this preceding block.
replace('''    formatPreviewOpen.value = true
    return
  }
  if (formatConfirmBusy.value) return''', '''    formatPreviewOpen.value = true
    return
  }
  if (formatPreviewOnly.value) {
    formatPreviewOpen.value = false
    formatSaveError.value = ''
    return
  }
  if (formatConfirmBusy.value) return''')
replace("  formatPreviewFile.value = ''\n  formatPreviewOpen.value = false\n}",
        "  formatPreviewFile.value = ''\n  formatPreviewOnly.value = false\n  formatPreviewMessage.value = ''\n  formatPreviewOpen.value = false\n}")
replace('''    const fileName = await prepareInvitationFormatDocx(invitationId)
    if (!fileName) throw new Error('未生成预览文件')
    formatPreviewFile.value = fileName''', '''    const preview = await prepareInvitationFormatDocx(invitationId, {
      ...form,
      layoutMode: form.requirementsDriven ? 'requirements' : (form.structureMode === 'outline' ? 'outline' : 'chapter5'),
      generateVolume: effectiveGenerationMode.value === 'full' ? 'full' : activeVolume.value,
    })
    if (!preview.fileName) throw new Error('未生成预览文件')
    formatPreviewFile.value = preview.fileName
    formatPreviewOnly.value = preview.previewOnly
    formatPreviewMessage.value = preview.message
    if (preview.previewOnly) {
      form.requirementsDriven = preview.source === 'requirements'
      form.structureMode = preview.source === 'requirements' ? 'outline' : 'fixed'
      form.layoutMode = preview.source === 'requirements' ? 'requirements' : 'chapter5'
    }''')
replace("    formatPreviewFile.value = ''\n    formatPreviewOpen.value = false\n    ensureDocumentFormat()",
        "    formatPreviewFile.value = ''\n    formatPreviewOnly.value = false\n    formatPreviewMessage.value = ''\n    formatPreviewOpen.value = false\n    ensureDocumentFormat()")
button = '''            <button
              type="button"
              class="h-7 px-2.5 rounded-md border border-border text-[11px] hover:bg-accent disabled:opacity-50"
              :disabled="formatPreviewBusy || !form.invitationId"
              @click="loadFormatFile(true)"
            >
              {{ formatPreviewBusy ? '打开中…' : '预览模板' }}
            </button>'''
replace('''          <p v-if="form.requirementsDriven" class="text-muted-foreground leading-relaxed">
            已按全文投标要求综合大纲，自动生成封面、目录和完整正文结构。缺少的资料及响应内容会标为【待补充】，请在生成后核对。
          </p>''', '''          <div v-if="form.requirementsDriven" class="space-y-2">
            <p class="text-muted-foreground leading-relaxed">
              已按全文投标要求综合大纲，自动生成封面、目录和完整正文结构。缺少的资料及响应内容会标为【待补充】，请在生成后核对。
            </p>
''' + button + '\n          </div>')
replace('''            <label>接标模板</label>''', '''            <label>接标模板</label>
''' + button)
replace(''':description="`从上传的 Word 里截出的「${form.outlineChapter || '投标文件格式'}」。可直接删改，关闭或确认时写回。生成用这份。`"''',
        ''':description="formatPreviewMessage || `从上传的 Word 里截出的「${form.outlineChapter || '投标文件格式'}」。可直接删改，关闭或确认时写回。生成用这份。`"''')
replace('''        download-name="招标文件格式章.docx"
        mode="edit"
        engine="onlyoffice"''', '''        :download-name="formatPreviewOnly ? '模板骨架预览.docx' : '招标文件格式章.docx'"
        :mode="formatPreviewOnly ? 'view' : 'edit'"
        :engine="formatPreviewOnly ? 'browser' : 'onlyoffice'"''')
replace('''      <button
        type="button"
        class="h-8 px-3 text-xs rounded-md bg-foreground text-background hover:opacity-90 disabled:opacity-50"
        :disabled="!formatPreviewFile || form.formatConfirmed || formatConfirmBusy"
        @click="confirmFormatTemplate"''', '''      <button
        v-if="!formatPreviewOnly"
        type="button"
        class="h-8 px-3 text-xs rounded-md bg-foreground text-background hover:opacity-90 disabled:opacity-50"
        :disabled="!formatPreviewFile || form.formatConfirmed || formatConfirmBusy"
        @click="confirmFormatTemplate"''')
patch = ''
for path, before, after in ((api_path, api_before, api), (view_path, view_before, view)):
    patch += ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                         fromfile=str(path), tofile=str(path)))
    (stage / (path.name + '.before')).write_text(before, encoding='utf-8')
    (stage / (path.name + '.staged')).write_text(after, encoding='utf-8')
    if '--apply' in sys.argv:
        path.write_text(after, encoding='utf-8', newline='')
(stage / 'frontend.patch').write_text(patch, encoding='utf-8')
print('Fallback preview patch ' + ('applied' if '--apply' in sys.argv else 'staged'))

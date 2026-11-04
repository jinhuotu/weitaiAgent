from pathlib import Path
import difflib

path = Path('E:/code/python/weitaiAgentui/src/views/TendersView.vue')
out = Path('E:/code/python/weitaiAgent/tmp/native-format')
before = path.read_text(encoding='utf-8')
after = before
needle = 'function mergeBriefIntoForm(brief: Partial<BidBrief>) {'
helper = '''/** 原版格式章优先，旧记录的结构来源标记不能覆盖已识别的范本。 */
function hasOriginalFormat(brief: Partial<BidBrief> = form): boolean {
  if (!brief.invitationId) return false
  const chapter = String(brief.outlineChapter || '').replace(/\\s+/g, '')
  const named = /^(?:第[一二三四五六七八九十百0-9]+(?:部分|章节|章|节)[、.．]?)?(?:投标|响应)文件(?:格式|组成)$/.test(chapter)
  return named || brief.layoutMode === 'outline' || Boolean(brief.formatConfirmed && !brief.requirementsDriven)
}

function generationLayoutMode(): string {
  if (hasOriginalFormat()) return 'outline'
  return form.requirementsDriven ? 'requirements' : (form.structureMode === 'outline' ? 'outline' : 'chapter5')
}

'''
assert needle in after
after = after.replace(needle, helper + needle, 1)
needle = "  if (brief.requirementsDriven) next.structureMode = 'outline'"
assert needle in after
after = after.replace(needle, """  if (hasOriginalFormat(next as Partial<BidBrief>)) {
    next.layoutMode = 'outline'
    next.structureMode = 'outline'
    next.requirementsDriven = false
  } else if (brief.requirementsDriven) next.structureMode = 'outline'""", 1)
needle = "layoutMode: form.requirementsDriven ? 'requirements' : (form.structureMode === 'outline' ? 'outline' : 'chapter5'),"
assert after.count(needle) == 2
after = after.replace(needle, 'layoutMode: generationLayoutMode(),')
needle = '    if (preview.previewOnly) {'
assert needle in after
after = after.replace(needle, """    if (preview.source === 'invitation') {
      form.requirementsDriven = false
      form.structureMode = 'outline'
      form.layoutMode = 'outline'
    } else if (preview.previewOnly) {""", 1)
needle = "function setStructureMode(mode: StructureMode) {\n  form.structureMode = mode"
assert needle in after
after = after.replace(needle, """function setStructureMode(mode: StructureMode) {
  if (hasOriginalFormat()) mode = 'outline'
  form.structureMode = mode""", 1)
needle = '                :checked="form.structureMode === \'fixed\'"'
assert needle in after
after = after.replace(needle, needle + '\n                :disabled="hasOriginalFormat()"', 1)
needle = '          <div v-if="form.requirementsDriven" class="space-y-2">'
assert needle in after
after = after.replace(needle, '''          <p v-if="hasOriginalFormat()" class="text-muted-foreground">
            优先沿用原版封面和章节格式，仅用内置格式补齐缺少的章节。
          </p>
''' + needle, 1)
# Keep requirement flags consistent in generation payloads, even for stale records.
needle = '      structureMode: form.structureMode,\n      layoutMode: generationLayoutMode(),'
assert needle in after
after = after.replace(needle, """      structureMode: hasOriginalFormat() ? 'outline' : form.structureMode,
      requirementsDriven: hasOriginalFormat() ? false : form.requirementsDriven,
      layoutMode: generationLayoutMode(),""", 1)
out.joinpath('TendersView.vue.before').write_text(before, encoding='utf-8')
out.joinpath('frontend.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=str(path), tofile=str(path))), encoding='utf-8')
path.write_text(after, encoding='utf-8', newline='')
print('Updated TendersView.vue; review diff saved in tmp/native-format/frontend.patch')

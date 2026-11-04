from pathlib import Path
import difflib
import sys

root = Path('E:/code/python/weitaiAgentui')
stage = Path('E:/code/python/weitaiAgent/tmp/requirement-bid')
api_path = root / 'src/lib/tenders-api.ts'
view_path = root / 'src/views/TendersView.vue'
api_before = api_path.read_text(encoding='utf-8')
view_before = view_path.read_text(encoding='utf-8')
api = api_before.replace('  layoutMode: string\n',
                         '  layoutMode: string\n  requirementsDriven?: boolean\n', 1)
view = view_before

def replace(old, new, expected=1):
    global view
    count = view.count(old)
    if count != expected:
        raise RuntimeError(f'Expected {expected} occurrences, got {count}: {old[:65]}')
    view = view.replace(old, new)

replace('  Object.assign(form, next)\n',
        "  if (brief.requirementsDriven) next.structureMode = 'outline'\n"
        '  Object.assign(form, next)\n')
replace("  if (form.structureMode === 'outline') {\n    parts.push(form.formatConfirmed",
        "  if (form.requirementsDriven) {\n    parts.push('按招标要求综合组卷')\n"
        "  } else if (form.structureMode === 'outline') {\n    parts.push(form.formatConfirmed")
replace("form.structureMode === 'outline' && !form.formatConfirmed",
        "form.structureMode === 'outline' && !form.requirementsDriven && !form.formatConfirmed", 2)
replace("      layoutMode: form.structureMode === 'outline' ? 'outline' : 'chapter5',",
        "      layoutMode: form.requirementsDriven ? 'requirements' : (form.structureMode === 'outline' ? 'outline' : 'chapter5'),")
replace('          <div class="flex flex-wrap gap-3">\n            <label class="inline-flex items-center gap-1.5">',
        '          <div v-if="!form.requirementsDriven" class="flex flex-wrap gap-3">\n            <label class="inline-flex items-center gap-1.5">')
replace('          <div\n            v-if="form.structureMode === \'fixed\'"\n            class="flex flex-wrap items-center gap-2 text-muted-foreground"',
        '          <p v-if="form.requirementsDriven" class="text-muted-foreground leading-relaxed">\n'
        '            已按全文投标要求综合大纲，自动生成封面、目录和完整正文结构。缺少的资料及响应内容会标为【待补充】，请在生成后核对。\n'
        '          </p>\n'
        '          <div\n            v-else-if="form.structureMode === \'fixed\'"\n            class="flex flex-wrap items-center gap-2 text-muted-foreground"')
if api == api_before:
    raise RuntimeError('Type field was not added')
patch = ''
for path, before, after in ((api_path, api_before, api), (view_path, view_before, view)):
    patch += ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                         fromfile=str(path), tofile=str(path)))
    (stage / (path.name + '.before')).write_text(before, encoding='utf-8')
    (stage / (path.name + '.staged')).write_text(after, encoding='utf-8')
    if '--apply' in sys.argv:
        path.write_text(after, encoding='utf-8', newline='')
(stage / 'frontend.patch').write_text(patch, encoding='utf-8')
print('Frontend requirement-mode patch ' + ('applied' if '--apply' in sys.argv else 'staged'))

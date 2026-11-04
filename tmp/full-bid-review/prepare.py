from pathlib import Path
import difflib
import shutil

ROOT = Path(__file__).resolve().parent
BACK = ROOT.parents[1]
UI = BACK.parent / 'weitaiAgentui'

def read(path):
    return path.read_text(encoding='utf-8-sig')

def replace(text, old, new, count=1):
    assert old in text, old[:120]
    return text.replace(old, new, count)

changes = {}
def add(project, name, edit):
    source = (BACK if project == 'backend' else UI) / name
    before = read(source)
    after = edit(before)
    changes[(project, name)] = (before, after)

def view(s):
    s = replace(s, "const followAdvice = ref(false)", """const followAdvice = ref(false)
type GenerationMode = 'full' | 'split' | 'single'
const generationMode = ref<GenerationMode | null>(null)
const effectiveGenerationMode = computed<GenerationMode>(() => {
  if (generationMode.value) return generationMode.value
  if (followAdvice.value && form.volumeAdvice === 'full') return 'full'
  if (followAdvice.value && form.volumeAdvice === 'split') return 'split'
  return 'single'
})
const draftPreference = reactive<{ business?: boolean; technical?: boolean }>({})

function syncFullDrafts() {
  if (effectiveGenerationMode.value !== 'full') return
  form.autoDraftBizResp = draftPreference.business ?? true
  form.autoDraftTechPlan = draftPreference.technical ?? true
}

function selectGenerationMode(event: Event) {
  generationMode.value = (event.target as HTMLSelectElement).value as GenerationMode
  followAdvice.value = false
  syncFullDrafts()
}""")
    s = replace(s, "  if (followAdvice.value && form.volumeAdvice === 'full') {\n    return result.value?.docxFile ? '覆盖生成投标文件' : '生成投标文件并预览'", "  if (effectiveGenerationMode.value === 'full') {\n    return result.value?.docxFile ? '覆盖生成完整投标文件（综合标）' : '生成完整投标文件（综合标）'")
    s = replace(s, "  if (followAdvice.value && form.volumeAdvice === 'split') return '按分册生成两卷'", "  if (effectiveGenerationMode.value === 'split') return '按分册生成两卷'")
    s = replace(s, "const volumeStatusHint = computed(() => {", """const volumeStatusHint = computed(() => {
  if (effectiveGenerationMode.value === 'full') return '将生成一本综合标，包含商务响应和技术方案，使用连续目录。'
  if (effectiveGenerationMode.value === 'split') return '将分别生成商务标、技术标两本 Word。'""")
    s = replace(s, "  followAdvice.value = false\n  activeVolume.value = volume", """  // 顶部卷别切换用于编辑；输出方式由生成模式控制。
  if (effectiveGenerationMode.value === 'single') {
    generationMode.value = 'single'
    followAdvice.value = false
  }
  activeVolume.value = volume""")
    s = replace(s, "    applyBriefToForm(detail.brief)", """    applyBriefToForm(detail.brief)
    generationMode.value = detail.brief.generateBoth ? 'split' : detail.brief.generateVolume === 'full' ? 'full' : 'single'
    activeVolume.value = detail.brief.generateVolume === 'technical' ? 'technical' : 'business'
    draftPreference.business = form.autoDraftBizResp
    draftPreference.technical = form.autoDraftTechPlan""")
    s = replace(s, "    followAdvice.value = true\n    volumeHint.value = ''", "    followAdvice.value = generationMode.value === null\n    syncFullDrafts()\n    volumeHint.value = ''")
    s = replace(s, "  if (activeVolume.value !== 'technical' || !note) return items", "  if ((effectiveGenerationMode.value === 'single' && activeVolume.value !== 'technical') || !note) return items")
    s = replace(s, "  generating.value = true\n  try {\n    const payload = {", "  generating.value = true\n  try {\n    syncFullDrafts()\n    const payload: BidBrief = {")
    s = replace(s, "      generateVolume: activeVolume.value as string,\n      generateBoth: pendingBoth.value,", """      generateVolume: effectiveGenerationMode.value === 'full' ? 'full' : activeVolume.value,
      generateBoth: effectiveGenerationMode.value === 'split' || pendingBoth.value,
      splitSlice: effectiveGenerationMode.value === 'split' || pendingBoth.value,""")
    s = replace(s, "    if (followAdvice.value && !payload.generateBoth) {\n      if (form.volumeAdvice === 'full') payload.generateVolume = 'full'\n      else if (form.volumeAdvice === 'split') payload.generateBoth = true\n    }\n", '')
    s = replace(s, "    previewVolume.value = activeVolume.value", "    previewVolume.value = payload.generateVolume === 'technical' ? 'technical' : 'business'")
    s = replace(s, "async function loadSavedQa(recordId: string) {", """const qaVolume = computed(() => result.value?.generateVolume === 'full' ? 'full' : previewVolume.value)

async function loadSavedQa(recordId: string) {""")
    s = s.replace("{ volume: previewVolume.value }", "{ volume: qaVolume.value }")
    s = replace(s, "      previewVolume.value,\n    ] as const", "      qaVolume.value,\n    ] as const")
    s = replace(s, "  pendingBoth.value = true\n  await onFormStepNext()", "  generationMode.value = 'split'\n  followAdvice.value = false\n  pendingBoth.value = true\n  await onFormStepNext()")
    s = replace(s, "  hasParsed.value = false\n  volumeHint.value = ''", "  hasParsed.value = false\n  generationMode.value = null\n  followAdvice.value = false\n  delete draftPreference.business\n  delete draftPreference.technical\n  volumeHint.value = ''")
    s = s.replace('      currentStep: item.currentStep,', '      currentStep: item.currentStep,\n      generateVolume: item.generateVolume,')
    s = s.replace('      currentStep: detail.currentStep,', '      currentStep: detail.currentStep,\n      generateVolume: detail.generateVolume,')
    s = s.replace("activeVolume.value === 'technical' && item.techDocxFile", "item.generateVolume !== 'full' && activeVolume.value === 'technical' && item.techDocxFile")
    s = s.replace("activeVolume.value === 'technical' && detail.techDocxFile", "detail.generateVolume !== 'full' && activeVolume.value === 'technical' && detail.techDocxFile")
    s = s.replace('v-if="result.docxFile && result.techDocxFile"', 'v-if="result.generateVolume !== \'full\' && result.docxFile && result.techDocxFile"')
    s = replace(s, '    <template #badges>\n', '''    <template #badges>
      <label class="flex items-center gap-2 text-[12px]">
        生成模式
        <select class="kb-input font-sans" :value="effectiveGenerationMode" :disabled="generating || workflowLocked" @change="selectGenerationMode">
          <option value="full">生成完整投标文件（综合标）</option>
          <option value="split">分册两本（商务标 + 技术标）</option>
          <option value="single">单卷（当前商务标 / 技术标）</option>
        </select>
      </label>
''')
    s = replace(s, '<input v-model="form.autoDraftBizResp" type="checkbox" />生成商务标时起草商务响应', '<input v-model="form.autoDraftBizResp" type="checkbox" @change="draftPreference.business = form.autoDraftBizResp" />生成商务标或综合标时起草商务响应')
    s = replace(s, '<input v-model="form.autoDraftTechPlan" type="checkbox" />生成技术标时为现有空章节起草正文', '<input v-model="form.autoDraftTechPlan" type="checkbox" @change="draftPreference.technical = form.autoDraftTechPlan" />生成技术标或综合标时为现有空章节起草正文')
    s = replace(s, "          followAdvice && form.volumeAdvice === 'full'", "          effectiveGenerationMode === 'full'")
    s = replace(s, ": followAdvice && form.volumeAdvice === 'split'", ": effectiveGenerationMode === 'split'")
    s = replace(s, ':title="previewVolume === \'technical\' ? \'AI 自检 · 技术标\' : \'AI 自检 · 商务标\'"', ':title="qaVolume === \'full\' ? \'AI 自检 · 完整投标文件（综合标）\' : previewVolume === \'technical\' ? \'AI 自检 · 技术标\' : \'AI 自检 · 商务标\'"')
    return s

add('frontend', 'src/views/TendersView.vue', view)
add('frontend', 'src/lib/tenders-api.ts', lambda s: replace(s, '  generateBoth?: boolean', '  generateBoth?: boolean\n  splitSlice?: boolean').replace("opts?: { volume?: 'business' | 'technical' }", "opts?: { volume?: BidVolume | 'full' }"))
add('backend', 'apps/api/services/tenders/generate.py', lambda s: replace(s, 'slice_biz = (getattr(brief, "volumeAdvice", None) or "").strip() == "split"', 'slice_biz = bool(brief.splitSlice) or (getattr(brief, "volumeAdvice", None) or "").strip() == "split"').replace('未建议分册时，商务卷与单卷调用相同。', '显式分册或建议分册时裁分商务卷。').replace('只有建议分册时，这次商务卷才裁技术部分。', '显式分册优先于识别建议，普通单卷调用保持原样。'))

def qa(s):
    s = replace(s, 'QA_VOLUMES = ("business", "technical")', 'QA_VOLUMES = ("business", "technical", "full")')
    s = replace(s, '    if vol:\n        from api.services.tenders.outline import items_for_volume\n\n        outline_items = items_for_volume(brief, vol)', '    if vol:\n        from api.services.tenders.outline import items_for_full, items_for_volume\n\n        outline_items = items_for_full(brief) if vol == "full" else items_for_volume(brief, vol)')
    s = replace(s, '            from api.services.tenders.outline import items_for_volume\n\n            outline_src = items_for_volume(brief, vol)', '            from api.services.tenders.outline import items_for_full, items_for_volume\n\n            outline_src = items_for_full(brief) if vol == "full" else items_for_volume(brief, vol)')
    s = replace(s, '        vol_label = "技术标 Word" if vol == "technical" else "商务标 Word"', '        vol_label = "完整投标文件（综合标）" if vol == "full" else "技术标 Word" if vol == "technical" else "商务标 Word"\n        scope = "同时检查商务响应和技术方案正文。" if vol == "full" else "只评这一卷该有的内容。"')
    s = replace(s, 'f"当前质检对象：{vol_label}。只评这一卷该有的内容。"', 'f"当前质检对象：{vol_label}。{scope}"')
    return replace(s, 'if vol in QA_VOLUMES and slot_volume(key, title) != vol:', 'if vol in {"business", "technical"} and slot_volume(key, title) != vol:')

add('backend', 'apps/api/services/tenders/qa.py', qa)

def records(s):
    anchor = 'def _qa_generated_file(\n'
    helper = '''def _record_qa_volume(brief: BidBrief, volume: str | None = None) -> str:
    """综合标始终按完整标检查，兼容仍发送 business 的旧前端。"""
    from api.services.tenders.qa import normalize_qa_volume

    generated = generate_volume(brief)
    if generated == "full" and not brief.generateBoth:
        return "full"
    want = (volume or "").strip().lower()
    if want == "full":
        raise AppError(ErrorCode.VALIDATION, "该记录不是综合标，请选择商务标或技术标", status_code=422)
    return normalize_qa_volume(want or generated)


'''
    s = replace(s, anchor, helper + anchor)
    s = replace(s, '    want = (volume or "").strip().lower()\n    if want == "technical":', '    resolved = _record_qa_volume(brief, volume)\n    if resolved == "full":\n        if biz:\n            return biz\n        raise AppError(ErrorCode.VALIDATION, "该记录没有完整投标文件 Word，无法自检", status_code=422)\n    want = (volume or "").strip().lower()\n    if want == "technical":')
    old = '    want = (volume or "").strip().lower()\n    vol = want if want in {"business", "technical"} else generate_volume(brief)'
    assert s.count(old) == 2
    s = s.replace(old, '    vol = _record_qa_volume(brief, volume)')
    s = replace(s, '    if not vol:\n        latest = load_qa_report(row.public_id)', '    brief = _qa_brief(row)\n    if generate_volume(brief) == "full" and not brief.generateBoth:\n        vol = "full"\n    elif vol == "full":\n        _record_qa_volume(brief, vol)\n    if not vol:\n        latest = load_qa_report(row.public_id)')
    s = replace(s, '    tech_file = generated.get("techDocxFile")', '    if vol == "full" and not both:\n        row.tech_docx_file = None\n        row.tech_download_name = None\n    tech_file = generated.get("techDocxFile")')
    return s

add('backend', 'apps/api/services/tenders/records.py', records)
add('backend', 'apps/api/routers/tenders.py', lambda s: s.replace('if vol and vol not in {"business", "technical"}:', 'if vol and vol not in {"business", "technical", "full"}:').replace('volume 只能是 business 或 technical', 'volume 只能是 business、technical 或 full'))

def assemble(s):
    return replace(s, '        out = list(notes or [])\n        if not anon:', '''        out = list(notes or [])
        if vol == "full":
            from api.services.tenders.pdf_format import unify_full_docx

            unify_full_docx(path, brief)
        if not anon:''')

add('backend', 'apps/api/services/tenders/assemble.py', assemble)

def pdf(s):
    helper = '''def unify_full_docx(path: Path, brief) -> None:
    """完整标填完商务、技术正文后，共用目录书签和连续页码。"""
    from api.services.tenders.categories import is_volume_label
    from api.services.tenders.document import _ensure_header
    from api.services.tenders.format_rules import apply_footer_page_number
    from api.services.tenders.schema import DocumentFormat

    doc = Document(str(path))
    in_front = True
    cover_named = False
    for para in list(doc.paragraphs):
        text = para.text.strip()
        if _is_toc_heading(text):
            in_front = False
        title = text.split("\\t", 1)[0].strip()
        if not is_volume_label(title):
            continue
        if in_front and not cover_named:
            # 保留封面原有段落和字体，只更换卷名。
            runs = list(para.runs)
            if runs:
                runs[0].text = "投标文件"
                for run in runs[1:]:
                    run.text = ""
            else:
                para.add_run("投标文件")
            cover_named = True
        elif para._p.find("w:pPr/w:sectPr", doc.element.nsmap) is not None:
            # 节属性承载原范本纸张/边距，清卷名时保留这些属性。
            for run in list(para.runs):
                run.text = ""
        else:
            para._p.getparent().remove(para._p)

    fmt = getattr(brief, "documentFormat", None) or DocumentFormat()
    sections = list(doc.sections)
    # 沿用既有正文起编位置，清除技术部分及后续附件里的重启页码。
    start = next((i for i, sec in enumerate(sections)
                  if sec._sectPr.find("w:pgNumType", doc.element.nsmap) is not None
                  and sec._sectPr.find("w:pgNumType", doc.element.nsmap).get(qn("w:start")) is not None), 0)
    for i, sec in enumerate(sections):
        _ensure_header(sec, (getattr(brief, "projectName", "") or "").strip(), cover=(i == 0))
        apply_footer_page_number(sec, fmt, numbered=i >= start, restart=i == start)
    _link_toc_body(doc, brief)
    _repair_format_table_toc(doc, brief)
    doc.save(str(path))


'''
    return replace(s, 'def assemble_from_word_format(\n', helper + 'def assemble_from_word_format(\n')

add('backend', 'apps/api/services/tenders/pdf_format.py', pdf)

# 完整包用于隔离验证；正式源码保持原样。
shutil.copytree(BACK / 'apps/api', ROOT / 'proposed/backend/apps/api', dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('__pycache__'))
shutil.copytree(UI / 'src', ROOT / 'proposed/frontend/src', dirs_exist_ok=True)
for config in UI.glob('*'):
    if config.is_file() and (config.suffix in {'.json', '.ts'}):
        shutil.copy2(config, ROOT / 'proposed/frontend' / config.name)
patch = []
for (project, name), (before, after) in changes.items():
    for folder, content in [('baseline', before), ('proposed', after)]:
        out = ROOT / folder / project / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding='utf-8', newline='\n')
    patch.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                    fromfile=f'a/{project}/{name}', tofile=f'b/{project}/{name}'))
(ROOT / 'full-bid.patch').write_text(''.join(patch), encoding='utf-8', newline='\n')
test_source = ROOT / 'full_tests.py'
if test_source.exists():
    test_content = read(test_source)
    out = ROOT / 'proposed/backend/tests/test_tender_full_bid.py'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(test_content, encoding='utf-8', newline='\n')
    patch.extend(difflib.unified_diff([], test_content.splitlines(True), fromfile='/dev/null',
                                    tofile='b/backend/tests/test_tender_full_bid.py'))
    (ROOT / 'full-bid.patch').write_text(''.join(patch), encoding='utf-8', newline='\n')
print(f'Prepared {len(changes)} files: {ROOT / "full-bid.patch"}')

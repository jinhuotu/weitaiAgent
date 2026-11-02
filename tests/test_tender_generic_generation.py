"""Cross-company/sector acceptance, using data as input rather than routing tables."""
import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image

from api.services.tenders.company_identity import same_company
from api.services.tenders.company_materials import document_matches_company
from api.services.tenders.document import _pageref_bookmark
from api.services.tenders.extract import apply_extract_patch, fill_empty_company_fields
from api.services.tenders.generate import generate_bid
from api.services.tenders.native_format import validate_native_toc
from api.services.tenders.performance import extract_performance_requirement, match_performance_line
from api.services.tenders.schema import BidBrief, OutlineItem, PerformanceLine, PlaceholderItem


CASES = [
    ('青山建设有限公司', '桥梁加固工程', '桥梁加固', 350000, 2),
    ('海风设备有限公司', '冷链冷藏柜采购', '冷链冷藏柜', 87000, 1),
    ('星河服务有限公司', '档案数字化服务', '档案数字化', 450000, 4),
    ('杏林医疗有限公司', '内镜清洗设备采购', '内镜清洗', 260000, 3),
]
SECTIONS = [('一、投标函及投标函附录', 'letter', 1),
            ('二、资格证明材料', 'company', 1),
            ('（一）营业执照', 'company', 2),
            ('（二）法定代表人身份证明', 'legal_id', 2),
            ('三、商务条款响应', 'biz_dev', 1),
            ('四、技术方案', 'tech_chapter', 1),
            ('（一）实施方案', 'tech_chapter', 2),
            ('（二）质量保障', 'tech_chapter', 2),
            ('五、报价文件', 'quote', 1), ('六、其他资料', 'unknown', 1)]


def picture(path, color, size):
    Image.new('RGB', size, color).save(path)
    return path


@pytest.mark.parametrize('company,project,scope,amount,count', CASES)
def test_requirements_and_company_are_only_current_inputs(company, project, scope, amount, count):
    text = f'须提供类似{scope}项目业绩不少于{count}份，每份合同金额不低于{amount}元，须已完成。'
    req = extract_performance_requirement(text)
    assert req.minAmountYuan == amount and req.minCount == count
    assert scope in req.keywords
    good = PerformanceLine(bidderName=company, projectName=scope+'项目合同', amountYuan=amount)
    assert match_performance_line(good, req).passed
    assert not match_performance_line(PerformanceLine(projectName='完全无关合同', amountYuan=amount), req).passed
    current = BidBrief(bidderName=company, projectName=project)
    patched, _ = apply_extract_patch(current, {'bidderName':'别的公司有限公司'})
    assert patched.bidderName == company
    filled, _ = fill_empty_company_fields(current, {'bidderName':'别的公司有限公司', 'bidderAddress':'不应混入的地址'})
    assert not filled.bidderAddress


def test_unknown_identity_and_thresholds_are_not_manufactured():
    blank = BidBrief()
    assert not blank.bidderName and not blank.bidderAddress and not blank.legalPersonName and not blank.legalPersonIdNo
    req = extract_performance_requirement('提供近三年类似项目业绩。')
    assert req.minCount == 0 and req.minAmountYuan == 0
    patched, _ = apply_extract_patch(blank, {'bidderName':'招标范例公司有限公司'})
    assert not patched.bidderName
    assert blank.quoteTaxRate == blank.deliveryDays == blank.warrantyYears == blank.bidValidityDays == 0
    assert not blank.quality


def test_library_company_ownership_is_explicit_and_identity_cards_match_selected_person():
    b = BidBrief(bidderName=CASES[0][0], legalPersonIdNo='person-a')
    owned = SimpleNamespace(tags=['bidder:'+b.bidderName],summary='')
    foreign = SimpleNamespace(tags=['bidder:'+CASES[1][0]],summary='')
    unknown = SimpleNamespace(tags=[],summary='')
    assert document_matches_company(owned,b,key='license')
    assert not document_matches_company(foreign,b,key='license')
    assert not document_matches_company(unknown,b,key='license')


@pytest.mark.parametrize('company,project,scope,amount,count', CASES)
@pytest.mark.parametrize('volume', ['business','technical','full'])
def test_multisector_native_toc_scans_blanks_and_download(tmp_path, monkeypatch, company, project, scope, amount, count, volume):
    src = Document()
    src.add_paragraph('第六章 投标文件格式', style='Heading 1')
    src.add_paragraph('项目名称：____（项目名称）____')
    src.add_paragraph('投标人：________')
    src.add_paragraph('招标编号：___\t＿＿')
    src.add_paragraph('日期：____年____月____日____')
    src.add_paragraph('目录')
    for title, _, level in SECTIONS:
        p = src.add_paragraph(title)
        if level == 2:
            p.paragraph_format.left_indent = 720*635
        p.add_run().add_tab()
        p.add_run('1')
    src.add_page_break()
    for title, _, level in SECTIONS:
        src.add_paragraph(title, style='Heading '+str(level))
        src.add_paragraph(f'{scope}：本章按本次招标要求响应。')
        if '营业执照' in title or '其他资料' in title:
            src.add_paragraph('此处粘贴营业执照扫描件')
        if '法定代表人' in title:
            src.add_paragraph('附：法定代表人身份证正反面扫描件')
    source = tmp_path/'original.docx'
    src.save(source)
    source.with_name(source.name+'.source').write_text('docx-raw',encoding='utf-8')
    original_bytes = source.read_bytes()
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file', lambda _: source)
    monkeypatch.setattr('api.services.tenders.generate.tenders_output_dir', lambda: tmp_path)
    brief = BidBrief(bidderName=company, bidderAddress='本次地址', legalPersonName='本次法人',
         legalPersonIdNo='person-a', invitationId='abcdef123456', projectName=project, bidContent=scope,
         tenderer='本次采购单位', bidDate='2026-10-30', tenderNo='PROJECT-2026-008',
         layoutMode='outline', outlineChapter='第六章 投标文件格式', formatConfirmed=True,
         includePlaceholders=False, includeCommitment=False, generateVolume=volume,
         outlineItems=[OutlineItem(id=str(i),title=t,rawTitle=t,kind=k,level=l) for i,(t,k,l) in enumerate(SECTIONS)])
    license = picture(tmp_path/'license.png','blue',(900,1200))
    front = picture(tmp_path/'front.png','yellow',(860,540))
    back = picture(tmp_path/'back.png','green',(860,540))
    output = generate_bid(brief, catalog_slots=[PlaceholderItem(key='license',title='营业执照'),
       PlaceholderItem(key='id_legal',title='法定代表人身份证正反面')],
       catalog_media={'license':[license,license], 'id_legal':[front,back]}, catalog_facts=[])
    name = output['techDocxFile'] if volume=='technical' else output['docxFile']
    doc = Document(tmp_path/name)
    stats = validate_native_toc(doc)
    rows = [p for p in doc.paragraphs if _pageref_bookmark(p)]
    expected = [t for t,k,_ in SECTIONS if volume=='full' or ((k=='tech_chapter') == (volume=='technical'))]
    assert [p.text.split('\t')[0] for p in rows] == expected
    assert all(p.text.split('\t')[-1].isdigit() for p in rows)
    assert all('PAGEREF' in p._p.xml and not list(p._p.iter(qn('w:drawing'))) for p in rows)
    names = [n.get(qn('w:name')) for n in doc.element.iter(qn('w:bookmarkStart'))]
    assert len(names) == len(set(names))
    images = list(doc.element.iter(qn('w:drawing')))
    assert len(images) == (0 if volume=='technical' else 2)
    for extent in doc.element.iter(qn('wp:extent')):
        assert int(extent.get('cy')) <= 12.01*360000
    assert source.read_bytes() == original_bytes
    cover = '\n'.join(p.text for p in doc.paragraphs[:stats['firstBodyBlock']])
    assert company in cover and 'PROJECT-2026-008' in cover
    assert '____'+company not in cover and '____2026' not in cover
    assert not any(other[0] in doc.element.xml for other in CASES if other[0]!=company)
    download = output['techDownloadName'] if volume=='technical' else output['downloadName']
    assert project in download
    assert ('技术标' if volume=='technical' else '投标文件' if volume=='full' else '商务标') in download


def test_toc_numbering_continues_across_root_numids_and_children_restart():
    from api.services.tenders.word_structure import numbering_labels
    doc = Document()
    root = doc.part.numbering_part.element
    def numbered(text, ident, level, pattern, fmt='chineseCounting'):
        if root.find(f'w:num[@w:numId="{ident}"]',root.nsmap) is None:
            abstract=OxmlElement('w:abstractNum');abstract.set(qn('w:abstractNumId'),str(ident))
            for n in range(2):
                lvl=OxmlElement('w:lvl');lvl.set(qn('w:ilvl'),str(n))
                for tag,value in [('start','1'),('numFmt',fmt),('lvlText',pattern if n==level else '%1、')]:
                    el=OxmlElement('w:'+tag);el.set(qn('w:val'),value);lvl.append(el)
                abstract.append(lvl)
            root.append(abstract)
            num=OxmlElement('w:num');num.set(qn('w:numId'),str(ident)); ref=OxmlElement('w:abstractNumId');ref.set(qn('w:val'),str(ident));num.append(ref);root.append(num)
        p=doc.add_paragraph(text); prop=OxmlElement('w:numPr')
        for tag,value in [('ilvl',str(level)),('numId',str(ident))]:
            el=OxmlElement('w:'+tag);el.set(qn('w:val'),value);prop.append(el)
        p._p.get_or_add_pPr().append(prop)
        return p._p
    paragraphs=[numbered('父甲',70,0,'%1、'),numbered('子甲',71,1,'（%2）'),
                numbered('子乙',71,1,'（%2）'),numbered('父乙',72,0,'%1、'),
                numbered('子丙',71,1,'（%2）')]
    labels=numbering_labels(doc,paragraphs,toc=True)
    assert [labels[p][0] for p in paragraphs]==['一、','（一）','（二）','二、','（一）']


@pytest.mark.asyncio
async def test_scoped_media_rejects_foreign_unknown_and_conflicting_sources(tmp_path, monkeypatch):
    from api.services.tenders import library_kb as library
    from api.services.tenders.company_materials import scoped_library_media
    from api.services.tenders.performance import dump_perf_meta
    company = CASES[0][0]
    paths = [picture(tmp_path/f'{i}.png', color, (20,20))
             for i,color in enumerate(['red','blue','green','yellow'])]
    docs = [SimpleNamespace(public_id=str(i), path=p, tags=tags, summary=summary)
            for i,(p,tags,summary) in enumerate(zip(paths,
                [['bidder:'+company],['bidder:'+CASES[1][0]],[],['bidder:'+company]],
                ['', '', '', dump_perf_meta(PerformanceLine(bidderName=CASES[1][0]))]))]
    async def base(*_): return SimpleNamespace(id=1)
    async def all_docs(*_): return docs
    monkeypatch.setattr(library,'get_base_by_public_id',base)
    monkeypatch.setattr(library,'_list_docs',all_docs)
    monkeypatch.setattr(library,'_doc_file_path',lambda doc: doc.path)
    media, allowed, _, notes = await scoped_library_media(object(),BidBrief(bidderName=company),{'license':paths})
    assert media == {'license':[paths[0]]} and allowed == {'0'}
    assert len(notes) == 3 and all('未采用' in n for n in notes)


@pytest.mark.asyncio
async def test_no_library_is_a_missing_input_not_generation_crash(tmp_path, monkeypatch):
    from api.services.tenders.company_materials import scoped_library_media
    from common.errors import AppError, ErrorCode
    async def missing(*_): raise AppError(ErrorCode.NOT_FOUND,'资料库不存在',404)
    monkeypatch.setattr('api.services.tenders.library_kb.get_base_by_public_id',missing)
    path=tmp_path/'unverified.png'
    media, allowed, _, notes=await scoped_library_media(object(),BidBrief(bidderName=CASES[0][0]),{'license':[path]})
    assert not media and not allowed and notes


def test_company_owned_rag_never_bypasses_current_identity():
    from api.services.knowledge.classify import should_skip_kb_retrieval
    for tag in ['投标资料','tech_bid']:
        assert not should_skip_kb_retrieval(tags=[tag,'bidder:'+CASES[0][0]], bidder_name=CASES[0][0])
        assert should_skip_kb_retrieval(tags=[tag,'bidder:'+CASES[1][0]], bidder_name=CASES[0][0])
        assert should_skip_kb_retrieval(tags=[tag], bidder_name=CASES[0][0])
        assert should_skip_kb_retrieval(tags=[tag,'bidder:'+CASES[0][0]], bidder_name='')
    # Ingestion can archive unassigned documents; adoption is checked separately.
    assert not should_skip_kb_retrieval(tags=['投标资料'])


def test_fallback_commitment_does_not_assume_an_industry():
    from api.services.tenders.commitment import write_commitment_body
    doc=Document()
    write_commitment_body(doc,BidBrief(bidderName=CASES[2][0],tenderer='本次采购人'))
    text='\n'.join(p.text for p in doc.paragraphs)
    assert CASES[2][0] in text
    assert not any(term in text for term in ['工程成本','施工机具','供货、安装','停工','没收投标保证金'])


def test_runtime_tender_code_has_no_legacy_company_or_project_literals():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'apps/api/services'
    paths=list((root/'tenders').rglob('*.py'))+list((root/'tenders/prompts').glob('*.md'))+[root/'knowledge/classify.py']
    forbidden=['河南伟泰','伟泰光电','BIDDER_LOCK','高途智成港','充电桩','直流桩','交流桩','箱变','MES/MOM']
    for path in paths:
        content=path.read_text(encoding='utf-8')
        assert not any(word in content for word in forbidden), path


def test_saved_performance_rows_require_current_company():
    from api.services.tenders.company_materials import scope_performance_lines
    brief=BidBrief(bidderName=CASES[0][0],performanceLines=[
        PerformanceLine(projectName='本公司合同',bidderName=CASES[0][0]),
        PerformanceLine(projectName='他公司合同',bidderName=CASES[1][0]),
        PerformanceLine(projectName='未知所属合同'),
    ])
    rows,notes=scope_performance_lines(brief)
    assert [r.projectName for r in rows]==['本公司合同']
    assert '2' in notes[0] and '未核验' in notes[0]


def test_current_id_is_compared_with_both_person_key_and_card_number():
    from api.services.tenders.id_card import dump_id_meta
    card=SimpleNamespace(tags=['bidder:'+CASES[0][0]],summary=dump_id_meta({'personKey':'key-a','idNo':'id-a'}))
    assert document_matches_company(card,BidBrief(bidderName=CASES[0][0],legalPersonIdNo='id-a'),key='id_legal')
    assert not document_matches_company(card,BidBrief(bidderName=CASES[1][0],legalPersonIdNo='id-a'),key='id_legal')


@pytest.mark.asyncio
async def test_replace_upload_preserves_another_companys_materials(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from api.services.tenders import library_kb as library
    company=CASES[0][0]
    own=SimpleNamespace(tags=['bidder:'+company],summary='',file_key='own.png',storage_path='')
    foreign=SimpleNamespace(tags=['bidder:'+CASES[1][0]],summary='',file_key='foreign.png',storage_path='')
    parent=SimpleNamespace(tags=['slot:license'],name='营业执照',summary='',file_key=None,storage_path=None,public_id='parent')
    db=SimpleNamespace(add=Mock(),delete=AsyncMock(),commit=AsyncMock(),refresh=AsyncMock())
    monkeypatch.setattr(library,'_get_parent',AsyncMock(return_value=(parent,[own,foreign])))
    monkeypatch.setattr(library,'ensure_library_base',AsyncMock(return_value=SimpleNamespace(id=1,public_id='library')))
    for name in ['require_embedding_ready','_attach_facts_extract','_enqueue_library_doc']:
        monkeypatch.setattr(library,name,AsyncMock())
    monkeypatch.setattr(library,'_purge_doc_vectors',Mock())
    unlink=Mock()
    monkeypatch.setattr(library,'unlink_stored_file',unlink)
    monkeypatch.setattr(library,'resolve_storage_path',lambda key:tmp_path/key)
    monkeypatch.setattr(library,'_files_of',lambda *_:[])
    monkeypatch.setattr(library,'item_to_slot',lambda *_:{})
    await library.save_library_file(db,'license',filename='new.png',data=b'new scan',replace=True,bidder_name=company)
    db.delete.assert_awaited_once_with(own)
    unlink.assert_called_once_with('own.png')
    new=db.add.call_args.args[0]
    assert 'bidder:'+company in new.tags


@pytest.mark.parametrize('label', ['投标人或联合体牵头人：\t（盖单位章）', '投标人：（加盖公章）', '投标人（盖章）：'])
def test_technical_cover_copies_current_company_after_structural_blank_fill(tmp_path, monkeypatch, label):
    src=Document()
    src.add_paragraph('第六章 投标文件格式')
    src.add_paragraph('投 标 文 件')
    src.add_paragraph(label)
    src.add_paragraph('目录')
    src.add_paragraph('一、技术方案\t1')
    src.add_page_break()
    src.add_paragraph('一、技术方案',style='Heading 1')
    src.add_paragraph('原版技术方案说明。')
    path=tmp_path/'source.docx';src.save(path)
    path.with_name(path.name+'.source').write_text('docx-raw',encoding='utf-8')
    original=path.read_bytes()
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:path)
    monkeypatch.setattr('api.services.tenders.generate.tenders_output_dir',lambda:tmp_path)
    b=BidBrief(invitationId='abcdef123456',outlineChapter='第六章 投标文件格式',layoutMode='outline',
        bidderName=CASES[0][0],projectName='本次项目',formatConfirmed=True,generateVolume='technical',
        includeCommitment=False,outlineItems=[OutlineItem(id='tech',title='一、技术方案',kind='tech_chapter')])
    result=generate_bid(b,catalog_slots=[],catalog_media={},catalog_facts=[])
    output=Document(tmp_path/result['techDocxFile'])
    front='\n'.join(p.text for p in output.paragraphs).split('目录',1)[0]
    assert CASES[0][0] in front and front.count(CASES[0][0])==1
    assert path.read_bytes()==original


def test_missing_native_cover_is_supplemented_without_reordering_forms():
    from api.services.tenders.native_format import fill_native_cover
    doc=Document()
    doc.add_paragraph('第三部分 投标文件格式')
    doc.add_paragraph('目录')
    doc.add_paragraph('一、资格证明\t1')
    doc.add_paragraph('二、报价文件\t2')
    doc.add_paragraph('一、资格证明',style='Heading 1')
    doc.add_paragraph('原版资格表格说明')
    doc.add_paragraph('二、报价文件',style='Heading 1')
    before=[p.text for p in doc.paragraphs]
    fill_native_cover(doc,BidBrief(bidderName=CASES[1][0],projectName='本次采购项目'))
    after=[p.text for p in doc.paragraphs]
    assert after[-len(before):]==before
    front=''.join(after[:after.index('目录')])
    assert CASES[1][0] in front and '本次采购项目' in front

import asyncio
import hashlib
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image

from api.services.tenders import section_materials as sm
from api.services.tenders.classifier import material_output_coverage
from api.services.tenders.schema import PlaceholderItem


@pytest.mark.parametrize('fixture',sorted((Path(__file__).parent/'fixtures').glob('*.docx')))
def test_fixed_sources_preserve_entire_original_xml_and_have_one_result(fixture,tmp_path):
    doc=Document(fixture)
    original=[(b,b.xml) for b in doc.element.body]
    heads=sm.sections(doc,set())
    eligible=[(h,b) for h,b,_ in heads if sm.section_type(h,b)!='A']
    slots=[PlaceholderItem(key='fixed'+str(i),title='独立核验凭证'+str(i),attachToSection=sm._heading_text(h)) for i,(h,b) in enumerate(eligible)]
    scan=tmp_path/'proof.png';Image.new('RGB',(120,90),'blue').save(scan)
    media={s.key:([scan] if i==0 else []) for i,s in enumerate(slots)}
    sm.append_section_materials(doc,slots,media,[s.key for s in slots])
    plans={p['slot'].key:p for p in doc._element._material_plans.values()}
    report=material_output_coverage(doc,plans)
    assert report['outputs']==report['expected'] and not report['duplicates'] and not report['unmet']
    assert sum(row['img'] for row in report['rows'])==1
    assert len(list(doc.element.body.iter(qn('w:drawing'))))==1
    assert sum(p['fallback'] for p in plans.values())/max(1,len(plans))<.05
    for block,xml in original:
        assert block.getparent() is doc.element.body and block.xml==xml
    assert not [br for br in doc.element.body.iter(qn('w:br')) if br.get(qn('w:type'))=='page']
    assert not list(doc.element.body.iter(qn('w:pageBreakBefore')))
    positions={b:i for i,b in enumerate(doc.element.body)}
    for mark in doc.element.body.iter(qn('w:bookmarkStart')):
        name=mark.get(qn('w:name'),'')
        if not name.startswith('result_'):continue
        host=mark.getparent()
        assert host.tag==qn('w:p') and host.getparent() is doc.element.body
        if name.endswith('_box'):
            assert host.find('w:pPr/w:pBdr',host.nsmap) is not None
            assert '所需材料' in sm._text(host)
        plan=next(p for k,p in plans.items() if name.startswith('result_'+hashlib.sha256(k.encode()).hexdigest()[:20]))
        assert positions[host]>positions[plan['anchor']]
    once=doc.element.xml
    sm.append_section_materials(doc,slots,media,[s.key for s in slots])
    assert doc.element.xml==once


def test_all_27_owned_keys_enter_pending_even_with_no_attachment_cue():
    doc=Document();slots=[]
    for i in range(27):
        title='材料条目'+str(i)
        doc.add_paragraph(title,style='Heading 1')
        if i%2:doc.add_table(rows=1,cols=1).cell(0,0).text='原版表格及说明'
        key='m171d4830fbbe' if i==26 else 'proof'+str(i)
        slots.append(PlaceholderItem(key=key,title='专项核验凭证'+str(i),attachToSection=title))
    last=doc.add_paragraph('原版末尾书签段')
    start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),'999');start.set(qn('w:name'),'source_keep')
    end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),'999')
    last._p.append(start);last._p.append(end)
    original=[(b,b.xml) for b in doc.element.body]
    notes,handled=sm.append_section_materials(doc,slots,{},[s.key for s in slots])
    report=material_output_coverage(doc,{p['slot'].key:p for p in doc._element._material_plans.values()})
    assert len(handled)==report['outputs']==report['expected']==27
    assert all(r['count']==r['occurrences']==r['box']==1 for r in report['rows'])
    assert all(b.xml==xml for b,xml in original)
    ids=[m.get(qn('w:id')) for m in doc.element.body.iter(qn('w:bookmarkStart'))]
    assert len(ids)==len(set(ids))


def test_coverage_anomaly_is_diagnostic_and_does_not_abort(monkeypatch):
    doc=Document();doc.add_paragraph('资格资料',style='Heading 1');doc.add_paragraph('原版说明')
    monkeypatch.setattr(sm,'insert_material',lambda *a,**k:False)
    notes,_=sm.append_section_materials(doc,[PlaceholderItem(key='proof',title='核验凭证',attachToSection='资格资料')],{},['proof'])
    assert any('警告' in n and 'count=0' in n for n in notes)


def test_stale_handled_flag_does_not_suppress_an_owned_result():
    doc=Document();doc.add_paragraph('核验资料',style='Heading 1')
    notes,handled=sm.append_section_materials(doc,[PlaceholderItem(key='proof',title='核验凭证',attachToSection='核验资料')],{},['proof'],skip_keys=['proof'])
    assert handled=={'proof'}
    assert material_output_coverage(doc,{'proof':'核验资料'})['outputs']==1


@pytest.mark.parametrize('name',['的资料','资料要求待核对','名称待核对',''])
def test_fragments_do_not_manufacture_boxes(name):
    doc=Document();doc.add_paragraph('资格资料',style='Heading 1');doc.add_paragraph('原版说明')
    before=doc.element.xml
    sm.append_section_materials(doc,[PlaceholderItem(key='fragment',title=name)],{},['fragment'])
    assert doc.element.xml==before


def test_idle_backoff_reset_and_notification_during_claim(monkeypatch):
    from api.services.knowledge import queue
    ev=asyncio.Event(); delays=[]; claims=[]; ran=[]
    class Session:
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
    async def claim(_):
        claims.append(1)
        if len(claims)==4:ev.set()  # enqueue races with empty claim
        if len(claims)==5:return 'queued'
        return None
    async def wait(coro,timeout):
        delays.append(timeout)
        if ev.is_set():return await coro
        coro.close()
        if len(delays)==5:raise asyncio.CancelledError
        raise TimeoutError
    async def run(task):ran.append(task)
    monkeypatch.setattr(queue,'_event',lambda:ev)
    monkeypatch.setattr(queue,'AsyncSessionLocal',Session)
    monkeypatch.setattr(queue,'_claim_next',claim)
    monkeypatch.setattr(queue,'_run_claimed',run)
    monkeypatch.setattr(queue.asyncio,'wait_for',wait)
    with pytest.raises(asyncio.CancelledError):asyncio.run(queue.run_ingest_worker())
    assert delays==[2,5,15,15,2] and ran==['queued']

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
sys.path[:0] = [str(Path.cwd()/'apps'), str(Path.cwd()/'packages')]

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
from sqlalchemy import select
from db.session import AsyncSessionLocal,engine
from db.models.knowledge import KnowledgeDocument,KnowledgeBase
from db.models.tender import TenderRecord
from api.services.tenders.schema import BidBrief,PlaceholderItem
from api.services.tenders import library_kb
from api.services.tenders.company_identity import company_from_document,credit_code_from_document
from api.services.tenders.company_materials import scoped_library_media,source_material_evidence
from api.services.tenders.id_card import load_id_meta
from api.services.tenders.facts import load_facts
from api.services.tenders.placeholders import DEFAULT_SLOTS,collect_slots,this_bid_keys
from api.services.tenders.section_materials import append_section_materials
from api.services.tenders.classifier import material_output_coverage
from api.services.tenders.material_layout import mark_repeating_headers
from api.services.tenders.assemble import assemble_bid_docx
from api.services.tenders.pdf_format import _word_format_file

OUT=Path('tmp/slot-placement-fix/audit');OUT.mkdir(parents=True,exist_ok=True)


async def main():
    engine.echo=False
    report={}
    async with AsyncSessionLocal() as db:
        base=(await db.execute(select(KnowledgeBase).where(KnowledgeBase.public_id=='tenderlib01'))).scalar_one()
        docs=list((await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.base_id==base.id))).scalars())
        by_id={doc.public_id:doc for doc in docs}
        parents=[doc for doc in docs if library_kb._is_material_parent(doc)]
        keys=[library_kb.key_from_tags(doc.tags) for doc in parents]
        assert len(keys)==len(set(keys))==6
        licensedoc=next(doc for doc in docs if doc.parent_id and library_kb.key_from_tags(doc.tags)=='license')
        owner=company_from_document(source_material_evidence(licensedoc,'license'))
        assert owner and owner!='__conflicting_company__', 'License lacks verified ownership'
        brief=BidBrief(bidderName=owner,bidderCreditCode=credit_code_from_document(source_material_evidence(licensedoc,'license')),authEnabled=True)
        for key,attr in [('id_legal','legalPersonIdNo'),('id_agent','agentIdNo')]:
            candidate=next(doc for doc in docs if doc.parent_id and library_kb.key_from_tags(doc.tags)==key)
            card=library_kb.library_id_meta(candidate) or {}
            identity=card.get('idNo') or next((field['value'] for field in (load_facts(candidate.summary) or {}).get('fields',[])
                if field.get('name') in {'身份证号','身份证号码'}),'')
            setattr(brief,attr,identity)
        catalog=await library_kb.catalog_placeholders(db)
        slots=collect_slots(catalog=catalog,include_keys=keys)
        media=await library_kb.resolve_attachments(db,slots)
        scoped,allowed,people,notes=await scoped_library_media(db,brief,media)
        report['library']={'parents':len(parents),'rows':len(docs), 'keys':sorted(keys),
            'resolvedFiles':{key:len(media.get(key,[])) for key in sorted(keys)},
            'companyMatchedFiles':{key:len(scoped.get(key,[])) for key in sorted(keys)},
            'ownershipWarnings':notes,'hasAgentIdentifier':bool(brief.agentIdNo)}
        assert all(scoped.get(key) for key in ('license','bond','credit','id_legal')), report['library']
        assert not scoped.get('bank_permit'), 'Mislabelled manufacturer document must not act as bank evidence'
        inventory=Document();inventory.styles['Normal'].font.size=Pt(10.5)
        inventory.add_paragraph('资料归并与落位核验',style='Title')
        inventory.add_paragraph('一、资格审查资料',style='Heading 1')
        sections=[('id_legal','法定代表人身份证明','法定代表人身份证'),
            ('id_agent','授权委托书','委托代理人身份证'),('license','营业执照','营业执照'),
            ('bank_permit','基本账户开户证明','基本账户开户证明'),('bond','投标保证金','保证金汇款凭证'),
            ('credit','国家企业信用信息公示系统网站查询','信用查询材料')]
        planned=[]
        for key,heading,caption in sections:
            inventory.add_paragraph(heading,style='Heading 2')
            if key=='bond':
                inventory.add_paragraph('附：汇款凭证');inventory.add_paragraph('的复印件或扫描件。')
            else:
                inventory.add_paragraph('附：'+caption+'复印件或扫描件。')
            planned.append(next(slot for slot in slots if slot.key==key).model_copy(update={'attachToSection':heading}))
        inventory.add_paragraph('专项佐证资料',style='Heading 2');inventory.add_paragraph('请附专项核验凭证扫描件。')
        planned.append(PlaceholderItem(key='proof_missing',title='专项核验凭证',attachToSection='专项佐证资料'))
        originals=[(node,node.xml) for node in inventory.element.body]
        append_section_materials(inventory,planned,scoped,[slot.key for slot in planned],auth_enabled=True)
        assert all(node.xml==xml for node,xml in originals)
        plans={key:value for key,value in inventory._element._material_plans.items()}
        coverage=material_output_coverage(inventory,plans)
        report['inventory']={'coverage':coverage,'drawings':len(list(inventory.element.body.iter(qn('w:drawing')))),
                             'fallback':sum(plan['fallback'] for plan in plans.values())}
        inventory.save(OUT/'inventory-materials.docx')
        # Regenerate existing snapshots without issuing new LLM calls or saving
        # records. Scope is document/material assembly, not proposal drafting.
        records=list((await db.execute(select(TenderRecord).order_by(TenderRecord.id.desc()).limit(12))).scalars())
        selected=next((row for row in records if row.brief_json and _word_format_file(row.brief_json.get('invitationId',''))),None)
        if selected:
            cached=BidBrief.model_validate(selected.brief_json)
            source=_word_format_file(cached.invitationId)
            source_before=source.read_bytes()
            req,include=this_bid_keys(extra=cached.extraPlaceholders,catalog=catalog,
                required_keys=cached.requiredSlotKeys,include_keys=cached.includeSlotKeys,
                has_agent=cached.authEnabled,outline_items=cached.outlineItems,requirements=cached.bidRequirements)
            selected_slots=collect_slots(cached.extraPlaceholders,catalog=catalog,include_keys=include,outline_items=cached.outlineItems)
            selected_media=await library_kb.resolve_attachments(db,selected_slots)
            selected_media,*_=await scoped_library_media(db,cached,selected_media)
            report['regenerated']={}
            for volume in ('business','full'):
                target=OUT/('regenerated-'+volume+'.docx')
                path,warnings=assemble_bid_docx(cached.model_copy(deep=True),target,volume=volume,
                    catalog_slots=selected_slots,catalog_media=selected_media)
                doc=Document(path)
                report['regenerated'][volume]={'tables':len(list(doc.element.body.iter(qn('w:tbl')))),
                    'headerRows':len(list(doc.element.body.iter(qn('w:tblHeader')))),
                    'drawings':len(list(doc.element.body.iter(qn('w:drawing')))), 'warningCount':len(warnings)}
            assert source.read_bytes()==source_before
            report['regenerated']['sourceUntouched']=True
    stress=Document();stress.styles['Normal'].font.size=Pt(11)
    stress.add_paragraph('表格自然分页核验',style='Title')
    stress.add_paragraph('跨页表头应重复，表格中的原文及单元格顺序保持不变。')
    table=stress.add_table(rows=71,cols=3);table.style='Table Grid'
    for cell,label in zip(table.rows[0].cells,['编号','核验内容','说明']):cell.text=label
    for number,row in enumerate(table.rows[1:],1):
        row.cells[0].text=str(number);row.cells[1].text='原版数据连续性核验';row.cells[2].text='自然换页，不添加强制分页符。'
    original=deepcopy(table._tbl)
    assert mark_repeating_headers(stress)==1
    for flag in list(table._tbl.iter(qn('w:tblHeader'))):
        props=flag.getparent();props.remove(flag)
        if not len(props):props.getparent().remove(props)
    assert deepcopy(table._tbl).xml==original.xml
    mark_repeating_headers(stress)
    from api.services.tenders.placeholders import _drop_after
    _drop_after(stress,table._tbl,PlaceholderItem(key='proof',title='表格后专项证明'),[],wording='对应表格后专项证明；所需材料：核验凭证')
    assert not list(stress.element.body.iter(qn('w:pageBreakBefore')))
    stress.save(OUT/'layout-stress.docx')
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='inventory'},ensure_ascii=False))
    print('inventory',json.dumps({key:value for key,value in report['inventory'].items() if key!='coverage'},ensure_ascii=False))
    await engine.dispose()


asyncio.run(main())

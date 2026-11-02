from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.tenders import company_materials as scope, library_kb
from api.services.tenders.schema import BidBrief


def row(text='', tags=()):
    return SimpleNamespace(public_id='test-original', summary=text, tags=list(tags))


def raw(monkeypatch, value):
    monkeypatch.setattr('api.services.knowledge.ingest.read_extracted_text', lambda *args: value)


def test_plaintext_licence_owner_and_code_are_read_without_db_changes(monkeypatch):
    text='营业执照\n统一社会信用代码：91320100MA00000001\n名 称\n甲方资料有限公司\n经营范围：以登记信息为准。'
    raw(monkeypatch,text)
    doc=row('截断摘要')
    brief=BidBrief(bidderName='甲方资料有限公司',bidderCreditCode='91320100MA00000001')
    assert scope.document_matches_company(doc,brief,key='license')
    assert doc.tags==[] and doc.summary=='截断摘要'
    assert not scope.document_matches_company(doc,BidBrief(bidderName='乙方资料有限公司',bidderCreditCode='91320100MA00000002'),key='license')


def test_credit_report_subject_is_distinct_from_other_entities_in_body(monkeypatch):
    raw(monkeypatch,'公共信用信息报告\n机构名称：甲方资料有限公司\n统一社会信用代码：91320100MA00000001\n报告出具单位：公共信息中心\n其他说明：乙方资料有限公司。')
    assert scope.document_matches_company(row(),BidBrief(bidderName='甲方资料有限公司'),key='credit')
    assert not scope.document_matches_company(row(),BidBrief(bidderName='乙方资料有限公司'),key='credit')


def test_payer_not_payee_or_bank_is_used_for_receipt(monkeypatch):
    raw(monkeypatch,'网上银行电子回执\n全称\n甲方资料有限公司\n全称\n乙方资料有限公司\n付款人\n收款人\n开户行\n银行服务有限公司')
    assert scope.document_matches_company(row(),BidBrief(bidderName='甲方资料有限公司'),key='bond')
    assert not scope.document_matches_company(row(),BidBrief(bidderName='乙方资料有限公司'),key='bond')


def test_wrong_document_type_cannot_satisfy_a_correctly_named_slot(monkeypatch):
    raw(monkeypatch,'原厂授权承诺书\n企业名称：甲方资料有限公司')
    assert not scope.document_matches_company(row(),BidBrief(bidderName='甲方资料有限公司'),key='bank_permit')


def test_ocr_contradiction_is_not_overridden_by_matching_code_or_manual_tag(monkeypatch):
    raw(monkeypatch,'营业执照\n名称\n乙方资料有限公司\n统一社会信用代码：91320100MA00000001')
    doc=row(tags=['bidder:甲方资料有限公司','credit_code:91320100MA00000001'])
    assert not scope.document_matches_company(doc,BidBrief(bidderName='甲方资料有限公司',bidderCreditCode='91320100MA00000001'),key='license')


def test_personal_id_trusts_slot_but_explicit_employer_counterevidence_still_blocks(monkeypatch):
    raw(monkeypatch,'居民身份证\n姓名：测试人\n公民身份号码：110105199001010011')
    assert scope.document_matches_company(row(),BidBrief(bidderName='甲方资料有限公司'),key='id_legal')
    raw(monkeypatch,'居民身份证\n工作单位：乙方资料有限公司\n公民身份号码：110105199001010011')
    assert not scope.document_matches_company(row(),BidBrief(bidderName='甲方资料有限公司'),key='id_legal')


def test_cached_identity_front_and_back_can_recover_person_mapping(monkeypatch):
    values={'front':'姓名测试人 性别男 民族汉\n公民身份号码110105199001010011',
            'back':'中华人民共和国居民身份证\n签发机关某公安机关\n有效期限长期'}
    monkeypatch.setattr('api.services.knowledge.ingest.read_extracted_text',lambda base, key:values[key])
    files=[(SimpleNamespace(id=1,public_id='front',summary='OCR text'),None),
           (SimpleNamespace(id=2,public_id='back',summary='OCR text'),None)]
    metas=library_kb._library_card_metas(files)
    assert len(metas)==2 and metas[0]['personKey']==metas[1]['personKey']=='110105199001010011'


@pytest.mark.asyncio
@pytest.mark.parametrize('count,selected,expected', [(1, '', 1), (2, '', 0), (2, 'person-1', 1), (1, 'unknown', 0)])
async def test_agent_selection_uses_unique_identity_without_guessing_between_people(tmp_path, monkeypatch, count, selected, expected):
    parent = SimpleNamespace(public_id='agents', parent_id='', tags=['slot:id_agent'], summary='')
    children = [SimpleNamespace(public_id=f'card-{i}', parent_id='agents', tags=['slot:id_agent'], summary='')
                for i in range(count)]
    paths = {doc.public_id: tmp_path / (doc.public_id + '.png') for doc in children}
    monkeypatch.setattr(library_kb, 'get_base_by_public_id', AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(library_kb, '_list_docs', AsyncMock(return_value=[parent, *children]))
    monkeypatch.setattr(library_kb, '_doc_file_path', lambda doc: paths.get(doc.public_id))
    monkeypatch.setattr(library_kb, 'library_id_meta', lambda doc: {'idNo': 'person-' + doc.public_id.rsplit('-', 1)[-1]})
    raw(monkeypatch, '居民身份证')
    scoped, _, _, _ = await scope.scoped_library_media(None, BidBrief(agentIdNo=selected), {'id_agent': list(paths.values())})
    assert len(scoped['id_agent']) == expected

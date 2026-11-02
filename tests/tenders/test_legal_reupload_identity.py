from types import SimpleNamespace
import pytest
from api.services.tenders.facts import apply_correction
from api.services.tenders.id_card import dump_id_meta,load_id_meta
from api.services.tenders.company_materials import document_matches_company


def test_correcting_identity_number_updates_group_key():
    saved=apply_correction(dump_id_meta({'side':'front','idNo':'old','personKey':'old'}),
        role='person/legal',fields=[{'name':'姓名','value':'测试姓名'},
                                  {'name':'身份证号','value':'11010119900101001x'}])
    card=load_id_meta(saved)
    assert card['personKey']==card['idNo']=='11010119900101001X'
    assert card['name']=='测试姓名'


def test_reuploaded_identity_uses_number_instead_of_cached_key():
    doc=SimpleNamespace(tags=[],summary=dump_id_meta({'side':'front',
        'idNo':'11010119900101001X','personKey':'new-key'}))
    brief=SimpleNamespace(bidderName='测试单位',legalPersonKey='old-key',
                          legalPersonIdNo='11010119900101001x')
    assert document_matches_company(doc,brief,key='id_legal')
    brief.legalPersonIdNo='110101199001010020'
    # Candidate membership does not depend on a stale selected identity.
    assert document_matches_company(doc,brief,key='id_legal')


@pytest.mark.parametrize('key',['id_legal','id_agent'])
def test_personal_id_without_company_evidence_is_candidate(key):
    doc=SimpleNamespace(tags=[],summary=dump_id_meta({'side':'back','personKey':'new-person'}))
    brief=SimpleNamespace(bidderName='投标单位',legalPersonKey='old',legalPersonIdNo='old',agentIdNo='old')
    assert document_matches_company(doc,brief,key=key)
    doc.tags=['bidder:不同单位']
    assert not document_matches_company(doc,brief,key=key)


@pytest.mark.parametrize('key',['license','perf','other'])
def test_nonpersonal_materials_still_require_company_evidence(key):
    doc=SimpleNamespace(tags=[],summary='')
    brief=SimpleNamespace(bidderName='投标单位')
    assert not document_matches_company(doc,brief,key=key)


def test_same_company_stale_identity_stays_candidate():
    doc=SimpleNamespace(tags=['bidder:同公司'],summary=dump_id_meta({
        'side':'front','idNo':'new-number','personKey':'new-person'}))
    brief=SimpleNamespace(bidderName='同公司',legalPersonKey='old-person',legalPersonIdNo='old-number')
    assert document_matches_company(doc,brief,key='id_legal')
    doc.tags=['bidder:其他公司']
    assert not document_matches_company(doc,brief,key='id_legal')


def test_credit_code_candidate_overrides_renamed_company_but_not_conflict():
    doc=SimpleNamespace(tags=['bidder:旧名称','uscc:91310000123456789A'],
        summary=dump_id_meta({'side':'front','personKey':'new'}))
    brief=SimpleNamespace(bidderName='新名称',bidderCreditCode='91310000123456789A',
                          legalPersonKey='old',legalPersonIdNo='old')
    assert document_matches_company(doc,brief,key='id_legal')
    brief.bidderName='旧名称';brief.bidderCreditCode='91310000123456789B'
    assert not document_matches_company(doc,brief,key='id_legal')

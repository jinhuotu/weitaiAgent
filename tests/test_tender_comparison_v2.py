"""Hand-labelled, template-independent scoring review regressions."""
import asyncio
import json
from pathlib import Path
import pytest
from api.services import tender_comparison as review


def install(monkeypatch,respond):
    class Client:
        async def complete(self,messages,**kwargs):
            data=json.loads(messages[-1]['content'])
            result=respond(data)
            if isinstance(result,Exception):raise result
            return result if isinstance(result,str) else json.dumps(result,ensure_ascii=False)
    async def client(*args):return Client()
    monkeypatch.setattr(review,'build_llm_client',client)


def reviews(data,status='not_scoring'):
    return [{'id':u['id'],'status':status,'reason':'本单元是普通填写或说明文字，不属于评标计分规则'} for u in data['units']]


def test_generic_false_flag_is_not_misreported_as_missing_scoring_conditions(monkeypatch):
    install(monkeypatch,lambda d:{'rows':[],'unitReviews':reviews(d),'complete':False})
    result=asyncio.run(review.extract_scoring(None,'请按合同约定供货。\n未含税合价=数量乘单价。'))
    assert result['extractionComplete'] is True
    assert result['coverage']['pendingUnits']==0
    assert result['coverage']['reviewedUnits']==2
    assert not any('不完整评分条件' in w for w in result['warnings'])


def test_missing_contract_is_parse_error_not_original_file_error(monkeypatch):
    install(monkeypatch,lambda d:{'rows':[]})
    result=asyncio.run(review.extract_scoring(None,'附录B 评分办法\n每份合同2分，最高6分。'))
    assert result['extractionComplete'] is False
    assert any(i['kind']=='parse_error' for i in result['issues'])
    assert result['coverage']['pendingUnits']>0


def test_claiming_all_units_reviewed_cannot_hide_omitted_scoring_row(monkeypatch):
    def response(d):
        result=reviews(d)
        for u,r in zip(d['units'],result):
            if '最高6分' in u['text']:r.update(status='extracted',reason='已提取')
        return {'rows':[],'unitReviews':result}
    install(monkeypatch,response)
    result=asyncio.run(review.extract_scoring(None,'附件Z 评价细则\n履约 | 一份2分，最高6分。'))
    assert result['extractionComplete'] is False
    assert result['coverage']['pendingUnits']==1


def test_missing_tier_blocks_conformance_even_with_model_positive_result(monkeypatch):
    def response(d):
        if 'units' not in d:return {'status':'conforming','bidEvidence':'合同A已验收','reason':'满足'}
        raw='一份2分'
        rs=reviews(d)
        for u,r in zip(d['units'],rs):
            if '一份2分' in u['text']:r.update(status='extracted',reason='提取评分')
        return {'rows':[{'requirement':raw,'scopeKind':'common','volume':'business'}],'unitReviews':rs}
    install(monkeypatch,response)
    result=asyncio.run(review.compare_scoring(None,'履约 | 一份2分，两份4分，三份6分。','合同A已验收','full'))
    assert result['rows'][0]['conditionsComplete'] is False
    assert result['rows'][0]['status']=='indeterminate'
    assert result['reviewComplete'] is False


def test_dynamic_scope_comes_from_current_task_and_source_label(monkeypatch):
    def response(d):
        rs=reviews(d)
        for u,r in zip(d['units'],rs):
            if '最高6分' in u['text']:r.update(status='extracted',reason='完整评分条款')
        return {'rows':[{'requirement':'每份合同2分，最高6分。','volume':'technical','scopeKind':'restricted','appliesTo':['标段乙']}],'unitReviews':rs}
    install(monkeypatch,response)
    source='附件丁 标段乙技术评分标准\n业绩 | 每份合同2分，最高6分。'
    selected=asyncio.run(review.extract_scoring(None,source,scope={'lotName':'标段乙'}))
    unknown=asyncio.run(review.extract_scoring(None,source,scope={'lotName':'标段甲'}))
    assert selected['rows'][0]['applicability']=='applicable'
    assert selected['rows'][0]['scopeLabels']==['标段乙']
    assert unknown['rows'][0]['applicability']=='unresolved'
    assert unknown['applicabilityPending']==1


def test_fabricated_scope_label_is_not_accepted(monkeypatch):
    def response(d):
        return {'rows':[{'requirement':'最高6分。','scopeKind':'restricted','appliesTo':['不存在的标段']}],
                'unitReviews':[{'id':u['id'],'status':'extracted','reason':'完整条款'} for u in d['units']]}
    install(monkeypatch,response)
    result=asyncio.run(review.extract_scoring(None,'最高6分。',scope={'lotName':'不存在的标段'}))
    assert result['rows'][0]['scopeLabels']==[]
    assert result['rows'][0]['applicability']=='unresolved'


@pytest.mark.parametrize('text',['根据方案完整性综合评价，最高8分。','价格部分得分=100×最低报价/本报价。','商务权重20%，技术权重30%。'])
def test_subjective_formula_and_weight_cannot_be_marked_conforming(monkeypatch,text):
    def response(d):
        if 'units' not in d:return {'status':'conforming','bidEvidence':'方案及报价完整','reason':'符合'}
        return {'rows':[{'requirement':text,'scopeKind':'common','volume':'both'}],
                'unitReviews':[{'id':u['id'],'status':'extracted','reason':'完整计分规则'} for u in d['units']]}
    install(monkeypatch,response)
    result=asyncio.run(review.compare_scoring(None,text,'方案及报价完整','full'))
    assert result['rows'][0]['status']=='indeterminate'
    assert result['rows'][0]['bidEvidence']==''


def test_partial_model_failure_does_not_discard_other_units(monkeypatch):
    def response(d):
        if '每份合同2分' in d['source']:return RuntimeError('test failure')
        return {'rows':[],'unitReviews':reviews(d)}
    install(monkeypatch,response)
    source='每份合同2分，最高6分。\n'+'\n'.join('普通说明'+str(i) for i in range(90))
    result=asyncio.run(review.extract_scoring(None,source))
    assert result['extractionComplete'] is False
    assert 0<result['coverage']['reviewedUnits']<result['coverage']['totalUnits']
    assert any(i['kind']=='request_error' for i in result['issues'])


def test_review_and_extraction_states_are_independent(monkeypatch):
    def response(d):
        return {'rows':[{'requirement':'每份合同2分，最高6分。','scopeKind':'common','volume':'business'}],
                'unitReviews':[{'id':u['id'],'status':'extracted','reason':'完整规则'} for u in d['units']]}
    install(monkeypatch,response)
    result=asyncio.run(review.extract_scoring(None,'每份合同2分，最高6分。'))
    assert result['extractionComplete'] is True
    assert result['reviewComplete'] is False
    assert result['phase']=='extracted'
    assert result['rows'][0]['status']=='indeterminate'


def test_score_must_be_explicit_maximum_not_tier_or_nonfinite():
    source='每份合同2分；本项满分6分。'
    assert review.normalize_rows([{'requirement':source,'maxScore':2}],source)[0]['maxScore'] is None
    assert review.normalize_rows([{'requirement':source,'maxScore':6}],source)[0]['maxScore']==6
    assert review.normalize_rows([{'requirement':source,'maxScore':'NaN'}],source)[0]['maxScore'] is None


def test_proof_requirement_cannot_disappear_after_scoring_number(monkeypatch):
    def response(d):
        return {'rows':[{'requirement':'每份合同2分，最高6分。','scopeKind':'common'}],
                'unitReviews':[{'id':u['id'],'status':'extracted','reason':'已完整提取'} for u in d['units']]}
    install(monkeypatch,response)
    result=asyncio.run(review.extract_scoring(None,'业绩 | 每份合同2分，最高6分。须提供合同及验收证明。'))
    assert result['extractionComplete'] is False
    assert result['rows'][0]['conditionsComplete'] is False
    assert result['units'][0]['status']=='pending'


def test_duplicate_review_claims_are_not_coverage(monkeypatch):
    def response(d):
        u=d['units'][0]
        return {'rows':[{'requirement':u['text'],'scopeKind':'common'}],
                'unitReviews':[{'id':u['id'],'status':'extracted','reason':'是评分'}, {'id':u['id'],'status':'not_scoring','reason':'不是评分'}]}
    install(monkeypatch,response)
    result=asyncio.run(review.extract_scoring(None,'最高6分。'))
    assert result['extractionComplete'] is False
    assert result['rows'][0]['conditionsComplete'] is False


def test_reference_without_target_stays_pending_not_file_corruption(monkeypatch):
    def response(d):
        return {'rows':[], 'unitReviews':[{'id':u['id'],'status':'reference','reason':'评分细则引用外部附件','referenceTarget':'附件评分细则'} for u in d['units']]}
    install(monkeypatch,response)
    result=asyncio.run(review.extract_scoring(None,'评分规则详见附件评分细则。'))
    assert result['extractionComplete'] is False
    assert result['units'][0]['status']=='reference'
    assert not any('不完整评分条件' in w for w in result['warnings'])


def test_identical_rules_in_different_tables_keep_distinct_provenance(monkeypatch):
    def response(d):
        rs=reviews(d);rows=[]
        for u,r in zip(d['units'],rs):
            if '最高6分。' in u['text']:
                r.update(status='extracted',reason='完整评分条款')
                rows.append({'sourceUnitId':u['id'],'requirement':'最高6分。','scopeKind':'restricted','appliesTo':['标段甲' if '标段甲' in u['section'] else '标段乙']})
        return {'rows':rows,'unitReviews':rs}
    install(monkeypatch,response)
    source='标段甲技术评分标准\n项目 | 评分\n业绩 | 最高6分。\n标段乙技术评分标准\n项目 | 评分\n业绩 | 最高6分。'
    result=asyncio.run(review.extract_scoring(None,source,scope={'lotName':'标段乙'}))
    assert len(result['rows'])==2
    assert result['rows'][0]['sourceOffset']==source.index('最高6分。')
    assert result['rows'][1]['sourceOffset']==source.rindex('最高6分。')
    assert result['rows'][0]['scopeLabels']==['标段甲']
    assert result['rows'][1]['scopeLabels']==['标段乙']
    assert result['rows'][0]['applicability']=='unresolved'
    assert result['rows'][1]['applicability']=='applicable'


def test_duplicate_quote_without_source_id_is_not_silently_attached_to_first_table(monkeypatch):
    install(monkeypatch,lambda d:{'rows':[{'requirement':'最高6分。'}],'complete':True})
    result=asyncio.run(review.extract_scoring(None,'规则甲：最高6分。\n规则乙：最高6分。'))
    assert result['rows']==[]
    assert any(i['kind']=='ambiguous_source' for i in result['issues'])

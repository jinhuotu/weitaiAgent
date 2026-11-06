import asyncio
import json

from api.services import tender_comparison as review


def test_every_character_is_scanned_including_tail():
    source = '甲' * 20500 + '技术评分：每项2分，最高6分。'
    chunks = list(review.source_chunks(source))
    assert chunks[0][0] == 0
    assert chunks[-1][1].endswith('技术评分：每项2分，最高6分。')
    assert all(any(start <= i < start + len(text) for start, text in chunks) for i in range(len(source)))


def test_source_validation_preserves_tiers_qualifiers_and_score():
    quote = '近三年同类业绩，每份合同2分，最高6分；须提供合同及验收证明。'
    rows = review.normalize_rows([
        {'title': '业绩', 'requirement': quote, 'maxScore': 6, 'volume': 'business'},
        {'requirement': '虚构评分条款', 'maxScore': 9},
    ], '商务评分\n' + quote)
    assert len(rows) == 1
    assert rows[0]['requirement'] == quote
    assert rows[0]['maxScore'] == 6
    assert rows[0]['volume'] == 'business'
    assert rows[0]['sourceOffset'] == 5
    assert rows[0]['status'] == 'indeterminate'
    assert review.normalize_rows([{'requirement': quote, 'maxScore': 80}], quote)[0]['maxScore'] is None


def test_copying_requirement_or_fabricating_evidence_never_passes():
    row = {'requirement': '提供三份验收证明，最高6分', 'status': 'indeterminate'}
    for evidence, bid in [('提供三份验收证明，最高6分', row['requirement']), ('满足要求', '满足要求'), ('合同A', '无合同')]:
        result = review.validated_check(row, {'status': 'conforming', 'bidEvidence': evidence}, bid)
        assert result['status'] == 'indeterminate'
        assert result['bidEvidence'] == ''


def test_explicit_contradiction_has_current_bid_evidence():
    row = {'requirement': '交货期不超过30天，最高5分', 'status': 'indeterminate'}
    result = review.validated_check(row, {'status': 'nonconforming', 'bidEvidence': '我方交货期为45天', 'reason': '45天超过30天'}, '供货承诺：我方交货期为45天。')
    assert result['status'] == 'nonconforming'
    assert result['bidEvidence'] == '我方交货期为45天'
    assert result['reason'] == '45天超过30天'
    assert result['bidLocation'] == '当前标书字符 6'


def test_failed_model_returns_incomplete_not_all_pass(monkeypatch):
    async def fail(*args):
        raise RuntimeError('unavailable')
    monkeypatch.setattr(review, 'build_llm_client', fail)
    result = asyncio.run(review.extract_scoring(None, '评分：最高6分'))
    assert result['complete'] is False
    assert result['rows'] == []
    assert result['warnings']


def test_extraction_deduplicates_overlap_and_marks_incomplete(monkeypatch):
    quote = '每份合同2分，最高6分。'
    class Client:
        async def complete(self, messages, **kwargs):
            return json.dumps({'rows': [{'requirement': quote, 'maxScore': 6}] if quote in messages[-1]['content'] else [], 'complete': True})
    async def client(*args):
        return Client()
    monkeypatch.setattr(review, 'build_llm_client', client)
    result = asyncio.run(review.extract_scoring(None, '甲' * 9000 + quote + '乙' * 3000))
    assert len(result['rows']) == 1
    assert result['rows'][0]['sourceOffset'] == 9000
    # A partial quote inside an unstructured oversized paragraph is not proof
    # that all surrounding conditions were reviewed. The row is retained.
    assert result['complete'] is False
    assert result['rows'][0]['conditionsComplete'] is False


def test_current_bid_comparison_preserves_evidence_and_filters_volume(monkeypatch):
    rows = review.normalize_rows([
        {'requirement': '交货期不超过30天，最高5分', 'volume': 'technical'},
        {'requirement': '商务信用最高3分', 'volume': 'business'},
    ], '交货期不超过30天，最高5分\n商务信用最高3分')
    async def extracted(*args):
        return {'rows': rows, 'complete': True, 'warnings': []}
    class Client:
        async def complete(self, *args, **kwargs):
            return json.dumps({'status': 'nonconforming', 'bidEvidence': '我方交货期为45天', 'reason': '45天超过30天'})
    async def client(*args):
        return Client()
    monkeypatch.setattr(review, 'extract_scoring', extracted)
    monkeypatch.setattr(review, 'build_llm_client', client)
    result = asyncio.run(review.compare_scoring(None, '', '我方交货期为45天', 'technical'))
    assert len(result['rows']) == 1
    assert result['rows'][0]['status'] == 'nonconforming'
    assert result['rows'][0]['bidEvidence'] == '我方交货期为45天'
    assert result['rows'][0]['bidLocation'] == '当前标书字符 1'
    assert result['bidDigest']


def test_failed_comparison_keeps_rows_unverified(monkeypatch):
    rows = review.normalize_rows([{'requirement': '技术评分最高5分'}], '技术评分最高5分')
    async def extracted(*args):
        return {'rows': rows, 'complete': True, 'warnings': []}
    async def fail(*args):
        raise RuntimeError('offline')
    monkeypatch.setattr(review, 'extract_scoring', extracted)
    monkeypatch.setattr(review, 'build_llm_client', fail)
    result = asyncio.run(review.compare_scoring(None, '', '测试标书正文', 'full'))
    assert result['complete'] is False
    assert result['rows'][0]['status'] == 'indeterminate'
    assert result['warnings']

import asyncio
import json

import pytest

from api.services.tenders.gen_tech_plan import draft_technical_plan
from api.services.tenders.schema import BidBrief, OutlineItem
from common.errors import AppError


def test_missing_technical_body_retried_as_single_chapter():
    brief = BidBrief(outlineItems=[OutlineItem(id='plan', title='响应方案', kind='tech_plan')])
    class Client:
        calls = 0
        async def complete(self, messages, mode):
            self.calls += 1
            return '{}' if self.calls == 1 else json.dumps({'chapters': [{'title': '响应方案', 'body': '根据本次技术要求逐项落实实施、检查与验收。'}]})
    client = Client()
    result, _ = asyncio.run(draft_technical_plan(None, brief, client=client))
    assert client.calls == 2
    assert result.outlineItems[0].draftBody == '根据本次技术要求逐项落实实施、检查与验收。'


def test_failed_technical_body_cannot_be_delivered_as_complete_document():
    brief = BidBrief(outlineItems=[OutlineItem(id='plan', title='响应方案', kind='tech_plan')])
    class Client:
        async def complete(self, messages, mode):
            return '{}'
    with pytest.raises(AppError):
        asyncio.run(draft_technical_plan(None, brief, client=Client()))
    assert not brief.outlineItems[0].draftBody

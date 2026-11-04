import asyncio
import ast
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from api.services.tenders.schema import BidBrief, BidRequirement, OutlineItem
from api.services.tenders.prompts import get_prompt

ROOT = Path('tmp/prompt-migration')
manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
for name, data in manifest.items():
    if 'original' in data:
        assert get_prompt(name) == data['original'], name
    else:
        current = importlib.import_module('api.services.tenders.gen_tech_plan')
        for route, original in data['originalByRoute'].items():
            assert get_prompt(name, route_label=current._ROUTE_LABELS[route],
                              route_topics=current._ROUTE_TOPICS[route]) == original


def old_module(name):
    spec = importlib.util.spec_from_file_location('baseline_' + name, ROOT / 'before' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Client:
    def __init__(self, result):
        self.result = result
        self.messages = []

    async def complete(self, messages, **kwargs):
        self.messages.append({'messages': messages, 'options': kwargs})
        return json.dumps(self.result, ensure_ascii=False)


async def run(kind, module):
    if kind == 'business':
        brief = BidBrief(projectName='测试项目', autoDraftBizResp=True,
          outlineItems=[OutlineItem(id='c',title='合同条款响应',kind='unknown')],
          bidRequirements=[BidRequirement(text='付款方式按合同约定，应提供承诺',volume='business')])
        client = Client({'clauses':[{'requirement':'付款方式按合同约定，应提供承诺',
                                    'response':'按合同约定付款','deviation':'无偏离','explanation':''}],
                         'chapters':[{'title':'合同条款响应','body':'我方按合同约定履行付款义务。'}]})
        result, notes = await module.draft_business_response(None, brief, client=client)
        output = {'brief':result.model_dump(), 'notes':notes}
    elif kind == 'technical':
        brief = BidBrief(projectName='测试项目', bidContent='工程施工', autoDraftTechPlan=True,
                        outlineItems=[OutlineItem(id='t',title='施工组织设计',kind='tech_chapter')])
        client = Client({'chapters':[{'title':'施工组织设计','body':'按现场条件组织施工，人员数量【待补充】。'}],
                         'deviations':[]})
        result, notes = await module.draft_technical_plan(None, brief, client=client)
        output = {'brief':result.model_dump(), 'notes':notes}
    else:
        brief = BidBrief(projectName='测试项目',generateVolume='full')
        client = Client({'similarityScore':95,'missing':[],'summary':'内容已覆盖，待人工核对。'})
        with patch('api.services.models.runtime.build_llm_client',AsyncMock(return_value=client)):
            output = await module.analyze_with_llm(None, invitation='本项目要求提供施工方案及商务响应。',
                        bid_text='本投标文件包含商务响应和施工方案。',brief=brief,volume='full')
    assert client.messages, kind
    return {'calls':client.messages, 'output':output}


async def main():
    report = {'promptFiles':len(manifest),'exactTexts':True,'technicalRoutes':3,'replays':{}}
    for kind, name in [('business','gen_biz_resp'),('technical','gen_tech_plan'),('qa','qa')]:
        before = await run(kind, old_module(name))
        after = await run(kind, importlib.import_module('api.services.tenders.'+name))
        assert before == after, kind
        (ROOT / (kind + '-before.json')).write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding='utf-8')
        (ROOT / (kind + '-after.json')).write_text(json.dumps(after,ensure_ascii=False,indent=2),encoding='utf-8')
        report['replays'][kind] = {'calls':len(after['calls']), 'messagesIdentical':True, 'outputIdentical':True}
    (ROOT / 'comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


asyncio.run(main())

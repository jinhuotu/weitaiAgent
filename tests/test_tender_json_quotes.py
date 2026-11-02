import json
import pytest
from api.services.tenders.extract import parse_extract_payload
from api.services.layouts.parse import extract_json_object


@pytest.mark.parametrize('wrapped',[False,True])
def test_original_chinese_quotes_inside_values_are_preserved(wrapped):
    payload={'projectName':'“陌生项目”采购','bidContent':'按“技术规范”、‘合同条款’和＂格式＂执行；含URL https://example.test/a//b',
             'requiredMaterials':[{'title':'“公示系统”查询截图','reason':'原文“应提供”证明。'}]}
    raw=json.dumps(payload,ensure_ascii=False)
    if wrapped:raw='```json\n'+raw+'\n```'
    assert parse_extract_payload(raw)==payload


def test_nonstandard_delimiter_quotes_do_not_change_embedded_source_quotes():
    assert extract_json_object('{“projectName”: “测试项目”, “notes”: ["原文“条款”不变"]}')=={'projectName':'测试项目','notes':['原文“条款”不变']}


def test_truncated_payload_is_not_silently_accepted_as_material_list():
    from common.errors import AppError
    with pytest.raises(AppError):
        parse_extract_payload('{"projectName":"项目", "requiredMaterials":[')

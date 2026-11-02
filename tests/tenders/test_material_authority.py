import pytest
from api.services.tenders.material_intents import SourceUnit, material_source_kind


@pytest.mark.parametrize('section,parents,expected',[
    ('企业证明',['资格审查条件'],'qualification'),
    ('项目经验',['评审办法'],'scoring'),
    ('附件清单',['投标文件格式'],'format'),
    ('合同通用条款',[] ,''),
    ('付款申请',['合同通用条款'],''),
    ('名词解释',['评审办法'],''),
    ('流程说明',[],''),
    ('无关章节',[],''),
])
def test_only_authoritative_sections_route_materials(section,parents,expected):
    unit=SourceUnit('source','提交申请单、通知书、银行保函。',section,parents=parents)
    assert material_source_kind(unit)==expected

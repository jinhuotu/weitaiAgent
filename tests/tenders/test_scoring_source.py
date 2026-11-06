import pytest
from api.services.cablepkg.scoring_source import source_units,batches,classify_rule,literal_coverage


def test_arbitrary_chapter_and_appendix_table_rows_preserve_source_and_context():
    source='第二章 合同\r\n普通条款\r\n附件乙 商务评分标准\r\n项目 | 要素 | 评审细则\r\n能力 | 业绩 | 每份合同2分，最高6分；须验收证明。\r\n扣分 | 事件 | 每次扣1分。\r\n'
    units=source_units(source)
    row=next(u for u in units if u.text.startswith('能力 |'))
    assert row.kind=='table_row'
    assert row.section=='附件乙 商务评分标准'
    assert row.table_header=='项目 | 要素 | 评审细则'
    assert row.candidate is True
    assert source[row.offset:row.offset+len(row.text)]==row.text
    assert all(source[u.offset:u.offset+len(u.text)]==u.text for u in units)
    assert [u for group in batches(source,units,size=50) for u in group]==units


def test_new_table_does_not_inherit_previous_header():
    units=source_units('技术评分标准\n项目 | 评分\n业绩 | 最高6分\n附录说明\n名称 | 数量\n铜 | 1\n')
    assert units[-1].table_header=='名称 | 数量'


@pytest.mark.parametrize('quote,kind',[
 ('每份合同2分，最高6分；须提供验收证明。','tiered'),
 ('发生违约每次扣2分，最多扣10分。','deduction'),
 ('由评委根据方案完整性综合评价，最高8分。','subjective'),
 ('价格部分得分=100-100×偏差率，基准价按平均价计算。','relative_price'),
 ('商务权重20%，技术权重30%。','weight'),
 ('满足要求得3分。','fixed'),
 ('根据表现计1至3分。','range'),
])
def test_rule_types_do_not_assume_one_scoring_template(quote,kind):
    assert classify_rule(quote)==kind


def test_oversized_row_is_never_silently_cut_into_conditions():
    source='项目 | 评审细则\n业绩 | '+ '甲'*25000+'最高6分。'
    units=source_units(source)
    groups=list(batches(source,units))
    assert groups[-1]==[units[-1]]
    assert len(groups[-1][0].text)>24000


def test_literal_coverage_detects_dropped_tiers():
    unit=source_units('业绩 | 一份2分，两份4分，三份6分。')[0]
    rows=[{'sourceOffset':5,'requirement':'一份2分'}]
    assert not literal_coverage(unit,rows)

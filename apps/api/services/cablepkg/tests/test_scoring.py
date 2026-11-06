import pytest

from api.services.cablepkg.scoring import normalize_scoring_rows


def test_normalize_scoring_row_preserves_conditions_and_source():
    rows = normalize_scoring_rows([{
        "id": "tech-1",
        "category": "技术评分",
        "text": "提供10kV电力电缆型式试验报告",
        "scoreWeight": 5,
        "source_file": "评标办法.docx",
        "source_page": "12",
        "evidence_required": True,
        "conditions": [{"name": "报告类型", "operator": "eq", "value": "型式试验报告"}],
    }])
    assert rows[0].criterion_id == "tech-1"
    assert rows[0].max_score == 5
    assert rows[0].conditions[0].expected == "型式试验报告"
    assert rows[0].source_page == "12"


def test_duplicate_ids_and_unknown_operator_are_rejected():
    with pytest.raises(ValueError, match="重复"):
        normalize_scoring_rows([{"id": "x", "text": "a"}, {"id": "x", "text": "b"}])
    with pytest.raises(ValueError, match="算子"):
        normalize_scoring_rows([{"id": "x", "text": "a", "conditions": [{"field": "报告", "operator": "contains"}]}])


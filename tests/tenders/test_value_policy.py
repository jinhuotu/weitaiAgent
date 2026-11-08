from api.services.tenders.value_policy import classify_value, field_kind, policy_counts, should_fill


def test_field_kind_has_conservative_default():
    assert field_kind("计划工期") == "约定类"
    assert field_kind("人员姓名") == "实体类"
    assert field_kind("未知字段") == "实体类"


def test_classification_matrix():
    assert classify_value("计划工期", "invitation:1.3.2") == "A"
    assert classify_value("人员姓名", "invitation:1.3.2") == "C"
    assert classify_value("投标人名称", "invitation:1.3.2") == "C"
    assert classify_value("单位性质", "license:xxx") == "B"
    assert classify_value("单位性质", "") == "C"
    assert classify_value("公司名称", "unregistered:value") == "C"
    assert classify_value("项目经理", "qual:certificate") == "B"
    assert classify_value("保证金", "invitation:2.1") == "A"
    assert classify_value("任意字段", "id:front") == "B"
    assert classify_value("任意字段", "finance:2025") == "B"
    assert classify_value("任意字段", "") == "C"
    assert classify_value("任意字段", "other:value") == "C"


def test_should_fill_requires_allowed_class_and_value():
    assert should_fill("计划工期", "invitation:2.1", "30日历天")
    assert should_fill("姓名", "license:x", "张三")
    assert not should_fill("人员姓名", "invitation:2.1", "张三")
    assert not should_fill("计划工期", "invitation:2.1", "")
    assert not should_fill("姓名", "", "张三")


def test_policy_counts():
    assert policy_counts([("工期", "invitation:x"), ("姓名", "license:x"), ("姓名", "")]) == {
        "A": 1,
        "B": 1,
        "C": 1,
    }


def test_invitation_convention_is_a():
    assert classify_value("质量要求", "invitation:x") == "A"


def test_invitation_person_is_c():
    assert classify_value("负责人", "invitation:x") == "C"


def test_invitation_bidder_is_c():
    assert classify_value("投标人名称", "invitation:x") == "C"


def test_license_entity_is_b():
    assert classify_value("单位性质", "license:x") == "B"


def test_empty_source_is_c():
    assert classify_value("单位性质", "") == "C"


def test_unknown_source_is_c():
    assert classify_value("公司名称", "unknown:x") == "C"


def test_qualification_manager_is_b():
    assert classify_value("项目经理", "qual:x") == "B"


def test_invitation_bond_is_a():
    assert classify_value("保证金", "invitation:x") == "A"



from api.services.tenders.company_identity import same_company


def test_credit_code_takes_precedence_over_name():
    assert same_company('旧名称','新名称',left_code='91310000123456789A',right_code='91310000123456789A')
    assert not same_company('相同名称','相同名称',left_code='91310000123456789A',right_code='91310000123456789B')


def test_legacy_name_match_requires_nonempty_names():
    assert same_company('某企业（集团）有限公司','某企业(集团)有限公司')
    assert not same_company('','')

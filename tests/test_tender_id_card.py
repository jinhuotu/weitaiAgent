"""法人身份证 OCR 文本归人。不连 OCR、不连库。"""

from datetime import date

from api.services.tenders.id_card import (
    age_from_id,
    assign_person,
    claim_orphan_back,
    parse_id_card_text,
    persons_from_metas,
)

_TODAY = date(2026, 10, 21)


def test_parse_front_and_age() -> None:
    text = (
        "姓名 郭志伟 性别 男 民族 汉 出生 1998年2月10日 住址 河南省郑州市 "
        "公民身份号码 410521199802104053"
    )
    card = parse_id_card_text(text, today=_TODAY)
    assert card["side"] == "front"
    assert card["name"] == "郭志伟"
    assert card["gender"] == "男"
    assert card["idNo"] == "410521199802104053"
    assert card["personKey"] == "410521199802104053"
    assert card["age"] == "28"
    assert age_from_id("410521199802104053", _TODAY) == "28"


def test_parse_back_has_no_person_yet() -> None:
    card = parse_id_card_text(
        "中华人民共和国 居民身份证 签发机关 郑州市公安局 有效期限 2018.02.10-2038.02.10"
    )
    assert card["side"] == "back"
    assert card["personKey"] == ""
    assert card["name"] == ""


def test_back_sticks_to_latest_open_front() -> None:
    first = parse_id_card_text("姓名李四性别男公民身份号码410521199001011234", today=_TODAY)
    second = parse_id_card_text("姓名王五性别女公民身份号码410521199202023220", today=_TODAY)
    back = parse_id_card_text("签发机关郑州市公安局有效期限2020.01.01-2040.01.01")
    stuck = assign_person(back, [first, second])
    assert stuck["personKey"] == second["idNo"]
    assert stuck["name"] == "王五"
    assert stuck["gender"] == "女"


def test_front_claims_orphan_back() -> None:
    back = parse_id_card_text("居民身份证签发机关郑州市公安局有效期限2018.02.10-2038.02.10")
    front = parse_id_card_text("姓名郭志伟性别男公民身份号码410521199802104053", today=_TODAY)
    claimed = claim_orphan_back(front, [back])
    assert claimed is not None
    assert claimed["personKey"] == front["idNo"]
    assert claimed["name"] == "郭志伟"
    assert claim_orphan_back(front, [front, claimed]) is None


def test_persons_group_front_and_back() -> None:
    front = {
        "side": "front",
        "name": "郭志伟",
        "gender": "男",
        "idNo": "410521199802104053",
        "age": "28",
        "personKey": "410521199802104053",
        "fileId": "a1",
    }
    back = {
        "side": "back",
        "name": "郭志伟",
        "gender": "男",
        "idNo": "410521199802104053",
        "age": "28",
        "personKey": "410521199802104053",
        "fileId": "a2",
    }
    other = {
        "side": "front",
        "name": "李四",
        "gender": "男",
        "idNo": "410521199001011234",
        "age": "36",
        "personKey": "410521199001011234",
        "fileId": "b1",
    }
    rows = persons_from_metas([front, back, other, {"side": "back", "personKey": ""}])
    assert [row["name"] for row in rows] == ["郭志伟", "李四"]
    assert rows[0]["fileIds"] == ["a1", "a2"]
    assert rows[0]["sides"] == ["front", "back"]

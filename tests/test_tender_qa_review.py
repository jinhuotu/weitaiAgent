from api.services.tenders.qa_review import preserve_reviews, review_key, update_review


def _report(text="投标有效期 90 日历天"):
    return {"attentionItems": [{"id": "attention-001", "kind": "validity", "requirement": text}]}


def test_review_survives_rerun_by_stable_key() -> None:
    previous = _report()
    previous["attentionItems"][0]["review"] = {
        "status": "confirmed", "owner": "张三", "note": "已核对", "confirmedAt": "t", "confirmedBy": "张三",
    }
    current = _report()
    current["attentionItems"][0]["id"] = "attention-009"
    merged = preserve_reviews(previous, current)
    assert merged["attentionItems"][0]["review"]["status"] == "confirmed"


def test_review_update_is_idempotent_for_same_payload() -> None:
    report = _report()
    report, changed = update_review(report, "attention-001", status="confirmed", owner="张三", note="已核对", confirmed_by="张三")
    assert changed
    first = dict(report["attentionItems"][0]["review"])
    report["attentionItems"][0]["review"]["confirmedAt"] = first["confirmedAt"]
    report, changed = update_review(report, "attention-001", status="confirmed", owner="张三", note="已核对", confirmed_by="张三")
    assert changed is False


def test_review_key_does_not_merge_different_requirements() -> None:
    previous = _report("投标有效期 90 日历天")
    previous["attentionItems"][0]["review"] = {"status": "confirmed"}
    current = _report("投标有效期 60 日历天")
    assert review_key(previous["attentionItems"][0]) != review_key(current["attentionItems"][0])
    assert "review" not in preserve_reviews(previous, current)["attentionItems"][0]

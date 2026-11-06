from types import SimpleNamespace

from api.services.knowledge import ingest
from api.services.tenders import library_kb


def test_keep_id_card_summary():
    assert ingest._keep_summary("idcard:{}")
    assert ingest._keep_summary("FACTS:{}")
    assert ingest._keep_summary("PERFJSON:{}")
    assert not ingest._keep_summary("公民身份号码")


def test_ocr_summary_still_pairs_legal_id(tmp_path, monkeypatch):
    front = tmp_path / "front.png"
    back = tmp_path / "back.png"
    front.write_bytes(b"x")
    back.write_bytes(b"x")
    parent = SimpleNamespace(
        public_id="parent",
        name="法定代表人身份证正反面",
        summary="说明",
        tags=["slot:id_legal"],
        file_key=None,
        storage_path=None,
    )
    front_doc = SimpleNamespace(
        id=1,
        public_id="front",
        name="正面",
        summary="公民身份号码110101199001011234\n性别男\n姓名张三\n",
        file_key="front.png",
        storage_path="front.png",
        file_type="png",
        tags=["slot:id_legal"],
        expire_at=None,
        is_perpetual=False,
    )
    back_doc = SimpleNamespace(
        id=2,
        public_id="back",
        name="反面",
        summary="签发机关某市公安局\n有效期限 2018.11.14-2028.11.14\n居民身份证\n",
        file_key="back.png",
        storage_path="back.png",
        file_type="png",
        tags=["slot:id_legal"],
        expire_at=None,
        is_perpetual=False,
    )
    paths = {"front": front, "back": back}
    monkeypatch.setattr(library_kb, "_doc_file_path", lambda doc: paths.get(doc.public_id))
    slot = library_kb.item_to_slot(parent, [front_doc, back_doc])
    cards = {row["id"]: row["idCard"] for row in slot["files"]}
    assert cards["front"]["name"] == "张三"
    assert cards["front"]["personKey"] == "110101199001011234"
    assert cards["back"]["side"] == "back"
    assert cards["back"]["personKey"] == cards["front"]["personKey"]

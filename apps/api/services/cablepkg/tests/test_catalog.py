import json
from pathlib import Path

from openpyxl import Workbook

from apps.api.services.cablepkg.catalog import load_catalog, persist


def test_persist_deduplicates_original_and_is_stable(tmp_path: Path):
    source = tmp_path / "参数表.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "技术参数"
    sheet.append(["技术规范ID", "参数名称", "单位", "投标人保证值"])
    sheet.append(["9985-1", "导体电阻", "Ω/km", "≤0.1"])
    workbook.save(source)
    catalog = tmp_path / "library"

    first = persist(source, catalog)
    first_bytes = (catalog / "catalog.json").read_bytes()
    second = persist(source, catalog)
    second_bytes = (catalog / "catalog.json").read_bytes()

    assert first["document_id"] == second["document_id"]
    assert first_bytes == second_bytes
    assert len(list((catalog / "originals").iterdir())) == 1
    assert len(load_catalog(catalog)) == 1
    assert json.loads(first_bytes)[0]["blocks"][0]["table"]["headers"][0] == "技术规范ID"

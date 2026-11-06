"""Replay an existing bid snapshot with Word library bindings, without changing its record."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps"), str(ROOT / "packages")]

from docx import Document
from sqlalchemy import select
from db.session import AsyncSessionLocal, engine
from db.models.tender import TenderRecord
from api.services.tenders.schema import BidBrief
from api.services.tenders.records import _generate_for_record
from api.services.tenders.material_intents import prepare_structural_demands
from api.services.tenders.outline import classify_kind


def document_text(path):
    document = Document(path)
    return "\n".join("".join(t.text or "" for t in block.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")) for block in document.element.body)


async def run(args):
    engine.echo = False
    originals = [args.invitation, args.tender, args.previous]
    fingerprints = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
    previous_hash = fingerprints[str(args.previous)]
    matches = [p.name for p in (ROOT / "storage/tenders").glob("*.docx")
               if p.stat().st_size == args.previous.stat().st_size
               and hashlib.sha256(p.read_bytes()).hexdigest() == previous_hash]
    if len(matches) != 1:
        raise ValueError("无法唯一定位与旧标书一致的服务器文件")
    async with AsyncSessionLocal() as db:
        record = (await db.execute(select(TenderRecord).where(TenderRecord.docx_file == matches[0]))).scalar_one()
        brief = BidBrief.model_validate(record.brief_json)
        brief.autoDraftTechPlan = False
        brief.autoDraftBizResp = False
        for item in brief.outlineItems:
            if item.kind == "unknown" or "投标人基本情况表" in item.title:
                item.kind = classify_kind(item.rawTitle or item.title)
        from unittest.mock import patch
        async def saved_recognition(current, *, catalog=None, **kwargs):
            prepare_structural_demands(current, catalog)
            return []
        with patch('api.services.tenders.material_intents.recognize_material_demands', saved_recognition):
            generated, attachment_match = await _generate_for_record(db, brief)
        company_notes = []
        output = ROOT / "storage/tenders" / generated["docxFile"]
        old_text, new_text = document_text(args.previous), document_text(output)
        original_text = document_text(args.tender)
        invitation_text = document_text(args.invitation)
        original_profiles = [table for table in Document(args.tender).tables if table.cell(0, 0).text.strip() == '投标人名称']
        generated_profiles = [table for table in Document(output).tables if table.cell(0, 0).text.strip() == '投标人名称']
        assertions = {
            "parameter_cover_project_number_filled": any(
                row.get('contentScope') == 'cover' and any(field.get('field') == '项目编号' and field.get('value') == brief.tenderNo for field in row.get('fields', []))
                for row in generated.get('wordMaterialOutcomes', [])),
            "financial_placeholders_not_adopted": all(not row.get('fields') for row in generated.get('wordMaterialOutcomes', []) if '财务信息' in row['targetTitle']),
            "invitation_project_matches": brief.tenderNo in invitation_text,
            "profile_table_structure_preserved": bool(original_profiles and generated_profiles and
                len(original_profiles[0].rows) == len(generated_profiles[0].rows) and
                len(original_profiles[0].columns) == len(generated_profiles[0].columns)),
            "original_scans_preserved": len(Document(output).inline_shapes) >= len(Document(args.previous).inline_shapes),
            "original_has_service_chapter": "技术服务，包括培训、安装指导等" in original_text,
            "original_has_company_table": "投标人基本情况表" in original_text,
            "service_body_copied": "设技术服务中心统一归口本项目技术服务" in new_text,
            "after_sales_body_copied": "首问负责制与24小时值班制" in "".join(new_text.split()),
            "quality_body_copied": "由公司质量负责人牵头的项目质量小组" in new_text,
            "no_project_name_box": "所需材料：项目名称" not in new_text,
            "no_internal_heading_box": "所需材料：一、项目理解" not in new_text,
        }
        report = dict(recordId=record.public_id, output=str(output), bindings=[b.model_dump() for b in brief.libraryBindings],
                      review=brief.libraryBindingReview, outcomes=generated.get("wordMaterialOutcomes"), assertions=assertions,
                      oldPlaceholderCount=old_text.count("所需材料"), newPlaceholderCount=new_text.count("所需材料"),
                      oldCharacters=len(old_text), newCharacters=len(new_text), warnings=generated["warnings"],
                      imageQuality=generated.get("imageQuality"), sourceFingerprints=fingerprints,
                      companyNotes=company_notes)
        report['summary'] = dict(bindings=len(brief.libraryBindings),
            outcomes=dict(Counter(row['status'] for row in report['outcomes'])),
            filledFields=sum(len(row.get('fields', [])) for row in report['outcomes']),
            sourceFilesUnchanged=3)
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == fingerprints[str(p)] for p in originals)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k:report[k] for k in ["output", "assertions", "oldPlaceholderCount", "newPlaceholderCount"]}, ensure_ascii=False), flush=True)
        for row in report["outcomes"]:
            print(json.dumps({k:v for k,v in row.items() if k != "fields"},ensure_ascii=False),flush=True)
    await engine.dispose()
    return 0 if all(assertions.values()) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invitation", type=Path, required=True)
    parser.add_argument("--tender", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "storage/reviews/word-library-regression.json")
    return asyncio.run(run(parser.parse_args()))

if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docx import Document

from api.services.tenders import pdf_format
from api.services.tenders.assemble import _fill_drafted_format_sections
from api.services.tenders.schema import BidBrief, OutlineItem


def make_src(tmp: Path) -> Path:
    raw = Document()
    raw.add_paragraph("第五章 响应文件格式")
    raw.add_paragraph("目录")
    raw.add_paragraph("技术标")
    raw.add_paragraph("一、技术方案")
    raw.add_paragraph("二、响应方案")
    raw.add_paragraph("技术方案")
    raw.add_paragraph("技术方案正文占位说明。")
    raw.add_paragraph("二、响应方案")
    raw.add_paragraph("参照评审办法和技术要求对技术方面详细说明。")
    raw.add_paragraph("此处粘贴售后服务、质保运维方案")
    src = tmp / "fmt.docx"
    raw.save(src)
    src.with_name(src.name + ".source").write_text("docx-raw", encoding="utf-8")
    return src


tmp = Path(tempfile.mkdtemp())
src = make_src(tmp)
pdf_format._word_format_file = lambda _: src
pdf_format._ready_format_docx = lambda _: None

brief = BidBrief(
    layoutMode="outline",
    invitationId="abc123abc123abc1",
    projectName="测试项目",
    bidderName="某公司",
    autoDraftTechPlan=True,
    includePlaceholders=False,
    includeCommitment=False,
    attachQualifications=False,
    outlineItems=[
        OutlineItem(id="o01", title="技术方案", rawTitle="一、技术方案", kind="tech_plan",
                    draftBody="技术方案正文：本项目采用分区施工，人员数量【待补充】。", draftStatus="drafted"),
        OutlineItem(id="o02", title="响应方案", rawTitle="二、响应方案", kind="tech_plan",
                    draftBody="响应方案正文：逐条响应技术要求，售后服务见附件。", draftStatus="drafted"),
    ],
)

dest = tmp / "out.docx"
_, notes = pdf_format.assemble_from_word_format(
    brief, dest, volume="technical", catalog_slots=[], catalog_media={}
)
print("=== notes ===")
for n in notes:
    print(" -", n)
d = Document(str(dest))
print("=== after assemble ===")
for i, p in enumerate(d.paragraphs):
    print(i, repr((p.text or "").replace("\n", "\\n")[:70]))
print("=== fill ===", _fill_drafted_format_sections(dest, brief))
d = Document(str(dest))
print("=== after fill ===")
for i, p in enumerate(d.paragraphs):
    print(i, repr((p.text or "").replace("\n", "\\n")[:70]))

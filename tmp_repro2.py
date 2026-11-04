from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docx import Document

from api.services.tenders import pdf_format
from api.services.tenders.schema import BidBrief, OutlineItem


def make_src(tmp: Path) -> Path:
    raw = Document()
    raw.add_paragraph("第五章 响应文件格式")
    raw.add_paragraph("目录")
    raw.add_paragraph("一、投标函")
    raw.add_paragraph("四、响应方案")
    raw.add_paragraph("投标函")
    raw.add_paragraph("我方已仔细研究了招标文件。")
    raw.add_paragraph("四、响应方案")
    raw.add_paragraph("参照评审办法和技术要求对技术方面详细说明。")
    raw.add_paragraph("此处粘贴售后服务、质保运维方案")
    raw.add_paragraph("五、分项报价表")
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
        OutlineItem(id="o01", title="投标函", rawTitle="一、投标函", kind="letter"),
        OutlineItem(
            id="o02",
            title="响应方案",
            rawTitle="四、响应方案",
            kind="tech_plan",
            draftBody="技术方案正文：本项目采用分区施工，人员数量【待补充】。",
            draftStatus="drafted",
        ),
        OutlineItem(id="o03", title="分项报价表", rawTitle="五、分项报价表", kind="quote"),
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
for i, p in enumerate(d.paragraphs):
    print(i, repr((p.text or "").replace("\n", "\\n")[:80]))

# now call the fill
from api.services.tenders.assemble import _fill_drafted_format_sections
print("=== fill ===", _fill_drafted_format_sections(dest, brief))
d = Document(str(dest))
for i, p in enumerate(d.paragraphs):
    print(i, repr((p.text or "").replace("\n", "\\n")[:80]))

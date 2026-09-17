"""PDF/DXF 草稿栅格化与车位回读。"""

from __future__ import annotations

import base64
from io import BytesIO, StringIO

from PIL import Image

from api.services.ai.chat_images import decode_chat_images, decode_chat_uploads
from api.services.layouts.cad_export.writer import plan_to_dxf_bytes
from api.services.layouts.draft import plan_from_cad_bytes
from api.services.layouts.parse import parse_plan
from api.services.layouts.schema import EXAMPLE_PLAN


def test_decode_pdf_rasterizes_jpeg() -> None:
    img = Image.new("RGB", (80, 50), (255, 255, 255))
    buf = BytesIO()
    img.save(buf, format="PDF")
    raw = buf.getvalue()
    out = decode_chat_images(
        [
            {
                "mimeType": "application/pdf",
                "data": base64.b64encode(raw).decode("ascii"),
                "fileName": "场地.pdf",
            }
        ]
    )
    assert len(out) == 1
    assert out[0][0] == "image/jpeg"
    assert out[0][1].startswith(b"\xff\xd8")


def test_dxf_upload_recovers_embedded_plan() -> None:
    plan = parse_plan(EXAMPLE_PLAN)
    raw = plan_to_dxf_bytes(plan)
    uploads = decode_chat_uploads(
        [
            {
                "mimeType": "application/dxf",
                "data": base64.b64encode(raw).decode("ascii"),
                "fileName": "场地.dxf",
            }
        ]
    )
    assert uploads.images[0][0] == "image/png"
    assert uploads.draft_plan is not None
    back = parse_plan(uploads.draft_plan)
    assert sum(int(r.stalls) for r in back.parkingRows) == 12


def test_dxf_geometry_recovers_stalls_without_json() -> None:
    from api.services.layouts.draft import load_dxf_doc

    plan = parse_plan(EXAMPLE_PLAN)
    raw = plan_to_dxf_bytes(plan)
    doc = load_dxf_doc(raw)
    xdict = doc.rootdict.get("WEITAI")
    if xdict is not None and "PLAN" in xdict:
        del xdict["PLAN"]
    buf = StringIO()
    doc.write(buf)
    stripped = buf.getvalue().encode("utf-8")
    back = plan_from_cad_bytes(stripped)
    assert back is not None
    assert sum(int(r.stalls) for r in back.parkingRows) >= 8

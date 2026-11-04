import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from docx import Document
from api.services.tenders.native_format import validate_native_toc
from common.errors import AppError


results = []
for filename in (
    "574ec4de4b7c.docx",
    "8e5f22252ad0.docx",
    "7bc090fb9655.docx",
    "../../tmp/full-letter-guard/full/c7f9e25f06d0.docx",
):
    path = Path("storage/tenders") / filename
    data = path.read_bytes()
    doc = Document(path)
    xml = doc.element.xml
    result = {"file": filename, "sha256": hashlib.sha256(data).hexdigest()}
    # Reproduce precisely the old validator's first-PAGEREF-only boundary.
    with patch(
        "api.services.tenders.native_format._validation_body_start",
        side_effect=lambda doc, children, rows, first: first,
    ):
        try:
            result["before"] = validate_native_toc(doc)
        except AppError as error:
            result["before_error"] = str(error)
    result["after"] = validate_native_toc(doc)
    result["xml_unchanged"] = xml == doc.element.xml
    result["file_unchanged"] = data == path.read_bytes()
    assert result["xml_unchanged"] and result["file_unchanged"]
    results.append(result)

assert "before_error" in results[0]
assert results[0]["after"] == {"entries": 8, "firstBodyBlock": 34}
assert all(item["before"] == item["after"] for item in results[1:])
try:
    validate_native_toc(Document("storage/tenders/d8953b4a65b4.docx"))
except AppError as error:
    results.append({"file": "d8953b4a65b4.docx", "still_rejected": str(error)})
else:
    raise AssertionError("The old wrong-anchor output must remain rejected")
report = Path("tmp/toc-validation-boundary/real-files.json")
report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(results, ensure_ascii=True, indent=2))

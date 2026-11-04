"""Apply the user-approved snapshot, refusing to overwrite newer edits."""
from pathlib import Path
import sys

review = Path(__file__).resolve().parent
project = sys.argv[1]
assert project in {"backend", "frontend"}
destination = review.parents[1] if project == "backend" else review.parents[2] / "weitaiAgentui"
baseline = review / "baseline" / project
files = [p for p in baseline.rglob("*") if p.is_file()]
for snapshot in files:
    target = destination / snapshot.relative_to(baseline)
    assert target.read_text(encoding="utf-8-sig") == snapshot.read_text(encoding="utf-8-sig"), f"Newer edits: {target}"
test = destination / "tests/test_tender_full_bid.py"
if project == "backend":
    assert not test.exists(), f"Test already exists: {test}"
for snapshot in files:
    relative = snapshot.relative_to(baseline)
    target = destination / relative
    target.write_bytes((review / "proposed" / project / relative).read_bytes())
    print(target)
if project == "backend":
    test.write_bytes((review / "proposed/backend/tests/test_tender_full_bid.py").read_bytes())
    print(test)

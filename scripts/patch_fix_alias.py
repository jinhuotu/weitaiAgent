import pathlib
q = pathlib.Path("E:/code/python/weitaiAgent/apps/api/services/tenders/tech_draft.py")
s = q.read_text(encoding="utf-8")
if "_DRAFT_SYSTEM_V2" in s and "_DRAFT_SYSTEM = _DRAFT_SYSTEM_V2" not in s:
    s = s.replace("_DRAFT_SYSTEM_V2 = \"\"\"", "_DRAFT_SYSTEM = \"\"\"", 1)
    q.write_text(s, encoding="utf-8")
    print("alias fixed (kept _DRAFT_SYSTEM name)")
else:
    print("no fix needed")
import py_compile
py_compile.compile(str(q), doraise=True)
print("COMPILE_OK")

from pathlib import Path
from types import SimpleNamespace
import importlib.util

spec = importlib.util.spec_from_file_location("word_import", Path(__file__).resolve().parents[1] / "scripts/import_tender_word_library.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_title_removes_volume_and_chapter_number():
    assert module.section_title(Path("19_技术文件_3.7_技术服务方案（培训、安装指导）.docx")) == "技术服务方案（培训、安装指导）"
    assert module.section_title(Path("26_提交前自查表（全流程核对）.docx")) == "提交前自查表（全流程核对）"


def test_templates_do_not_match_original_evidence():
    parents = [SimpleNamespace(name="所投产品检测报告 / 认证 / 鉴定证书"),
               SimpleNamespace(name="法定代表人身份证正反面"),
               SimpleNamespace(name="投标保证金缴存回单")]
    for title in ["检测检验报告清单", "型式试验报告承诺函", "法定代表人身份证明及授权委托书", "投标保证金情况说明（含明细表）"]:
        assert module.matching_parent(title, parents) is None


def test_exact_title_and_narrow_alias_reuse_existing():
    scheme = SimpleNamespace(name="技术服务方案")
    license = SimpleNamespace(name="营业执照")
    assert module.matching_parent("技术服务方案", [scheme, license]) is scheme
    assert module.matching_parent("营业执照副本", [scheme, license]) is license


def test_management_does_not_merge_template_with_original_category():
    rows = [
        {"key": "bond", "title": "投标保证金缴存回单", "docId": "existing", "fileCount": 1, "files": [{"id": "scan"}]},
        {"key": "mtemplate", "title": "投标保证金情况说明（含明细表）", "docId": "new", "fileCount": 1, "files": [{"id": "word"}]},
    ]
    result = module.library._merged_library_rows(rows)
    assert result["bond"]["files"] == [{"id": "scan"}]
    assert result["mtemplate"]["files"] == [{"id": "word"}]

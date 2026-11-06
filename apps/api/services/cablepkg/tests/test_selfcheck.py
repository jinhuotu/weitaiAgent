"""Independent rendering cases, full seed coverage, and actual CLI replay."""

from collections import Counter
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

import pytest

from api.services.cablepkg.selfcheck import main, render_report, run_pipeline


ROOT = Path(__file__).resolve().parents[5]
SEED = ROOT / "storage/cable-seed/parameters.json"
ABSOLUTE_PATH = re.compile(r"[A-Za-z]:[\\/]|\\\\|/home/|/Users/|/tmp/|/var/|/etc/|/opt/")


@pytest.fixture
def tmp_path():
    # Isolate files inside the writable workspace; the host pytest temp root
    # is not accessible in this environment. Default mkdir permissions suffice.
    parent = (ROOT / "tmp").resolve()
    parent.mkdir(exist_ok=True)
    directory = parent / ("cable-selfcheck-" + uuid.uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        assert directory.resolve().parent == parent
        shutil.rmtree(directory)


def _section(markdown, title):
    return markdown.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0]


def _data_rows(markdown):
    rows = []
    for line in markdown.splitlines():
        if not line.startswith("| "):
            continue
        cells = [html.unescape(cell.strip()) for cell in line.strip("|").split("|")]
        if cells[0] in {"序号", "固化ID"} or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(cells)
    return rows


@pytest.fixture(scope="module")
def seed_report():
    result = run_pipeline(SEED, task_name="SL2639-package1")
    return result, render_report(result, no_timestamp=True)


def test_seed_stats_and_all_detail_rows(seed_report):
    result, report = seed_report
    assert result.gate.decision == "not_biddable"
    assert result.gate.stats["blocking_reasons"] == {
        "key_parameter": 30, "design_confirmation": 130, "enterprise_required": 251,
        "master_data": 349, "placeholder": 80, "derivation_skip": 0, "material_mismatch": 1, "key_na_response": 10, "union_rows": 769,
    }
    assert "决策：**不可投标**" in report and "可投 0 / 769" in report
    assert "本报告基于示例数据生成，L0/L1 证据为 0，不代表任何企业真实能力，不得作为投标依据。" in report
    assert len(_data_rows(_section(report, "关键参数专区"))) == 30
    assert len(_data_rows(_section(report, "待设计确认清单"))) == 130
    assert len(_data_rows(_section(report, "组件材料待填清单"))) == 80
    details = _section(report, "按固化ID明细")
    assert len(_data_rows(details)) == 769
    headings = re.findall(r"^### (.+)$", details, re.M)
    data = json.loads(SEED.read_text(encoding="utf-8"))
    assert len(headings) == 10
    assert [heading.split(" · ")[0] for heading in headings] == [m["fid"] for m in data["materials"]]
    actual = []
    for group in details.split("### ")[1:]:
        fid = group.split(" · ", 1)[0]
        actual.extend((fid, cells[0], cells[1], cells[2], cells[3]) for cells in _data_rows(group))
    # Exact row multiset: duplicates, missing rows or substituted IDs fail.
    assert Counter(actual) == Counter((r["solidified_id"], r["seq"], r["name"], r["unit"], r["requirement_raw"])
                                      for r in data["rows"])


def test_new_conflict_section_lists_every_G7_G8_and_disposition(seed_report):
    result, report = seed_report
    title = "数据冲突与不可通过补证解决的问题"
    assert report.index("## 阻断总览") < report.index("## " + title) < report.index("## 关键参数专区")
    section = _section(report, title)
    rows = _data_rows(section)
    assert len(rows) == 11
    assert Counter(row[6] for row in rows) == {"G7": 1, "G8": 10}
    assert [(row[0], row[1]) for row in rows if row[6] == "G7"] == [("9985-500108211-00001", "1.42")]
    assert {row[0] for row in rows if row[6] == "G8"} == {m["fid"] for m in result.materials}
    assert all(row[1] == "1.20" for row in rows if row[6] == "G8")
    assert all(row[8] in {"改物料主数据", "改响应值", "列入技术偏差表并附说明"} for row in rows)
    assert all("补检测报告不能解决" in row[7] for row in rows if row[6] == "G8")
    assert result.gate.stats["evidence_cannot_fix_count"] == 10


def test_report_paths_values_method_and_timestamp(seed_report):
    result, report = seed_report
    assert not ABSOLUTE_PATH.search(report)
    policy_file = ROOT / "apps/api/services/cablepkg/rules_cable.json"
    policy = json.loads(policy_file.read_bytes())
    assert hashlib.sha256(policy_file.read_bytes()).hexdigest() in _section(report, "方法与口径")
    assert "承诺值为设计估算，非检测结果" in report
    assert "来源：招标包内文件" in report
    assert "设计待确认行数：130" in report
    assert "生成时间：" not in report
    timestamped = render_report(result)
    assert re.search(r"^生成时间：\d{4}-\d{2}-\d{2}T.+\+08:00$", timestamped, re.M)
    assert re.sub(r"^生成时间：.*\n", "", timestamped, flags=re.M) == report
    for cells in _data_rows(_section(report, "按固化ID明细")):
        promise = cells[4]
        assert not any(re.fullmatch(pattern, promise) for pattern in policy["forbidden_patterns"])
        assert "已通过" not in " | ".join(cells)
        assert "可投" not in " | ".join(cells)


def test_independent_numeric_sort_and_concrete_values(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    seed = fixtures / "input.json"
    # Hand-annotated values independent of the 769-row seed:
    # 4.5mm+0.1mm=4.6mm, 12.5MPa+1MPa=13.5MPa, component is L4.
    data = {"materials": [
        {"fid": "z", "model": "YJV22", "cores": 3, "area": 150, "cond": "铜"},
        {"fid": "a", "model": "YJLV22", "cores": 3, "area": 120, "cond": "铝"},
    ], "rows": [
        {"solidified_id": "z", "seq": "1.10", "name": "※绝缘平均厚度", "unit": "mm", "requirement_raw": "大于等于4.5(10kV)"},
        {"solidified_id": "z", "seq": "1.2", "name": "绝缘老化前抗张强度", "unit": "MPa", "requirement_raw": "大于等于12.5"},
        {"solidified_id": "a", "seq": "1", "name": "交联聚乙烯绝缘料", "unit": "无", "requirement_raw": "满足通用要求"},
    ]}
    seed.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    result = run_pipeline(seed, task_name="独立测试任务")
    req = result.requirements[0]
    assert (req.operator, req.value, req.unit_condition, req.qualifier, req.is_key) == ("ge", 4.5, "10kV", None, True)
    report = render_report(result, no_timestamp=True)
    details = _section(report, "按固化ID明细")
    assert re.findall(r"^### (.+)$", details, re.M) == ["z · YJV22 3×150 铜", "a · YJLV22 3×120 铝"]
    rows = _data_rows(details)
    assert [(row[0], row[4], row[5]) for row in rows] == [
        ("1.2", "13.5", "L3"), ("1.10", "4.6", "L3"), ("1", "（待填：本厂或外购厂家型式规格）", "L4"),
    ]
    assert rows[1][6] == "G2、G1、G3"
    assert len(_data_rows(_section(report, "关键参数专区"))) == 1
    assert result.gate.stats["blocking_reasons"] == {
        "key_parameter": 1, "design_confirmation": 1, "enterprise_required": 0,
        "master_data": 1, "placeholder": 1, "derivation_skip": 0, "material_mismatch": 0, "key_na_response": 0, "union_rows": 3,
    }


def test_escape_paths_table_delimiters_and_keep_unknown_material_rows(tmp_path):
    seed = tmp_path / "input.json"
    seed.write_text(json.dumps({"materials": [{"fid": "a", "model": "YJV22", "cores": 3, "area": 150, "cond": "铜"}],
                               "rows": [{"solidified_id": "unknown", "seq": "1", "name": "参数|第二列\n新行",
                                         "unit": "mm", "requirement_raw": "C:\\secret\\input.txt /home/user/private.txt"}]}), encoding="utf-8")
    result = run_pipeline(seed, task_name="任务 E:\\private\\secret")
    report = render_report(result, no_timestamp=True)
    assert not ABSOLUTE_PATH.search(report)
    assert "secret" not in report and "private.txt" not in report
    assert "参数&#124;第二列<br>新行" in report
    assert len(_data_rows(_section(report, "按固化ID明细"))) == 1
    assert "未生成" in report and "G6" in report
    for path in ("/home/user/private.txt", "/secret.txt", "C:/private/input.txt", r"\\server\private\input.txt"):
        redacted = render_report(run_pipeline(seed, task_name=path), no_timestamp=True)
        assert path not in redacted
        assert "# 招标包内文件 自检报告" in redacted


def test_cli_no_timestamp_replay_creates_directory_and_keeps_seed(tmp_path):
    before = SEED.read_bytes()
    output = tmp_path / "new" / "nested" / "selfcheck.md"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "apps"), "PYTHONIOENCODING": "utf-8"}
    command = [sys.executable, "-m", "api.services.cablepkg.selfcheck", "--seed", str(SEED),
               "--out", str(output), "--task-name", "CLI独立重放", "--no-timestamp"]
    generated = []
    for _ in range(2):
        execution = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
        assert execution.returncode == 0, execution.stderr
        generated.append(output.read_bytes())
    assert generated[0] == generated[1]
    assert not ABSOLUTE_PATH.search(generated[0].decode("utf-8"))
    assert SEED.read_bytes() == before


def test_cli_rejects_writing_inside_seed_directory(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text("unchanged", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--seed", str(seed), "--out", str(seed)])
    assert exc.value.code == 2
    assert seed.read_text(encoding="utf-8") == "unchanged"

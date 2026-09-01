"""布置 JSON 抽取：思考链、尾逗号、条件表兜底。"""

from api.services.layouts.parse import extract_json_object, parse_plan
from api.services.layouts.v2.constraints import LayoutConstraints
from api.services.layouts.v2.pipeline import prepare_v2_plan


def test_extract_json_strips_think_and_trailing_comma() -> None:
    blob = """<think>
先写一个碎片 { "cars": 8
</think>
```json
{"schemaVersion":"1","kind":"ev_charging_station_plan","site":{"widthM":30,"heightM":20,},
 "parkingRows":[{"id":"cars","stalls":8,"stallWidthM":3,"stallLengthM":6,
 "origin":{"x":1,"y":1},"along":"y","charger":{"type":"dc_160kw","startNo":1,"side":"left"}}],
 "equipment":[]}
```
"""
    obj = extract_json_object(blob)
    assert obj["kind"] == "ev_charging_station_plan"
    plan = parse_plan(blob)
    assert sum(r.stalls for r in plan.parkingRows) == 8
    assert plan.site.widthM == 30


def test_extract_json_prefers_plan_over_earlier_object() -> None:
    blob = (
        '{"kind":"ev_charging_station_constraints","fleet":{"cars":8}}\n'
        '{"schemaVersion":"1","kind":"ev_charging_station_plan",'
        '"site":{"widthM":20,"heightM":30},'
        '"parkingRows":[{"id":"a","stalls":4,"stallWidthM":3,"stallLengthM":6,'
        '"origin":{"x":1,"y":1},"along":"y"}]}'
    )
    obj = extract_json_object(blob)
    assert obj["kind"] == "ev_charging_station_plan"
    assert obj["site"]["widthM"] == 20


def test_v2_prepare_seeds_from_constraints_when_json_broken() -> None:
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 8, "trucks": 0, "piles": 8},
            "site": {"widthM": 20, "heightM": 30, "gates": [{"side": "south"}]},
            "transformers": [{"count": 2, "kva": 2000}],
        }
    )
    plan, _issues = prepare_v2_plan(
        "<think>{ not json",
        query="20m×30m 安装8台轿车充电桩 2台2000kVA箱变 靠墙 中间留道路",
        vision="",
        constraints=cons,
        prior=None,
        revise=False,
    )
    assert plan.site.widthM == 20
    assert plan.site.heightM == 30
    assert sum(r.stalls for r in plan.parkingRows) == 8
    assert sum(1 for e in plan.equipment if e.type == "box_transformer") == 2


def test_choice_piece_reads_reasoning_content() -> None:
    from api.services.ai.llm import _choice_piece_text

    content, reason = _choice_piece_text(
        {"delta": {"content": None, "reasoning_content": '{"kind":"x"}'}}
    )
    assert content == ""
    assert '{"kind":"x"}' in reason


def test_constraints_from_chinese_site_and_car_piles() -> None:
    from api.services.layouts.v2.constraints import constraints_from_user_text

    cons = constraints_from_user_text(
        "600平米长方形 长30米 宽20米 安装8台轿车充电桩 2台2000w的箱变"
    )
    assert cons is not None
    assert cons.site.widthM == 30
    assert cons.site.heightM == 20
    assert cons.fleet.cars == 8
    assert cons.transformers[0].count == 2
    assert cons.transformers[0].kva == 2000


def test_eight_cars_use_west_east_and_straight_gate_aisle() -> None:
    """南门 30×20、8 桩：只贴东西墙，过道直线对准出入口，转角不得北+西顶死。"""
    from api.services.layouts.pack import _infer_row_wall
    from api.services.layouts.schema import collect_site_gates
    from api.services.layouts.symbols import aisle_marking_polylines
    from api.services.layouts.v2.constraints import seed_plan_payload

    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 8, "trucks": 0, "piles": 8},
            "transformers": [{"count": 2, "kva": 2000}],
            "site": {
                "widthM": 30,
                "heightM": 20,
                "gates": [{"side": "south", "widthM": 8}],
            },
        }
    )
    plan, _issues = prepare_v2_plan(
        seed_plan_payload(cons, query="30m×20m 8桩"),
        query="长30米宽20米 8台轿车充电桩 靠墙 中间留道路 南侧出入口",
        vision="",
        constraints=cons,
        prior=None,
        revise=False,
    )
    walls = {_infer_row_wall(plan, row) for row in plan.parkingRows}
    assert walls <= {"west", "east"}
    assert walls == {"west", "east"}
    gates = collect_site_gates(plan.site)
    assert gates and gates[0].side == "south"
    gx = float(gates[0].offsetM) + float(gates[0].widthM) / 2.0
    drive = next(a for a in plan.aisles if len(a.centerline) >= 2)
    xs = [p.x for p in drive.centerline]
    ys = [p.y for p in drive.centerline]
    assert len(drive.centerline) == 2
    assert max(xs) - min(xs) < 0.4
    assert abs(sum(xs) / len(xs) - gx) < 1.6
    assert min(ys) < 2.2
    marks = aisle_marking_polylines(drive)
    assert len(marks) == 3
    for line in marks:
        x0, y0 = line[0]
        x1, y1 = line[-1]
        for x, y in line:
            dx, dy = x1 - x0, y1 - y0
            cross = abs((x - x0) * dy - (y - y0) * dx)
            assert cross < 0.15, "过道虚线必须是直线，不能带半圆端头"


def _lshape_north_west_payload() -> dict:
    """用户图里那种北+西转角：8 号车位被 1 号挡住，过道偏离开口。"""
    return {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 30,
            "heightM": 20,
            "gate": {"side": "south", "offsetM": 11, "widthM": 8, "label": "出入口"},
        },
        "buildings": [],
        "roads": [],
        "parkingRows": [
            {
                "id": "north",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 8.0, "y": 13.2},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
            },
            {
                "id": "west",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 0.8, "y": 2.0},
                "along": "y",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "left"},
            },
        ],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 27.0,
                "y": 17.0,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            },
            {
                "id": "tx2",
                "type": "box_transformer",
                "x": 24.0,
                "y": 17.0,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            },
        ],
        "aisles": [],
        "legend": ["box_transformer", "dc_160kw", "parking"],
        "notes": [],
    }


def _assert_west_east_gate_aisle(plan) -> None:
    from api.services.layouts.pack import _infer_row_wall
    from api.services.layouts.schema import collect_site_gates
    from api.services.layouts.symbols import aisle_marking_polylines

    walls = {_infer_row_wall(plan, row) for row in plan.parkingRows}
    assert walls == {"west", "east"}, f"必须两侧对列，不能北+西转角，实际 {walls}"
    gates = collect_site_gates(plan.site)
    assert gates and gates[0].side == "south"
    gx = float(gates[0].offsetM) + float(gates[0].widthM) / 2.0
    drive = next(a for a in plan.aisles if len(a.centerline) >= 2)
    xs = [p.x for p in drive.centerline]
    ys = [p.y for p in drive.centerline]
    assert len(drive.centerline) == 2
    assert max(xs) - min(xs) < 0.4
    assert abs(sum(xs) / len(xs) - gx) < 1.6
    assert min(ys) < 2.2
    for line in aisle_marking_polylines(drive):
        x0, y0 = line[0]
        x1, y1 = line[-1]
        for x, y in line:
            dx, dy = x1 - x0, y1 - y0
            cross = abs((x - x0) * dy - (y - y0) * dx)
            assert cross < 0.15


def test_lshape_png_prepare_forces_west_east() -> None:
    """出图 gentle prepare 不得把对侧排法冲回北+西，过道必须对准南门。"""
    from api.services.layouts.pack import prepare_plan
    from api.services.layouts.parse import parse_plan

    plan = prepare_plan(parse_plan(_lshape_north_west_payload()), gentle=True)
    _assert_west_east_gate_aisle(plan)


def test_lshape_prior_revise_without_relayout_unblocks_stall_8() -> None:
    """同一会话只改过道画法时，仍要解开转角堵死的 8 号车位。"""
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 8, "trucks": 0, "piles": 8},
            "transformers": [{"count": 2, "kva": 2000}],
            "site": {
                "widthM": 30,
                "heightM": 20,
                "gates": [{"side": "south", "widthM": 8}],
            },
        }
    )
    prior = _lshape_north_west_payload()
    plan, _issues = prepare_v2_plan(
        prior,
        query="过道不要半圆，虚线对准出入口",
        vision="",
        constraints=cons,
        prior=prior,
        revise=True,
    )
    _assert_west_east_gate_aisle(plan)


def test_revise_320kw_overrides_extract_default_160() -> None:
    """同一会话改成 320kW：条件表即使默认 160kW，也不得盖回上一张的桩型。"""
    from api.services.layouts.revise import (
        is_layout_revise_query,
        parse_charger_type_hint,
    )

    assert parse_charger_type_hint("改成320KW的直流充电桩") == "dc_320kw"
    assert (
        parse_charger_type_hint(
            "请你帮我把刚才绘制的图中的160KW充电桩修改成320KW的直流充电桩"
        )
        == "dc_320kw"
    )
    assert is_layout_revise_query(
        "改成320KW的直流充电桩", has_prior=True, has_images=False
    )
    prior = _lshape_north_west_payload()
    prior["parkingRows"][0]["charger"]["type"] = "dc_160kw"
    prior["parkingRows"][1]["charger"]["type"] = "dc_160kw"
    prior["legend"] = ["box_transformer", "dc_160kw", "parking"]
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 8, "trucks": 0, "piles": 8},
            "chargers": {"dcType": "dc_160kw"},
            "transformers": [{"count": 2, "kva": 2000}],
            "site": {
                "widthM": 30,
                "heightM": 20,
                "gates": [{"side": "south", "widthM": 8}],
            },
        }
    )
    plan, _issues = prepare_v2_plan(
        prior,
        query="请你帮我把刚才绘制的图中的160KW充电桩修改成320KW的直流充电桩",
        vision="",
        constraints=cons,
        prior=prior,
        revise=True,
    )
    cars = [r for r in plan.parkingRows if float(r.stallLengthM) < 10]
    assert cars
    assert all(r.charger and r.charger.type == "dc_320kw" for r in cars)
    assert any("320kW" in (r.labelPrefix or "") for r in cars)
    assert "dc_320kw" in (plan.legend or [])
    assert "dc_160kw" not in (plan.legend or [])
    _assert_west_east_gate_aisle(plan)

    from api.services.layouts.pack import prepare_plan
    from api.services.layouts.parse import parse_plan, plan_summary
    from api.services.layouts.revise import describe_plan_changes

    drawn = prepare_plan(plan, gentle=True)
    assert "dc_320kw" in (drawn.legend or [])
    assert "dc_160kw" not in (drawn.legend or [])
    assert all(
        "320kW" in (r.labelPrefix or "")
        for r in drawn.parkingRows
        if r.charger and r.charger.type != "none"
    )
    text = plan_summary(drawn)
    assert "320kW直流充电桩" in text
    notes = describe_plan_changes(
        parse_plan(prior),
        drawn,
        query="请你帮我把刚才绘制的图中的160KW充电桩修改成320KW的直流充电桩",
    )
    blob = "\n".join(notes)
    assert "320kW" in blob
    assert "160kW" in blob
    assert "已修改" in blob


def test_120kw_is_first_class_dc_type() -> None:
    """120kW 是独立桩型 dc_120kw，不得被当成 160kW。"""
    from api.services.layouts.parse import parse_plan, plan_summary
    from api.services.layouts.pack import prepare_plan
    from api.services.layouts.revise import parse_charger_type_hint
    from api.services.layouts.schema import coerce_charger_type

    assert coerce_charger_type("120kW") == "dc_120kw"
    assert parse_charger_type_hint("10个120kW直流充电桩") == "dc_120kw"
    assert parse_charger_type_hint("请你增加一下120KW的直流充电桩") == "dc_120kw"
    assert (
        parse_charger_type_hint("把160KW充电桩修改成120KW的直流充电桩")
        == "dc_120kw"
    )

    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 8, "trucks": 0, "piles": 8},
            "chargers": {"dcType": "dc_120kw"},
            "transformers": [{"count": 2, "kva": 2000}],
            "site": {
                "widthM": 30,
                "heightM": 20,
                "gates": [{"side": "south", "widthM": 8}],
            },
        }
    )
    prior = _lshape_north_west_payload()
    plan, _issues = prepare_v2_plan(
        prior,
        query="请你增加一下120KW的直流充电桩",
        vision="",
        constraints=cons,
        prior=prior,
        revise=True,
    )
    cars = [r for r in plan.parkingRows if float(r.stallLengthM) < 10]
    assert cars
    assert all(r.charger and r.charger.type == "dc_120kw" for r in cars)
    assert any("120kW" in (r.labelPrefix or "") for r in cars)
    assert "dc_120kw" in (plan.legend or [])
    assert "dc_160kw" not in (plan.legend or [])

    drawn = prepare_plan(plan, gentle=True)
    assert "dc_120kw" in (drawn.legend or [])
    assert "dc_160kw" not in (drawn.legend or [])
    assert all(
        "120kW" in (r.labelPrefix or "")
        for r in drawn.parkingRows
        if r.charger and r.charger.type != "none"
    )
    assert "120kW直流充电桩" in plan_summary(drawn)

    seeded, _ = prepare_v2_plan(
        "<think>{ not json",
        query="30m×20m 安装8台120kW直流充电桩 2台2000kVA箱变 靠墙",
        vision="",
        constraints=cons,
        prior=None,
        revise=False,
    )
    cars2 = [r for r in seeded.parkingRows if float(r.stallLengthM) < 10]
    assert cars2
    assert all(r.charger and r.charger.type == "dc_120kw" for r in cars2)
    assert "dc_120kw" in (seeded.legend or [])
    assert "dc_160kw" not in (seeded.legend or [])


def test_unspecified_site_grows_to_fit_sixteen_stalls() -> None:
    """未给场地尺寸时，16 台必须全部落在红线内，不能沿用过小的 24×20 把车位画出界。"""
    from api.services.layouts.pack import _row_inside_site, prepare_plan
    from api.services.layouts.revise import describe_plan_changes

    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 16, "trucks": 0, "piles": 16},
            "chargers": {"dcType": "dc_320kw"},
            "site": {
                "widthM": 24,
                "heightM": 20,
                "shapeFrom": "unknown",
                "gates": [{"side": "south", "widthM": 8}],
            },
        }
    )
    prior = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 24,
            "heightM": 20,
            "gate": {"side": "south", "offsetM": 8, "widthM": 8, "label": "出入口"},
            "polygon": [
                {"x": 0, "y": 0},
                {"x": 24, "y": 0},
                {"x": 24, "y": 20},
                {"x": 0, "y": 20},
            ],
        },
        "parkingRows": [
            {
                "id": "cars_w",
                "stalls": 8,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 0.8, "y": 0.8},
                "along": "y",
                "charger": {"type": "dc_320kw", "startNo": 1, "side": "left"},
            },
            {
                "id": "cars_e",
                "stalls": 8,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 17.2, "y": 0.8},
                "along": "y",
                "charger": {"type": "dc_320kw", "startNo": 9, "side": "right"},
            },
        ],
        "equipment": [],
    }
    plan, issues = prepare_v2_plan(
        prior,
        query="如图所示 我并没有说场地多大 说明场地足够放下这16台充电桩",
        vision="",
        constraints=cons,
        prior=prior,
        revise=True,
    )
    assert sum(r.stalls for r in plan.parkingRows) == 16
    assert plan.site.heightM >= 30
    assert all(_row_inside_site(r, plan) for r in plan.parkingRows)
    assert not any(i.code == "redline" for i in issues)
    drawn = prepare_plan(plan, gentle=True)
    assert drawn.site.heightM >= 30
    assert all(_row_inside_site(r, drawn) for r in drawn.parkingRows)
    notes = describe_plan_changes(parse_plan(prior), drawn, query="场地足够放下16台")
    blob = "\n".join(notes)
    assert "放大场地" in blob

    seeded, _ = prepare_v2_plan(
        "<think>{ not json",
        query="配置16台320kW直流充电桩",
        vision="",
        constraints=LayoutConstraints.model_validate(
            {
                "fleet": {"cars": 16, "trucks": 0, "piles": 16},
                "chargers": {"dcType": "dc_320kw"},
                "site": {"gates": [{"side": "south", "widthM": 8}]},
            }
        ),
        prior=None,
        revise=False,
    )
    assert sum(r.stalls for r in seeded.parkingRows) == 16
    assert all(_row_inside_site(r, seeded) for r in seeded.parkingRows)
    assert seeded.site.heightM >= 30


def test_gate_stays_put_when_query_only_mentions_south() -> None:
    """图纸门位不得因「南侧出入口」被改到居中。"""
    from api.services.layouts.pack import prepare_plan
    from api.services.layouts.revise import user_asked_to_move_gate
    from api.services.layouts.schema import collect_site_gates

    assert not user_asked_to_move_gate("南侧出入口配置8台充电桩")
    assert user_asked_to_move_gate("把出入口改到北侧")
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 8, "trucks": 0, "piles": 8},
            "site": {
                "widthM": 30,
                "heightM": 20,
                "gates": [{"side": "south", "widthM": 8}],
            },
        }
    )
    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 30,
            "heightM": 20,
            "gate": {"side": "south", "offsetM": 4.0, "widthM": 8, "label": "出入口"},
        },
        "parkingRows": [
            {
                "id": "cars_w",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "origin": {"x": 0.8, "y": 2},
                "along": "y",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "left"},
            },
            {
                "id": "cars_e",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "origin": {"x": 23, "y": 2},
                "along": "y",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "right"},
            },
        ],
        "equipment": [],
    }
    plan, _ = prepare_v2_plan(
        payload,
        query="南侧出入口 8台轿车充电桩 靠墙",
        vision="",
        constraints=cons,
        prior=payload,
        revise=True,
    )
    gates = collect_site_gates(plan.site)
    assert gates[0].side == "south"
    assert gates[0].offsetM == 4.0
    assert gates[0].widthM == 8
    drawn = prepare_plan(plan, gentle=True)
    again = collect_site_gates(drawn.site)
    assert again[0].side == "south"
    assert again[0].offsetM == 4.0


def test_prefer_left_right_walls_on_wide_site() -> None:
    """未指定贴哪面墙时，优先左右两侧，不因场地更扁而改贴南北。"""
    from api.services.layouts.pack import _infer_row_wall, prepare_plan
    from api.services.layouts.parse import parse_plan

    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {"widthM": 40, "heightM": 20},
        "parkingRows": [
            {
                "id": "a",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "origin": {"x": 1, "y": 1},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
            },
            {
                "id": "b",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "origin": {"x": 1, "y": 10},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
            },
        ],
        "equipment": [],
    }
    plan = prepare_plan(parse_plan(payload))
    walls = {_infer_row_wall(plan, row) for row in plan.parkingRows}
    assert walls == {"west", "east"}


def test_aisle_does_not_overlap_stalls_and_opening_faces_drive() -> None:
    """车道不得压车位；桩在贴墙一头，开口朝过道。"""
    from api.services.layouts.pack import (
        _aisle_aabb,
        _infer_row_wall,
        _overlap,
        _row_aabb,
        charger_side_for_wall,
    )

    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 8, "trucks": 0, "piles": 8},
            "site": {
                "widthM": 30,
                "heightM": 20,
                "gates": [{"side": "south", "widthM": 8}],
            },
        }
    )
    plan, _ = prepare_v2_plan(
        {"schemaVersion": "1", "kind": "ev_charging_station_plan",
         "site": {"widthM": 30, "heightM": 20,
                  "gate": {"side": "south", "offsetM": 11, "widthM": 8, "label": "出入口"}},
         "parkingRows": [
             {"id": "w", "stalls": 4, "stallWidthM": 3, "stallLengthM": 6,
              "origin": {"x": 1, "y": 2}, "along": "y",
              "charger": {"type": "dc_160kw", "startNo": 1, "side": "left"}},
             {"id": "e", "stalls": 4, "stallWidthM": 3, "stallLengthM": 6,
              "origin": {"x": 22, "y": 2}, "along": "y",
              "charger": {"type": "dc_160kw", "startNo": 5, "side": "right"}},
         ],
         "equipment": []},
        query="30m×20m 8台轿车充电桩 南侧出入口",
        vision="",
        constraints=cons,
        prior=None,
        revise=False,
    )
    stall_boxes = [_row_aabb(r) for r in plan.parkingRows]
    for aisle in plan.aisles:
        box = _aisle_aabb(aisle)
        assert box is not None
        assert not any(_overlap(box, s, pad=0.0) for s in stall_boxes)
    for row in plan.parkingRows:
        wall = _infer_row_wall(plan, row)
        assert wall in {"west", "east"}
        assert row.charger
        assert row.charger.side == charger_side_for_wall(wall)


def test_truck_rows_keep_turning_gap() -> None:
    """重卡开口侧须留足回转，车道不压车位。"""
    from api.services.layouts.pack import _aisle_aabb, _overlap, _row_aabb
    from api.services.layouts.rules import TRUCK_AISLE_M

    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 0, "trucks": 4, "piles": 4},
            "site": {
                "widthM": 40,
                "heightM": 40,
                "gates": [{"side": "south", "widthM": 10}],
            },
        }
    )
    plan, _ = prepare_v2_plan(
        {"schemaVersion": "1", "kind": "ev_charging_station_plan",
         "site": {"widthM": 40, "heightM": 40,
                  "gate": {"side": "south", "offsetM": 15, "widthM": 10, "label": "出入口"}},
         "parkingRows": [
             {"id": "tw", "stalls": 2, "stallWidthM": 5, "stallLengthM": 17,
              "origin": {"x": 1, "y": 4}, "along": "y",
              "charger": {"type": "dc_320kw", "startNo": 1, "side": "left"}},
             {"id": "te", "stalls": 2, "stallWidthM": 5, "stallLengthM": 17,
              "origin": {"x": 22, "y": 4}, "along": "y",
              "charger": {"type": "dc_320kw", "startNo": 3, "side": "right"}},
         ],
         "equipment": []},
        query="4台重卡充电桩 南侧出入口",
        vision="",
        constraints=cons,
        prior=None,
        revise=False,
    )
    boxes = [_row_aabb(r) for r in plan.parkingRows]
    west_r = max(b[2] for b in boxes if b[0] < plan.site.widthM * 0.5)
    east_l = min(b[0] for b in boxes if b[2] > plan.site.widthM * 0.5)
    assert east_l - west_r + 1e-6 >= TRUCK_AISLE_M * 0.85
    for aisle in plan.aisles:
        box = _aisle_aabb(aisle)
        assert box is not None
        assert not any(_overlap(box, s, pad=0.0) for s in boxes)

"""LLM 节点内置系统提示：三层优先级，只产出布置 JSON，供 layout_out 消费。"""

from __future__ import annotations

import json

from api.services.layouts.rules import (
    BUSINESS_RULES_TEXT,
    CAD_TEMPLATE_TEXT,
    STYLE_REF_TEXT,
)

# 只示范字段，不要当空间模板抄。
_COMPACT_EXAMPLE = {
    "schemaVersion": "1",
    "kind": "ev_charging_station_plan",
    "titleBlock": {"title": "充电站平面布置图", "sheetNo": "001", "project": ""},
    "site": {
        "widthM": 40,
        "heightM": 28,
        "northDeg": 0,
        "gate": {"side": "south", "offsetM": 14, "widthM": 8, "label": "出入口"},
        "polygon": [],
    },
    "buildings": [],
    "parkingRows": [
        {
            "id": "cars",
            "stalls": 8,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": -45,
            "origin": {"x": 6, "y": 8},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
            "labelPrefix": "直流充电桩",
        }
    ],
    "equipment": [],
    "trenches": [],
    "cables": [],
    "trees": [],
    "greenery": [],
    "roads": [],
    "legend": ["dc_160kw", "parking"],
    "notes": [],
}

_JSON_CONTRACT = """你是充电站平面布置助手。只输出一个可被程序绘图的 JSON，不要 Markdown、不要代码围栏、不要解释。

【契约】schemaVersion="1"，kind="ev_charging_station_plan"。原点西南角，X东 Y北，单位米。
【职责边界·必须遵守】你只填语义与粗位置；程序会强制覆盖车位尺寸、回转车道宽度，并重布电缆沟。
禁止自由发明与目录冲突的 stallWidthM/stallLengthM（轿车固定 3×6，重卡固定 5×17）。
aisles / trenches / cables / sheetStyle 可省略或写空，由程序生成。
禁止输出位图描述，禁止模仿参考图的布局。

【场地】用户消息里若附带草稿图，外轮廓以图为准，不要凭文字臆造。不规则用地必须写 site.polygon（顶点沿边界顺序），widthM/heightM 取包络。
读图文字里若已有 polygon 顶点，原样写入 site.polygon，禁止改成另一种形状。禁止把 T 型/梯形画成大矩形或 L 形。
草稿里实际有的建筑写成 buildings[].rect{x,y,w,h}；没有建筑就写 buildings:[]。不要只写 label 不写 rect。
【出入口】草稿有几处写几处。角上的门必须把 offsetM 放到该边靠角一端（东南=南墙偏东，勿居中）。side 只能 north/south/east/west。
【车位】用 parkingRows 整排写 stalls 数量与 origin/angleDeg/along；尺寸字段按目录填即可（程序会锁死）。
靠墙布置时车位贴围墙，charger.side：南北墙用 head/tail，东西墙用 left/right（贴墙侧）；程序也会按贴墙强制校正。
charger.type 只能 dc_320kw/dc_160kw/dc_120kw/ac_14kw/none。
【箱变】用户要几台写几台，capacityKva 用用户给出的 kVA；每台靠场地边缘并紧邻其供电充电排端头，禁止放在场地中央；程序会强制校正。
【车道】aisles 可空，由程序在开口侧生成回转车道，保证车辆进出。
【字段】site buildings parkingRows equipment legend notes；可选 aisles sheetStyle greenery roads

【示例（只看字段，数值和构图按用户改，不要抄示例里的 8 车位/-45°）】
""" + json.dumps(_COMPACT_EXAMPLE, ensure_ascii=False, separators=(",", ":"))


def build_layout_system_prompt() -> str:
    return "\n\n".join((BUSINESS_RULES_TEXT, CAD_TEMPLATE_TEXT, _JSON_CONTRACT))


LAYOUT_LLM_SYSTEM_PROMPT = build_layout_system_prompt()


def build_layout_user_text(
    *,
    query: str,
    vision: str = "",
    history: str = "",
    has_style_image: bool = False,
    prior_layout_json: str = "",
    revise: bool = False,
) -> str:
    """第一层用户原文 + 读图几何；改参时附带上一张布置 JSON。"""
    parts: list[str] = []
    q = (query or "").strip()
    if q:
        parts.append(f"【当前任务·必须遵守·禁止篡改】\n{q}")
    if revise and prior_layout_json:
        parts.append(
            "【修订模式·必须遵守】\n"
            "下面是本会话上一张已生成的布置 JSON。以它为底做增量修改，不要重做整案。\n"
            "允许改：用户点名的车位数量、桩型功率、斜列角度、靠墙/贴边、"
            "指定编号车位、箱变台数/容量/挪到某角或某侧。\n"
            "必须原样保留：site.polygon、widthM/heightM、buildings、roads、出入口；"
            "未提及的 parkingRows.origin/angleDeg/along、equipment 坐标也尽量原样。"
            "禁止整场重排、禁止另起一套排位方案。\n"
            f"{prior_layout_json}"
        )
    elif prior_layout_json and not revise:
        parts.append(
            "【本会话上一张布置·仅作参考】\n"
            "若【当前任务】是新场地/新草图，不要照抄上一张外形；"
            "以外框与读图为准。\n"
            f"{prior_layout_json[:4000]}"
        )
    v = (vision or "").strip()
    if v:
        parts.append(
            "【当前场地读图·只取轮廓与出入口位置】\n"
            "下列文字里的尺寸数字若与【当前任务】冲突，一律以当前任务为准；"
            "看不清的数字不要采用。"
            "若读图已给出 polygon 顶点，必须原样写入 site.polygon；"
            "同时对照附图校验，不要改成其它外形。\n"
            f"{v}"
        )
    if not revise:
        parts.append(
            "【附图】若本消息附带草稿图，场地外轮廓与建筑位置以图为准，"
            "禁止只按文字想象成矩形或其它形状。"
        )
    h = (history or "").strip()
    if h:
        if revise:
            parts.append("【对话历史·改参上下文】\n" + h)
        else:
            parts.append(
                "【对话历史·仅作上下文；新场地时禁止复用上一张外形】\n" + h
            )
    if has_style_image and not revise:
        parts.append(STYLE_REF_TEXT)
    return "\n\n".join(parts)

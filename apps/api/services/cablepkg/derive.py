"""Data-driven design estimates, never manufacturer facts or test evidence.

The bundled policy is an unverified design profile for 10 kV cables. Formula
coefficients and margins are editable policy, not certified standard values.
No seed data is accessed by this module.
"""

from dataclasses import asdict, dataclass, replace
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from .model import EvidenceLevel, Requirement, Response


@dataclass
class DerivationSkip:
    reason: str
    detail: str
    solidified_id: str = ""
    seq: str = ""
    name: str = ""
    is_key: bool = False
    bidder_supplied: bool = False
    operator: str = "empty"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_rules(path: str | Path | None = None) -> dict[str, Any]:
    """Read policy afresh so callers can inspect or replace it without globals."""
    return json.loads(Path(path or Path(__file__).with_name("rules_cable.json")).read_text(encoding="utf-8"))


def _geometry(material, profile):
    area, cores = float(material["area"]), int(material["cores"])
    if area <= 0 or cores not in (1, 3):
        raise ValueError("positive area and one or three cores required")
    d = math.sqrt(4 * area / (math.pi * profile["compaction"]))
    core = d + 2 * sum(profile["core_layers"])
    steel = profile["steel_large"] if cores == 3 and area >= profile["large_area"] else profile["steel_small"]
    armor = (core * (1 if cores == 1 else profile["bundle_factor"])
             + (profile["single_core_gap"] if cores == 1 else 0)
             + 2 * profile["liner"] + 4 * steel)
    sheath = profile["sheath_single"] if cores == 1 else (profile["sheath_small"] if area <= profile["small_area"] else profile["sheath_default"])
    # Tape volume per axial length: width*thickness*helix_length/axial_pitch.
    # winding_pitch is the pre-overlap pitch; all lengths are in mm.
    axial_pitch = profile["winding_pitch"] * (1 - profile["overlap"])
    if axial_pitch <= 0 or not 0 <= profile["overlap"] < 1:
        raise ValueError("invalid tape pitch/overlap")
    screen_area = (profile["tape_width"] * profile["screen_thickness"]
                   * math.hypot(math.pi * core, axial_pitch) / axial_pitch)
    return {"conductor": d, "armor": armor, "cable": armor + 2 * sheath,
            "sheath": sheath, "steel": steel, "screen_area": screen_area}


def _short_circuit_inputs(rule, req, material, policy):
    params, settings = rule["params"], policy["short_circuit"]
    time = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*s", req.name + " " + (req.unit_condition or ""), re.I)
    seconds = float(time[1]) if time else params["seconds"]
    if seconds not in settings["time_bases"]:
        raise ValueError("unsupported short-circuit time base")
    key = "k_copper" if params.get("screen") else settings["conductor_keys"][material["cond"]]
    return settings[key], seconds


def _calculate(rule, req, material, policy):
    action = rule["action"]
    p = rule.get("params", {})
    if action == "constant":
        return p["value"]
    if action == "metadata":
        return material[p["field"]]
    if action == "structure":
        profile = policy["models"][material["model"]]
        return profile[p["field"]]
    if action == "core_area":
        return f"{int(material['cores'])}×{float(material['area']):g}"
    if action == "copy":
        return req.value
    if action == "affine":
        base = req.value if isinstance(req.value, (int, float)) else p.get("fallback")
        if base is None:
            raise ValueError("numeric requirement missing")
        return base * p.get("factor", 1) + p.get("offset", 0)
    if action == "geometry":
        return _geometry(material, policy["geometry"])[p["field"]] * p.get("factor", 1)
    if action == "geometry_parameter":
        return policy["geometry"][p["field"]] * p.get("factor", 1)
    if action == "percent":
        if "%标称厚度" not in (req.qualifier or ""):
            raise ValueError("percentage qualifier required")
        nominal = p.get("nominal")
        if nominal is None:
            nominal = _geometry(material, policy["geometry"])[p["nominal_field"]]
        return nominal * req.value / 100
    if action == "resistance":
        table = policy["resistance_limits"][material["cond"]]
        limit = table[f"{float(material['area']):g}"]
        return limit * p["factor"]
    if action == "screen_resistance":
        return p["resistivity"] / _geometry(material, policy["geometry"])["screen_area"]
    if action == "short_circuit":
        area = _geometry(material, policy["geometry"])["screen_area"] if p.get("screen") else float(material["area"])
        coefficient, seconds = _short_circuit_inputs(rule, req, material, policy)
        return coefficient * area / math.sqrt(seconds) / 1000
    raise ValueError(f"unsupported action: {action}")


def derive(requirement: Requirement, material: Mapping[str, Any], *, rules=None) -> Response | DerivationSkip:
    """Return one design conclusion or a recorded skip, without side effects."""
    result = _derive(requirement, material, rules=rules)
    return replace(result, solidified_id=requirement.solidified_id or material.get("fid", ""),
                   seq=requirement.seq, name=requirement.name, is_key=requirement.is_key,
                   bidder_supplied=requirement.bidder_supplied or requirement.operator == "bidder_supplied",
                   operator=requirement.operator)


def _derive(requirement: Requirement, material: Mapping[str, Any], *, rules=None) -> Response | DerivationSkip:
    req = requirement
    if req.operator == "na":
        return DerivationSkip("na", "不适用")
    if req.operator == "empty":
        return DerivationSkip("empty", "需求值为空")
    policy = rules if rules is not None else load_rules()
    for rule in policy["rules"]:
        if not re.search(rule["name_pattern"], req.name):
            continue
        if "operators" in rule and req.operator not in rule["operators"]:
            continue
        if "qualifier" in rule and rule["qualifier"] not in (req.qualifier or ""):
            continue
        if "%标称厚度" in (req.qualifier or "") and rule["action"] != "percent":
            return DerivationSkip("invalid_input", "百分比需求缺少标称厚度规则，禁止按毫米直接计算")
        try:
            value = _calculate(rule, req, material, policy)
            level = EvidenceLevel.L3 if req.bidder_supplied or req.operator == "bidder_supplied" else EvidenceLevel(rule["level"])
            if level not in {EvidenceLevel.L2, EvidenceLevel.L3, EvidenceLevel.L4}:
                return DerivationSkip("invalid_rule", "规则不得输出 L0/L1")
            if rule.get("source"):
                level = EvidenceLevel.L3
            if isinstance(value, (int, float)):
                if not math.isfinite(value):
                    raise ValueError("non-finite result")
                digits = rule.get("digits", 2)
                value = (f"{value:.{rule['significant_digits']}g}" if "significant_digits" in rule
                         else f"{value:.{digits}f}")
                if rule.get("trim", True):
                    value = value.rstrip("0").rstrip(".") if "." in value else value
                value = rule.get("prefix", "") + value
            if value is None or any(re.fullmatch(pattern, str(value).strip()) for pattern in policy["forbidden_patterns"]):
                return DerivationSkip("invalid_value", "空值或禁止的空洞保证值")
            basis = f"{rule['id']}: {rule['basis']}; action={rule['action']}; params={json.dumps(rule.get('params', {}), ensure_ascii=False, sort_keys=True)}"
            if rule["action"] == "short_circuit":
                coefficient, seconds = _short_circuit_inputs(rule, req, material, policy)
                basis += f"; 绝热法，k={coefficient:g}，t={seconds:g}s"
            if rule.get("source"):
                basis += f"; source={rule['source']}"
            if req.qualifier:
                basis += f"; qualifier={req.qualifier}; output_unit={req.unit}"
            return Response(seq=req.seq, name=req.name, unit=req.unit,
                            guarantee_value=str(value), evidence_level=level, basis=basis)
        except (KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
            return DerivationSkip("invalid_input", f"{rule['id']}: {exc}")
    return DerivationSkip("no_rule", f"未命中: name={req.name!r}, operator={req.operator}")

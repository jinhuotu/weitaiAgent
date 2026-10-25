"""法人身份证：从 OCR 文本抽出姓名和证号，正反面按证号归到同一个人。"""

from __future__ import annotations

import json
import re
from datetime import date

ID_META_PREFIX = "idcard:"

_ID_RE = re.compile(r"\d{17}[\dXx]")
_NAME_RE = re.compile(r"姓名[:：\s]*([\u4e00-\u9fff·]{2,6}?)(?=性别|民族|出生|住址|公民|$)")
_GENDER_RE = re.compile(r"性别[:：\s]*([男女])")


def dump_id_meta(card: dict) -> str:
    keep = {
        "side": str(card.get("side") or ""),
        "name": str(card.get("name") or ""),
        "gender": str(card.get("gender") or ""),
        "idNo": str(card.get("idNo") or ""),
        "age": str(card.get("age") or ""),
        "personKey": str(card.get("personKey") or ""),
    }
    return ID_META_PREFIX + json.dumps(keep, ensure_ascii=False)


def load_id_meta(raw: str | None) -> dict | None:
    text = (raw or "").strip()
    if not text.startswith(ID_META_PREFIX):
        return None
    try:
        payload = json.loads(text[len(ID_META_PREFIX) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return {
        "side": str(payload.get("side") or ""),
        "name": str(payload.get("name") or ""),
        "gender": str(payload.get("gender") or ""),
        "idNo": str(payload.get("idNo") or ""),
        "age": str(payload.get("age") or ""),
        "personKey": str(payload.get("personKey") or ""),
    }


def age_from_id(id_no: str, today: date | None = None) -> str:
    raw = (id_no or "").strip()
    if len(raw) != 18 or not raw[:17].isdigit():
        return ""
    try:
        born = date(int(raw[6:10]), int(raw[10:12]), int(raw[12:14]))
    except ValueError:
        return ""
    day = today or date.today()
    years = day.year - born.year
    if (day.month, day.day) < (born.month, born.day):
        years -= 1
    if years < 0 or years > 120:
        return ""
    return str(years)


def gender_from_id(id_no: str) -> str:
    raw = (id_no or "").strip()
    if len(raw) != 18 or not raw[16].isdigit():
        return ""
    return "男" if int(raw[16]) % 2 else "女"


def parse_id_card_text(text: str, *, today: date | None = None) -> dict:
    compact = re.sub(r"\s+", "", text or "")
    id_no = ""
    found = _ID_RE.search(compact)
    if found:
        id_no = found.group(0).upper()
    name = ""
    nm = _NAME_RE.search(compact)
    if nm:
        name = nm.group(1).strip("·")
    gender = ""
    gm = _GENDER_RE.search(compact)
    if gm:
        gender = gm.group(1)
    if not gender:
        gender = gender_from_id(id_no)
    side = "unknown"
    if id_no or "姓名" in compact or "公民身份" in compact:
        side = "front"
    elif any(mark in compact for mark in ("签发机关", "有效期限", "居民身份证")):
        side = "back"
    return {
        "side": side,
        "name": name,
        "gender": gender,
        "idNo": id_no,
        "age": age_from_id(id_no, today),
        "personKey": id_no if side == "front" else "",
    }


def assign_person(card: dict, existing: list[dict]) -> dict:
    """反面没有证号。挂到还没有反面的最近一张正面上。"""
    out = dict(card)
    if out.get("idNo"):
        out["side"] = "front"
        out["personKey"] = out["idNo"]
        if not out.get("gender"):
            out["gender"] = gender_from_id(out["idNo"])
        if not out.get("age"):
            out["age"] = age_from_id(out["idNo"])
        return out
    if out.get("side") != "back":
        return out
    backed = {
        str(m.get("personKey") or "")
        for m in existing
        if m.get("side") == "back" and m.get("personKey")
    }
    open_fronts = [
        m
        for m in existing
        if m.get("side") == "front" and m.get("personKey") and str(m.get("personKey")) not in backed
    ]
    if not open_fronts:
        return out
    hit = open_fronts[-1]
    out["personKey"] = str(hit.get("personKey") or "")
    out["name"] = str(hit.get("name") or "")
    out["idNo"] = str(hit.get("idNo") or out["personKey"])
    out["gender"] = str(hit.get("gender") or "")
    out["age"] = str(hit.get("age") or "")
    return out


def claim_orphan_back(front: dict, existing: list[dict]) -> dict | None:
    """正面后到时，把还没挂上的反面收过来。只收最近一张。"""
    key = str(front.get("personKey") or "")
    if not key or front.get("side") != "front":
        return None
    if any(m.get("side") == "back" and str(m.get("personKey") or "") == key for m in existing):
        return None
    orphans = [m for m in existing if m.get("side") == "back" and not str(m.get("personKey") or "")]
    if not orphans:
        return None
    back = dict(orphans[-1])
    back["personKey"] = key
    back["name"] = str(front.get("name") or "")
    back["idNo"] = str(front.get("idNo") or key)
    back["gender"] = str(front.get("gender") or "")
    back["age"] = str(front.get("age") or "")
    return back


def persons_from_metas(rows: list[dict]) -> list[dict]:
    order: list[str] = []
    buckets: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("personKey") or "").strip()
        if not key:
            continue
        if key not in buckets:
            buckets[key] = {
                "key": key,
                "name": "",
                "gender": "",
                "age": "",
                "idNo": str(row.get("idNo") or key),
                "fileIds": [],
                "sides": [],
            }
            order.append(key)
        bucket = buckets[key]
        if row.get("name"):
            bucket["name"] = str(row["name"])
        if row.get("gender"):
            bucket["gender"] = str(row["gender"])
        if row.get("age"):
            bucket["age"] = str(row["age"])
        if row.get("idNo"):
            bucket["idNo"] = str(row["idNo"])
        file_id = str(row.get("fileId") or "").strip()
        if file_id and file_id not in bucket["fileIds"]:
            bucket["fileIds"].append(file_id)
        side = str(row.get("side") or "")
        if side and side not in bucket["sides"]:
            bucket["sides"].append(side)
    out = [buckets[key] for key in order if buckets[key]["name"] or buckets[key]["idNo"]]
    return out


def id_file_label(card: dict) -> str:
    name = str(card.get("name") or "").strip()
    if not name:
        return ""
    side = str(card.get("side") or "")
    if side == "front":
        return f"{name}·正面"
    if side == "back":
        return f"{name}·反面"
    return name

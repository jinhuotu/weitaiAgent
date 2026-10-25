"""资料事实：扫出来的字段。身份证、业绩仍用原来的前缀，不改盘上的旧摘要。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from api.services.tenders.id_card import dump_id_meta, load_id_meta
from api.services.tenders.performance import dump_perf_meta, load_perf_meta

logger = logging.getLogger(__name__)

FACTS_PREFIX = "FACTS:"
_ROLES = {"company", "person/legal", "person/pm", "person/pmWork", "performance", "other"}
_KEEP_SUMMARY = ("PERFJSON:", "idcard:", "FACTS:")
_BANNED = ("报价", "投标总价", "工期", "质保", "付款")
_MAX_CHARS = 6000

# 填空点, 角色, 字段名, 表。表：pm 简历 / company 企业表 / work 工作经历 / any
_FIELD_MAP = (
    ("统一社会信用代码", "company", "统一社会信用代码", "any"),
    ("注册资本", "company", "注册资本", "any"),
    ("成立日期", "company", "成立日期", "any"),
    ("经营范围", "company", "经营范围", "any"),
    ("身份证号码", "person/legal", "身份证号", "company"),
    ("身份证号", "person/legal", "身份证号", "company"),
    ("性别", "person/legal", "性别", "company"),
    ("年龄", "person/legal", "年龄", "company"),
    ("姓名", "person/legal", "姓名", "company"),
    ("专业等级", "person/pm", "级别", "pm"),
    ("注册证号", "person/pm", "注册证号", "pm"),
    ("建造师专业", "person/pm", "建造师专业", "pm"),
    ("职称", "person/pm", "职称", "pm"),
    ("学历", "person/pm", "学历", "pm"),
    ("级别", "person/pm", "级别", "pm"),
    ("项目经理", "person/pm", "姓名", "pm"),
    ("姓名", "person/pm", "姓名", "pm"),
    ("起止时间", "person/pmWork", "起止时间", "work"),
    ("担任职务", "person/pmWork", "担任职务", "work"),
    ("项目名称", "person/pmWork", "项目名称", "work"),
)

# 这些只跟本标表单走，不从资料库填
_FORM_ONLY = (
    "招标编号",
    "投标范围",
    "投标质量",
    "投标有效期",
    "缺陷责任期",
    "电话",
    "地址",
    "邮箱",
    "联合体",
    "投标人",
    "单位名称",
    "企业名称",
    "邮政编码",
    "传真",
    "网址",
)


def summary_locked(raw: str | None) -> bool:
    text = (raw or "").lstrip()
    return text.startswith(_KEEP_SUMMARY)


def dump_facts(bundle: dict) -> str:
    role = str(bundle.get("role") or "other")
    if role not in _ROLES:
        role = "other"
    fields = []
    seen: set[str] = set()
    for item in bundle.get("fields") or []:
        name = str(item.get("name") or "").strip()[:32]
        value = str(item.get("value") or "").strip()[:500]
        if not name or not value or name in seen:
            continue
        if any(mark in name for mark in _BANNED):
            continue
        conf = _conf(item.get("conf", 1))
        if conf < 0.6:
            continue
        seen.add(name)
        fields.append({"name": name, "value": value, "conf": round(conf, 2)})
        if len(fields) >= 16:
            break
    body = {"role": role, "fields": fields}
    return FACTS_PREFIX + json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def load_facts(raw: str | None) -> dict | None:
    text = (raw or "").strip()
    if not text.startswith(FACTS_PREFIX):
        return None
    try:
        payload = json.loads(text[len(FACTS_PREFIX) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    role = str(payload.get("role") or "other")
    if role not in _ROLES:
        role = "other"
    fields = []
    for item in payload.get("fields") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if not name or not value:
            continue
        fields.append({"name": name, "value": value, "conf": _conf(item.get("conf", 1))})
    return {"role": role, "fields": fields}


def view_facts(raw: str | None) -> dict | None:
    hit = load_facts(raw)
    if hit is not None:
        hit["source"] = "llm"
        return hit
    card = load_id_meta(raw)
    if card is not None:
        return _from_id(card)
    line = load_perf_meta(raw)
    if line is not None:
        return _from_perf(line)
    return None


def apply_correction(raw: str | None, *, role: str, fields: list) -> str:
    clean = _clean_fields(fields, drop_banned=False)
    card = load_id_meta(raw)
    if card is not None:
        for item in clean:
            attr = {"姓名": "name", "性别": "gender", "身份证号": "idNo", "年龄": "age"}.get(item["name"])
            if attr:
                card[attr] = item["value"]
        return dump_id_meta(card)
    line = load_perf_meta(raw)
    if line is not None:
        for item in clean:
            if item["name"] == "项目名称":
                line.projectName = item["value"][:255]
            elif item["name"] == "规格":
                line.spec = item["value"][:255]
            elif item["name"] == "发包人":
                line.client = item["value"][:255]
            elif item["name"] == "概况":
                line.summary = item["value"][:800]
            elif item["name"] == "合同金额":
                line.amountYuan = _money(item["value"], line.amountYuan)
        if not (line.projectName or "").strip():
            raise ValueError("项目名称不能为空")
        return dump_perf_meta(line)
    picked = role if role in _ROLES else "other"
    return dump_facts({"role": picked, "fields": [{**item, "conf": 1} for item in clean]})


def fact_fill(label: str, sheet: str, facts: list | None) -> tuple[str, str]:
    if not facts:
        return "", ""
    tail = re.sub(r"[\s:：]+$", "", re.sub(r"\s+", "", label or ""))
    if not tail or any(mark in tail for mark in _BANNED):
        return "", ""
    if sheet != "work" and "项目名称" in tail:
        return "", ""
    if any(mark in tail for mark in _FORM_ONLY):
        return "", ""
    hit = None
    best = 0
    for lab, role, field, where in _FIELD_MAP:
        if where != "any" and where != sheet:
            continue
        if tail == lab or tail.endswith(lab):
            if len(lab) > best:
                best = len(lab)
                hit = (role, field, lab)
    if hit is None:
        return "", ""
    role, field, lab = hit
    got: list[str] = []
    for bundle in facts:
        if (bundle.get("role") or "") != role:
            continue
        for item in bundle.get("fields") or []:
            if (item.get("name") or "") != field:
                continue
            value = str(item.get("value") or "").strip()
            if not value or _conf(item.get("conf", 1)) < 0.6 or value in got:
                continue
            got.append(value)
    if len(got) == 1:
        return got[0], ""
    if len(got) > 1:
        return "", f"多份未选定，未填{lab}"
    return "", ""


def guess_role(key: str, title: str) -> str:
    blob = f"{key} {title}"
    if "工作经历" in blob or "简历" in blob:
        return "person/pmWork"
    if any(k in blob for k in ("项目经理", "建造师", "安全生产考核")):
        return "person/pm"
    if "身份证" in blob:
        return "person/legal"
    return "company"


async def extract_facts(db, path: Path, *, key: str, title: str, hint: str, filename: str) -> dict | None:
    text = ""
    try:
        from api.services.knowledge.parsers import extract_text_from_file

        extracted = await extract_text_from_file(path, ext=path.suffix.lower().lstrip("."))
        text = (extracted.text or "")[:_MAX_CHARS]
    except Exception:
        logger.info("facts OCR skipped file=%s", filename)
        return None
    if not text.strip():
        return None
    try:
        from api.services.layouts.parse import extract_json_object
        from api.services.models.runtime import build_llm_client

        client = await build_llm_client(db, "fast")
        tip = (hint or "").strip()[:200]
        raw = await client.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "你从投标资料扫描件的识别文本里抽字段。只保留文本里能看见的，禁止编造。"
                        "报价、工期、质保、付款、投标总价不要抽。看不出就 fields 为空。"
                        "role 只能是 company、person/legal、person/pm、person/pmWork、other。"
                        "营业执照和许可证用 company；法人身份证用 person/legal；"
                        "项目经理证件用 person/pm；工作经历用 person/pmWork。"
                        "字段名只用：统一社会信用代码、注册资本、成立日期、经营范围、姓名、性别、年龄、"
                        "身份证号、注册证号、级别、职称、学历、建造师专业、起止时间、担任职务、项目名称。"
                        "只输出一个 JSON：{\"role\":\"\",\"fields\":[{\"name\":\"\",\"value\":\"\",\"conf\":0.9}]}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"资料项：{title}\n内部键：{key}\n"
                        f"人工说明：{tip or '无'}\n文件名：{filename}\n正文：\n{text}"
                    ),
                },
            ],
            mode="fast",
        )
        payload = extract_json_object(raw)
    except Exception:
        logger.exception("facts LLM extract failed file=%s", filename)
        return None
    if not isinstance(payload, dict):
        return None
    fields = _clean_fields(payload.get("fields") or [], drop_banned=True)
    role = str(payload.get("role") or "").strip()
    if role not in _ROLES:
        role = ""
    if role in {"", "other"} and fields:
        role = guess_role(key, title)
    if role not in _ROLES:
        role = "other"
    if not fields:
        return None
    return {"role": role, "fields": fields}


def _from_id(card: dict) -> dict | None:
    pairs = (
        ("姓名", card.get("name")),
        ("性别", card.get("gender")),
        ("身份证号", card.get("idNo")),
        ("年龄", card.get("age")),
    )
    fields = [{"name": n, "value": str(v).strip(), "conf": 1.0} for n, v in pairs if str(v or "").strip()]
    if not fields:
        return None
    return {
        "role": "person/legal",
        "fields": fields,
        "source": "idcard",
        "personKey": card.get("personKey") or "",
    }


def _from_perf(line) -> dict | None:
    amount = float(line.amountYuan or 0)
    amount_text = ""
    if amount > 0:
        amount_text = str(int(round(amount))) if abs(amount - round(amount)) < 0.001 else f"{amount:.2f}"
    pairs = (
        ("项目名称", line.projectName),
        ("规格", line.spec),
        ("发包人", line.client),
        ("合同金额", amount_text),
        ("概况", line.summary),
    )
    fields = [{"name": n, "value": str(v).strip(), "conf": 1.0} for n, v in pairs if str(v or "").strip()]
    if not fields:
        return None
    return {"role": "performance", "fields": fields, "source": "perf"}


def _clean_fields(items, *, drop_banned: bool) -> list[dict]:
    fields = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:32]
        value = str(item.get("value") or "").strip()[:500]
        if not name or not value or name in seen:
            continue
        if drop_banned and any(mark in name for mark in _BANNED):
            continue
        conf = _conf(item.get("conf", 1))
        if drop_banned and conf < 0.6:
            continue
        seen.add(name)
        fields.append({"name": name, "value": value, "conf": round(conf, 2)})
        if len(fields) >= 16:
            break
    return fields


def _conf(raw) -> float:
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if n > 1:
        n = n / 100.0
    if n < 0:
        return 0.0
    return n


def _money(text: str, fallback: float) -> float:
    raw = (text or "").replace(",", "").replace("，", "").replace("元", "").strip()
    wan = "万" in raw
    raw = raw.replace("万", "").strip()
    try:
        n = float(raw)
    except ValueError:
        return fallback
    if wan:
        n *= 10000
    return max(0.0, n)
